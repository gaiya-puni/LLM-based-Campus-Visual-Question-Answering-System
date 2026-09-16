<template>
  <section class="heatmap-panel" aria-label="场景热力图">
    <div class="mode-row">
      <button :disabled="busy" :class="{ selected: mode === 'sakde' }" @click="$emit('update:mode', 'sakde')">场景热图</button>
      <button :disabled="busy" :class="{ selected: mode === 'rule' }" @click="$emit('update:mode', 'rule')">原版推荐</button>
    </div>
    <template v-if="mode === 'sakde'">
      <div class="scene-row">
        <label for="heatmap-scene">场景</label>
        <select id="heatmap-scene" v-model="scene" :disabled="busy" @change="loadSelected">
          <option v-for="item in scenes" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
        <button :disabled="busy" @click="toggle">{{ visible ? '隐藏热图' : '查看热图' }}</button>
      </div>
      <p v-if="loading" role="status">正在读取{{ campus }}校区热图…</p>
      <p v-else-if="error" class="error" role="status">{{ error }}</p>
      <template v-else-if="visible && current">
        <div class="legend-bar"></div>
        <div class="legend-labels"><span>相对较低</span><span>{{ current.sceneName }}</span><span>相对较高</span></div>
        <p v-if="current.seasonLabel" class="season-tag" role="status">{{ current.seasonLabel }}花景参考</p>
        <label class="opacity-label">透明度
          <input aria-label="热图透明度" type="range" min="0.15" max="0.85" step="0.05" v-model.number="opacity" />
        </label>
        <p>仅在同一校区、同一场景内比较；非实时花期、人流或噪声。</p>
        <p>边界为数据覆盖范围，通行情况未核实。点击地图标记查看依据。</p>
      </template>
      <p v-else>四类场景 × 两个校区。选择场景查看，或直接提问。</p>
    </template>
    <p v-else>已使用第一阶段规则推荐。重新提问可对照结果。</p>
  </section>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue';
import type { HeatmapPayload } from './sceneHeatmapTypes';

const props = defineProps<{
  map: any;
  campus: string;
  mode: string;
  response: HeatmapPayload | null;
  resetToken: number;
  busy: boolean;
}>();
const emit = defineEmits<{
  (event: 'update:mode', mode: string): void;
  (event: 'places', places: HeatmapPayload['places']): void;
  (event: 'active', active: boolean): void;
}>();
const scenes = [
  { id: 'flower_viewing', name: '赏花' }, { id: 'photo', name: '拍照' },
  { id: 'walk', name: '散步休息' }, { id: 'date', name: '浪漫约会' },
];
const scene = ref('walk');
const visible = ref(false);
const loading = ref(false);
const error = ref('');
const opacity = ref(0.55);
const current = ref<HeatmapPayload | null>(null);
let layer: any = null;
let layerMap: any = null;
let requestId = 0;
let abort: AbortController | null = null;

const clearLayer = () => {
  if (layer && layerMap) layerMap.remove(layer);
  layer = null;
  layerMap = null;
  emit('active', false);
};
const cancel = () => {
  requestId++;
  abort?.abort();
  abort = null;
  loading.value = false;
};
const reset = () => {
  cancel();
  clearLayer();
  visible.value = false;
  current.value = null;
  error.value = '';
};

// Render the computed raster as an image. Do NOT run the map SDK's KDE again.
const render = () => {
  clearLayer();
  const data = current.value;
  if (!props.map || !data || !visible.value || props.mode !== 'sakde') return;
  const grid = data.grid;
  if (grid.values.length !== grid.width * grid.height || grid.rowOrder !== 'north_to_south') {
    error.value = '热力网格格式不正确';
    return;
  }
  const canvas = document.createElement('canvas');
  canvas.width = grid.width;
  canvas.height = grid.height;
  const context = canvas.getContext('2d');
  if (!context) return;
  const raster = context.createImageData(grid.width, grid.height);
  const colors = [[41, 103, 195], [34, 179, 163], [252, 198, 69], [215, 47, 55]];
  grid.values.forEach((value, index) => {
    if (value === grid.noData) return;
    const t = Math.min(1, Math.max(0, value / grid.maxValue));
    const step = Math.min(2, Math.floor(t * 3));
    const mix = t * 3 - step;
    for (let channel = 0; channel < 3; channel++) {
      raster.data[index * 4 + channel] = Math.round(colors[step][channel] * (1 - mix) + colors[step + 1][channel] * mix);
    }
    raster.data[index * 4 + 3] = Math.round(90 + t * 165);
  });
  context.putImageData(raster, 0, 0);
  const AMap = (window as any).AMap;
  try {
    layer = new AMap.ImageLayer({ url: canvas.toDataURL('image/png'), bounds: grid.bounds,
      zooms: [3, 22], opacity: opacity.value, zIndex: 90 });
    layerMap = props.map;
    layerMap.add(layer);
    emit('active', true);
  } catch {
    clearLayer();
    error.value = '地图热图图层加载失败，请刷新后重试';
  }
};

const loadSelected = async () => {
  cancel();
  clearLayer();
  current.value = null;
  if (props.mode !== 'sakde') return;
  visible.value = true;
  loading.value = true;
  error.value = '';
  emit('places', []);
  const id = requestId;
  abort = new AbortController();
  try {
    const query = new URLSearchParams({ campus: props.campus, scene: scene.value });
    const response = await fetch(`/api/heatmaps?${query}`, { signal: abort.signal });
    const data = await response.json();
    if (id !== requestId) return;
    if (!response.ok || !data.available) throw new Error(data.reason || '热力图不可用');
    current.value = data;
    render();
    emit('places', data.places);
  } catch (err) {
    if (id !== requestId) return;
    error.value = err instanceof Error ? err.message : '热力图请求失败';
    clearLayer();
  } finally {
    if (id === requestId) loading.value = false;
  }
};
const toggle = () => {
  if (visible.value) {
    reset();
  } else {
    loadSelected();
  }
};

watch(() => props.resetToken, reset);
watch(() => props.mode, reset);
watch(() => props.campus, () => {
  const reload = visible.value;
  reset();
  if (reload && props.mode === 'sakde') loadSelected();
});
watch(() => props.response, data => {
  if (!data || props.mode !== 'sakde' || data.campus !== props.campus) return;
  cancel();
  error.value = '';
  current.value = data;
  scene.value = data.scene;
  visible.value = true;
  render();
});
watch(() => props.map, render);
watch(opacity, value => layer?.setOpacity(value));
onBeforeUnmount(reset);
</script>

<style scoped>
.heatmap-panel { position: absolute; left: 12px; top: 62px; z-index: 101; width: 272px;
  box-sizing: border-box; padding: 12px; background: rgba(255,255,255,.96);
  border-radius: 12px; box-shadow: 0 3px 14px #0002; font-size: 12px; color: #4b5563; }
.mode-row { display: flex; gap: 6px; margin-bottom: 10px; }
button, select { font: inherit; border: 1px solid #d9dde3; background: white; border-radius: 6px; padding: 6px 8px; cursor: pointer; color: #374151; }
.mode-row button { flex: 1; }
button.selected { background: #b7081b; color: white; border-color: #b7081b; }
.scene-row { display: flex; align-items: center; gap: 8px; }
.scene-row select { flex: 1; min-width: 0; }
p { margin: 8px 0 0; font-size: 11px; line-height: 1.5; }
.error { color: #af1827; }
.legend-bar { margin-top: 12px; height: 9px; border-radius: 4px; background: linear-gradient(90deg,#2967c3,#22b3a3,#fcc645,#d72f37); }
.legend-labels { display: flex; justify-content: space-between; margin-top: 4px; font-size: 11px; }
.season-tag { display: inline-block; margin-top: 8px; padding: 3px 10px; border-radius: 999px;
  background: #fdeef0; color: #b7081b; font-size: 12px; font-weight: 600; }
.opacity-label { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.opacity-label input { width: 160px; accent-color: #b7081b; }
@media (max-width: 900px) { .heatmap-panel { width: 240px; left: 8px; } }
</style>
