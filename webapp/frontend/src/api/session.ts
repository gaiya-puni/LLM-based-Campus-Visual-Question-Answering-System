/**
 * 登录态探测。
 *
 * 为什么需要单独一个接口：会话 Cookie 是 HttpOnly 的，前端拿不到，
 * 只能问后端"我现在是不是登录状态"。评论区的"未登录只能看、点操作引导登录"
 * 依赖这个字段，所以做成模块级缓存 + 主动刷新，避免每条评论都打一次网络。
 */

export interface SessionState {
  loggedIn: boolean;
  username: string;
}

let cached: SessionState = { loggedIn: false, username: '' };
let inflight: Promise<SessionState> | null = null;

/** 同步读取上一次探测结果（可能是初始值）；界面首帧用它避免按钮闪烁。 */
export function currentSession(): SessionState {
  return cached;
}

/** 强制向后端确认一次登录态（登录/退出后调用）。 */
export function refreshSession(): Promise<SessionState> {
  if (inflight) return inflight;
  inflight = fetch('/api/session')
    .then(response => (response.ok ? response.json() : null))
    .then((payload: any) => {
      cached = {
        loggedIn: Boolean(payload?.loggedIn),
        username: String(payload?.username || ''),
      };
      return cached;
    })
    .catch(() => cached)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}
