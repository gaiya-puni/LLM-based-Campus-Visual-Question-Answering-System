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
        placeholder="例如：帮我做华东师范大学闵行校区的约会热力图"
      />
      <div class="request-options">
        <el-input v-model="webUrlsText" placeholder="公开网页 URL（可选，多个用换行分隔）" />
        <el-button type="primary" :loading="submitting" :disabled="!query.trim()" @click="submit">
          开始发现
        </el-button>
      </div>
      <div class="source-options">
        <el-checkbox v-model="includeAmap">搜索高德 POI</el-checkbox>
        <el-checkbox v-model="includeWeb">采集公开网页</el-checkbox>
        <span class="source-hint">高德 POI 需要后端配置 AMAP_WEB_SERVICE_KEY；浏览器地图 Key 不能替代。</span>
      </div>
      <div class="resume-row">
        <el-input v-model="jobInput" size="small" placeholder="已有任务 ID，可直接载入" />
        <el-button size="small" :disabled="!jobInput.trim()" @click="loadExisting">载入任务</el-button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <section v-if="job" class="job-card">
      <div class="job-summary">
        <div><span>任务</span><code>{{ job.id }}</code></div>
        <div><span>阶段</span>{{ stageLabel(job.stage) }}</div>
        <div><span>候选</span>{{ job.candidateCount ?? 0 }}</div>
        <div><span>通过</span>{{ review?.approved.length ?? job.approvedCount ?? 0 }}</div>
        <div v-if="job.theme"><span>主题</span>{{ themeLabel(job.theme) }}</div>
      </div>
      <el-progress v-if="job.status === 'queued' || job.status === 'running'" :percentage="progress" :indeterminate="job.status === 'running'" />
      <p v-if="job.error" class="error">任务失败：{{ job.error }}</p>
      <p v-if="job.discoveryWarnings?.length" class="warning">{{ job.discoveryWarnings.join('；') }}</p>
    </section>

    <section v-if="review" class="workspace">
      <div class="candidate-panel">
        <div class="panel-head">
          <div>
            <h3>{{ review.school }} · {{ review.campus }}</h3>
            <p>已登记的植物与场景地点自动保留；这里审核新发现的校园地点。</p>
          </div>
          <el-button size="small" @click="reloadReview" :loading="loadingReview">刷新</el-button>
        </div>
        <div class="review-stats">
          <el-tag type="warning">待审 {{ pendingItems.length }}</el-tag>
          <el-tag type="success">通过 {{ review.approved.length }}</el-tag>
          <el-tag type="info">无效 {{ review.pendingInvalid.length }}</el-tag>
        </div>
        <div v-if="!reviewQueue.length" class="empty">暂无待审核地点，请检查采集来源或任务警告。</div>
        <article
          v-for="item in reviewQueue"
          :key="item.id"
          class="candidate-item"
          :class="{ selected: selected?.id === item.id, approved: item.reviewStatus === 'approved', rejected: item.reviewStatus === 'rejected' }"
          @click="selectCandidate(item)"
        >
          <div class="candidate-title">
            <strong>{{ item.name }}</strong>
            <el-tag size="small" :type="reviewTagType(item.reviewStatus)">{{ reviewLabel(item.reviewStatus) }}</el-tag>
          </div>
          <p>{{ item.subCategory }} · {{ item.lng.toFixed(5) }}, {{ item.lat.toFixed(5) }}</p>
          <div class="candidate-meta">
            <span>{{ sourceLabel(item.source) }}</span>
            <span>置信度 {{ item.confidence.toFixed(2) }}</span>
          </div>
        </article>
      </div>

      <div class="map-panel">
        <div ref="mapEl" class="map"></div>
        <div class="map-toolbar">
          <el-button size="small" :disabled="previewing || (review.approved.length < 3)" :loading="previewing" type="primary" @click="buildPreview">
            {{ previewing ? '构建预览中…' : previewReady ? '重新生成临时热图' : '生成临时热图' }}
          </el-button>
          <el-button size="small" :disabled="publishing || !review.approved.length" :loading="publishing" @click="showPublishPlan">
            查看正式发布变更
          </el-button>
          <el-select v-model="previewScene" size="small" :disabled="!previewReady" @change="loadPreview">
            <el-option v-for="scene in scenes" :key="scene.id" :label="scene.name" :value="scene.id" />
          </el-select>
          <el-select v-if="previewScene === 'photo' || previewScene === 'flower_viewing'" v-model="previewSeason" size="small" :disabled="!previewReady" @change="loadPreview">
            <el-option v-for="season in seasons" :key="season.id" :label="season.name" :value="season.id" />
          </el-select>
        </div>
        <p v-if="!previewReady" class="map-hint">已通过地点 {{ review.approved.length }} 个；新候选通过后即可生成隔离热图预览。</p>
        <p v-else class="map-hint preview-success">临时热图已生成，待审核标记已隐藏，可切换场景和季节查看。</p>
        <p v-if="publishPlan" class="publish-hint">正式发布预览：植物新增 {{ publishPlan.plantAdded.length }} / 修改 {{ publishPlan.plantUpdated.length }}，场景新增 {{ publishPlan.sceneAdded.length }} / 修改 {{ publishPlan.sceneUpdated.length }}；发布后需重建索引和热图缓存。</p>
      </div>

      <el-drawer v-model="drawerVisible" title="候选地点审核" size="380px">
        <template v-if="selected">
          <el-form label-position="top">
            <el-form-item label="地点名称"><el-input v-model="edit.name" /></el-form-item>
            <el-form-item label="位置描述"><el-input v-model="edit.locationName" /></el-form-item>
            <div class="coord-fields">
              <el-form-item label="经度"><el-input-number v-model="edit.lng" :precision="6" :step="0.0001" /></el-form-item>
              <el-form-item label="纬度"><el-input-number v-model="edit.lat" :precision="6" :step="0.0001" /></el-form-item>
            </div>
          </el-form>
          <dl class="evidence">
            <div><dt>来源</dt><dd>{{ sourceLabel(selected.source) }}</dd></div>
            <div><dt>置信度</dt><dd>{{ selected.confidence.toFixed(2) }}</dd></div>
            <div><dt>证据</dt><dd>{{ evidenceText }}</dd></div>
          </dl>
          <div class="drawer-actions">
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
import {
  getCampusBuild, getCampusPreview, getCampusReview, submitCampusDiscovery,
  submitCampusPreview, submitCampusReview, getCampusPublishPlan, publishCampusBuild,
  type BuildJob, type CandidatePoi, type ReviewBundle, type ReviewStatus, type PublishPlan,
} from '../api/campusBuild';

const query = ref('');
const webUrlsText = ref('');
const includeAmap = ref(true);
const includeWeb = ref(true);
const jobInput = ref('');
const job = ref<BuildJob | null>(null);
const review = ref<ReviewBundle | null>(null);
const selected = ref<CandidatePoi | null>(null);
const error = ref('');
const submitting = ref(false);
const loadingReview = ref(false);
const reviewing = ref(false);
const previewing = ref(false);
const previewReady = ref(false);
const previewScene = ref('walk');
const previewSeason = ref('autumn');
const publishPlan = ref<PublishPlan | null>(null);
const publishing = ref(false);
const drawerVisible = ref(false);
const mapEl = ref<HTMLElement | null>(null);
const edit = reactive({ name: '', locationName: '', lng: 0, lat: 0 });
const progress = computed(() => job.value?.status === 'queued' ? 12 : 62);
const pendingItems = computed(() => review.value?.candidates.filter(item => item.reviewStatus === 'pending') || []);
const reviewQueue = computed(() => {
  const items = review.value?.candidates || [];
  const pending = items.filter(item => item.reviewStatus === 'pending');
  return selected.value && !pending.some(item => item.id === selected.value?.id)
    ? [...pending, selected.value]
    : pending;
});
const evidenceText = computed(() => {
  const evidence = selected.value?.evidence;
  if (!evidence) return '暂无结构化证据';
  return Object.values(evidence).filter(Boolean).join('；');
});

const scenes = [
  { id: 'walk', name: '散步休息' }, { id: 'date', name: '浪漫约会' },
  { id: 'photo', name: '拍照' }, { id: 'flower_viewing', name: '赏花' },
];
const seasons = [
  { id: 'spring', name: '春季' }, { id: 'summer', name: '夏季' },
  { id: 'autumn', name: '秋季' }, { id: 'winter', name: '冬季' },
];
let timer: number | null = null;
let map: any = null;
let markers: any[] = [];
let heatLayer: any = null;

const statusLabel = (value?: string) => ({ queued: '排队中', running: '采集中', completed: '待审核', blocked: '已阻塞', failed: '失败' }[value || ''] || value || '未知');
const stageLabel = (value?: string) => ({ harvest: '采集来源', normalize: '整理候选', complete: '等待审核', failed: '失败' }[value || ''] || value || '准备中');
const themeLabel = (value?: string) => ({ walk: '散步', date: '约会', photo: '拍照', flower: '赏花', study: '学习', food: '餐饮', general: '综合' }[value || ''] || value || '综合');
const sourceLabel = (value?: string) => ({ amap: '高德 POI', amap_plant: '高德植物候选', amap_scene: '高德场景候选', public_web: '公开网页', public_web_plant: '网页植物证据', public_web_scene: '网页场景证据', campus_registry: '已登记植物', scene_registry: '已登记场景', json: 'JSON 来源' }[value || ''] || value || '未知来源');
const reviewLabel = (value: ReviewStatus) => ({ pending: '待审', approved: '已通过', rejected: '已拒绝' }[value]);
const statusType = (value?: string) => value === 'failed' || value === 'blocked' ? 'danger' : value === 'completed' ? 'success' : 'warning';
const reviewTagType = (value: ReviewStatus) => value === 'approved' ? 'success' : value === 'rejected' ? 'danger' : 'warning';

const clearMarkers = () => { markers.forEach(marker => marker.setMap(null)); markers = []; };
const renderMarkers = () => {
  if (!map || !review.value) return;
  clearMarkers();
  // 预览只展示已通过地点生成的热图。待审核候选没有参与计算，
  // 继续显示会遮挡热图，也容易让人误以为它们已被采用。
  if (previewReady.value) return;
  const AMap = (window as any).AMap;
  // 已登记地点可能有数千条，全部创建带标签 Marker 会阻塞页面并掩盖热图。
  // 地图仅展示需要审核的新候选，已通过数据由热图图层呈现。
  reviewQueue.value.forEach(item => {
    const color = item.reviewStatus === 'approved' ? '#67c23a' : item.reviewStatus === 'rejected' ? '#909399' : '#c20a1c';
    const marker = new AMap.Marker({ map, position: [item.lng, item.lat], title: item.name,
      label: { content: item.name, direction: 'top', offset: new AMap.Pixel(0, -4) },
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
    renderMarkers();
  } catch (err) { error.value = err instanceof Error ? err.message : '地图加载失败'; }
};

const selectCandidate = (item: CandidatePoi) => {
  selected.value = item;
  edit.name = item.name; edit.locationName = item.locationName; edit.lng = item.lng; edit.lat = item.lat;
  drawerVisible.value = true;
  map?.setCenter?.([item.lng, item.lat]);
};
const loadReview = async () => {
  if (!job.value?.id) return;
  loadingReview.value = true;
  try {
    review.value = await getCampusReview(job.value.id);
    await nextTick();
    await initMap();
    if (review.value.profile?.campus?.center) map?.setCenter(review.value.profile.campus.center);
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
  submitting.value = true; error.value = ''; review.value = null; previewReady.value = false;
  try {
    const result = await submitCampusDiscovery({
      query: query.value.trim(),
      webUrls: webUrlsText.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean),
      includeAmap: includeAmap.value,
      includeWeb: includeWeb.value,
    });
    previewScene.value = ({ walk: 'walk', date: 'date', photo: 'photo', flower: 'flower_viewing' } as Record<string, string>)[result.theme] || 'walk';
    jobInput.value = result.jobId; job.value = { success: true, id: result.jobId, status: 'queued', theme: result.theme, keywords: result.keywords };
    startPolling();
  } catch (err) { error.value = err instanceof Error ? err.message : '任务提交失败'; }
  finally { submitting.value = false; }
};
const loadExisting = async () => {
  error.value = ''; job.value = null; review.value = null; previewReady.value = false; clearHeatmap();
  try {
    job.value = await getCampusBuild(jobInput.value.trim());
    previewScene.value = ({ walk: 'walk', date: 'date', photo: 'photo', flower: 'flower_viewing' } as Record<string, string>)[job.value.theme || ''] || 'walk';
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
    publishPlan.value = null;
    clearHeatmap();
    renderMarkers();
    ElMessage.success(action === 'approve' ? '已通过候选地点' : action === 'reject' ? '已拒绝候选地点' : '已保留待审');
  } catch (err) { error.value = err instanceof Error ? err.message : '审核操作失败'; }
  finally { reviewing.value = false; }
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
    const { value: reviewToken } = await ElMessageBox.prompt(
      '请输入后端 .env 中配置的 USERDATA_REVIEW_TOKEN。令牌仅用于本次正式发布请求。',
      '发布权限验证',
      { confirmButtonText: '验证并发布', cancelButtonText: '取消', inputType: 'password', inputPattern: /^.{16,}$/, inputErrorMessage: '审核令牌至少需要 16 位' },
    );
    const published = await publishCampusBuild(job.value.id, result.plan.afterHash, reviewToken);
    ElMessage.success(published.message || '正式数据已发布');
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') error.value = err instanceof Error ? err.message : '正式发布失败';
  } finally { publishing.value = false; }
};
const buildPreview = async () => {
  if (!job.value) return;
  previewing.value = true; previewReady.value = false; error.value = ''; clearHeatmap();
  try {
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
const clearHeatmap = () => { if (heatLayer && map) map.remove(heatLayer); heatLayer = null; };
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
    const canvas = document.createElement('canvas'); canvas.width = grid.width; canvas.height = grid.height;
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    const image = ctx.createImageData(grid.width, grid.height);
    grid.values.forEach((value: number, index: number) => { if (value === grid.noData) return; const t = Math.max(0, Math.min(1, value / grid.maxValue)); image.data[index * 4] = Math.round(41 + 174 * t); image.data[index * 4 + 1] = Math.round(103 + 40 * (1 - t)); image.data[index * 4 + 2] = Math.round(195 - 150 * t); image.data[index * 4 + 3] = Math.round(70 + 150 * t); });
    ctx.putImageData(image, 0, 0); clearHeatmap();
    const AMap = (window as any).AMap; heatLayer = new AMap.ImageLayer({ url: canvas.toDataURL('image/png'), bounds: grid.bounds, zooms: [3, 22], opacity: 0.58, zIndex: 80 }); map.add(heatLayer);
  } catch (err) { error.value = err instanceof Error ? err.message : '预览读取失败'; }
};
onMounted(initMap);
watch(() => review.value?.candidates, renderMarkers, { deep: true });
onBeforeUnmount(() => { if (timer) window.clearInterval(timer); clearMarkers(); clearHeatmap(); map?.destroy?.(); });
</script>

<style scoped>
.campus-build-page { display: flex; flex-direction: column; gap: 14px; padding: 18px 20px 32px; background: rgba(255,255,255,.94); border-radius: 14px; min-height: calc(100vh - 75px); box-sizing: border-box; }
.page-header, .request-options, .resume-row, .job-summary, .panel-head, .review-stats, .map-toolbar, .drawer-actions { display: flex; align-items: center; gap: 10px; }
.page-header { justify-content: space-between; } h2, h3, p { margin: 0; } h2 { color: #303133; font-size: 21px; } h3 { color: #303133; font-size: 16px; } .page-header p, .panel-head p { color: #909399; font-size: 13px; margin-top: 5px; }
.request-card, .job-card, .candidate-panel, .map-panel { border: 1px solid #ebeef5; border-radius: 12px; background: #fff; padding: 14px; }
.request-options { margin-top: 10px; } .request-options .el-input { flex: 1; } .resume-row { margin-top: 10px; max-width: 420px; } .resume-row .el-input { flex: 1; }
.source-options { display: flex; align-items: center; gap: 12px; margin-top: 9px; } .source-hint { color: #909399; font-size: 12px; }
.error { color: #f56c6c; font-size: 13px; margin-top: 8px; } .warning { color: #b88230; font-size: 12px; margin-top: 8px; }
.job-summary { flex-wrap: wrap; color: #606266; font-size: 13px; } .job-summary div { padding-right: 16px; border-right: 1px solid #ebeef5; } .job-summary div:last-child { border-right: 0; } .job-summary span { color: #909399; margin-right: 5px; } code { color: #909399; font-size: 11px; }
.workspace { display: grid; grid-template-columns: minmax(320px, 430px) minmax(0, 1fr); gap: 14px; min-height: 560px; } .candidate-panel { overflow: auto; max-height: 680px; } .panel-head { justify-content: space-between; align-items: flex-start; } .review-stats { margin: 14px 0 8px; }
.candidate-item { padding: 11px 10px; margin: 8px 0; border: 1px solid #ebeef5; border-left: 4px solid #e6a23c; border-radius: 9px; cursor: pointer; transition: .15s; } .candidate-item:hover, .candidate-item.selected { background: #fff8f1; border-color: #e6a23c; } .candidate-item.approved { border-left-color: #67c23a; } .candidate-item.rejected { border-left-color: #909399; opacity: .7; } .candidate-title { display: flex; justify-content: space-between; gap: 8px; } .candidate-item p, .candidate-meta { color: #909399; font-size: 12px; margin-top: 5px; } .candidate-meta { display: flex; justify-content: space-between; }
.map-panel { position: relative; min-height: 560px; padding: 0; overflow: hidden; } .map { width: 100%; height: 100%; min-height: 560px; } .map-toolbar { position: absolute; z-index: 3; left: 12px; top: 12px; padding: 8px; border-radius: 9px; background: rgba(255,255,255,.95); box-shadow: 0 3px 12px #0002; } .map-hint { position: absolute; left: 12px; bottom: 12px; padding: 7px 10px; background: rgba(255,255,255,.92); color: #909399; font-size: 12px; border-radius: 7px; }
.preview-success { color: #529b2e; } .publish-hint { position: absolute; left: 12px; bottom: 44px; padding: 7px 10px; background: rgba(255,248,230,.95); color: #b88230; font-size: 12px; border-radius: 7px; }
.empty { padding: 40px 10px; text-align: center; color: #909399; font-size: 13px; } .coord-fields { display: flex; gap: 10px; } .coord-fields .el-form-item { flex: 1; } .evidence { border-top: 1px solid #ebeef5; padding-top: 12px; } .evidence div { margin: 8px 0; } .evidence dt { color: #909399; font-size: 12px; } .evidence dd { margin: 3px 0; color: #606266; font-size: 13px; line-height: 1.5; word-break: break-word; } .drawer-actions { justify-content: flex-end; margin-top: 22px; }
@media (max-width: 900px) { .workspace { grid-template-columns: 1fr; } .map-panel { min-height: 430px; } .map { min-height: 430px; } .request-options { align-items: stretch; flex-direction: column; } }
</style>
