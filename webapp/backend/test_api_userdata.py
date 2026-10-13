"""用户共建路由的直测：令牌门禁（403）、入参校验（400）与落盘契约。

拆分前 `/api/feedback`、`/api/place_report`、`/api/userdata/*` 只被"经 /api/chat 旁路"
间接覆盖，审核门禁与参数边界几乎没有直测。本模块补上。

隔离方式与 `test_chat_usercapture.py` 一致：把 `USERDATA_DIR` 指到临时目录，
审核令牌通过环境变量注入，测试结束后清理。
"""

import os
import shutil
import tempfile
import unittest

import campus_config
import server

REVIEW_TOKEN = 'unit-test-review-token-1234'
PUTUO_CENTER = campus_config.center('普陀')
#: 明确落在所有校区信任半径之外的坐标（用于验证"校外点一律拒绝"）。
OUTSIDE_CAMPUS = (121.0000, 30.0000)


class _UserdataRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ud_routes_')
        os.environ['USERDATA_DIR'] = self.tmp
        os.environ['USERDATA_REVIEW_TOKEN'] = REVIEW_TOKEN
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server._rate_limit_buckets.clear()
        os.environ.pop('USERDATA_DIR', None)
        os.environ.pop('USERDATA_REVIEW_TOKEN', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _token_headers(token=REVIEW_TOKEN):
        return {'X-Review-Token': token}


class ReviewGateTests(_UserdataRouteTestCase):
    """审核态接口在"没配密钥 / 密钥不对"时必须一律 403，而不是放行。"""

    def test_review_endpoints_are_closed_without_a_token(self):
        requests = (
            ('get', '/api/userdata/pending', None),
            ('post', '/api/userdata/review', {'id': 'x', 'status': 'approved'}),
            ('post', '/api/userdata/extract', {'limit': 5}),
            ('get', '/api/userdata/export', None),
        )
        for method, url, payload in requests:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, json=payload)
                self.assertEqual(response.status_code, 403)
                self.assertIn('USERDATA_REVIEW_TOKEN', response.get_json()['message'])

    def test_a_mismatched_token_is_rejected(self):
        response = self.client.get('/api/userdata/pending',
                                   headers=self._token_headers('wrong-token-value-1234'))

        self.assertEqual(response.status_code, 403)

    def test_a_short_configured_token_keeps_the_door_closed(self):
        """配置了但太短：等于没有门禁，必须仍然拒绝（而不是"配了就放行"）。"""
        os.environ['USERDATA_REVIEW_TOKEN'] = 'too-short'

        response = self.client.get('/api/userdata/pending',
                                   headers=self._token_headers('too-short'))

        self.assertEqual(response.status_code, 403)

    def test_correct_token_opens_the_pending_list(self):
        response = self.client.get('/api/userdata/pending', headers=self._token_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        for key in ('counts', 'stats', 'extractionMode', 'total', 'page', 'pageSize', 'items'):
            self.assertIn(key, payload)


class PendingListTests(_UserdataRouteTestCase):
    def test_pagination_is_clamped_instead_of_trusting_the_client(self):
        response = self.client.get('/api/userdata/pending?page=0&pageSize=9999',
                                   headers=self._token_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['pageSize'], 200)

    def test_garbage_pagination_falls_back_to_defaults(self):
        response = self.client.get('/api/userdata/pending?page=abc&pageSize=null',
                                   headers=self._token_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual((payload['page'], payload['pageSize']), (1, 50))

    def test_extract_without_pending_queries_reports_zero(self):
        response = self.client.post('/api/userdata/extract', json={'limit': 5},
                                    headers=self._token_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual((payload['processed'], payload['added']), (0, 0))


class ReviewDecisionTests(_UserdataRouteTestCase):
    def test_invalid_status_is_rejected(self):
        response = self.client.post('/api/userdata/review', json={'id': 'x', 'status': 'maybe'},
                                    headers=self._token_headers())

        self.assertEqual(response.status_code, 400)
        self.assertIn('审核状态无效', response.get_json()['message'])


class FeedbackRouteTests(_UserdataRouteTestCase):
    def test_invalid_rating_is_rejected(self):
        response = self.client.post('/api/feedback', json={'messageId': 'm1', 'rating': 'meh'})

        self.assertEqual(response.status_code, 400)

    def test_missing_message_id_is_rejected(self):
        """messageId 是评价挂靠的唯一依据，缺了就成了一条无法追溯的脏数据。"""
        response = self.client.post('/api/feedback', json={'rating': 'up'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('消息标识', response.get_json()['message'])

    def test_valid_feedback_is_recorded(self):
        response = self.client.post('/api/feedback', json={
            'messageId': 'm1', 'rating': 'up', 'reason': '答得准',
            'query': '河西食堂在哪里', 'messageEngine': 'rule', 'campus': '普陀'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_withdrawal_is_recorded_as_a_rating_event(self):
        response = self.client.post('/api/feedback', json={'messageId': 'm1', 'rating': 'none'})

        self.assertEqual(response.status_code, 200)


class PlaceReportRouteTests(_UserdataRouteTestCase):
    def test_name_length_is_enforced(self):
        for name in ('短', 'x' * 21):
            with self.subTest(name=name):
                response = self.client.post('/api/place_report', json={
                    'name': name, 'lng': PUTUO_CENTER[0], 'lat': PUTUO_CENTER[1]})
                self.assertEqual(response.status_code, 400)

    def test_non_numeric_coordinates_are_rejected(self):
        response = self.client.post('/api/place_report', json={
            'name': '文创小店', 'lng': 'abc', 'lat': None})

        self.assertEqual(response.status_code, 400)
        self.assertIn('地图上点选', response.get_json()['message'])

    def test_non_finite_coordinates_are_rejected(self):
        """NaN 能骗过距离比较、且落盘后是非法 JSON，必须在入口挡掉。"""
        response = self.client.post('/api/place_report', json={
            'name': '文创小店', 'lng': float('nan'), 'lat': float('nan')})

        self.assertEqual(response.status_code, 400)
        self.assertIn('坐标无效', response.get_json()['message'])

    def test_coordinates_outside_every_campus_are_rejected(self):
        response = self.client.post('/api/place_report', json={
            'name': '校园外小店', 'lng': OUTSIDE_CAMPUS[0], 'lat': OUTSIDE_CAMPUS[1]})

        self.assertEqual(response.status_code, 400)
        self.assertIn('校区范围', response.get_json()['message'])

    def test_campus_is_derived_from_coordinates_not_from_the_client(self):
        """界面上选普陀、点在闵行也必须记成闵行，不能采信前端传来的校区名。"""
        minhang = campus_config.center('闵行')
        response = self.client.post('/api/place_report', json={
            'name': '文创小店', 'campus': '普陀',
            'lng': minhang[0], 'lat': minhang[1], 'category': 'canteen'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['campus'], '闵行')

    def test_valid_report_returns_an_auditable_item_id(self):
        response = self.client.post('/api/place_report', json={
            'name': '文创小店', 'lng': PUTUO_CENTER[0], 'lat': PUTUO_CENTER[1],
            'category': 'canteen', 'note': '新开的'})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['campus'], '普陀')
        self.assertTrue(payload['itemId'])


if __name__ == '__main__':
    unittest.main()
