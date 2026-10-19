"""Regression tests for the grid × POI-type scan that fills recall gaps."""

from __future__ import annotations

import unittest

import requests

from tools.campus_generator.harvest import AmapGridTypeHarvester
from tools.campus_generator.quality import assess_candidate_quality

PROFILE = {
    "school": {"name": "示例大学"},
    "campus": {
        "name": "示例校区",
        "slug": "demo-campus",
        "center": [121.0, 31.0],
        "trustRadiusM": 1000,
        "coordinateSystem": "GCJ-02",
    },
}


def _poi(identifier: str, name: str = "外国语学院", poi_type: str = "科教文化服务;学校;高等院校",
         location: str = "121.0,31.0"):
    # 名称不含任何主题关键词：这正是纯关键词召回采不到的那类地点。
    return {
        "id": identifier,
        "name": name,
        "type": poi_type,
        "location": location,
        "address": "示例大学示例校区",
    }


class _RecordingCache:
    def __init__(self, value=None):
        self.value = value
        self.puts = []

    def get(self, source, parameters):
        return self.value

    def put(self, source, parameters, value, *, ttl_seconds=None):
        self.value = value
        self.puts.append({"source": source, "parameters": parameters, "value": value})


class AmapGridScanTests(unittest.TestCase):
    def test_grid_cells_tile_the_whole_campus(self):
        harvester = AmapGridTypeHarvester(PROFILE, grid=3, api_key="key")
        cells = harvester.cells()
        self.assertEqual(len(cells), 9)
        centers = [cell[0] for cell in cells]
        self.assertEqual(len({tuple(point) for point in centers}), 9)
        # 每个网格的搜索半径必须能覆盖自己的半对角线，否则相邻格之间会有空洞。
        radius_total = 1000
        step = 2 * radius_total / 3
        for _, cell_radius in cells:
            self.assertGreaterEqual(cell_radius, step * 0.7)

    def test_requests_use_types_instead_of_keywords(self):
        captured = []

        def request(params):
            captured.append(params)
            return {"status": "1", "pois": []}

        harvester = AmapGridTypeHarvester(
            PROFILE, grid=2, api_key="key", request_json=request, max_pages=1,
        )
        harvester.collect()
        self.assertEqual(len(captured), 4)
        for params in captured:
            self.assertIn("types", params)
            self.assertNotIn("keywords", params)
            self.assertIn("141200", params["types"])
            self.assertIn("120000", params["types"])
            self.assertIn("location", params)
            self.assertIn("radius", params)

    def test_same_poi_returned_by_two_cells_is_merged(self):
        def request(params):
            return {"status": "1", "pois": [_poi("shared")]}

        harvester = AmapGridTypeHarvester(
            PROFILE, grid=2, api_key="key", request_json=request, max_pages=1,
        )
        result = harvester.collect()
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.source, "amap_grid")
        self.assertIn("deduplicated", " ".join(result.warnings))

    def test_one_failed_cell_does_not_discard_the_others(self):
        calls = []

        def request(params):
            calls.append(params)
            if len(calls) == 1:
                raise requests.ConnectionError("boom")
            # 名称与坐标都错开，避免被"同名 30m 内"的去重规则合并。
            index = len(calls)
            return {"status": "1", "pois": [
                _poi(f"poi-{index}", name=f"外国语学院{index}",
                     location=f"{121.0 + index * 0.001},{31.0}"),
            ]}

        harvester = AmapGridTypeHarvester(
            PROFILE, grid=2, api_key="key", request_json=request, max_pages=1,
            max_retries=0, request_interval_seconds=0,
        )
        result = harvester.collect()
        self.assertEqual(len(result.candidates), 3)
        self.assertIn("failed after 1 attempt", " ".join(result.warnings))

    def test_cached_cell_skips_network(self):
        # 缓存里存的是已转换的候选（有 lng/lat），不是高德原始 POI。
        cache = _RecordingCache(value=[{
            "sourceId": "cached", "name": "外国语学院", "lng": 121.0, "lat": 31.0,
            "evidence": {"distanceMeters": 100},
        }])

        def request(params):  # pragma: no cover - must never run
            raise AssertionError("cache hit should not issue a request")

        harvester = AmapGridTypeHarvester(
            PROFILE, grid=1, api_key="key", request_json=request, cache=cache,
        )
        result = harvester.collect()
        self.assertEqual([item["sourceId"] for item in result.candidates], ["cached"])

    def test_missing_api_key_fails_explicitly(self):
        # 构造后会回落到 AMAP_WEB_SERVICE_KEY 环境变量，这里显式清空以隔离本机配置。
        harvester = AmapGridTypeHarvester(PROFILE, grid=1, api_key="placeholder")
        harvester.api_key = ""
        with self.assertRaises(ValueError):
            harvester.collect()


class QualityDifferentiationTests(unittest.TestCase):
    def _candidate(self, name: str, category: str, poi_type: str) -> dict:
        return {
            "name": name,
            "category": "scene",
            "subCategory": category,
            "campus": "示例大学",
            "source": "amap_grid",
            "evidence": {
                "type": poi_type,
                "campusMembershipMethod": "polygon",
                "campusMembershipRelation": "inside",
                "distanceMeters": 200,
            },
        }

    def test_general_theme_separates_poi_categories(self):
        scenic = assess_candidate_quality(
            self._candidate("未名湖", "风景名胜", "风景名胜;公园广场;公园"),
            theme="general",
        )
        logistics = assess_candidate_quality(
            self._candidate("快递驿站", "生活服务", "生活服务;物流速递;物流速递"),
            theme="general",
        )
        campus = assess_candidate_quality(
            self._candidate("外国语学院", "科教文化服务", "科教文化服务;学校;高等院校"),
            theme="general",
        )
        self.assertGreater(scenic["scores"]["themeRelevance"], campus["scores"]["themeRelevance"])
        self.assertGreater(campus["scores"]["themeRelevance"], logistics["scores"]["themeRelevance"])
        # 旧口径下同一来源、同一范围的点会得到同一个 confidence；这里必须拉开。
        self.assertGreater(scenic["confidence"] - logistics["confidence"], 0.05)

    def test_passive_facilities_are_penalised(self):
        parking = assess_candidate_quality(
            self._candidate("地下停车场", "交通设施服务", "交通设施服务;停车场;停车场"),
            theme="general",
        )
        plaza = assess_candidate_quality(
            self._candidate("百周年纪念讲堂广场", "风景名胜", "风景名胜;公园广场;广场"),
            theme="general",
        )
        self.assertGreater(plaza["scores"]["themeRelevance"], parking["scores"]["themeRelevance"])


if __name__ == "__main__":
    unittest.main()
