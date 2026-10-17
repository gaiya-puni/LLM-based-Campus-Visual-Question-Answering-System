
"""Campus data loading: POI files, plant templates, tree/college locations, emotions.

All datasets are loaded exactly once at import time and then treated as read-only.
Paths come from `paths`, so this module must not compute its own `__file__` offsets.
"""

import json
import os

import campus_config
import paths  # noqa: F401  (sys.path bootstrap + .env loading)
from paths import BASE as _BASE
from scene_heatmaps import HeatmapStore, register_heatmap_routes
from semantic_retrieval import SemanticRetriever
from flask_app import app
from tools.campus_generator.publish import formal_assets_guard

def _campus(lng, lat):
    return campus_config.campus_of(lng, lat)


POI_REQUIRED_FIELDS = (
    'id', 'category', 'subCategory', 'name', 'locationName',
    'lng', 'lat', 'text', 'tags'
)


# 加载植物模板数据（一次性加载到内存）
_HEATMAP_STORE = HeatmapStore(_BASE)
register_heatmap_routes(app, _HEATMAP_STORE)
_SEMANTIC_RETRIEVER = SemanticRetriever(_BASE)
with open(os.path.join(_BASE, '../../data/all_templates.json'), encoding='utf-8') as f:
    _PLANT_TEMPLATES = json.load(f)

# 加载预处理好的植物位置数据（由 convert_locations.py 生成）
with open(os.path.join(_BASE, 'tree_locations.json'), encoding='utf-8') as f:
    _TREE_LOCATIONS = json.load(f)

with open(os.path.join(_BASE, 'college_locations.json'), encoding='utf-8') as f:
    _COLLEGE_LOCATIONS = json.load(f)

# 加载 all_trees.json，按植物名聚合精确位置（number 字段）和经纬度
with open(os.path.join(_BASE, '../../data/all_trees.json'), encoding='utf-8') as f:
    _all_trees_raw = json.load(f)

_PLANT_TEMPLATE_BY_NAME = {p.get('name'): p for p in _PLANT_TEMPLATES if p.get('name')}
_TREE_PRECISE_LOCATIONS = {}  # {植物名: [精确位置描述, ...]}
_TREE_COORDS = {}             # {植物名: [{lng, lat, number}, ...]}
_TREE_POINTS = []             # 每棵植物的完整坐标与文本信息，用于规则评分推荐地点
_TREE_BY_ID = {}              # {树 ID: 精简植物与认养信息}，供寄语页按 id 关联"这棵树是什么植物"
for _t in _all_trees_raw:
    _name = (_t.get('template') or {}).get('name')
    _num = (_t.get('number') or '').strip()
    _lng, _lat = _t.get('longitude'), _t.get('latitude')
    if _name and _num:
        _TREE_PRECISE_LOCATIONS.setdefault(_name, [])
        if _num not in _TREE_PRECISE_LOCATIONS[_name]:
            _TREE_PRECISE_LOCATIONS[_name].append(_num)
    if _name and _lng and _lat:
        _TREE_COORDS.setdefault(_name, [])
        _TREE_COORDS[_name].append({'lng': _lng, 'lat': _lat, 'number': _num})
        _template = _PLANT_TEMPLATE_BY_NAME.get(_name, (_t.get('template') or {}))
        _owner = _t.get('owner') or {}
        _TREE_POINTS.append({
            'name': _name,
            'template': _template,
            'lng': _lng,
            'lat': _lat,
            'number': _num,
            'place': _t.get('place', ''),
            'likeCount': _t.get('likeCount') or 0,
            'owner_title': _owner.get('title', ''),
            'owner_slogan': _owner.get('slogan', ''),
            'owner_content': _owner.get('content', ''),
        })
    if _t.get('id') is not None:
        _tree_template = _t.get('template') or {}
        _tree_owner = _t.get('owner') or {}
        _TREE_BY_ID[str(_t.get('id'))] = {
            'plantName': _tree_template.get('name') or '',
            'scientificName': _tree_template.get('scientificName') or '',
            'branch': _tree_template.get('branch') or '',
            'habit': _tree_template.get('habit') or '',
            'icon': _tree_template.get('icon') or '',
            # location = 树自身的编号或位置描述（number 字段）；
            # place    = 原始数据的地点分组，粒度可能是"华东师大中山北路校区数学馆东北(友谊路)"，
            #            不能直接当校区用，要归并后才谈得上"几个校区"。
            'location': _num,
            'place': _t.get('place') or '',
            'lng': _lng,
            'lat': _lat,
            'likeCount': _t.get('likeCount') or 0,
            'plantedAt': _t.get('createdAt') or '',
            'ownerTitle': _tree_owner.get('title') or '',
        }
del _all_trees_raw

# 加载仓库内的完整植树留言数据。
_EMOTION_FILE = os.path.join(_BASE, '../frontend/src/assets/emotion_analysis.json')
try:
    with open(_EMOTION_FILE, encoding='utf-8') as f:
        _EMOTIONS = json.load(f)
    if not isinstance(_EMOTIONS, list):
        raise ValueError('emotion data must be a list')
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f'Unable to load emotion data: {exc}')
    _EMOTIONS = []

# 寄语页要展示"这位寄语者认养的是哪棵植物"，按 id 关联 all_trees.json 的植物信息。
# 只保留寄语实际涉及的那些 id（153 条量级），不把 2976 条全量树木数据推给前端。
_EMOTION_PLANTS = {
    str(_e.get('id')): _TREE_BY_ID[str(_e.get('id'))]
    for _e in _EMOTIONS
    if str(_e.get('id')) in _TREE_BY_ID
}

# 校园植物总览：寄语页用它说明"校园里一共有多少种植物"，与"被认养的只有 2 种"形成对照。
_PLANT_STATS = {
    'species': len(_PLANT_TEMPLATE_BY_NAME),   # 植物模板库里的种类数（193）
    'trees': len(_TREE_BY_ID),                 # 已登记树木总数（2976）
}


def _discover_poi_data_files() -> tuple:
    """自动加载 backend 下的统一 POI 数据文件，便于后续扩展咖啡、停车等类别。"""
    discovered = sorted(
        filename for filename in os.listdir(_BASE)
        if filename.endswith('_pois.json') and os.path.isfile(os.path.join(_BASE, filename))
    )
    return tuple(dict.fromkeys(['campus_pois.json'] + discovered))


POI_DATA_FILES = _discover_poi_data_files()

POI_SEARCH_META_FIELDS = (
    'collegeName', 'buildingName', 'address', 'branch', 'habit',
    'scientificName', 'number', 'place', 'ownerTitle', 'ownerSlogan',
    'ownerContent', 'amapName', 'amapType', 'sourceQuery'
)

DEFAULT_POI_CATEGORY_CONFIG = {
    'groupBy': 'location',
    'tracksPlants': False,
    'directLookup': False,
    'nearbyCollegeDistance': False,
    'intentPattern': '',
    'exactMetaFields': ('aliases', 'amapName', 'sourceQuery'),
    'exactMatchMinLength': 3,
    'exactMatchScore': 64,
    'exactMatchReason': '命中地点名称',
    'genericIntentScore': 0,
    'genericIntentReason': '匹配地点类别',
    'targetMatcher': 'exact',
    'scoreCap': 90,
    'exactScoreCap': 90,
    'dataReason': '校园位置数据',
    'contextMode': 'location',
    'contextName': '地点',
    'fallbackDensityScore': 0,
    'fallbackDensityReason': '按当前校区点位密度推荐',
}

# 新增 POI 类别时优先扩展这里；场景推荐可只配 scene_profiles，精确直达查询再开启 directLookup。
POI_CATEGORY_CONFIGS = {
    'plant': {
        'tracksPlants': True,
        'nearbyCollegeDistance': True,
        'intentPattern': r'植物|树|花|草|竹|松|梅|樱|桂|荷|叶|乔木|灌木',
        'exactMatchMinLength': 2,
        'nameMatchScore': 42,
        'targetMatchScore': 46,
        'subCategoryMatchScore': 38,
        'targetMatcher': 'plant',
        'scoreCap': 90,
        'dataReason': '校园植物位置数据',
        'contextMode': 'plants',
        'contextName': '植物',
        'fallbackDensityScore': 10,
    },
    'college': {
        'groupBy': 'id',
        'exactMatchScore': 76,
        'exactMatchReason': '命中学院/楼宇名称',
        'targetMatcher': 'college',
        'scoreCap': 90,
        'dataReason': '学院机构位置数据',
        'contextMode': 'location',
        'contextName': '学院/楼宇',
        'requiresTargetOrScene': True,
    },
    'canteen': {
        'groupBy': 'id',
        'directLookup': True,
        'nearbyCollegeDistance': True,
        'intentPattern': r'食堂|饭堂|餐厅|饭店|餐饮|就餐|用餐|吃饭|吃点|早饭|午饭|晚饭|夜宵|西餐',
        'exactMatchScore': 82,
        'exactMatchReason': '命中食堂/餐厅名称',
        'genericIntentScore': 40,
        'genericIntentReason': '校园餐饮地点',
        'targetMatcher': 'exact',
        'scoreCap': 70,
        'exactScoreCap': 100,
        'dataReason': '校园餐饮位置数据',
        'contextMode': 'location',
        'contextName': '食堂/餐厅',
    },
    'parking': {
        'groupBy': 'id',
        'directLookup': True,
        'intentPattern': r'停车|停车场|车位|停车位|泊车|开车|自驾',
        'exactMatchScore': 82,
        'exactMatchReason': '命中停车地点名称',
        'genericIntentScore': 40,
        'genericIntentReason': '校园停车地点',
        'targetMatcher': 'exact',
        'scoreCap': 75,
        'exactScoreCap': 100,
        'dataReason': '校园停车位置数据',
        'contextMode': 'location',
        'contextName': '停车场',
    },
    'scene': {
        'groupBy': 'id',
        'directLookup': True,
        # 避免“图书馆在哪里”这类泛称因去掉学校前缀后命中所有校区；
        # “包玉刚图书馆”等有辨识度的专名仍可精确直达。
        'exactMatchMinLength': 4,
        'exactMatchScore': 82,
        'exactMatchReason': '命中校园地点名称',
        'targetMatcher': 'exact',
        'scoreCap': 75,
        'exactScoreCap': 100,
        'dataReason': '校园地点位置数据',
        'contextMode': 'location',
        'contextName': '校园地点',
    },
}


def _load_campus_pois() -> list:
    """加载并校验统一 POI 数据，非法记录跳过，避免影响问答服务启动。"""
    raw_pois = []
    for filename in POI_DATA_FILES:
        path = os.path.join(_BASE, filename)
        if not os.path.exists(path):
            if filename == 'campus_pois.json':
                print('campus_pois.json not found, unified POI ranking disabled')
            else:
                print(f'{filename} not found, skipping optional POI data')
            continue

        with open(path, encoding='utf-8') as f:
            loaded = json.load(f)
        if not isinstance(loaded, list):
            print(f'{filename} is not a list, skipping this POI file')
            continue
        raw_pois.extend((filename, index, poi) for index, poi in enumerate(loaded))

    if not raw_pois:
        return []

    valid_pois = []
    errors = []
    category_counts = {}
    seen_ids = set()
    for filename, index, poi in raw_pois:
        if not isinstance(poi, dict):
            errors.append(f'{filename}#{index}: not an object')
            continue

        missing = [
            field for field in POI_REQUIRED_FIELDS
            if poi.get(field) in (None, '') or (field == 'tags' and not isinstance(poi.get(field), list))
        ]
        try:
            lng = float(poi.get('lng'))
            lat = float(poi.get('lat'))
        except (TypeError, ValueError):
            missing.extend(['lng', 'lat'])

        text = str(poi.get('text') or '').strip()
        if not text:
            missing.append('text')

        if missing:
            errors.append(f"{filename}#{index} {poi.get('id', '<no id>')}: missing/invalid {sorted(set(missing))}")
            continue

        poi_id = poi.get('id')
        if poi_id in seen_ids:
            errors.append(f"{filename}#{index} {poi_id}: duplicate id")
            continue

        seen_ids.add(poi_id)
        normalized = dict(poi)
        normalized['lng'] = lng
        normalized['lat'] = lat
        normalized['campus'] = normalized.get('campus') or _campus(lng, lat)
        normalized['tags'] = [str(tag) for tag in normalized.get('tags', []) if tag]
        valid_pois.append(normalized)
        category = normalized.get('category', 'unknown')
        category_counts[category] = category_counts.get(category, 0) + 1

    if errors:
        print(f"campus_pois validation skipped {len(errors)} invalid records; examples: {errors[:5]}")
    print(f"campus_pois loaded {len(valid_pois)}/{len(raw_pois)} records; categories={category_counts}")
    return valid_pois


with formal_assets_guard():
    # Recovery and both formal POI reads happen under the same lock, so startup
    # can only expose the complete before or after state of a publication.
    POI_DATA_FILES = _discover_poi_data_files()
    _CAMPUS_POIS = _load_campus_pois()


def _string_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def _weight_map(value) -> dict:
    if not isinstance(value, dict):
        return {}
    weights = {}
    for key, weight in value.items():
        if not key:
            continue
        try:
            weights[str(key)] = float(weight)
        except (TypeError, ValueError):
            continue
    return weights


def _load_scene_profiles() -> list:
    """加载轻量场景推荐配置；新增场景优先改 JSON，少改 Python 逻辑。"""
    path = os.path.join(_BASE, 'scene_profiles.json')
    if not os.path.exists(path):
        print('scene_profiles.json not found, scene profile ranking disabled')
        return []
    try:
        with open(path, encoding='utf-8') as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'scene_profiles.json load failed: {exc}')
        return []
    if not isinstance(loaded, list):
        print('scene_profiles.json is not a list, scene profile ranking disabled')
        return []

    profiles = []
    errors = []
    for index, profile in enumerate(loaded):
        if not isinstance(profile, dict):
            errors.append(f'#{index}: not an object')
            continue
        if not profile.get('id') or not profile.get('name'):
            errors.append(f'#{index}: missing id/name')
            continue

        normalized = dict(profile)
        normalized['intentKeywords'] = _string_list(profile.get('intentKeywords'))
        normalized['categories'] = _string_list(profile.get('categories'))
        normalized['terms'] = _string_list(profile.get('terms'))
        normalized['contextTexts'] = _string_list(profile.get('contextTexts'))
        normalized['positiveQueries'] = _string_list(profile.get('positiveQueries'))
        normalized['negativeQueries'] = _string_list(profile.get('negativeQueries'))
        normalized['placeTerms'] = _string_list(
            profile.get('placeTerms') or profile.get('place_terms')
        )
        for key in (
            'categoryWeights',
            'subCategoryWeights',
            'tagWeights',
            'textWeights',
            'placeTermWeights'
        ):
            normalized[key] = _weight_map(profile.get(key))
        profiles.append(normalized)

    if errors:
        print(f'scene_profiles validation skipped {len(errors)} invalid records; examples: {errors[:5]}')
    print(f'scene_profiles loaded {len(profiles)}/{len(loaded)} records')
    return profiles


_SCENE_PROFILES = _load_scene_profiles()

_SCENE_RULES = [
    {
        'id': profile.get('id'),
        'name': profile.get('name'),
        'keywords': profile.get('intentKeywords', []),
        'terms': profile.get('terms', []),
        'place_terms': profile.get('placeTerms', [])
    }
    for profile in _SCENE_PROFILES
]

_QUERY_STOP_TOKENS = {
    '学校', '校园', '华东', '师范', '大学', '校区', '哪里', '在哪', '位置',
    '地点', '附近', '周边', '周围', '旁边', '有哪些', '有什么', '推荐',
    '适合', '一下', '一个', '两个', '地图', '显示', '植物', '地方'
} | {word.casefold() for word in (
    campus_config.school_names() + campus_config.school_fragments())}

_SEMANTIC_STOP_TOKENS = _QUERY_STOP_TOKENS | {
    '我想', '想找', '找个', '可以', '请问', '目前', '现在', '一下',
    '有没有', '能不能', '哪里有', '哪里能', '适不适合'
}

_SEMANTIC_SCENE_MATCH_THRESHOLD = 0.18
_SEMANTIC_POI_MATCH_THRESHOLD = 0.12
_SEMANTIC_POI_SCORE_SCALE = 28

_SEMANTIC_SYNONYM_GROUPS = (
    (
        ('吃饭', '用餐', '就餐', '吃点', '吃点东西', '简单吃点', '解决一餐',
         '填饱', '饭点', '饿了', '干饭', '一餐', '早饭', '午饭', '晚饭', '夜宵'),
        ('食堂', '饭堂', '餐厅', '餐饮', '就餐', '用餐', '吃饭')
    ),
    (
        ('拍照', '拍点照片', '照片', '摄影', '打卡', '出片', '取景', '留影',
         '好看', '漂亮', '风景', '景色'),
        ('拍照', '摄影', '打卡', '出片', '观赏', '风景', '景色', '好看')
    ),
    (
        ('赏花', '看花', '花海', '花期', '开花', '春天', '春游',
         '樱花', '梅花', '桂花', '荷花'),
        ('赏花', '看花', '花期', '开花', '观赏', '樱花', '梅花', '桂花', '荷花')
    ),
    (
        ('散步', '走走', '逛逛', '转转', '休息', '坐坐', '坐一会',
         '坐一会儿', '歇会', '乘凉', '放松', '安静', '清静', '休闲'),
        ('散步', '休闲', '放松', '安静', '乘凉', '草坪', '树荫', '河畔')
    ),
    (
        ('学习', '自习', '看书', '科普', '认识植物', '植物知识', '介绍植物', '了解植物'),
        ('学习', '自习', '科普', '植物知识', '学院', '教学', '图书馆')
    ),
    (
        ('情侣', '约会', '浪漫', '表白', '对象', '两个人', '一起走'),
        ('情侣', '约会', '浪漫', '安静', '草坪', '河畔', '花园')
    ),
)
