import { getReviewToken } from './userdata';

export type BuildStatus = 'queued' | 'running' | 'completed' | 'blocked' | 'failed';
export type ReviewStatus = 'pending' | 'approved' | 'rejected';

export interface WebSearchAudit {
  schemaVersion: number;
  requested: boolean;
  provider?: 'tavily' | 'brave' | string | null;
  status: 'not_requested' | 'unconfigured' | 'completed' | 'empty' | 'failed';
  queries: string[];
  results: Array<{ url: string; title?: string; query?: string; rank?: number }>;
  acceptedUrlCount: number;
}

export interface CampusDiscoveryDetails {
  method?: string;
  query?: string;
  cleanedQuery?: string;
  address?: string;
  resolvedSchool?: string;
  resolvedCampus?: string;
  /** 中心来源：amap_poi / amap_geocode / local_config */
  centerSource?: string;
  center?: [number, number];
  centerPoiName?: string;
  radiusMeters?: number;
  radiusSource?: string;
  warning?: string;
}

export interface CampusProfileSummary {
  school?: { name?: string };
  campus?: { name?: string; center?: [number, number]; slug?: string; trustRadiusM?: number };
  discovery?: CampusDiscoveryDetails;
}

export interface CandidatePoi {
  id: string;
  name: string;
  locationName: string;
  lng: number;
  lat: number;
  campus: string;
  category: string;
  subCategory: string;
  source?: string;
  sourceUrls?: string[];
  coordinateSystem?: 'GCJ-02' | 'WGS84' | 'BD-09';
  confidence: number;
  quality?: {
    scores?: Record<string, number>;
    confidence?: number;
    confidenceReasons?: string[];
  };
  // Compatibility with candidates produced during the scoring MVP rollout.
  scores?: Record<string, number>;
  confidenceReasons?: string[];
  sourceConfidence?: number;
  text?: string;
  /** 采集证据；`distanceMeters` 仅高德链路写入，页面据此显示"距中心"。 */
  evidence?: Record<string, unknown>;
  reviewStatus: ReviewStatus;
  selectedForPreview?: boolean;
  /** 用户在地图上手工补的点：只在本次任务内有效，不会进入正式数据。 */
  userAdded?: boolean;
}

export interface UserPoiPayload {
  name: string;
  lng: number;
  lat: number;
  category?: 'plant' | 'scene';
  subCategory?: string;
  locationName?: string;
  scenes?: string[];
  text?: string;
}

export interface BuildJob {
  success: boolean;
  id: string;
  status: BuildStatus;
  stage?: string;
  query?: string;
  theme?: string;
  targetCategory?: string;
  keywords?: string[];
  sources?: string[];
  candidateCount?: number;
  normalizedCount?: number;
  pendingCount?: number;
  approvedCount?: number;
  pendingInvalid?: number;
  warnings?: string[];
  discoveryWarnings?: string[];
  discovery?: CampusDiscoveryDetails;
  webSearch?: WebSearchAudit;
  error?: string | null;
  previewStatus?: string;
  previewError?: string | null;
}

export interface ReviewBundle {
  success: boolean;
  jobId: string;
  school: string;
  campus: string;
  profile?: CampusProfileSummary;
  candidates: CandidatePoi[];
  /** 本任务内用户手工补的点（候选列表里同时出现，带 userAdded 标记）。 */
  userAdded?: CandidatePoi[];
  pendingInvalid: Array<{ candidate?: Record<string, unknown>; reason?: string }>;
  approved: CandidatePoi[];
  selection?: { selectedIds: string[]; updatedAt?: string | null };
  validationProblems?: string[];
}

export interface PreviewGrid {
  width: number;
  height: number;
  bounds: [number, number, number, number];
  rowOrder: string;
  values: number[];
  maxValue: number;
  noData: number;
}

export interface PreviewPlace {
  name?: string;
  lng?: number;
  lat?: number;
  score?: number;
  rank?: number;
  category?: string;
}

export interface PreviewResponse {
  success: boolean;
  jobId: string;
  preview: {
    grid?: PreviewGrid;
    places?: PreviewPlace[];
    scene?: string;
    sceneName?: string;
    [key: string]: unknown;
  };
}

export interface PublishPlan {
  campus: string;
  approvedCount: number;
  added: CandidatePoi[];
  updated: Array<{ id: string; before: CandidatePoi; after: CandidatePoi }>;
  plantAdded: CandidatePoi[];
  plantUpdated: Array<{ id: string; before: CandidatePoi; after: CandidatePoi }>;
  sceneAdded: CandidatePoi[];
  sceneUpdated: Array<{ id: string; before: CandidatePoi; after: CandidatePoi }>;
  unchangedCount: number;
  validationProblems: string[];
  beforeHashes: { plants: string; scenes: string };
  afterHashes: { plants: string; scenes: string };
  campusPolicyHash: string;
  beforeHash: string;
  afterHash: string;
  requiresRebuild: boolean;
}

async function readJson<T>(response: Response, url: string): Promise<T> {
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.success === false) {
    throw new Error(payload?.message || `请求失败（${response.status}）：${url}`);
  }
  return payload as T;
}

export function submitCampusDiscovery(payload: {
  query: string;
  webUrls?: string[];
  keywords?: string[];
  includeAmap?: boolean;
  includeWeb?: boolean;
  autoSearch?: boolean;
}): Promise<{
  success: boolean;
  jobId: string;
  profile: CampusProfileSummary;
  theme: string;
  keywords: string[];
  sources: string[];
  warnings: string[];
  webSearch: WebSearchAudit;
}> {
  return fetch('/api/campus/discover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(response => readJson(response, '/api/campus/discover'));
}

export function getCampusBuild(jobId: string): Promise<BuildJob> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}`)
    .then(response => readJson<BuildJob>(response, '/api/campus/build'));
}

export function getCampusReview(jobId: string): Promise<ReviewBundle> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/review`)
    .then(response => readJson<ReviewBundle>(response, '/api/campus/review'));
}

export function submitCampusReview(jobId: string, decisions: Array<{
  id: string;
  action: 'approve' | 'reject' | 'pending';
  patch?: Partial<CandidatePoi>;
}>): Promise<ReviewBundle> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Review-Token': getReviewToken(),
    },
    body: JSON.stringify({ decisions }),
  }).then(response => readJson<ReviewBundle>(response, '/api/campus/review'));
}

export function submitCampusPreview(jobId: string): Promise<BuildJob> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/preview`, {
    method: 'POST',
  }).then(response => readJson<BuildJob>(response, '/api/campus/preview'));
}

export function submitCampusSelection(jobId: string, selectedIds: string[]): Promise<{
  success: boolean; jobId: string; selection: { selectedIds: string[]; updatedAt?: string | null };
}> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selectedIds }),
  }).then(response => readJson(response, '/api/campus/selection'));
}

export function getCampusPreview(jobId: string, scene: string, season?: string): Promise<PreviewResponse> {
  const query = season ? `?season=${encodeURIComponent(season)}` : '';
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/preview/${scene}${query}`)
    .then(response => readJson<PreviewResponse>(response, '/api/campus/preview'));
}

export function addCampusUserPoi(jobId: string, payload: UserPoiPayload): Promise<{
  success: boolean;
  jobId: string;
  item: CandidatePoi;
  count: number;
  maxUserPois: number;
  selection?: { selectedIds: string[]; updatedAt?: string | null };
}> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/user-pois`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(response => readJson(response, '/api/campus/user-pois'));
}

export function removeCampusUserPoi(jobId: string, poiId: string): Promise<{
  success: boolean;
  jobId: string;
  removedId: string;
  count: number;
}> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/user-pois/${encodeURIComponent(poiId)}`, {
    method: 'DELETE',
  }).then(response => readJson(response, '/api/campus/user-pois'));
}

export function getCampusPublishPlan(jobId: string): Promise<{ success: boolean; jobId: string; plan: PublishPlan }> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/publish-plan`)
    .then(response => readJson<{ success: boolean; jobId: string; plan: PublishPlan }>(response, '/api/campus/publish-plan'));
}

export function publishCampusBuild(jobId: string, expectedAfterHash: string, reviewToken: string): Promise<{ success: boolean; jobId: string; published: boolean; backup?: string; message?: string; reason?: string }> {
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Review-Token': reviewToken },
    body: JSON.stringify({ confirm: true, expectedAfterHash }),
  }).then(response => readJson(response, '/api/campus/publish'));
}
