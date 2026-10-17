"""审核态接口的门禁：校园资产生成与用户共建两条链路共用。

**未配置即拒绝**（而不是放行）：本地工具最容易出的问题就是"忘了配密钥"，让审核写操作
暴露出去。密钥只存本机 `.env`（已被 `.gitignore` 命中），不入代码、不入文档。
"""

import hmac
import os

from flask import jsonify, request

#: 审核接口要求的最小密钥长度：太短等于没有门禁，宁可不开放
REVIEW_TOKEN_MIN_LENGTH = 16


def review_configured() -> bool:
    """本机是否已启用审核态（`.env` 配了足够长的 `USERDATA_REVIEW_TOKEN`）。

    只回答"有没有开"，不参与任何校验：前端据此决定是否显示「评论审核」入口。
    配置口径与 `review_token_ok()` 共用同一处判断，避免两边各写一份长度规则。
    """
    return len((os.getenv('USERDATA_REVIEW_TOKEN') or '').strip()) >= REVIEW_TOKEN_MIN_LENGTH


def review_token_ok() -> bool:
    """请求头 `X-Review-Token` 必须等于 .env 的 `USERDATA_REVIEW_TOKEN`。"""
    if not review_configured():
        return False
    expected = (os.getenv('USERDATA_REVIEW_TOKEN') or '').strip()
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
