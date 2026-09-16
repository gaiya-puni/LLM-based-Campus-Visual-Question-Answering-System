"""Build four scenes x two campuses. Downloads are opt-in; existing index untouched.

散步(walk)/约会(date) 使用两个可解释、且只依赖本场景输入的信号：
  1. 地名先验：prepare_geometry(scene=...) 按 locationName 生成逐点权重；
  2. 场景类别先验：fuse_fields 里的 scene_class_priors；
四类场景彼此独立构建，改变配置中的场景顺序不会改变任一场景结果。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from scene_heatmaps import (ALGORITHM_VERSION, SEASON_ORDER, audit_pois, build_map,
                           build_plant_priors, class_documents, fingerprint, load_config,
                           prepare_geometry)
from semantic_retrieval import DEFAULT_MODEL_NAME, build_scene_document, encode_documents

BASE = Path(__file__).resolve().parent
def write_json(path: Path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def load_pois() -> list[dict]:
    """植物 POI + 可选的 scene_pois.json（座椅/共青场等非植物场景 POI）。"""
    pois = json.loads((BASE / "campus_pois.json").read_text(encoding="utf-8"))
    scene_pois_path = BASE / "scene_pois.json"
    if scene_pois_path.exists():
        extra = json.loads(scene_pois_path.read_text(encoding="utf-8"))
        if isinstance(extra, list):
            pois = pois + extra
    return pois


def pois_for_scene(pois: list[dict], scene: str) -> list[dict]:
    """场景门控：植物始终参与；非植物 POI 只有其 scenes 声明包含该场景时才参与。

    赏花/拍照因此永远只拿到 plant，输出与旧版本一致。
    """
    selected = []
    for poi in pois:
        if (poi.get("category") or "plant") == "plant":
            selected.append(poi)
        elif scene in (poi.get("scenes") or []):
            selected.append(poi)
    return selected


def scene_class_weight_map(pois: list[dict], scene: str) -> dict:
    """Return uniform weights for verified scene POIs.

    Point-specific weights make it possible to tune a named location into first place.
    Until independent labels justify such weights, every verified scene class is equal.
    """
    weights = {}
    for poi in pois:
        if (poi.get("category") or "plant") == "plant":
            continue
        name = poi.get("subCategory")
        if not name:
            continue
        weights[name] = 1.0
    return weights


def scene_document(profiles: dict, config: dict, scene: str) -> str:
    """场景语料：优先使用 heatmap_config.json 的 sceneCorpusOverrides，
    以免改动 scene_profiles.json 影响规则引擎与语义索引。"""
    override = (config.get("sceneCorpusOverrides") or {}).get(scene)
    if override:
        return override
    return build_scene_document(profiles[scene])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true", help="allow first-time BGE download")
    args = parser.parse_args()
    started = time.perf_counter()
    config = load_config(BASE)
    raw = load_pois()
    pois, audit = audit_pois(raw, config)
    templates = json.loads((BASE / "../../data/all_templates.json").read_text(encoding="utf-8"))
    profiles = {p["id"]: p for p in json.loads((BASE / "scene_profiles.json").read_text(encoding="utf-8"))}
    documents = class_documents(pois, templates)
    plant_priors = build_plant_priors(templates, pois)
    scenes = list(config["scenes"])
    names = list(documents)
    from sentence_transformers import SentenceTransformer
    model_name = os.getenv("SEMANTIC_MODEL_PATH", DEFAULT_MODEL_NAME).strip()
    source_hash = fingerprint(BASE, model_name)
    model = SentenceTransformer(model_name, local_files_only=not args.allow_download)
    scene_vectors = encode_documents(model, [scene_document(profiles, config, s) for s in scenes])
    class_vectors = encode_documents(model, list(documents.values()))
    matrix = scene_vectors @ class_vectors.T
    similarities = {s: dict(zip(names, map(float, matrix[i]))) for i, s in enumerate(scenes)}
    out = BASE / "heatmap_cache"
    out.mkdir(exist_ok=True)
    timings = {}
    total_maps = 0
    for campus, campus_config in config["campuses"].items():
        campus_start = time.perf_counter()
        for scene in scenes:
            # 每个场景按自己的点位集合与地名先验建几何。赏花不在地名先验场景内，
            # 其点位集合与逐点权重都与旧版本一致，输出不变。
            scene_pois = pois_for_scene(pois, scene)
            geometry = prepare_geometry(scene_pois, campus, config, scene=scene)
            class_weights = scene_class_weight_map(scene_pois, scene)
            if scene in ("flower_viewing", "photo"):
                # 按季节生成多套缓存，运行时按当前月份选取。
                for season in SEASON_ORDER:
                    payload = build_map(geometry, scene, similarities[scene], plant_priors, season,
                                        class_weights)
                    payload["sourceHash"] = source_hash
                    write_json(out / f"{campus_config['slug']}_{scene}_{season}.json", payload)
                    total_maps += 1
                    print(f"{campus} {scene}[{season}]: {geometry['width']}x{geometry['height']}; "
                          f"Top={[p['name'] for p in payload['places']]}", flush=True)
            else:
                payload = build_map(geometry, scene, similarities[scene], plant_priors, None,
                                    class_weights)
                payload["sourceHash"] = source_hash
                write_json(out / f"{campus_config['slug']}_{scene}.json", payload)
                total_maps += 1
                print(f"{campus} {scene}: {geometry['width']}x{geometry['height']}; "
                      f"Top={[p['name'] for p in payload['places']]}", flush=True)
        timings[campus] = round(time.perf_counter() - campus_start, 3)
    if fingerprint(BASE, model_name) != source_hash:
        raise RuntimeError("Source files changed during build; rerun the builder")
    write_json(out / "class_similarities.json", {"sourceHash": source_hash, "scores": similarities})
    # Commit manifest last; runtime rejects incomplete or outdated cache files.
    write_json(out / "manifest.json", {
        "algorithm": ALGORITHM_VERSION, "sourceHash": source_hash, "model": model_name,
        "createdAt": datetime.now(timezone.utc).isoformat(), "mapCount": total_maps,
        "audit": audit,
        "classCorpus": "species template: morphology/culture/habit/bloom-season/ornamental-rank; no personal messages",
        "placePriorScenes": list(config.get("placePriorScenes") or []),
        "sceneCorpusOverrides": sorted((config.get("sceneCorpusOverrides") or {}).keys()),
        "facilityShare": config.get("facilityShare"),
        "scenePoiWeighting": "uniform",
        "placeLimit": config.get("placeLimit"),
        "sceneIndependence": "each_scene_uses_only_its_own_inputs",
        "secondsByCampus": timings, "totalSeconds": round(time.perf_counter()-started, 3),
    })
    print(f"Ready: {total_maps} scene maps. Existing semantic_index and rule ranking were not changed.")


if __name__ == "__main__":
    main()
