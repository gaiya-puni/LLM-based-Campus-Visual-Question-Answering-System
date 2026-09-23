<template>
    <div style="margin-top: 20px;">
            <el-radio-group v-model="school" size="large" @change="handleCampusChange">
                <el-radio-button value="中北" label="中北" />
                <el-radio-button value="闵行" label="闵行" />
            </el-radio-group>
            <el-radio-group v-model="iconkind" size="large" style="margin-left: 80px;" @change="IconChange">
                <el-radio-button value="全部" label="全部" />
                <el-radio-button value="灌木" label="灌木" />
                <el-radio-button value="藤本" label="藤本" />
                <el-radio-button value="草本" label="草本" />
                <el-radio-button value="乔木" label="乔木" />
            </el-radio-group>
            <el-radio-group v-model="select" size="large" style="margin-left: 80px;" @change="SelectedChange">
                <el-radio-button value="全部" label="全部" />
                <el-radio-button value="已认领" label="已认领" />
                <el-radio-button value="未认领" label="未认领" />
            </el-radio-group>
            <el-radio-group v-model="model" size="large" style="margin-left: 80px;" @change="ModelChange">
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

<style lang="scss"></style>
