"""Build the optional sentence-vector index used by semantic_retrieval.py."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from semantic_retrieval import (
    DEFAULT_INDEX_FILE,
    DEFAULT_META_FILE,
    DEFAULT_MODEL_NAME,
    build_poi_document,
    build_scene_document,
    build_scene_negative_document,
    encode_documents,
    semantic_source_hash,
    semantic_source_paths,
)


BASE = Path(__file__).resolve().parent


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true", help="allow first-time BGE download")
    parser.add_argument("--output-dir", type=Path, help="write index artifacts here")
    args = parser.parse_args()
    model_name = os.getenv("SEMANTIC_MODEL_PATH", DEFAULT_MODEL_NAME).strip()
    output_dir = args.output_dir.resolve() if args.output_dir else BASE
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / os.getenv("SEMANTIC_INDEX_FILE", DEFAULT_INDEX_FILE)
    meta_path = output_dir / os.getenv("SEMANTIC_INDEX_META_FILE", DEFAULT_META_FILE)
    source_paths = semantic_source_paths(BASE)
    scene_path, *poi_paths = source_paths
    source_hash = semantic_source_hash(source_paths)

    scenes = _load_json(scene_path)
    pois = []
    for path in poi_paths:
        data = _load_json(path)
        if not isinstance(data, list):
            raise ValueError(f"{path.name} must contain a JSON list")
        pois.extend(data)

    scene_ids = [str(item["id"]) for item in scenes]
    poi_ids = [str(item["id"]) for item in pois]
    if len(poi_ids) != len(set(poi_ids)):
        raise ValueError("POI ids must be globally unique before building the semantic index")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, local_files_only=not args.allow_download)
    scene_vectors = encode_documents(model, [build_scene_document(item) for item in scenes])
    scene_negative_vectors = encode_documents(
        model,
        [build_scene_negative_document(item) or "不属于该场景" for item in scenes],
    )
    poi_vectors = encode_documents(model, [build_poi_document(item) for item in pois])

    current_paths = semantic_source_paths(BASE)
    if ([path.name for path in current_paths] != [path.name for path in source_paths]
            or semantic_source_hash(current_paths) != source_hash):
        raise RuntimeError("Source files changed during build; rerun the builder")

    temporary_index = index_path.with_suffix(index_path.suffix + ".tmp")
    with temporary_index.open("wb") as output:
        np.savez_compressed(
            output,
            scene_ids=np.asarray(scene_ids),
            scene_vectors=scene_vectors,
            scene_negative_vectors=scene_negative_vectors,
            poi_ids=np.asarray(poi_ids),
            poi_vectors=poi_vectors,
        )
    temporary_index.replace(index_path)
    meta = {
        "model": model_name,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceHash": source_hash,
        "sceneCount": len(scene_ids),
        "poiCount": len(poi_ids),
        "sourceFiles": [path.name for path in source_paths],
    }
    temporary_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")
    temporary_meta.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_meta.replace(meta_path)
    print(f"semantic index written: {index_path}")
    print(f"scenes={len(scene_ids)} pois={len(poi_ids)} model={model_name}")


if __name__ == "__main__":
    main()
