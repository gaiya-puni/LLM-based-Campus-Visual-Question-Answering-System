"""User-added POIs for one isolated build job.

系统漏掉少量地点时，普通用户可以自己在地图上补一个点。这类点只写进任务私有层
``user_added_pois.json``：

- 参与该用户自己的临时热图（和 ``preview_selection.json`` 同一条链路）；
- 不写 ``normalized_pois.json``，因此不会混进采集候选池；
- 不写 ``approved_pois.json``，没有管理员令牌就无法进入正式数据；
- 管理员在审核面板显式通过后才会转正，转正时从本层移除，避免同一地点出现两份。

与自动采集的候选相比，用户补点缺少外部证据，因此固定 ``source="manual"``、
较低 ``confidence``，并在 ``evidence`` 里标明未经核验。质量分仍由现有打分器统一计算，
不在这里伪造"校园证据/来源可信性"。
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .boundary import CampusMembershipEvaluator
from .formal_asset_io import atomic_write_json
from .normalize import normalize_candidate
from .selection import load_selection, save_selection


USER_POI_FILE = "user_added_pois.json"
MAX_USER_POIS = 20
MIN_SEPARATION_METERS = 15.0
USER_POI_CONFIDENCE = 0.45
ALLOWED_SCENES = {"walk", "date", "photo", "flower", "flower_viewing"}

_NAME_LIMIT = 40
_SUBCATEGORY_LIMIT = 20
_TEXT_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return copy.deepcopy(default)


def _short_text(payload: dict, key: str, limit: int, default: str = "") -> str:
    value = payload.get(key)
    text = str(value).strip() if value is not None else default
    if len(text) > limit:
        raise ValueError(f"{key} 超过 {limit} 字，请精简后再提交")
    return text


def _coordinates(payload: dict) -> tuple[float, float]:
    try:
        lng = float(payload.get("lng"))
        lat = float(payload.get("lat"))
    except (TypeError, ValueError) as exc:
        raise ValueError("经度和纬度必须是数字") from exc
    if not (math.isfinite(lng) and math.isfinite(lat)):
        raise ValueError("经度和纬度必须是有效数字")
    return lng, lat


def _distance_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    lng1, lat1 = left
    lng2, lat2 = right
    rad = math.pi / 180
    dlat = (lat2 - lat1) * rad
    dlng = (lng2 - lng1) * rad
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlng / 2) ** 2)
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def load_user_added(output: str | Path) -> list[dict]:
    """Return this job's user-added POIs (never formal data)."""
    raw = _read(Path(output) / USER_POI_FILE, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _job_candidates(root: Path) -> list[dict]:
    """Everything a preview can see: harvest candidates plus user additions."""
    normalized = _read(root / "normalized_pois.json", [])
    normalized = [item for item in normalized if isinstance(item, dict)] \
        if isinstance(normalized, list) else []
    return normalized + load_user_added(root)


def _reject_near_duplicate(root: Path, candidate: dict) -> None:
    name = str(candidate.get("name") or "")
    point = (float(candidate["lng"]), float(candidate["lat"]))
    for existing in _job_candidates(root):
        if str(existing.get("id")) == str(candidate.get("id")):
            raise ValueError(f"该地点已在本次任务中（{existing.get('name')}），请直接勾选它")
        try:
            other = (float(existing.get("lng")), float(existing.get("lat")))
        except (TypeError, ValueError):
            continue
        distance = _distance_m(point, other)
        if str(existing.get("name") or "").strip() == name and distance < 5:
            raise ValueError(f"该地点已在本次任务中（{existing.get('name')}），请直接勾选它")
        if distance < MIN_SEPARATION_METERS:
            raise ValueError(
                f"距离已有地点「{existing.get('name')}」仅 {distance:.0f} 米，"
                "请移动坐标或直接勾选已有地点"
            )


def add_user_added(output: str | Path, payload: dict, profile: dict, *,
                   theme: str | None = None) -> dict:
    """Validate and append one user-added POI, then auto-select it for preview."""
    root = Path(output)
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是对象")
    campus = (profile.get("campus") or {})
    campus_name = str(campus.get("name") or "")
    if not campus_name:
        raise ValueError("任务缺少校区信息，无法校验补点位置")

    existing = load_user_added(root)
    if len(existing) >= MAX_USER_POIS:
        raise ValueError(f"本次任务最多手动添加 {MAX_USER_POIS} 个地点，请先删除不用的补点")

    name = _short_text(payload, "name", _NAME_LIMIT)
    if not name:
        raise ValueError("请填写地点名称")
    category = str(payload.get("category") or "scene").strip()
    if category not in {"plant", "scene"}:
        raise ValueError("地点类别只能是 plant（植物）或 scene（场景）")
    sub_category = _short_text(payload, "subCategory", _SUBCATEGORY_LIMIT, "用户标注") or "用户标注"
    location_name = _short_text(payload, "locationName", _NAME_LIMIT, name) or name
    note = _short_text(payload, "text", _TEXT_LIMIT)

    scenes = payload.get("scenes") or []
    if isinstance(scenes, str):
        scenes = [scenes]
    if not isinstance(scenes, list):
        raise ValueError("scenes 必须是数组")
    scenes = list(dict.fromkeys(str(item).strip() for item in scenes if str(item).strip()))
    unknown_scenes = [item for item in scenes if item not in ALLOWED_SCENES]
    if unknown_scenes:
        raise ValueError("未知场景：" + "、".join(unknown_scenes[:3]))

    lng, lat = _coordinates(payload)
    candidate = {
        "name": name,
        "locationName": location_name,
        "category": category,
        "subCategory": sub_category,
        "lng": lng,
        "lat": lat,
        "campus": campus_name,
        "scenes": scenes,
        "tags": ["用户补点", sub_category],
        "text": note or f"{name} {location_name} {sub_category} {campus_name} 用户补点",
        "source": "manual",
        "sourceUrls": [],
        "confidence": USER_POI_CONFIDENCE,
        "verified": False,
        "coordinateSystem": str(campus.get("coordinateSystem") or "GCJ-02"),
        "evidence": {
            "userAdded": True,
            "note": "用户手动标注，未经外部来源核验",
            "createdAt": _now(),
        },
    }
    evaluator = CampusMembershipEvaluator(campus)
    try:
        item = normalize_candidate(
            candidate, campus_name, 0,
            membership_evaluator=evaluator,
            theme=(str(theme).strip() if theme else None),
        )
    except ValueError as exc:
        if str(exc).startswith("outside campus"):
            raise ValueError("该位置不在校区范围内，请把点放在校园内再试") from exc
        raise
    _reject_near_duplicate(root, item)

    item["userAdded"] = True
    item["source"] = "manual"
    item["evidence"] = {
        **(item.get("evidence") or {}),
        "userAdded": True,
        "note": "用户手动标注，未经外部来源核验",
        "createdAt": _now(),
    }
    items = existing + [item]
    atomic_write_json(root / USER_POI_FILE, items)

    # 新补的点默认参与用户自己的临时热图，否则用户还要再勾一次才知道效果。
    candidates = _job_candidates(root)
    selected = load_selection(root, candidates)["selectedIds"]
    if item["id"] not in selected:
        selected.append(item["id"])
    selection = save_selection(root, selected, candidates)
    return {"item": item, "items": items, "count": len(items),
            "selection": selection, "maxUserPois": MAX_USER_POIS}


def remove_user_added(output: str | Path, poi_id: str) -> dict:
    """Delete one user-added POI and drop it from the preview selection."""
    root = Path(output)
    target = str(poi_id or "").strip()
    items = load_user_added(root)
    remaining = [item for item in items if str(item.get("id")) != target]
    if len(remaining) == len(items):
        raise LookupError("该地点不是本任务手动添加的点，无法删除")
    atomic_write_json(root / USER_POI_FILE, remaining)
    candidates = _job_candidates(root)
    selected = [value for value in load_selection(root, candidates)["selectedIds"]
                if value != target]
    selection = save_selection(root, selected, candidates)
    return {"items": remaining, "count": len(remaining),
            "selection": selection, "removedId": target}
