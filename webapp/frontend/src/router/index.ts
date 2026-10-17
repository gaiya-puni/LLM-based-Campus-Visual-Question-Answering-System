import { createRouter, createWebHashHistory} from "vue-router";
import { ElMessage } from "element-plus";
import { ensureReviewAccess } from "../composables/useReviewAccess";
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
  {
    path: "/campus-build",
    name: "campusBuild",
    meta: {
      title: "校园热力图生成",
    },
    component: () => import("../views/campusBuild.vue"),
  },
  {
    path: "/comment-review",
    name: "commentReview",
    meta: {
      title: "评论审核",
      // 只有后端配好 USERDATA_REVIEW_TOKEN 时才允许进入；未配置直接回首页
      requiresReview: true,
    },
    component: () => import("../views/commentReview.vue"),
  },
];
const router = createRouter({ history: createWebHashHistory(), routes });

/**
 * 审核页守卫：`/comment-review` 只在后端已启用审核态时可进入。
 *
 * 拦住的是"误闯"：`.env` 没配密钥时该页所有接口都是 403，进去了也只能看到
 * 一张空表和密钥输入框，容易让人以为功能坏了。密钥校验本身仍在后端。
 * 探测失败（`null`）不拦——旧后端没有该接口时不能把页面变成死链。
 */
router.beforeEach(async (to) => {
  if (to.meta?.requiresReview !== true) return true;
  const enabled = await ensureReviewAccess();
  if (enabled === false) {
    ElMessage({
      message: '「评论审核」未开启：请在根目录 .env 配置 USERDATA_REVIEW_TOKEN（至少 16 位）后重启后端',
      type: 'warning',
      duration: 4500,
    });
    return { path: '/index' };
  }
  return true;
});

export default router;
