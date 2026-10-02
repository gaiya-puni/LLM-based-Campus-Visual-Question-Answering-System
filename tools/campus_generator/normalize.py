"""Normalize harvested or manually reviewed candidates into the POI contract."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable


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


def normalize_candidate(candidate: dict, campus: str, index: int) -> dict:
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
        "confidence": float(candidate.get("confidence", 0.5)),
        "verified": bool(candidate.get("verified", False)),
    })
    if not 0 <= normalized["confidence"] <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return normalized


def normalize_candidates(candidates: Iterable[dict], campus: str) -> tuple[list[dict], list[dict]]:
    """Return ``(normalized, pending)`` while preserving rejection reasons."""
    normalized: list[dict] = []
    pending: list[dict] = []
    seen_ids: set[str] = set()
    seen_points: set[tuple[str, float, float]] = set()
    for index, candidate in enumerate(candidates or []):
        try:
            item = normalize_candidate(candidate, campus, index)
            point_key = (item["name"], round(item["lng"], 6), round(item["lat"], 6))
            if item["id"] in seen_ids or point_key in seen_points:
                pending.append({"index": index, "candidate": candidate, "reason": "duplicate"})
                continue
            seen_ids.add(item["id"])
            seen_points.add(point_key)
            normalized.append(item)
        except (TypeError, ValueError) as exc:
            pending.append({"index": index, "candidate": candidate, "reason": str(exc)})
    return normalized, pending
