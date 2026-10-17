#!/usr/bin/env node
/**
 * 移动端多视口巡检：在多种手机 / 平板尺寸下逐个打开全部路由页面，
 * 断言不出现横向溢出，并逐页截图归档，替代原先"手工逐页 resize + 截图"的验证方式。
 *
 *   npm run test:mobile                  # 构建（产物缺失时）+ 起 preview + 巡检 + 截图
 *   npm run test:mobile -- --skip-build  # 复用已有构建产物
 *   npm run test:mobile -- --only map    # 只巡检路由名包含 map 的页面
 *   npm run test:mobile -- --headed      # 显示浏览器窗口（排查用）
 *
 * 浏览器来源：复用本机已安装的 Edge / Chrome，**不下载任何浏览器**。
 * 探测顺序可用环境变量 MOBILE_TEST_BROWSER 覆盖（直接给可执行文件绝对路径）。
 *
 * 结果：截图与报告写入 tests/mobile-snapshots/（该目录已被 .gitignore 忽略）。
 * 存在横向溢出时进程以非零退出码结束，便于后续接入 CI。
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BUILD_INDEX = path.join(FRONTEND_DIR, 'build', 'index.html');
const SNAPSHOT_DIR = path.join(FRONTEND_DIR, 'tests', 'mobile-snapshots');
const VITE_BIN = path.join(FRONTEND_DIR, 'node_modules', 'vite', 'bin', 'vite.js');
const PREVIEW_PORT = 4173;
const READY_TIMEOUT_MS = 30_000;
const PAGE_TIMEOUT_MS = 25_000;
/** 允许 1px 的取整误差，避免亚像素导致的假阳性 */
const OVERFLOW_TOLERANCE = 1;

const VIEWPORTS = [
  { width: 320, height: 568, label: '小屏手机' },
  { width: 375, height: 667, label: 'iPhone SE / 8' },
  { width: 390, height: 844, label: 'iPhone 12-15' },
  { width: 414, height: 896, label: 'iPhone XR / 11' },
  { width: 768, height: 1024, label: '平板竖屏（骨架断点临界）' },
  { width: 800, height: 600, label: '桌面骨架窄容器（769-900px 溢出临界区）' },
  { width: 1024, height: 768, label: '平板横屏 / 小桌面' },
  { width: 1440, height: 900, label: '桌面宽屏（零回归对照）' },
];

/**
 * 页内分支：关键图表藏在页内切换之后（默认首屏测不到，而这正是最容易撑破页面的部分），
 * 这里逐一走到每个分支再判定与截图。suffix 用于截图文件名区分。
 */
const PAGE_VARIANTS = {
  classify: [
    { suffix: '', label: '植物类型 · 旭日图', selector: '', text: '' },
    { suffix: '-adoption', label: '领养情况 · 桑基图', selector: '.el-radio-button__inner', text: '领养情况' },
    { suffix: '-3d', label: '植物三维 · 平行坐标', selector: '.el-radio-button__inner', text: '植物三维' },
  ],
};

const BROWSER_CANDIDATES = [
  process.env.MOBILE_TEST_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome',
  '/usr/bin/microsoft-edge',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

const argv = process.argv.slice(2);
const hasFlag = (name) => argv.includes(name);
const valueOf = (name, fallback) => {
  const index = argv.indexOf(name);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};

const skipBuild = hasFlag('--skip-build');
const headed = hasFlag('--headed');
const only = valueOf('--only', '');

/** 从路由表读取页面，避免新增路由后漏测 */
function readRoutes() {
  const source = readFileSync(path.join(FRONTEND_DIR, 'src', 'router', 'index.ts'), 'utf8');
  const routes = [];
  const pattern = /path:\s*"([^"]+)"[\s\S]*?name:\s*"([^"]+)"/g;
  let matched = pattern.exec(source);
  while (matched) {
    routes.push({ route: matched[1], name: matched[2] });
    matched = pattern.exec(source);
  }
  return routes;
}

function resolveBrowserPath() {
  for (const candidate of BROWSER_CANDIDATES) {
    if (candidate && existsSync(candidate)) return candidate;
  }
  return null;
}

const isPortOpen = (host, port, timeout = 600) => new Promise((resolve) => {
  const socket = net.connect({ host, port });
  const finish = (open) => { socket.destroy(); resolve(open); };
  socket.setTimeout(timeout);
  socket.once('connect', () => finish(true));
  socket.once('timeout', () => finish(false));
  socket.once('error', () => finish(false));
});

const waitForPort = async (host, port, timeout = READY_TIMEOUT_MS) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await isPortOpen(host, port)) return true;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
};

const children = [];
function spawnChild(command, args, options) {
  const child = spawn(command, args, { stdio: 'inherit', ...options });
  children.push(child);
  return child;
}

let shuttingDown = false;
function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null && !child.killed) {
      try { child.kill(); } catch { /* 进程已退出时忽略 */ }
    }
  }
  setTimeout(() => process.exit(code), 300);
}
process.on('SIGINT', () => shutdown(1));
process.on('SIGTERM', () => shutdown(1));

const line = (text = '') => console.log(text);

/** 转发子进程输出，并滤掉后端未启动时的 /api 代理报错噪音（对布局判定无意义） */
const NOISE_PATTERN = /http proxy error|^AggregateError|afterConnectMultiple|internalConnectMultiple|^ECONNREFUSED/;
const ANSI_PATTERN = /\u001b\[[0-9;]*m/g;
function pipeFiltered(stream, target) {
  let buffer = '';
  stream.on('data', (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? '';
    for (const raw of lines) {
      const text = raw.replace(ANSI_PATTERN, '').trimEnd();
      if (!text || NOISE_PATTERN.test(text)) continue;
      target.write(`${text}\n`);
    }
  });
}

/** 在页面内执行的溢出探测：同时给出越界元素线索，便于定位 */
const OVERFLOW_PROBE = `(() => {
  const viewport = window.innerWidth;
  const doc = document.documentElement;
  const result = { viewport, scrollWidth: doc.scrollWidth, offenders: [], clipped: [], chartBoxes: [] };

  // 固定宽度的宽图表若外层容器不可横向滚动，内容会被直接裁掉（看不见、也滑不到）。
  // 这种问题不会撑破页面，上面的 scrollWidth 判定抓不到，必须单独检查。
  for (const canvas of document.querySelectorAll('.chart-canvas')) {
    const box = canvas.parentElement;
    if (!box) continue;
    const overflowX = getComputedStyle(box).overflowX;
    const scrollable = overflowX === 'auto' || overflowX === 'scroll';
    const neededWidth = canvas.getBoundingClientRect().width;
    const entry = {
      container: typeof box.className === 'string' ? box.className : '',
      neededWidth: Math.round(neededWidth),
      visibleWidth: Math.round(box.clientWidth),
      overflowX,
      scrollable,
    };
    result.chartBoxes.push(entry);
    if (neededWidth > box.clientWidth + ${OVERFLOW_TOLERANCE} && !scrollable) {
      result.clipped.push(entry);
    }
  }

  if (result.scrollWidth <= viewport + ${OVERFLOW_TOLERANCE}) return result;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    if (rect.right <= viewport + ${OVERFLOW_TOLERANCE} && rect.left >= -${OVERFLOW_TOLERANCE}) continue;
    offenders.push({
      tag: el.tagName.toLowerCase(),
      className: typeof el.className === 'string' ? el.className.slice(0, 100) : '',
      left: Math.round(rect.left),
      right: Math.round(rect.right),
      width: Math.round(rect.width),
    });
    if (offenders.length >= 8) break;
  }
  result.offenders = offenders;
  return result;
})()`;

const STABILIZE = `(async () => {
  if (document.fonts && document.fonts.ready) {
    await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2000))]);
  }
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
})()`;

async function run() {
  const browserPath = resolveBrowserPath();
  if (!browserPath) {
    line('[错误] 没有找到可用的本机浏览器（Edge / Chrome）。');
    line('       本脚本刻意不下载浏览器，请安装 Edge 或 Chrome，');
    line('       或用环境变量指定绝对路径：MOBILE_TEST_BROWSER="C:\\...\\chrome.exe"');
    return 1;
  }

  if (!existsSync(VITE_BIN)) {
    line(`[错误] 未找到 ${VITE_BIN}，请先在 webapp/frontend 执行 npm install。`);
    return 1;
  }

  if (only) {
    line(`[提示] 只巡检路由名包含 "${only}" 的页面`);
  }
  const routes = readRoutes().filter((item) => !only || item.name.includes(only) || item.route.includes(only));
  if (!routes.length) {
    line('[错误] 没有匹配到任何路由，请检查 --only 关键字。');
    return 1;
  }

  if (skipBuild) {
    line('[构建] 已按 --skip-build 跳过。');
  } else if (existsSync(BUILD_INDEX)) {
    line('[构建] 已有构建产物，跳过构建（需要重新构建时先删除 webapp/frontend/build/ 再重跑）。');
  } else {
    line('[构建] 未发现构建产物，执行 vite build …');
    const build = spawnChild(process.execPath, [VITE_BIN, 'build'], { cwd: FRONTEND_DIR });
    const buildCode = await new Promise((resolve) => build.on('exit', resolve));
    if (buildCode !== 0) {
      line(`[错误] 构建失败（退出码 ${buildCode}）。`);
      return 1;
    }
  }

  if (!existsSync(BUILD_INDEX)) {
    line(`[错误] 未找到 ${BUILD_INDEX}，请先执行 npm run build。`);
    return 1;
  }

  if (await isPortOpen('127.0.0.1', PREVIEW_PORT)) {
    line(`[错误] 端口 ${PREVIEW_PORT} 已被占用，请先关闭占用该端口的进程。`);
    return 1;
  }

  line(`[预览] 启动 vite preview --port ${PREVIEW_PORT} …`);
  const preview = spawnChild(
    process.execPath,
    [VITE_BIN, 'preview', '--port', String(PREVIEW_PORT), '--strictPort', '--host', '127.0.0.1'],
    { cwd: FRONTEND_DIR, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  pipeFiltered(preview.stdout, process.stdout);
  pipeFiltered(preview.stderr, process.stderr);
  if (!await waitForPort('127.0.0.1', PREVIEW_PORT)) {
    line(`[错误] 预览服务在 ${READY_TIMEOUT_MS / 1000} 秒内未就绪。`);
    return 1;
  }

  if (!await isPortOpen('127.0.0.1', 5000)) {
    line('[提示] 未检测到后端（127.0.0.1:5000），页面里的 /api 请求会失败并显示错误提示；');
    line('       这不影响布局与横向溢出的判定。需要完整数据时请先启动后端再重跑。');
  }

  const { chromium } = await import('playwright-core');
  line(`[浏览器] 使用本机浏览器：${browserPath}`);
  const browser = await chromium.launch({
    executablePath: browserPath,
    headless: !headed,
    // 显式禁用 HTTP 缓存：preview 的静态资源带长缓存头，
    // 重新构建后若命中旧缓存会得到"改了没生效"的假结论。
    args: ['--disable-http-cache', '--disable-application-cache'],
  });

  const results = [];
  let round = 0;
  try {
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        hasTouch: true,
        isMobile: true,
        deviceScaleFactor: 1,
      });
      const page = await context.newPage();
      page.setDefaultTimeout(PAGE_TIMEOUT_MS);
      const dir = path.join(SNAPSHOT_DIR, `${viewport.width}x${viewport.height}`);
      mkdirSync(dir, { recursive: true });

      line('');
      line(`— 视口 ${viewport.width}x${viewport.height}（${viewport.label}）—`);

      for (const item of routes) {
        const variants = PAGE_VARIANTS[item.name] || [{ suffix: '', label: '', selector: '', clickIndex: null }];

        for (const variant of variants) {
          // 带上每次递增的查询参数：hash 路由下仅改 hash 不会整页重载，
          // 上一页残留的抽屉 / 弹窗会拦截后续操作。
          const url = `http://127.0.0.1:${PREVIEW_PORT}/?r=${round++}#${item.route}`;
          const warnings = [];
          try {
            await page.goto(url, { waitUntil: 'load', timeout: PAGE_TIMEOUT_MS });
          } catch (error) {
            warnings.push(`加载超时（${error instanceof Error ? error.message.split('\n')[0] : error}）`);
          }
          await page.waitForTimeout(400);

          if (variant.text) {
            try {
              await page.locator(variant.selector, { hasText: variant.text }).first().click({ timeout: 5000 });
              await page.waitForTimeout(600);
            } catch (error) {
              warnings.push(`切换到「${variant.label}」失败（${error instanceof Error ? error.message.split('\n')[0] : error}）`);
            }
            // 切换后必须确认分支内容真的出现了，否则会"点了没生效却算通过"
            const appeared = await page.locator('.chart-canvas').count();
            if (!appeared) {
              warnings.push(`未检测到「${variant.label}」的图表容器，本项结论仅供参考`);
            }
          }

          try {
            await page.evaluate(STABILIZE);
          } catch { /* 页面已被导航打断时忽略 */ }
          await page.waitForTimeout(250);

          const probe = await page.evaluate(OVERFLOW_PROBE);
          const overflowPx = probe.scrollWidth - probe.viewport;
          const pageOverflow = overflowPx > OVERFLOW_TOLERANCE;
          const clippedCharts = probe.clipped.length > 0;
          const pass = !pageOverflow && !clippedCharts;

          const caseName = `${item.name}${variant.suffix}`;
          const shot = path.join(dir, `${caseName}.png`);
          try {
            await page.screenshot({ path: shot, fullPage: false });
          } catch (error) {
            warnings.push(`截图失败（${error instanceof Error ? error.message.split('\n')[0] : error}）`);
          }

          results.push({
            viewport: `${viewport.width}x${viewport.height}`,
            page: caseName,
            route: item.route,
            variant: variant.label,
            pass,
            scrollWidth: probe.scrollWidth,
            viewportWidth: probe.viewport,
            overflowPx,
            offenders: probe.offenders,
            clippedCharts: probe.clipped,
            chartBoxes: probe.chartBoxes,
            screenshot: path.relative(FRONTEND_DIR, shot).replace(/\\/g, '/'),
            warnings,
          });

          const reasons = [];
          if (pageOverflow) reasons.push(`页面横向溢出 ${overflowPx}px`);
          if (clippedCharts) reasons.push(`宽图表被裁切 ${probe.clipped.length} 处`);
          const mark = pass ? '通过' : reasons.join('；');
          const caption = variant.label ? `${caseName} [${variant.label}]` : caseName;
          line(`  ${pass ? '[通过]' : '[问题]'} ${caption} — ${mark}${warnings.length ? `（${warnings.join('；')}）` : ''}`);
          if (pageOverflow) {
            for (const offender of probe.offenders) {
              line(`         ↳ <${offender.tag} class="${offender.className}"> left=${offender.left} right=${offender.right} width=${offender.width}`);
            }
          }
          for (const chart of probe.clipped) {
            line(`         ↳ 容器 .${chart.container}：画布需 ${chart.neededWidth}px、可视 ${chart.visibleWidth}px、overflow-x=${chart.overflowX}`);
          }
        }
      }

      await context.close();
    }
  } finally {
    await browser.close();
  }

  mkdirSync(SNAPSHOT_DIR, { recursive: true });
  const failed = results.filter((item) => !item.pass);
  const report = {
    generatedAt: new Date().toISOString(),
    browser: browserPath,
    viewports: VIEWPORTS.map((item) => `${item.width}x${item.height}`),
    pages: routes.map((item) => item.name),
    total: results.length,
    failed: failed.length,
    results,
  };
  writeFileSync(path.join(SNAPSHOT_DIR, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');

  line('');
  line('=============================');
  line(`共 ${results.length} 项检查，通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  if (failed.length) {
    line('存在问题的页面：');
    for (const item of failed) {
      const detail = [];
      if (item.overflowPx > OVERFLOW_TOLERANCE) detail.push(`横向溢出 ${item.overflowPx}px`);
      if (item.clippedCharts?.length) detail.push(`宽图表被裁切 ${item.clippedCharts.length} 处`);
      line(`  - ${item.viewport}  ${item.page}（${item.route}）：${detail.join('；')}`);
    }
  }
  line(`截图与报告：${path.relative(FRONTEND_DIR, SNAPSHOT_DIR).replace(/\\/g, '/')}`);
  line('=============================');

  return failed.length ? 1 : 0;
}

run()
  .then((code) => shutdown(code))
  .catch((error) => {
    console.error('[错误]', error);
    shutdown(1);
  });
