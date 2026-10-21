<template>
  <div class="common-layout" id="building">
    <router-view v-if="isFullScreen"></router-view>

    <!-- 移动端骨架：顶部品牌栏 + 内容区 + 底部标签导航 -->
    <div v-else-if="isMobile" class="mobile-shell">
      <header class="mobile-topbar">
        <div class="mobile-topbar__logo">
          <img src="./views/icons/tree.png" alt="知行校园图标" />
        </div>
        <span class="mobile-topbar__title">知行校园 CampusMind</span>
        <!-- 移动端空间有限：只放"切到桌面版"的单按钮，避免压住底部输入区 -->
        <view-mode-switch placement="topbar" />
      </header>

      <main class="mobile-main">
        <router-view></router-view>
      </main>

      <nav class="mobile-tabbar">
        <router-link
          v-for="tab in mainTabs"
          :key="tab.path"
          :to="tab.path"
          class="mobile-tab"
          :class="{ 'is-active': route.path === tab.path }"
        >
          <el-icon :size="22"><component :is="tab.icon" /></el-icon>
          <span class="mobile-tab__label">{{ tab.label }}</span>
        </router-link>
        <button
          type="button"
          class="mobile-tab"
          :class="{ 'is-active': moreOpen }"
          @click="moreOpen = true"
        >
          <el-icon :size="22"><Grid /></el-icon>
          <span class="mobile-tab__label">更多</span>
        </button>
      </nav>

      <el-drawer
        v-model="moreOpen"
        direction="btt"
        size="248px"
        :with-header="false"
        class="mobile-more-drawer"
      >
        <div class="mobile-more-list">
          <button
            v-for="item in moreItems"
            :key="item.path"
            type="button"
            class="mobile-more-item"
            :class="{ 'is-active': route.path === item.path }"
            @click="goMore(item.path)"
          >
            <el-icon :size="20"><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
            <el-icon class="mobile-more-item__arrow" :size="14"><ArrowRight /></el-icon>
          </button>
        </div>
      </el-drawer>
    </div>

    <!-- 桌面端保持原布局 -->
    <el-container v-else style="margin-top: 20px; margin-left: 20px;">
      <el-aside width="240px"><sidebar /></el-aside>
      <el-container>
        <el-main><router-view></router-view></el-main>
      </el-container>
    </el-container>

    <!-- 设备框预览层（端切换按钮分别放在侧栏左下角 / 移动端顶栏，见 sidebar.vue 与本页顶栏） -->
    <device-preview-frame v-if="!embedded" />
  </div>
</template>


<script setup lang="ts">
// 引入你的组件

import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import sidebar from './views/sidebar.vue';
import { isEmbedded, useIsMobile } from './composables/useIsMobile';
import { ensureReviewAccess, useReviewEnabled } from './composables/useReviewAccess';
import ViewModeSwitch from './components/ViewModeSwitch.vue';
import DevicePreviewFrame from './components/DevicePreviewFrame.vue';
import {
  ArrowRight,
  ChatDotRound,
  ChatDotSquare,
  DataAnalysis,
  DocumentChecked,
  Grid,
  House,
  MapLocation,
  Menu,
  Notebook,
} from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const isFullScreen = computed(() => route.meta.fullScreen === true);
const isMobile = useIsMobile();
// 自己被装进设备框（?frame=...）时不再渲染切换器与预览层，否则会无限嵌套
const embedded = isEmbedded();

const mainTabs = [
  { path: '/index', label: '主页', icon: House },
  { path: '/map', label: '地图', icon: MapLocation },
  { path: '/chatbot', label: '问答', icon: ChatDotRound },
  { path: '/plantmessage', label: '寄语', icon: Notebook },
];

const MORE_ITEMS = [
  { path: '/classify', label: '植物图表', icon: Menu, requiresReview: false },
  { path: '/place-review', label: '数据审核', icon: DocumentChecked, requiresReview: false },
  { path: '/comment-review', label: '评论审核', icon: ChatDotSquare, requiresReview: true },
  { path: '/campus-build', label: '热力图生成', icon: DataAnalysis, requiresReview: false },
];

// 审核态未知（null）时保留入口，只有后端确认"未配置"才隐藏——见 useReviewAccess
const reviewEnabled = useReviewEnabled();
const moreItems = computed(() => MORE_ITEMS.filter(
  item => !item.requiresReview || reviewEnabled.value !== false,
));

const moreOpen = ref(false);

function goMore(path: string): void {
  moreOpen.value = false;
  router.push(path);
}

onMounted(() => {
  // 移动端骨架没有 sidebar 组件，入口显隐在这里单独确认一次
  ensureReviewAccess();
});

</script>

<style>
/* 侧栏内的满宽组件（如端切换按钮）不应把 el-aside 撑出横向滚动条 */
.el-aside {
  overflow-x: hidden;
}

#building{
background:url("../src/assets/background.jpg");
width:100%;
height:100vh;
height:100dvh;
position:fixed;
inset:0;
overflow-x:hidden;
overflow-y:auto;
scrollbar-gutter:stable;
background-size:100% 100%;
}

/* 移动端（含设备框预览）：滚动条按 overlay 处理，不占宽度。
   #building 上的 scrollbar-gutter: stable 是给桌面端防抖用的，它会在右侧
   永久预留 15px 槽位，窄视口下就成了一条白边，这里必须把 gutter 关掉。 */
@media (max-width: 768px) {
  #building {
    scrollbar-gutter: auto;
    scrollbar-width: none;
  }

  #building::-webkit-scrollbar,
  .mobile-main::-webkit-scrollbar {
    width: 0;
    height: 0;
  }

  .mobile-main {
    scrollbar-width: none;
    -ms-overflow-style: none;
  }
}

/* ===================== 移动端骨架 ===================== */
.mobile-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}

.mobile-topbar {
  flex-shrink: 0;
  height: calc(var(--mobile-topbar-h) + var(--safe-area-top));
  padding: var(--safe-area-top) 14px 0;
  display: flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(90deg, var(--ecnu-red) 0%, var(--ecnu-red-dark) 100%);
  box-shadow: 0 2px 10px rgba(160, 8, 24, 0.35);
  position: relative;
  z-index: 30;
}

.mobile-topbar__logo {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}

.mobile-topbar__logo img {
  width: 88%;
  height: 88%;
  object-fit: contain;
}

.mobile-topbar__title {
  flex: 1;
  min-width: 0;
  color: #fff;
  font-size: clamp(13px, 4vw, 17px);
  font-weight: 600;
  letter-spacing: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mobile-main {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  position: relative;
  z-index: 1;
}

.mobile-tabbar {
  flex-shrink: 0;
  height: calc(var(--mobile-tabbar-h) + var(--safe-area-bottom));
  padding: 0 4px var(--safe-area-bottom);
  display: flex;
  align-items: stretch;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-top: 1px solid rgba(194, 10, 28, 0.12);
  box-shadow: 0 -2px 12px rgba(0, 0, 0, 0.08);
  position: relative;
  z-index: 30;
}

.mobile-tab {
  flex: 1;
  min-width: 44px;
  min-height: 44px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: none;
  background: transparent;
  color: #646a73;
  text-decoration: none;
  cursor: pointer;
  padding: 4px 0;
  transition: color 0.2s ease;
}

.mobile-tab .el-icon {
  transition: transform 0.2s ease;
}

.mobile-tab:active .el-icon {
  transform: scale(0.88);
}

.mobile-tab.is-active {
  color: var(--ecnu-red);
  font-weight: 600;
}

.mobile-tab__label {
  font-size: 11px;
  line-height: 1;
}

/* 底部抽屉：更多菜单 */
.mobile-more-drawer {
  border-radius: 16px 16px 0 0 !important;
}

.mobile-more-list {
  display: flex;
  flex-direction: column;
  padding: 8px 12px calc(12px + var(--safe-area-bottom));
}

.mobile-more-item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 52px;
  padding: 0 14px;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: #1f2329;
  font-size: 15px;
  cursor: pointer;
  transition: background 0.2s ease;
}

.mobile-more-item:active {
  background: rgba(194, 10, 28, 0.08);
}

.mobile-more-item.is-active {
  color: var(--ecnu-red);
  font-weight: 600;
  background: rgba(194, 10, 28, 0.06);
}

.mobile-more-item__arrow {
  margin-left: auto;
  color: #c0c4cc;
}
</style>
