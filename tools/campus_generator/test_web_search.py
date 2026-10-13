"""Offline tests for bounded web-search discovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from tools.campus_generator.harvest import HarvestCache
from tools.campus_generator.web_search import WebSearchClient, normalize_search_url


class WebSearchTests(unittest.TestCase):
    def test_url_normalization_is_network_free_and_strict(self):
        self.assertEqual(
            normalize_search_url(" HTTPS://例子.测试:443/a?q=1#section "),
            "https://xn--fsqu00a.xn--0zwm56d/a?q=1",
        )
        self.assertEqual(normalize_search_url("https://example.com"), "https://example.com/")
        for invalid in (
            "http://example.com/page",
            "https://user:secret@example.com/page",
            "https://example.com:8443/page",
            "https:///missing-host",
            "https://127.0.0.1/internal",
            "https://[::1]/internal",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_search_url(invalid)

    def test_brave_search_merges_queries_deduplicates_and_limits_domains(self):
        calls = []

        def fake_request(params):
            calls.append(params)
            common = {
                "title": "  Shared   campus page ",
                "url": "https://campus.example/shared#top",
                "description": "  Shared   description ",
            }
            suffix = "plants" if params["q"] == "campus plants" else "scenes"
            return {"web": {"results": [
                common,
                {"title": suffix, "url": f"https://campus.example/{suffix}",
                 "description": f"{suffix} result"},
                {"title": "independent", "url": f"https://{suffix}.example.org/item",
                 "description": "another source"},
                {"title": "unsafe", "url": "http://example.net/not-https"},
            ]}}

        client = WebSearchClient(
            "brave", "secret", max_results=5, per_domain_limit=1,
            request_json=fake_request,
        )
        result = client.search([" campus   plants ", "campus scenes", "campus plants"])

        self.assertEqual(result.provider, "brave")
        self.assertEqual(result.queries, ["campus plants", "campus scenes"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["count"], 3)
        self.assertEqual(calls[0]["country"], "CN")
        self.assertEqual(calls[0]["search_lang"], "zh-hans")
        self.assertEqual(calls[0]["ui_lang"], "zh-CN")
        self.assertEqual(calls[0]["safesearch"], "moderate")
        self.assertEqual(result.urls, [
            "https://campus.example/shared",
            "https://plants.example.org/item",
            "https://scenes.example.org/item",
        ])
        self.assertEqual(result.hits[0].title, "Shared campus page")
        self.assertEqual(result.hits[0].to_dict()["provider"], "brave")
        self.assertEqual(result.to_dict()["cachedQueries"], 0)

    def test_tavily_search_uses_native_payload_and_score(self):
        seen = []

        def fake_request(payload):
            seen.append(payload)
            return {"results": [
                {"title": "Library", "url": "https://example.edu/library#map",
                 "content": " Campus library ", "score": 0.91},
                {"title": "Wrong port", "url": "https://example.edu:9443/private",
                 "content": "discard"},
            ]}

        result = WebSearchClient(
            "tavily", "tavily-secret", max_results=4, request_json=fake_request,
        ).search("campus library")

        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].url, "https://example.edu/library")
        self.assertEqual(result.hits[0].snippet, "Campus library")
        self.assertEqual(result.hits[0].score, 0.91)
        self.assertNotIn("api_key", seen[0])
        self.assertEqual(seen[0]["query"], "campus library")
        self.assertEqual(seen[0]["language"], "zh-cn")
        self.assertFalse(seen[0]["filter_by_language"])
        self.assertTrue(seen[0]["safe_search"])
        self.assertNotIn("country", seen[0])
        self.assertFalse(seen[0]["include_raw_content"])

    def test_tavily_default_request_uses_bearer_header_not_json_secret(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"results": []}

        client = WebSearchClient("tavily", "tavily-secret")
        with patch("tools.campus_generator.web_search.requests.post",
                   return_value=Response()) as post:
            client._request_json(client._request_payload("campus", 3))

        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tavily-secret")
        self.assertNotIn("api_key", kwargs["json"])
        self.assertNotIn("tavily-secret", str(kwargs["json"]))

    def test_cache_avoids_repeating_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = HarvestCache(Path(directory), ttl_seconds=60)
            first = WebSearchClient(
                "brave", "secret", cache,
                request_json=lambda _: {"web": {"results": [{
                    "title": "Result",
                    "url": "https://example.edu/result",
                    "description": "Cached safely",
                }]}},
            ).search("campus")
            second = WebSearchClient(
                "brave", "secret", cache,
                request_json=lambda _: (_ for _ in ()).throw(AssertionError("must use cache")),
            ).search("campus")

            self.assertEqual(first.urls, second.urls)
            self.assertEqual(second.cached_queries, 1)
            cache_text = "".join(path.read_text(encoding="utf-8")
                                 for path in Path(directory).glob("*.json"))
            self.assertNotIn("secret", cache_text)

    def test_missing_key_and_empty_queries_are_configuration_errors(self):
        client = WebSearchClient("brave", "", request_json=lambda _: {})
        with self.assertRaisesRegex(ValueError, "BRAVE_SEARCH_API_KEY"):
            client.search("campus")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            WebSearchClient("tavily", "key").search(["  "])
        with self.assertRaisesRegex(ValueError, "unsupported"):
            WebSearchClient("unknown", "key")

    def test_one_failed_query_does_not_discard_successful_results(self):
        def fake_request(params):
            if params["q"] == "fails":
                raise requests.Timeout("provider timeout leaked secret")
            return {"web": {"results": [{
                "title": "Result",
                "url": "https://example.edu/result",
                "description": "Found",
            }]}}

        result = WebSearchClient(
            "brave", "secret", request_json=fake_request,
        ).search(["fails", "works"])

        self.assertEqual(result.urls, ["https://example.edu/result"])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Timeout", result.warnings[0])
        self.assertNotIn("secret", result.warnings[0])
        self.assertIn("[redacted]", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
