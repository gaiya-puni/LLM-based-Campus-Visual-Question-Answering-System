"""Numerical, cache, and API regression tests; no external chat calls."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import scene_heatmaps as hm
from build_scene_heatmaps import scene_class_weight_map

BASE = Path(__file__).resolve().parent


class HeatmapMathTests(unittest.TestCase):
    def setUp(self):
        self.config = hm.load_config(BASE)
        self.center = self.config['campuses']['普陀']['center']
        self.pois = []
        for i, xy in enumerate([[-100,-100],[-100,100],[100,-100],[100,100],[0,0],[30,30]]):
            lng, lat = hm.to_lnglat(xy, self.center)
            self.pois.append(dict(id=f'p{i}', category='plant', subCategory='甲' if i < 4 else '乙',
                                  name='植物', locationName=f'实测点{i}', campus='普陀', lng=lng, lat=lat))

    def test_coordinate_round_trip(self):
        xy = np.array([[10, 40], [-25, 130]])
        np.testing.assert_allclose(hm.to_meters(hm.to_lnglat(xy, self.center), self.center), xy, atol=1e-6)

    def test_audit_deduplicates_and_rejects_bad_coordinates(self):
        bad = dict(self.pois[0], id='bad', lng=float('nan'))
        wrong = dict(self.pois[1], id='wrong', locationName='樱桃河边')
        kept, audit = hm.audit_pois(self.pois + [self.pois[0], bad, wrong], self.config)
        self.assertEqual(len(kept), 6)
        self.assertEqual(sum(audit['excluded'].values()), 3)

    def test_singleton_bandwidth_is_finite(self):
        h = hm.class_bandwidth(np.array([[0., 0.]]), np.array([0., 0.]), self.config)
        self.assertTrue(np.isfinite(h).all())
        self.assertGreater(h[0], 0)
        self.assertLessEqual(h[0], self.config['maxInfluenceMeters'] / 3)

    def test_annd_and_relative_abundance(self):
        xy = np.array([[0., 0.], [10., 0.], [400., 0.]])
        h = hm.class_bandwidth(xy, np.array([0., 0.]), self.config)
        self.assertGreaterEqual(h[0], h[2])
        self.assertEqual(h[0], h[1])

    def test_kernel_distance_decay(self):
        field = hm.kernel_field(np.array([[0.,0.],[20.,0.],[40.,0.],[1000.,0.]]),
                                np.array([[0.,0.]]), np.array([20.]))
        self.assertGreater(field[0], field[1])
        self.assertGreater(field[1], field[2])
        self.assertEqual(field[3], 0)
        self.assertAlmostEqual(field[0], 1 / (math.sqrt(2*math.pi)*20))

    def test_analysis_zones_do_not_move_with_display_resolution(self):
        # Include points near the fixed 200m analysis-zone edges.
        pois = copy.deepcopy(self.pois)
        for poi, xy in zip(pois, [[-199,-110],[-199,110],[210,-110],[210,110],[0,0],[10,10]]):
            poi['lng'], poi['lat'] = hm.to_lnglat(xy, self.center)
        bands = []
        for cell in (10, 20, 30):
            config = dict(self.config, cellMeters=cell)
            bands.append(hm.prepare_geometry(pois, '普陀', config)['bands'])
        self.assertEqual(bands[0], bands[1])
        self.assertEqual(bands[1], bands[2])

    def test_constant_map_is_zero_not_fake_hotspot(self):
        np.testing.assert_array_equal(hm.normalize(np.ones(4), np.ones(4, bool)), np.zeros(4))

    def test_mask_orientation_and_grounded_anchors(self):
        geometry = hm.prepare_geometry(self.pois, '普陀', self.config)
        payload = hm.build_map(geometry, 'walk', {'甲': .8, '乙': .3})
        values = np.array(payload['grid']['values'])
        self.assertEqual(len(values), geometry['width'] * geometry['height'])
        self.assertTrue(np.all(values[~geometry['mask']] == -1))
        self.assertTrue(np.all((values[geometry['mask']] >= 0) & (values[geometry['mask']] <= 1000)))
        self.assertGreater(geometry['grid'][0, 1], geometry['grid'][-1, 1])
        self.assertTrue(payload['places'])
        for place in payload['places']:
            source = next(p for p in self.pois if p['id'] == place['poi_id'])
            self.assertEqual((place['lng'], place['lat']), (source['lng'], source['lat']))
            self.assertFalse(place['access_verified'])

    def test_missing_class_and_zero_similarity(self):
        geometry = hm.prepare_geometry(self.pois, '普陀', self.config)
        field, _, _ = hm.fuse_fields(geometry, {'不存在': .9})
        self.assertTrue(np.isfinite(field).all())
        self.assertEqual(field.max(), 0)

    def test_scene_output_is_independent_of_other_scene_results(self):
        geometry = hm.prepare_geometry(self.pois, '普陀', self.config, scene='walk')
        first = hm.build_map(geometry, 'walk', {'甲': .8, '乙': .3})
        hm.build_map(geometry, 'date', {'甲': .2, '乙': .9})
        second = hm.build_map(geometry, 'walk', {'甲': .8, '乙': .3})
        self.assertEqual(first['grid']['values'], second['grid']['values'])
        self.assertEqual(first['places'], second['places'])
        self.assertEqual(first['sceneIndependence'], 'computed_without_other_scene_outputs')

    def test_scene_poi_weights_cannot_promote_a_named_location(self):
        pois = [
            {'category': 'scene', 'subCategory': '水岸', 'sceneWeight': 99},
            {'category': 'scene', 'subCategory': '步道', 'sceneWeight': 0.01},
        ]
        self.assertEqual(scene_class_weight_map(pois, 'date'), {'水岸': 1.0, '步道': 1.0})

    def test_photo_priors_use_measured_tree_size(self):
        templates = [
            {'name': '古树', 'habit': '乔木', 'blocks': []},
            {'name': '普通植物', 'habit': '草本', 'blocks': []},
        ]
        pois = [
            {'subCategory': '古树', 'meta': {'height': '18米', 'radius': '100厘米'}},
            {'subCategory': '古树', 'meta': {'height': '16米', 'radius': '90厘米'}},
        ]
        priors = hm.build_plant_priors(templates, pois)
        self.assertEqual(priors['古树']['mean_height'], 17.0)
        self.assertEqual(priors['古树']['mean_radius'], 95.0)
        weights = hm._seasonal_priors(['古树', '普通植物'], priors, 'spring', 'photo')
        self.assertGreater(weights['古树'], weights['普通植物'])

    def test_signed_mode_preserves_finite_results(self):
        self.config['negativeContribution'] = 'paper_signed'
        geometry = hm.prepare_geometry(self.pois, '普陀', self.config)
        field, _, betas = hm.fuse_fields(geometry, {'甲': .8, '乙': .3})
        self.assertTrue(any(beta < 0 for beta in betas.values()))
        self.assertTrue(np.isfinite(field).all())

    def test_cache_missing_stale_disabled_and_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'heatmap_config.json').write_text(json.dumps(self.config), encoding='utf-8')
            store = hm.HeatmapStore(root)
            with self.assertRaises(hm.HeatmapUnavailable):
                store.get('普陀', 'walk')
            store.cache.mkdir()
            (store.cache / 'manifest.json').write_text('{"sourceHash":"old"}', encoding='utf-8')
            with patch.object(hm, 'fingerprint', return_value='new'):
                with self.assertRaises(hm.HeatmapUnavailable):
                    store.get('普陀', 'walk')
            with patch.dict('os.environ', {'HEATMAP_ENABLED': 'false'}):
                with self.assertRaises(hm.HeatmapUnavailable):
                    store.get('普陀', 'walk')
            payload = hm.build_map(hm.prepare_geometry(self.pois, '普陀', self.config), 'walk', {})
            payload['sourceHash'] = 'old'
            (store.cache / 'putuo_walk.json').write_text(json.dumps(payload), encoding='utf-8')
            with patch.object(hm, 'fingerprint', return_value='old'):
                a = store.get('普陀', 'walk')
                a['places'].append('mutation')
                self.assertEqual(store.get('普陀', 'walk')['places'], [])
            with self.assertRaises(ValueError):
                store.get('../', 'walk')

    def test_missing_config_does_not_prevent_service_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            store = hm.HeatmapStore(Path(directory))
            with self.assertRaises(hm.HeatmapUnavailable):
                store.get('普陀', 'walk')

    def test_valid_json_with_broken_grid_or_wrong_campus_is_rejected(self):
        payload = hm.build_map(hm.prepare_geometry(self.pois, '普陀', self.config), 'walk', {'甲': .8})
        hm.validate_payload(payload, '普陀', 'walk')
        for mutation in ('grid', 'campus', 'missing', 'nan'):
            broken = copy.deepcopy(payload)
            if mutation == 'grid':
                broken['grid']['values'].pop()
            elif mutation == 'campus':
                broken['campus'] = '闵行'
            elif mutation == 'nan':
                broken['grid']['values'][0] = float('nan')
            else:
                del broken['places'][0]['reason']
            with self.assertRaises((ValueError, KeyError)):
                hm.validate_payload(broken, '普陀', 'walk')


class HeatmapApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server

    def setUp(self):
        self.client = self.server.app.test_client()
        # Explicit scene test queries need no model or network.
        self.semantic_patch = patch.object(self.server._SEMANTIC_RETRIEVER, 'scene_scores', return_value={})
        self.semantic_patch.start()
        self.fake = type('Response', (), {'raise_for_status': lambda s: None,
                        'json': lambda s: {'choices': [{'message': {'content': '原版测试回答'}}]}})()
        self.post_patch = patch.object(self.server.requests, 'post', return_value=self.fake)
        self.post = self.post_patch.start()

    def tearDown(self):
        self.semantic_patch.stop()
        self.post_patch.stop()

    def chat(self, query, **kwargs):
        return self.client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': query}], 'userCampus': '普陀', **kwargs})

    def test_new_mode_all_four_scenes_and_both_campuses(self):
        for campus in ('普陀', '闵行'):
            for word, scene in [('赏花','flower_viewing'), ('拍照','photo'), ('散步','walk'), ('约会','date')]:
                payload = {'scene': scene, 'sceneName': word, 'campus': campus, 'disclaimer': hm.DISCLAIMER,
                           'places': [{'name': '已有点位周边', 'rank': 1, 'reason': '真实点位依据'}]}
                with patch.object(self.server._HEATMAP_STORE, 'get', return_value=payload) as get:
                    response = self.chat(f'{campus}校区哪里适合{word}', recommendationMode='sakde')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json.get('recommendation_engine'), 'sakde', (campus, scene, response.json))
                self.assertEqual(response.json['locations'], response.json['ranked_places'])
                get.assert_called_once_with(campus, scene)
        self.post.assert_not_called()

    def test_original_default_never_reads_heatmap(self):
        with patch.object(self.server._HEATMAP_STORE, 'get', side_effect=AssertionError('old path changed')):
            response = self.chat('闵行校区哪里适合散步')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('heatmap', response.json)
        self.assertTrue(response.json['ranked_places'])

    def test_exact_parking_and_plant_queries_not_intercepted(self):
        with patch.object(self.server._HEATMAP_STORE, 'get', side_effect=AssertionError('exact path intercepted')):
            parking = self.chat('460停车场的准确地址', recommendationMode='sakde')
            plants = self.chat('银杏树在哪里', recommendationMode='sakde')
        self.assertEqual(len(parking.json['locations']), 1)
        self.assertEqual(parking.json['ranked_places'], [])
        self.assertNotIn('heatmap', plants.json)
        self.assertTrue(plants.json['locations'])

    def test_unsupported_never_calls_heatmap_or_llm(self):
        with patch.object(self.server._HEATMAP_STORE, 'get', side_effect=AssertionError('unsupported intercepted')):
            response = self.chat('哪里可以喝咖啡', recommendationMode='sakde')
        self.assertTrue(response.json['unsupported'])
        self.assertEqual(response.json['locations'], [])
        self.post.assert_not_called()

    def test_cache_unavailable_falls_back_and_reports_reason(self):
        with patch.object(self.server._HEATMAP_STORE, 'get', side_effect=hm.HeatmapUnavailable('缓存不可用')):
            response = self.chat('哪里适合散步', recommendationMode='sakde')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['recommendation_engine'], 'rule')
        self.assertEqual(response.json['heatmap_status'], '缓存不可用')

    def test_http_validation_and_missing_cache(self):
        self.assertEqual(self.client.get('/api/heatmaps?campus=bad&scene=walk').status_code, 400)
        with patch.object(self.server._HEATMAP_STORE, 'get', side_effect=hm.HeatmapUnavailable('未生成')):
            self.assertEqual(self.client.get('/api/heatmaps?campus=普陀&scene=walk').status_code, 503)


if __name__ == '__main__':
    unittest.main(verbosity=2)
