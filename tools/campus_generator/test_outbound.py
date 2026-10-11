"""出站代理策略的回归测试。

锁住一条踩过的坑：Windows 的 Clash 一类工具会把系统代理注册进注册表，
`requests` 会读取它，导致高德 Web 服务（国内域名）全部 `ProxyError`——
一次采集 13 个关键词 × 3 次重试全部失败，看起来却像"高德没有数据"。

两个关键不变量：
1. **"直连"必须写成 `{'http': None, 'https': None}`**，写成 `{}` 是无效的：
   `requests` 会用 `setdefault` 把系统代理补回来。本文件专门守住这一点。
2. 高德走直连；公开网页与境外搜索服务沿用系统设置（可用环境变量显式覆盖）。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.campus_generator import harvest, web_search
from tools.campus_generator.outbound import (
    AMAP_PROXY_ENV,
    WEB_PROXY_ENV,
    direct_proxies,
    system_proxies,
)
from tools.campus_generator.harvest import AmapPoiHarvester, PublicWebHarvester
from tools.campus_generator.web_search import WebSearchClient

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


class DirectProxyTests(unittest.TestCase):
    def test_direct_means_both_schemes_explicitly_none(self):
        """空字典不算直连——这是本模块存在的理由。"""
        proxies = direct_proxies()

        self.assertEqual(proxies, {"http": None, "https": None})
        self.assertNotEqual(proxies, {})

    def test_env_var_overrides_direct_default(self):
        with patch.dict("os.environ", {AMAP_PROXY_ENV: "http://127.0.0.1:7897"}):
            self.assertEqual(
                direct_proxies(),
                {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"},
            )

    def test_blank_env_var_still_means_direct(self):
        with patch.dict("os.environ", {AMAP_PROXY_ENV: "   "}):
            self.assertEqual(direct_proxies(), {"http": None, "https": None})


class SystemProxyTests(unittest.TestCase):
    def test_system_default_does_not_override_anything(self):
        """返回空字典 = 不干预，让 requests 自己按系统设置决定。"""
        with patch.dict("os.environ", {WEB_PROXY_ENV: ""}):
            self.assertEqual(system_proxies(), {})

    def test_env_var_pins_the_proxy(self):
        with patch.dict("os.environ", {WEB_PROXY_ENV: "http://127.0.0.1:7897"}):
            self.assertEqual(
                system_proxies(),
                {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"},
            )


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"status": "1", "pois": [], "results": []}


class RealRequestCallSiteTests(unittest.TestCase):
    """不满足于测函数本身，直接验证真实调用点确实传了 proxies。"""

    def test_amap_harvester_passes_direct_proxies(self):
        harvester = AmapPoiHarvester(PROFILE, ["图书馆"], api_key="key",
                                     max_retries=0, request_interval_seconds=0.0)

        with patch.object(harvest.requests, "get", return_value=_FakeResponse()) as get:
            harvester._request_json({"keywords": "图书馆"})

        self.assertEqual(get.call_args.kwargs.get("proxies"), {"http": None, "https": None})

    def test_amap_harvester_honours_amap_proxy_env(self):
        harvester = AmapPoiHarvester(PROFILE, ["图书馆"], api_key="key",
                                     max_retries=0, request_interval_seconds=0.0)

        with patch.dict("os.environ", {AMAP_PROXY_ENV: "http://127.0.0.1:7897"}), \
                patch.object(harvest.requests, "get", return_value=_FakeResponse()) as get:
            harvester._request_json({"keywords": "图书馆"})

        self.assertEqual(get.call_args.kwargs.get("proxies"),
                         {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})

    def test_public_web_harvester_keeps_system_default(self):
        harvester = PublicWebHarvester(["https://example.com/a"], max_pages=1)

        with patch.dict("os.environ", {WEB_PROXY_ENV: ""}), \
                patch.object(harvest.requests, "get", return_value=_FakeResponse()) as get:
            try:
                harvester._fetch("https://example.com/a")
            except Exception:  # 只关心调用参数，响应内容不重要
                pass

        self.assertEqual(get.call_args.kwargs.get("proxies"), {})

    def test_web_search_client_passes_proxies_for_both_providers(self):
        for provider in ("tavily", "brave"):
            with self.subTest(provider=provider), \
                    patch.dict("os.environ", {WEB_PROXY_ENV: ""}), \
                    patch.object(web_search, "BRAVE_ENDPOINT", "https://brave.invalid"), \
                    patch.object(web_search, "TAVILY_ENDPOINT", "https://tavily.invalid"):
                client = WebSearchClient(provider, api_key="key", timeout=1)
                method = "get" if provider == "brave" else "post"
                with patch.object(web_search.requests, method,
                                  return_value=_FakeResponse()) as called:
                    client._request_json({"query": "示例"})

                self.assertEqual(called.call_args.kwargs.get("proxies"), {})


if __name__ == "__main__":
    unittest.main()
