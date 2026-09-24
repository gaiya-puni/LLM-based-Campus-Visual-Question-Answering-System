<template>
  <el-dialog
    v-model="visible"
    width="720px"
    top="8vh"
    class="place-report-dialog"
    :close-on-click-modal="false"
    @opened="initPicker"
  >
    <template #header>
      <div class="dialog-header">
        <h3>补充一个校园地点</h3>
        <p>提交后需人工审核，确认后会出现在地图与推荐里。</p>
      </div>
    </template>

    <div class="dialog-body">
      <div class="form-column">
        <label class="field">
          <span class="field-label">地点名称 <em>*</em></span>
          <input
            v-model="name"
            class="field-input"
            :class="{ invalid: showErrors && !nameValid }"
            type="text"
            maxlength="20"
            placeholder="例如：理科大楼、思源湖、第三食堂"
          />
          <span v-if="showErrors && !nameValid" class="field-error">请输入 2-20 个字的地点名称</span>
        </label>

        <label class="field">
          <span class="field-label">类型</span>
          <el-select v-model="category" size="default" class="field-select">
            <el-option
              v-for="item in REPORT_CATEGORIES"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </label>

        <label class="field">
          <span class="field-label">说明</span>
          <textarea
            v-model="note"
            class="field-textarea"
            rows="3"
            maxlength="200"
            placeholder="补充线索，例如：在图书馆东侧、靠近北门、有蓝色招牌（选填）"
          ></textarea>
          <span class="field-counter">{{ note.length }}/200</span>
        </label>

        <div class="position-summary">
          <span class="field-label">位置 <em>*</em></span>
          <p v-if="point" class="position-value">
            {{ point.lng.toFixed(5) }}, {{ point.lat.toFixed(5) }}
          </p>
          <p v-else class="position-value muted" :class="{ invalid: showErrors }">尚未选取</p>
          <p v-if="point && geo.campus" class="position-campus">所属校区：{{ geo.campus }}</p>
          <p v-else-if="point" class="position-campus outside">该点不在已登记的校区范围内，请重新点选</p>
        </div>

        <p v-if="errorTip" class="submit-error">{{ errorTip }}</p>
      </div>

      <div class="map-column">
        <div ref="pickerEl" class="picker-map"></div>
        <p class="map-hint">点击地图或拖动针尖选择位置</p>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <button type="button" class="ghost-btn" :disabled="submitting" @click="close">取消</button>
        <button
          type="button"
          class="primary-btn"
          :disabled="submitting || !formValid"
          :title="formValid ? '提交给管理员审核' : '请先填写名称并在地图上点选位置'"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '提交' }}
        </button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { loadAMap } from '../amap';
import { CAMPUS_CENTERS, CAMPUS_LOCATION_RADIUS_M, DEFAULT_CAMPUS } from '../campusConfig';
import { REPORT_CATEGORIES, submitPlaceReport } from '../api/userdata';

const props = defineProps<{
  modelValue: boolean;
  /** 界面上当前的校区：地图初始中心跟随它 */
  campus?: string;
  /** 触发上报的那一轮问句（随上报一起存，便于审核还原上下文） */
  querySnippet?: string;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'submitted', payload: { name: string; campus: string }): void;
}>();

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
});

const name = ref('');
const category = ref('building');
const note = ref('');
const point = ref<{ lng: number; lat: number } | null>(null);
const showErrors = ref(false);
const submitting = ref(false);
const errorTip = ref('');
const pickerEl = ref<HTMLElement | null>(null);

let pickerMap: any = null;
let pickerMarker: any = null;

const nameValid = computed(() => name.value.trim().length >= 2);

const distanceMeters = (lng1: number, lat1: number, lng2: number, lat2: number) => {
  const meanLat = ((lat1 + lat2) / 2) * Math.PI / 180;
  const dx = (lng1 - lng2) * 111320 * Math.cos(meanLat);
  const dy = (lat1 - lat2) * 110540;
  return Math.sqrt(dx * dx + dy * dy);
};

/** 前端只做展示提示；最终校区以**后端按坐标判定**的结果为准 */
const geo = computed(() => {
  if (!point.value) return { campus: '', outside: true };
  const ranked = Object.entries(CAMPUS_CENTERS)
    .map(([campus, center]) => ({
      campus,
      distance: distanceMeters(point.value!.lng, point.value!.lat, center[0], center[1]),
    }))
    .sort((a, b) => a.distance - b.distance)[0];
  const radius = CAMPUS_LOCATION_RADIUS_M[ranked.campus] || 1500;
  return ranked.distance <= radius
    ? { campus: ranked.campus, outside: false }
    : { campus: '', outside: true };
});

const formValid = computed(() => nameValid.value && !!point.value && !geo.value.outside);

const initPicker = async () => {
  await loadAMap();
  await nextTick();
  const center = CAMPUS_CENTERS[props.campus || DEFAULT_CAMPUS] || CAMPUS_CENTERS[DEFAULT_CAMPUS];
  if (!pickerMap && pickerEl.value) {
    pickerMap = new (window as any).AMap.Map(pickerEl.value, { zoom: 16, center });
    pickerMap.on('click', (event: any) => setPoint(event.lnglat.getLng(), event.lnglat.getLat()));
  } else if (pickerMap) {
    pickerMap.setCenter(center);
  }
  if (pickerMap) pickerMap.resize();
};

const setPoint = (lng: number, lat: number) => {
  point.value = { lng, lat };
  errorTip.value = '';
  const AMap = (window as any).AMap;
  if (!pickerMarker && pickerMap && AMap) {
    pickerMarker = new AMap.Marker({ position: [lng, lat], map: pickerMap });
  } else if (pickerMarker) {
    pickerMarker.setPosition([lng, lat]);
  }
};

const reset = () => {
  name.value = '';
  category.value = 'building';
  note.value = '';
  point.value = null;
  showErrors.value = false;
  errorTip.value = '';
  if (pickerMarker) {
    pickerMarker.setMap(null);
    pickerMarker = null;
  }
};

const close = () => { visible.value = false; };

const submit = async () => {
  showErrors.value = true;
  if (!formValid.value || !point.value) return;
  submitting.value = true;
  errorTip.value = '';
  try {
    const result = await submitPlaceReport({
      name: name.value.trim(),
      lng: point.value.lng,
      lat: point.value.lat,
      campus: geo.value.campus,
      category: category.value,
      note: note.value.trim(),
      querySnippet: props.querySnippet,
    });
    ElMessage({ message: result.message || '已提交，感谢补充', type: 'success', duration: 3000 });
    emit('submitted', { name: name.value.trim(), campus: result.campus || geo.value.campus });
    reset();
    close();
  } catch (error) {
    errorTip.value = error instanceof Error ? error.message : '提交失败，请稍后再试';
  } finally {
    submitting.value = false;
  }
};

watch(visible, (open) => { if (!open) showErrors.value = false; });

onUnmounted(() => {
  pickerMap?.destroy?.();
  pickerMap = null;
  pickerMarker = null;
});
</script>

<style lang="scss" scoped>
.dialog-header {
  h3 { margin: 0; color: #303133; font-size: 18px; font-weight: 600; }
  p { margin: 4px 0 0; color: #909399; font-size: 13px; }
}

.dialog-body {
  display: flex;
  gap: 20px;
}

.form-column {
  display: flex;
  flex: 0 0 300px;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  position: relative;

  .field-label {
    color: #606266;
    font-size: 13px;

    em { color: #f56c6c; font-style: normal; }
  }
}

.field-input,
.field-textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  color: #303133;
  font-family: inherit;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;

  &::placeholder { color: #c0c4cc; }

  &:focus {
    border-color: #c20a1c;
    box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12);
  }

  &.invalid { border-color: #f56c6c; }
}

.field-textarea { line-height: 1.6; resize: vertical; }
.field-select { width: 100%; }

.field-error {
  color: #f56c6c;
  font-size: 12px;
}

.field-counter {
  position: absolute;
  right: 2px;
  bottom: -16px;
  color: #c0c4cc;
  font-size: 11px;
}

.position-summary {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px;
  border-radius: 10px;
  background: #f5f6f8;

  .position-value {
    margin: 0;
    color: #303133;
    font-size: 13px;
    font-variant-numeric: tabular-nums;

    &.muted { color: #909399; }
    &.invalid { color: #f56c6c; }
  }

  .position-campus {
    margin: 0;
    color: #67c23a;
    font-size: 12px;

    &.outside { color: #e6a23c; }
  }
}

.submit-error {
  margin: 0;
  color: #f56c6c;
  font-size: 12px;
}

.map-column {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;

  .picker-map {
    width: 100%;
    height: 400px;
    border-radius: 12px;
    overflow: hidden;
    background: #eef1f5;
  }

  .map-hint {
    margin: 0;
    color: #909399;
    font-size: 12px;
  }
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.ghost-btn,
.primary-btn {
  padding: 8px 18px;
  border-radius: 10px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.ghost-btn {
  border: 1px solid #dcdfe6;
  background: #fff;
  color: #606266;

  &:hover:not(:disabled) { border-color: #c20a1c; color: #c20a1c; }
}

.primary-btn {
  border: 1px solid #c20a1c;
  background: #c20a1c;
  color: #fff;

  &:hover:not(:disabled) { background: #a50918; border-color: #a50918; }
}

.ghost-btn:disabled,
.primary-btn:disabled { opacity: 0.55; cursor: not-allowed; }

@media (max-width: 1024px) {
  .dialog-body { flex-direction: column; }
  .form-column { flex: none; }
  .map-column .picker-map { height: 300px; }
}
</style>
