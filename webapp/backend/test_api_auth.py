"""鉴权类路由的直测：健康检查、情感数据、注册 / 登录 / 改密。

鉴权端点集中在本模块测试，包含从旧安全测试迁移的改密与限流检查。
本模块补齐入参校验、凭据错误、数据库不可用降级与历史明文口令自动升级等分支。

约定：
- 所有请求走 `server.app.test_client()`，不启动真实服务、不连数据库；
- 数据库与用户读写一律用 `patch('server.<name>')` 替换（这是门面设计的直接受益点）；
- 每个用例前清空限流桶，避免 5～10 次/分钟的阈值干扰断言。
"""

import os
import unittest
from unittest.mock import patch

import server


class _AuthRouteTestCase(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server._rate_limit_buckets.clear()


class HealthRouteTests(_AuthRouteTestCase):
    def test_health_reports_ok_when_database_answers(self):
        with patch('server.get_cursor') as cursor:
            response = self.client.get('/api/health')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['database'], 'ok')
        cursor.return_value.execute.assert_called_once_with('SELECT 1')

    def test_health_degrades_to_503_without_leaking_the_exception(self):
        with patch('server.get_cursor', side_effect=RuntimeError('database password leaked')):
            response = self.client.get('/api/health')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['database'], 'error')
        self.assertNotIn(b'password leaked', response.data)

    def test_health_reports_whether_image_understanding_is_enabled(self):
        """前端据此决定图片入口是可点还是带禁用提示，所以这个字段必须稳定存在。"""
        with patch('server.get_cursor'), patch.dict(os.environ, {'VISION_ENABLED': 'false'}):
            disabled = self.client.get('/api/health').get_json()['vision']
        with patch('server.get_cursor'), patch.dict(
                os.environ, {'VISION_API_KEY': 'sk-vision'}):
            enabled = self.client.get('/api/health').get_json()['vision']

        self.assertFalse(disabled)
        self.assertTrue(enabled)


class EmotionsRouteTests(_AuthRouteTestCase):
    def test_emotions_returns_the_dataset_exposed_on_the_facade(self):
        payload = [{'id': 7, 'owner': 'tester', 'slogan': 'hello'}]

        with patch.object(server, '_EMOTIONS', payload):
            response = self.client.get('/api/emotions')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), payload)


class RegisterRouteTests(_AuthRouteTestCase):
    def test_missing_credentials_are_rejected(self):
        response = self.client.post('/api/register_json', json={})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_username_length_and_password_strength_are_enforced(self):
        cases = (
            {'username': 'x' * 21, 'password': 'password123'},
            {'username': 'alice', 'password': 'short'},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post('/api/register_json', json=payload)
                self.assertEqual(response.status_code, 400)

    def test_existing_username_is_rejected_before_writing(self):
        with patch('server.get_user', return_value=server.User(1, 'alice', 'x')), \
                patch('server.create_user') as create_user:
            response = self.client.post(
                '/api/register_json', json={'username': 'alice', 'password': 'password123'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('已存在', response.get_json()['message'])
        create_user.assert_not_called()

    def test_successful_registration_writes_the_user(self):
        with patch('server.get_user', return_value=None), \
                patch('server.create_user') as create_user:
            response = self.client.post(
                '/api/register_json', json={'username': 'alice', 'password': 'password123'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        create_user.assert_called_once_with('alice', 'password123')


class LoginRouteTests(_AuthRouteTestCase):
    def test_missing_credentials_are_rejected(self):
        response = self.client.post('/api/login_json', json={'username': 'alice'})

        self.assertEqual(response.status_code, 400)

    def test_unknown_user_is_rejected_without_revealing_which_field_is_wrong(self):
        with patch('server.get_user', return_value=None):
            response = self.client.post(
                '/api/login_json', json={'username': 'nobody', 'password': 'password123'})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['message'], '用户名或密码有误')

    def test_wrong_password_is_rejected(self):
        user = server.User(1, 'alice', server.hash_password('correct-password'))
        with patch('server.get_user', return_value=user):
            response = self.client.post(
                '/api/login_json', json={'username': 'alice', 'password': 'wrong-password'})

        self.assertEqual(response.status_code, 401)

    def test_hashed_password_logs_in_without_rewriting_the_row(self):
        user = server.User(1, 'alice', server.hash_password('correct-password'))
        with patch('server.get_user', return_value=user), \
                patch('server.update_user_password') as update:
            response = self.client.post(
                '/api/login_json', json={'username': 'alice', 'password': 'correct-password'})

        self.assertEqual(response.status_code, 200)
        update.assert_not_called()

    def test_legacy_plaintext_password_is_upgraded_on_successful_login(self):
        """历史明文口令登录成功后就地改成哈希：否则这个账号会一直是明文。"""
        user = server.User(1, 'legacy', 'plaintext-password')
        with patch('server.get_user', return_value=user), \
                patch('server.update_user_password') as update:
            response = self.client.post(
                '/api/login_json', json={'username': 'legacy', 'password': 'plaintext-password'})

        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with('legacy', 'plaintext-password')


class ChangePasswordRouteTests(_AuthRouteTestCase):
    def test_missing_fields_are_rejected(self):
        cases = (
            {'username': 'alice'},
            {'username': 'alice', 'newPassword': 'new-password-123'},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post('/api/change_password', json=payload)
                self.assertEqual(response.status_code, 400)

    def test_new_password_is_length_checked(self):
        response = self.client.post('/api/change_password', json={
            'username': 'alice', 'currentPassword': 'correct-password', 'newPassword': 'short'})

        self.assertEqual(response.status_code, 400)

    def test_unknown_user_is_rejected(self):
        with patch('server.get_user', return_value=None), \
                patch('server.update_user_password') as update:
            response = self.client.post('/api/change_password', json={
                'username': 'nobody', 'currentPassword': 'whatever123',
                'newPassword': 'new-password-123'})

        self.assertEqual(response.status_code, 401)
        update.assert_not_called()

    def test_too_long_new_password_is_rejected(self):
        response = self.client.post('/api/change_password', json={
            'username': 'alice', 'currentPassword': 'correct-password',
            'newPassword': 'x' * 129})

        self.assertEqual(response.status_code, 400)

    def test_incorrect_current_password_is_rejected_without_writing(self):
        user = server.User(1, 'alice', server.hash_password('correct-password'))
        with patch('server.get_user', return_value=user), \
                patch('server.update_user_password') as update:
            response = self.client.post('/api/change_password', json={
                'username': 'alice', 'currentPassword': 'wrong-password',
                'newPassword': 'new-password-123'})

        self.assertEqual(response.status_code, 401)
        update.assert_not_called()

    def test_successful_change_updates_the_row(self):
        user = server.User(1, 'alice', server.hash_password('correct-password'))
        with patch('server.get_user', return_value=user), \
                patch('server.update_user_password') as update:
            response = self.client.post('/api/change_password', json={
                'username': 'alice', 'currentPassword': 'correct-password',
                'newPassword': 'new-password-123'})

        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with('alice', 'new-password-123')


class RateLimitTests(_AuthRouteTestCase):
    def test_rate_limit_rejects_excess_requests(self):
        for _ in range(5):
            self.client.post('/api/register_json', json={})
        response = self.client.post('/api/register_json', json={})
        self.assertEqual(response.status_code, 429)


if __name__ == '__main__':
    unittest.main()
