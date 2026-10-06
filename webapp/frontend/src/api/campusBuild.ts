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
  evidence?: Record<string, unknown>;
  reviewStatus: ReviewStatus;
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
  profile?: {
    school?: { name?: string };
    campus?: { name?: string; center?: [number, number]; slug?: string };
  };
  candidates: CandidatePoi[];
  pendingInvalid: Array<{ candidate?: Record<string, unknown>; reason?: string }>;
  approved: CandidatePoi[];
  validationProblems?: string[];
}

export interface PreviewResponse {
  success: boolean;
  jobId: string;
  preview: any;
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
  profile: ReviewBundle['profile'];
  theme: string;
  keywords: string[];
  sources: string[];
  warnings: string[];
  webSearch: WebSearchAudit;
}> {
  return fetch('/api/campus/discover', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Review-Token': getReviewToken(),
    },
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
    headers: { 'X-Review-Token': getReviewToken() },
  }).then(response => readJson<BuildJob>(response, '/api/campus/preview'));
}

export function getCampusPreview(jobId: string, scene: string, season?: string): Promise<PreviewResponse> {
  const query = season ? `?season=${encodeURIComponent(season)}` : '';
  return fetch(`/api/campus/build/${encodeURIComponent(jobId)}/preview/${scene}${query}`)
    .then(response => readJson<PreviewResponse>(response, '/api/campus/preview'));
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
