"""用户共建数据：消息反馈、地点上报、待审清单、审核与导出。

只做"收集 + 审核 + 导出"；并入正式 POI 仍走 tools/campus_generator 的离线确认流程。
"""

import math

import campus_config
import paths  # noqa: F401
import place_extraction
import userdata_store
from flask import Blueprint, jsonify, request
from flask_app import app, rate_limit
from runtime import facade
from api.chat_api import _extract_llm_call
from api.review_guard import REVIEW_TOKEN_MIN_LENGTH  # noqa: F401
from api.review_guard import review_denied, review_token_ok

bp = Blueprint('userdata_api', __name__)

# 只做"收集 + 审核 + 导出"；并入正式 POI 仍走 tools/campus_generator 的离线确认流程。


@bp.route('/api/feedback', methods=['POST'])
@rate_limit(20)
def feedback():
    """消息级评价：有用/没用 + 可选文字理由，`none` 表示撤回先前的评价。失败只回软提示。"""
    data = request.get_json(silent=True) or {}
    rating = str(data.get('rating') or '').strip().lower()
    if rating not in ('up', 'down', 'none'):
        return jsonify({'success': False, 'message': '评价取值无效'}), 400
    # messageId 是"评价挂在哪条回答上"的唯一依据，缺了就成了一条无法追溯的脏数据
    message_id = str(data.get('messageId') or '').strip()
    if not message_id:
        return jsonify({'success': False, 'message': '缺少消息标识'}), 400
    event = userdata_store.record_rating(
        message_id, rating, data.get('reason') or '',
        data.get('query') or '', data.get('messageEngine') or '', data.get('campus') or '')
    if not event:
        return jsonify({'success': False, 'message': '记录失败，稍后可再试'})
    app.logger.info('userdata rating recorded rating=%s', rating)
    return jsonify({'success': True, 'message': '已收到，谢谢反馈'})


@bp.route('/api/place_report', methods=['POST'])
@rate_limit(10)
def place_report():
    """用户主动上报地点：校验名称/坐标 → 脱敏落盘 → 立即进入待确认清单。"""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    if not 2 <= len(name) <= 20:
        return jsonify({'success': False, 'message': '地点名称需为 2-20 个字符'}), 400
    try:
        lng, lat = float(data.get('lng')), float(data.get('lat'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '请在地图上点选位置'}), 400
    # 非有限值必须在这里挡掉：`float('nan')` 能让后面的距离比较全部为假、骗过校区判定，
    # 而 NaN 一旦写进清单就变成非法 JSON，浏览器 JSON.parse 抛错、审核页整页打不开。
    if not (math.isfinite(lng) and math.isfinite(lat)):
        return jsonify({'success': False, 'message': '位置坐标无效，请重新在地图上点选'}), 400
    # 校区**由坐标判定**，不采信前端传来的名字：否则界面上选着普陀、点在闵行，也会被记成普陀。
    # 只接受落在已登记校区信任半径内的点，校外坐标一律拒绝——否则地图上会出现"没人能核实的点"。
    campus = campus_config.campus_at(lng, lat)
    if not campus:
        return jsonify({'success': False, 'message': '请把位置点选在已登记的校区范围内'}), 400
    event = userdata_store.record_place_report(
        name, campus, lng, lat, str(data.get('category') or ''),
        str(data.get('note') or ''), str(data.get('querySnippet') or ''))
    if not event:
        return jsonify({'success': False, 'message': '提交失败，请稍后再试'})
    app.logger.info('userdata place report recorded campus=%s', campus)
    return jsonify({
        'success': True,
        'message': f'已提交，感谢补充（已记到{campus}校区）',
        'campus': campus,
        'itemId': userdata_store.make_candidate_id(name, campus),
        'counts': userdata_store.load_pending()['counts'],
    })


@bp.route('/api/userdata/pending', methods=['GET'])
@rate_limit(60)
def userdata_pending():
    """待审清单（审核态）：支持校区/状态/来源过滤、名称搜索与分页。"""
    if not review_token_ok():
        return review_denied()
    payload = userdata_store.list_pending(
        campus=request.args.get('campus') or None,
        status=request.args.get('status') or None,
        source=request.args.get('source') or None,
    )
    items = payload['items']
    keyword = (request.args.get('q') or '').strip()
    if keyword:
        items = [item for item in items if keyword in (item.get('name') or '')]
    try:
        page = max(1, int(request.args.get('page') or 1))
        page_size = min(max(int(request.args.get('pageSize') or 50), 1), 200)
    except (TypeError, ValueError):
        page, page_size = 1, 50
    start = (page - 1) * page_size
    return jsonify({
        'success': True,
        'counts': payload['counts'],
        'stats': userdata_store.stats(),
        'extractionMode': place_extraction.extraction_mode(),
        'total': len(items),
        'page': page,
        'pageSize': page_size,
        'items': items[start:start + page_size],
    })


@bp.route('/api/userdata/review', methods=['POST'])
@rate_limit(30)
def userdata_review():
    """审核单条候选：通过 / 驳回 / 退回待审，可带备注。"""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    item_id = str(data.get('id') or '').strip()
    status = str(data.get('status') or '').strip().lower()
    if status not in userdata_store.STATUSES:
        return jsonify({'success': False, 'message': '审核状态无效'}), 400
    if not userdata_store.update_status(item_id, status, str(data.get('note') or '')):
        return jsonify({'success': False, 'message': '清单写入失败'}), 500
    app.logger.info('userdata review status=%s', status)
    return jsonify({'success': True, 'counts': userdata_store.load_pending()['counts']})


@bp.route('/api/userdata/extract', methods=['POST'])
@rate_limit(10)
def userdata_extract():
    """用大模型批量研判"规则判不出来"的问句（batch 模式的补齐入口，带已处理台账）。"""
    if not review_token_ok():
        return review_denied()
    data = request.get_json(silent=True) or {}
    try:
        limit = min(max(int(data.get('limit') or 20), 1), 100)
    except (TypeError, ValueError):
        limit = 20
    campus = str(data.get('campus') or '').strip() or None
    scanned = userdata_store.scanned_queries()
    queries, campus_by_query = [], {}
    for event in userdata_store.read_events('unresolved'):
        query = event.get('query')
        if not query or query in scanned or query in queries:
            continue
        queries.append(query)
        campus_by_query.setdefault(query, event.get('campus') or '')
        if len(queries) >= limit:
            break
    if not queries:
        return jsonify({'success': True, 'processed': 0, 'added': 0,
                        'message': '没有待研判的问句', 'counts': userdata_store.load_pending()['counts']})
    if not facade.resolve_llm():
        return jsonify({'success': False, 'message': '大模型未配置，无法研判'})
    found = place_extraction.llm_extract(queries, campus=campus, call=_extract_llm_call)
    # 问句落盘时已带校区：抽取结果没带校区就回填，否则候选会变成"未标校区"而难以核实
    for item in found:
        if not item.get('campus'):
            item['campus'] = campus_by_query.get(item.get('query') or '') or campus or ''
    added = userdata_store.record_extracted_places(
        found, campus=campus, source='llm', engine='review')
    userdata_store.record_llm_scan(queries, added=added)
    if added:
        userdata_store.rebuild_pending()
    app.logger.info('userdata llm extract processed=%s added=%s', len(queries), added)
    return jsonify({'success': True, 'processed': len(queries), 'added': added,
                    'counts': userdata_store.load_pending()['counts']})


@bp.route('/api/userdata/export', methods=['GET'])
@rate_limit(10)
def userdata_export():
    """导出已通过条目（字段对齐 POI 契约）与 Markdown 汇总。"""
    if not review_token_ok():
        return review_denied()
    summary = userdata_store.export_approved()
    app.logger.info('userdata export approved=%s', summary.get('approved'))
    return jsonify({'success': True, **summary})
