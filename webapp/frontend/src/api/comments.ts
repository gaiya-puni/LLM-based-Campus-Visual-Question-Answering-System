/**
 * 校园点位评论接口封装：预览 / 列表 / 发布 / 删除 / 点赞 / 爱心 / 举报 / 管理端复核。
 *
 * 写法与 `userdata.ts` 一致：`fetch` + 相对路径 `/api/...`（开发期由 vite 代理到后端）。
 * 写操作依赖 Flask 的 HttpOnly 会话 Cookie，前端读不到，因此后端对未登录写操作返回
 * 401 + `needLogin`；这里统一翻译成 `CommentApiError`，界面据此弹出登录引导而不是报错。
 * 管理端复用 `userdata.ts` 的 `X-Review-Token`（同一个 localStorage 键，管理员模式一处开关）。
 */

import { getReviewToken } from './userdata';

export type CommentReaction = 'like' | 'love';
export type ReactionAction = 'add' | 'remove';
export type CommentSort = 'hot' | 'new';
export type CommentStatus = 'visible' | 'hidden' | 'deleted';
export type ModerationAction = 'restore' | 'delete' | 'ban_author';
export type ManageFilter = 'reported' | 'hidden' | 'deleted' | 'all';

export interface PoiComment {
  id: number;
  poiId: string;
  poiName: string;
  poiCategory: string;
  userId: number;
  username: string;
  content: string;
  contentLen: number;
  /** 长评（达到后端 `LONG_COMMENT_MIN_CHARS` 阈值），弹窗预览优先展示 */
  isLong: boolean;
  status: CommentStatus;
  likeCount: number;
  loveCount: number;
  reportCount: number;
  createdAt: string;
  /** 便于前端算"x 分钟前"，避免解析本地时间字符串 */
  createdAtMs: number;
  isMine: boolean;
  isLiked: boolean;
  isLoved: boolean;
  isReported: boolean;
  /** 仅管理端列表返回：举报原因分布 */
  reasonBreakdown?: { reason: string; label: string; count: number }[];
}

export interface CommentStats {
  total: number;
  long: number;
  short: number;
  like: number;
  love: number;
}

export interface CommentPreview {
  poiId: string;
  poiName: string;
  /** long=1 条长评；short=2 条短评；empty=还没有留言 */
  mode: 'long' | 'short' | 'empty';
  items: PoiComment[];
  /** 只统计公开可见的留言 */
  stats: CommentStats;
  /** 当前用户自己**被折叠**的留言条数；用于决定"查看全部"入口是否出现 */
  myHidden: number;
}

export interface CommentListResult {
  items: PoiComment[];
  total: number;
  page: number;
  size: number;
  hasMore: boolean;
}

/** 坐标反查得到的点位引用 */
export interface PoiRef {
  id: string;
  name: string;
  category: string;
  subCategory: string;
  locationName: string;
  lng: number;
  lat: number;
  distanceM: number;
}

export interface CommentMeta {
  limits: {
    maxChars: number;
    longMinChars: number;
    maxReasonChars: number;
    reportHideThreshold: number;
    pageSize: number;
    maxPageSize: number;
  };
  reportReasons: { value: string; label: string }[];
  reactions: CommentReaction[];
}

export interface ReactionResult {
  id: number;
  likeCount: number;
  loveCount: number;
  isLiked: boolean;
  isLoved: boolean;
}

export interface ReportResult {
  message: string;
  duplicate: boolean;
  reportCount: number;
  hidden: boolean;
}

export interface ManageSummary {
  total: number;
  hidden: number;
  deleted: number;
  reported: number;
  bannedUsers: number;
}

export interface ManageResult {
  status: ManageFilter;
  items: PoiComment[];
  summary: ManageSummary;
  thresholds: { reportHideThreshold: number };
}

/** 评论接口的统一错误：带 HTTP 状态码与"是否只是没登录"，方便界面分流处理。 */
export class CommentApiError extends Error {
  readonly status: number;
  readonly needLogin: boolean;

  constructor(message: string, status: number, needLogin: boolean) {
    super(message);
    this.name = 'CommentApiError';
    this.status = status;
    this.needLogin = needLogin;
  }
}

function readJson<T>(response: Response, url: string): Promise<T> {
  return response.json()
    .catch(() => null)
    .then((payload: any) => {
      if (!response.ok || payload?.success === false) {
        throw new CommentApiError(
          payload?.message || `请求失败（${response.status}）：${url}`,
          response.status,
          Boolean(payload?.needLogin),
        );
      }
      return payload as T;
    });
}

function request<T>(
  url: string,
  init: RequestInit = {},
  withToken = false,
): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body) headers['Content-Type'] = 'application/json';
  if (withToken) headers['X-Review-Token'] = getReviewToken();
  return fetch(url, {
    credentials: 'same-origin',
    ...init,
    headers,
  }).then(response => readJson<T>(response, url));
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  });
  const suffix = search.toString();
  return suffix ? `?${suffix}` : '';
}

/** 限额与举报原因枚举：与后端同源，避免两边各写一份口径。 */
export function fetchCommentMeta(): Promise<CommentMeta> {
  return request<CommentMeta>('/api/poi/comments/meta');
}

/**
 * 坐标反查点位 id 的进程内缓存。
 *
 * 问答地图的结果点（聚类点 / 学院 / 附近植物）本身不带 POI id，需要回落到真实点位，
 * 才能和植物地图共用同一块留言板。点位库在一次运行内是静态的，因此结果可长期缓存；
 * 同一坐标只会查一次，避免每次点击打点都多一个来回。
 */
const poiRefCache = new Map<string, PoiRef | null>();

export function resolvePoiRef(
  lng: number,
  lat: number,
  name = '',
): Promise<PoiRef | null> {
  const key = `${lng.toFixed(6)},${lat.toFixed(6)}`;
  if (poiRefCache.has(key)) return Promise.resolve(poiRefCache.get(key) ?? null);
  return request<{ poi: PoiRef | null }>(`/api/poi/resolve${query({ lng, lat, name })}`)
    .then(payload => {
      poiRefCache.set(key, payload.poi ?? null);
      return payload.poi ?? null;
    })
    .catch(() => {
      // 反查失败不缓存：网络抖动不该让这个点永久失去留言板
      return null;
    });
}

/** 弹窗预览：1 条长评，或无长评时 2 条短评。 */
export function fetchCommentPreview(poiId: string): Promise<CommentPreview> {
  return request<CommentPreview>(`/api/poi/comments/preview${query({ poiId })}`);
}

export function fetchComments(params: {
  poiId: string;
  sort?: CommentSort;
  page?: number;
  size?: number;
}): Promise<CommentListResult> {
  return request<CommentListResult>(`/api/poi/comments${query(params)}`);
}

export function createComment(payload: {
  poiId: string;
  content: string;
  poiName?: string;
  poiCategory?: string;
}): Promise<{ comment: PoiComment }> {
  return request<{ comment: PoiComment }>('/api/poi/comments', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function deleteComment(id: number): Promise<void> {
  return request<void>(`/api/poi/comments/${id}`, { method: 'DELETE' });
}

/** 点赞 / 爱心：两者独立，重复点击同一项即取消。 */
export function reactToComment(
  id: number,
  type: CommentReaction,
  action: ReactionAction,
): Promise<ReactionResult> {
  return request<ReactionResult>(`/api/poi/comments/${id}/reaction`, {
    method: 'POST',
    body: JSON.stringify({ type, action }),
  });
}

export function reportComment(
  id: number,
  reasonCode: string,
  reasonText = '',
): Promise<ReportResult> {
  return request<ReportResult>(`/api/poi/comments/${id}/report`, {
    method: 'POST',
    body: JSON.stringify({ reasonCode, reasonText }),
  });
}

/** 管理端列表（需 X-Review-Token） */
export function fetchManagedComments(status: ManageFilter = 'reported'): Promise<ManageResult> {
  return request<ManageResult>(`/api/poi/comments/manage${query({ status })}`, {}, true);
}

/** 管理端处置：恢复 / 删除 / 封禁作者（需 X-Review-Token） */
export function moderateComment(
  id: number,
  action: ModerationAction,
  note = '',
): Promise<{ message: string }> {
  return request<{ message: string }>(`/api/poi/comments/${id}/moderate`, {
    method: 'POST',
    body: JSON.stringify({ action, note }),
  }, true);
}
