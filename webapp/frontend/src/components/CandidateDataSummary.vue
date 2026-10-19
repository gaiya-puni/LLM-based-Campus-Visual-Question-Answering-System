<script setup lang="ts">
import { computed, ref } from 'vue';
import type { EChartsOption } from 'echarts';
import Echarts from './Echarts.vue';
import { useIsMobile } from '../composables/useIsMobile';
import {
  UNCATEGORIZED_LABEL,
  radiusSourceLabel,
  summarizeDistances,
  summarizeQualityScores,
  summarizeSubCategories,
  type CandidateLike,
} from './candidateSummary';

/**
 * 本次采集的数据概况：质量分项均值、细分类别构成、距中心距离分布。
 *
 * 只读展示，不参与勾选/审核/生成热图。三张图都直接从 review 响应在前端算，
 * 不新增接口，也不改变任何热图缓存或算法版本。
 */

interface DiscoveryLike {
  radiusMeters?: number;
  radiusSource?: string;
  centerSource?: string;
}

const props = withDefaults(defineProps<{
  candidates: CandidateLike[];
  discovery?: DiscoveryLike | null;
}>(), {
  discovery: null,
});

const isMobile = useIsMobile();
const chartHeight = computed(() => (isMobile.value ? '170px' : '200px'));
const activeTab = ref<'quality' | 'category' | 'distance'>('quality');
const expanded = ref<string[]>(['summary']);

const candidateCount = computed(() => props.candidates.length);

const quality = computed(() => summarizeQualityScores(props.candidates));
const scoredDimensions = computed(() => quality.value.items.filter(item => item.count > 0));
/** 旧任务没有分项，只有总置信度：降级成单条，不画一张全零的图。 */
const qualityFallback = computed(() => (
  !scoredDimensions.value.length ? quality.value.legacyConfidence : null
));
const qualitySampleCount = computed(() => Math.max(0, ...quality.value.items.map(item => item.count)));

const categories = computed(() => summarizeSubCategories(props.candidates));
const uncategorizedCount = computed(() => (
  categories.value.items.find(item => item.name === UNCATEGORIZED_LABEL)?.value || 0
));

const radiusMeters = computed(() => {
  const value = Number(props.discovery?.radiusMeters);
  return Number.isFinite(value) && value > 0 ? value : null;
});
const distance = computed(() => summarizeDistances(props.candidates, radiusMeters.value));

const qualityOption = computed<EChartsOption>(() => {
  const rows = qualityFallback.value
    ? [{ key: 'legacyConfidence', label: '总体置信度', value: qualityFallback.value.value, risk: false }]
    : scoredDimensions.value;
  return {
    animation: false,
    grid: { left: isMobile.value ? 88 : 100, right: 30, top: 6, bottom: 18 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: { fontSize: 10, color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f2f5' } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: rows.map(row => row.label),
      axisLabel: { fontSize: 11, color: '#606266' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#ebeef5' } },
    },
    series: [{
      type: 'bar',
      barMaxWidth: 13,
      label: { show: true, position: 'right', fontSize: 10, color: '#606266', formatter: '{c}' },
      data: rows.map(row => ({
        value: Number(row.value.toFixed(2)),
        itemStyle: { color: row.risk ? '#e6a23c' : '#409eff', borderRadius: [0, 3, 3, 0] },
      })),
    }],
  } as unknown as EChartsOption;
});

const categoryOption = computed<EChartsOption>(() => {
  const items = categories.value.items;
  return {
    animation: false,
    tooltip: { trigger: 'item', formatter: '{b}：{c} 个（{d}%）' },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      right: 4,
      top: 'middle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 5,
      textStyle: { fontSize: 11, color: '#606266' },
    },
    series: [{
      type: 'pie',
      radius: ['46%', '70%'],
      center: ['27%', '52%'],
      avoidLabelOverlap: true,
      label: { show: false },
      labelLine: { show: false },
      data: items.map(item => ({ name: item.name, value: item.value })),
    }],
  } as unknown as EChartsOption;
});

const distanceOption = computed<EChartsOption>(() => {
  const summary = distance.value;
  const bins = summary.bins;
  const labels = bins.map(bin => bin.label);
  const markLine = (() => {
    if (!summary.radiusMeters || !bins.length) return undefined;
    const width = bins[0].end - bins[0].start;
    const index = Math.min(bins.length - 1, Math.max(0, Math.floor(summary.radiusMeters / (width || 1))));
    return {
      symbol: 'none',
      silent: true,
      lineStyle: { color: '#e6a23c', type: 'dashed', width: 1 },
      label: {
        formatter: `半径 ${Math.round(summary.radiusMeters)}m`,
        fontSize: 10,
        color: '#b88230',
        position: 'end',
        rotate: 0,
      },
      data: [{ xAxis: labels[index] }],
    };
  })();

  return {
    animation: false,
    grid: { left: 40, right: 14, top: 22, bottom: 32 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: {
        fontSize: 10,
        color: '#909399',
        interval: 0,
        rotate: bins.length > 6 ? 34 : 0,
        formatter: (value: string, index: number) => (
          index === 0 ? '0' : String(Math.round(bins[index]?.start ?? 0))
        ),
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#ebeef5' } },
      name: 'm',
      nameTextStyle: { fontSize: 10, color: '#909399', padding: [0, 0, 0, 26] },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { fontSize: 10, color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f2f5' } },
    },
    series: [{
      type: 'bar',
      barMaxWidth: 26,
      itemStyle: { color: '#409eff', borderRadius: [3, 3, 0, 0] },
      data: bins.map(bin => bin.count),
      markLine,
    }],
  } as unknown as EChartsOption;
});

const categoryNote = computed(() => {
  const parts = [`按细分类别统计，共 ${categories.value.total} 个候选`];
  if (uncategorizedCount.value) parts.push(`其中未分类 ${uncategorizedCount.value} 个`);
  parts.push('标注只表示地点类型，不代表场景适宜度');
  return parts.join('；');
});

const distanceNote = computed(() => {
  const summary = distance.value;
  const parts: string[] = [];
  if (summary.radiusMeters) {
    parts.push(`搜索半径 ${Math.round(summary.radiusMeters)}m（${radiusSourceLabel(props.discovery?.radiusSource)}）`);
  } else {
    parts.push('本任务未返回搜索半径，未画参考线');
  }
  if (summary.unknown) parts.push(`距离未知 ${summary.unknown} 个未计入分布`);
  parts.push('圆形半径只是搜索范围近似，不是校界，也不代表步行可达');
  return parts.join('；');
});

const qualityNote = computed(() => {
  if (qualityFallback.value) {
    return `本任务未返回分项评分，仅展示 ${qualityFallback.value.count} 个候选的总体置信度均值；质量分与热力强度无关`;
  }
  const parts = [`${qualitySampleCount.value} 个候选参与均值`];
  if (quality.value.missing) parts.push(`${quality.value.missing} 个无分项评分未计入`);
  parts.push('风险项越低越好；质量分与热力强度无关');
  return parts.join('；');
});
</script>

<template>
  <section v-if="candidateCount" class="data-summary">
    <el-collapse v-model="expanded">
      <el-collapse-item name="summary">
        <template #title>
          <div class="summary-title">
            <strong>数据概况</strong>
            <span>{{ candidateCount }} 个候选 · 含已拒绝</span>
          </div>
        </template>
        <!-- 用 radio 切换而非 el-tabs：tabs 用 v-show 隐藏，ECharts 会在 0 宽容器里初始化成 100×100。 -->
        <el-radio-group v-model="activeTab" size="small" class="summary-switch">
          <el-radio-button value="quality" label="质量分项" />
          <el-radio-button value="category" label="类别构成" />
          <el-radio-button value="distance" label="距离分布" />
        </el-radio-group>

        <template v-if="activeTab === 'quality'">
          <div v-if="scoredDimensions.length || qualityFallback" class="summary-chart" :style="{ height: chartHeight }">
            <Echarts :option="qualityOption" />
          </div>
          <div v-else class="summary-empty">本任务没有可用的分项评分，也没有总体置信度。</div>
          <p class="summary-note">{{ qualityNote }}</p>
        </template>

        <template v-if="activeTab === 'category'">
          <div v-if="categories.items.length" class="summary-chart" :style="{ height: chartHeight }">
            <Echarts :option="categoryOption" />
          </div>
          <div v-else class="summary-empty">候选没有可识别的类别标注。</div>
          <p class="summary-note">{{ categoryNote }}</p>
        </template>

        <template v-if="activeTab === 'distance'">
          <div v-if="distance.bins.length" class="summary-chart" :style="{ height: chartHeight }">
            <Echarts :option="distanceOption" />
          </div>
          <div v-else class="summary-empty">本任务未返回距离信息，无法绘制距离分布。</div>
          <p class="summary-note">{{ distanceNote }}</p>
        </template>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.data-summary { margin: 10px 0 8px; border: 1px solid #ebeef5; border-radius: 9px; background: #fafcff; overflow: hidden; }
.data-summary :deep(.el-collapse) { border: 0; }
.data-summary :deep(.el-collapse-item__header) { padding: 0 10px; height: 38px; border-bottom: 0; background: transparent; }
.data-summary :deep(.el-collapse-item__wrap) { border-bottom: 0; background: transparent; }
.data-summary :deep(.el-collapse-item__content) { padding: 0 10px 10px; }
.summary-title { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.summary-title strong { color: #303133; font-size: 13px; }
.summary-title span { color: #909399; font-size: 11px; }
.summary-switch { display: flex; margin-bottom: 6px; }
.summary-switch :deep(.el-radio-button__inner) { padding: 5px 9px; font-size: 12px; }
/* 320px 窄屏下三个中文标签仍需压缩，否则候选面板会出现 6px 横向滚动。 */
@media (max-width: 400px) {
  .summary-switch :deep(.el-radio-button__inner) { padding: 5px 6px; font-size: 11px; }
}
.summary-chart { width: 100%; min-width: 0; }
.summary-note { margin: 2px 0 0; color: #909399; font-size: 11px; line-height: 1.45; }
.summary-empty { padding: 26px 6px; color: #909399; font-size: 12px; text-align: center; }
</style>
