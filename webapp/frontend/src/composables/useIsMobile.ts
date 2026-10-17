import { ref, type Ref } from "vue";

/**
 * 全局移动端断点检测（单例共享，多个组件复用同一份状态）
 * 断点与 style.css 中 --bp-mobile 保持一致：<=768px 视为移动端
 *
 * 另外支持 URL 参数强制端型，供「桌面端 / 手机端」设备框预览使用：
 * - `?frame=mobile`  —— 强制走移动骨架（设备框里的人造窄视口）
 * - `?frame=desktop` —— 强制走桌面布局（真机上查看桌面版）
 *
 * 为什么设备框必须用 iframe + 参数、而不是给根节点加 class：全站的响应式
 * 都写在 `@media (max-width: 768px)` 里，媒体查询只看**视口宽度**，
 * 只有在真实窄视口（iframe）中才会生效。
 */
const MOBILE_QUERY = "(max-width: 768px)";

export type ForcedFrame = "mobile" | "desktop" | null;

function readForcedFrame(): ForcedFrame {
  if (typeof window === "undefined" || !window.location) return null;
  const value = new URLSearchParams(window.location.search).get("frame");
  return value === "mobile" || value === "desktop" ? value : null;
}

const forcedFrame = ref<ForcedFrame>(readForcedFrame());
const isMobile = ref(false);

let mediaQuery: MediaQueryList | null = null;
let initialized = false;

/** 当前生效的端型：URL 强制优先，其次跟随视口宽度 */
function apply(): void {
  if (forcedFrame.value === "mobile") {
    isMobile.value = true;
    return;
  }
  if (forcedFrame.value === "desktop") {
    isMobile.value = false;
    return;
  }
  isMobile.value = mediaQuery ? mediaQuery.matches : false;
}

function ensureListener(): void {
  if (initialized || typeof window === "undefined" || !window.matchMedia) {
    apply();
    return;
  }
  initialized = true;
  mediaQuery = window.matchMedia(MOBILE_QUERY);
  apply();
  // 兼容旧版 Safari：存在 addEventListener 则使用，否则降级 addListener
  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", apply);
  } else if (typeof mediaQuery.addListener === "function") {
    mediaQuery.addListener(apply);
  }
}

/** 返回全局共享的移动端标记（响应式只读引用） */
export function useIsMobile(): Ref<boolean> {
  ensureListener();
  return isMobile;
}

/** 当前页面是否被设备框内嵌（有 `?frame=` 参数）——内嵌时不要再渲染切换按钮，避免递归 */
export function isEmbedded(): boolean {
  return forcedFrame.value !== null;
}

/** 当前生效的强制端型（`null` 表示跟随视口） */
export function useForcedFrame(): Ref<ForcedFrame> {
  return forcedFrame;
}
