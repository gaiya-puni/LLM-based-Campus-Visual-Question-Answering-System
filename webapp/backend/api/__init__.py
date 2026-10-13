"""API 蓝图注册入口。

导入本包即完成全部路由注册（导入即副作用，与拆分前的 `@app.route` 等价）。
`server.py` 只需要 `import api` 就能拿到与拆分前完全一致的 URL 集合。
"""

import paths  # noqa: F401
from flask_app import app

from api import auth_api, campus_build_api, chat_api, userdata_api

BLUEPRINTS = (campus_build_api.bp, chat_api.bp, userdata_api.bp, auth_api.bp)

for _blueprint in BLUEPRINTS:
    app.register_blueprint(_blueprint)
