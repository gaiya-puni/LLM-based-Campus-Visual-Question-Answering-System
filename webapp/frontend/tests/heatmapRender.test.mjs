import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/components/heatmapRender.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
});
const { alphaFor, upsampleGrid, renderHeatmapCanvas, quantileBounds, CHAT_HEATMAP_OPTIONS, PREVIEW_HEATMAP_OPTIONS, HEATMAP_UPSCALE, HEATMAP_BLUR_PX } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const previewAlpha = t => alphaFor(t, PREVIEW_HEATMAP_OPTIONS.threshold, PREVIEW_HEATMAP_OPTIONS.alphaExponent);

test('chat can explicitly disable contour outlines while preview keeps its own options', () => {
  assert.equal(PREVIEW_HEATMAP_OPTIONS.contours, false);
});

test('low-intensity outskirts are visible, zero intensity stays transparent', () => {
  assert.equal(previewAlpha(0), 0);
  assert.equal(previewAlpha(0.05), 0);
  assert.ok(previewAlpha(0.12) >= 20);
  assert.ok(previewAlpha(0.25) >= 60);
  assert.equal(previewAlpha(1), 255);
});

test('legacy renderer defaults remain unchanged', () => {
  assert.equal(HEATMAP_UPSCALE, 3);
  assert.equal(HEATMAP_BLUR_PX, 0);
  for (let i = 0; i <= 100; i++) {
    const t = i / 100;
    assert.equal(alphaFor(t), t <= 0.12 ? 0 : Math.round(255 * ((t - 0.12) / 0.88) ** 1.5));
  }
  const grid = {
    width: 2, height: 1, bounds: [0, 0, 1, 1], rowOrder: 'north_to_south',
    values: [1000, -1], maxValue: 1000, noData: -1,
  };
  const original = upsampleGrid(grid, 4);
  const preview = upsampleGrid(grid, 4, true);
  assert.equal(original.values[3], 1000);
  assert.ok(preview.values[3] > 0 && preview.values[3] < 1000);
});

test('canvas paints zero-heat campus context softly but not no-data or previews', () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => {
      const canvas = {};
      const context = {
        createImageData: (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) }),
        putImageData: raster => { canvas.raster = raster; },
        getImageData: () => ({ data: canvas.raster.data.slice() }),
        clearRect: () => {},
      };
      canvas.getContext = () => context;
      return canvas;
    },
  };
  try {
    const grid = {
      width: 2, height: 1, bounds: [0, 0, 1, 1], rowOrder: 'north_to_south',
      values: [0, -1], coverage: [1000, 0], maxValue: 1000, noData: -1,
    };
    const chat = renderHeatmapCanvas(grid, { ...CHAT_HEATMAP_OPTIONS, upscale: 1, blurPx: 0 });
    assert.equal(chat.raster.data[3], CHAT_HEATMAP_OPTIONS.coverageAlpha);
    assert.equal(chat.raster.data[7], 0);
    assert.ok(chat.raster.data[2] > chat.raster.data[0]);
    const preview = renderHeatmapCanvas(grid, { ...PREVIEW_HEATMAP_OPTIONS, upscale: 1, blurPx: 0 });
    assert.equal(preview.raster.data[3], 0);
    assert.deepEqual(grid.values, [0, -1]);
    const edge = renderHeatmapCanvas({ ...grid, values: [1000, -1], coverage: [100, 0] },
      { ...CHAT_HEATMAP_OPTIONS, upscale: 1, blurPx: 0 });
    assert.equal(edge.raster.data[3], 26);
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('opacity increases monotonically without exceeding the alpha range', () => {
  let previous = 0;
  for (let i = 0; i <= 100; i++) {
    const alpha = alphaFor(i / 100);
    assert.ok(alpha >= previous && alpha <= 255);
    previous = alpha;
  }
});

test('interpolation keeps geographic bounds and no-data regions intact', () => {
  const grid = {
    width: 2, height: 2, bounds: [116.30, 39.98, 116.32, 40.00],
    rowOrder: 'north_to_south', values: [-1, -1, -1, -1], maxValue: 1000, noData: -1,
  };
  const dense = upsampleGrid(grid, 4);
  assert.deepEqual(dense.bounds, grid.bounds);
  assert.equal(dense.rowOrder, grid.rowOrder);
  assert.equal(dense.values.length, 64);
  assert.ok(dense.values.every(value => value === -1));
  assert.deepEqual(grid.values, [-1, -1, -1, -1]);
});

test('chat soft coverage is separate from heat values and preview policy', () => {
  assert.equal(CHAT_HEATMAP_OPTIONS.contours, false);
  assert.ok(CHAT_HEATMAP_OPTIONS.blurPx > 0);
  assert.ok(CHAT_HEATMAP_OPTIONS.coverageAlpha >= 45 && CHAT_HEATMAP_OPTIONS.coverageAlpha <= 60);
  assert.equal(PREVIEW_HEATMAP_OPTIONS.coverageAlpha, undefined);
  assert.equal(CHAT_HEATMAP_OPTIONS.clipToData, true);
  assert.equal(PREVIEW_HEATMAP_OPTIONS.clipToData, undefined);
  const grid = {
    width: 2, height: 1, bounds: [0, 0, 1, 1], rowOrder: 'north_to_south',
    values: [0, -1], coverage: [1000, 0], maxValue: 1000, noData: -1,
  };
  const dense = upsampleGrid(grid, 4, true);
  assert.equal(dense.coverage.length, dense.values.length);
  assert.ok(dense.values.every(value => value === 0 || value === -1));
  assert.ok(dense.coverage[3] > 0 && dense.coverage[3] < 1000);
  assert.deepEqual(grid.coverage, [1000, 0]);
});

test('chat clips interpolation and post-blur spill to the original footprint, not previews', () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => {
      const canvas = {};
      const context = {
        createImageData: (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) }),
        putImageData: raster => { canvas.raster = raster; },
        getImageData: () => ({ data: canvas.raster.data.slice() }),
        clearRect: () => {},
        // Deliberately spill alpha to every pixel, including no-data pixels.
        drawImage: source => {
          canvas.raster = { data: source.raster.data.slice() };
          for (let i = 3; i < canvas.raster.data.length; i += 4) canvas.raster.data[i] = 80;
        },
      };
      canvas.getContext = () => context;
      return canvas;
    },
  };
  try {
    const grid = {
      width: 3, height: 2, bounds: [0, 0, 1, 1], rowOrder: 'north_to_south',
      values: [1000, -1, 0, -1, 500, -1], coverage: [1000, 0, 1000, 0, 1000, 0], maxValue: 1000, noData: -1,
    };
    for (const blurPx of [0, 6]) {
      const chat = renderHeatmapCanvas(grid, { ...CHAT_HEATMAP_OPTIONS, upscale: 3, blurPx });
      for (let y = 0; y < chat.height; y++) {
        for (let x = 0; x < chat.width; x++) {
          const sample = grid.values[Math.floor(y / 3) * grid.width + Math.floor(x / 3)];
          const alpha = chat.raster.data[(y * chat.width + x) * 4 + 3];
          if (sample === -1) assert.equal(alpha, 0);
          else assert.ok(alpha > 0);
        }
      }
    }
    const preview = renderHeatmapCanvas(grid, { ...PREVIEW_HEATMAP_OPTIONS, upscale: 3 });
    assert.equal(preview.raster.data[(0 * preview.width + 4) * 4 + 3], 80);
    assert.deepEqual(grid.values, [1000, -1, 0, -1, 500, -1]);
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test('chat low and medium heat remain clear on bright tiles without changing preview', () => {
  const chatAlpha = t => alphaFor(t, CHAT_HEATMAP_OPTIONS.threshold, CHAT_HEATMAP_OPTIONS.alphaExponent);
  assert.equal(chatAlpha(0), 0);
  assert.ok(chatAlpha(0.1) >= 45);
  assert.ok(chatAlpha(0.25) >= 95);
  assert.ok(chatAlpha(0.5) >= 155);
  assert.equal(chatAlpha(1), 255);
  for (const t of [0.1, 0.25, 0.5]) {
    assert.ok(chatAlpha(t) > alphaFor(t, 0.04, 1.1));
  }
  assert.equal(PREVIEW_HEATMAP_OPTIONS.threshold, 0.05);
  assert.equal(PREVIEW_HEATMAP_OPTIONS.alphaExponent, 0.9);
  assert.equal(PREVIEW_HEATMAP_OPTIONS.blurPx, 10);
  assert.equal(PREVIEW_HEATMAP_OPTIONS.stretch, 'quantile');
  assert.equal(CHAT_HEATMAP_OPTIONS.stretch, undefined);
});

test('quantile stretch only changes preview display, not values or chat output', () => {
  const values = Array.from({ length: 100 }, (_, index) => 100 + index * 5);
  const bounds = quantileBounds(values, -1, 0.1, 0.95);
  assert.deepEqual(bounds, [145, 570]);
  assert.equal(quantileBounds([1, 2, 3], -1), null);
  assert.equal(quantileBounds([1000, -1, -1, -1, -1, -1, -1, -1], -1), null);

  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => {
      const canvas = {};
      const context = {
        createImageData: (width, height) => ({ data: new Uint8ClampedArray(width * height * 4) }),
        putImageData: raster => { canvas.raster = raster; },
        getImageData: () => ({ data: canvas.raster.data.slice() }),
        clearRect: () => {},
      };
      canvas.getContext = () => context;
      return canvas;
    },
  };
  try {
    const grid = {
      width: 10, height: 10, bounds: [0, 0, 1, 1], rowOrder: 'north_to_south',
      values: new Array(100).fill(0).map((_, index) => 200 + index * 5),
      maxValue: 1000, noData: -1,
    };
    const before = grid.values.slice();
    const linear = renderHeatmapCanvas(grid, { upscale: 1, blurPx: 0, threshold: 0.05, contours: false });
    const quantile = renderHeatmapCanvas(grid, {
      upscale: 1, blurPx: 0, threshold: 0.05, contours: false, stretch: 'quantile',
    });
    const linearMid = linear.raster.data[(5 * 10 + 5) * 4 + 3];
    const quantileMid = quantile.raster.data[(5 * 10 + 5) * 4 + 3];
    assert.ok(quantileMid > linearMid);
    assert.deepEqual(grid.values, before);
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});
