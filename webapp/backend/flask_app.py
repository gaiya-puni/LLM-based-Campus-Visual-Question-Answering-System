"""Flask 应用对象、全局配置、CORS、限流与通用错误处理。

这里只放"与业务无关"的框架级配置；所有路由都在 `api/` 包内各自注册自己的蓝图。
"""

import os
import threading
from collections import defaultdict, deque
from functools import wraps
from time import monotonic

import paths  # noqa: F401  必须最先导入：完成 sys.path 引导与 .env 载入
from flask import Flask, jsonify, request
from flask_cors import CORS

#: 单张图片上限（见 api/chat_api.py 的 MAX_ATTACHMENT_BYTES）与单轮张数上限。
#: 请求体上限必须能容纳 2 张上限图（base64 约 4/3 膨胀）再留一点 JSON 余量，
#: 否则图片会在进入业务逻辑之前就被 Flask 以 413 拒绝。
ATTACHMENTS_BODY_BUDGET = 12 * 1024 * 1024

app = Flask(__name__)
app.config.update(
    # 缺省时随机生成（重启即失效）。生产部署请显式配置 FLASK_SECRET_KEY。
    SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or os.urandom(32),
    MAX_CONTENT_LENGTH=int(os.getenv('MAX_CONTENT_LENGTH', str(ATTACHMENTS_BODY_BUDGET))),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
)

allowed_origins = [
    origin.strip() for origin in os.getenv('ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]
if allowed_origins:
    CORS(
        app,
        resources={r'/api/*': {'origins': allowed_origins}},
        supports_credentials=True,
    )


_rate_limit_buckets = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def rate_limit(max_requests: int, window_seconds: int = 60):
    """Small per-process limiter for JSON endpoints."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            now = monotonic()
            key = (request.remote_addr or 'unknown', request.endpoint)
            with _rate_limit_lock:
                bucket = _rate_limit_buckets[key]
                while bucket and bucket[0] <= now - window_seconds:
                    bucket.popleft()
                if len(bucket) >= max_requests:
                    return jsonify({
                        'success': False,
                        'message': '请求过于频繁，请稍后重试',
                    }), 429
                bucket.append(now)
            return view(*args, **kwargs)
        return wrapped
    return decorate


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # camera/microphone 保持禁用：移动端拍照走 `<input type="file" capture>` 的原生文件选择，
    # 不需要浏览器摄像头权限，因此不给页面开放 getUserMedia。
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(self)')
    return response


@app.errorhandler(413)
def request_body_too_large(_error):
    """请求体超限时返回 JSON（默认的 HTML 错误页会让前端拿到无法解析的响应）。"""
    limit_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
    return jsonify({
        'success': False,
        'message': f'请求体超过 {limit_mb}MB 上限：图片请压缩后重试（单张不超过 4MB，最多 2 张）',
    }), 413


def warn_if_body_limit_too_small():
    """启动自检：上限低于附件预算时明确提示，避免图片链路"看起来能用、一发就 413"。"""
    limit = int(app.config['MAX_CONTENT_LENGTH'])
    if limit < ATTACHMENTS_BODY_BUDGET:
        print(f'提示：MAX_CONTENT_LENGTH={limit} 小于图片上传所需预算 '
              f'{ATTACHMENTS_BODY_BUDGET}；上传较大图片会返回 413。'
              f'可在 .env 中调大该值（或删掉该行使用新默认值）。')
