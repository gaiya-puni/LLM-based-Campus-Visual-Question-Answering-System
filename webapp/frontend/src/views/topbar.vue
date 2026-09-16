<template>
    <div style="margin-top: 20px;">
            <el-radio-group v-model="school" size="large" @change="handleCampusChange">
                <el-radio-button label="中北" />
                <el-radio-button label="闵行" />
            </el-radio-group>
            <el-radio-group v-model="iconkind" size="large" style="margin-left: 80px;" @change="IconChange">
                <el-radio-button label="全部" />
                <el-radio-button label="灌木" />
                <el-radio-button label="藤本" />
                <el-radio-button label="草本" />
                <el-radio-button label="乔木" />
            </el-radio-group>
            <el-radio-group v-model="select" size="large" style="margin-left: 80px;" @change="SelectedChange">
                <el-radio-button label="全部" />
                <el-radio-button label="已认领" />
                <el-radio-button label="未认领" />
            </el-radio-group>
            <el-radio-group v-model="model" size="large" style="margin-left: 80px;" @change="ModelChange">
                <el-radio-button label="地图" />
                <el-radio-button label="植物库" />
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
