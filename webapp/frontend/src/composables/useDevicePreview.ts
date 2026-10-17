import { computed, ref, type ComputedRef, type Ref } from "vue";

/**
 * 「桌面端 / 手机端」预览的全局单例。
 *
 * 为什么会存在这个 composable：全站响应式都写在 `@media (max-width: 768px)`
 * 里，媒体查询只看视口宽度。想在宽屏浏览器里看真实的移动端效果，只能把整站
 * 塞进一个窄视口——也就是 iframe（并带上 `?frame=mobile` 让内层走移动骨架）。
 * 外层只负责"设备框 + 工具条"，不复制任何页面逻辑。
 */

export type PreviewMode = "mobile" | "desktop";

export type DeviceId =
  | "iphone-se"
  | "iphone-14"
  | "iphone-15-promax"
  | "android-360"
  | "pixel-7"
  | "auto";

export interface DevicePreset {
  id: DeviceId;
  label: string;
  /** CSS 像素宽（竖屏） */
  w: number;
  /** CSS 像素高（竖屏） */
  h: number;
  /** 是否画刘海（iOS 机型） */
  notch: boolean;
  hint: string;
}

/** 覆盖 iPhone SE ~ iPhone 15 Pro Max 与常见安卓机型的 CSS 视口尺寸 */
export const DEVICE_PRESETS: DevicePreset[] = [
  { id: "iphone-se", label: "iPhone SE", w: 375, h: 667, notch: false, hint: "375 × 667" },
  { id: "iphone-14", label: "iPhone 14 / 15", w: 390, h: 844, notch: true, hint: "390 × 844" },
  { id: "iphone-15-promax", label: "iPhone 15 Pro Max", w: 430, h: 932, notch: true, hint: "430 × 932" },
  { id: "android-360", label: "Android 小屏", w: 360, h: 800, notch: false, hint: "360 × 800" },
  { id: "pixel-7", label: "Pixel 7", w: 412, h: 915, notch: false, hint: "412 × 915" },
  { id: "auto", label: "自适应窗口", w: 0, h: 0, notch: false, hint: "跟随窗口" },
];

/** 真机「桌面版预览」的虚拟桌面视口 */
const DESKTOP_VIEWPORT = { w: 1280, h: 800 };

/**
 * 设备框顶部状态栏高度（时间 / 信号示意）。
 *
 * 它必须占在 iframe **之外**：iframe 的视口尺寸直接决定媒体查询命中哪一档，
 * 如果把状态栏叠在页面之上，既会遮住站点自己的顶部栏，也会让"390×844"
 * 这个标称尺寸失真。
 */
export const STATUS_BAR_H = 22;

const STORAGE_KEY = "campus.preview.device";

const isOpen = ref(false);
const mode = ref<PreviewMode>("mobile");
const deviceId = ref<DeviceId>(readDevice());
const landscape = ref(false);
/** 缩放模式：true = 适应窗口，false = 1:1（超出窗口时设备框内可滚动查看） */
const fit = ref(true);
const windowWidth = ref(0);
const windowHeight = ref(0);
/** 打开设备框那一刻外层的路由（关闭时以 iframe 内的最新路由回写） */
const startHash = ref("#/index");

let listening = false;

function readDevice(): DeviceId {
  if (typeof window === "undefined" || !window.localStorage) return "iphone-14";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  return DEVICE_PRESETS.some(item => item.id === saved) ? (saved as DeviceId) : "iphone-14";
}

function persistDevice(value: DeviceId): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  window.localStorage.setItem(STORAGE_KEY, value);
}

function measure(): void {
  if (typeof window === "undefined") return;
  windowWidth.value = window.innerWidth;
  windowHeight.value = window.innerHeight;
}

function onResize(): void {
  measure();
}

function startListening(): void {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener("resize", onResize);
  window.addEventListener("orientationchange", onResize);
}

function stopListening(): void {
  if (!listening || typeof window === "undefined") return;
  listening = false;
  window.removeEventListener("resize", onResize);
  window.removeEventListener("orientationchange", onResize);
}

const preset = computed(
  () => DEVICE_PRESETS.find(item => item.id === deviceId.value) ?? DEVICE_PRESETS[1],
);

/** 设备框内容区尺寸（CSS 像素，未缩放） */
const frameSize = computed(() => {
  if (mode.value === "desktop") return DESKTOP_VIEWPORT;
  if (preset.value.id === "auto") {
    // 自适应：铺满窗口，留出工具条与四周呼吸位
    return {
      w: Math.max(320, windowWidth.value - 28),
      h: Math.max(420, windowHeight.value - 148),
    };
  }
  const { w, h } = preset.value;
  return landscape.value ? { w: h, h: w } : { w, h };
});

/** 等比缩放比：窗口装不下时整体缩小，绝不裁切 */
const scale = computed(() => {
  if (!fit.value) return 1;
  if (mode.value === "mobile" && preset.value.id === "auto") return 1;
  const availW = Math.max(260, windowWidth.value - 48);
  const availH = Math.max(320, windowHeight.value - 152);
  return Math.min(1, availW / frameSize.value.w, availH / (frameSize.value.h + STATUS_BAR_H));
});

/** 设备框里要加载的地址：同源站点 + 强制端型参数 + 打开时的路由 */
const frameSrc = computed(() => {
  if (typeof window === "undefined") return "about:blank";
  const url = new URL(window.location.href);
  url.searchParams.set("frame", mode.value);
  url.hash = startHash.value || "#/index";
  return url.toString();
});

const sizeLabel = computed(() => {
  if (mode.value === "desktop") return `桌面视口 ${frameSize.value.w} × ${frameSize.value.h}`;
  if (preset.value.id === "auto") return `自适应 ${frameSize.value.w} × ${frameSize.value.h}`;
  const orientation = landscape.value ? "横屏" : "竖屏";
  return `${preset.value.label} · ${frameSize.value.w} × ${frameSize.value.h}（${orientation}）`;
});

function open(next: PreviewMode = "mobile"): void {
  mode.value = next;
  startHash.value = window.location.hash || "#/index";
  measure();
  startListening();
  isOpen.value = true;
}

function close(): void {
  isOpen.value = false;
  stopListening();
}

function setDevice(value: DeviceId): void {
  deviceId.value = value;
  persistDevice(value);
  if (value === "auto") landscape.value = false;
}

function toggleLandscape(): void {
  landscape.value = !landscape.value;
}

function toggleFit(): void {
  fit.value = !fit.value;
}

export interface DevicePreviewApi {
  isOpen: Ref<boolean>;
  mode: Ref<PreviewMode>;
  preset: ComputedRef<DevicePreset>;
  deviceId: Ref<DeviceId>;
  landscape: Ref<boolean>;
  fit: Ref<boolean>;
  frameSize: ComputedRef<{ w: number; h: number }>;
  scale: ComputedRef<number>;
  frameSrc: ComputedRef<string>;
  sizeLabel: ComputedRef<string>;
  open: (next?: PreviewMode) => void;
  close: () => void;
  setDevice: (value: DeviceId) => void;
  toggleLandscape: () => void;
  toggleFit: () => void;
}

/** 取全局共享的预览状态（单例，不随组件重建） */
export function useDevicePreview(): DevicePreviewApi {
  return {
    isOpen,
    mode,
    preset,
    deviceId,
    landscape,
    fit,
    frameSize,
    scale,
    frameSrc,
    sizeLabel,
    open,
    close,
    setDevice,
    toggleLandscape,
    toggleFit,
  };
}
