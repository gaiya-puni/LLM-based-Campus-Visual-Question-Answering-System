import { ref, type Ref } from 'vue';
import { fetchReviewStatus } from '../api/review';

/**
 * 审核态开关的全局单例（写法对齐 `useIsMobile` / `api/session`）。
 *
 * 三态语义：
 * - `true`  —— 后端已配置审核密钥，显示「评论审核」入口；
 * - `false` —— 确认为未配置，隐藏入口、路由守卫挡回去；
 * - `null`  —— 还不知道（首次加载中、探测失败、旧后端没有该路由）。
 *
 * 为什么 `null` 要"显示入口"而不是隐藏：旧后端或一次网络抖动不应该把管理员
 * 的功能入口藏起来；宁可让入口点到 403 提示，也不要让人找不到页面。
 */

const enabled = ref<boolean | null>(null);

let inflight: Promise<boolean | null> | null = null;
/** 上一次探测的时间戳：失败后保持 null 时，避免每次路由跳转都重打一次网络。 */
let lastAttempt = 0;

/** 探测失败后的重试间隔（毫秒）。 */
const RETRY_INTERVAL_MS = 30_000;

/** 返回全局共享的审核态（`null` 表示未知）。 */
export function useReviewEnabled(): Ref<boolean | null> {
  return enabled;
}

/** 入口是否显示：只有"确认未配置"才隐藏。 */
export function showReviewEntry(): boolean {
  return enabled.value !== false;
}

/**
 * 确认一次审核态；已有结果或正在请求时直接复用。
 *
 * @param force 忽略缓存强制刷新（例如管理员刚改完 `.env` 重启后端）
 */
export function ensureReviewAccess(force = false): Promise<boolean | null> {
  if (inflight) return inflight;
  if (!force && enabled.value !== null) return Promise.resolve(enabled.value);
  if (!force && Date.now() - lastAttempt < RETRY_INTERVAL_MS) {
    return Promise.resolve(enabled.value);
  }
  lastAttempt = Date.now();
  inflight = fetchReviewStatus()
    .then(result => {
      enabled.value = result.configured;
      return enabled.value;
    })
    .catch(() => {
      // 保持 null（未知）——入口照常显示，后端仍会用 403 兜住
      console.warn('审核态探测失败，入口按"未知"处理');
      return enabled.value;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** 清空缓存（退出登录、切换部署时可调用）。 */
export function resetReviewAccess(): void {
  enabled.value = null;
  lastAttempt = 0;
  inflight = null;
}
