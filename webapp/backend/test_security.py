import unittest
from unittest.mock import patch

import server


class SecurityTests(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()

    def test_change_password_requires_current_password(self):
        response = self.client.post('/api/change_password', json={
            'username': 'alice',
            'newPassword': 'new-password-123',
        })
        self.assertEqual(response.status_code, 400)

    @patch('server.update_user_password')
    @patch('server.get_user')
    def test_change_password_rejects_incorrect_current_password(self, get_user, update):
        get_user.return_value = server.User(1, 'alice', server.hash_password('correct-password'))
        response = self.client.post('/api/change_password', json={
            'username': 'alice',
            'currentPassword': 'wrong-password',
            'newPassword': 'new-password-123',
        })
        self.assertEqual(response.status_code, 401)
        update.assert_not_called()

    @patch('server.update_user_password')
    @patch('server.get_user')
    def test_change_password_accepts_correct_current_password(self, get_user, update):
        get_user.return_value = server.User(1, 'alice', server.hash_password('correct-password'))
        response = self.client.post('/api/change_password', json={
            'username': 'alice',
            'currentPassword': 'correct-password',
            'newPassword': 'new-password-123',
        })
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with('alice', 'new-password-123')

    @patch('server.get_cursor', side_effect=RuntimeError('database password leaked'))
    def test_health_hides_database_exception(self, _get_cursor):
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['database'], 'error')
        self.assertNotIn(b'password leaked', response.data)

    def test_rate_limit_rejects_excess_requests(self):
        for _ in range(5):
            self.client.post('/api/register_json', json={})
        response = self.client.post('/api/register_json', json={})
        self.assertEqual(response.status_code, 429)


if __name__ == '__main__':
    unittest.main()
