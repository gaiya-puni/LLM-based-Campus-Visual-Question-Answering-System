"""用户提供数据的落盘层：append-only 事件流 + 派生的待确认清单。

设计要点
--------
1. **纯标准库、零 web 依赖**：本模块不 import `server` / `campus_config`，既避免循环依赖，
   也让单测完全离线运行（本机语义检索不可用时的既有约束）。
2. **事件流 + 派生视图**：用户反馈、上报、未收录问句抽取结果都按行追加到 `*.jsonl`
   （O(1)，不重写历史）；`pending_places.json` 是由事件聚合出的**待确认清单**，可随时重放
   重建；人工的通过/驳回决定单独存 `review_state.json`，因此重建不会丢掉审核结论。
3. **只进待确认，不进正式 POI**：清单并入 `webapp/backend/*_pois.json` 仍走既有离线生成
   流程人工确认（见 `POI_CATEGORY_GUIDE.md` 与 `tools/campus_generator/normalize.py`）。
4. **失败静默**：所有 IO 都用 try/except 包住，异常只写一行日志且**不含用户原文**，
   绝不把异常抛回问答主链路。

文件布局（默认目录 `webapp/backend/userdata/`，可用环境变量 `USERDATA_DIR` 覆盖）
--------------------------------------------------------------------------
- `ratings.jsonl`           消息级评价事件
- `place_reports.jsonl`     用户主动上报的地点
- `unresolved_queries.jsonl` 未收录且规则判不出来的问句（供批量大模型研判）
- `extracted_places.jsonl`  规则/大模型抽取出的地点候选事件
- `review_state.json`       人工审核结论（status / note）
- `pending_places.json`     派生：待确认清单（对前端与生成流水线的契约）
- `pending_places.md`       派生：人类可读汇总
- `approved_places.json`    派生：已通过条目（字段对齐 POI 契约，交离线流程并入）
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_DIR_NAME = 'userdata'

#: 事件流文件名（按 kind 索引）
EVENT_FILES = {
    'rating': 'ratings.jsonl',
    'report': 'place_reports.jsonl',
    'unresolved': 'unresolved_queries.jsonl',
    'extracted': 'extracted_places.jsonl',
    # 大模型批量研判的"已处理"台账（含零命中的问句），避免同一句反复付费
    'llm_scan': 'llm_scan.jsonl',
}

#: 派生文件
PENDING_FILE = 'pending_places.json'
PENDING_MARKDOWN = 'pending_places.md'
REVIEW_STATE_FILE = 'review_state.json'
APPROVED_FILE = 'approved_places.json'

STATUSES = ('pending', 'approved', 'rejected')
#: 来源优先级：用户上报 > 大模型识别 > 规则识别
SOURCE_PRIORITY = {'user': 3, 'llm': 2, 'rule': 1}
SOURCE_LABELS = {'user': '用户上报', 'llm': '大模型识别', 'rule': '规则识别'}

#: 上报时的类型选择（值 → 中文标签），与 POI 契约的 category/subCategory 对齐
CATEGORY_CHOICES = (
    ('building', '建筑'),
    ('canteen', '餐饮'),
    ('parking', '停车'),
    ('plant', '植物'),
    ('water', '水景'),
    ('scene', '其他'),
)
CATEGORY_LABELS = dict(CATEGORY_CHOICES)

#: 同一个名称+校区若坐标相距超过该值，视为两个不同地点，不合并
SAME_PLACE_MAX_METERS = 200.0

MAX_QUERY_CHARS = 200
MAX_REASON_CHARS = 300
MAX_NOTE_CHARS = 300
MAX_SAMPLES = 3

_LOCK = threading.RLock()

# 脱敏用正则
_RE_PHONE = re.compile(r'(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)')
_RE_EMAIL = re.compile(r'([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})')
_RE_LONG_DIGITS = re.compile(r'(?<!\d)(\d{9,})(?!\d)')
_RE_SELF_INTRO = re.compile(r'(我叫|我名字叫|姓名[:：]?\s*|叫)([\u4e00-\u9fa5]{2,4})(?=[，。,.!?！？\s]|$)')
_PAREN_TAIL = re.compile(r'[（(][^）)]{0,12}[）)]\s*$')
_SPACE = re.compile(r'\s+')


# ---------------------------------------------------------------------------
# 目录与路径
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """数据目录；每次调用都读环境变量，便于测试指向临时目录。"""
    override = (os.getenv('USERDATA_DIR') or '').strip()
    return Path(override) if override else (_BASE_DIR / _DEFAULT_DIR_NAME)


def event_path(kind: str) -> Path:
    filename = EVENT_FILES.get(kind)
    if not filename:
        raise ValueError(f'unknown event kind: {kind}')
    return data_dir() / filename


def ensure_dir() -> Path:
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 脱敏与归一
# ---------------------------------------------------------------------------


def mask_sensitive(text, limit: int | None = None) -> str:
    """脱敏：手机号、邮箱、9 位以上连续数字（学号/身份证）与"我叫X"式自我介绍。

    只做保守替换，其余文本原样保留——过度脱敏会毁掉"用户在问哪个地点"这个核心价值。
    """
    value = '' if text is None else str(text)
    value = _RE_PHONE.sub(lambda m: f'{m.group(1)}****{m.group(3)}', value)
    value = _RE_EMAIL.sub(lambda m: f'{m.group(1)}***{m.group(2)}', value)
    value = _RE_LONG_DIGITS.sub(lambda m: _mask_digits(m.group(1)), value)
    value = _RE_SELF_INTRO.sub(lambda m: f'{m.group(1)}***', value)
    value = _SPACE.sub(' ', value).strip()
    if limit and len(value) > limit:
        value = value[:limit].rstrip() + '…'
    return value


def _mask_digits(digits: str) -> str:
    if len(digits) <= 5:
        return '*' * len(digits)
    return f'{digits[:3]}{"*" * (len(digits) - 5)}{digits[-2:]}'


def normalize_name(name) -> str:
    """名称归一：全角转半角、去空白、去尾部括号限定（"图书馆（普陀）"→"图书馆"）。"""
    value = unicodedata.normalize('NFKC', '' if name is None else str(name))
    value = _SPACE.sub('', value)
    value = _PAREN_TAIL.sub('', value)
    return value.strip().lower()


def make_candidate_id(name, campus) -> str:
    """稳定可复现的候选 id：名称 + 校区哈希（同一地点跨次重建得到同一个 id）。"""
    raw = f'{normalize_name(name)}|{campus or ""}'.encode('utf-8')
    return f'p_{hashlib.sha1(raw).hexdigest()[:10]}'


def _now_iso() -> str:
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _distance_meters(lng1, lat1, lng2, lat2) -> float:
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111320 * math.cos(mean_lat)
    dy = (lat1 - lat2) * 110540
    return (dx * dx + dy * dy) ** 0.5


def _as_float(value):
    """转 float；**非有限值（NaN/±Inf）一律视为无效**。

    `float('nan')` 能通过几乎所有数值校验，但写进 JSON 会变成字面量 `NaN`——那是**非法 JSON**，
    浏览器 `JSON.parse` 直接抛错，审核页会整页打不开。因此这里就把它挡掉。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ---------------------------------------------------------------------------
# 事件读写
# ---------------------------------------------------------------------------


def append_event(kind: str, record: dict) -> bool:
    """追加一条事件（线程安全、O(1)）；失败静默返回 False。"""
    try:
        ensure_dir()
        # allow_nan=False：任何非有限数值都写不进去（宁可丢这一条事件，也不写出非法 JSON）
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        with _LOCK:
            with open(event_path(kind), 'a', encoding='utf-8', newline='\n') as handle:
                handle.write(line + '\n')
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning('userdata append failed kind=%s error=%s', kind, type(exc).__name__)
        return False


def read_events(kind: str, limit: int | None = None) -> list:
    """读事件流；坏行（半截写入/手工编辑）直接跳过，不让一条脏数据毁掉整份清单。"""
    path = event_path(kind)
    if not path.exists():
        return []
    records = []
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        logger.warning('userdata read failed kind=%s error=%s', kind, type(exc).__name__)
        return []
    return records[-limit:] if limit else records


def record_rating(message_id, rating, reason='', query='', engine='', campus='') -> dict | None:
    """记录一条消息级评价；成功返回事件，写入失败返回 None。

    `rating` 取值 `up` / `down` / `none`：同一 `messageId` 可能有多条事件，**取最新一条**
    为当前评价（`none` 即撤回），历史保留在事件流里便于追溯。
    """
    normalized = str(rating).lower()
    event = {
        'at': _now_iso(),
        'messageId': mask_sensitive(message_id, 64),
        'rating': normalized if normalized in ('up', 'down', 'none') else 'down',
        'reason': mask_sensitive(reason, MAX_REASON_CHARS),
        'query': mask_sensitive(query, MAX_QUERY_CHARS),
        'engine': mask_sensitive(engine, 32),
        'campus': mask_sensitive(campus, 16),
    }
    return event if append_event('rating', event) else None


def record_place_report(name, campus, lng, lat, category='', note='', query_snippet='') -> dict | None:
    """记录用户主动上报的地点，并立即重聚合待确认清单；返回事件或 None。"""
    clean_name = mask_sensitive(name, 40)
    if not normalize_name(clean_name):
        return None
    event = {
        'at': _now_iso(),
        'name': clean_name,
        'campus': mask_sensitive(campus, 16),
        'lng': _as_float(lng),
        'lat': _as_float(lat),
        'category': category if category in CATEGORY_LABELS else '',
        'note': mask_sensitive(note, MAX_NOTE_CHARS),
        'query': mask_sensitive(query_snippet, MAX_QUERY_CHARS),
    }
    if not append_event('report', event):
        return None
    rebuild_pending()
    return event


def record_unresolved_query(query, campus='', engine='') -> bool:
    """记录"未收录且规则判不出来"的问句，供审核页批量大模型研判。"""
    clean = mask_sensitive(query, MAX_QUERY_CHARS)
    if not clean:
        return False
    return append_event('unresolved', {
        'at': _now_iso(),
        'query': clean,
        'campus': mask_sensitive(campus, 16),
        'engine': mask_sensitive(engine, 32),
    })


def record_llm_scan(queries, added=0) -> int:
    """登记"已交给大模型研判过"的问句（含零命中的），避免重复付费。"""
    written = 0
    for query in queries or []:
        clean = mask_sensitive(query, MAX_QUERY_CHARS)
        if not clean:
            continue
        if append_event('llm_scan', {'at': _now_iso(), 'query': clean, 'added': int(added)}):
            written += 1
    return written


def scanned_queries() -> set:
    return {event.get('query') for event in read_events('llm_scan') if event.get('query')}


def record_extracted_places(items, query='', campus='', source='rule', engine='') -> int:
    """记录抽取层判出的地点候选（规则或大模型），返回成功写入条数。"""
    written = 0
    for item in items or []:
        name = mask_sensitive(item.get('name'), 40)
        if not normalize_name(name):
            continue
        event = {
            'at': _now_iso(),
            'name': name,
            'campus': mask_sensitive(item.get('campus') or campus, 16),
            'lng': _as_float(item.get('lng')),
            'lat': _as_float(item.get('lat')),
            'category': item.get('category') if item.get('category') in CATEGORY_LABELS else '',
            'confidence': _as_float(item.get('confidence')) or 0.0,
            'source': source if source in SOURCE_PRIORITY else 'rule',
            'query': mask_sensitive(item.get('query') or query, MAX_QUERY_CHARS),
            'engine': mask_sensitive(engine, 32),
        }
        if append_event('extracted', event):
            written += 1
    return written


# ---------------------------------------------------------------------------
# 审核状态
# ---------------------------------------------------------------------------


def load_review_state() -> dict:
    payload = _read_json(REVIEW_STATE_FILE)
    state = payload.get('items') if isinstance(payload, dict) else None
    return state if isinstance(state, dict) else {}


def save_review_state(state: dict) -> bool:
    return _atomic_write_json(REVIEW_STATE_FILE, {'updatedAt': _now_iso(), 'items': state or {}})


def update_status(item_id: str, status: str, note: str = '') -> bool:
    """写入人工审核结论（通过/驳回/退回待审），并立即把清单投影刷新。

    结论存在 `review_state.json`，清单里的 status 只是它的投影：两处都更新，
    审核页才能"原地变色"，而重放事件时结论不会丢。
    """
    if status not in STATUSES or not item_id:
        return False
    state = load_review_state()
    entry = dict(state.get(item_id) or {})
    entry['status'] = status
    entry['note'] = mask_sensitive(note, MAX_NOTE_CHARS)
    entry['updatedAt'] = _now_iso()
    state[item_id] = entry
    if not save_review_state(state):
        return False
    rebuild_pending()
    return True


# ---------------------------------------------------------------------------
# 聚合：事件流 → 待确认清单
# ---------------------------------------------------------------------------


def aggregate_items() -> list:
    """把全部事件聚合成候选条目（纯函数：同样的事件流永远得到同样的结果）。"""
    buckets: dict = {}
    for event in read_events('report'):
        _merge_event(buckets, event, forced_source='user', forced_confidence=1.0)
    for event in read_events('extracted'):
        _merge_event(buckets, event, forced_source=event.get('source'), forced_confidence=None)
    state = load_review_state()
    items = []
    for bucket in buckets.values():
        bucket['status'] = 'pending'
        bucket['note'] = ''
        override = state.get(bucket['id']) or {}
        if override.get('status') in STATUSES:
            bucket['status'] = override['status']
        bucket['note'] = override.get('note') or ''
        items.append(bucket)
    items.sort(key=lambda item: (-item['mentions'], item['firstSeenAt']))
    return items


def _merge_event(buckets: dict, event: dict, forced_source=None, forced_confidence=None) -> None:
    name = (event.get('name') or '').strip()
    if not normalize_name(name):
        return
    campus = (event.get('campus') or '').strip()
    lng, lat = _as_float(event.get('lng')), _as_float(event.get('lat'))
    bucket = _match_bucket(buckets, name, campus, lng, lat)
    if bucket is None:
        bucket = {
            # 同名同校区但相隔很远（校园里真的有两个"学生宿舍"）时 id 会撞车：
            # 撞了就加序号，否则后一个条目会把前一个覆盖掉。
            'id': _unique_id(buckets, name, campus),
            'name': name,
            'campus': campus,
            'lng': lng,
            'lat': lat,
            'category': event.get('category') or None,
            'source': 'rule',
            'confidence': 0.0,
            'mentions': 0,
            'firstSeenAt': event.get('at') or _now_iso(),
            'lastSeenAt': event.get('at') or _now_iso(),
            'samples': [],
        }
        buckets[bucket['id']] = bucket
    at = event.get('at') or _now_iso()
    bucket['mentions'] = int(bucket.get('mentions', 0)) + 1
    bucket['firstSeenAt'] = min(bucket.get('firstSeenAt') or at, at)
    bucket['lastSeenAt'] = max(bucket.get('lastSeenAt') or at, at)
    if lng is not None and lat is not None and (bucket.get('lng') is None or forced_source == 'user'):
        bucket['lng'], bucket['lat'] = lng, lat
    if not bucket.get('category') and event.get('category'):
        bucket['category'] = event['category']
    source = forced_source or event.get('source') or 'rule'
    if SOURCE_PRIORITY.get(source, 0) > SOURCE_PRIORITY.get(bucket.get('source'), 0):
        bucket['source'] = source
    confidence = forced_confidence if forced_confidence is not None else _as_float(event.get('confidence'))
    if confidence is not None:
        bucket['confidence'] = max(float(bucket.get('confidence') or 0.0), float(confidence))
    sample = mask_sensitive(event.get('query'), MAX_QUERY_CHARS)
    if sample and sample not in bucket['samples'] and len(bucket['samples']) < MAX_SAMPLES:
        bucket['samples'].append(sample)


def _unique_id(buckets: dict, name: str, campus: str) -> str:
    """稳定 id；与既有条目撞车时追加序号（同样的事件流得到同样的编号）。"""
    base = make_candidate_id(name, campus)
    if base not in buckets:
        return base
    index = 2
    while f'{base}_{index}' in buckets:
        index += 1
    return f'{base}_{index}'


def _match_bucket(buckets: dict, name: str, campus: str, lng, lat):
    """按"归一化名称 + 校区"合并；同名但坐标相距过远（>200m）视作两个地点。"""
    name_key = normalize_name(name)
    for bucket in buckets.values():
        if normalize_name(bucket['name']) != name_key or bucket['campus'] != campus:
            continue
        b_lng, b_lat = bucket.get('lng'), bucket.get('lat')
        if None in (lng, lat, b_lng, b_lat):
            return bucket
        if _distance_meters(lng, lat, b_lng, b_lat) <= SAME_PLACE_MAX_METERS:
            return bucket
    return None


def load_pending() -> dict:
    """读派生清单；不存在时返回空结构（不触发重建）。"""
    payload = _read_json(PENDING_FILE)
    if isinstance(payload, dict) and isinstance(payload.get('items'), list):
        return payload
    return {'generatedAt': None, 'counts': _count_by_status([]), 'items': []}


def rebuild_pending() -> dict:
    """按事件流重建清单并落盘（含 Markdown 汇总）；失败时返回内存结果。"""
    items = aggregate_items()
    payload = {'generatedAt': _now_iso(), 'counts': _count_by_status(items), 'items': items}
    if _atomic_write_json(PENDING_FILE, payload):
        _atomic_write_text(PENDING_MARKDOWN, render_markdown(items))
    return payload


def list_pending(campus: str | None = None, status: str | None = None, source: str | None = None) -> dict:
    payload = load_pending()
    items = payload.get('items') or []
    if campus:
        items = [item for item in items if item.get('campus') == campus]
    if status:
        items = [item for item in items if item.get('status') == status]
    if source:
        items = [item for item in items if item.get('source') == source]
    counts = _count_by_status(payload.get('items') or [])
    return {'generatedAt': payload.get('generatedAt'), 'counts': counts, 'total': len(items), 'items': items}


def _count_by_status(items) -> dict:
    counts = {status: 0 for status in STATUSES}
    for item in items or []:
        status = item.get('status') or 'pending'
        counts[status] = counts.get(status, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def render_markdown(items) -> str:
    lines = [
        '# 待确认地点清单',
        '',
        f'生成时间：{_now_iso()}',
        '',
        '> 本文件由 `userdata_store.py` 从用户反馈事件自动汇总，**不可直接并入正式 POI**；',
        '> 通过审核后请交给 `tools/campus_generator/normalize.py` 人工确认流程处理。',
        '',
        '| 名称 | 校区 | 来源 | 置信度 | 提及次数 | 状态 | 坐标 | 最近出现 |',
        '|---|---|---|---|---|---|---|---|',
    ]
    if not items:
        lines.append('| （暂无） | - | - | - | - | - | - | - |')
    for item in items or []:
        coord = '-' if item.get('lng') is None else f"{item['lng']:.5f},{item['lat']:.5f}"
        lines.append(
            f"| {item.get('name', '')} | {item.get('campus', '') or '-'} | "
            f"{SOURCE_LABELS.get(item.get('source'), item.get('source', ''))} | "
            f"{round(float(item.get('confidence') or 0.0), 2)} | {item.get('mentions', 0)} | "
            f"{item.get('status', '')} | {coord} | {item.get('lastSeenAt', '')} |"
        )
    lines.append('')
    return '\n'.join(lines)


def to_poi_record(item: dict) -> dict | None:
    """把已通过条目转成 POI 契约记录；缺坐标的条目无法上图，返回 None。"""
    lng, lat = _as_float(item.get('lng')), _as_float(item.get('lat'))
    if lng is None or lat is None:
        return None
    campus = item.get('campus') or ''
    name = item.get('name') or ''
    category = item.get('category') or 'scene'
    sub_category = CATEGORY_LABELS.get(category, '其他')
    notes = item.get('note') or ''
    return {
        'id': f"user_{item.get('id', '')}",
        'category': category,
        'subCategory': sub_category,
        'name': name,
        'locationName': name,
        'lng': lng,
        'lat': lat,
        'campus': campus,
        'source': 'user_contributed',
        'text': ' '.join(part for part in (name, campus, sub_category, notes) if part),
        'tags': [tag for tag in (sub_category, campus, '用户补充') if tag],
        'meta': {
            'aliases': [],
            'notes': notes,
            'reportedBy': SOURCE_LABELS.get(item.get('source'), ''),
            'mentions': item.get('mentions', 0),
        },
    }


def export_approved() -> dict:
    """导出已通过条目（字段对齐 POI 契约）与 Markdown 汇总，返回导出摘要。"""
    items = [item for item in (load_pending().get('items') or []) if item.get('status') == 'approved']
    records, skipped = [], []
    for item in items:
        record = to_poi_record(item)
        (records if record else skipped).append(record or item.get('name'))
    _atomic_write_json(APPROVED_FILE, {
        'generatedAt': _now_iso(),
        'total': len(records),
        'skippedWithoutCoordinates': skipped,
        'records': records,
    })
    _atomic_write_text(PENDING_MARKDOWN, render_markdown(load_pending().get('items') or []))
    return {
        'approved': len(records),
        'skipped': len(skipped),
        'file': str(data_dir() / APPROVED_FILE),
        'markdown': str(data_dir() / PENDING_MARKDOWN),
    }


def stats() -> dict:
    payload = load_pending()
    return {
        'counts': _count_by_status(payload.get('items') or []),
        'unresolvedPending': len(read_events('unresolved')),
        'ratingsTotal': len(read_events('rating')),
        'reportsTotal': len(read_events('report')),
        'generatedAt': payload.get('generatedAt'),
    }


# ---------------------------------------------------------------------------
# 低层 IO
# ---------------------------------------------------------------------------


def _read_json(filename: str) -> dict:
    path = data_dir() / filename
    if not path.exists():
        return {}
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning('userdata read failed file=%s error=%s', filename, type(exc).__name__)
        return {}


def _atomic_write_json(filename: str, payload) -> bool:
    # allow_nan=False 同上：清单是给浏览器读的，绝不允许出现 NaN/Infinity 字面量
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError as exc:
        logger.warning('userdata write rejected file=%s error=%s', filename, type(exc).__name__)
        return False
    return _atomic_write_text(filename, text)


def _atomic_write_text(filename: str, text: str) -> bool:
    """先写临时文件再 os.replace：审核中途崩溃不会留下半截 JSON。"""
    try:
        ensure_dir()
        path = data_dir() / filename
        tmp = path.with_name(path.name + '.tmp')
        with _LOCK:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning('userdata write failed file=%s error=%s', filename, type(exc).__name__)
        return False
