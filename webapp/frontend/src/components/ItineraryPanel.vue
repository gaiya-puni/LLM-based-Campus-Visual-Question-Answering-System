<template>
  <section v-if="itinerary && itinerary.stops.length" class="itinerary-panel" aria-label="一日行程规划">
    <header class="panel-head">
      <div class="head-text">
        <h2>{{ itinerary.title }}</h2>
        <span class="campus-tag">{{ itinerary.campus }}</span>
      </div>
      <button
        class="collapse"
        :aria-expanded="!collapsed"
        :title="collapsed ? '展开行程' : '收起行程'"
        @click="collapsed = !collapsed"
      >{{ collapsed ? '展开' : '收起' }}</button>
    </header>

    <p v-if="itinerary.fallback" class="fallback-note" role="status">
      大模型文案暂不可用，已改用默认方案，行程结构不受影响。
    </p>

    <ol v-show="!collapsed" class="timeline">
      <template v-for="group in timeline" :key="group.period">
        <li v-if="!group.rows.length" class="skip-row">
          <span class="skip-dot" aria-hidden="true"></span>
          <p><strong>{{ group.label }}</strong>暂无合适地点，这一段可以自由安排。</p>
        </li>
        <template v-for="row in group.rows" :key="row.stop.seq">
          <li class="stop-row" :style="{ '--delay': `${(row.stop.seq - 1) * 70}ms` }">
            <button
              class="badge"
              :title="`在地图上定位第 ${row.stop.seq} 站`"
              :aria-label="`在地图上定位第 ${row.stop.seq} 站：${row.stop.name}`"
              @click="emit('focus', row.stop)"
            >{{ row.stop.seq }}</button>
            <div
              class="card"
              role="button"
              tabindex="0"
              @click="emit('focus', row.stop)"
              @keydown.enter.self.prevent="emit('focus', row.stop)"
              @keydown.space.self.prevent="emit('focus', row.stop)"
            >
              <div class="card-head">
                <span class="stop-name">{{ row.stop.name }}</span>
                <span class="period-chip">{{ row.stop.periodLabel }}</span>
              </div>
              <p class="reason">{{ row.stop.reason }}</p>
              <div class="card-foot">
                <span class="dwell">建议停留 {{ row.stop.dwellMinutes }} 分钟</span>
                <button class="go" @click.stop="emit('navigate', row.stop)">导航</button>
              </div>
            </div>
          </li>
          <!-- 通行段紧跟在"出发那一站"之后：同一时段内与跨时段的连接都会显示出来 -->
          <li v-if="row.leg" class="leg-row" :class="{ riding: row.leg.mode !== 'walking' }">
            <span class="leg-line" aria-hidden="true"></span>
            <span class="leg-text">{{ legText(row.leg) }}</span>
          </li>
        </template>
      </template>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ItineraryLeg, ItineraryPayload, ItineraryStop } from './itineraryTypes';

const props = defineProps<{
  itinerary: ItineraryPayload | null;
}>();

const emit = defineEmits<{
  (event: 'focus', stop: ItineraryStop): void;
  (event: 'navigate', stop: ItineraryStop): void;
}>();

const PERIOD_ORDER = ['morning', 'noon', 'afternoon'];
const PERIOD_LABELS: Record<string, string> = { morning: '上午', noon: '中午', afternoon: '下午' };

const collapsed = ref(false);

/** 按上午/中午/下午分组；**每一站**都带上"从该站出发"的通行段。 */
const timeline = computed(() => {
  const payload = props.itinerary;
  if (!payload) return [];
  const legs = new Map<number, ItineraryLeg>(
    (payload.legs || []).map(leg => [leg.fromSeq, leg] as [number, ItineraryLeg]));
  const stops = payload.stops || [];
  // leg 的 fromSeq 就是"出发那一站"，逐站挂载才能同时覆盖段内与跨段连接。
  const toRows = (list: ItineraryStop[]) =>
    list.map(stop => ({ stop, leg: legs.get(stop.seq) || null }));
  const groups = PERIOD_ORDER.map(period => {
    const periodStops = stops.filter(stop => stop.period === period);
    return { period, label: PERIOD_LABELS[period] || period, rows: toRows(periodStops) };
  });
  // 后端若出现未知时段，追加到末尾而不是丢弃站点。
  const known = new Set(PERIOD_ORDER);
  const extras = stops.filter(stop => !known.has(stop.period));
  if (extras.length) {
    groups.push({ period: 'other', label: '其它', rows: toRows(extras) });
  }
  return groups;
});

/** `estimated` 为真说明该腿还没有高德真实算路结果，用"约"标出这是估算值。 */
const legText = (leg: ItineraryLeg) => {
  const approx = leg.estimated ? '约 ' : '';
  return leg.mode === 'walking'
    ? `步行${approx}${leg.durationMinutes} 分钟 · ${leg.distanceMeters} 米`
    : `${approx}${leg.distanceMeters} 米，建议骑行或乘校车`;
};

// 新一轮行程到达时自动展开，避免用户以为没有内容。
// 只跟踪站点数组的引用：通行段被真实路线数据更新时不应把用户收起的面板强制展开。
watch(() => props.itinerary?.stops, value => {
  if (value && value.length) collapsed.value = false;
});
</script>

<style scoped>
.itinerary-panel {
  position: absolute; left: 12px; top: 62px; z-index: 102; width: 272px;
  box-sizing: border-box; padding: 12px; background: rgba(255, 255, 255, .96);
  border-radius: 12px; box-shadow: 0 3px 14px #0002; font-size: 12px; color: #4b5563;
  max-height: calc(100vh - 150px); display: flex; flex-direction: column;
}
.panel-head { display: flex; align-items: flex-start; gap: 8px; }
.head-text { min-width: 0; }
h2 { margin: 0; font-size: 14px; font-weight: 600; color: #2b2b2b; line-height: 1.35; }
.campus-tag {
  display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: 999px;
  background: #fdeef0; color: #b7081b; font-size: 11px; font-weight: 600;
}
.collapse {
  margin-left: auto; flex: 0 0 auto; font: inherit; font-size: 11px; color: #374151;
  border: 1px solid #d9dde3; background: #fff; border-radius: 6px; padding: 4px 8px; cursor: pointer;
}
.collapse:hover { border-color: #b7081b; color: #b7081b; }
.fallback-note {
  margin: 8px 0 0; padding: 6px 8px; border-radius: 6px; background: #fff7e6;
  color: #8a5a00; font-size: 11px; line-height: 1.5;
}
.timeline { list-style: none; margin: 10px 0 0; padding: 0; overflow-y: auto; }
.stop-row {
  display: flex; gap: 8px; align-items: flex-start;
  animation: stop-in .34s ease both; animation-delay: var(--delay, 0ms);
}
.badge {
  flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%; border: none;
  background: linear-gradient(135deg, #c20a1c, #a50918); color: #fff;
  font: inherit; font-size: 11px; font-weight: 600; cursor: pointer;
  box-shadow: 0 2px 6px rgba(183, 8, 27, .28); transition: transform .16s ease;
}
.badge:hover { transform: translateY(-1px); }
.card {
  flex: 1 1 auto; min-width: 0; margin-bottom: 8px; padding: 8px 10px; border-radius: 10px;
  background: #fff; border: 1px solid #eef0f3; box-shadow: 0 2px 8px rgba(0, 0, 0, .06);
  cursor: pointer; transition: transform .18s ease, box-shadow .18s ease;
}
.card:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0, 0, 0, .1); }
.card-head { display: flex; align-items: center; gap: 6px; }
.stop-name { font-size: 13px; font-weight: 600; color: #2b2b2b; }
.period-chip {
  margin-left: auto; flex: 0 0 auto; padding: 1px 7px; border-radius: 999px;
  background: #f4f5f7; color: #6b6b6b; font-size: 10px;
}
.reason { margin: 4px 0 0; font-size: 11px; line-height: 1.5; color: #6b6b6b; }
.card-foot { display: flex; align-items: center; margin-top: 6px; }
.dwell { font-size: 10px; color: #9ca3af; }
.go {
  margin-left: auto; font: inherit; font-size: 11px; color: #b7081b;
  border: 1px solid #f0c9ce; background: #fff; border-radius: 6px; padding: 2px 8px; cursor: pointer;
}
.go:hover { background: #b7081b; color: #fff; border-color: #b7081b; }
.leg-row { display: flex; align-items: center; gap: 8px; padding: 0 0 8px 11px; }
.leg-line { width: 1px; height: 18px; background: linear-gradient(#e4a0a6, #f3d7da); }
.leg-text { font-size: 10px; color: #b7081b; }
.leg-row.riding .leg-line { background: repeating-linear-gradient(#d97706 0 3px, transparent 3px 6px); }
.leg-row.riding .leg-text { color: #d97706; }
.skip-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.skip-dot { flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%; border: 1px dashed #cbd0d6; }
.skip-row p {
  flex: 1 1 auto; margin: 0; padding: 8px 10px; border: 1px dashed #dcdfe4;
  border-radius: 10px; color: #9ca3af; font-size: 11px; line-height: 1.5;
}
@keyframes stop-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .stop-row { animation: none; }
  .card, .badge { transition: none; }
}
@media (max-width: 900px) { .itinerary-panel { width: 240px; left: 8px; } }
</style>
