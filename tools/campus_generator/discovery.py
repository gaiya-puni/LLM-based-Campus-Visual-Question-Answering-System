"""Natural-language campus discovery and multi-source harvest planning.

This module deliberately stops at an auditable candidate bundle.  It does not
publish POIs or mutate the runtime campus registry.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Callable

import requests

from .harvest import (AmapPoiHarvester, HarvestCache, Harvester, HarvestResult,
                       PublicWebHarvester)
from .profile import validate_profile
from .web_search import WebSearchClient


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CAMPUSES = _ROOT / "webapp" / "backend" / "campuses.json"


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
    "walk": ["步道", "林荫道", "绿地", "草坪", "湖泊", "河流", "河岸", "桥", "亭子", "运动场", "体育馆"],
    "date": ["湖泊", "河流", "河岸", "桥", "亭子", "花园", "草坪", "图书馆", "咖啡店"],
    "photo": ["湖泊", "河流", "河岸", "花园", "亭子", "樱花", "银杏", "草坪", "建筑", "图书馆", "体育馆", "校门", "广场", "雕塑", "桥"],
    "flower": ["花园", "植物园", "樱花", "梅花", "荷花", "桂花", "银杏"],
    "study": ["图书馆", "教学楼", "自习室", "咖啡店"],
    "food": ["食堂", "餐厅", "咖啡店", "小吃"],
    "general": ["图书馆", "湖泊", "花园", "食堂", "体育场", "咖啡店"],
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

SCENE_TERM_ALIASES = {
    "步道": ("步道", "小径", "漫步", "步行"),
    "林荫道": ("林荫", "林荫道", "树荫"),
    "绿地": ("绿地", "绿化", "公园"),
    "草坪": ("草坪", "草地", "绿地"),
    "湖泊": ("湖", "湖泊", "水景"),
    "河流": ("河", "河流", "溪", "渠"),
    "河岸": ("河岸", "河畔", "水岸", "湖畔", "湖边"),
    "桥": ("桥",),
    "亭子": ("亭", "亭子", "凉亭"),
    "花园": ("花园", "园林", "公园", "花圃", "花境", "园"),
    "地标建筑": ("楼", "馆", "塔", "门", "中心", "建筑"),
    "建筑": ("楼", "馆", "塔", "门", "中心", "建筑"),
    "雕塑": ("雕塑", "雕像", "铜像", "纪念碑"),
    "运动场": ("运动场", "操场", "田径场", "足球场", "球场"),
    "体育场": ("体育场", "运动场", "操场"),
    "体育馆": ("体育馆", "游泳馆", "健身中心"),
    "图书馆": ("图书馆",),
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


def _scoped_keyword(profile: dict, keyword: str) -> str:
    school_info = profile.get("school") or {}
    school = str(school_info.get("name") or "").strip()
    campus = profile.get("campus") or {}
    campus_name = str(campus.get("name") or "").strip()
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
    """Build a small, reproducible search budget for one campus request."""
    scope = _scoped_keyword(profile, "")
    theme_terms = THEME_SEARCH_TERMS.get(theme) or THEME_SEARCH_TERMS["general"]
    focused = " ".join(keywords[:6])
    return list(dict.fromkeys(filter(None, (
        f"{scope} {theme_terms}".strip(),
        f"{scope} {focused}".strip(),
    ))))[:2]


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


def _registry_paths(profile: dict, primary: Path) -> list[Path]:
    paths = [primary]
    slug = str((profile.get("campus") or {}).get("slug") or "").strip()
    campus_file = _ROOT / "webapp" / "backend" / f"{slug}_pois.json"
    if slug and campus_file.is_file() and campus_file.resolve() != primary.resolve():
        paths.append(campus_file)
    return paths


def infer_theme(query: str) -> str:
    text = str(query or "").lower()
    for theme, words in {
        "date": ("约会", "情侣", "浪漫"),
        "walk": ("散步", "遛弯", "走走", "休息", "放松"),
        "photo": ("拍照", "摄影", "出片"),
        "flower": ("赏花", "花", "樱花", "银杏"),
        "study": ("学习", "自习", "安静"),
        "food": ("吃饭", "美食", "餐厅", "食堂"),
    }.items():
        if any(word in text for word in words):
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


def _clean_request(query: str) -> str:
    text = re.sub(r"(帮我|请|想要|做一个|生成一个|制作一个|的)?\s*热力图", "", query)
    text = re.sub(r"(校园|校内)?(地点|景点|场所)(推荐|分布)?", "", text)
    text = re.sub(r"(约会|情侣|浪漫|散步|遛弯|走走|休息|放松|拍照|摄影|出片|赏花|学习|自习|安静|吃饭|美食|餐厅|食堂)", "", text)
    return re.sub(r"\s+", " ", text).strip(" ，,。")


class CampusProfileResolver:
    """Resolve a request against local campus config, then AMap geocoding."""

    def __init__(self, config_path: str | Path = _DEFAULT_CAMPUSES,
                 *, api_key: str | None = None, request_json: Callable | None = None):
        self.config_path = Path(config_path)
        self.api_key = (api_key or os.getenv("AMAP_WEB_SERVICE_KEY", "")).strip()
        self.request_json = request_json or self._request_json

    def _config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))


    def _request_json(self, params: dict) -> dict:
        response = requests.get("https://restapi.amap.com/v3/geocode/geo",
                                params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "1":
            raise RuntimeError(data.get("info", "AMap geocode failed"))
        return data

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
            campus_score = sum(len(str(term)) for term in campus_terms
                               if term and str(term).lower() in text)
            if school_score or campus_score:
                # School identity must win over a same-named campus.  The
                # campus score remains a tie-breaker for queries such as
                # "交大闵行校区" where the school is expressed by an alias.
                matches.append((school_score, campus_score, campus, school))
        if not matches:
            return None
        _, _, campus, school = max(matches, key=lambda item: (item[0], item[1]))
        return {
            "school": {"id": campus["school"], "name": school.get("name", campus["school"]),
                       "enName": school.get("enName", ""), "aliases": school.get("aliases", [])},
            "campus": {key: campus[key] for key in (
                "name", "slug", "aliases", "center", "trustRadiusM", "waterName",
                "coordinateSystem", "boundary", "boundarySource", "boundaryConfidence",
            )
                       if key in campus},
            "discovery": {"method": "local_config", "query": query},
        }

    def _geocode(self, query: str) -> dict:
        if not self.api_key:
            raise ValueError("unknown campus; AMAP_WEB_SERVICE_KEY is required for geocoding")
        payload = self.request_json({"key": self.api_key, "address": _clean_request(query), "output": "json"})
        geocodes = payload.get("geocodes") or []
        if not geocodes:
            raise ValueError("could not resolve a campus center from the request")
        item = geocodes[0]
        center = _finite_location(item.get("location"))
        if not center:
            raise ValueError("geocoding result has no valid coordinates")
        address = str(item.get("formatted_address") or _clean_request(query))
        name = _clean_request(query) or address
        school_name = re.split(r"(校区|大学|学院)", name, maxsplit=1)[0] or name
        campus_name = name if "校区" in name else f"{name}校区"
        school_id = _slug(school_name, "school")
        campus_slug = _slug(campus_name, "campus")
        return {
            "school": {"id": school_id, "name": school_name, "enName": "", "aliases": []},
            "campus": {"name": campus_name, "slug": campus_slug, "aliases": [],
                       "center": center, "trustRadiusM": 2000,
                       "coordinateSystem": "GCJ-02", "waterName": ""},
            "discovery": {"method": "amap_geocode", "query": query, "address": address},
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
                if _matches_terms(registry_text, NON_SCENIC_REGISTRY_TERMS):
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
            if any(term in haystack for term in self._PLANT_TERMS) and _has_campus_evidence(item, self.profile):
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
        candidates, warnings = super()._collect_keyword(_scoped_keyword(self.profile, keyword))
        filtered = []
        for item in candidates:
            text = _candidate_text(item)
            if (_matches_terms(text, COMMERCIAL_TERMS) or not _keyword_matches_candidate(item, keyword) or
                    not _has_campus_evidence(item, self.profile)):
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
            if (_matches_terms(text, COMMERCIAL_TERMS) or not _matches_terms(text, self.terms) or
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
            primary_source = str(candidate.get("source") or self.source)
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
        theme = infer_theme(query)
        plant_keywords = list(THEME_PLANT_KEYWORDS.get(theme) or THEME_PLANT_KEYWORDS["general"])
        selected = list(plant_keywords)
        if theme != "flower":
            selected.extend(THEME_KEYWORDS.get(theme) or THEME_KEYWORDS["general"])
        selected.extend(str(item).strip() for item in (keywords or []) if str(item).strip())
        selected = list(dict.fromkeys(selected))[:20]
        manual_urls = list(dict.fromkeys(
            str(item).strip() for item in (web_urls or []) if str(item).strip()
        ))[:10]
        urls = list(manual_urls)
        sources = []
        warnings = []
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
