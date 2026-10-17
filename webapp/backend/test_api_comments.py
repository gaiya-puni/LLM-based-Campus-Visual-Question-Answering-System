"""评论类路由与评论数据层纯函数的直测。

约定（与 `test_api_auth.py` 一致）：
- 请求走 `server.app.test_client()`，不启动服务、不连 MySQL；
- 数据层调用一律用 `patch('server.<name>')` 整体替换（门面设计的直接受益点）；
- 每个用例前清空限流桶，避免 10～120 次/分钟的阈值干扰断言。

登录态的模拟：`flask_login` 的 `user_loader` 会在会话里回查数据库，CI 里没有 MySQL，
因此这里直接替换 `api.comments_api` 模块内的 `current_user` 绑定（该模块
`from flask_login import current_user`，是普通模块属性，可 patch）。
"""

import unittest
from pathlib import Path
from unittest.mock import patch

import server
import comments_store


class _FakeUser:
    """最小可用的已登录用户替身。"""

    def __init__(self, user_id=1, username='alice'):
        self.id = user_id
        self.username = username
        self.is_authenticated = True


class _Anonymous:
    """flask_login 未登录时的等价物。"""

    is_authenticated = False
    id = None
    username = ''


class _CurrentUserPatch:
    """同时替换评论蓝图与鉴权蓝图里的 `current_user` 绑定。

    两个模块都是 `from flask_login import current_user`，各自持有独立引用，
    只 patch 一个会让另一个漏掉（例如 `/api/session` 仍报未登录）。
    """

    def __init__(self, value):
        self._patches = (
            patch('api.comments_api.current_user', value),
            patch('api.auth_api.current_user', value),
        )

    def __enter__(self):
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *_exc):
        for item in self._patches:
            item.stop()
        return False


class _CommentsRouteTestCase(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        server._rate_limit_buckets.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        server._rate_limit_buckets.clear()

    @staticmethod
    def as_user(user_id=1, username='alice'):
        return _CurrentUserPatch(_FakeUser(user_id, username))

    @staticmethod
    def as_guest():
        return _CurrentUserPatch(_Anonymous())


# ---------------------------------------------------------------------------
# 纯函数（无 IO）
# ---------------------------------------------------------------------------


class CommentHelperTests(unittest.TestCase):
    def test_content_is_stripped_and_whitespace_collapsed(self):
        self.assertEqual(comments_store.normalize_content('  好 看\n的  地方 '), '好 看 的 地方')
        self.assertEqual(comments_store.normalize_content(None), '')

    def test_long_comment_threshold_is_inclusive(self):
        limit = comments_store.LONG_COMMENT_MIN_CHARS

        self.assertFalse(comments_store.is_long_comment('好' * (limit - 1)))
        self.assertTrue(comments_store.is_long_comment('好' * limit))
        # 首尾空白不计入字数，避免"多敲几个空格就成了长评"
        self.assertFalse(comments_store.is_long_comment(' ' * 20 + '好' * (limit - 1)))

    def test_preview_prefers_one_long_comment(self):
        long_item = {'id': 1, 'isLong': True}
        item_list, mode = comments_store.pick_preview(long_item, [{'id': 2}, {'id': 3}])

        self.assertEqual(mode, 'long')
        self.assertEqual(item_list, [long_item])

    def test_preview_falls_back_to_two_short_comments(self):
        shorts = [{'id': 2}, {'id': 3}, {'id': 4}]
        item_list, mode = comments_store.pick_preview(None, shorts)

        self.assertEqual(mode, 'short')
        self.assertEqual([item['id'] for item in item_list], [2, 3])

    def test_preview_is_empty_without_any_comment(self):
        item_list, mode = comments_store.pick_preview(None, [])

        self.assertEqual(mode, 'empty')
        self.assertEqual(item_list, [])

    def test_page_clamping_guards_against_bad_input(self):
        self.assertEqual(comments_store.clamp_page('0', '999'),
                         (1, comments_store.MAX_PAGE_SIZE))
        self.assertEqual(comments_store.clamp_page(None, 'abc'), (1, comments_store.DEFAULT_PAGE_SIZE))
        self.assertEqual(comments_store.clamp_page('-3', '5'), (1, 5))

    def test_text_is_truncated_to_the_column_width(self):
        self.assertEqual(len(comments_store.clamp_text('好' * 200, 20)), 20)

    def test_manage_filters_cover_the_documented_statuses(self):
        self.assertEqual(set(comments_store.MANAGE_FILTERS),
                         {'reported', 'hidden', 'deleted', 'all'})
        self.assertIn('report_count > 0', ' '.join(comments_store.MANAGE_FILTERS['reported']))


class CommentQuerySqlTests(unittest.TestCase):
    """列表 SQL 的拼接约束。

    这些断言针对一个真实踩过的坑：SELECT 列表少一个逗号会把
    `c.created_at` 与 `rl.user_id IS NOT NULL` 粘成 `c.created_atrl.user_id`，
    只有连上 MySQL 才会报 1054。纯拼接层单测能在 CI（无数据库）就拦住它。
    """

    def test_placeholders_match_parameters_for_every_variation(self):
        variations = [
            dict(include_hidden=False, sort='hot', only_long=None, own_hidden=True),
            dict(include_hidden=False, sort='new', only_long=True, own_hidden=False),
            dict(include_hidden=False, sort='hot', only_long=False, own_hidden=False),
            dict(include_hidden=True, sort='hot', only_long=None, own_hidden=True),
            dict(include_hidden=True, sort='new', only_long=False, own_hidden=True),
        ]
        for options in variations:
            with self.subTest(options=options):
                sql, params = comments_store.build_list_query(
                    'plant_3023', 7, limit=10, offset=20, **options)
                self.assertEqual(sql.count('%s'), len(params))

    def test_select_list_is_comma_separated(self):
        sql, _ = comments_store.build_list_query('plant_3023', 0, False, 'hot', 5, 0)

        self.assertIn('c.created_at, rl.user_id IS NOT NULL', sql)

    def test_every_main_column_is_selected_with_the_table_alias(self):
        sql, _ = comments_store.build_list_query('plant_3023', 0, False, 'hot', 5, 0)

        for column in comments_store._COMMENT_COLUMNS:
            with self.subTest(column=column):
                self.assertIn(f'c.{column}', sql)

    def test_only_long_filter_switches_the_predicate(self):
        long_sql, _ = comments_store.build_list_query(
            'plant_3023', 0, False, 'hot', 1, 0, only_long=True)
        short_sql, _ = comments_store.build_list_query(
            'plant_3023', 0, False, 'hot', 1, 0, only_long=False)

        self.assertIn('c.is_long = 1', long_sql)
        self.assertIn('c.is_long = 0', short_sql)

    def test_hot_sort_orders_by_the_two_counters(self):
        sql, _ = comments_store.build_list_query('plant_3023', 0, False, 'hot', 5, 0)

        self.assertIn('(c.like_count + c.love_count) DESC', sql)

    def test_public_viewer_sees_only_visible_comments(self):
        sql, params = comments_store.build_list_query(
            'plant_3023', 9, False, 'hot', 5, 0, own_hidden=False)

        self.assertIn("c.status = 'visible'", sql)
        # 三个 JOIN 参数 + poi_id + limit/offset；不加自己的折叠留言条件
        self.assertEqual(len(params), 6)


# ---------------------------------------------------------------------------
# 元信息与登录态
# ---------------------------------------------------------------------------


class CommentMetaRouteTests(_CommentsRouteTestCase):
    def test_meta_exposes_limits_and_report_reasons(self):
        payload = self.client.get('/api/poi/comments/meta').get_json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['limits']['maxChars'], comments_store.MAX_COMMENT_CHARS)
        self.assertEqual(payload['limits']['longMinChars'], comments_store.LONG_COMMENT_MIN_CHARS)
        self.assertEqual(payload['limits']['maxReasonChars'],
                         comments_store.MAX_REASON_TEXT_CHARS)
        self.assertEqual(payload['limits']['reportHideThreshold'],
                         comments_store.REPORT_HIDE_THRESHOLD)
        self.assertEqual([item['value'] for item in payload['reportReasons']],
                         list(comments_store.REPORT_REASON_CODES))

    def test_session_reports_guest(self):
        with self.as_guest():
            payload = self.client.get('/api/session').get_json()

        self.assertTrue(payload['success'])
        self.assertFalse(payload['loggedIn'])
        self.assertEqual(payload['username'], '')

    def test_session_reports_logged_in_user(self):
        with self.as_user(7, 'bob'):
            payload = self.client.get('/api/session').get_json()

        self.assertTrue(payload['loggedIn'])
        self.assertEqual(payload['username'], 'bob')


class PoiResolveRouteTests(_CommentsRouteTestCase):
    """问答地图的打点不带 POI id，需要按坐标回落到真实点位。"""

    def test_invalid_coordinates_are_rejected(self):
        for query in ('', '?lng=abc&lat=1', '?lng=nan&lat=1', '?lng=1&lat=inf'):
            with self.subTest(query=query):
                response = self.client.get(f'/api/poi/resolve{query}')
                self.assertEqual(response.status_code, 400)

    def test_known_plant_is_resolved_from_its_own_coordinates(self):
        poi = next(item for item in server._CAMPUS_POIS if item['id'] == 'plant_3023')

        payload = self.client.get(
            f"/api/poi/resolve?lng={poi['lng']}&lat={poi['lat']}&name={poi['name']}").get_json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['poi']['id'], 'plant_3023')
        self.assertEqual(payload['poi']['name'], poi['name'])

    def test_far_away_coordinates_resolve_to_nothing(self):
        payload = self.client.get('/api/poi/resolve?lng=0&lat=0').get_json()

        self.assertTrue(payload['success'])
        self.assertIsNone(payload['poi'])


# ---------------------------------------------------------------------------
# 读接口
# ---------------------------------------------------------------------------


class CommentReadRouteTests(_CommentsRouteTestCase):
    def test_preview_requires_a_poi_id(self):
        for query in ('', '?poiId=', '?poiId=bad id!'):
            with self.subTest(query=query):
                response = self.client.get(f'/api/poi/comments/preview{query}')
                self.assertEqual(response.status_code, 400)

    def test_preview_passes_viewer_identity_for_flag_highlighting(self):
        with self.as_user(9), \
                patch('server.preview_comments', return_value={'mode': 'empty', 'items': []}) as store:
            response = self.client.get('/api/poi/comments/preview?poiId=plant_3023')

        self.assertEqual(response.status_code, 200)
        store.assert_called_once_with('plant_3023', 9)

    def test_preview_surfaces_how_many_of_my_own_comments_are_folded(self):
        """前端靠这个字段决定"查看全部"入口是否出现：没有它，作者自己
        被折叠的留言在弹窗里既看不到也进不去。"""
        payload = {
            'mode': 'long', 'items': [], 'myHidden': 2,
            'stats': {'total': 1, 'long': 1, 'short': 0, 'like': 0, 'love': 0},
        }
        with self.as_user(9), patch('server.preview_comments', return_value=payload):
            body = self.client.get('/api/poi/comments/preview?poiId=plant_3023').get_json()

        self.assertEqual(body['myHidden'], 2)

    def test_list_is_readable_without_login(self):
        payload = {'items': [], 'total': 0, 'page': 1, 'size': 20, 'hasMore': False}
        with self.as_guest(), patch('server.list_comments', return_value=payload) as store:
            response = self.client.get('/api/poi/comments?poiId=plant_3023&sort=new&page=2&size=5')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        store.assert_called_once_with('plant_3023', 0, sort='new', page='2', size='5')

    def test_unknown_sort_falls_back_to_hot(self):
        with self.as_guest(), patch('server.list_comments', return_value={'items': []}) as store:
            self.client.get('/api/poi/comments?poiId=plant_3023&sort=weird')

        self.assertEqual(store.call_args.kwargs['sort'], 'hot')

    def test_store_failure_degrades_to_503(self):
        with self.as_guest(), patch('server.preview_comments',
                                    side_effect=comments_store.CommentStoreError('读取留言预览失败')):
            response = self.client.get('/api/poi/comments/preview?poiId=plant_3023')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['message'], '读取留言预览失败')


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


class CommentCreateRouteTests(_CommentsRouteTestCase):
    def test_guest_cannot_post(self):
        with self.as_guest(), patch('server.create_comment') as store:
            response = self.client.post(
                '/api/poi/comments', json={'poiId': 'plant_3023', 'content': '好看'})

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()['needLogin'])
        store.assert_not_called()

    def test_empty_content_is_rejected(self):
        with self.as_user(), patch('server.create_comment') as store:
            response = self.client.post(
                '/api/poi/comments', json={'poiId': 'plant_3023', 'content': '   '})

        self.assertEqual(response.status_code, 400)
        store.assert_not_called()

    def test_overlong_content_is_rejected_before_writing(self):
        with self.as_user(), patch('server.create_comment') as store:
            response = self.client.post('/api/poi/comments', json={
                'poiId': 'plant_3023',
                'content': '好' * (comments_store.MAX_COMMENT_CHARS + 1),
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn(str(comments_store.MAX_COMMENT_CHARS), response.get_json()['message'])
        store.assert_not_called()

    def test_banned_author_is_blocked(self):
        with self.as_user(), \
                patch('server.is_user_banned', return_value=True), \
                patch('server.create_comment') as store:
            response = self.client.post(
                '/api/poi/comments', json={'poiId': 'plant_3023', 'content': '好看'})

        self.assertEqual(response.status_code, 403)
        store.assert_not_called()

    def test_store_rejects_illegal_content(self):
        with self.as_user(), \
                patch('server.is_user_banned', return_value=False), \
                patch('server.create_comment', return_value=None):
            response = self.client.post(
                '/api/poi/comments', json={'poiId': 'plant_3023', 'content': '好看'})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_successful_post_uses_the_catalog_name_when_available(self):
        created = {'id': 11, 'content': '好看', 'isLong': False}
        with self.as_user(3, 'carol'), \
                patch('server.is_user_banned', return_value=False), \
                patch('server.create_comment', return_value=created) as store:
            response = self.client.post('/api/poi/comments', json={
                'poiId': 'plant_3023', 'content': ' 好看 ', 'poiName': '伪造名', 'poiCategory': 'x'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['comment'], created)
        poi_id, poi_name, poi_category, user_id, username, content = store.call_args.args
        self.assertEqual((poi_id, user_id, username), ('plant_3023', 3, 'carol'))
        self.assertEqual(content, '好看')
        # 点位库里有 plant_3023 时以服务端为准，客户端传的名字不可信
        self.assertNotEqual(poi_name, '伪造名')
        self.assertEqual(poi_category, 'plant')


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


class CommentDeleteRouteTests(_CommentsRouteTestCase):
    def test_guest_cannot_delete(self):
        with self.as_guest(), patch('server.delete_comment') as store:
            response = self.client.delete('/api/poi/comments/5')

        self.assertEqual(response.status_code, 401)
        store.assert_not_called()

    def test_missing_or_not_owned_comment_returns_404(self):
        with self.as_user(), patch('server.delete_comment', return_value=False):
            response = self.client.delete('/api/poi/comments/5')

        self.assertEqual(response.status_code, 404)

    def test_own_comment_is_soft_deleted(self):
        with self.as_user(4), patch('server.delete_comment', return_value=True) as store:
            response = self.client.delete('/api/poi/comments/5')

        self.assertEqual(response.status_code, 200)
        store.assert_called_once_with(5, 4, False)


# ---------------------------------------------------------------------------
# 表态：点赞 / 爱心
# ---------------------------------------------------------------------------


class CommentReactionRouteTests(_CommentsRouteTestCase):
    def test_guest_cannot_react(self):
        with self.as_guest(), patch('server.react') as store:
            response = self.client.post(
                '/api/poi/comments/5/reaction', json={'type': 'like', 'action': 'add'})

        self.assertEqual(response.status_code, 401)
        store.assert_not_called()

    def test_invalid_reaction_or_action_is_rejected(self):
        cases = (
            {'type': 'star', 'action': 'add'},
            {'type': 'like', 'action': 'toggle'},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.as_user(), patch('server.react') as store:
                response = self.client.post('/api/poi/comments/5/reaction', json=payload)
                self.assertEqual(response.status_code, 400)
                store.assert_not_called()

    def test_like_and_love_are_independent_reactions(self):
        """点赞与爱心各自计数：两者可以同时存在。"""
        result = {'id': 5, 'likeCount': 3, 'loveCount': 1, 'isLiked': True, 'isLoved': True}
        with self.as_user(6), patch('server.react', return_value=result) as store:
            response = self.client.post(
                '/api/poi/comments/5/reaction', json={'type': 'love', 'action': 'add'})

        self.assertEqual(response.status_code, 200)
        store.assert_called_once_with(5, 6, 'love', 'add')
        self.assertTrue(response.get_json()['isLoved'])

    def test_reaction_on_missing_comment_returns_404(self):
        with self.as_user(), patch('server.react', return_value=None):
            response = self.client.post(
                '/api/poi/comments/5/reaction', json={'type': 'like', 'action': 'add'})

        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 举报
# ---------------------------------------------------------------------------


class CommentReportRouteTests(_CommentsRouteTestCase):
    def test_guest_cannot_report(self):
        with self.as_guest(), patch('server.report_comment') as store:
            response = self.client.post(
                '/api/poi/comments/5/report', json={'reasonCode': 'spam'})

        self.assertEqual(response.status_code, 401)
        store.assert_not_called()

    def test_reason_code_is_required_and_validated(self):
        for payload in ({}, {'reasonCode': 'not-a-reason'}):
            with self.subTest(payload=payload), self.as_user(), patch('server.report_comment') as store:
                response = self.client.post('/api/poi/comments/5/report', json=payload)
                self.assertEqual(response.status_code, 400)
                store.assert_not_called()

    def test_cannot_report_own_comment(self):
        with self.as_user(), patch('server.report_comment',
                                   return_value={'ok': False, 'reason': 'self'}):
            response = self.client.post(
                '/api/poi/comments/5/report', json={'reasonCode': 'spam'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('自己', response.get_json()['message'])

    def test_missing_comment_returns_404(self):
        with self.as_user(), patch('server.report_comment',
                                   return_value={'ok': False, 'reason': 'missing'}):
            response = self.client.post(
                '/api/poi/comments/5/report', json={'reasonCode': 'spam'})

        self.assertEqual(response.status_code, 404)

    def test_duplicate_report_is_reported_as_such(self):
        with self.as_user(), patch('server.report_comment', return_value={
                'ok': True, 'reason': 'duplicate', 'reportCount': 2, 'hidden': False}):
            payload = self.client.post(
                '/api/poi/comments/5/report', json={'reasonCode': 'spam'}).get_json()

        self.assertTrue(payload['success'])
        self.assertTrue(payload['duplicate'])
        self.assertIn('已举报过', payload['message'])

    def test_report_hitting_the_threshold_tells_the_user_it_is_folded(self):
        with self.as_user(), patch('server.report_comment', return_value={
                'ok': True, 'reason': '', 'reportCount': comments_store.REPORT_HIDE_THRESHOLD,
                'hidden': True}) as store:
            payload = self.client.post(
                '/api/poi/comments/5/report',
                json={'reasonCode': 'abuse', 'reasonText': '辱骂'}).get_json()

        self.assertTrue(payload['hidden'])
        self.assertIn('人工复核', payload['message'])
        store.assert_called_once_with(5, 1, 'abuse', '辱骂')


# ---------------------------------------------------------------------------
# 管理端
# ---------------------------------------------------------------------------


class CommentManageRouteTests(_CommentsRouteTestCase):
    """管理端一律要 `X-Review-Token`；未配置 `.env` 即 403（未配置即拒绝）。"""

    def setUp(self):
        super().setUp()
        self.env = patch.dict('os.environ', {'USERDATA_REVIEW_TOKEN': 'unit-test-review-token'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_manage_list_without_token_is_forbidden(self):
        response = self.client.get('/api/poi/comments/manage')

        self.assertEqual(response.status_code, 403)

    def test_manage_list_returns_items_and_summary(self):
        summary = {'total': 4, 'hidden': 1, 'deleted': 1, 'reported': 2, 'bannedUsers': 0}
        with patch('server.list_comments_for_management', return_value=[{'id': 5}]) as items, \
                patch('server.comment_admin_summary', return_value=summary):
            response = self.client.get(
                '/api/poi/comments/manage?status=hidden',
                headers={'X-Review-Token': 'unit-test-review-token'})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'hidden')
        self.assertEqual(payload['summary'], summary)
        items.assert_called_once_with('hidden')

    def test_unknown_manage_filter_falls_back_to_reported(self):
        with patch('server.list_comments_for_management', return_value=[]) as items, \
                patch('server.comment_admin_summary',
                      return_value={'total': 0, 'hidden': 0, 'deleted': 0,
                                    'reported': 0, 'bannedUsers': 0}):
            self.client.get('/api/poi/comments/manage?status=nonsense',
                            headers={'X-Review-Token': 'unit-test-review-token'})

        items.assert_called_once_with('reported')

    def test_moderate_requires_token(self):
        with patch('server.moderate_comment') as store:
            response = self.client.post(
                '/api/poi/comments/5/moderate', json={'action': 'restore'})

        self.assertEqual(response.status_code, 403)
        store.assert_not_called()

    def test_moderate_rejects_unknown_action(self):
        with patch('server.moderate_comment') as store:
            response = self.client.post(
                '/api/poi/comments/5/moderate', json={'action': 'explode'},
                headers={'X-Review-Token': 'unit-test-review-token'})

        self.assertEqual(response.status_code, 400)
        store.assert_not_called()

    def test_moderate_restore_clears_the_folded_state(self):
        with patch('server.moderate_comment', return_value={
                'id': 5, 'action': 'restore', 'authorId': 2, 'author': 'bob'}) as store:
            payload = self.client.post(
                '/api/poi/comments/5/moderate',
                json={'action': 'restore', 'note': '误判'},
                headers={'X-Review-Token': 'unit-test-review-token'}).get_json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['message'], '已恢复展示')
        store.assert_called_once_with(5, 'restore', '误判')

    def test_moderate_ban_author_is_labelled(self):
        with patch('server.moderate_comment', return_value={
                'id': 5, 'action': 'ban_author', 'authorId': 2, 'author': 'bob'}):
            payload = self.client.post(
                '/api/poi/comments/5/moderate', json={'action': 'ban_author'},
                headers={'X-Review-Token': 'unit-test-review-token'}).get_json()

        self.assertEqual(payload['message'], '已封禁该留言作者')

    def test_moderate_missing_comment_returns_404(self):
        with patch('server.moderate_comment', return_value=None):
            response = self.client.post(
                '/api/poi/comments/5/moderate', json={'action': 'delete'},
                headers={'X-Review-Token': 'unit-test-review-token'})

        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 审核态开关（只回布尔，供前端决定入口显隐）
# ---------------------------------------------------------------------------


class ReviewStatusRouteTests(_CommentsRouteTestCase):
    """`/api/review-status` 与 `review_guard.review_configured()` 共用同一口径。"""

    def _status(self, token):
        with patch.dict('os.environ', {'USERDATA_REVIEW_TOKEN': token}):
            return self.client.get('/api/review-status').get_json()

    def test_configured_when_token_is_long_enough(self):
        payload = self._status('unit-test-review-token')

        self.assertTrue(payload['success'])
        self.assertTrue(payload['configured'])

    def test_not_configured_when_token_is_missing(self):
        self.assertFalse(self._status('')['configured'])

    def test_not_configured_when_token_is_too_short(self):
        self.assertFalse(self._status('short-token')['configured'])

    def test_status_never_leaks_the_token(self):
        payload = self._status('unit-test-review-token')

        self.assertNotIn('unit-test-review-token', str(payload))


# ---------------------------------------------------------------------------
# 表结构自检
# ---------------------------------------------------------------------------


class CommentSchemaTests(unittest.TestCase):
    def test_ddl_covers_every_table_the_store_uses(self):
        """`poi_comments.sql` 与代码内置的 DDL 必须同步，否则手工建表会漏表。"""
        sql_file = Path(comments_store.__file__).with_name('poi_comments.sql')
        ddl = sql_file.read_text(encoding='utf-8')
        joined = '\n'.join(comments_store._DDL_STATEMENTS)

        for table in ('poi_comments', 'comment_reactions', 'comment_reports',
                      'comment_moderation_log', 'comment_banned_users'):
            with self.subTest(table=table):
                self.assertIn(f'`{table}`', ddl)
                self.assertIn(table, joined)

    def test_reaction_and_report_tables_enforce_uniqueness(self):
        joined = '\n'.join(comments_store._DDL_STATEMENTS)

        self.assertIn('uniq_comment_user_reaction', joined)
        self.assertIn('uniq_comment_user`', joined)


if __name__ == '__main__':
    unittest.main()
