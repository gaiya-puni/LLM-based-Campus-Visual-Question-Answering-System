<template>
  <section class="poi-comments" :aria-busy="loading ? 'true' : 'false'">
    <!-- 顶部：标题 + 汇总 + 排序 -->
    <header class="pc-top">
      <div class="pc-top__title">
        <svg class="pc-top__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8Z" />
        </svg>
        <h4>留言板</h4>
        <span v-if="poiName" class="pc-top__poi">{{ poiName }}</span>
      </div>
      <div class="pc-top__side">
        <span v-if="stats.total" class="pc-top__stat">
          {{ stats.total }} 条<template v-if="stats.like + stats.love"> · {{ stats.like + stats.love }} 赞</template>
        </span>
        <div v-if="view === 'all' && visibleComments.length" class="pc-sort" role="tablist" aria-label="留言排序">
          <button
            v-for="option in SORT_OPTIONS"
            :key="option.value"
            type="button"
            role="tab"
            class="pc-sort__item"
            :class="{ 'is-on': sort === option.value }"
            :aria-selected="sort === option.value"
            @click="changeSort(option.value)"
          >{{ option.label }}</button>
        </div>
      </div>
    </header>

    <!-- 主体：可下滑 -->
    <div class="pc-body">
      <CommentReportDialog
        v-if="reportTarget"
        :reasons="meta?.reportReasons || []"
        :target="reportTarget"
        :submitting="reporting"
        :max-chars="meta?.limits.maxReasonChars ?? 200"
        @submit="submitReport"
        @cancel="reportTarget = null"
      />

      <template v-else>
        <div v-if="loading" class="pc-skeleton" aria-hidden="true">
          <span v-for="row in 3" :key="row" class="pc-skeleton__row"></span>
        </div>

        <p v-else-if="!visibleComments.length" class="pc-empty">
          还没有人留言，来写下关于这里的第一句吧。
        </p>

        <ul v-else class="pc-list">
          <li v-for="item in visibleComments" :key="item.id" class="pc-item">
            <span class="pc-item__avatar" :class="{ 'is-mine': item.isMine }" aria-hidden="true">
              {{ avatarChar(item.username) }}
            </span>
            <div class="pc-item__main">
              <div class="pc-item__meta">
                <span class="pc-item__name">{{ item.username || '匿名用户' }}</span>
                <span v-if="item.isMine" class="pc-item__tag pc-item__tag--mine">我</span>
                <span v-if="item.isLong" class="pc-item__tag pc-item__tag--long">长评</span>
                <span class="pc-item__time" :title="formatAbsoluteTime(item.createdAtMs)">
                  {{ formatRelativeTime(item.createdAtMs) }}
                </span>
              </div>

              <button
                v-if="item.status === 'hidden' && !revealed.has(item.id)"
                type="button"
                class="pc-folded"
                @click="reveal(item.id)"
              >
                该留言因被多次举报已折叠，点击查看
              </button>
              <template v-else>
                <p v-if="item.status === 'hidden'" class="pc-folded-hint">
                  已折叠（{{ item.reportCount }} 人举报）
                </p>
                <p class="pc-item__content">{{ item.content }}</p>
              </template>

              <div class="pc-actions">
                <button
                  type="button"
                  class="pc-act"
                  :class="{ 'is-on': item.isLiked }"
                  :aria-pressed="item.isLiked"
                  :title="item.isLiked ? '取消点赞' : '点赞'"
                  @click="toggleReaction(item, 'like')"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M7 10v12" />
                    <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
                  </svg>
                  <span>{{ item.likeCount || '点赞' }}</span>
                </button>

                <button
                  type="button"
                  class="pc-act pc-act--love"
                  :class="{ 'is-on': item.isLoved }"
                  :aria-pressed="item.isLoved"
                  :title="item.isLoved ? '取消爱心' : '送一颗爱心'"
                  @click="toggleReaction(item, 'love')"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l8.84 8.84 8.84-8.84a5.5 5.5 0 0 0 0-7.78Z" />
                  </svg>
                  <span>{{ item.loveCount || '爱心' }}</span>
                </button>

                <button
                  v-if="item.isMine"
                  type="button"
                  class="pc-act pc-act--danger"
                  title="删除我的留言"
                  @click="removeComment(item)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M3 6h18" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                  <span>删除</span>
                </button>

                <button
                  v-else
                  type="button"
                  class="pc-act pc-act--report"
                  :disabled="item.isReported"
                  :title="item.isReported ? '你已举报过该留言' : '举报该留言'"
                  @click="openReport(item)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
                       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1Z" />
                    <path d="M4 22v-7" />
                  </svg>
                  <span>{{ item.isReported ? '已举报' : '举报' }}</span>
                </button>
              </div>
            </div>
          </li>
        </ul>

        <button
          v-if="view === 'preview' && entryTotal > visibleComments.length"
          type="button"
          class="pc-more"
          @click="openAll"
        >查看全部 {{ entryTotal }} 条留言</button>

        <button
          v-else-if="view === 'all' && hasMore"
          type="button"
          class="pc-more"
          :disabled="loadingMore"
          @click="loadMore"
        >{{ loadingMore ? '加载中…' : '加载更多' }}</button>
      </template>
    </div>

    <!-- 底部：发布 / 登录引导 -->
    <footer class="pc-foot">
      <template v-if="loggedIn">
        <textarea
          v-model="draft"
          class="pc-input"
          rows="2"
          :maxlength="maxChars"
          :placeholder="composerPlaceholder"
          aria-label="写下你的留言"
        ></textarea>
        <div class="pc-foot__row">
          <span class="pc-counter" :class="{ 'is-warn': draft.length >= longMinChars }">
            {{ draft.length }}/{{ maxChars }}
            <template v-if="draft.length >= longMinChars"> · 将作为长评优先展示</template>
          </span>
          <button
            type="button"
            class="pc-submit"
            :disabled="submitting || !draft.trim()"
            @click="submitComment"
          >{{ submitting ? '发布中…' : '发布留言' }}</button>
        </div>
      </template>

      <template v-else>
        <p class="pc-login">
          登录后即可留言、点赞、送爱心与举报
          <button type="button" class="pc-login__btn" @click="goLogin">去登录</button>
        </p>
      </template>

      <p v-if="errorTip" class="pc-error" role="alert">{{ errorTip }}</p>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import CommentReportDialog from './CommentReportDialog.vue';
import {
  CommentApiError,
  createComment,
  deleteComment,
  fetchCommentMeta,
  fetchCommentPreview,
  fetchComments,
  reactToComment,
  reportComment,
  type CommentMeta,
  type CommentReaction,
  type CommentSort,
  type CommentStats,
  type PoiComment,
} from '../api/comments';
import { currentSession, refreshSession } from '../api/session';
import { formatAbsoluteTime, formatRelativeTime } from '../utils/relativeTime';

const props = defineProps<{
  /** 点位唯一标识（与后端 POI 库一致，如 plant_3023） */
  poiId: string;
  /** 点位名，仅用于标题展示 */
  poiName?: string;
}>();

const emit = defineEmits<{
  /** 未登录用户触发了写操作：宿主可据此做额外引导（如顶部提示条） */
  (e: 'login-required'): void;
}>();

const SORT_OPTIONS: { value: CommentSort; label: string }[] = [
  { value: 'hot', label: '最热' },
  { value: 'new', label: '最新' },
];

const meta = ref<CommentMeta | null>(null);
const stats = ref<CommentStats>({ total: 0, long: 0, short: 0, like: 0, love: 0 });
/** 当前用户自己**被折叠**的留言条数：预览与头部计数都不含它，但列表里有 */
const myHidden = ref(0);
const previewItems = ref<PoiComment[]>([]);
const listItems = ref<PoiComment[]>([]);
const view = ref<'preview' | 'all'>('preview');
const sort = ref<CommentSort>('hot');
const page = ref(1);
const hasMore = ref(false);
const loading = ref(false);
const loadingMore = ref(false);
const submitting = ref(false);
const reporting = ref(false);
const draft = ref('');
const errorTip = ref('');
const reportTarget = ref<PoiComment | null>(null);
const revealed = reactive(new Set<number>());

const loginState = ref(currentSession());
const loggedIn = computed(() => loginState.value.loggedIn);
const visibleComments = computed(() => (view.value === 'all' ? listItems.value : previewItems.value));
// 预览态下的"全部条数"= 公开留言 + 自己被折叠的留言，否则作者看不见自己的折叠留言
const entryTotal = computed(() => stats.value.total + myHidden.value);
const maxChars = computed(() => meta.value?.limits.maxChars ?? 500);
const longMinChars = computed(() => meta.value?.limits.longMinChars ?? 80);
const composerPlaceholder = computed(
  () => `说说 ${props.poiName || '这里'} 给你的印象（满 ${longMinChars.value} 字将成为长评）`,
);

const avatarChar = (name: string): string => (name || '游').trim().slice(0, 1) || '游';
const reveal = (id: number) => { revealed.add(id); };

function handleError(error: unknown, fallback: string): void {
  if (error instanceof CommentApiError && error.needLogin) {
    loginState.value = { loggedIn: false, username: '' };
    emit('login-required');
    errorTip.value = error.message;
    return;
  }
  errorTip.value = error instanceof Error ? error.message : fallback;
}

function goLogin(): void {
  emit('login-required');
  // hash 路由：直接改哈希比引入 router 更稳妥（组件可能挂在 AMap InfoWindow 里，
  // 不是路由视图的一部分，拿不到 useRouter 的注入上下文）。
  window.location.hash = '#/';
}

function requireLogin(): boolean {
  if (loggedIn.value) return true;
  errorTip.value = '';
  ElMessage({ message: '登录后即可留言、点赞与举报', type: 'info' });
  goLogin();
  return false;
}

/** 同一份数据在预览与全部列表里各持一份引用，表态后要两边同步。 */
function patchComment(id: number, patch: Partial<PoiComment>): void {
  [previewItems.value, listItems.value].forEach(collection => {
    const found = collection.find(item => item.id === id);
    if (found) Object.assign(found, patch);
  });
}

function dropComment(id: number): void {
  previewItems.value = previewItems.value.filter(item => item.id !== id);
  listItems.value = listItems.value.filter(item => item.id !== id);
}

async function loadPreview(): Promise<void> {
  loading.value = true;
  errorTip.value = '';
  try {
    const result = await fetchCommentPreview(props.poiId);
    previewItems.value = result.items;
    stats.value = result.stats;
    myHidden.value = result.myHidden ?? 0;
  } catch (error) {
    handleError(error, '留言加载失败');
  } finally {
    loading.value = false;
  }
}

async function loadPage(targetPage: number, append: boolean): Promise<void> {
  try {
    const result = await fetchComments({ poiId: props.poiId, sort: sort.value, page: targetPage });
    listItems.value = append ? [...listItems.value, ...result.items] : result.items;
    page.value = result.page;
    hasMore.value = result.hasMore;
    stats.value = { ...stats.value, total: result.total };
  } catch (error) {
    handleError(error, '留言加载失败');
  }
}

async function openAll(): Promise<void> {
  view.value = 'all';
  loading.value = true;
  try {
    await loadPage(1, false);
  } finally {
    loading.value = false;
  }
}

async function changeSort(next: CommentSort): Promise<void> {
  if (sort.value === next) return;
  sort.value = next;
  loading.value = true;
  try {
    await loadPage(1, false);
  } finally {
    loading.value = false;
  }
}

async function loadMore(): Promise<void> {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  try {
    await loadPage(page.value + 1, true);
  } finally {
    loadingMore.value = false;
  }
}

async function toggleReaction(item: PoiComment, type: CommentReaction): Promise<void> {
  if (!requireLogin()) return;
  const active = type === 'like' ? item.isLiked : item.isLoved;
  const action = active ? 'remove' : 'add';
  const before = {
    isLiked: item.isLiked,
    isLoved: item.isLoved,
    likeCount: item.likeCount,
    loveCount: item.loveCount,
  };
  // 乐观更新：先动界面，失败再回滚，避免每次点击都等一个来回
  patchComment(item.id, type === 'like'
    ? { isLiked: !active, likeCount: item.likeCount + (active ? -1 : 1) }
    : { isLoved: !active, loveCount: item.loveCount + (active ? -1 : 1) });
  try {
    const result = await reactToComment(item.id, type, action);
    patchComment(item.id, {
      likeCount: result.likeCount,
      loveCount: result.loveCount,
      isLiked: result.isLiked,
      isLoved: result.isLoved,
    });
  } catch (error) {
    patchComment(item.id, before);
    handleError(error, '操作失败，请稍后再试');
  }
}

async function submitComment(): Promise<void> {
  if (!requireLogin()) return;
  const content = draft.value.trim();
  if (!content) return;
  submitting.value = true;
  errorTip.value = '';
  try {
    await createComment({ poiId: props.poiId, content, poiName: props.poiName });
    draft.value = '';
    ElMessage({ message: '留言已发布', type: 'success' });
    if (view.value === 'all') {
      await loadPage(1, false);
    } else {
      await loadPreview();
    }
  } catch (error) {
    handleError(error, '发布失败，请稍后再试');
  } finally {
    submitting.value = false;
  }
}

async function removeComment(item: PoiComment): Promise<void> {
  try {
    await ElMessageBox.confirm('删除后其他人将看不到这条留言，确定删除吗？', '删除留言', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    });
  } catch {
    return; // 用户取消
  }
  try {
    await deleteComment(item.id);
    dropComment(item.id);
    stats.value = { ...stats.value, total: Math.max(0, stats.value.total - 1) };
    ElMessage({ message: '已删除', type: 'success' });
  } catch (error) {
    handleError(error, '删除失败，请稍后再试');
  }
}

function openReport(item: PoiComment): void {
  if (!requireLogin()) return;
  errorTip.value = '';
  reportTarget.value = item;
}

async function submitReport(payload: { reasonCode: string; reasonText: string }): Promise<void> {
  const target = reportTarget.value;
  if (!target) return;
  reporting.value = true;
  try {
    const result = await reportComment(target.id, payload.reasonCode, payload.reasonText);
    reportTarget.value = null;
    ElMessage({ message: result.message, type: result.duplicate ? 'info' : 'success' });
    if (result.hidden && !target.isMine) {
      // 达阈值折叠：别人的留言对普通用户不再展示
      dropComment(target.id);
      stats.value = { ...stats.value, total: Math.max(0, stats.value.total - 1) };
    } else {
      patchComment(target.id, { isReported: true, reportCount: result.reportCount });
    }
  } catch (error) {
    handleError(error, '举报失败，请稍后再试');
  } finally {
    reporting.value = false;
  }
}

onMounted(async () => {
  loadPreview();
  // 先给一个静态兜底（限额/举报原因），再确认登录态：两者互不阻塞
  fetchCommentMeta()
    .then(result => { meta.value = result; })
    .catch(() => undefined);
  const state = await refreshSession();
  loginState.value = state;
});
</script>

<style lang="scss" scoped>
.poi-comments {
  --pc-red: var(--ecnu-red, #c20a1c);
  --pc-red-dark: var(--ecnu-red-dark, #a00818);
  --pc-line: #eceef2;
  --pc-ink: #23262b;
  --pc-muted: #8f959e;

  display: flex;
  flex-direction: column;
  width: 276px;
  max-width: min(300px, calc(100vw - 48px));
  font-family: inherit;
  color: var(--pc-ink);
  text-align: left;
}

/* ============ 顶部 ============ */
.pc-top {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--pc-line);
}

.pc-top__title {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;

  h4 {
    margin: 0;
    font-size: 12.5px;
    font-weight: 700;
    letter-spacing: 0.3px;
  }
}

.pc-top__icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--pc-red);
}

.pc-top__poi {
  overflow: hidden;
  color: var(--pc-muted);
  font-size: 11px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pc-top__side {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}

.pc-top__stat {
  color: var(--pc-muted);
  font-size: 11px;
}

.pc-sort {
  display: inline-flex;
  padding: 2px;
  border-radius: 999px;
  background: #f2f3f5;
}

.pc-sort__item {
  padding: 1px 7px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--pc-muted);
  font-size: 10.5px;
  cursor: pointer;
  transition: all 0.18s ease;

  &.is-on {
    background: #fff;
    color: var(--pc-red);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(35, 38, 43, 0.12);
  }
}

/* ============ 主体（固定高度滚动区，保证 InfoWindow 尺寸稳定） ============ */
.pc-body {
  height: 160px;
  margin: 5px 0;
  padding-right: 2px;
  overflow-y: auto;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;

  &::-webkit-scrollbar { width: 4px; }
  &::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: rgba(35, 38, 43, 0.16);
  }
  &::-webkit-scrollbar-track { background: transparent; }
}

.pc-empty {
  margin: 26px 10px;
  color: var(--pc-muted);
  font-size: 11.5px;
  line-height: 1.65;
  text-align: center;
}

.pc-skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 0;
}

.pc-skeleton__row {
  display: block;
  height: 42px;
  border-radius: 10px;
  background: linear-gradient(100deg, #f4f5f7 30%, #eaecef 50%, #f4f5f7 70%);
  background-size: 240% 100%;
  animation: pcShimmer 1.3s ease-in-out infinite;
}

@keyframes pcShimmer {
  0% { background-position: 120% 0; }
  100% { background-position: -120% 0; }
}

.pc-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pc-item {
  display: flex;
  gap: 7px;
  padding: 6px 5px 7px;
  border-radius: 8px;
  transition: background 0.18s ease;

  & + .pc-item { border-top: 1px solid #f5f6f8; }
  &:hover { background: #fafbfc; }
}

.pc-item__avatar {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: linear-gradient(140deg, #c9ced6, #a8aeb8);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 22px;
  text-align: center;
  user-select: none;

  &.is-mine { background: linear-gradient(140deg, var(--ecnu-red-light, #e64553), var(--pc-red)); }
}

.pc-item__main {
  flex: 1;
  min-width: 0;
}

.pc-item__meta {
  display: flex;
  align-items: center;
  gap: 5px;
  line-height: 1.4;
}

.pc-item__name {
  overflow: hidden;
  max-width: 96px;
  color: #4c5058;
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pc-item__tag {
  flex-shrink: 0;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 10px;
  line-height: 15px;

  &--mine { background: rgba(194, 10, 28, 0.1); color: var(--pc-red); }
  &--long { background: #fdf6e3; color: #a8791b; }
}

.pc-item__time {
  margin-left: auto;
  flex-shrink: 0;
  color: #b6bbc3;
  font-size: 11px;
}

.pc-item__content {
  margin: 2px 0 0;
  color: var(--pc-ink);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.pc-folded {
  display: block;
  width: 100%;
  margin-top: 3px;
  padding: 6px 8px;
  border: 1px dashed #dfe2e8;
  border-radius: 7px;
  background: #fafbfc;
  color: var(--pc-muted);
  font-size: 11px;
  text-align: left;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover { border-color: rgba(194, 10, 28, 0.4); color: var(--pc-red); }
}

.pc-folded-hint {
  margin: 5px 0 0;
  padding: 2px 6px;
  border-radius: 6px;
  background: #fdf1f2;
  color: #b0727a;
  font-size: 11px;
}

/* ============ 操作行 ============ */
.pc-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
}

.pc-act {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 1px 6px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--pc-muted);
  font-size: 10.5px;
  line-height: 16px;
  cursor: pointer;
  transition: all 0.18s ease;

  svg { width: 11px; height: 11px; flex-shrink: 0; }

  &:hover:not(:disabled) { background: #f2f3f5; color: #4c5058; }
  &:disabled { cursor: default; opacity: 0.75; }

  &.is-on {
    color: var(--pc-red);
    font-weight: 600;

    svg { fill: currentColor; transform: scale(1.06); }
  }
}

.pc-act--love.is-on { color: #e6356b; }

.pc-act--danger:hover:not(:disabled) { background: #fdf1f2; color: #d03050; }

.pc-act--report { margin-left: auto; }
.pc-act--report:hover:not(:disabled) { color: #d08700; }

.pc-more {
  display: block;
  width: 100%;
  margin: 3px 0 2px;
  padding: 6px 0;
  border: 1px dashed #dfe2e8;
  border-radius: 8px;
  background: #fff;
  color: var(--pc-red);
  font-size: 11.5px;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover:not(:disabled) { border-color: var(--pc-red); background: rgba(194, 10, 28, 0.04); }
  &:disabled { color: var(--pc-muted); cursor: default; }
}

/* ============ 底部发布区 ============ */
.pc-foot {
  padding-top: 6px;
  border-top: 1px solid var(--pc-line);
}

.pc-input {
  box-sizing: border-box;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #e2e5ea;
  border-radius: 8px;
  background: #fbfbfc;
  color: var(--pc-ink);
  font-family: inherit;
  font-size: 11.5px;
  line-height: 1.5;
  resize: none;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;

  &::placeholder { color: #b6bbc3; }

  &:focus {
    border-color: var(--pc-red);
    background: #fff;
    box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.1);
  }
}

.pc-foot__row {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 4px;
}

.pc-counter {
  color: #b6bbc3;
  font-size: 10.5px;

  &.is-warn { color: #a8791b; font-weight: 600; }
}

.pc-submit {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid var(--pc-red);
  border-radius: 999px;
  background: var(--pc-red);
  color: #fff;
  font-size: 11.5px;
  line-height: 1.4;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover:not(:disabled) { background: var(--pc-red-dark); border-color: var(--pc-red-dark); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.pc-login {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0;
  color: var(--pc-muted);
  font-size: 12px;
}

.pc-login__btn {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid var(--pc-red);
  border-radius: 999px;
  background: #fff;
  color: var(--pc-red);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover { background: var(--pc-red); color: #fff; }
}

.pc-error {
  margin: 5px 0 0;
  color: #f56c6c;
  font-size: 11.5px;
  line-height: 1.5;
}

/* ============ 移动端 ============ */
@media (max-width: 768px) {
  .poi-comments {
    width: min(288px, calc(100vw - 40px));
    max-width: none;
  }

  /* 外层 .poi-comment-window 已限高并统一滚动（寄语 + 留言板一起滑），
     这里再给固定高度就会变成"框里再套一个滚动条"，手机上很难操作 */
  .pc-body {
    height: auto;
    min-height: 0;
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .pc-empty { margin: 12px 6px; }

  .pc-item__content { font-size: 13px; }
  .pc-item__name { font-size: 12px; }

  /* 触控热区按项目规范抬到 44px；输入字号 16px 防 iOS 聚焦放大 */
  .pc-act { padding: 0 11px; min-height: 44px; font-size: 12.5px; }
  .pc-act svg { width: 15px; height: 15px; }
  .pc-input { font-size: 16px; }
  .pc-submit { min-height: 44px; padding: 0 18px; font-size: 13px; }
  .pc-more { min-height: 44px; font-size: 13px; }
  .pc-login { min-height: 44px; }
  .pc-login__btn { min-height: 40px; padding: 0 16px; font-size: 13px; }
}
</style>
