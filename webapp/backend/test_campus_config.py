"""校区配置单一真源与前后端生成物的一致性测试。"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import campus_config


class CampusConfigAssetTests(unittest.TestCase):
    def setUp(self):
        campus_config.reset_cache()

    def tearDown(self):
        campus_config.reset_cache()

    def test_runtime_and_heatmap_configs_are_consistent(self):
        self.assertEqual(campus_config.check_consistency(), [])

        runtime = campus_config.config()
        heatmap = json.loads(
            Path(campus_config.HEATMAP_CONFIG_PATH).read_text(encoding='utf-8'))
        self.assertEqual(set(heatmap['campuses']), set(campus_config.campus_names()))
        for item in runtime['campuses']:
            heat_item = heatmap['campuses'][item['name']]
            self.assertEqual(heat_item['slug'], item['slug'])
            self.assertEqual(heat_item['center'], item['center'])
            self.assertIsInstance(heat_item['auditRadiusMeters'], (int, float))
            self.assertNotIsInstance(heat_item['auditRadiusMeters'], bool)
            self.assertGreater(heat_item['auditRadiusMeters'], 0)

    def test_default_uniqueness_and_school_foreign_keys(self):
        items = campus_config.campuses()
        names = [item['name'] for item in items]
        slugs = [item['slug'] for item in items]
        self.assertIn(campus_config.default_campus(), names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(slugs), len(set(slugs)))

        school_table = campus_config.schools()
        for item in items:
            self.assertIn(item.get('school'), school_table, item['name'])

        required_mapping = {
            '普陀': 'ecnu',
            '闵行': 'ecnu',
            '交大闵行': 'sjtu',
        }
        actual_mapping = {item['name']: item['school'] for item in items}
        self.assertLessEqual(required_mapping.items(), actual_mapping.items())

    def test_frontend_generated_config_has_not_drifted(self):
        actual = Path(campus_config.FRONTEND_TS_PATH).read_text(encoding='utf-8')
        self.assertEqual(actual, campus_config.render_frontend_ts())

    def test_each_center_is_recognized_as_its_own_campus(self):
        for item in campus_config.campuses():
            with self.subTest(campus=item['name']):
                self.assertEqual(campus_config.campus_at(*item['center']), item['name'])
                self.assertEqual(campus_config.campus_of(*item['center']), item['name'])

    def test_query_matching_disambiguates_the_two_minhang_campuses(self):
        cases = {
            '普陀校区哪里适合散步': '普陀',
            '华东师范大学闵行校区图书馆': '闵行',
            '上海交通大学闵行校区包玉刚图书馆': '交大闵行',
            '交大闵行校区哪里适合拍照': '交大闵行',
            '闵行本部食堂在哪里': '交大闵行',
            'SJTU 包玉刚图书馆': '交大闵行',
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(campus_config.campus_from_text(query), expected)
        self.assertIsNone(campus_config.campus_from_text('华东师范大学有哪些校区'))
        self.assertIsNone(campus_config.campus_from_text('复旦大学邯郸校区'))


class CampusConfigConsistencyMutationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {
            'defaultCampus': '甲',
            'schools': {
                'demo': {
                    'name': '示例大学',
                    'enName': 'DEMO',
                    'aliases': [],
                    'stopWords': [],
                },
            },
            'campuses': [
                {
                    'name': '甲', 'slug': 'alpha', 'school': 'demo', 'aliases': [],
                    'center': [121.0, 31.0], 'trustRadiusM': 1000,
                },
                {
                    'name': '乙', 'slug': 'beta', 'school': 'demo', 'aliases': [],
                    'center': [121.1, 31.1], 'trustRadiusM': 1000,
                },
            ],
        }
        self.heatmap = {
            'campuses': {
                '甲': {
                    'slug': 'alpha', 'center': [121.0, 31.0],
                    'auditRadiusMeters': 1200,
                },
                '乙': {
                    'slug': 'beta', 'center': [121.1, 31.1],
                    'auditRadiusMeters': 1200,
                },
            },
        }

    def _problems(self, mutate_runtime=None, mutate_heatmap=None):
        runtime = copy.deepcopy(self.runtime)
        heatmap = copy.deepcopy(self.heatmap)
        if mutate_runtime:
            mutate_runtime(runtime)
        if mutate_heatmap:
            mutate_heatmap(heatmap)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heatmap_config.json'
            path.write_text(json.dumps(heatmap, ensure_ascii=False), encoding='utf-8')
            with patch.object(campus_config, '_CACHE', runtime), patch.object(
                    campus_config, 'HEATMAP_CONFIG_PATH', str(path)):
                return campus_config.check_consistency()

    def test_checker_rejects_schema_and_cross_file_drift(self):
        cases = (
            (
                'missing school',
                lambda data: data['campuses'][0].pop('school'),
                None,
                '必须声明 school 外键',
            ),
            (
                'unknown school',
                lambda data: data['campuses'][0].update(school='missing'),
                None,
                '未在 schools 表中登记',
            ),
            (
                'unknown default',
                lambda data: data.update(defaultCampus='不存在'),
                None,
                'defaultCampus',
            ),
            (
                'duplicate slug',
                lambda data: data['campuses'][1].update(slug='alpha'),
                None,
                'slug 必须全局唯一',
            ),
            (
                'missing heatmap campus',
                None,
                lambda data: data['campuses'].pop('乙'),
                'heatmap_config.json 缺少该校区',
            ),
            (
                'extra heatmap campus',
                None,
                lambda data: data['campuses'].update({
                    '丙': {'slug': 'gamma', 'center': [0, 0], 'auditRadiusMeters': 1},
                }),
                '但未登记到 campuses.json',
            ),
            (
                'center drift',
                None,
                lambda data: data['campuses']['甲'].update(center=[122.0, 31.0]),
                'center 不一致',
            ),
            (
                'slug drift',
                None,
                lambda data: data['campuses']['甲'].update(slug='changed'),
                'slug 不一致',
            ),
            (
                'invalid audit radius',
                None,
                lambda data: data['campuses']['甲'].update(auditRadiusMeters=False),
                'auditRadiusMeters 必须是正数',
            ),
        )
        for label, mutate_runtime, mutate_heatmap, expected in cases:
            with self.subTest(case=label):
                problems = self._problems(mutate_runtime, mutate_heatmap)
                self.assertTrue(any(expected in problem for problem in problems), problems)


if __name__ == '__main__':
    unittest.main()
