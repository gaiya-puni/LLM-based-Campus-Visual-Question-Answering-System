"""“指哪问哪”（地图中心针尖坐标）的单元测试。

锁定三条约定：
1. 脏坐标一律忽略（不参与判定，也不让请求走进针尖分支）；
2. 缩放级别决定查询粒度（放大答得更细）；
3. **核心回归：校区由坐标按地理判定，绝不被界面选中的校区覆盖**。
"""
import os
import unittest

# 测试隔离：不把用例问句写进真实的用户共建数据目录（见 USER_DATA_PIPELINE.md）
os.environ.setdefault('USERDATA_CAPTURE', '0')

import campus_config
import server


class ViewCenterParsingTests(unittest.TestCase):
    def test_missing_or_invalid_center_is_ignored(self):
        cases = (
            {},
            {'viewCenter': None},
            {'viewCenter': 'not-a-dict'},
            {'viewCenter': {'lng': 'abc', 'lat': 31.0}},
            {'viewCenter': {'lng': 121.4}},
            {'viewCenter': {'lng': 0.0, 'lat': 0.0}},      # 不在中国量级
            {'viewCenter': {'lng': -200.0, 'lat': 31.0}},
        )
        for payload in cases:
            self.assertIsNone(server.view_center_from_request(payload), payload)

    def test_valid_center_defaults_zoom_when_missing(self):
        center = server.view_center_from_request(
            {'viewCenter': {'lng': 121.4369, 'lat': 31.0256}})
        self.assertAlmostEqual(center['lng'], 121.4369)
        self.assertAlmostEqual(center['lat'], 31.0256)
        self.assertEqual(center['zoom'], 16.0)

    def test_zoom_drives_radius(self):
        self.assertEqual(server.needle_radius_m(18), 80.0)
        self.assertLess(server.needle_radius_m(17), server.needle_radius_m(15))
        self.assertLess(server.needle_radius_m(15), server.needle_radius_m(13))
        self.assertGreater(server.needle_radius_m(3), server.needle_radius_m(14))

    def test_indicative_query_detection(self):
        for query in ('这里是哪', '这是哪里？', '我现在在哪', '眼前是什么', '这块是什么'):
            self.assertTrue(server.is_indicative_query(query), query)
        for query in ('图书馆在哪里', '食堂怎么走', '', None, '思源湖'):
            self.assertFalse(server.is_indicative_query(query), query)


class RankingLocationSanitizingTests(unittest.TestCase):
    """`userLocation` 入参清洗：脏坐标宁可当"没有定位"，也不能让 /api/chat 500。"""

    def _location(self, lng, lat, **extra):
        payload = {'lng': lng, 'lat': lat, 'source': 'amap', 'trusted': True,
                   'useForDistance': True, 'campus': '普陀'}
        payload.update(extra)
        return server._ranking_location_from_request({'userLocation': payload})

    def test_non_finite_coordinates_are_dropped(self):
        # 回归：NaN 会让距离比较恒为假，最终在 round(nan) 抛 ValueError（500）
        for lng, lat in (('nan', 31.2), (121.4, 'nan'), (float('inf'), 31.2), (121.4, float('-inf'))):
            self.assertIsNone(self._location(lng, lat), f'{lng},{lat}')

    def test_out_of_range_coordinates_are_dropped(self):
        for lng, lat in ((0.0, 0.0), (-200.0, 31.2), (121.4, 0.0)):
            self.assertIsNone(self._location(lng, lat), f'{lng},{lat}')

    def test_valid_coordinates_still_pass(self):
        location = self._location(121.406079, 31.227073)
        self.assertIsNotNone(location)
        self.assertEqual(location['campus'], '普陀')

    def test_chat_survives_nan_user_location(self):
        client = server.app.test_client()
        for query, extra in (('普陀校区哪里适合看花', {}),
                             ('我第一次来普陀校区，帮我规划一天', {'recommendationMode': 'sakde'})):
            response = client.post('/api/chat', json={
                'messages': [{'role': 'user', 'content': query}],
                'userCampus': '普陀',
                'userLocation': {'lng': float('nan'), 'lat': 31.2, 'source': 'amap',
                                 'trusted': True, 'useForDistance': True, 'campus': '普陀'},
                **extra,
            })
            self.assertEqual(response.status_code, 200, query)


class NeedleAnswerTests(unittest.TestCase):
    def _center_of(self, campus, zoom=17.0):
        lng, lat = campus_config.center(campus)
        return {'lng': lng, 'lat': lat, 'zoom': zoom}

    def test_answer_reports_the_geo_campus(self):
        for campus in campus_config.campus_names():
            answer = server.needle_answer(self._center_of(campus))
            self.assertEqual(answer['needle']['campus'], campus)
            self.assertIn(campus, answer['choices'][0]['message']['content'])
            for location in answer['locations']:
                self.assertEqual(location['kind'], 'needle')
                self.assertTrue(location['name'])
                self.assertIsNotNone(location.get('distance_m'))

    def test_answer_never_uses_the_selected_campus(self):
        """坐标在 A 校区，界面选的是 B 校区：答案必须说 A，而不是 B。"""
        candidates = [name for name in campus_config.campus_names()
                      if name != campus_config.default_campus()]
        self.assertTrue(candidates, '配置里需要至少两个校区才能验证这条回归')
        target = candidates[0]
        client = server.app.test_client()
        response = client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': '这里是哪'}],
            'userCampus': campus_config.default_campus(),      # 界面上选的是别的校区
            'viewCenter': self._center_of(target, zoom=17.0),
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['recommendation_engine'], 'needle')
        self.assertEqual(payload['needle']['campus'], target)
        self.assertIn(target, payload['choices'][0]['message']['content'])
        self.assertNotIn(campus_config.default_campus(),
                         payload['choices'][0]['message']['content'])

    def test_outside_all_campuses_is_stated_clearly(self):
        answer = server.needle_answer({'lng': 121.0, 'lat': 31.0, 'zoom': 17.0})
        content = answer['choices'][0]['message']['content']
        self.assertIn('不在已登记的校区范围内', content)
        self.assertIsNone(answer['needle']['campus'])
        self.assertEqual(answer['locations'], [])       # 校外不标注任何未经核实的位置

    def test_coarse_zoom_gives_looser_radius(self):
        campus = campus_config.default_campus()
        coarse = server.needle_answer(self._center_of(campus, zoom=12.0))
        fine = server.needle_answer(self._center_of(campus, zoom=17.0))
        self.assertGreater(coarse['needle']['radiusM'], fine['needle']['radiusM'])
        self.assertLessEqual(len(fine['locations']), 3)
        self.assertLessEqual(len(coarse['locations']), 3)

    def test_zoom_out_hint_when_far_away(self):
        answer = server.needle_answer(self._center_of(
            campus_config.default_campus(), zoom=11.0))
        self.assertIn('放大', answer['choices'][0]['message']['content'])


if __name__ == '__main__':
    unittest.main()
