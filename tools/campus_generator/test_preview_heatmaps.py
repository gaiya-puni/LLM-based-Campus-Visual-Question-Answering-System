"""Preview policy regression tests, including isolation from formal heatmaps."""

import copy
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp/backend"))
import scene_heatmaps as core
from tools.campus_generator.preview_heatmaps import fuse_preview_fields


class PreviewHeatmapTests(unittest.TestCase):
    def setUp(self):
        self.config = core.load_config(Path(core.__file__).parent)
        self.geometry = {
            "config": self.config, "mask": np.ones(3, dtype=bool),
            "fields": {"first": np.array([1., 0., 0.]), "second": np.array([0., 1., 0.])},
            "class_categories": {"first": "facility", "second": "facility"},
        }

    def test_preview_retains_second_category_without_changing_formal_policy(self):
        config_before = copy.deepcopy(self.config)
        original_fuse = core.fuse_fields
        original_version = core.ALGORITHM_VERSION
        _, original_contributions, original_betas = original_fuse(
            self.geometry, {"first": .49571, "second": .49304},
        )
        field, contributions, betas = fuse_preview_fields(
            self.geometry, {"first": .49571, "second": .49304},
        )
        self.assertEqual(original_betas["second"], 0)
        self.assertFalse(original_contributions["second"].any())
        self.assertGreaterEqual(betas["second"], .2 - 1e-12)
        self.assertGreater(contributions["second"][1], 0)
        self.assertGreater(field[1], 0)
        self.assertEqual(self.config, config_before)
        self.assertIs(core.fuse_fields, original_fuse)
        self.assertEqual(core.ALGORITHM_VERSION, original_version)

    def test_single_category_is_not_discarded(self):
        geometry = dict(self.geometry, fields={"first": self.geometry["fields"]["first"]})
        field, _, betas = fuse_preview_fields(geometry, {"first": .5})
        self.assertGreater(field.max(), 0)
        self.assertGreater(betas["first"], 0)

    def test_many_categories_keep_low_ranked_evidence(self):
        fields = {f"class{i}": np.eye(8)[i] for i in range(8)}
        geometry = dict(self.geometry, mask=np.ones(8, bool), fields=fields,
                        class_categories={name: "facility" for name in fields})
        _, contributions, betas = fuse_preview_fields(
            geometry, {name: .8 - i * .05 for i, name in enumerate(fields)},
        )
        self.assertTrue(all(beta > 0 for beta in betas.values()))
        self.assertTrue(all(value.max() > 0 for value in contributions.values()))

    def test_missing_or_negative_similarity_does_not_get_fake_heat(self):
        field, _, _ = fuse_preview_fields(self.geometry, {"first": 0, "second": -.1})
        np.testing.assert_array_equal(field, np.zeros(3))

    def test_invalid_activation_is_rejected(self):
        for value in (0, -1, 2, True, "0.2"):
            with self.subTest(value=value):
                geometry = dict(self.geometry, config=dict(self.config, minimumCategoryActivation=value))
                with self.assertRaisesRegex(ValueError, "minimumCategoryActivation"):
                    fuse_preview_fields(geometry, {"first": .5})

    def test_density_scene_weights_each_class_by_its_point_count(self):
        geometry = {
            "config": self.config,
            "mask": np.ones(4, dtype=bool),
            "fields": {
                "many": np.array([1., 1., 0., 0.]),
                "few": np.array([0., 0., 1., 0.]),
            },
            "groups": {"many": [0, 1, 2], "few": [3]},
            "class_categories": {"many": "facility", "few": "facility"},
        }
        field, contributions, betas = fuse_preview_fields(
            geometry, {"many": .5, "few": .5}, scene="density",
        )
        # 3 个点的类别应比 1 个点的类别更高，且两者都不被丢弃。
        self.assertGreater(field[0], field[2])
        self.assertGreater(field[2], 0)
        self.assertGreater(contributions["many"][0], contributions["few"][2])
        self.assertTrue(all(beta == 1.0 for beta in betas.values()))

    def test_density_scene_keeps_single_category(self):
        geometry = {
            "config": self.config,
            "mask": np.ones(2, dtype=bool),
            "fields": {"only": np.array([1., 0.])},
            "groups": {"only": [0]},
            "class_categories": {"only": "facility"},
        }
        field, _, _ = fuse_preview_fields(geometry, {"only": .3}, scene="density")
        self.assertGreater(field[0], 0)
        self.assertEqual(field[1], 0)


if __name__ == "__main__":
    unittest.main()
