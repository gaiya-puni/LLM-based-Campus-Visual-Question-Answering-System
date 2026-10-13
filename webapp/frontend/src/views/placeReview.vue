<template>
  <div class="review-page">
    <header class="page-head">
      <div class="head-text">
        <h2>数据审核</h2>
        <p>
          用户在使用中贡献的地点线索汇总于此。通过只代表"已核实"，
          并入正式地点数据仍走离线生成流程（<code>tools/campus_generator</code>）。
        </p>
      </div>
      <div class="head-actions">
        <button type="button" class="ghost-btn" :disabled="busy" @click="load">刷新</button>
        <button type="button" class="ghost-btn" :disabled="busy || extracting" @click="runExtract">
          {{ extracting ? '研判中…' : '用大模型补齐' }}
        </button>
        <button type="button" class="primary-btn" :disabled="busy || exporting" @click="runExport">
          {{ exporting ? '导出中…' : '导出已通过' }}
        </button>
      </div>
    </header>

    <!-- 审核密钥门禁：后端未配置 USERDATA_REVIEW_TOKEN 时，所有审核接口都会 403 -->
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
        <article v-for="card in statCards" :key="card.status" class="stat-card">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
          <span class="stat-hint">{{ card.hint }}</span>
        </article>
      </section>

      <section class="filter-row">
        <el-select v-model="filters.campus" size="default" class="filter-select" @change="applyFilters">
          <el-option label="全部校区" value="" />
          <el-option v-for="name in CAMPUS_NAMES" :key="name" :label="name" :value="name" />
        </el-select>
        <el-select v-model="filters.status" size="default" class="filter-select" @change="applyFilters">
          <el-option label="待审" value="pending" />
          <el-option label="已通过" value="approved" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="全部状态" value="" />
        </el-select>
        <el-select v-model="filters.source" size="default" class="filter-select" @change="applyFilters">
          <el-option label="全部来源" value="" />
          <el-option v-for="(label, value) in SOURCE_LABELS" :key="value" :label="label" :value="value" />
        </el-select>
        <input
          v-model="filters.keyword"
          class="search-input"
          type="search"
          placeholder="按名称搜索"
          @keyup.enter="applyFilters"
        />
        <span class="filter-total">共 {{ total }} 条</span>
      </section>

      <section class="table-wrap">
        <el-skeleton v-if="loading && !items.length" :rows="6" animated />
        <el-table
          v-else
          v-loading="loading"
          :data="items"
          size="small"
          highlight-current-row
          class="review-table"
          empty-text="暂无待确认地点，继续收集用户反馈即可"
          @row-click="openDetail"
        >
          <el-table-column prop="name" label="名称" min-width="150" />
          <el-table-column prop="campus" label="校区" width="110">
            <template #default="{ row }">{{ row.campus || '—' }}</template>
          </el-table-column>
          <el-table-column label="来源" width="118">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceTagType(row.source)" effect="plain">
                {{ SOURCE_LABELS[row.source] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="!narrow" label="置信度" width="120">
            <template #default="{ row }">
              <div class="conf-cell">
                <span class="conf-bar"><i :style="{ width: `${Math.round(row.confidence * 100)}%` }"></i></span>
                <span class="conf-value">{{ row.confidence.toFixed(2) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="提及" width="86">
            <template #default="{ row }">
              <span class="mentions" :class="{ hot: row.mentions >= 3 }">{{ row.mentions }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="!narrow" label="首次出现" width="150">
            <template #default="{ row }">{{ pretty(row.firstSeenAt) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="96">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTagType(row.status)" effect="dark">
                {{ STATUS_LABELS[row.status] }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          class="pager"
          layout="prev, pager, next, total"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPageChange"
        />
      </section>

      <p v-if="stats" class="stats-line">
        反馈 {{ stats.ratingsTotal }} 条 · 上报 {{ stats.reportsTotal }} 条 ·
        待研判问句 {{ stats.unresolvedPending }} 条 · 抽取模式 {{ extractionMode || 'batch' }}
      </p>
    </template>

    <PlaceReviewDrawer
      v-model="drawerVisible"
      :item="activeItem"
      :busy="reviewing"
      :error-tip="reviewError"
      @review="onReview"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PlaceReviewDrawer from '../components/PlaceReviewDrawer.vue';
import { CAMPUS_NAMES } from '../campusConfig';
import {
  SOURCE_LABELS, STATUS_LABELS, exportApproved, fetchPending, getReviewToken,
  reviewCandidate, runLlmExtract, setReviewToken,
  type CandidateSource, type CandidateStatus, type PendingItem,
} from '../api/userdata';

const items = ref<PendingItem[]>([]);
const counts = reactive<Record<CandidateStatus, number>>({ pending: 0, approved: 0, rejected: 0 });
const stats = ref<any>(null);
const extractionMode = ref('batch');
const total = ref(0);
const page = ref(1);
const pageSize = ref(50);
const loading = ref(false);
const extracting = ref(false);
const exporting = ref(false);
const tokenRequired = ref(!getReviewToken());
const tokenInput = ref('');
const tokenError = ref('');
const drawerVisible = ref(false);
const activeItem = ref<PendingItem | null>(null);
const reviewing = ref(false);
const reviewError = ref('');
const narrow = ref(false);

const filters = reactive({ campus: '', status: 'pending', source: '', keyword: '' });

const busy = computed(() => loading.value || extracting.value || exporting.value);

const statCards = computed(() => [
  { status: 'pending', label: '待审', value: counts.pending, hint: '等待人工确认' },
  { status: 'approved', label: '已通过', value: counts.approved, hint: '可导出并入数据' },
  { status: 'rejected', label: '已驳回', value: counts.rejected, hint: '已排除的线索' },
]);

const pretty = (value?: string) => (value || '').replace('T', ' ');

const sourceTagType = (source: CandidateSource) => {
  if (source === 'user') return 'danger';
  return source === 'llm' ? 'primary' : 'info';
};

const statusTagType = (status: CandidateStatus) => {
  if (status === 'approved') return 'success';
  return status === 'rejected' ? 'danger' : 'warning';
};

const load = async () => {
  if (tokenRequired.value) return;
  loading.value = true;
  try {
    const result = await fetchPending({
      campus: filters.campus,
      status: filters.status,
      source: filters.source,
      q: filters.keyword.trim(),
      page: page.value,
      pageSize: pageSize.value,
    });
    items.value = result.items;
    total.value = result.total;
    Object.assign(counts, result.counts);
    stats.value = result.stats;
    extractionMode.value = result.extractionMode;
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

const applyFilters = () => { page.value = 1; load(); };
const onPageChange = (next: number) => { page.value = next; load(); };

const saveToken = () => {
  setReviewToken(tokenInput.value);
  tokenInput.value = '';
  tokenRequired.value = false;
  tokenError.value = '';
  load();
};

const openDetail = (row: PendingItem) => {
  activeItem.value = row;
  reviewError.value = '';
  drawerVisible.value = true;
};

const onReview = async ({ status, note }: { status: CandidateStatus; note: string }) => {
  const item = activeItem.value;
  if (!item) return;
  reviewing.value = true;
  reviewError.value = '';
  try {
    const result = await reviewCandidate(item.id, status, note);
    item.status = status;
    item.note = note;
    Object.assign(counts, result.counts);
    ElMessage({ message: status === 'approved' ? '已通过' : '已驳回', type: 'success', duration: 1800 });
  } catch (error) {
    reviewError.value = error instanceof Error ? error.message : '操作失败';
  } finally {
    reviewing.value = false;
  }
};

const runExtract = async () => {
  extracting.value = true;
  try {
    const result = await runLlmExtract(20);
    ElMessage({
      message: result.processed
        ? `已研判 ${result.processed} 条问句，新增 ${result.added} 个候选`
        : (result.message || '没有待研判的问句'),
      type: 'success',
      duration: 3000,
    });
    await load();
  } catch (error) {
    ElMessage({ message: error instanceof Error ? error.message : '研判失败', type: 'error', duration: 3000 });
  } finally {
    extracting.value = false;
  }
};

const runExport = async () => {
  exporting.value = true;
  try {
    const result = await exportApproved();
    ElMessageBox.alert(
      `已导出 ${result.approved} 条（跳过 ${result.skipped} 条缺坐标的条目）。\n\n`
      + `POI 契约文件：${result.file}\nMarkdown 汇总：${result.markdown}\n\n`
      + '下一步：交给 tools/campus_generator/normalize.py 的人工确认流程并入正式数据。',
      '导出完成',
      { confirmButtonText: '知道了' },
    );
    await load();
  } catch (error) {
    ElMessage({ message: error instanceof Error ? error.message : '导出失败', type: 'error', duration: 3000 });
  } finally {
    exporting.value = false;
  }
};

const syncNarrow = () => { narrow.value = window.innerWidth < 1280; };

onMounted(() => {
  syncNarrow();
  window.addEventListener('resize', syncNarrow);
  load();
});

onUnmounted(() => {
  window.removeEventListener('resize', syncNarrow);
});
</script>

<style lang="scss" scoped>
.review-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 20px 32px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.06);
}

.page-head {
  display: flex;
  align-items: flex-start;
  gap: 16px;

  .head-text {
    h2 { margin: 0; color: #303133; font-size: 20px; font-weight: 600; }
    p {
      max-width: 620px;
      margin: 6px 0 0;
      color: #909399;
      font-size: 13px;
      line-height: 1.6;

      code { padding: 1px 5px; border-radius: 4px; background: #f5f6f8; color: #c20a1c; }
    }
  }

  .head-actions {
    display: flex;
    gap: 10px;
    margin-left: auto;
    flex-shrink: 0;
  }
}

.ghost-btn,
.primary-btn {
  padding: 8px 16px;
  border-radius: 10px;
  font-size: 13px;
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

.token-gate {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px;
  border-left: 4px solid #c20a1c;
  border-radius: 10px;
  background: #fdf5f6;

  h3 { margin: 0; color: #c20a1c; font-size: 15px; font-weight: 600; }
  p { margin: 0; color: #606266; font-size: 13px; line-height: 1.6; }

  .gate-row { display: flex; gap: 10px; }

  .gate-input {
    flex: 1;
    padding: 8px 10px;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    font-size: 13px;
    outline: none;

    &:focus { border-color: #c20a1c; box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12); }
  }
}

.hint { color: #909399 !important; font-size: 12px !important; }

.stat-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  background: #fff;
  transition: transform 0.18s ease, box-shadow 0.18s ease;

  &:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0, 0, 0, 0.07); }

  .stat-label { color: #909399; font-size: 13px; }
  .stat-value { color: #c20a1c; font-size: 28px; font-weight: 700; line-height: 1.2; }
  .stat-hint { color: #c0c4cc; font-size: 12px; }
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;

  .filter-select { width: 140px; }

  .search-input {
    width: 220px;
    padding: 8px 12px;
    border: 1px solid #dcdfe6;
    border-radius: 8px;
    font-size: 13px;
    outline: none;

    &:focus { border-color: #c20a1c; box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12); }
  }

  .filter-total { margin-left: auto; color: #909399; font-size: 13px; }
}

.table-wrap {
  :deep(.el-table__row) { cursor: pointer; height: 44px; }
  :deep(.el-table__row:hover > td) { background: #fafafb; }
}

.conf-cell {
  display: flex;
  align-items: center;
  gap: 8px;

  .conf-bar {
    display: inline-block;
    width: 54px;
    height: 6px;
    border-radius: 3px;
    background: #f0f2f5;

    i { display: block; height: 100%; border-radius: 3px; background: #c20a1c; }
  }

  .conf-value { color: #909399; font-size: 12px; font-variant-numeric: tabular-nums; }
}

.mentions {
  display: inline-block;
  min-width: 26px;
  padding: 1px 8px;
  border-radius: 999px;
  background: #f5f6f8;
  color: #606266;
  font-size: 12px;
  text-align: center;

  &.hot { background: rgba(103, 194, 58, 0.14); color: #529b2e; font-weight: 600; }
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.stats-line {
  margin: 0;
  color: #c0c4cc;
  font-size: 12px;
}
</style>
