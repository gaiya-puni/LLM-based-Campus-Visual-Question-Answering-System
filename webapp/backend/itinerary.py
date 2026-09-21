"""一日行程规划（itinerary）：确定性编排三段行程骨架，大模型只负责写文案。

设计要点
--------
- **纯函数模块**：不 import server。候选列表、起点与文案全部由调用方注入，
  因此单测可完全离线运行，也不需要句向量模型（规避本机既有的原生崩溃问题）。
- **只读消费方**：不读取、不修改 scene_heatmaps 的算法与任何热力图缓存，只对
  调用方给入的候选做筛选、去重与串联。
- **骨架与文案分离**：老师提的三条硬约束（三段都要有落点、中午必须是餐饮、
  点与点不折返）交给这里的确定性算法保证；自然语言描述交给大模型。
- 阈值与时长参数集中为模块级常量，便于调参与单测。
"""

from __future__ import annotations

import math
import re

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PERIODS = ("morning", "noon", "afternoon")

PERIOD_LABELS = {"morning": "上午", "noon": "中午", "afternoon": "下午"}

#: 各时段最多串联的站点数。中午固定 1 个（一顿饭），因此总站点为 3~5 个。
PERIOD_STOP_CAP = {"morning": 2, "noon": 1, "afternoon": 2}

#: 调用方可覆盖的"每段站点数"上限，最终取它与 PERIOD_STOP_CAP 的较小值。
DEFAULT_MAX_STOPS_PER_PERIOD = 2

#: 行程总站点上限；少于 MIN_TOTAL_STOPS 视为行程不成立，调用方应退回单场景推荐。
MAX_TOTAL_STOPS = 5
MIN_TOTAL_STOPS = 3

#: 中午时段必须落到餐饮类别。
NOON_CATEGORY = "canteen"

#: 相邻两点超过该距离即不建议步行，降级为骑行/校车提示。
DEFAULT_MAX_WALK_METERS = 1200.0

#: 建议停留时长（分钟）与通行速度（米/分钟），用于估算行程时长。
DEFAULT_DWELL_MINUTES = {"morning": 90, "noon": 60, "afternoon": 120}
WALK_SPEED_M_PER_MIN = 75.0
RIDE_SPEED_M_PER_MIN = 250.0

SCENE_LABELS = {
    "walk": "散步",
    "photo": "拍照",
    "flower_viewing": "赏花",
    "study": "自习",
    "date": "约会",
    "dining": "餐饮",
}

# ---------------------------------------------------------------------------
# 行程意图判定（纯规则，不依赖句向量）
# ---------------------------------------------------------------------------

#: 强规划词：出现即按行程处理（除非同时是精确地点查询）。
_STRONG_KEYWORDS = (
    "一日游", "一日行程", "一日游览", "一天行程", "一天的行程",
    "规划一天", "安排一天", "行程规划", "游览路线", "参观路线",
    "带我逛", "怎么逛", "帮我规划", "帮我安排", "一整天", "整天",
)

#: 弱意图词：只描述"新人身份"，单独出现不足以判定行程——"我刚来学校，想去图书馆"
#: 问的是地点而不是行程。必须与 _TIME_HINTS 同时出现才按行程处理。
_WEAK_KEYWORDS = ("第一次来", "第一次到", "初来", "初到", "刚来")

#: 时段/时长线索：与弱意图词搭配出现时，才说明用户想安排一整天。
_TIME_HINTS = ("上午", "中午", "下午", "早上", "晚上",
               "一天", "一日", "半天", "全天", "整天")

#: 精确地点查询：即使带规划词也应交给"地点直查"链路。
_LOCATION_LOOKUP = re.compile(r"在哪里|在哪|在哪儿|怎么走|怎么去|导航")

#: 单一场景诉求词：命中即交回原场景推荐，避免抢走单场景提问。
_SINGLE_SCENE_HINTS = (
    "拍照", "赏花", "看花", "散步", "约会", "自习", "吃饭", "食堂", "停车",
)

#: 同时提到上午与下午，视作想要分时段安排。
_TIME_WINDOW_PATTERN = re.compile(r"上午[\s\S]{0,20}下午|下午[\s\S]{0,20}上午")


def is_itinerary_query(query) -> bool:
    """判断是否为"一日行程规划"类提问。

    采用纯规则实现：既避免误伤既有单场景问答，也让本功能在
    `SEMANTIC_ENABLED=false`（不加载句向量模型）时依然可用。
    """
    text = (query or "").strip()
    if not text or len(text) > 120:
        return False
    if _LOCATION_LOOKUP.search(text):
        return False
    if any(keyword in text for keyword in _STRONG_KEYWORDS):
        return True
    if any(keyword in text for keyword in _SINGLE_SCENE_HINTS):
        return False
    if _TIME_WINDOW_PATTERN.search(text):
        return True
    # 弱意图词必须配时段线索才算行程意图，否则会抢走"刚来/初来 + 地点"的正常问句。
    if not any(keyword in text for keyword in _WEAK_KEYWORDS):
        return False
    return any(keyword in text for keyword in _TIME_HINTS)


# ---------------------------------------------------------------------------
# 几何与候选处理
# ---------------------------------------------------------------------------


def _distance_meters(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """与 server.py `_distance_meters` 同口径的近似平面距离（米）。

    这里独立实现而不 import server，以保持本模块的纯函数性质与可离线测试性。
    """
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111320 * math.cos(mean_lat)
    dy = (lat1 - lat2) * 110540
    return (dx * dx + dy * dy) ** 0.5


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _key(item: dict) -> tuple:
    """候选的统一标识（名称 + 6 位小数坐标）。

    去重与"已占用"判定必须同口径，集中在这里避免两处各写一份导致漂移。
    """
    return (item["name"], round(item["lng"], 6), round(item["lat"], 6))


def _normalize_candidates(items) -> list[dict]:
    """清洗候选：剔除无坐标项、按 (name, lng, lat) 去重、保持调用方给入的优先级。"""
    cleaned, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        lng = _as_float(item.get("lng"))
        lat = _as_float(item.get("lat"))
        name = (item.get("name") or "").strip()
        if lng is None or lat is None or not name:
            continue
        normalized = dict(item, name=name, lng=lng, lat=lat)
        key = _key(normalized)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def _period_pool(period: str, candidates: list[dict]) -> list[dict]:
    """取出该时段的候选池。

    中午强制只保留餐饮候选：候选完全没有类别信息时，信任调用方已按餐饮筛过、
    原样返回；部分候选带类别时以 canteen 为准，若一条餐饮都没有则退回"未标注
    类别"的候选，避免因调用方只标注了一部分而把中午整体清空。
    """
    if period == PERIODS[1]:
        with_category = [item for item in candidates if item.get("category")]
        if with_category:
            dining = [item for item in candidates if item.get("category") == NOON_CATEGORY]
            return dining or [item for item in candidates if not item.get("category")]
    return candidates


def _nearest_available(pool, current, used, max_walk_meters):
    """在未使用过的候选里挑离 current 最近的一个。

    优先取距离不超过阈值的候选；若全都在阈值之外，则退化为"最近的那个"并
    由调用方据此把该段通行标记为骑行/校车，保证三段仍有落点。
    """
    best, best_distance = None, None
    fallback, fallback_distance = None, None
    for item in pool:
        if _key(item) in used:
            continue
        distance = 0.0 if current is None else _distance_meters(
            current[0], current[1], item["lng"], item["lat"]
        )
        if best_distance is None or distance < best_distance:
            best, best_distance = item, distance
        if distance <= max_walk_meters and (fallback_distance is None or distance < fallback_distance):
            fallback, fallback_distance = item, distance
    if fallback is not None:
        return fallback, fallback_distance
    return best, best_distance


def _dwell_minutes(period: str, candidate: dict) -> int:
    value = candidate.get("dwellMinutes")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return int(DEFAULT_DWELL_MINUTES.get(period, 60))


def _stop_from(candidate: dict, seq: int, period: str, campus: str) -> dict:
    """把候选裁剪成对外契约里的 stop 结构（只保留前端需要的字段）。"""
    reason = (candidate.get("reason") or "").strip()
    if not reason:
        scene_label = SCENE_LABELS.get(candidate.get("scene") or "")
        reason = f"{scene_label}推荐地点" if scene_label else f"{PERIOD_LABELS.get(period, '')}推荐地点"
    return {
        "seq": seq,
        "period": period,
        "periodLabel": PERIOD_LABELS.get(period, ""),
        "name": candidate["name"],
        "lng": candidate["lng"],
        "lat": candidate["lat"],
        "campus": candidate.get("campus") or campus,
        "scene": candidate.get("scene"),
        "category": candidate.get("category"),
        "subCategory": candidate.get("subCategory"),
        "reason": reason,
        "dwellMinutes": _dwell_minutes(period, candidate),
    }


def _leg_from(previous: dict, following: dict, max_walk_meters: float) -> dict:
    distance = _distance_meters(
        previous["lng"], previous["lat"], following["lng"], following["lat"]
    )
    walkable = distance <= max_walk_meters
    speed = WALK_SPEED_M_PER_MIN if walkable else RIDE_SPEED_M_PER_MIN
    return {
        "fromSeq": previous["seq"],
        "toSeq": following["seq"],
        "mode": "walking" if walkable else "riding",
        "distanceMeters": int(round(distance)),
        "durationMinutes": max(1, int(round(distance / speed))),
        "fromName": previous["name"],
        "toName": following["name"],
    }


# ---------------------------------------------------------------------------
# 编排主入口
# ---------------------------------------------------------------------------


def plan_day(campus, period_candidates, origin=None, *,
             max_walk_meters=DEFAULT_MAX_WALK_METERS,
             max_stops_per_period=DEFAULT_MAX_STOPS_PER_PERIOD) -> dict:
    """把三个时段的候选编排成一条行程。

    参数
    ----
    campus: 校区名（字符串，不写死具体校区）。
    period_candidates: `{'morning': [...], 'noon': [...], 'afternoon': [...]}`，
        每个元素至少含 `name/lng/lat`，可选 `category/scene/reason/dwellMinutes`。
    origin: 起点 `(lng, lat)`；为 None 时从上午首个候选起步（首次到访场景下
        起点未知，用"评分最高的上午候选"起步比凭空取一个中心点更稳妥）。

    返回
    ----
    `{'campus', 'stops', 'legs', 'skipped', 'sufficient'}`
    `sufficient` 为 False 表示站点不足，调用方应退回单场景推荐。
    """
    pools = {period: _normalize_candidates((period_candidates or {}).get(period))
             for period in PERIODS}

    stops, legs, skipped = [], [], []
    used = set()
    current = None
    if origin is not None:
        try:
            lng, lat = _as_float(origin[0]), _as_float(origin[1])
        except (TypeError, IndexError, KeyError):
            lng = lat = None
        if lng is not None and lat is not None:
            current = (lng, lat)

    for index, period in enumerate(PERIODS):
        pool = _period_pool(period, pools[period])
        remaining_periods = len(PERIODS) - index - 1
        # 为后面的时段各预留 1 个名额，保证三段都能落到点。
        allowance = MAX_TOTAL_STOPS - len(stops) - remaining_periods
        cap = min(max_stops_per_period, PERIOD_STOP_CAP.get(period, max_stops_per_period))
        take = max(1, min(cap, allowance))
        picked = 0
        while picked < take and len(stops) < MAX_TOTAL_STOPS:
            candidate, distance = _nearest_available(pool, current, used, max_walk_meters)
            if candidate is None:
                break
            used.add(_key(candidate))
            stop = _stop_from(candidate, len(stops) + 1, period, campus)
            if stops:
                legs.append(_leg_from(stops[-1], stop, max_walk_meters))
            stops.append(stop)
            current = (stop["lng"], stop["lat"])
            picked += 1
        if picked == 0:
            skipped.append({"period": period, "label": PERIOD_LABELS.get(period, ""),
                            "reason": "no_candidate"})

    return {
        "campus": campus,
        "stops": stops,
        "legs": legs,
        "skipped": skipped,
        "sufficient": len(stops) >= MIN_TOTAL_STOPS,
    }


# ---------------------------------------------------------------------------
# 文案：大模型上下文 + 确定性兜底
# ---------------------------------------------------------------------------


def build_itinerary_context(campus, plan) -> str:
    """拼装交给大模型的行程上下文，约束其只能按既定骨架写作。"""
    stops = plan.get("stops") or []
    if not stops:
        return ""
    lines = [f"【一日行程骨架】校区：{campus}。请严格按下列顺序与站点描述，"
             "不得新增、替换或遗漏地点，也不要编造营业时间与票价。"]
    for stop in stops:
        lines.append(
            f"{stop['seq']}. {stop['periodLabel']}｜{stop['name']}"
            f"（停留约 {stop['dwellMinutes']} 分钟）｜推荐理由：{stop['reason']}"
        )
    for leg in plan.get("legs") or []:
        mode_label = "步行" if leg["mode"] == "walking" else "建议骑行或乘校车"
        lines.append(
            f"第 {leg['fromSeq']} 站 → 第 {leg['toSeq']} 站：{mode_label}，"
            f"约 {leg['distanceMeters']} 米 / {leg['durationMinutes']} 分钟"
        )
    for item in plan.get("skipped") or []:
        lines.append(f"{item['label']}时段暂无合适地点，请在文案中说明该段留白的原因。")
    lines.append("请用第二人称写一段总时长约一天的中文行程说明，语气亲切、给出顺序与理由。")
    return "\n".join(lines)


def render_fallback_text(campus, plan) -> str:
    """大模型不可用时的确定性文案，保证功能不中断。"""
    stops = plan.get("stops") or []
    if not stops:
        return f"{campus}校区暂时无法生成一日行程，建议先按单场景推荐选择想去的地方。"
    lines = [f"为你规划了{campus}校区的一日行程，共 {len(stops)} 站，按顺序游览即可："]
    for stop in stops:
        lines.append(
            f"{stop['periodLabel']}去{stop['name']}（建议停留约 {stop['dwellMinutes']} 分钟）："
            f"{stop['reason']}"
        )
    for leg in plan.get("legs") or []:
        if leg["mode"] == "walking":
            lines.append(
                f"从{leg['fromName']}到{leg['toName']}步行约 {leg['durationMinutes']} 分钟"
                f"（{leg['distanceMeters']} 米）。"
            )
        else:
            lines.append(
                f"{leg['fromName']}到{leg['toName']}约 {leg['distanceMeters']} 米，"
                "步行偏远，建议骑行或乘校车。"
            )
    for item in plan.get("skipped") or []:
        lines.append(f"{item['label']}时段暂无合适地点，这一段可以自由安排。")
    return "\n".join(lines)


def build_itinerary_payload(campus, plan, fallback=False) -> dict:
    """组装对外响应结构（前端契约见 webapp/frontend/src/components/itineraryTypes.ts）。

    结构里不放文案：行程说明只出现在 `choices[0].message.content`，避免同一段
    文本在响应里传两份、也避免前端出现"两份真相"。
    """
    return {
        "title": f"{campus}校区一日游",
        "campus": campus,
        "fallback": bool(fallback),
        "stops": plan.get("stops") or [],
        "legs": plan.get("legs") or [],
        "skipped": plan.get("skipped") or [],
    }


def build_location_pins(plan) -> list[dict]:
    """把站点投影成 /api/chat 的 `locations` 打点数组。

    `kind` 用 `itinerary_stop` 与既有的 `ranked_place` 区分，前端据此决定是否
    按序号标注并连通步行路线；`number` 与地图标签保持一致。
    """
    return [
        {
            "name": stop["name"],
            "lng": stop["lng"],
            "lat": stop["lat"],
            "campus": stop["campus"],
            "number": f"第{stop['seq']}站",
            "kind": "itinerary_stop",
            "seq": stop["seq"],
            "period": stop["period"],
            "periodLabel": stop["periodLabel"],
            "reason": stop["reason"],
            "dwellMinutes": stop["dwellMinutes"],
        }
        for stop in plan.get("stops") or []
    ]


def build_chat_reply(campus, plan, summary, fallback=False) -> dict:
    """行程查询的完整 /api/chat 响应体，与 `heatmap_chat_response` 同层。

    沿用既有的 `choices` / `locations` 契约（助手文本与地图打点照旧可用），
    行程结构化数据放在新增的顶层 `itinerary` 字段里，既有字段一个不改。
    """
    return {
        "choices": [{"message": {"role": "assistant", "content": summary}}],
        "locations": build_location_pins(plan),
        "ranked_places": [],
        "itinerary": build_itinerary_payload(campus, plan, fallback=fallback),
        "recommendation_engine": "itinerary",
    }
