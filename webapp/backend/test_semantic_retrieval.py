"""Semantic-index source discovery and stale-cache regression tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import semantic_retrieval as semantic


BASE = Path(__file__).resolve().parent


class SemanticSourceTests(unittest.TestCase):
    def test_repository_source_set_includes_sjtu_and_is_deterministic(self):
        names = [path.name for path in semantic.semantic_source_paths(BASE)]

        self.assertEqual(names[0], semantic.SCENE_SOURCE_FILE)
        self.assertEqual(names[1:], sorted(names[1:]))
        self.assertIn("sjtu_minhang_pois.json", names)

    def test_source_hash_changes_with_new_campus_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / semantic.SCENE_SOURCE_FILE).write_text("[]", encoding="utf-8")
            (base / "campus_pois.json").write_text("[]", encoding="utf-8")
            sjtu = base / "sjtu_minhang_pois.json"
            sjtu.write_text('[{"id":"sjtu-1"}]', encoding="utf-8")

            paths = semantic.semantic_source_paths(base)
            before = semantic.semantic_source_hash(paths)
            sjtu.write_text('[{"id":"sjtu-2"}]', encoding="utf-8")

            self.assertNotEqual(before, semantic.semantic_source_hash(paths))

    def test_repository_index_matches_current_sources(self):
        paths = semantic.semantic_source_paths(BASE)
        metadata = json.loads(
            (BASE / semantic.DEFAULT_META_FILE).read_text(encoding='utf-8'))

        self.assertEqual(metadata['sourceFiles'], [path.name for path in paths])
        self.assertEqual(metadata['sourceHash'], semantic.semantic_source_hash(paths))
        self.assertEqual(metadata['poiCount'], 3122)
        with np.load(BASE / semantic.DEFAULT_INDEX_FILE, allow_pickle=False) as index:
            self.assertEqual(index['poi_vectors'].shape, (3122, 512))
            self.assertEqual(index['scene_vectors'].shape, (6, 512))


class SemanticRuntimeSourceSetTests(unittest.TestCase):
    def _write_metadata(self, base: Path) -> None:
        paths = semantic.semantic_source_paths(base)
        metadata = {
            "model": semantic.DEFAULT_MODEL_NAME,
            "sourceHash": semantic.semantic_source_hash(paths),
            "sourceFiles": [path.name for path in paths],
        }
        (base / semantic.DEFAULT_META_FILE).write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (base / semantic.DEFAULT_INDEX_FILE).write_bytes(b"placeholder")

    def _status(self, base: Path) -> dict:
        environment = {
            "SEMANTIC_ENABLED": "auto",
            "SEMANTIC_INDEX_FILE": semantic.DEFAULT_INDEX_FILE,
            "SEMANTIC_INDEX_META_FILE": semantic.DEFAULT_META_FILE,
            "SEMANTIC_MODEL_PATH": semantic.DEFAULT_MODEL_NAME,
        }
        with patch.dict(os.environ, environment, clear=False):
            return semantic.SemanticRetriever(base).status()

    def test_added_poi_file_marks_existing_index_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / semantic.SCENE_SOURCE_FILE).write_text("[]", encoding="utf-8")
            (base / "campus_pois.json").write_text("[]", encoding="utf-8")
            self._write_metadata(base)

            (base / "sjtu_minhang_pois.json").write_text("[]", encoding="utf-8")
            status = self._status(base)

            self.assertFalse(status["available"])
            self.assertIn("stale", status["reason"])
            self.assertIn("source file set changed", status["reason"])

    def test_deleted_poi_file_marks_existing_index_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / semantic.SCENE_SOURCE_FILE).write_text("[]", encoding="utf-8")
            sjtu = base / "sjtu_minhang_pois.json"
            sjtu.write_text("[]", encoding="utf-8")
            self._write_metadata(base)

            sjtu.unlink()
            status = self._status(base)

            self.assertFalse(status["available"])
            self.assertIn("stale", status["reason"])
            self.assertIn("source file set changed", status["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
