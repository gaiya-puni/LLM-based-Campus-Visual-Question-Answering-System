<template>
    <el-container>
        <el-row>
            <!-- 地图容器 -->
            <el-container style="margin-bottom: 20px;">
                <topbar />
            </el-container>
            <div class="map-layout">
            <div class="outline">
                <div v-show ="currentComponent === 'plantmap'">
                <div id="container"></div>
            </div>
                <div v-show ="currentComponent === 'plantlibrary'">
                    <plantlibrary />
                </div>
            </div>

            <!-- 植物信息卡片 -->

            <plantcard />
            </div>
        </el-row>
    </el-container>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';
import emitter from "../bus";
import { loadAMap } from "../amap";
import topbar from "./topbar.vue";
import TryJson from '../assets/try.json';
import plantcard from './plantcard.vue';
import plantlibrary from './plantlibrary.vue';
import { createCommentInfoWindowController } from '../components/commentInfoWindow';

var selecteddata = TryJson;
var icondata = TryJson;
var filteredData = TryJson;

var map = null;
var markersArray = [];
// 共享的留言板弹窗：植物地图一次可能有上千个 marker，绝不能逐点建 Vue 应用。
let commentWindow: ReturnType<typeof createCommentInfoWindowController> | null = null;
const currentComponent = ref('plantmap');
const center = ref({ lat: 31.227073, lng: 121.406079 });

const selectedPlant = ref({
    name: '', id: '', scientificName: '', scientificNameEn: '',
    habit: '', branch: '', place: '', number: '',
    hasOwner: '', key_phrase: '', updatetime: '',
    // 认养与寄语：只有被认养的树才有值，其余为空串（卡片据此显示占位）
    ownerName: '', ownerTitle: '', slogan: '', content: ''
});

const escapeHtml = (value: unknown) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

/** 寄语全文可能很长，弹窗里只放摘要（全文在植物信息卡看） */
const truncateText = (value: unknown, max: number) => {
    const text = String(value ?? '').trim();
    if (!text) return '';
    return text.length > max ? `${text.slice(0, max)}…` : text;
};

// 留言板弹窗顶部：植物简介 + 认养寄语，顺序固定为「寄语在前、留言板在后」
const buildPlantHeader = (props: any) => {
    const name = escapeHtml(props.name);
    const branch = escapeHtml(props.branch);
    const number = escapeHtml(props.number);
    const ownerName = escapeHtml(props.ownerName);
    const ownerTitle = escapeHtml(props.ownerTitle);
    const slogan = escapeHtml(truncateText(props.slogan, 40));
    // content 往往是 slogan 的全文，完全相同就不再重复占高度
    const content = escapeHtml(
        props.content && props.content !== props.slogan ? truncateText(props.content, 80) : '',
    );
    const hasBlessing = Boolean(ownerName || slogan || content);
    const ownerLine = ownerName || ownerTitle
        ? `<p class="poi-blessing__owner">—— ${ownerName || '匿名认养人'}${ownerTitle ? `（${ownerTitle}）` : ''}</p>`
        : '';
    const blessing = hasBlessing
        ? `<div class="poi-blessing">
            <p class="poi-blessing__label">认养与寄语</p>
            ${slogan ? `<p class="poi-blessing__slogan">「${slogan}」</p>` : ''}
            ${ownerLine}
            ${content ? `<p class="poi-blessing__content">${content}</p>` : ''}
          </div>`
        : `<div class="poi-blessing is-empty">
            <p class="poi-blessing__label">认养与寄语</p>
            <p class="poi-blessing__empty">这棵树还没有认养寄语，可以在下面的留言板写下第一句印象。</p>
          </div>`;
    return `<div class="poi-head">
        <div class="poi-head__title"><b>${name}</b><span>（${branch}）</span></div>
        <div class="poi-head__sub">${number}</div>
        ${blessing}
      </div>`;
};

// 提取公共的标记添加逻辑
const addMarker = (item) => {
    const props = {
        name: item.template.name,
        scientificName: item.template.scientificName,
        scientificNameEn: item.template.scientificNameEn,
        id: item.id,
        habit: item.template.habit,
        branch: item.template.branch,
        place: item.place,
        number: item.number,
        hasOwner: item.hasOwner,
        key_phrase: item.key_phrase,
        updatetime: item.updatedAt,
        // 认养人 / 寄语：`owner` 为 null 表示这棵树还没被认养
        ownerName: item.owner?.name || '',
        ownerTitle: item.owner?.title || '',
        slogan: item.owner?.slogan || '',
        content: item.owner?.content || '',
    };
    const currentMarker = new (window as any).AMap.Marker({
        map,
        position: [item.longitude, item.latitude],
    });
    markersArray.push(currentMarker);
    currentMarker.on("click", () => {
        // 植物地图的 id 是树木整数 id，与 POI 库的 `plant_<treeId>` 一一对应，
        // 因此这里直接归一，不必再走坐标反查。
        commentWindow?.open(map, currentMarker.getPosition(), {
            poiId: item.id ? `plant_${item.id}` : '',
            poiName: props.name,
            header: { html: buildPlantHeader(props) },
        });
        // 保持与植物信息卡片的既有联动不变
        selectedPlant.value = { ...props };
        emitter.emit('plantvalueChange', selectedPlant.value);
    });
};

const updateMarkers = () => {
    commentWindow?.close();
    markersArray.forEach(m => m.setMap(null));
    markersArray = [];
    filteredData.forEach(addMarker);
};

const updateFilteredData = () => {
    filteredData = selecteddata.filter(item => icondata.includes(item));
    updateMarkers();
};

const initMap = () => {
    map = new (window as any).AMap.Map('container', {
        center: [center.value.lng, center.value.lat],
        zoom: 18,
    });
    // 全地图共用一个留言板弹窗（内容走 DOM + Vue 组件，见 commentInfoWindow.ts）
    commentWindow = createCommentInfoWindowController((window as any).AMap, -32);
    filteredData.forEach(addMarker);
};

// 所有事件只注册一次
const onCampusChange = (school: string) => {
    center.value = school === '闵行'
        ? { lat: 31.03148, lng: 121.453725 }
        : { lat: 31.227073, lng: 121.406079 };
    if (map) map.setCenter([center.value.lng, center.value.lat]);
};

const onSelectedChange = (select: string) => {
    if (select === '已认领') selecteddata = TryJson.filter(item => item.owner !== null);
    else if (select === '未认领') selecteddata = TryJson.filter(item => item.owner === null);
    else selecteddata = TryJson;
    updateFilteredData();
};

const onIconChange = (iconkind: string) => {
    icondata = iconkind === '全部' ? TryJson : TryJson.filter(item => item.template.habit === iconkind);
    updateFilteredData();
};

const onModelChange = (model: string) => {
    currentComponent.value = model === '地图' ? 'plantmap' : 'plantlibrary';
};

emitter.on('campusChange', onCampusChange);
emitter.on('selectedChange', onSelectedChange);
emitter.on('iconChange', onIconChange);
emitter.on('modelChange', onModelChange);

// index.html 用 async 引入高德脚本，必须等它就绪再建图，否则 window.AMap 可能还是 undefined。
onMounted(() => nextTick(async () => {
    try {
        await loadAMap();
        initMap();
    } catch (err) {
        console.error('高德地图加载失败：', err);
    }
}));

onUnmounted(() => {
    emitter.off('campusChange', onCampusChange);
    emitter.off('selectedChange', onSelectedChange);
    emitter.off('iconChange', onIconChange);
    emitter.off('modelChange', onModelChange);
    // 必须先销毁共享弹窗里挂着的 Vue 应用，否则会留下游离的组件实例
    commentWindow?.destroy();
    commentWindow = null;
    markersArray.forEach(m => m.setMap(null));
    markersArray = [];
    map?.destroy?.();
    map = null;
});
</script>
<style lang="scss" scoped>
.map-layout {
    display: flex;
    align-items: flex-start;
    flex-wrap: nowrap;
    width: 100%;
}

.outline {
    flex: 1;
    min-width: 0;
    margin-right: 20px;
}

#container {
    width: 100%;
    height: 600px;
}

/* 调整卡片容器的样式 */
.el-card {
    height: 550px;
    overflow: auto;
    padding: 10px;
}

.plant-info {
    padding: 20px;
}

.info-item {
    margin-bottom: 8px;
}

.info-label {
    font-weight: bold;
    margin-right: 8px;
}

/* 移动端：地图占满通栏、高度贴合视口，植物卡片落到地图下方 */
@media (max-width: 768px) {
    .map-layout {
        flex-wrap: wrap;
    }

    /* flex: 0 0 100% —— 仅写 width: 100% 无效：
       桌面的 flex: 1 会让 flex-basis 变成 0，缩小到 0 宽（地图整块不可见） */
    .outline {
        flex: 0 0 100%;
        margin-right: 0;
        width: 100%;
    }

    #container {
        /* 视口高度扣除顶部品牌栏、底部标签栏与筛选栏，保证地图近满屏 */
        height: calc(100dvh - var(--mobile-topbar-h) - var(--mobile-tabbar-h) - 150px);
        min-height: 320px;
    }

    .el-card {
        height: auto;
        max-height: 45dvh;
    }
}
</style>
