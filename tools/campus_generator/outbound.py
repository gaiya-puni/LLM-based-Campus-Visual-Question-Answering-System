"""出站 HTTP 的代理策略（采集器与搜索适配器共用）。

## 背景（实测过的坑）

Windows 上 `requests` 会去读注册表里的系统代理设置，Clash 一类工具通常会把它注册成
`http://127.0.0.1:7897`。于是**所有**出站请求都会绕到该代理上：

- 高德 Web 服务（`restapi.amap.com`，国内域名）经该代理直接抛 `ProxyError`，
  一次采集的 13 个关键词 × 3 次重试全部失败，看起来却像"高德查不到数据"；
- 大模型调用之所以没受影响，是因为 `llm_config.py` 显式传了
  `{'http': None, 'https': None}` 绕开系统代理。

## 为什么不能用 `proxies={}` 表示"直连"

`requests.Session.merge_environment_settings()` 在 `trust_env` 为真时会执行
`proxies.setdefault(k, env_proxies[k])`——传空字典反而会把系统代理**补回来**。
必须显式把每个 scheme 置为 `None`，`select_proxy()` 才会返回 None（真直连）。

## 两种口径

- `direct_proxies()`：**默认直连**，用于国内服务（高德）。
- `system_proxies()`：**默认沿用系统设置**，用于域名不确定的公开网页与境外搜索服务
  （Tavily / Brave）——这类站点在受限网络下往往必须走代理。

两者都可用各自的环境变量显式覆盖，便于不同网络环境的成员自行配置。
"""

import os

#: 高德 Web 服务：国内域名，默认不走代理。
AMAP_PROXY_ENV = "AMAP_HTTP_PROXY"
#: 公开网页与搜索服务：默认沿用系统代理设置。
WEB_PROXY_ENV = "WEB_HTTP_PROXY"

_NO_PROXY = {"http": None, "https": None}


def direct_proxies(env_var: str = AMAP_PROXY_ENV) -> dict:
    """返回"默认直连"的 proxies 参数；设置 env_var 时改为走该代理。"""
    proxy = (os.getenv(env_var) or "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return dict(_NO_PROXY)


def system_proxies(env_var: str = WEB_PROXY_ENV) -> dict:
    """返回"默认沿用系统设置"的 proxies 参数；设置 env_var 时改为固定走该代理。"""
    proxy = (os.getenv(env_var) or "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}
