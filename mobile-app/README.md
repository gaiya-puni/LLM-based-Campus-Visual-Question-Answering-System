# mobile-app 安卓套壳工程

用 uni-app 空壳（单页 `web-view`）承载已上线的 H5 站点 `https://ecnucampusvqa.cn/`，
通过 **HBuilderX 云打包** 生成 Android APK。无需 Mac、无需 Apple 开发者账号；
iOS 端由 H5 的 PWA 能力兜底（Safari「添加到主屏幕」全屏运行），本工程不需要出 iOS 包。

## 目录结构

```
mobile-app/
├── pages/index/index.vue   # 唯一页面：web-view 加载线上站点
├── pages.json              # 页面配置（自绘导航，沉浸式）
├── manifest.json           # 应用名/版本/Android 权限（网络、相机、定位）
├── App.vue / main.js       # uni-app Vue3 工程入口
└── static/logo.png         # 应用图标源图（512x512，与 PWA 图标同源）
```

## 云打包 APK 步骤

1. 安装 HBuilderX（最新正式版，含 App 开发插件）。
2. 文件 → 导入 → 从本地目录导入，选择本 `mobile-app/` 目录。
3. 双击 `manifest.json` 可视化配置：
   - 基础配置：填入 uni-app `appid`（点"重新获取"）；确认应用名"校园可视可答"。
   - App 图标配置：选 `static/logo.png`，点"自动生成所有图标并替换"。
   - App 启动界面（splash）：按需配置启动图。
4. 菜单 发行 → 原生 App-云打包：
   - Android：勾选"使用公共测试证书"（演示够用；正式发布再申请自有证书）；
   - 打包方式选"正式包"，渠道包不需要；
   - 提交后等待云端构建完成，控制台会给 APK 下载链接。
5. 把 APK 发到安卓手机安装（需允许"安装未知来源应用"）。

## 打包前检查清单

- `pages/index/index.vue` 中 `targetUrl` 指向的站点可正常访问；
- 后端 `webapp/backend` 已部署且 `/api/` 接口在线（问答、热图依赖）；
- 线上站点已完成移动端响应式适配（本仓库 `webapp/frontend` 已实现）。

## 注意事项

- **不要在本工程里写业务逻辑**：业务改动一律改 `webapp/frontend` 并部署到线上，
  壳会自动加载最新版，无需重新打包 APK。
- 高德地图密钥由 H5 站点自己注入（`index.html` 中的占位符在部署时替换），
  壳工程不持有任何密钥。
- 修改了 `manifest.json`（应用名/图标/权限）才需要重新打包；只改 H5 无需重打。

## iOS 怎么办（本项目决策）

原生 iOS 上架需要 Apple 开发者账号（$99/年）、签名证书与 App Store 审核，
纯 web-view 套壳应用也很难过审。因此本项目 iOS 采用 **PWA**：
用 Safari 打开 https://ecnucampusvqa.cn/ → 分享 →「添加到主屏幕」，
即可全屏独立运行，体验接近原生 App，零成本免审核。
