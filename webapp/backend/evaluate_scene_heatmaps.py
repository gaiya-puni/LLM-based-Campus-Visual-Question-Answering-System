"""Compare rule, fixed KDE and SAKDE. No invented human accuracy scores.

sakde-campus-3 起散步/约会各自拥有独立的点位集合（scene_pois.json），凸包掩膜与栅格
范围可能与其他场景不同，因此场景对比不再假设共用一个栅格：先把每张图重采样到同一
20 米格网上，只在**两图都有效**的公共格上计算相关系数与平均绝对差，另外单独报告
覆盖重叠度（coverageJaccard），避免用缺失值当差异虚增区分度。
"""
import argparse
import copy
import json
import time
from itertools import combinations
from pathlib import Path

import numpy as np

from build_scene_heatmaps import load_pois, pois_for_scene, write_json
from scene_heatmaps import (HeatmapStore, audit_pois, build_plant_priors, fuse_fields,
                           hotspot_places, kernel_field, load_config, normalize,
                           prepare_geometry, to_meters)

BASE = Path(__file__).resolve().parent


def _raster_view(data, center):
    """把缓存栅格转成 1 维数组 + 米制原点，便于重采样到公共格网。"""
    grid = data['grid']
    raw = np.array(grid['values'], dtype=float)
    values = raw / float(grid['maxValue'])
    values[raw == grid['noData']] = np.nan
    west, south = grid['bounds'][0], grid['bounds'][1]
    x0, y0 = to_meters([[west, south]], center)[0]
    # y 轴：栅格第 0 行在最北，上边界 = 南边界 + 行数 * 格宽
    y_top = y0 + grid['height'] * grid['cellMeters']
    return dict(values=values, x0=x0, y_top=y_top, width=grid['width'], height=grid['height'])


def _sample(view, cell, xs, ys):
    """把某场景栅格重采样到给定格点；越界或缺失记为 NaN。"""
    i = np.floor((xs - view['x0']) / cell).astype(int)
    j = np.floor((view['y_top'] - ys) / cell).astype(int)
    inside = (i >= 0) & (i < view['width']) & (j >= 0) & (j < view['height'])
    out = np.full(xs.shape, np.nan)
    flat = np.flatnonzero(inside)
    if flat.size:
        out[flat] = view['values'][j[flat] * view['width'] + i[flat]]
    return out


def _fixed_kde(geometry, point_count, config):
    """同点集、统一带宽、等权的纯 KDE 对照（不使用语义与先验）。"""
    contributions = {}
    for name, indexes in geometry['groups'].items():
        points = geometry['xy'][indexes]
        contributions[name] = kernel_field(
            geometry['grid'], points, np.full(len(points), config['fixedBandwidthMeters'])
        ) * len(points) / max(1, point_count)
    return normalize(sum(contributions.values()), geometry['mask']), contributions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, help='human annotations: [{campus, scene, acceptablePoiIds}]')
    parser.add_argument('--sensitivity', action='store_true', help='also compare 10/20/30m grids')
    args = parser.parse_args()
    import server
    config = load_config(BASE)
    store = HeatmapStore(BASE)
    # 植物 + 场景专属 POI 一起参与审计，与构建脚本保持一致。
    pois, audit = audit_pois(load_pois(), config)
    similarities = json.loads((store.cache / 'class_similarities.json').read_text(encoding='utf-8'))
    report = {'audit': audit, 'cases': [], 'sceneComparisons': [],
              'qualityNote': '无人工标注时，仅报告技术对照；不能据此宣称SAKDE推荐更准确。'}
    labels = json.loads(args.labels.read_text(encoding='utf-8')) if args.labels else []
    cell = config['cellMeters']
    for campus in config['campuses']:
        center = config['campuses'][campus]['center']
        views, fields = {}, {}
        for scene, display_name in config['scenes'].items():
            scene_pois = pois_for_scene(pois, scene)
            geometry = prepare_geometry(scene_pois, campus, config, scene=scene)
            fixed, fixed_contributions = _fixed_kde(geometry, len(scene_pois), config)
            fixed_places = hotspot_places(geometry, fixed, fixed_contributions)
            started = time.perf_counter()
            data = store.get(campus, scene)
            elapsed = time.perf_counter() - started
            if data['sourceHash'] != similarities['sourceHash']:
                raise RuntimeError('evaluation similarities/cache mismatch; rebuild heatmaps')
            view = _raster_view(data, center)
            if view['width'] * view['height'] != len(geometry['mask']):
                raise RuntimeError(
                    f'{campus}/{scene}: cached grid size differs from the rebuilt geometry; '
                    'run build_scene_heatmaps.py again')
            views[scene] = view
            fields[scene] = np.nan_to_num(view['values'][geometry['mask']], nan=0.0)
            # 图点一致性：Top1 标点必须落在真正的热区峰值上，否则就是"标点不在最热处"。
            masked = np.where(geometry['mask'], view['values'], -1.0)
            peak_row, peak_col = divmod(int(np.argmax(masked)), view['width'])
            peak_xy = np.array([view['x0'] + (peak_col + 0.5) * cell,
                                view['y_top'] - (peak_row + 0.5) * cell])
            top1 = data['places'][0] if data['places'] else None
            peak_gap = None
            if top1:
                anchor_xy = to_meters([[top1['lng'], top1['lat']]], center)[0]
                peak_gap = round(float(np.linalg.norm(peak_xy - anchor_xy)), 1)
            query = f'{campus}校区哪里适合{display_name}'
            started = time.perf_counter()
            rules = server.rank_campus_places(query, server.search_plants(query), server.search_colleges(query),
                                              preferred_campus=campus)
            rule_seconds = time.perf_counter() - started
            case = {'campus': campus, 'scene': scene, 'query': query,
                    'scenePointCount': len(scene_pois), 'gridWidth': view['width'], 'gridHeight': view['height'],
                    'sakdeReadMs': round(elapsed * 1000, 2), 'ruleRankMs': round(rule_seconds * 1000, 2),
                    'gridCells': view['width'] * view['height'], 'supportedCells': int(geometry['mask'].sum()),
                    'sakdeVsFixedKdeMAE': round(float(np.abs(fields[scene] - fixed[geometry['mask']]).mean()), 5),
                    'top1Score': top1['score'] if top1 else None,
                    'peakToTop1Meters': peak_gap,
                    'sakde': [{'name': p['name'], 'poiId': p['poi_id'], 'score': p['score']} for p in data['places']],
                    'fixedKde': [{'name': p['name'], 'poiId': p['poi_id']} for p in fixed_places],
                    'rule': [{'name': p['name'], 'lng': p['lng'], 'lat': p['lat']} for p in rules]}
            annotation = next((x for x in labels if x['campus'] == campus and x['scene'] == scene), None)
            if annotation and annotation.get('acceptablePoiIds'):
                expected = set(annotation['acceptablePoiIds'])
                case['humanLabelHitAt2'] = {
                    'sakde': bool(expected & {p['poi_id'] for p in data['places'][:2]}),
                    'fixedKde': bool(expected & {p['poi_id'] for p in fixed_places[:2]})}
            report['cases'].append(case)
        # 场景对比：重采样到公共格网，只在两图都有效的格上比较。
        x_min = min(view['x0'] for view in views.values())
        y_min = min(view['y_top'] - view['height'] * cell for view in views.values())
        x_max = max(view['x0'] + view['width'] * cell for view in views.values())
        y_max = max(view['y_top'] for view in views.values())
        width = int(round((x_max - x_min) / cell))
        height = int(round((y_max - y_min) / cell))
        xs, ys = np.meshgrid(x_min + (np.arange(width) + 0.5) * cell,
                             y_max - (np.arange(height) + 0.5) * cell)
        xs, ys = xs.ravel(), ys.ravel()
        sampled = {scene: _sample(view, cell, xs, ys) for scene, view in views.items()}
        for left, right in combinations(sampled, 2):
            a, b = sampled[left], sampled[right]
            both = ~np.isnan(a) & ~np.isnan(b)
            either = ~np.isnan(a) | ~np.isnan(b)
            paired_a, paired_b = a[both], b[both]
            correlation = None
            if paired_a.size > 1 and paired_a.std() and paired_b.std():
                correlation = round(float(np.corrcoef(paired_a, paired_b)[0, 1]), 5)
            report['sceneComparisons'].append({
                'campus': campus, 'scenes': [left, right],
                'correlation': correlation,
                'commonCells': int(both.sum()),
                'coverageJaccard': round(float(both.sum()) / max(1, int(either.sum())), 5),
                'meanAbsoluteDifference': round(float(np.abs(paired_a - paired_b).mean()), 5)
                if both.any() else None,
            })
    if args.sensitivity:
        report['gridSensitivity'] = []
        templates = json.loads((BASE / '../../data/all_templates.json').read_text(encoding='utf-8'))
        plant_priors = build_plant_priors(templates, pois)
        walk_pois = pois_for_scene(pois, 'walk')
        for cell_meters in [10, 20, 30]:
            for campus in config['campuses']:
                varied = copy.deepcopy(config)
                varied['cellMeters'] = cell_meters
                started = time.perf_counter()
                geometry = prepare_geometry(walk_pois, campus, varied, scene='walk')
                field, contributions, _ = fuse_fields(geometry, similarities['scores']['walk'],
                                                      plant_priors, None, 'walk')
                report['gridSensitivity'].append({'campus': campus, 'cellMeters': cell_meters,
                    'seconds': round(time.perf_counter() - started, 3),
                    'places': [p['name'] for p in hotspot_places(geometry, field, contributions)]})
    write_json(store.cache / 'evaluation.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
