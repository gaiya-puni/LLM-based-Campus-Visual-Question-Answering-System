<template>
  <Teleport to="body">
    <div v-if="isOpen" class="dp-mask" role="dialog" aria-modal="true" aria-label="手机端预览">
      <div class="dp-toolbar">
        <select
          class="dp-select"
          :value="deviceId"
          :disabled="desktopMode"
          aria-label="选择机型"
          @change="onDeviceChange"
        >
          <option v-for="item in DEVICE_PRESETS" :key="item.id" :value="item.id">
            {{ item.label }} · {{ item.hint }}
          </option>
        </select>
        <button
          type="button"
          class="dp-btn"
          :disabled="desktopMode || deviceId === 'auto'"
          title="切换横竖屏"
          @click="toggleLandscape"
        >旋转</button>
        <button
          type="button"
          class="dp-btn"
          :title="fit ? '按实际像素显示' : '缩放到适应窗口'"
          @click="toggleFit"
        >{{ fit ? '1:1' : '适应窗口' }}</button>
        <button type="button" class="dp-btn dp-btn--primary" @click="close">关闭</button>
      </div>

      <div class="dp-stage" :class="{ 'is-scrollable': !fit }">
        <div class="dp-frame" :class="{ 'is-landscape': landscape }" :style="frameStyle">
          <div class="dp-statusbar" aria-hidden="true">
            <span>{{ clock }}</span>
            <span v-if="showIsland" class="dp-island"></span>
            <span class="dp-statusbar__right">5G ▮</span>
          </div>
          <iframe ref="frameEl" class="dp-screen" :src="frameSrc" title="手机端预览内容"></iframe>
        </div>
      </div>

      <p class="dp-caption">{{ sizeLabel }} · 缩放 {{ zoomText }} · 按 Esc 关闭</p>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import {
  DEVICE_PRESETS,
  STATUS_BAR_H,
  useDevicePreview,
  type DeviceId,
} from '../composables/useDevicePreview';

/**
 * 设备框预览层。
 *
 * 它只做三件事：画一圈手机外壳、在框里用 iframe 加载同源站点（`?frame=mobile`
 * 或 `?frame=desktop`）、把工具条（机型 / 旋转 / 缩放 / 关闭）接上。
 * 页面本身的响应式全部由 iframe 的真实视口触发，与真机一致。
 */
const router = useRouter();
const preview = useDevicePreview();
const {
  isOpen, mode, preset, deviceId, landscape, fit,
  frameSize, scale, frameSrc, sizeLabel,
  close: closePreview, setDevice, toggleLandscape, toggleFit,
} = preview;

const frameEl = ref<HTMLIFrameElement | null>(null);
const clock = ref('09:41');

const desktopMode = computed(() => mode.value === 'desktop');
const showIsland = computed(() => !desktopMode.value && preset.value.notch && !landscape.value);
const zoomText = computed(() => `${Math.round(scale.value * 100)}%`);

const frameStyle = computed(() => ({
  width: `${frameSize.value.w}px`,
  height: `${frameSize.value.h + STATUS_BAR_H}px`,
  transform: `scale(${scale.value})`,
}));

function onDeviceChange(event: Event): void {
  setDevice((event.target as HTMLSelectElement).value as DeviceId);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close();
}

/** 关闭时把设备框里的最新路由带回外层，避免"框里逛完，回到桌面端还是旧页面" */
function close(): void {
  const frameWindow = frameEl.value?.contentWindow;
  if (frameWindow) {
    const hash = frameWindow.location.hash;
    if (hash && hash !== window.location.hash) {
      router.replace(hash.slice(1));
    }
  }
  closePreview();
}

let previousOverflow = '';

watch(isOpen, (open) => {
  if (open) {
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    clock.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    window.addEventListener('keydown', onKeydown);
  } else {
    document.body.style.overflow = previousOverflow;
    window.removeEventListener('keydown', onKeydown);
  }
}, { immediate: true });

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown);
  document.body.style.overflow = previousOverflow;
});
</script>

<style scoped>
.dp-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 14px 16px 10px;
  background: rgba(23, 20, 22, 0.74);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  overflow: hidden;
}

/* ---------------- 工具条 ---------------- */
.dp-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.28);
  margin-bottom: 12px;
  z-index: 2;
}

.dp-select {
  height: 30px;
  max-width: 216px;
  padding: 0 10px;
  border: 1px solid #e3e6ea;
  border-radius: 999px;
  background: #fff;
  color: #23262b;
  font-size: 12px;
  outline: none;
  cursor: pointer;
}

.dp-select:focus {
  border-color: #c20a1c;
  box-shadow: 0 0 0 3px rgba(194, 10, 28, 0.12);
}

.dp-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.dp-btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid #e3e6ea;
  border-radius: 999px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
}

.dp-btn:hover:not(:disabled) {
  border-color: rgba(194, 10, 28, 0.45);
  color: #c20a1c;
}

.dp-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.dp-btn--primary {
  border-color: #c20a1c;
  background: #c20a1c;
  color: #fff;
}

.dp-btn--primary:hover:not(:disabled) {
  background: #a00818;
  border-color: #a00818;
  color: #fff;
}

/* ---------------- 设备框 ---------------- */
.dp-stage {
  flex: 1;
  min-height: 0;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

/* 1:1 模式下设备框可能比窗口大，允许在此区域内滚动查看 */
.dp-stage.is-scrollable {
  overflow: auto;
  align-items: flex-start;
  padding: 4px 0 12px;
}

.dp-frame {
  position: relative;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-radius: 38px;
  overflow: hidden;
  background: #171412;
  transform-origin: center center;
  /* 用外阴影做机身包边：不占布局，iframe 视口尺寸才等于标称尺寸 */
  box-shadow:
    0 0 0 8px #171412,
    0 0 0 9px rgba(255, 255, 255, 0.08),
    0 28px 64px rgba(0, 0, 0, 0.55);
  transition: transform 0.22s ease, width 0.22s ease, height 0.22s ease;
}

.dp-frame.is-landscape {
  border-radius: 30px;
}

.dp-statusbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 22px;
  padding: 0 14px;
  background: #171412;
  color: rgba(255, 255, 255, 0.82);
  font-size: 10px;
  line-height: 1;
  letter-spacing: 0.2px;
}

.dp-island {
  position: absolute;
  top: 4px;
  left: 50%;
  width: 64px;
  height: 13px;
  border-radius: 999px;
  background: #000;
  transform: translateX(-50%);
}

.dp-statusbar__right {
  font-size: 9.5px;
  letter-spacing: 0.4px;
}

.dp-screen {
  flex: 1;
  width: 100%;
  min-height: 0;
  border: 0;
  background: #fff;
  display: block;
}

.dp-caption {
  flex-shrink: 0;
  margin: 10px 0 0;
  color: rgba(255, 255, 255, 0.72);
  font-size: 11.5px;
  line-height: 1.5;
  text-align: center;
}

@media (max-width: 768px) {
  .dp-mask { padding: 10px 8px 8px; }
  .dp-toolbar { gap: 6px; padding: 5px 6px; }
  .dp-select { max-width: 150px; font-size: 11.5px; }
  .dp-btn { padding: 0 10px; font-size: 11.5px; }
}
</style>
