<template>
    <el-container>
        <el-row>
            <!-- 地图容器 -->
            <el-container style="margin-bottom: 20px;">
                <topbar />
            </el-container>
            <div style="display:flex; align-items:flex-start; flex-wrap:nowrap; width:100%;">
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
import topbar from "./topbar.vue";
import TryJson from '../assets/try.json';
import plantcard from './plantcard.vue';
import plantlibrary from './plantlibrary.vue';

var selecteddata = TryJson;
var icondata = TryJson;
var filteredData = TryJson;

var map = null;
var markersArray = [];
const currentComponent = ref('plantmap');
const center = ref({ lat: 31.227073, lng: 121.406079 });

const selectedPlant = ref({
    name: '', id: '', scientificName: '', scientificNameEn: '',
    habit: '', branch: '', place: '', number: '',
    hasOwner: '', key_phrase: '', updatetime: ''
});

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
    };
    const currentMarker = new (window as any).AMap.Marker({
        map,
        position: [item.longitude, item.latitude],
    });
    markersArray.push(currentMarker);
    const infoWindow = new (window as any).AMap.InfoWindow({
        offset: new (window as any).AMap.Pixel(0, -32),
    });
    currentMarker.on("click", () => {
        infoWindow.setContent(props.name);
        infoWindow.open(map, currentMarker.getPosition());
        selectedPlant.value = { ...props };
        emitter.emit('plantvalueChange', selectedPlant.value);
    });
};

const updateMarkers = () => {
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

onMounted(() => nextTick(initMap));

onUnmounted(() => {
    emitter.off('campusChange', onCampusChange);
    emitter.off('selectedChange', onSelectedChange);
    emitter.off('iconChange', onIconChange);
    emitter.off('modelChange', onModelChange);
    markersArray.forEach(m => m.setMap(null));
    markersArray = [];
    map?.destroy?.();
    map = null;
});
</script>
<style lang="scss" scoped>
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
</style>
