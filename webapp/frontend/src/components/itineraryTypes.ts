/**
 * 一日行程规划的前端契约。
 *
 * 与后端 `webapp/backend/itinerary.py` 的 `build_chat_reply` 一一对应：
 * 行程数据挂在 `/api/chat` 响应的新增顶层字段 `itinerary` 上，既有字段不变。
 */

export type ItineraryPeriod = 'morning' | 'noon' | 'afternoon';

export interface ItineraryStop {
  /** 从 1 起，决定地图序号与游览顺序 */
  seq: number;
  period: ItineraryPeriod | string;
  periodLabel: string;
  name: string;
  lng: number;
  lat: number;
  campus: string;
  /** 来源场景，如 walk / photo / flower_viewing / study / dining */
  scene?: string | null;
  category?: string | null;
  subCategory?: string | null;
  /** 推荐理由，用于面板展示 */
  reason: string;
  dwellMinutes: number;
}

export interface ItineraryLeg {
  fromSeq: number;
  toSeq: number;
  /** 超过步行距离阈值时降级为 riding，并给出骑行/校车提示 */
  mode: 'walking' | 'riding';
  /** 后端给出的是平面直线估算；前端拿到高德真实算路结果后会覆盖它 */
  distanceMeters: number;
  durationMinutes: number;
  fromName: string;
  toName: string;
  /**
   * true / 缺省表示该腿的距离与时长是后端的直线估算（高德真实路线尚未回填）。
   * 后端用高德 Web 服务算过真实路线时会给出 false，此时前端不得覆盖，以保证
   * 面板数字与对话文案一致；面板据此在数字前加"约"字标出估算。
   */
  estimated?: boolean;
}

/** 某时段没有可用地点时的说明（后端 skipped 字段） */
export interface ItinerarySkippedPeriod {
  period: string;
  label: string;
  reason: string;
}

export interface ItineraryPayload {
  title: string;
  campus: string;
  /** true 表示大模型不可用，文案由确定性模板生成 */
  fallback: boolean;
  stops: ItineraryStop[];
  legs: ItineraryLeg[];
  skipped: ItinerarySkippedPeriod[];
}
