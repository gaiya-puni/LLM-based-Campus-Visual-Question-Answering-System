/**
 * 场景热图栅格渲染：数值上采样 + 等值带 + 图例同源色带。
 *
 * 这个模块存在的原因是：原先"问答页场景热图"（`SceneHeatmapPanel.vue`）与
 * "校园生成页临时预览"（`campusBuild.vue`）各写了一套 Canvas 上色代码，而两套都有
 * 同样的三个毛病（实测得出的，不是主观感受）：
 *
 * 1. **alpha 有下限**（`70 + 150t` / `90 + 165t`，即 t=0 时也有 27% / 35% 不透明）。
 *    而实测一份缓存里 `t≥0.2` 的格子占了有效格的 68.5%、`t≥0.1` 占 87.2%——
 *    于是"其实没什么热度"的格子也全都在上色，整体表现为给数据覆盖区**刷了一层均匀蒙版**，
 *    底图被压暗、热点也不突出。
 * 2. **RGB 线性插值中段必然发灰**。生成页那套从蓝 `(41,103,195)` 直接插到橙 `(215,143,45)`，
 *    `t≈0.5` 时得到 `rgb(128,123,120)`，是纯灰——这就是"脏灰绿"的来源。
 *    这里改成 HSL 插值（色相单调下降、饱和与明度固定），中段始终是饱和色。
 * 3. **低分辨率栅格被直接拉伸**：60×69 的栅格铺满整个 bounds，等值过渡发虚。
 *    这里先把**数值**做双线性上采样，再着色并叠加等值带边界，过渡更细腻。
 *
 * 另外把色带抽成单一来源，图例（CSS 渐变）与栅格（Canvas）取自同一个函数，不会再出现
 * "两个页面颜色不一样"。
 */

export interface HeatmapGrid {
  width: number;
  height: number;
  bounds: [number, number, number, number];
  rowOrder: string;
  values: number[];
  maxValue: number;
  noData: number;
}

/**: 低于这个相对强度完全不画（让底图保持可读）。 */
export const HEATMAP_DISPLAY_THRESHOLD = 0.12;
/**: 栅格默认上采样倍数（数值插值，不是图像拉伸）。 */
export const HEATMAP_UPSCALE = 3;
/**: 等值带档数。 */
export const HEATMAP_BANDS = 6;
/**: 图层默认不透明度。alpha 改为从 0 起之后，需要比旧值(0.55/0.58)高一些才够亮。 */
export const HEATMAP_LAYER_OPACITY = 0.8;

/**
 * 色带控制点。色相沿 214 → 190 → 48 → 26 → -4（≡356）**单调下降**，
 * 这样插值时不会绕远路；饱和与明度保持在较高水平，避免出现灰调。
 */
const RAMP_STOPS: { t: number; h: number; s: number; l: number }[] = [
  { t: 0.0, h: 214, s: 82, l: 50 },
  { t: 0.25, h: 190, s: 78, l: 45 },
  { t: 0.5, h: 48, s: 95, l: 52 },
  { t: 0.75, h: 26, s: 92, l: 53 },
  { t: 1.0, h: -4, s: 78, l: 50 },
];

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const hue = ((h % 360) + 360) % 360;
  const sat = clamp01(s / 100);
  const light = clamp01(l / 100);
  const chroma = (1 - Math.abs(2 * light - 1)) * sat;
  const secondary = chroma * (1 - Math.abs(((hue / 60) % 2) - 1));
  const offset = light - chroma / 2;
  let rgb: [number, number, number];
  if (hue < 60) rgb = [chroma, secondary, 0];
  else if (hue < 120) rgb = [secondary, chroma, 0];
  else if (hue < 180) rgb = [0, chroma, secondary];
  else if (hue < 240) rgb = [0, secondary, chroma];
  else if (hue < 300) rgb = [secondary, 0, chroma];
  else rgb = [chroma, 0, secondary];
  return [
    Math.round((rgb[0] + offset) * 255),
    Math.round((rgb[1] + offset) * 255),
    Math.round((rgb[2] + offset) * 255),
  ];
}

/** 相对强度 t ∈ [0,1] → RGB。 */
export function rampColor(t: number): [number, number, number] {
  const value = clamp01(t);
  let index = 0;
  while (index < RAMP_STOPS.length - 2 && value > RAMP_STOPS[index + 1].t) index++;
  const from = RAMP_STOPS[index];
  const to = RAMP_STOPS[index + 1];
  const span = to.t - from.t || 1;
  const mix = clamp01((value - from.t) / span);
  return hslToRgb(
    from.h + (to.h - from.h) * mix,
    from.s + (to.s - from.s) * mix,
    from.l + (to.l - from.l) * mix,
  );
}

/**
 * 相对强度 → alpha(0~255)。
 * **下限是 0**：低于阈值完全不画；阈值以上用幂曲线抬升，让热点之间的层次拉开。
 */
export function alphaFor(t: number, threshold = HEATMAP_DISPLAY_THRESHOLD): number {
  const value = clamp01(t);
  if (value <= threshold) return 0;
  const ratio = (value - threshold) / (1 - threshold);
  return Math.round(255 * Math.pow(ratio, 1.5));
}

/** 等值带序号（用于画边界线）。 */
export function bandIndexFor(t: number, bands = HEATMAP_BANDS): number {
  return Math.min(bands - 1, Math.floor(clamp01(t) * bands));
}

/** 与栅格同源的 CSS 渐变，供图例使用。 */
export function heatmapRampCss(samples = 12): string {
  const parts: string[] = [];
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const [r, g, b] = rampColor(t);
    parts.push(`rgb(${r},${g},${b}) ${(t * 100).toFixed(1)}%`);
  }
  return `linear-gradient(90deg, ${parts.join(',')})`;
}

/**
 * 数值栅格双线性上采样（不是把图像拉大，所以不会出现糊斑）。
 * `noData` 参与插值时按"有效邻居加权"处理；四邻全无效才输出 noData。
 */
export function upsampleGrid(grid: HeatmapGrid, factor = HEATMAP_UPSCALE): HeatmapGrid {
  const step = Math.max(1, Math.round(factor));
  if (step === 1) return grid;
  const width = grid.width * step;
  const height = grid.height * step;
  const values = new Array<number>(width * height).fill(grid.noData);

  for (let y = 0; y < height; y++) {
    const sourceY = (y + 0.5) / step - 0.5;
    const y0 = Math.floor(sourceY);
    const wy = sourceY - y0;
    for (let x = 0; x < width; x++) {
      const sourceX = (x + 0.5) / step - 0.5;
      const x0 = Math.floor(sourceX);
      const wx = sourceX - x0;
      let weightSum = 0;
      let valueSum = 0;
      for (let dy = 0; dy <= 1; dy++) {
        for (let dx = 0; dx <= 1; dx++) {
          const sx = Math.min(grid.width - 1, Math.max(0, x0 + dx));
          const sy = Math.min(grid.height - 1, Math.max(0, y0 + dy));
          const sample = grid.values[sy * grid.width + sx];
          if (sample === undefined || sample === grid.noData) continue;
          const weight = (dx ? wx : 1 - wx) * (dy ? wy : 1 - wy);
          if (weight <= 0) continue;
          weightSum += weight;
          valueSum += sample * weight;
        }
      }
      if (weightSum > 0) values[y * width + x] = valueSum / weightSum;
    }
  }

  return { ...grid, width, height, values };
}

export interface HeatmapRenderOptions {
  /** 数值上采样倍数，默认 3。 */
  upscale?: number;
  /** 等值带档数，默认 6。 */
  bands?: number;
  /** 显示阈值（相对强度），默认 0.12。 */
  threshold?: number;
  /** 是否画等值带边界线，默认 true。 */
  contours?: boolean;
}

/**
 * 把栅格渲染成可直接交给 `AMap.ImageLayer` 的 canvas。
 * 返回 `null` 表示浏览器不支持 2D canvas。
 */
export function renderHeatmapCanvas(
  grid: HeatmapGrid,
  options: HeatmapRenderOptions = {},
): HTMLCanvasElement | null {
  const bands = options.bands ?? HEATMAP_BANDS;
  const threshold = options.threshold ?? HEATMAP_DISPLAY_THRESHOLD;
  const dense = upsampleGrid(grid, options.upscale ?? HEATMAP_UPSCALE);

  const canvas = document.createElement('canvas');
  canvas.width = dense.width;
  canvas.height = dense.height;
  const context = canvas.getContext('2d');
  if (!context) return null;

  const raster = context.createImageData(dense.width, dense.height);
  const bandOf = new Int16Array(dense.width * dense.height).fill(-1);
  const maxValue = grid.maxValue || 1;

  for (let index = 0; index < dense.values.length; index++) {
    const value = dense.values[index];
    if (value === undefined || value === grid.noData) continue;
    const t = clamp01(value / maxValue);
    const alpha = alphaFor(t, threshold);
    if (alpha <= 0) continue;
    const [r, g, b] = rampColor(t);
    raster.data[index * 4] = r;
    raster.data[index * 4 + 1] = g;
    raster.data[index * 4 + 2] = b;
    raster.data[index * 4 + 3] = alpha;
    bandOf[index] = bandIndexFor(t, bands);
  }

  // 等值带边界：相邻格落在不同档时压暗一格，形成可见的等值线，
  // 让结果读起来像"分层热力图"而不是一团云雾。
  if (options.contours !== false) {
    const width = dense.width;
    for (let y = 0; y < dense.height; y++) {
      for (let x = 0; x < width; x++) {
        const index = y * width + x;
        if (bandOf[index] < 0) continue;
        const right = x + 1 < width ? bandOf[index + 1] : -1;
        const down = y + 1 < dense.height ? bandOf[index + width] : -1;
        const isEdge = (right >= 0 && right !== bandOf[index])
          || (down >= 0 && down !== bandOf[index]);
        if (!isEdge) continue;
        raster.data[index * 4] = Math.round(raster.data[index * 4] * 0.45);
        raster.data[index * 4 + 1] = Math.round(raster.data[index * 4 + 1] * 0.45);
        raster.data[index * 4 + 2] = Math.round(raster.data[index * 4 + 2] * 0.45);
        raster.data[index * 4 + 3] = Math.max(raster.data[index * 4 + 3], 170);
      }
    }
  }

  context.putImageData(raster, 0, 0);
  return canvas;
}
