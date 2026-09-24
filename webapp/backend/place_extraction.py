"""未收录地点的抽取：地理通名规则优先，大模型兜底。

设计要点
--------
1. **零 web 依赖**：本模块不 import `server`，只有 `campus_config` 这一个本地依赖（它同样
   不反向依赖 server），因此可在离线单测里直接调用。大模型调用以**回调注入**的方式传入，
   真实实现留在 `server.py`（复用既有的 `resolve_llm()` + 直连代理口径）。
2. **规则先行**：中文地名几乎都带地理通名（楼 / 馆 / 门 / 苑 / 食堂 / 湖…），用"通名 +
   左侧最多 6 字"的窗口取证即可拿到高质量候选，复杂度 O(len(query))，微秒级。
3. **大模型只做兜底**，且默认 `batch`：在线请求只把问句落进 `unresolved_queries.jsonl`
   （零额外网络调用、零延迟增加），由审核页按钮或离线命令批量研判并缓存结果；
   需要"当场判一次"时把 `USERDATA_LLM_EXTRACT` 设为 `inline`。
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata

import campus_config

logger = logging.getLogger(__name__)

#: 地理通名（按长度倒序匹配，保证"停车场"优先于"场"）
GEO_SUFFIXES = (
    '停车场', '实验室', '图书馆', '博物馆', '体育馆', '咖啡厅', '咖啡店',
    '书院', '学院', '学部', '食堂', '餐厅', '公寓', '宿舍', '球场', '操场',
    '广场', '中心', '大楼', '教学楼', '实验楼', '美术馆', '音乐厅', '报告厅',
    '超市', '快递', '咖啡', '水吧', '门', '楼', '馆', '苑', '园', '桥', '湖',
    '河', '塘', '池', '岛', '亭', '塔', '像', '雕像', '草坪', '花园', '街道',
    '路', '门岗', '校门', '基地', '所', '站', '场',
)

#: 通名 → POI 类别（值取自 userdata_store.CATEGORY_CHOICES）
SUFFIX_CATEGORY = {
    '食堂': 'canteen', '餐厅': 'canteen', '咖啡': 'canteen', '咖啡厅': 'canteen',
    '咖啡店': 'canteen', '水吧': 'canteen', '超市': 'canteen',
    '停车场': 'parking', '停车': 'parking',
    '湖': 'water', '河': 'water', '塘': 'water', '池': 'water', '岛': 'water',
    '楼': 'building', '大楼': 'building', '教学楼': 'building', '实验楼': 'building',
    '馆': 'building', '图书馆': 'building', '博物馆': 'building', '体育馆': 'building',
    '美术馆': 'building', '音乐厅': 'building', '报告厅': 'building', '门': 'building',
    '校门': 'building', '门岗': 'building', '桥': 'building', '中心': 'building',
    '学院': 'building', '书院': 'building', '学部': 'building', '实验室': 'building',
    '公寓': 'building', '宿舍': 'building', '亭': 'scene', '塔': 'scene',
    '像': 'scene', '雕像': 'scene', '广场': 'scene', '草坪': 'scene',
    '花园': 'scene', '苑': 'scene', '园': 'scene', '操场': 'scene', '球场': 'scene',
    '基地': 'scene', '所': 'scene', '站': 'scene', '场': 'scene', '快递': 'scene',
}

#: 地名里不可能出现的疑问/指示/泛化成分（命中即判为噪声）
_NOISE_PATTERN = re.compile(
    r'哪|什么|怎么|多少|为什么|是否|吗|呢|吧|我|你|他|她|这|那|此|该|'
    r'附近|周边|周围|校园|学校|校区|里面|外面|一下|一点|可以|能|有|是|的|了|和|与|及'
)

#: 窗口左侧的天然边界（标点/空格/连接词）
_WINDOW_BOUNDARY = re.compile(r'[，。！？；：、,.!?;:（）()「」“”"\'\s]|(?:在|去|到|从|往|离|与|和|及|的|了|有|找|问|是)')

#: 允许出现在地名里的字符
_NAME_CHARS = re.compile(r'^[\u3400-\u9fffA-Za-z0-9·\-]+$')

MAX_NAME_CHARS = 12
#: 规则侧刻意"宁缺勿滥"：至少 3 字且通名之前还有 2 个实词，避免把"食堂""大楼"这类
#: 泛指词当成地名；两三字的短名（"北门"）交给大模型兜底那一层。
MIN_NAME_CHARS = 3
WINDOW_LEFT_CHARS = 6
MAX_CANDIDATES_PER_QUERY = 3


def extraction_mode() -> str:
    """大模型兜底模式：batch（默认，在线零调用）或 inline（未收录时当场判一次）。"""
    mode = (os.getenv('USERDATA_LLM_EXTRACT') or 'batch').strip().lower()
    return mode if mode in ('batch', 'inline') else 'batch'


def normalize_text(text) -> str:
    return unicodedata.normalize('NFKC', '' if text is None else str(text)).strip()


def normalize_key(text) -> str:
    """候选比较用的归一化键：NFKC + 去空白 + 去尾部括号限定 + 小写。

    "图书馆（普陀）"与"图书馆"必须归到同一个键，否则同一个地点会被反复收进清单。
    调用方（`server._known_place_names`）构造"已在册名称"时也用这个口径，两边必须一致。
    """
    value = re.sub(r'\s+', '', normalize_text(text))
    value = re.sub(r'[（(][^）)]{0,12}[）)]$', '', value)
    return value.lower()


def rule_extract(query, campus=None, known_names=None) -> list:
    """规则抽取：返回 `[{name, campus, category, confidence, query}]`，可能为空。

    `known_names` 是"已在册的地点名/别名"归一化集合（由调用方传入，避免本模块依赖
    `server._CAMPUS_POIS` 造成循环 import）。
    """
    text = normalize_text(query)
    if not text or len(text) > 200:
        return []
    known = known_names or frozenset()
    stop_words = set(campus_config.stop_words())
    found, seen = [], set()
    for suffix in GEO_SUFFIXES:
        start = 0
        while True:
            index = text.find(suffix, start)
            if index < 0:
                break
            start = index + len(suffix)
            end = index + len(suffix)
            name = _cut_left_window(text, index) + suffix
            name = name.strip()
            key = normalize_key(name)
            if (key in seen or not _is_plausible(name, known, stop_words)
                    or any(key in other for other in seen)):
                continue
            seen.add(key)
            found.append({
                'name': name,
                'campus': campus,
                'category': _category_of(suffix),
                'confidence': _confidence_of(name, text),
                'query': text,
            })
            if len(found) >= MAX_CANDIDATES_PER_QUERY:
                return found
    # 长通名先命中时，短通名可能给出更完整的名字（"图书馆"vs"馆"），按长度择优去重
    return _prune_overlaps(found)


def _cut_left_window(text: str, suffix_index: int) -> str:
    """从通名左侧向左取最多 6 字，遇到标点/介词/助词即停。"""
    left = max(0, suffix_index - WINDOW_LEFT_CHARS)
    window = text[left:suffix_index]
    for match in _WINDOW_BOUNDARY.finditer(window):
        window = window[match.end():]
    return window


def _is_plausible(name: str, known: frozenset, stop_words: set) -> bool:
    if not (MIN_NAME_CHARS <= len(name) <= MAX_NAME_CHARS):
        return False
    if not _NAME_CHARS.match(name):
        return False
    if _NOISE_PATTERN.search(name):
        return False
    key = normalize_key(name)
    if key in known:
        return False
    # 校区名/校名片段（"普陀校区"、"华东师范"）不是待补充地点
    for word in stop_words:
        if word and len(word) >= 2 and word in name:
            return False
    # 通名之前必须还有实词（"楼"、"门"这类单字通名不能自成地名）
    return len(name) - len(_longest_suffix(name)) >= 2


def _longest_suffix(name: str) -> str:
    for suffix in GEO_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ''


def _category_of(suffix: str) -> str | None:
    return SUFFIX_CATEGORY.get(suffix)


def _confidence_of(name: str, query: str) -> float:
    """规则置信度：长度越长越可信；问句带方位意图再加一点。纯启发式，只用于排序。"""
    score = 0.55 + min(0.2, (len(name) - 2) * 0.05)
    if re.search(r'在哪|哪里|怎么走|怎么去|位置|地址|导航|哪个门', query):
        score += 0.15
    return round(min(score, 0.9), 2)


def _prune_overlaps(items: list) -> list:
    """同名或互相包含的候选只留信息量最大的那个（"图书馆"优于"馆"）。"""
    result = []
    for item in sorted(items, key=lambda entry: -len(entry['name'])):
        if any(item['name'] in kept['name'] or kept['name'] in item['name'] for kept in result):
            continue
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# 大模型兜底（回调注入，便于离线单测）
# ---------------------------------------------------------------------------


LLM_SYSTEM_PROMPT = (
    '你从校园问答系统的用户提问里抽取"具体地点名称"。'
    '只输出**校园内可指认的具体地点**（楼、馆、门、食堂、湖、广场、商店等），'
    '不要输出类别词（如"食堂"、"图书馆"这种泛指）、方位词、学院名、或校外地点。'
    '严格返回 JSON 数组，元素形如 {"name": "地点名", "category": "building|canteen|parking|plant|water|scene"}；'
    '没有任何具体地点时返回 []。不要输出解释文字。'
)


def build_llm_messages(query: str) -> list:
    return [
        {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
        {'role': 'user', 'content': f'用户提问：{query}'},
    ]


def parse_llm_places(content, campus=None, query='') -> list:
    """解析大模型返回的 JSON 数组；脏输出一律当作"没抽到"。"""
    if not content:
        return []
    text = str(content).strip()
    if text.startswith('```'):
        text = text.strip('`')
        text = text.split('\n', 1)[-1] if '\n' in text else text
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return []
    items, seen = [], set()
    for entry in payload if isinstance(payload, list) else []:
        if isinstance(entry, str):
            entry = {'name': entry}
        if not isinstance(entry, dict):
            continue
        name = normalize_text(entry.get('name'))[:MAX_NAME_CHARS]
        if not name or name in seen or _NOISE_PATTERN.search(name):
            continue
        seen.add(name)
        category = (entry.get('category') or '').strip().lower()
        items.append({
            'name': name,
            'campus': campus,
            'category': category if category in SUFFIX_CATEGORY.values() else None,
            'confidence': 0.75,
            'query': query,
        })
    return items[:MAX_CANDIDATES_PER_QUERY * 2]


def llm_extract(queries, campus=None, call=None) -> list:
    """批量大模型抽取；`call` 是 `(messages) -> str|None` 的回调，未提供则返回空。"""
    if call is None:
        return []
    results = []
    for query in queries or []:
        text = normalize_text(query)
        if not text:
            continue
        try:
            content = call(build_llm_messages(text))
        except Exception as exc:  # 兜底层绝不把异常抛给调用方
            logger.warning('place_extraction llm call failed: %s', type(exc).__name__)
            continue
        results.extend(parse_llm_places(content, campus=campus, query=text))
    return results


def extract(query, campus=None, known_names=None, llm_call=None, mode=None) -> dict:
    """统一入口：先规则、后大模型兜底。

    返回 `{'items': [...], 'via': 'rule'|'llm'|'', 'deferred': bool}`；
    `deferred=True` 表示 batch 模式下"规则没判出来、留给批量研判"。
    """
    items = rule_extract(query, campus=campus, known_names=known_names)
    if items:
        return {'items': items, 'via': 'rule', 'deferred': False}
    effective = mode or extraction_mode()
    if effective == 'inline' and llm_call is not None:
        llm_items = llm_extract([query], campus=campus, call=llm_call)
        if llm_items:
            return {'items': llm_items, 'via': 'llm', 'deferred': False}
        return {'items': [], 'via': 'llm', 'deferred': False}
    return {'items': [], 'via': '', 'deferred': True}
