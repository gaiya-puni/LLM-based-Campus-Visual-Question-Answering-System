"""Safe, explicit publication of reviewed campus plant and scene candidates.

Publication is deliberately separate from preview generation.  It only runs
after a caller has requested a diff and explicitly confirms the same diff.
Formal assets are backed up before the atomic write; semantic indexes and
heatmap caches are marked stale and must be rebuilt by the existing scripts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .profile import write_json
from .validate import validate_pois


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "webapp" / "backend"
FORMAL_POI_PATH = BACKEND / "campus_pois.json"
FORMAL_SCENE_PATH = BACKEND / "scene_pois.json"
FORMAL_CAMPUSES_PATH = BACKEND / "campuses.json"
BACKUP_ROOT = BACKEND / "userdata" / "campus_publish_backups"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_TRANSIENT_FIELDS = {"autoApprove", "reviewStatus", "reviewedAt"}


def _approved(root: Path) -> list[dict]:
    values = _read(root / "approved_pois.json", [])
    approved = []
    for item in values:
        if not isinstance(item, dict) or item.get("category") not in {"plant", "scene"}:
            continue
        # Registry plants already exist in the formal file.  They are loaded
        # only to make previews complete and must never be republished as a
        # mass update merely because the review bundle adds runtime metadata.
        if item.get("source") in {"campus_registry", "scene_registry"} or item.get("autoApprove"):
            continue
        approved.append({key: value for key, value in item.items()
                         if key not in _TRANSIENT_FIELDS})
    return approved


def _asset_plan(current: list[dict], approved: list[dict], campus: str) -> dict:
    current_by_id = {str(item.get("id")): item for item in current
                     if isinstance(item, dict) and item.get("id")}
    additions, updates, unchanged = [], [], []
    for item in approved:
        if item.get("campus") != campus:
            continue
        item_id = str(item.get("id"))
        old = current_by_id.get(item_id)
        if old is None:
            additions.append(item)
        elif old != item:
            updates.append({"id": item_id, "before": old, "after": item})
        else:
            unchanged.append(item_id)
    next_items = list(current)
    index = {str(item.get("id")): i for i, item in enumerate(next_items)
             if isinstance(item, dict) and item.get("id")}
    for item in additions:
        index[str(item["id"])] = len(next_items)
        next_items.append(item)
    for change in updates:
        next_items[index[change["id"]]] = change["after"]
    return {"added": additions, "updated": updates, "unchanged": unchanged,
            "current": current, "next": next_items}


def build_publish_plan(root: str | Path) -> dict:
    root = Path(root)
    profile = _read(root / "campus_profile.json", {})
    campus = str((profile.get("campus") or {}).get("name") or "")
    config = _read(FORMAL_CAMPUSES_PATH, {})
    known = {str(item.get("name")) for item in (config.get("campuses") or []) if isinstance(item, dict)}
    if not campus:
        raise ValueError("campus profile is missing campus name")
    if campus not in known:
        raise ValueError("new campuses require separate configuration review before publication")
    current_plants = _read(FORMAL_POI_PATH, [])
    current_scenes = _read(FORMAL_SCENE_PATH, [])
    if not isinstance(current_plants, list):
        raise ValueError("formal campus_pois.json must be a JSON array")
    if not isinstance(current_scenes, list):
        raise ValueError("formal scene_pois.json must be a JSON array")
    approved = _approved(root)
    plant_plan = _asset_plan(current_plants,
                             [item for item in approved if item.get("category") == "plant"], campus)
    scene_plan = _asset_plan(current_scenes,
                             [item for item in approved if item.get("category") == "scene"], campus)
    additions = [*plant_plan["added"], *scene_plan["added"]]
    updates = [*plant_plan["updated"], *scene_plan["updated"]]
    changed = [*additions, *[item["after"] for item in updates]]
    problems = validate_pois(changed, campus) if changed else []
    before_hashes = {"plants": _digest(current_plants), "scenes": _digest(current_scenes)}
    after_hashes = {"plants": _digest(plant_plan["next"]), "scenes": _digest(scene_plan["next"])}
    return {
        "campus": campus,
        "approvedCount": len(approved),
        "added": additions,
        "updated": updates,
        "plantAdded": plant_plan["added"],
        "plantUpdated": plant_plan["updated"],
        "sceneAdded": scene_plan["added"],
        "sceneUpdated": scene_plan["updated"],
        "unchangedCount": len(plant_plan["unchanged"]) + len(scene_plan["unchanged"]),
        "validationProblems": problems,
        "beforeHashes": before_hashes,
        "afterHashes": after_hashes,
        "beforeHash": _digest(before_hashes),
        "afterHash": _digest(after_hashes),
        "requiresRebuild": bool(additions or updates),
    }


def publish(root: str | Path, *, confirm: bool, expected_after_hash: str | None = None) -> dict:
    if not confirm:
        raise ValueError("publication requires explicit confirm=true")
    root = Path(root)
    plan = build_publish_plan(root)
    if plan["validationProblems"]:
        raise ValueError("publish candidates failed validation: " + "; ".join(plan["validationProblems"]))
    if expected_after_hash and expected_after_hash != plan["afterHash"]:
        raise ValueError("publish plan changed; refresh the preview before confirming")
    if not plan["requiresRebuild"]:
        return {"published": False, "reason": "no formal asset changes", **plan}
    current_plants = _read(FORMAL_POI_PATH, [])
    current_scenes = _read(FORMAL_SCENE_PATH, [])
    current_hashes = {"plants": _digest(current_plants), "scenes": _digest(current_scenes)}
    if current_hashes != plan["beforeHashes"]:
        raise ValueError("formal POI assets changed; refresh the publish plan before confirming")
    plant_plan = _asset_plan(current_plants, plan["plantAdded"] +
                             [item["after"] for item in plan["plantUpdated"]], plan["campus"])
    scene_plan = _asset_plan(current_scenes, plan["sceneAdded"] +
                             [item["after"] for item in plan["sceneUpdated"]], plan["campus"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(FORMAL_POI_PATH, backup / FORMAL_POI_PATH.name)
    shutil.copy2(FORMAL_SCENE_PATH, backup / FORMAL_SCENE_PATH.name)
    if plan["plantAdded"] or plan["plantUpdated"]:
        write_json(FORMAL_POI_PATH, plant_plan["next"])
    if plan["sceneAdded"] or plan["sceneUpdated"]:
        write_json(FORMAL_SCENE_PATH, scene_plan["next"])
    write_json(backup / "publish_plan.json", plan)
    return {"published": True, "backup": str(backup.relative_to(BACKEND)), **plan,
            "message": "formal POIs updated; rebuild semantic index and heatmap caches before serving them"}
