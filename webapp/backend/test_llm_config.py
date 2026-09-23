"""大模型服务商配置的单元测试。

锁定约定：**服务商由 .env 的 LLM_PROVIDER 显式选择；选中的服务商缺少密钥时一律视为
未配置，绝不静默切换到另一个服务商**（避免调用方误判实际的数据流向）。

`resolve_llm(env)` 支持注入 env 映射，因此这里不需要真的改环境变量。
"""
import unittest

import server


class LlmProviderTests(unittest.TestCase):
    def test_defaults_to_deepseek(self):
        config = server.resolve_llm({'DEEPSEEK_API_KEY': 'sk-demo'})
        self.assertIsNotNone(config)
        self.assertEqual(config['name'], 'deepseek')
        self.assertEqual(config['api_url'], 'https://api.deepseek.com/v1/chat/completions')
        self.assertEqual(config['model'], 'deepseek-chat')

    def test_explicitly_selects_chatecnu(self):
        config = server.resolve_llm({
            'LLM_PROVIDER': 'chatecnu',
            'CHATECNU_API_KEY': 'sk-demo',
        })
        self.assertIsNotNone(config)
        self.assertEqual(config['name'], 'chatecnu')
        self.assertEqual(config['label'], 'ChatECNU')
        self.assertEqual(config['api_url'], 'https://chat.ecnu.edu.cn/open/api/v1/chat/completions')
        self.assertEqual(config['model'], 'ecnu-plus')
        self.assertEqual(config['api_key'], 'sk-demo')

    def test_provider_name_is_case_insensitive_and_trimmed(self):
        config = server.resolve_llm({
            'LLM_PROVIDER': ' ChatECNU ',
            'CHATECNU_API_KEY': 'sk-demo',
        })
        self.assertEqual(config['name'], 'chatecnu')

    def test_does_not_silently_fall_back_to_the_other_provider(self):
        # 选了 ChatECNU 却没配它的密钥：即使 DeepSeek 的密钥齐备，也必须返回未配置
        config = server.resolve_llm({
            'LLM_PROVIDER': 'chatecnu',
            'DEEPSEEK_API_KEY': 'sk-deepseek',
            'DEEPSEEK_API_URL': 'https://api.deepseek.com/v1/chat/completions',
            'DEEPSEEK_MODEL': 'deepseek-chat',
        })
        self.assertIsNone(config)

    def test_unknown_provider_is_unconfigured(self):
        self.assertIsNone(server.resolve_llm({
            'LLM_PROVIDER': 'openai',
            'DEEPSEEK_API_KEY': 'sk-demo',
        }))

    def test_missing_key_is_unconfigured(self):
        self.assertIsNone(server.resolve_llm({'LLM_PROVIDER': 'deepseek'}))
        self.assertIsNone(server.resolve_llm({'LLM_PROVIDER': 'chatecnu'}))

    def test_url_and_model_have_defaults_but_key_does_not(self):
        config = server.resolve_llm({'LLM_PROVIDER': 'chatecnu', 'CHATECNU_API_KEY': 'sk-demo'})
        self.assertTrue(config['api_url'].startswith('https://'))
        self.assertTrue(config['model'])
        # 空白密钥也要视为未配置
        self.assertIsNone(server.resolve_llm({'LLM_PROVIDER': 'chatecnu',
                                              'CHATECNU_API_KEY': '   '}))

    def test_every_provider_declares_the_same_fields(self):
        expected = {'label', 'api_key_env', 'api_url_env', 'api_url_default',
                    'model_env', 'model_default'}
        for name, entry in server.LLM_PROVIDERS.items():
            self.assertEqual(set(entry), expected, name)


if __name__ == '__main__':
    unittest.main()
