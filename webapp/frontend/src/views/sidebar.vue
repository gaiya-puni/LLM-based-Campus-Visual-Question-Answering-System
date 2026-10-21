<template>
  <div class="sidebar-root">
    <div style="margin-bottom: 25px; display: flex; align-items: center; gap: 10px; padding: 15px; background: linear-gradient(90deg, #c20a1c 0%, #a50918 100%); border-radius: 12px;">
        <img src="./icons/tree.png" alt="知行校园图标" style="width:48px;height:48px;object-fit:contain;flex-shrink:0;" />
        <el-text style="font-family: 'Times New Roman', Times, serif; color: white; font-size: 19px; font-weight: bold; line-height: 1.4;">
            知行校园 CampusMind</el-text>
    </div>

    <el-radio-group v-model="isCollapse" style="margin-bottom: 20px">

        <el-radio-button :value="false">显示</el-radio-button>
        <el-radio-button :value="true">隐藏</el-radio-button>
    </el-radio-group>
    <div class="sidebar-menu">
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
        <el-menu-item index="/place-review">
            <el-icon>
                <DocumentChecked />
            </el-icon>
            <template #title>数据审核</template>
        </el-menu-item>
        <el-menu-item v-if="showCommentReview" index="/comment-review">
            <el-icon>
                <ChatDotSquare />
            </el-icon>
            <template #title>评论审核</template>
        </el-menu-item>
        <el-menu-item index="/campus-build">
            <el-icon>
                <DataAnalysis />
            </el-icon>
            <template #title>热力图生成</template>
        </el-menu-item>
    </el-menu>
    </div>

    <!-- 端切换：侧栏左下角。菜单在中间区域独立滚动，按钮占一行固定高度，互不遮挡 -->
    <div class="sidebar-foot">
        <view-mode-switch placement="inline" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRoute } from "vue-router";
import { ensureReviewAccess, useReviewEnabled } from "../composables/useReviewAccess";
import ViewModeSwitch from "../components/ViewModeSwitch.vue";
import {
    ChatDotRound,
    ChatDotSquare,
    DataAnalysis,
    DocumentChecked,
    House,
    MapLocation,
    Menu,
    Notebook,
} from "@element-plus/icons-vue";

const route = useRoute();

const isCollapse = ref(false)

// 审核态未知（null）时保留入口，只有后端确认"未配置"才隐藏——见 useReviewAccess
const reviewEnabled = useReviewEnabled();
const showCommentReview = computed(() => reviewEnabled.value !== false);

onMounted(() => {
    ensureReviewAccess();
});

</script>

<style lang="scss" scoped>
/*
 * 侧栏改成"整列定高 + 菜单独立滚动 + 底部常驻"：
 * 端切换按钮固定在左下角，且不会被菜单项压住（窗口只有 600px 高时也不会重叠）。
 * sticky 让侧栏在主内容滚动时留在视口内，按钮始终可见。
 */
.sidebar-root {
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 20px;
    height: calc(100vh - 40px);
    min-height: 420px;
}

.sidebar-menu {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;

    /* 细滚动条，避免挤占 240px 的侧栏宽度 */
    &::-webkit-scrollbar {
        width: 4px;
    }

    &::-webkit-scrollbar-thumb {
        border-radius: 999px;
        background: rgba(35, 38, 43, 0.16);
    }
}

.sidebar-foot {
    flex-shrink: 0;
    display: flex;
    justify-content: center;
    padding: 8px 0 4px;
}
</style>
