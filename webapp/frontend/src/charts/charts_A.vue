<script setup lang="ts">
import Echarts from '../components/Echarts.vue';
import { chartsAOption } from './options/chartsAOption';

</script>

<template>
    <div :style="{ width: '900px', height: '600px' }">
        <Echarts :option="chartsAOption" />
    </div>
</template>
