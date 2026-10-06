"""Safe, explicit publication of reviewed campus plant and scene candidates.

Publication is deliberately separate from preview generation.  It only runs
after a caller has requested a diff and explicitly confirms the same diff.
Formal assets are backed up before the atomic write; semantic indexes and
heatmap caches are marked stale and must be rebuilt by the existing scripts.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .formal_asset_io import (atomic_copy, atomic_write_json, formal_asset_lock,
                              sync_directory)
from .validate import validate_pois


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "webapp" / "backend"
FORMAL_POI_PATH = BACKEND / "campus_pois.json"
FORMAL_SCENE_PATH = BACKEND / "scene_pois.json"
FORMAL_CAMPUSES_PATH = BACKEND / "campuses.json"
BACKUP_ROOT = BACKEND / "userdata" / "campus_publish_backups"

_CAMPUS_POLICY_FIELDS = (
    "name", "slug", "school", "center", "trustRadiusM", "coordinateSystem",
    "boundary", "boundarySource", "boundaryConfidence",
)


def _read_required(path: Path, label: str):
    """Read required JSON without turning damage into an empty data set."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{label} could not be read: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _formal_campus(config: dict, campus: str) -> dict:
    if not isinstance(config, dict) or not isinstance(config.get("campuses"), list):
        raise ValueError("formal campuses.json must contain a campuses array")
    matches = [item for item in (config.get("campuses") or [])
               if isinstance(item, dict) and str(item.get("name")) == campus]
    if not matches:
        raise ValueError("new campuses require separate configuration review before publication")
    if len(matches) > 1:
        raise ValueError(f"formal campus configuration contains duplicate name {campus!r}")
    return matches[0]


def _campus_policy_hash(campus: dict) -> str:
    policy = {key: campus[key] for key in _CAMPUS_POLICY_FIELDS if key in campus}
    return _digest(policy)


def _formal_directory() -> Path:
    targets = (FORMAL_POI_PATH, FORMAL_SCENE_PATH, FORMAL_CAMPUSES_PATH)
    for target in targets:
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("formal asset paths and their parent must not be symlinks")
    parents = {target.resolve().parent for target in targets}
    if len(parents) != 1:
        raise ValueError("formal campus and POI assets must share one directory")
    return parents.pop()


def _lock_path() -> Path:
    return _formal_directory() / ".campus_publish.lock"


def _transaction_path() -> Path:
    return _formal_directory() / ".campus_publish_transaction.json"


def _clear_transaction(path: Path) -> None:
    existed = path.exists()
    path.unlink(missing_ok=True)
    if existed:
        sync_directory(path.parent)


def _read_asset_pair(plant_path: Path, scene_path: Path, label: str) -> tuple[list[dict], list[dict]]:
    plants = _read_required(plant_path, f"{label} campus_pois.json")
    scenes = _read_required(scene_path, f"{label} scene_pois.json")
    if not isinstance(plants, list):
        raise ValueError(f"{label} campus_pois.json must be a JSON array")
    if not isinstance(scenes, list):
        raise ValueError(f"{label} scene_pois.json must be a JSON array")
    return plants, scenes


def _read_formal_assets() -> tuple[list[dict], list[dict]]:
    return _read_asset_pair(FORMAL_POI_PATH, FORMAL_SCENE_PATH, "formal")


def _asset_hashes_for_paths(plant_path: Path, scene_path: Path, label: str) -> dict[str, str]:
    plants, scenes = _read_asset_pair(plant_path, scene_path, label)
    return {"plants": _digest(plants), "scenes": _digest(scenes)}


def _asset_hashes() -> dict[str, str]:
    return _asset_hashes_for_paths(FORMAL_POI_PATH, FORMAL_SCENE_PATH, "formal")


def _journal_details(
        journal: object,
) -> tuple[dict[str, str], dict[str, str], Path, str, str]:
    if not isinstance(journal, dict) or journal.get("version") != 2:
        raise ValueError("campus publication transaction journal is invalid")
    if journal.get("state") not in {"prepared", "committed"}:
        raise ValueError("campus publication transaction state is invalid")
    targets = journal.get("targets")
    expected_targets = {
        "plants": str(FORMAL_POI_PATH.resolve()),
        "scenes": str(FORMAL_SCENE_PATH.resolve()),
        "campuses": str(FORMAL_CAMPUSES_PATH.resolve()),
    }
    if targets != expected_targets:
        raise ValueError("campus publication transaction targets do not match formal assets")
    before = journal.get("beforeHashes")
    after = journal.get("afterHashes")
    for label, hashes in (("before", before), ("after", after)):
        if (not isinstance(hashes, dict)
                or set(hashes) != {"plants", "scenes"}
                or not all(isinstance(value, str) and value for value in hashes.values())):
            raise ValueError(f"campus publication transaction {label} hashes are invalid")
    backup_value = journal.get("backup")
    if not isinstance(backup_value, str) or not backup_value:
        raise ValueError("campus publication transaction backup is invalid")
    backup = Path(backup_value).resolve()
    try:
        backup.relative_to(BACKUP_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("campus publication transaction backup is outside backup root") from exc
    campus = journal.get("campus")
    policy_hash = journal.get("campusPolicyHash")
    if not isinstance(campus, str) or not campus:
        raise ValueError("campus publication transaction campus is invalid")
    if not isinstance(policy_hash, str) or not policy_hash:
        raise ValueError("campus publication transaction policy hash is invalid")
    return before, after, backup, campus, policy_hash


def _current_campus_policy_hash(campus: str) -> str:
    config = _read_required(FORMAL_CAMPUSES_PATH, "formal campuses.json")
    return _campus_policy_hash(_formal_campus(config, campus))


def _recover_interrupted_publish() -> str | None:
    """Finish or roll back a durable transaction left by an interrupted process."""
    transaction_path = _transaction_path()
    if not transaction_path.exists():
        return None
    journal = _read_required(
        transaction_path, "campus publication transaction journal",
    )
    before_hashes, after_hashes, backup, campus, policy_hash = _journal_details(journal)
    try:
        current_hashes = _asset_hashes()
    except ValueError:
        current_hashes = None
    if current_hashes == before_hashes:
        _clear_transaction(transaction_path)
        return "not-started"
    if (current_hashes == after_hashes
            and _current_campus_policy_hash(campus) == policy_hash):
        _clear_transaction(transaction_path)
        return "committed"

    plant_backup = backup / FORMAL_POI_PATH.name
    scene_backup = backup / FORMAL_SCENE_PATH.name
    if not plant_backup.is_file() or not scene_backup.is_file():
        raise RuntimeError(
            "interrupted campus publication cannot be recovered because its backup is incomplete"
        )
    try:
        backup_hashes = _asset_hashes_for_paths(
            plant_backup, scene_backup, "publication backup",
        )
    except ValueError as exc:
        raise RuntimeError("interrupted campus publication backup is invalid") from exc
    if backup_hashes != before_hashes:
        raise RuntimeError("interrupted campus publication backup hash mismatch")
    atomic_copy(plant_backup, FORMAL_POI_PATH)
    atomic_copy(scene_backup, FORMAL_SCENE_PATH)
    if _asset_hashes() != before_hashes:
        raise RuntimeError("interrupted campus publication rollback verification failed")
    _clear_transaction(transaction_path)
    return "rolled-back"


@contextmanager
def formal_assets_guard():
    """Lock the formal asset pair and recover any interrupted publication."""
    with formal_asset_lock(_lock_path()):
        recovery = _recover_interrupted_publish()
        yield recovery


_TRANSIENT_FIELDS = {"autoApprove", "reviewStatus", "reviewedAt"}


def _approved(root: Path) -> list[dict]:
    values = _read_required(root / "approved_pois.json", "approved_pois.json")
    if not isinstance(values, list):
        raise ValueError("approved_pois.json must be a JSON array")
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
    current_by_id = {}
    for item in current:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        item_id = str(item["id"])
        if item_id in current_by_id:
            raise ValueError(f"formal asset contains duplicate POI id {item_id!r}")
        current_by_id[item_id] = item
    additions, updates, unchanged = [], [], []
    for item in approved:
        if item.get("campus") != campus:
            continue
        item_id = str(item.get("id"))
        old = current_by_id.get(item_id)
        if old is None:
            additions.append(item)
        elif old.get("campus") != campus:
            raise ValueError(
                f"POI id {item_id!r} is already owned by campus {old.get('campus')!r}"
            )
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


def _validate_asset_id_ownership(plants: list[dict], scenes: list[dict],
                                 approved: list[dict]) -> None:
    owners: dict[str, tuple[str, dict]] = {}
    for asset, values in (("plant", plants), ("scene", scenes)):
        for item in values:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            item_id = str(item["id"])
            if item_id in owners:
                raise ValueError(f"formal assets contain duplicate POI id {item_id!r}")
            owners[item_id] = (asset, item)
    for item in approved:
        item_id = str(item.get("id") or "")
        owner = owners.get(item_id)
        if owner is None:
            continue
        expected_asset = "plant" if item.get("category") == "plant" else "scene"
        if owner[0] != expected_asset:
            raise ValueError(
                f"POI id {item_id!r} is already owned by the {owner[0]} asset"
            )


def _build_publish_plan_unlocked(root: str | Path) -> dict:
    root = Path(root)
    profile = _read_required(root / "campus_profile.json", "campus_profile.json")
    if not isinstance(profile, dict):
        raise ValueError("campus_profile.json must be a JSON object")
    campus = str((profile.get("campus") or {}).get("name") or "")
    school = str((profile.get("school") or {}).get("id") or "")
    config = _read_required(FORMAL_CAMPUSES_PATH, "formal campuses.json")
    if not campus:
        raise ValueError("campus profile is missing campus name")
    if not school:
        raise ValueError("campus profile is missing school id")
    formal_campus = _formal_campus(config, campus)
    if str(formal_campus.get("school") or "") != school:
        raise ValueError(
            f"campus {campus!r} belongs to school {formal_campus.get('school')!r}, "
            f"not {school!r}"
        )
    campus_policy_hash = _campus_policy_hash(formal_campus)
    current_plants, current_scenes = _read_formal_assets()
    approved = _approved(root)
    _validate_asset_id_ownership(current_plants, current_scenes, approved)
    plant_plan = _asset_plan(current_plants,
                             [item for item in approved if item.get("category") == "plant"], campus)
    scene_plan = _asset_plan(current_scenes,
                             [item for item in approved if item.get("category") == "scene"], campus)
    additions = [*plant_plan["added"], *scene_plan["added"]]
    updates = [*plant_plan["updated"], *scene_plan["updated"]]
    problems = validate_pois(approved, campus, campus_profile=formal_campus)
    before_hashes = {"plants": _digest(current_plants), "scenes": _digest(current_scenes)}
    after_hashes = {"plants": _digest(plant_plan["next"]), "scenes": _digest(scene_plan["next"])}
    before_commitment = {"assets": before_hashes, "campusPolicyHash": campus_policy_hash}
    after_commitment = {"assets": after_hashes, "campusPolicyHash": campus_policy_hash}
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
        "campusPolicyHash": campus_policy_hash,
        "beforeHash": _digest(before_commitment),
        "afterHash": _digest(after_commitment),
        "requiresRebuild": bool(additions or updates),
    }


def build_publish_plan(root: str | Path) -> dict:
    """Build a consistent plan after recovering and locking formal assets."""
    with formal_assets_guard():
        return _build_publish_plan_unlocked(root)


def publish(root: str | Path, *, confirm: bool, expected_after_hash: str | None = None) -> dict:
    if confirm is not True:
        raise ValueError("publication requires explicit confirm=true")
    if not isinstance(expected_after_hash, str) or not expected_after_hash.strip():
        raise ValueError("publication requires expectedAfterHash from the publish plan")
    root = Path(root)
    expected_after_hash = expected_after_hash.strip()
    with formal_assets_guard():
        # Rebuild the whole plan only after the exclusive lock is held.  The
        # UI's hash is an optimistic-concurrency token for both assets and the
        # formal campus spatial policy.
        plan = _build_publish_plan_unlocked(root)
        if plan["validationProblems"]:
            raise ValueError(
                "publish candidates failed validation: " +
                "; ".join(plan["validationProblems"])
            )
        if expected_after_hash != plan["afterHash"]:
            raise ValueError("publish plan changed; refresh the preview before confirming")
        if not plan["requiresRebuild"]:
            return {"published": False, "reason": "no formal asset changes", **plan}

        current_plants, current_scenes = _read_formal_assets()
        current_policy_hash = _current_campus_policy_hash(plan["campus"])
        if current_policy_hash != plan["campusPolicyHash"]:
            raise ValueError(
                "formal campus policy changed; refresh the publish plan before confirming"
            )
        current_hashes = {"plants": _digest(current_plants),
                          "scenes": _digest(current_scenes)}
        if current_hashes != plan["beforeHashes"]:
            raise ValueError(
                "formal POI assets changed; refresh the publish plan before confirming"
            )
        plant_plan = _asset_plan(
            current_plants,
            plan["plantAdded"] + [item["after"] for item in plan["plantUpdated"]],
            plan["campus"],
        )
        scene_plan = _asset_plan(
            current_scenes,
            plan["sceneAdded"] + [item["after"] for item in plan["sceneUpdated"]],
            plan["campus"],
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_root_existed = BACKUP_ROOT.exists()
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        if not backup_root_existed:
            sync_directory(BACKUP_ROOT.parent)
        backup = BACKUP_ROOT / stamp
        backup.mkdir(exist_ok=False)
        sync_directory(backup.parent)
        plant_backup = backup / FORMAL_POI_PATH.name
        scene_backup = backup / FORMAL_SCENE_PATH.name
        atomic_copy(FORMAL_POI_PATH, plant_backup)
        atomic_copy(FORMAL_SCENE_PATH, scene_backup)
        atomic_write_json(backup / "publish_plan.json", plan)
        backup_hashes = _asset_hashes_for_paths(
            plant_backup, scene_backup, "publication backup",
        )
        if backup_hashes != plan["beforeHashes"]:
            raise RuntimeError("publication backup verification failed")

        transaction_path = _transaction_path()
        journal = {
            "version": 2,
            "state": "prepared",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "backup": str(backup.resolve()),
            "campus": plan["campus"],
            "campusPolicyHash": plan["campusPolicyHash"],
            "targets": {
                "plants": str(FORMAL_POI_PATH.resolve()),
                "scenes": str(FORMAL_SCENE_PATH.resolve()),
                "campuses": str(FORMAL_CAMPUSES_PATH.resolve()),
            },
            "beforeHashes": plan["beforeHashes"],
            "afterHashes": plan["afterHashes"],
        }
        journal_written = False
        try:
            atomic_write_json(transaction_path, journal)
            journal_written = True
            if plan["plantAdded"] or plan["plantUpdated"]:
                atomic_write_json(FORMAL_POI_PATH, plant_plan["next"])
            if plan["sceneAdded"] or plan["sceneUpdated"]:
                atomic_write_json(FORMAL_SCENE_PATH, scene_plan["next"])
            if _asset_hashes() != plan["afterHashes"]:
                raise RuntimeError("formal POI asset verification failed after publication")
            if _current_campus_policy_hash(plan["campus"]) != plan["campusPolicyHash"]:
                raise ValueError(
                    "formal campus policy changed during publication; assets were rolled back"
                )
            atomic_write_json(transaction_path, dict(journal, state="committed"))
            _clear_transaction(transaction_path)
        except BaseException as publish_error:
            if journal_written:
                try:
                    atomic_copy(plant_backup, FORMAL_POI_PATH)
                    atomic_copy(scene_backup, FORMAL_SCENE_PATH)
                    if _asset_hashes() != plan["beforeHashes"]:
                        raise RuntimeError("formal POI rollback verification failed")
                    _clear_transaction(transaction_path)
                except BaseException as rollback_error:
                    raise RuntimeError(
                        "campus publication failed "
                        f"({type(publish_error).__name__}) and rollback failed; "
                        f"recovery journal retained at {transaction_path}"
                    ) from rollback_error
            raise

        try:
            backup_label = str(backup.relative_to(BACKEND))
        except ValueError:
            backup_label = str(backup)
        return {
            "published": True,
            "backup": backup_label,
            **plan,
            "message": (
                "formal POIs updated; rebuild semantic index and heatmap caches "
                "before serving them"
            ),
        }
