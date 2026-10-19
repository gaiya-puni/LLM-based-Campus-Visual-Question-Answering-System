"""Spatial alignment regression: heatmap raster must honour POI coordinates.

The preview endpoint returns `grid.bounds` and the frontend stretches the canvas
over those bounds.  If the projection used to build the grid and the projection
used to convert the bounds back to lng/lat ever diverge, hotspots drift away
from their source POIs — exactly the "整体比例偏大" symptom.  These tests pin the
round-trip and the cell-index mapping at Beijing latitude.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "webapp" / "backend"
sys.path.insert(0, str(BACKEND))

import build_scene_heatmaps as builder  # noqa: E402
import scene_heatmaps as core  # noqa: E402
from tools.campus_generator.boundary import distance_m  # noqa: E402


CENTER = [116.310918, 39.992873]
POIS = [
    [116.2990, 39.9850],
    [116.3220, 39.9990],
    [116.3050, 40.0010],
    [116.3180, 39.9800],
]


class ProjectionRoundTripTests(unittest.TestCase):
    def test_meters_round_trip_is_exact_at_beijing_latitude(self):
        xy = core.to_meters(POIS, CENTER)
        back = core.to_lnglat(xy, CENTER)
        np.testing.assert_allclose(back, np.asarray(POIS, dtype=float), atol=1e-12)

    def test_one_degree_longitude_matches_local_scale(self):
        xy = core.to_meters([[CENTER[0] + 0.01, CENTER[1]]], CENTER)
        expected = 0.01 * 111320 * math.cos(math.radians(CENTER[1]))
        self.assertAlmostEqual(float(xy[0][0]), expected, places=6)
        self.assertAlmostEqual(float(xy[0][1]), 0.0, places=6)


class RasterBoundsAlignmentTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((BACKEND / "heatmap_config.json").read_text(encoding="utf-8"))
        config["campuses"] = {
            "测试校区": {
                "slug": "spatial_test",
                "center": CENTER,
                "auditRadiusMeters": 5000,
            },
        }
        self.config = config
        self.pois = [
            {
                "id": f"poi-{index}", "name": f"点位{index}",
                "locationName": f"点位{index}", "campus": "测试校区",
                "subCategory": "风景名胜", "category": "scene",
                "lng": lng, "lat": lat, "scenes": ["walk"], "text": f"点位{index}",
            }
            for index, (lng, lat) in enumerate(POIS)
        ]

    def test_bounds_contain_every_poi(self):
        geometry = core.prepare_geometry(self.pois, "测试校区", self.config)
        west, south, east, north = geometry["bounds"]
        self.assertLess(west, east)
        self.assertLess(south, north)
        for poi in self.pois:
            self.assertGreaterEqual(poi["lng"], west)
            self.assertLessEqual(poi["lng"], east)
            self.assertGreaterEqual(poi["lat"], south)
            self.assertLessEqual(poi["lat"], north)

    def test_each_poi_lands_in_the_cell_that_maps_back_to_it(self):
        geometry = core.prepare_geometry(self.pois, "测试校区", self.config)
        bounds = geometry["bounds"]
        cell = float(self.config["cellMeters"])
        low, high = core.to_meters(
            [[bounds[0], bounds[1]], [bounds[2], bounds[3]]], CENTER,
        )
        width, height = geometry["width"], geometry["height"]
        xy = core.to_meters([[poi["lng"], poi["lat"]] for poi in self.pois], CENTER)
        tolerance = cell * math.sqrt(2) / 2 + 1e-6
        for index, point in enumerate(xy):
            column = min(width - 1, max(0, int(math.floor((point[0] - low[0]) / cell))))
            row = min(height - 1, max(0, int(math.floor((high[1] - point[1]) / cell))))
            center_xy = np.array([
                low[0] + (column + 0.5) * cell,
                high[1] - (row + 0.5) * cell,
            ])
            center_lnglat = core.to_lnglat(center_xy[None, :], CENTER)[0]
            error = distance_m(center_lnglat.tolist(), POIS[index])
            self.assertLessEqual(error, tolerance)


class DensitySceneGateTests(unittest.TestCase):
    def test_density_scene_takes_every_preview_poi(self):
        # preview._write_preview_pois 会给每个非植物 POI 追加 "density"。
        pois = [
            {"category": "plant"},
            {"category": "scene", "scenes": ["walk", "density"]},
            {"category": "scene", "scenes": ["photo"]},
        ]
        self.assertEqual(builder.pois_for_scene(pois, "density"),
                         [pois[0], pois[1]])
        self.assertEqual(builder.pois_for_scene(pois, "photo"),
                         [pois[0], pois[2]])


if __name__ == "__main__":
    unittest.main()
