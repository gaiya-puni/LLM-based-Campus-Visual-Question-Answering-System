import { createRouter, createWebHashHistory} from "vue-router";
const routes = [
  {
    path: "/",
    name: "login",
    meta: {
      title: "用户登录",
      fullScreen: true
    },
    component: () => import("../login/login.vue"),
  },
  {
    path: "/changePassword",
    name: "changePassword",
    meta: {
      title: "密码修改",
      fullScreen: true
    },
    component: () => import("../login/changePassword.vue"),
  },
  {
    path: "/index",
    name: "index",
    meta: {
      title: "首页",
    },
    component: () => import("../views/index.vue"),
  },
  {
    path: "/classify",
    name: "classify",
    meta: {
      title: "植物图表",
    },
    component: () => import("../views/classify.vue"),
  },
  {
    path: "/map",
    name: "map",
    meta: {
      title: "地图",
    },
    component: () => import("../views/map.vue"),
  },
  {
    path: "/plantmessage",
    name: "plantmessage",
    meta: {
      title: "植物寄语",
    },
    component: () => import("../views/plantmessage.vue"),
  },
  {
    path: "/chatbot",
    name: "chatbot",
    meta: {
      title: "智能问答",
    },
    component: () => import("../views/chatbot.vue"),
  },
  {
    path: "/place-review",
    name: "placeReview",
    meta: {
      title: "数据审核",
    },
    component: () => import("../views/placeReview.vue"),
  },
];
const router = createRouter({ history: createWebHashHistory(), routes });
export default router;
