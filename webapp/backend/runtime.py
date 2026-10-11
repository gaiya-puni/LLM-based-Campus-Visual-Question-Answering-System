"""门面代理：让拆出去的模块仍能观察到测试对 `server` 模块属性的 monkeypatch。

背景：现有测试大量使用 ``patch.object(server, "X", ...)`` / ``server.X = ...``。
这种写法**只改 server 模块自己的属性**，不会影响 X 原本所在的模块。因此凡是被 patch 过的
符号（`_CAMPUS_BUILD_MANAGER`、`CampusProfileResolver`、`WebSearchClient`、`resolve_llm`、
`plan_day`、`_EMOTIONS`、`get_cursor`、`get_user`、`update_user_password` …），
调用方都必须在**调用时**经 `facade` 解析，而不是在导入时绑定::

    from runtime import facade
    ...
    manager = facade._CAMPUS_BUILD_MANAGER
    config = facade.resolve_llm()

`server.py` 顶部会执行 ``sys.modules.setdefault('server', sys.modules[__name__])``，
因此无论入口是 ``import server``（测试）还是 ``python server.py``（`__main__`），
这里都能拿到同一个模块对象，不会出现"第二份 app / 第二份任务管理器"。
"""

import sys

_ENTRY_MODULE = 'server'


def _entry_module():
    module = sys.modules.get(_ENTRY_MODULE)
    if module is None:
        raise RuntimeError(
            'server 门面模块尚未就绪：请通过 `import server` 或 `python server.py` 启动，'
            '不要在 server.py 初始化完成前访问可 patch 的符号。'
        )
    return module


class _FacadeProxy:
    """属性访问即转发到 `server` 模块（含其模块级 ``__getattr__`` 兜底）。"""

    __slots__ = ()

    def __getattr__(self, name):
        return getattr(_entry_module(), name)

    def __dir__(self):
        return sorted(set(dir(_entry_module())))

    def __repr__(self):  # pragma: no cover - 仅调试用
        return '<runtime.facade proxy>'


#: 全局唯一的门面代理实例。**只做属性访问**，不要把它当函数调用。
facade = _FacadeProxy()
