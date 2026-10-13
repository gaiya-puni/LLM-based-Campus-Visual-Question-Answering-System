"""用户共建链路的接口级测试：采集、反馈、上报、审核、导出。

锁定五条约定：
1. 未收录问答会静默采集，但**回答内容一字不变**；
2. 采集失败（例如目录不可写）绝不影响问答本身；
3. 反馈与上报接口可用，坐标必须落在已登记校区内；
4. 审核态接口在未配置密钥时一律 403（不能因为"忘了配"而裸奔）；
5. 审核 → 导出能产出字段合规的 POI 记录。

不访问网络：大模型调用被替换为桩。
"""

import json
import os
import shutil
import tempfile
import unittest

import campus_config
import server
import userdata_store as store

REVIEW_TOKEN = 'unit-test-review-token'
PUTUO_CENTER = campus_config.center('普陀')


class FakeLlmResponse:
    """同时兼容对话文案与抽取两种调用：返回内容是合法 JSON 数组时抽取层即可解析。"""

    def __init__(self, content='这是模拟回答。'):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {'choices': [{'message': {'content': self._content}}]}


class _UserdataTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ud_test_')
        os.environ['USERDATA_DIR'] = self.tmp
        os.environ['USERDATA_REVIEW_TOKEN'] = REVIEW_TOKEN
        # 本模块要验证采集本身，因此显式打开采集开关（其它测试模块可能把它关掉）
        self._prev_capture = os.environ.get('USERDATA_CAPTURE')
        os.environ.pop('USERDATA_CAPTURE', None)
        self.original_post = server.requests.post
        server.requests.post = lambda *args, **kwargs: FakeLlmResponse()
        # 限流桶按 (地址, 端点) 跨用例累积：不清掉的话，跑到后面的用例时
        # /api/place_report（10 次/分钟）会先返回 429，播种直接失败。
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server.requests.post = self.original_post
        os.environ.pop('USERDATA_DIR', None)
        os.environ.pop('USERDATA_REVIEW_TOKEN', None)
        if self._prev_capture is None:
            os.environ.pop('USERDATA_CAPTURE', None)
        else:
            os.environ['USERDATA_CAPTURE'] = self._prev_capture
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _chat(self, query, campus='普陀', mode='sakde'):
        response = self.client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': query}],
            'userCampus': campus,
            'recommendationMode': mode,
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def _headers(self):
        return {'X-Review-Token': REVIEW_TOKEN}


class CaptureTests(_UserdataTestCase):
    def test_unknown_place_with_clean_name_is_captured(self):
        payload = self._chat('明月湖在哪里')
        self.assertIn('choices', payload)                       # 回答照旧
        pending = store.load_pending()
        names = [item['name'] for item in pending['items']]
        self.assertEqual(names, ['明月湖'])
        item = pending['items'][0]
        self.assertEqual(item['campus'], '普陀')
        self.assertEqual(item['source'], 'rule')
        self.assertEqual(item['status'], 'pending')
        self.assertIn('明月湖在哪里', item['samples'])

    def test_repeated_mentions_accumulate(self):
        self._chat('明月湖在哪里')
        self._chat('明月湖在哪里')
        self.assertEqual(store.load_pending()['items'][0]['mentions'], 2)

    def test_known_place_is_not_captured(self):
        # 已在册的地点不该进清单（由 _known_place_names 排除）
        self._chat('图书馆在哪里')
        self.assertEqual(store.load_pending()['items'], [])

    def test_scene_and_chitchat_queries_are_not_captured(self):
        for query in ('普陀校区哪里适合看花', '今天天气真好呀', '你好呀'):
            self._chat(query)
        self.assertEqual(store.load_pending()['items'], [])
        self.assertEqual(store.read_events('unresolved'), [])

    def test_unsupported_query_is_deferred_for_batch_extraction(self):
        self._chat('校医院在哪里')
        unresolved = [event['query'] for event in store.read_events('unresolved')]
        self.assertEqual(unresolved, ['校医院在哪里'])

    def test_answer_is_unchanged_by_capture(self):
        payload = self._chat('明月湖在哪里')
        content = payload['choices'][0]['message']['content']
        self.assertEqual(content, '这是模拟回答。')

    def test_capture_failure_never_breaks_chat(self):
        # 把数据目录指到一个"文件"上，写入必然失败；问答必须照常返回
        blocker = os.path.join(self.tmp, 'blocker')
        with open(blocker, 'w', encoding='utf-8') as handle:
            handle.write('x')
        os.environ['USERDATA_DIR'] = blocker
        payload = self._chat('明月湖在哪里')
        self.assertEqual(payload['choices'][0]['message']['content'], '这是模拟回答。')
        self.assertEqual(store.load_pending()['items'], [])

    def test_inline_mode_uses_llm_when_rules_miss(self):
        server.requests.post = lambda *args, **kwargs: FakeLlmResponse(
            '[{"name": "文创小店", "category": "scene"}]')
        os.environ['USERDATA_LLM_EXTRACT'] = 'inline'
        try:
            self._chat('那个卖文创的小店在哪')
        finally:
            os.environ.pop('USERDATA_LLM_EXTRACT', None)
        items = store.load_pending()['items']
        self.assertEqual([item['name'] for item in items], ['文创小店'])
        self.assertEqual(items[0]['source'], 'llm')

    def test_batch_mode_does_not_call_llm_from_chat(self):
        calls = []

        def spy(*args, **kwargs):
            calls.append(args)
            return FakeLlmResponse()

        server.requests.post = spy
        self._chat('校医院在哪里')
        # 未收录分支是确定性秒回，batch 模式下不应产生任何大模型调用
        self.assertEqual(calls, [])


class FeedbackApiTests(_UserdataTestCase):
    def test_rating_is_recorded(self):
        response = self.client.post('/api/feedback', json={
            'messageId': 'm-1', 'rating': 'down', 'reason': '没回答地点',
            'query': '文创小店在哪', 'messageEngine': 'rule', 'campus': '普陀',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        events = store.read_events('rating')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['rating'], 'down')
        self.assertEqual(events[0]['reason'], '没回答地点')

    def test_rating_masks_sensitive_text(self):
        self.client.post('/api/feedback', json={
            'messageId': 'm-2', 'rating': 'down',
            'reason': '我叫张伟，电话13812345678', 'query': 'x',
        })
        reason = store.read_events('rating')[0]['reason']
        self.assertNotIn('13812345678', reason)
        self.assertNotIn('张伟', reason)

    def test_rating_without_message_id_is_rejected(self):
        # 没有 messageId 的评价无法追溯到任何一条回答，属脏数据
        response = self.client.post('/api/feedback', json={'rating': 'up'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(store.read_events('rating'), [])

    def test_invalid_rating_is_rejected(self):
        response = self.client.post('/api/feedback', json={'messageId': 'm', 'rating': 'maybe'})
        self.assertEqual(response.status_code, 400)


class PlaceReportApiTests(_UserdataTestCase):
    def _report(self, **overrides):
        payload = {'name': '文创小店', 'campus': '普陀',
                   'lng': PUTUO_CENTER[0], 'lat': PUTUO_CENTER[1],
                   'category': 'canteen', 'note': '在图书馆旁边', 'querySnippet': '文创小店在哪'}
        payload.update(overrides)
        return self.client.post('/api/place_report', json=payload)

    def test_report_enters_pending_list(self):
        response = self._report()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['counts']['pending'], 1)
        item = store.load_pending()['items'][0]
        self.assertEqual(item['name'], '文创小店')
        self.assertEqual(item['source'], 'user')
        self.assertEqual(item['confidence'], 1.0)
        self.assertEqual(item['category'], 'canteen')

    def test_report_outside_campus_is_rejected(self):
        response = self._report(lng=121.0, lat=31.0)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(store.load_pending()['items'], [])

    def test_campus_comes_from_coordinates_not_from_the_client(self):
        # 界面上选着闵行、点在普陀：必须以坐标为准记成普陀
        response = self._report(campus='闵行')
        self.assertEqual(response.get_json()['campus'], '普陀')
        self.assertEqual(store.load_pending()['items'][0]['campus'], '普陀')

    def test_report_validates_name_and_position(self):
        self.assertEqual(self._report(name='x').status_code, 400)
        self.assertEqual(self._report(lng=None, lat=None).status_code, 400)

    def test_non_finite_coordinates_are_rejected(self):
        """nan/inf 必须挡在门口：NaN 能骗过距离比较，且会让清单变成非法 JSON。"""
        for lng, lat in (('nan', 'nan'), (float('nan'), 31.0), ('inf', 31.0), (121.4, '-inf')):
            response = self._report(lng=lng, lat=lat)
            self.assertEqual(response.status_code, 400, f'{lng},{lat}')
        self.assertEqual(store.load_pending()['items'], [])
        # 清单必须是浏览器能直接 JSON.parse 的合法 JSON
        raw = (store.data_dir() / store.PENDING_FILE)
        if raw.exists():
            self.assertNotIn('NaN', raw.read_text(encoding='utf-8'))


class ReviewGateTests(_UserdataTestCase):
    def test_review_endpoints_require_token(self):
        for method, path, body in (
            ('get', '/api/userdata/pending', None),
            ('post', '/api/userdata/review', {'id': 'p_x', 'status': 'approved'}),
            ('post', '/api/userdata/extract', {}),
            ('get', '/api/userdata/export', None),
        ):
            caller = getattr(self.client, method)
            response = caller(path, json=body) if body is not None else caller(path)
            self.assertEqual(response.status_code, 403, path)

    def test_short_token_configuration_stays_closed(self):
        os.environ['USERDATA_REVIEW_TOKEN'] = 'short'
        self.assertEqual(self.client.get('/api/userdata/pending').status_code, 403)

    def test_wrong_token_is_rejected(self):
        response = self.client.get('/api/userdata/pending', headers={'X-Review-Token': 'wrong-token-value'})
        self.assertEqual(response.status_code, 403)


class ReviewFlowTests(_UserdataTestCase):
    def setUp(self):
        super().setUp()
        self.client.post('/api/place_report', json={
            'name': '文创小店', 'campus': '普陀',
            'lng': PUTUO_CENTER[0], 'lat': PUTUO_CENTER[1], 'category': 'canteen',
        })

    def _item_id(self):
        return store.load_pending()['items'][0]['id']

    def test_pending_list_supports_filters(self):
        body = self.client.get('/api/userdata/pending', headers=self._headers()).get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['counts']['pending'], 1)
        filtered = self.client.get(
            '/api/userdata/pending?campus=闵行', headers=self._headers()).get_json()
        self.assertEqual(filtered['total'], 0)

    def test_approve_then_export_produces_poi_records(self):
        item_id = self._item_id()
        response = self.client.post('/api/userdata/review', headers=self._headers(),
                                    json={'id': item_id, 'status': 'approved', 'note': '已核实'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['counts']['approved'], 1)
        summary = self.client.get('/api/userdata/export', headers=self._headers()).get_json()
        self.assertEqual(summary['approved'], 1)
        with open(os.path.join(self.tmp, store.APPROVED_FILE), encoding='utf-8') as handle:
            records = json.load(handle)['records']
        self.assertEqual(len(records), 1)
        record = records[0]
        for field in ('id', 'category', 'subCategory', 'name', 'locationName', 'lng', 'lat', 'text', 'tags'):
            self.assertIn(field, record)
        self.assertIsInstance(record['tags'], list)
        self.assertEqual(record['campus'], '普陀')
        self.assertEqual(record['subCategory'], '餐饮')

    def test_reject_marks_item_without_exporting(self):
        self.client.post('/api/userdata/review', headers=self._headers(),
                         json={'id': self._item_id(), 'status': 'rejected'})
        counts = store.load_pending()['counts']
        self.assertEqual((counts['rejected'], counts['approved']), (1, 0))
        self.assertEqual(store.export_approved()['approved'], 0)

    def test_invalid_status_is_rejected(self):
        response = self.client.post('/api/userdata/review', headers=self._headers(),
                                    json={'id': self._item_id(), 'status': 'whatever'})
        self.assertEqual(response.status_code, 400)

    def test_extract_backfills_campus_from_the_original_query(self):
        # 问句落盘时带了校区，大模型没给校区时要回填，否则候选变成"未标校区"
        store.record_unresolved_query('那个荷花池在哪', campus='闵行')
        server.requests.post = lambda *args, **kwargs: FakeLlmResponse('[{"name": "荷花池"}]')
        result = self.client.post('/api/userdata/extract', headers=self._headers(), json={}).get_json()
        self.assertEqual(result['added'], 1)
        item = next(entry for entry in store.load_pending()['items'] if entry['name'] == '荷花池')
        self.assertEqual(item['campus'], '闵行')

    def test_extract_endpoint_uses_llm_and_skips_scanned_queries(self):
        store.record_unresolved_query('那个卖文创的小店在哪', campus='普陀')
        server.requests.post = lambda *args, **kwargs: FakeLlmResponse(
            '[{"name": "文创小店", "category": "canteen"}]')
        first = self.client.post('/api/userdata/extract', headers=self._headers(), json={}).get_json()
        self.assertTrue(first['success'])
        self.assertEqual(first['processed'], 1)
        names = [item['name'] for item in store.load_pending()['items']]
        self.assertIn('文创小店', names)
        second = self.client.post('/api/userdata/extract', headers=self._headers(), json={}).get_json()
        self.assertEqual(second['processed'], 0)      # 已研判过的问句不再重复付费


if __name__ == '__main__':
    unittest.main()
