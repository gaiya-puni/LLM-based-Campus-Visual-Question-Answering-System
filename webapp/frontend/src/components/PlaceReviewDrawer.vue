<template>
  <el-drawer
    :model-value="modelValue"
    direction="rtl"
    size="420px"
    :with-header="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="item" class="drawer-body">
      <header class="drawer-head">
        <div>
          <h3>{{ item.name }}</h3>
          <p class="drawer-sub">
            <el-tag size="small" effect="plain">{{ item.campus || '未标校区' }}</el-tag>
            <el-tag size="small" :type="sourceTagType" effect="plain">{{ SOURCE_LABELS[item.source] }}</el-tag>
            <el-tag size="small" :type="statusTagType" effect="dark">{{ STATUS_LABELS[item.status] }}</el-tag>
          </p>
        </div>
        <button type="button" class="close-btn" aria-label="关闭详情" @click="emit('update:modelValue', false)">
          <Close />
        </button>
      </header>

      <dl class="meta-grid">
        <div><dt>累计提及</dt><dd>{{ item.mentions }} 次</dd></div>
        <div><dt>置信度</dt><dd>{{ item.confidence.toFixed(2) }}</dd></div>
        <div><dt>首次出现</dt><dd>{{ pretty(item.firstSeenAt) }}</dd></div>
        <div><dt>最近出现</dt><dd>{{ pretty(item.lastSeenAt) }}</dd></div>
      </dl>

      <section class="drawer-section">
        <h4>位置</h4>
        <p v-if="item.lng && item.lat" class="coord">
          {{ item.lng.toFixed(5) }}, {{ item.lat.toFixed(5) }}
          <a :href="mapLink" target="_blank" rel="noopener">在地图上查看 ↗</a>
        </p>
        <p v-else class="hint">暂无坐标：只有用户上报或后续补齐位置后才能上图。</p>
      </section>

      <section class="drawer-section">
        <h4>原始问句（已脱敏，最多 3 条）</h4>
        <ul v-if="item.samples?.length" class="sample-list">
          <li v-for="(sample, index) in item.samples" :key="index">{{ sample }}</li>
        </ul>
        <p v-else class="hint">没有留存问句样本。</p>
      </section>

      <section class="drawer-section">
        <h4>审核备注</h4>
        <textarea
          v-model="note"
          class="note-input"
          rows="3"
          maxlength="200"
          placeholder="例如：已实地核实位置 / 与既有 POI 重复 / 名称不规范"
        ></textarea>
        <p class="hint">{{ note.length }}/200</p>
      </section>

      <p v-if="errorTip" class="error-tip">{{ errorTip }}</p>

      <footer class="drawer-actions">
        <button type="button" class="reject-btn" :disabled="busy" @click="review('rejected')">
          驳回
        </button>
        <button type="button" class="approve-btn" :disabled="busy" @click="review('approved')">
          {{ busy ? '处理中…' : '通过' }}
        </button>
      </footer>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Close } from '@element-plus/icons-vue';
import { SOURCE_LABELS, STATUS_LABELS, type CandidateStatus, type PendingItem } from '../api/userdata';

const props = defineProps<{
  modelValue: boolean;
  item: PendingItem | null;
  busy?: boolean;
  errorTip?: string;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'review', payload: { status: CandidateStatus; note: string }): void;
}>();

const note = ref('');
const errorTip = computed(() => props.errorTip || '');

watch(() => props.item, (item) => { note.value = item?.note || ''; });

const sourceTagType = computed(() => {
  if (props.item?.source === 'user') return 'danger';
  return props.item?.source === 'llm' ? 'primary' : 'info';
});

const statusTagType = computed(() => {
  if (props.item?.status === 'approved') return 'success';
  return props.item?.status === 'rejected' ? 'danger' : 'warning';
});

const pretty = (value?: string) => (value || '').replace('T', ' ');

const mapLink = computed(() => {
  if (!props.item?.lng || !props.item?.lat) return '#';
  const name = encodeURIComponent(props.item.name);
  return `https://uri.amap.com/marker?position=${props.item.lng},${props.item.lat}&name=${name}`;
});

const review = (status: CandidateStatus) => {
  emit('review', { status, note: note.value.trim() });
};
</script>

<style lang="scss" scoped>
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  height: 100%;
  padding: 4px;
}

.drawer-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;

  h3 { margin: 0; color: #303133; font-size: 18px; font-weight: 600; }

  .drawer-sub {
    display: flex;
    gap: 6px;
    margin: 8px 0 0;
  }

  .close-btn {
    margin-left: auto;
    padding: 4px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: #909399;
    cursor: pointer;
    transition: all 0.18s ease;

    &:hover { background: #f5f6f8; color: #c20a1c; }
  }
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0;

  dt { color: #909399; font-size: 12px; }
  dd { margin: 2px 0 0; color: #303133; font-size: 14px; font-variant-numeric: tabular-nums; }
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 8px;

  h4 { margin: 0; color: #606266; font-size: 13px; font-weight: 600; }
}

.coord {
  margin: 0;
  color: #303133;
  font-size: 13px;
  font-variant-numeric: tabular-nums;

  a { margin-left: 8px; color: #c20a1c; text-decoration: none; }
  a:hover { text-decoration: underline; }
}

.sample-list {
  margin: 0;
  padding-left: 18px;
  color: #606266;
  font-size: 13px;
  line-height: 1.8;
}

.note-input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  color: #303133;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;

  &:focus { border-color: #c20a1c; box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12); }
}

.hint { margin: 0; color: #909399; font-size: 12px; }
.error-tip { margin: 0; color: #f56c6c; font-size: 12px; }

.drawer-actions {
  display: flex;
  gap: 10px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid #f0f2f5;
}

.reject-btn,
.approve-btn {
  flex: 1;
  padding: 10px 0;
  border-radius: 10px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.reject-btn {
  border: 1px solid #f56c6c;
  background: #fff;
  color: #f56c6c;

  &:hover:not(:disabled) { background: #fef0f0; }
}

.approve-btn {
  border: 1px solid #c20a1c;
  background: #c20a1c;
  color: #fff;

  &:hover:not(:disabled) { background: #a50918; border-color: #a50918; }
}

.reject-btn:disabled,
.approve-btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
