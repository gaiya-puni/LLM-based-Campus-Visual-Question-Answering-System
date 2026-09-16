<template>
  <div class="common-layout" id="building">
    <router-view v-if="isFullScreen"></router-view>

    <el-container v-else style="margin-top: 20px; margin-left: 20px;">
      <el-aside width="240px"><sidebar /></el-aside>
      <el-container>
        <el-main><router-view></router-view></el-main>
      </el-container>
    </el-container>
  </div>
</template>


<script setup lang="ts">
// 引入你的组件

import { computed } from 'vue';
import { useRoute } from 'vue-router';
import sidebar from './views/sidebar.vue';

const route = useRoute();
const isFullScreen = computed(() => route.meta.fullScreen === true);

</script>

<style>

#building{
background:url("../src/assets/background.jpg");
width:100%;
height:100%;
position:fixed;
background-size:100% 100%;
}
</style>
