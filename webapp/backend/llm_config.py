
"""LLM provider registry, image-understanding capability probe and HTTP proxy setup."""

import os

# 大模型服务商注册表。DeepSeek 与 ChatECNU 都提供 OpenAI 兼容的 /chat/completions，
# 响应结构一致（choices[0].message.content），因此调用与解析代码完全复用。
# 这里只登记"读哪些环境变量"，取值在 resolve_llm() 里进行——它是纯函数，便于单测。
LLM_PROVIDERS = {
    'deepseek': {
        'label': 'DeepSeek',
        'api_key_env': 'DEEPSEEK_API_KEY',
        'api_url_env': 'DEEPSEEK_API_URL',
        'api_url_default': 'https://api.deepseek.com/v1/chat/completions',
        'model_env': 'DEEPSEEK_MODEL',
        'model_default': 'deepseek-chat',
        # deepseek-chat 是纯文本模型：收到 image_url 会报错，因此默认不声明视觉能力。
        'supports_vision': False,
    },
    'chatecnu': {
        'label': 'ChatECNU',
        'api_key_env': 'CHATECNU_API_KEY',
        'api_url_env': 'CHATECNU_API_URL',
        'api_url_default': 'https://chat.ecnu.edu.cn/open/api/v1/chat/completions',
        'model_env': 'CHATECNU_MODEL',
        'model_default': 'ecnu-plus',
        # 未确认 ecnu-plus 的多模态能力，保守记为 False，需要时用 VISION_ENABLED=true 显式开启。
        'supports_vision': False,
    },
}

DEFAULT_LLM_PROVIDER = 'deepseek'


def _provider_config(name: str, source) -> dict:
    entry = LLM_PROVIDERS[name]
    return {
        'label': entry['label'],
        'name': name,
        'api_key': (source.get(entry['api_key_env']) or '').strip(),
        'api_url': (source.get(entry['api_url_env']) or entry['api_url_default']).strip(),
        'model': (source.get(entry['model_env']) or entry['model_default']).strip(),
    }


def selected_llm_provider(source=None) -> str:
    """当前选择的（小写）服务商名。未设置 LLM_PROVIDER 时用默认值。"""
    return ((source or os.environ).get('LLM_PROVIDER') or DEFAULT_LLM_PROVIDER).strip().lower()


def resolve_llm(env=None):
    """返回当前生效的服务商配置；未配置（或服务商名无效）时返回 None。

    服务商由 .env 的 LLM_PROVIDER 显式选择（deepseek / chatecnu）。**不会静默回退**：
    选中的服务商缺少密钥时一律视为未配置，避免调用方误判实际的数据流向。
    env 参数仅用于单测注入，默认读进程环境变量。
    """
    source = os.environ if env is None else env
    name = selected_llm_provider(source)
    if name not in LLM_PROVIDERS:
        return None
    config = _provider_config(name, source)
    if not config['api_key'] or not config['api_url'] or not config['model']:
        return None
    return config


def log_llm_setup(source=None):
    """启动时打印一次当前服务商与模型，便于部署者排查；绝不打印密钥。"""
    source = os.environ if source is None else source
    name = selected_llm_provider(source)
    if name not in LLM_PROVIDERS:
        print(f'LLM_PROVIDER="{name}" 不是已知服务商，可选：'
              f'{"、".join(LLM_PROVIDERS)}；请在 .env 中修正。')
        return
    config = _provider_config(name, source)
    if not config['api_key']:
        key_env = LLM_PROVIDERS[name]['api_key_env']
        print(f'{config["label"]} 未配置密钥（{key_env}），'
              '/api/chat 将返回"AI 服务尚未配置"。')
        return
    print(f'LLM provider: {config["label"]} ({name}) model={config["model"]}')
    vision = resolve_vision(source)
    if vision:
        print(f'Vision provider: {vision["label"]} model={vision["model"]}')
    else:
        print('Vision 未启用（图片理解不可用）：配置 VISION_API_KEY 或设 VISION_ENABLED=true 后重启。')


# ---------------------------------------------------------------------------
# 图片理解（多模态）能力探测
# ---------------------------------------------------------------------------
# 默认**不假设**任何服务商具备视觉能力：deepseek-chat 等纯文本模型收到 image_url
# 会直接报错，静默借用只会让用户看到"AI 服务请求失败"。要在本机启用图片问答，二选一：
#   1) 配置一组独立的视觉模型（VISION_API_KEY，可选 VISION_API_URL / VISION_MODEL）；
#   2) 确认当前服务商本身就是多模态，设 VISION_ENABLED=true 显式开启。
# 两者都没有时 vision_available() 为 False，问答会如实回答"本部署未启用图片理解"。
VISION_DISABLED_VALUES = ('0', 'false', 'off', 'no')
VISION_FORCED_VALUES = ('true', '1', 'yes', 'on')


def resolve_vision(env=None):
    """返回当前生效的视觉模型配置；未启用时返回 None（**不静默借用文本模型**）。"""
    source = os.environ if env is None else env
    switch = (source.get('VISION_ENABLED') or 'auto').strip().lower()
    if switch in VISION_DISABLED_VALUES:
        return None
    dedicated_key = (source.get('VISION_API_KEY') or '').strip()
    if dedicated_key:
        fallback = LLM_PROVIDERS[DEFAULT_LLM_PROVIDER]
        base = resolve_llm(source) or {}
        return {
            'label': (source.get('VISION_LABEL') or 'Vision').strip() or 'Vision',
            'name': 'vision',
            'api_key': dedicated_key,
            'api_url': (source.get('VISION_API_URL') or base.get('api_url')
                        or fallback['api_url_default']).strip(),
            'model': (source.get('VISION_MODEL') or base.get('model')
                      or fallback['model_default']).strip(),
        }
    if switch in VISION_FORCED_VALUES:
        return resolve_llm(source)
    entry = LLM_PROVIDERS.get(selected_llm_provider(source))
    if entry and entry.get('supports_vision'):
        return resolve_llm(source)
    return None


def vision_available(env=None) -> bool:
    """当前部署是否具备图片理解能力（prompt 文案与前端提示共用同一判断）。"""
    return resolve_vision(env) is not None


log_llm_setup()


# 大模型与高德都是国内服务，默认**直连**：显式把两个代理都置为 None，避免被系统的
# 代理设置带偏。Clash 等工具会把 Windows 系统代理注册成 `https://127.0.0.1:7897`，
# requests 会把它当成 TLS 代理去连，抛 SSLEOFError，表现为"AI 服务请求失败"。
# 若网络环境确实必须走代理，在 .env 里配置 LLM_HTTP_PROXY=http://127.0.0.1:7897。
_LLM_PROXY = os.getenv('LLM_HTTP_PROXY', '').strip()
_LLM_PROXIES = ({'http': _LLM_PROXY, 'https': _LLM_PROXY} if _LLM_PROXY
                else {'http': None, 'https': None})
