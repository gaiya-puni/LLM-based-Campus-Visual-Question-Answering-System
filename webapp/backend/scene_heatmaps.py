"""Isolated, offline-first SAKDE-campus implementation (SenseMap IV-D/E).

Only plant POIs are used in phase two. Existing rule ranking is not imported or
modified. Analysis zones are metric grid cells, NOT official TAZs; the raster
mask is a POI coverage hull, NOT a campus boundary or walkability assessment.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np

ALGORITHM_VERSION = "sakde-campus-8-independent"
SCENE_IDS = ("flower_viewing", "photo", "walk", "date")
# 每个场景在每个校区展示的推荐点数（Top1..TopN）。该上限同时是 validate_payload 的
# 结构校验硬边界，防止被篡改的缓存塞入过多点位。
MAX_PLACES = 5
# 启用"地名先验 + 非季节类别先验"的场景：
#   walk/date 靠设施与路径地名区分，photo 靠地标（图书馆、校门、河畔、桥）区分。
# flower_viewing 不进入此分支，输出保持不变。
PLACE_PRIOR_SCENES = ("walk", "date", "photo")
DISCLAIMER = (
    "基于植物资料与点位分布的相对适宜度，不是实时花期、人流或噪声测量。"
    "覆盖范围由数据推定，非正式校界；标记为真实POI参考位置，通行情况需现场确认。"
)

# ---------------------------------------------------------------------------
# 散步 / 约会场景增强（sakde-campus-3）：地名先验 + 非季节类别先验
#
# 背景：赏花用"花期季节"、拍照用"树形/秋色叶/古树"做区分，但散步与约会此前没有
# 自己的区分维度——两者 categories 都只有 plant、运行时 season=None，导致先验系数
# 全退化为 1.0，输出几乎一致（相关系数 0.99+）。这里补两个只作用于 walk/date 的
# 信号。各场景必须独立计算，不能用其他场景的热点反向压低本场景：
#   1. 地名先验（point-level）——以真实 locationName 为准，散步偏向步道/运动场/
#      体育馆/共青场，约会偏向丽娃河/樱桃河/河畔/图书馆/亭。不新增也不伪造坐标。
#   2. 场景类别先验（class-level，不依赖季节）——散步看遮阴乔木，约会看观花芳香。
# 赏花 / 拍照不进入这些分支，数值输出不受影响。
# ---------------------------------------------------------------------------

# 地名先验：正则 -> 乘性权重（多条命中连乘）。此处为兜底默认值，可被
# heatmap_config.json 的 placePriors 覆盖。
DEFAULT_PLACE_PRIORS = {
    "walk": {
        r"共青": 2.4,
        r"运动场|体育场": 2.2,
        r"体育馆": 2.2,
        r"操场": 2.0,
        r"步道|路|道": 1.6,
        r"草坪|绿地|园": 1.4,
        r"门": 1.1,
        r"河|湖|池|桥": 0.7,
    },
    "date": {
        r"丽娃河|樱桃河": 2.6,
        r"河畔|河边|滨": 2.2,
        r"图书馆": 1.8,
        r"亭|廊": 1.8,
        r"草坪|绿地": 1.6,
        r"花园|园": 1.5,
        r"运动场|体育场|体育馆|操场": 0.5,
        r"路|道": 0.9,
    },
    # 拍照强调"可识别的地标"：图书馆、校门、水岸、桥；纯教学/实验楼取景价值低。
    "photo": {
        r"图书馆": 2.4,
        r"校门|大门|西门|东门|南门|北门|正门": 2.0,
        r"丽娃河|樱桃河": 2.0,
        r"河畔|河边|滨": 1.8,
        r"桥": 1.9,
        r"广场|大草坪|草坪|绿地": 1.3,
        r"花园|园|花": 1.3,
        r"办公楼|教学楼|学院|实验|大楼": 0.85,
    },
}

# 场景类别先验规则：(谓词, 乘性权重)。谓词支持 habit:、rank:、bloom:、height:>=。
# 同样为兜底默认值，可被 heatmap_config.json 的 sceneClassPriors 覆盖。
DEFAULT_SCENE_CLASS_PRIORS = {
    "walk": [
        ("habit:乔木", 1.30),
        ("habit:灌木", 0.80),
        ("habit:草本", 0.80),
        ("rank:high", 0.70),
        ("rank:low", 1.15),
        # 不再额外偏好"参天大树"：那会与拍照场景抢同一批密林，改由步道/草坪/开阔地名主导。
        ("height:>=15", 1.00),
    ],
    "date": [
        ("habit:乔木", 1.10),
        ("habit:藤本", 1.20),
        ("habit:灌木", 0.95),
        ("rank:high", 1.50),
        ("rank:low", 0.70),
        ("bloom:any", 1.20),
    ],
    # 拍照看"取景价值"：古树/参天大树是主体，藤本花架与观花植物次之，常绿行道树降权。
    "photo": [
        ("habit:乔木", 1.15),
        ("habit:藤本", 1.25),
        ("rank:high", 1.15),
        ("rank:low", 0.85),
        ("height:>=15", 1.20),
    ],
}


def scene_point_weights(pois: list[dict], scene: str, config: dict) -> np.ndarray | None:
    """散步/约会按 locationName 计算逐点地名先验；其他场景返回 None（保持原样）。"""
    table = (config.get("placePriors") or DEFAULT_PLACE_PRIORS).get(scene)
    if scene not in PLACE_PRIOR_SCENES or not table:
        return None
    weights = np.ones(len(pois), dtype=float)
    for index, poi in enumerate(pois):
        label = poi.get("locationName") or ""
        factor = 1.0
        for pattern, value in table.items():
            if re.search(pattern, label):
                factor *= float(value)
        weights[index] = min(max(factor, 0.05), 8.0)
    return weights


def scene_class_priors(names: list[str], plant_priors: dict | None, scene: str,
                       config: dict | None = None,
                       class_weights: dict | None = None) -> dict[str, float]:
    """散步/约会/拍照的非季节类别先验；其他场景全部 1.0（不改变赏花）。"""
    priors = {name: 1.0 for name in names}
    if scene not in PLACE_PRIOR_SCENES:
        return priors
    raw = (config or {}).get("sceneClassPriors") or DEFAULT_SCENE_CLASS_PRIORS
    rules = [(str(item[0]).split(":", 1)[0], str(item[0]).split(":", 1)[1], float(item[1]))
             for item in raw.get(scene, [])]
    for name in names:
        # 非植物场景 POI（座椅/共青场等）按其自带权重参与，不适用植物谓词。
        weight = float((class_weights or {}).get(name, 1.0))
        info = (plant_priors or {}).get(name)
        if info:
            habit = info.get("habit") or ""
            rank = info.get("rank")
            height = info.get("mean_height")
            blooms = info.get("bloom_seasons") or set()
            for kind, operand, value in rules:
                if kind == "habit":
                    hit = habit == operand
                elif kind == "rank":
                    hit = rank == operand
                elif kind == "bloom":
                    hit = bool(blooms)
                elif kind == "height" and operand.startswith(">="):
                    hit = height is not None and float(height) >= float(operand[2:])
                else:
                    hit = False
                if hit:
                    weight *= value
        priors[name] = min(max(weight, 0.25), 3.0)
    return priors


# ---------------------------------------------------------------------------
# 赏花场景增强：花期季节感知 + 观赏价值先验
#
# 背景：四类场景热力图过于相近，根因是场景语料与类别语料都缺少“花期”与
# “美观度”这两个区分维度。这里为“赏花”场景引入两个信号：
#   1. 花期季节感知 —— 从植物模板文本提取花期月份，构建时按季节生成多套
#      缓存，运行时按当前月份选取对应季节的缓存。
#   2. 观赏价值先验 —— 硬编码高观赏价值（观花/芳香/艳丽）植物名单，在融合
#      阶段加权；同时把“无花期、无观赏词”的常绿行道树视为低观赏价值降权。
# ---------------------------------------------------------------------------

# 高观赏价值观花植物（赏花场景重点加权）。以真实模板名称为准，宁可少列。
ORNAMENTAL_HIGH = {
    # 樱花 / 桃李杏梅类（早春观花主力）
    "东京樱花", "日本晚樱", "大叶早樱", "山樱花", "尾叶樱桃", "樱桃", "梅", "杏", "李",
    "垂丝海棠", "木瓜", "皱皮木瓜",
    # 玉兰 / 含笑类
    "玉兰", "紫玉兰", "二乔玉兰", "飞黄玉兰", "荷花玉兰", "乐昌含笑", "含笑花",
    # 蔷薇 / 月季类
    "月季花", "木香花", "七姊妹", "棣棠花", "榆叶梅",
    # 观花灌木
    "山茶", "紫薇", "杜鹃", "绣球", "牡丹", "蜡梅", "栀子", "狭叶栀子",
    "木芙蓉", "木槿", "紫丁香", "白丁香", "紫荆", "结香", "金边六月雪", "六月雪",
    # 藤本 / 草本 / 水生
    "紫藤", "凌霄", "美人蕉", "莲", "夹竹桃", "再力花", "密蒙花",
}

# 季节 → 月份（1-12）。用于花期季节归属与运行时季节选择。
SEASON_MONTHS = {
    "spring": {3, 4, 5},
    "summer": {6, 7, 8},
    "autumn": {9, 10, 11},
    "winter": {12, 1, 2},
}
SEASON_ORDER = ("spring", "summer", "autumn", "winter")
SEASON_LABELS = {"spring": "春季", "summer": "夏季", "autumn": "秋季", "winter": "冬季"}

# 花期文本提取正则：捕获“花期3-4月 / 花期2月下旬 / 花期冬春季”中的关键片段。
_BLOOM_FRAGMENT_PATTERN = re.compile(r"花期\s*([^。；，,;\n]{0,12}?)(?:月|季)")
_SEASON_CHAR = {"春": "spring", "夏": "summer", "秋": "autumn", "冬": "winter"}


def month_to_season(month: int) -> str:
    """1-12 月映射到季节（含跨年冬季 12/1/2）。"""
    month = ((month - 1) % 12) + 1
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    return "spring"


def current_season(now=None) -> str:
    """返回当前月份对应的季节标签，供运行时选取对应缓存。"""
    if now is None:
        from datetime import datetime
        now = datetime.now()
    return month_to_season(now.month)


def extract_bloom_months(template: dict) -> set[int]:
    """从植物模板的形态/文化文本中提取花期月份集合（1-12）。"""
    text = " ".join(block.get("content") or "" for block in template.get("blocks", []))
    months: set[int] = set()
    for match in _BLOOM_FRAGMENT_PATTERN.finditer(text):
        fragment = match.group(1)
        digits = [int(d) for d in re.findall(r"[0-9]+", fragment)]
        months.update(d for d in digits if 1 <= d <= 12)
        for char, season in _SEASON_CHAR.items():
            if char in fragment:
                months.update(SEASON_MONTHS[season])
    return months


def bloom_seasons(template: dict) -> set[str]:
    """返回该植物开花的季节集合。"""
    return {month_to_season(m) for m in extract_bloom_months(template)}


def ornamental_rank(name: str, habit: str, template: dict) -> str:
    """观赏价值分级：high（观花主力）/ low（常绿行道树）/ mid（其余）。"""
    if name in ORNAMENTAL_HIGH:
        return "high"
    text = " ".join(block.get("content") or "" for block in template.get("blocks", []))
    has_bloom = bool(extract_bloom_months(template))
    has_ornament = any(
        keyword in text
        for keyword in ("观赏价值", "花色", "艳丽", "满树", "盛开", "芳香", "花香", "秋色", "彩叶", "观花", "观叶")
    )
    # 乔木且无花期、无观赏词 → 常绿/遮阴行道树，赏花场景低价值
    if habit == "乔木" and not has_bloom and not has_ornament:
        return "low"
    return "mid"


AUTUMN_COLOR_KEYWORDS = ("秋色", "彩叶", "秋叶", "红叶", "金黄", "变色", "叶色")


def has_autumn_color(template: dict) -> bool:
    """判断植物是否具秋色叶/变色叶观赏价值（拍照场景秋季加权依据）。"""
    text = " ".join(block.get("content") or "" for block in template.get("blocks", []))
    return any(keyword in text for keyword in AUTUMN_COLOR_KEYWORDS)


def _to_float(value) -> float | None:
    """从形如 '8'、'5.5'、'3×3' 的字符串里取首个数值，解析失败返回 None。"""
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def build_plant_priors(templates: list[dict], pois: list[dict] | None = None) -> dict[str, dict]:
    """为每个植物类别预计算观赏价值、花期季节、秋色叶、平均树高与平均胸径，供场景融合加权。

    平均树高来自 POI meta 的 height 字段（覆盖率约 1/3），平均胸径来自 radius
    字段（覆盖率约 3/8），均作为拍照场景的"参天大树 / 古树"软信号；无值时为
    None，调用方回退到中性权重，不影响无值植物。
    """
    # 从 POI 按 subCategory 聚合树高、胸径样本（audit 后的植物 POI）
    heights: dict[str, list[float]] = {}
    radii: dict[str, list[float]] = {}
    for poi in pois or []:
        name = poi.get("subCategory")
        if not name:
            continue
        meta = poi.get("meta") or {}
        value = _to_float(meta.get("height"))
        if value is not None:
            heights.setdefault(name, []).append(value)
        radius = _to_float(meta.get("radius"))
        if radius is not None:
            radii.setdefault(name, []).append(radius)

    priors = {}
    for template in templates:
        name = template.get("name")
        if not name:
            continue
        habit = template.get("habit") or ""
        hs = heights.get(name, [])
        rs = radii.get(name, [])
        priors[name] = {
            "habit": habit,
            "bloom_seasons": bloom_seasons(template),
            "rank": ornamental_rank(name, habit, template),
            "autumn_color": has_autumn_color(template),
            "mean_height": round(sum(hs) / len(hs), 2) if hs else None,
            "mean_radius": round(sum(rs) / len(rs), 2) if rs else None,
            "size_sample": max(len(hs), len(rs)),
        }
    return priors


def _seasonal_priors(names: list[str], plant_priors: dict, season, scene=None) -> dict[str, float]:
    """场景季节先验系数；season 为 None 时退化为全 1.0（不影响原逻辑）。

    让两个场景由完全不同的数据维度驱动，避免 top 热区雷同：

    赏花（flower_viewing）—— 花期驱动：只认"当季正在开花"的观花植物。
    观花主力当季开花强加权，非花期与无花常绿乔木被大幅压制，热区随花期移动。

    拍照（photo）—— 树形 + 景观驱动：弱化"是否当季开花"，转而看植物的
    "取景价值"——秋色叶、高大乔木（林荫/参天树取景主体）、藤本（花架）。
    这些信号与花期无关，因此与赏花场景的排序来源天然不同。
    """
    priors = {}
    for name in names:
        if not season:
            priors[name] = 1.0
            continue
        info = (plant_priors or {}).get(name)
        if not info:
            priors[name] = 1.0
            continue
        rank = info.get("rank")
        blooms = info.get("bloom_seasons") or set()
        autumn = info.get("autumn_color")
        habit = info.get("habit") or ""
        mean_height = info.get("mean_height")
        mean_radius = info.get("mean_radius")
        if scene == "photo":
            # 拍照看"景"：秋色叶秋季最出片；古树/参天大树/藤本是稳定取景主体。
            # 平均胸径(radius)≥90cm 视为古树级，是拍照最强稳定信号之一。
            if autumn and season == "autumn":
                priors[name] = 2.2  # 秋色叶秋季强加权
            elif habit == "乔木" and mean_radius and mean_radius >= 90:
                priors[name] = 2.0  # 古树（胸径≥90cm，top25%）四季出片
            elif habit == "乔木" and mean_height and mean_height >= 15:
                priors[name] = 1.7  # 参天大树（≥15米）是林荫取景主体
            elif habit == "藤本":
                priors[name] = 1.5  # 藤本花架（紫藤、凌霄等）四季出片
            elif habit == "乔木":
                priors[name] = 1.4  # 普通高大乔木
            elif autumn:
                priors[name] = 1.1  # 非秋季的秋色叶植物仅轻微加权
            elif rank == "high":
                priors[name] = 1.3 if season in blooms else 1.0
            else:
                priors[name] = 1.0
        else:
            # 赏花看"当下开不开花"：花期成为近乎二值的硬约束，完全不看树形/胸径
            if rank == "high":
                priors[name] = 3.0 if season in blooms else 0.03
            elif rank == "low":
                priors[name] = 0.02
            else:
                priors[name] = 0.25
    return priors



class HeatmapUnavailable(RuntimeError):
    pass


def load_config(base: Path) -> dict:
    config = json.loads((base / "heatmap_config.json").read_text(encoding="utf-8"))
    for key in ("cellMeters", "zoneMeters", "minInfluenceMeters", "maxInfluenceMeters",
                "supportMeters", "peakSeparationMeters", "anchorRadiusMeters", "fixedBandwidthMeters"):
        if not isinstance(config[key], (int, float)) or not 0 < config[key] <= 5000:
            raise ValueError(f"invalid heatmap configuration: {key}")
    if config["cellMeters"] < 5 or config["minInfluenceMeters"] > config["maxInfluenceMeters"]:
        raise ValueError("invalid grid resolution/influence range")
    if not 0 < config["positiveRatio"] <= 1:
        raise ValueError("positiveRatio must be in (0, 1]")
    if config["negativeContribution"] not in ("clip", "paper_signed"):
        raise ValueError("unknown negativeContribution mode")
    share = config.get("facilityShare", 0)
    if not isinstance(share, (int, float)) or not 0 <= share <= 1:
        raise ValueError("invalid facilityShare")
    limit = config.get("placeLimit", MAX_PLACES)
    if type(limit) is not int or not 1 <= limit <= MAX_PLACES:
        raise ValueError("invalid placeLimit")
    return config


def source_paths(base: Path) -> list[Path]:
    paths = [base / "heatmap_config.json", base / "scene_profiles.json",
             base / "campus_pois.json"]
    # 散步/约会专用的非植物 POI（座椅、共青场、凉亭等）为可选文件；
    # 存在时纳入哈希，改变内容必须重建缓存。
    scene_pois = base / "scene_pois.json"
    if scene_pois.exists():
        paths.append(scene_pois)
    paths.extend([base / "../../data/all_templates.json", base / "scene_heatmaps.py",
                  base / "build_scene_heatmaps.py", base / "semantic_retrieval.py"])
    return paths


# 指纹要读取并哈希全部语料（campus_pois.json 约 4MB），而 /api/heatmaps 每次
# 请求都会调用它做过期校验。按「路径 + mtime + size」缓存结果：语料没变就直接
# 复用哈希；语料被改动时签名随之变化并自动重算，因此过期检测语义保持不变。
_fingerprint_cache: dict = {}
_fingerprint_lock = threading.Lock()


def fingerprint(base: Path, model: str) -> str:
    paths = source_paths(base)
    try:
        signature = (
            str(base),
            model,
            tuple(
                (path.name, path.stat().st_mtime_ns, path.stat().st_size)
                for path in paths
            ),
        )
    except OSError:
        signature = None
    if signature is not None:
        with _fingerprint_lock:
            cached = _fingerprint_cache.get(signature)
        if cached is not None:
            return cached
    digest = hashlib.sha256((ALGORITHM_VERSION + model).encode("utf-8"))
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    result = digest.hexdigest()
    if signature is not None:
        with _fingerprint_lock:
            _fingerprint_cache.clear()
            _fingerprint_cache[signature] = result
    return result


def to_meters(coords, center) -> np.ndarray:
    scale = np.array([111320 * math.cos(math.radians(center[1])), 110540])
    return (np.asarray(coords, dtype=float) - center) * scale


def to_lnglat(points, center) -> np.ndarray:
    scale = np.array([111320 * math.cos(math.radians(center[1])), 110540])
    return np.asarray(points, dtype=float) / scale + center


def audit_pois(raw: list[dict], config: dict) -> tuple[list[dict], dict]:
    # 白名单默认只有 plant；scene_pois.json 里的非植物 POI 需要配置显式放开。
    allowed = set(config.get("allowedCategories") or ["plant"])
    accepted, seen_ids, seen_points, rejected = [], set(), set(), Counter()
    for poi in raw:
        if poi.get("category") not in allowed:
            continue
        campus = config["campuses"].get(poi.get("campus"))
        try:
            coords = [float(poi["lng"]), float(poi["lat"])]
            valid = campus and all(math.isfinite(v) for v in coords)
            valid = valid and np.linalg.norm(to_meters(coords, campus["center"])) <= campus["auditRadiusMeters"]
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid or not poi.get("id") or not poi.get("subCategory"):
            rejected["invalid_coordinate_campus_or_class"] += 1
            continue
        point_key = (poi["campus"], poi["subCategory"], *[round(v, 7) for v in coords])
        if poi["id"] in seen_ids or point_key in seen_points:
            rejected["duplicate_id_or_same_class_coordinate"] += 1
            continue
        label = poi.get("locationName") or ""
        if (poi["campus"] == "闵行" and "丽娃河" in label) or (poi["campus"] == "普陀" and "樱桃河" in label):
            rejected["cross_campus_landmark"] += 1
            continue
        seen_ids.add(poi["id"])
        seen_points.add(point_key)
        accepted.append(poi)
    return sorted(accepted, key=lambda p: p["id"]), {
        "accepted": len(accepted), "excluded": dict(rejected),
        "byCampus": dict(Counter(p["campus"] for p in accepted)),
        "classes": len({p["subCategory"] for p in accepted}),
    }


def class_documents(pois: list[dict], templates: list[dict]) -> dict[str, str]:
    """Use botanical descriptions, not personal messages/addresses as class corpus."""
    by_name = {t["name"]: t for t in templates}
    documents = {}
    for poi in pois:
        name = poi["subCategory"]
        if name in documents:
            continue
        if poi.get("category") != "plant":
            # 非植物场景 POI（座椅/共青场/凉亭等）：直接用其语料字段构建类别文档。
            parts = [name, str(poi.get("category") or ""), str(poi.get("name") or ""),
                     str(poi.get("locationName") or ""), str(poi.get("text") or "")]
            parts.extend(str(tag) for tag in (poi.get("tags") or []))
            documents[name] = "。".join(p for p in parts if p)
            continue
        template = by_name.get(name, {})
        meta = poi.get("meta") or {}
        habit = template.get("habit") or meta.get("habit", "")
        parts = [name, template.get("branch") or meta.get("branch", ""), habit]
        for block in template.get("blocks", []):
            if block.get("title") in ("形态特征", "植物文化", "生长习性"):
                parts.append(str(block.get("content") or "")[:450])
        # 赏花增强：注入花期与观赏价值标签，让 BGE 能区分观花/非观花植物
        seasons = sorted(bloom_seasons(template), key=SEASON_ORDER.index)
        if seasons:
            parts.append("花期：" + "、".join(seasons))
        rank = ornamental_rank(name, habit, template)
        if rank == "high":
            parts.append("观赏价值高，观花或芳香植物")
        elif rank == "low":
            parts.append("常绿行道树，遮阴为主，无明显观花价值")
        documents[name] = "。".join(str(p) for p in parts if p)
    return dict(sorted(documents.items()))


def convex_hull(points: np.ndarray) -> np.ndarray:
    vertices = sorted(set(map(tuple, points.tolist())))
    if len(vertices) < 3:
        return np.asarray(vertices)
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower, upper = [], []
    for group, sequence in ((lower, vertices), (upper, reversed(vertices))):
        for v in sequence:
            while len(group) >= 2 and cross(group[-2], group[-1], v) <= 0:
                group.pop()
            group.append(v)
    return np.asarray(lower[:-1] + upper[:-1])


def inside_hull(points: np.ndarray, hull: np.ndarray) -> np.ndarray:
    if len(hull) < 3:
        return np.zeros(len(points), dtype=bool)
    inside = np.ones(len(points), dtype=bool)
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        cross = (b[0]-a[0])*(points[:, 1]-a[1]) - (b[1]-a[1])*(points[:, 0]-a[0])
        inside &= cross >= -1e-6
    return inside


def normalize(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    result = np.zeros_like(values, dtype=float)
    if not mask.any():
        return result
    low, high = float(values[mask].min()), float(values[mask].max())
    if high - low > 1e-12:
        result[mask] = (values[mask] - low) / (high - low)
    return result


def class_bandwidth(points: np.ndarray, origin: np.ndarray, config: dict) -> np.ndarray:
    if len(points) < 2:
        annd = config["minInfluenceMeters"]
    else:
        distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        annd = distances.min(axis=1).mean()
    zones = np.floor((points - origin) / config["zoneMeters"]).astype(int)
    counts = Counter(map(tuple, zones.tolist()))
    abundance = np.array([counts[tuple(zone)] / len(points) for zone in zones])
    radius = np.exp(abundance) * max(annd, config["minInfluenceMeters"])
    return np.minimum(radius, config["maxInfluenceMeters"]) / 3


def kernel_field(grid: np.ndarray, points: np.ndarray, bandwidth: np.ndarray,
                 weights: np.ndarray | None = None) -> np.ndarray:
    """Paper Eq.2: (1/n) sum G(distance/h)/h; radial, not 2D 1/h**2 KDE.

    weights 为逐点先验时改用加权平均，让点位所在地名也能影响热区分布；
    weights=None 时与原文一致（等权平均），保证赏花/拍照结果不变。
    """
    values = np.zeros(len(grid))
    if len(points) == 0:
        return values
    point_weights = None
    if weights is None:
        normalizer = float(len(points))
    else:
        point_weights = np.asarray(weights, dtype=float)
        normalizer = float(point_weights.sum())
        if normalizer <= 1e-12:
            return values
    for start in range(0, len(grid), 256):
        distance = np.linalg.norm(grid[start:start+256, None, :] - points[None, :, :], axis=2)
        z = distance / bandwidth
        kernels = np.exp(-0.5 * z**2) / (np.sqrt(2*np.pi) * bandwidth)
        kernels[z > 3] = 0
        if point_weights is None:
            values[start:start+256] = kernels.mean(axis=1)
        else:
            values[start:start+256] = (kernels * point_weights).sum(axis=1) / normalizer
    return values


def prepare_geometry(pois: list[dict], campus: str, config: dict,
                     scene: str | None = None) -> dict:
    selected = [p for p in pois if p["campus"] == campus]
    if len(selected) < 3:
        raise ValueError(f"{campus}: insufficient spatial coverage")
    center = config["campuses"][campus]["center"]
    xy = to_meters([[p["lng"], p["lat"]] for p in selected], center)
    cell = config["cellMeters"]
    low = np.floor(xy.min(axis=0) / cell) * cell - cell
    high = np.ceil(xy.max(axis=0) / cell) * cell + cell
    width, height = np.ceil((high-low) / cell).astype(int)
    if width * height > 150000:
        raise ValueError("heatmap grid exceeds safe size; use a larger cellMeters")
    # Raster rows go north -> south; cell centers and image edges are distinct.
    xs = low[0] + (np.arange(width)+0.5)*cell
    ys = high[1] - (np.arange(height)+0.5)*cell
    gx, gy = np.meshgrid(xs, ys)
    grid = np.column_stack((gx.ravel(), gy.ravel()))
    mask = inside_hull(grid, convex_hull(xy))
    for start in range(0, len(grid), 256):
        distance = np.linalg.norm(grid[start:start+256, None, :] - xy[None, :, :], axis=2)
        mask[start:start+256] &= distance.min(axis=1) <= config["supportMeters"]
    # 散步/约会的地名先验：逐点权重，随场景变化；其他场景为 None（等权）。
    point_weights = scene_point_weights(selected, scene, config)
    groups, fields, bands, class_categories = {}, {}, {}, {}
    for name in sorted({p["subCategory"] for p in selected}):
        indexes = [i for i, p in enumerate(selected) if p["subCategory"] == name]
        group_xy = xy[indexes]
        group_weights = None if point_weights is None else point_weights[indexes]
        # Analysis zones are anchored at the campus center, independent of the
        # display raster's cell size and padded extent.
        h = class_bandwidth(group_xy, np.zeros(2), config)
        groups[name] = indexes
        # 类别归属：植物走"植物档"，非植物 POI 走"设施/地标档"（见 fuse_fields）。
        class_categories[name] = ("plant" if all((selected[i].get("category") or "plant") == "plant"
                                                 for i in indexes) else "facility")
        fields[name] = kernel_field(grid, group_xy, h, group_weights)
        bands[name] = [round(float(h.min()), 2), round(float(h.max()), 2)]
    return dict(pois=selected, xy=xy, grid=grid, mask=mask, fields=fields,
                groups=groups, bands=bands, class_categories=class_categories,
                width=int(width), height=int(height),
                bounds=to_lnglat(np.array([low, high]), center).ravel().tolist(),
                center=center, config=config, campus=campus, scene=scene)


def _unit_scale(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """按掩膜内最大值把场缩放到 [0, 1]：只统一量级、不改变形状。"""
    peak = float(values[mask].max()) if mask.any() else 0.0
    return values / peak if peak > 1e-12 else np.zeros_like(values)


def fuse_fields(geometry: dict, similarities: dict[str, float],
                plant_priors: dict | None = None, season=None, scene=None,
                class_weights: dict | None = None) -> tuple[np.ndarray, dict, dict]:
    config, mask = geometry["config"], geometry["mask"]
    names = sorted(geometry["fields"], key=lambda name: (-similarities.get(name, 0), name))
    betas = {name: math.log(config["positiveRatio"] * len(names) / rank)
             for rank, name in enumerate(names, 1)}
    if config["negativeContribution"] == "clip":
        betas = {name: max(0.0, value) for name, value in betas.items()}
    # 语义相似度之外叠加“观赏价值 + 花期季节”先验（season=None 时全为 1.0）
    priors = _seasonal_priors(names, plant_priors, season, scene)
    if scene in PLACE_PRIOR_SCENES:
        # 散步/约会/拍照再叠加非季节类别先验；赏花不进入此分支，输出不变。
        static_priors = scene_class_priors(names, plant_priors, scene, config, class_weights)
        priors = {name: priors[name] * static_priors.get(name, 1.0) for name in names}
    weights = {name: max(0.0, similarities.get(name, 0)) * priors[name] for name in names}
    # With per-class min-max, beta magnitude cancels; its sign selects/inverts the
    # field. Preserve this property rather than secretly weighting twice.
    fields = {name: normalize(geometry["fields"][name] * betas[name], mask) for name in names}
    categories = geometry.get("class_categories") or {}
    facility_names = [name for name in names if categories.get(name) == "facility"]
    share = float(config.get("facilityShare") or 0.0)
    if facility_names and share > 0:
        # 设施/地标单独权重档（sakde-campus-5）：非植物类别只有几个（座椅/运动场/图书馆/
        # 河流等），若与上百个植物类别共用同一个分母，其贡献会被稀释到 1% 量级——实测这些
        # 类别的相似度排名第 1~4 却永远进不了 Top。这里把"植物档"与"设施/地标档"各自组内
        # 归一、再各自缩放到 [0,1]，最后按 facilityShare 融合；share>0.5 表示"可命名的真实
        # 地点证据"略优先于"植物密度代理"。
        plant_total = sum(weights[n] for n in names
                          if categories.get(n) != "facility" and abs(betas[n]) > 1e-12)
        facility_total = sum(weights[n] for n in facility_names if abs(betas[n]) > 1e-12)
        plant_fields = [np.zeros(len(mask))]
        facility_fields = [np.zeros(len(mask))]
        contributions = {}
        for name in names:
            is_facility = categories.get(name) == "facility"
            group_total = facility_total if is_facility else plant_total
            value = weights[name] / group_total if group_total > 0 else 0.0
            contributions[name] = fields[name] * value
            (facility_fields if is_facility else plant_fields).append(fields[name] * value)
        plant_part = _unit_scale(sum(plant_fields), mask)
        fused = (1.0 - share) * plant_part + share * _unit_scale(sum(facility_fields), mask)
    else:
        # 该场景没有设施/地标类别（如赏花）时完全走原路径，数值与旧版逐位一致。
        total = sum(weights[name] for name in names if abs(betas[name]) > 1e-12)
        contributions = {name: (fields[name] * weights[name] / total if total > 0
                                else fields[name] * 0) for name in names}
        fused = sum(contributions.values(), np.zeros(len(mask)))
    return normalize(fused, mask), contributions, betas


def hotspot_places(geometry: dict, field: np.ndarray, contributions: dict,
                   limit=MAX_PLACES) -> list[dict]:
    grid, xy, pois = geometry["grid"], geometry["xy"], geometry["pois"]
    config = geometry["config"]
    picked, used_labels = [], set()
    # 跨场景"不重合"由 fuse_fields 的软抑制完成，这里只按场本身的峰值挑选，
    # 保证标点始终落在真正的热区峰上。
    # Greedy non-maximum suppression, then attach to a genuine source POI.
    for index in np.argsort(-field, kind="stable"):
        if not geometry["mask"][index] or field[index] <= 0:
            continue
        peak = grid[index]
        if any(np.linalg.norm(peak - item[0]) < config["peakSeparationMeters"] for item in picked):
            continue
        distance = np.linalg.norm(xy-peak, axis=1)
        nearby = np.flatnonzero(distance <= config["anchorRadiusMeters"])
        if not len(nearby):
            continue
        named = [i for i in nearby if re.search(r"[\u4e00-\u9fff]", pois[i].get("locationName") or "")]
        anchor_index = min(named or nearby.tolist(), key=lambda i: (distance[i], pois[i]["id"]))
        poi = pois[anchor_index]
        label = poi.get("locationName") or f"{poi['name']}点位（{poi['id']}）"
        name = label + "周边" if named and not label.endswith("周边") else label
        if name in used_labels:
            continue
        if any(np.linalg.norm(xy[anchor_index]-xy[item[1]]) < config["peakSeparationMeters"] for item in picked):
            continue
        classes = Counter(pois[i]["subCategory"] for i in nearby)
        evidence = sorted(classes, key=lambda c: (-float(contributions[c][index]), c))[:4]
        explanation = [{"className": c, "pointCount": classes[c],
                        "contribution": round(float(contributions[c][index]), 5)} for c in evidence]
        anchor_category = poi.get("category") or "plant"
        # 纯植物热区保持原有文案，避免改动赏花/拍照的回答；混入非植物点位时才改写。
        if all((pois[i].get("category") or "plant") == "plant" for i in nearby):
            reason = f"场景热区；{len(nearby)}个植物点位、{len(classes)}类植物；代表植物：{'、'.join(evidence)}"
        else:
            reason = f"场景热区；{len(nearby)}个校园点位、{len(classes)}类；代表：{'、'.join(evidence)}"
        place = dict(name=name, number=label, lng=poi["lng"], lat=poi["lat"],
                     campus=poi["campus"], kind="ranked_place", category=anchor_category,
                     subCategory=poi["subCategory"], rank=len(picked)+1,
                     score=round(float(field[index])*100, 1), reason=reason, plants=evidence,
                     poi_id=poi["id"], algorithm=ALGORITHM_VERSION, evidence=explanation,
                     anchor_distance_m=round(float(distance[anchor_index]), 1),
                     access_verified=False, peak=to_lnglat(peak, geometry["center"]).tolist())
        picked.append((peak, anchor_index, place))
        used_labels.add(name)
        if len(picked) >= limit:
            break
    return [item[2] for item in picked]


def build_map(geometry: dict, scene: str, similarities: dict[str, float],
              plant_priors: dict | None = None, season=None,
              class_weights: dict | None = None) -> dict:
    field, contributions, betas = fuse_fields(geometry, similarities, plant_priors, season, scene,
                                              class_weights)
    raster = np.where(geometry["mask"], np.rint(field * 1000), -1).astype(int)
    return {
        "available": True, "algorithm": ALGORITHM_VERSION,
        "campus": geometry["campus"], "scene": scene,
        "sceneName": geometry["config"]["scenes"][scene],
        "season": season,
        "seasonLabel": SEASON_LABELS.get(season) if season else None,
        "grid": {"width": geometry["width"], "height": geometry["height"],
                 "bounds": geometry["bounds"], "cellMeters": geometry["config"]["cellMeters"],
                 "rowOrder": "north_to_south", "noData": -1, "maxValue": 1000,
                 "values": raster.tolist()},
        "places": hotspot_places(geometry, field, contributions,
                                 limit=int(geometry["config"].get("placeLimit") or MAX_PLACES)),
        "sceneIndependence": "computed_without_other_scene_outputs",
        "classContributions": [{"name": name, "similarity": round(similarities.get(name, 0), 5),
                                "beta": round(betas[name], 5),
                                "count": len(geometry["groups"][name]),
                                "bandwidthMeters": geometry["bands"][name]}
                               for name in sorted(betas, key=lambda n: -similarities.get(n, 0))],
        "disclaimer": DISCLAIMER, "boundarySource": "poi_convex_hull_with_support_mask",
        "coordinateSystem": geometry["config"]["coordinateSystem"],
        "normalization": "within_campus_and_scene_only",
        "negativeContribution": geometry["config"]["negativeContribution"],
    }


class HeatmapStore:
    """Read-only cache. No downloads, recomputation, or dependency on Flask."""
    def __init__(self, base: Path):
        self.base = Path(base)
        self.cache = self.base / "heatmap_cache"
        self.model = os.getenv("SEMANTIC_MODEL_PATH", "BAAI/bge-small-zh-v1.5").strip()
        self._lock = threading.Lock()
        self._memory = {}

    def get(self, campus: str, scene: str) -> dict:
        if campus not in ("普陀", "闵行") or scene not in SCENE_IDS:
            raise ValueError("请选择有效校区和四类场景之一")
        if os.getenv("HEATMAP_ENABLED", "true").lower() in {"0", "false", "off"}:
            raise HeatmapUnavailable("场景热力图已关闭")
        # 赏花/拍照场景按当前月份选择对应季节的缓存，实现季节感知
        season = current_season() if scene in ("flower_viewing", "photo") else None
        with self._lock:
            try:
                # A missing/broken optional configuration must not prevent the
                # original Flask service from starting or serving old requests.
                config = load_config(self.base)
                meta = json.loads((self.cache / "manifest.json").read_text(encoding="utf-8"))
                current_hash = fingerprint(self.base, self.model)
                if meta.get("sourceHash") != current_hash:
                    # 指纹由「算法版本 + 模型标识 + 被哈希绑定的源文件」组成。逐项比对，
                    # 避免把"后端进程没重启"误报成"缓存需要重建"——前者只需重启，
                    # 后者才需要跑构建脚本。
                    if meta.get("algorithm") != ALGORITHM_VERSION:
                        raise HeatmapUnavailable(
                            f"后端进程仍在运行旧版算法（代码 {ALGORITHM_VERSION}，缓存 "
                            f"{meta.get('algorithm')}），请重启后端")
                    if meta.get("model") != self.model:
                        raise HeatmapUnavailable(
                            f"语义模型标识不一致（后端 {self.model}，缓存 {meta.get('model')}），"
                            "请重启后端或重建缓存")
                    raise HeatmapUnavailable("热力图缓存已过期，请运行 python build_scene_heatmaps.py")
                key = (current_hash, campus, scene, season)
                if key not in self._memory:
                    slug = config["campuses"][campus]["slug"]
                    filename = f"{slug}_{scene}_{season}.json" if season else f"{slug}_{scene}.json"
                    data = json.loads((self.cache / filename).read_text(encoding="utf-8"))
                    if data.get("sourceHash") != current_hash:
                        raise HeatmapUnavailable("热力图生成未完成，请重新运行构建脚本")
                    validate_payload(data, campus, scene)
                    self._memory.clear()
                    self._memory[key] = data
                return copy.deepcopy(self._memory[key])
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
                raise HeatmapUnavailable("热力图缓存不可用，请运行 python build_scene_heatmaps.py") from exc


def validate_payload(data: dict, campus: str, scene: str):
    """Reject syntactically valid but incomplete/mismatched cached JSON."""
    grid = data["grid"]
    width, height = grid["width"], grid["height"]
    valid = (data["available"] is True and data["algorithm"] == ALGORITHM_VERSION
             and data["campus"] == campus and data["scene"] == scene
             and isinstance(data["sceneName"], str) and isinstance(data["disclaimer"], str)
             and type(width) is int and type(height) is int and width > 0 and height > 0
             and width * height <= 150000 and len(grid["values"]) == width * height
             and grid["rowOrder"] == "north_to_south" and grid["maxValue"] == 1000
             and grid["noData"] == -1 and len(grid["bounds"]) == 4)
    if not valid:
        raise ValueError("invalid heatmap grid metadata")
    bounds = np.asarray(grid["bounds"], dtype=float)
    values = np.asarray(grid["values"], dtype=float)
    if (not np.isfinite(bounds).all() or not (bounds[0] < bounds[2] and bounds[1] < bounds[3])
            or not np.isfinite(values).all() or not (((values >= 0) & (values <= 1000)) | (values == -1)).all()):
        raise ValueError("invalid heatmap grid values")
    if not isinstance(data["places"], list) or len(data["places"]) > MAX_PLACES:
        raise ValueError("invalid heatmap places")
    for place in data["places"]:
        if (place["campus"] != campus or not place["poi_id"] or place["access_verified"] is not False
                or not isinstance(place["name"], str) or not isinstance(place["reason"], str)
                or place["rank"] not in range(1, MAX_PLACES + 1)
                or not all(math.isfinite(float(place[k])) for k in ("lng", "lat"))):
            raise ValueError("invalid heatmap place")


def register_heatmap_routes(app, store: HeatmapStore):
    from flask import jsonify, request

    @app.get("/api/heatmaps")
    def scene_heatmap():
        try:
            return jsonify(store.get(request.args.get("campus", ""), request.args.get("scene", "")))
        except ValueError as exc:
            return jsonify(available=False, reason=str(exc)), 400
        except HeatmapUnavailable as exc:
            return jsonify(available=False, reason=str(exc)), 503


def heatmap_chat_response(payload: dict) -> dict:
    """Evidence-only answer so model prose cannot rename/relocate a hotspot."""
    parts = [f"根据{payload['campus']}校区的植物资料与空间分布，为你找到以下{payload['sceneName']}参考区域："]
    for p in payload["places"]:
        parts.append(f"**Top{p['rank']}【{p['name']}】**\n\n{p['reason']}。")
    parts.append("地图已显示对应热区和真实POI参考位置。\n\n" + payload["disclaimer"])
    return {"choices": [{"message": {"role": "assistant", "content": "\n\n".join(parts)}}],
            "locations": payload["places"], "ranked_places": payload["places"],
            "heatmap": payload, "recommendation_engine": "sakde"}
