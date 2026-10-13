"""Campus profile loading, validation and runtime-config composition."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

from .boundary import validate_campus_boundary


SLUG_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")


def load_profile(path: str | Path) -> dict:
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("profile must be a JSON object")
    return data


def _finite_pair(value, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be [lng, lat]")
    try:
        lng, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numbers") from exc
    if not math.isfinite(lng) or not math.isfinite(lat):
        raise ValueError(f"{label} must contain finite numbers")
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ValueError(f"{label} is outside coordinate bounds")
    return lng, lat


def validate_profile(profile: dict) -> list[str]:
    """Return all profile problems instead of failing on the first one."""
    problems: list[str] = []
    school = profile.get("school")
    campus = profile.get("campus")
    if not isinstance(school, dict):
        problems.append("school must be an object")
    if not isinstance(campus, dict):
        problems.append("campus must be an object")
    if not isinstance(school, dict) or not isinstance(campus, dict):
        return problems

    for field in ("id", "name"):
        if not str(school.get(field) or "").strip():
            problems.append(f"school.{field} is required")
    if school.get("id") and not SLUG_RE.fullmatch(str(school["id"])):
        problems.append("school.id must be lowercase slug text")
    if not str(campus.get("name") or "").strip():
        problems.append("campus.name is required")
    slug = str(campus.get("slug") or "")
    if not SLUG_RE.fullmatch(slug):
        problems.append("campus.slug must be lowercase slug text")
    if not isinstance(campus.get("aliases", []), list):
        problems.append("campus.aliases must be a list")
    try:
        _finite_pair(campus.get("center"), "campus.center")
    except ValueError as exc:
        problems.append(str(exc))
    try:
        radius = float(campus.get("trustRadiusM"))
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("campus.trustRadiusM must be a positive number")
    problems.extend(validate_campus_boundary(campus))
    return problems


def runtime_config(profile: dict, existing: dict | None = None, *, allow_replace: bool = False) -> dict:
    """Return an existing-style multi-school config with one new campus.

    The function never mutates ``existing``.  It is intentionally independent
    of the Flask runtime so it can be used by a CLI, tests, or a future job
    worker.
    """
    problems = validate_profile(profile)
    if problems:
        raise ValueError("invalid profile: " + "; ".join(problems))
    result = copy.deepcopy(existing) if existing else {
        "brand": {
            "appTitle": "校园可视可答系统",
            "assistantName": "校园导览助手",
            "logo": "",
        },
        "schools": {},
        "defaultCampus": profile["campus"]["name"],
        "campuses": [],
    }
    result.setdefault("schools", {})
    result.setdefault("campuses", [])
    school = copy.deepcopy(profile["school"])
    school_id = school["id"]
    school.setdefault("enName", "")
    school.setdefault("aliases", [])
    school.setdefault("stopWords", [])
    result["schools"][school_id] = school

    campus = copy.deepcopy(profile["campus"])
    campus.setdefault("aliases", [])
    campus.setdefault("waterName", "")
    campus["school"] = school_id
    collisions = [
        item for item in result["campuses"]
        if item.get("name") == campus["name"] or item.get("slug") == campus["slug"]
    ]
    if collisions and not allow_replace:
        labels = ", ".join(str(item.get("name")) for item in collisions)
        raise ValueError(f"campus conflicts with existing configuration: {labels}")
    if collisions:
        result["campuses"] = [item for item in result["campuses"] if item not in collisions]
    result["campuses"].append(campus)
    result["defaultCampus"] = result.get("defaultCampus") or campus["name"]
    return result


def write_json(path: str | Path, data: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
