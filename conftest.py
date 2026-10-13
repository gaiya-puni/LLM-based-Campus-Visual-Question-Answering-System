"""pytest 全局配置：让测试在**任何**环境下都能跑通，不依赖开发者本机的 `.env`。

问题：`resolve_llm()` 读不到密钥时返回 `None`，`/api/chat` 会直接走
503「AI 服务尚未配置」分支。CI（以及任何没配 `.env` 的机器）里没有任何凭据，
于是一大批聊天类测试失败——而它们在开发者本机是绿的，只因为 `.env` 里有真实密钥。
这类"只在本地绿"的测试是回归保护里最危险的一种，所以这里补一组**占位配置**。

几点设计约束：

1. 占位值刻意**不写成 `sk-xxxx` 形式**（而是普通单词），否则会被
   `scripts/check_publication.py` 的密钥特征规则误报。
2. 用 `setdefault` 语义：只填空缺，不覆盖测试自己 `patch.dict` 的值，
   也不覆盖开发者已设好的值（`load_dotenv()` 默认同样不覆盖已有变量）。
3. 在 `pytest_configure` 阶段设置，早于测试收集与模块导入，
   这样"导入期读环境变量"的代码也能看到这组默认值。
4. 需要断言"未配置时会怎样"的测试**显式传入 env 字典**
   （例如 `test_llm_config.py` 的 `resolve_llm({...})`），因此不受这里影响。

测试全部 mock 了出站 HTTP，占位密钥不会真的发出去。
"""

import os

DEFAULT_TEST_ENV = {
    "LLM_PROVIDER": "deepseek",
    "DEEPSEEK_API_KEY": "placeholder-for-tests-not-a-real-key",
}


def pytest_configure(config):
    for key, value in DEFAULT_TEST_ENV.items():
        os.environ.setdefault(key, value)
