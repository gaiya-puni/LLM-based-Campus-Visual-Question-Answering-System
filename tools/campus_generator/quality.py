"""Explainable, deterministic quality scoring for campus POI candidates.

The scorer is intentionally conservative and side-effect free.  It does not
approve, reject, merge, or mutate candidates; the existing spatial and review
policies remain authoritative.  Its result can therefore be attached to an
already-normalized candidate without changing the publication contract.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


_REGISTRY_SOURCES = {"campus_registry", "scene_registry"}
_SOURCE_RELIABILITY = {
    "campus_registry": 0.98,
    "scene_registry": 0.98,
    "official_web": 0.88,
    "public_web": 0.72,
    "public_web_plant": 0.72,
    "public_web_scene": 0.72,
    "amap": 0.72,
    "amap_plant": 0.68,
    "amap_scene": 0.72,
    "json": 0.55,
    "manual": 0.55,
    "harvested": 0.45,
}

# A generic garden is not commercial by itself.  Terms below require an
# explicit residential/business signal, which avoids penalising campus gardens.
_HIGH_COMMERCIAL_RISK_TERMS = (
    "住宅", "小区", "公寓", "楼盘", "房地产", "售楼处", "购物中心", "商场",
    "酒店", "宾馆", "ktv", "酒吧", "花店", "鲜花店", "花卉市场", "园艺公司",
    "景观公司", "苗木市场", "便利店", "超市",
)
_MODERATE_COMMERCIAL_RISK_TERMS = (
    "餐厅", "饭店", "咖啡店", "奶茶店", "快餐", "小吃", "底商", "商铺",
)

_THEME_ALIASES = {
    "walk": ("walk", "散步", "漫步", "步道", "小径", "绿地", "河岸"),
    "date": ("date", "约会", "情侣", "浪漫", "花园", "湖", "咖啡"),
    "photo": ("photo", "拍照", "摄影", "出片", "景观", "建筑", "雕塑"),
    "flower": ("flower", "flower_viewing", "赏花", "花园", "樱花", "银杏", "梅花"),
    "flower_viewing": (
        "flower", "flower_viewing", "赏花", "花园", "樱花", "银杏", "梅花",
    ),
    "study": ("study", "学习", "自习", "图书馆", "教学楼"),
    "food": ("food", "美食", "食堂", "餐厅"),
    "general": ("general",),
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _canonical_source(value: str) -> str:
    source = str(value or "").strip().lower()
    if not source:
        return ""
    if "amap" in source or source in {"高德", "gaode"}:
        return "amap"
    if source in {"json-ld", "html text", "web", "official_web"}:
        return "public_web"
    if source.startswith("public_web"):
        return "public_web"
    if source in _REGISTRY_SOURCES:
        return source
    return source


def _evidence(candidate: dict) -> dict:
    value = candidate.get("evidence") or {}
    return value if isinstance(value, dict) else {}


def _source_set(candidate: dict, evidence: dict) -> set[str]:
    values = [candidate.get("source"), evidence.get("provider")]
    values.extend(_strings(candidate.get("sources")))
    values.extend(_strings(evidence.get("sources")))
    values.extend(_strings(evidence.get("providers")))
    return {source for value in values if (source := _canonical_source(value))}


def _candidate_text(candidate: dict, evidence: dict) -> str:
    parts = [
        candidate.get("name"), candidate.get("locationName"),
        candidate.get("subCategory"), candidate.get("text"),
        *(_strings(candidate.get("tags"))), *(_strings(candidate.get("scenes"))),
        evidence.get("type"), evidence.get("address"), evidence.get("keyword"),
        evidence.get("sceneFilter"), evidence.get("plantFilter"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _campus_evidence_score(candidate: dict, evidence: dict) -> float:
    source = _canonical_source(candidate.get("source"))
    provider = _canonical_source(evidence.get("provider"))
    if source in _REGISTRY_SOURCES or provider in _REGISTRY_SOURCES:
        return 1.0
    if candidate.get("verified"):
        return 0.9
    method = str(evidence.get("campusMembershipMethod") or "")
    if method == "polygon":
        return 0.95
    if method == "radius_fallback":
        return 0.78
    if any(evidence.get(key) for key in ("campusMatch", "campusEvidence", "schoolMatch")):
        return 0.75
    # A campus label is useful context, but is not proof by itself because it
    # is assigned during normalization.
    return 0.35 if candidate.get("campus") else 0.2


def _boundary_evidence_score(evidence: dict) -> float:
    method = str(evidence.get("campusMembershipMethod") or "")
    if method == "polygon":
        relation = str(evidence.get("campusMembershipRelation") or "inside")
        return 1.0 if relation in {"inside", "boundary"} else 0.0
    if method == "radius_fallback":
        return 0.65
    return 0.15


def _theme_relevance_score(candidate: dict, evidence: dict, theme: str | None) -> float:
    scenes = {value.strip().lower() for value in _strings(candidate.get("scenes"))}
    text = _candidate_text(candidate, evidence)
    requested = str(theme or "").strip().lower()
    if requested:
        aliases = _THEME_ALIASES.get(requested, (requested,))
        if requested in scenes or any(alias in scenes for alias in aliases):
            return 1.0
        if any(alias and alias in text for alias in aliases):
            return 0.82
        return 0.2
    if evidence.get("sceneFilter") or evidence.get("plantFilter"):
        return 0.9
    if scenes:
        return 0.78
    if str(candidate.get("category") or "").lower() == "plant":
        return 0.65
    return 0.5


def _source_reliability_score(candidate: dict, sources: set[str]) -> float:
    primary = str(candidate.get("source") or "harvested").strip().lower()
    # A specific primary adapter (for example ``amap_plant``) intentionally
    # carries its own reliability. Its generic provider family must not
    # silently upgrade that declared value.
    score = _SOURCE_RELIABILITY.get(primary)
    if score is None:
        score = max(
            (_SOURCE_RELIABILITY.get(source) for source in sources
             if _SOURCE_RELIABILITY.get(source) is not None),
            default=0.45,
        )
    if candidate.get("verified"):
        score = max(score, 0.9)
    return score


def _commercial_risk_score(text: str) -> float:
    if any(term in text for term in _HIGH_COMMERCIAL_RISK_TERMS):
        return 1.0
    if any(term in text for term in _MODERATE_COMMERCIAL_RISK_TERMS):
        return 0.6
    return 0.0


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _duplicate_risk_score(candidate: dict, evidence: dict) -> float:
    explicit = _number(candidate.get("duplicateRisk"))
    if explicit is not None:
        return _clamp(explicit)
    if candidate.get("duplicateOf") or evidence.get("duplicateOf"):
        return 1.0
    if candidate.get("possibleDuplicate") or evidence.get("possibleDuplicate"):
        return 0.8
    counts = [
        _number(candidate.get("nearbyDuplicateCount")),
        _number(evidence.get("nearbyDuplicateCount")),
        _number(evidence.get("duplicateCount")),
    ]
    count = max((value for value in counts if value is not None), default=0.0)
    if count > 0:
        return _clamp(0.5 + 0.1 * min(count, 5))
    return 0.0


def assess_candidate_quality(candidate: dict, *, theme: str | None = None) -> dict:
    """Return deterministic quality metadata for one candidate.

    The returned object always has ``scores``, ``confidence`` and
    ``confidenceReasons``.  Risk scores use the intuitive convention that
    zero is safest and one is riskiest; all other scores use one as strongest.
    """
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")

    evidence = _evidence(candidate)
    sources = _source_set(candidate, evidence)
    text = _candidate_text(candidate, evidence)
    scores = {
        "campusEvidence": _campus_evidence_score(candidate, evidence),
        "boundaryEvidence": _boundary_evidence_score(evidence),
        "themeRelevance": _theme_relevance_score(candidate, evidence, theme),
        "sourceReliability": _source_reliability_score(candidate, sources),
        "crossSourceAgreement": 1.0 if len(sources) >= 2 else 0.35 if sources else 0.1,
        "commercialRisk": _commercial_risk_score(text),
        "duplicateRisk": _duplicate_risk_score(candidate, evidence),
    }

    confidence = (
        0.24 * scores["campusEvidence"]
        + 0.22 * scores["boundaryEvidence"]
        + 0.17 * scores["themeRelevance"]
        + 0.20 * scores["sourceReliability"]
        + 0.17 * scores["crossSourceAgreement"]
        - 0.18 * scores["commercialRisk"]
        - 0.10 * scores["duplicateRisk"]
    )

    reasons: list[str] = []
    method = str(evidence.get("campusMembershipMethod") or "")
    if method == "polygon":
        reasons.append("位于可信校园边界内")
    elif method == "radius_fallback":
        reasons.append("通过校园可信半径校验（尚无正式边界）")
    elif candidate.get("verified"):
        reasons.append("候选已由可信来源核验")
    else:
        reasons.append("缺少明确的校园空间证据")
    if len(sources) >= 2:
        reasons.append(f"{len(sources)} 个独立来源提供一致证据")
    elif sources:
        reasons.append("目前仅有单一来源证据")
    else:
        reasons.append("未标明候选来源")
    if scores["themeRelevance"] >= 0.8:
        reasons.append("候选内容与目标主题高度相关")
    elif theme and scores["themeRelevance"] <= 0.2:
        reasons.append("未发现与目标主题直接相关的证据")
    if scores["commercialRisk"] >= 0.8:
        reasons.append("名称或描述包含明显商业/住宅场所信号")
    elif scores["commercialRisk"] > 0:
        reasons.append("名称或描述包含潜在商业场所信号")
    if scores["duplicateRisk"] > 0:
        reasons.append("存在重复或邻近重复候选信号")

    return {
        "scores": {key: round(_clamp(value), 2) for key, value in scores.items()},
        "confidence": round(_clamp(confidence), 2),
        "confidenceReasons": reasons,
    }


__all__ = ["assess_candidate_quality"]
