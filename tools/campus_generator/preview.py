"""Build isolated heatmap previews from approved candidate POIs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .profile import write_json


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "webapp" / "backend"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_common_sources(workspace: Path) -> None:
    for name in ("heatmap_config.json", "scene_profiles.json", "scene_heatmaps.py",
                 "build_scene_heatmaps.py", "semantic_retrieval.py"):
        shutil.copy2(BACKEND / name, workspace / name)
    shutil.copy2(ROOT / "data" / "all_templates.json", workspace / "all_templates.json")


def _write_preview_pois(workspace: Path, profile: dict, approved: list[dict]) -> Path:
    campus = profile["campus"]
    config = _read(BACKEND / "heatmap_config.json")
    campus_name = campus["name"]
    existing = config.get("campuses", {}).get(campus_name)
    if not existing:
        config.setdefault("campuses", {})[campus_name] = {
            "slug": campus["slug"],
            "center": campus["center"],
            "auditRadiusMeters": campus["trustRadiusM"],
        }
    else:
        config["campuses"][campus_name] = dict(existing, slug=campus["slug"],
                                                center=campus["center"],
                                                auditRadiusMeters=campus["trustRadiusM"])
    write_json(workspace / "heatmap_config.json", config)
    prepared = []
    for item in approved:
        poi = dict(item)
        # A reviewed scene POI without an explicit scene label is useful for a
        # preview, but must not silently disappear from every scene gate.
        if poi.get("category") != "plant" and not poi.get("scenes"):
            poi["scenes"] = ["walk", "date", "photo"]
        prepared.append(poi)
    poi_path = workspace / f"{campus['slug']}_pois.json"
    write_json(poi_path, prepared)
    return poi_path


def build_preview(output: str | Path) -> dict:
    root = Path(output)
    profile = _read(root / "campus_profile.json")
    approved = _read(root / "approved_pois.json")
    approved = [item for item in approved if isinstance(item, dict) and
                item.get("category") in {"plant", "scene"}]
    if len(approved) < 3:
        raise ValueError("at least 3 approved POIs are required for a heatmap preview")
    workspace = root / "preview_workspace"
    cache = root / "preview_heatmap_cache"
    workspace.mkdir(parents=True, exist_ok=True)
    _copy_common_sources(workspace)
    _write_preview_pois(workspace, profile, approved)
    command = [sys.executable, str(BACKEND / "build_scene_heatmaps.py"),
               "--base-dir", str(workspace), "--output-dir", str(cache)]
    completed = subprocess.run(command, cwd=str(BACKEND), capture_output=True,
                               text=True, timeout=900, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "heatmap preview failed").strip()
        raise RuntimeError(detail[-3000:])
    manifest = cache / "manifest.json"
    if not manifest.exists():
        raise RuntimeError("heatmap builder did not produce a manifest")
    result = {
        "status": "completed",
        "workspace": "preview_workspace",
        "cache": "preview_heatmap_cache",
        "manifest": _read(manifest),
        "approvedCount": len(approved),
        "stdoutTail": (completed.stdout or "")[-2000:],
    }
    write_json(root / "preview_report.json", result)
    return result
