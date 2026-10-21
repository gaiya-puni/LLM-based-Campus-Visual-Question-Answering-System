<template>
  <div class="campus-build-page">
    <header class="page-header">
      <div>
        <h2>校园热力图生成</h2>
        <p>从自然语言发现校区和主题地点，植物、运动场、体育馆、河岸、亭子等都可参与热图。</p>
      </div>
      <el-tag v-if="job" :type="statusType(job.status)" effect="dark">{{ statusLabel(job.status) }}</el-tag>
    </header>

    <section class="request-card">
      <el-input
        v-model="query"
        type="textarea"
        :rows="2"
        maxlength="240"
        show-word-limit
        placeholder="例如：帮我做XX大学XX校区的约会热力图"
      />
      <div class="request-options">
        <el-input v-model="webUrlsText" placeholder="补充公开网页 URL（可选，多个用换行分隔）" />
        <el-button type="primary" :loading="submitting" :disabled="!query.trim()" @click="submit">
          开始发现
        </el-button>
      </div>
      <div class="source-options">
        <el-checkbox v-model="includeAmap">搜索高德 POI</el-checkbox>
        <el-checkbox v-model="includeWeb">采集公开网页</el-checkbox>
        <el-checkbox v-model="autoSearch" :disabled="!includeWeb">自动搜索公开网页</el-checkbox>
      </div>
      <div class="review-token-row">
        <el-input
          v-model="reviewToken"
          type="password"
          show-password
          autocomplete="off"
          placeholder="管理员令牌（仅正式审核/发布时填写，可留空）"
          @input="saveReviewToken"
        />
        <span>普通用户无需令牌即可搜索、筛选并生成自己的临时热力图；令牌只用于管理员正式审核和发布。</span>
      </div>
      <div class="resume-row">
        <el-input v-model="jobInput" size="small" placeholder="已有任务 ID，可直接载入" />
        <el-button size="small" :disabled="!jobInput.trim()" @click="loadExisting">载入任务</el-button>
      </div>
      <p v-if="error" class="error">{{ safeError }}</p>
    </section>

    <section v-if="job" class="job-card">
      <div class="job-summary">
        <div><span>任务</span><code>{{ job.id }}</code></div>
        <div><span>阶段</span>{{ stageLabel(job.stage) }}</div>
        <div><span>候选</span>{{ job.candidateCount ?? 0 }}</div>
        <div><span>已选</span>{{ selectedPreviewIds.length }}</div>
        <div><span>正式通过</span>{{ review?.approved.length ?? job.approvedCount ?? 0 }}</div>
        <div v-if="job.theme"><span>主题</span>{{ themeLabel(job.theme) }}</div>
        <div v-if="job.webSearch?.requested">
          <span>网页搜索</span>{{ webSearchLabel(job.webSearch.status) }} · {{ job.webSearch.acceptedUrlCount }} 页
        </div>
      </div>
      <el-progress v-if="job.status === 'queued' || job.status === 'running'" :percentage="progress" :indeterminate="job.status === 'running'" />
      <p v-if="job.error" class="error">任务失败：{{ redactDiagnostic(job.error) }}</p>
      <div v-if="hasDiscoveryDetails" class="discovery-details" aria-label="校区解析结果">
        <strong>解析结果</strong>
        <dl>
          <div><dt>学校</dt><dd>{{ resolvedDiscovery.school || '未返回' }}</dd></div>
          <div><dt>校区</dt><dd>{{ resolvedDiscovery.campus || '未返回' }}</dd></div>
          <div><dt>方式</dt><dd>{{ discoveryMethodLabel(resolvedDiscovery.method) }}</dd></div>
          <div v-if="resolvedDiscovery.center"><dt>中心</dt><dd>{{ resolvedDiscovery.center }}</dd></div>
          <div v-if="resolvedDiscovery.centerSource">
            <dt>中心来源</dt><dd>{{ centerSourceLabel(resolvedDiscovery.centerSource) }}</dd>
          </div>
          <div v-if="resolvedDiscovery.radius"><dt>搜索半径</dt><dd>{{ resolvedDiscovery.radius }}</dd></div>
          <div v-if="resolvedDiscovery.cleanedQuery"><dt>清洗后请求</dt><dd>{{ resolvedDiscovery.cleanedQuery }}</dd></div>
          <div v-if="resolvedDiscovery.address"><dt>地址</dt><dd>{{ resolvedDiscovery.address }}</dd></div>
        </dl>
        <p v-if="resolvedDiscovery.warning" class="discovery-warning">{{ resolvedDiscovery.warning }}</p>
      </div>
      <div v-if="showZeroCandidateHelp" class="zero-candidate-help" role="alert">
        <strong>未发现可用地点</strong>
        <p>请先核对上方解析出的学校和校区是否正确，再按以下方式重试：</p>
        <ul>
          <li>输入准确的学校与校区名称，例如“XX大学XX校区拍照热力图”。</li>
          <li>保持“搜索高德 POI”开启，并确认后端已配置高德 Web 服务 Key；若提示请求限流，请稍后重试。</li>
          <li>网页搜索只能提供地点线索，最终地点仍需可解析的名称与坐标；也可补充学校官网的地点介绍页。</li>
        </ul>
      </div>
      <details v-if="job.webSearch?.results.length" class="search-results">
        <summary>查看自动发现的网页（{{ job.webSearch.results.length }}）</summary>
        <ol>
          <li v-for="item in job.webSearch.results" :key="item.url">
            <a :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title || item.url }}</a>
            <small v-if="item.query">查询：{{ item.query }}</small>
          </li>
        </ol>
      </details>
    </section>

    <section v-if="review" class="workspace">
      <div class="candidate-panel">
        <div class="panel-head">
          <div>
            <h3>{{ review.school }} · {{ review.campus }}</h3>
            <p>勾选要加入本次热力图的地点；你的选择只保存在当前任务，不会修改全站正式数据。</p>
          </div>
          <el-button size="small" @click="reloadReview" :loading="loadingReview">刷新</el-button>
        </div>
        <div class="review-stats">
          <el-tag type="warning">待审 {{ pendingItems.length }}</el-tag>
          <el-tag type="primary">已选 {{ selectedPreviewIds.length }}</el-tag>
          <el-tag type="success">正式通过 {{ review.approved.length }}</el-tag>
          <el-tag type="info">无效 {{ review.pendingInvalid.length }}</el-tag>
        </div>
        <!-- 勾选之前先摊开本次采集的质量/类别/距离概况，只读，不参与审核与生成。 -->
        <CandidateDataSummary
          :candidates="review.candidates"
          :discovery="review.profile?.discovery"
        />
        <div v-if="pendingItems.length" class="batch-review-toolbar">
          <el-checkbox
            :model-value="allPendingSelected"
            :indeterminate="somePendingSelected"
            @change="toggleAllPending"
          >全选候选</el-checkbox>
          <span>已选 {{ selectedPreviewIds.length }} 个</span>
          <el-button
            size="small"
            type="primary"
            :disabled="selectedPreviewIds.length < 3"
            :loading="previewing"
            @click="buildPreview"
          >生成我的临时热力图</el-button>
          <template v-if="reviewToken.trim().length >= 16">
          <el-button
            size="small"
            type="primary"
            :disabled="!batchSelectedIds.length"
            :loading="batchReviewing"
            @click="batchDecide('approve')"
          >批量通过并导入</el-button>
          <el-button
            size="small"
            :disabled="!batchSelectedIds.length"
            :loading="batchReviewing"
            @click="batchDecide('reject')"
          >批量拒绝</el-button>
          </template>
        </div>
        <div v-if="!reviewQueue.length" class="empty">暂无待审核地点，请检查采集来源或任务警告。</div>
        <div v-for="group in candidateGroups" :key="group.key" class="candidate-group">
          <div class="candidate-group-head" @click="toggleGroup(group.key)">
            <span class="group-caret" :class="{ open: group.open }">▸</span>
            <strong>{{ group.label }}</strong>
            <span class="group-count">{{ group.items.length }}</span>
          </div>
          <template v-if="group.open">
            <article
              v-for="item in group.items"
              :key="item.id"
              class="candidate-item"
              :class="{ selected: selected?.id === item.id, approved: item.reviewStatus === 'approved', rejected: item.reviewStatus === 'rejected' }"
              @click="selectCandidate(item)"
            >
              <div class="candidate-title">
                <div class="candidate-name">
                  <el-checkbox
                    :model-value="selectedPreviewIds.includes(item.id)"
                    :aria-label="`选择 ${item.name}`"
                    @click.stop
                    @change="value => togglePreviewCandidate(item.id, Boolean(value))"
                  />
                  <strong>{{ item.name }}</strong>
                </div>
                <el-tag size="small" :type="reviewTagType(item.reviewStatus)">{{ reviewLabel(item.reviewStatus) }}</el-tag>
              </div>
              <p>
                {{ item.subCategory }} · {{ item.lng.toFixed(5) }}, {{ item.lat.toFixed(5) }}
                <span v-if="distanceOf(item) !== null"> · 距中心 {{ distanceOf(item) }} m</span>
              </p>
              <div class="candidate-meta">
                <span>{{ sourceLabel(item.source) }}</span>
                <span>{{ qualityLabel(item) }} {{ qualityConfidence(item).toFixed(2) }}</span>
              </div>
              <div v-if="item.userAdded" class="user-poi-actions">
                <el-tag size="small" type="warning" effect="plain">我添加</el-tag>
                <el-button
                  link
                  type="danger"
                  size="small"
                  :loading="removingUserPoiId === item.id"
                  @click.stop="removeUserPoi(item)"
                >删除</el-button>
              </div>
            </article>
          </template>
        </div>
      </div>

      <div class="map-panel">
        <div ref="mapEl" class="map"></div>
        <div class="map-toolbar">
          <el-button size="small" :disabled="previewing || (selectedPreviewIds.length < 3)" :loading="previewing" type="primary" @click="buildPreview">
            {{ previewing ? '构建预览中…' : previewReady ? '重新生成临时热图' : '生成临时热图' }}
          </el-button>
          <el-button size="small" :type="addMode ? 'warning' : undefined" @click="toggleAddMode">
            {{ addMode ? '点击地图选位置…' : '手动补点' }}
          </el-button>
          <el-button v-if="reviewToken.trim().length >= 16" size="small" :disabled="publishing || !review.approved.length" :loading="publishing" @click="showPublishPlan">
            查看正式发布变更
          </el-button>
          <el-select v-model="previewScene" size="small" :disabled="!previewReady" @change="loadPreview">
            <el-option v-for="scene in scenes" :key="scene.id" :label="scene.name" :value="scene.id" />
          </el-select>
          <el-select v-if="previewScene === 'photo' || previewScene === 'flower_viewing'" v-model="previewSeason" size="small" :disabled="!previewReady" @change="loadPreview">
            <el-option v-for="season in seasons" :key="season.id" :label="season.name" :value="season.id" />
          </el-select>
          <el-checkbox v-model="showPreviewPoints" size="small">显示已选点位</el-checkbox>
        </div>
        <div v-if="previewReady" class="map-legend">
          <div class="legend-bar" :style="{ background: rampCss }"></div>
          <div class="legend-labels"><span>相对较低</span><span>相对较高</span></div>
          <p class="legend-note">热力强度来自点位分布与场景相关度，与质量分无关；边界为数据覆盖范围，通行情况未核实。</p>
        </div>
        <p v-if="addMode" class="map-hint add-hint">在地图上点击系统漏掉的地点即可补点；补点只进入你自己的临时热图，不会写入正式数据。</p>
        <p v-if="!previewReady" class="map-hint">已选择 {{ selectedPreviewIds.length }} 个地点；至少选择 3 个后即可生成你的临时热力图。</p>
        <p v-else class="map-hint preview-success">临时热图已生成，待审核标记已隐藏，可切换场景和季节查看。</p>
        <p v-if="publishPlan" class="publish-hint">正式发布预览：植物新增 {{ publishPlan.plantAdded.length }} / 修改 {{ publishPlan.plantUpdated.length }}，场景新增 {{ publishPlan.sceneAdded.length }} / 修改 {{ publishPlan.sceneUpdated.length }}；发布后需重建索引和热图缓存。</p>
      </div>

      <el-dialog
        v-model="addDialogVisible"
        title="手动补一个地点"
        :width="isMobile ? '92%' : '460px'"
        append-to-body
      >
        <el-form label-position="top">
          <el-form-item label="地点名称（必填）">
            <el-input v-model="addForm.name" maxlength="40" show-word-limit placeholder="例如：思源湖西岸草坪" />
          </el-form-item>
          <div class="coord-fields">
            <el-form-item label="经度">
              <el-input-number v-model="addForm.lng" :precision="6" :step="0.0001" controls-position="right" />
            </el-form-item>
            <el-form-item label="纬度">
              <el-input-number v-model="addForm.lat" :precision="6" :step="0.0001" controls-position="right" />
            </el-form-item>
          </div>
          <el-form-item label="类别">
            <el-radio-group v-model="addForm.category">
              <el-radio value="scene">场景</el-radio>
              <el-radio value="plant">植物</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="类型">
            <el-select
              v-model="addForm.subCategory"
              filterable
              allow-create
              default-first-option
              placeholder="可输入自定义类型"
              style="width: 100%"
            >
              <el-option v-for="item in userPoiTypes" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="用于场景">
            <el-checkbox-group v-model="addForm.scenes">
              <el-checkbox v-for="scene in userPoiScenes" :key="scene.id" :value="scene.id">{{ scene.name }}</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="补充说明（可选）">
            <el-input v-model="addForm.text" type="textarea" :rows="2" maxlength="200" show-word-limit placeholder="例如：树下有长椅，傍晚光线好" />
          </el-form-item>
        </el-form>
        <p class="user-poi-note">
          补点只用于你本次的临时热图，不会写入全站正式数据；管理员在审核面板通过后才会进入正式热图。
        </p>
        <template #footer>
          <el-button @click="addDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="adding" :disabled="!addForm.name.trim()" @click="submitUserPoi">加入本次热图</el-button>
        </template>
      </el-dialog>

      <el-drawer v-model="drawerVisible" title="候选地点审核" :size="drawerSize">
        <template v-if="selected">
          <el-form label-position="top">
            <el-form-item label="地点名称"><el-input v-model="edit.name" /></el-form-item>
            <el-form-item label="位置描述"><el-input v-model="edit.locationName" /></el-form-item>
            <div class="coord-fields">
              <el-form-item label="经度"><el-input-number v-model="edit.lng" :precision="6" :step="0.0001" /></el-form-item>
              <el-form-item label="纬度"><el-input-number v-model="edit.lat" :precision="6" :step="0.0001" /></el-form-item>
            </div>
          </el-form>
          <section class="quality-review">
            <div class="quality-heading">
              <strong>质量评分</strong>
              <span>{{ hasQuality(selected) ? '总质量分' : '旧版置信度' }} {{ qualityConfidence(selected).toFixed(2) }}</span>
            </div>
            <template v-if="qualityScoreEntries.length || riskScoreEntries.length">
              <div v-if="qualityScoreEntries.length" class="score-group">
                <div v-for="score in qualityScoreEntries" :key="score.key" class="score-row">
                  <span>{{ scoreLabel(score.key) }}</span>
                  <el-progress
                    :percentage="scorePercent(score.value)"
                    :stroke-width="8"
                    :show-text="false"
                    :color="scoreColor(score.value, false)"
                  />
                  <b>{{ score.value.toFixed(2) }}</b>
                </div>
              </div>
              <div v-if="riskScoreEntries.length" class="score-group risk-group">
                <div class="score-group-title">风险（越低越好）</div>
                <div v-for="score in riskScoreEntries" :key="score.key" class="score-row">
                  <span>{{ scoreLabel(score.key) }}</span>
                  <el-progress
                    :percentage="scorePercent(score.value)"
                    :stroke-width="8"
                    :show-text="false"
                    :color="scoreColor(score.value, true)"
                  />
                  <b>{{ score.value.toFixed(2) }}</b>
                </div>
              </div>
            </template>
            <p v-else class="legacy-quality">旧候选暂无分项评分，仅保留总置信度。</p>
            <div class="confidence-reasons">
              <strong>评分理由</strong>
              <ul v-if="confidenceReasons.length">
                <li v-for="(reason, index) in confidenceReasons" :key="index">{{ reason }}</li>
              </ul>
              <p v-else>暂无评分理由，请结合来源与证据人工复核。</p>
            </div>
          </section>
          <dl class="evidence">
            <div><dt>来源</dt><dd>{{ sourceLabel(selected.source) }}</dd></div>
            <div v-if="sourceConfidence(selected) !== undefined"><dt>来源置信度</dt><dd>{{ sourceConfidence(selected)?.toFixed(2) }}</dd></div>
            <div><dt>证据</dt><dd>{{ evidenceText }}</dd></div>
          </dl>
          <div v-if="reviewToken.trim().length >= 16" class="drawer-actions">
            <el-button :loading="reviewing" @click="decide('reject')">拒绝</el-button>
            <el-button :loading="reviewing" @click="decide('pending')">保留待审</el-button>
            <el-button type="primary" :loading="reviewing" @click="decide('approve')">通过</el-button>
          </div>
        </template>
      </el-drawer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { loadAMap } from '../amap';
import { useIsMobile } from '../composables/useIsMobile';
import {
  addCampusUserPoi, removeCampusUserPoi,
  getCampusBuild, getCampusPreview, getCampusReview, submitCampusDiscovery,
  submitCampusPreview, submitCampusSelection, submitCampusReview, getCampusPublishPlan, publishCampusBuild,
  type BuildJob, type CandidatePoi, type PreviewPlace, type ReviewBundle, type ReviewStatus, type PublishPlan,
  type UserPoiPayload,
} from '../api/campusBuild';
import { getReviewToken, setReviewToken } from '../api/userdata';
import CandidateDataSummary from '../components/CandidateDataSummary.vue';
import {
  HEATMAP_LAYER_OPACITY,
  PREVIEW_HEATMAP_OPTIONS,
  heatmapRampCss,
  renderHeatmapCanvas,
} from '../components/heatmapRender';

/** 与栅格同源的图例色带（此前这个页面没有任何图例，颜色含义无从得知）。 */
const rampCss = heatmapRampCss();

const isMobile = useIsMobile();

const query = ref('');
const webUrlsText = ref('');
const includeAmap = ref(true);
const includeWeb = ref(true);
const autoSearch = ref(true);
const reviewToken = ref(getReviewToken());
const jobInput = ref('');
const job = ref<BuildJob | null>(null);
const review = ref<ReviewBundle | null>(null);
const selected = ref<CandidatePoi | null>(null);
const error = ref('');
const submitting = ref(false);
const loadingReview = ref(false);
const reviewing = ref(false);
const batchReviewing = ref(false);
const batchSelectedIds = ref<string[]>([]);
const selectedPreviewIds = ref<string[]>([]);
const previewing = ref(false);
const previewReady = ref(false);
const previewScene = ref('walk');
const previewSeason = ref('autumn');
/** 预览生成后默认隐藏候选点，勾选后可对照“点位 ↔ 热斑”是否对齐。 */
const showPreviewPoints = ref(false);
const previewPlaces = ref<PreviewPlace[]>([]);
const publishPlan = ref<PublishPlan | null>(null);
const publishing = ref(false);
const drawerVisible = ref(false);
/** 手动补点：进入该模式后，下一次地图点击会带上坐标并打开表单。 */
const addMode = ref(false);
const addDialogVisible = ref(false);
const adding = ref(false);
const removingUserPoiId = ref('');
const addForm = reactive({
  name: '',
  lng: 0,
  lat: 0,
  category: 'scene' as 'plant' | 'scene',
  subCategory: '用户标注',
  scenes: ['walk', 'photo'] as string[],
  text: '',
});
const mapEl = ref<HTMLElement | null>(null);
/** 抽屉宽度：手机端铺满屏幕，桌面端保持原有 380px */
const drawerSize = computed(() => (isMobile.value ? '100%' : '380px'));
const edit = reactive({ name: '', locationName: '', lng: 0, lat: 0 });
const progress = computed(() => job.value?.status === 'queued' ? 12 : 62);
const pendingItems = computed(() => review.value?.candidates.filter(item => item.reviewStatus === 'pending') || []);
const pendingIdSet = computed(() => new Set(pendingItems.value.map(item => item.id)));
const allPendingSelected = computed(() => (
  (review.value?.candidates.length || 0) > 0
  && (review.value?.candidates || []).every(item => selectedPreviewIds.value.includes(item.id))
));
const somePendingSelected = computed(() => (
  selectedPreviewIds.value.length > 0 && !allPendingSelected.value
));
const resolvedDiscovery = computed(() => {
  const discovery = job.value?.discovery || review.value?.profile?.discovery || {};
  const campus = review.value?.profile?.campus;
  const center = discovery.center || campus?.center;
  const radius = Number(discovery.radiusMeters ?? campus?.trustRadiusM);
  return {
    school: discovery.resolvedSchool || review.value?.profile?.school?.name || review.value?.school || '',
    campus: discovery.resolvedCampus || campus?.name || review.value?.campus || '',
    method: discovery.method || '',
    cleanedQuery: discovery.cleanedQuery || '',
    centerSource: discovery.centerSource || '',
    center: Array.isArray(center) && center.length === 2
      ? `${center[0].toFixed(5)}, ${center[1].toFixed(5)}` : '',
    radiusMeters: Number.isFinite(radius) && radius > 0 ? radius : 0,
    radius: Number.isFinite(radius) && radius > 0
      ? `${Math.round(radius)} m（约 ${(radius / 1000).toFixed(1)} km）` : '',
    address: discovery.address || '',
    warning: discovery.warning || '',
  };
});
const hasDiscoveryDetails = computed(() => Object.values(resolvedDiscovery.value).some(Boolean));
const redactDiagnostic = (value: unknown) => String(value || '')
  .replace(/([?&](?:key|token|api_key|access_token)=)[^&\s]+/gi, '$1[凭证已隐藏]')
  .replace(/((?:api[_ -]?key|access[_ -]?token|authorization)\s*[:=]\s*["']?)[^\s"',;&]+/gi, '$1[凭证已隐藏]')
  .replace(/(bearer\s+)[a-z0-9._~+\/-]+=*/gi, '$1[凭证已隐藏]')
  .replace(/\b(?:tvly|sk)-[a-z0-9_-]{12,}\b/gi, '[凭证已隐藏]')
  .trim();
const safeError = computed(() => redactDiagnostic(error.value));
const showZeroCandidateHelp = computed(() => (
  ['completed', 'blocked'].includes(job.value?.status || '')
  && (job.value?.normalizedCount ?? job.value?.candidateCount ?? 0) === 0
));
const reviewQueue = computed(() => {
  const items = review.value?.candidates || [];
  return selected.value && !items.some(item => item.id === selected.value?.id)
    ? [...items, selected.value]
    : items;
});
/** 校园相关类型默认展开；商业与配套类型默认折叠，避免几百个候选一次铺满。 */
const EXPANDED_CATEGORY_GROUPS = new Set([
  '科教文化服务', '商务住宅', '体育休闲服务', '风景名胜', '医疗保健服务',
]);
type CandidateGroup = { key: string; label: string; items: CandidatePoi[]; open: boolean };
/** 用户自己补的点单独成组，避免混进采集来源的分类里被误当成系统发现。 */
const USER_POI_GROUP = '我的添加';
/** 只记录用户手动改动过的分组，未出现的分组按类型默认开合。 */
const groupOpenState = ref<Record<string, boolean>>({});
const candidateGroups = computed<CandidateGroup[]>(() => {
  const buckets = new Map<string, CandidatePoi[]>();
  for (const item of reviewQueue.value) {
    const key = item.userAdded
      ? USER_POI_GROUP
      : (String(item.subCategory || '').trim() || '未分类');
    const list = buckets.get(key) || [];
    list.push(item);
    buckets.set(key, list);
  }
  return [...buckets.entries()]
    .map(([key, items]) => ({
      key,
      label: key,
      items,
      open: groupOpenState.value[key]
        ?? (key === USER_POI_GROUP || EXPANDED_CATEGORY_GROUPS.has(key)),
    }))
    .sort((left, right) => (
      Number(right.key === USER_POI_GROUP) - Number(left.key === USER_POI_GROUP)
      || Number(!EXPANDED_CATEGORY_GROUPS.has(left.key)) - Number(!EXPANDED_CATEGORY_GROUPS.has(right.key))
      || right.items.length - left.items.length
      || left.key.localeCompare(right.key, 'zh-Hans-CN')
    ));
});
const toggleGroup = (key: string) => {
  const group = candidateGroups.value.find(item => item.key === key);
  groupOpenState.value = { ...groupOpenState.value, [key]: !(group?.open ?? false) };
};
const evidenceText = computed(() => {
  const evidence = selected.value?.evidence;
  if (!evidence) return '暂无结构化证据';
  return Object.values(evidence).filter(Boolean).join('；');
});
type ScoreEntry = { key: string; value: number };
const allScoreEntries = computed<ScoreEntry[]>(() => {
  const candidate = selected.value;
  if (!candidate) return [];
  return Object.entries(candidate.quality?.scores || candidate.scores || {})
    .filter((entry): entry is [string, number] => Number.isFinite(entry[1]))
    .map(([key, value]) => ({ key, value }));
});
const confidenceReasons = computed(() => selected.value?.quality?.confidenceReasons || selected.value?.confidenceReasons || []);
const isRiskScore = (key: string) => /risk/i.test(key);
const qualityScoreEntries = computed(() => allScoreEntries.value.filter(entry => !isRiskScore(entry.key)));
const riskScoreEntries = computed(() => allScoreEntries.value.filter(entry => isRiskScore(entry.key)));

const scenes = [
  { id: 'density', name: '点位密度' },
  { id: 'walk', name: '散步休息' }, { id: 'date', name: '浪漫约会' },
  { id: 'photo', name: '拍照' }, { id: 'flower_viewing', name: '赏花' },
];
const seasons = [
  { id: 'spring', name: '春季' }, { id: 'summer', name: '夏季' },
  { id: 'autumn', name: '秋季' }, { id: 'winter', name: '冬季' },
];
/** 补点表单的可选项：只列校园里最常见、最能被热图吸收的类型，仍允许自定义输入。 */
const userPoiTypes = [
  '湖泊', '河岸', '草坪', '亭子', '桥', '广场', '校门', '雕塑',
  '花园', '建筑', '运动场', '体育场', '纪念馆', '食堂', '咖啡',
];
const userPoiScenes = [
  { id: 'walk', name: '散步休息' }, { id: 'date', name: '浪漫约会' },
  { id: 'photo', name: '拍照' }, { id: 'flower_viewing', name: '赏花' },
];
let timer: number | null = null;
let map: any = null;
let markers: any[] = [];
let peakMarkers: any[] = [];
let scopeCircle: any = null;
let heatLayer: any = null;

const statusLabel = (value?: string) => ({ queued: '排队中', running: '采集中', completed: '待审核', blocked: '已阻塞', failed: '失败' }[value || ''] || value || '未知');
const webSearchLabel = (value?: string) => ({ not_requested: '未请求', unconfigured: '未配置', completed: '已完成', empty: '无结果', failed: '失败' }[value || ''] || value || '未知');
const stageLabel = (value?: string) => ({ harvest: '采集来源', normalize: '整理候选', complete: '等待审核', failed: '失败' }[value || ''] || value || '准备中');
const discoveryMethodLabel = (value?: string) => ({ local_config: '本地校区配置', amap_geocode: '高德地理编码' }[value || ''] || value || '未返回');
const centerSourceLabel = (value?: string) => ({ local_config: '本地校区配置', amap_poi: '高德 POI 校正', amap_geocode: '高德地理编码' }[value || ''] || value || '未返回');
const distanceOf = (item: CandidatePoi) => {
  const value = Number((item.evidence as Record<string, unknown> | undefined)?.distanceMeters);
  return Number.isFinite(value) ? Math.round(value) : null;
};
const themeLabel = (value?: string) => ({ walk: '散步', date: '约会', photo: '拍照', flower: '赏花', study: '学习', food: '餐饮', general: '综合' }[value || ''] || value || '综合');
/** 混合/综合请求用点位密度；主题类请求保持原有四类场景。 */
const previewSceneForTheme = (theme?: string) => {
  if (!theme || theme === 'general') return 'density';
  return ({ walk: 'walk', date: 'date', photo: 'photo', flower: 'flower_viewing' } as Record<string, string>)[theme] || 'walk';
};
const sourceLabel = (value?: string) => ({ amap: '高德 POI', amap_plant: '高德植物候选', amap_scene: '高德场景候选', public_web: '公开网页', public_web_plant: '网页植物证据', public_web_scene: '网页场景证据', campus_registry: '已登记植物', scene_registry: '已登记场景', manual: '用户手工添加', json: 'JSON 来源' }[value || ''] || value || '未知来源');
const reviewLabel = (value: ReviewStatus) => ({ pending: '待审', approved: '已通过', rejected: '已拒绝' }[value]);
const statusType = (value?: string) => value === 'failed' || value === 'blocked' ? 'danger' : value === 'completed' ? 'success' : 'warning';
const reviewTagType = (value: ReviewStatus) => value === 'approved' ? 'success' : value === 'rejected' ? 'danger' : 'warning';
const hasQuality = (candidate: CandidatePoi) => Boolean(
  candidate.quality || candidate.scores || candidate.confidenceReasons,
);
const qualityConfidence = (candidate: CandidatePoi) => {
  const value = candidate.quality?.confidence;
  return Number.isFinite(value) ? value as number : candidate.confidence;
};
const qualityLabel = (candidate: CandidatePoi) => hasQuality(candidate) ? '质量分' : '置信度';
const sourceConfidence = (candidate: CandidatePoi) => {
  if (Number.isFinite(candidate.sourceConfidence)) return candidate.sourceConfidence as number;
  return candidate.quality && Number.isFinite(candidate.confidence) ? candidate.confidence : undefined;
};
const saveReviewToken = () => setReviewToken(reviewToken.value);
const toggleBatchCandidate = (id: string, checked: boolean) => {
  const selectedIds = new Set(batchSelectedIds.value);
  if (checked && pendingIdSet.value.has(id)) selectedIds.add(id);
  else selectedIds.delete(id);
  batchSelectedIds.value = [...selectedIds];
};
const toggleAllPending = (checked: boolean | string | number) => {
  const ids = review.value?.candidates.map(item => item.id) || [];
  selectedPreviewIds.value = checked ? ids : [];
  batchSelectedIds.value = checked ? pendingItems.value.map(item => item.id) : [];
};
const togglePreviewCandidate = (id: string, checked: boolean) => {
  const ids = new Set(selectedPreviewIds.value);
  if (checked) ids.add(id); else ids.delete(id);
  selectedPreviewIds.value = [...ids];
  batchSelectedIds.value = pendingItems.value.filter(item => ids.has(item.id)).map(item => item.id);
};
const scoreLabel = (key: string) => ({
  campusEvidence: '校园证据',
  boundaryEvidence: '边界证据',
  themeRelevance: '主题相关',
  sourceReliability: '来源可靠性',
  sourceConfidence: '来源置信度',
  crossSourceAgreement: '跨来源一致性',
  commercialRisk: '商业场所风险',
  duplicateRisk: '重复候选风险',
}[key] || key);
const scorePercent = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 100);
const scoreColor = (value: number, risk: boolean) => {
  const strength = risk ? 1 - value : value;
  return strength >= 0.7 ? '#67c23a' : strength >= 0.4 ? '#e6a23c' : '#f56c6c';
};

const escapeHtml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (char) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] || char
));
const clearMarkers = () => { markers.forEach(marker => marker.setMap(null)); markers = []; };
const clearPeaks = () => { peakMarkers.forEach(marker => marker.setMap(null)); peakMarkers = []; };
const clearScope = () => { if (scopeCircle && map) map.remove(scopeCircle); scopeCircle = null; };
/** 搜索范围圆：让“点位为什么会被收进来”在图上可解释。 */
const renderScope = () => {
  clearScope();
  if (!map) return;
  const center = review.value?.profile?.campus?.center;
  const radius = Number(resolvedDiscovery.value.radiusMeters);
  if (!Array.isArray(center) || center.length !== 2 || !Number.isFinite(radius) || radius <= 0) return;
  const AMap = (window as any).AMap;
  scopeCircle = new AMap.Circle({
    center, radius, strokeColor: '#409eff', strokeWeight: 1.5,
    strokeOpacity: 0.9, strokeStyle: 'dashed', fillOpacity: 0, zIndex: 70,
  });
  map.add(scopeCircle);
};
/** Top 热区峰值点：让热斑有名字，避免只看到一层颜色。 */
const renderPeaks = () => {
  clearPeaks();
  if (!map || !previewReady.value) return;
  const AMap = (window as any).AMap;
  previewPlaces.value.forEach((place, index) => {
    if (!Number.isFinite(place.lng) || !Number.isFinite(place.lat)) return;
    const label = escapeHtml(place.name || '热区');
    const marker = new AMap.Marker({
      map, position: [place.lng, place.lat], zIndex: 95, offset: new AMap.Pixel(-10, -10),
      content: `<div style="display:flex;align-items:center;gap:5px;padding:2px 7px;border-radius:11px;`
        + `background:#fff;border:1px solid #e6a23c;color:#8a5a00;font-size:12px;line-height:18px;`
        + `white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.18)">`
        + `<b style="font-weight:600">${label}</b>`
        + `<span style="display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;`
        + `border-radius:50%;background:#e6a23c;color:#fff;font-size:10px">${index + 1}</span></div>`,
    });
    peakMarkers.push(marker);
  });
};
const renderMarkers = () => {
  if (!map || !review.value) return;
  clearMarkers();
  // 预览默认隐藏候选点（避免遮挡热图）；勾选“显示已选点位”后只显示
  // 真正参与本次计算的已选点，方便核对点位与热斑是否对齐。
  if (previewReady.value && !showPreviewPoints.value) return;
  const AMap = (window as any).AMap;
  const items = previewReady.value
    ? reviewQueue.value.filter(item => selectedPreviewIds.value.includes(item.id))
    : reviewQueue.value;
  // 已登记地点可能有数千条，全部创建带标签 Marker 会阻塞页面并掩盖热图。
  items.forEach(item => {
    const color = item.userAdded ? '#e6a23c'
      : item.reviewStatus === 'approved' ? '#67c23a' : item.reviewStatus === 'rejected' ? '#909399' : '#c20a1c';
    const marker = new AMap.Marker({ map, position: [item.lng, item.lat], title: item.name,
      label: previewReady.value ? undefined
        : { content: item.name, direction: 'top', offset: new AMap.Pixel(0, -4) },
      icon: new AMap.Icon({ image: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="26" height="34"><path fill="${color}" d="M13 0C6 0 1 5 1 12c0 9 12 22 12 22s12-13 12-22C25 5 20 0 13 0z"/><circle cx="13" cy="12" r="5" fill="white"/></svg>`)}`, size: new AMap.Size(26, 34), imageSize: new AMap.Size(26, 34) }),
    });
    marker.on('click', () => selectCandidate(item));
    markers.push(marker);
  });
};
const initMap = async () => {
  if (map || !mapEl.value) return;
  try {
    const AMap = await loadAMap();
    map = new AMap.Map(mapEl.value, { zoom: 15, center: [121.453725, 31.03148] });
    map.on('click', onMapClick);
    renderScope();
    renderMarkers();
  } catch (err) { error.value = err instanceof Error ? err.message : '地图加载失败'; }
};

// 热力图任务切换时，旧的 review 区域会被 Vue 卸载并重新创建。
// 先销毁旧 AMap 实例，否则 initMap 会误以为地图已存在，导致新容器保持空白。
const disposeMap = () => {
  addMode.value = false;
  addDialogVisible.value = false;
  clearMarkers();
  clearPeaks();
  clearScope();
  clearHeatmap();
  try { map?.destroy?.(); } catch { /* 地图实例已被 DOM 卸载时忽略销毁异常 */ }
  map = null;
};

const selectCandidate = (item: CandidatePoi) => {
  selected.value = item;
  edit.name = item.name; edit.locationName = item.locationName; edit.lng = item.lng; edit.lat = item.lat;
  drawerVisible.value = true;
  map?.setCenter?.([item.lng, item.lat]);
};
const resetAddForm = () => {
  addForm.name = '';
  addForm.text = '';
  addForm.subCategory = '用户标注';
  addForm.category = 'scene';
  addForm.scenes = ['walk', 'photo'];
};

const toggleAddMode = () => {
  if (!map) { ElMessage.warning('地图尚未加载完成，请稍后再试'); return; }
  addMode.value = !addMode.value;
  if (addMode.value) ElMessage.info('请在地图上点击系统漏掉的地点');
};

/** 只有处于补点模式时才响应地图点击，避免和选点、预览操作互相干扰。 */
const onMapClick = (event: any) => {
  if (!addMode.value) return;
  const lnglat = event?.lnglat;
  const lng = Number(typeof lnglat?.getLng === 'function' ? lnglat.getLng() : lnglat?.lng);
  const lat = Number(typeof lnglat?.getLat === 'function' ? lnglat.getLat() : lnglat?.lat);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
  addForm.lng = Number(lng.toFixed(6));
  addForm.lat = Number(lat.toFixed(6));
  addMode.value = false;
  addDialogVisible.value = true;
};

/** 补点会改变热图输入：旧预览必须作废，前端和后端状态一起清掉。 */
const markPreviewStale = () => {
  previewReady.value = false;
  if (job.value) job.value.previewStatus = 'stale';
  publishPlan.value = null;
  clearHeatmap();
};

const submitUserPoi = async () => {
  if (!job.value) return;
  const name = addForm.name.trim();
  if (!name) { ElMessage.warning('请填写地点名称'); return; }
  adding.value = true; error.value = '';
  try {
    const payload: UserPoiPayload = {
      name,
      lng: addForm.lng,
      lat: addForm.lat,
      category: addForm.category,
      subCategory: addForm.subCategory.trim() || '用户标注',
      scenes: addForm.scenes,
      text: addForm.text.trim(),
    };
    const result = await addCampusUserPoi(job.value.id, payload);
    addDialogVisible.value = false;
    resetAddForm();
    markPreviewStale();
    await loadReview();
    renderMarkers();
    ElMessage.success(`已加入本次热图（共 ${result.count} 个补点），重新生成临时热图后生效`);
  } catch (err) {
    const message = err instanceof Error ? err.message : '补点失败';
    error.value = message;
    ElMessage.error(message);
  } finally { adding.value = false; }
};

const removeUserPoi = async (item: CandidatePoi) => {
  if (!job.value) return;
  try {
    await ElMessageBox.confirm(
      `确认删除手动添加的「${item.name}」吗？它只会从本次热图中移除。`,
      '删除补点',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    );
  } catch { return; }
  removingUserPoiId.value = item.id;
  try {
    await removeCampusUserPoi(job.value.id, item.id);
    markPreviewStale();
    await loadReview();
    renderMarkers();
    ElMessage.success('已删除补点');
  } catch (err) {
    const message = err instanceof Error ? err.message : '删除补点失败';
    error.value = message;
    ElMessage.error(message);
  } finally { removingUserPoiId.value = ''; }
};

const loadReview = async () => {
  if (!job.value?.id) return;
  loadingReview.value = true;
  try {
    review.value = await getCampusReview(job.value.id);
    selectedPreviewIds.value = review.value.selection?.selectedIds
      || review.value.candidates.filter(item => item.selectedForPreview).map(item => item.id);
    batchSelectedIds.value = review.value.candidates
      .filter(item => item.reviewStatus === 'pending' && selectedPreviewIds.value.includes(item.id))
      .map(item => item.id);
    await nextTick();
    await initMap();
    if (review.value.profile?.campus?.center) map?.setCenter(review.value.profile.campus.center);
    renderScope();
    renderMarkers();
  } catch (err) { error.value = err instanceof Error ? err.message : '候选读取失败'; }
  finally { loadingReview.value = false; }
};
const reloadReview = () => loadReview();
const poll = async () => {
  if (!job.value?.id) return;
  try {
    job.value = await getCampusBuild(job.value.id);
    if (job.value.status === 'completed' || job.value.status === 'blocked' || job.value.status === 'failed') {
      if (timer) window.clearInterval(timer); timer = null;
      if (job.value.status === 'completed') await loadReview();
    }
  } catch (err) { error.value = err instanceof Error ? err.message : '任务状态读取失败'; }
};
const startPolling = () => { if (timer) window.clearInterval(timer); timer = window.setInterval(poll, 1800); poll(); };
const submit = async () => {
  disposeMap();
  submitting.value = true; error.value = ''; review.value = null; selectedPreviewIds.value = [];
  batchSelectedIds.value = []; previewReady.value = false;
  try {
    const result = await submitCampusDiscovery({
      query: query.value.trim(),
      webUrls: webUrlsText.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
      includeAmap: includeAmap.value,
      includeWeb: includeWeb.value,
      autoSearch: autoSearch.value,
    });
    previewScene.value = previewSceneForTheme(result.theme);
    jobInput.value = result.jobId;
    job.value = {
      success: true, id: result.jobId, status: 'queued', theme: result.theme,
      keywords: result.keywords, sources: result.sources,
      discovery: {
        ...result.profile?.discovery,
        resolvedSchool: result.profile?.discovery?.resolvedSchool || result.profile?.school?.name,
        resolvedCampus: result.profile?.discovery?.resolvedCampus || result.profile?.campus?.name,
      },
      discoveryWarnings: result.warnings, webSearch: result.webSearch,
    };
    startPolling();
  } catch (err) { error.value = err instanceof Error ? err.message : '任务提交失败'; }
  finally { submitting.value = false; }
};
const loadExisting = async () => {
  disposeMap();
  error.value = ''; job.value = null; review.value = null; previewReady.value = false;
  try {
    job.value = await getCampusBuild(jobInput.value.trim());
    previewScene.value = previewSceneForTheme(job.value.theme);
    previewReady.value = job.value.previewStatus === 'completed';
    if (job.value.status === 'completed') {
      await loadReview();
      if (previewReady.value) await loadPreview();
    } else {
      startPolling();
    }
  }
  catch (err) { error.value = err instanceof Error ? err.message : '任务载入失败'; }
};
const decide = async (action: 'approve' | 'reject' | 'pending') => {
  if (!job.value || !selected.value) return;
  reviewing.value = true; error.value = '';
  try {
    const patch = action === 'approve' ? { name: edit.name.trim(), locationName: edit.locationName.trim(), lng: edit.lng, lat: edit.lat } : undefined;
    review.value = await submitCampusReview(job.value.id, [{ id: selected.value.id, action, patch }]);
    const updated = review.value.candidates.find(item => item.id === selected.value?.id);
    if (updated) selected.value = updated;
    previewReady.value = false;
    job.value.previewStatus = 'stale';
    publishPlan.value = null;
    clearHeatmap();
    renderMarkers();
    ElMessage.success(action === 'approve' ? '已通过候选地点' : action === 'reject' ? '已拒绝候选地点' : '已保留待审');
  } catch (err) { error.value = err instanceof Error ? err.message : '审核操作失败'; }
  finally { reviewing.value = false; }
};
const batchDecide = async (action: 'approve' | 'reject') => {
  if (!job.value || !batchSelectedIds.value.length) return;
  const ids = batchSelectedIds.value.filter(id => pendingIdSet.value.has(id));
  if (!ids.length) return;
  try {
    await ElMessageBox.confirm(
      action === 'approve'
        ? `确认将选中的 ${ids.length} 个地点批量通过并导入当前热图任务吗？`
        : `确认批量拒绝选中的 ${ids.length} 个地点吗？`,
      action === 'approve' ? '批量导入地点' : '批量拒绝地点',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: action === 'approve' ? 'warning' : 'info' },
    );
  } catch { return; }
  batchReviewing.value = true; error.value = '';
  try {
    review.value = await submitCampusReview(
      job.value.id,
      ids.map(id => ({ id, action })),
    );
    batchSelectedIds.value = [];
    selected.value = null;
    drawerVisible.value = false;
    previewReady.value = false;
    job.value.previewStatus = 'stale';
    publishPlan.value = null;
    clearHeatmap();
    renderMarkers();
    ElMessage.success(action === 'approve' ? `已批量导入 ${ids.length} 个地点` : `已批量拒绝 ${ids.length} 个地点`);
  } catch (err) { error.value = err instanceof Error ? err.message : '批量审核失败'; }
  finally { batchReviewing.value = false; }
};
const showPublishPlan = async () => {
  if (!job.value) return;
  publishing.value = true; error.value = '';
  try {
    const result = await getCampusPublishPlan(job.value.id);
    publishPlan.value = result.plan;
    if (result.plan.validationProblems.length) {
      throw new Error(result.plan.validationProblems.join('；'));
    }
    if (!result.plan.requiresRebuild) {
      ElMessage.info('当前审核结果没有需要发布的正式数据变更');
      return;
    }
    await ElMessageBox.confirm(
      `将向${result.plan.campus}正式数据新增植物 ${result.plan.plantAdded.length} 个、场景 ${result.plan.sceneAdded.length} 个，修改植物 ${result.plan.plantUpdated.length} 个、场景 ${result.plan.sceneUpdated.length} 个。系统会先备份两个原文件，发布后还需要重建语义索引和正式热图缓存。确认发布吗？`,
      '确认正式发布',
      { confirmButtonText: '确认发布', cancelButtonText: '取消', type: 'warning' },
    );
    const { value: confirmedToken } = await ElMessageBox.prompt(
      '请输入后端 .env 中配置的 USERDATA_REVIEW_TOKEN。令牌仅用于本次正式发布请求。',
      '发布权限验证',
      { confirmButtonText: '验证并发布', cancelButtonText: '取消', inputType: 'password', inputValue: reviewToken.value, inputPattern: /^.{16,}$/, inputErrorMessage: '审核令牌至少需要 16 位' },
    );
    reviewToken.value = confirmedToken;
    saveReviewToken();
    const published = await publishCampusBuild(job.value.id, result.plan.afterHash, confirmedToken);
    if (published.published) {
      ElMessage.success(published.message || '正式数据已发布');
    } else {
      ElMessage.info(published.reason || '正式数据没有待发布的变化');
    }
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') error.value = err instanceof Error ? err.message : '正式发布失败';
  } finally { publishing.value = false; }
};
const buildPreview = async () => {
  if (!job.value) return;
  previewing.value = true; previewReady.value = false; error.value = ''; clearHeatmap();
  try {
    await submitCampusSelection(job.value.id, selectedPreviewIds.value);
    await submitCampusPreview(job.value.id);
    let completed = false;
    for (let i = 0; i < 120; i += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 1500));
      job.value = await getCampusBuild(job.value.id);
      if (job.value.previewStatus === 'completed') {
        previewReady.value = true;
        renderMarkers();
        await loadPreview();
        completed = true;
        ElMessage.success('临时热力图已生成');
        break;
      }
      if (job.value.previewStatus === 'failed') throw new Error(job.value.previewError || '热图预览失败');
    }
    if (!completed) throw new Error('热图构建超时，任务可能仍在后台运行，请稍后重新载入任务');
  } catch (err) { error.value = err instanceof Error ? err.message : '预览构建失败'; }
  finally { previewing.value = false; }
};
const clearHeatmap = () => {
  if (heatLayer && map) map.remove(heatLayer);
  heatLayer = null;
  clearPeaks();
};
const loadPreview = async () => {
  if (!job.value || !previewReady.value) return;
  try {
    await nextTick();
    await initMap();
    if (!map) throw new Error('地图容器尚未就绪，请刷新页面后重试');
    const result = await getCampusPreview(job.value.id, previewScene.value, previewSeason.value);
    const grid = result.preview.grid;
    if (!grid || grid.values.length !== grid.width * grid.height || grid.rowOrder !== 'north_to_south') {
      throw new Error('热力网格格式不正确');
    }
    // 仅预览使用柔和配置，不改变智能问答页的默认渲染。
    const canvas = renderHeatmapCanvas(grid, PREVIEW_HEATMAP_OPTIONS);
    if (!canvas) throw new Error('当前浏览器不支持热力图渲染');
    clearHeatmap();
    const AMap = (window as any).AMap;
    // 用显式 AMap.Bounds 而不是扁平数组，避免不同版本对 [w,s,e,n] 的解释差异。
    const bounds = new AMap.Bounds(
      [grid.bounds[0], grid.bounds[1]],
      [grid.bounds[2], grid.bounds[3]],
    );
    heatLayer = new AMap.ImageLayer({
      url: canvas.toDataURL('image/png'), bounds, zooms: [3, 22],
      opacity: HEATMAP_LAYER_OPACITY, zIndex: 80,
    });
    map.add(heatLayer);
    // 自动框住热区，避免用户停在校园中心却看不到外围热斑。
    map.setBounds(bounds);
    previewPlaces.value = Array.isArray(result.preview.places) ? result.preview.places : [];
    renderPeaks();
    renderMarkers();
  } catch (err) {
    // 旧任务没有 density 缓存：自动退回散步场景，而不是把页面留在报错态。
    if (previewScene.value === 'density') {
      previewScene.value = 'walk';
      ElMessage.info('该任务没有点位密度缓存，已切换到散步场景');
      await loadPreview();
      return;
    }
    error.value = err instanceof Error ? err.message : '预览读取失败';
  }
};
onMounted(initMap);
watch(() => review.value?.candidates, renderMarkers, { deep: true });
watch(showPreviewPoints, renderMarkers);
// 高德地图实例不会感知容器的"非窗口"尺寸变化（侧栏折叠、断点切换、旋转屏幕后的重排），
// 容器尺寸变化时补一次重排；容器随 review 被 v-if 卸载时自动解绑。
let mapResizeObserver: ResizeObserver | null = null;
watch(mapEl, el => {
  mapResizeObserver?.disconnect();
  mapResizeObserver = null;
  if (!el || typeof ResizeObserver === 'undefined') return;
  mapResizeObserver = new ResizeObserver(() => {
    try { map?.resize?.(); } catch { /* 地图实例已销毁时忽略 */ }
  });
  mapResizeObserver.observe(el);
});
watch(pendingItems, items => {
  const ids = new Set(items.map(item => item.id));
  batchSelectedIds.value = batchSelectedIds.value.filter(id => ids.has(id));
});
onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
  mapResizeObserver?.disconnect();
  mapResizeObserver = null;
  disposeMap();
});
</script>

<style scoped>
.campus-build-page { display: flex; flex-direction: column; min-width: 0; gap: 14px; padding: 18px 20px 32px; background: rgba(255,255,255,.94); border-radius: 14px; min-height: calc(100vh - 75px); box-sizing: border-box; }
.page-header, .request-options, .resume-row, .job-summary, .panel-head, .review-stats, .map-toolbar, .drawer-actions { display: flex; align-items: center; gap: 10px; }
.page-header { justify-content: space-between; } h2, h3, p { margin: 0; } h2 { color: #303133; font-size: 21px; } h3 { color: #303133; font-size: 16px; } .page-header p, .panel-head p { color: #909399; font-size: 13px; margin-top: 5px; }
.request-card, .job-card, .candidate-panel, .map-panel { border: 1px solid #ebeef5; border-radius: 12px; background: #fff; padding: 14px; }
.request-options { margin-top: 10px; } .request-options .el-input { flex: 1; } .resume-row { margin-top: 10px; max-width: 420px; } .resume-row .el-input { flex: 1; }
.source-options { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 9px; }
.review-token-row { display: grid; grid-template-columns: minmax(280px, 1fr) 1.4fr; align-items: center; gap: 12px; margin-top: 10px; } .review-token-row span { color: #909399; font-size: 12px; }
.error { color: #f56c6c; font-size: 13px; margin-top: 8px; }
.discovery-details { margin-top: 10px; padding: 10px 12px; border: 1px solid #d9ecff; border-radius: 8px; background: #f4f9ff; color: #606266; font-size: 12px; }
.discovery-details strong { color: #337ecc; }
.discovery-details dl { display: flex; flex-wrap: wrap; gap: 7px 22px; margin: 7px 0 0; }
.discovery-details dl div { display: flex; min-width: 170px; gap: 6px; }
.discovery-details dt { color: #909399; }
.discovery-details dd { margin: 0; color: #303133; word-break: break-word; }
.discovery-warning { margin: 8px 0 0; color: #b45309; font-size: 12px; line-height: 1.5; }
.zero-candidate-help { margin-top: 10px; padding: 10px 12px; border: 1px solid #fab6b6; border-radius: 8px; background: #fef0f0; color: #b42318; font-size: 12px; line-height: 1.55; }
.zero-candidate-help ul { margin: 5px 0 0; padding-left: 18px; }
.zero-candidate-help p { margin-top: 5px; }
.search-results { margin-top: 10px; color: #606266; font-size: 12px; } .search-results summary { cursor: pointer; color: #409eff; } .search-results ol { margin: 8px 0 0; padding-left: 22px; } .search-results li { margin: 5px 0; } .search-results a { color: #409eff; word-break: break-all; } .search-results small { display: block; margin-top: 2px; color: #909399; }
.job-summary { flex-wrap: wrap; color: #606266; font-size: 13px; } .job-summary div { padding-right: 16px; border-right: 1px solid #ebeef5; } .job-summary div:last-child { border-right: 0; } .job-summary span { color: #909399; margin-right: 5px; } code { color: #909399; font-size: 11px; }
.workspace { display: grid; grid-template-columns: minmax(320px, 430px) minmax(0, 1fr); min-width: 0; gap: 14px; min-height: 560px; } .candidate-panel { min-width: 0; overflow: auto; max-height: 680px; } .panel-head { justify-content: space-between; align-items: flex-start; } .review-stats { margin: 14px 0 8px; }
.batch-review-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 9px 10px; margin-bottom: 8px; border: 1px solid #d9ecff; border-radius: 8px; background: #f4f9ff; } .batch-review-toolbar > span { margin-right: auto; color: #606266; font-size: 12px; }
/* 候选按高德大类分组：校园相关类型默认展开，商业/配套默认折叠。 */
.candidate-group-head { display: flex; align-items: center; gap: 6px; margin: 10px 0 2px; padding: 5px 8px; border-radius: 6px; background: #f5f7fa; color: #606266; font-size: 12px; cursor: pointer; user-select: none; }
.candidate-group-head:hover { background: #eef1f6; }
.candidate-group-head strong { flex: 1; color: #303133; font-size: 12px; font-weight: 600; }
.group-count { color: #909399; font-variant-numeric: tabular-nums; }
.group-caret { display: inline-block; color: #c0c4cc; transition: transform .15s; }
.group-caret.open { transform: rotate(90deg); }
.candidate-item { padding: 11px 10px; margin: 8px 0; border: 1px solid #ebeef5; border-left: 4px solid #e6a23c; border-radius: 9px; cursor: pointer; transition: .15s; } .candidate-item:hover, .candidate-item.selected { background: #fff8f1; border-color: #e6a23c; } .candidate-item.approved { border-left-color: #67c23a; } .candidate-item.rejected { border-left-color: #909399; opacity: .7; } .candidate-title { display: flex; justify-content: space-between; gap: 8px; } .candidate-item p, .candidate-meta { color: #909399; font-size: 12px; margin-top: 5px; } .candidate-meta { display: flex; justify-content: space-between; }
.candidate-name { display: flex; align-items: flex-start; min-width: 0; gap: 8px; } .candidate-name strong { overflow-wrap: anywhere; }
.map-panel { position: relative; min-width: 0; min-height: 560px; padding: 0; overflow: hidden; } .map { width: 100%; height: 100%; min-height: 560px; } .map-toolbar { position: absolute; z-index: 3; left: 12px; right: 12px; top: 12px; flex-wrap: wrap; width: fit-content; max-width: calc(100% - 24px); box-sizing: border-box; padding: 8px; border-radius: 9px; background: rgba(255,255,255,.95); box-shadow: 0 3px 12px #0002; } .map-hint { position: absolute; left: 12px; right: 12px; bottom: 12px; width: fit-content; max-width: calc(100% - 24px); box-sizing: border-box; padding: 7px 10px; background: rgba(255,255,255,.92); color: #909399; font-size: 12px; border-radius: 7px; }
.preview-success { color: #529b2e; } .publish-hint { position: absolute; left: 12px; bottom: 44px; padding: 7px 10px; background: rgba(255,248,230,.95); color: #b88230; font-size: 12px; border-radius: 7px; }
.add-hint { bottom: 76px; color: #b88230; background: rgba(255,248,230,.95); }
.user-poi-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 6px; }
.user-poi-note { margin: 0; color: #909399; font-size: 12px; line-height: 1.55; }
/* 临时热图图例：颜色含义此前无从得知，这里补齐（渐变与栅格同源）。 */
.map-legend { position: absolute; right: 12px; bottom: 12px; z-index: 3; width: min(240px, 46%); box-sizing: border-box; padding: 8px 10px; border-radius: 8px; background: rgba(255,255,255,.94); box-shadow: 0 3px 12px #0002; }
.legend-bar { height: 9px; border-radius: 4px; }
.legend-labels { display: flex; justify-content: space-between; margin-top: 4px; color: #606266; font-size: 11px; }
.legend-note { margin: 6px 0 0; color: #909399; font-size: 11px; line-height: 1.4; }
.empty { padding: 40px 10px; text-align: center; color: #909399; font-size: 13px; } .coord-fields { display: flex; gap: 10px; } .coord-fields .el-form-item { flex: 1; }
.quality-review { margin-top: 2px; padding: 12px; border: 1px solid #ebeef5; border-radius: 9px; background: #fafafa; } .quality-heading { display: flex; align-items: center; justify-content: space-between; color: #303133; font-size: 13px; } .quality-heading span { color: #606266; font-variant-numeric: tabular-nums; } .score-group { margin-top: 10px; } .score-group-title { margin-bottom: 7px; color: #909399; font-size: 12px; } .risk-group { padding-top: 9px; border-top: 1px dashed #dcdfe6; } .score-row { display: grid; grid-template-columns: 94px minmax(80px, 1fr) 32px; align-items: center; gap: 8px; margin: 7px 0; color: #606266; font-size: 12px; } .score-row b { text-align: right; color: #606266; font-size: 12px; font-variant-numeric: tabular-nums; } .legacy-quality { margin-top: 9px; color: #909399; font-size: 12px; } .confidence-reasons { margin-top: 12px; padding-top: 10px; border-top: 1px dashed #dcdfe6; color: #606266; font-size: 12px; } .confidence-reasons ul { margin: 7px 0 0; padding-left: 18px; } .confidence-reasons li { margin: 4px 0; line-height: 1.45; } .confidence-reasons p { margin-top: 7px; color: #909399; line-height: 1.45; }
.evidence { border-top: 1px solid #ebeef5; padding-top: 12px; } .evidence div { margin: 8px 0; } .evidence dt { color: #909399; font-size: 12px; } .evidence dd { margin: 3px 0; color: #606266; font-size: 13px; line-height: 1.5; word-break: break-word; } .drawer-actions { justify-content: flex-end; margin-top: 22px; }
/* 内容断点取 900px 而非骨架的 768px：769–900px 区间本页仍套用桌面骨架（含 240px 侧栏），
   若此时用双列工作区（候选面板至少 320px + 地图列），地图列会被压到不可用，故仍按单列排布。
   抽屉宽度已改为响应式（drawerSize），不依赖此断点。 */
@media (max-width: 900px) { .campus-build-page { padding: 14px 12px 28px; } .page-header { align-items: flex-start; flex-direction: column; } .workspace { grid-template-columns: minmax(0, 1fr); } .map-panel { min-height: 430px; } .map { min-height: 430px; } .request-options { align-items: stretch; flex-direction: column; } .review-token-row { grid-template-columns: minmax(0, 1fr); } }
</style>
