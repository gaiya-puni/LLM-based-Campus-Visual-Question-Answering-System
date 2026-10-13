"""未收录地名抽取单测：通名规则、排除策略、大模型兜底解析与两种模式。

本模块完全离线：大模型以回调注入的方式用桩替换，不产生任何网络请求。
"""

import os
import unittest

import place_extraction as pe


class RuleExtractionTests(unittest.TestCase):
    def test_generic_suffixes_are_recognized(self):
        cases = {
            '理科大楼怎么走': ('理科大楼', 'building'),
            '荷花池在哪里': ('荷花池', 'water'),
            '第三食堂怎么去': ('第三食堂', 'canteen'),
            '北区停车场在哪': ('北区停车场', 'parking'),
            '思源湖怎么走': ('思源湖', 'water'),
        }
        for query, expected in cases.items():
            items = pe.rule_extract(query)
            self.assertTrue(items, query)
            self.assertEqual((items[0]['name'], items[0]['category']), expected, query)

    def test_known_names_are_excluded(self):
        known = frozenset({pe.normalize_key('理科大楼')})
        self.assertEqual(pe.rule_extract('理科大楼怎么走', known_names=known), [])
        # 括号限定要归一到同一个键，否则同一地点会被反复收进清单
        self.assertEqual(pe.rule_extract('理科大楼（普陀）怎么走', known_names=known), [])

    def test_generic_and_noisy_phrases_are_ignored(self):
        for query in ('我想去食堂', '这个地方在哪里', '哪里有卖奶茶的', '附近的楼怎么走',
                      '校园里哪个楼最老', '你觉得图书馆怎么样'):
            self.assertEqual(pe.rule_extract(query), [], query)

    def test_campus_words_are_not_places(self):
        # 校区名/校名片段来自 campus_config.stop_words()
        self.assertEqual(pe.rule_extract('普陀校区在哪里'), [])

    def test_at_most_three_candidates(self):
        query = '理科大楼、荷花池、第三食堂、北区停车场分别在哪'
        self.assertLessEqual(len(pe.rule_extract(query)), pe.MAX_CANDIDATES_PER_QUERY)

    def test_overlong_query_is_ignored(self):
        self.assertEqual(pe.rule_extract('理科大楼' + '啊' * 200), [])

    def test_confidence_rewards_location_intent(self):
        with_intent = pe.rule_extract('理科大楼在哪里')[0]['confidence']
        without_intent = pe.rule_extract('理科大楼')[0]['confidence']
        self.assertGreater(with_intent, without_intent)
        self.assertLessEqual(with_intent, 0.9)

    def test_normalize_key_merges_variants(self):
        self.assertEqual(pe.normalize_key('图书馆（普陀）'), pe.normalize_key('图书馆'))
        self.assertEqual(pe.normalize_key('  图书馆 '), '图书馆')


class LlmFallbackTests(unittest.TestCase):
    def test_parse_plain_and_fenced_json(self):
        self.assertEqual(
            [item['name'] for item in pe.parse_llm_places('[{"name": "文创小店", "category": "canteen"}]')],
            ['文创小店'])
        self.assertEqual(
            [item['name'] for item in pe.parse_llm_places('```json\n[{"name": "文创小店"}]\n```')],
            ['文创小店'])

    def test_parse_tolerates_junk_and_none(self):
        for payload in (None, '', '不知道', '["不是对象"]', '{"name": "x"}'):
            self.assertEqual(pe.parse_llm_places(payload), [])
        self.assertEqual(pe.parse_llm_places([{'name': '这个地方在哪里'}]), [])

    def test_parse_accepts_plain_strings(self):
        self.assertEqual([item['name'] for item in pe.parse_llm_places('["明月湖"]')], ['明月湖'])

    def test_llm_extract_uses_injected_call(self):
        seen = []

        def call(messages):
            seen.append(messages)
            return '[{"name": "文创小店", "category": "scene"}]'

        items = pe.llm_extract(['那个卖文创的小店在哪'], campus='普陀', call=call)
        self.assertEqual([item['name'] for item in items], ['文创小店'])
        self.assertEqual(items[0]['campus'], '普陀')
        self.assertEqual(items[0]['source'] if 'source' in items[0] else 'llm', 'llm')
        self.assertEqual(seen[0][0]['role'], 'system')

    def test_llm_extract_survives_callback_failure(self):
        def boom(_messages):
            raise RuntimeError('network down')

        self.assertEqual(pe.llm_extract(['x'], call=boom), [])

    def test_llm_extract_without_callback_is_empty(self):
        self.assertEqual(pe.llm_extract(['理科大楼在哪']), [])

    def test_batch_mode_defers_and_inline_mode_calls_llm(self):
        calls = []

        def call(messages):
            calls.append(messages)
            return '[{"name": "文创小店"}]'

        batch = pe.extract('那个卖文创的小店在哪', mode='batch', llm_call=call)
        self.assertEqual((batch['items'], batch['deferred'], calls), ([], True, []))

        inline = pe.extract('那个卖文创的小店在哪', mode='inline', llm_call=call)
        self.assertEqual([item['name'] for item in inline['items']], ['文创小店'])
        self.assertEqual(inline['via'], 'llm')
        self.assertEqual(len(calls), 1)

    def test_rules_win_over_llm(self):
        def call(_messages):        # 规则命中时不应该调用大模型
            raise AssertionError('should not be called')

        outcome = pe.extract('理科大楼怎么走', mode='inline', llm_call=call)
        self.assertEqual(outcome['via'], 'rule')

    def test_inline_mode_returns_empty_when_llm_finds_nothing(self):
        outcome = pe.extract('随便问点什么', mode='inline', llm_call=lambda messages: '[]')
        self.assertEqual(outcome['items'], [])
        self.assertEqual(outcome['via'], 'llm')

    def test_mode_env_var_is_normalized(self):
        original = os.environ.get('USERDATA_LLM_EXTRACT')
        try:
            os.environ['USERDATA_LLM_EXTRACT'] = 'INLINE'
            self.assertEqual(pe.extraction_mode(), 'inline')
            os.environ['USERDATA_LLM_EXTRACT'] = 'nonsense'
            self.assertEqual(pe.extraction_mode(), 'batch')
            os.environ.pop('USERDATA_LLM_EXTRACT')
            self.assertEqual(pe.extraction_mode(), 'batch')
        finally:
            if original is None:
                os.environ.pop('USERDATA_LLM_EXTRACT', None)
            else:
                os.environ['USERDATA_LLM_EXTRACT'] = original


if __name__ == '__main__':
    unittest.main()
