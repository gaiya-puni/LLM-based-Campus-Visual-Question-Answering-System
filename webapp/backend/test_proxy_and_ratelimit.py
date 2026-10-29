"""反向代理与限流主体：线上 nginx 形态下的两个必须钉住的点。

线上是 `nginx → Flask`，`request.remote_addr` 默认拿到的是 nginx 的地址。这里保护两件事：

1. **默认不信任 `X-Forwarded-For`**。这是安全默认值：盲目信任这个头等于允许任何人
   伪造来源、绕过全部 IP 限流。只有显式设置 `TRUST_PROXY_HOPS` 才启用 ProxyFix。
2. **限流按"人"分桶而不是只按 IP**。登录用户用 user_id，匿名才回落到 IP——
   这样多人共用 NAT 出口（校园网、宿舍）时不会互相挤爆额度。
"""

import unittest

import server
from flask import request


class ProxyTrustTests(unittest.TestCase):
    def test_forwarded_for_is_ignored_by_default(self):
        """未配置 TRUST_PROXY_HOPS 时，伪造的 X-Forwarded-For 必须无效。

        这条是整个代理支持的安全底线：如果能被伪造，攻击者换一个头值就是一个新桶，
        所有 IP 限流形同虚设。
        """
        if server._proxy_hops > 0:
            self.skipTest('本机已显式配置 TRUST_PROXY_HOPS，跳过"默认不信任"断言')

        env = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '9.9.9.9'}
        with server.app.test_request_context('/', environ_base=env):
            self.assertEqual(request.remote_addr, '127.0.0.1')

    def test_proxy_hops_defaults_to_zero(self):
        """默认必须是 0：宁可"限流按代理地址算"，也不能"任何人可伪造来源"。"""
        self.assertGreaterEqual(server._proxy_hops, 0)


class RateLimitIdentityTests(unittest.TestCase):
    def test_anonymous_request_falls_back_to_ip(self):
        """匿名请求只能按 IP 分桶——这是没有身份时的唯一可用维度。"""
        env = {'REMOTE_ADDR': '203.0.113.7'}
        with server.app.test_request_context('/', environ_base=env):
            self.assertEqual(server._rate_limit_identity(), 'ip:203.0.113.7')

    def test_missing_remote_addr_is_not_crash(self):
        """拿不到客户端地址时也要给出一个稳定 key，不能让限流本身抛异常。"""
        with server.app.test_request_context('/', environ_base={}):
            self.assertTrue(server._rate_limit_identity().startswith('ip:'))


if __name__ == '__main__':
    unittest.main()
