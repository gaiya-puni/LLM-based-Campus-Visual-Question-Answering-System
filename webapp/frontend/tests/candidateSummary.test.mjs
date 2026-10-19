import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/components/candidateSummary.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
});
const {
  QUALITY_DIMENSIONS,
  UNCATEGORIZED_LABEL,
  radiusSourceLabel,
  summarizeDistances,
  summarizeQualityScores,
  summarizeSubCategories,
} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('quality summary is empty for no candidates and never fabricates rows', () => {
  const summary = summarizeQualityScores([]);
  assert.equal(summary.items.length, QUALITY_DIMENSIONS.length);
  assert.ok(summary.items.every(item => item.count === 0 && item.value === 0));
  assert.equal(summary.missing, 0);
  assert.equal(summary.legacyConfidence, null);
});

test('quality means only average the dimensions a candidate actually scored', () => {
  const summary = summarizeQualityScores([
    { quality: { scores: { campusEvidence: 1, themeRelevance: 0.5 } } },
    { quality: { scores: { campusEvidence: 0.5 } } },
  ]);
  const campus = summary.items.find(item => item.key === 'campusEvidence');
  const theme = summary.items.find(item => item.key === 'themeRelevance');
  assert.equal(campus.value, 0.75);
  assert.equal(campus.count, 2);
  assert.equal(theme.value, 0.5);
  assert.equal(theme.count, 1);
  // 缺失分项按"没有数据"处理，不能被当成 0 拉低均值。
  assert.equal(summary.items.find(item => item.key === 'boundaryEvidence').count, 0);
  assert.equal(summary.missing, 0);
});

test('legacy candidates fall back to scores and to overall confidence', () => {
  const fromScores = summarizeQualityScores([
    // quality.scores 为空对象时不能挡住旧字段。
    { quality: { scores: {} }, scores: { themeRelevance: 0.8 } },
  ]);
  assert.equal(fromScores.items.find(item => item.key === 'themeRelevance').value, 0.8);
  assert.equal(fromScores.missing, 0);

  const legacy = summarizeQualityScores([
    { confidence: 0.6 },
    { quality: { confidence: 0.8 } },
  ]);
  assert.ok(legacy.items.every(item => item.count === 0));
  assert.equal(legacy.missing, 2);
  assert.equal(legacy.legacyConfidence.count, 2);
  assert.equal(legacy.legacyConfidence.value, 0.7);
});

test('sub-category summary folds placeholders into 未分类 and keeps order stable', () => {
  const summary = summarizeSubCategories([
    { subCategory: '水杉' }, { subCategory: '水杉' },
    { subCategory: '地点' }, { subCategory: '  ' }, { subCategory: '待抽取地点' },
    { subCategory: '樱花' }, { subCategory: '香樟' },
  ]);
  assert.equal(summary.total, 7);
  const water = summary.items.find(item => item.name === '水杉');
  assert.equal(water.value, 2);
  assert.equal(summary.items.find(item => item.name === UNCATEGORIZED_LABEL).value, 3);
  // 同数量按名称排序：香樟 / 樱花 的中文排序结果必须可重复。
  const singles = summary.items.filter(item => item.value === 1).map(item => item.name);
  assert.deepEqual(singles, [...singles].sort((left, right) => left.localeCompare(right, 'zh-Hans-CN')));
});

test('sub-category summary keeps the top eight and merges the rest into 其他', () => {
  const candidates = [];
  for (let index = 0; index < 10; index++) {
    const repeat = 10 - index;
    for (let count = 0; count < repeat; count++) {
      candidates.push({ subCategory: `类别${index}` });
    }
  }
  const summary = summarizeSubCategories(candidates, 8);
  assert.equal(summary.items.length, 9);
  assert.equal(summary.items.at(-1).name, '其他');
  assert.equal(summary.items.at(-1).value, 3); // 类别8 + 类别9 + 类别10 → 2 + 1 … 由 repeat 决定
  assert.equal(summary.items.reduce((sum, item) => sum + item.value, 0), candidates.length);
});

test('distance bins count every point, including one exactly on the upper edge', () => {
  const summary = summarizeDistances([
    { evidence: { distanceMeters: 0 } },
    { evidence: { distanceMeters: 120 } },
    { evidence: { distanceMeters: 1199 } },
    { evidence: { distanceMeters: 1200 } },
  ], 1200, 8);
  assert.equal(summary.bins.length, 8);
  assert.equal(summary.unknown, 0);
  assert.equal(summary.radiusMeters, 1200);
  assert.equal(summary.maxMeters, 1200);
  assert.equal(summary.bins[0].count, 2);
  // 落在最大边界上的点必须并入最后一箱（1199 + 1200），不能越界丢弃。
  assert.equal(summary.bins.at(-1).count, 2);
  assert.equal(summary.bins.reduce((sum, bin) => sum + bin.count, 0), 4);
});

test('distance summary reports missing distances instead of dropping them silently', () => {
  const summary = summarizeDistances([
    { evidence: { distanceMeters: 300 } },
    {},
    { evidence: {} },
    { evidence: { distanceMeters: 'unknown' } },
  ], null);
  assert.equal(summary.unknown, 3);
  assert.equal(summary.radiusMeters, null);
  assert.equal(summary.maxMeters, 300);
  assert.equal(summary.bins.reduce((sum, bin) => sum + bin.count, 0), 1);
});

test('distance axis keeps the search radius when every point sits closer', () => {
  const summary = summarizeDistances([
    { evidence: { distanceMeters: 200 } },
    { evidence: { distanceMeters: 400 } },
  ], 1200);
  assert.equal(summary.maxMeters, 1200);
  assert.equal(summary.bins.at(-1).count, 0);
});

test('distance summary stays empty when no candidate carries a distance', () => {
  const summary = summarizeDistances([{}, {}], 1200);
  assert.deepEqual(summary.bins, []);
  assert.equal(summary.unknown, 2);
  assert.equal(summary.radiusMeters, 1200);
});

test('radius source labels distinguish defaults from explicit requests', () => {
  assert.equal(radiusSourceLabel('campus_config'), '已登记校区配置');
  assert.equal(radiusSourceLabel('explicit'), '请求中显式指定');
  assert.equal(radiusSourceLabel(undefined), '系统默认值');
  assert.equal(radiusSourceLabel('something-else'), '系统默认值');
});
