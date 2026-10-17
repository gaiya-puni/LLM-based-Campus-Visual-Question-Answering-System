<template>
    <div class="topbar-root">
            <el-radio-group v-model="school" size="large" @change="handleCampusChange">
                <el-radio-button value="中北" label="中北" />
                <el-radio-button value="闵行" label="闵行" />
            </el-radio-group>
            <el-radio-group v-model="iconkind" size="large" class="topbar-group" @change="IconChange">
                <el-radio-button value="全部" label="全部" />
                <el-radio-button value="灌木" label="灌木" />
                <el-radio-button value="藤本" label="藤本" />
                <el-radio-button value="草本" label="草本" />
                <el-radio-button value="乔木" label="乔木" />
            </el-radio-group>
            <el-radio-group v-model="select" size="large" class="topbar-group" @change="SelectedChange">
                <el-radio-button value="全部" label="全部" />
                <el-radio-button value="已认领" label="已认领" />
                <el-radio-button value="未认领" label="未认领" />
            </el-radio-group>
            <el-radio-group v-model="model" size="large" class="topbar-group" @change="ModelChange">
                <el-radio-button value="地图" label="地图" />
                <el-radio-button value="植物库" label="植物库" />
            </el-radio-group>
    </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import emitter from "../bus"

const school = ref('中北')
const select = ref('全部')
const iconkind = ref('全部')
const model = ref('地图')

const handleCampusChange = () => {
    emitter.emit('campusChange', school.value);
}

const SelectedChange = () => {
    emitter.emit('selectedChange', select.value);
}

const IconChange = () => {
    emitter.emit('iconChange', iconkind.value);
}

const ModelChange = () => {
    emitter.emit('modelChange', model.value);
}
</script>

<style lang="scss" scoped>
.topbar-root {
    margin-top: 20px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px 0;
}

.topbar-group {
    margin-left: 80px;
}

/* 移动端：筛选按钮换行铺排、缩小尺寸，适配小屏触控 */
@media (max-width: 768px) {
    .topbar-root {
        margin-top: 10px;
        gap: 8px;
        padding: 0 2px;
    }

    .topbar-group {
        margin-left: 0;
    }

    .topbar-root :deep(.el-radio-button__inner) {
        padding: 8px 12px;
        font-size: 13px;
    }
}
</style>
