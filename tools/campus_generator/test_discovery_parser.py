"""Focused regressions for natural-language campus identity parsing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from tools.campus_generator.discovery import (
    CampusProfileResolver,
    _clean_request,
    _scoped_keyword,
)


def _geocode_payload(address: str) -> dict:
    return {
        "status": "1",
        "geocodes": [{
            "location": "121.5036,31.2824",
            "formatted_address": address,
        }],
    }


class _EmptyConfigResolver(CampusProfileResolver):
    def _config(self) -> dict:
        return {"schools": {}, "campuses": []}


class _FixtureConfigResolver(CampusProfileResolver):
    def __init__(self, fixture: dict, **kwargs):
        self.fixture = fixture
        super().__init__(**kwargs)

    def _config(self) -> dict:
        return self.fixture


_MULTI_CAMPUS_FIXTURE = {
    "schools": {
        "demo": {
            "name": "示例大学",
            "enName": "Demo University",
            "aliases": ["示大"],
        },
    },
    "campuses": [
        {
            "name": "东校区",
            "slug": "demo_east",
            "school": "demo",
            "aliases": ["东校区"],
            "center": [121.1, 31.1],
            "trustRadiusM": 1000,
            "coordinateSystem": "GCJ-02",
        },
        {
            "name": "西校区",
            "slug": "demo_west",
            "school": "demo",
            "aliases": ["西校区"],
            "center": [121.2, 31.2],
            "trustRadiusM": 1000,
            "coordinateSystem": "GCJ-02",
        },
    ],
}


class CampusDiscoveryParserTests(unittest.TestCase):
    def resolver(self, request_json):
        return _EmptyConfigResolver(
            api_key="test-key",
            request_json=request_json,
        )

    def test_tongji_request_removes_intent_and_keeps_complete_names(self):
        calls = []

        def fake_request(params):
            calls.append(dict(params))
            return _geocode_payload("上海市杨浦区同济大学四平路校区")

        resolver = self.resolver(fake_request)
        profile = resolver.resolve("帮我生产上海同济大学四平路校区的拍照热力图")

        self.assertEqual(calls[0]["address"], "同济大学四平路校区")
        self.assertEqual(profile["school"]["name"], "同济大学")
        self.assertIn("上海同济大学", profile["school"]["aliases"])
        self.assertEqual(profile["campus"]["name"], "四平路校区")
        self.assertEqual(profile["discovery"]["resolvedSchool"], "同济大学")
        self.assertEqual(profile["discovery"]["resolvedCampus"], "四平路校区")
        keyword = _scoped_keyword(profile, "图书馆")
        self.assertEqual(keyword, "同济大学 四平路校区 图书馆")
        self.assertNotRegex(keyword, r"帮我|生成|生产|制作")

    def test_fudan_school_only_uses_broad_scope_without_fake_campus(self):
        calls = []

        def fake_request(params):
            calls.append(dict(params))
            return _geocode_payload("上海市杨浦区复旦大学")

        resolver = self.resolver(fake_request)
        profile = resolver.resolve("请帮我生成一个上海复旦大学的热力图")

        self.assertEqual(calls[0]["address"], "复旦大学")
        self.assertEqual(profile["school"]["name"], "复旦大学")
        self.assertEqual(profile["campus"]["name"], "复旦大学")
        self.assertNotEqual(profile["campus"]["name"], "复旦大学校区")
        self.assertEqual(_scoped_keyword(profile, "食堂"), "复旦大学 食堂")

    def test_specific_fudan_campus_is_preserved(self):
        resolver = self.resolver(
            lambda _: _geocode_payload("上海市杨浦区复旦大学邯郸校区")
        )
        profile = resolver.resolve("制作复旦大学邯郸校区校园景点热力图")
        self.assertEqual(profile["school"]["name"], "复旦大学")
        self.assertEqual(profile["campus"]["name"], "邯郸校区")
        self.assertEqual(_scoped_keyword(profile, "图书馆"), "复旦大学 邯郸校区 图书馆")

    def test_ambiguous_unregistered_head_campus_is_rejected_before_geocode(self):
        calls = []
        resolver = _EmptyConfigResolver(
            api_key="",
            request_json=lambda params: calls.append(params) or _geocode_payload("不应调用"),
        )
        with self.assertRaisesRegex(ValueError, "无法唯一确定校区.*具体校区名称"):
            resolver.resolve("帮我制作上海同济大学本部的热力图")
        self.assertEqual(calls, [])

    def test_fudan_head_campus_maps_to_handan_before_geocode(self):
        calls = []

        def fake_request(params):
            calls.append(dict(params))
            return _geocode_payload("上海市杨浦区复旦大学邯郸校区")

        profile = self.resolver(fake_request).resolve("上海复旦大学本部热力图")
        self.assertEqual(calls[0]["address"], "复旦大学邯郸校区")
        self.assertEqual(profile["school"]["name"], "复旦大学")
        self.assertEqual(profile["campus"]["name"], "邯郸校区")
        self.assertEqual(profile["discovery"]["resolvedCampus"], "邯郸校区")
        self.assertEqual(_scoped_keyword(profile, "图书馆"), "复旦大学 邯郸校区 图书馆")

    def test_fudan_main_campus_label_remains_ambiguous(self):
        calls = []
        resolver = _EmptyConfigResolver(
            api_key="",
            request_json=lambda params: calls.append(params) or _geocode_payload("不应调用"),
        )
        with self.assertRaisesRegex(ValueError, "无法唯一确定校区.*具体校区名称"):
            resolver.resolve("上海复旦大学主校区热力图")
        self.assertEqual(calls, [])

    def test_non_fudan_head_campus_is_rejected_even_with_one_registered_campus(self):
        fixture = {
            **_MULTI_CAMPUS_FIXTURE,
            "campuses": _MULTI_CAMPUS_FIXTURE["campuses"][:1],
        }
        resolver = _FixtureConfigResolver(fixture, api_key="")
        with self.assertRaisesRegex(ValueError, "无法唯一确定校区.*具体校区名称"):
            resolver.resolve("示例大学本部热力图")

    def test_explicit_school_scoped_registry_alias_is_respected(self):
        fixture = {
            **_MULTI_CAMPUS_FIXTURE,
            "campuses": [{
                **_MULTI_CAMPUS_FIXTURE["campuses"][0],
                "aliases": ["示例大学主校区"],
            }],
        }
        profile = _FixtureConfigResolver(fixture, api_key="").resolve(
            "示例大学主校区热力图"
        )
        self.assertEqual(profile["campus"]["slug"], "demo_east")
        self.assertEqual(profile["discovery"]["method"], "local_config")

    def test_registered_multi_campus_school_does_not_silently_pick_first(self):
        calls = []

        def fake_request(params):
            calls.append(dict(params))
            return _geocode_payload("示例大学")

        resolver = _FixtureConfigResolver(
            _MULTI_CAMPUS_FIXTURE,
            api_key="test-key",
            request_json=fake_request,
        )
        profile = resolver.resolve("示例大学热力图")
        self.assertEqual(calls[0]["address"], "示例大学")
        self.assertEqual(profile["campus"]["name"], "示例大学")

    def test_known_school_unregistered_campus_falls_back_to_geocoding(self):
        for query in (
            "上海交通大学徐汇校区热力图",
            "上海交大徐汇校区热力图",
            "交大徐汇校区热力图",
        ):
            with self.subTest(query=query):
                calls = []

                def fake_request(params):
                    calls.append(dict(params))
                    return _geocode_payload("上海市徐汇区上海交通大学徐汇校区")

                profile = CampusProfileResolver(
                    api_key="test-key",
                    request_json=fake_request,
                ).resolve(query)
                self.assertEqual(calls[0]["address"], "上海交通大学徐汇校区")
                self.assertEqual(profile["school"]["name"], "上海交通大学")
                self.assertEqual(profile["campus"]["name"], "徐汇校区")
                self.assertEqual(profile["discovery"]["method"], "amap_geocode")

    def test_known_school_alias_with_ambiguous_head_campus_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "无法唯一确定校区"):
            CampusProfileResolver(api_key="").resolve("交大本部热力图")

    def test_geocode_http_error_does_not_expose_request_url_or_key(self):
        secret = "test-secret-that-must-not-leak"
        response = requests.Response()
        response.status_code = 429
        response.url = (
            "https://restapi.amap.com/v3/geocode/geo?key=" + secret
        )
        failure = requests.HTTPError(
            "429 Client Error for url: " + response.url,
            response=response,
        )

        with patch(
            "tools.campus_generator.discovery.requests.get",
            side_effect=failure,
        ):
            with self.assertRaisesRegex(RuntimeError, r"HTTP 429") as caught:
                CampusProfileResolver(api_key=secret)._request_json({
                    "key": secret,
                    "address": "示例大学",
                    "output": "json",
                })
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    def test_registered_head_campus_alias_still_resolves_locally(self):
        profile = CampusProfileResolver().resolve("帮我生成上海交通大学闵行本部热力图")
        self.assertEqual(profile["school"]["id"], "sjtu")
        self.assertEqual(profile["campus"]["slug"], "sjtu_minhang")
        self.assertEqual(profile["discovery"]["resolvedSchool"], "上海交通大学")
        self.assertEqual(profile["discovery"]["resolvedCampus"], "交大闵行")

    def test_college_suffix_is_not_truncated(self):
        resolver = self.resolver(lambda _: _geocode_payload("示例学院东校区"))
        profile = resolver.resolve("帮我做示例学院东校区的热力图")
        self.assertEqual(profile["school"]["name"], "示例学院")
        self.assertEqual(profile["campus"]["name"], "东校区")

    def test_clean_request_handles_each_supported_action(self):
        for action in ("做", "生成", "生产", "制作"):
            with self.subTest(action=action):
                self.assertEqual(
                    _clean_request(f"帮我{action}上海同济大学四平路校区的热力图"),
                    "同济大学四平路校区",
                )


if __name__ == "__main__":
    unittest.main()
