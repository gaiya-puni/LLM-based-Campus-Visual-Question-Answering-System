<script setup lang="ts">
import Echarts from '../components/Echarts.vue';
import { chartsBOption } from './options/chartsBOption';

</script>

<template>
    <!-- 旭日图（sunburst）按容器比例自适应，窄屏降低高度即可完整显示 -->
    <div class="chart-fit" :style="{ width: 'min(600px, 100%)', height: '600px' }">
        <Echarts :option="chartsBOption" />
    </div>
</template>

<style scoped>
@media (max-width: 768px) {
    .chart-fit {
        height: 420px !important;
    }
}
</style>
