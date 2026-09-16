"""Optional sentence-embedding retrieval for campus scene and POI matching.

The application keeps its deterministic rules as a fallback.  This module is
loaded lazily so a missing model, package, or index never prevents Flask from
starting.  Use ``build_semantic_index.py`` after changing POI data or scene
profiles.
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
from pathlib import Path
from typing import Iterable, Optional, Union

import numpy as np


DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DEFAULT_INDEX_FILE = "semantic_index.npz"
DEFAULT_META_FILE = "semantic_index_meta.json"


def _as_text_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_scene_document(profile: dict) -> str:
    """Build the definition-and-context corpus used for scene embeddings."""
    parts = [
        str(profile.get("name") or ""),
        str(profile.get("definition") or ""),
        *_as_text_list(profile.get("contextTexts")),
        *_as_text_list(profile.get("positiveQueries")),
        *_as_text_list(profile.get("intentKeywords")),
        *_as_text_list(profile.get("terms")),
        *_as_text_list(profile.get("placeTerms")),
    ]
    return "。".join(dict.fromkeys(part for part in parts if part))


def build_scene_negative_document(profile: dict) -> str:
    """Build a compact confusion corpus used as a small similarity penalty."""
    return "。".join(_as_text_list(profile.get("negativeQueries")))


def build_poi_document(poi: dict) -> str:
    """Build a stable POI corpus from searchable fields and metadata."""
    meta = poi.get("meta") or {}
    parts = [
        str(poi.get("name") or ""),
        str(poi.get("subCategory") or ""),
        str(poi.get("category") or ""),
        str(poi.get("campus") or ""),
        str(poi.get("locationName") or ""),
        str(poi.get("text") or ""),
        *_as_text_list(poi.get("tags")),
        *_as_text_list(meta.get("aliases")),
        str(meta.get("address") or ""),
        str(meta.get("notes") or ""),
    ]
    return "。".join(dict.fromkeys(part for part in parts if part))


def _normalized_rows(vectors) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


class SemanticRetriever:
    """Lazy, read-only accessor for a precomputed sentence-vector index."""

    def __init__(self, base_dir: Union[str, Path]):
        self.base_dir = Path(base_dir)
        self.enabled = os.getenv("SEMANTIC_ENABLED", "auto").strip().lower()
        self.model_name = os.getenv("SEMANTIC_MODEL_PATH", DEFAULT_MODEL_NAME).strip()
        self.index_path = self.base_dir / os.getenv("SEMANTIC_INDEX_FILE", DEFAULT_INDEX_FILE)
        self.meta_path = self.base_dir / os.getenv("SEMANTIC_INDEX_META_FILE", DEFAULT_META_FILE)
        self._lock = threading.Lock()
        self._loaded = False
        self._available = False
        self._reason = "not loaded"
        self._model = None
        self._scene_ids: list[str] = []
        self._scene_vectors: Optional[np.ndarray] = None
        self._scene_negative_vectors: Optional[np.ndarray] = None
        self._poi_ids: list[str] = []
        self._poi_vectors: Optional[np.ndarray] = None
        self._scene_lookup: dict[str, int] = {}
        self._poi_lookup: dict[str, int] = {}
        self._query_cache: dict[str, np.ndarray] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if self.enabled in {"0", "false", "off", "disabled"}:
                self._reason = "disabled by SEMANTIC_ENABLED"
                return
            if not self.index_path.exists() or not self.meta_path.exists():
                self._reason = "semantic index not found; run build_semantic_index.py"
                return
            try:
                from sentence_transformers import SentenceTransformer

                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                source_files = [self.base_dir / name for name in meta.get("sourceFiles", [])]
                if not source_files or any(not path.exists() for path in source_files):
                    self._reason = "semantic index source files are missing; rebuild index"
                    return
                digest = hashlib.sha256()
                for path in source_files:
                    digest.update(path.name.encode("utf-8"))
                    digest.update(path.read_bytes())
                if meta.get("sourceHash") != digest.hexdigest():
                    self._reason = "semantic index is stale; run build_semantic_index.py"
                    return
                index_model = str(meta.get("model") or "")
                if index_model and index_model != self.model_name:
                    self._reason = (
                        f"index model mismatch ({index_model} != {self.model_name}); rebuild index"
                    )
                    return
                data = np.load(self.index_path, allow_pickle=False)
                self._scene_ids = [str(value) for value in data["scene_ids"].tolist()]
                self._scene_vectors = _normalized_rows(data["scene_vectors"])
                if "scene_negative_vectors" in data.files:
                    self._scene_negative_vectors = _normalized_rows(data["scene_negative_vectors"])
                self._poi_ids = [str(value) for value in data["poi_ids"].tolist()]
                self._poi_vectors = _normalized_rows(data["poi_vectors"])
                self._scene_lookup = {value: index for index, value in enumerate(self._scene_ids)}
                self._poi_lookup = {value: index for index, value in enumerate(self._poi_ids)}
                # Runtime must be deterministic and must not block Flask on
                # network retries. The builder is the only component allowed
                # to download the configured model.
                self._model = SentenceTransformer(self.model_name, local_files_only=True)
                self._available = True
                self._reason = "ok"
            except Exception as exc:  # optional integration must fail closed
                self._reason = f"semantic retriever unavailable: {exc}"
                self._available = False

    def status(self) -> dict:
        self._load()
        return {
            "enabled": self.enabled,
            "available": self._available,
            "model": self.model_name,
            "index": str(self.index_path),
            "reason": self._reason,
        }

    def _query_vector(self, query: str) -> Optional[np.ndarray]:
        self._load()
        if not self._available or not query or self._model is None:
            return None
        with self._lock:
            cached = self._query_cache.get(query)
            if cached is not None:
                return cached
            vector = self._model.encode(
                [query],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            normalized = _normalized_rows(vector)[0]
            if len(self._query_cache) >= 32:
                self._query_cache.pop(next(iter(self._query_cache)))
            self._query_cache[query] = normalized
            return normalized

    def scene_scores(self, query: str, scene_ids: Iterable[str]) -> dict[str, float]:
        query_vector = self._query_vector(query)
        if query_vector is None or self._scene_vectors is None:
            return {}
        scores = {}
        for scene_id in scene_ids:
            index = self._scene_lookup.get(str(scene_id))
            if index is not None:
                score = float(np.dot(query_vector, self._scene_vectors[index]))
                if self._scene_negative_vectors is not None:
                    negative_score = float(np.dot(query_vector, self._scene_negative_vectors[index]))
                    score -= max(0.0, negative_score - 0.40) * 0.25
                scores[str(scene_id)] = score
        return scores

    def poi_score(self, query: str, poi_id: str) -> Optional[float]:
        query_vector = self._query_vector(query)
        if query_vector is None or self._poi_vectors is None:
            return None
        index = self._poi_lookup.get(str(poi_id))
        if index is None:
            return None
        return float(np.dot(query_vector, self._poi_vectors[index]))

    def scene_poi_score(self, scene_id: str, poi_id: str) -> Optional[float]:
        self._load()
        if not self._available or self._scene_vectors is None or self._poi_vectors is None:
            return None
        scene_index = self._scene_lookup.get(str(scene_id))
        poi_index = self._poi_lookup.get(str(poi_id))
        if scene_index is None or poi_index is None:
            return None
        return float(np.dot(self._scene_vectors[scene_index], self._poi_vectors[poi_index]))


def encode_documents(model, documents: list[str]) -> np.ndarray:
    """Encode and normalize documents for the offline index builder."""
    return _normalized_rows(
        model.encode(
            documents,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
    )
