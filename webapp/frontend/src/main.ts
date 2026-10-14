import { createApp } from "vue";
import "./style.css";
import App from "./App.vue";
import "element-plus/dist/index.css";
import router from "./router";
import ElementPlus from "element-plus";

const app = createApp(App);
app.use(router);
app.use(ElementPlus);

router.isReady().then(() => {
  app.mount("#app");
});
