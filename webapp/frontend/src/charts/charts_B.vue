<script setup lang="ts">
import Echarts from '../components/Echarts.vue';
import { chartsBOption } from './options/chartsBOption';

</script>

<template>
    <div :style="{ width: '600px', height: '600px' }">
        <Echarts :option="chartsBOption" />
    </div>
</template>
