"""校园资产生成路由的状态码契约测试：400 / 403 / 404 / 409 / 413。

`test_generator.py` 已经覆盖采集与发布的业务路径，本模块只补"错误分支"：

- 任务号格式非法 → 400；
- 任务号合法但查不到 → 404；
- 写操作缺少审核令牌 → 403（读任务状态不设门禁，否则前端轮询要把令牌放进浏览器）；
- 任务未就绪 → 409；
- 请求体超过上限 → 413，且必须是 JSON（默认 HTML 错误页前端解析不了）。
"""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import server
from tools.campus_generator.jobs import JobNotReadyError

REVIEW_TOKEN = 'unit-test-review-token-1234'
REVIEW_HEADERS = {'X-Review-Token': REVIEW_TOKEN}
VALID_JOB_ID = 'a' * 32


@contextmanager
def _empty_dir_lease():
    with tempfile.TemporaryDirectory(prefix='campus_build_lease_') as directory:
        yield Path(directory)


class _StubManager:
    """只实现测试需要触达的方法；缺失的方法会在调错时立刻暴露为 AttributeError。

    `use_operation=False` 时把 `operation` 置空，让 `_campus_job_operation` 走
    `_campus_job_output` 兜底路径（真实任务管理器在旧版本里就是这个形状）。
    """

    def __init__(self, record=None, ready=True, use_operation=True):
        self.record = record
        self.ready = ready
        self.leases = []
        if not use_operation:
            self.operation = None

    def get(self, job_id):
        return self.record

    def output_path(self, job_id):
        return Path(tempfile.gettempdir()) / 'campus_build_stub' / job_id

    @contextmanager
    def operation(self, job_id, name):
        self.leases.append(name)
        if not self.ready:
            raise JobNotReadyError('job is not stable yet')
        with _empty_dir_lease() as output:
            yield output


class _BuildRouteTestCase(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()
        self._original_manager = server._CAMPUS_BUILD_MANAGER
        self._original_body_limit = server.app.config['MAX_CONTENT_LENGTH']

    def tearDown(self):
        server._CAMPUS_BUILD_MANAGER = self._original_manager
        server.app.config['MAX_CONTENT_LENGTH'] = self._original_body_limit
        server._rate_limit_buckets.clear()


class JobLookupTests(_BuildRouteTestCase):
    def test_malformed_job_id_is_rejected_with_400(self):
        for job_id in ('short', 'z' * 32, 'A' * 32):
            with self.subTest(job_id=job_id):
                response = self.client.get(f'/api/campus/build/{job_id}')
                self.assertEqual(response.status_code, 400)
                self.assertIn('job id', response.get_json()['message'])

    def test_unknown_job_is_reported_as_404(self):
        server._CAMPUS_BUILD_MANAGER = _StubManager(record=None)

        response = self.client.get(f'/api/campus/build/{VALID_JOB_ID}')

        self.assertEqual(response.status_code, 404)
        self.assertIn('not found', response.get_json()['message'])

    def test_status_read_is_not_behind_the_review_token(self):
        server._CAMPUS_BUILD_MANAGER = _StubManager(
            record={'id': VALID_JOB_ID, 'status': 'completed'})

        with patch.dict(os.environ, {'USERDATA_REVIEW_TOKEN': ''}):
            response = self.client.get(f'/api/campus/build/{VALID_JOB_ID}')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])


class MutationTokenTests(_BuildRouteTestCase):
    def test_admin_write_endpoints_require_the_review_token(self):
        requests = (
            ('post', f'/api/campus/build/{VALID_JOB_ID}/review', {'decisions': {}}),
            ('post', f'/api/campus/build/{VALID_JOB_ID}/publish', {}),
        )
        with patch.dict(os.environ, {'USERDATA_REVIEW_TOKEN': ''}):
            for method, url, payload in requests:
                with self.subTest(url=url):
                    response = getattr(self.client, method)(url, json=payload)
                    self.assertEqual(response.status_code, 403)
                    self.assertIn('USERDATA_REVIEW_TOKEN', response.get_json()['message'])

    def test_public_discovery_does_not_require_admin_token(self):
        with patch.dict(os.environ, {'USERDATA_REVIEW_TOKEN': ''}):
            response = self.client.post(
                '/api/campus/discover',
                json={'query': '帮我做闵行校区拍照热力图', 'includeAmap': False,
                      'includeWeb': False},
            )
        self.assertNotEqual(response.status_code, 403)

    def test_publish_plan_is_intentionally_a_read_without_a_token(self):
        """publish-plan 只回差异、不改正式 POI，且需要 32 位随机 jobId 才能问到，
        所以与其它读端点一样不设门禁——这里把它锁成"行为"而不是"巧合"。"""
        server._CAMPUS_BUILD_MANAGER = _StubManager(
            record=None, ready=True, use_operation=False)

        with patch.dict(os.environ, {'USERDATA_REVIEW_TOKEN': ''}):
            response = self.client.get(f'/api/campus/build/{VALID_JOB_ID}/publish-plan')

        self.assertEqual(response.status_code, 404)


class JobReadinessTests(_BuildRouteTestCase):
    def test_unstable_job_yields_409(self):
        server._CAMPUS_BUILD_MANAGER = _StubManager(record=None, ready=False)

        response = self.client.get(f'/api/campus/build/{VALID_JOB_ID}/review')

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()['success'])

    def test_malformed_job_id_on_review_read_is_rejected_with_400(self):
        server._CAMPUS_BUILD_MANAGER = _StubManager(
            record=None, ready=True, use_operation=False)

        response = self.client.get('/api/campus/build/not-a-job-id/review')

        self.assertEqual(response.status_code, 400)


class RequestBodyLimitTests(_BuildRouteTestCase):
    def test_oversized_body_returns_json_413_not_html(self):
        """图片链路依赖这条：默认 HTML 错误页会让前端拿到无法解析的响应。"""
        server.app.config['MAX_CONTENT_LENGTH'] = 1024

        response = self.client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': 'x' * 4096}]})

        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertFalse(payload['success'])
        self.assertIn('请求体超过', payload['message'])


if __name__ == '__main__':
    unittest.main()
