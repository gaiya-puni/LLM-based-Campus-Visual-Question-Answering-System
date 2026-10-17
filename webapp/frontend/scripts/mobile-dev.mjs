#!/usr/bin/env node
/**
 * 真机局域网联调：一条命令拉起前端 dev server（可选同时拉起后端），
 * 并打印手机可直接打开的局域网地址。
 *
 *   npm run dev:mobile                        # 前端 + 后端（后端已在运行则复用）
 *   npm run dev:mobile -- --no-backend         # 只起前端（后端你另开终端跑）
 *   npm run dev:mobile -- --port 5174          # 换一个前端端口
 *
 * 为什么不用 80 端口：vite.config.ts 默认 port 为 80，Windows 下绑定 80 需要管理员权限，
 * 且容易与 IIS 等既有服务冲突。这里改用高位端口（默认 5173），不修改 vite.config.ts，
 * 桌面端 `npm run dev`（走 80）流程完全不受影响。
 *
 * 手机与后端的通信经由 Vite 的 /api 代理（服务端代理到 127.0.0.1:5000），
 * 因此手机不需要直连 5000 端口，后端只需要在开发机上跑着即可。
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND_DIR = path.resolve(FRONTEND_DIR, '..', 'backend');
const BACKEND_PORT = 5000;
const DEFAULT_PORT = 5173;
const READY_TIMEOUT_MS = 30_000;

const argv = process.argv.slice(2);
const hasFlag = (name) => argv.includes(name);
const valueOf = (name, fallback) => {
  const index = argv.indexOf(name);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};

const port = Number(valueOf('--port', DEFAULT_PORT));
if (!Number.isInteger(port) || port <= 0 || port > 65535) {
  console.error(`[错误] --port 需要 1-65535 的整数，收到：${valueOf('--port', '')}`);
  process.exit(1);
}
const withBackend = !hasFlag('--no-backend');
const withQr = !hasFlag('--no-qr');

/** 本机可用于手机访问的 IPv4 地址（私有网段优先） */
function lanAddresses() {
  const isPrivate = (ip) => /^192\.168\./.test(ip)
    || /^10\./.test(ip)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(ip);
  const addresses = [];
  for (const infos of Object.values(os.networkInterfaces())) {
    for (const info of infos || []) {
      if (info.family !== 'IPv4' || info.internal) continue;
      if (info.address.startsWith('169.254.')) continue; // APIPA：拿不到 DHCP 时的自分配地址
      addresses.push(info.address);
    }
  }
  return addresses.sort((a, b) => Number(isPrivate(b)) - Number(isPrivate(a)));
}

const isPortOpen = (host, targetPort, timeout = 600) => new Promise((resolve) => {
  const socket = net.connect({ host, port: targetPort });
  const finish = (open) => { socket.destroy(); resolve(open); };
  socket.setTimeout(timeout);
  socket.once('connect', () => finish(true));
  socket.once('timeout', () => finish(false));
  socket.once('error', () => finish(false));
});

const waitForPort = async (host, targetPort, timeout = READY_TIMEOUT_MS) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await isPortOpen(host, targetPort)) return true;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
};

/** 只读取 VITE_APP_API_HOST（不读取、不打印任何密钥类字段） */
function readApiHost() {
  const envPath = path.join(FRONTEND_DIR, '.env');
  if (!existsSync(envPath)) return null;
  const matched = readFileSync(envPath, 'utf8').match(/^\s*VITE_APP_API_HOST\s*=\s*(.+?)\s*$/m);
  return matched ? matched[1] : null;
}

const children = [];
function spawnChild(command, args, options) {
  const child = spawn(command, args, { stdio: 'inherit', ...options });
  children.push(child);
  return child;
}

let shuttingDown = false;
function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null && !child.killed) {
      try { child.kill(); } catch { /* 进程已退出时忽略 */ }
    }
  }
  setTimeout(() => process.exit(0), 300);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

async function printQr(url) {
  if (!withQr) return;
  try {
    const mod = await import('qrcode-terminal');
    const qrcode = mod.default ?? mod;
    qrcode.generate(url, { small: true });
  } catch {
    // 未安装 qrcode-terminal 时静默跳过，二维码只是可选增强
  }
}

const line = (text = '') => console.log(text);

async function main() {
  line('=== 校园可视可答系统 · 移动端局域网联调 ===');

  const addresses = lanAddresses();
  if (!addresses.length) {
    line('[警告] 没有找到可用的局域网 IPv4 地址，请确认已连接 Wi-Fi / 网线。');
  } else {
    line(`本机局域网地址：${addresses.join('  ')}`);
  }

  const apiHost = readApiHost();
  const envPath = path.join(FRONTEND_DIR, '.env');
  if (!apiHost) {
    line(`[警告] 未在 ${path.relative(process.cwd(), envPath)} 中找到 VITE_APP_API_HOST。`);
    line('       /api 代理没有目标地址，手机上的问答与热图接口会失败（页面布局仍可查看）。');
    line('       可复制 .env.example 为 .env 后填写，例如 VITE_APP_API_HOST=http://127.0.0.1:5000');
  } else if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(apiHost)) {
    line(`[提示] VITE_APP_API_HOST 当前为 ${apiHost}（不是本机 127.0.0.1）。`);
    line('       如果指向的是远程后端，请确认那是你这次要联调的目标。');
  }

  if (withBackend) {
    const running = await isPortOpen('127.0.0.1', BACKEND_PORT);
    if (running) {
      line(`[后端] 127.0.0.1:${BACKEND_PORT} 已在运行，直接复用。`);
    } else if (!existsSync(path.join(BACKEND_DIR, 'server.py'))) {
      line(`[警告] 未找到 ${path.join(BACKEND_DIR, 'server.py')}，跳过后端启动。`);
    } else {
      line(`[后端] 启动 python server.py（工作目录 ${BACKEND_DIR}）…`);
      const backend = spawnChild('python', ['server.py'], { cwd: BACKEND_DIR });
      backend.on('error', (error) => {
        line(`[警告] 后端启动失败（${error.message}）。`);
        line('       请确认 python 在 PATH 中，或另开终端手动执行：cd webapp/backend; python server.py');
      });
      const backendReady = await waitForPort('127.0.0.1', BACKEND_PORT, 20_000);
      line(backendReady
        ? `[后端] 已就绪：http://127.0.0.1:${BACKEND_PORT}`
        : '[警告] 后端在 20 秒内未就绪，请查看上方日志（首次启动或数据库未配置时会较慢）。');
    }
  } else {
    line('[后端] 已按 --no-backend 跳过，请自行确保后端在运行。');
  }

  const viteBin = path.join(FRONTEND_DIR, 'node_modules', 'vite', 'bin', 'vite.js');
  if (!existsSync(viteBin)) {
    line(`[错误] 未找到 ${viteBin}`);
    line('       请先在 webapp/frontend 执行：npm install');
    process.exit(1);
  }

  if (await isPortOpen('127.0.0.1', port)) {
    line(`[错误] 端口 ${port} 已被占用，请用 --port 换一个端口。`);
    shutdown();
    return;
  }

  line(`[前端] 启动 vite --host --port ${port}（热更新已开启，改代码手机端会自动刷新）…`);
  spawnChild(process.execPath, [viteBin, '--host', '--port', String(port), '--strictPort'], {
    cwd: FRONTEND_DIR,
  });

  const ready = await waitForPort('127.0.0.1', port);
  if (!ready) {
    line(`[错误] 前端在 ${READY_TIMEOUT_MS / 1000} 秒内未就绪，请查看上方日志。`);
    shutdown();
    return;
  }

  const accessUrl = addresses.length ? `http://${addresses[0]}:${port}/` : null;

  line('');
  line('---------------------------------------------');
  line('前端已就绪，手机浏览器打开下面地址：');
  if (accessUrl) line(`  ${accessUrl}`);
  line(`  （本机自测：http://localhost:${port}/）`);
  line('---------------------------------------------');
  if (accessUrl) await printQr(accessUrl);

  line('');
  line('注意事项：');
  line('1. 手机需与本机连同一个 Wi-Fi；公司/校园网络可能隔离了设备互访，必要时改用手机热点。');
  line('2. 首次启动若弹出 Windows 防火墙提示，请勾选“专用网络”并允许访问，否则手机连不上。');
  line('3. 地图空白但页面布局正常，通常是高德密钥绑定了域名白名单（IP 访问不被允许），');
  line('   这属于预期现象，不影响布局与交互的联调；需要验证地图时请用线上站点或 localhost。');
  line('4. 相机与定位在 http 下可用（本项目相机走 file input，定位由浏览器授权）。');
  line('5. 按 Ctrl+C 结束本次联调，脚本会一并关闭它拉起的进程。');
  line('');
  line('打开页面：/#/index 主页，/#/map 地图，/#/chatbot 问答，/#/place-review 数据审核');
  line('');

  // 前端进程退出（如崩溃或被手动结束）时，一并收尾，避免留下孤儿后端进程
  const frontend = children[children.length - 1];
  frontend.on('exit', (code) => {
    if (!shuttingDown) {
      line(`[前端] 进程已退出（code=${code}），正在收尾…`);
      shutdown();
    }
  });
}

main().catch((error) => {
  console.error('[错误]', error);
  shutdown();
});
