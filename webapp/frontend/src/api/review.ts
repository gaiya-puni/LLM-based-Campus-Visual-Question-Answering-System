/**
 * 审核态开关探测。
 *
 * 为什么需要单独一个接口：`USERDATA_REVIEW_TOKEN` 只存在于后端根 `.env`，
 * 前端不可能也不应该知道密钥本身；它只需要回答"这个部署到底有没有开审核"，
 * 才能决定侧栏要不要出现「评论审核」入口。
 *
 * 注意：这里返回的 `configured` 只是**体验层**的显隐依据。
 * 真正的门禁永远是请求头 `X-Review-Token` —— 没填密钥、密钥不对，管理接口一律 403。
 */

export interface ReviewStatus {
  /** 后端是否已配置审核密钥（长度 ≥ 16 位） */
  configured: boolean;
}

/**
 * 查询审核态开关。
 *
 * 任何异常（网络断开、旧后端没有该路由、返回体不合契约）都向上抛，
 * 由调用方决定降级策略；本函数不做静默兜底，避免"假未知"被误当成"未开启"。
 */
export function fetchReviewStatus(): Promise<ReviewStatus> {
  return fetch('/api/review-status', { headers: { Accept: 'application/json' } })
    .then(response => response.json()
      .catch(() => null)
      .then((payload: any) => {
        if (!response.ok || !payload || payload.success === false
          || typeof payload.configured !== 'boolean') {
          throw new Error(`审核态探测失败（${response.status}）`);
        }
        return { configured: payload.configured };
      }));
}
