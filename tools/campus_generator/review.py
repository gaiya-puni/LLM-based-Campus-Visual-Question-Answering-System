"""Review state and publication candidates for an isolated build job."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from .normalize import normalize_candidate
from .profile import write_json
from .validate import validate_pois


ALLOWED_PATCH_FIELDS = {
    "name", "locationName", "category", "subCategory", "lng", "lat",
    "tags", "scenes", "text", "source", "sourceUrls", "confidence", "evidence",
    "meta",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError, TypeError):
        return copy.deepcopy(default)


def _write(path: Path, value) -> None:
    write_json(path, value)


def load_review(output: str | Path) -> dict:
    """Return normalized candidates, review statuses and approved POIs."""
    root = Path(output)
    profile = _read(root / "campus_profile.json", {})
    normalized = _read(root / "normalized_pois.json", [])
    pending = _read(root / "pending_pois.json", [])
    state = _read(root / "review_state.json", {})
    decisions = state.get("decisions") if isinstance(state, dict) else {}
    decisions = decisions if isinstance(decisions, dict) else {}
    items = []
    for candidate in normalized if isinstance(normalized, list) else []:
        item = copy.deepcopy(candidate)
        item["reviewStatus"] = decisions.get(
            str(candidate.get("id")),
            "approved" if candidate.get("autoApprove") or candidate.get("source") in
            {"campus_registry", "scene_registry"} else "pending",
        )
        items.append(item)
    approved = _read(root / "approved_pois.json", [])
    return {
        "campus": (profile.get("campus") or {}).get("name", ""),
        "school": (profile.get("school") or {}).get("name", ""),
        "profile": profile,
        "candidates": items,
        "pendingInvalid": pending if isinstance(pending, list) else [],
        "approved": approved if isinstance(approved, list) else [],
        "updatedAt": state.get("updatedAt") if isinstance(state, dict) else None,
    }


def apply_review(output: str | Path, decisions: list[dict]) -> dict:
    """Apply approve/reject/edit decisions and write ``approved_pois.json``.

    Only normalized candidates are eligible by default. An edit is normalized
    again, so changing coordinates or names cannot bypass validation.
    """
    root = Path(output)
    profile = _read(root / "campus_profile.json", {})
    campus = (profile.get("campus") or {}).get("name")
    normalized = _read(root / "normalized_pois.json", [])
    if not campus or not isinstance(normalized, list):
        raise ValueError("build output does not contain a valid candidate bundle")
    by_id = {str(item.get("id")): copy.deepcopy(item) for item in normalized if isinstance(item, dict)}
    previous = _read(root / "review_state.json", {})
    status = dict(previous.get("decisions") or {}) if isinstance(previous, dict) else {}
    approved_by_id = {str(item.get("id")): copy.deepcopy(item)
                      for item in (_read(root / "approved_pois.json", []) or [])
                      if isinstance(item, dict) and item.get("id")}
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    errors = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"decision[{index}] must be an object")
            continue
        candidate_id = str(decision.get("id") or "")
        action = str(decision.get("action") or "").lower()
        if candidate_id not in by_id:
            errors.append(f"decision[{index}] references an unknown candidate")
            continue
        if action not in {"approve", "reject", "pending"}:
            errors.append(f"decision[{index}] has invalid action")
            continue
        candidate = copy.deepcopy(by_id[candidate_id])
        patch = decision.get("patch") or {}
        if patch:
            if not isinstance(patch, dict):
                errors.append(f"decision[{index}].patch must be an object")
                continue
            unknown = set(patch) - ALLOWED_PATCH_FIELDS
            if unknown:
                errors.append(f"decision[{index}] has unsupported fields: {', '.join(sorted(unknown))}")
                continue
            candidate.update(patch)
        if action == "approve":
            try:
                approved = normalize_candidate(candidate, campus, index)
                approved["verified"] = True
                approved["reviewStatus"] = "approved"
                approved["reviewedAt"] = _now()
                approved_by_id[approved["id"]] = approved
                status[candidate_id] = "approved"
            except (TypeError, ValueError) as exc:
                errors.append(f"decision[{index}] cannot be approved: {exc}")
        elif action == "reject":
            status[candidate_id] = "rejected"
            approved_by_id.pop(candidate_id, None)
        else:
            status[candidate_id] = "pending"
            approved_by_id.pop(candidate_id, None)
    if errors:
        raise ValueError("; ".join(errors))
    approved = list(approved_by_id.values())
    problems = validate_pois(approved, campus)
    _write(root / "approved_pois.json", approved)
    _write(root / "review_state.json", {"updatedAt": _now(), "decisions": status,
                                         "approvedCount": len(approved),
                                         "validationProblems": problems})
    result = load_review(root)
    result["validationProblems"] = problems
    result["approvedCount"] = len(approved)
    result["reviewedCount"] = sum(value in {"approved", "rejected"} for value in status.values())
    return result
