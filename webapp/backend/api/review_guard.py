"""审核态接口的门禁：校园资产生成与用户共建两条链路共用。

**未配置即拒绝**（而不是放行）：本地工具最容易出的问题就是"忘了配密钥"，让审核写操作
暴露出去。密钥只存本机 `.env`（已被 `.gitignore` 命中），不入代码、不入文档。
"""

import hmac
import os

from flask import jsonify, request

#: 审核接口要求的最小密钥长度：太短等于没有门禁，宁可不开放
REVIEW_TOKEN_MIN_LENGTH = 16


def review_token_ok() -> bool:
    """请求头 `X-Review-Token` 必须等于 .env 的 `USERDATA_REVIEW_TOKEN`。"""
    expected = (os.getenv('USERDATA_REVIEW_TOKEN') or '').strip()
    if len(expected) < REVIEW_TOKEN_MIN_LENGTH:
        return False
    provided = (request.headers.get('X-Review-Token') or '').strip()
    return hmac.compare_digest(provided, expected)


def review_denied():
    return jsonify({
        'success': False,
        'message': (
            f'审核接口未开启：请在 .env 配置 USERDATA_REVIEW_TOKEN（至少 {REVIEW_TOKEN_MIN_LENGTH} 位），'
            '并在请求头 X-Review-Token 中携带同一密钥'
        ),
    }), 403
