<template>
    <div>
        <!-- 图片轮播（移动端取消 card 模式并降高，避免小屏裁切） -->
        <el-carousel :interval="4000" :height="isMobile ? '220px' : '400px'" :type="isMobile ? undefined : 'card'">
            <el-carousel-item v-for="item in images" :key="item.url">
                <!-- 使用v-bind绑定不同的图片地址 -->
                <img :src="item.url" style="width: 100%; height: 100%;" />
            </el-carousel-item>
        </el-carousel>

        <!-- 介绍卡片 -->
        <el-card class="intro-card">
            <h3>知行校园 CampusMind——基于大模型与地图交互的校园可视问答系统</h3>
            <p>
                漫步在大学校园，随处可见品种繁多的植物，四季各有风景。知行校园 CampusMind 将校园植物、地点信息与智能问答结合，帮助你发现身边的校园之美。
                校园中的植物不仅承载着自然之美，也记录着同学们的认领与寄语。在日夜生活的校园中，了解一株植物的名字与故事，是一件浪漫且有意义的事。
                于是我们结合了前后端、数据库的网页开发以及爬虫、可视化等的数据分析，以这一网站的形式进行呈现，旨在让大家对学校的植物与领养系统有更多认识。开启你的探索吧……
            </p>
        </el-card>
        <el-container>
            <el-main>
                <predict />
            </el-main>
        </el-container>
    </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useIsMobile } from "../composables/useIsMobile";

const isMobile = useIsMobile();

import image1 from "../assets/carousel1.jpg";
import image2 from "../assets/carousel2.jpg";
import image3 from "../assets/carousel3.jpg";
import image4 from "../assets/carousel4.jpg";
import image5 from "../assets/carousel5.jpg";
import predict from "./predict.vue";

const images = ref([
    { url: image1 },
    { url: image2 },
    { url: image3 },
    { url: image4 },
    { url: image5 },
]);


</script>

<style lang="scss">
/* 你现有的样式 */

/* 介绍卡片的额外样式 */
.intro-card {
    margin-top: 20px;
    /* 根据需要调整上边距 */
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.intro-card h3 {
    color: #333333;
    font-size: 1.5rem;
    margin-bottom: 10px;
    text-align: center;
}

.intro-card p {
    color: #555555;
    font-size: 1rem;
    line-height: 1.5;
}

/* 调整图片轮播的样式 */
.el-carousel {
    max-width: 100%;
    margin: 0 auto;
}

/* 移动端：介绍卡片内边距收紧 */
@media (max-width: 768px) {
    .intro-card {
        margin-top: 12px;
    }

    .intro-card h3 {
        font-size: 1.2rem;
    }

    .intro-card p {
        font-size: 0.9rem;
    }
}
</style>
