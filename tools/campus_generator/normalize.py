"""Normalize harvested or manually reviewed candidates into the POI contract."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

from .boundary import CampusMembershipEvaluator, membership_evidence
from .quality import assess_candidate_quality


REQUIRED_FIELDS = (
    "id", "category", "subCategory", "name", "locationName",
    "lng", "lat", "campus", "text", "tags",
)


def _slug(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value.strip().lower(), flags=re.UNICODE)
    return text.strip("_") or "poi"


def _stable_id(campus: str, name: str, lng: float, lat: float) -> str:
    raw = f"{campus}|{name}|{lng:.6f}|{lat:.6f}".encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:10]
    return f"{_slug(campus)}_{_slug(name)[:36]}_{digest}"


def _number(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def normalize_candidate(candidate: dict, campus: str, index: int, *,
                        membership_evaluator: CampusMembershipEvaluator | None = None,
                        theme: str | None = None) -> dict:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    name = str(candidate.get("name") or candidate.get("locationName") or "").strip()
    if not name:
        raise ValueError("name is required")
    lng = _number(candidate.get("lng", candidate.get("longitude")), "lng")
    lat = _number(candidate.get("lat", candidate.get("latitude")), "lat")
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ValueError("coordinates are outside bounds")

    category = str(candidate.get("category") or "scene").strip()
    sub_category = str(candidate.get("subCategory") or candidate.get("type") or "地点").strip()
    location_name = str(candidate.get("locationName") or name).strip()
    tags = candidate.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = list(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
    source_urls = candidate.get("sourceUrls") or []
    if isinstance(source_urls, str):
        source_urls = [source_urls]
    source_urls = list(dict.fromkeys(str(url).strip() for url in source_urls if str(url).strip()))
    scenes = candidate.get("scenes") or []
    if isinstance(scenes, str):
        scenes = [scenes]
    scenes = list(dict.fromkeys(str(scene).strip() for scene in scenes if str(scene).strip()))
    text_parts = [name, location_name, category, sub_category, campus, *tags]
    text = str(candidate.get("text") or " ".join(dict.fromkeys(part for part in text_parts if part)))
    source_confidence = float(candidate.get("confidence", 0.5))
    normalized = dict(candidate)
    normalized.update({
        "id": str(candidate.get("id") or _stable_id(campus, name, lng, lat)),
        "category": category,
        "subCategory": sub_category,
        "name": name,
        "locationName": location_name,
        "lng": lng,
        "lat": lat,
        "campus": campus,
        "text": text,
        "tags": tags,
        "scenes": scenes,
        "source": str(candidate.get("source") or "harvested"),
        "sourceUrls": source_urls,
        "confidence": source_confidence,
        "sourceConfidence": source_confidence,
        "verified": bool(candidate.get("verified", False)),
    })
    if not 0 <= normalized["confidence"] <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if membership_evaluator is not None:
        coordinate_system = str(candidate.get("coordinateSystem") or "").strip()
        if membership_evaluator.coordinate_system is not None and not coordinate_system:
            raise ValueError("coordinateSystem is required by campus spatial policy")
        if (coordinate_system and
                coordinate_system != membership_evaluator.effective_coordinate_system):
            raise ValueError(
                "coordinateSystem does not match campus spatial policy "
                f"({coordinate_system!r} != "
                f"{membership_evaluator.effective_coordinate_system!r})"
            )
        # Legacy profiles implicitly used GCJ-02.  Persist that resolved value
        # so a bundle accepted during normalization stays publishable after
        # the formal campus policy is made explicit.
        normalized["coordinateSystem"] = (
            coordinate_system or membership_evaluator.effective_coordinate_system
        )
        accepted, distance, decision = membership_evaluator.evaluate((lng, lat))
        if not accepted:
            if decision["method"] == "polygon":
                raise ValueError("outside campus boundary")
            raise ValueError("outside campus trust radius")
        evidence = candidate.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be an object")
        normalized["evidence"] = {
            **evidence,
            **membership_evidence(decision, distance),
        }
    normalized["quality"] = assess_candidate_quality(
        normalized,
        theme=(str(theme or candidate.get("theme") or "").strip() or None),
    )
    return normalized


def _reason_code(reason: str) -> str:
    if reason.startswith("outside campus"):
        return "outside_campus"
    if "coordinateSystem" in reason:
        return "coordinate_system"
    return "invalid_candidate"


def normalize_candidates(candidates: Iterable[dict], campus: str, *,
                         campus_profile: dict | None = None,
                         theme: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return ``(normalized, pending)`` while preserving rejection reasons."""
    membership_evaluator = None
    if campus_profile is not None:
        profile_name = str(campus_profile.get("name") or "")
        if profile_name != campus:
            raise ValueError(
                f"campus profile belongs to {profile_name!r}, expected {campus!r}"
            )
        membership_evaluator = CampusMembershipEvaluator(campus_profile)
    normalized: list[dict] = []
    pending: list[dict] = []
    seen_ids: set[str] = set()
    seen_points: set[tuple[str, float, float]] = set()
    for index, candidate in enumerate(candidates or []):
        try:
            item = normalize_candidate(
                candidate, campus, index, membership_evaluator=membership_evaluator,
                theme=theme,
            )
            point_key = (item["name"], round(item["lng"], 6), round(item["lat"], 6))
            if item["id"] in seen_ids or point_key in seen_points:
                pending.append({"index": index, "candidate": candidate, "reason": "duplicate",
                                "reasonCode": "duplicate"})
                continue
            seen_ids.add(item["id"])
            seen_points.add(point_key)
            normalized.append(item)
        except (TypeError, ValueError) as exc:
            reason = str(exc)
            pending.append({"index": index, "candidate": candidate, "reason": reason,
                            "reasonCode": _reason_code(reason)})
    return normalized, pending
