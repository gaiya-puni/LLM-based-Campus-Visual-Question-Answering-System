"""`/api/chat` 端点，以及行程、指哪问哪、图片附件、未收录采集的辅助实现。"""

import json
import os
import re

import campus_config
import paths  # noqa: F401
import place_extraction
import requests
import userdata_store
from flask import Blueprint, jsonify, request
from flask_app import app, rate_limit
from runtime import facade
from itinerary import (build_chat_reply, build_itinerary_context, is_itinerary_query,
                       render_fallback_text)
from llm_config import _LLM_PROXIES
from poi_data import _CAMPUS_POIS, _HEATMAP_STORE, _TREE_COORDS, _campus
from retrieval_engine import (
    _campus_filter_from_query,
    _direct_location_plant_names,
    _direct_target_plant_names,
    _distance_meters,
    _format_poi_number,
    _intent_categories_from_query,
    _is_direct_plant_location_query,
    _is_unsupported_data_query,
    _looks_like_institution_location_query,
    _matched_scene_profiles,
    _plant_candidates_from_location_query,
    _preferred_campus_from_request,
    _ranking_location_from_request,
    _unsupported_data_label,
    _wants_nearby_plants,
    build_college_context,
    build_direct_plant_location_reply,
    build_direct_poi_context,
    build_emotion_context,
    build_nearby_plant_context,
    build_plant_context,
    build_ranked_place_context,
    direct_configured_poi_matches,
    find_nearby_plants,
    rank_campus_places,
    search_colleges,
    search_emotions,
    search_plants,
)
from scene_heatmaps import SCENE_IDS, HeatmapUnavailable, heatmap_chat_response

bp = Blueprint('chat_api', __name__)


# ---------------------------------------------------------------------------
# 一日行程规划：骨架由 itinerary.py 确定性编排，大模型只负责写文案
# ---------------------------------------------------------------------------

_ITINERARY_PERIOD_QUERIES = (
    ('morning', (('校园里适合散步的地方', 'walk'),
                 ('校园里适合拍照的地方', 'photo'))),
    ('noon', (('校园里吃饭的食堂餐厅', 'dining'),)),
    ('afternoon', (('校园里哪里适合赏花', 'flower_viewing'),
                   ('校园里适合拍照的景点', 'photo'),
                   ('校园里适合安静自习的地方', 'study'))),
)

_ITINERARY_SYSTEM_PROMPT = (
    '你是{school_name}校园导览助手。用户第一次来校，需要一份一日行程。'
    '下面给出的站点顺序、名称与推荐理由已由系统确定，你只能据此撰写自然语言行程说明，'
    '不得新增、替换或删除地点，也不得编造营业时间、票价或未提供的任何信息。'
    '下面没有给出的距离、耗时等数字一律不要写，也不要估算，只说"步行可达""建议骑行"这类说法。'
    '请分上午/中午/下午三段描述，说明每站的推荐理由、建议停留时长，以及相邻两站如何前往。'
)


def _itinerary_summary(context: str, campus=None):
    """用当前生效的大模型生成行程文案；失败返回 None，由调用方走确定性兜底文案。"""
    llm = facade.resolve_llm()
    if not llm or not context:
        return None
    try:
        resp = requests.post(
            llm['api_url'],
            headers={'Authorization': f"Bearer {llm['api_key']}", 'Content-Type': 'application/json'},
            json={
                'model': llm['model'],
                'messages': [
                    {'role': 'system', 'content': _ITINERARY_SYSTEM_PROMPT.format(
                        school_name=(campus_config.school_of(
                            campus or campus_config.default_campus()).get('name') or '校园'))},
                    {'role': 'user', 'content': context},
                ],
                'temperature': 0.4,
                'max_tokens': 1024,
            },
            timeout=30,
            proxies=_LLM_PROXIES,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content']
        return content.strip() or None
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return None


def _itinerary_origin(campus, ranking_location, view_center):
    """行程起点归属：优先用户定位，其次地图针尖（"从我看到的这个位置出发"）。

    针尖只有**确实落在行程校区内**时才可用（`campus_config.campus_at`，超出校区的
    信任半径即返回 None）。否则会拿另一个校区的坐标当起点，第一站莫名为远，整条行程
    还可能被判成骑行。两者都不可用时返回 None，由 `plan_day` 从上午首个候选起步。
    """
    if isinstance(ranking_location, dict):
        return ranking_location
    if not isinstance(view_center, dict):
        return None
    try:
        lng, lat = float(view_center['lng']), float(view_center['lat'])
    except (KeyError, TypeError, ValueError):
        return None
    if campus_config.campus_at(lng, lat) == campus:
        return {'lng': lng, 'lat': lat}
    return None


def _build_itinerary_reply(campus, origin_location):
    """编排一日行程并生成文案；站点不足时返回 None，由调用方回退到普通推荐。

    候选只来自既有的规则排序结果（`rank_campus_places`），不读取也不修改
    热力图算法与任何缓存，因此与 SAKDE 侧的演进完全解耦。

    `origin_location` 只决定"从哪儿出发"（起点本身不进站点列表）：它既是最近邻串联的
    起点，也按既有口径交给 `rank_campus_places` 参与距离加权（真实定位才带 `source`
    标记，针尖坐标不带，因此不会改变既有排序权重）。
    """
    period_candidates = {}
    for period, sources in _ITINERARY_PERIOD_QUERIES:
        items = []
        for query, scene in sources:
            ranked = rank_campus_places(
                query, [], [], top_k=3,
                preferred_campus=campus,
                ranking_location=origin_location,
                strict_campus=True
            )
            items.extend(dict(place, scene=scene) for place in ranked)
        period_candidates[period] = items

    origin = None
    if isinstance(origin_location, dict):
        origin = (origin_location.get('lng'), origin_location.get('lat'))
    plan = facade.plan_day(campus, period_candidates, origin)
    if (not plan['sufficient']
            or any(stop.get('campus') != campus for stop in plan.get('stops', []))):
        return None

    context = build_itinerary_context(campus, plan)
    summary = _itinerary_summary(context, campus)
    return build_chat_reply(
        campus, plan, summary or render_fallback_text(campus, plan), fallback=summary is None)


# ---------------------------------------------------------------------------
# "指哪问哪"：地图中心针尖坐标进决策链
# ---------------------------------------------------------------------------

#: 指示性问句：用户问的是**眼前/屏幕中心**那个点，而不是某个已知地点名。
#: 末行补的是口语里的"这个位置"：只收"指代眼前"的明确问法，**不收裸词**——针尖分支排在
#: 场景/行程分支之前，若裸"这个位置"就算指示性，"这个位置适合散步吗"会被针尖抢走。
_INDICATIVE_QUERY_PATTERN = re.compile(
    r'这里是哪|这里是哪里|这是哪|这是哪里|这儿是哪|这儿是哪里|这是什么地方|'
    r'我在哪|我在哪里|我现在在哪|这块是什么|这一块是什么|眼前是什么|面前是什么|'
    r'地图上这个点|地图中心|屏幕中心|针尖|指的位置|当前这个位置|'
    r'我看到的这个位置|看到的这个位置|这个位置是|这个位置在哪|这个位置有什么|地图上这个位置'
)

#: 缩放级别 → 查询半径（米）：地图放得越大，答得越细
_NEEDLE_RADIUS_BY_ZOOM = ((16, 80.0), (15, 150.0), (14, 300.0))
_NEEDLE_DEFAULT_RADIUS_M = 600.0


def view_center_from_request(data: dict):
    """解析地图中心（"针尖"）坐标；缺失或非法返回 None。

    高德 `getCenter()` 返回的就是 GCJ-02，与全部 POI、热度缓存、距离计算同坐标系，
    **不需要任何转换**；这里只做数值与量级校验，避免脏值把"最近 POI"算到天边。
    """
    raw = data.get('viewCenter')
    if not isinstance(raw, dict):
        return None
    try:
        lng = float(raw.get('lng'))
        lat = float(raw.get('lat'))
    except (TypeError, ValueError):
        return None
    if not (73.0 <= lng <= 136.0 and 3.0 <= lat <= 54.0):
        return None
    try:
        zoom = float(raw.get('zoom'))
    except (TypeError, ValueError):
        zoom = 16.0
    return {'lng': lng, 'lat': lat, 'zoom': zoom}


def needle_radius_m(zoom: float) -> float:
    """按缩放级别取查询半径：街道级只答眼前，缩小时给出片区级答案。"""
    for threshold, radius in _NEEDLE_RADIUS_BY_ZOOM:
        if zoom >= threshold:
            return radius
    return _NEEDLE_DEFAULT_RADIUS_M


def nearest_pois(lng: float, lat: float, radius_m: float, limit: int = 3) -> list:
    """取针尖附近最近的校园 POI（带距离）；线性扫描数千条，毫秒级。"""
    found = []
    for poi in _CAMPUS_POIS:
        poi_lng, poi_lat = poi.get('lng'), poi.get('lat')
        if not poi_lng or not poi_lat:
            continue
        distance = _distance_meters(lng, lat, poi_lng, poi_lat)
        if distance > radius_m:
            continue
        found.append({**poi, 'distance_m': round(distance)})
    found.sort(key=lambda item: item['distance_m'])
    return found[:limit]


def is_indicative_query(query: str) -> bool:
    """问句是否指向"屏幕中心/眼前"（而非某个已知地点名）。"""
    return bool(query) and bool(_INDICATIVE_QUERY_PATTERN.search(query))


def needle_answer(view_center: dict) -> dict:
    """由针尖坐标生成**确定性**回答（不调模型）：零幻觉、秒回，并给出可打点的点位。

    校区一律由 `campus_at()` 按**地理**判定，**完全不看界面选中的校区**——
    这正是"我在看哪"与"我选了哪个校区"必须分开的地方。
    """
    lng, lat, zoom = view_center['lng'], view_center['lat'], view_center['zoom']
    radius_m = needle_radius_m(zoom)
    campus = campus_config.campus_at(lng, lat)
    pois = nearest_pois(lng, lat, radius_m, limit=3)
    locations = [{
        'name': poi.get('name') or '',
        'lng': poi.get('lng'),
        'lat': poi.get('lat'),
        'number': poi.get('locationName') or poi.get('number') or '',
        'campus': poi.get('campus') or campus or '',
        'kind': 'needle',
        'distance_m': poi.get('distance_m'),
    } for poi in pois]
    lines = []
    if not campus:
        nearest = campus_config.campus_of(lng, lat)
        offset = campus_config.distance_m(lng, lat, *campus_config.center(nearest))
        lines.append(f'你现在看的位置**不在已登记的校区范围内**'
                     f'（离最近的 {nearest} 校区约 {round(offset)} 米）。')
        lines.append('地图上不会标注未经核实的位置；把地图拖回校园里再问一次，就能看到具体地点。')
        return {
            'choices': [{'message': {'role': 'assistant', 'content': '\n\n'.join(lines)}}],
            'locations': [],
            'ranked_places': [],
            'needle': {'campus': None, 'radiusM': radius_m, 'zoom': zoom},
        }
    lines.append(f'你现在看的位置在 **{campus}** 校区范围内。')
    if pois:
        labels = '、'.join(f"{poi.get('name')}（约 {poi.get('distance_m')} 米）" for poi in pois)
        lines.append(f'针尖附近最近的校园地点：{labels}。')
        if zoom < 14:
            lines.append('把地图放大一些再问，我能答得更具体。')
    else:
        lines.append(f'针尖周围 {round(radius_m)} 米内没有记录到的校园地点；'
                     '可以放大地图再问，或直接说出地点名。')
    return {
        'choices': [{'message': {'role': 'assistant', 'content': '\n\n'.join(lines)}}],
        'locations': locations,
        'ranked_places': [],
        'needle': {'campus': campus, 'radiusM': radius_m, 'zoom': zoom},
    }


# ---------------------------------------------------------------------------
# 图片输入（多模态）：附件校验、消息构造与"图中文字线索 → 地图打点"
# ---------------------------------------------------------------------------

#: 单轮最多几张图（与前端 imageAttach.ts 的 MAX_ATTACHMENTS 同口径）
MAX_ATTACHMENTS = 2
#: 单张图片体积上限（base64 解码后的字节）
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
#: 允许的图片类型
ATTACHMENT_MIMES = ('image/jpeg', 'image/png', 'image/webp')


def _base64_size(payload: str) -> int:
    """由 base64 文本推算原始字节数（去掉 padding 补位）。"""
    padding = 2 if payload.endswith('==') else 1 if payload.endswith('=') else 0
    return max(0, len(payload) * 3 // 4 - padding)


def parse_attachments(data: dict):
    """校验请求体里的图片附件，返回 `(可用附件, 错误说明)`。

    只接受 `data:image/{jpeg,png,webp};base64,...` 形式：声明的 mime 必须与 dataUrl
    前缀一致，张数与单张体积都有上限。图片**不落盘**，日志只记张数与体积、不记内容。
    """
    raw = data.get('attachments')
    if not raw:
        return [], ''
    if not isinstance(raw, list):
        return [], '图片格式无效'
    if len(raw) > MAX_ATTACHMENTS:
        return [], f'一次最多上传 {MAX_ATTACHMENTS} 张图片'
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], '图片格式无效'
        data_url = str(entry.get('dataUrl') or '').strip()
        mime = str(entry.get('mime') or '').strip().lower()
        if not data_url.startswith('data:image/') or ';' not in data_url:
            return [], '图片格式无效'
        declared = data_url[len('data:'):data_url.index(';')].strip().lower()
        if declared != mime or mime not in ATTACHMENT_MIMES:
            return [], '仅支持 JPG / PNG / WebP 图片'
        payload = data_url.split(',', 1)[1] if ',' in data_url else ''
        if not payload:
            return [], '图片内容为空'
        size = _base64_size(payload)
        if size > MAX_ATTACHMENT_BYTES:
            return [], f'单张图片不能超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB'
        items.append({'mime': mime, 'dataUrl': data_url, 'bytes': size})
    return items, ''


def build_llm_messages(messages: list, attachments: list) -> list:
    """把附件并入**最后一条用户消息**，构成 OpenAI 多模态 content 数组。

    历史轮次保持字符串：图片只在当轮参与推理，否则每轮都要重发 base64，
    token 与带宽都会失控。
    """
    if not attachments:
        return messages
    last_user = None
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get('role') == 'user':
            last_user = index
    built = []
    for index, message in enumerate(messages):
        if index != last_user or not isinstance(message, dict):
            built.append(message)
            continue
        text = str(message.get('content') or '').strip() or '请看这张图片。'
        parts = [{'type': 'text', 'text': text}]
        parts.extend({'type': 'image_url', 'image_url': {'url': item['dataUrl']}}
                     for item in attachments)
        built.append({**message, 'content': parts})
    return built


def _append_notice(result: dict, notice: str) -> None:
    """把一条提示追加到模型回答末尾（前端按 Markdown 渲染成引用块）。"""
    choices = result.get('choices') or []
    if not choices or not isinstance(choices[0], dict):
        return
    message = choices[0].get('message') or {}
    message['content'] = f"{message.get('content') or ''}\n\n> {notice}"
    choices[0]['message'] = message


#: 图中线索抽取的提示词：只要**可读**的地点名，禁止猜测
_PHOTO_EXTRACT_PROMPT = (
    '你是图像信息抽取器。只输出 JSON，不要解释、不要 Markdown 代码块。'
    '格式：{"places": ["..."], "text_seen": "图中可读文字，没有就留空"}。'
    'places 只填**图中真实可读**的校园地点名（楼名、场馆、路牌、标牌、门名、店名），'
    '读不出来就填空数组，**禁止猜测或补全**。'
)


def parse_places_json(text: str) -> list:
    """从模型返回里稳健地取出地点名列表（容忍代码块包裹与前后废话）。"""
    if not text:
        return []
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return []
    places = payload.get('places') if isinstance(payload, dict) else None
    if not isinstance(places, list):
        return []
    names = []
    for item in places:
        name = str(item or '').strip()
        if len(name) >= 2 and name not in names:
            names.append(name)
    return names[:8]


def extract_places_from_photo(attachments: list, vision: dict) -> list:
    """用视觉模型抽取图中可读的地点名；任何失败都返回 []（**不影响主回答**）。"""
    content = [{'type': 'text', 'text': _PHOTO_EXTRACT_PROMPT}]
    content.extend({'type': 'image_url', 'image_url': {'url': item['dataUrl']}}
                   for item in attachments)
    try:
        resp = requests.post(
            vision['api_url'],
            headers={'Authorization': f"Bearer {vision['api_key']}",
                     'Content-Type': 'application/json'},
            json={
                'model': vision['model'],
                'messages': [{'role': 'user', 'content': content}],
                'temperature': 0.0,
                'max_tokens': 300,
            },
            timeout=45,
            proxies=_LLM_PROXIES,
        )
        if resp.status_code != 200:
            print(f'[photo] 图中线索抽取失败：HTTP {resp.status_code}')
            return []
        choices = resp.json().get('choices') or [{}]
        message = (choices[0] or {}).get('message') or {}
        return parse_places_json(message.get('content') or '')
    except requests.RequestException as error:
        print(f'[photo] 图中线索抽取请求失败：{error.__class__.__name__}')
        return []
    except ValueError:
        print('[photo] 图中线索抽取返回的不是合法 JSON')
        return []


def build_photo_poi_context(pois: list) -> str:
    """把"图中线索命中的校园地点"写进提示词上下文。

    否则会出现自相矛盾：地图上已经标出该地点，模型却回答"资料里没有收录"。
    """
    if not pois:
        return ''
    lines = ['【图中可读文字命中的校园地点（本轮真实数据，可直接使用）】']
    for poi in pois[:3]:
        place = poi.get('locationName') or poi.get('number') or ''
        lines.append(f"- {poi.get('name')}｜校区：{poi.get('campus') or ''}"
                     + (f'｜位置：{place}' if place else ''))
    lines.append('回答时请直接使用上述地点名与校区，不要声称未收录这些地点。')
    return '\n'.join(lines)


def match_pois_by_names(names: list) -> list:
    """把抽取到的地点名匹配到校园 POI（双向包含，短名过滤），按 POI 名长度排序。"""
    matches = []
    seen = set()
    for name in names:
        candidate = str(name or '').strip()
        # 单字候选（"楼"、"A"）会命中一大片 POI，直接跳过；这里与抽取阶段的口径一致
        if len(candidate) < 2:
            continue
        for poi in _CAMPUS_POIS:
            poi_name = str(poi.get('name') or '').strip()
            if len(poi_name) < 2:
                continue
            if name not in poi_name and poi_name not in name:
                continue
            key = poi.get('id') or (poi_name, poi.get('lng'), poi.get('lat'))
            if key in seen:
                continue
            seen.add(key)
            matches.append(poi)
    matches.sort(key=lambda poi: len(str(poi.get('name') or '')), reverse=True)
    return matches[:5]


def _image_capability_clause() -> str:
    """图片口径**按部署能力生成**（追加进 system prompt 的独立段落）。

    旧实现写死"你**没有**图像识别能力"，导致即使部署了视觉模型，助手仍对用户说
    "我看不到图片"，并把"本部署未启用"错误表述成"模型天生不会看图"。
    """
    if facade.vision_available():
        return (
            '【图片输入】本轮可能随消息带了图片。只有本轮上下文里确实带了图片时才可以说你看过图；'
            '可以描述图片内容，但不得仅凭图片猜测地名——只有当图中出现可读文字'
            '（路牌、标牌、建筑名、截图文字）或与下方数据一致时才给出地点；'
            '看不出来就直说"从图中看不出具体位置，请补充地点名"。'
        )
    return (
        '【图片输入】本部署未启用图片理解能力。用户提到截图、照片、图片时，如实说明'
        '"本部署未启用图片理解，请直接用文字描述地点或问题"，'
        '不要声称自己天生不具备看图能力，也不要假装看过图片。'
    )


# ---------------------------------------------------------------------------
# 用户共建：未收录地点的静默采集（纯旁路，不改变任何回答）
# ---------------------------------------------------------------------------

#: inline 模式下抽取大模型的超时（秒）：宁可这次不判，也不能拖慢用户等待
USERDATA_LLM_TIMEOUT_SECONDS = 6

def userdata_capture_enabled() -> bool:
    """采集总开关：`USERDATA_CAPTURE=0/false/off` 时整个未收录采集都不执行（默认开启）。

    自动化测试与"不想被收集用户问句"的部署用它一次性关掉，无需改代码。
    **每次调用都读环境变量**（不是模块常量）：同一进程里先跑测试模块、后跑功能模块也生效。
    """
    return (os.getenv('USERDATA_CAPTURE', 'on') or 'on').strip().lower() \
        not in ('0', 'false', 'off', 'no')

#: "用户确实在问一个地点"的闸门：只有命中它、且本轮一个点位都没给出时才采集，
#: 避免把闲聊与纯场景问句也存进待确认清单。
_CAPTURE_LOCATION_INTENT = re.compile(
    r'在哪|哪里|哪边|哪栋|哪个门|怎么走|怎么去|怎么到|位置|地址|导航|叫什么|哪条路')

#: 采集的兜底闸门：命中"方位词"或"疑问口吻"之一才值得记。没有它，一句"今天天气真好呀"
#: 也会因为命中"天气"这个无数据类别而被收进未收录清单。
_CAPTURE_QUESTION_HINT = re.compile(
    r'吗|呢|\?|？|附近|周边|周围|有没有|能不能|多少钱|几点|怎么样|怎么卖')

_KNOWN_PLACE_NAMES = None


def _known_place_names() -> frozenset:
    """已收录地点的归一化名称集合（名称/展示名/别名），用于把"已知地点"排除在抽取之外。

    `_CAMPUS_POIS` 在导入时一次加载完毕（数千条），这里只归一化一次并缓存，之后每次采集
    都是集合查找，不增加问答耗时。
    """
    global _KNOWN_PLACE_NAMES
    if _KNOWN_PLACE_NAMES is None:
        names = set()
        for poi in _CAMPUS_POIS:
            values = [poi.get('name'), poi.get('locationName')]
            values.extend((poi.get('meta') or {}).get('aliases') or [])
            for value in values:
                key = place_extraction.normalize_key(value)
                if key:
                    names.add(key)
        _KNOWN_PLACE_NAMES = frozenset(names)
    return _KNOWN_PLACE_NAMES


def _capture_campus(user_query: str, preferred_campus=None):
    """采集用的校区：问句里显式写的优先于界面上选的（与回答口径一致）。"""
    return _campus_filter_from_query(user_query) or preferred_campus


def _extract_llm_call(messages):
    """`place_extraction` 的大模型回调：沿用与行程文案相同的服务商选择与直连口径。"""
    llm = facade.resolve_llm()
    if not llm:
        return None
    response = requests.post(
        llm['api_url'],
        headers={'Authorization': f"Bearer {llm['api_key']}", 'Content-Type': 'application/json'},
        json={
            'model': llm['model'],
            'messages': messages,
            'temperature': 0.0,
            'max_tokens': 256,
        },
        timeout=USERDATA_LLM_TIMEOUT_SECONDS,
        proxies=_LLM_PROXIES,
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']


def _capture_unknown_place(user_query, campus=None, engine='', record_unresolved=True, extra_known=None):
    """未收录地点采集：先规则、后大模型兜底，结果只进待确认清单。

    **绝不改变本轮回答**：全程 try/except，任何失败只写一行日志（不含用户原文）并返回 None。
    默认 batch 模式下不产生额外网络调用，只把判不出来的问句留给审核页批量研判。

    `record_unresolved=False`：连"待研判问句"也不记（用于采点②，避免把场景问句堆进队列）。
    `extra_known`：额外视为"已收录"的名字（例如本轮回答里刚刚标注过的点位）。
    `USERDATA_CAPTURE=0/false/off` 可整体关掉采集（自动化测试与不希望被收集的部署用）。
    """
    if not userdata_capture_enabled():
        return None
    try:
        query = (user_query or '').strip()
        if not query or len(query) > 200:
            return None
        if not (_CAPTURE_LOCATION_INTENT.search(query) or _CAPTURE_QUESTION_HINT.search(query)):
            return None
        known_names = _known_place_names()
        if extra_known:
            known_names = known_names | frozenset(
                key for key in (place_extraction.normalize_key(value) for value in extra_known) if key)
        mode = place_extraction.extraction_mode()
        outcome = place_extraction.extract(
            query,
            campus=campus,
            known_names=known_names,
            llm_call=_extract_llm_call if mode == 'inline' else None,
            mode=mode,
        )
        if outcome['items']:
            written = userdata_store.record_extracted_places(
                outcome['items'], query=query, campus=campus,
                source=outcome['via'] or 'rule', engine=engine)
            if written:
                userdata_store.rebuild_pending()
                app.logger.info('userdata captured %s candidate(s) via %s', written, outcome['via'])
            return written
        if record_unresolved and outcome.get('deferred') and userdata_store.record_unresolved_query(
                query, campus=campus, engine=engine):
            app.logger.info('userdata deferred one unresolved query')
        return 0
    except Exception as exc:
        app.logger.warning('userdata capture failed: %s', type(exc).__name__)
        return None


@bp.route("/api/chat", methods=["POST"])
@rate_limit(30)
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    if not isinstance(messages, list) or len(messages) > 30:
        return jsonify({'error': '消息格式无效或上下文过长'}), 400
    # 逐条校验而不是容错跳过：畸形 message（缺 role、content 不是字符串）此前会抛 KeyError
    # 变成 500，调用方看不到任何线索。这里明确回 400，把契约写死。
    for message in messages:
        if (not isinstance(message, dict)
                or message.get('role') not in ('user', 'assistant', 'system')
                or not isinstance(message.get('content'), str)):
            return jsonify({
                'error': '消息格式无效：每条消息需包含 role（user/assistant/system）'
                         '与字符串类型的 content',
            }), 400
    user_query = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    # 图片附件：校验失败直接 400（张数/类型/单张体积），不静默丢弃——否则用户会以为图传上去了。
    attachments, attachment_error = parse_attachments(data)
    if attachment_error:
        return jsonify({'error': attachment_error}), 400
    preferred_campus = _preferred_campus_from_request(data)
    ranking_location = _ranking_location_from_request(data)
    configured_category_requested = bool(_intent_categories_from_query(user_query))
    direct_pois = direct_configured_poi_matches(user_query)
    # 行程意图是**强信号**，且 `is_itinerary_query` 内部已排除精确地点查询
    # （`_LOCATION_LOOKUP`：在哪里/怎么走/导航…）。因此它一旦命中，就应优先于"指哪问哪"
    # 与机构位置链路——否则"从我看到的这个位置出发，规划一下普陀校区的参观路线"这类句子
    # 里既有指示词又有"校区/位置/路线"，会被那两条链路依次抢走，行程永远接管不到。
    itinerary_intent = is_itinerary_query(user_query)

    # 检索相关植物、学院位置和植树留言
    plants = search_plants(user_query)
    location_plant_candidates = plants or _plant_candidates_from_location_query(user_query)
    colleges = search_colleges(user_query)
    named_location_plants = _direct_location_plant_names(user_query, plants)
    institution_location_requested = (
        _looks_like_institution_location_query(user_query)
        and not named_location_plants
    )
    nearby_requested = bool(colleges and _wants_nearby_plants(user_query))
    nearby_plants = find_nearby_plants(colleges) if nearby_requested else []

    # ---- "指哪问哪"：屏幕中心针尖坐标优先于界面选中校区 ----
    # 触发条件刻意保守：带 viewCenter + 问句指示性 + 既有直达通道（POI/植物/学院）都没命中，
    # 以免打断"问点名地点"的原有链路。命中后走确定性作答，不再调模型。
    # 行程意图例外：它问的是"怎么安排一整天"，而不是"眼前这个点是什么"，因此让给行程分支。
    view_center = view_center_from_request(data)
    if (view_center and is_indicative_query(user_query) and not itinerary_intent
            and not direct_pois and not plants and not colleges):
        answer = needle_answer(view_center)
        answer['recommendation_engine'] = 'needle'
        return jsonify(answer)
    if colleges and institution_location_requested and not nearby_requested:
        plants = []
    direct_plant_location_requested = _is_direct_plant_location_query(
        user_query,
        location_plant_candidates,
        colleges,
        institution_location_requested
    )
    if direct_plant_location_requested and not plants:
        plants = location_plant_candidates
    direct_plant_names = (_direct_location_plant_names(user_query, plants)
                          if direct_plant_location_requested else [])
    if direct_plant_location_requested and not direct_plant_names and plants is location_plant_candidates:
        direct_plant_names = [plant.get('name') for plant in plants if plant.get('name')]
    if direct_plant_names:
        plants = [plant for plant in plants if plant.get('name') in direct_plant_names]
    direct_plant_campus = None
    if direct_plant_location_requested:
        explicit_campus = _campus_filter_from_query(user_query)
        requested_campus = explicit_campus or preferred_campus
        campus_has_target = bool(requested_campus and any(
            _campus(coord.get('lng'), coord.get('lat')) == requested_campus
            for plant in plants
            for coord in _TREE_COORDS.get(plant.get('name'), [])
        ))
        # An explicitly named campus is strict. A UI-selected campus is used
        # when it has data; otherwise return the available campus so the UI can
        # switch with its existing warning.
        if explicit_campus or campus_has_target:
            direct_plant_campus = requested_campus
    unsupported_data_query = _is_unsupported_data_query(
        user_query,
        plants,
        colleges,
        institution_location_requested
    )

    if unsupported_data_query:
        unsupported_label = _unsupported_data_label(user_query) or '相关地点'
        # 采集点①：这是最典型的"问了但我们没有"——趁回答"暂无数据"时把可疑地名收进待确认清单
        _capture_unknown_place(user_query, _capture_campus(user_query, preferred_campus), 'unsupported')
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

    # 一日行程规划：用纯规则判定意图，优先于下面的 SAKDE 场景分支；命中即直接返回，
    # 未命中的查询完全走原逻辑。它与场景热图同属"新模式"的能力，因此同样只在用户主动
    # 选择 sakde 时接管，保证常规导航（默认路径）的行为与过去完全一致；站点不足时记一条
    # 提示，交给下面的普通推荐继续回答（与 heatmap_status 同一范式）。
    # 这里**不再**用 `institution_location_requested` 当守卫：行程意图已由 itinerary_intent
    # 单独判定，再叠加机构位置标志只会让"…普陀校区的参观路线"这类问句被两头落空。
    # 起点优先用户定位，其次地图针尖——用户说"从我看到的这个位置出发"时就是后者。
    itinerary_status = None
    if (data.get('recommendationMode') == 'sakde' and itinerary_intent
            and not direct_pois and not direct_plant_location_requested):
        itinerary_campus = _campus_filter_from_query(user_query) or preferred_campus or campus_config.default_campus()
        itinerary_reply = _build_itinerary_reply(
            itinerary_campus,
            _itinerary_origin(itinerary_campus, ranking_location, view_center)
        )
        if itinerary_reply:
            return jsonify(itinerary_reply)
        itinerary_status = '一日行程的可用地点不足，已改为按单场景推荐'

    # 第二阶段只接管主动选择 sakde 的四类宽泛场景。旧请求默认走原逻辑，
    # 精确地点、指定植物、学院附近和停车/餐饮查询均不改变。
    heatmap_fallback = None
    if (data.get('recommendationMode') == 'sakde' and not direct_pois
            and not direct_plant_location_requested and not institution_location_requested
            and not colleges and not any(c != 'plant' for c in _intent_categories_from_query(user_query))):
        heat_scenes = [s for s in _matched_scene_profiles(user_query)
                       if s.get('id') in SCENE_IDS]
        if heat_scenes and not _direct_target_plant_names(user_query, plants, True):
            heat_campus = (_campus_filter_from_query(user_query)
                           or preferred_campus or campus_config.default_campus())
            try:
                heat_payload = _HEATMAP_STORE.get(heat_campus, heat_scenes[0]['id'])
                if heat_payload['places']:
                    return jsonify(heatmap_chat_response(heat_payload))
                heatmap_fallback = '该场景暂无有依据的热区推荐，使用原有规则推荐'
            except (HeatmapUnavailable, ValueError) as exc:
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
    plant_context = '' if ranked_places or direct_pois else build_plant_context(
        plants,
        campus_filter=direct_plant_campus,
        direct_location=direct_plant_location_requested,
    )
    college_context = '' if direct_pois else build_college_context(colleges)
    nearby_plant_context = '' if ranked_places or direct_pois else build_nearby_plant_context(nearby_plants)
    emotions = [] if unsupported_data_query or direct_pois else search_emotions(user_query)
    emotion_context = build_emotion_context(emotions)

    answer_campus = (_campus_filter_from_query(user_query)
                     or preferred_campus or campus_config.default_campus())
    answer_school = campus_config.school_of(answer_campus)
    school_name = answer_school.get('name') or '校园'
    school_en_name = answer_school.get('enName') or ''
    school_label = f'{school_name}（{school_en_name}）' if school_en_name else school_name
    system_content = f"""你是{school_label}的校园植物与导览助手，像一个熟悉校园的老朋友，回答时自然亲切、有温度，适当使用 emoji 增加趣味感。

【回答规则】
1. 【禁止编造位置】只能使用下方数据中明确出现的位置，绝对不能补充、推测或编造任何数据中没有的位置信息
2. 【位置格式】具体植物位置查询只概括校区和地图点位数量，配合植物简介即可；不要逐条罗列多个地址、不要输出距离，也不要只挑一个点位冒充全部结果。末尾可加一句"左侧地图已标注相关位置，点击标记可导航 🗺️"
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
    if (not unsupported_data_query and preferred_campus
            and not _campus_filter_from_query(user_query)
            and not (direct_plant_location_requested and direct_plant_campus is None)):
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

    # ---- 图片链路：先把"图中可读文字"抽成校园地点，再让主回答直接使用这些地点 ----
    # 抽取单独一次视觉调用（失败返回 []，不影响主回答）；命中的 POI 同时写进上下文与地图打点，
    # 避免出现"模型说这是 XX 楼、地图上却没有点"的自相矛盾。
    photo_pois = []
    vision = facade.resolve_vision() if attachments else None
    if vision:
        photo_pois = match_pois_by_names(extract_places_from_photo(attachments, vision))
        photo_context = build_photo_poi_context(photo_pois)
        if photo_context:
            system_content += '\n\n' + photo_context
    system_content += '\n\n' + _image_capability_clause()

    llm = facade.resolve_llm()
    if not llm:
        return jsonify({'error': 'AI 服务尚未配置'}), 503

    try:
        resp = requests.post(
            llm['api_url'],
            headers={'Authorization': f"Bearer {llm['api_key']}", 'Content-Type': 'application/json'},
            json={
                'model': llm['model'],
                # 附件只并入最后一条用户消息；历史轮次保持字符串，避免每轮重发 base64。
                # 纯文本部署（未配置视觉模型）**不转发图片**：那只会让上游直接报错。
                'messages': [{'role': 'system', 'content': system_content}]
                            + build_llm_messages(messages, attachments if vision else []),
                'temperature': 0.3,
                'max_tokens': 1024,
            },
            timeout=30,
            proxies=_LLM_PROXIES,
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
            if direct_plant_location_requested and direct_plant_campus:
                coords = [
                    coord for coord in coords
                    if _campus(coord.get('lng'), coord.get('lat')) == direct_plant_campus
                ]
            elif not direct_plant_location_requested:
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

    # 图片链路：把"图中可读文字命中的校园地点"也标到地图上。放在最后追加，
    # 不改动既有链路的打点结果。
    for poi in photo_pois:
        add_location(
            poi.get('name', ''),
            poi.get('lng'),
            poi.get('lat'),
            _format_poi_number(poi),
            poi.get('campus', ''),
            'photo_poi',
            category=poi.get('category', ''),
            subCategory=poi.get('subCategory', '')
        )

    

    # 采集点②：用户在问一个**具体地名**（带方位意图），而这个名字不在已收录数据里。
    # 闸门刻意选在"规则抽到一个干净地名"上，而不是"本轮没有点位"——后者在项目里几乎总
    # 不成立（场景/植物兜底几乎总会给出一堆点位，例如任何问句都可能拿到 93 个打点）。
    # 本轮已经标注过的点位名排除在外，避免"答了还记一笔"。
    if _CAPTURE_LOCATION_INTENT.search(user_query or ''):
        _capture_unknown_place(
            user_query, _capture_campus(user_query, preferred_campus), 'chat',
            record_unresolved=False,
            extra_known=[loc.get('name') for loc in locations if loc.get('name')],
        )

    result['locations'] = locations
    result['ranked_places'] = ranked_places
    plant_location_response = direct_plant_location_requested or bool(
        locations and all(location.get('kind') == 'plant' for location in locations)
        and re.search(r'在哪里|在哪|位置|地点|地图|显示|分布', user_query or '')
    )
    if plant_location_response:
        choices = result.setdefault('choices', [])
        if not choices:
            choices.append({})
        choices[0]['message'] = {
            'role': 'assistant',
            'content': build_direct_plant_location_reply(plants, direct_plant_campus),
        }
    if data.get('recommendationMode') == 'sakde':
        result['recommendation_engine'] = 'rule'
        if heatmap_fallback:
            result['heatmap_status'] = heatmap_fallback
        if itinerary_status:
            result['itinerary_status'] = itinerary_status
    return jsonify(result)
