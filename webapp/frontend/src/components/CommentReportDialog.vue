<template>
  <div class="report-view">
    <div class="report-view__head">
      <button type="button" class="back-btn" aria-label="返回留言列表" @click="emit('cancel')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="m15 18-6-6 6-6" />
        </svg>
        返回
      </button>
      <h5>举报留言</h5>
    </div>

    <p class="report-view__target">
      举报对象：<b>{{ target?.username || '匿名用户' }}</b>
      <span class="report-view__excerpt">{{ excerpt }}</span>
    </p>

    <p class="report-view__label">请选择举报原因（必选）</p>
    <div class="reason-list" role="radiogroup" aria-label="举报原因">
      <label
        v-for="reason in reasons"
        :key="reason.value"
        class="reason-item"
        :class="{ 'is-active': picked === reason.value }"
      >
        <input
          type="radio"
          name="comment-report-reason"
          :value="reason.value"
          :checked="picked === reason.value"
          @change="picked = reason.value"
        />
        <span class="reason-item__dot" aria-hidden="true"></span>
        <span class="reason-item__text">{{ reason.label }}</span>
      </label>
    </div>

    <label class="report-view__note">
      <span>补充说明（选填，{{ note.length }}/{{ maxChars }}）</span>
      <textarea
        v-model="note"
        rows="2"
        :maxlength="maxChars"
        placeholder="例如：含广告链接 / 与事实不符（选填）"
      ></textarea>
    </label>

    <p v-if="tip" class="report-view__tip" role="alert">{{ tip }}</p>

    <div class="report-view__actions">
      <button type="button" class="ghost-btn" :disabled="submitting" @click="emit('cancel')">取消</button>
      <button type="button" class="primary-btn" :disabled="submitting || !picked" @click="submit">
        {{ submitting ? '提交中…' : '提交举报' }}
      </button>
    </div>

    <p class="report-view__policy">
      同一留言每人只能举报一次；被多人举报的留言会自动折叠并进入人工复核。
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';

const props = defineProps<{
  reasons: { value: string; label: string }[];
  target?: { username: string; content: string } | null;
  submitting?: boolean;
  maxChars?: number;
}>();

const emit = defineEmits<{
  (e: 'submit', payload: { reasonCode: string; reasonText: string }): void;
  (e: 'cancel'): void;
}>();

const picked = ref('');
const note = ref('');
const tip = ref('');

const excerpt = computed(() => {
  const content = (props.target?.content || '').trim();
  if (!content) return '';
  return content.length > 40 ? `“${content.slice(0, 40)}…”` : `“${content}”`;
});

const submit = () => {
  if (!picked.value) {
    tip.value = '请先选择举报原因';
    return;
  }
  tip.value = '';
  emit('submit', { reasonCode: picked.value, reasonText: note.value.trim() });
};
</script>

<style lang="scss" scoped>
.report-view {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.report-view__head {
  display: flex;
  align-items: center;
  gap: 6px;

  h5 {
    margin: 0;
    color: #1f2329;
    font-size: 13px;
    font-weight: 700;
  }
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 6px 2px 2px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #8a5a00;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.18s ease;

  svg { width: 14px; height: 14px; }

  &:hover { background: rgba(194, 10, 28, 0.08); color: var(--ecnu-red, #c20a1c); }
}

.report-view__target {
  margin: 0;
  color: #606266;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.report-view__excerpt {
  display: block;
  margin-top: 2px;
  color: #909399;
  font-style: italic;
}

.report-view__label {
  margin: 2px 0 0;
  color: #303133;
  font-size: 12px;
  font-weight: 600;
}

.reason-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.reason-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafafb;
  cursor: pointer;
  transition: all 0.18s ease;

  input { position: absolute; opacity: 0; pointer-events: none; }

  &:hover { border-color: rgba(194, 10, 28, 0.35); background: #fff; }

  &.is-active {
    border-color: var(--ecnu-red, #c20a1c);
    background: rgba(194, 10, 28, 0.06);

    .reason-item__dot {
      border-color: var(--ecnu-red, #c20a1c);
      background: var(--ecnu-red, #c20a1c);
      box-shadow: inset 0 0 0 2px #fff;
    }

    .reason-item__text { color: var(--ecnu-red, #c20a1c); font-weight: 600; }
  }
}

.reason-item__dot {
  flex-shrink: 0;
  width: 13px;
  height: 13px;
  border: 1.5px solid #c0c4cc;
  border-radius: 50%;
  box-sizing: border-box;
  transition: all 0.18s ease;
}

.reason-item__text {
  font-size: 12px;
  color: #303133;
}

.report-view__note {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #303133;
  font-size: 12px;
  font-weight: 600;

  textarea {
    box-sizing: border-box;
    width: 100%;
    padding: 7px 9px;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    background: #fff;
    color: #303133;
    font-family: inherit;
    font-size: 12px;
    font-weight: 400;
    line-height: 1.55;
    resize: vertical;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;

    &:focus {
      border-color: var(--ecnu-red, #c20a1c);
      box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12);
    }
  }
}

.report-view__tip {
  margin: 0;
  color: #f56c6c;
  font-size: 12px;
}

.report-view__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.ghost-btn,
.primary-btn {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
}

.ghost-btn {
  border: 1px solid #dcdfe6;
  background: #fff;
  color: #606266;

  &:hover:not(:disabled) { border-color: var(--ecnu-red, #c20a1c); color: var(--ecnu-red, #c20a1c); }
}

.primary-btn {
  border: 1px solid var(--ecnu-red, #c20a1c);
  background: var(--ecnu-red, #c20a1c);
  color: #fff;

  &:hover:not(:disabled) { background: var(--ecnu-red-dark, #a00818); border-color: var(--ecnu-red-dark, #a00818); }
}

.ghost-btn:disabled,
.primary-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.report-view__policy {
  margin: 0;
  color: #c0c4cc;
  font-size: 11px;
  line-height: 1.5;
}

/* 移动端：触控热区不低于 44px（项目规范），输入字号不低于 16px（避免 iOS 聚焦放大页面） */
@media (max-width: 768px) {
  .reason-item { min-height: 44px; padding: 8px 12px; }
  .reason-item__text { font-size: 13px; }
  .report-view__note textarea { font-size: 16px; }
  .report-view__head .back-btn { min-height: 44px; }
  .ghost-btn,
  .primary-btn { min-height: 44px; padding: 0 18px; font-size: 13px; }
}
</style>
