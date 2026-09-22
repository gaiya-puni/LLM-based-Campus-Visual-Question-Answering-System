<template>
  <div class="chatbot-page">
    <!-- 左侧地图 -->
    <div class="map-panel">
      <div class="campus-toggle">
        <button :class="['campus-btn', { active: activeCampus === '普陀' }]" @click="switchCampus('普陀')">普陀校区</button>
        <button :class="['campus-btn', { active: activeCampus === '闵行' }]" @click="switchCampus('闵行')">闵行校区</button>
      </div>
      <div id="chat-map"></div>
      <SceneHeatmapPanel :map="heatmapMap" :campus="activeCampus" :mode="recommendationMode"
        :response="heatmapResponse" :reset-token="heatmapReset" :busy="isLoading"
        @update:mode="setRecommendationMode" @places="showHeatmapPlaces" @active="heatmapActive = $event" />
      <ItineraryPanel :itinerary="panelItinerary"
        @focus="focusItineraryStop" @navigate="navigateItineraryStop" />
      <div v-if="mapError" class="map-placeholder">
        <p>⚠️ {{ mapError }}</p>
      </div>
      <div v-else-if="mapLocations.length === 0 && !heatmapActive" class="map-placeholder">
        <p>{{ allLocations.length ? `${activeCampus}校区暂无该地点` : '💬 提问后，相关位置将在地图上显示' }}</p>
      </div>
    </div>

    <!-- 右侧问答 -->
    <div class="chatbot-container">
      <!-- 顶部标题栏 -->
      <div class="chatbot-header">
        <img src="@/assets/ecnu-logo.png" class="header-logo" alt="ECNU Logo" />
        <el-text class="header-title">校园智能助手</el-text>
        <el-text class="header-subtitle">有什么可以帮您的？</el-text>
      </div>

      <!-- 聊天消息区域 -->
      <div class="chat-messages" ref="messagesContainer">
        <div class="message-bot">
          <div class="avatar-bot">
            <img src="@/assets/ecnu-logo.png" class="avatar-img" alt="ECNU" />
          </div>
          <div class="message-content">
            <p>您好！我是校园智能助手，很高兴为您服务！</p>
            <p>请问您想了解什么？比如：</p>
            <ul class="suggestions">
              <li @click="sendMessage('校园里有哪些樱花？在哪里？')">校园里有哪些樱花？在哪里？</li>
              <li @click="sendMessage('银杏树在哪里？')">银杏树在哪里？</li>
              <li @click="sendMessage('校园里有哪些植物？')">校园里有哪些植物？</li>
              <li @click="sendMessage('蔷薇科的植物有哪些？')">蔷薇科的植物有哪些？</li>
            </ul>
          </div>
        </div>

        <div v-for="(msg, index) in messages" :key="index" :class="['message-item', msg.type]">
          <div :class="['avatar', msg.type === 'user' ? 'avatar-user' : 'avatar-bot']">
            <div v-if="msg.type === 'user'" class="user-avatar-icon"><User /></div>
            <img v-else src="@/assets/ecnu-logo.png" class="avatar-img" alt="ECNU" />
          </div>
          <div :class="['message-bubble', msg.type === 'user' ? 'user-bubble' : 'bot-bubble']">
            <p v-if="msg.type === 'user'">{{ msg.content }}</p>
            <p v-else v-html="parseMarkdown(msg.content)"></p>
            <span class="message-time">{{ msg.time }}</span>
          </div>
        </div>

        <div v-if="isLoading" class="loading-message">
          <div class="avatar-bot">
            <img src="@/assets/ecnu-logo.png" class="avatar-img" alt="ECNU" />
          </div>
          <div class="loading-bubble">
            <span class="loading-dot"></span>
            <span class="loading-dot"></span>
            <span class="loading-dot"></span>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="chat-input-area">
        <div class="input-wrapper">
          <el-input
            v-model="inputMessage"
            placeholder="输入您的问题..."
            class="input-field"
            @keyup.enter="sendMessage(inputMessage)"
            :disabled="isLoading"
          >
            <template #append>
              <el-button
                type="primary"
                @click="sendMessage(inputMessage)"
                :disabled="!inputMessage.trim() || isLoading"
                class="send-btn"
              >发送</el-button>
            </template>
          </el-input>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, computed, nextTick, onMounted, onUnmounted } from "vue";
import { marked } from "marked";
import DOMPurify from 'dompurify';
import SceneHeatmapPanel from '../components/SceneHeatmapPanel.vue';
import ItineraryPanel from '../components/ItineraryPanel.vue';
import type { HeatmapPayload } from '../components/sceneHeatmapTypes';
import type { ItineraryPayload, ItineraryStop } from '../components/itineraryTypes';
import { loadAMap } from '../amap';

marked.setOptions({ breaks: true, gfm: true });
const parseMarkdown = (text: string) => DOMPurify.sanitize(
  marked.parse(text) as string,
  { USE_PROFILES: { html: true } }
);

interface Message { type: 'user' | 'bot'; content: string; time: string; }
interface Location {
  name: string;
  lng: number;
  lat: number;
  number: string;
  campus: string;
  kind?: string;
  rank?: number;
  score?: number;
  reason?: string;
  plants?: string[];
  access_verified?: boolean;
  /** 一日行程站点专用字段（kind === 'itinerary_stop'） */
  seq?: number;
  period?: string;
  periodLabel?: string;
  dwellMinutes?: number;
}
interface UserLocation {
  lng: number;
  lat: number;
  campus: string;
  accuracy?: number;
  trusted?: boolean;
  campusTrusted?: boolean;
  useForDistance?: boolean;
  source?: string;
  distanceToCampusCenter?: number;
}

const messages = ref<Message[]>([]);
const inputMessage = ref('');
const isLoading = ref(false);
const messagesContainer = ref<HTMLElement | null>(null);
const mapLocations = ref<Location[]>([]);
const allLocations = ref<Location[]>([]);
const activeCampus = ref('普陀');
const userLocation = ref<UserLocation | null>(null);
const recommendationMode = ref('sakde');
const heatmapMap = shallowRef<any>(null);
const heatmapResponse = shallowRef<HeatmapPayload | null>(null);
const itineraryResponse = shallowRef<ItineraryPayload | null>(null);
// 行程面板必须与地图保持一致：切到别的校区时该行程的标记已被过滤掉，面板也不应再显示。
// 只做显示层过滤、不清数据，切回原校区即自动恢复。
const visibleItinerary = computed(() =>
  itineraryResponse.value && itineraryResponse.value.campus === activeCampus.value
    ? itineraryResponse.value
    : null);

// 高德算路返回的真实步行数据（key = `${fromSeq}-${toSeq}`）。
// 后端为了不依赖外部服务，给出的是平面直线估算；这里用地图上真实算出来的路线结果覆盖它，
// 拿不到结果（算路失败/无网络）的腿保留估算，并由 `estimated` 让面板标出"约"。
const legRouteInfo = ref<Record<string, { distanceMeters: number; durationMinutes: number }>>({});
const panelItinerary = computed(() => {
  const payload = visibleItinerary.value;
  if (!payload) return null;
  return {
    ...payload,
    legs: (payload.legs || []).map(leg => {
      // 后端已用高德真实路线算过（estimated === false）时以它为准——对话文案里的数字就是它，
      // 前端再覆盖反而造成"气泡与面板不一致"。只有后端给的是估算时才用地图上的算路结果补上。
      if (leg.estimated === false) return leg;
      const real = legRouteInfo.value[`${leg.fromSeq}-${leg.toSeq}`];
      return real ? { ...leg, ...real, estimated: false } : { ...leg, estimated: true };
    }),
  };
});
const heatmapActive = ref(false);
const heatmapReset = ref(0);

const CAMPUS_CENTERS: Record<string, [number, number]> = {
  '普陀': [121.406079, 31.227073],
  '闵行': [121.453725, 31.03148],
};
const CAMPUS_LOCATION_RADIUS_M: Record<string, number> = {
  '普陀': 1300,
  '闵行': 2200,
};
const MAX_GEO_ACCURACY_M = 800;

let map: any = null;
let markersArray: any[] = [];
let markerInfoWindows: any[] = [];
let walking: any = null;
let userMarker: any = null;
let geolocation: any = null;
// 行程路线使用独立实例数组，与单点导航（walking）互不干扰地清理与绘制。
let itineraryWalkings: any[] = [];

const mapError = ref('');

const initMap = () => {
  map = new (window as any).AMap.Map('chat-map', {
    zoom: 16,
    center: CAMPUS_CENTERS[activeCampus.value],
  });
  heatmapMap.value = map;
};

const distanceMeters = (lng1: number, lat1: number, lng2: number, lat2: number) => {
  const meanLat = ((lat1 + lat2) / 2) * Math.PI / 180;
  const dx = (lng1 - lng2) * 111320 * Math.cos(meanLat);
  const dy = (lat1 - lat2) * 110540;
  return Math.sqrt(dx * dx + dy * dy);
};

const nearestCampusByLocation = (lng: number, lat: number) => {
  return Object.entries(CAMPUS_CENTERS)
    .map(([campus, center]) => ({
      campus,
      distance: distanceMeters(lng, lat, center[0], center[1])
    }))
    .sort((a, b) => a.distance - b.distance)[0];
};

const campusCenterLocation = (campus = activeCampus.value): UserLocation => {
  const center = CAMPUS_CENTERS[campus] || CAMPUS_CENTERS['普陀'];
  return {
    lng: center[0],
    lat: center[1],
    campus,
    trusted: false,
    campusTrusted: false,
    useForDistance: false,
    source: 'campus_center',
    distanceToCampusCenter: 0,
  };
};

const assessMapLocation = (lng: number, lat: number, accuracy?: number): UserLocation => {
  const nearest = nearestCampusByLocation(lng, lat);
  const campus = nearest.campus;
  const accuracyTrusted = !accuracy || accuracy <= MAX_GEO_ACCURACY_M;
  const campusTrusted = nearest.distance <= (CAMPUS_LOCATION_RADIUS_M[campus] || 1500)
    && accuracyTrusted;
  return {
    lng,
    lat,
    campus,
    accuracy,
    trusted: accuracyTrusted,
    campusTrusted,
    useForDistance: accuracyTrusted,
    source: 'amap',
    distanceToCampusCenter: nearest.distance,
  };
};

const watchAmapGeolocation = (AMap: any) => new Promise<UserLocation | null>((resolve) => {
  AMap.plugin('AMap.Geolocation', () => {
    try {
      if (!geolocation) {
        geolocation = new AMap.Geolocation({
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 30000,
          convert: true,
          showButton: false,
          showMarker: false,
          showCircle: false,
          panToLocation: false,
          zoomToAccuracy: false,
        });
      }
      geolocation.getCurrentPosition((status: string, result: any) => {
        if (status === 'complete' && result?.position) {
          const position = result.position;
          const lng = Number(position.lng ?? position.getLng?.() ?? position[0]);
          const lat = Number(position.lat ?? position.getLat?.() ?? position[1]);
          const accuracy = Number(result.accuracy);
          if (Number.isFinite(lng) && Number.isFinite(lat)) {
            resolve(assessMapLocation(
              lng,
              lat,
              Number.isFinite(accuracy) ? accuracy : undefined
            ));
            return;
          }
        }
        resolve(null);
      });
    } catch {
      resolve(null);
    }
  });
});

// index.html 用 async 引入高德脚本，这里必须等它就绪，否则定位会静默返回 null。
const getAmapCurrentLocation = async (): Promise<UserLocation | null> => {
  const AMap = await loadAMap().catch(() => null);
  return AMap ? watchAmapGeolocation(AMap) : null;
};

// 检测用户地址对应的最近校区，用于后端优先推荐
const detectUserCampus = async () => {
  const assessed = await getAmapCurrentLocation();
  if (assessed?.trusted) {
    userLocation.value = assessed;
    if (assessed.campusTrusted) {
      activeCampus.value = assessed.campus;
      map?.setCenter(CAMPUS_CENTERS[assessed.campus]);
    }
  } else {
    userLocation.value = campusCenterLocation(activeCampus.value);
  }
};

const clearMarkers = () => {
  markerInfoWindows.forEach(info => info.close());
  markerInfoWindows = [];
  markersArray.forEach(m => m.setMap(null));
  markersArray = [];
  if (walking) walking.clear();
  itineraryWalkings.forEach(item => item.clear());
  itineraryWalkings = [];
  if (userMarker) { userMarker.setMap(null); userMarker = null; }
};

const escapeHtml = (value: unknown) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');

const buildInfoContent = (loc: Location, navBtnId: string) => {
  const isRanked = loc.kind === 'ranked_place';
  const isStop = loc.kind === 'itinerary_stop';
  const rankLine = isRanked
    ? `<div style="margin-bottom:6px;color:#c20a1c;font-weight:700">Top ${loc.rank ?? '-'}</div>`
    : isStop
      ? `<div style="margin-bottom:6px;color:#c20a1c;font-weight:700">第 ${loc.seq ?? '-'} 站${loc.periodLabel ? ` · ${escapeHtml(loc.periodLabel)}` : ''}</div>`
      : '';
  const plantsLine = loc.plants?.length
    ? `<br><span style="color:#666;font-size:13px">代表植物：${escapeHtml(loc.plants.join('、'))}</span>`
    : '';
  const reasonLine = loc.reason
    ? `<br><span style="color:#666;font-size:13px">推荐依据：${escapeHtml(loc.reason)}</span>`
    : '';
  const dwellLine = isStop && loc.dwellMinutes
    ? `<br><span style="color:#666;font-size:13px">建议停留约 ${loc.dwellMinutes} 分钟</span>`
    : '';

  return `<div style="padding:4px 2px;max-width:260px">
    ${rankLine}<b>${escapeHtml(loc.name)}</b>
    <br><span style="color:#666;font-size:13px">${escapeHtml(loc.number)}</span>
    ${plantsLine}${reasonLine}${dwellLine}
    ${loc.access_verified === false ? '<br><span style="color:#8a5a00;font-size:12px">真实POI参考位置，通行情况需现场确认</span>' : ''}
    <br><button id="${navBtnId}" style="margin-top:8px;padding:4px 12px;background:#c20a1c;color:white;border:none;border-radius:4px;cursor:pointer;font-size:13px">步行导航到这里</button>
  </div>`;
};

const pickMapLocations = (locations: Location[]) => {
  const ranked = locations.filter(
    loc => loc.kind === 'ranked_place' || loc.kind === 'itinerary_stop');
  return ranked.length ? ranked : locations;
};

let itineraryRouteToken = 0;

// 行程路线：为每一段"步行"腿各建一个独立的 AMap.Walking 实例分别绘制，
// 因此多段路线可同时呈现，且不会与单点导航（walking）互相清除。
// 超过步行阈值的腿（后端标记为 riding）不画线，由面板与信息窗给出骑行/校车提示。
const drawItineraryRoute = (locations: Location[]) => {
  itineraryWalkings.forEach(item => item.clear());
  itineraryWalkings = [];
  if (!map || !(window as any).AMap) return;
  const stops = locations
    .filter(loc => loc.kind === 'itinerary_stop' && typeof loc.seq === 'number')
    .sort((a, b) => (a.seq as number) - (b.seq as number));
  if (stops.length < 2) return;
  const riding = new Set((itineraryResponse.value?.legs || [])
    .filter(leg => leg.mode !== 'walking')
    .map(leg => `${leg.fromSeq}-${leg.toSeq}`));
  const token = ++itineraryRouteToken;
  (window as any).AMap.plugin('AMap.Walking', () => {
    if (token !== itineraryRouteToken) return;
    for (let index = 0; index < stops.length - 1; index++) {
      const legKey = `${stops[index].seq}-${stops[index + 1].seq}`;
      if (riding.has(legKey)) continue;
      const route = new (window as any).AMap.Walking({
        map, hideMarkers: true, autoFitView: false,
        // 描边用校园红，与单点导航的默认样式区分开（路线主体仍为平台样式）。
        isOutline: true, outlineColor: '#b7081b',
      });
      route.search(
        [stops[index].lng, stops[index].lat],
        [stops[index + 1].lng, stops[index + 1].lat],
        // 复用同一份算路结果：把高德返回的真实距离/时长回填给面板，失败则保留后端估算。
        (status: string, result: any) => {
          if (token !== itineraryRouteToken || status !== 'complete') return;
          const best = result?.routes?.[0];
          const distance = Number(best?.distance);
          const seconds = Number(best?.time);
          if (!Number.isFinite(distance) || !Number.isFinite(seconds)) return;
          legRouteInfo.value = {
            ...legRouteInfo.value,
            [legKey]: {
              distanceMeters: Math.round(distance),
              durationMinutes: Math.max(1, Math.round(seconds / 60)),
            },
          };
        },
      );
      itineraryWalkings.push(route);
    }
  });
};

const showLocationsOnMap = (locations: Location[]) => {
  clearMarkers();
  if (!locations.length || !map) return;
  locations.forEach((loc, index) => {
    const markerOptions: any = {
      map, position: [loc.lng, loc.lat], title: `${loc.name}（${loc.number}）`,
    };
    if (loc.kind === 'ranked_place' && loc.rank) {
      markerOptions.label = {
        content: `Top${loc.rank}`,
        offset: new (window as any).AMap.Pixel(0, -30),
      };
    } else if (loc.kind === 'itinerary_stop' && loc.seq) {
      // 序号与行程面板的徽章一一对应，形成面板与地图的双向对应。
      markerOptions.label = {
        content: `第${loc.seq}站`,
        offset: new (window as any).AMap.Pixel(0, -30),
      };
    }
    const marker = new (window as any).AMap.Marker(markerOptions);
    const navBtnId = `nav-btn-${index}-${loc.lng}-${loc.lat}`;
    const infoWindow = new (window as any).AMap.InfoWindow({
      content: buildInfoContent(loc, navBtnId),
      offset: new (window as any).AMap.Pixel(0, -32),
    });
    markerInfoWindows.push(infoWindow);
    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition());
      setTimeout(() => {
        const btn = document.getElementById(navBtnId);
        if (btn) btn.onclick = () => navigateTo(loc.lng, loc.lat);
      }, 100);
    });
    markersArray.push(marker);
  });
  const hasItineraryStops = locations.some(loc => loc.kind === 'itinerary_stop');
  if (locations.some(loc => loc.access_verified === false) || hasItineraryStops) {
    // AMap padding order is top, bottom, left, right; keep new heatmap markers
    // outside the controls without changing the original recommendation view.
    const panelPadding = window.matchMedia('(max-width: 900px)').matches ? 260 : 300;
    map.setFitView(markersArray, false, [80, 60, panelPadding, 60]);
  } else {
    map.setFitView(markersArray);
  }
  drawItineraryRoute(locations);
};

// 按当前校区过滤并显示标记
const filterAndShow = () => {
  const filtered = allLocations.value.filter(l => l.campus === activeCampus.value);
  mapLocations.value = filtered;
  showLocationsOnMap(filtered);
};

const showHeatmapPlaces = (places: HeatmapPayload['places']) => {
  allLocations.value = places;
  filterAndShow();
};
const setRecommendationMode = (mode: string) => {
  recommendationMode.value = mode;
  heatmapResponse.value = null;
  itineraryResponse.value = null;
  heatmapReset.value++;
  allLocations.value = [];
  filterAndShow();
};

const switchToFirstAvailableCampus = () => {
  const firstCampus = allLocations.value.find(loc => loc.campus)?.campus;
  if (!firstCampus || firstCampus === activeCampus.value) return;
  activeCampus.value = firstCampus;
  map?.setCenter(CAMPUS_CENTERS[firstCampus]);
  map?.setZoom(16);
  filterAndShow();
};

// 手动切换校区：移动地图中心 + 重新过滤标记
const switchCampus = (campus: string) => {
  activeCampus.value = campus;
  if (!userLocation.value?.trusted || userLocation.value.source !== 'amap') {
    userLocation.value = campusCenterLocation(campus);
  }
  map?.setCenter(CAMPUS_CENTERS[campus]);
  map?.setZoom(16);
  filterAndShow();
};

// 行程面板 → 地图：定位并弹出该站标记的信息窗，形成双向对应。
const focusItineraryStop = (stop: ItineraryStop) => {
  if (!map) return;
  const index = markersArray.findIndex((marker: any) => {
    const position = marker.getPosition();
    return Math.abs(position.lng - stop.lng) < 1e-6 && Math.abs(position.lat - stop.lat) < 1e-6;
  });
  map.setZoom(17);
  map.setCenter([stop.lng, stop.lat]);
  if (index >= 0 && markerInfoWindows[index]) {
    markerInfoWindows[index].open(map, markersArray[index].getPosition());
  }
};

// 行程面板 → 单站导航：复用既有单点导航，行程路线保持独立不被清除。
const navigateItineraryStop = (stop: ItineraryStop) => navigateTo(stop.lng, stop.lat);

const navigateTo = (destLng: number, destLat: number) => {
  const doRoute = (startLng: number, startLat: number) => {
    if (walking) { walking.clear(); walking.search([startLng, startLat], [destLng, destLat]); }
    else {
      (window as any).AMap.plugin('AMap.Walking', () => {
        walking = new (window as any).AMap.Walking({ map });
        walking.search([startLng, startLat], [destLng, destLat]);
      });
    }
  };

  const drawRouteFrom = (start: UserLocation) => {
    const label = start.source === 'amap' ? '我的位置（可拖动）' : `${start.campus}校区起点（可拖动）`;
    if (userMarker) userMarker.setMap(null);
    userMarker = new (window as any).AMap.Marker({
      map,
      position: [start.lng, start.lat],
      title: label,
      draggable: true,
      cursor: 'move',
      label: { content: label, offset: new (window as any).AMap.Pixel(0, -30) },
    });
    userMarker.on('dragend', (e: any) => { const p = e.target.getPosition(); doRoute(p.lng, p.lat); });
    doRoute(start.lng, start.lat);
  };

  getAmapCurrentLocation().then((assessed) => {
    drawRouteFrom(assessed?.trusted
      ? assessed
      : campusCenterLocation(activeCampus.value));
  });
};

onMounted(() => {
  nextTick(async () => {
    try {
      await loadAMap();
    } catch (err) {
      mapError.value = err instanceof Error ? err.message : '地图加载失败';
      return;
    }
    initMap();
    // 地图就绪前若已有问答结果，这里补渲染一次，避免标记丢失。
    filterAndShow();
    detectUserCampus();
  });
});

onUnmounted(() => {
  clearMarkers();
  heatmapMap.value = null;
  map?.destroy?.();
  map = null;
});

const CHAT_API_URL = '/api/chat';
const getCurrentTime = () => {
  const now = new Date();
  return `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
};

const sendMessage = async (content: string) => {
  if (!content.trim() || isLoading.value) return;
  heatmapResponse.value = null;
  heatmapReset.value++;
  messages.value.push({ type: 'user', content: content.trim(), time: getCurrentTime() });
  inputMessage.value = '';
  isLoading.value = true;
  await nextTick();
  scrollToBottom();
  try {
    const freshLocation = await getAmapCurrentLocation();
    if (freshLocation?.trusted) {
      userLocation.value = freshLocation;
    }
    const requestLocation = freshLocation?.trusted
      ? freshLocation
      : (userLocation.value || campusCenterLocation(activeCampus.value));
    const response = await fetch(CHAT_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: messages.value.map(msg => ({
          role: msg.type === 'user' ? 'user' : 'assistant',
          content: msg.content
        })),
        userCampus: activeCampus.value,
        userLocation: requestLocation,
        recommendationMode: recommendationMode.value
      })
    });
    if (!response.ok) throw new Error(`API请求失败: ${response.status}`);
    const data = await response.json();
    messages.value.push({ type: 'bot', content: data.choices?.[0]?.message?.content || '抱歉，我暂时无法回答这个问题。', time: getCurrentTime() });
    // 行程数据需先于地图渲染更新：绘制行程路线时会读 legs 判断哪些腿是骑行。
    itineraryResponse.value = data.itinerary || null;
    if (data.locations?.length) {
      allLocations.value = pickMapLocations(data.locations);
      const campuses = Array.from(new Set(allLocations.value.map(loc => loc.campus).filter(Boolean)));
      if (campuses.length === 1 && campuses[0] !== activeCampus.value) {
        activeCampus.value = campuses[0];
        map?.setCenter(CAMPUS_CENTERS[activeCampus.value]);
        map?.setZoom(16);
      }
      filterAndShow();
      // 当前校区无此地点，但其他校区有 → 追加提示
      if (mapLocations.value.length === 0) {
        switchToFirstAvailableCampus();
        const last = messages.value[messages.value.length - 1];
        if (last?.type === 'bot' && mapLocations.value.length)
          last.content += `\n\n> ⚠️ 您所在校区暂无该地点，已为您显示 **${activeCampus.value}校区** 的相关位置 🗺️`;
      }
    } else {
      allLocations.value = [];
      mapLocations.value = [];
      showLocationsOnMap([]);
    }
    // Render only the raster returned by this answer; exact/unsupported queries clear it.
    await nextTick();
    heatmapResponse.value = data.heatmap || null;
    if (data.heatmap_status) {
      const last = messages.value[messages.value.length - 1];
      if (last?.type === 'bot') last.content += `\n\n> 热图提示：${data.heatmap_status}。本次使用原版推荐。`;
    }
    if (data.itinerary_status) {
      const last = messages.value[messages.value.length - 1];
      if (last?.type === 'bot') last.content += `\n\n> 行程提示：${data.itinerary_status}。`;
    }
  } catch {
    messages.value.push({
      type: 'bot',
      content: '当前是本地演示模式，未连接后端智能问答服务。您仍可浏览植物图鉴、地图页面和静态可视化成果；如需体验完整问答，请按源码包说明启动 Flask 后端。',
      time: getCurrentTime()
    });
  } finally {
    isLoading.value = false;
    await nextTick();
    scrollToBottom();
  }
};

const scrollToBottom = () => {
  if (messagesContainer.value) messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
};
</script>

<style lang="scss" scoped>
.chatbot-page {
  display: flex;
  position: fixed;
  top: 0;
  left: 240px;  /* sidebar 宽度 */
  right: 0;
  bottom: 0;
  overflow: hidden;
  z-index: 10;
}

.map-panel {
  flex: 1;
  position: relative;
  background: #f0f0f0;

  #chat-map {
    width: 100%;
    height: 100%;
  }

  .campus-toggle {
    position: absolute;
    top: 12px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 100;
    display: flex;
    gap: 4px;
    background: rgba(255,255,255,0.95);
    border-radius: 20px;
    padding: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);

    .campus-btn {
      padding: 5px 16px;
      border: none;
      border-radius: 16px;
      font-size: 13px;
      cursor: pointer;
      background: transparent;
      color: #666;
      transition: all 0.2s;

      &.active {
        background: #c20a1c;
        color: white;
        font-weight: 500;
      }

      &:not(.active):hover { background: #f5f5f5; }
    }
  }

  .map-placeholder {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;

    p {
      background: rgba(255,255,255,0.85);
      padding: 12px 20px;
      border-radius: 8px;
      color: #666;
      font-size: 14px;
    }
  }
}

.chatbot-container {
  width: 420px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: linear-gradient(135deg, #e8f5e9 0%, #f3e5f5 100%);
}

.chatbot-header {
  display: flex;
  align-items: center;
  padding: 16px 20px;
  background: linear-gradient(90deg, #c20a1c 0%, #a50918 100%);
  color: white;
  flex-shrink: 0;

  .header-logo {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    margin-right: 12px;
    object-fit: cover;
  }

  .header-title {
    font-size: 18px;
    font-weight: bold;
    color: #ffffff !important;
  }

  .header-subtitle {
    margin-left: auto;
    font-size: 13px;
    opacity: 0.8;
    color: #ffffff !important;
  }
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.message-bot {
  display: flex;
  margin-bottom: 16px;

  .avatar-bot {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: white;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-right: 12px;
    padding: 3px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);

    .avatar-img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
  }

  .message-content {
    background: white;
    border-radius: 14px;
    padding: 14px 16px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);

    p { margin: 0 0 8px 0; color: #333; line-height: 1.6; font-size: 14px; }

    ul.suggestions {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;

      li {
        background: #ffebee;
        color: #c20a1c;
        padding: 6px 12px;
        border-radius: 16px;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;

        &:hover { background: #ffcdd2; }
      }
    }
  }
}

.message-item {
  display: flex;
  margin-bottom: 16px;

  &.user {
    justify-content: flex-end;

    .avatar-user { order: 2; margin-left: 10px; margin-right: 0; }
    .user-bubble { order: 1; background: linear-gradient(135deg, #c20a1c 0%, #a50918 100%); color: white; }
  }

  .avatar {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    flex-shrink: 0;
    margin-right: 10px;

    &.avatar-user { background: linear-gradient(135deg, #64b5f6 0%, #1e88e5 100%); color: white; }
    &.avatar-bot { background: white; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 3px; }

    .avatar-img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
  }

  .message-bubble {
    border-radius: 16px;
    padding: 12px 16px;
    max-width: 80%;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    font-size: 14px;

    p { margin: 0; line-height: 1.6; word-break: break-word; }

    .message-time {
      display: block;
      font-size: 11px;
      margin-top: 6px;
      text-align: right;
      color: #999;
    }
  }

  .bot-bubble { background: white; color: #333; }
}

.loading-message {
  display: flex;

  .avatar-bot {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    background: white;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-right: 10px;
    padding: 3px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);

    .avatar-img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
  }

  .loading-bubble {
    background: white;
    border-radius: 16px;
    padding: 14px 18px;
    display: flex;
    gap: 6px;

    .loading-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #4caf50;
      animation: dotPulse 1.4s infinite ease-in-out;

      &:nth-child(1) { animation-delay: 0s; }
      &:nth-child(2) { animation-delay: 0.2s; }
      &:nth-child(3) { animation-delay: 0.4s; }
    }
  }
}

@keyframes dotPulse {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}

.chat-input-area {
  padding: 12px 16px;
  background: white;
  border-top: 1px solid #eee;
  flex-shrink: 0;

  .send-btn {
    background: linear-gradient(135deg, #c20a1c 0%, #a50918 100%);
    border: none;
    color: white;

    &:disabled { background: #ccc; }
  }
}

.chat-messages::-webkit-scrollbar { width: 5px; }
.chat-messages::-webkit-scrollbar-thumb { background: #ffcdd2; border-radius: 3px; }
</style>
