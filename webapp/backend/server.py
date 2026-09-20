from collections import defaultdict, deque
from functools import lru_cache, wraps
from time import monotonic

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import pymysql
import json
import math
import os
import requests
import re
import threading

from semantic_retrieval import SemanticRetriever
from scene_heatmaps import SCENE_IDS, HeatmapStore, HeatmapUnavailable, heatmap_chat_response, register_heatmap_routes

_BASE = os.path.dirname(__file__)
load_dotenv(os.path.join(_BASE, '../../.env'))
load_dotenv(os.path.join(_BASE, '.env'))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or os.urandom(32),
    MAX_CONTENT_LENGTH=int(os.getenv('MAX_CONTENT_LENGTH', str(1024 * 1024))),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
)

allowed_origins = [
    origin.strip() for origin in os.getenv('ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]
if allowed_origins:
    CORS(
        app,
        resources={r'/api/*': {'origins': allowed_origins}},
        supports_credentials=True,
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
del _all_trees_raw

# 加载植树留言数据
with open(os.path.join(_BASE, '../frontend/src/assets/emotion_analysis.json'), encoding='utf-8') as f:
    _EMOTIONS = json.load(f)

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_URL = os.getenv('DEEPSEEK_API_URL', 'https://api.deepseek.com/v1/chat/completions')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')


DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DATABASE', 'test'),
    'charset': os.getenv('MYSQL_CHARSET', 'utf8mb4'),
}

# 普陀(中北)中心: 31.2271, 121.4061 | 闵行中心: 31.0315, 121.4537
def _campus(lng, lat):
    d_pt = (lng - 121.406079) ** 2 + (lat - 31.227073) ** 2
    d_mh = (lng - 121.453725) ** 2 + (lat - 31.03148) ** 2
    return '普陀' if d_pt < d_mh else '闵行'


POI_REQUIRED_FIELDS = (
    'id', 'category', 'subCategory', 'name', 'locationName',
    'lng', 'lat', 'text', 'tags'
)


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


_CAMPUS_POIS = _load_campus_pois()


def _poi_category_config(category: str) -> dict:
    config = dict(DEFAULT_POI_CATEGORY_CONFIG)
    config.update(POI_CATEGORY_CONFIGS.get(category or '', {}))
    return config


def _meta_text_values(meta: dict, fields) -> list:
    values = []
    for field in fields or []:
        value = (meta or {}).get(field)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    return [str(value) for value in values if value]


def _poi_exact_match_texts(poi: dict) -> list:
    config = _poi_category_config(poi.get('category'))
    meta = poi.get('meta') or {}
    texts = [
        poi.get('name', ''),
        poi.get('locationName', ''),
    ]
    texts.extend(_meta_text_values(meta, config.get('exactMetaFields')))
    return list(dict.fromkeys(str(text).strip() for text in texts if str(text).strip()))


_LOOKUP_FILLER_PATTERN = re.compile(
    r'华东师范大学|华师大|普陀校区|中北校区|中山北路校区|闵行校区|'
    r'请问|麻烦问下|帮我找找|具体|准确|地址|位置|地点|在哪里|在哪儿|在哪|怎么走|如何去|'
    r'[\s，。！？、,.!?：:；;（）()【】\[\]“”"\'`]'
)


def _normalize_poi_lookup_text(value: str) -> str:
    """Normalize direct-place queries without discarding identifying numbers.

    Numeric gate names commonly appear both as ``460号门停车场`` and
    ``460停车场``.  Only this numeric form is collapsed; directional gates
    such as 东门/西门 remain distinct.
    """
    normalized = _LOOKUP_FILLER_PATTERN.sub('', (value or '').lower())
    normalized = re.sub(r'(\d+)号门(?=停车场|停车|入口|$)', r'\1', normalized)
    return normalized


def _normalized_lookup_match(text: str, query: str, min_length: int) -> bool:
    text_normalized = _normalize_poi_lookup_text(text)
    query_normalized = _normalize_poi_lookup_text(query)
    if len(text_normalized) < min_length or not query_normalized:
        return False
    return text_normalized in query_normalized


def _poi_exact_matches_query(poi: dict, query: str) -> list:
    config = _poi_category_config(poi.get('category'))
    min_length = int(config.get('exactMatchMinLength') or 2)
    matched = [
        text for text in _poi_exact_match_texts(poi)
        if len(text) >= min_length and (
            text in (query or '') or _normalized_lookup_match(text, query, min_length)
        )
    ]
    return sorted(matched, key=len, reverse=True)


def _category_has_intent(query: str, category: str) -> bool:
    query = query or ''
    config = _poi_category_config(category)
    pattern = config.get('intentPattern')
    if pattern and re.search(pattern, query):
        return True
    if config.get('directLookup'):
        return any(
            _poi_exact_matches_query(poi, query)
            for poi in _CAMPUS_POIS
            if poi.get('category') == category
        )
    return False


@lru_cache(maxsize=256)
def _intent_categories_from_query(query: str) -> list:
    # 同一轮问答里该函数会被 _classify_poi_query、_should_rank_places 等重复调用，
    # 而食堂/停车类别每次都要全量扫描 3000+ 条 POI。结果只取决于 query 与启动时
    # 固定的 POI 配置，因此按 query 缓存；调用方需自行复制后再修改。
    return [
        category for category in POI_CATEGORY_CONFIGS
        if _category_has_intent(query, category)
    ]


def _category_group_key(poi: dict):
    category = poi.get('category')
    config = _poi_category_config(category)
    if config.get('groupBy') == 'id':
        return category, poi.get('id')
    return (
        category,
        poi.get('campus'),
        poi.get('locationName') or f"{round(poi['lng'], 5)},{round(poi['lat'], 5)}"
    )


def _format_poi_number(poi: dict, location_name: str = '') -> str:
    meta = poi.get('meta') or {}
    address = meta.get('address') or ''
    label = location_name or poi.get('locationName') or '暂无具体位置描述'
    return f"{label}｜{address}" if address else label


def search_plants(query: str, top_k: int = 8) -> list:
    """在植物模板中检索，支持模糊匹配并按相关度排序，返回最多 top_k 条结果。"""
    query_clean = re.sub(r'[^\w一-鿿]', ' ', query)
    # 空格分词作为"完整词"（可做双向匹配）
    full_tokens = set(kw for kw in query_clean.split() if kw)
    # 2~4字滑窗子串（只做正向匹配 kw in name，避免误匹配）
    sub_tokens = set()
    for i in range(len(query_clean)):
        for l in (2, 3, 4):
            sub = query_clean[i:i+l].strip()
            if len(sub) >= 2 and re.search(r'[一-鿿]', sub):
                sub_tokens.add(sub)
    if not full_tokens and not sub_tokens:
        return []

    scored = []
    for plant in _PLANT_TEMPLATES:
        name = plant.get('name', '')
        name2 = plant.get('name2', '') or ''
        sci = plant.get('scientificName', '') or ''
        branch = plant.get('branch', '') or ''
        habit = plant.get('habit', '') or ''
        block_text = ' '.join(b.get('content', '') for b in plant.get('blocks', []))
        searchable = f"{name} {name2} {sci} {branch} {habit} {block_text}".lower()
        name_l, name2_l = name.lower(), name2.lower()

        score = 0
        # 完整词：双向匹配（"樱花" 能匹配 "东京樱花"，"东京樱花" 能匹配 "东京樱花在哪"）
        for kw in full_tokens:
            kw_l = kw.lower()
            if kw_l in name_l or name_l in kw_l or kw_l in name2_l or (name2_l and name2_l in kw_l):
                score += 10
            elif kw_l in sci.lower() or kw_l in branch.lower():
                score += 5
            elif kw_l in searchable:
                score += 1
        # 滑窗子串：只匹配名称和科属，不匹配 block 内容（避免"哪里"匹配到无关植物）
        for kw in sub_tokens:
            kw_l = kw.lower()
            if kw_l in name_l or kw_l in name2_l:
                score += 10
            elif kw_l in sci.lower() or kw_l in branch.lower():
                score += 5
        if score > 0:
            scored.append((score, plant))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:top_k]]


def build_plant_context(plants: list) -> str:
    """将植物列表格式化为可注入 system prompt 的文本，包含精确位置和建筑位置。"""
    if not plants:
        return ''
    lines = ['以下是校园内相关植物的真实数据，请优先基于此作答：']
    for p in plants:
        lines.append(f"\n【{p['name']}】科属：{p.get('branch','')} | 习性：{p.get('habit','')}")
        for block in p.get('blocks', []):
            lines.append(f"  {block['title']}：{block['content'].strip()}")
        precise = [loc for loc in _TREE_PRECISE_LOCATIONS.get(p['name'], [])
                   if re.search(r'[一-鿿]', loc)]
        building = _TREE_LOCATIONS.get(p['name'], [])
        if precise:
            lines.append(f"  校园位置：{'、'.join(precise[:3])}")
        elif building:
            lines.append(f"  校园位置：{'、'.join(building[:3])}")
        else:
            lines.append("  校园位置：暂无具体位置记录")
    return '\n'.join(lines)


def search_colleges(query: str, top_k: int = 5) -> list:
    """检索学院/机构位置，支持正式名称、简称、楼宇名的模糊匹配。"""
    if not re.search(r'学院|书院|学部|研究院|实验室|中心|楼|馆|系', query or ''):
        return []
    query_lower = (query or '').lower()
    query_clean = re.sub(r'[^\w一-鿿]', ' ', query_lower)
    full_tokens = set(kw for kw in query_clean.split() if len(kw) >= 2)
    sub_tokens = set()
    for i in range(len(query_clean)):
        for l in (2, 3, 4, 5, 6):
            sub = query_clean[i:i+l].strip()
            if len(sub) >= 2 and re.search(r'[一-鿿]', sub):
                sub_tokens.add(sub)

    stop_tokens = {
        '学院', '大学', '学校', '校区', '华东', '师范', '位置', '哪里', '在哪',
        '附近', '周边', '周围', '旁边', '植物', '什么', '哪些', '有没有',
        '闵行', '普陀', '中北', '中山', '北路', '适合', '推荐', '赏花', '看花',
        '拍照', '摄影', '打卡', '散步', '风景', '景色'
    }
    scored = []
    for college in _COLLEGE_LOCATIONS:
        names = [college.get('name', '')] + college.get('aliases', [])
        buildings = college.get('buildings', [])
        building_names = []
        for b in buildings:
            building_names.extend([b.get('name', ''), b.get('address', '')])

        score = 0
        exact_match = False
        exact_texts = []
        for text in names + building_names:
            text_lower = (text or '').lower()
            if not text_lower:
                continue
            if text_lower in query_lower:
                exact_match = True
                exact_texts.append(text_lower)
                score += 100 + len(text_lower)
            for kw in full_tokens:
                if kw in text_lower or text_lower in kw:
                    score += 12 if text in names else 5
            for kw in sub_tokens:
                if kw not in stop_tokens and kw in text_lower:
                    score += min(len(kw), 4)

        if score > 0:
            scored.append((score, exact_match, exact_texts, college))

    scored.sort(key=lambda x: x[0], reverse=True)
    if any(exact for _, exact, _, _ in scored):
        all_exact_texts = [text for _, exact, texts, _ in scored if exact for text in texts]

        def has_standalone_exact(texts):
            for text in texts:
                if not any(text != other and text in other for other in all_exact_texts):
                    return True
            return False

        scored = [item for item in scored if item[1] and has_standalone_exact(item[2])]
    return [c for _, _, _, c in scored[:top_k]]


def build_college_context(colleges: list) -> str:
    if not colleges:
        return ''
    lines = ['以下是校园学院、书院或研究机构的位置数据，请优先基于此作答：']
    for college in colleges:
        lines.append(f"\n【{college['name']}】校区：{college.get('campus', '')}")
        aliases = [a for a in college.get('aliases', []) if a != college.get('name')]
        if aliases:
            lines.append(f"  常用称呼：{'、'.join(aliases[:5])}")
        for building in college.get('buildings', []):
            lines.append(
                f"  建筑：{building.get('name', '')}；地址：{building.get('address', '')}"
            )
    return '\n'.join(lines)


def _distance_meters(lng1, lat1, lng2, lat2) -> float:
    """用近似平面距离计算上海范围内两点距离，单位米。"""
    import math
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111320 * math.cos(mean_lat)
    dy = (lat1 - lat2) * 110540
    return (dx * dx + dy * dy) ** 0.5


NEARBY_PLANT_MAX_DISTANCE_M = 100
TARGET_PLANT_CLUSTER_RADIUS_M = 120


def _wants_nearby_plants(query: str) -> bool:
    if not query:
        return False
    has_nearby = re.search(r'附近|附件|周边|周围|旁边|边上|近处|离.+近', query)
    has_plant_intent = re.search(r'植物|树|花|草|有哪些|有什么|推荐|看看', query)
    has_college_place = re.search(r'学院|书院|学部|研究院|楼|馆', query)
    return bool(has_plant_intent and (has_nearby or has_college_place))


def _looks_like_institution_location_query(query: str) -> bool:
    """学院/楼宇位置查询不要退化成植物推荐。"""
    if not query:
        return False
    if re.search(r'适合|合适|推荐|赏花|看花|看.*花|拍照|摄影|打卡|散步|风景|景色', query):
        return False
    has_institution = re.search(r'学院|书院|学部|研究院|实验室|中心|楼|馆|系', query)
    has_campus_location = re.search(r'校区', query)
    has_location_intent = re.search(r'哪里|在哪|位置|地址|坐标|怎么去|导航|路线', query)
    return bool((has_institution or has_campus_location) and has_location_intent)


def find_nearby_plants(colleges: list, top_k: int = 8, max_distance_m: int = NEARBY_PLANT_MAX_DISTANCE_M) -> list:
    """按学院楼宇坐标查找最近的校园植物点。"""
    candidates = {}
    for college in colleges:
        for building in college.get('buildings', []):
            b_lng = building.get('longitude')
            b_lat = building.get('latitude')
            if not b_lng or not b_lat:
                continue
            for plant_name, coords in _TREE_COORDS.items():
                for coord in coords:
                    lng, lat = coord.get('lng'), coord.get('lat')
                    if not lng or not lat or _campus(lng, lat) != college.get('campus'):
                        continue
                    distance = _distance_meters(b_lng, b_lat, lng, lat)
                    if distance > max_distance_m:
                        continue
                    existing = candidates.get(plant_name)
                    if not existing or distance < existing['distance_m']:
                        candidates[plant_name] = {
                            'name': plant_name,
                            'plant': _PLANT_TEMPLATE_BY_NAME.get(plant_name, {'name': plant_name}),
                            'college': college.get('name', ''),
                            'building': building.get('name', ''),
                            'distance_m': round(distance),
                            'lng': lng,
                            'lat': lat,
                            'number': coord.get('number', '')
                        }

    return sorted(candidates.values(), key=lambda x: x['distance_m'])[:top_k]


def build_nearby_plant_context(nearby_plants: list) -> str:
    if not nearby_plants:
        return ''
    lines = ['以下是命中学院附近的校园植物，距离为近似计算：']
    for item in nearby_plants:
        plant = item.get('plant', {})
        lines.append(
            f"  {item['college']} - {item['building']}附近约{item['distance_m']}米："
            f"{item['name']}（科属：{plant.get('branch', '')}；习性：{plant.get('habit', '')}；"
            f"位置：{item.get('number') or '暂无具体位置描述'}）"
        )
    return '\n'.join(lines)


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
}

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

_PLANT_COMMON_NAME_ALIASES = {
    '桂花': ['木犀'],
    '桂树': ['木犀'],
    '丹桂': ['木犀'],
    '金桂': ['木犀'],
    '银桂': ['木犀'],
    '四季桂': ['木犀'],
}


def _display_plant_name_for_query(plant_name: str, query: str) -> str:
    for alias, plant_names in _PLANT_COMMON_NAME_ALIASES.items():
        if alias in (query or '') and plant_name in plant_names:
            return f"{plant_name}（{alias}）"
    return plant_name


def _campus_filter_from_query(query: str):
    if re.search(r'闵行|紫竹', query or ''):
        return '闵行'
    if re.search(r'普陀|中北|中山北路', query or ''):
        return '普陀'
    return None


def _normalize_campus(value):
    if value in ('普陀', '闵行'):
        return value
    return None


def _preferred_campus_from_request(data: dict):
    ranking_location = _ranking_location_from_request(data)
    user_campus = _normalize_campus(data.get('userCampus'))
    if ranking_location and ranking_location.get('campus_trusted'):
        return ranking_location.get('campus')
    if user_campus:
        return user_campus
    if ranking_location:
        return ranking_location.get('campus')
    return None


def _ranking_location_from_request(data: dict):
    location = data.get('userLocation') or {}
    if not isinstance(location, dict):
        return None
    source = location.get('source') or ''
    trusted = bool(location.get('trusted'))
    use_for_distance = bool(location.get('useForDistance', trusted))
    is_actual_location = source in ('amap', 'browser')
    if source != 'campus_center' and not (is_actual_location and use_for_distance):
        return None
    try:
        lng = location.get('lng', location.get('longitude'))
        lat = location.get('lat', location.get('latitude'))
        if lng is None or lat is None:
            return None
        lng = float(lng)
        lat = float(lat)
    except (TypeError, ValueError, AttributeError):
        return None
    campus = _normalize_campus(location.get('campus')) or _campus(lng, lat)
    return {
        'lng': lng,
        'lat': lat,
        'campus': campus,
        'trusted': trusted,
        'campus_trusted': bool(location.get('campusTrusted', trusted if source == 'campus_center' else False)),
        'use_for_distance': use_for_distance,
        'source': source or 'browser',
    }


def _query_terms(query: str) -> set:
    query_lower = (query or '').lower()
    cleaned = re.sub(r'[^\w一-鿿]', ' ', query_lower)
    terms = {kw for kw in cleaned.split() if len(kw) >= 2 and kw not in _QUERY_STOP_TOKENS}
    for i in range(len(query_lower)):
        for length in (2, 3, 4):
            sub = query_lower[i:i + length].strip()
            if len(sub) >= 2 and re.search(r'[一-鿿]', sub) and sub not in _QUERY_STOP_TOKENS:
                terms.add(sub)
    return terms


def _add_semantic_token(tokens: set, token: str):
    token = (token or '').strip().lower()
    if len(token) >= 2 and token not in _SEMANTIC_STOP_TOKENS:
        tokens.add(token)


def _semantic_tokens(text: str) -> set:
    """轻量语义 token：中文短语滑窗 + 小规模同义短语扩展，不依赖外部模型。"""
    text_lower = (text or '').lower()
    cleaned = re.sub(r'[^\w一-鿿]', ' ', text_lower)
    tokens = set()

    for token in cleaned.split():
        _add_semantic_token(tokens, token)

    for run in re.findall(r'[一-鿿]+', text_lower):
        for length in (2, 3, 4):
            for index in range(0, max(len(run) - length + 1, 0)):
                _add_semantic_token(tokens, run[index:index + length])

    for triggers, expansions in _SEMANTIC_SYNONYM_GROUPS:
        if any(trigger in text_lower for trigger in triggers):
            for expansion in expansions:
                _add_semantic_token(tokens, expansion)

    return tokens


def _semantic_similarity(left_text: str, right_text: str) -> float:
    left_tokens = _semantic_tokens(left_text)
    right_tokens = _semantic_tokens(right_text)
    if not left_tokens or not right_tokens:
        return 0.0

    overlap = left_tokens & right_tokens
    if not overlap:
        return 0.0

    query_coverage = len(overlap) / len(left_tokens)
    jaccard = len(overlap) / len(left_tokens | right_tokens)
    cosine = len(overlap) / math.sqrt(len(left_tokens) * len(right_tokens))
    return min(1.0, query_coverage * 0.55 + cosine * 0.30 + jaccard * 0.15)


def _scene_profile_semantic_text(profile: dict) -> str:
    parts = [
        profile.get('id', ''),
        profile.get('name', ''),
        profile.get('definition', ''),
        ' '.join(profile.get('contextTexts', [])),
        ' '.join(profile.get('positiveQueries', [])),
        ' '.join(profile.get('intentKeywords', [])),
        ' '.join(profile.get('categories', [])),
        ' '.join(profile.get('terms', [])),
        ' '.join(profile.get('placeTerms', [])),
    ]
    for key in (
        'categoryWeights',
        'subCategoryWeights',
        'tagWeights',
        'textWeights',
        'placeTermWeights'
    ):
        parts.extend((profile.get(key) or {}).keys())
    return ' '.join(str(part) for part in parts if part)


def _scene_profile_match_score(query: str, profile: dict, vector_score=None) -> float:
    if any(keyword in (query or '') for keyword in profile.get('intentKeywords', [])):
        return 1.0
    lexical_score = _semantic_similarity(query, _scene_profile_semantic_text(profile))
    if vector_score is None:
        return lexical_score
    return max(lexical_score, lexical_score * 0.30 + max(0.0, vector_score) * 0.70)


def _poi_semantic_text(poi: dict) -> str:
    config = _poi_category_config(poi.get('category'))
    parts = [
        _poi_search_text(poi),
        poi.get('category', ''),
        config.get('contextName', ''),
        config.get('genericIntentReason', ''),
        config.get('dataReason', ''),
    ]
    return ' '.join(str(part) for part in parts if part)


def _matched_scene_rules(query: str) -> list:
    return [
        rule for rule in _SCENE_RULES
        if any(keyword in (query or '') for keyword in rule['keywords'])
    ]


def _matched_scene_profiles(query: str) -> list:
    matches = []
    vector_scores = _SEMANTIC_RETRIEVER.scene_scores(
        query,
        (profile.get('id') for profile in _SCENE_PROFILES)
    )
    for profile in _SCENE_PROFILES:
        exact = any(keyword in (query or '') for keyword in profile.get('intentKeywords', []))
        vector_score = vector_scores.get(str(profile.get('id')))
        semantic_score = _scene_profile_match_score(query, profile, vector_score)
        threshold = (
            float(profile.get('semanticThreshold') or 0.42)
            if vector_score is not None else _SEMANTIC_SCENE_MATCH_THRESHOLD
        )
        if exact or semantic_score >= threshold:
            matched = dict(profile)
            matched['_semanticScore'] = round(semantic_score, 4)
            matched['_semanticMatch'] = not exact
            matched['_semanticBackend'] = 'sentence-transformer' if vector_score is not None else 'lexical-fallback'
            matches.append(matched)
    # 明确关键词命中时，以用户直接表达的场景为准，避免相近场景的向量
    # 结果混入。例如“散步”不应因为语义相近而同时启用“情侣约会”。
    exact_matches = [profile for profile in matches if not profile.get('_semanticMatch')]
    if exact_matches:
        matches = exact_matches
    matches.sort(
        key=lambda profile: (
            not profile.get('_semanticMatch'),
            profile.get('_semanticScore', 0),
            len(profile.get('intentKeywords', []))
        ),
        reverse=True
    )
    return matches


def _scene_profiles_as_rules(scene_profiles: list) -> list:
    rules = []
    for profile in scene_profiles or []:
        rules.append({
            'id': profile.get('id'),
            'name': profile.get('name'),
            'keywords': profile.get('intentKeywords', []),
            'terms': profile.get('terms', []),
            'place_terms': profile.get('placeTerms', [])
        })
    return rules


def _scene_profile_categories(scene_profiles: list) -> list:
    categories = []
    for profile in scene_profiles or []:
        for category in profile.get('categories', []):
            if category not in categories:
                categories.append(category)
    return categories


def _scene_profile_score_cap(scene_profiles: list):
    caps = []
    for profile in scene_profiles or []:
        try:
            cap = float(profile.get('scoreCap') or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap > 0:
            caps.append(cap)
    return max(caps) if caps else None


def _scene_profile_top_k(profile: dict, requested_top_k: int, candidates: list) -> int:
    top_k = requested_top_k
    for scene_profile in profile.get('scene_profiles', []):
        try:
            top_k = max(top_k, int(scene_profile.get('defaultTopK') or requested_top_k))
        except (TypeError, ValueError):
            pass
        if scene_profile.get('returnAllWhenCampusSpecified') and profile.get('explicit_campus_filter'):
            top_k = max(top_k, len(candidates))
    return max(top_k, 1)


def _ranking_location_distance(profile: dict, campus: str, lng: float, lat: float):
    location = profile.get('ranking_location')
    if not location or lng is None or lat is None:
        return 0, None, ''
    is_actual_location = location.get('source') in ('amap', 'browser') and location.get('use_for_distance', location.get('trusted'))
    if not is_actual_location and campus != location.get('campus'):
        return 0, None, ''
    distance_m = _distance_meters(location['lng'], location['lat'], lng, lat)
    max_distance_m = 1200 if profile.get('categories') == ['canteen'] else 900
    bonus = max(0, 10 * (1 - distance_m / max_distance_m))
    label = '距你当前位置' if is_actual_location else '距当前校区中心'
    return bonus, distance_m, label


def _point_text(point: dict) -> str:
    template = point.get('template') or {}
    block_text = ' '.join(
        f"{block.get('title', '')} {block.get('content', '')}"
        for block in template.get('blocks', [])
    )
    return ' '.join(filter(None, [
        point.get('name', ''),
        template.get('name2', '') or '',
        template.get('scientificName', '') or '',
        template.get('branch', '') or '',
        template.get('habit', '') or '',
        point.get('number', ''),
        point.get('place', ''),
        point.get('owner_title', ''),
        point.get('owner_slogan', ''),
        point.get('owner_content', ''),
        block_text,
    ])).lower()


def _direct_target_plant_names(query: str, plants: list, has_scene: bool) -> set:
    """只在用户明确点名植物/科属时限制候选，避免场景推荐被检索结果过早收窄。"""
    query_lower = (query or '').lower()
    terms = _query_terms(query)
    target = set()
    known_plant_names = {
        poi.get('name')
        for poi in _CAMPUS_POIS
        if poi.get('category') == 'plant' and poi.get('name')
    }
    for alias, plant_names in _PLANT_COMMON_NAME_ALIASES.items():
        if alias in (query or ''):
            target.update(name for name in plant_names if name in known_plant_names)
    for plant in plants or []:
        name = plant.get('name', '')
        name2 = plant.get('name2', '') or ''
        branch = plant.get('branch', '') or ''
        name_lower = name.lower()
        name2_lower = name2.lower()
        branch_lower = branch.lower()
        if name and name in query:
            target.add(name)
        elif name2 and name2 in query:
            target.add(name)
        elif branch.endswith('科') and branch in (query or ''):
            target.add(name)
        elif any(term in name_lower or (name2_lower and term in name2_lower) for term in terms):
            target.add(name)
        elif branch.endswith('科') and branch in (query or ''):
            target.add(name)
    for plant_name in known_plant_names:
        plant_name_lower = plant_name.lower()
        if plant_name and plant_name in (query or ''):
            target.add(plant_name)
        elif any(term == plant_name_lower or term in plant_name_lower for term in terms):
            target.add(plant_name)
    return target


def _direct_location_plant_names(query: str, plants: list) -> list:
    """直接询问某类植物在哪里时，返回用户真正点名的植物，避免“银杏”误带出“杏”。"""
    query_text = query or ''
    terms = _query_terms(query_text)
    matched = []
    exact_names = set()

    for plant in plants or []:
        name = plant.get('name', '')
        name2 = plant.get('name2', '') or ''
        branch = plant.get('branch', '') or ''
        name_lower = name.lower()
        name2_lower = name2.lower()

        is_match = False
        if name and name in query_text:
            exact_names.add(name)
            is_match = True
        elif name2 and name2 in query_text:
            exact_names.add(name)
            is_match = True
        elif branch.endswith('科') and branch in query_text:
            is_match = True
        elif any(term in name_lower or (name2_lower and term in name2_lower) for term in terms):
            is_match = True

        if is_match and name and name not in matched:
            matched.append(name)

    if exact_names:
        exact_names = {
            name for name in exact_names
            if not any(name != other and name in other for other in exact_names)
        }
        matched = [name for name in matched if name in exact_names]

    return matched


def _is_direct_plant_location_query(query: str, plants: list, colleges: list,
                                    institution_location_requested: bool) -> bool:
    if not plants or colleges or institution_location_requested or _wants_nearby_plants(query):
        return False
    if re.search(r'适合|合适|推荐|赏|看.*花|拍照|摄影|打卡|散步|风景|景色|约会', query or ''):
        return False
    has_location_intent = re.search(r'哪里|在哪|位置|地点|地图|显示|分布|有哪些|有什么', query or '')
    return bool(has_location_intent and _direct_location_plant_names(query, plants))


def _nearest_college_distance(point: dict, colleges: list):
    best = None
    for college in colleges or []:
        for building in college.get('buildings', []):
            b_lng = building.get('longitude')
            b_lat = building.get('latitude')
            if not b_lng or not b_lat:
                continue
            distance = _distance_meters(b_lng, b_lat, point['lng'], point['lat'])
            if not best or distance < best['distance_m']:
                best = {
                    'distance_m': distance,
                    'college': college.get('name', ''),
                    'building': building.get('name', ''),
                }
    return best


def _scene_score_for_point(point: dict, scenes: list):
    text = _point_text(point)
    score = 0
    scene_names = []
    for rule in scenes:
        hits = [term for term in rule['terms'] if term.lower() in text]
        place_hits = [term for term in rule['place_terms'] if term in (point.get('number') or '')]
        if hits or place_hits:
            scene_names.append(rule['name'])
        score += min(34, len(set(hits)) * 5 + len(set(place_hits)) * 4)
    return score, scene_names


def _best_cluster_label(points: list, target_plants: set) -> str:
    counts = {}
    for point in points:
        label = (point.get('number') or '').strip()
        if not label:
            continue
        weight = 3 if re.search(r'[一-鿿]', label) else 1
        counts[label] = counts.get(label, 0) + weight
    if counts:
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]
    primary = sorted(target_plants)[0] if target_plants else '植物'
    return f"{primary}密集点"


def _item_like_count(item: dict) -> int:
    meta = item.get('meta') or {}
    try:
        return int(meta.get('likeCount') or item.get('likeCount') or 0)
    except (TypeError, ValueError):
        return 0


_LANDMARK_ANCHORS = {
    ('普陀', '图书馆'): {'lng': 121.406582, 'lat': 31.228318},
}


def _landmark_anchor_point(label: str, campus: str):
    """场景推荐使用地标中心作锚点，避免植物点落在地标边缘导致误解。"""
    label_base = re.sub(r'(周边|附近|沿线|密集点)$', '', label or '').strip()
    return _LANDMARK_ANCHORS.get((campus, label_base))


def _representative_anchor_point(items: list, label: str, label_key: str):
    """聚类展示坐标使用真实点位，避免均值坐标落到河道/建筑内部。"""
    valid_items = [item for item in items if item.get('lng') and item.get('lat')]
    if not valid_items:
        return None
    labeled_items = [
        item for item in valid_items
        if (item.get(label_key) or '').strip() == (label or '').strip()
    ]
    label_base = re.sub(r'(周边|附近|沿线|密集点)$', '', label or '').strip()
    landmark_items = []
    if label_base and label_base != (label or '').strip():
        landmark_items = [
            item for item in valid_items
            if label_base in ((item.get(label_key) or '').strip())
        ]
    candidates = labeled_items or landmark_items or valid_items
    center_lng = sum(item['lng'] for item in valid_items) / len(valid_items)
    center_lat = sum(item['lat'] for item in valid_items) / len(valid_items)
    return sorted(
        candidates,
        key=lambda item: (
            _distance_meters(item['lng'], item['lat'], center_lng, center_lat),
            -_item_like_count(item)
        )
    )[0]


def _ranked_place_dedupe_keys(item: dict) -> list:
    category = item.get('category') or ('plant' if item.get('plants') else '')
    campus = item.get('campus') or ''
    name = re.sub(r'\s+', '', item.get('name') or item.get('number') or '')
    keys = []
    if category and campus and name:
        keys.append(('name', category, campus, name))
    if _poi_category_config(category).get('tracksPlants'):
        if name:
            # Avoid returning two indistinguishable labels (for example two
            # "图书馆周边" cards from different campuses) in one Top-K list.
            keys.append(('plant-display-name', category, name))
        try:
            keys.append(('coord', category, campus, round(float(item.get('lng')), 5), round(float(item.get('lat')), 5)))
        except (TypeError, ValueError):
            pass
    return keys


def _dedupe_and_rank_places(ranked: list, top_k: int) -> list:
    unique = []
    seen = set()
    for item in ranked:
        keys = _ranked_place_dedupe_keys(item)
        if keys and any(key in seen for key in keys):
            continue
        unique.append(item)
        seen.update(keys)
    for index, item in enumerate(unique[:top_k], 1):
        item['rank'] = index
    return unique[:top_k]


def _rank_distance_sort_value(item: dict):
    distance = item.get('user_distance_m')
    return -distance if distance is not None else -(10 ** 9)


def _rank_dense_target_places(query: str, target_plants: set, scenes: list, colleges: list,
                              campus_filter, top_k: int, max_distance_m: int,
                              use_college_radius: bool) -> list:
    """明确点名植物并带场景时，优先推荐该植物的空间密集区。"""
    clusters = []
    for point in _TREE_POINTS:
        if point.get('name') not in target_plants or not point.get('lng') or not point.get('lat'):
            continue
        campus = _campus(point['lng'], point['lat'])
        if campus_filter and campus != campus_filter:
            continue

        nearest = _nearest_college_distance(point, colleges) if colleges else None
        if use_college_radius and (not nearest or nearest['distance_m'] > max_distance_m):
            continue

        target_cluster = None
        for cluster in clusters:
            if cluster['campus'] != campus:
                continue
            center_lng = cluster['lng_sum'] / cluster['point_count']
            center_lat = cluster['lat_sum'] / cluster['point_count']
            if _distance_meters(center_lng, center_lat, point['lng'], point['lat']) <= TARGET_PLANT_CLUSTER_RADIUS_M:
                target_cluster = cluster
                break
        if not target_cluster:
            target_cluster = {
                'campus': campus,
                'points': [],
                'lng_sum': 0.0,
                'lat_sum': 0.0,
                'point_count': 0,
                'scene_score': 0,
                'scene_names': set(),
                'like_total': 0,
                'owner_count': 0,
                'nearest': None,
            }
            clusters.append(target_cluster)

        scene_score, scene_names = _scene_score_for_point(point, scenes)
        target_cluster['points'].append(point)
        target_cluster['lng_sum'] += point['lng']
        target_cluster['lat_sum'] += point['lat']
        target_cluster['point_count'] += 1
        target_cluster['scene_score'] = max(target_cluster['scene_score'], scene_score)
        target_cluster['scene_names'].update(scene_names)
        target_cluster['like_total'] += int(point.get('likeCount') or 0)
        if point.get('owner_content') or point.get('owner_slogan'):
            target_cluster['owner_count'] += 1
        if nearest and (not target_cluster['nearest'] or nearest['distance_m'] < target_cluster['nearest']['distance_m']):
            target_cluster['nearest'] = nearest

    ranked = []
    for cluster in clusters:
        target_count = cluster['point_count']
        density_score = min(76, target_count * 18)
        scene_score = min(14, cluster['scene_score'] * 0.4)
        heat_score = min(8, cluster['like_total'] * 0.6 + cluster['owner_count'] * 1.2)
        distance_score = 0
        if cluster['nearest']:
            distance_score = max(0, 12 * (1 - cluster['nearest']['distance_m'] / max_distance_m))
        score = min(100, density_score + scene_score + heat_score + distance_score)

        label = _best_cluster_label(cluster['points'], target_plants)
        anchor = _representative_anchor_point(cluster['points'], label, 'number')
        top_plants = [
            name for name in sorted({point['name'] for point in cluster['points']})
        ][:4]
        if re.search(r'[一-鿿]', label):
            display_name = f"{label}周边" if target_count > 1 and not label.endswith('周边') else label
        else:
            display_name = f"{top_plants[0]}密集点" if top_plants else label

        reasons = []
        if cluster['scene_names']:
            reasons.append('匹配' + '、'.join(sorted(cluster['scene_names'])))
        plant_display_name = _display_plant_name_for_query(top_plants[0], query) if top_plants else '目标植物'
        reasons.append(f"{TARGET_PLANT_CLUSTER_RADIUS_M}米范围内聚合{target_count}个{plant_display_name}真实点位")
        if cluster['nearest']:
            reasons.append(f"距{cluster['nearest']['building']}约{round(cluster['nearest']['distance_m'])}米")
        if cluster['owner_count']:
            reasons.append(f"含{cluster['owner_count']}条认养/留言信息")

        ranked.append({
            'name': display_name,
            'number': label,
            'lng': round(anchor['lng'], 6),
            'lat': round(anchor['lat'], 6),
            'campus': cluster['campus'],
            'kind': 'ranked_place',
            'category': 'plant',
            'score': round(score, 1),
            'reason': '；'.join(reasons[:4]),
            'plants': top_plants,
            'target_count': target_count,
            'distance_m': round(cluster['nearest']['distance_m']) if cluster['nearest'] else None,
            'college': cluster['nearest']['college'] if cluster['nearest'] else '',
        })

    ranked.sort(key=lambda item: (item.get('target_count', 0), item['score']), reverse=True)
    return _dedupe_and_rank_places(ranked, top_k)


def _has_canteen_intent(query: str) -> bool:
    return _category_has_intent(query, 'canteen')


def _has_nearby_intent(query: str) -> bool:
    return bool(re.search(r'附近|附件|周边|周围|旁边|边上|近处|离.+近', query or ''))


def _canteen_name_texts(poi: dict) -> list:
    return _poi_exact_match_texts(poi)


def direct_poi_matches(query: str, category: str) -> list:
    """用户明确问某个具体 POI 时，直接返回该类别点位，不走 Top 排名。"""
    query = query or ''
    config = _poi_category_config(category)
    if not config.get('directLookup') or not _category_has_intent(query, category):
        return []

    campus_filter = _campus_filter_from_query(query)
    matches = []
    seen_ids = set()
    for poi in _CAMPUS_POIS:
        if poi.get('category') != category:
            continue
        if campus_filter and poi.get('campus') != campus_filter:
            continue

        matched_texts = _poi_exact_matches_query(poi, query)
        if not matched_texts or poi.get('id') in seen_ids:
            continue

        matched_texts.sort(key=len, reverse=True)
        item = dict(poi)
        item['matched_text'] = matched_texts[0]
        matches.append(item)
        seen_ids.add(poi.get('id'))

    matches.sort(key=lambda poi: len(poi.get('matched_text', '')), reverse=True)
    return matches


def direct_configured_poi_matches(query: str) -> list:
    matches = []
    seen_ids = set()
    for category, config in POI_CATEGORY_CONFIGS.items():
        if not config.get('directLookup'):
            continue
        for poi in direct_poi_matches(query, category):
            poi_id = poi.get('id')
            if poi_id in seen_ids:
                continue
            seen_ids.add(poi_id)
            matches.append(poi)
    matches.sort(key=lambda poi: len(poi.get('matched_text', '')), reverse=True)
    return matches


def direct_canteen_matches(query: str) -> list:
    """用户明确问某个食堂在哪里时，直接返回该食堂点位，不走 Top 排名。"""
    return direct_poi_matches(query, 'canteen')


def _should_rank_places(query: str, plants: list, colleges: list, institution_location_requested: bool) -> bool:
    if institution_location_requested and not _wants_nearby_plants(query) and not _intent_categories_from_query(query):
        return False
    has_place_intent = re.search(r'推荐|适合|合适|哪里|在哪|位置|地点|地图|显示|去哪里|赏|看.*花|拍照|散步|打卡|吃饭|用餐|就餐', query or '')
    return bool(
        _matched_scene_rules(query) or
        _matched_scene_profiles(query) or
        _intent_categories_from_query(query) or
        _wants_nearby_plants(query) or
        (has_place_intent and (plants or colleges))
    )


_UNSUPPORTED_DATA_PATTERNS = (
    (r'咖啡', '咖啡地点'),
    (r'奶茶', '奶茶地点'),
    (r'充电桩|充电站|充电位置', '充电桩'),
    (r'校医院|医院|医务室', '校医院或医务室'),
    (r'快递|驿站|取件', '快递服务点'),
    (r'自动取款机|\bATM\b|银行网点', '自动取款机或银行网点'),
    (r'超市|便利店', '超市或便利店'),
    (r'打印|复印', '打印或复印地点'),
    (r'火锅', '火锅餐饮地点'),
    (r'宠物|猫|狗|动物|鸟|鱼', '宠物或动物相关地点'),
    (r'天气|气温|下雨', '实时天气'),
)


def _unsupported_data_label(query: str):
    for pattern, label in _UNSUPPORTED_DATA_PATTERNS:
        if re.search(pattern, query or '', flags=re.IGNORECASE):
            return label
    return None


def _is_unsupported_data_query(query: str, plants: list, colleges: list,
                               institution_location_requested: bool) -> bool:
    """当前没有对应 POI 数据的对象，不要退化成植物/景色推荐。"""
    # These categories currently have no trusted data source.  A semantic
    # match to an existing restaurant/plant must never override this guard.
    if _unsupported_data_label(query):
        return True
    if (
        plants or colleges or institution_location_requested or
        _matched_scene_rules(query) or _matched_scene_profiles(query) or
        _intent_categories_from_query(query)
    ):
        return False
    return False


def _poi_search_text(poi: dict) -> str:
    meta = poi.get('meta') or {}
    parts = [
        poi.get('name', ''),
        poi.get('subCategory', ''),
        poi.get('locationName', ''),
        poi.get('campus', ''),
        poi.get('text', ''),
        ' '.join(str(tag) for tag in poi.get('tags', [])),
    ]
    parts.extend(_meta_text_values(meta, POI_SEARCH_META_FIELDS))
    parts.extend(_meta_text_values(meta, ('aliases',)))
    return ' '.join(str(part) for part in parts if part).lower()


def _poi_tags_text(poi: dict) -> str:
    return ' '.join(str(tag) for tag in poi.get('tags', []) if tag).lower()


def _query_has_density_intent(query: str) -> bool:
    return bool(re.search(
        r'去哪里|去哪|逛逛|转转|哪里多|哪.*多|最多|密集|集中|成片|一片|适合|合适|推荐|赏花|看花|拍照|摄影|打卡|风景|景色|好看|漂亮|出片',
        query or ''
    ))


def _college_target_texts(colleges: list) -> set:
    texts = set()
    for college in colleges or []:
        texts.add(college.get('name', ''))
        texts.update(college.get('aliases', []))
        for building in college.get('buildings', []):
            texts.add(building.get('name', ''))
            texts.add(building.get('address', ''))
    return {text for text in texts if text}


def _poi_college_texts(poi: dict) -> set:
    meta = poi.get('meta') or {}
    aliases = meta.get('aliases') or []
    if isinstance(aliases, str):
        aliases = [aliases]
    texts = {
        poi.get('name', ''),
        poi.get('subCategory', ''),
        poi.get('locationName', ''),
        meta.get('collegeName', ''),
        meta.get('buildingName', ''),
        meta.get('address', ''),
    }
    texts.update(aliases)
    return {text for text in texts if text}


def _poi_matches_college_target(poi: dict, target_texts: set, query: str) -> bool:
    poi_texts = _poi_college_texts(poi)
    query = query or ''
    for text in poi_texts:
        if text and text in query:
            return True
    for target in target_texts:
        for text in poi_texts:
            if target == text or target in text or text in target:
                return True
    return False


def _poi_matches_target_plant(poi: dict, target_plants: set, query_terms: set) -> bool:
    if not target_plants:
        return True
    name = poi.get('name', '')
    sub_category = poi.get('subCategory', '')
    return name in target_plants or sub_category in target_plants


def _configured_category_match_score(poi: dict, profile: dict) -> dict:
    query = profile['query']
    category = poi.get('category')
    config = _poi_category_config(category)
    matcher = config.get('targetMatcher')
    name = poi.get('name', '')
    sub_category = poi.get('subCategory', '')

    if matcher == 'plant':
        if name in profile['target_plants']:
            return {
                'score': config.get('targetMatchScore', 46),
                'reason': f"命中{name}",
                'exact': True,
            }
        if name and name in query:
            return {
                'score': config.get('nameMatchScore', config.get('exactMatchScore', 64)),
                'reason': f"命中{name}",
                'exact': True,
            }
        if sub_category and sub_category in query:
            return {
                'score': config.get('subCategoryMatchScore', config.get('exactMatchScore', 64)),
                'reason': f"命中{sub_category}",
                'exact': True,
            }
        return {'score': 0, 'reason': '', 'exact': False}

    if matcher == 'college':
        if _poi_matches_college_target(poi, profile['college_target_texts'], query):
            return {
                'score': config.get('exactMatchScore', 76),
                'reason': config.get('exactMatchReason', '命中地点名称'),
                'exact': True,
            }
        return {'score': 0, 'reason': '', 'exact': False}

    exact_matches = _poi_exact_matches_query(poi, query)
    if exact_matches:
        return {
            'score': config.get('exactMatchScore', 64),
            'reason': config.get('exactMatchReason', '命中地点名称'),
            'exact': True,
        }
    if _category_has_intent(query, category) and config.get('genericIntentScore'):
        return {
            'score': config.get('genericIntentScore', 0),
            'reason': config.get('genericIntentReason', '匹配地点类别'),
            'exact': False,
        }
    return {'score': 0, 'reason': '', 'exact': False}


def _classify_poi_query(query: str, plants: list, colleges: list,
                        institution_location_requested: bool,
                        preferred_campus=None,
                        ranking_location=None) -> dict:
    scene_profiles = _matched_scene_profiles(query)
    scenes = _matched_scene_rules(query)
    scene_rule_ids = {rule.get('id') for rule in scenes}
    scenes.extend(
        rule for rule in _scene_profiles_as_rules(scene_profiles)
        if rule.get('id') not in scene_rule_ids
    )
    has_scene = bool(scenes)
    query_terms = _query_terms(query)
    target_plants = _direct_target_plant_names(query, plants, has_scene)
    wants_nearby = bool(colleges and _wants_nearby_plants(query))
    intent_categories = _intent_categories_from_query(query)
    poi_nearby = bool(colleges and intent_categories and _has_nearby_intent(query))
    plant_intent = bool(
        plants or target_plants or has_scene or
        _category_has_intent(query, 'plant')
    )
    college_intent = bool(colleges or institution_location_requested)

    categories = []
    intent = 'general'
    scene_categories = _scene_profile_categories(scene_profiles)
    if wants_nearby:
        categories = ['plant']
        intent = 'nearby_plant'
    elif scene_categories:
        categories = scene_categories
        intent = 'scene_recommendation'
    elif intent_categories:
        # 复制一份，避免后续对该列表的修改写回 lru_cache 的缓存对象。
        categories = list(intent_categories)
        intent = f"{intent_categories[0]}_lookup" if len(intent_categories) == 1 else 'poi_lookup'
    elif institution_location_requested or (college_intent and not plant_intent):
        categories = ['college']
        intent = 'college_location'
    elif plant_intent:
        categories = ['plant']
        intent = 'plant_scene' if has_scene or _query_has_density_intent(query) else 'plant_lookup'
    elif college_intent:
        categories = ['college']
        intent = 'college_location'

    explicit_campus = _campus_filter_from_query(query)

    return {
        'query': query or '',
        'categories': categories,
        'intent': intent,
        'scenes': scenes,
        'scene_profiles': scene_profiles,
        'has_scene': has_scene,
        'query_terms': query_terms,
        'target_plants': target_plants,
        'colleges': colleges or [],
        'college_target_texts': _college_target_texts(colleges),
        'campus_filter': explicit_campus or _normalize_campus(preferred_campus),
        'explicit_campus_filter': explicit_campus,
        'preferred_campus': _normalize_campus(preferred_campus),
        'ranking_location': ranking_location,
        'wants_nearby': wants_nearby or poi_nearby,
        'wants_density': bool(has_scene or _query_has_density_intent(query)),
        'institution_location_requested': institution_location_requested,
    }


def _score_poi_match(poi: dict, profile: dict) -> dict:
    query = profile['query']
    terms = profile['query_terms']
    category = poi.get('category')
    name = poi.get('name', '')
    sub_category = poi.get('subCategory', '')
    location_name = poi.get('locationName', '')
    meta = poi.get('meta') or {}
    text = _poi_search_text(poi)
    tags_text = _poi_tags_text(poi)
    score = 0
    reasons = []
    scene_names = set()
    matched_terms = set()
    category_config = _poi_category_config(category)
    category_match = _configured_category_match_score(poi, profile)
    exact_match = bool(category_match.get('exact'))
    if category_match['score']:
        score += category_match['score']
        if category_match.get('reason'):
            reasons.append(category_match['reason'])

    for scene_profile in profile.get('scene_profiles', []):
        scene_name = scene_profile.get('name', '场景')
        scene_hit = False

        category_weight = scene_profile.get('categoryWeights', {}).get(category, 0)
        if category_weight:
            score += category_weight
            scene_hit = True

        for text_item, weight in scene_profile.get('subCategoryWeights', {}).items():
            if text_item and (text_item in (sub_category or '') or text_item in (name or '')):
                score += weight
                scene_hit = True

        for text_item, weight in scene_profile.get('tagWeights', {}).items():
            if text_item and text_item.lower() in tags_text:
                score += weight
                scene_hit = True

        for text_item, weight in scene_profile.get('textWeights', {}).items():
            if text_item and text_item.lower() in text:
                score += weight
                scene_hit = True

        place_weight_map = scene_profile.get('placeTermWeights', {})
        for text_item in scene_profile.get('placeTerms', []):
            if text_item and text_item in location_name:
                score += place_weight_map.get(text_item, 4)
                scene_hit = True

        if profile.get('campus_filter') and poi.get('campus') == profile.get('campus_filter'):
            try:
                score += float(scene_profile.get('campusBonus') or 0)
                scene_hit = True
            except (TypeError, ValueError):
                pass

        if _poi_exact_matches_query(poi, query):
            try:
                score += float(scene_profile.get('nameMatchBonus') or 0)
                scene_hit = True
            except (TypeError, ValueError):
                pass

        if scene_hit:
            scene_names.add(scene_name)
            if not reasons:
                reasons.append(f"匹配{scene_name}")

    for term in terms:
        term_l = term.lower()
        if term_l in (name or '').lower() or term_l in (sub_category or '').lower():
            score += 12
            matched_terms.add(term)
        elif term_l in (location_name or '').lower():
            score += 8
            matched_terms.add(term)
        elif term_l in tags_text:
            score += 7
            matched_terms.add(term)
        elif term_l in text:
            score += 2
            matched_terms.add(term)

    for rule in profile['scenes']:
        hits = [term for term in rule['terms'] if term.lower() in text]
        place_hits = [term for term in rule['place_terms'] if term in location_name]
        if hits or place_hits:
            scene_names.add(rule['name'])
            score += min(34, len(set(hits)) * 5 + len(set(place_hits)) * 4)

    poi_semantic_text = _poi_semantic_text(poi)
    lexical_semantic_similarity = _semantic_similarity(query, poi_semantic_text)
    embedding_semantic_similarity = _SEMANTIC_RETRIEVER.poi_score(query, poi.get('id'))
    for scene_profile in profile.get('scene_profiles', []):
        scene_categories = scene_profile.get('categories') or []
        if scene_categories and category not in scene_categories:
            continue
        scene_similarity = _semantic_similarity(
            _scene_profile_semantic_text(scene_profile),
            poi_semantic_text
        )
        lexical_semantic_similarity = max(lexical_semantic_similarity, scene_similarity * 0.75)
        scene_poi_similarity = _SEMANTIC_RETRIEVER.scene_poi_score(
            scene_profile.get('id'),
            poi.get('id')
        )
        if scene_poi_similarity is not None:
            weighted_scene_similarity = scene_poi_similarity * 0.75
            embedding_semantic_similarity = max(
                embedding_semantic_similarity if embedding_semantic_similarity is not None else 0,
                weighted_scene_similarity
            )

    semantic_score = 0
    if embedding_semantic_similarity is not None:
        semantic_similarity = max(lexical_semantic_similarity, embedding_semantic_similarity)
        semantic_threshold = 0.52
        semantic_score_value = max(0, (semantic_similarity - 0.45) / 0.55) * 18
    else:
        semantic_similarity = lexical_semantic_similarity
        semantic_threshold = _SEMANTIC_POI_MATCH_THRESHOLD
        semantic_score_value = semantic_similarity * _SEMANTIC_POI_SCORE_SCALE
    if semantic_similarity >= semantic_threshold:
        semantic_score = min(18, semantic_score_value)
        score += semantic_score
        if not exact_match:
            reasons.append('语义匹配用户意图')

    if matched_terms and not reasons:
        reasons.append('命中关键词：' + '、'.join(sorted(matched_terms)[:4]))
    if scene_names:
        reasons.append('匹配' + '、'.join(sorted(scene_names)))

    score_cap = category_config.get('exactScoreCap') if exact_match else category_config.get('scoreCap')
    configured_score_cap = _scene_profile_score_cap(profile.get('scene_profiles', []))
    if configured_score_cap:
        score_cap = configured_score_cap

    return {
        'match_score': min(score, score_cap),
        'reasons': reasons,
        'scene_names': scene_names,
        'semantic_score': round(semantic_score, 2),
    }


def _poi_candidates(profile: dict, max_distance_m: int) -> list:
    candidates = []
    use_distance_filter = bool(profile['colleges'] and (profile['wants_nearby'] or profile['has_scene']))

    for poi in _CAMPUS_POIS:
        category = poi.get('category')
        category_config = _poi_category_config(category)
        if profile['categories'] and category not in profile['categories']:
            continue
        if profile['campus_filter'] and poi.get('campus') != profile['campus_filter']:
            continue
        if category_config.get('targetMatcher') == 'plant' and not _poi_matches_target_plant(
            poi, profile['target_plants'], profile['query_terms']
        ):
            continue
        if category_config.get('requiresTargetOrScene'):
            has_target = bool(profile['college_target_texts'])
            if has_target and not _poi_matches_college_target(poi, profile['college_target_texts'], profile['query']):
                continue
            if not has_target and not profile['institution_location_requested'] and not profile.get('scene_profiles'):
                continue

        nearest = _nearest_college_distance(poi, profile['colleges']) if (
            profile['colleges'] and category_config.get('nearbyCollegeDistance')
        ) else None
        if use_distance_filter and (not nearest or nearest['distance_m'] > max_distance_m):
            continue

        score_info = _score_poi_match(poi, profile)
        if score_info['match_score'] <= 0:
            if profile['wants_nearby'] and nearest:
                score_info['match_score'] = 18
                score_info['reasons'].append('位于目标学院附近')
            elif category_config.get('fallbackDensityScore') and profile['wants_density']:
                score_info['match_score'] = category_config.get('fallbackDensityScore')
                score_info['reasons'].append(category_config.get('fallbackDensityReason'))
            else:
                continue

        candidates.append({
            'poi': poi,
            'match_score': score_info['match_score'],
            'reasons': score_info['reasons'],
            'scene_names': score_info['scene_names'],
            'nearest': nearest,
        })

    return candidates


def _best_poi_cluster_label(pois: list, target_plants: set) -> str:
    landmark_rules = [
        ('图书馆', r'图书馆'),
        ('办公楼', r'办公楼'),
        ('运动场', r'运动场|体育场'),
        ('体育馆', r'体育馆'),
        ('科学会堂', r'科学会堂'),
        ('教学楼', r'教学楼'),
    ]
    landmark_counts = []
    for name, pattern in landmark_rules:
        count = sum(1 for poi in pois if re.search(pattern, poi.get('locationName') or ''))
        if count >= 3:
            landmark_counts.append((count, name))
    if landmark_counts:
        landmark_counts.sort(key=lambda item: item[0], reverse=True)
        return f"{landmark_counts[0][1]}周边"

    counts = {}
    for poi in pois:
        label = (poi.get('locationName') or '').strip()
        if not label:
            continue
        water_or_bridge = re.search(r'河|湖|池|水|桥', label)
        weight = 3 if re.search(r'[一-鿿]', label) and not water_or_bridge else 1
        counts[label] = counts.get(label, 0) + weight
    if counts:
        repeated = [
            item for item in counts.items()
            if item[1] >= 2 and re.search(r'[一-鿿]', item[0])
        ]
        if repeated:
            return sorted(repeated, key=lambda item: item[1], reverse=True)[0][0]
        if len(pois) <= 8:
            return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]
    primary = sorted(target_plants)[0] if target_plants else '植物'
    return f"{primary}密集点"


def _scene_names(profile: dict) -> set:
    return {rule.get('name') for rule in profile.get('scenes', []) if rule.get('name')}


def _cluster_pattern_count(pois: list, pattern: str) -> int:
    return sum(1 for poi in pois if re.search(pattern, poi.get('locationName') or ''))


def _cluster_waterfront_label(pois: list) -> str:
    """Return the real campus river name instead of a campus-agnostic label."""
    campuses = {poi.get('campus') for poi in pois if poi.get('campus')}
    if campuses == {'闵行'}:
        return '樱桃河'
    if campuses == {'普陀'}:
        return '丽娃河'
    return '滨水区域'


def _scene_cluster_fit(pois: list, profile: dict) -> float:
    scene_names = _scene_names(profile)
    if not scene_names:
        return 0

    water = _cluster_pattern_count(pois, r'河|湖|池|水|桥')
    grass = _cluster_pattern_count(pois, r'草坪|绿地|花坛|花园|园')
    road = _cluster_pattern_count(pois, r'路|道|步道|旁')
    sports = _cluster_pattern_count(pois, r'运动场|体育场')
    gym = _cluster_pattern_count(pois, r'体育馆')
    library = _cluster_pattern_count(pois, r'图书馆')
    office = _cluster_pattern_count(pois, r'办公楼')
    flowers = sum(
        1 for poi in pois
        if re.search(r'花|梅|樱|桂|荷|玉兰|紫荆|杜鹃|绣球|月季', _poi_search_text(poi))
    )

    score = 0
    if '散步休闲' in scene_names:
        score += min(65, road * 1.5 + grass * 1.2 + sports * 4 + gym * 4 + office * 0.8 + library * 0.3 - water * 0.8)
    if '情侣约会' in scene_names:
        liwa = _cluster_pattern_count(pois, r'丽娃河')
        score += min(85, library * 2.2 + water * 0.8 + grass * 1.5 + liwa * 1.4 - sports * 3 - gym * 3)
        if library and water:
            score += 20
    if '拍照打卡' in scene_names:
        score += min(65, library * 3 + water * 1.2 + grass * 1.5 + flowers * 0.35 - office * 0.4)
    if '赏花' in scene_names:
        score += min(65, flowers * 0.7 + grass * 1.2 + water * 0.6)
    if '学习科普' in scene_names:
        score += min(45, library * 2 + office * 1.2 + _cluster_pattern_count(pois, r'学院|教学|实验') * 1.6)
    return max(0, score)


def _scene_cluster_label(pois: list, profile: dict):
    scene_names = _scene_names(profile)
    if not scene_names:
        return None

    waterfront_label = _cluster_waterfront_label(pois)
    rules = []
    if '情侣约会' in scene_names:
        liwa = _cluster_pattern_count(pois, r'丽娃河')
        library = _cluster_pattern_count(pois, r'图书馆')
        if liwa >= 20:
            return f'{waterfront_label}周边'
        if library >= 3:
            return '图书馆周边'
        rules.extend([
            (waterfront_label, r'丽娃河|樱桃河|河畔|河边|桥', 4),
            ('图书馆', r'图书馆', 3),
            ('草坪', r'草坪|绿地', 2),
        ])
    if '散步休闲' in scene_names:
        rules.extend([
            ('体育馆', r'体育馆', 4),
            ('运动场', r'运动场|体育场', 4),
            ('办公楼', r'办公楼', 1),
            ('图书馆', r'图书馆', 0.6),
        ])
    if '拍照打卡' in scene_names:
        rules.extend([
            ('图书馆', r'图书馆', 4),
            (waterfront_label, r'丽娃河|樱桃河|河畔|河边|桥', 1.8),
            ('草坪', r'草坪|绿地', 1.5),
            ('花坛', r'花坛|花园', 1.5),
        ])
    if '赏花' in scene_names:
        rules.extend([
            ('花坛', r'花坛|花园|花', 3),
            ('草坪', r'草坪|绿地', 1.5),
            (waterfront_label, r'丽娃河|樱桃河|河畔|河边|桥', 1),
        ])

    scored = []
    for label, pattern, weight in rules:
        count = _cluster_pattern_count(pois, pattern)
        if count >= 3:
            scored.append((count * weight, count, label))
    if not scored:
        return None
    scored.sort(reverse=True)
    return f"{scored[0][2]}周边"


def _rank_poi_density_clusters(candidates: list, profile: dict, top_k: int,
                               max_distance_m: int) -> list:
    clusters = []
    for candidate in candidates:
        poi = candidate['poi']
        target_cluster = None
        for cluster in clusters:
            if cluster['campus'] != poi.get('campus'):
                continue
            center_lng = cluster['lng_sum'] / cluster['point_count']
            center_lat = cluster['lat_sum'] / cluster['point_count']
            if _distance_meters(center_lng, center_lat, poi['lng'], poi['lat']) <= TARGET_PLANT_CLUSTER_RADIUS_M:
                target_cluster = cluster
                break
        if not target_cluster:
            target_cluster = {
                'campus': poi.get('campus'),
                'pois': [],
                'lng_sum': 0.0,
                'lat_sum': 0.0,
                'point_count': 0,
                'plants': {},
                'match_score': 0,
                'like_total': 0,
                'owner_count': 0,
                'scene_names': set(),
                'reasons': [],
                'nearest': None,
            }
            clusters.append(target_cluster)

        meta = poi.get('meta') or {}
        target_cluster['pois'].append(poi)
        target_cluster['lng_sum'] += poi['lng']
        target_cluster['lat_sum'] += poi['lat']
        target_cluster['point_count'] += 1
        target_cluster['plants'][poi.get('name', '植物')] = target_cluster['plants'].get(poi.get('name', '植物'), 0) + 1
        target_cluster['match_score'] = max(target_cluster['match_score'], candidate['match_score'])
        target_cluster['like_total'] += int(meta.get('likeCount') or 0)
        if meta.get('ownerContent') or meta.get('ownerSlogan'):
            target_cluster['owner_count'] += 1
        target_cluster['scene_names'].update(candidate['scene_names'])
        target_cluster['reasons'].extend(candidate['reasons'])
        nearest = candidate.get('nearest')
        if nearest and (not target_cluster['nearest'] or nearest['distance_m'] < target_cluster['nearest']['distance_m']):
            target_cluster['nearest'] = nearest

    ranked = []
    has_target = bool(profile['target_plants'])
    for cluster in clusters:
        point_count = cluster['point_count']
        density_score = min(76, point_count * 18) if has_target else min(20, point_count * 0.05)
        match_score = min(32 if has_target else 28, cluster['match_score'])
        scene_fit_score = 0 if has_target else _scene_cluster_fit(cluster['pois'], profile)
        heat_score = min(8, cluster['like_total'] * 0.6 + cluster['owner_count'] * 1.2)
        distance_score = 0
        if cluster['nearest']:
            distance_score = max(0, 12 * (1 - cluster['nearest']['distance_m'] / max_distance_m))
        base_sort_score = density_score + match_score + scene_fit_score + heat_score + distance_score

        top_plants = [
            name for name, _ in sorted(cluster['plants'].items(), key=lambda item: item[1], reverse=True)[:4]
        ]
        label = _best_poi_cluster_label(cluster['pois'], profile['target_plants']) if has_target else (
            _scene_cluster_label(cluster['pois'], profile) or _best_poi_cluster_label(cluster['pois'], profile['target_plants'])
        )
        anchor = _landmark_anchor_point(label, cluster['campus']) if not has_target else None
        if not anchor:
            anchor = _representative_anchor_point(cluster['pois'], label, 'locationName')
        display_name = label
        if top_plants and not re.search(r'[一-鿿]', display_name):
            display_name = f"{top_plants[0]}密集点"
        elif point_count > 1 and not display_name.endswith('周边'):
            display_name = f"{display_name}周边"

        reasons = []
        if cluster['scene_names']:
            reasons.append('匹配' + '、'.join(sorted(cluster['scene_names'])))
        if has_target and top_plants:
            plant_display_name = _display_plant_name_for_query(top_plants[0], profile['query'])
            reasons.append(f"{TARGET_PLANT_CLUSTER_RADIUS_M}米范围内聚合{point_count}个{plant_display_name}真实点位")
        else:
            reasons.append('周边植物分布密集、种类丰富')
        if cluster['nearest']:
            reasons.append(f"距{cluster['nearest']['building']}约{round(cluster['nearest']['distance_m'])}米")
        if cluster['owner_count']:
            reasons.append(f"含{cluster['owner_count']}条认养/留言信息")

        location_distance_score, location_distance_m, location_distance_label = _ranking_location_distance(
            profile,
            cluster['campus'],
            anchor['lng'],
            anchor['lat']
        )
        if location_distance_m is not None:
            reasons.append(f"{location_distance_label}约{round(location_distance_m)}米")
        sort_score = base_sort_score + location_distance_score
        score = min(100, sort_score)

        ranked.append({
            'name': display_name,
            'number': label,
            'lng': round(anchor['lng'], 6),
            'lat': round(anchor['lat'], 6),
            'campus': cluster['campus'],
            'kind': 'ranked_place',
            'category': 'plant',
            'subCategory': top_plants[0] if top_plants else '植物',
            'score': round(score, 1),
            'reason': '；'.join(reasons[:4]),
            'plants': top_plants,
            'target_count': point_count,
            'scene_fit': round(scene_fit_score, 1),
            'sort_score': round(sort_score, 1),
            'distance_m': round(cluster['nearest']['distance_m']) if cluster['nearest'] else (
                round(location_distance_m) if location_distance_m is not None else None
            ),
            'user_distance_m': round(location_distance_m) if location_distance_m is not None else None,
            'distance_source': (profile.get('ranking_location') or {}).get('source', ''),
            'college': cluster['nearest']['college'] if cluster['nearest'] else '',
        })

    if has_target:
        ranked.sort(
            key=lambda item: (
                item.get('target_count', 0),
                item.get('sort_score', item['score']),
                _rank_distance_sort_value(item)
            ),
            reverse=True
        )
    else:
        ranked.sort(
            key=lambda item: (
                item.get('sort_score', item['score']),
                item.get('scene_fit', 0),
                item.get('target_count', 0),
                _rank_distance_sort_value(item)
            ),
            reverse=True
        )
    return _dedupe_and_rank_places(ranked, top_k)


def _rank_poi_groups(candidates: list, profile: dict, top_k: int,
                     max_distance_m: int) -> list:
    groups = {}
    for candidate in candidates:
        poi = candidate['poi']
        category = poi.get('category')
        category_config = _poi_category_config(category)
        key = _category_group_key(poi)

        group = groups.setdefault(key, {
            'category': category,
            'campus': poi.get('campus'),
            'location_name': poi.get('locationName') or '暂无具体位置描述',
            'name': poi.get('name', ''),
            'lng_sum': 0.0,
            'lat_sum': 0.0,
            'point_count': 0,
            'plants': {},
            'match_score': 0,
            'like_total': 0,
            'owner_count': 0,
            'scene_names': set(),
            'reasons': [],
            'nearest': None,
            'sample_poi': poi,
        })
        meta = poi.get('meta') or {}
        group['lng_sum'] += poi['lng']
        group['lat_sum'] += poi['lat']
        group['point_count'] += 1
        group['match_score'] = max(group['match_score'], candidate['match_score'])
        group['scene_names'].update(candidate['scene_names'])
        group['reasons'].extend(candidate['reasons'])
        if category_config.get('tracksPlants'):
            group['plants'][poi.get('name', '植物')] = group['plants'].get(poi.get('name', '植物'), 0) + 1
            group['like_total'] += int(meta.get('likeCount') or 0)
            if meta.get('ownerContent') or meta.get('ownerSlogan'):
                group['owner_count'] += 1
        nearest = candidate.get('nearest')
        if nearest and (not group['nearest'] or nearest['distance_m'] < group['nearest']['distance_m']):
            group['nearest'] = nearest

    ranked = []
    for group in groups.values():
        category_config = _poi_category_config(group['category'])
        if not category_config.get('tracksPlants'):
            density_score = 0
            heat_score = 0
            display_name = group['sample_poi'].get('name', group['name'])
            number = _format_poi_number(group['sample_poi'], group['location_name'])
            plants = []
            reasons = list(dict.fromkeys(group['reasons']))
        else:
            density_score = min(24, group['point_count'] * 3)
            heat_score = min(10, group['like_total'] * 0.8 + group['owner_count'] * 1.5)
            display_name = group['location_name']
            top_plants = [
                name for name, _ in sorted(group['plants'].items(), key=lambda item: item[1], reverse=True)[:4]
            ]
            if top_plants and not re.search(r'[一-鿿]', display_name):
                display_name = f"{top_plants[0]}点位 {display_name}"
            number = group['location_name']
            plants = top_plants
            reasons = []
            if group['scene_names']:
                reasons.append('匹配' + '、'.join(sorted(group['scene_names'])))
            if profile['target_plants'] and plants:
                reasons.append('包含' + '、'.join(plants[:3]))
            reasons.append(f"聚合{len(group['plants'])}种植物、{group['point_count']}个真实点位")
            if group['owner_count']:
                reasons.append(f"含{group['owner_count']}条认养/留言信息")

        anchor = group['sample_poi']
        distance_score = 0
        if group['nearest']:
            distance_score = max(0, 28 * (1 - group['nearest']['distance_m'] / max_distance_m))
            reasons.append(f"距{group['nearest']['building']}约{round(group['nearest']['distance_m'])}米")

        location_distance_score, location_distance_m, location_distance_label = _ranking_location_distance(
            profile,
            group['campus'],
            anchor['lng'],
            anchor['lat']
        )
        if location_distance_m is not None:
            reasons.append(f"{location_distance_label}约{round(location_distance_m)}米")

        if not category_config.get('tracksPlants'):
            reasons.append(category_config.get('dataReason', '校园位置数据'))

        score = min(100, group['match_score'] + density_score + heat_score + distance_score + location_distance_score)
        ranked.append({
            'name': display_name,
            'number': number,
            'lng': round(anchor['lng'], 6),
            'lat': round(anchor['lat'], 6),
            'campus': group['campus'],
            'kind': 'ranked_place',
            'category': group['category'],
            'subCategory': group['sample_poi'].get('subCategory', ''),
            'score': round(score, 1),
            'reason': '；'.join(list(dict.fromkeys(reasons))[:4]),
            'plants': plants,
            'distance_m': round(group['nearest']['distance_m']) if group['nearest'] else (
                round(location_distance_m) if location_distance_m is not None else None
            ),
            'user_distance_m': round(location_distance_m) if location_distance_m is not None else None,
            'distance_source': (profile.get('ranking_location') or {}).get('source', ''),
            'college': group['nearest']['college'] if group['nearest'] else '',
        })

    actual_location = (profile.get('ranking_location') or {}).get('source') in ('amap', 'browser')
    if profile.get('categories') == ['canteen'] and actual_location:
        ranked.sort(
            key=lambda item: (
                item.get('user_distance_m') is not None,
                _rank_distance_sort_value(item),
                item['score']
            ),
            reverse=True
        )
    else:
        ranked.sort(
            key=lambda item: (
                item['score'],
                _rank_distance_sort_value(item)
            ),
            reverse=True
        )
    return _dedupe_and_rank_places(ranked, top_k)


def rank_campus_pois(query: str, plants: list, colleges: list, top_k: int = 2,
                      institution_location_requested: bool = False,
                      preferred_campus=None,
                      ranking_location=None) -> list:
    """统一 POI 规则评分：分类问题、筛 POI 候选，再按文本命中/密度/距离排序。"""
    if not _CAMPUS_POIS:
        return []
    profile = _classify_poi_query(
        query,
        plants,
        colleges,
        institution_location_requested,
        preferred_campus=preferred_campus,
        ranking_location=ranking_location
    )
    if not profile['categories']:
        return []

    max_distance_m = 450 if profile['has_scene'] or (
        profile['intent'] == 'canteen_lookup' and profile['wants_nearby']
    ) else 300
    candidates = _poi_candidates(profile, max_distance_m)
    if not candidates and profile.get('preferred_campus') and not profile.get('explicit_campus_filter'):
        profile = dict(profile)
        profile['campus_filter'] = None
        candidates = _poi_candidates(profile, max_distance_m)
    if not candidates:
        return []

    top_k = _scene_profile_top_k(profile, top_k, candidates)

    if profile['categories'] == ['plant'] and profile['wants_density']:
        dense_ranked = _rank_poi_density_clusters(candidates, profile, top_k, max_distance_m)
        if dense_ranked:
            return dense_ranked

    return _rank_poi_groups(candidates, profile, top_k, max_distance_m)


def rank_campus_places(query: str, plants: list, colleges: list, top_k: int = 2,
                        institution_location_requested: bool = False,
                        preferred_campus=None,
                        ranking_location=None) -> list:
    """规则评分版地点推荐：优先走统一 POI 场景配置，必要时回退到植物点聚合。"""
    if direct_configured_poi_matches(query):
        return []
    if _is_unsupported_data_query(query, plants, colleges, institution_location_requested):
        return []
    if not _should_rank_places(query, plants, colleges, institution_location_requested):
        return []

    poi_ranked = rank_campus_pois(
        query,
        plants,
        colleges,
        top_k=top_k,
        institution_location_requested=institution_location_requested,
        preferred_campus=preferred_campus,
        ranking_location=ranking_location
    )
    if poi_ranked:
        return poi_ranked
    if institution_location_requested and not _wants_nearby_plants(query):
        return []

    scene_profiles = _matched_scene_profiles(query)
    scenes = _matched_scene_rules(query)
    scene_rule_ids = {rule.get('id') for rule in scenes}
    scenes.extend(
        rule for rule in _scene_profiles_as_rules(scene_profiles)
        if rule.get('id') not in scene_rule_ids
    )
    has_scene = bool(scenes)
    campus_filter = _campus_filter_from_query(query) or _normalize_campus(preferred_campus)
    query_terms = _query_terms(query)
    target_plants = _direct_target_plant_names(query, plants, has_scene)
    use_college_radius = bool(colleges and (_wants_nearby_plants(query) or has_scene))
    max_distance_m = 450 if has_scene else 300
    if target_plants and has_scene:
        dense_ranked = _rank_dense_target_places(
            query,
            target_plants,
            scenes,
            colleges,
            campus_filter,
            top_k,
            max_distance_m,
            use_college_radius
        )
        if dense_ranked:
            return dense_ranked

    groups = {}

    for point in _TREE_POINTS:
        if not point.get('lng') or not point.get('lat'):
            continue
        campus = _campus(point['lng'], point['lat'])
        if campus_filter and campus != campus_filter:
            continue
        if target_plants and point['name'] not in target_plants:
            continue

        nearest = _nearest_college_distance(point, colleges) if colleges else None
        if use_college_radius and (not nearest or nearest['distance_m'] > max_distance_m):
            continue

        text = _point_text(point)
        term_score = min(24, sum(4 for term in query_terms if term in text))
        scene_score = 0
        scene_names = []
        for rule in scenes:
            hits = [term for term in rule['terms'] if term.lower() in text]
            place_hits = [term for term in rule['place_terms'] if term in (point.get('number') or '')]
            if hits or place_hits:
                scene_names.append(rule['name'])
            scene_score += min(34, len(set(hits)) * 5 + len(set(place_hits)) * 4)

        plant_score = 36 if target_plants and point['name'] in target_plants else 0
        text_score = max(term_score, scene_score, plant_score)
        if text_score <= 0 and not colleges:
            continue

        key = (campus, point.get('number') or f"{round(point['lng'], 5)},{round(point['lat'], 5)}")
        group = groups.setdefault(key, {
            'campus': campus,
            'number': point.get('number') or '暂无具体位置描述',
            'lng_sum': 0.0,
            'lat_sum': 0.0,
            'point_count': 0,
            'plants': {},
            'match_score': 0,
            'like_total': 0,
            'owner_count': 0,
            'scene_names': set(),
            'nearest': None,
            'points': [],
        })
        group['lng_sum'] += point['lng']
        group['lat_sum'] += point['lat']
        group['point_count'] += 1
        group['points'].append(point)
        group['plants'][point['name']] = group['plants'].get(point['name'], 0) + 1
        group['match_score'] = max(group['match_score'], text_score)
        group['like_total'] += int(point.get('likeCount') or 0)
        if point.get('owner_content') or point.get('owner_slogan'):
            group['owner_count'] += 1
        group['scene_names'].update(scene_names)
        if nearest and (not group['nearest'] or nearest['distance_m'] < group['nearest']['distance_m']):
            group['nearest'] = nearest

    ranked = []
    for group in groups.values():
        density_score = min(18, group['point_count'] * 2)
        diversity_score = min(16, len(group['plants']) * 4)
        heat_score = min(10, group['like_total'] * 0.8 + group['owner_count'] * 1.5)
        distance_score = 0
        if group['nearest']:
            distance_score = max(0, 28 * (1 - group['nearest']['distance_m'] / max_distance_m))

        top_plants = [
            name for name, _ in sorted(group['plants'].items(), key=lambda item: item[1], reverse=True)[:4]
        ]
        display_name = group['number']
        if top_plants and not re.search(r'[一-鿿]', display_name):
            display_name = f"{top_plants[0]}点位 {display_name}"
        reasons = []
        if group['scene_names']:
            reasons.append('匹配' + '、'.join(sorted(group['scene_names'])))
        if target_plants:
            reasons.append('包含' + '、'.join(top_plants[:3]))
        if group['nearest']:
            reasons.append(
                f"距{group['nearest']['building']}约{round(group['nearest']['distance_m'])}米"
            )
        reasons.append(f"聚合{len(group['plants'])}种植物、{group['point_count']}个真实点位")
        if group['owner_count']:
            reasons.append(f"含{group['owner_count']}条认养/留言信息")

        anchor = _representative_anchor_point(group['points'], group['number'], 'number')
        location_distance_score, location_distance_m, location_distance_label = _ranking_location_distance(
            {'categories': ['plant'], 'ranking_location': ranking_location},
            group['campus'],
            anchor['lng'],
            anchor['lat']
        )
        if location_distance_m is not None:
            reasons.append(f"{location_distance_label}约{round(location_distance_m)}米")

        score = min(
            100,
            group['match_score'] + density_score + diversity_score + heat_score + distance_score + location_distance_score
        )
        ranked.append({
            'name': display_name,
            'number': group['number'],
            'lng': round(anchor['lng'], 6),
            'lat': round(anchor['lat'], 6),
            'campus': group['campus'],
            'kind': 'ranked_place',
            'category': 'plant',
            'subCategory': top_plants[0] if top_plants else '植物',
            'score': round(score, 1),
            'reason': '；'.join(reasons[:4]),
            'plants': top_plants,
            'distance_m': round(group['nearest']['distance_m']) if group['nearest'] else (
                round(location_distance_m) if location_distance_m is not None else None
            ),
            'user_distance_m': round(location_distance_m) if location_distance_m is not None else None,
            'distance_source': (ranking_location or {}).get('source', ''),
            'college': group['nearest']['college'] if group['nearest'] else '',
        })

    ranked.sort(
        key=lambda item: (
            item['score'],
            _rank_distance_sort_value(item)
        ),
        reverse=True
    )
    return _dedupe_and_rank_places(ranked, top_k)


def build_ranked_place_context(ranked_places: list) -> str:
    if not ranked_places:
        return ''
    lines = [
        '以下是系统筛选出的校园推荐地点，请只围绕这些地点作答，并只推荐这些地点；'
        '回答中的推荐地点名称必须和 Top 名称完全一致，不要改写、替换或自行补充相邻地点：'
    ]
    for place in ranked_places:
        category_config = _poi_category_config(place.get('category'))
        if category_config.get('contextMode') == 'plants':
            lines.append(
                f"Top{place['rank']}【{place['name']}】校区：{place['campus']}；"
                f"代表植物：{'、'.join(place.get('plants', [])[:4]) or '暂无'}；"
                f"推荐依据：{place['reason']}"
            )
        else:
            lines.append(
                f"Top{place['rank']}【{place['name']}】校区：{place['campus']}；"
                f"位置：{place.get('number', '')}；推荐依据：{place['reason']}"
            )
    return '\n'.join(lines)


def build_direct_poi_context(pois: list) -> str:
    if not pois:
        return ''
    category_names = list(dict.fromkeys(
        _poi_category_config(poi.get('category')).get('contextName', '地点')
        for poi in pois
    ))
    label = '、'.join(category_names) if category_names else '地点'
    lines = [
        f'以下是用户明确询问的{label}位置，请只回答这些{label}；'
        '不要使用 Top 或排名表述，不要补充其他未命中的地点：'
    ]
    for poi in pois:
        lines.append(
            f"【{poi['name']}】校区：{poi.get('campus', '')}；位置：{_format_poi_number(poi)}"
        )
    return '\n'.join(lines)


def build_direct_canteen_context(canteens: list) -> str:
    return build_direct_poi_context(canteens)


def search_emotions(query: str, top_k: int = 3) -> list:
    """检索与查询相关的植树留言。"""
    query_lower = query.lower()
    # 中文没有空格分隔词，用2字符滑窗提取子串；同时保留英文空格分词
    tokens = set(kw for kw in query_lower.split() if kw)
    for i in range(len(query_lower) - 1):
        sub = query_lower[i:i+2]
        if re.search(r'[一-鿿]', sub):
            tokens.add(sub)
    if not tokens:
        return []
    results = []
    for e in _EMOTIONS:
        searchable = ' '.join(filter(None, [
            e.get('slogan', ''), e.get('content', ''), e.get('title', '')
        ])).lower()
        if any(kw in searchable for kw in tokens):
            results.append(e)
            if len(results) >= top_k:
                break
    return results


def build_emotion_context(emotions: list) -> str:
    if not emotions:
        return ''
    lines = ['\n以下是校园植树者的真实留言：']
    for e in emotions:
        lines.append(f"  [{e.get('title','')} · {e.get('owner','')}] 「{e.get('slogan','')}」{e.get('content','')[:80]}")
    return '\n'.join(lines)

# 用线程本地存储代替全局变量，每个请求线程拥有独立连接，避免竞态条件
_local = threading.local()
_rate_limit_buckets = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def rate_limit(max_requests: int, window_seconds: int = 60):
    """Small per-process limiter for the public JSON endpoints."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            now = monotonic()
            key = (request.remote_addr or 'unknown', request.endpoint)
            with _rate_limit_lock:
                bucket = _rate_limit_buckets[key]
                while bucket and bucket[0] <= now - window_seconds:
                    bucket.popleft()
                if len(bucket) >= max_requests:
                    return jsonify({'success': False, 'message': '请求过于频繁，请稍后重试'}), 429
                bucket.append(now)
            return view(*args, **kwargs)
        return wrapped
    return decorate


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(self)')
    return response

def get_cursor():
    if not hasattr(_local, 'db') or _local.db is None:
        _local.db = pymysql.connect(**DB_CONFIG)
    else:
        _local.db.ping(reconnect=True)
    _local.cursor = _local.db.cursor()
    return _local.cursor


@app.teardown_appcontext
def close_database(_error=None):
    cursor = getattr(_local, 'cursor', None)
    database = getattr(_local, 'db', None)
    if cursor is not None:
        cursor.close()
        _local.cursor = None
    if database is not None:
        database.close()
        _local.db = None

# 创建登录管理对象
login_manager = LoginManager()
login_manager.init_app(app)


# 创建用户类，继承UserMixin
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password


PASSWORD_HASH_PREFIXES = ('pbkdf2:', 'scrypt:')


def is_password_hash(password):
    return bool(password and password.startswith(PASSWORD_HASH_PREFIXES))


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_password, provided_password):
    if not stored_password or not provided_password:
        return False
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, provided_password)
    return stored_password == provided_password


def update_user_password(username, password):
    sql = "UPDATE users SET password=%s WHERE username=%s"
    c = get_cursor()
    c.execute(sql, (hash_password(password), username))
    _local.db.commit()


def create_user(username, password):
    sql = "insert into users (username, password) values (%s, %s)"
    c = get_cursor()
    c.execute(sql, (username, hash_password(password)))
    _local.db.commit()


def get_user(username):
    sql = "select * from users where username=%s"
    c = get_cursor()
    c.execute(sql, username)
    result = c.fetchone()
    if result:
        return User(result[0], result[1], result[2])
    return None


@login_manager.user_loader
def load_user(user_id):
    sql = "select * from users where id=%s"
    c = get_cursor()
    c.execute(sql, user_id)
    result = c.fetchone()
    if result:
        return User(result[0], result[1], result[2])
    return None


@app.route("/api/health", methods=["GET"])
def health():
    try:
        c = get_cursor()
        c.execute('SELECT 1')
        database = 'ok'
        status_code = 200
    except Exception:
        database = 'error'
        status_code = 503
    return jsonify({'status': 'ok', 'database': database}), status_code


@app.route("/api/register_json", methods=["POST"])
@rate_limit(5)
def register_json():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    if len(username) > 20 or len(password) < 8 or len(password) > 128:
        return jsonify({'success': False, 'message': '用户名最多20个字符，密码需为8至128个字符'}), 400
    if get_user(username):
        return jsonify({'success': False, 'message': '用户名已存在'}), 400
    create_user(username, password)
    return jsonify({'success': True})


@app.route("/api/login_json", methods=["POST"])
@rate_limit(10)
def login_json():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    user = get_user(username)
    if user and verify_password(user.password, password):
        if not is_password_hash(user.password):
            update_user_password(username, password)
        login_user(user)
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '用户名或密码有误'}), 401


@app.route("/api/change_password", methods=["POST"])
@rate_limit(5)
def change_password():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip()
    current_password = str(data.get('currentPassword', ''))
    new_password = str(data.get('newPassword', ''))
    if not username or not current_password or not new_password:
        return jsonify({'success': False, 'message': '用户名、当前密码和新密码不能为空'}), 400
    if len(new_password) < 8 or len(new_password) > 128:
        return jsonify({'success': False, 'message': '新密码需为8至128个字符'}), 400
    user = get_user(username)
    if not user or not verify_password(user.password, current_password):
        return jsonify({'success': False, 'message': '用户名或当前密码错误'}), 401
    update_user_password(username, new_password)
    return jsonify({'success': True})


@app.route("/api/chat", methods=["POST"])
@rate_limit(30)
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    if not isinstance(messages, list) or len(messages) > 30:
        return jsonify({'error': '消息格式无效或上下文过长'}), 400
    user_query = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    preferred_campus = _preferred_campus_from_request(data)
    ranking_location = _ranking_location_from_request(data)
    configured_category_requested = bool(_intent_categories_from_query(user_query))
    direct_pois = direct_configured_poi_matches(user_query)

    # 检索相关植物、学院位置和植树留言
    plants = search_plants(user_query)
    colleges = search_colleges(user_query)
    institution_location_requested = _looks_like_institution_location_query(user_query)
    nearby_requested = bool(colleges and _wants_nearby_plants(user_query))
    nearby_plants = find_nearby_plants(colleges) if nearby_requested else []
    if colleges and institution_location_requested and not nearby_requested:
        plants = []
    direct_plant_location_requested = _is_direct_plant_location_query(
        user_query,
        plants,
        colleges,
        institution_location_requested
    )
    direct_plant_names = _direct_location_plant_names(user_query, plants) if direct_plant_location_requested else []
    if direct_plant_names:
        plants = [plant for plant in plants if plant.get('name') in direct_plant_names]
    unsupported_data_query = _is_unsupported_data_query(
        user_query,
        plants,
        colleges,
        institution_location_requested
    )

    if unsupported_data_query:
        unsupported_label = _unsupported_data_label(user_query) or '相关地点'
        return jsonify({
            'choices': [{
                'message': {
                    'role': 'assistant',
                    'content': (
                        f'目前校园位置数据中暂无可靠的{unsupported_label}数据，'
                        '因此暂时不能推荐具体地点。地图不会标注未经核实的位置。'
                    )
                }
            }],
            'locations': [],
            'ranked_places': [],
            'unsupported': True,
        })

    # 第二阶段只接管主动选择 sakde 的四类宽泛场景。旧请求默认走原逻辑，
    # 精确地点、指定植物、学院附近和停车/餐饮查询均不改变。
    heatmap_fallback = None
    if (data.get('recommendationMode') == 'sakde' and not direct_pois
            and not direct_plant_location_requested and not institution_location_requested
            and not colleges and not any(c != 'plant' for c in _intent_categories_from_query(user_query))):
        heat_scenes = [s for s in _matched_scene_profiles(user_query)
                       if s.get('id') in SCENE_IDS]
        if heat_scenes and not _direct_target_plant_names(user_query, plants, True):
            heat_campus = _campus_filter_from_query(user_query) or preferred_campus or '普陀'
            try:
                heat_payload = _HEATMAP_STORE.get(heat_campus, heat_scenes[0]['id'])
                if heat_payload['places']:
                    return jsonify(heatmap_chat_response(heat_payload))
                heatmap_fallback = '该场景暂无有依据的热区推荐，使用原有规则推荐'
            except HeatmapUnavailable as exc:
                heatmap_fallback = str(exc)

    # 场景类问题无精确匹配时，注入代表性植物作为兜底上下文
    if not unsupported_data_query and not configured_category_requested and not plants and not colleges and not institution_location_requested:
        scenic_keywords = ['花', '树', '草', '竹', '松', '梅', '樱', '桂', '荷']
        seen, fallback = set(), []
        for kw in scenic_keywords:
            for p in search_plants(kw, top_k=2):
                if p['name'] not in seen:
                    seen.add(p['name'])
                    fallback.append(p)
                    if len(fallback) >= 10:
                        break
            if len(fallback) >= 10:
                break
        plants = fallback

    if unsupported_data_query or direct_plant_location_requested or direct_pois:
        ranked_places = []
    else:
        ranked_places = rank_campus_places(
            user_query,
            plants,
            colleges,
            top_k=2,
            institution_location_requested=institution_location_requested,
            preferred_campus=preferred_campus,
            ranking_location=ranking_location
        )
    ranked_place_context = build_ranked_place_context(ranked_places)
    direct_poi_context = build_direct_poi_context(direct_pois)
    plant_context = '' if ranked_places or direct_pois else build_plant_context(plants)
    college_context = '' if direct_pois else build_college_context(colleges)
    nearby_plant_context = '' if ranked_places or direct_pois else build_nearby_plant_context(nearby_plants)
    emotions = [] if unsupported_data_query or direct_pois else search_emotions(user_query)
    emotion_context = build_emotion_context(emotions)

    system_content = """你是华东师范大学（ECNU）的校园植物向导，像一个熟悉校园每棵树的老朋友，回答时自然亲切、有温度，适当使用 emoji 增加趣味感。

【回答规则】
1. 【禁止编造位置】只能使用下方数据中明确出现的位置，绝对不能补充、推测或编造任何数据中没有的位置信息
2. 【位置格式】回答精确位置时按数据回答；回答系统推荐地点时必须覆盖下方提供的全部 Top 地点；只有植物大量点位查询才列出 2~3 个代表性位置。末尾可加一句"左侧地图已标注相关位置，点击标记可导航 🗺️"
3. 【无位置记录】若数据中某植物没有位置记录，只说"暂无具体位置记录"，不做任何补充猜测
4. 【场景/情感类问题】若用户问的是场景推荐（如吃饭、散步、约会、拍照、赏花、学习等），只根据系统筛选出的植物、学院或食堂 POI 推荐地点，并说明推荐理由
5. 【学院位置】若用户询问学院、书院、研究院或楼宇在哪里，只回答其校区、建筑名和地址；不要编造路线、楼层或附近植物
6. 【学院附近植物】若用户询问某学院附近有什么植物，只能使用"命中学院附近的校园植物"数据；若没有该数据，直接说暂无可靠的附近植物记录；不得凭常识补充植物名
7. 【食堂/餐厅位置】若用户询问某个具体食堂在哪里，只回答该食堂/餐厅的位置，不要使用 Top 或排名表述；若用户泛泛询问吃饭地点，才使用系统筛选出的餐饮推荐地点；不要编造营业时间、窗口、菜品、价格或拥挤程度
8. 【完全无关问题】若问题与校园植物、校园景色、校园学院位置、校园餐饮位置完全无关，礼貌说明自己只了解校园植物和校园位置相关内容
9. 【推荐地点】若下方提供"系统筛选出的校园推荐地点"，必须逐一覆盖这些 Top 地点，回答中的地点名称必须与 Top 名称完全一致；不要改写、替换或补充其他未入选地点；不要展示后台分数、内部排序过程或地图定位数值
10. 【无数据类型】若系统提示本轮没有对应 POI 数据，只能说明暂无相关数据；不要猜测地点、不要推荐替代地点、不要说地图已标注
11. 回答要有层次感：按用户问题选择介绍植物、学院或食堂，最后给出数据中明确出现的位置

请用中文回答。
"""

    if unsupported_data_query:
        system_content += '\n\n本轮问题涉及当前 POI 数据中没有覆盖的对象；请直接说明暂无相关位置数据，不要猜测校园里可能出现的位置，也不要推荐植物点位或地图地点。'
    if direct_poi_context:
        system_content += '\n\n' + direct_poi_context
    if ranked_place_context:
        system_content += '\n\n' + ranked_place_context
    if not unsupported_data_query and preferred_campus and not _campus_filter_from_query(user_query):
        system_content += f'\n\n用户当前位置更接近{preferred_campus}校区；本轮未明确指定校区时，优先围绕{preferred_campus}校区作答。'
    if plant_context:
        system_content += '\n\n' + plant_context
    if college_context:
        system_content += '\n\n' + college_context
    if nearby_plant_context:
        system_content += '\n\n' + nearby_plant_context
    elif colleges and not direct_pois:
        if nearby_requested:
            system_content += '\n\n本轮没有命中学院附近植物数据；请直接说明暂无可靠的附近植物记录，不要猜测植物名称。'
        else:
            system_content += '\n\n用户本轮没有询问学院附近植物；回答学院位置时不要主动补充附近植物。'
    if emotion_context:
        system_content += '\n' + emotion_context

    if not DEEPSEEK_API_KEY:
        return jsonify({'error': 'AI 服务尚未配置'}), 503

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': DEEPSEEK_MODEL,
                'messages': [{'role': 'system', 'content': system_content}] + messages,
                'temperature': 0.3,
                'max_tokens': 1024,
            },
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.Timeout:
        return jsonify({'error': 'AI 服务响应超时，请稍后重试'}), 504
    except requests.RequestException:
        return jsonify({'error': 'AI 服务请求失败，请稍后重试'}), 502

    # 附加命中植物/学院/附近植物的经纬度坐标，供前端地图打点
    locations = []
    seen_locations = set()

    def add_location(name, lng, lat, number, campus, kind, **extra):
        if not lng or not lat:
            return
        key = (name, round(lng, 6), round(lat, 6), number)
        if key in seen_locations:
            return
        seen_locations.add(key)
        locations.append({
            'name': name,
            'lng': lng,
            'lat': lat,
            'number': number,
            'campus': campus,
            'kind': kind
        })
        locations[-1].update(extra)

    if direct_pois:
        for poi in direct_pois:
            add_location(
                poi.get('name', ''),
                poi.get('lng'),
                poi.get('lat'),
                _format_poi_number(poi),
                poi.get('campus', ''),
                poi.get('category', 'poi'),
                category=poi.get('category', ''),
                subCategory=poi.get('subCategory', '')
            )
    elif ranked_places:
        for place in ranked_places:
            add_location(
                place['name'],
                place['lng'],
                place['lat'],
                place['number'],
                place['campus'],
                'ranked_place',
                rank=place.get('rank'),
                score=place.get('score'),
                reason=place.get('reason'),
                plants=place.get('plants', []),
                distance_m=place.get('distance_m'),
                college=place.get('college', ''),
                category=place.get('category', ''),
                subCategory=place.get('subCategory', '')
            )
    else:
        for college in colleges:
            for building in college.get('buildings', []):
                add_location(
                    college.get('name', ''),
                    building.get('longitude'),
                    building.get('latitude'),
                    f"{building.get('name', '')}｜{building.get('address', '')}",
                    college.get('campus', ''),
                    'college'
                )

        for p in plants:
            coords = _TREE_COORDS.get(p['name'], [])
            if not direct_plant_location_requested:
                coords = coords[:20]
            for c in coords:
                add_location(p['name'], c['lng'], c['lat'], c['number'], _campus(c['lng'], c['lat']), 'plant')

        for item in nearby_plants:
            add_location(
                item['name'],
                item['lng'],
                item['lat'],
                f"{item.get('number') or '暂无具体位置描述'}（距{item['building']}约{item['distance_m']}米）",
                _campus(item['lng'], item['lat']),
                'nearby_plant'
            )

    result['locations'] = locations
    result['ranked_places'] = ranked_places
    if data.get('recommendationMode') == 'sakde':
        result['recommendation_engine'] = 'rule'
        if heatmap_fallback:
            result['heatmap_status'] = heatmap_fallback
    return jsonify(result)


# 运行Flask应用
if __name__ == "__main__":
    # sentence-transformers 会在首次请求时延迟导入大量模块。Windows 下
    # Flask 的 watchdog 可能把这些导入误判为源码变更并重启进程，导致
    # 正在处理的 /api/chat 请求出现 ECONNRESET，因此禁用自动重载。
    debug_enabled = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_enabled, use_reloader=False)
