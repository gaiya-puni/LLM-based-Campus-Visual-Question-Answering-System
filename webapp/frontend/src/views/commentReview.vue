<template>
  <div class="comment-review">
    <header class="page-head">
      <div class="head-text">
        <h2>评论审核</h2>
        <p>
          点位留言被多人举报后会<b>自动折叠</b>并进入这里待审。该入口只在后端根目录
          <b>.env</b> 配好 USERDATA_REVIEW_TOKEN（至少 16 位）后出现；首次进入需粘贴同一枚密钥，
          由本机浏览器记住。密钥未配置或填写错误时，后端一律拒绝。
        </p>
      </div>
      <div class="head-actions">
        <span v-if="threshold" class="threshold-chip">自动折叠阈值 {{ threshold }} 人</span>
        <button type="button" class="ghost-btn" :disabled="loading" @click="load">刷新</button>
      </div>
    </header>

    <!-- 审核密钥门禁：与「数据审核」页共用同一个 localStorage 键 -->
    <section v-if="tokenRequired" class="token-gate">
      <h3>需要审核密钥</h3>
      <p>{{ tokenError || '请在 .env 设置 USERDATA_REVIEW_TOKEN（至少 16 位），并把同一密钥填在这里。' }}</p>
      <div class="gate-row">
        <input
          v-model="tokenInput"
          class="gate-input"
          type="password"
          placeholder="粘贴审核密钥"
          autocomplete="off"
        />
        <button type="button" class="primary-btn" :disabled="tokenInput.trim().length < 16" @click="saveToken">
          保存并载入
        </button>
      </div>
      <p class="hint">密钥只保存在本机浏览器 localStorage，不会写入代码或提交到仓库。</p>
    </section>

    <template v-else>
      <section class="stat-row">
        <article v-for="card in statCards" :key="card.key" class="stat-card" :class="{ 'is-alert': card.alert }">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
          <span class="stat-hint">{{ card.hint }}</span>
        </article>
      </section>

      <section class="filter-row">
        <el-radio-group v-model="statusFilter" @change="load">
          <el-radio-button
            v-for="option in FILTER_OPTIONS"
            :key="option.value"
            :value="option.value"
          >{{ option.label }}</el-radio-button>
        </el-radio-group>
        <input
          v-model="keyword"
          class="search-input"
          type="search"
          placeholder="按点位 / 作者 / 内容筛选"
        />
        <span class="filter-total">共 {{ visibleItems.length }} 条</span>
      </section>

      <el-skeleton v-if="loading && !items.length" :rows="5" animated />

      <p v-else-if="!visibleItems.length" class="empty-tip">
        {{ keyword.trim() ? '没有匹配的留言' : '当前分类下没有需要处理的留言' }}
      </p>

      <ul v-else class="comment-list">
        <li v-for="item in visibleItems" :key="item.id" class="comment-card" :class="statusClass(item.status)">
          <div class="card-head">
            <span class="card-poi">{{ item.poiName || item.poiId }}</span>
            <el-tag size="small" :type="statusTagType(item.status)" effect="dark">
              {{ STATUS_LABELS[item.status] }}
            </el-tag>
            <span class="card-report">
              举报 <b>{{ item.reportCount }}</b>
            </span>
            <span class="card-time">{{ formatRelativeTime(item.createdAtMs) }}</span>
          </div>

          <p class="card-content">{{ item.content }}</p>

          <div class="card-meta">
            <span class="meta-item">作者：{{ item.username || '未知' }}（#{{ item.userId }}）</span>
            <span class="meta-item">点位：{{ item.poiId }}</span>
            <span class="meta-item">分类：{{ item.poiCategory || '—' }}</span>
            <span class="meta-item">👍 {{ item.likeCount }} · ❤ {{ item.loveCount }}</span>
            <span class="meta-item">发布于 {{ formatAbsoluteTime(item.createdAtMs) }}</span>
          </div>

          <div v-if="item.reasonBreakdown?.length" class="reason-chips">
            <span v-for="reason in item.reasonBreakdown" :key="reason.reason" class="reason-chip">
              {{ reason.label }} × {{ reason.count }}
            </span>
          </div>

          <div class="card-actions">
            <button
              type="button"
              class="ghost-btn"
              :disabled="busyId === item.id || item.status === 'visible'"
              @click="moderate(item, 'restore')"
            >恢复展示</button>
            <button
              type="button"
              class="ghost-btn danger"
              :disabled="busyId === item.id || item.status === 'deleted'"
              @click="moderate(item, 'delete')"
            >删除留言</button>
            <button
              type="button"
              class="primary-btn danger"
              :disabled="busyId === item.id"
              @click="moderate(item, 'ban_author')"
            >封禁作者</button>
            <span v-if="busyId === item.id" class="busy-tip">处理中…</span>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  fetchManagedComments,
  moderateComment,
  type CommentStatus,
  type ManageFilter,
  type ManageSummary,
  type ModerationAction,
  type PoiComment,
} from '../api/comments';
import { getReviewToken, setReviewToken } from '../api/userdata';
import { formatAbsoluteTime, formatRelativeTime } from '../utils/relativeTime';

const FILTER_OPTIONS: { value: ManageFilter; label: string }[] = [
  { value: 'reported', label: '被举报' },
  { value: 'hidden', label: '已折叠' },
  { value: 'deleted', label: '已删除' },
  { value: 'all', label: '全部' },
];

const STATUS_LABELS: Record<CommentStatus, string> = {
  visible: '正常',
  hidden: '已折叠',
  deleted: '已删除',
};

const ACTION_LABELS: Record<ModerationAction, string> = {
  restore: '恢复展示',
  delete: '删除留言',
  ban_author: '封禁作者',
};

const items = ref<PoiComment[]>([]);
const summary = ref<ManageSummary>({ total: 0, hidden: 0, deleted: 0, reported: 0, bannedUsers: 0 });
const threshold = ref(0);
const statusFilter = ref<ManageFilter>('reported');
const keyword = ref('');
const loading = ref(false);
const busyId = ref<number | null>(null);
const tokenRequired = ref(!getReviewToken());
const tokenInput = ref('');
const tokenError = ref('');

const visibleItems = computed(() => {
  const word = keyword.value.trim().toLowerCase();
  if (!word) return items.value;
  return items.value.filter(item => [item.poiName, item.poiId, item.username, item.content]
    .some(field => (field || '').toLowerCase().includes(word)));
});

const statCards = computed(() => [
  { key: 'reported', label: '被举报', value: summary.value.reported, hint: '有举报记录', alert: summary.value.reported > 0 },
  { key: 'hidden', label: '已折叠', value: summary.value.hidden, hint: '达阈值自动隐藏', alert: summary.value.hidden > 0 },
  { key: 'deleted', label: '已删除', value: summary.value.deleted, hint: '软删除，可追溯' , alert: false },
  { key: 'total', label: '留言总数', value: summary.value.total, hint: '全部状态合计', alert: false },
  { key: 'banned', label: '封禁账号', value: summary.value.bannedUsers, hint: '禁止再发留言', alert: summary.value.bannedUsers > 0 },
]);

const statusClass = (status: CommentStatus) => `is-${status}`;

const statusTagType = (status: CommentStatus) => {
  if (status === 'hidden') return 'warning';
  return status === 'deleted' ? 'danger' : 'success';
};

const load = async () => {
  if (tokenRequired.value) return;
  loading.value = true;
  try {
    const result = await fetchManagedComments(statusFilter.value);
    items.value = result.items;
    summary.value = result.summary;
    threshold.value = result.thresholds.reportHideThreshold;
    tokenError.value = '';
  } catch (error) {
    const message = error instanceof Error ? error.message : '载入失败';
    if (message.includes('审核接口未开启') || message.includes('403')) {
      tokenRequired.value = true;
      tokenError.value = message;
    } else {
      ElMessage({ message, type: 'error', duration: 3000 });
    }
  } finally {
    loading.value = false;
  }
};

const saveToken = () => {
  setReviewToken(tokenInput.value);
  tokenInput.value = '';
  tokenRequired.value = false;
  tokenError.value = '';
  load();
};

const moderate = async (item: PoiComment, action: ModerationAction) => {
  try {
    const { value } = await ElMessageBox.prompt(
      `确定对「${item.username || '该用户'}」的这条留言执行「${ACTION_LABELS[action]}」？`,
      ACTION_LABELS[action],
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        inputPlaceholder: '处置备注（选填，会记入操作台账）',
        inputValidator: () => true,
        type: action === 'restore' ? 'info' : 'warning',
      },
    );
    busyId.value = item.id;
    const result = await moderateComment(item.id, action, (value || '').trim());
    ElMessage({ message: result.message, type: 'success', duration: 2000 });
    await load();
  } catch (error) {
    if (error instanceof Error && error.message) {
      ElMessage({ message: error.message, type: 'error', duration: 3000 });
    }
    // 用户取消（reject 的字符串）无需提示
  } finally {
    busyId.value = null;
  }
};

onMounted(load);
</script>

<style lang="scss" scoped>
.comment-review {
  --cr-red: var(--ecnu-red, #c20a1c);
  --cr-ink: #23262b;
  --cr-muted: #8f959e;

  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px 32px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.06);
  color: var(--cr-ink);
}

.page-head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.head-text {
  min-width: 0;

  h2 {
    margin: 0 0 6px;
    font-size: 20px;
    font-weight: 700;
  }

  p {
    margin: 0;
    max-width: 720px;
    color: #606266;
    font-size: 13px;
    line-height: 1.7;
  }
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}

.threshold-chip {
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(194, 10, 28, 0.08);
  color: var(--cr-red);
  font-size: 12px;
}

.token-gate {
  padding: 18px 20px;
  border: 1px solid #f0d5d8;
  border-radius: 12px;
  background: #fffafb;

  h3 {
    margin: 0 0 6px;
    font-size: 15px;
  }

  p {
    margin: 0 0 10px;
    color: #606266;
    font-size: 13px;
    line-height: 1.7;
  }
}

.gate-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.gate-input {
  flex: 1;
  min-width: 220px;
  padding: 8px 12px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  font-size: 13px;
  outline: none;

  &:focus { border-color: var(--cr-red); box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12); }
}

.hint {
  margin-top: 8px !important;
  color: #a8abb2 !important;
  font-size: 12px !important;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 14px;
  border: 1px solid #eef0f3;
  border-radius: 12px;
  background: #fff;

  &.is-alert {
    border-color: #f2d7db;
    background: linear-gradient(180deg, #fff, #fffafa);

    .stat-value { color: var(--cr-red); }
  }
}

.stat-label {
  color: var(--cr-muted);
  font-size: 12px;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-hint {
  color: #c0c4cc;
  font-size: 11px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.search-input {
  flex: 1;
  min-width: 200px;
  padding: 7px 12px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  font-size: 13px;
  outline: none;

  &:focus { border-color: var(--cr-red); box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12); }
}

.filter-total {
  color: var(--cr-muted);
  font-size: 12px;
}

.empty-tip {
  margin: 28px 0;
  color: var(--cr-muted);
  font-size: 13px;
  text-align: center;
}

.comment-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.comment-card {
  padding: 14px 16px;
  border: 1px solid #eef0f3;
  border-left: 3px solid #dcdfe6;
  border-radius: 12px;
  background: #fff;

  &.is-hidden { border-left-color: #e6a23c; }
  &.is-deleted { border-left-color: #f56c6c; opacity: 0.86; }
  &.is-visible { border-left-color: #67c23a; }
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.card-poi {
  font-size: 14px;
  font-weight: 700;
}

.card-report {
  color: #b0727a;
  font-size: 12px;

  b { color: var(--cr-red); }
}

.card-time {
  margin-left: auto;
  color: #c0c4cc;
  font-size: 12px;
}

.card-content {
  margin: 9px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fafbfc;
  font-size: 13.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.card-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 8px;
  color: var(--cr-muted);
  font-size: 12px;
}

.reason-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.reason-chip {
  padding: 2px 9px;
  border-radius: 999px;
  background: #fdf1f2;
  color: #b0727a;
  font-size: 11.5px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.busy-tip {
  color: var(--cr-muted);
  font-size: 12px;
}

.ghost-btn,
.primary-btn {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  line-height: 1.3;
  cursor: pointer;
  transition: all 0.18s ease;
}

.ghost-btn {
  border: 1px solid #dcdfe6;
  background: #fff;
  color: #606266;

  &:hover:not(:disabled) { border-color: var(--cr-red); color: var(--cr-red); }
  &.danger:hover:not(:disabled) { border-color: #f56c6c; color: #f56c6c; }
}

.primary-btn {
  border: 1px solid var(--cr-red);
  background: var(--cr-red);
  color: #fff;

  &:hover:not(:disabled) { background: #a00818; border-color: #a00818; }
  &.danger { background: #f56c6c; border-color: #f56c6c; &:hover:not(:disabled) { background: #e04b4b; } }
}

.ghost-btn:disabled,
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* 移动端：卡片单列铺满，按钮抬到可点尺寸 */
@media (max-width: 768px) {
  .comment-review {
    padding: 14px 12px 24px;
    border-radius: 0;
  }

  .head-actions { margin-left: 0; }
  .card-time { margin-left: 0; }
  .card-content { font-size: 14px; }
  .ghost-btn,
  .primary-btn { min-height: 38px; padding: 0 14px; font-size: 13px; }
  .gate-input,
  .search-input { font-size: 16px; }
}
</style>
