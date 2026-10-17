"""校园点位评论的数据访问层：留言、表态（点赞 / 爱心）、举报与审核留痕。

设计要点
--------
1. **复用 `auth_db` 的线程本地连接**：与 `users` 表同一套 MySQL 配置与连接管理，
   坚持项目"裸 SQL、无 ORM"的既有约定，不引入连接池或 ORM。
2. **表自检**：本仓库没有迁移体系（DDL 靠手写 `*.sql` 手工执行）。这里在首次访问时
   执行一次 `CREATE TABLE IF NOT EXISTS`（幂等，内容与 `poi_comments.sql` 一致），
   让功能开箱即用；失败只记日志，不阻塞其它接口。
3. **纯函数与 IO 分离**：`is_long_comment()` / `pick_preview()` 不碰数据库，
   可以在 CI（无 MySQL）里直接单测；路由层只做参数校验与错误映射。
4. **计数冗余**：`poi_comments` 冗余 `like_count/love_count/report_count`，
   与表态表 / 举报表在同一事务内更新，列表页不再 `COUNT(*)`。
5. **状态机**：`visible → hidden`（举报达阈值自动折叠，或管理员隐藏）→ `visible`（恢复）；
   `visible/hidden → deleted`（软删除）。`hidden` 只影响可见性，绝不删数据。
6. **失败静默**：所有 IO 都用 try/except 包住，异常只写一行**不含用户原文**的日志，
   并抛 `CommentStoreError` 交给路由层转成软提示，绝不把数据库异常漏给前端。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from auth_db import commit, get_cursor, rollback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 阈值与限额（便于调整，勿在别处散落魔数）
# ---------------------------------------------------------------------------

#: 同一评论被多少人举报即自动折叠隐藏
REPORT_HIDE_THRESHOLD = 3
#: 达到该字数视为"长评"，弹窗预览优先展示 1 条长评
LONG_COMMENT_MIN_CHARS = 80
#: 单条留言字数上限
MAX_COMMENT_CHARS = 500
#: 分页
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
#: 字段长度上限（与 poi_comments.sql 的列宽一致）
MAX_POI_ID_CHARS = 64
MAX_POI_NAME_CHARS = 100
MAX_POI_CATEGORY_CHARS = 32
MAX_USERNAME_CHARS = 20
MAX_REASON_TEXT_CHARS = 200
MAX_NOTE_CHARS = 200
#: 管理端一次最多返回多少条
MANAGE_PAGE_LIMIT = 200

STATUS_VISIBLE = 'visible'
STATUS_HIDDEN = 'hidden'
STATUS_DELETED = 'deleted'
STATUSES = (STATUS_VISIBLE, STATUS_HIDDEN, STATUS_DELETED)

REACTION_LIKE = 'like'
REACTION_LOVE = 'love'
REACTIONS = (REACTION_LIKE, REACTION_LOVE)

MODERATION_RESTORE = 'restore'
MODERATION_DELETE = 'delete'
MODERATION_BAN_AUTHOR = 'ban_author'
MODERATION_ACTIONS = (MODERATION_RESTORE, MODERATION_DELETE, MODERATION_BAN_AUTHOR)

#: 举报原因枚举（值 → 中文标签），前端单选框直接复用同一份口径
REPORT_REASONS = (
    ('spam', '垃圾广告 / 营销'),
    ('porn', '色情低俗'),
    ('abuse', '人身攻击 / 辱骂'),
    ('misinfo', '不实信息'),
    ('privacy', '泄露隐私'),
    ('politics', '涉政违规'),
    ('other', '其他'),
)
REPORT_REASON_LABELS = dict(REPORT_REASONS)
REPORT_REASON_CODES = tuple(REPORT_REASON_LABELS)


class CommentStoreError(RuntimeError):
    """数据层不可用或写入失败；路由层捕获后统一转成 503 软提示。"""


# ---------------------------------------------------------------------------
# 建表自检
# ---------------------------------------------------------------------------

_DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS `poi_comments` (
      `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
      `poi_id` varchar(64) NOT NULL,
      `poi_name` varchar(100) NOT NULL DEFAULT '',
      `poi_category` varchar(32) NOT NULL DEFAULT '',
      `user_id` int(10) unsigned NOT NULL,
      `username` varchar(20) NOT NULL,
      `content` varchar(500) NOT NULL,
      `content_len` smallint(5) unsigned NOT NULL DEFAULT '0',
      `is_long` tinyint(1) NOT NULL DEFAULT '0',
      `status` varchar(16) NOT NULL DEFAULT 'visible',
      `like_count` int(10) unsigned NOT NULL DEFAULT '0',
      `love_count` int(10) unsigned NOT NULL DEFAULT '0',
      `report_count` int(10) unsigned NOT NULL DEFAULT '0',
      `created_at` datetime NOT NULL,
      `updated_at` datetime NOT NULL,
      PRIMARY KEY (`id`),
      KEY `idx_poi_created` (`poi_id`,`status`,`created_at`),
      KEY `idx_poi_hot` (`poi_id`,`status`,`like_count`),
      KEY `idx_user` (`user_id`),
      KEY `idx_status_report` (`status`,`report_count`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `comment_reactions` (
      `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
      `comment_id` bigint(20) unsigned NOT NULL,
      `user_id` int(10) unsigned NOT NULL,
      `reaction` varchar(8) NOT NULL,
      `created_at` datetime NOT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uniq_comment_user_reaction` (`comment_id`,`user_id`,`reaction`),
      KEY `idx_user` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `comment_reports` (
      `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
      `comment_id` bigint(20) unsigned NOT NULL,
      `user_id` int(10) unsigned NOT NULL,
      `reason_code` varchar(32) NOT NULL,
      `reason_text` varchar(200) NOT NULL DEFAULT '',
      `created_at` datetime NOT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uniq_comment_user` (`comment_id`,`user_id`),
      KEY `idx_comment` (`comment_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `comment_moderation_log` (
      `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
      `comment_id` bigint(20) unsigned NOT NULL,
      `admin_action` varchar(32) NOT NULL,
      `note` varchar(200) NOT NULL DEFAULT '',
      `created_at` datetime NOT NULL,
      PRIMARY KEY (`id`),
      KEY `idx_comment` (`comment_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `comment_banned_users` (
      `user_id` int(10) unsigned NOT NULL,
      `username` varchar(20) NOT NULL DEFAULT '',
      `note` varchar(200) NOT NULL DEFAULT '',
      `banned_at` datetime NOT NULL,
      PRIMARY KEY (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema() -> bool:
    """幂等建表；成功返回 True。失败只记一行日志并返回 False（下次调用会重试）。"""
    global _schema_ready
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        try:
            cursor = get_cursor()
            for statement in _DDL_STATEMENTS:
                cursor.execute(statement)
            commit()
        except Exception:
            logger.warning('poi comment schema check failed; tables may be missing')
            return False
        _schema_ready = True
        return True


# ---------------------------------------------------------------------------
# 纯函数（无 IO，可直接单测）
# ---------------------------------------------------------------------------


def normalize_content(content) -> str:
    """统一正文口径：去首尾空白、合并内部空白，长度按字符数计。"""
    if content is None:
        return ''
    return ' '.join(str(content).split())


def is_long_comment(content) -> bool:
    """字数达阈值即为长评（弹窗预览优先展示长评）。"""
    return len(normalize_content(content)) >= LONG_COMMENT_MIN_CHARS


def pick_preview(long_comment, short_comments):
    """弹窗预览策略：有长评则取 1 条长评，否则取至多 2 条短评。

    返回 ``(items, mode)``，``mode`` ∈ ``{'long', 'short', 'empty'}``。
    """
    if long_comment:
        return [long_comment], 'long'
    shorts = list(short_comments or [])[:2]
    return shorts, ('short' if shorts else 'empty')


def clamp_page(page, size):
    """分页参数收敛：非法输入一律回落到第一页默认条数，避免 SQL 注入式拼接。"""
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    page = max(1, page)
    size = max(1, min(MAX_PAGE_SIZE, size))
    return page, size


def clamp_text(value, limit) -> str:
    """字段级截断，保证不会因为超长写入被数据库拒绝。"""
    text = normalize_content(value)
    return text[:limit]


# ---------------------------------------------------------------------------
# 行映射
# ---------------------------------------------------------------------------

_COMMENT_COLUMNS = (
    'id', 'poi_id', 'poi_name', 'poi_category', 'user_id', 'username',
    'content', 'content_len', 'is_long', 'status',
    'like_count', 'love_count', 'report_count', 'created_at',
)

_VIEWER_JOINS = (
    " LEFT JOIN comment_reactions rl"
    " ON rl.comment_id = c.id AND rl.reaction = 'like' AND rl.user_id = %s"
    " LEFT JOIN comment_reactions rv"
    " ON rv.comment_id = c.id AND rv.reaction = 'love' AND rv.user_id = %s"
    " LEFT JOIN comment_reports rp"
    " ON rp.comment_id = c.id AND rp.user_id = %s"
)

_FLAG_COLUMNS = ('is_liked', 'is_loved', 'is_reported')

_SELECT_COLUMNS = ', '.join(f'c.{name}' for name in _COMMENT_COLUMNS)
_FLAG_ALIASES = ('rl.user_id', 'rv.user_id', 'rp.user_id')
_SELECT_FLAGS = ', '.join(f'{alias} IS NOT NULL' for alias in _FLAG_ALIASES)


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _serialize(row, viewer_id=0) -> dict:
    """把一行（主表列 + 三个表态标记）转成前端契约对象。"""
    values = dict(zip(_COMMENT_COLUMNS + _FLAG_COLUMNS, row))
    created_at = values.get('created_at')
    created_ms = 0
    if isinstance(created_at, datetime):
        created_ms = int(created_at.timestamp() * 1000)
    return {
        'id': int(values['id']),
        'poiId': values['poi_id'],
        'poiName': values['poi_name'],
        'poiCategory': values['poi_category'],
        'userId': int(values['user_id']),
        'username': values['username'],
        'content': values['content'],
        'contentLen': int(values['content_len'] or 0),
        'isLong': bool(values['is_long']),
        'status': values['status'],
        'likeCount': int(values['like_count'] or 0),
        'loveCount': int(values['love_count'] or 0),
        'reportCount': int(values['report_count'] or 0),
        'createdAt': created_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(created_at, datetime) else '',
        'createdAtMs': created_ms,
        'isMine': bool(viewer_id) and int(values['user_id']) == int(viewer_id),
        'isLiked': bool(values.get('is_liked')),
        'isLoved': bool(values.get('is_loved')),
        'isReported': bool(values.get('is_reported')),
    }


def _log_failure(action: str, *ids) -> None:
    """失败日志只带标识，不带用户正文。"""
    logger.warning('poi comment %s failed %s', action, ' '.join(str(i) for i in ids))


def _write_moderation_log(cursor, comment_id, action, note) -> None:
    cursor.execute(
        'INSERT INTO comment_moderation_log (comment_id, admin_action, note, created_at)'
        ' VALUES (%s, %s, %s, %s)',
        (comment_id, action, clamp_text(note, MAX_NOTE_CHARS), _now()),
    )


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------


def _visibility_sql(include_hidden, own_hidden=True, prefix='c.') -> str:
    """可见性条件。

    - `include_hidden=True`（管理员）：除软删除外全部可见；
    - 普通用户：只看 `visible`，外加**自己**被折叠的评论（前端渲染"已折叠"占位条）。
      `own_hidden=False` 时连自己折叠的也不看（弹窗预览用，保证预览内容是干净的）。
    """
    if include_hidden:
        return f"{prefix}status <> 'deleted'"
    if not own_hidden:
        return f"{prefix}status = 'visible'"
    return (
        f"({prefix}status = 'visible'"
        f" OR ({prefix}status = 'hidden' AND {prefix}user_id = %s))"
    )


def build_list_query(poi_id, viewer_id, include_hidden, sort, limit, offset,
                     only_long=None, own_hidden=True):
    """构造列表 / 预览查询，返回 ``(sql, params)``。

    单独抽成**纯函数**的原因：拼接出的 SQL 一旦少个逗号（例如把
    `c.created_at` 和 `rl.user_id IS NOT NULL` 粘成 `c.created_atrl.user_id`），
    只有真正连上 MySQL 才会暴露。这里让单测能直接校验"占位符数量 == 参数数量"
    与列分隔，不必依赖数据库。
    """
    where = ['c.poi_id = %s', _visibility_sql(include_hidden, own_hidden)]
    # 参数顺序：三个 JOIN 的 viewer_id 在前，然后是 WHERE 里的 poi_id 与可见性条件
    params = [viewer_id, viewer_id, viewer_id, poi_id]
    if not include_hidden and own_hidden:
        params.append(viewer_id)
    if only_long is True:
        where.append('c.is_long = 1')
    elif only_long is False:
        where.append('c.is_long = 0')
    if sort == 'new':
        order = 'c.id DESC'
    else:
        # 热度 = 点赞 + 爱心，再按 id 兜底，保证分页排序稳定
        order = '(c.like_count + c.love_count) DESC, c.id DESC'
    sql = (
        f'SELECT {_SELECT_COLUMNS}, {_SELECT_FLAGS} FROM poi_comments c'
        f'{_VIEWER_JOINS} WHERE {" AND ".join(where)}'
        f' ORDER BY {order} LIMIT %s OFFSET %s'
    )
    return sql, tuple(params + [limit, offset])


def _query_comments(cursor, poi_id, viewer_id, include_hidden, sort, limit, offset,
                    only_long=None, own_hidden=True):
    sql, params = build_list_query(
        poi_id, viewer_id, include_hidden, sort, limit, offset, only_long, own_hidden)
    cursor.execute(sql, params)
    return [_serialize(row, viewer_id) for row in cursor.fetchall()]


def _count_comments(cursor, poi_id, viewer_id, include_hidden, only_long=None,
                    own_hidden=True) -> int:
    where = ['poi_id = %s', _visibility_sql(include_hidden, own_hidden, prefix='')]
    params = [poi_id]
    if not include_hidden and own_hidden:
        params.append(viewer_id)
    if only_long is True:
        where.append('is_long = 1')
    elif only_long is False:
        where.append('is_long = 0')
    cursor.execute(
        f'SELECT COUNT(*) FROM poi_comments WHERE {" AND ".join(where)}', tuple(params))
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def list_comments(poi_id, viewer_id=0, sort='hot', page=1, size=DEFAULT_PAGE_SIZE,
                  include_hidden=False) -> dict:
    """按点位分页取评论（附带当前用户的表态标记）。"""
    ensure_schema()
    poi_id = clamp_text(poi_id, MAX_POI_ID_CHARS)
    if not poi_id:
        return {'items': [], 'total': 0, 'page': 1, 'size': DEFAULT_PAGE_SIZE, 'hasMore': False}
    page, size = clamp_page(page, size)
    try:
        cursor = get_cursor()
        total = _count_comments(cursor, poi_id, viewer_id, include_hidden)
        items = _query_comments(
            cursor, poi_id, viewer_id, include_hidden,
            'new' if sort == 'new' else 'hot', size, (page - 1) * size)
    except Exception:
        rollback()
        _log_failure('list', poi_id)
        raise CommentStoreError('读取留言失败')
    return {
        'items': items,
        'total': total,
        'page': page,
        'size': size,
        'hasMore': page * size < total,
    }


def preview_comments(poi_id, viewer_id=0) -> dict:
    """弹窗预览：1 条长评，或无长评时 2 条短评；同时带出该点的汇总统计。

    `stats` 只统计公开可见的留言（弹窗顶部显示的就是这个数），
    另用 `myHidden` 单独告诉前端"你自己有 N 条被折叠了"——否则作者自己
    被折叠的留言在弹窗里既看不到、也没有入口进列表查看（头部计数不含它，
    "查看全部"按钮就不会出现）。
    """
    ensure_schema()
    poi_id = clamp_text(poi_id, MAX_POI_ID_CHARS)
    empty_stats = {'total': 0, 'long': 0, 'short': 0, 'like': 0, 'love': 0}
    if not poi_id:
        return {'poiId': poi_id, 'mode': 'empty', 'items': [], 'stats': empty_stats,
                'myHidden': 0}
    try:
        cursor = get_cursor()
        long_items = _query_comments(
            cursor, poi_id, viewer_id, False, 'hot', 1, 0, only_long=True, own_hidden=False)
        long_comment = long_items[0] if long_items else None
        short_items = []
        if long_comment is None:
            short_items = _query_comments(
                cursor, poi_id, viewer_id, False, 'hot', 2, 0,
                only_long=False, own_hidden=False)
        items, mode = pick_preview(long_comment, short_items)
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_long = 1), 0), COALESCE(SUM(is_long = 0), 0),"
            " COALESCE(SUM(like_count), 0), COALESCE(SUM(love_count), 0)"
            " FROM poi_comments WHERE poi_id = %s AND status = 'visible'",
            (poi_id,),
        )
        row = cursor.fetchone() or (0, 0, 0, 0, 0)
        stats = {
            'total': int(row[0] or 0),
            'long': int(row[1] or 0),
            'short': int(row[2] or 0),
            'like': int(row[3] or 0),
            'love': int(row[4] or 0),
        }
        my_hidden = 0
        if viewer_id:
            cursor.execute(
                "SELECT COUNT(*) FROM poi_comments"
                " WHERE poi_id = %s AND status = %s AND user_id = %s",
                (poi_id, STATUS_HIDDEN, int(viewer_id)),
            )
            my_hidden = int((cursor.fetchone() or (0,))[0] or 0)
    except Exception:
        rollback()
        _log_failure('preview', poi_id)
        raise CommentStoreError('读取留言预览失败')
    return {
        'poiId': poi_id,
        'poiName': items[0]['poiName'] if items else '',
        'mode': mode,
        'items': items,
        'stats': stats,
        'myHidden': my_hidden,
    }


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------


def create_comment(poi_id, poi_name, poi_category, user_id, username, content):
    """发布留言；正文非法（空 / 超长）时返回 None 表示"入参不合法"。"""
    ensure_schema()
    poi_id = clamp_text(poi_id, MAX_POI_ID_CHARS)
    text = normalize_content(content)
    if not poi_id or not text or len(text) > MAX_COMMENT_CHARS:
        return None
    now = _now()
    long_flag = 1 if is_long_comment(text) else 0
    try:
        cursor = get_cursor()
        cursor.execute(
            'INSERT INTO poi_comments (poi_id, poi_name, poi_category, user_id, username,'
            ' content, content_len, is_long, status, created_at, updated_at)'
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'visible', %s, %s)",
            (
                poi_id,
                clamp_text(poi_name, MAX_POI_NAME_CHARS),
                clamp_text(poi_category, MAX_POI_CATEGORY_CHARS),
                int(user_id),
                clamp_text(username, MAX_USERNAME_CHARS),
                text,
                len(text),
                long_flag,
                now,
                now,
            ),
        )
        new_id = int(cursor.lastrowid)
        commit()
    except Exception:
        rollback()
        _log_failure('create', poi_id, user_id)
        raise CommentStoreError('发布留言失败')
    return {
        'id': new_id,
        'poiId': poi_id,
        'poiName': clamp_text(poi_name, MAX_POI_NAME_CHARS),
        'poiCategory': clamp_text(poi_category, MAX_POI_CATEGORY_CHARS),
        'userId': int(user_id),
        'username': clamp_text(username, MAX_USERNAME_CHARS),
        'content': text,
        'contentLen': len(text),
        'isLong': bool(long_flag),
        'status': STATUS_VISIBLE,
        'likeCount': 0,
        'loveCount': 0,
        'reportCount': 0,
        'createdAt': now,
        'createdAtMs': int(datetime.now().timestamp() * 1000),
        'isMine': True,
        'isLiked': False,
        'isLoved': False,
        'isReported': False,
    }


def delete_comment(comment_id, user_id, is_admin=False) -> bool:
    """软删除：本人可删自己的，管理员可删任意一条。"""
    ensure_schema()
    try:
        cursor = get_cursor()
        if is_admin:
            cursor.execute(
                "UPDATE poi_comments SET status = %s, updated_at = %s"
                " WHERE id = %s AND status <> %s",
                (STATUS_DELETED, _now(), int(comment_id), STATUS_DELETED),
            )
        else:
            cursor.execute(
                "UPDATE poi_comments SET status = %s, updated_at = %s"
                " WHERE id = %s AND user_id = %s AND status <> %s",
                (STATUS_DELETED, _now(), int(comment_id), int(user_id), STATUS_DELETED),
            )
        changed = cursor.rowcount > 0
        commit()
    except Exception:
        rollback()
        _log_failure('delete', comment_id, user_id)
        raise CommentStoreError('删除留言失败')
    return changed


def react(comment_id, user_id, reaction, action):
    """点赞 / 爱心表态，幂等。

    `reaction` ∈ {like, love}（两者互不影响，可同时存在）；
    `action` ∈ {add, remove}。返回最新的计数与本人表态状态。
    """
    ensure_schema()
    if reaction not in REACTIONS or action not in ('add', 'remove'):
        return None
    column = 'like_count' if reaction == REACTION_LIKE else 'love_count'
    try:
        cursor = get_cursor()
        cursor.execute('SELECT status FROM poi_comments WHERE id = %s', (int(comment_id),))
        row = cursor.fetchone()
        if not row or row[0] == STATUS_DELETED:
            return None
        if action == 'add':
            cursor.execute(
                'INSERT IGNORE INTO comment_reactions (comment_id, user_id, reaction, created_at)'
                ' VALUES (%s, %s, %s, %s)',
                (int(comment_id), int(user_id), reaction, _now()),
            )
            if cursor.rowcount:
                cursor.execute(
                    f'UPDATE poi_comments SET {column} = {column} + 1, updated_at = %s'
                    ' WHERE id = %s',
                    (_now(), int(comment_id)),
                )
        else:
            cursor.execute(
                'DELETE FROM comment_reactions WHERE comment_id = %s AND user_id = %s'
                ' AND reaction = %s',
                (int(comment_id), int(user_id), reaction),
            )
            if cursor.rowcount:
                cursor.execute(
                    f'UPDATE poi_comments SET {column} = GREATEST({column} - 1, 0),'
                    ' updated_at = %s WHERE id = %s',
                    (_now(), int(comment_id)),
                )
        cursor.execute(
            'SELECT like_count, love_count, status FROM poi_comments WHERE id = %s',
            (int(comment_id),),
        )
        counts = cursor.fetchone()
        cursor.execute(
            'SELECT reaction FROM comment_reactions WHERE comment_id = %s AND user_id = %s',
            (int(comment_id), int(user_id)),
        )
        mine = {item[0] for item in cursor.fetchall()}
        commit()
    except Exception:
        rollback()
        _log_failure('react', comment_id, user_id)
        raise CommentStoreError('表态失败')
    if not counts:
        return None
    return {
        'id': int(comment_id),
        'likeCount': int(counts[0] or 0),
        'loveCount': int(counts[1] or 0),
        'isLiked': REACTION_LIKE in mine,
        'isLoved': REACTION_LOVE in mine,
    }


def report_comment(comment_id, user_id, reason_code, reason_text=''):
    """举报一条评论；达标即自动折叠。

    返回 ``{'ok': bool, 'reason': str, 'reportCount': int, 'hidden': bool}``：
    - ``reason == 'missing'``：评论不存在或已删除；
    - ``reason == 'self'``：不能举报自己的评论；
    - ``reason == 'duplicate'``：本次为重复举报（不重复计数）。
    """
    ensure_schema()
    if reason_code not in REPORT_REASON_CODES:
        reason_code = 'other'
    try:
        cursor = get_cursor()
        cursor.execute(
            'SELECT user_id, status, report_count FROM poi_comments WHERE id = %s',
            (int(comment_id),),
        )
        row = cursor.fetchone()
        if not row or row[1] == STATUS_DELETED:
            return {'ok': False, 'reason': 'missing', 'reportCount': 0, 'hidden': False}
        if int(row[0]) == int(user_id):
            return {'ok': False, 'reason': 'self', 'reportCount': int(row[2] or 0), 'hidden': False}
        cursor.execute(
            'INSERT IGNORE INTO comment_reports'
            ' (comment_id, user_id, reason_code, reason_text, created_at)'
            ' VALUES (%s, %s, %s, %s, %s)',
            (
                int(comment_id), int(user_id), reason_code,
                clamp_text(reason_text, MAX_REASON_TEXT_CHARS), _now(),
            ),
        )
        if not cursor.rowcount:
            commit()
            return {
                'ok': True,
                'reason': 'duplicate',
                'reportCount': int(row[2] or 0),
                'hidden': row[1] == STATUS_HIDDEN,
            }
        cursor.execute(
            'UPDATE poi_comments SET report_count = report_count + 1, updated_at = %s'
            ' WHERE id = %s',
            (_now(), int(comment_id)),
        )
        cursor.execute('SELECT report_count, status FROM poi_comments WHERE id = %s', (int(comment_id),))
        count, status = cursor.fetchone()
        count = int(count or 0)
        hidden = status == STATUS_HIDDEN
        if count >= REPORT_HIDE_THRESHOLD and status == STATUS_VISIBLE:
            cursor.execute(
                'UPDATE poi_comments SET status = %s, updated_at = %s WHERE id = %s',
                (STATUS_HIDDEN, _now(), int(comment_id)),
            )
            _write_moderation_log(cursor, int(comment_id), 'auto_hide', f'report_count={count}')
            hidden = True
        commit()
    except Exception:
        rollback()
        _log_failure('report', comment_id, user_id)
        raise CommentStoreError('举报失败')
    return {'ok': True, 'reason': '', 'reportCount': count, 'hidden': hidden}


# ---------------------------------------------------------------------------
# 管理端
# ---------------------------------------------------------------------------


def is_user_banned(user_id) -> bool:
    ensure_schema()
    try:
        cursor = get_cursor()
        cursor.execute('SELECT 1 FROM comment_banned_users WHERE user_id = %s', (int(user_id),))
        return cursor.fetchone() is not None
    except Exception:
        rollback()
        _log_failure('ban-check', user_id)
        return False


#: 管理端列表的筛选口径（值 → SQL 条件）
MANAGE_FILTERS = {
    'reported': ["c.status <> 'deleted'", 'c.report_count > 0'],
    'hidden': ["c.status = 'hidden'"],
    'deleted': ["c.status = 'deleted'"],
    'all': ['1 = 1'],
}


def list_comments_for_management(status_filter='reported') -> list:
    """管理端列表：默认看"被举报但未处理"的评论，附举报原因分布。"""
    ensure_schema()
    conditions = MANAGE_FILTERS.get(status_filter, MANAGE_FILTERS['reported'])
    try:
        cursor = get_cursor()
        cursor.execute(
            f'SELECT {_SELECT_COLUMNS} FROM poi_comments c'
            f' WHERE {" AND ".join(conditions)}'
            ' ORDER BY c.report_count DESC, c.id DESC LIMIT %s',
            (MANAGE_PAGE_LIMIT,),
        )
        rows = cursor.fetchall()
        items = []
        for row in rows:
            # 管理端不关心"当前管理员表过什么态"，三个标记一律为假
            item = _serialize(tuple(row) + (False, False, False), 0)
            item['reasonBreakdown'] = _reason_breakdown(cursor, item['id'])
            items.append(item)
    except Exception:
        rollback()
        _log_failure('manage-list', status_filter)
        raise CommentStoreError('读取待审评论失败')
    return items


def _reason_breakdown(cursor, comment_id) -> list:
    cursor.execute(
        'SELECT reason_code, COUNT(*) FROM comment_reports WHERE comment_id = %s'
        ' GROUP BY reason_code',
        (int(comment_id),),
    )
    return [
        {'reason': code, 'label': REPORT_REASON_LABELS.get(code, code), 'count': int(count)}
        for code, count in cursor.fetchall()
    ]


def comment_admin_summary() -> dict:
    ensure_schema()
    try:
        cursor = get_cursor()
        cursor.execute(
            "SELECT COUNT(*),"
            " COALESCE(SUM(status = 'hidden'), 0),"
            " COALESCE(SUM(status = 'deleted'), 0),"
            " COALESCE(SUM(report_count > 0), 0)"
            ' FROM poi_comments'
        )
        row = cursor.fetchone() or (0, 0, 0, 0)
        cursor.execute('SELECT COUNT(*) FROM comment_banned_users')
        banned = cursor.fetchone() or (0,)
    except Exception:
        rollback()
        _log_failure('summary')
        raise CommentStoreError('读取评论统计失败')
    return {
        'total': int(row[0] or 0),
        'hidden': int(row[1] or 0),
        'deleted': int(row[2] or 0),
        'reported': int(row[3] or 0),
        'bannedUsers': int(banned[0] or 0),
    }


def moderate_comment(comment_id, action, note=''):
    """管理员处置：恢复 / 删除 / 封禁作者，全部留痕。"""
    ensure_schema()
    if action not in MODERATION_ACTIONS:
        return None
    try:
        cursor = get_cursor()
        cursor.execute(
            'SELECT user_id, username, status FROM poi_comments WHERE id = %s',
            (int(comment_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        author_id, author_name, _status = int(row[0]), row[1], row[2]
        if action == MODERATION_RESTORE:
            # 恢复时把举报数清零：否则刚恢复就被阈值判定再次折叠
            cursor.execute(
                "UPDATE poi_comments SET status = 'visible', report_count = 0, updated_at = %s"
                ' WHERE id = %s',
                (_now(), int(comment_id)),
            )
        elif action == MODERATION_DELETE:
            cursor.execute(
                "UPDATE poi_comments SET status = 'deleted', updated_at = %s WHERE id = %s",
                (_now(), int(comment_id)),
            )
        else:  # ban_author：封禁作者，并把这条争议留言一并软删除
            cursor.execute(
                "INSERT INTO comment_banned_users (user_id, username, note, banned_at)"
                ' VALUES (%s, %s, %s, %s)'
                ' ON DUPLICATE KEY UPDATE note = VALUES(note), banned_at = VALUES(banned_at)',
                (author_id, author_name, clamp_text(note, MAX_NOTE_CHARS), _now()),
            )
            cursor.execute(
                "UPDATE poi_comments SET status = 'deleted', updated_at = %s WHERE id = %s",
                (_now(), int(comment_id)),
            )
        _write_moderation_log(cursor, int(comment_id), action, note)
        commit()
    except Exception:
        rollback()
        _log_failure('moderate', comment_id, action)
        raise CommentStoreError('审核操作失败')
    return {'id': int(comment_id), 'action': action, 'authorId': author_id, 'author': author_name}
