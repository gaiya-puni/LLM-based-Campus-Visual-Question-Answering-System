"""运行时校区配置：校名、别名、中心点、半径、河流名与地标锚点的**唯一真源**。

设计要点
--------
- `campuses.json` 是唯一真源。`heatmap_config.json` 里既有的 `campuses` 条目仍由两个构建
  脚本与既有单测直接读取，因此这里提供 `check_consistency()` 做交叉校验（由
  `test_campus_config.py` 断言），避免中心点/半径在两处漂移。
- **只依赖标准库、不 import server**，所以单测可以低成本导入，不会触发数据加载。
- 前端需要的校区表由 `render_frontend_ts()` 生成到 `webapp/frontend/src/campusConfig.ts`；
  同一个单测会校验磁盘内容与生成结果一致，防止前后端两处漂移。
- 半径分两个概念：`trustRadiusM`（前端"认为属于该校区"的判定半径）与 `auditRadiusMeters`
  （构建脚本的热图审计半径，只在 `heatmap_config.json` 里维护），二者刻意不合并。
- **多校共存**：用「`schools` 表 + 每个校区的 `school` 外键」表达"学校 → 校区"两级。
  `school_of(campus)` 取校区所属学校；`school_names()` / `school_fragments()` 取**全部学校的并集**，
  避免多校共存时查询清洗漏词（旧配置只有一个全局 `school`，多校下会互相污染）。
- `campus_of()` 保持"必定归属最近校区"的旧语义（既有调用点与单测依赖）；
  **判断坐标是否真在校园内**要用 `campus_at()`（超出全部 `trustRadiusM` 返回 `None`），供"指哪问哪"。
"""
from __future__ import annotations

import json
import math
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, 'campuses.json')
HEATMAP_CONFIG_PATH = os.path.join(BASE, 'heatmap_config.json')
FRONTEND_TS_PATH = os.path.abspath(
    os.path.join(BASE, '..', 'frontend', 'src', 'campusConfig.ts'))

_CACHE = None


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def load(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def config() -> dict:
    """读取并缓存配置；单测可先调用 `reset_cache()`。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = load()
    return _CACHE


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def campuses() -> list:
    return config()['campuses']


def campus_names() -> list:
    return [item['name'] for item in campuses()]


def default_campus() -> str:
    return config()['defaultCampus']


def schools() -> dict:
    """全部学校（id → 学校信息）。旧配置（只有全局 `school`）下退化为单元素表。"""
    data = config()
    if 'schools' in data:
        return data['schools']
    legacy = data.get('school') or {}
    return {'legacy': legacy} if legacy else {}


def school_id_of(campus):
    """校区的学校 id；未登记或旧配置下无该字段时返回 None。"""
    entry = _entry(campus)
    return entry.get('school') if entry else None


def school_of(campus) -> dict:
    """校区所属学校（多校共存下的正确取法）。"""
    school_id = school_id_of(campus)
    table = schools()
    if school_id and school_id in table:
        return table[school_id]
    # 旧配置：没有 schools 表也没有 school 外键时，退回全局 school
    return config().get('school') or (next(iter(table.values())) if table else {})


def school(campus=None) -> dict:
    """兼容旧调用点：不带参数时取**默认校区所属学校**的学校信息。"""
    return school_of(campus or default_campus())


def campuses_of_school(school_id) -> list:
    """某学校名下的全部校区名（保持配置顺序）。"""
    return [item['name'] for item in campuses() if school_id_of(item['name']) == school_id]


def brand() -> dict:
    return config().get('brand') or {}


def app_title() -> str:
    return brand().get('appTitle') or '校园可视可答系统'


def assistant_name() -> str:
    return brand().get('assistantName') or '校园导览助手'


def brand_logo() -> str:
    """品牌 logo 地址；留空表示不显示图片 logo（模板化的默认形态）。"""
    return brand().get('logo') or ''


def _entry(campus):
    for item in campuses():
        if item['name'] == campus:
            return item
    return None


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def aliases(campus) -> list:
    entry = _entry(campus)
    return list(entry['aliases']) if entry else []


def normalize(value):
    """把别名或规范名归一到规范名；未命中返回 None。"""
    text = (value or '').strip()
    if not text:
        return None
    for item in campuses():
        if text == item['name'] or text in item['aliases']:
            return item['name']
    return None


def center(campus):
    entry = _entry(campus)
    return tuple(entry['center']) if entry else None


def trust_radius(campus) -> float:
    entry = _entry(campus)
    return float(entry['trustRadiusM']) if entry else 0.0


def water_name(campus):
    """校区标志性水体名（河或湖，如 丽娃河 / 思源湖）；未登记则为 None。"""
    entry = _entry(campus)
    return entry.get('waterName') if entry else None


def water_pattern() -> str:
    """所有校区水体名的正则交替串，如 ``丽娃河|樱桃河`` 或 ``思源湖|涵泽湖``。"""
    return '|'.join(item['waterName'] for item in campuses() if item.get('waterName'))


def landmark_anchor(campus, label):
    entry = _entry(campus)
    if not entry:
        return None
    point = (entry.get('landmarkAnchors') or {}).get(label)
    if not point:
        return None
    return {'lng': point[0], 'lat': point[1]}


def school_names() -> list:
    """**全部学校**的校名 + 别名 + 英文名，供查询清洗与提示词使用。

    多校共存时必须取并集：只取默认校区的学校会让另一所学校的校名留在查询词里，
    导致按校名检索时匹配不到（旧实现只覆盖单个全局 school）。
    """
    names = []
    for info in schools().values():
        for word in [info['name']] + list(info.get('aliases') or []):
            if word and word not in names:
                names.append(word)
        if info.get('enName') and info['enName'] not in names:
            names.append(info['enName'])
    return names


def campus_words() -> list:
    """校区相关的词：规范名 + 全部别名（供停用词与查询清洗使用）。

    去重并保持稳定顺序，避免同一别名在多处各写一份导致口径漂移。
    """
    words = []
    for item in campuses():
        for word in [item['name']] + list(item['aliases']):
            if word not in words:
                words.append(word)
    return words


def school_fragments() -> list:
    """**全部学校**的校名片段（如"华东"/"师范"/"上海"/"交通"），用于查询停用词与清洗。"""
    fragments = []
    for info in schools().values():
        for word in info.get('stopWords') or []:
            if word and word not in fragments:
                fragments.append(word)
    return fragments


def stop_words() -> list:
    """停用词：校名片段 + 校区名与全部别名 + 多字别名的 2 字片段。

    2 字片段与原实现口径一致（原先手写的"中山"/"北路"就是"中山北路"的片段），
    这样分词窗口产生的短词也能被正确忽略。
    """
    words = list(school_fragments())
    for word in campus_words():
        words.append(word)
        if len(word) >= 3:
            words.extend(word[index:index + 2] for index in range(len(word) - 1))
    return list(dict.fromkeys(words))


def campus_of(lng, lat):
    """按最近的校区中心点判定校区（校区数量不限，替代原先的两分支二分法）。"""
    best, best_distance = None, None
    for item in campuses():
        cx, cy = item['center']
        distance = (lng - cx) ** 2 + (lat - cy) ** 2
        if best_distance is None or distance < best_distance:
            best, best_distance = item['name'], distance
    return best


def distance_m(lng1, lat1, lng2, lat2) -> float:
    """两点球面距离（米）：haversine 实现，保持 stdlib-only（与后端 `_distance_meters` 同口径）。"""
    earth_radius = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * earth_radius * math.asin(math.sqrt(a))


def campus_at(lng, lat):
    """坐标是否**真的落在**某个已登记校区内：返回最近的且在 `trustRadiusM` 内的校区名，否则 None。

    与 `campus_of()` 的分工：`campus_of()` 无论多远都返回"最近的校区"（既有调用点与单测依赖
    这个"兜底归属"语义）；本函数用来回答"这里是不是校园里"，超出全部信任半径时明确返回 None，
    避免把校外坐标硬说成某校区（供"指哪问哪"使用）。
    """
    best, best_distance = None, None
    for item in campuses():
        cx, cy = item['center']
        distance = distance_m(lng, lat, cx, cy)
        if distance > float(item['trustRadiusM']):
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = item['name'], distance
    return best


def audit_radius(campus):
    """审计半径只在 heatmap_config.json 中维护，这里只读不复制。"""
    with open(HEATMAP_CONFIG_PATH, encoding='utf-8') as handle:
        heat = json.load(handle)
    entry = (heat.get('campuses') or {}).get(campus) or {}
    return entry.get('auditRadiusMeters')


# ---------------------------------------------------------------------------
# 一致性校验（供单测断言）
# ---------------------------------------------------------------------------


def check_consistency() -> list:
    """返回不一致项清单；空列表表示两处配置一致。"""
    problems = []
    with open(HEATMAP_CONFIG_PATH, encoding='utf-8') as handle:
        heat_campuses = (json.load(handle).get('campuses') or {})
    known = campus_names()
    known_schools = schools()
    if 'schools' in config():
        # 多校配置：每个校区必须声明 school，且该 id 必须在 schools 表里登记
        for item in campuses():
            school_id = item.get('school')
            if not school_id:
                problems.append(f'{item["name"]}: 多校配置下必须声明 school 外键')
            elif school_id not in known_schools:
                problems.append(
                    f'{item["name"]}: school "{school_id}" 未在 schools 表中登记')
    for item in campuses():
        name = item['name']
        other = heat_campuses.get(name)
        if not other:
            problems.append(f'{name}: heatmap_config.json 缺少该校区')
            continue
        if list(other.get('center') or []) != list(item['center']):
            problems.append(
                f'{name}: center 不一致 {other.get("center")} != {item["center"]}')
        if other.get('slug') != item.get('slug'):
            problems.append(
                f'{name}: slug 不一致 {other.get("slug")} != {item.get("slug")}')
    for name in heat_campuses:
        if name not in known:
            problems.append(f'{name}: 出现在 heatmap_config.json 但未登记到 campuses.json')
    return problems


# ---------------------------------------------------------------------------
# 前端校区表生成
# ---------------------------------------------------------------------------


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_frontend_ts() -> str:
    """生成 `webapp/frontend/src/campusConfig.ts`。

    导出的 `CAMPUS_CENTERS` / `CAMPUS_LOCATION_RADIUS_M` 刻意保持与既有组件里常量的
    相同形状，接入时只需把字面量换成 import。
    """
    school_table = schools()
    brand_info = brand()
    lines = [
        '/**',
        ' * 本文件由 webapp/backend/campus_config.py::render_frontend_ts() 自动生成，请勿手工编辑。',
        ' * 真源：webapp/backend/campuses.json；改完请重新生成，test_campus_config.py 会校验一致性。',
        ' */',
        '',
        'export interface CampusInfo {',
        '  name: string;',
        '  slug: string;',
        '  /** 所属学校 id（对应 SCHOOLS 的键） */',
        '  school: string;',
        '  /** 所属学校中文名，界面可直接展示 */',
        '  schoolName: string;',
        '  aliases: string[];',
        '  /** [lng, lat] */',
        '  center: [number, number];',
        '  trustRadiusM: number;',
        '  waterName?: string;',
        '}',
        '',
        'export interface SchoolInfo {',
        '  name: string;',
        '  enName: string;',
        '  aliases: string[];',
        '}',
        '',
        'export const BRAND = {',
        f'  appTitle: {_js_string(brand_info.get("appTitle") or "校园可视可答系统")},',
        f'  assistantName: {_js_string(brand_info.get("assistantName") or "校园导览助手")},',
        f'  logo: {_js_string(brand_info.get("logo") or "")},',
        '} as const;',
        '',
        'export const APP_TITLE = BRAND.appTitle;',
        'export const ASSISTANT_NAME = BRAND.assistantName;',
        '',
        'export const SCHOOLS: Record<string, SchoolInfo> = {',
    ]
    for school_id, info in school_table.items():
        alias_text = ', '.join(_js_string(word) for word in (info.get('aliases') or []))
        lines += [
            f'  {_js_string(school_id)}: {{',
            f'    name: {_js_string(info["name"])},',
            f'    enName: {_js_string(info.get("enName", ""))},',
            f'    aliases: [{alias_text}],',
            '  },',
        ]
    lines += [
        '};',
        '',
        'export const CAMPUSES: CampusInfo[] = [',
    ]
    for item in campuses():
        alias_text = ', '.join(_js_string(word) for word in item['aliases'])
        school_id = item.get('school') or ''
        school_name = (school_table.get(school_id) or {}).get('name', '')
        lines += [
            '  {',
            f'    name: {_js_string(item["name"])},',
            f'    slug: {_js_string(item["slug"])},',
            f'    school: {_js_string(school_id)},',
            f'    schoolName: {_js_string(school_name)},',
            f'    aliases: [{alias_text}],',
            f'    center: [{item["center"][0]}, {item["center"][1]}],',
            f'    trustRadiusM: {item["trustRadiusM"]},',
            f'    waterName: {_js_string(item.get("waterName") or "")},',
            '  },',
        ]
    lines += [
        '];',
        '',
        f'export const DEFAULT_CAMPUS = {_js_string(default_campus())};',
        '',
        'export const CAMPUS_CENTERS: Record<string, [number, number]> = {',
    ]
    for item in campuses():
        lines.append(
            f'  {_js_string(item["name"])}: [{item["center"][0]}, {item["center"][1]}],')
    lines += ['};', '']
    lines += ['export const CAMPUS_LOCATION_RADIUS_M: Record<string, number> = {']
    for item in campuses():
        lines.append(f'  {_js_string(item["name"])}: {item["trustRadiusM"]},')
    lines += ['};', '']
    lines.append('export const CAMPUS_NAMES: string[] = ['
                 + ', '.join(_js_string(item['name']) for item in campuses()) + '];')
    lines.append('')
    return '\n'.join(lines)


def write_frontend_ts(path: str = FRONTEND_TS_PATH) -> str:
    """把生成结果写入前端文件，返回写入路径。"""
    content = render_frontend_ts()
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)
    return path


if __name__ == '__main__':
    problems = check_consistency()
    if problems:
        print('校区配置不一致：')
        for item in problems:
            print('  -', item)
        raise SystemExit(1)
    print('校区配置一致：', '、'.join(campus_names()),
          '| 默认：', default_campus(),
          '| 品牌：', app_title(), '/', assistant_name(),
          '| 水体：', water_pattern() or '（未登记）')
    for school_id, info in schools().items():
        print('  学校 %-8s %s（%s）→ 校区：%s'
              % (school_id, info.get('name'), info.get('enName'),
                 '、'.join(campuses_of_school(school_id)) or '（无）'))
    print('已写入前端校区表：', write_frontend_ts())
