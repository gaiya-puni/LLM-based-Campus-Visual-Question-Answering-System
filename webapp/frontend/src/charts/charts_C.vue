<script setup lang="ts">
import Echarts from '../components/Echarts.vue';
import { chartsCOption } from './options/chartsCOption';

</script>

<template>
    <div :style="{ width: '900px', height: '600px' }">
        <Echarts :option="chartsCOption" />
    </div>
</template>
