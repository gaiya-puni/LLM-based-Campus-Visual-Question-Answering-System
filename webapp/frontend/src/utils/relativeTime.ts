/**
 * 相对时间显示（评论区与审核页共用）。
 *
 * 后端随每条留言返回 `createdAtMs`（epoch 毫秒），前端不解析本地时间字符串，
 * 避免浏览器时区与服务器不一致导致的"刚发的留言显示成 8 小时前"。
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** 绝对时间，用作 `title` 悬停提示。 */
export function formatAbsoluteTime(ms: number): string {
  if (!ms) return '';
  const date = new Date(ms);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatRelativeTime(ms: number, now = Date.now()): string {
  if (!ms) return '';
  const diff = now - ms;
  if (diff < MINUTE) return '刚刚';
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`;
  if (diff < 30 * DAY) return `${Math.floor(diff / DAY)} 天前`;
  const date = new Date(ms);
  const monthDay = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return date.getFullYear() === new Date(now).getFullYear()
    ? monthDay
    : `${date.getFullYear()}-${monthDay}`;
}

/** 超过该字数即为长评（与后端 `LONG_COMMENT_MIN_CHARS` 的兜底值保持一致）。 */
export const DEFAULT_LONG_COMMENT_MIN_CHARS = 80;
