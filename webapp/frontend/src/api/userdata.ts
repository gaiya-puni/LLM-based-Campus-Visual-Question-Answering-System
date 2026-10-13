/**
 * 用户共建接口封装：消息级反馈、地点上报、待审清单、审核、大模型补齐、导出。
 *
 * 写法与项目既有页面一致：`fetch` + 相对路径 `/api/...`（开发期由 vite 代理到后端）。
 * 审核态接口需要密钥：保存在 localStorage，随请求头 `X-Review-Token` 发送；
 * 后端未配置 `USERDATA_REVIEW_TOKEN` 时一律返回 403。
 */

export const REVIEW_TOKEN_KEY = 'campus.userdata.reviewToken';

export type FeedbackRating = 'up' | 'down' | 'none';
export type CandidateSource = 'rule' | 'llm' | 'user';
export type CandidateStatus = 'pending' | 'approved' | 'rejected';

export interface FeedbackPayload {
  messageId: string;
  rating: FeedbackRating;
  reason?: string;
  query?: string;
  messageEngine?: string;
  campus?: string;
}

export interface PlaceReportPayload {
  name: string;
  lng: number;
  lat: number;
  campus?: string;
  category?: string;
  note?: string;
  querySnippet?: string;
}

export interface PendingItem {
  id: string;
  name: string;
  campus: string;
  lng: number | null;
  lat: number | null;
  category: string | null;
  source: CandidateSource;
  confidence: number;
  mentions: number;
  firstSeenAt: string;
  lastSeenAt: string;
  samples: string[];
  status: CandidateStatus;
  note: string;
}

export interface PendingQuery {
  campus?: string;
  status?: string;
  source?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}

export interface PendingResult {
  success: boolean;
  counts: Record<CandidateStatus, number>;
  stats: {
    counts: Record<CandidateStatus, number>;
    unresolvedPending: number;
    ratingsTotal: number;
    reportsTotal: number;
    generatedAt: string | null;
  };
  extractionMode: string;
  total: number;
  page: number;
  pageSize: number;
  items: PendingItem[];
}

export interface ReviewResult {
  success: boolean;
  counts: Record<CandidateStatus, number>;
}

export interface ExtractResult {
  success: boolean;
  processed: number;
  added: number;
  message?: string;
  counts?: Record<CandidateStatus, number>;
}

export interface ExportResult {
  success: boolean;
  approved: number;
  skipped: number;
  file: string;
  markdown: string;
}

export interface SimpleResult {
  success: boolean;
  message: string;
  campus?: string;
  itemId?: string;
}

/** 上报类型选项（值与后端 `userdata_store.CATEGORY_CHOICES` 对齐） */
export const REPORT_CATEGORIES = [
  { value: 'building', label: '建筑' },
  { value: 'canteen', label: '餐饮' },
  { value: 'parking', label: '停车' },
  { value: 'plant', label: '植物' },
  { value: 'water', label: '水景' },
  { value: 'scene', label: '其他' },
];

export const SOURCE_LABELS: Record<CandidateSource, string> = {
  rule: '规则识别',
  llm: '大模型',
  user: '用户上报',
};

export const STATUS_LABELS: Record<CandidateStatus, string> = {
  pending: '待审',
  approved: '已通过',
  rejected: '已驳回',
};

export function getReviewToken(): string {
  return localStorage.getItem(REVIEW_TOKEN_KEY) || '';
}

export function setReviewToken(token: string): void {
  localStorage.setItem(REVIEW_TOKEN_KEY, token.trim());
}

function readJson<T>(response: Response, url: string): Promise<T> {
  return response.json()
    .catch(() => null)
    .then((payload: any) => {
      if (!response.ok || payload?.success === false) {
        throw new Error(payload?.message || `请求失败（${response.status}）：${url}`);
      }
      return payload as T;
    });
}

function postJson<T>(url: string, body: unknown, withToken = false): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (withToken) headers['X-Review-Token'] = getReviewToken();
  return fetch(url, { method: 'POST', headers, body: JSON.stringify(body) })
    .then(response => readJson<T>(response, url));
}

function getJson<T>(url: string): Promise<T> {
  return fetch(url, { headers: { 'X-Review-Token': getReviewToken() } })
    .then(response => readJson<T>(response, url));
}

/** 消息级评价：👍 / 👎 / 撤回（`none` 表示撤回先前的评价） */
export function submitFeedback(payload: FeedbackPayload): Promise<SimpleResult> {
  return postJson<SimpleResult>('/api/feedback', payload);
}

/** 用户主动上报地点：坐标由后端按校区信任半径校验 */
export function submitPlaceReport(payload: PlaceReportPayload): Promise<SimpleResult> {
  return postJson<SimpleResult>('/api/place_report', payload);
}

/** 待审清单（审核态） */
export function fetchPending(query: PendingQuery = {}): Promise<PendingResult> {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return getJson<PendingResult>(`/api/userdata/pending${suffix}`);
}

/** 审核单条候选（通过 / 驳回 / 退回待审） */
export function reviewCandidate(id: string, status: CandidateStatus, note = ''): Promise<ReviewResult> {
  return postJson<ReviewResult>('/api/userdata/review', { id, status, note }, true);
}

/** 用大模型批量研判"规则判不出来"的问句 */
export function runLlmExtract(limit = 20, campus?: string): Promise<ExtractResult> {
  return postJson<ExtractResult>('/api/userdata/extract', { limit, campus }, true);
}

/** 导出已通过条目（POI 契约 JSON + Markdown 汇总） */
export function exportApproved(): Promise<ExportResult> {
  return getJson<ExportResult>('/api/userdata/export');
}
