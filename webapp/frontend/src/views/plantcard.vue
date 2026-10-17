<template>
    <div class="plantcard-root">
        <el-card class="box-card">
            <template #header>
                <div class="clearfix" style="text-align: center; font-size: 24px; font-weight: bold;">
                    <span>植物信息</span>
                </div>
            </template>
            <div class="plant-info">
                <!-- Add some styles to the plant information -->
                <div class="info-item">
                    <span class="info-label">植物名称:</span>
                    <el-text>{{ selectedPlant.name }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">ID:</span>
                    <el-text>{{ selectedPlant.id }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">学名:</span>
                    <el-text>{{ selectedPlant.scientificName }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">英文学名:</span>
                    <el-text>{{ selectedPlant.scientificNameEn }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">习性:</span>
                    <el-text>{{ selectedPlant.habit }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">分支:</span>
                    <el-text>{{ selectedPlant.branch }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">校区:</span>
                    <el-text>{{ selectedPlant.place }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">位置:</span>
                    <el-text>{{ selectedPlant.number }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">领养情况:</span>
                    <el-text>{{ selectedPlant.hasOwner }}</el-text>
                </div>
                <!-- 认养与寄语：数据来自树木数据里的 owner（认养人姓名 / 单位 / 寄语 / 寄语全文） -->
                <div class="blessing-block" :class="{ 'is-empty': !hasBlessing }">
                    <p class="blessing-label">认养与寄语</p>
                    <template v-if="hasBlessing">
                        <p v-if="selectedPlant.slogan" class="blessing-slogan">「{{ selectedPlant.slogan }}」</p>
                        <p class="blessing-owner">
                            —— {{ selectedPlant.ownerName || '匿名认养人' }}<span
                                v-if="selectedPlant.ownerTitle">（{{ selectedPlant.ownerTitle }}）</span>
                        </p>
                        <p v-if="selectedPlant.content" class="blessing-content"
                            :class="{ 'is-clamped': !expanded && contentIsLong }">{{ selectedPlant.content }}</p>
                        <button v-if="contentIsLong" type="button" class="blessing-toggle"
                            @click="expanded = !expanded">{{ expanded ? '收起' : '展开全文' }}</button>
                    </template>
                    <p v-else class="blessing-empty">
                        这棵树还没有认养寄语。点开地图上的它，在留言板写下你的第一句印象吧。
                    </p>
                </div>
                <div class="info-item">
                    <span class="info-label">关键词:</span>
                    <el-text>{{ selectedPlant.key_phrase }}</el-text>
                </div>
                <div class="info-item">
                    <span class="info-label">领养时间:</span>
                    <el-text>{{ selectedPlant.updatetime }}</el-text>
                </div>
            </div>
        </el-card>
    </div>
</template>

<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue';
import emitter from "../bus";
const selectedPlant = ref
({
name: '',
id: '',
scientificName: '',
scientificNameEn: '',
habit: '',
branch: '',
place: '',
number: '',
hasOwner: '',
key_phrase: '',
updatetime: '',
ownerName: '',
ownerTitle: '',
slogan: '',
content: ''
});

/** 寄语全文默认折 4 行，超过 90 字才给"展开全文"，避免短句下方挂个多余按钮 */
const CONTENT_CLAMP_CHARS = 90;
const expanded = ref(false);
const hasBlessing = computed(() => Boolean(
    selectedPlant.value.ownerName || selectedPlant.value.slogan || selectedPlant.value.content
));
const contentIsLong = computed(
    () => (selectedPlant.value.content || '').length > CONTENT_CLAMP_CHARS
);

// 换一棵树就回到折叠态，否则上一棵的"展开"会带到下一棵
watch(() => selectedPlant.value.id, () => { expanded.value = false; });

const handlePlantValueChange = (plantvalue: any) => {
    selectedPlant.value = plantvalue;
};

emitter.on('plantvalueChange', handlePlantValueChange);

onUnmounted(() => {
    emitter.off('plantvalueChange', handlePlantValueChange);
});

</script>

<style lang="scss" scoped>
.plantcard-root {
width: 360px;
flex-shrink: 0;
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
white-space: nowrap;
}

/* ================= 认养与寄语（信笺质感） ================= */
.blessing-block {
    margin: 12px 0 14px;
    padding: 12px 13px;
    border: 1px solid #f2dfe2;
    border-left: 3px solid var(--ecnu-red, #c20a1c);
    border-radius: 10px;
    background: linear-gradient(180deg, #fffafb 0%, #fff 100%);
}

/* 未认养：换成轻虚线占位，不抢视觉重心 */
.blessing-block.is-empty {
    border: 1px dashed #e3e6ea;
    border-left: 3px dashed #dcdfe6;
    background: #fafbfc;
}

.blessing-label {
    margin: 0 0 8px;
    color: var(--ecnu-red, #c20a1c);
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.6px;
}

.blessing-slogan {
    margin: 0 0 6px;
    color: #23262b;
    font-size: 15px;
    font-weight: 600;
    line-height: 1.7;
    overflow-wrap: anywhere;
}

.blessing-owner {
    margin: 0;
    color: #8f959e;
    font-size: 12px;
    line-height: 1.6;
    text-align: right;
}

.blessing-content {
    margin: 10px 0 0;
    padding-top: 9px;
    border-top: 1px dashed #f0dfe2;
    color: #555;
    font-size: 12.5px;
    line-height: 1.85;
    white-space: pre-wrap;
    overflow-wrap: anywhere;

    &.is-clamped {
        display: -webkit-box;
        overflow: hidden;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 4;
    }
}

.blessing-toggle {
    margin-top: 6px;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--ecnu-red, #c20a1c);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
}

.blessing-toggle:hover {
    text-decoration: underline;
}

.blessing-empty {
    margin: 0;
    color: #a8abb2;
    font-size: 12px;
    line-height: 1.75;
}

/* 移动端：信息卡片通栏铺在地图下方，高度自适应 */
@media (max-width: 768px) {
    .plantcard-root {
        flex: 0 0 100%;
        width: 100%;
    }

    .el-card {
        height: auto;
    }

    .plant-info {
        padding: 14px;
    }
}
</style>
