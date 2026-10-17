<template>
  <view class="webview-page">
    <!-- 套壳入口：加载已上线且完成移动端适配的 H5 站点 -->
    <web-view :src="targetUrl" @message="onMessage"></web-view>
  </view>
</template>

<script>
export default {
  data() {
    return {
      // 线上站点：H5 已做全站响应式 + PWA，壳只负责承载与原生能力（相机/定位）授权
      targetUrl: 'https://ecnucampusvqa.cn/'
    };
  },
  methods: {
    // H5 页面可通过 uni.postMessage 向壳传递消息（预留）
    onMessage(e) {
      console.log('web-view message:', JSON.stringify(e.detail));
    }
  }
};
</script>

<style>
.webview-page {
  width: 100%;
  height: 100vh;
}
</style>
