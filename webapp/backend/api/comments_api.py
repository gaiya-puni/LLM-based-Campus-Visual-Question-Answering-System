"""校园点位评论：留言、点赞/爱心、举报与管理端复核。

设计要点
--------
1. **读放开、写必须登录**：未登录只能看，发布 / 表态 / 举报一律 401（前端据此引导登录）。
2. **走门面解析数据层**：所有存储调用都经 `runtime.facade`，因此测试可以像既有用例那样
   `patch('server.list_comments')` 整体替换，不依赖 MySQL。
3. **管理端复用既有门禁**：全部管理路由过 `review_guard.review_token_ok()`，
   未在 `.env` 配置 `USERDATA_REVIEW_TOKEN` 时一律 403（与项目"未配置即拒绝"约定一致）。
4. **软失败**：数据层异常统一捕获成 503 软提示，不把数据库报错漏给前端。
"""

import math

import paths  # noqa: F401
from flask import Blueprint, jsonify, request
from flask_login import current_user
from geo_utils import distance_meters
from runtime import facade
from flask_app import rate_limit

import comments_store
from api.review_guard import review_denied, review_token_ok

bp = Blueprint('comments_api', __name__)

#: poi_id 的允许字符集：字母 / 数字 / 下划线 / 连字符（如 plant_3023、canteen_12）
_POI_ID_ALLOWED = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')

#: 坐标反查点位的最大半径：问答地图的打点来自聚类/学院等合成来源，本身不带 POI id，
#: 需要按坐标就近回落到真实点位，才能与植物地图共用同一块留言板。
POI_RESOLVE_RADIUS_M = 60.0

#: 点位库索引（惰性构建一次）：id → {name, category, subCategory, locationName, lng, lat}。
#: 用途一：发布留言时以服务端数据为准，避免客户端伪造展示名；
#: 用途二：坐标反查点位。查不到就回落客户端传来的名称，绝不因数据源不完全重合而拒绝写入。
_poi_index_cache = None


def _poi_index() -> dict:
    global _poi_index_cache
    if _poi_index_cache is None:
        index = {}
        try:
            for poi in getattr(facade, '_CAMPUS_POIS', None) or []:
                if not isinstance(poi, dict):
                    continue
                poi_id = poi.get('id')
                if not poi_id:
                    continue
                index[str(poi_id)] = {
                    'name': str(poi.get('name') or ''),
                    'category': str(poi.get('category') or ''),
                    'subCategory': str(poi.get('subCategory') or ''),
                    'locationName': str(poi.get('locationName') or ''),
                    'lng': float(poi.get('lng') or 0.0),
                    'lat': float(poi.get('lat') or 0.0),
                }
        except Exception:
            index = {}
        _poi_index_cache = index
    return _poi_index_cache


def _resolve_poi(lng: float, lat: float, name: str = ''):
    """坐标反查点位：同名优先，其次取半径内最近的点位；查不到返回 None。"""
    index = _poi_index()
    if not index or not (math.isfinite(lng) and math.isfinite(lat)):
        return None
    name = (name or '').strip()

    pool = [(key, item) for key, item in index.items() if item['name'] == name] if name else []
    if not pool:
        pool = list(index.items())

    best = None
    for item_id, item in pool:
        if not item['lng'] or not item['lat']:
            continue
        distance = distance_meters(lng, lat, item['lng'], item['lat'])
        if distance <= POI_RESOLVE_RADIUS_M and (best is None or distance < best[0]):
            best = (distance, item_id, item)
    if best is None:
        return None
    distance, item_id, item = best
    return {'id': item_id, **item, 'distanceM': round(distance, 1)}


def _viewer_id() -> int:
    """当前登录用户 id；未登录返回 0（0 不是任何真实用户的 id，天然不匹配任何表态）。"""
    try:
        if current_user and current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return 0


def _viewer_name() -> str:
    try:
        if current_user and current_user.is_authenticated:
            return str(current_user.username or '')
    except Exception:
        pass
    return ''


def _need_login():
    return jsonify({'success': False, 'message': '请先登录后再操作', 'needLogin': True}), 401


def _store_failed(message: str):
    return jsonify({'success': False, 'message': message}), 503


def _clean_poi_id(value) -> str:
    """点位标识只允许安全字符集，避免脏数据与日志注入。"""
    text = str(value or '').strip()
    if not text or len(text) > comments_store.MAX_POI_ID_CHARS:
        return ''
    if any(char not in _POI_ID_ALLOWED for char in text):
        return ''
    return text


@bp.route('/api/poi/comments/meta', methods=['GET'])
@rate_limit(60)
def comments_meta():
    """前端一次性拿到限额与举报原因枚举，避免两边各写一份口径。"""
    return jsonify({
        'success': True,
        'limits': {
            'maxChars': comments_store.MAX_COMMENT_CHARS,
            'longMinChars': comments_store.LONG_COMMENT_MIN_CHARS,
            'maxReasonChars': comments_store.MAX_REASON_TEXT_CHARS,
            'reportHideThreshold': comments_store.REPORT_HIDE_THRESHOLD,
            'pageSize': comments_store.DEFAULT_PAGE_SIZE,
            'maxPageSize': comments_store.MAX_PAGE_SIZE,
        },
        'reportReasons': [
            {'value': value, 'label': label} for value, label in comments_store.REPORT_REASONS
        ],
        'reactions': list(comments_store.REACTIONS),
    })


@bp.route('/api/poi/resolve', methods=['GET'])
@rate_limit(240)
def poi_resolve():
    """按坐标（可选名称）反查点位，供问答地图把打点接到留言板上。

    问答结果里的打点包含聚类点、学院等合成来源，本身不带 POI id；
    没有这一层回落，"每个点都能留言"就只覆盖植物地图。
    """
    try:
        lng = float(request.args.get('lng'))
        lat = float(request.args.get('lat'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '坐标无效'}), 400
    if not (math.isfinite(lng) and math.isfinite(lat)):
        return jsonify({'success': False, 'message': '坐标无效'}), 400
    poi = _resolve_poi(lng, lat, request.args.get('name') or '')
    return jsonify({'success': True, 'poi': poi, 'radiusM': POI_RESOLVE_RADIUS_M})


@bp.route('/api/poi/comments/preview', methods=['GET'])
@rate_limit(120)
def comments_preview():
    """弹窗预览：1 条长评，或无长评时 2 条短评。"""
    poi_id = _clean_poi_id(request.args.get('poiId'))
    if not poi_id:
        return jsonify({'success': False, 'message': '缺少或非法的点位标识'}), 400
    try:
        payload = facade.preview_comments(poi_id, _viewer_id())
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    return jsonify({'success': True, **payload})


@bp.route('/api/poi/comments', methods=['GET'])
@rate_limit(120)
def comments_list():
    """按点位分页取评论，附带当前用户的点赞 / 爱心 / 举报状态。"""
    poi_id = _clean_poi_id(request.args.get('poiId'))
    if not poi_id:
        return jsonify({'success': False, 'message': '缺少或非法的点位标识'}), 400
    try:
        payload = facade.list_comments(
            poi_id,
            _viewer_id(),
            sort='new' if request.args.get('sort') == 'new' else 'hot',
            page=request.args.get('page', 1),
            size=request.args.get('size', comments_store.DEFAULT_PAGE_SIZE),
        )
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    return jsonify({'success': True, **payload})


@bp.route('/api/poi/comments', methods=['POST'])
@rate_limit(10)
def comments_create():
    """发布留言（需登录）。"""
    viewer_id = _viewer_id()
    if not viewer_id:
        return _need_login()
    data = request.get_json(silent=True) or {}
    poi_id = _clean_poi_id(data.get('poiId'))
    if not poi_id:
        return jsonify({'success': False, 'message': '缺少或非法的点位标识'}), 400
    content = comments_store.normalize_content(data.get('content'))
    if not content:
        return jsonify({'success': False, 'message': '留言内容不能为空'}), 400
    if len(content) > comments_store.MAX_COMMENT_CHARS:
        return jsonify({
            'success': False,
            'message': f'留言最多 {comments_store.MAX_COMMENT_CHARS} 个字符',
        }), 400
    try:
        if facade.is_user_banned(viewer_id):
            return jsonify({'success': False, 'message': '该账号已被限制发布留言'}), 403
        known = _poi_index().get(poi_id) or {}
        comment = facade.create_comment(
            poi_id,
            known.get('name') or data.get('poiName') or '',
            known.get('category') or data.get('poiCategory') or '',
            viewer_id,
            _viewer_name(),
            content,
        )
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    if not comment:
        return jsonify({'success': False, 'message': '留言内容不合法'}), 400
    return jsonify({'success': True, 'comment': comment})


@bp.route('/api/poi/comments/<int:comment_id>', methods=['DELETE'])
@rate_limit(20)
def comments_delete(comment_id: int):
    """删除自己的留言（需登录）。"""
    viewer_id = _viewer_id()
    if not viewer_id:
        return _need_login()
    try:
        removed = facade.delete_comment(comment_id, viewer_id, False)
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    if not removed:
        return jsonify({'success': False, 'message': '留言不存在或无权删除'}), 404
    return jsonify({'success': True})


@bp.route('/api/poi/comments/<int:comment_id>/reaction', methods=['POST'])
@rate_limit(60)
def comments_react(comment_id: int):
    """点赞 / 爱心（两者独立，可同时存在；重复点击同一项即取消）。"""
    viewer_id = _viewer_id()
    if not viewer_id:
        return _need_login()
    data = request.get_json(silent=True) or {}
    reaction = str(data.get('type') or '').strip().lower()
    action = str(data.get('action') or 'add').strip().lower()
    if reaction not in comments_store.REACTIONS:
        return jsonify({'success': False, 'message': '表态类型无效'}), 400
    if action not in ('add', 'remove'):
        return jsonify({'success': False, 'message': '表态动作无效'}), 400
    try:
        result = facade.react(comment_id, viewer_id, reaction, action)
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    if not result:
        return jsonify({'success': False, 'message': '留言不存在'}), 404
    return jsonify({'success': True, **result})


@bp.route('/api/poi/comments/<int:comment_id>/report', methods=['POST'])
@rate_limit(10)
def comments_report(comment_id: int):
    """举报一条留言；同一用户只能举报一次，达阈值自动折叠。"""
    viewer_id = _viewer_id()
    if not viewer_id:
        return _need_login()
    data = request.get_json(silent=True) or {}
    reason_code = str(data.get('reasonCode') or '').strip().lower()
    if reason_code not in comments_store.REPORT_REASON_CODES:
        return jsonify({'success': False, 'message': '请选择举报原因'}), 400
    try:
        result = facade.report_comment(
            comment_id, viewer_id, reason_code, data.get('reasonText') or '')
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    if not result.get('ok'):
        if result.get('reason') == 'missing':
            return jsonify({'success': False, 'message': '留言不存在'}), 404
        if result.get('reason') == 'self':
            return jsonify({'success': False, 'message': '不能举报自己的留言'}), 400
        return jsonify({'success': False, 'message': '举报失败，请稍后再试'}), 400
    duplicate = result.get('reason') == 'duplicate'
    message = '你已举报过该留言' if duplicate else (
        '举报已提交，该留言已进入人工复核' if result.get('hidden') else '举报已提交，感谢反馈')
    return jsonify({
        'success': True,
        'message': message,
        'duplicate': duplicate,
        'reportCount': result.get('reportCount', 0),
        'hidden': bool(result.get('hidden')),
    })


# ---------------------------------------------------------------------------
# 管理端（需 X-Review-Token）
# ---------------------------------------------------------------------------


@bp.route('/api/poi/comments/manage', methods=['GET'])
@rate_limit(60)
def comments_manage():
    """管理端列表：默认看被举报但未处理的评论。"""
    if not review_token_ok():
        return review_denied()
    status_filter = str(request.args.get('status') or 'reported').strip().lower()
    if status_filter not in comments_store.MANAGE_FILTERS:
        status_filter = 'reported'
    try:
        items = facade.list_comments_for_management(status_filter)
        summary = facade.comment_admin_summary()
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    return jsonify({
        'success': True,
        'status': status_filter,
        'items': items,
        'summary': summary,
        'thresholds': {'reportHideThreshold': comments_store.REPORT_HIDE_THRESHOLD},
    })


@bp.route('/api/poi/comments/<int:comment_id>/moderate', methods=['POST'])
@rate_limit(30)
def comments_moderate(comment_id: int):
    """管理员处置：恢复 / 删除 / 封禁作者。"""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '').strip().lower()
    if action not in comments_store.MODERATION_ACTIONS:
        return jsonify({'success': False, 'message': '处置动作无效'}), 400
    try:
        result = facade.moderate_comment(comment_id, action, data.get('note') or '')
    except comments_store.CommentStoreError as error:
        return _store_failed(str(error))
    if not result:
        return jsonify({'success': False, 'message': '留言不存在'}), 404
    labels = {
        comments_store.MODERATION_RESTORE: '已恢复展示',
        comments_store.MODERATION_DELETE: '已删除该留言',
        comments_store.MODERATION_BAN_AUTHOR: '已封禁该留言作者',
    }
    return jsonify({'success': True, 'message': labels[action], **result})
