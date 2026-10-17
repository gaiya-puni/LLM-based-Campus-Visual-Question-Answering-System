<script setup lang="ts">
import Echarts from '../components/Echarts.vue';
import { chartsAOption } from './options/chartsAOption';

</script>

<template>
    <!-- 桑基图信息密度高，窄屏不做压缩，改为容器横向滑动查看 -->
    <div class="chart-scroll">
        <div class="chart-canvas" :style="{ width: '900px', height: '600px' }">
            <Echarts :option="chartsAOption" />
        </div>
        <p class="chart-scroll-hint">图表较宽，可左右滑动查看完整内容</p>
    </div>
</template>

<style scoped>
.chart-scroll {
    width: 100%;
    max-width: 900px;
    /* 画布固定 900px：容器一旦窄于画布（平板与手机）就启用容器内横向滑动，
       否则 769–900px 区间骨架仍是桌面布局、又没有滚动容器，900px 画布会撑破整页。
       宽屏下容器恰为 900px，不产生滚动条，观感与改动前一致。 */
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}

.chart-scroll-hint {
    display: none;
}

/* 仅在容器确实窄于画布时提示可左右滑动（900px 与 .chart-scroll 的 max-width 对齐） */
@media (max-width: 900px) {
    .chart-scroll-hint {
        display: block;
        position: sticky;
        left: 0;
        width: 100%;
        margin: 6px 0 0;
        padding: 0 4px;
        font-size: 12px;
        color: #909399;
    }
}
</style>
