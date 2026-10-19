"""Build isolated heatmap previews from approved candidate POIs."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from .boundary import covering_radius_m
from .profile import write_json
from .validate import validate_pois
from .selection import load_selection
from .user_pois import load_user_added


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "webapp" / "backend"

#: 生成页预览允许的场景。前四项与 `scene_heatmaps.SCENE_IDS` 保持一致，
#: 第五项 `density` 只存在于预览 workspace，不进入问答页缓存。
PREVIEW_SCENE_IDS = ("flower_viewing", "photo", "walk", "date", "density")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_common_sources(workspace: Path) -> None:
    for name in ("heatmap_config.json", "scene_profiles.json", "scene_heatmaps.py",
                 "build_scene_heatmaps.py", "semantic_retrieval.py"):
        shutil.copy2(BACKEND / name, workspace / name)
    shutil.copy2(ROOT / "data" / "all_templates.json", workspace / "all_templates.json")


def _write_preview_pois(workspace: Path, profile: dict, approved: list[dict]) -> Path:
    campus = profile["campus"]
    if campus.get("coordinateSystem") not in (None, "GCJ-02"):
        raise ValueError(
            "heatmap preview requires GCJ-02 coordinates; convert candidates explicitly first"
        )
    config = _read(BACKEND / "heatmap_config.json")
    # Campus-wide display coverage is a chat-only policy, not a preview score.
    for entry in config.get("campuses", {}).values():
        entry.pop("displayCoverage", None)
    # Reviewed campus landmarks are much sparser than the original plant data.
    # Enlarge their local neighbourhoods, not the image bounds: stretching the
    # raster would move hotspots away from the POIs that support them.
    # 2026-10-19：收紧凸包缓冲与核带宽，减少"热区比校园大一圈"的观感；
    # density 场景（混合请求）用更小的缓冲，让热点边界贴近点位。
    config.update(supportMeters=120, minInfluenceMeters=120, maxInfluenceMeters=300,
                  minimumCategoryActivation=0.2)
    # 预览专用"点位密度"：混合类型请求（建筑 + 植物 + 地标）没有单一场景语义，
    # 强行套 walk/photo 只会得到一层与点位无关的淡色。density 只写入预览 workspace，
    # 不会进入问答页的 SCENE_IDS / 正式缓存。
    scenes = config.setdefault("scenes", {})
    scenes.setdefault("density", "点位密度")
    overrides = config.setdefault("sceneCorpusOverrides", {})
    overrides.setdefault(
        "density",
        "校园混合类型点位分布密度。用于建筑、植物、地标等多类地点同时出现的请求；"
        "每个已选地点等权参与，不区分场景适宜度与植物观赏价值。",
    )
    campus_name = campus["name"]
    existing = config.get("campuses", {}).get(campus_name)
    preview_campus = dict(existing or {})
    preview_campus.update({
        "slug": campus["slug"],
        "center": campus["center"],
        # The heatmap builder still audits with a circle.  Cover the complete
        # trusted polygon so a valid point in an elongated campus is not lost.
        "auditRadiusMeters": math.ceil(covering_radius_m(campus)),
    })
    for key in ("coordinateSystem", "boundary", "boundarySource", "boundaryConfidence"):
        if key in campus:
            preview_campus[key] = campus[key]
    config.setdefault("campuses", {})[campus_name] = preview_campus
    write_json(workspace / "heatmap_config.json", config)
    prepared = []
    for item in approved:
        poi = dict(item)
        # A reviewed scene POI without an explicit scene label is useful for a
        # preview, but must not silently disappear from every scene gate.
        if poi.get("category") != "plant":
            scenes = list(poi.get("scenes") or []) or ["walk", "date", "photo"]
            # 预览专用"点位密度"需要所有已选点位参与；只在预览 workspace 的
            # POI 副本上追加，不改变正式资产。
            if "density" not in scenes:
                scenes.append("density")
            poi["scenes"] = scenes
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
    # Public previews use the caller's isolated selection when present.  The
    # formal approved bundle remains the backwards-compatible fallback for
    # older jobs and administrator publication previews.
    selection_path = root / "preview_selection.json"
    if selection_path.is_file():
        normalized = _read(root / "normalized_pois.json")
        candidates = [item for item in normalized if isinstance(item, dict)]
        # 用户手动补的点同样只是任务私有数据：它参与本次临时热图，但不进正式资产。
        candidates += load_user_added(root)
        by_id = {str(item.get("id")): item for item in approved + candidates
                 if item.get("id")}
        selected_ids = load_selection(root, candidates)["selectedIds"]
        approved = [by_id[item_id] for item_id in selected_ids if item_id in by_id]
    if len(approved) < 3:
        raise ValueError("at least 3 approved POIs are required for a heatmap preview")
    campus = profile.get("campus") or {}
    if campus.get("coordinateSystem") not in (None, "GCJ-02"):
        raise ValueError(
            "heatmap preview requires GCJ-02 coordinates; convert candidates explicitly first"
        )
    problems = validate_pois(
        approved, campus.get("name"), campus_profile=campus,
    )
    if problems:
        raise ValueError("preview candidates failed validation: " + "; ".join(problems))
    workspace = root / "preview_workspace"
    cache = root / "preview_heatmap_cache"
    workspace.mkdir(parents=True, exist_ok=True)
    _copy_common_sources(workspace)
    _write_preview_pois(workspace, profile, approved)
    command = [sys.executable, "-m", "tools.campus_generator.preview_heatmaps",
               "--base-dir", str(workspace), "--output-dir", str(cache)]
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True,
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
