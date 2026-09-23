<template>
    <div style="margin-bottom: 25px; display: flex; align-items: center; padding: 15px; background: linear-gradient(90deg, #c20a1c 0%, #a50918 100%); border-radius: 12px;">
        <div style="width:55px;height:55px;border-radius:50%;background:white;display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0;">
            <img src="./icons/ecnu_logo.png" style="width:90%;height:90%;object-fit:contain;" />
        </div>
            <el-text style="font-family: 'Times New Roman', Times, serif; color: white; margin-left: 10px; font-size: 19px; font-weight: bold; line-height: 1.4;">
                ECNU校园可视问答系统</el-text>
    </div>

    <el-radio-group v-model="isCollapse" style="margin-bottom: 20px">

        <el-radio-button :value="false">显示</el-radio-button>
        <el-radio-button :value="true">隐藏</el-radio-button>
    </el-radio-group>
    <el-menu :default-active="route.path" class="el-menu-vertical-demo" :collapse="isCollapse" :router="true">
        <el-menu-item index="/index">
            <el-icon>
                <House />
            </el-icon>
            <template #title>主页</template>
        </el-menu-item>
        <el-menu-item index="/map">
            <el-icon>
                <MapLocation />
            </el-icon>
            <template #title>植物地图</template>
        </el-menu-item>
        <el-menu-item index="/classify">
            <el-icon>
                <Menu />
            </el-icon>
            <template #title>植物图表</template>
        </el-menu-item>
        <el-menu-item index="/plantmessage">
            <el-icon>
                <Notebook />
            </el-icon>
            <template #title>植物寄语</template>
        </el-menu-item>
        <el-menu-item index="/chatbot">
            <el-icon>
                <ChatDotRound />
            </el-icon>
            <template #title>智能问答</template>
        </el-menu-item>
    </el-menu>
</template>

<script setup lang="ts">
import { ref } from "vue"
import { useRoute } from "vue-router";
const route = useRoute();

const isCollapse = ref(false)

</script>

<style lang="scss" scoped></style>