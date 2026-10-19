"""Natural-language campus discovery and multi-source harvest planning.

This module deliberately stops at an auditable candidate bundle.  It does not
publish POIs or mutate the runtime campus registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Callable

import requests

from .boundary import covering_radius_m, distance_m
from .harvest import (AmapGridTypeHarvester, AmapPoiHarvester, HarvestCache, Harvester,
                       HarvestResult, PublicWebHarvester)
from .outbound import direct_proxies
from .profile import validate_profile
from .web_search import WebSearchClient


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CAMPUSES = _ROOT / "webapp" / "backend" / "campuses.json"

logger = logging.getLogger(__name__)


# AMap and public pages normally use the official school name without a city
# prefix.  Keep this alias table intentionally small and explicit instead of
# applying a generic "remove city" rule, which would corrupt official names
# such as Shanghai Jiao Tong University (上海交通大学).
_SCHOOL_NAME_ALIASES = {
    "上海市同济大学": "同济大学",
    "上海同济大学": "同济大学",
    "上海市复旦大学": "复旦大学",
    "上海复旦大学": "复旦大学",
}

_SCHOOL_ALIASES_BY_OFFICIAL = {
    official: sorted(
        (alias for alias, target in _SCHOOL_NAME_ALIASES.items() if target == official),
        key=len,
        reverse=True,
    )
    for official in set(_SCHOOL_NAME_ALIASES.values())
}

# These labels are not stable campus identities.  Registered aliases (for
# example SJTU's "闵行本部") are still resolved by _known() before this check.
_AMBIGUOUS_CAMPUS_LABELS = frozenset({"本部", "本部校区", "主校区"})

# This mapping is deliberately scoped by official school name.  "本部" is not
# a universal campus identifier; the maintained Fudan rule resolves it to the
# Handan campus for this workflow.  Other schools must still provide a concrete
# campus unless their local registry contains an explicit alias.
_SCHOOL_CAMPUS_LABELS = {
    ("复旦大学", "本部"): "邯郸校区",
    ("复旦大学", "本部校区"): "邯郸校区",
}

_CAMPUS_NAME_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·-]{1,24}?校区")

# 未登记学校的默认采集半径。旧实现固定 2000m，会把校园外的点位也收进来，
# 表现为"点云比真实校园大一圈"；收紧后由页面明确展示搜索范围。
DEFAULT_DISCOVERY_RADIUS_M = 1200
MIN_DISCOVERY_RADIUS_M = 300
MAX_DISCOVERY_RADIUS_M = 3000

#: 请求前缀：礼貌语、人称与意图动词，允许任意组合并重复剥离。
#: 只在字符串开头匹配，且剥完至少保留 2 个字符，避免误伤正式校名。
_REQUEST_PREFIX_TOKENS = (
    "麻烦您", "麻烦你", "麻烦", "请问您", "请问", "请您", "请", "你好", "您",
    "帮我", "帮忙", "给我", "替我", "为我", "我想要", "我需要", "我想", "我要",
    "能不能", "可以", "能否", "请你", "你", "我",
)
_REQUEST_ACTION_TOKENS = (
    "搜索一下", "查询一下", "查找一下", "搜一下", "找一下",
    "搜索", "查询", "查找", "采集", "获取", "抓取", "统计", "分析",
    "生成", "制作", "生产", "创建", "绘制", "构建", "显示", "展示",
    "搜", "找", "画", "做",
)

#: 机构名左侧的天然边界。取窗口内"最后一个边界之后"的子串作为候选校名，
#: 这样 "你给我搜索北京大学" 只会留下 "北京大学"。
_LEFT_BOUNDARY_TOKENS = tuple(sorted({
    "，", "。", "；", "：", "、", ",", ".", ";", ":", "！", "？", "!", "?",
    "（", "(", "）", ")", "“", "”", "\"", "《", "》", " ", "\t", "\n",
    "搜索一下", "查询一下", "查找一下", "搜一下", "找一下",
    "搜索", "查询", "查找", "采集", "获取", "抓取", "统计", "分析",
    "生成", "制作", "生产", "创建", "绘制", "构建", "显示", "展示",
    "麻烦", "请问", "请您", "请", "帮我", "帮忙", "给我", "替我", "为我",
    "我想要", "我需要", "我想", "我要", "能不能", "可以", "能否",
    "位于", "关于", "针对", "在", "去", "到", "从", "往", "离",
    "与", "和", "及", "的", "你", "我", "搜", "找", "画", "做",
}, key=len, reverse=True))

#: 机构名里不可能出现的请求词（命中即判为噪声）。
_INSTITUTION_NOISE_RE = re.compile(
    r"请|帮|麻烦|搜索|查询|查找|采集|获取|统计|分析|生成|制作|绘制|构建|"
    r"热力图|热度图|分布图|地点|景点|场所|校园|一下|所有|全部|相关|附近|周边|"
    r"你|我"
)

#: "大学城 / 大学路 / 大学生" 不是学校名，命中后缀后一个字符即跳过。
_INSTITUTION_TAIL_STOP = frozenset("城路道街桥门生苑区")

#: 显式类别词。用户写了什么就采什么，避免被默认主题词挤掉。
_REQUEST_CATEGORY_SYNONYMS = (
    ("建筑", ("建筑物", "建筑群", "楼宇", "大楼", "教学楼", "实验楼", "建筑")),
    ("地标建筑", ("地标建筑", "标志性建筑", "地标")),
    ("树木", ("花草树木", "花木", "乔木", "树木", "植被", "绿植")),
    ("植物", ("植物", "花草")),
    ("绿化", ("绿化",)),
    ("亭子", ("亭子", "凉亭")),
    ("河岸", ("河岸", "河畔", "水岸", "湖边", "湖畔", "河边")),
    ("运动场", ("运动场", "操场", "田径场", "足球场")),
    ("体育馆", ("体育馆", "游泳馆", "健身中心")),
    ("图书馆", ("图书馆",)),
    ("食堂", ("食堂", "餐厅")),
    ("咖啡店", ("咖啡店", "咖啡厅", "咖啡馆", "咖啡")),
    ("湖泊", ("湖泊", "水景", "湖")),
    ("桥", ("桥",)),
    ("校门", ("校门", "大门", "正门")),
    ("广场", ("广场",)),
    ("雕塑", ("雕塑", "雕像")),
    ("花园", ("花园", "园林")),
    ("草坪", ("草坪", "绿地")),
    ("步道", ("步道", "小径")),
    ("樱花", ("樱花",)),
    ("银杏", ("银杏",)),
    ("梅花", ("梅花",)),
    ("荷花", ("荷花", "荷塘")),
    ("桂花", ("桂花",)),
)

_THEME_INTENTS = {
    "date": ("约会", "情侣", "浪漫", "二人世界"),
    "walk": ("散步", "遛弯", "走走", "漫步", "放松"),
    "photo": ("拍照", "摄影", "出片", "拍摄"),
    "flower": ("赏花", "看花", "花季", "花期", "樱花", "银杏", "梅花", "荷花", "桂花"),
    "study": ("学习", "自习", "安静"),
    "food": ("吃饭", "美食", "餐厅", "食堂", "咖啡"),
}

#: 半径写法："500米"、"1.5公里"、"2km 内"。单位按长到短匹配，避免 "km" 被 "k" 截断。
_RADIUS_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(公里|千米|km|KM|Km|k|米|m|M)(?:以内|之内|范围内|内)?"
)


@dataclass(frozen=True)
class DiscoveryPlan:
    query: str
    profile: dict
    theme: str
    keywords: list[str]
    web_urls: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    targetCategory: str = "mixed"
    searchQueries: list[str] = field(default_factory=list)
    searchResults: list[dict] = field(default_factory=list)
    webSearch: dict = field(default_factory=dict)


THEME_KEYWORDS = {
    "walk": ["步道", "林荫道", "绿地", "草坪", "湖泊", "河流", "河岸", "桥", "亭子", "运动场", "体育馆", "操场", "足球场", "网球场", "游泳馆", "广场", "雕塑", "建筑"],
    "date": ["湖泊", "河流", "河岸", "桥", "亭子", "花园", "草坪", "水景", "园林", "风景", "景观", "图书馆", "咖啡店", "广场", "雕塑", "文化场馆", "展馆", "博物馆", "艺术中心", "校门"],
    "photo": ["湖泊", "河流", "河岸", "花园", "亭子", "樱花", "银杏", "草坪", "建筑", "图书馆", "体育馆", "校门", "广场", "雕塑", "桥", "操场", "游泳馆"],
    "flower": ["花园", "植物园", "樱花", "梅花", "荷花", "桂花", "银杏"],
    "study": ["图书馆", "教学楼", "自习室", "咖啡店"],
    "food": ["食堂", "餐厅", "咖啡店", "小吃", "广场"],
    "general": ["图书馆", "湖泊", "花园", "食堂", "体育场", "咖啡店", "河岸", "桥", "亭子", "体育馆", "建筑", "广场"],
}

PLANT_KEYWORDS = ["树木", "花卉", "植物", "樱花", "银杏", "梅花", "桂花", "紫藤", "绿化"]
THEME_PLANT_KEYWORDS = {
    "walk": ["树木", "植物", "绿化"],
    "date": ["花卉", "植物", "樱花", "桂花"],
    "photo": ["植物", "樱花", "银杏"],
    "flower": PLANT_KEYWORDS,
    "general": ["树木", "植物", "绿化"],
}

THEME_SEARCH_TERMS = {
    "walk": "散步 步道 绿地 河岸 校园景点",
    "date": "约会 花园 湖泊 亭子 校园景点",
    "photo": "拍照 摄影 建筑 雕塑 校园景点",
    "flower": "赏花 樱花 银杏 植物 花园",
    "study": "学习 自习 图书馆 教学楼",
    "food": "食堂 餐厅 咖啡 校园美食",
    "general": "校园 景点 植物 建筑",
}

_GENERIC_SEARCH_MATCH_NAMES = {
    "图书馆", "体育馆", "运动场", "食堂", "餐厅", "咖啡店", "花园",
    "草坪", "湖泊", "河岸", "亭子", "教学楼", "校门", "广场",
}
PLANT_EVIDENCE_TERMS = tuple(PLANT_KEYWORDS + ["园林", "苗圃", "绿地", "植物园", "花园", "樱园", "梅园", "荷塘"])
NON_CAMPUS_PLANT_TERMS = (
    "鲜花店", "花店", "花坊", "花艺", "花卉市场", "苗木市场", "园艺店", "园艺公司",
    "花鸟市场", "花市", "盆栽店", "植物店", "园林公司", "景观公司", "农资店",
)
NON_SCENIC_REGISTRY_TERMS = ("充电站", "面馆", "快餐", "小吃")
COMMERCIAL_TERMS = NON_CAMPUS_PLANT_TERMS + (
    "商场", "购物中心", "超市", "便利店", "房地产", "酒店", "宾馆", "KTV", "酒吧",
    "住宅小区", "公寓", "底商", "服务公寓",
    "餐饮服务", "面馆", "餐厅", "饭店", "食堂", "小吃",
)
DATE_UNSUITABLE_TERMS = (
    "学院", "教学楼", "实验楼", "科研楼", "研究院", "研究所", "工程训练",
    "行政楼", "办公楼", "宿舍", "学生公寓", "科技园", "产业园", "充电站",
)
DATE_GENERIC_BUILDING_TERMS = ("楼", "大楼", "教学", "学院", "实验", "科研", "研究院", "研究所")

SCENE_TERM_ALIASES = {
    "步道": ("步道", "小径", "漫步", "步行"),
    "林荫道": ("林荫", "林荫道", "树荫"),
    "绿地": ("绿地", "绿化", "公园"),
    "草坪": ("草坪", "草地", "绿地"),
    "湖泊": ("湖", "湖泊", "水景"),
    "河流": ("河", "河流", "溪", "渠"),
    "河岸": ("河岸", "河畔", "水岸", "湖畔", "湖边"),
    "水景": ("水景", "湖", "河", "溪", "水岸", "景观水体"),
    "桥": ("桥",),
    "亭子": ("亭", "亭子", "凉亭"),
    "花园": ("花园", "园林", "公园", "花圃", "花境", "园"),
    "园林": ("园林", "园", "花园", "植物园", "景观"),
    "风景": ("风景", "景点", "景观", "园林", "水景"),
    "景观": ("景观", "风景", "园林", "水景", "绿地"),
    "地标建筑": ("楼", "馆", "塔", "门", "中心", "建筑"),
    "建筑": ("楼", "馆", "塔", "门", "中心", "建筑", "科教文化", "楼宇"),
    "雕塑": ("雕塑", "雕像", "铜像", "纪念碑"),
    "运动场": ("运动场", "操场", "田径场", "足球场", "球场", "体育场", "体育场馆", "运动场馆"),
    "体育场": ("体育场", "运动场", "操场", "体育场馆", "运动场馆"),
    "体育馆": ("体育馆", "游泳馆", "健身中心", "体育场馆", "运动场馆"),
    "图书馆": ("图书馆",),
    "文化场馆": ("文化场馆", "会堂", "展馆", "博物馆", "艺术中心", "美术馆", "文化中心"),
    "展馆": ("展馆", "博物馆", "会堂", "美术馆", "展览馆"),
    "博物馆": ("博物馆", "校史馆", "展馆", "会堂"),
    "艺术中心": ("艺术中心", "艺术馆", "美术馆", "文化中心", "会堂"),
    "校门": ("校门", "大门", "门"),
    "广场": ("广场",),
    "咖啡店": ("咖啡",),
    "樱花": ("樱花", "樱园", "樱花林"),
    "银杏": ("银杏",),
    "梅花": ("梅花", "梅园"),
    "荷花": ("荷花", "荷塘", "莲池"),
    "桂花": ("桂花",),
}


def _theme_scene(theme: str) -> str:
    return "flower_viewing" if theme == "flower" else theme if theme in {"walk", "date", "photo"} else "walk"


def _candidate_text(item: dict) -> str:
    evidence = item.get("evidence") or {}
    return " ".join(str(value or "") for value in (
        item.get("name"), item.get("subCategory"), item.get("locationName"),
        evidence.get("type"), evidence.get("address"),
    ))


def _matches_terms(text: str, terms) -> bool:
    lowered = text.lower()
    return any(str(term).lower() in lowered for term in terms)


def _has_campus_evidence(item: dict, profile: dict) -> bool:
    school = profile.get("school") or {}
    campus = profile.get("campus") or {}
    campus_terms = [campus.get("name"), *(campus.get("aliases") or [])]
    campus_terms = [term for term in campus_terms if "校区" in str(term or "")]
    terms = [school.get("name"), school.get("enName"), *(school.get("aliases") or []),
             *campus_terms, "校园", "校内"]
    terms = [str(term).strip() for term in terms if str(term or "").strip()]
    return _matches_terms(_candidate_text(item), terms)


def _has_spatial_campus_evidence(item: dict) -> bool:
    """Use the already-enforced campus boundary/radius decision as evidence."""
    evidence = item.get("evidence") or {}
    if not isinstance(evidence, dict):
        return False
    return (
        evidence.get("campusMembershipMethod") in {"polygon", "radius_fallback"}
        and evidence.get("campusMembershipRelation", "inside") in {"inside", "boundary"}
    )


def _scoped_keyword(profile: dict, keyword: str) -> str:
    school_info = profile.get("school") or {}
    # Profiles discovered from natural language should already contain clean
    # identity fields.  Sanitising once more here prevents prompt verbs from
    # leaking into provider queries when an older persisted profile is reused.
    school = _clean_request(str(school_info.get("name") or ""))
    campus = profile.get("campus") or {}
    campus_name = _clean_request(str(campus.get("name") or ""))
    # Internal names may include a school abbreviation (for example
    # "交大闵行").  High-quality AMap results use the official phrase
    # "上海交通大学 闵行校区", so remove that prefix before
    # composing the scoped query.
    school_aliases = [school, school_info.get("enName"), *(school_info.get("aliases") or [])]
    campus_label = campus_name
    for alias in sorted((str(value).strip() for value in school_aliases if str(value or "").strip()),
                        key=len, reverse=True):
        if campus_label.lower().startswith(alias.lower()):
            campus_label = campus_label[len(alias):].strip()
            break
    if campus_label and "校区" not in campus_label:
        campus_label += "校区"
    return " ".join(value for value in (school, campus_label, keyword) if value)


def _search_queries(profile: dict, theme: str, keywords: list[str]) -> list[str]:
    """Build a diverse, reproducible search budget for one campus request."""
    scope = _scoped_keyword(profile, "")
    theme_terms = THEME_SEARCH_TERMS.get(theme) or THEME_SEARCH_TERMS["general"]
    focused = " ".join(keywords[:8])
    return list(dict.fromkeys(filter(None, (
        f"{scope} {theme_terms}".strip(),
        f"{scope} 校园地图 校园导览 景点".strip(),
        f"{scope} {focused}".strip(),
        f"{scope} 官网 校园风光 {theme_terms}".strip(),
    ))))[:4]


def _search_hit_dict(hit) -> dict:
    if isinstance(hit, dict):
        value = dict(hit)
    elif is_dataclass(hit):
        value = asdict(hit)
    elif hasattr(hit, "to_dict"):
        value = dict(hit.to_dict())
    else:
        raise TypeError("web search returned an unsupported hit object")
    # Search evidence is persisted with a deliberately small allowlist.  Raw
    # provider responses and credentials must never enter job metadata.
    return {
        key: value[key]
        for key in ("provider", "query", "rank", "url", "title", "snippet", "score")
        if key in value and value[key] not in (None, "")
    }


def _matchable_name(value: str) -> str:
    return re.sub(r"[\s·•・()（）\[\]【】_-]+", "", str(value or "")).lower()


def _keyword_matches_candidate(item: dict, keyword: str) -> bool:
    aliases = SCENE_TERM_ALIASES.get(keyword, (keyword,))
    return _matches_terms(_candidate_text(item), aliases)


def _date_candidate_is_suitable(item: dict) -> bool:
    """Exclude ordinary teaching/office facilities from date heatmaps."""
    text = _candidate_text(item)
    if _matches_terms(text, DATE_UNSUITABLE_TERMS):
        return False
    # A generic building keyword can return many academic buildings.  Allow
    # named cultural/landmark facilities, but reject unqualified building POIs.
    if _matches_terms(text, DATE_GENERIC_BUILDING_TERMS):
        return _matches_terms(text, ("图书馆", "会堂", "展馆", "博物馆", "艺术中心", "美术馆", "校门", "广场"))
    return True


def _scene_commercial_terms(theme: str) -> tuple[str, ...]:
    """Keep campus coffee shops as date candidates while filtering businesses."""
    if theme == "date":
        return tuple(term for term in COMMERCIAL_TERMS if term != "咖啡店")
    return COMMERCIAL_TERMS


def _registry_paths(profile: dict, primary: Path) -> list[Path]:
    paths = [primary]
    slug = str((profile.get("campus") or {}).get("slug") or "").strip()
    campus_file = _ROOT / "webapp" / "backend" / f"{slug}_pois.json"
    if slug and campus_file.is_file() and campus_file.resolve() != primary.resolve():
        paths.append(campus_file)
    return paths


def infer_theme(query: str) -> str:
    """按"显式意图词"判定主题；多个类别并列时归为 general。

    旧实现只要文本含单字"花"就判赏花，导致"花草树木 + 建筑 + 地标"这类混合请求
    被强行套进赏花场景，热图里只剩一层与点位无关的淡色。
    """
    text = str(query or "").lower()
    if not text:
        return "general"
    for theme in ("date", "walk", "photo", "flower", "study", "food"):
        if any(word in text for word in _THEME_INTENTS[theme]):
            return theme
    return "general"


def _slug(value: str, prefix: str) -> str:
    ascii_text = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    if ascii_text:
        return ascii_text[:48]
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _finite_location(value):
    try:
        lng, lat = (float(item) for item in str(value).split(",", 1))
        if math.isfinite(lng) and math.isfinite(lat):
            return [lng, lat]
    except (TypeError, ValueError):
        return None
    return None


def _name_key(value) -> str:
    """比较用地名键：去空白/间隔符/括号，统一小写。"""
    return re.sub(r"[\s·•・()（）\[\]【】_\-]+", "", str(value or "")).lower()


def _address_contains(address, term) -> bool:
    """地理编码地址里是否真的出现了这个词（防止"海淀校区"被编码到同名行政区）。"""
    key = _name_key(term)
    return bool(key) and key in _name_key(address)


def _pick_poi_location(pois, school: str, campus_label: str = "") -> dict | None:
    """从高德 place/text 结果里挑一个可信的学校/校区点位。

    只接受与校名对齐、且不是附属中学/小学/分校的点位；`fullCampus=True` 表示
    命中了"学校+校区"的完整名称，可作为校区存在性的证据。
    """
    target_school = _name_key(school)
    target_full = _name_key(f"{school}{campus_label}") if campus_label else ""
    best = None
    for poi in pois or []:
        if not isinstance(poi, dict):
            continue
        name = _name_key(poi.get("name"))
        center = _finite_location(poi.get("location"))
        if not name or not center:
            continue
        if any(term in name for term in ("附属", "中学", "小学", "幼儿园", "分校")):
            continue
        full_campus = bool(target_full) and name == target_full
        if full_campus:
            score = 4.0
        elif name == target_school:
            score = 3.0
        elif target_full and name.startswith(target_full):
            score = 2.0
        elif name.startswith(target_school):
            score = 1.0
        else:
            continue
        if "高等院校" in str(poi.get("type") or ""):
            score += 0.5
        if best is None or score > best["score"]:
            best = {"score": score, "center": center,
                    "name": str(poi.get("name") or ""), "fullCampus": full_campus}
    return best


def _strip_request_prefix(text: str) -> str:
    """反复剥离礼貌语、人称与意图动词前缀；只在字符串开头匹配。

    "请你给我搜索北京大学…" 会依次剥掉 请 → 你 → 给我 → 搜索，只留下真正的请求内容。
    每步都要求剩余内容至少 2 个字符，避免把校名本身吃掉。
    """
    value = str(text or "").strip()
    tokens = _REQUEST_PREFIX_TOKENS + _REQUEST_ACTION_TOKENS
    for _ in range(12):
        original = value
        value = value.lstrip(" \t\r\n，,、:：;；。.!！?？-")
        for token in tokens:
            if value.startswith(token) and len(value) >= len(token) + 2:
                value = value[len(token):]
                value = re.sub(r"^(?:一下|一个|一份|一张|一次)\s*", "", value.lstrip())
                break
        if value == original:
            break
    return value.strip()


def _request_intent_text(query: str) -> str:
    """保留主题/类别词，只去掉请求壳、半径和"热力图"等非语义尾巴。

    与 `_clean_request` 分开：后者为了拼采集关键词会删掉"拍照/约会/食堂"等词，
    但主题判定与显式类别解析恰恰需要这些词。
    """
    text = _strip_request_prefix(str(query or "").strip())
    text = _RADIUS_RE.sub("", text)
    text = re.sub(r"(?:的)?(?:校园)?(?:热力图|热度图|分布图)(?:页面|数据|项目)?", "", text)
    text = re.sub(r"(?:相关|主题|推荐)?\s*(?:可以吗|行吗|吗|呢)?$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" 的，,。；;：:？?!！")
    return text


def _cut_institution_left(text: str, suffix_start: int, window_chars: int = 16) -> str:
    """从后缀左侧窗口里取"最后一个边界之后"的子串。"""
    window = text[max(0, suffix_start - window_chars):suffix_start]
    cut = 0
    for token in _LEFT_BOUNDARY_TOKENS:
        index = window.rfind(token)
        if index >= 0:
            cut = max(cut, index + len(token))
    return window[cut:]


class _NameMatch:
    """轻量匹配结果：只暴露抽取后的完整机构名与匹配结束位置。"""

    __slots__ = ("name", "end")

    def __init__(self, name: str, end: int):
        self.name = name
        self.end = end

    def group(self, _group: int = 0) -> str:
        return self.name


def _institution_at(value: str, suffix_end: int, suffix: str) -> str:
    """把后缀与左侧紧邻的实词拼成机构名，并拒绝"大学城/大学路"等非学校词。"""
    tail = value[suffix_end:suffix_end + 1]
    if tail and tail in _INSTITUTION_TAIL_STOP:
        return ""
    prefix = _cut_institution_left(value, suffix_end - len(suffix))
    if len(prefix) < 2:
        return ""
    name = prefix + suffix
    if _INSTITUTION_NOISE_RE.search(name):
        return ""
    return name


def _school_match(value: str):
    """抽取完整校名；大学优先于学院，返回"边界锚定"后的干净名称。"""
    for suffix in ("大学", "学院"):
        for match in re.finditer(suffix, value):
            name = _institution_at(value, match.end(), suffix)
            if name:
                return _NameMatch(name, match.end())
    return None


def _clean_request(query: str) -> str:
    text = _strip_request_prefix(str(query or "").strip())
    text = _RADIUS_RE.sub("", text)
    # Intent words are usually prefixes, so remove them only at the beginning
    # rather than deleting the same characters from a legitimate place name.
    for pattern in (
        r"^(?:请问|请|麻烦)\s*",
        r"^(?:(?:帮|替|给|为)我|我(?:想要|需要|想|要))\s*",
        r"^(?:做|生成|生产|制作|创建|绘制|构建)\s*(?:一(?:个|份|张)|一下)?\s*",
    ):
        # A request can contain more than one polite/intent prefix, for example
        # "请帮我生成一个……".  Repeat until this prefix no longer matches.
        while True:
            updated = re.sub(pattern, "", text, count=1)
            if updated == text:
                break
            text = updated.lstrip()
    text = re.sub(r"(?:的)?(?:校园)?(?:热力图|热度图|分布图)(?:页面|数据|项目)?", "", text)
    text = re.sub(r"(校园|校内)?(地点|景点|场所)(推荐|分布)?", "", text)
    text = re.sub(r"(约会|情侣|浪漫|散步|遛弯|走走|休息|放松|拍照|摄影|出片|赏花|学习|自习|安静|吃饭|美食|餐厅|食堂)", "", text)
    # 先切掉"并生成热力图"这类动作尾巴，再清"等一系列/等等"。
    text = re.sub(r"(?:并|然后|再)?\s*(?:生成|制作|绘制|构建|输出|给出|展示|显示).*$", "", text)
    text = re.sub(r"^(?:关于|针对|位于|在)\s*", "", text)
    text = re.sub(r"(?:等)?(?:一?系列)?(?:之类|等等|等)?$", "", text)
    text = re.sub(r"(?:相关|主题|推荐)?\s*(?:可以吗|行吗|吗|呢)?$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" 的，,。；;：:？?!！")
    for alias in sorted(_SCHOOL_NAME_ALIASES, key=len, reverse=True):
        text = text.replace(alias, _SCHOOL_NAME_ALIASES[alias])
    return text


def _request_identity(value: str) -> tuple[str, str]:
    """Extract an official school name and a stable campus display name.

    A school-only request deliberately uses the school name as the broad campus
    scope.  That produces queries such as "复旦大学 图书馆" without inventing a
    fake "复旦大学校区".  Ambiguous labels must be made explicit by the caller.
    """
    normalized = _clean_request(value)
    if not normalized:
        raise ValueError("校园名称不能为空，请填写完整学校名称和校区名称")

    match = _school_match(normalized)
    if match:
        school_name = _SCHOOL_NAME_ALIASES.get(match.name, match.name)
        remainder = normalized[match.end:].strip(" 的，,。；;：:")
        campus_match = _CAMPUS_NAME_RE.search(remainder)
        campus_name = campus_match.group(0) if campus_match else ""
        ambiguous_label = campus_name or remainder
        mapped_campus = _SCHOOL_CAMPUS_LABELS.get((school_name, ambiguous_label))
        if mapped_campus:
            return school_name, mapped_campus
        if ambiguous_label in _AMBIGUOUS_CAMPUS_LABELS:
            raise ValueError(
                f"“{ambiguous_label}”无法唯一确定校区，请填写具体校区名称"
                "（例如“四平路校区”或“邯郸校区”）"
            )
        return school_name, campus_name or school_name

    # Preserve support for institutions whose short query does not contain a
    # suffix.  The geocoder can still resolve their centre, but no 校区 suffix
    # is silently fabricated.
    if normalized in _AMBIGUOUS_CAMPUS_LABELS:
        raise ValueError("校区名称过于模糊，请填写完整学校名称和具体校区名称")
    return normalized, normalized


def _requested_keywords(text: str) -> list[str]:
    """把用户显式写的类别词映射为采集关键词，保持出现顺序。"""
    lowered = str(text or "").lower()
    found = []
    for keyword, synonyms in _REQUEST_CATEGORY_SYNONYMS:
        if any(str(term).lower() in lowered for term in synonyms):
            found.append(keyword)
    return list(dict.fromkeys(found))


def _strip_identity_terms(text: str, profile: dict) -> str:
    """从意图文本里移除学校/校区名，避免"湖北大学"里的"湖"被当成湖泊。"""
    value = str(text or "")
    school = profile.get("school") or {}
    campus = profile.get("campus") or {}
    terms = [school.get("name"), school.get("enName"), *(school.get("aliases") or []),
             campus.get("name"), *(campus.get("aliases") or [])]
    for term in sorted((str(item).strip() for item in terms if str(item or "").strip()),
                       key=len, reverse=True):
        if len(term) >= 2:
            value = value.replace(term, " ")
    return value


def _parse_radius(query: str) -> tuple[int | None, str]:
    """解析"500米 / 1.5公里 / 2km内"，返回 (米, 来源)。"""
    match = _RADIUS_RE.search(str(query or ""))
    if not match:
        return None, ""
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None, ""
    unit = match.group(2).lower()
    meters = value * 1000 if unit in {"公里", "千米", "km", "k"} else value
    if not math.isfinite(meters) or meters <= 0:
        return None, ""
    return int(min(max(meters, MIN_DISCOVERY_RADIUS_M), MAX_DISCOVERY_RADIUS_M)), "query"


def _default_discovery_radius() -> int:
    raw = (os.getenv("CAMPUS_DISCOVERY_RADIUS_M") or "").strip()
    try:
        value = int(float(raw)) if raw else DEFAULT_DISCOVERY_RADIUS_M
    except (TypeError, ValueError):
        value = DEFAULT_DISCOVERY_RADIUS_M
    return int(min(max(value, MIN_DISCOVERY_RADIUS_M), MAX_DISCOVERY_RADIUS_M))


class CampusProfileResolver:
    """Resolve a request against local campus config, then AMap geocoding."""

    def __init__(self, config_path: str | Path = _DEFAULT_CAMPUSES,
                 *, api_key: str | None = None, request_json: Callable | None = None,
                 request_poi: Callable | None = None, poi_timeout: float = 8):
        self.config_path = Path(config_path)
        self.api_key = (api_key or os.getenv("AMAP_WEB_SERVICE_KEY", "")).strip()
        self.request_json = request_json or self._request_json
        self.poi_timeout = min(max(float(poi_timeout), 3), 20)
        # 离线单测通常注入 request_json 且不提供 request_poi：这种组合绝不能发出
        # 真实网络请求，因此只有"使用默认地理编码器"时才默认开启 POI 查询。
        if request_poi is not None:
            self.request_poi = request_poi
        elif request_json is not None:
            self.request_poi = None
        else:
            self.request_poi = self._request_poi

    def _config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _registered_school_alias(self, value: str) -> tuple[str, str, int] | None:
        """Return one unambiguous registered school name, matched term and offset."""

        folded = value.casefold()
        matches = []
        for school_id, school in (self._config().get("schools") or {}).items():
            terms = [school.get("name"), school.get("enName"), *(school.get("aliases") or [])]
            for term in terms:
                text = str(term or "").strip()
                offset = folded.find(text.casefold()) if text else -1
                if offset >= 0:
                    matches.append((len(text), school_id, str(school.get("name") or text), text, offset))
        if not matches:
            return None
        longest = max(item[0] for item in matches)
        strongest = [item for item in matches if item[0] == longest]
        if len({item[1] for item in strongest}) != 1:
            return None
        _, _, official_name, term, offset = strongest[0]
        return official_name, term, offset

    def _request_identity(self, query: str) -> tuple[str, str, str]:
        """Parse identity after expanding a registered abbreviation if needed."""

        normalized = _clean_request(query)
        if not _school_match(normalized):
            matched = self._registered_school_alias(normalized)
            if matched:
                official_name, term, offset = matched
                normalized = (
                    normalized[:offset] + official_name + normalized[offset + len(term):]
                )
        school_name, campus_name = _request_identity(normalized)
        return school_name, campus_name, normalized


    def _request_json(self, params: dict) -> dict:
        try:
            response = requests.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params=params,
                timeout=10,
                # restapi.amap.com is a domestic endpoint: the system proxy
                # registered by Clash-like tools makes every request fail with
                # ProxyError.  Same policy as the POI harvester; AMAP_HTTP_PROXY
                # overrides it for networks that really need a proxy.
                proxies=direct_proxies(),
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = status if status is not None else "unknown"
            # Do not propagate requests' exception text: it commonly embeds
            # the complete request URL, including the Web service key.  The
            # exception class alone is safe and is what actually locates the
            # fault (ProxyError / ConnectTimeout / SSLError).
            raise RuntimeError(f"AMap geocoding request failed (HTTP {detail})") from None
        except requests.RequestException as exc:
            raise RuntimeError(
                f"AMap geocoding request failed (network error: {type(exc).__name__})"
            ) from None
        try:
            data = response.json()
        except (TypeError, ValueError):
            raise RuntimeError("AMap geocoding returned invalid JSON") from None
        if not isinstance(data, dict):
            raise RuntimeError("AMap geocoding returned an invalid response")
        if str(data.get("status", "")) != "1":
            infocode = str(data.get("infocode") or "unknown")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", infocode):
                infocode = "unknown"
            raise RuntimeError(f"AMap geocoding failed (infocode={infocode})")
        return data

    def _request_poi(self, params: dict) -> dict:
        """高德 POI 文本搜索（place/text）。错误信息沿用脱敏口径，不含 key/URL。"""
        try:
            response = requests.get(
                "https://restapi.amap.com/v3/place/text",
                params=params,
                timeout=self.poi_timeout,
                proxies=direct_proxies(),
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = status if status is not None else "unknown"
            raise RuntimeError(f"AMap POI search failed (HTTP {detail})") from None
        except requests.RequestException as exc:
            raise RuntimeError(
                f"AMap POI search failed (network error: {type(exc).__name__})"
            ) from None
        try:
            data = response.json()
        except (TypeError, ValueError):
            raise RuntimeError("AMap POI search returned invalid JSON") from None
        if not isinstance(data, dict):
            raise RuntimeError("AMap POI search returned an invalid response")
        if str(data.get("status", "")) != "1":
            infocode = str(data.get("infocode") or "unknown")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", infocode):
                infocode = "unknown"
            raise RuntimeError(f"AMap POI search failed (infocode={infocode})")
        if data.get("pois") is not None and not isinstance(data.get("pois"), list):
            raise RuntimeError("AMap POI search returned an invalid poi list")
        return data

    def _poi_center(self, school_name: str, campus_label: str = "") -> dict | None:
        """用 POI 精确点位校正校区中心；没有配置/失败时返回 None。"""
        if self.request_poi is None or not self.api_key:
            return None
        queries = []
        if campus_label and campus_label != school_name:
            queries.append(f"{school_name}{campus_label}")
        queries.append(school_name)
        for query in queries:
            try:
                payload = self.request_poi({
                    "key": self.api_key, "keywords": query,
                    "offset": 10, "page": 1, "extensions": "base",
                })
            except (RuntimeError, requests.RequestException, OSError) as exc:
                # POI 只是校正项，失败不应阻断地理编码兜底。
                logger.warning("AMap POI lookup failed: %s", type(exc).__name__)
                continue
            picked = _pick_poi_location(
                payload.get("pois") or [], school_name,
                campus_label if query != school_name else "",
            )
            if picked:
                picked["query"] = query
                return picked
        return None

    def _known(self, query: str) -> dict | None:
        config = self._config()
        text = query.lower()
        schools = config.get("schools") or {}
        matches = []
        for campus in config.get("campuses") or []:
            school = schools.get(campus.get("school"), {})
            campus_terms = [campus.get("name", ""), *(campus.get("aliases") or [])]
            school_terms = [school.get("name", ""), school.get("enName", ""),
                            *(school.get("aliases") or [])]
            school_score = sum(len(str(term)) for term in school_terms
                               if term and str(term).lower() in text)
            if school_score:
                # Once the school is unambiguous, campus aliases may safely
                # drop that school's own prefix.  For example, the registered
                # alias "交大闵行校区" should match
                # "上海交通大学闵行校区", while the same bare "闵行校区"
                # must not globally select ECNU or SJTU before school scoping.
                scoped_terms = list(campus_terms)
                for term in campus_terms:
                    term_text = str(term or "")
                    for prefix in school_terms:
                        prefix_text = str(prefix or "")
                        if (prefix_text and
                                term_text.casefold().startswith(prefix_text.casefold())):
                            suffix = term_text[len(prefix_text):]
                            if suffix:
                                scoped_terms.append(suffix)
                campus_terms = list(dict.fromkeys(scoped_terms))
            campus_score = sum(len(str(term)) for term in campus_terms
                               if term and str(term).lower() in text)
            if school_score or campus_score:
                # School identity must win over a same-named campus.  The
                # campus score remains a tie-breaker for queries such as
                # "交大闵行校区" where the school is expressed by an alias.
                matches.append((school_score, campus_score, campus, school))
        if not matches:
            return None
        top_school_score = max(item[0] for item in matches)
        if top_school_score:
            school_matches = [item for item in matches if item[0] == top_school_score]
            top_campus_score = max(item[1] for item in school_matches)
            if not top_campus_score:
                # Validate an unregistered campus label even for a school
                # that currently has only one configured campus.  An explicit
                # school-scoped alias still has a campus score and is allowed.
                requested_school, requested_campus, _ = self._request_identity(query)
                if requested_campus != requested_school:
                    # The school is known, but the requested campus is not.
                    # Let geocoding resolve it instead of silently selecting
                    # the school's only registered campus.
                    return None
                if len(school_matches) > 1:
                    # Never pick the first campus merely because dictionary
                    # order is stable; geocoding provides a broad school scope.
                    return None
            candidates = (
                [item for item in school_matches if item[1] == top_campus_score]
                if top_campus_score else school_matches
            )
        else:
            # A bare "本部/主校区" must not match a generic registry alias
            # without any school context.
            _request_identity(query)
            top_campus_score = max(item[1] for item in matches)
            candidates = [item for item in matches if item[1] == top_campus_score]
        _, _, campus, school = max(candidates, key=lambda item: (item[0], item[1]))
        # 采集半径是"覆盖校界"的圆，可信半径只是兜底；页面上应显示实际搜索范围。
        try:
            radius = covering_radius_m(campus)
        except (TypeError, ValueError):
            radius = float(campus.get("trustRadiusM", DEFAULT_DISCOVERY_RADIUS_M))
        return {
            "school": {"id": campus["school"], "name": school.get("name", campus["school"]),
                       "enName": school.get("enName", ""), "aliases": school.get("aliases", [])},
            "campus": {key: campus[key] for key in (
                "name", "slug", "aliases", "center", "trustRadiusM", "waterName",
                "coordinateSystem", "boundary", "boundarySource", "boundaryConfidence",
            )
                       if key in campus},
            "discovery": {
                "method": "local_config",
                "query": query,
                "cleanedQuery": _clean_request(query),
                "resolvedSchool": school.get("name", campus["school"]),
                "resolvedCampus": campus.get("name", ""),
                "centerSource": "local_config",
                "center": campus.get("center"),
                "radiusMeters": radius,
                "radiusSource": "campus_config",
            },
        }

    def _geocode(self, query: str) -> dict:
        school_name, campus_name, normalized_query = self._request_identity(query)
        if not self.api_key:
            raise ValueError("unknown campus; AMAP_WEB_SERVICE_KEY is required for geocoding")
        campus_label = "" if campus_name == school_name else campus_name
        geocode_address = school_name if not campus_label else f"{school_name}{campus_label}"
        radius_m, radius_source = _parse_radius(query)
        if radius_m is None:
            radius_m, radius_source = _default_discovery_radius(), "default"
        warnings: list[str] = []

        payload = self.request_json({
            "key": self.api_key,
            "address": geocode_address,
            "output": "json",
        })
        geocodes = payload.get("geocodes") or []
        if not geocodes:
            raise ValueError("could not resolve a campus center from the request")
        item = geocodes[0]
        geocode_center = _finite_location(item.get("location"))
        if not geocode_center:
            raise ValueError("geocoding result has no valid coordinates")
        address = str(item.get("formatted_address") or normalized_query)

        # POI 精确点位是校区中心的首选校正项；地理编码只作为地址与兜底来源。
        poi = self._poi_center(school_name, campus_label)

        school_ok = _address_contains(address, school_name)
        if not school_ok:
            # 高德地理编码会把不存在的校区落到同名行政区，例如
            # "北京大学海淀校区" → "北京市海淀区海淀校区"（离北大本部约 5km）。
            # 这时退回"只编码校名"，用校本部中心并给出明确提示。
            try:
                fallback_payload = self.request_json({
                    "key": self.api_key, "address": school_name, "output": "json",
                })
                fallback_item = (fallback_payload.get("geocodes") or [{}])[0]
                fallback_center = _finite_location(fallback_item.get("location"))
                fallback_address = str(fallback_item.get("formatted_address") or "")
                if fallback_center and _address_contains(fallback_address, school_name):
                    geocode_center = fallback_center
                    address = fallback_address
                    school_ok = True
                    if not campus_label:
                        warnings.append("地理编码结果未包含校名，已按校本部中心校正")
            except (RuntimeError, requests.RequestException, OSError) as exc:
                logger.warning("AMap school geocode fallback failed: %s", type(exc).__name__)
        if not school_ok and not campus_label:
            warnings.append("地理编码结果未包含校名，中心坐标请人工核对")

        # 校名对不上时，地址里出现"海淀校区"也不足以证明这是北大的校区。
        campus_ok = ((not campus_label) or _address_contains(address, campus_label)) and school_ok
        if poi and poi.get("fullCampus"):
            campus_ok = True

        center = geocode_center
        center_source = "amap_geocode"
        if poi is not None:
            poi_center = poi["center"]
            drift = distance_m(poi_center, geocode_center)
            # 只有"校区级 POI"或"地理编码不可信"时才用 POI 覆盖。
            # 否则"上海交通大学徐汇校区"会被仅匹配到校本部的 POI 拉到闵行。
            poi_is_campus_level = bool(poi.get("fullCampus")) or not campus_label
            if (not school_ok or not campus_ok
                    or (poi_is_campus_level and drift > 8000)):
                center = poi_center
                center_source = "amap_poi"
                if poi_is_campus_level and drift > 8000:
                    warnings.append("地理编码中心与高德 POI 偏差过大，已使用 POI 中心")

        resolved_campus = campus_name
        if campus_label and not campus_ok:
            # 校区无法验证时按校本部范围采集，采集关键词不能带一个不存在的校区。
            resolved_campus = school_name
            warnings.append(f"未能定位到“{campus_label}”，已按{school_name}校本部范围采集")

        school_id = _slug(school_name, "school")
        campus_slug = _slug(resolved_campus, "campus")
        aliases = list(_SCHOOL_ALIASES_BY_OFFICIAL.get(school_name, []))
        discovery = {
            "method": "amap_geocode",
            "query": query,
            "cleanedQuery": normalized_query,
            "address": address,
            "resolvedSchool": school_name,
            "resolvedCampus": resolved_campus,
            "centerSource": center_source,
            "center": center,
            "radiusMeters": radius_m,
            "radiusSource": radius_source,
        }
        if poi is not None:
            discovery["centerPoiName"] = poi.get("name") or ""
        if warnings:
            discovery["warning"] = "；".join(dict.fromkeys(warnings))
        return {
            "school": {"id": school_id, "name": school_name, "enName": "", "aliases": aliases},
            "campus": {"name": resolved_campus, "slug": campus_slug, "aliases": [],
                       "center": center, "trustRadiusM": radius_m,
                       "coordinateSystem": "GCJ-02", "waterName": ""},
            "discovery": discovery,
        }

    def resolve(self, query: str) -> dict:
        text = str(query or "").strip()
        if not text:
            raise ValueError("query is required")
        profile = self._known(text) or self._geocode(text)
        problems = validate_profile(profile)
        if problems:
            raise ValueError("invalid discovered profile: " + "; ".join(problems))
        return profile


class CampusPlantHarvester:
    """Load verified plant points already registered for a known campus."""

    source = "campus_registry"

    def __init__(self, profile: dict, path: str | Path | None = None):
        self.profile = profile
        self.path = Path(path) if path else _ROOT / "webapp" / "backend" / "campus_pois.json"

    def collect(self) -> HarvestResult:
        campus = self.profile["campus"]["name"]
        candidates = []
        seen = set()
        for path in _registry_paths(self.profile, self.path):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError(f"{path.name} must be a JSON array")
            for item in data:
                if (not isinstance(item, dict) or item.get("category") != "plant" or
                        item.get("campus") != campus or item.get("id") in seen):
                    continue
                seen.add(item.get("id"))
                candidate = dict(item)
                candidate.update({
                    "source": self.source,
                    "confidence": 1.0,
                    "verified": True,
                    "autoApprove": True,
                    "coordinateSystem": "GCJ-02",
                    "sourceUrls": candidate.get("sourceUrls") or [],
                    "evidence": {"provider": "campus_registry", "registry": path.name},
                })
                candidates.append(candidate)
        return HarvestResult(candidates, self.source, [])


class CampusSceneHarvester:
    """Load verified scene locations already registered for the requested theme."""

    source = "scene_registry"

    def __init__(self, profile: dict, theme: str, path: str | Path | None = None):
        self.profile = profile
        self.scene = _theme_scene(theme)
        self.path = Path(path) if path else _ROOT / "webapp" / "backend" / "scene_pois.json"

    def collect(self) -> HarvestResult:
        campus = self.profile["campus"]["name"]
        candidates = []
        seen = set()
        for path in _registry_paths(self.profile, self.path):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError(f"{path.name} must be a JSON array")
            for item in data:
                if (not isinstance(item, dict) or item.get("category") != "scene" or
                        item.get("campus") != campus or self.scene not in (item.get("scenes") or []) or
                        item.get("id") in seen):
                    continue
                registry_text = " ".join(str(value or "") for value in (
                    item.get("name"), item.get("locationName"), item.get("subCategory"),
                    (item.get("meta") or {}).get("amapType"),
                ))
                if (_matches_terms(registry_text, NON_SCENIC_REGISTRY_TERMS) or
                        (self.scene == "date" and
                         _matches_terms(registry_text, DATE_UNSUITABLE_TERMS))):
                    continue
                seen.add(item.get("id"))
                candidate = dict(item)
                candidate.update({
                    "source": self.source,
                    "confidence": 1.0,
                    "verified": True,
                    "autoApprove": True,
                    "coordinateSystem": "GCJ-02",
                    "sourceUrls": candidate.get("sourceUrls") or [],
                    "evidence": {"provider": "scene_registry", "registry": path.name},
                })
                candidates.append(candidate)
        return HarvestResult(candidates, self.source, [])


class PlantSupplementHarvester(AmapPoiHarvester):
    """AMap supplement that keeps only plausible plant-related results."""

    source = "amap_plant"
    _PLANT_TERMS = PLANT_EVIDENCE_TERMS
    _EXCLUDED_TERMS = NON_CAMPUS_PLANT_TERMS

    def _collect_keyword(self, keyword: str):
        candidates, warnings = super()._collect_keyword(_scoped_keyword(self.profile, keyword))
        filtered = []
        for item in candidates:
            evidence = item.get("evidence") or {}
            haystack = " ".join(str(item.get(key) or "") for key in ("name", "subCategory"))
            haystack += " " + str(evidence.get("address") or "")
            if any(term in haystack for term in self._EXCLUDED_TERMS):
                continue
            if any(term in haystack for term in self._PLANT_TERMS) and (
                    _has_campus_evidence(item, self.profile) or
                    _has_spatial_campus_evidence(item)):
                item["category"] = "plant"
                item["source"] = self.source
                item["confidence"] = min(float(item.get("confidence", 0.72)), 0.68)
                item["evidence"] = dict(item.get("evidence") or {}, plantFilter="name/type keyword match")
                filtered.append(item)
        return filtered, warnings


class PlantWebHarvester:
    """Keep only web candidates with explicit plant-related evidence."""

    source = "public_web_plant"

    def __init__(self, urls: list[str], profile: dict | None = None,
                 cache: HarvestCache | None = None):
        self.delegate = PublicWebHarvester(urls, cache=cache)
        self.profile = profile

    def collect(self) -> HarvestResult:
        result = self.delegate.collect()
        candidates = []
        excluded = 0
        for item in result.candidates:
            evidence = item.get("evidence") or {}
            # Page-wide prose may mention plants while describing unrelated
            # lakes or buildings.  Only candidate-specific fields count as
            # evidence that this particular place is plant-related.
            haystack = " ".join(str(item.get(key) or "") for key in
                                ("name", "subCategory", "locationName"))
            haystack += " " + str(evidence.get("address") or "")
            if any(term in haystack for term in NON_CAMPUS_PLANT_TERMS):
                excluded += 1
                continue
            if not any(term in haystack for term in PLANT_EVIDENCE_TERMS):
                excluded += 1
                continue
            if self.profile is not None and not _has_campus_evidence(item, self.profile):
                excluded += 1
                continue
            candidate = dict(item)
            candidate["category"] = "plant"
            candidate["source"] = self.source
            candidate["confidence"] = min(float(candidate.get("confidence", 0.35)), 0.72)
            candidate["evidence"] = dict(evidence, plantFilter="explicit plant keyword match")
            candidates.append(candidate)
        warnings = list(result.warnings)
        if excluded:
            warnings.append(f"public web plant filter excluded {excluded} non-plant or commercial candidates")
        return HarvestResult(candidates, self.source, warnings)


class SceneSupplementHarvester(AmapPoiHarvester):
    """AMap supplement for non-plant campus places relevant to one theme."""

    source = "amap_scene"

    def __init__(self, profile: dict, theme: str, keywords: list[str], **kwargs):
        super().__init__(profile, keywords, **kwargs)
        self.theme = theme
        self.scene = _theme_scene(theme)
        self.terms = tuple(THEME_KEYWORDS.get(theme) or THEME_KEYWORDS["general"])

    def _collect_keyword(self, keyword: str):
        # Keep the raw category term for candidate matching.  The provider
        # query is campus-scoped, but that full query must not be required in
        # an ordinary POI name such as “东区体育馆”.
        raw_keyword = keyword
        candidates, warnings = super()._collect_keyword(_scoped_keyword(self.profile, raw_keyword))
        filtered = []
        for item in candidates:
            text = _candidate_text(item)
            if (_matches_terms(text, _scene_commercial_terms(self.theme)) or
                    (self.theme == "date" and not _date_candidate_is_suitable(item)) or
                    not _keyword_matches_candidate(item, raw_keyword) or
                    not (_has_campus_evidence(item, self.profile) or
                         _has_spatial_campus_evidence(item))):
                continue
            candidate = dict(item)
            candidate.update({
                "category": "scene",
                "source": self.source,
                "scenes": [self.scene],
                "confidence": min(float(item.get("confidence", 0.72)), 0.72),
                "evidence": dict(item.get("evidence") or {},
                                 sceneFilter=f"{self.theme} candidate-specific keyword match"),
            })
            filtered.append(candidate)
        return filtered, warnings


class SceneWebHarvester:
    """Keep public-web places whose own structured fields match the theme."""

    source = "public_web_scene"

    def __init__(self, urls: list[str], theme: str, profile: dict | None = None,
                 cache: HarvestCache | None = None):
        self.delegate = PublicWebHarvester(urls, cache=cache)
        self.theme = theme
        self.scene = _theme_scene(theme)
        self.profile = profile
        self.terms = tuple(THEME_KEYWORDS.get(theme) or THEME_KEYWORDS["general"])

    def collect(self) -> HarvestResult:
        result = self.delegate.collect()
        candidates = []
        excluded = 0
        for item in result.candidates:
            text = _candidate_text(item)
            if (_matches_terms(text, _scene_commercial_terms(self.theme)) or
                    (self.theme == "date" and not _date_candidate_is_suitable(item)) or
                    not _matches_terms(text, self.terms) or
                    (self.profile is not None and not _has_campus_evidence(item, self.profile))):
                excluded += 1
                continue
            candidate = dict(item)
            candidate.update({
                "category": "scene",
                "source": self.source,
                "scenes": [self.scene],
                "confidence": min(float(item.get("confidence", 0.35)), 0.72),
                "evidence": dict(item.get("evidence") or {},
                                 sceneFilter=f"{self.theme} candidate-specific keyword match"),
            })
            candidates.append(candidate)
        warnings = list(result.warnings)
        if excluded:
            warnings.append(f"public web scene filter excluded {excluded} irrelevant or commercial candidates")
        return HarvestResult(candidates, self.source, warnings)


class SearchEvidenceEnricher:
    """Attach bounded search provenance to candidates without inventing coordinates.

    A URL match annotates an already-fetched web candidate.  A sufficiently
    distinctive exact place-name match is retained only as a non-scoring lead
    on an AMap candidate while preserving the provider's GCJ-02 coordinates.
    Search data never auto-approves a candidate or replaces its primary source.
    """

    def __init__(self, delegate: Harvester, search_results: list[dict],
                 profile: dict | None = None):
        self.delegate = delegate
        self.source = getattr(delegate, "source", delegate.__class__.__name__)
        self.search_results = [dict(item) for item in search_results if isinstance(item, dict)]
        school = (profile or {}).get("school") or {}
        identity_values = [
            school.get("name"), school.get("enName"), *(school.get("aliases") or []),
        ]
        self.identity_terms = {
            term for value in identity_values
            if len(term := _matchable_name(str(value or ""))) >= 2
        }

    @staticmethod
    def _urls(candidate: dict) -> set[str]:
        values = candidate.get("sourceUrls") or []
        if isinstance(values, str):
            values = [values]
        return {str(value).split("#", 1)[0] for value in values if str(value).strip()}

    def _matches(self, candidate: dict) -> list[dict]:
        candidate_urls = self._urls(candidate)
        name = str(candidate.get("name") or candidate.get("locationName") or "").strip()
        normalized_name = _matchable_name(name)
        distinctive_name = (
            len(normalized_name) >= 3 and name not in _GENERIC_SEARCH_MATCH_NAMES
        )
        matches = []
        for hit in self.search_results:
            url = str(hit.get("url") or "").split("#", 1)[0]
            text = _matchable_name(" ".join((
                str(hit.get("title") or ""), str(hit.get("snippet") or ""),
            )))
            campus_match = not self.identity_terms or any(
                term in text for term in self.identity_terms
            )
            if (url and url in candidate_urls) or (
                    distinctive_name and campus_match and normalized_name in text):
                matches.append(hit)
        return matches[:3]

    def collect(self) -> HarvestResult:
        result = self.delegate.collect()
        enriched = []
        for original in result.candidates:
            matches = self._matches(original)
            if not matches:
                enriched.append(original)
                continue
            candidate = dict(original)
            source_urls = candidate.get("sourceUrls") or []
            if isinstance(source_urls, str):
                source_urls = [source_urls]
            candidate_urls = {
                str(value).split("#", 1)[0]
                for value in source_urls if str(value).strip()
            }
            direct_page_matches = [
                hit for hit in matches
                if str(hit.get("url") or "").split("#", 1)[0] in candidate_urls
            ]
            evidence = candidate.get("evidence") or {}
            if not isinstance(evidence, dict):
                evidence = {}
            candidate["evidence"] = {
                **evidence,
                # A search snippet is a lead, not proof that the destination
                # page was fetched or that it is independent of AMap.  Only
                # an exact URL match on an already-fetched web candidate is
                # called evidence; name-only AMap matches remain non-scoring.
                "searchEvidence" if direct_page_matches else "searchLeads": [{
                    key: hit[key]
                    for key in ("provider", "query", "rank", "url", "title", "snippet")
                    if key in hit
                } for hit in (direct_page_matches or matches)],
            }
            enriched.append(candidate)
        return HarvestResult(enriched, result.source, list(result.warnings))


class DiscoveryOrchestrator:
    """Turn one natural-language request into a reproducible harvest plan."""

    def __init__(self, resolver: CampusProfileResolver | None = None,
                 *, cache: HarvestCache | None = None,
                 web_search: WebSearchClient | None = None,
                 web_search_unavailable: str | None = None,
                 web_search_provider: str | None = None):
        self.resolver = resolver or CampusProfileResolver()
        self.cache = cache
        self.web_search = web_search
        self.web_search_unavailable = str(web_search_unavailable or "").strip()
        self.web_search_provider = str(web_search_provider or "").strip().lower() or None

    def plan(self, query: str, *, keywords: list[str] | None = None,
             web_urls: list[str] | None = None, include_amap: bool = True,
             include_web: bool = True, auto_search: bool = False) -> DiscoveryPlan:
        profile = self.resolver.resolve(query)
        # 主题与"用户显式写的类别"都要从去掉校名后的意图文本里读，
        # 否则"湖北大学"里的"湖"会被当成湖泊。
        intent_text = _strip_identity_terms(_request_intent_text(query), profile)
        theme = infer_theme(intent_text)
        explicit_keywords = _requested_keywords(intent_text)
        plant_keywords = list(THEME_PLANT_KEYWORDS.get(theme) or THEME_PLANT_KEYWORDS["general"])
        selected = list(explicit_keywords)
        selected.extend(plant_keywords)
        if theme != "flower":
            selected.extend(THEME_KEYWORDS.get(theme) or THEME_KEYWORDS["general"])
        selected.extend(str(item).strip() for item in (keywords or []) if str(item).strip())
        # 用户显式类别排在最前面，不能被默认词挤出 28 个上限。
        selected = list(dict.fromkeys(selected))[:28]
        manual_urls = list(dict.fromkeys(
            str(item).strip() for item in (web_urls or []) if str(item).strip()
        ))[:10]
        urls = list(manual_urls)
        sources = []
        warnings = []
        discovery_warning = str((profile.get("discovery") or {}).get("warning") or "").strip()
        if discovery_warning:
            warnings.append(discovery_warning)
        search_queries: list[str] = []
        search_results: list[dict] = []
        provider = getattr(self.web_search, "provider", None) or self.web_search_provider
        web_search_audit = {
            "schemaVersion": 1,
            "requested": bool(include_web and auto_search),
            "provider": provider,
            "status": "not_requested",
            "queries": [],
            "results": [],
            "acceptedUrlCount": 0,
        }
        if include_web and auto_search:
            search_queries = _search_queries(profile, theme, selected)
            web_search_audit["queries"] = search_queries
            if self.web_search is None:
                web_search_audit["status"] = "unconfigured"
                warning = self.web_search_unavailable or (
                    "automatic public-web search is not configured; set "
                    "WEB_SEARCH_PROVIDER and its API key"
                )
                warnings.append(warning)
            else:
                try:
                    outcome = self.web_search.search(search_queries)
                    search_results = [_search_hit_dict(hit) for hit in outcome.hits]
                    outcome_queries = [str(value) for value in outcome.queries if str(value).strip()]
                    if outcome_queries:
                        search_queries = outcome_queries
                        web_search_audit["queries"] = search_queries
                    warnings.extend(str(value) for value in outcome.warnings if str(value).strip())
                    automatic_urls = [str(item.get("url") or "") for item in search_results]
                    urls = list(dict.fromkeys([*manual_urls, *automatic_urls]))[:10]
                    accepted = set(urls)
                    accepted_results = [item for item in search_results if item.get("url") in accepted]
                    search_results = accepted_results
                    web_search_audit.update({
                        "provider": getattr(outcome, "provider", provider),
                        "status": (
                            "completed" if search_results else
                            "failed" if outcome.warnings else "empty"
                        ),
                        "acceptedUrlCount": len(search_results),
                        "results": [{
                            key: item[key]
                            for key in ("url", "title", "query", "rank") if key in item
                        } for item in search_results],
                    })
                    if search_results:
                        sources.append("web_search")
                except (requests.RequestException, OSError, RuntimeError) as exc:
                    web_search_audit["status"] = "failed"
                    warnings.append(
                        f"automatic public-web search failed ({type(exc).__name__}); "
                        "manual URLs, campus registry and AMap sources will still run"
                    )
        if include_amap:
            sources.append("amap")
        if include_web and urls:
            sources.append("public_web")
        if include_web and not urls:
            warnings.append(
                "no public web URLs were discovered or supplied; registry and AMap harvesting will still run"
            )
        return DiscoveryPlan(
            query=str(query), profile=profile, theme=theme, keywords=selected,
            web_urls=urls, sources=sources, warnings=warnings,
            targetCategory="mixed", searchQueries=search_queries,
            searchResults=search_results, webSearch=web_search_audit,
        )

    def harvesters(self, plan: DiscoveryPlan, *, include_amap: bool = True,
                   include_web: bool = True) -> list[Harvester]:
        result: list[Harvester] = []
        if plan.profile.get("discovery", {}).get("method") == "local_config":
            result.append(CampusPlantHarvester(plan.profile))
            result.append(CampusSceneHarvester(plan.profile, plan.theme))
        if include_amap:
            plant_keywords = [word for word in plan.keywords if word in
                              (THEME_PLANT_KEYWORDS.get(plan.theme) or THEME_PLANT_KEYWORDS["general"])]
            plant_harvester: Harvester = PlantSupplementHarvester(
                plan.profile, plant_keywords, cache=self.cache,
            )
            if plan.searchResults:
                plant_harvester = SearchEvidenceEnricher(
                    plant_harvester, plan.searchResults, plan.profile,
                )
            result.append(plant_harvester)
            if plan.theme != "flower":
                scene_keywords = [word for word in plan.keywords if word in
                                  (THEME_KEYWORDS.get(plan.theme) or THEME_KEYWORDS["general"])]
                scene_harvester: Harvester = SceneSupplementHarvester(
                    plan.profile, plan.theme, scene_keywords, cache=self.cache,
                )
                if plan.searchResults:
                    scene_harvester = SearchEvidenceEnricher(
                        scene_harvester, plan.searchResults, plan.profile,
                    )
                result.append(scene_harvester)
            # 网格 × 类型扫描：关键词召回只能命中名称里含主题词的点，院系、楼栋、
            # 校医院等永远采不到。这里按 POI 类型码把范围扫一遍补齐。
            # 请求量约为关键词召回的 3–5 倍，离线排查可用 CAMPUS_GRID_SCAN=0 关闭。
            if str(os.getenv("CAMPUS_GRID_SCAN", "1")).strip().lower() not in {"0", "false", "off"}:
                result.append(AmapGridTypeHarvester(plan.profile, cache=self.cache))
        if include_web and plan.web_urls:
            plant_web: Harvester = PlantWebHarvester(
                plan.web_urls, plan.profile, cache=self.cache,
            )
            if plan.searchResults:
                plant_web = SearchEvidenceEnricher(
                    plant_web, plan.searchResults, plan.profile,
                )
            result.append(plant_web)
            if plan.theme != "flower":
                scene_web: Harvester = SceneWebHarvester(
                    plan.web_urls, plan.theme, plan.profile, cache=self.cache,
                )
                if plan.searchResults:
                    scene_web = SearchEvidenceEnricher(
                        scene_web, plan.searchResults, plan.profile,
                    )
                result.append(scene_web)
        if not result:
            raise ValueError("discovery plan has no enabled harvest source")
        return result
