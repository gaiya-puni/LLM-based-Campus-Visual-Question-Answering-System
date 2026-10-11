"""图片（多模态）链路测试：附件校验、消息构造、能力文案与 `/api/chat` 端到端接线。

这条链路此前只有实现、没有测试，也没有接线（`parse_attachments` / `build_llm_messages` /
`extract_places_from_photo` 全都没人调用）。本模块把"能不能用"和"用错了会不会崩"一起锁住。

三条硬约定：
1. 未配置视觉模型时**不把图片转发给上游**（纯文本模型收到 image_url 会直接报错）；
2. 附件校验失败一律 400，不静默丢弃——否则用户以为图传上去了；
3. 图中线索抽取失败（超时、非 JSON、HTTP 错误）只返回空结果，**不影响主回答**。
"""

import base64
import os
import unittest
from unittest.mock import patch

import server

#: 1x1 像素的合法 PNG，足够通过"前缀 + 体积"校验。
PNG_BYTES = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/'
    'q842mQAAAABJRU5ErkJggg=='
)
PNG_DATA_URL = 'data:image/png;base64,' + base64.b64encode(PNG_BYTES).decode('ascii')

VISION_ENV = {
    'LLM_PROVIDER': 'deepseek',
    'DEEPSEEK_API_KEY': 'sk-text-only',
    'VISION_API_KEY': 'sk-vision',
}


def _attachment(data_url=PNG_DATA_URL, mime='image/png'):
    return {'mime': mime, 'dataUrl': data_url}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _answer(content='测试回答'):
    return FakeResponse({'choices': [{'message': {'role': 'assistant', 'content': content}}]})


def _photo_places(names):
    """模拟视觉模型返回"图中可读的地点名"（`parse_places_json` 约定的是 `{"places": [...]}`）。"""
    import json
    content = json.dumps({'places': list(names)}, ensure_ascii=False)
    return FakeResponse({'choices': [{'message': {'content': content}}]})


def _is_extraction_call(body):
    """抽取调用只有一条 user 消息；主回答一定带 system 提示词。"""
    messages = body.get('messages') or []
    return bool(messages) and messages[0].get('role') != 'system'


def _routing_post(places, calls):
    """按请求形态分流：抽取调用返回地点名，主回答调用返回普通文案。"""

    def post(url, **kwargs):
        body = kwargs.get('json') or {}
        calls.append(body)
        if _is_extraction_call(body):
            return _photo_places(places)
        return _answer()

    return post


class AttachmentValidationTests(unittest.TestCase):
    def test_no_attachments_is_not_an_error(self):
        items, error = server.parse_attachments({})
        self.assertEqual((items, error), ([], ''))

    def test_non_list_attachments_are_rejected(self):
        items, error = server.parse_attachments({'attachments': 'nope'})
        self.assertEqual(items, [])
        self.assertEqual(error, '图片格式无效')

    def test_too_many_attachments_are_rejected(self):
        raw = [_attachment() for _ in range(server.MAX_ATTACHMENTS + 1)]
        items, error = server.parse_attachments({'attachments': raw})
        self.assertEqual(items, [])
        self.assertIn(f'最多上传 {server.MAX_ATTACHMENTS} 张', error)

    def test_non_dict_entry_is_rejected(self):
        items, error = server.parse_attachments({'attachments': ['data:image/png;base64,AA']})
        self.assertEqual((items, error), ([], '图片格式无效'))

    def test_non_data_url_is_rejected(self):
        items, error = server.parse_attachments(
            {'attachments': [_attachment(data_url='https://example.com/a.png')]})
        self.assertEqual((items, error), ([], '图片格式无效'))

    def test_declared_mime_must_match_the_data_url(self):
        """声明 png 却给了 jpeg 的 dataUrl：必须拒绝，而不是按声明值信任。"""
        jpeg_prefix = 'data:image/jpeg;base64,' + PNG_DATA_URL.split(',', 1)[1]
        items, error = server.parse_attachments(
            {'attachments': [{'mime': 'image/png', 'dataUrl': jpeg_prefix}]})
        self.assertEqual((items, error), ([], '仅支持 JPG / PNG / WebP 图片'))

    def test_unsupported_mime_is_rejected(self):
        items, error = server.parse_attachments(
            {'attachments': [{'mime': 'image/gif', 'dataUrl': 'data:image/gif;base64,AAAA'}]})
        self.assertEqual(items, [])
        self.assertEqual(error, '仅支持 JPG / PNG / WebP 图片')

    def test_empty_payload_is_rejected(self):
        items, error = server.parse_attachments(
            {'attachments': [{'mime': 'image/png', 'dataUrl': 'data:image/png;base64,'}]})
        self.assertEqual((items, error), ([], '图片内容为空'))

    def test_oversized_image_is_rejected(self):
        oversized = 'A' * ((server.MAX_ATTACHMENT_BYTES * 4 // 3) + 8)
        items, error = server.parse_attachments(
            {'attachments': [{'mime': 'image/png', 'dataUrl': 'data:image/png;base64,' + oversized}]})
        self.assertEqual(items, [])
        self.assertIn('不能超过', error)

    def test_valid_attachment_reports_decoded_size(self):
        items, error = server.parse_attachments({'attachments': [_attachment()]})
        self.assertEqual(error, '')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['bytes'], len(PNG_BYTES))
        self.assertNotIn('\n', items[0]['dataUrl'])


class MultimodalMessageTests(unittest.TestCase):
    def test_without_attachments_messages_are_untouched(self):
        messages = [{'role': 'user', 'content': '你好'}]
        self.assertIs(server.build_llm_messages(messages, []), messages)

    def test_only_the_last_user_message_carries_images(self):
        messages = [
            {'role': 'user', 'content': '第一轮'},
            {'role': 'assistant', 'content': '回答'},
            {'role': 'user', 'content': '这是哪栋楼'},
        ]
        built = server.build_llm_messages(messages, [_attachment()])

        self.assertEqual(built[0]['content'], '第一轮')
        self.assertEqual(built[1]['content'], '回答')
        parts = built[2]['content']
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0], {'type': 'text', 'text': '这是哪栋楼'})
        self.assertEqual(parts[1]['type'], 'image_url')
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_empty_text_falls_back_to_a_placeholder(self):
        built = server.build_llm_messages([{'role': 'user', 'content': '   '}], [_attachment()])
        self.assertEqual(built[0]['content'][0]['text'], '请看这张图片。')


class ImageCapabilityClauseTests(unittest.TestCase):
    def test_clause_describes_the_image_rule_when_vision_is_configured(self):
        with patch.dict(os.environ, VISION_ENV):
            clause = server._image_capability_clause()
        self.assertIn('不得仅凭图片猜测地名', clause)

    def test_clause_says_disabled_when_no_vision_model_is_configured(self):
        with patch.dict(os.environ, {'LLM_PROVIDER': 'deepseek', 'DEEPSEEK_API_KEY': 'sk-text'},
                        clear=False), patch.dict(os.environ, {'VISION_ENABLED': 'false'}):
            clause = server._image_capability_clause()
        self.assertIn('本部署未启用图片理解', clause)
        self.assertNotIn('不得仅凭图片猜测地名', clause)


class ChatVisionIntegrationTests(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        server._rate_limit_buckets.clear()

    def _chat(self, calls, places=('包玉刚图书馆',), url='/api/chat', **extra):
        payload = {'messages': [{'role': 'user', 'content': '这是哪栋楼'}],
                   'attachments': [_attachment()]}
        payload.update(extra)
        with patch.object(server.requests, 'post', side_effect=_routing_post(list(places), calls)):
            return self.client.post(url, json=payload)

    def test_configured_vision_forwards_images_and_pins_photo_places(self):
        with patch.dict(os.environ, VISION_ENV):
            calls = []
            response = self._chat(calls, userCampus='交大闵行')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2, '应有一次图中线索抽取 + 一次主回答')

        extraction, main = calls
        self.assertIsInstance(extraction['messages'][-1]['content'], list)
        system_prompt = main['messages'][0]['content']
        self.assertIn('图中可读文字命中的校园地点', system_prompt)
        self.assertIn('不得仅凭图片猜测地名', system_prompt)

        main_user = main['messages'][-1]['content']
        self.assertIsInstance(main_user, list, '主回答也要带上图片')
        self.assertEqual(main_user[1]['type'], 'image_url')

        photo_pins = [loc for loc in response.json['locations'] if loc['kind'] == 'photo_poi']
        self.assertTrue(photo_pins, '图中命中的地点必须在地图上打点')
        self.assertIn('包玉刚图书馆', photo_pins[0]['name'])

    def test_text_only_deployment_never_forwards_images(self):
        """未配置视觉模型时仍要把图片转成文本提示，否则上游会因 image_url 直接报错。"""
        env = {'LLM_PROVIDER': 'deepseek', 'DEEPSEEK_API_KEY': 'sk-text', 'VISION_ENABLED': 'false'}
        with patch.dict(os.environ, env):
            calls = []
            response = self._chat(calls)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1, '没有视觉模型就不该发抽取请求')
        main_user = calls[0]['messages'][-1]['content']
        self.assertIsInstance(main_user, str)
        self.assertIn('本部署未启用图片理解', calls[0]['messages'][0]['content'])
        kinds = {loc['kind'] for loc in response.json['locations']}
        self.assertNotIn('photo_poi', kinds)

    def test_photo_place_extraction_failure_does_not_break_the_answer(self):
        with patch.dict(os.environ, VISION_ENV):

            def failing_post(url, **kwargs):
                body = kwargs.get('json') or {}
                if _is_extraction_call(body):
                    raise server.requests.Timeout('vision timeout')
                return _answer('仍然回答了')

            with patch.object(server.requests, 'post', side_effect=failing_post):
                response = self.client.post('/api/chat', json={
                    'messages': [{'role': 'user', 'content': '这是哪栋楼'}],
                    'attachments': [_attachment()]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['choices'][0]['message']['content'], '仍然回答了')
        kinds = {loc['kind'] for loc in response.json['locations']}
        self.assertNotIn('photo_poi', kinds)

    def test_invalid_attachment_is_rejected_with_400(self):
        response = self.client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': '这是哪栋楼'}],
            'attachments': [{'mime': 'image/gif', 'dataUrl': 'data:image/gif;base64,AAAA'}]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error'], '仅支持 JPG / PNG / WebP 图片')

    def test_malformed_messages_return_400_instead_of_500(self):
        cases = (
            {'messages': [{'role': 'user'}, 'oops']},
            {'messages': [{'content': '没有角色'}]},
            {'messages': [{'role': 'user', 'content': [{'type': 'text'}]}]},
            {'messages': 'not-a-list'},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post('/api/chat', json=payload)
                self.assertEqual(response.status_code, 400)

    def test_attachment_free_requests_keep_the_old_payload_shape(self):
        """回归护栏：没有图片时请求体必须与接线前逐字节一致（仍是 content 字符串）。"""
        with patch.dict(os.environ, VISION_ENV):
            calls = []
            with patch.object(server.requests, 'post', side_effect=_routing_post([], calls)):
                response = self.client.post('/api/chat', json={
                    'messages': [{'role': 'user', 'content': '河西食堂在哪里'}]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0]['messages'][-1]['content'], str)


if __name__ == '__main__':
    unittest.main()
