<template>
  <div v-if="visible" class="view-mode-switch" :class="`is-${placement}`" role="group" aria-label="切换预览端">
    <!-- 移动端顶栏：横向空间紧张，只保留"切到另一端"的单个按钮 -->
    <button
      v-if="placement === 'topbar'"
      type="button"
      class="vm-btn"
      :title="otherTitle"
      @click="switchToOther"
    >
      <svg v-if="isMobile" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="2" y="4" width="20" height="13" rx="2" />
        <path d="M8 21h8M12 17v4" />
      </svg>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="6" y="2" width="12" height="20" rx="2.5" />
        <path d="M11 18h2" />
      </svg>
      <span>{{ otherLabel }}</span>
    </button>

    <template v-else>
      <button
        type="button"
        class="vm-btn"
        :class="{ 'is-on': !isMobile }"
        title="以桌面宽度浏览"
        @click="toDesktop"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="2" y="4" width="20" height="13" rx="2" />
          <path d="M8 21h8M12 17v4" />
        </svg>
        <span>桌面端</span>
      </button>
      <button
        type="button"
        class="vm-btn"
        :class="{ 'is-on': isMobile }"
        title="以手机尺寸浏览"
        @click="toMobile"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="6" y="2" width="12" height="20" rx="2.5" />
          <path d="M11 18h2" />
        </svg>
        <span>手机端</span>
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { isEmbedded, useIsMobile } from '../composables/useIsMobile';
import { useDevicePreview } from '../composables/useDevicePreview';

/**
 * 「桌面端 / 手机端」切换器。
 *
 * 两种摆放位置：
 * - `inline`（默认，桌面端）：作为**侧栏最后一行**，落在左下角。侧栏本身是定高 flex 列，
 *   菜单在中间独立滚动，所以这一行既不会遮住菜单项，也不会被菜单项遮住。
 *   （早期版本用 fixed 挂在右下角，会压住问答页的输入框与发送按钮。）
 * - `topbar`（移动端骨架）：塞在顶部品牌栏右侧，只显示"切到另一端"的单按钮，避免遮住
 *   底部输入区与标签导航。
 *
 * 设备框内部、登录/改密等全屏页、预览层打开时都不渲染，避免自我嵌套。
 */
withDefaults(defineProps<{ placement?: 'inline' | 'topbar' }>(), {
  placement: 'inline',
});

const route = useRoute();
const isMobile = useIsMobile();
const preview = useDevicePreview();
const embedded = isEmbedded();

const visible = computed(
  () => !embedded && route.meta?.fullScreen !== true && !preview.isOpen.value,
);

const otherLabel = computed(() => (isMobile.value ? '桌面版' : '手机版'));
const otherTitle = computed(
  () => (isMobile.value ? '以桌面宽度预览当前页' : '以手机尺寸预览当前页'),
);

function toMobile(): void {
  if (isMobile.value) return;
  preview.open('mobile');
}

/** 当前是窄视口（真机或窄窗口）时，用 1280×800 虚拟视口看桌面版布局 */
function toDesktop(): void {
  if (!isMobile.value) return;
  preview.open('desktop');
}

function switchToOther(): void {
  if (isMobile.value) preview.open('desktop');
  else preview.open('mobile');
}
</script>

<style scoped>
.view-mode-switch {
  display: inline-flex;
  /* 必须 border-box：侧栏里 width:100% + 内边距/边框会撑出 8px，
     el-aside 默认 overflow:auto 就会在按钮下方冒出一条横向滚动条 */
  box-sizing: border-box;
  gap: 2px;
  padding: 3px;
  border: 1px solid rgba(194, 10, 28, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 6px 18px rgba(35, 38, 43, 0.14);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

/* 桌面端：侧栏最后一行（由侧栏的 flex 布局摆在左下角），小而紧凑 */
.is-inline {
  width: auto;
  padding: 2px;
  gap: 2px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 3px 10px rgba(35, 38, 43, 0.1);
}

.is-inline .vm-btn {
  gap: 3px;
  height: 24px;
  padding: 0 10px;
  font-size: 11.5px;
  justify-content: center;
}

.is-inline .vm-btn svg {
  width: 12px;
  height: 12px;
}

/* 移动端顶栏：半透明贴片，跟随品牌红底 */
.is-topbar {
  flex-shrink: 0;
  margin-left: auto;
  padding: 2px;
  border-color: rgba(255, 255, 255, 0.42);
  background: rgba(255, 255, 255, 0.16);
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

.vm-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 11px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: #606266;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

.vm-btn svg {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
}

.vm-btn:hover:not(.is-on) {
  background: rgba(194, 10, 28, 0.07);
  color: #c20a1c;
}

.vm-btn:active {
  transform: scale(0.96);
}

.vm-btn.is-on {
  background: var(--ecnu-red, #c20a1c);
  color: #fff;
  font-weight: 600;
}

/* 顶栏变体：白字，不抢品牌标题 */
.is-topbar .vm-btn {
  height: 28px;
  padding: 0 9px;
  color: #fff;
  font-size: 11.5px;
}

.is-topbar .vm-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.is-topbar .vm-btn:active {
  background: rgba(255, 255, 255, 0.28);
}

</style>
