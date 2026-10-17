"""健康检查、情感数据、注册 / 登录 / 改密。"""

import paths  # noqa: F401
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_user
from runtime import facade
from flask_app import rate_limit
from auth_db import is_password_hash, verify_password
from api.review_guard import review_configured

bp = Blueprint('auth_api', __name__)

@bp.route("/api/health", methods=["GET"])
def health():
    try:
        c = facade.get_cursor()
        c.execute('SELECT 1')
        database = 'ok'
        status_code = 200
    except Exception:
        database = 'error'
        status_code = 503
    # vision 一并暴露：前端据此决定"图片上传入口"是可点还是带禁用提示，
    # 避免用户传了图才发现本部署没配视觉模型。
    return jsonify({
        'status': 'ok',
        'database': database,
        'vision': facade.vision_available(),
    }), status_code


@bp.route("/api/emotions", methods=["GET"])
@rate_limit(60)
def emotions():
    return jsonify(facade._EMOTIONS)


@bp.route("/api/session", methods=["GET"])
@rate_limit(120)
def session_state():
    """当前登录态。

    会话 Cookie 是 HttpOnly 的，前端读不到，因此需要一个显式接口告诉它
    "现在是不是登录了、登录的是谁"——评论区的"未登录只能看"引导依赖这个字段。
    """
    try:
        authenticated = bool(current_user and current_user.is_authenticated)
        username = str(current_user.username) if authenticated else ''
    except Exception:
        authenticated, username = False, ''
    return jsonify({'success': True, 'loggedIn': authenticated, 'username': username})


@bp.route("/api/review-status", methods=["GET"])
@rate_limit(120)
def review_status():
    """审核态是否已开启（只回布尔值，绝不回密钥本身）。

    前端用它决定「评论审核」入口显不显示：`.env` 没配 `USERDATA_REVIEW_TOKEN`
    时该页对所有接口都是 403，入口留着只会误导人。真正的门禁仍是
    请求头 `X-Review-Token`，这里只负责"看得见 / 看不见"这一层体验。
    """
    return jsonify({'success': True, 'configured': review_configured()})


@bp.route("/api/emotion-plants", methods=["GET"])
@rate_limit(60)
def emotion_plants():
    """寄语 id → 对应植物的精简信息（植物名 / 科属 / 图标 / 位置 / 院系）。

    单独成接口而不是塞进 /api/emotions：后者被测试与 build_emotion_context 当作
    纯留言数据使用，返回值契约不宜改变。
    """
    return jsonify(facade._EMOTION_PLANTS)


@bp.route("/api/plant-stats", methods=["GET"])
@rate_limit(60)
def plant_stats():
    """校园植物总览：{species: 植物种类数, trees: 已登记树木数}。

    与 emotion_plants 分开：那个接口的返回值是按寄语 id 索引的映射，
    往里塞总数会破坏其契约。
    """
    return jsonify(facade._PLANT_STATS)


@bp.route("/api/register_json", methods=["POST"])
@rate_limit(5)
def register_json():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    if len(username) > 20 or len(password) < 8 or len(password) > 128:
        return jsonify({'success': False, 'message': '用户名最多20个字符，密码需为8至128个字符'}), 400
    if facade.get_user(username):
        return jsonify({'success': False, 'message': '用户名已存在'}), 400
    facade.create_user(username, password)
    return jsonify({'success': True})


@bp.route("/api/login_json", methods=["POST"])
@rate_limit(10)
def login_json():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    user = facade.get_user(username)
    if user and verify_password(user.password, password):
        if not is_password_hash(user.password):
            facade.update_user_password(username, password)
        login_user(user)
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '用户名或密码有误'}), 401


@bp.route("/api/change_password", methods=["POST"])
@rate_limit(5)
def change_password():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    current_password = str(data.get('currentPassword', ''))
    new_password = str(data.get('newPassword', ''))
    if not username or not current_password or not new_password:
        return jsonify({'success': False, 'message': '用户名、当前密码和新密码不能为空'}), 400
    if len(new_password) < 8 or len(new_password) > 128:
        return jsonify({'success': False, 'message': '新密码需为8至128个字符'}), 400
    user = facade.get_user(username)
    if not user or not verify_password(user.password, current_password):
        return jsonify({'success': False, 'message': '用户名或当前密码错误'}), 401
    facade.update_user_password(username, new_password)
    return jsonify({'success': True})
