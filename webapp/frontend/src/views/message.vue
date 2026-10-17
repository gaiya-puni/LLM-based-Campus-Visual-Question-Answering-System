<template>
  <section class="message-panel">
    <!-- 概览 + 搜索 -->
    <header class="panel-tools">
      <div class="panel-stats">
        <span>共 <b>{{ tableData.length }}</b> 条寄语</span>
        <span>涉及 <b>{{ campusCount }}</b> 个校区</span>
        <span v-if="plantStats.species">校园植物 <b>{{ plantStats.species }}</b> 种</span>
      </div>
      <el-input
        v-model="keyword"
        size="small"
        clearable
        placeholder="搜索寄语者 / 标语 / 植物名 / 地点"
      />
      <!-- 按植物种类筛选，选项由数据现算，不写死 -->
      <div class="panel-filters">
        <button
          type="button"
          class="filter-chip"
          :class="{ 'is-on': !speciesFilter }"
          @click="speciesFilter = ''"
        >全部 <em>{{ tableData.length }}</em></button>
        <button
          v-for="option in speciesOptions"
          :key="option.value"
          type="button"
          class="filter-chip"
          :class="{ 'is-on': speciesFilter === option.value }"
          @click="speciesFilter = option.value"
        >{{ option.value }} <em>{{ option.count }}</em></button>
      </div>
    </header>

    <!-- 卡片列表 -->
    <div class="card-scroll">
      <article
        v-for="item in filteredData"
        :key="item.id"
        class="message-card"
        @click="openDetail(item)"
      >
        <div class="card-head">
          <img
            v-if="item.plant?.plantName"
            class="plant-icon"
            :src="iconUrl(item.plant.icon)"
            :alt="item.plant.plantName"
            @error="hideBrokenIcon"
          />
          <div v-else class="plant-icon plant-icon--empty">树</div>

          <div class="plant-meta">
            <p class="plant-name">{{ item.plant?.plantName || '未登记植物' }}</p>
            <p class="plant-sub">
              <span v-if="item.plant?.branch" class="plant-tag">{{ item.plant.branch }}</span>
              <span v-if="item.plant?.habit" class="plant-tag">{{ item.plant.habit }}</span>
              <span v-if="campusOf(placeOf(item.plant))" class="plant-tag">
                {{ campusOf(placeOf(item.plant)) }}
              </span>
              <span v-if="item.plant?.location" class="plant-loc">
                {{ locationText(item.plant.location) }}
              </span>
            </p>
          </div>

          <span v-if="item.plant?.likeCount" class="card-like">♥ {{ item.plant.likeCount }}</span>
        </div>

        <p class="card-slogan">「{{ item.slogan }}」</p>

        <div class="card-foot">
          <span class="owner-name">{{ item.owner }}</span>
          <span v-if="item.title" class="owner-title">{{ item.title }}</span>
          <span v-if="plantedDate(item.plant?.plantedAt)" class="owner-date">
            {{ plantedDate(item.plant?.plantedAt) }} 栽种
          </span>
        </div>
      </article>

      <p v-if="!filteredData.length" class="card-empty">
        {{ keyword || speciesFilter ? '没有匹配的寄语，试试换关键词或植物种类' : '暂无可显示的植物寄语' }}
      </p>
    </div>

    <!-- 详情 -->
    <el-dialog v-model="detailVisible" title="留言板" center draggable>
      <div class="detail">
        <div v-if="detail.plant?.plantName" class="detail-plant">
          <img
            class="plant-icon"
            :src="iconUrl(detail.plant.icon)"
            :alt="detail.plant.plantName"
            @error="hideBrokenIcon"
          />
          <div class="plant-meta">
            <p class="plant-name">{{ detail.plant.plantName }}</p>
            <p v-if="detail.plant.scientificName" class="plant-sci">
              {{ detail.plant.scientificName.trim() }}
            </p>
            <p class="plant-sub">
              <span v-if="detail.plant.branch" class="plant-tag">{{ detail.plant.branch }}</span>
              <span v-if="detail.plant.habit" class="plant-tag">{{ detail.plant.habit }}</span>
              <span v-if="campusOf(placeOf(detail.plant))" class="plant-tag">
                {{ campusOf(placeOf(detail.plant)) }}
              </span>
              <span v-if="detail.plant.location" class="plant-loc">
                {{ locationText(detail.plant.location) }}
              </span>
            </p>
            <p v-if="placeOf(detail.plant)" class="plant-place">{{ placeOf(detail.plant) }}</p>
          </div>
        </div>

        <h3 class="detail-slogan">「{{ detail.slogan }}」</h3>
        <p class="detail-content">{{ detail.content }}</p>
        <p class="detail-owner">
          —— {{ detail.owner }}<span v-if="detail.title">（{{ detail.title }}）</span>
        </p>

        <div v-if="detail.emotion" class="detail-emotion">
          <span class="emotion-label">情感分析</span>
          <p>{{ detail.emotion }}</p>
        </div>
      </div>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import emitter from '../bus'

interface PlantInfo {
  plantName?: string
  scientificName?: string
  branch?: string
  habit?: string
  icon?: string
  location?: string
  place?: string
  /** 旧字段名：后端一度把地点描述叫 campus，兼容未重启的旧进程。 */
  campus?: string
  likeCount?: number
  plantedAt?: string
  ownerTitle?: string
}

interface User {
  id: string
  owner: string
  slogan: string
  content: string
  emotion: string
  title: string
  plant?: PlantInfo
}

const tableData = ref<User[]>([])
const keyword = ref('')
/**
 * 植物种类筛选（取 plant.plantName）：空串表示不筛。
 * 没有用 habit（乔木/灌木）作维度——现存 153 条寄语对应植物全是"乔木"，
 * 那样的筛选只有一个按钮、没有区分度；换成植物名后数据扩充会自动变丰富。
 */
const speciesFilter = ref('')

/** 种类选项由当前数据现算并按条数降序，不把植物名写死在代码里 */
const speciesOptions = computed(() => {
  const counts = new Map<string, number>()
  tableData.value.forEach((item) => {
    const key = (item.plant?.plantName || '').trim()
    if (!key) return
    counts.set(key, (counts.get(key) || 0) + 1)
  })
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([value, count]) => ({ value, count }))
})

const detailVisible = ref(false)
const detail = ref<User>({
  id: '',
  owner: '',
  slogan: '',
  content: '',
  emotion: '',
  title: ''
})

/** 校园植物总览（种类数 / 树木数），来自 /api/plant-stats；取不到时第三项不显示。 */
const plantStats = ref<{ species?: number; trees?: number }>({})
/** place 是后端的地点描述字段，早期叫 campus；两个都读，避免后后端未重启时统计静默变成 0。 */
const placeOf = (plant?: PlantInfo) => plant?.place || plant?.campus || ''

/** 地点描述粒度细到"某校区某楼东北(某路)"，归并后才是真实校区。 */
const campusOf = (place?: string) => {
  if (!place) return ''
  if (place.includes('闵行')) return '闵行校区'
  if (place.includes('中山北路') || place.includes('中北')) return '中山北路校区'
  return ''
}

const campusCount = computed(
  () => new Set(tableData.value.map((item) => campusOf(placeOf(item.plant))).filter(Boolean)).size
)

const filteredData = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const species = speciesFilter.value
  return tableData.value.filter((item) => {
    if (species && (item.plant?.plantName || '').trim() !== species) return false
    if (!kw) return true
    return [item.owner, item.slogan, item.title, item.plant?.plantName, item.plant?.location]
      .some((field) => (field || '').toLowerCase().includes(kw))
  })
})

/** icon 字段是 OSS 完整 URL；项目已把图标镜像到 public/icons/，按文件名取本地图。 */
const iconUrl = (icon?: string) => (icon ? '/icons/' + icon.split('/').pop() : '')

const hideBrokenIcon = (event: Event) => {
  ;(event.target as HTMLImageElement).style.visibility = 'hidden'
}

const plantedDate = (value?: string) => (value ? value.slice(0, 10) : '')

/** 树木的 number 字段一半是地点描述（"大学生活动中心桥头"），一半是编号（"08688"）。 */
const locationText = (value?: string) => {
  if (!value) return ''
  return /^\d+$/.test(value) ? `编号 ${value}` : value
}

const openDetail = (item: User) => {
  detail.value = item
  detailVisible.value = true
}

const readJson = async (url: string) => {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

const loadMessages = async () => {
  try {
    const [emotions, plants, stats] = await Promise.all([
      readJson('/api/emotions'),
      // 植物索引单独成接口；取不到时退化为"没有植物信息"的列表，不影响寄语浏览
      readJson('/api/emotion-plants').catch(() => ({}) as Record<string, PlantInfo>),
      readJson('/api/plant-stats').catch(() => ({}))
    ])
    if (!Array.isArray(emotions)) throw new Error('Invalid emotion data')
    plantStats.value = stats ?? {}
    tableData.value = emotions.map((item: any) => ({
      id: String(item.id),
      owner: String(item.owner ?? ''),
      slogan: String(item.slogan ?? ''),
      content: String(item.content ?? ''),
      emotion: String(item.emotion ?? ''),
      title: String(item.title ?? ''),
      plant: plants[String(item.id)] as PlantInfo | undefined
    }))
  } catch (error) {
    console.error('Failed to load plant messages:', error)
    tableData.value = []
  }
}

onMounted(loadMessages)

// 词云点击某个词时，直接打开对应那条寄语的详情
const handleWordValueChange = (wordvalue: string) => {
  const target = tableData.value.find((item) => item.id === String(wordvalue))
  if (target) openDetail(target)
}

emitter.on('wordvalueChange', handleWordValueChange)

onUnmounted(() => {
  emitter.off('wordvalueChange', handleWordValueChange)
})
</script>

<style lang="scss" scoped>
.message-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  min-width: 0;
}

/* ---------- 概览与搜索 ---------- */
.panel-tools {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
}

.panel-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  color: #909399;

  span:not(:last-child)::after {
    content: '·';
    margin-left: 6px;
    color: #dcdfe6;
  }

  b {
    margin-right: 2px;
    font-size: 16px;
    font-weight: 600;
    color: #c20a1c;
  }
}

/* ---------- 类别筛选 ---------- */
.panel-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid #ebeef5;
  border-radius: 999px;
  background: #f7f8fa;
  color: #606266;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  transition: all 0.18s ease;

  em {
    font-style: normal;
    font-size: 11px;
    color: #a8abb2;
  }

  &:hover {
    border-color: #f0b7bd;
    color: #c20a1c;
  }

  &.is-on {
    border-color: #c20a1c;
    background: #fdf2f3;
    color: #c20a1c;
    font-weight: 600;

    em { color: #c20a1c; }
  }
}

/* ---------- 卡片列表 ---------- */
.card-scroll {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 620px;
  overflow-y: auto;
  padding-right: 4px;
}

.message-card {
  padding: 12px 14px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s;

  &:hover {
    border-color: #f0b7bd;
    box-shadow: 0 6px 18px rgba(194, 10, 28, 0.08);
    transform: translateY(-1px);
  }

  &:active {
    transform: translateY(0);
  }
}

.card-head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.plant-icon {
  flex: 0 0 auto;
  width: 42px;
  height: 42px;
  padding: 4px;
  border-radius: 10px;
  background: #f2f8f2;
  object-fit: contain;
  box-sizing: border-box;

  &--empty {
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    color: #b6c6b6;
    background: #f5f6f7;
  }
}

.plant-meta {
  flex: 1 1 auto;
  min-width: 0;
}

.plant-name {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  line-height: 1.4;
}

.plant-sci {
  margin: 2px 0 0;
  font-size: 12px;
  font-style: italic;
  color: #a8abb2;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.plant-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  margin: 4px 0 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}

.plant-tag {
  padding: 1px 6px;
  border-radius: 4px;
  background: #f4f4f5;
  color: #606266;
}

.plant-loc {
  color: #a06a2c;
  overflow-wrap: anywhere;
}

.plant-place {
  margin: 4px 0 0;
  font-size: 12px;
  color: #a8abb2;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.card-like {
  flex: 0 0 auto;
  font-size: 12px;
  color: #f56c6c;
}

.card-slogan {
  display: -webkit-box;
  margin: 10px 0 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 500;
  color: #303133;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.card-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}

.owner-name {
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}

.owner-title {
  padding: 1px 6px;
  border-radius: 4px;
  background: #fdf2f3;
  color: #c20a1c;
}

.owner-date {
  margin-left: auto;
}

.card-empty {
  margin: 40px 0;
  font-size: 13px;
  color: #a8abb2;
  text-align: center;
}

/* ---------- 详情 ---------- */
.detail-plant {
  display: flex;
  gap: 12px;
  padding: 12px;
  margin-bottom: 16px;
  background: #f7fbf7;
  border-radius: 10px;
}

.detail-slogan {
  margin: 0 0 10px;
  font-size: 17px;
  font-weight: 700;
  color: #303133;
  line-height: 1.6;
}

.detail-content {
  margin: 0;
  font-size: 15px;
  color: #555;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.detail-owner {
  margin: 14px 0 0;
  font-size: 14px;
  color: #606266;
  text-align: right;
}

.detail-emotion {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed #ebeef5;

  p {
    margin: 8px 0 0;
    font-size: 13px;
    color: #888;
    line-height: 1.8;
  }
}

.emotion-label {
  padding: 2px 8px;
  border-radius: 4px;
  background: #f4f4f5;
  font-size: 12px;
  color: #909399;
}

/* ---------- 移动端 ---------- */
@media (max-width: 768px) {
  .card-scroll {
    height: 58vh;
  }

  .panel-tools {
    padding: 10px;
  }

  /* 窄屏类别多：单行横滑，别把搜索框挤到屏幕外 */
  .panel-filters {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;

    &::-webkit-scrollbar { height: 0; }
  }

  .filter-chip {
    flex: 0 0 auto;
    min-height: 32px;
  }

  .message-card {
    padding: 12px;
  }
}
</style>
