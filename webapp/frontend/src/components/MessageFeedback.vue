<template>
  <div class="message-feedback">
    <div class="rating-row">
      <button
        type="button"
        class="rating-btn"
        :class="{ selected: current === 'up' }"
        :disabled="submitting"
        :title="current === 'up' ? '再点一次可撤回这条评价' : '这条回答有用'"
        @click="pick('up')"
      >
        <svg class="rating-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M7 10v12" />
          <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
        </svg>
        <span>有用</span>
      </button>
      <button
        type="button"
        class="rating-btn"
        :class="{ selected: current === 'down' }"
        :disabled="submitting"
        :title="current === 'down' ? '再点一次可撤回这条评价' : '这条回答没用'"
        @click="pick('down')"
      >
        <svg class="rating-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M17 14V2" />
          <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
        </svg>
        <span>没用</span>
      </button>
      <transition name="fade">
        <span v-if="receivedTip" class="received-tip">已收到，谢谢反馈</span>
      </transition>
    </div>

    <transition name="expand">
      <div v-if="reasonOpen" class="reason-area">
        <textarea
          v-model="reason"
          class="reason-input"
          rows="2"
          maxlength="200"
          placeholder="哪里不对？例如：地点名字错了 / 位置标偏了 / 答非所问（选填）"
        ></textarea>
        <div class="reason-actions">
          <span class="reason-counter">{{ reason.length }}/200</span>
          <button type="button" class="ghost-btn" :disabled="submitting" @click="cancelReason">取消</button>
          <button type="button" class="primary-btn" :disabled="submitting" @click="submitDown">
            {{ submitting ? '提交中…' : '提交' }}
          </button>
        </div>
        <p v-if="errorTip" class="error-tip">{{ errorTip }}</p>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { submitFeedback, type FeedbackRating } from '../api/userdata';

const props = defineProps<{
  /** 消息 id：同一消息的重复评价会被后端按"最新一条"处理 */
  messageId: string;
  /** 这一轮的用户问句（随评价一起存，便于审核时还原上下文） */
  query?: string;
  /** 回答来源引擎（rule / itinerary / needle…），用于按引擎统计 */
  engine?: string;
  campus?: string;
  /** 已提交的评价，由消息对象持有，重渲染后不丢 */
  value?: FeedbackRating | null;
}>();

const emit = defineEmits<{
  (e: 'submitted', payload: { rating: FeedbackRating; reason: string }): void;
}>();

const current = ref<FeedbackRating | null>(props.value || null);
const reasonOpen = ref(false);
const reason = ref('');
const submitting = ref(false);
const receivedTip = ref(false);
const errorTip = ref('');

let tipTimer: number | undefined;

watch(() => props.value, (value) => { current.value = value || null; });

const flashTip = () => {
  receivedTip.value = true;
  window.clearTimeout(tipTimer);
  tipTimer = window.setTimeout(() => { receivedTip.value = false; }, 2200);
};

const send = async (rating: FeedbackRating, text = ''): Promise<void> => {
  submitting.value = true;
  errorTip.value = '';
  try {
    await submitFeedback({
      messageId: props.messageId,
      rating,
      reason: text,
      query: props.query,
      messageEngine: props.engine,
      campus: props.campus,
    });
    current.value = rating === 'none' ? null : rating;
    emit('submitted', { rating, reason: text });
    reasonOpen.value = false;
    reason.value = '';
    flashTip();
  } catch (error) {
    errorTip.value = error instanceof Error ? error.message : '提交失败，请稍后再试';
  } finally {
    submitting.value = false;
  }
};

/** 点选中项 = 撤回；点另一项 = 提交/改判 */
const pick = (rating: 'up' | 'down') => {
  if (submitting.value) return;
  if (current.value === rating) {
    send('none');
    return;
  }
  if (rating === 'up') {
    send('up');
    return;
  }
  reasonOpen.value = true;
};

const submitDown = () => { send('down', reason.value.trim()); };
const cancelReason = () => {
  reasonOpen.value = false;
  errorTip.value = '';
};
</script>

<style lang="scss" scoped>
.message-feedback {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}

.rating-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 26px;
}

.rating-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 999px;
  background: transparent;
  color: #909399;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover:not(:disabled) {
    border-color: #c20a1c;
    color: #c20a1c;
    background: rgba(194, 10, 28, 0.06);
    transform: translateY(-1px);
  }

  &:disabled { cursor: not-allowed; opacity: 0.6; }

  &.selected {
    border-color: #c20a1c;
    background: #c20a1c;
    color: #fff;
    animation: ratingPop 0.18s cubic-bezier(0.34, 1.56, 0.64, 1);
  }

  .rating-icon { width: 13px; height: 13px; flex-shrink: 0; }
}

@keyframes ratingPop {
  0% { transform: scale(0.92); }
  60% { transform: scale(1.06); }
  100% { transform: scale(1); }
}

.received-tip {
  font-size: 12px;
  color: #67c23a;
}

.reason-area {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 420px;
  padding: 10px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fafafb;
}

.reason-input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  color: #303133;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;

  &:focus {
    border-color: #c20a1c;
    box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12);
  }
}

.reason-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.reason-counter {
  margin-right: auto;
  color: #c0c4cc;
  font-size: 11px;
}

.ghost-btn,
.primary-btn {
  padding: 5px 12px;
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

  &:hover:not(:disabled) { border-color: #c20a1c; color: #c20a1c; }
}

.primary-btn {
  border: 1px solid #c20a1c;
  background: #c20a1c;
  color: #fff;

  &:hover:not(:disabled) { background: #a50918; border-color: #a50918; }
}

.ghost-btn:disabled,
.primary-btn:disabled { opacity: 0.6; cursor: not-allowed; }

.error-tip {
  margin: 0;
  color: #f56c6c;
  font-size: 12px;
}

.fade-enter-active,
.fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from,
.fade-leave-to { opacity: 0; }

.expand-enter-active,
.expand-leave-active { transition: opacity 0.2s ease, max-height 0.25s ease; overflow: hidden; }
.expand-enter-from,
.expand-leave-to { opacity: 0; max-height: 0; }
.expand-enter-to,
.expand-leave-from { max-height: 320px; }
</style>
