"""用户共建落盘层单测：脱敏、聚合去重、原子写、坏行容错与导出。

不依赖 Flask、不访问网络，可在临时目录里离线运行（`USERDATA_DIR` 指到 tmp）。
"""

import json
import os
import shutil
import tempfile
import unittest

import userdata_store as store

PUTUO = (121.406079, 31.227073)
MINHANG = (121.453725, 31.03148)


class _StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ud_store_')
        os.environ['USERDATA_DIR'] = self.tmp

    def tearDown(self):
        os.environ.pop('USERDATA_DIR', None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_event(self, kind, payload):
        with open(store.event_path(kind), 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def _items(self):
        return store.rebuild_pending()['items']


class MaskTests(unittest.TestCase):
    def test_phone_email_and_long_digits_are_masked(self):
        masked = store.mask_sensitive('我叫张伟，电话13812345678，邮箱 zhangsan@ecnu.edu.cn，学号10235501401')
        self.assertNotIn('张伟', masked)
        self.assertNotIn('13812345678', masked)
        self.assertNotIn('zhangsan@', masked)
        self.assertNotIn('10235501401', masked)
        self.assertIn('138****5678', masked)
        self.assertIn('@ecnu.edu.cn', masked)      # 域名保留，便于判断是不是校内邮箱

    def test_normal_text_is_untouched(self):
        # 过度脱敏会毁掉"用户在问哪个地点"这个核心价值
        text = '理科大楼怎么走，离图书馆远吗'
        self.assertEqual(store.mask_sensitive(text), text)

    def test_limit_truncates_with_ellipsis(self):
        masked = store.mask_sensitive('啊' * 30, 10)
        self.assertEqual(len(masked), 11)
        self.assertTrue(masked.endswith('…'))


class NonFiniteTests(_StoreTestCase):
    """NaN/Inf 专项：它们是"非法 JSON"与"骗过距离比较"两个问题的共同入口。"""

    def test_as_float_rejects_non_finite(self):
        for value in ('nan', 'inf', '-inf', float('nan'), float('inf'), float('-inf')):
            self.assertIsNone(store._as_float(value), value)
        self.assertEqual(store._as_float('121.4'), 121.4)
        self.assertEqual(store._as_float(31), 31.0)
        self.assertIsNone(store._as_float(None))
        self.assertIsNone(store._as_float('abc'))

    def test_events_with_nan_are_never_written(self):
        # confidence 用 `or 0.0` 兜底，但 NaN 是 truthy——必须靠 _as_float 拦掉
        self.assertFalse(store.append_event('report', {
            'at': '2026-09-24T10:00:00', 'name': 'NaN 点', 'campus': '普陀',
            'lng': float('nan'), 'lat': 31.0, 'confidence': float('nan'),
        }))
        self.assertEqual(store.read_events('report'), [])

    def test_nan_confidence_becomes_zero_and_file_stays_valid_json(self):
        self._write_event('extracted', {
            'at': '2026-09-24T10:00:00', 'name': '明月湖', 'campus': '普陀',
            'lng': None, 'lat': None, 'confidence': float('nan'), 'source': 'rule',
        })
        store.rebuild_pending()
        item = self._items()[0]
        self.assertEqual(item['confidence'], 0.0)
        raw = (store.data_dir() / store.PENDING_FILE).read_text(encoding='utf-8')
        # 浏览器 JSON.parse 不接受 NaN/Infinity，因此清单里出现它们就等于"审核页打不开"
        self.assertNotIn('NaN', raw)
        self.assertNotIn('Infinity', raw)
        self.assertIn('items', json.loads(raw))

    def test_atomic_write_refuses_nan_payload(self):
        self.assertFalse(store._atomic_write_json('x.json', {'lng': float('nan')}))
        self.assertFalse((store.data_dir() / 'x.json').exists())


class NormalizeTests(unittest.TestCase):
    def test_parenthetical_suffix_is_merged(self):
        self.assertEqual(store.normalize_name('图书馆（普陀）'), store.normalize_name('图书馆'))
        self.assertEqual(store.normalize_name('理科大楼'), store.normalize_name(' 理科大楼 '))

    def test_candidate_id_is_stable(self):
        self.assertEqual(store.make_candidate_id('图书馆（普陀）', '普陀'),
                         store.make_candidate_id('图书馆', '普陀'))
        self.assertNotEqual(store.make_candidate_id('图书馆', '普陀'),
                            store.make_candidate_id('图书馆', '闵行'))


class AggregateTests(_StoreTestCase):
    def test_events_merge_into_one_item_with_mentions_and_samples(self):
        self._write_event('report', {
            'at': '2026-09-24T10:00:00', 'name': '理科大楼', 'campus': '普陀',
            'lng': PUTUO[0], 'lat': PUTUO[1], 'category': 'building', 'note': '', 'query': '理科大楼怎么走',
        })
        self._write_event('extracted', {
            'at': '2026-09-24T10:05:00', 'name': '理科大楼（普陀）', 'campus': '普陀',
            'lng': None, 'lat': None, 'category': None, 'confidence': 0.7,
            'source': 'llm', 'query': '理科大楼在哪里呢',
        })
        items = self._items()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['mentions'], 2)
        self.assertEqual(item['source'], 'user')            # 来源取优先级最高的那个
        self.assertEqual(item['confidence'], 1.0)
        self.assertEqual(item['category'], 'building')
        self.assertEqual(item['firstSeenAt'], '2026-09-24T10:00:00')
        self.assertEqual(item['lastSeenAt'], '2026-09-24T10:05:00')
        self.assertEqual(item['samples'], ['理科大楼怎么走', '理科大楼在哪里呢'])
        self.assertAlmostEqual(item['lng'], PUTUO[0])

    def test_samples_are_capped_and_deduplicated(self):
        for index in range(5):
            self._write_event('extracted', {
                'at': f'2026-09-24T10:0{index}:00', 'name': '明月湖', 'campus': '普陀',
                'confidence': 0.6, 'source': 'rule', 'query': '明月湖在哪里' if index % 2 else f'明月湖怎么走{index}',
            })
        item = self._items()[0]
        self.assertEqual(item['mentions'], 5)
        self.assertEqual(len(item['samples']), store.MAX_SAMPLES)

    def test_same_name_far_apart_stays_two_places(self):
        self._write_event('report', {'at': '2026-09-24T10:00:00', 'name': '学生宿舍',
                                     'campus': '普陀', 'lng': PUTUO[0], 'lat': PUTUO[1]})
        self._write_event('report', {'at': '2026-09-24T10:01:00', 'name': '学生宿舍',
                                     'campus': '普陀', 'lng': MINHANG[0], 'lat': MINHANG[1]})
        self.assertEqual(len(self._items()), 2)

    def test_rebuild_is_idempotent(self):
        self._write_event('extracted', {'at': '2026-09-24T10:00:00', 'name': '明月湖',
                                        'campus': '普陀', 'confidence': 0.6, 'source': 'rule'})
        first = store.rebuild_pending()
        second = store.rebuild_pending()
        self.assertEqual(first['items'], second['items'])
        self.assertEqual(second['counts']['pending'], 1)

    def test_review_decision_survives_rebuild(self):
        self._write_event('extracted', {'at': '2026-09-24T10:00:00', 'name': '明月湖',
                                        'campus': '普陀', 'confidence': 0.6, 'source': 'rule'})
        item_id = self._items()[0]['id']
        self.assertTrue(store.update_status(item_id, 'approved', '已核实'))
        rebuilt = store.rebuild_pending()['items'][0]
        self.assertEqual(rebuilt['status'], 'approved')
        self.assertEqual(rebuilt['note'], '已核实')

    def test_invalid_status_is_rejected(self):
        self.assertFalse(store.update_status('p_x', 'whatever'))

    def test_mentions_ordering_puts_hot_items_first(self):
        self._write_event('extracted', {'at': '2026-09-24T10:00:00', 'name': '冷门湖',
                                        'campus': '普陀', 'confidence': 0.6, 'source': 'rule'})
        for index in range(3):
            self._write_event('extracted', {'at': f'2026-09-24T10:0{index}:00', 'name': '热门楼',
                                            'campus': '普陀', 'confidence': 0.6, 'source': 'rule'})
        self.assertEqual([item['name'] for item in self._items()], ['热门楼', '冷门湖'])


class IoRobustnessTests(_StoreTestCase):
    def test_missing_files_are_empty_not_errors(self):
        self.assertEqual(store.read_events('rating'), [])
        self.assertEqual(store.load_pending()['items'], [])

    def test_bad_lines_are_skipped(self):
        self._write_event('rating', {'at': '2026-09-24T10:00:00', 'rating': 'up'})
        with open(store.event_path('rating'), 'a', encoding='utf-8') as handle:
            handle.write('{半截写入的坏行\n')
            handle.write('\n')
        self._write_event('rating', {'at': '2026-09-24T10:01:00', 'rating': 'down'})
        ratings = [event['rating'] for event in store.read_events('rating')]
        self.assertEqual(ratings, ['up', 'down'])

    def test_atomic_write_leaves_no_tmp_file(self):
        store.rebuild_pending()
        leftovers = [name for name in os.listdir(self.tmp) if name.endswith('.tmp')]
        self.assertEqual(leftovers, [])
        with open(os.path.join(self.tmp, store.PENDING_FILE), encoding='utf-8') as handle:
            self.assertIn('items', json.load(handle))

    def test_write_failure_is_silent(self):
        blocker = os.path.join(self.tmp, 'not-a-dir')
        with open(blocker, 'w', encoding='utf-8') as handle:
            handle.write('x')
        os.environ['USERDATA_DIR'] = blocker
        self.assertFalse(store.append_event('rating', {'rating': 'up'}))
        self.assertIsNone(store.record_rating('m1', 'up'))
        self.assertIsNone(store.record_place_report('明月湖', '普陀', *PUTUO))
        self.assertEqual(store.read_events('rating'), [])

    def test_unknown_event_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            store.event_path('whatever')


class ConcurrencyTests(_StoreTestCase):
    def test_parallel_appends_never_interleave_lines(self):
        """多线程并发写事件流：每一行都必须是完整 JSON，且总条数不丢。"""
        import threading

        threads_count, per_thread = 8, 20

        def worker(index):
            for seq in range(per_thread):
                store.append_event('rating', {
                    'at': '2026-09-24T10:00:00', 'messageId': f'm-{index}-{seq}',
                    'rating': 'up', 'reason': '并发写入', 'query': '', 'engine': '',
                })

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with open(store.event_path('rating'), encoding='utf-8') as handle:
            lines = [line for line in handle if line.strip()]
        self.assertEqual(len(lines), threads_count * per_thread)
        for line in lines:
            json.loads(line)                     # 任何一行被写坏都会在这里抛错

    def test_parallel_reports_keep_the_aggregate_readable(self):
        import threading

        def worker(index):
            store.record_place_report(f'并发点{index}', '普陀', PUTUO[0] + index * 1e-5, PUTUO[1])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        payload = store.load_pending()
        self.assertEqual(len(payload['items']), 6)
        self.assertEqual(payload['counts']['pending'], 6)


class ExportTests(_StoreTestCase):
    def _seed(self, with_coords=True):
        self._write_event('report', {
            'at': '2026-09-24T10:00:00', 'name': '文创小店', 'campus': '普陀',
            'lng': PUTUO[0] if with_coords else None,
            'lat': PUTUO[1] if with_coords else None,
            'category': 'canteen', 'note': '在图书馆旁边',
        })
        return self._items()[0]['id']

    def test_export_only_includes_approved_and_maps_contract_fields(self):
        item_id = self._seed()
        self.assertFalse(store.export_approved()['approved'])       # 未审核不导出
        store.update_status(item_id, 'approved', '已核实')
        summary = store.export_approved()
        self.assertEqual(summary['approved'], 1)
        with open(os.path.join(self.tmp, store.APPROVED_FILE), encoding='utf-8') as handle:
            record = json.load(handle)['records'][0]
        for field in ('id', 'category', 'subCategory', 'name', 'locationName', 'lng', 'lat', 'text', 'tags'):
            self.assertIn(field, record)
        self.assertEqual(record['category'], 'canteen')
        self.assertEqual(record['subCategory'], '餐饮')
        self.assertEqual(record['campus'], '普陀')
        self.assertEqual(record['source'], 'user_contributed')

    def test_items_without_coordinates_are_skipped_not_exported(self):
        item_id = self._seed(with_coords=False)
        store.update_status(item_id, 'approved')
        summary = store.export_approved()
        self.assertEqual((summary['approved'], summary['skipped']), (0, 1))

    def test_markdown_lists_items(self):
        self._seed()
        store.rebuild_pending()
        with open(os.path.join(self.tmp, store.PENDING_MARKDOWN), encoding='utf-8') as handle:
            markdown = handle.read()
        self.assertIn('文创小店', markdown)
        self.assertIn('待确认地点清单', markdown)

    def test_stats_reports_volumes(self):
        store.record_rating('m1', 'down')
        store.record_unresolved_query('校医院在哪里')
        self._seed()
        stats = store.stats()
        self.assertEqual(stats['ratingsTotal'], 1)
        self.assertEqual(stats['unresolvedPending'], 1)
        self.assertEqual(stats['reportsTotal'], 1)

    def test_rating_none_means_withdrawal(self):
        store.record_rating('m1', 'up')
        store.record_rating('m1', 'none')
        self.assertEqual([event['rating'] for event in store.read_events('rating')], ['up', 'none'])


if __name__ == '__main__':
    unittest.main()
