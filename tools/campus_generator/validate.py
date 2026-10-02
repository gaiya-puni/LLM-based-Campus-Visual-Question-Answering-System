"""Quality checks for campus profiles, POIs and generated asset bundles."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .profile import validate_profile


REQUIRED_FIELDS = (
    "id", "category", "subCategory", "name", "locationName",
    "lng", "lat", "campus", "text", "tags",
)


def validate_pois(pois: Iterable[dict], campus: str | None = None) -> list[str]:
    problems: list[str] = []
    ids: set[str] = set()
    for index, poi in enumerate(pois or []):
        if not isinstance(poi, dict):
            problems.append(f"poi[{index}] must be an object")
            continue
        missing = [field for field in REQUIRED_FIELDS if poi.get(field) in (None, "")]
        if missing:
            problems.append(f"poi[{index}] missing: {', '.join(missing)}")
        if not isinstance(poi.get("tags"), list):
            problems.append(f"poi[{index}] tags must be a list")
        if campus and poi.get("campus") != campus:
            problems.append(f"poi[{index}] belongs to {poi.get('campus')!r}, expected {campus!r}")
        try:
            lng, lat = float(poi.get("lng")), float(poi.get("lat"))
            if not all(math.isfinite(value) for value in (lng, lat)):
                raise ValueError
            if not -180 <= lng <= 180 or not -90 <= lat <= 90:
                raise ValueError
        except (TypeError, ValueError):
            problems.append(f"poi[{index}] has invalid coordinates")
        try:
            confidence = float(poi.get("confidence", 0.5))
            if not 0 <= confidence <= 1:
                raise ValueError
        except (TypeError, ValueError):
            problems.append(f"poi[{index}] confidence must be between 0 and 1")
        poi_id = str(poi.get("id") or "")
        if poi_id in ids:
            problems.append(f"duplicate poi id: {poi_id}")
        ids.add(poi_id)
    return problems


def validate_runtime_config(runtime: dict) -> list[str]:
    problems: list[str] = []
    schools = runtime.get("schools") or {}
    campuses = runtime.get("campuses") or []
    names = [item.get("name") for item in campuses]
    slugs = [item.get("slug") for item in campuses]
    if len(names) != len(set(names)):
        problems.append("campus names must be unique")
    if len(slugs) != len(set(slugs)):
        problems.append("campus slugs must be unique")
    if runtime.get("defaultCampus") not in names:
        problems.append("defaultCampus must reference a campus")
    for item in campuses:
        if item.get("school") not in schools:
            problems.append(f"{item.get('name')}: unknown school {item.get('school')!r}")
    return problems


def validate_bundle(profile: dict, runtime: dict, pois: Iterable[dict]) -> list[str]:
    return validate_profile(profile) + validate_runtime_config(runtime) + validate_pois(
        pois, (profile.get("campus") or {}).get("name")
    )
