"""行程规划的 /api/chat 路由测试：意图优先级与起点来源。

锁定四条约定（都是本轮踩过的坑）：
1. 行程意图优先于"指哪问哪"（针尖分支）与"机构位置查询"，不会被它们抢走；
2. 起点优先用户定位，其次地图针尖，且针尖必须**落在行程校区内**才可用；
3. 起点判定细则（优先级、跨校区拒绝、脏坐标）由 `ItineraryOriginTests` 直接测纯函数，
   这里只验证"判定结果确实被送到了编排入口"；
4. 常规模式（非 sakde）下行程不接管（模式开关仍然生效）。

不访问网络：大模型调用替换为桩，行程文案由桩返回。
"""

import json
import os
import unittest

# 测试隔离：不把用例问句写进真实的用户共建数据目录（见 USER_DATA_PIPELINE.md）
os.environ.setdefault('USERDATA_CAPTURE', '0')

import campus_config
import server


#: 截图里失效的那句原话：既含指示性措辞（"我看到的这个位置"），又含"校区/路线"。
INDICATIVE_PLANNING_QUERY = '请从我看到的这个位置出发，请你为我规划一下普陀校区的参观路线'

PUTUO = '普陀'

#: 一次行程请求要做 6 次候选排序（秒级），同一个请求体在多个用例里重复出现，
#: 因此按请求体缓存响应，避免测试套件被重复计算拖慢。
_RESPONSE_CACHE = {}


class FakeLlmResponse:
    """替代 requests 响应：只要拿到 choices[0].message.content 即可。"""

    def raise_for_status(self):
        return None

    def json(self):
        return {'choices': [{'message': {'content': '行程文案（测试桩）。'}}]}


class ItineraryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.original_post = server.requests.post
        server.requests.post = lambda *args, **kwargs: FakeLlmResponse()
        self.client = server.app.test_client()
        lng, lat = campus_config.center(PUTUO)
        self.putuo_center = {'lng': lng, 'lat': lat, 'zoom': 17.0}

    def tearDown(self):
        server.requests.post = self.original_post

    def _chat(self, query, cached=True, **extra):
        payload = {'messages': [{'role': 'user', 'content': query}]}
        payload.update(extra)
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if cached and key in _RESPONSE_CACHE:
            return _RESPONSE_CACHE[key]
        response = self.client.post('/api/chat', json=payload)
        self.assertEqual(response.status_code, 200)
        payload_json = response.get_json()
        if cached:
            _RESPONSE_CACHE[key] = payload_json
        return payload_json

    def _spy_origin(self, **extra):
        """跑一次请求并捕获真正传给 `plan_day` 的起点（必须绕过响应缓存）。"""
        captured = {}
        original = server.plan_day

        def spy(campus, candidates, origin=None, **kwargs):
            captured['origin'] = origin
            return original(campus, candidates, origin, **kwargs)

        server.plan_day = spy
        try:
            payload = self._chat(INDICATIVE_PLANNING_QUERY, cached=False,
                                 recommendationMode='sakde', **extra)
        finally:
            server.plan_day = original
        return payload, captured.get('origin')

    def test_indicative_planning_query_reaches_itinerary(self):
        payload = self._chat(INDICATIVE_PLANNING_QUERY,
                             userCampus=PUTUO,
                             recommendationMode='sakde',
                             viewCenter=self.putuo_center)
        self.assertEqual(payload['recommendation_engine'], 'itinerary')
        itinerary = payload['itinerary']
        self.assertEqual(itinerary['campus'], PUTUO)
        self.assertGreaterEqual(len(itinerary['stops']), 3)
        self.assertEqual([stop['seq'] for stop in itinerary['stops']],
                         list(range(1, len(itinerary['stops']) + 1)))
        # 三段都要有落点，且地图打点与站点一一对应
        self.assertEqual({stop['period'] for stop in itinerary['stops']},
                         {'morning', 'noon', 'afternoon'})
        self.assertEqual([pin['number'] for pin in payload['locations']],
                         [f"第{stop['seq']}站" for stop in itinerary['stops']])

    def test_itinerary_intent_wins_over_needle(self):
        """同句在针尖分支与行程分支都够条件时，必须由行程接管。"""
        self.assertTrue(server.is_indicative_query(INDICATIVE_PLANNING_QUERY))
        payload = self._chat(INDICATIVE_PLANNING_QUERY,
                             userCampus=PUTUO,
                             recommendationMode='sakde',
                             viewCenter=self.putuo_center)
        self.assertEqual(payload['recommendation_engine'], 'itinerary')
        self.assertNotIn('needle', payload)

    def test_needle_still_wins_for_pure_indicative_question(self):
        payload = self._chat('这里是哪',
                             userCampus=PUTUO,
                             recommendationMode='sakde',
                             viewCenter=self.putuo_center)
        self.assertEqual(payload['recommendation_engine'], 'needle')

    def test_view_center_is_used_as_origin(self):
        payload, origin = self._spy_origin(userCampus=PUTUO,
                                           viewCenter=self.putuo_center)
        self.assertEqual(payload['recommendation_engine'], 'itinerary')
        self.assertIsNotNone(origin)
        self.assertAlmostEqual(origin[0], self.putuo_center['lng'], places=6)
        self.assertAlmostEqual(origin[1], self.putuo_center['lat'], places=6)

    def test_user_location_wins_over_view_center(self):
        location = {'lng': 121.403126, 'lat': 31.230404, 'campus': PUTUO,
                    'trusted': True, 'useForDistance': True, 'source': 'amap'}
        payload, origin = self._spy_origin(userCampus=PUTUO,
                                           viewCenter=self.putuo_center,
                                           userLocation=location)
        self.assertEqual(payload['recommendation_engine'], 'itinerary')
        self.assertIsNotNone(origin)
        self.assertAlmostEqual(origin[0], location['lng'], places=6)
        self.assertAlmostEqual(origin[1], location['lat'], places=6)

    def test_origin_is_none_when_needle_is_in_another_campus(self):
        """针尖在别的校区时不能当起点，否则第一站会莫名地远（此时退回 plan_day 的默认起步）。"""
        other = next(name for name in campus_config.campus_names() if name != PUTUO)
        lng, lat = campus_config.center(other)
        payload, origin = self._spy_origin(userCampus=PUTUO,
                                           viewCenter={'lng': lng, 'lat': lat, 'zoom': 17.0})
        self.assertEqual(payload['recommendation_engine'], 'itinerary')
        self.assertIsNone(origin)

    def test_regular_mode_never_takes_itinerary_over(self):
        payload = self._chat(INDICATIVE_PLANNING_QUERY,
                             userCampus=PUTUO,
                             recommendationMode='rule',
                             viewCenter=self.putuo_center)
        self.assertNotEqual(payload.get('recommendation_engine'), 'itinerary')
        self.assertNotIn('itinerary', payload)


class ItineraryOriginTests(unittest.TestCase):
    """`_itinerary_origin` 的纯粹判定（不经过 HTTP）。"""

    def setUp(self):
        lng, lat = campus_config.center(PUTUO)
        self.putuo = {'lng': lng, 'lat': lat}
        self.other = next(name for name in campus_config.campus_names() if name != PUTUO)

    def test_ranking_location_wins(self):
        location = {'lng': 121.0, 'lat': 31.0, 'source': 'amap'}
        self.assertIs(server._itinerary_origin(PUTUO, location, self.putuo), location)

    def test_view_center_inside_campus_is_accepted(self):
        origin = server._itinerary_origin(PUTUO, None, dict(self.putuo, zoom=17.0))
        self.assertAlmostEqual(origin['lng'], self.putuo['lng'], places=6)
        self.assertAlmostEqual(origin['lat'], self.putuo['lat'], places=6)

    def test_view_center_outside_campus_is_rejected(self):
        lng, lat = campus_config.center(self.other)
        self.assertIsNone(server._itinerary_origin(PUTUO, None, {'lng': lng, 'lat': lat}))
        self.assertIsNone(server._itinerary_origin(PUTUO, None, {'lng': 121.0, 'lat': 31.0}))

    def test_invalid_view_center_is_rejected(self):
        for value in (None, 'not-a-dict', {}, {'lng': 'abc', 'lat': 31.0},
                      {'lng': 121.4}, {'lng': None, 'lat': None}):
            self.assertIsNone(server._itinerary_origin(PUTUO, None, value), value)


if __name__ == '__main__':
    unittest.main()
