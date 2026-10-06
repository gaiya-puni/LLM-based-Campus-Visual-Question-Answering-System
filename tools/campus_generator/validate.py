"""Quality checks for campus profiles, POIs and generated asset bundles."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .boundary import CampusMembershipEvaluator
from .profile import validate_profile


REQUIRED_FIELDS = (
    "id", "category", "subCategory", "name", "locationName",
    "lng", "lat", "campus", "text", "tags",
)


def validate_pois(pois: Iterable[dict], campus: str | None = None, *,
                  campus_profile: dict | None = None) -> list[str]:
    problems: list[str] = []
    membership_evaluator = None
    if campus_profile is not None:
        profile_name = str(campus_profile.get("name") or "")
        if campus and profile_name != campus:
            problems.append(
                f"campus policy belongs to {profile_name!r}, expected {campus!r}"
            )
        else:
            campus = campus or profile_name
            try:
                membership_evaluator = CampusMembershipEvaluator(campus_profile)
            except (TypeError, ValueError) as exc:
                problems.append(f"invalid campus policy: {exc}")
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
        coordinates = None
        try:
            lng, lat = float(poi.get("lng")), float(poi.get("lat"))
            if not all(math.isfinite(value) for value in (lng, lat)):
                raise ValueError
            if not -180 <= lng <= 180 or not -90 <= lat <= 90:
                raise ValueError
            coordinates = (lng, lat)
        except (TypeError, ValueError):
            problems.append(f"poi[{index}] has invalid coordinates")
        if membership_evaluator is not None and coordinates is not None:
            compatible_coordinate_system = True
            coordinate_system = str(poi.get("coordinateSystem") or "").strip()
            if membership_evaluator.coordinate_system is not None and not coordinate_system:
                problems.append(
                    f"poi[{index}] coordinateSystem is required by campus spatial policy"
                )
                compatible_coordinate_system = False
            elif (coordinate_system and coordinate_system !=
                  membership_evaluator.effective_coordinate_system):
                problems.append(
                    f"poi[{index}] coordinateSystem {coordinate_system!r} does not match "
                    "campus spatial policy "
                    f"{membership_evaluator.effective_coordinate_system!r}"
                )
                compatible_coordinate_system = False
            if compatible_coordinate_system:
                try:
                    accepted, distance, decision = membership_evaluator.evaluate(coordinates)
                    if not accepted:
                        if decision["method"] == "polygon":
                            problems.append(
                                f"poi[{index}] is outside campus boundary "
                                f"({round(distance)} metres from campus center)"
                            )
                        else:
                            problems.append(
                                f"poi[{index}] is outside campus trust radius "
                                f"({round(distance)} > {round(decision['radiusMeters'])} metres)"
                            )
                except (TypeError, ValueError) as exc:
                    problems.append(f"poi[{index}] campus membership check failed: {exc}")
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
        pois, (profile.get("campus") or {}).get("name"),
        campus_profile=profile.get("campus") or {},
    )
