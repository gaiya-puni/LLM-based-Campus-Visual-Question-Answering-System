"""健康检查、情感数据、注册 / 登录 / 改密。"""

import paths  # noqa: F401
from flask import Blueprint, jsonify, request
from flask_login import login_user
from runtime import facade
from flask_app import rate_limit
from auth_db import is_password_hash, verify_password

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
