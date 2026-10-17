"""校园可视可答系统后端入口（门面 / facade）。

拆分前这里是约 4500 行的单体文件。现在只保留三件事：

1. 把本模块登记为 ``sys.modules['server']``，使 ``import server``（测试）与
   ``python server.py``（``__main__``）指向**同一个模块对象**——否则会出现
   "第二份 app / 第二份任务管理器"，表现为路由重复注册、任务状态互相看不见。
2. 按**严格的导入顺序**装载各功能模块：
   ``paths``（sys.path + .env）→ ``flask_app`` → ``llm_config`` → ``poi_data``
   → ``retrieval_engine`` → ``auth_db`` → ``api``（导入即注册全部蓝图）。
3. 启动 Flask 开发服务器。

模块级 ``__getattr__``（PEP 562）让拆分出去的任何名字仍然可以写成 ``server.xxx``，
因此既有的 ``from server import ...`` 与测试里的 ``server.xxx`` 全部继续可用。
但**凡是被 monkeypatch 的符号，调用点必须用 ``runtime.facade``**（见 runtime.py 的说明），
否则 patch 不会生效。
"""

import sys as _sys

# 必须最先执行：让 `python server.py` 的 __main__ 也能以 'server' 被查到。
_sys.modules.setdefault('server', _sys.modules[__name__])

import os  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402,F401  (测试会 patch server.requests.post)

import paths  # noqa: E402,F401  必须最先导入：sys.path 引导 + .env 载入
import flask_app  # noqa: E402
import llm_config  # noqa: E402
import poi_data  # noqa: E402
import retrieval_engine  # noqa: E402
import auth_db  # noqa: E402
import comments_store  # noqa: E402  评论数据层：供门面透出，测试可整体替换
import api  # noqa: E402,F401  导入即注册全部蓝图
from api import auth_api, campus_build_api, chat_api, comments_api, userdata_api  # noqa: E402
from flask_app import app, rate_limit, warn_if_body_limit_too_small  # noqa: E402,F401
# 行程编排入口：测试会整体替换它（`server.plan_day = spy`），因此在这里显式再导出，
# 供 api/chat_api.py 通过 `runtime.facade` 读取。
from itinerary import plan_day  # noqa: E402,F401
from tools.campus_generator.jobs import BuildJobManager  # noqa: E402

# 校园资产生成任务管理器。
# 测试会整体替换它（``server._CAMPUS_BUILD_MANAGER = FakeManager(...)``），且路由通过
# ``runtime.facade`` 读取，所以按"无法安全外移的符号就留在门面"的原则保留在这里。
_CAMPUS_BUILD_ROOT = Path(paths.BASE) / 'userdata' / 'campus_builds'
_CAMPUS_BUILD_MANAGER = BuildJobManager(
    _CAMPUS_BUILD_ROOT,
    max_workers=int(os.getenv('CAMPUS_BUILD_WORKERS', '2')),
    max_pending_tasks=int(os.getenv('CAMPUS_BUILD_MAX_PENDING', '16')),
    max_jobs=int(os.getenv('CAMPUS_BUILD_MAX_JOBS', '256')),
)

#: 门面查找的模块顺序。同名符号以**先出现者**为准。
_SUBMODULES = (
    paths,
    flask_app,
    llm_config,
    poi_data,
    retrieval_engine,
    auth_db,
    comments_store,
    campus_build_api,
    chat_api,
    comments_api,
    userdata_api,
    auth_api,
)


def __getattr__(name):
    """PEP 562：把拆分到子模块的名字透出为 ``server.<name>``。

    只在常规属性查找失败时调用，因此 ``patch.object(server, name)`` 依然优先命中被
    patch 的值；解析成功后写回本模块 ``globals()``，后续访问不再走这里。
    """
    for module in _SUBMODULES:
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


warn_if_body_limit_too_small()


if __name__ == "__main__":
    # sentence-transformers 会在首次请求时延迟导入大量模块。Windows 下
    # Flask 的 watchdog 可能把这些导入误判为源码变更并重启进程，导致
    # 正在处理的 /api/chat 请求出现 ECONNRESET，因此禁用自动重载。
    _debug_enabled = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=_debug_enabled, use_reloader=False)
