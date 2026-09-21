"""一日行程规划单元测试。

不依赖句向量模型、不访问网络，可离线运行（规避本机既有的原生崩溃问题）。
覆盖：意图判定、三段完整性、中午必为餐饮、站点去重、贪心顺序、距离阈值降级、
缺失时段跳过、站点不足回退、以及文案与响应结构。
"""

import unittest

import itinerary as it


def _poi(name, lng, lat, **extra):
    return dict({'name': name, 'lng': lng, 'lat': lat}, **extra)


# 普陀校区附近的真实坐标量级
MORNING = [
    _poi('夏雨岛', 121.404854, 31.229632, scene='walk', reason='水岸安静'),
    _poi('文史楼草坪', 121.4068, 31.2280, scene='walk'),
]
NOON = [
    _poi('河西食堂', 121.403126, 31.230404, category='canteen', reason='校园食堂'),
    _poi('丽娃餐厅', 121.4090, 31.2270, category='canteen'),
]
AFTERNOON = [
    _poi('图书馆', 121.4066, 31.2283, scene='study'),
    _poi('网院楼后', 121.4075, 31.2271, scene='photo'),
]


def _plan(morning=None, noon=None, afternoon=None, **kwargs):
    return it.plan_day('普陀', {
        'morning': MORNING if morning is None else morning,
        'noon': NOON if noon is None else noon,
        'afternoon': AFTERNOON if afternoon is None else afternoon,
    }, **kwargs)


class ItineraryIntentTests(unittest.TestCase):
    def test_planning_phrases_are_itinerary(self):
        for query in (
            '我第一次来普陀校区，帮我规划一天',
            '帮我规划一下校园一日游',
            '一日游怎么安排',
            '带我逛逛这个校园',
            '校园一日行程怎么安排',
            '一整天在校园里怎么玩',
        ):
            self.assertTrue(it.is_itinerary_query(query), query)

    def test_time_window_pair_is_itinerary(self):
        self.assertTrue(it.is_itinerary_query('上午和下午分别做什么'))
        self.assertTrue(it.is_itinerary_query('上午做什么，下午做什么'))

    def test_weak_intent_words_need_a_time_hint(self):
        # 只有"新人身份"描述时，不应抢走地点 / 植物类问句
        for query in (
            '我刚来学校，想去图书馆',
            '第一次来，看看有什么古树',
            '初来乍到，有哪些学院',
        ):
            self.assertFalse(it.is_itinerary_query(query), query)
        # 配上时段或时长线索，才说明用户想安排一整天
        for query in (
            '初来乍到，只有半天时间',
            '刚来学校，下午想去逛逛',
        ):
            self.assertTrue(it.is_itinerary_query(query), query)

    def test_single_scene_questions_are_not_itinerary(self):
        for query in (
            '校园里哪里适合拍照',
            '第一次来这里拍照推荐哪里',
            '下午哪里适合散步',
            '学校有什么好吃的食堂',
        ):
            self.assertFalse(it.is_itinerary_query(query), query)

    def test_location_lookup_wins_over_planning_words(self):
        for query in (
            '第一次来闵行校区，图书馆在哪里',
            '第一次来普陀，河西食堂怎么走',
            '带我逛的路线怎么导航过去',
        ):
            self.assertFalse(it.is_itinerary_query(query), query)

    def test_blank_and_overlong_queries_are_not_itinerary(self):
        self.assertFalse(it.is_itinerary_query(''))
        self.assertFalse(it.is_itinerary_query(None))
        self.assertFalse(it.is_itinerary_query('帮我规划一天' + '逛' * 130))


class ItineraryPlanTests(unittest.TestCase):
    def test_all_three_periods_get_stops(self):
        plan = _plan()
        self.assertEqual({stop['period'] for stop in plan['stops']}, set(it.PERIODS))
        self.assertTrue(plan['sufficient'])
        self.assertEqual(plan['skipped'], [])

    def test_noon_is_always_dining(self):
        # 中午候选里混入非餐饮项，也必须只选餐饮
        mixed = NOON + [_poi('图书馆', 121.4066, 31.2283, category='plant')]
        plan = _plan(noon=mixed)
        noon_stops = [stop for stop in plan['stops'] if stop['period'] == 'noon']
        self.assertTrue(noon_stops)
        for stop in noon_stops:
            self.assertEqual(stop['category'], 'canteen')

    def test_noon_without_category_trusts_caller(self):
        # 候选完全没有 category 字段时信任调用方已筛过，不应清空中午
        plan = _plan(noon=[_poi('某食堂', 121.403126, 31.230404)])
        self.assertEqual([stop['period'] for stop in plan['stops']].count('noon'), 1)

    def test_noon_mixed_categories_without_dining_falls_back(self):
        # 部分候选带类别、且一条餐饮都没有时，退回"未标注类别"的候选而不是清空中午
        mixed = [_poi('图书馆', 121.4066, 31.2283, category='plant'),
                 _poi('候选食堂', 121.403126, 31.230404)]
        plan = _plan(noon=mixed)
        self.assertEqual([stop['name'] for stop in plan['stops'] if stop['period'] == 'noon'],
                         ['候选食堂'])

    def test_stops_are_unique_and_seq_is_continuous(self):
        plan = _plan()
        names = [stop['name'] for stop in plan['stops']]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual([stop['seq'] for stop in plan['stops']],
                         list(range(1, len(plan['stops']) + 1)))

    def test_duplicate_candidates_are_deduped(self):
        duplicated = MORNING + [dict(MORNING[0])]
        plan = _plan(morning=duplicated)
        morning_names = [s['name'] for s in plan['stops'] if s['period'] == 'morning']
        self.assertEqual(morning_names.count('夏雨岛'), 1)

    def test_total_stops_within_limits(self):
        plan = _plan()
        self.assertLessEqual(len(plan['stops']), it.MAX_TOTAL_STOPS)
        self.assertGreaterEqual(len(plan['stops']), it.MIN_TOTAL_STOPS)

    def test_greedy_order_follows_nearest_neighbour(self):
        origin = (121.4000, 31.2300)
        plan = it.plan_day('普陀', {
            # 故意乱序给入，编排必须由近及远
            'morning': [_poi('远', 121.4040, 31.2303), _poi('中', 121.4020, 31.2302),
                        _poi('近', 121.4005, 31.2301)],
            'noon': [_poi('食堂', 121.4045, 31.2303, category='canteen')],
            'afternoon': [_poi('下午点', 121.4050, 31.2304)],
        }, origin)
        self.assertEqual([s['name'] for s in plan['stops'] if s['period'] == 'morning'],
                         ['近', '中'])
        self.assertEqual(plan['legs'][0]['fromName'], '近')
        self.assertEqual(plan['legs'][0]['toName'], '中')

    def test_origin_none_starts_from_first_candidate(self):
        # 起点未知时从上午首个候选起步，其后仍按最近邻串联
        plan = it.plan_day('普陀', {
            'morning': [_poi('甲', 121.4000, 31.2300), _poi('乙', 121.4005, 31.2301),
                        _poi('丙', 121.4100, 31.2300)],
            'noon': NOON,
            'afternoon': AFTERNOON,
        })
        self.assertEqual([s['name'] for s in plan['stops'] if s['period'] == 'morning'],
                         ['甲', '乙'])

    def test_legs_match_consecutive_stops(self):
        plan = _plan()
        stops, legs = plan['stops'], plan['legs']
        self.assertEqual(len(legs), len(stops) - 1)
        for index, leg in enumerate(legs):
            self.assertEqual(leg['fromSeq'], stops[index]['seq'])
            self.assertEqual(leg['toSeq'], stops[index + 1]['seq'])
            expected = it._distance_meters(
                stops[index]['lng'], stops[index]['lat'],
                stops[index + 1]['lng'], stops[index + 1]['lat'])
            self.assertAlmostEqual(leg['distanceMeters'], expected, delta=2)
            self.assertGreaterEqual(leg['durationMinutes'], 1)

    def test_far_candidate_degrades_to_riding(self):
        plan = _plan(afternoon=[_poi('远点', 121.46, 31.03)])
        self.assertTrue(plan['legs'])
        self.assertEqual(plan['legs'][-1]['mode'], 'riding')

    def test_near_candidates_stay_walking(self):
        plan = _plan()
        self.assertTrue(all(leg['mode'] == 'walking' for leg in plan['legs']))

    def test_missing_period_is_skipped_with_reason(self):
        plan = _plan(noon=[])
        self.assertEqual([s['period'] for s in plan['stops']].count('noon'), 0)
        skipped = [item for item in plan['skipped'] if item['period'] == 'noon']
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['reason'], 'no_candidate')
        self.assertTrue(plan['sufficient'])  # 仍有 4 站，行程成立

    def test_insufficient_stops_flags_fallback(self):
        plan = _plan(morning=MORNING[:1], noon=NOON[:1], afternoon=[])
        self.assertEqual(len(plan['stops']), 2)
        self.assertFalse(plan['sufficient'])

    def test_empty_candidates_never_raise(self):
        plan = it.plan_day('普陀', {'morning': [], 'noon': [], 'afternoon': []})
        self.assertEqual(plan['stops'], [])
        self.assertEqual(plan['legs'], [])
        self.assertFalse(plan['sufficient'])
        self.assertEqual(len(plan['skipped']), 3)

    def test_invalid_candidates_are_ignored(self):
        plan = it.plan_day('普陀', {
            'morning': [_poi('缺坐标', None, None), 'not-a-dict', _poi('', 121.4, 31.2)],
            'noon': NOON,
            'afternoon': AFTERNOON,
        })
        self.assertNotIn('缺坐标', [s['name'] for s in plan['stops']])

    def test_stop_carries_campus_and_dwell(self):
        plan = _plan()
        for stop in plan['stops']:
            self.assertEqual(stop['campus'], '普陀')
            self.assertGreater(stop['dwellMinutes'], 0)
            self.assertTrue(stop['reason'])
            self.assertIn(stop['periodLabel'], it.PERIOD_LABELS.values())


class ItineraryCopyTests(unittest.TestCase):
    def test_fallback_text_covers_every_stop(self):
        plan = _plan()
        text = it.render_fallback_text('普陀', plan)
        for stop in plan['stops']:
            self.assertIn(stop['name'], text)

    def test_fallback_text_handles_empty_plan(self):
        plan = it.plan_day('普陀', {'morning': [], 'noon': [], 'afternoon': []})
        self.assertIn('暂时无法生成', it.render_fallback_text('普陀', plan))

    def test_context_lists_stops_and_legs(self):
        plan = _plan()
        context = it.build_itinerary_context('普陀', plan)
        for stop in plan['stops']:
            self.assertIn(stop['name'], context)
        self.assertIn('第 1 站 → 第 2 站', context)
        self.assertIn('不得新增', context)

    def test_context_is_empty_without_stops(self):
        plan = it.plan_day('普陀', {'morning': [], 'noon': [], 'afternoon': []})
        self.assertEqual(it.build_itinerary_context('普陀', plan), '')

    def test_payload_shape(self):
        plan = _plan()
        payload = it.build_itinerary_payload('普陀', plan, fallback=True)
        self.assertEqual(sorted(payload.keys()),
                         ['campus', 'fallback', 'legs', 'skipped', 'stops', 'title'])
        self.assertTrue(payload['fallback'])
        self.assertEqual(payload['campus'], '普陀')
        self.assertIn('普陀', payload['title'])

    def test_location_pins_use_itinerary_stop_kind(self):
        plan = _plan()
        pins = it.build_location_pins(plan)
        self.assertEqual(len(pins), len(plan['stops']))
        for pin, stop in zip(pins, plan['stops']):
            self.assertEqual(pin['kind'], 'itinerary_stop')
            self.assertEqual(pin['seq'], stop['seq'])
            self.assertEqual(pin['name'], stop['name'])
            self.assertEqual(pin['lng'], stop['lng'])

    def test_chat_reply_keeps_existing_contract(self):
        plan = _plan()
        reply = it.build_chat_reply('普陀', plan, '行程文案')
        self.assertEqual(reply['choices'][0]['message']['content'], '行程文案')
        self.assertEqual(reply['ranked_places'], [])
        self.assertEqual(reply['recommendation_engine'], 'itinerary')
        self.assertIn('itinerary', reply)
        self.assertEqual(len(reply['locations']), len(plan['stops']))


if __name__ == '__main__':
    unittest.main()
