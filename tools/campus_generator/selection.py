"""Per-job, non-formal POI selections used for user heatmap previews.

Selections deliberately live beside a build job and never change
``approved_pois.json`` or any formal campus asset.  This keeps the public
"try a heatmap" flow separate from the administrator publication workflow.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from .formal_asset_io import atomic_write_json


SELECTION_FILE = "preview_selection.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return copy.deepcopy(default)


def load_selection(output: str | Path, candidates: list[dict] | None = None) -> dict:
    """Return the current temporary selection, defaulting to all valid candidates."""
    root = Path(output)
    raw = _read(root / SELECTION_FILE, {})
    candidate_ids = {
        str(item.get("id")) for item in (candidates or [])
        if isinstance(item, dict) and item.get("id")
    }
    selected = raw.get("selectedIds") if isinstance(raw, dict) else None
    if selected is None:
        selected_ids = sorted(candidate_ids)
    elif isinstance(selected, list):
        selected_ids = sorted({str(value) for value in selected} & candidate_ids)
    else:
        selected_ids = []
    return {
        "selectedIds": selected_ids,
        "updatedAt": raw.get("updatedAt") if isinstance(raw, dict) else None,
    }


def save_selection(output: str | Path, selected_ids: list[str], candidates: list[dict]) -> dict:
    """Validate and atomically save only IDs from this job's candidate bundle."""
    if not isinstance(selected_ids, list):
        raise ValueError("selectedIds must be a list")
    candidate_ids = {
        str(item.get("id")) for item in candidates
        if isinstance(item, dict) and item.get("id")
    }
    normalized = sorted({str(value) for value in selected_ids if str(value)} )
    unknown = [value for value in normalized if value not in candidate_ids]
    if unknown:
        raise ValueError("selectedIds contains unknown candidate: " + ", ".join(unknown[:3]))
    result = {"updatedAt": _now(), "selectedIds": normalized}
    atomic_write_json(Path(output) / SELECTION_FILE, result)
    return result
