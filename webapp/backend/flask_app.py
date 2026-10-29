"""Flask 应用对象、全局配置、CORS、限流与通用错误处理。

这里只放"与业务无关"的框架级配置；所有路由都在 `api/` 包内各自注册自己的蓝图。
"""

import os
import threading
from collections import defaultdict, deque
from datetime import date
from functools import wraps
from time import monotonic

import paths  # noqa: F401  必须最先导入：完成 sys.path 引导与 .env 载入
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

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


def _env_int(name: str, default: int) -> int:
    """读整数配置；非法值回落默认。0 是合法值（表示"不启用"）。"""
    raw = (os.getenv(name) or '').strip()
    return int(raw) if raw.isdigit() else default


# ---------------------------------------------------------------------------
# 反向代理：线上部署必需，且必须显式声明信任层数
# ---------------------------------------------------------------------------
# 线上形态是 nginx 终结 TLS 再转发给 Flask（`Server: nginx/1.18.0`）。此时
# `request.remote_addr` 拿到的是 **nginx 的地址**（与 Flask 同机就是 127.0.0.1），
# 而不是真实用户 IP。
#
# 后果不是"限流稍微不准"，而是**全站共用一个桶**：`/api/chat` 的 30 次/分钟会变成
# 全网共享 30 次/分钟——任何人单发 31 个请求，就能让所有用户的问答在这一分钟内
# 全部 429。登录（10 次/分钟）、注册（5 次/分钟）、留言（10 次/分钟）同理。
#
# `TRUST_PROXY_HOPS` 必须设成**真实代理层数**（nginx 一层在前 → 1）。保持默认 0
# 意味着不信任任何 `X-Forwarded-*`，因为盲目信任这个头会让任何人伪造来源、
# 绕过全部 IP 限流——那比不装更危险。
#
# 硬前提：Flask 只能被 nginx 访问，绝不能同时把 5000 端口直接暴露到公网。
_proxy_hops = _env_int('TRUST_PROXY_HOPS', 0)
if _proxy_hops > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix

    # x_for 取 X-Forwarded-For 中**从右往左第 N 个**，正好跳过客户端自己伪造的前缀，
    # 取到 nginx 追加的那个真实 IP；x_proto 让 request.is_secure 在 TLS 终结后仍为真。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxy_hops, x_proto=_proxy_hops, x_host=_proxy_hops)


_rate_limit_buckets = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def _rate_limit_identity() -> str:
    """限流主体：登录用户按 user_id，匿名才回落到 IP。

    只在 IP 维度分桶会被两种真实场景误伤：
    1. 反向代理没装 ProxyFix —— 所有人共享代理地址（见上）；
    2. 多人共用 NAT 出口（校园网、宿舍路由器）—— 彼此无关的用户被塞进同一个桶，
       一个人刷满额度，其他人全部 429。

    登录用户按 id 分桶就绕开了这两个问题；匿名请求只能按 IP，这是没得选的。
    """
    try:
        from flask_login import current_user

        if current_user and current_user.is_authenticated:
            return f'user:{current_user.id}'
    except Exception:
        # 未初始化 / 无请求上下文时退回 IP：限流本身绝不该因为身份探测失败而报错。
        pass
    return f'ip:{request.remote_addr or "unknown"}'


def rate_limit(max_requests: int, window_seconds: int = 60):
    """Small per-process limiter for JSON endpoints."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            now = monotonic()
            key = (_rate_limit_identity(), request.endpoint)
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



# ---------------------------------------------------------------------------
# 每日额度：付费上游的保险丝
# ---------------------------------------------------------------------------
# `/api/chat` 按产品设计允许**匿名**调用（用户不该被迫自备 API Key，密钥只放在
# 服务端 `.env` 里）。代价是这个接口的每一次调用都在花队长的钱，因此必须有硬上限：
# `rate_limit` 只能拦住"短时间内高频"，拦不住"每天慢慢刷"——后者才是最省钱、
# 最难被发现的滥用方式。
#
# 这一层按**自然日**记账：当天调用数达到上限后直接 503，在钱花出去之前就断掉。
#
# 已知边界（写清楚，避免误以为它是分布式限流）：计数在**进程内存**里，重启清零；
# 多 worker 部署时每个进程各记一份，实际上限 = 配置值 × worker 数。对本项目
# （单进程演示）足够；将来上多进程，把这里换成 Redis 的 INCR + EXPIRE 即可，
# 调用方的口径不用动。
_daily_quota_lock = threading.Lock()
_daily_quota_counters = {}


def daily_quota(max_requests: int, name: str, env_var: str = ''):
    """给某个接口设"每自然日 max_requests 次"的硬上限，超出即 503。

    `env_var` 指定的环境变量可在不改代码的前提下调整上限；写成 `0` 表示关闭
    （仅建议在本机调试时这么设）。
    """
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            limit = max_requests
            if env_var:
                raw = (os.getenv(env_var) or '').strip()
                if raw.isdigit():
                    limit = int(raw)
            if limit <= 0:
                return view(*args, **kwargs)
            key = (name, date.today().isoformat())
            with _daily_quota_lock:
                if key not in _daily_quota_counters:
                    # 跨天：前一天的所有计数直接丢弃，避免字典无限增长。
                    _daily_quota_counters.clear()
                used = _daily_quota_counters.get(key, 0)
                if used >= limit:
                    return jsonify({
                        'success': False,
                        'error': '今日智能问答额度已用完，请明天再试',
                    }), 503, {'Retry-After': '3600'}
                _daily_quota_counters[key] = used + 1
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


@app.errorhandler(Exception)
def unexpected_error(error):
    """未捕获异常的兜底：栈只进服务端日志，绝不进响应体。

    这条 handler 是"密钥不泄露"的最后一道保险。`.env` 里的 `DEEPSEEK_API_KEY`
    等凭据在进程环境变量里，任何一个把 traceback 或局部变量打回客户端的路径都等于
    直接把密钥交出去。这里统一收敛成一句固定文案，细节只写进 `app.logger`。

    HTTP 异常（404/405/413…）原样返回，保持既有状态码契约不变。
    """
    if isinstance(error, HTTPException):
        return error
    app.logger.exception('unhandled error on %s %s', request.method, request.path)
    return jsonify({'success': False, 'message': '服务器内部错误'}), 500


def warn_if_body_limit_too_small():
    """启动自检：上限低于附件预算时明确提示，避免图片链路"看起来能用、一发就 413"。"""
    limit = int(app.config['MAX_CONTENT_LENGTH'])
    if limit < ATTACHMENTS_BODY_BUDGET:
        print(f'提示：MAX_CONTENT_LENGTH={limit} 小于图片上传所需预算 '
              f'{ATTACHMENTS_BODY_BUDGET}；上传较大图片会返回 413。'
              f'可在 .env 中调大该值（或删掉该行使用新默认值）。')
