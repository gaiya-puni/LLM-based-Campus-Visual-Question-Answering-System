/**
 * 候选地点的统计口径（纯函数，不依赖 Vue / ECharts / 网络）。
 *
 * 数据来源都是 `GET /api/campus/build/:id/review` 已返回的内容：
 * - 质量分项：`quality.scores`（旧任务回落 `scores`）
 * - 类别：`subCategory`（`category` 只有 plant / scene 两类，做图没有信息量）
 * - 距离：`evidence.distanceMeters`（仅高德采集链路写入，其余来源缺失）
 *
 * 这些函数只描述"本次采集是什么样"，不表达场景适宜度，也不参与任何排序。
 */

export interface CandidateLike {
  category?: string;
  subCategory?: string;
  confidence?: number;
  quality?: {
    scores?: Record<string, number>;
    confidence?: number;
  };
  /** 评分 MVP 期的旧字段，与 `quality.scores` 同义。 */
  scores?: Record<string, number>;
  evidence?: Record<string, unknown>;
}

export interface QualityDimension {
  key: string;
  label: string;
  /** 风险项：数值越低越好，图上单独配色。 */
  risk: boolean;
}

/**
 * 顺序与 `tools/campus_generator/quality.py` 的 `scores` 字典一致，
 * 中文标签与抽屉里的单候选「质量评分」保持同一口径，避免两处叫法不同。
 */
export const QUALITY_DIMENSIONS: ReadonlyArray<QualityDimension> = [
  { key: 'campusEvidence', label: '校园证据', risk: false },
  { key: 'boundaryEvidence', label: '边界证据', risk: false },
  { key: 'themeRelevance', label: '主题相关', risk: false },
  { key: 'sourceReliability', label: '来源可靠性', risk: false },
  { key: 'crossSourceAgreement', label: '跨来源一致性', risk: false },
  { key: 'commercialRisk', label: '商业场所风险', risk: true },
  { key: 'duplicateRisk', label: '重复候选风险', risk: true },
];

/** 没有抽取到具体类型的候选统一归到这里，避免图表出现大量单例扇区。 */
export const UNCATEGORIZED_LABEL = '未分类';
const UNCATEGORIZED_SOURCES = new Set([
  '', '地点', '待抽取地点', '网页地点', '未知', 'unknown', 'place',
]);

export interface QualitySummaryItem extends QualityDimension {
  value: number;
  /** 参与均值计算的候选数（该分项缺失的候选不计入）。 */
  count: number;
}

export interface QualitySummary {
  items: QualitySummaryItem[];
  /** 完全没有分项评分的候选数（旧任务）。 */
  missing: number;
  /** 无分项、但有总置信度的候选均值；没有这类候选时为 null。 */
  legacyConfidence: { value: number; count: number } | null;
}

const finiteNumber = (value: unknown): number | null => {
  const num = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(num) ? num : null;
};

/** 逐键读取，空对象 `{}` 不能挡住旧字段（`a || b` 在空对象上会误判）。 */
const scoreOf = (candidate: CandidateLike, key: string): number | null => {
  const fromQuality = finiteNumber(candidate.quality?.scores?.[key]);
  if (fromQuality !== null) return fromQuality;
  return finiteNumber(candidate.scores?.[key]);
};

/**
 * 7 个质量分项的批均值。只对真实存在的分项求均值并回传样本数，
 * 缺失分项不会被当成 0 —— 否则会把"没评分"误画成"评分为零"。
 */
export function summarizeQualityScores(candidates: CandidateLike[]): QualitySummary {
  const sums = new Map<string, { sum: number; count: number }>();
  for (const dimension of QUALITY_DIMENSIONS) {
    sums.set(dimension.key, { sum: 0, count: 0 });
  }
  let missing = 0;
  let legacySum = 0;
  let legacyCount = 0;

  for (const candidate of candidates) {
    let scored = 0;
    for (const dimension of QUALITY_DIMENSIONS) {
      const value = scoreOf(candidate, dimension.key);
      if (value === null) continue;
      const bucket = sums.get(dimension.key)!;
      bucket.sum += value;
      bucket.count += 1;
      scored += 1;
    }
    if (scored > 0) continue;
    missing += 1;
    const confidence = finiteNumber(candidate.quality?.confidence) ?? finiteNumber(candidate.confidence);
    if (confidence !== null) {
      legacySum += confidence;
      legacyCount += 1;
    }
  }

  const items = QUALITY_DIMENSIONS.map((dimension) => {
    const bucket = sums.get(dimension.key)!;
    return {
      ...dimension,
      value: bucket.count ? bucket.sum / bucket.count : 0,
      count: bucket.count,
    };
  });

  return {
    items,
    missing,
    legacyConfidence: legacyCount ? { value: legacySum / legacyCount, count: legacyCount } : null,
  };
}

export interface CategorySummaryItem {
  name: string;
  value: number;
}

export interface CategorySummary {
  items: CategorySummaryItem[];
  total: number;
}

const normalizeSubCategory = (candidate: CandidateLike): string => {
  const raw = typeof candidate.subCategory === 'string' ? candidate.subCategory.trim() : '';
  if (UNCATEGORIZED_SOURCES.has(raw.toLowerCase()) || UNCATEGORIZED_SOURCES.has(raw)) {
    return UNCATEGORIZED_LABEL;
  }
  return raw;
};

/**
 * 按 `subCategory` 统计数量，降序取前 `limit` 类，其余合并为"其他"。
 * 数量相同的按名称排序，保证多次渲染顺序稳定（否则每次刷新颜色都会串）。
 */
export function summarizeSubCategories(
  candidates: CandidateLike[],
  limit = 8,
): CategorySummary {
  const counts = new Map<string, number>();
  for (const candidate of candidates) {
    const name = normalizeSubCategory(candidate);
    counts.set(name, (counts.get(name) || 0) + 1);
  }

  const sorted = [...counts.entries()]
    .map(([name, value]) => ({ name, value }))
    .sort((left, right) => right.value - left.value || left.name.localeCompare(right.name, 'zh-Hans-CN'));

  const safeLimit = Math.max(1, Math.floor(limit));
  if (sorted.length <= safeLimit) {
    return { items: sorted, total: candidates.length };
  }

  const head = sorted.slice(0, safeLimit);
  const rest = sorted.slice(safeLimit);
  const restTotal = rest.reduce((sum, item) => sum + item.value, 0);
  return {
    items: [...head, { name: '其他', value: restTotal }],
    total: candidates.length,
  };
}

export interface DistanceBin {
  label: string;
  count: number;
  start: number;
  end: number;
}

export interface DistanceSummary {
  bins: DistanceBin[];
  /** 没有距离字段的候选数（非高德来源），必须显式展示，不能静默丢弃。 */
  unknown: number;
  /** 有效距离与搜索半径中的较大者，用于确定坐标轴范围。 */
  maxMeters: number;
  /** 半径缺失或非法时为 null，此时不画参考线。 */
  radiusMeters: number | null;
}

const distanceOf = (candidate: CandidateLike): number | null => {
  const value = finiteNumber(candidate.evidence?.distanceMeters);
  if (value === null || value < 0) return null;
  return value;
};

/**
 * 距中心距离直方图数据。箱宽由"实际最远点"与"搜索半径"的较大者决定：
 * 半径大于实际范围时，右端空白本身就是"没采到"的证据，不该被裁掉。
 */
export function summarizeDistances(
  candidates: CandidateLike[],
  radiusMeters?: number | null,
  binCount = 8,
): DistanceSummary {
  const radius = finiteNumber(radiusMeters);
  const validRadius = radius !== null && radius > 0 ? radius : null;
  const distances: number[] = [];
  for (const candidate of candidates) {
    const value = distanceOf(candidate);
    if (value !== null) distances.push(value);
  }
  const unknown = candidates.length - distances.length;

  if (!distances.length) {
    return {
      bins: [],
      unknown,
      maxMeters: validRadius ?? 0,
      radiusMeters: validRadius,
    };
  }

  const actualMax = Math.max(...distances);
  const maxMeters = Math.max(actualMax, validRadius ?? 0);
  const count = Math.max(1, Math.floor(binCount));
  const width = maxMeters > 0 ? maxMeters / count : 1;

  const bins: DistanceBin[] = Array.from({ length: count }, (_, index) => {
    const start = width * index;
    const end = index === count - 1 ? maxMeters : width * (index + 1);
    return { label: `${Math.round(start)}–${Math.round(end)}m`, count: 0, start, end };
  });

  for (const distance of distances) {
    const index = Math.min(count - 1, Math.max(0, Math.floor(distance / width)));
    bins[index].count += 1;
  }

  return { bins, unknown, maxMeters, radiusMeters: validRadius };
}

/** 半径来源文案：默认值最弱，必须与"请求里显式写了"区分开。 */
export function radiusSourceLabel(source?: string | null): string {
  if (source === 'campus_config') return '已登记校区配置';
  if (source === 'explicit') return '请求中显式指定';
  if (source === 'amap_geocode' || source === 'amap_poi') return '由地理编码/POI 校正';
  return '系统默认值';
}
