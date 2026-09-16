"""Build the optional sentence-vector index used by semantic_retrieval.py."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

from semantic_retrieval import (
    DEFAULT_INDEX_FILE,
    DEFAULT_META_FILE,
    DEFAULT_MODEL_NAME,
    build_poi_document,
    build_scene_document,
    build_scene_negative_document,
    encode_documents,
)


BASE = Path(__file__).resolve().parent


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _poi_files() -> list[Path]:
    return sorted(path for path in BASE.glob("*_pois.json") if path.is_file())


def _source_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    model_name = os.getenv("SEMANTIC_MODEL_PATH", DEFAULT_MODEL_NAME).strip()
    index_path = BASE / os.getenv("SEMANTIC_INDEX_FILE", DEFAULT_INDEX_FILE)
    meta_path = BASE / os.getenv("SEMANTIC_INDEX_META_FILE", DEFAULT_META_FILE)
    scene_path = BASE / "scene_profiles.json"
    poi_paths = _poi_files()

    scenes = _load_json(scene_path)
    pois = []
    for path in poi_paths:
        pois.extend(_load_json(path))

    scene_ids = [str(item["id"]) for item in scenes]
    poi_ids = [str(item["id"]) for item in pois]
    if len(poi_ids) != len(set(poi_ids)):
        raise ValueError("POI ids must be globally unique before building the semantic index")

    model = SentenceTransformer(model_name)
    scene_vectors = encode_documents(model, [build_scene_document(item) for item in scenes])
    scene_negative_vectors = encode_documents(
        model,
        [build_scene_negative_document(item) or "不属于该场景" for item in scenes],
    )
    poi_vectors = encode_documents(model, [build_poi_document(item) for item in pois])

    np.savez_compressed(
        index_path,
        scene_ids=np.asarray(scene_ids),
        scene_vectors=scene_vectors,
        scene_negative_vectors=scene_negative_vectors,
        poi_ids=np.asarray(poi_ids),
        poi_vectors=poi_vectors,
    )
    source_paths = [scene_path, *poi_paths]
    meta = {
        "model": model_name,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceHash": _source_hash(source_paths),
        "sceneCount": len(scene_ids),
        "poiCount": len(poi_ids),
        "sourceFiles": [path.name for path in source_paths],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"semantic index written: {index_path}")
    print(f"scenes={len(scene_ids)} pois={len(poi_ids)} model={model_name}")


if __name__ == "__main__":
    main()
