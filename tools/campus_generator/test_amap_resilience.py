"""Focused regression tests for resilient AMap POI harvesting."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.campus_generator.harvest import AmapPoiHarvester, HarvestCache


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


def _poi(identifier: str = "inside") -> dict:
    return {
        "id": identifier,
        "name": "示例大学图书馆",
        "type": "科教文化服务;图书馆",
        "location": "121.0,31.0",
        "address": "示例大学示例校区",
    }


class _MemoryCache:
    def __init__(self):
        self.value = None
        self.puts = []

    def get(self, source, parameters):
        return self.value

    def put(self, source, parameters, value, *, ttl_seconds=None):
        self.value = value
        self.puts.append({
            "source": source,
            "parameters": parameters,
            "value": value,
            "ttlSeconds": ttl_seconds,
        })


class AmapResilienceTests(unittest.TestCase):
    def test_qps_error_retries_with_exponential_backoff_and_recovers(self):
        responses = [
            {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"},
            {"status": "0", "info": "SERVER_IS_BUSY", "infocode": "10016"},
            {"status": "0", "info": "GATEWAY_TIMEOUT", "infocode": "10015"},
            {"status": "1", "pois": [_poi()]},
        ]
        sleeps: list[float] = []

        harvester = AmapPoiHarvester(
            PROFILE, ["图书馆"], api_key="test-secret", max_pages=1,
            request_json=lambda _: responses.pop(0), request_interval_seconds=0,
            max_retries=3, retry_backoff_seconds=0.25, sleep=sleeps.append,
        )
        result = harvester.collect()

        self.assertEqual([item["sourceId"] for item in result.candidates], ["inside"])
        self.assertEqual(sleeps, [0.25, 0.5, 1.0])
        warning = " ".join(result.warnings)
        self.assertIn("CUQPS_HAS_EXCEEDED_THE_LIMIT", warning)
        self.assertIn("10021", warning)
        self.assertIn("SERVER_IS_BUSY", warning)
        self.assertIn("10016", warning)
        self.assertIn("GATEWAY_TIMEOUT", warning)
        self.assertIn("10015", warning)
        self.assertIn("recovered after 4 attempts", warning)

    def test_failed_request_does_not_poison_cache_and_redacts_diagnostics(self):
        secret = "do-not-log-this-key"
        malicious_info = (
            "SERVER_IS_BUSY https://restapi.amap.com/v3/place/around?key=" + secret
        )
        cache = _MemoryCache()
        failed = AmapPoiHarvester(
            PROFILE, ["图书馆"], api_key=secret, cache=cache, max_pages=1,
            request_json=lambda _: {
                "status": "0", "info": malicious_info, "infocode": "10016",
            },
            request_interval_seconds=0, max_retries=0,
        ).collect()

        self.assertEqual(failed.candidates, [])
        warning = " ".join(failed.warnings)
        self.assertIn("SERVER_IS_BUSY", warning)
        self.assertIn("infocode=10016", warning)
        self.assertIn("failed result not cached", warning)
        self.assertNotIn(secret, warning)
        self.assertNotIn("https://", warning)
        self.assertEqual(cache.puts, [])

        calls = []
        recovered = AmapPoiHarvester(
            PROFILE, ["图书馆"], api_key=secret, cache=cache, max_pages=1,
            request_json=lambda params: calls.append(params) or {
                "status": "1", "pois": [_poi("fresh")],
            },
            request_interval_seconds=0, max_retries=0,
        ).collect()
        self.assertEqual([item["sourceId"] for item in recovered.candidates], ["fresh"])
        self.assertEqual(len(calls), 1)

    def test_partial_page_failure_returns_data_but_never_caches_it(self):
        calls = []

        def request(params):
            calls.append(params["page"])
            if params["page"] == 1:
                return {"status": "1", "pois": [_poi("partial")]}
            return {"status": "0", "info": "GATEWAY_TIMEOUT", "infocode": "10015"}

        cache = _MemoryCache()
        result = AmapPoiHarvester(
            PROFILE, ["图书馆"], api_key="key", cache=cache,
            max_pages=2, page_size=1, request_json=request,
            request_interval_seconds=0, max_retries=0,
        ).collect()

        self.assertEqual([item["sourceId"] for item in result.candidates], ["partial"])
        self.assertEqual(calls, [1, 2])
        self.assertIn("partial result not cached", " ".join(result.warnings))
        self.assertEqual(cache.puts, [])

    def test_request_interval_is_injectable_for_deterministic_throttling(self):
        now = [0.0]
        sleeps: list[float] = []
        starts: list[float] = []

        def sleep(seconds: float):
            sleeps.append(seconds)
            now[0] += seconds

        def request(params):
            starts.append(now[0])
            pois = [_poi("page-one")] if params["page"] == 1 else []
            return {"status": "1", "pois": pois}

        result = AmapPoiHarvester(
            PROFILE, ["图书馆"], api_key="key", max_pages=2, page_size=1,
            request_json=request, request_interval_seconds=0.4,
            max_retries=0, sleep=sleep, monotonic=lambda: now[0],
        ).collect()

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(starts, [0.0, 0.4])
        self.assertEqual(sleeps, [0.4])

    def test_successful_empty_result_is_short_lived_and_explained(self):
        cache = _MemoryCache()
        first = AmapPoiHarvester(
            PROFILE, ["不存在地点"], api_key="key", cache=cache, max_pages=1,
            request_json=lambda _: {"status": "1", "pois": []},
            request_interval_seconds=0, max_retries=0,
            empty_cache_ttl_seconds=120,
        ).collect()
        second = AmapPoiHarvester(
            PROFILE, ["不存在地点"], api_key="key", cache=cache, max_pages=1,
            request_json=lambda _: (_ for _ in ()).throw(AssertionError("must use cache")),
            request_interval_seconds=0, max_retries=0,
            empty_cache_ttl_seconds=120,
        ).collect()

        self.assertEqual(first.candidates, [])
        self.assertEqual(second.candidates, [])
        self.assertIn("cached for up to 120 seconds", " ".join(first.warnings))
        self.assertIn("cached successful empty result", " ".join(second.warnings))
        self.assertEqual(cache.puts[0]["ttlSeconds"], 120)

    def test_cache_honors_per_entry_ttl_without_extending_global_ttl(self):
        cache = HarvestCache("unused", ttl_seconds=86400)
        record = json.dumps({"storedAt": 100.0, "ttlSeconds": 120, "value": []})
        with patch.object(Path, "read_text", return_value=record):
            with patch("tools.campus_generator.harvest.time.time", return_value=219.0):
                self.assertEqual(cache.get("amap", {"query": "x"}), [])
            with patch("tools.campus_generator.harvest.time.time", return_value=221.0):
                self.assertIsNone(cache.get("amap", {"query": "x"}))


if __name__ == "__main__":
    unittest.main()
