/**
 * 高德地图 JS API 就绪等待。
 *
 * index.html 用 `<script async src="https://webapi.amap.com/maps...">` 引入高德脚本，
 * `async` 表示它的下载与执行时机不确定；而 `type="module"` 的入口脚本本身是 defer 的，
 * 因此组件的 onMounted 里 `window.AMap` 可能仍然不存在，直接 `new AMap.Map(...)` 会抛
 * `Cannot read properties of undefined (reading 'Map')`。
 *
 * 这里统一提供 loadAMap()，让所有用到 AMap 的地方都先 await，而不是直接取全局变量。
 */
let ready: Promise<any> | null = null;

const POLL_INTERVAL_MS = 50;
const TIMEOUT_MS = 20000;

export const loadAMap = (): Promise<any> => {
  if (ready) return ready;
  ready = new Promise((resolve, reject) => {
    const started = Date.now();
    const check = () => {
      const amap = (window as any).AMap;
      // 要求 Map 构造器存在：避免脚本"已挂载全局对象但尚未初始化完"的中间态。
      if (amap?.Map) {
        resolve(amap);
        return;
      }
      if (Date.now() - started > TIMEOUT_MS) {
        reject(new Error('高德地图脚本加载超时，请检查网络后刷新页面'));
        return;
      }
      window.setTimeout(check, POLL_INTERVAL_MS);
    };
    check();
  });
  // 失败不缓存，刷新或重试时可以重新等待。
  ready.catch(() => { ready = null; });
  return ready;
};
