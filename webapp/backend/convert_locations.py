"""
预处理脚本：将 all_trees.json 中每棵树的经纬度通过高德 POI 周边搜索转成建筑名，
按植物名聚合后保存到 tree_locations.json。只需运行一次。
"""
import json, time, requests, os, sys
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, '../../.env'))
AMAP_KEY = os.getenv('AMAP_WEB_SERVICE_KEY', '')

with open(os.path.join(BASE, '../../data/all_trees.json'), encoding='utf-8') as f:
    trees = json.load(f)

# 按植物名聚合坐标，每种植物最多取 15 个坐标点
plant_coords = {}
for t in trees:
    name = (t.get('template') or {}).get('name')
    lng, lat = t.get('longitude'), t.get('latitude')
    if name and lng and lat:
        plant_coords.setdefault(name, [])
        if len(plant_coords[name]) < 15:
            plant_coords[name].append((lng, lat))

def get_nearest_building(lng, lat):
    """用高德 POI 周边搜索，返回最近的校园建筑名。"""
    try:
        resp = requests.get(
            'https://restapi.amap.com/v3/place/around',
            params={
                'key': AMAP_KEY,
                'location': f'{lng},{lat}',
                'types': '120000|090000|141200|120201',
                'radius': 300,
                'sortrule': 'distance',
                'offset': 1,
            },
            timeout=5
        )
        pois = resp.json().get('pois', [])
        if pois:
            # 去掉"华东师范大学闵行校区"/"华东师范大学普陀校区"前缀，保留具体建筑名
            name = pois[0]['name']
            for prefix in ['华东师范大学闵行校区', '华东师范大学普陀校区', '华东师范大学']:
                name = name.replace(prefix, '').strip()
            return name or pois[0]['name']
    except Exception:
        pass
    return None

result = {}
total = sum(len(v) for v in plant_coords.values())
done = 0

for plant_name, coords in plant_coords.items():
    locs = []
    seen = set()
    for lng, lat in coords:
        desc = get_nearest_building(lng, lat)
        if desc and desc not in seen:
            seen.add(desc)
            locs.append(desc)
        done += 1
        time.sleep(0.06)
    if locs:
        result[plant_name] = locs
    print(f'[{done}/{total}] {plant_name}: {locs[:3]}')

out_path = os.path.join(BASE, 'tree_locations.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f'\n完成！已保存到 {out_path}，共 {len(result)} 种植物')
