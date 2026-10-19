"""用户手工补点路由的契约测试：公开可写、权限隔离、错误码。

补点属于"普通用户无需令牌"的能力（与 `/selection` 同级），但它只能落在任务私有层，
因此这里同时验证：无令牌可写、不属于自己的任务不可写、非法输入返回 400。
"""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import server
from tools.campus_generator.jobs import JobNotReadyError

VALID_JOB_ID = 'a' * 32
PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区", "slug": "demo_main", "center": [121.0, 31.0],
        "trustRadiusM": 1200,
    },
}
PAYLOAD = {
    "name": "思源湖西岸草坪", "lng": 121.0005, "lat": 31.0005,
    "category": "scene", "subCategory": "草坪", "scenes": ["walk"],
}


class _DirManager:
    """Lease a real job directory so the route can read/write its bundle."""

    def __init__(self, directory, record=None, ready=True):
        self.directory = Path(directory)
        self.record = record if record is not None else {
            'id': VALID_JOB_ID, 'status': 'completed'}
        self.ready = ready
        self.invalidated = []

    def get(self, job_id):
        return self.record

    def output_path(self, job_id):
        return self.directory

    @contextmanager
    def operation(self, job_id, name):
        if not self.ready:
            raise JobNotReadyError('job is not stable yet')
        yield self.directory

    def invalidate_preview(self, job_id):
        self.invalidated.append(job_id)


class _UserPoiRouteTestCase(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()
        self._original_manager = server._CAMPUS_BUILD_MANAGER
        self._temp = tempfile.TemporaryDirectory(prefix='campus_user_poi_')
        self.root = Path(self._temp.name)
        (self.root / 'campus_profile.json').write_text(
            json.dumps(PROFILE, ensure_ascii=False), encoding='utf-8')
        (self.root / 'normalized_pois.json').write_text('[]', encoding='utf-8')
        (self.root / 'approved_pois.json').write_text('[]', encoding='utf-8')
        (self.root / 'build_report.json').write_text(
            json.dumps({'theme': 'walk'}, ensure_ascii=False), encoding='utf-8')
        self.manager = _DirManager(self.root)
        server._CAMPUS_BUILD_MANAGER = self.manager

    def tearDown(self):
        server._CAMPUS_BUILD_MANAGER = self._original_manager
        server._rate_limit_buckets.clear()
        self._temp.cleanup()

    def test_public_user_can_add_and_delete_without_admin_token(self):
        response = self.client.post(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois', json=PAYLOAD)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['item']['source'], 'manual')
        self.assertTrue(body['item']['userAdded'])
        self.assertIn(body['item']['id'], body['selection']['selectedIds'])
        self.assertEqual(self.manager.invalidated, [VALID_JOB_ID])

        deleted = self.client.delete(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois/{body["item"]["id"]}')
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()['count'], 0)

    def test_invalid_payload_and_unknown_point_are_client_errors(self):
        bad = self.client.post(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois',
            json={**PAYLOAD, 'name': ''})
        self.assertEqual(bad.status_code, 400)

        outside = self.client.post(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois',
            json={**PAYLOAD, 'lng': 121.9, 'lat': 31.9})
        self.assertEqual(outside.status_code, 400)
        self.assertIn('校区', outside.get_json()['message'])

        missing = self.client.delete(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois/not-a-user-point')
        self.assertEqual(missing.status_code, 404)

    def test_other_users_job_is_hidden_and_unstable_job_is_409(self):
        server._CAMPUS_BUILD_MANAGER = _DirManager(
            self.root, record={'id': VALID_JOB_ID, 'status': 'completed',
                               'ownerId': 'x' * 40})
        denied = self.client.post(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois', json=PAYLOAD)
        self.assertEqual(denied.status_code, 404)

        server._CAMPUS_BUILD_MANAGER = _DirManager(self.root, ready=False)
        # reviewer/普通用户写入前要求任务包稳定，未完成的任务不得写入半个 bundle。
        unstable = self.client.post(
            f'/api/campus/build/{VALID_JOB_ID}/user-pois', json=PAYLOAD)
        self.assertEqual(unstable.status_code, 409)


if __name__ == '__main__':
    unittest.main()
