# HANDOVER

**面向**：下一位接手本项目的人
**本文档创建于**：2026-10-14（2026-10-15 补移动端专章；同日补第二轮：缺陷修复与测试能力；2026-10-16 补第三、四轮：teleport 弹窗宽度修复 + 寄语列表改造；同日补第五轮：点位留言板与评论审核，见第七节；2026-10-17 补第六轮：审核入口显隐 / 留言板收紧 / 植物寄语回归 / 端切换，见第八节；2026-10-19 补第七轮：热力图生成语义解析与空间精度，见第九节；同日补第八轮：生成页候选数据概况图表，见第十节；同日补第九轮：采集召回率与候选分组，见第十一节；同日补第十轮：生成页用户手动补点，见第十二节）
**对应分支 / HEAD**：`test/dev` @ `5dd1ea5`（工作区另有未提交改动，勿用 `git reset --hard`）
**最近一次改动**：2026-10-19 第十轮——**生成页用户手动补点**：新增 `tools/campus_generator/user_pois.py`（任务私有层 `user_added_pois.json`），`review.py` 合并补点并支持管理员转正，`preview.py` 把补点纳入临时热图；新增 `POST/DELETE /api/campus/build/<id>/user-pois`（无需令牌，按会话隔离）；前端工具栏「手动补点」+ 地图落点 + 弹窗表单 + 「我的添加」分组。详见第十二节
**上一轮改动**：2026-10-19 第九轮——**采集召回率 + 候选分组 + 得分差异化**：`harvest.py` 抽出 `_candidates_from_pois()` 并新增 `AmapGridTypeHarvester`（5×5 网格 × 高德 `types` 分类码，不带 `keywords`，含 120000 商务住宅；跨格按 `sourceId` 去重、单格失败隔离、逐格缓存）；`discovery.harvesters()` 接入，`CAMPUS_GRID_SCAN=0` 可关；`quality.py` 新增 `_AMAP_CATEGORY_RELEVANCE` 按大类给 `general` 主题分层相关度并对配套设施降权；`campusBuild.vue` 候选按 `subCategory` 分组，校园相关类型默认展开。详见第十一节
**上一轮**：2026-10-19 第八轮——**生成页候选数据概况三图**（纯前端，后端与热图缓存零改动）：新增 `components/candidateSummary.ts`（质量分项均值 / 类别归一 / 距离分箱三个纯函数）与 `components/CandidateDataSummary.vue`（折叠区 + 三选一图），挂在 `views/campusBuild.vue` 候选面板的 `review-stats` 之下；`api/campusBuild.ts` 补 `CandidatePoi.evidence` 声明；新增 `tests/candidateSummary.test.mjs`（10 项）与 `npm run test:summary`。详见第十节
**上一轮**：2026-10-19 第七轮——**热力图生成语义解析 + 校区中心/范围 + 点位密度预览**。①`tools/campus_generator/discovery.py`：请求前缀循环剥离 + “大学/学院后缀 + 左侧边界锚定”校名抽取，修复“你给我搜索北京大学”被当成校名；显式类别（建筑/花草树木/地标/亭子/河岸…）进入采集关键词；`infer_theme` 去掉单字“花”触发；`500米/1.5公里/2km内` 半径解析，未登记学校默认 **1200m**；`request_poi`（高德 place/text）优先校正中心，地理编码地址必须含校名，否则退“只编码校名”，校区无法验证时回退校本部并写 `discovery.warning`。②`harvest.py` 按 `sourceId` 与同名 30m 去重、按距离排序。③`preview.py` / `preview_heatmaps.py` 新增预览专用 **点位密度** 场景（混合类请求默认），收紧 `supportMeters` 200→120、`maxInfluenceMeters` 450→300；`campus_build_api` 预览白名单含 `density`。④前端 `campusBuild.vue` 展示中心/来源/半径/清洗后请求/回退提示，画搜索范围圆，候选显示“距中心 x m”，预览自动 `setBounds` + Top5 热区标签 + “显示已选点位”开关；`heatmapRender.ts` 新增仅预览使用的分位拉伸，`ImageLayer` 改显式 `AMap.Bounds`。**上一轮**（审核入口/留言板/寄语/端切换，见第八节）涉及 `review_guard.py`、`PoiCommentPanel.vue`、`ViewModeSwitch.vue` 等；`技术报告/`、`商业计划书/`、`data/`、正式 `heatmap_cache/` 均未改动

---

## 一、本轮改动范围

**全部为新增文件，不触碰 `webapp/`、`data/`、`analysis/`、`visualization/`、`tools/`、`scripts/` 中任何已有内容。**

新增目录 `商业计划书/`：

```
商业计划书/
├── 基于大模型的校园可视可答系统-商业计划书_v1.docx   # 主交付物（40 页）
├── 数据来源与参考文献.md                            # 外部数据登记表 [S1]—[S65]
└── build/
    ├── build_plan.py        # 生成入口
    ├── blocks.py            # 内容块定义（内容与排版分离）
    ├── docx_render.py       # 渲染层：样式、目录域、分页页码、图表编号、匿名化检查
    ├── figures.py           # matplotlib 图表生成
    ├── fetch_chart_pngs.py  # HTML 可视化 → PNG
    ├── finance_model.py     # 财务测算模型
    ├── export_pdf.py        # docx → PDF（Word COM）
    ├── data/market_data.csv # 市场数据表
    ├── data/finance_results.json
    └── assets/figures/      # 全部插图 PNG
```

另新增仓库根目录的 `PROJECT_STATUS.md` 与 `HANDOVER.md`（此前不存在）。

## 二、设计思路（为什么这样组织）

1. **内容与排版分离。** 正文按章拆成 `content/*.py` 的纯数据块（标题、段落、表格数据、插图路径），排版参数全部集中在 `docx_render.py`。想改字号、行距、编号规则，只动渲染层；想改文字，只动内容层，互不影响。
2. **一切数字可复算。** 市场数据放在 `data/market_data.csv`（每条含来源编号），财务假设与测算在 `finance_model.py` 中显式声明，图表由脚本从这两处数据生成。**修改假设后重跑脚本即可得到全套新数字与新图表**，不需要手工改文档。
3. **真实目录域而非手写目录。** `docx_render.py` 插入 `TOC \o "1-2"` 域并在 `settings.xml` 中置 `updateFields=true`，Word 打开时自动刷新页码。手写目录的页码必然错位。
4. **匿名化是渲染层的安全网。** 赛事要求作品材料不得出现学校名称、校徽与指导教师姓名。`docx_render.py` 中的 `FORBIDDEN_TOKENS` 会对渲染出的全部文本做检查，命中即抛错并终止生成——**宁可不生成，也不生成一份含违禁信息的文档**。正文本身也已按匿名口径撰写（“试点校区 A/B”“教务验证高校”等）。
5. **复用项目已有分析成果而不是另造一套。** 植物认养预测的混淆矩阵与 ROC 曲线直接从 `analysis/prediction/svm/` 复制；桑基图与词云从 `visualization/` 的 HTML 用无头浏览器截图得到。

## 三、已知坑（务必先读）

1. **Chinese 全角引号。** 内容模块是 Python 字符串，正文里请一律使用中文引号 `“ ”`；写 ASCII 双引号会直接导致语法错误。
2. **OOXML 子元素顺序是强制的。** `w:pPr`、`w:tblPr`、`w:tcPr` 中的元素顺序写错会被严格校验拒绝（`This element is not expected`）。`docx_render.py` 里的 `_insert_ordered()` 就是为此写的；此前那个 bug 的根因是拿 `child.tag`（带命名空间）去比顺序表，导致永远退化成“追加到末尾”。
3. **两份复用的模型图路径依赖 `build/../..`。** 即 `商业计划书/build` → 上溯两级才是工作区根目录的 `analysis/`。改动目录结构时记得同步 `figures.py` 中的 `PREDICTION_DIR`。
4. **HTML 可视化的两个采集坑。** `visualization/charts/sunburst/sunburst-only.html` 用 `d3.json()` 读同目录 JSON，`file://` 下会被 CORS 拒绝，必须用本地 HTTP 服务托管；同一页面引用的 `http://d3js.org/d3.v3.min.js` 会被拦，脚本已用 `d3.json` 垫片替代。另外 `sankey.html` 首次冷启动时 CDN 可能加载较慢，`fetch_chart_pngs.py` 已带三次重试。
5. **图内文字也要匿名。** 桑基图的节点里含有学校名称，而 ECharts 的文字画在 canvas 上、无法从 DOM 事后修改。`fetch_chart_pngs.py` 的做法是在本地 HTTP 层就替换掉源文件中的字样，并且**同时替换 `\uXXXX` 转义形式**——pyecharts 生成的中文在 HTML 里是转义存储的，只替换原文字符串无效。
6. **导出 PDF 前先关闭阅读器。** Word COM 无法覆盖被占用的 PDF，`export_pdf.py` 会直接报错提示；脚本同时只会结束“没有主窗口”的 WORD 进程，不影响你正在编辑的文档。
7. **篇幅是被“压”出来的。** 文档初版 64 页，通过正文改五号、行距 1.32、表格 9pt、参考文献 8.5pt、插图统一缩放到 0.82、目录压到二级、附录连续排版，最终落在 40 页。**若要恢复通风格，请优先调整 `docx_render.py` 顶部的常量**（`BODY_SIZE`、`LINE_SPACING`、`TABLE_SIZE`、`REF_SIZE`、`FIG_SCALE`），不要逐处改内容。
8. **数据纪律是硬约束。** 项目没有正式问卷数据，因此正文中所有画像、优先级判断都标注为“定性判断、待调研验证”；市场规模的不同机构口径是**并列呈现**的，并明确说明差异原因；凡二手转述的数据一律标注“待核实”。不要为了好看把估算写成事实。

## 四、未来工作

1. **装帧**：封面主视觉、背景图与版式装饰由队友另行完成，本次只交付文字与图表齐全的初版。
2. **数据补强**：`数据来源与参考文献.md` 第九节列出了 10 条存疑或待核实条目（如“高德覆盖 1300 所高校”与官方口径冲突、研学旅行市场规模原始机构不明）。正式提交前建议逐条复核。
3. **调研**：面向试点高校开展正式问卷，替换商业计划书中的定性判断。
4. **同步到队长仓库**：本目录的 `商业计划书/` 若需进入团队仓库，请先按 `scripts/check_publication.py` 做一次发布前检查（确认无密钥泄漏）。本计划书正文已做匿名化处理，但 `build/` 下的生成脚本引用了 `analysis/prediction/svm/` 的真实数据文件路径，属内部工具，是否入库由队长决定。

## 五、技术报告专章（2026-10-14 新增，作品提交件）

### 1. 目录与产出

```
技术报告/
├── 基于大模型的校园可视可答系统-技术报告_v1.docx   # 主交付物（21 页，11 图 17 表）
├── 基于大模型的校园可视可答系统-技术报告_v1.pdf    # 提交件（1.28 MB，官方上限 10 MB）
├── 作品简介.md                                    # 报名系统字段（名称 14 字、简介 285 字）
├── 字数核对.py                                    # 核对上述两个字段的字数
├── 数据来源与参考文献.md                          # [R1]—[R13] 来源登记 + 登记编号↔正文上标序号映射
└── build/
    ├── build_report.py       # 生成入口：内容层 → 渲染层 → docx（--pdf 追加导出与页数统计）
    ├── blocks.py             # 内容块定义（含新增的 code 块，用于附录代码节选）
    ├── docx_render.py        # 渲染层：排版常量、图表编号、目录域、分节页码、双重安全网
    ├── export_pdf.py         # docx → PDF（Word COM），只结束无主窗口的 WORD 进程
    ├── check_report.py       # 交付前自检（页码、目录域、编号连续性、插图、匿名与凭据）
    ├── figures_tech.py       # 新增技术插图（数据来自 report_facts.json 与热力图评测结果）
    ├── fetch_chart_pngs.py   # 复用 visualization/ 的 HTML 图表转 PNG（含图内匿名替换）
    ├── data/report_facts.json# 报告数字的单一来源，每条附仓库内复核路径
    ├── content/              # 按官方八章大纲拆分的内容模块 + facts.py 取数器
    └── assets/figures/       # 26 张复用图 + 新增 fig_tech_*.png
```

### 2. 设计思路

1. **与商业计划书完全隔离的第二条管线。** `blocks.py` / `docx_render.py` / `export_pdf.py` 均为**克隆**而来，改动只发生在新目录内；`商业计划书/`、`webapp/`、`data/`、`analysis/`、`visualization/`、`tools/`、`scripts/` 一律只读引用。
2. **章节硬对齐官方《创新方案参考大纲》八章**（项目概述、需求分析、解决方案设计、项目实施、测试与验证、应用效果与成果、总结与展望、附录）；正文之后、附录之前另有独立的**参考文献**章，与前后章节均另起一页。报告只保留正式体例，不放“评分项与章节对照”一类内部校对内容。
3. **数字单一来源。** 正文通过 `content/facts.py` 从 `data/report_facts.json` 取数，报告里每个数字都带仓库内复核路径；改数字只需改 JSON 后重跑。
4. **图表由真实结果驱动。** 热力图评测图与时序对照图直接读取 `webapp/backend/heatmap_cache/evaluation.json`；核密度示意图按 `scene_heatmaps.py` 的自适应带宽公式复现（合成一维数据，图题已标注为示意）。
5. **双重安全网。** 渲染层在保存前执行匿名检查（校名、校徽、指导教师姓名与可反推校名的地名）与凭据检查（密钥样式），任一命中即抛错、拒绝生成文档。**这是硬要求，不要为了方便把检查关掉。**

### 3. 已知坑（技术报告部分）

1. **官方明确要求匿名。** 规则汇总 PDF“三、作品基本要求 →（五）公平性”规定作品材料不得出现学校名称及 Logo、指导教师信息；报告正文统一用“试点校区 A / 试点校区 B / 外部验证校区”。**插图里的文字也算材料**，新增图件的标签必须使用匿名代号（`figures_tech.py` 的 `CAMPUS_ALIAS` 已统一映射）。
2. **篇幅靠顶部常量调，不要逐处改正文。** `docx_render.py` 顶部 `BODY_SIZE`、`TABLE_SIZE`、`CAPTION_SIZE`、`REF_SIZE`、`SUP_SIZE`、`FIG_SCALE`、`LINE_SPACING` 是控制旋钮；当前取 10.5 / 8.0 / 9.0 / 8.0 / 7.5 / 0.80 / 1.30，对应 21 页。**只对“参考文献”与“附录”两章使用强制分页**（`B.chapter(..., page_break=True)`），其余章间不分页（否则会多出约 5 页留白），增加分页前请先确认页数。
3. **字体与对齐的三个坑（都已在渲染层修掉，改样式后请重跑自检）。** ① **表格文本一律居中**：`PL 表格` / `PL 表格头` 两个样式都设为居中，`_fill()` 里不要再按字数长短切换对齐方式。② **主题字体陷阱**：python-docx 自带模板在 `docDefaults` 与 Heading 样式里写着主题字体（`w:asciiTheme="minorHAnsi"`、`w:eastAsiaTheme="majorEastAsia"` 等），而 Word 中**主题属性优先于字体名**，会把手写的 `w:eastAsia="黑体"` 覆盖掉，标题实际渲染成主题的 Cambria / 等线一类；因此 `_apply_fonts()` 写入字体时必须删掉这四个主题属性，`_set_default_fonts()` 还要把 `docDefaults` 也锁成“Times New Roman + 宋体”（否则目录域的点引导线、超链接字符样式会漏出 Cambria）。③ **标题为黑体不加粗**：黑体没有真正的粗体字面，`w:b` 只会触发 Word 的模拟加粗（笔画发糊），层级靠字号区分；需要加粗改 `HEAD_BOLD`。`check_report.py` 第 7、8 项分别核对表格居中（17 张表、368 个单元格）与标题字体。
4. **引用序号只在 `build/content/references.py` 里定义。** 正文用 `R.cite("R4")` 生成 `^[1]^` 标记，渲染层把 `**加粗**` 与 `^[n]^`（上标方括号，7.5 pt）统一处理；不要手写序号，改引用序号只改 `CITE_INDEX`。`数据来源与参考文献.md` 里的 [R1]—[R13] 是内部登记编号，与该文件的映射表一一对应。
5. **控制台是 GBK。** 直接 `print` 勾叉等符号会抛 `UnicodeEncodeError`；脚本里已统一用 `[通过]`/`[问题]` 这样的 ASCII 标记，或先 `sys.stdout.reconfigure(encoding='utf-8')`。
6. **Word COM 导出前先关闭 PDF 阅读器**（与商业计划书相同），否则报“只读文件”。
7. **改内容层字符串请用全角引号**（`“ ”`），字符画引号会直接引发语法错误。
8. **图片内不含标题**是既定约定：图题由 Word 的图题段承载，新增图件不要调用 `title()`。
9. **`fig_heatmap.png` 等复用图的匿名口径以商业计划书为准**：试点校区 A 对应的是同一份离线缓存所属校区，新图件沿用同一映射，避免同一代号指向两个校区。

### 4. 未来工作

1. 演示视频（3—5 分钟）、答辩 PPT（PDF）、佐证材料（合并为一个 PDF）与完整代码包（网盘链接）尚未准备。
2. 4.3 节的成员分工为按能力域拟定，需各成员确认后定稿。
3. 官方“作品方案模板”（百度网盘）未取到，报告结构以赛题页大纲与规则汇总 PDF 为准；若后续取得模板，请对照检查章节标题措辞。
4. `数据来源与参考文献.md` 中 [R13]（核密度方法参考的文献）出版信息不完整，正式引用前需核对原文。

## 六、移动端专章（2026-10-15 新增）

### 1. 改动范围

**桌面端行为保持不变，移动端能力全部由 CSS 媒体查询 + 少数条件渲染实现。**

```
webapp/frontend/
├── index.html                                  # [改] PWA 清单引用、iOS meta、Service Worker 注册（localhost 不注册）
├── public/manifest.webmanifest                 # [新] PWA 清单（standalone、主题色 #c20a1c、192/512 图标）
├── public/sw.js                                # [新] Service Worker：静态资源缓存兜底，/api/ 不缓存
├── public/icons/pwa-180|192|512.png            # [新] 主屏幕图标（由 ecnu_logo.png 导出）
└── src/
    ├── style.css                               # [改] 移动端全局变量、断点约定、输入字号与触控热区规范
    ├── App.vue                                 # [改] ≤768px 切换"顶部品牌栏 + 底部标签导航 + 更多抽屉"
    ├── composables/useIsMobile.ts              # [新] matchMedia 断点检测（全局单例，各组件复用）
    ├── views/map.vue、topbar.vue、plantcard.vue、plantlibrary.vue   # [改] 地图通栏、筛选换行、卡片下移
    ├── views/chatbot.vue                       # [改] 对话/地图双视图、面板底部抽屉、移动端隐藏重复头部
    ├── views/index.vue、predict.vue、plantmessage.vue、message.vue、wordcloud.vue、classify.vue、placeReview.vue  # [改] 逐页小屏适配
    ├── components/ItineraryPanel.vue、SceneHeatmapPanel.vue          # [改] 小屏改底部抽屉
    └── charts/charts_A|B|C.vue                 # [改] 旭日图自适应；桑基图/平行坐标改为横向滑动
mobile-app/                                     # [新] uni-app 安卓套壳工程（HBuilderX 云打包出 APK）
├── pages/index/index.vue                       # web-view 指向 https://ecnucampusvqa.cn/
├── pages.json、manifest.json、App.vue、main.js  # 应用名、权限（网络/相机/定位）、abiFilters
├── static/logo.png                             # 应用图标（与 PWA 图标同源）
└── README.md                                   # 云打包 APK 步骤 + 打包前检查清单
```

`webapp/frontend/src/components.d.ts` 在构建时会被 unplugin 重写行尾（LF↔CRLF），内容无变化，属正常现象。

**第二轮（2026-10-15，缺陷修复 + 测试能力）的改动与新增：**

```
webapp/frontend/
├── components/PlaceReviewDrawer.vue   # [改] 抽屉宽度随 isMobile 响应式 + 移动端触控热区与输入字号
├── charts/charts_A.vue、charts_C.vue  # [改] 滚动容器无条件生效（修容器窄于画布时图表被裁切），提示断点放宽到 900px
├── views/campusBuild.vue              # [改] 抽屉宽度响应式 + ResizeObserver 触发 map.resize()
├── components/Echarts.vue             # [改] 增加容器 ResizeObserver 自适应（保留 500ms 防抖）
├── components/MessageFeedback.vue     # [改] 移动端触控热区 ≥44px、aria-label 与可见的撤回提示
├── views/message.vue                  # [改] 小屏弹窗宽度（:deep(.el-dialog)）
├── views/predict.vue                  # [改] 弹窗宽度选择器改 :deep（原写法 teleport 后失效）
├── style.css                          # [改] 弹层（drawer/dialog/message-box）内输入字号统一 16px
├── scripts/mobile-dev.mjs             # [新] npm run dev:mobile：真机局域网一键联调
├── scripts/mobile-regression.mjs      # [新] npm run test:mobile：多视口巡检 + 截图 + report.json
├── tests/mobile-snapshots/            # [新] 巡检产物目录（已加入根 .gitignore，不入库）
└── package.json                       # [改] 新增 dev:mobile / test:mobile；devDependencies 新增 playwright-core
docs/移动端测试手册.md                  # [新] 三套测试手段 + 人工验收清单 + 队长线上自检
.gitignore                              # [改] 忽略 webapp/frontend/tests/mobile-snapshots/
```

`playwright-core` 是**仅驱动**的包，安装时不会下载浏览器；脚本通过 `executablePath` 复用本机已安装的 Edge / Chrome。

### 2. 设计思路

1. **桌面优先、移动增补**：所有改动都放在 `@media (max-width: 768px)` 内或由 `isMobile` 条件渲染，桌面端零回归——这是硬约束，改样式时请先确认宽屏效果未变。
2. **断点判断统一走 composable**：`useIsMobile()` 内部是模块级单例 `ref` + 一次 `matchMedia` 监听，避免每个组件各写一套媒体查询（需要 JS 判断时才用，纯样式优先用 CSS）。
3. **移动骨架复制台账导航**：底部标签只放 4 个高频入口（主页/地图/问答/寄语），其余入口收进"更多"底部抽屉，避免小屏挤成 6—7 个标签。
4. **面板不下线、只隐身**：问答页的"对话/地图"用 `visibility: hidden + pointer-events: none` 切换而非 `display: none`，两个面板始终保持整屏尺寸（理由见"已知坑"第 2 条）。
5. **iOS 走 PWA 而非原生**：原生上架需 $99/年开发者账号、证书与审核，套壳应用难过审；PWA 零成本、免审核，体验接近原生，适合本项目。
6. **壳工程不含业务**：`mobile-app/` 只是一个 web-view 容器，业务改动一律改 H5 并部署，壳自动加载最新版本，不需要每次重打包 APK。

### 3. 已知坑（移动端部分，务必先读）

1. **flex 简写会把 `width` 吃掉**：`.outline` 桌面是 `flex: 1`（等价 `flex-basis: 0`），移动端只写 `width: 100%` **不会生效**——同一行里它会被压到 0 宽，表现为"地图区域空白但容器高度正常"。正确写法是 `flex: 0 0 100%`。同类问题排查方法：`getBoundingClientRect().width` 为 0 但 `offsetHeight` 正常。
2. **不要用 `display: none` 隐藏高德地图容器**：容器尺寸归零后地图会渲染成灰块/空白，且 AMap 没有可靠的"重新布局"方法。改用 `visibility: hidden`（容器保持整屏尺寸），或至少要保证初始化那一刻容器有真实尺寸。
3. **`el-drawer` 的 `size="auto"` 不可靠**：Element Plus 2.x 在 `direction="btt"` 下给的是 `height: auto`，实际会塌陷；这里用的是固定 `248px`（App.vue 的"更多"抽屉），改条目数时记得同步。
4. **Service Worker 只在非本地环境注册**：`index.html` 里对 `localhost/127.0.0.1/0.0.0.0` 直接 return。如果哪天去掉这个判断，本地开发会遇到"改了代码刷新不生效"（旧 SW 缓存的 dev 模块）。上线更新缓存内容时，改 `public/sw.js` 顶部的 `CACHE` 版本号即可。
5. **hash 路由下 `page.goto` 不同 hash 不会重载页面**：用 Playwright 验证移动端时，若上一次操作留下了打开状态的抽屉/弹窗，切换路由后它依然在，会拦截后续点击。做法是在 URL 上带一个变化过的查询参数（如 `/?r=map1#/map`）强制整页加载。
6. **`vite preview` 会命中断点缓存**：改完代码重新构建后，旧会话仍可能加载旧资源，验证前请 `playwright-cli close` 再 `open`（或硬刷新）。
7. **移动端字体不要小于 16px**：iOS Safari 对 `<16px` 的输入框聚焦时会强制放大页面；`style.css` 里已对 `.mobile-shell` 内的输入控件统一到 16px，不要再单独调小。
8. **测试数据里的个人信息照旧**：本次未触碰 `data/`、`campus_pois.json`、`emotion_analysis.json` 等公开个人信息；也不要运行 `scripts/sanitize_public_data.py`。
9. **高德密钥仍是占位符**：`index.html` 中 `%VITE_AMAP_KEY%` / `%VITE_AMAP_SECURITY_CODE%` 由 Vite 从 `.env` 注入（本地未配 `.env` 时地图不显示，但布局验证不受影响）；真实密钥只在 `.env`，切勿写入代码或文档。
10. **teleport 弹层的尺寸规则只能写在全局样式里，`scoped` 与 `:deep` 都命中不了**：`el-dialog` 会 teleport 到 `body`，渲染后的链路是 `body > .el-overlay > .el-dialog`，**其中没有任何元素带本组件 scopeId**。2026-10-16 实测（400×851 视口）：`.el-overlay` 的属性是 `class/style`，`.el-dialog` 的属性是 `class/tabindex`，都没有 `data-v-*`；因此组件里写 `.el-dialog { width: 92% }` 或 `:deep(.el-dialog) { … }`（编译为 `[data-v-xxx] .el-dialog`）**都匹配不上**——`views/message.vue`、`views/predict.vue` 当初按 `:deep` 写法的"修正"实际无效，弹窗一直是 Element Plus 默认的 50% 宽。正确做法是写在 `src/style.css` 的全局移动端块（现有 `.el-dialog { width: calc(100vw - 24px) !important; max-width: 560px; }`）。`el-drawer` 不受影响，它的宽度走 `size` prop，不依赖 CSS。
11. **`.mobile-shell` 的全局规则覆盖不到弹层**：`style.css` 里针对 `.mobile-shell` 的移动端规则（输入字号 ≥16px 等）管不到 teleport 出去的 drawer / dialog，因此移动端块里单独补了 `.el-drawer` / `.el-dialog` / `.el-message-box` 的输入字号选择器。新增弹层时注意别漏。
12. **`el-card` 的 `overflow: hidden` 会掩盖横向溢出**：宽图表撑破容器时页面**不会**出现横向滚动条（内容被直接裁掉），只测 `document.scrollWidth` 会漏判。巡检脚本因此额外检查「`.chart-canvas` 宽于容器且容器不可横向滚动」。排查图表类问题时两点都要看。
13. **flex 容器的子项会被内容撑开**：`.box-card` 是 `display: flex`，其子项 `.chart-scroll` 的 `min-width: auto` 会被 900px 画布撑到 900px；给子项设 `overflow-x: auto` 后 `min-width` 才按规范归零、正常收缩。改这几段布局时留意。
14. **巡检脚本必须禁 HTTP 缓存**：`vite preview` 的静态资源带长缓存头，命中旧缓存会得出「改了没生效」的假结论（首个视口尤其容易）。脚本已在 `chromium.launch` 加 `--disable-http-cache` / `--disable-application-cache`；手工用浏览器验证请硬刷新或重开标签页。
15. **页内分支要按文案点，别按索引**：植物图表页的桑基图 / 平行坐标图在 radio 切换之后才渲染，按 `nth(n)` 点击会点到别处而「静默通过」。脚本改为按 `hasText` 定位，并在点击后校验 `.chart-canvas` 是否真的出现。
16. **「寄语」页的植物信息来自新接口 `/api/emotion-plants`**：后端在 `poi_data._EMOTION_PLANTS` 里按寄语 id 提供植物信息（只含寄语涉及的 153 条，不是 2976 条全量）。`server.py` 禁用了 reloader，**改完后端必须重启进程**，否则该接口 404、页面退化成"只有寄语、没有植物"的卡片（前端做了降级，不报错，容易被误判成功能没做）。关联前提是 `data/all_trees.json` 的 `id` 与寄语 `id` 一致——换数据源时要重新核对（现为 153/153 全匹配）。
17. **页面上出现"0 个校区"几乎都是新旧版本混用，不是数据问题**：地点字段后端现名 `place`（早期叫 `campus`），前端 `placeOf()` 两个字段名都读。若仍显示 0，先 `Ctrl+Shift+R` 硬刷新（dev 模块会被浏览器缓存，改代码过程中打开过的标签页尤其容易拿到中间状态），再确认后端进程已重启；两边都对齐时应为"2 个校区"。正确值：中北 140 / 闵行 7 / 无法从文本判定 6。

### 4. 验证方式（可复现）

**首选：一条命令跑完全部页面与图表分支的多视口巡检**（产物缺失时会自动构建，复用本机 Edge / Chrome）：

```powershell
cd webapp/frontend
npm run test:mobile        # 8 种视口 × 全部路由与图表分支，共 88 项；有问题返回非零退出码
```

其余回归：

```powershell
cd webapp/frontend
npm run build                      # 含 vue-tsc 类型检查
npm run test:heatmap; npm run test:assets
```

**需要手工复现单个页面时**（`playwright-cli` 若不在 PATH，用 `npm prefix -g` 找到绝对路径，通常形如 `C:\Users\<你>\AppData\Roaming\npm\playwright-cli.cmd`）：

```powershell
cd webapp/frontend
npx vite preview --port 4173 --strictPort      # 另开一个终端

playwright-cli open --browser=msedge
playwright-cli resize 390 844
playwright-cli goto "http://127.0.0.1:4173/?r=1#/map"    # 带变化的查询参数强制整页加载
playwright-cli eval "document.documentElement.scrollWidth - window.innerWidth"   # 应为 0
playwright-cli screenshot --filename=mobile-map.png
playwright-cli close
```

**真机联调**（相机 / 定位 / 地图手势 / 触摸手感只能在真机上看）：

```powershell
cd webapp/frontend
npm run dev:mobile         # 打印手机可访问的局域网地址与二维码
```

完整用法、常见问题与人工验收清单见 `docs/移动端测试手册.md`。

### 5. 未来工作（移动端）

1. **部署到线上**：本轮与上一轮的改动都只在本地验证过，需重新部署 `https://ecnucampusvqa.cn/`（**部署由队长操作**），部署后按 `docs/移动端测试手册.md` 第四节的「5 分钟自检」确认；PWA 首次访问后由用户手动「添加到主屏幕」。
2. **打安卓 APK**：按 `mobile-app/README.md` 用 HBuilderX 云打包，真机验收相机调起、定位授权、地图手势与系统返回键行为。
3. **真机微调**：底部标签栏高度、iOS 安全区（`env(safe-area-inset-*)` 已留变量）、横竖屏切换后地图与图表的重排。
4. **交互类验收仍需人工**：详情抽屉、生成页抽屉、留言弹窗、问答页「对话 / 地图」切换、反馈按钮触控热区这些依赖后端数据与真实手指操作，自动化覆盖不到，请按手册第三节的清单在真机上勾选。
5. **可选增强**：移动端 POI 详情与行程面板支持上拉/下滑手势（当前为固定底部抽屉）；图表的移动端专用简化配置（现在是横向滑动）。

### 6. 测试能力（第二轮新增）

| 命令 / 文件 | 说明 |
| --- | --- |
| `npm run dev:mobile` | `scripts/mobile-dev.mjs`。探测局域网 IPv4 → 校验 `.env` 的 `VITE_APP_API_HOST` → 拉起后端（已在运行则复用）与前端 `vite --host --port 5173` → 打印手机可访问地址与二维码 → 提示防火墙与高德域名白名单。手机与后端经 Vite 的 `/api` 代理通信，**不需要**直连 5000 端口；用 5173 而非 80 是为绕开 Windows 绑定 80 需管理员权限的问题，且**不改 `vite.config.ts`**，桌面端 `npm run dev`（走 80）不受影响。`Ctrl+C` 会一并收尾子进程。 |
| `npm run test:mobile` | `scripts/mobile-regression.mjs`。8 种视口（320×568、375×667、390×844、414×896、768×1024、800×600、1024×768、1440×900）× 全部路由（自动读 `src/router/index.ts`，新增路由无需改脚本），植物图表页还会依次走到三个 radio 分支。判定两类问题：页面横向溢出、宽图表被裁切且容器不可横向滚动。截图与 `report.json` 写入 `tests/mobile-snapshots/`（已忽略，不入库）。支持 `--skip-build` / `--only <关键字>` / `--headed`；浏览器来源可用环境变量 `MOBILE_TEST_BROWSER` 指定绝对路径覆盖。 |
| `docs/移动端测试手册.md` | 三套手段的用法、排查顺序、人工验收清单、给队长的部署后自检。 |

### 7. 第三轮（2026-10-16）：teleport 弹窗宽度修复

**问题**：手机上点开「寄语」页的留言板，弹窗只有半屏宽（400px 视口实测 200px），内容被压成窄条。

**根因**：第二轮把弹窗宽度写在组件内的 `:deep(.el-dialog)`，而 `el-dialog` teleport 到 `body` 后，`.el-overlay` 与 `.el-dialog` **都不带组件 scopeId**（实测属性分别是 `class/style` 与 `class/tabindex`），`[data-v-xxx] .el-dialog` 永远匹配不上——是一条"看着改了、实际没生效"的规则。它逃过自动化的原因：`npm run test:mobile` 只判定横向溢出与图表裁切，不检查弹窗宽度。

**改法**：弹窗宽度规则移到全局 `webapp/frontend/src/style.css` 的移动端块：

```css
.el-dialog { width: calc(100vw - 24px) !important; max-width: 560px; }
```

`views/message.vue`（留言板）与 `views/predict.vue`（植物预测 / 预测结果两个弹窗，原来还写死 `width="40%"`）里失效的 `:deep(.el-dialog)` 规则删除，原地留注释指向全局。

**验证**：`playwright-cli` 在 400×851 视口打开 `http://localhost:5173/?r=2#/plantmessage`，点第一行表格触发弹窗，实测 `.el-dialog` 宽度由 200 → 376（左右各 12px 边距）。截图存 `tests/mobile-snapshots/dialog-fix-400x851.png`（该目录已忽略，不入库）。

**遗留的测试盲区**：弹窗 / 抽屉宽度这类"teleport 出去的元素"目前不在巡检脚本覆盖范围内，属于人工验收项（见手册第三节）。

### 8. 第四轮（2026-10-16）：寄语列表改造与植物关联

**问题**：「寄语」页原先是 `el-table` 三列（序号 150px / 留言者 / 标语），既看不出"这位寄语者认养的是哪棵植物"，也没有院系、栽种地点、日期等信息，观感单调；而该页可用宽度只有 420px（移动端整屏 400px），多列表格先天不适配。

**数据关联**：`data/all_trees.json` 每条记录同时含植物（`template.name/branch/habit/icon`）与认养人（`owner.name/title`），其 `id` 与寄语 `id` 对应，**153 条寄语全部能匹配到植物**。

**改动**：

| 文件 | 改动 |
| --- | --- |
| `webapp/backend/poi_data.py` | 建树时顺带构建 `_TREE_BY_ID`，并在寄语加载后生成 `_EMOTION_PLANTS`（只含寄语涉及的 153 条 id） |
| `webapp/backend/api/auth_api.py` | 新增 `GET /api/emotion-plants` 与 `GET /api/plant-stats`（校园植物种类数 / 树木总数）。**都没有塞进 `/api/emotions`**：后者被测试与 `build_emotion_context` 当作纯留言数据使用，契约不宜改变 |
| `webapp/backend/test_api_auth.py` | 新增 `EmotionPlantsRouteTests`（facade 透传 + 真实数据结构约束） |
| `webapp/frontend/src/views/message.vue` | 表格重写为卡片列表：植物图标 / 植物名 / 科与习性标签 / 栽种地点 / 点赞数 / 标语 / 寄语者 / 院系标签 / 栽种日期；顶部加搜索与概览统计；详情弹窗补植物信息块。`el-table` 与 `ElTable` 引用已移除 |

**设计取舍**：不用表格而用卡片，是因为本页可用宽度只有 400—420px，多列表格必然挤压；卡片能在同一宽度下同时承载 6—8 项信息，并天然适配移动端单列。不要为了"像表格"再把它改回去。

**实现细节与字段口径**：

1. `icon` 是 OSS 完整 URL，按文件名改用项目已镜像的本地 `/icons/<name>.png`。
2. 树记录里的 `number` 一半是地点描述、一半是纯编号（135/153），纯数字时渲染为"编号 08688"。
3. **`place` 是原始地点分组（细到"某校区某楼东北(某路)"），不是校区。** 首版把它直接计数，页面上出现了"9 个校区"这个错误数字——9 只是它在该数据集里的不同取值数。真实校区只有 2 个：中北 140 / 闵行 7，另有 6 条 `place` 不含校区字样（如"华东师范大学化学馆"）无法判定。前端现用 `campusOf()` 归并后再统计；后端字段名已从 `campus` 改正为 `place`。
4. `likeCount` 是**原始数据快照里的点赞数**（"芳秾"小程序抓取结果），不是本站统计，也不会随本站使用变化；153 条里 89 条为 0，卡片对 0 不显示爱心。
5. 那 7 条闵行寄语的 id 恰好排在数据最前面（列表位置 0—6），所以页面开头几张卡片都显示"闵行校区"，是数据顺序而非统计错误。
6. 概览第三项展示的是**校园植物种类总数**（`/api/plant-stats` 的 `species` = 193），**不是"被认养的种类数"**。后者只有 2 种（水杉 155 / 东京樱花 2），放在页面上像是功能缺陷，不要改回去。最终文案：`共 153 条寄语 · 涉及 2 个校区 · 校园植物 193 种`。

**验证**：`python -m pytest -q` 303 项 + 45 子项通过；`npx vue-tsc --noEmit` 通过；`npm run test:mobile -- --only plantmessage` 88 项通过；`playwright-cli` 在 400×851 与 1440×900 实测 153 张卡片、横向溢出为 0、详情弹窗宽度 376。

## 七、点位留言板与评论审核专章（2026-10-16 新增）

### 1. 改动范围

需求：高德的「评分 + 评价」只对景点开放，本项目要求**校园里每一个点**（建筑 / 地标 / 花草树木，3122 条）都能留言——点开地图打点，弹窗除原有介绍外可下滑看留言，按「1 条长评 / 无长评时 2 条短评」精选；留言可点赞、送爱心、举报。

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `webapp/backend/poi_comments.sql` | 新增 | 5 张表的 DDL（可读版，便于人工执行 / 审阅） |
| `webapp/backend/comments_store.py` | 新增 | 数据访问层（纯 pymysql）：建表自检、读写、表态幂等、举报阈值、折叠 / 恢复 / 删除、封禁、审核台账 |
| `webapp/backend/api/comments_api.py` | 新增 | 评论蓝图（11 个接口） |
| `webapp/backend/api/__init__.py`、`server.py` | 修改 | 注册蓝图并补 facade 透传 |
| `webapp/backend/test_api_comments.py` | 新增 | 55 项 + 35 子项，**整体替换数据层，无 MySQL 也能跑** |
| `webapp/frontend/src/components/commentInfoWindow.ts` | 新增 | 信息窗 DOM 容器 + `createApp` 挂载 Vue 组件 / 关闭时 `unmount` |
| `webapp/frontend/src/components/PoiCommentPanel.vue` | 新增 | 弹窗内留言板（预览 / 全部 / 排序 / 加载更多 / 发布 / 点赞 / 爱心 / 举报 / 折叠占位条） |
| `webapp/frontend/src/components/CommentReportDialog.vue` | 新增 | 举报视图（原因单选 + 补充说明） |
| `webapp/frontend/src/api/comments.ts`、`session.ts`、`relativeTime.ts` | 新增 | 接口封装 + 登录态 + 相对时间（拆开是为了不让 `comments.ts` 反向依赖视图层） |
| `webapp/frontend/src/views/commentReview.vue` | 新增 | 评论审核页（管理员模式） |
| `webapp/frontend/src/views/chatbot.vue`、`views/map.vue` | 修改 | 两个信息窗改接留言板；**导航按钮、行程路线、`plantvalueChange` 联动均未改动** |
| `webapp/frontend/src/router/index.ts`、`style.css`、`views/sidebar.vue` | 修改 | `/comment-review` 路由、移动端样式、侧边栏入口 |

### 2. 数据模型（5 张表，`comments_store.py` 里也有一份等价的内联 DDL）

| 表 | 关键点 |
| --- | --- |
| `poi_comments` | `status ∈ visible/hidden/deleted`；冗余 `like_count/love_count/report_count`；`is_long` 由服务端按 `LONG_COMMENT_MIN_CHARS` 判定；索引 `(poi_id,status,created_at)`、`(poi_id,status,like_count)`、`(status,report_count)` |
| `comment_reactions` | `UNIQUE(comment_id,user_id,reaction)`，点赞 / 爱心各自幂等、互不影响 |
| `comment_reports` | `UNIQUE(comment_id,user_id)`，一人一评一举报 |
| `comment_moderation_log` | 管理动作 + 备注 + 时间，**恢复 / 删除 / 封禁都留痕** |
| `comment_banned_users` | 被封禁作者（`user_id` 主键 + 用户名快照），封禁后其发布接口统一 403 |

表在**首次使用接口时**由 `ensure_schema()` 幂等创建（内联 DDL，含 `CREATE TABLE IF NOT EXISTS`）。**MySQL 不可用时它只记一行日志并跳过**，不阻断模块导入，也不影响不依赖数据库的功能（语义检索 / 热力图等照常）。

### 3. 阈值与规则常量（改行为请只改这里）

```python
REPORT_HIDE_THRESHOLD = 3    # 被多少人举报即自动折叠
LONG_COMMENT_MIN_CHARS = 80  # 长评门槛（决定弹窗取 1 条长评还是 2 条短评）
MAX_COMMENT_CHARS = 500      # 单条上限
DEFAULT_PAGE_SIZE = 20 / MAX_PAGE_SIZE = 50 / MANAGE_PAGE_LIMIT = 200
REPORT_REASONS               # 7 类举报原因，经 GET /api/poi/comments/meta 下发给前端
```

弹窗取用规则：**有长评 → 取热度最高（like+love，同分按 id 倒序）的 1 条长评；无长评 → 取热度最高的 2 条短评。**

### 4. 接口契约

- `GET /api/poi/comments/meta` → `{limits:{longMinChars,maxChars,pageSize,maxPageSize,maxReasonChars,reportHideThreshold}, reactions:['like','love'], reportReasons:[{value,label}]}`
- `GET /api/poi/resolve?lng=&lat=&name=` → `{poi:{id,name,category,...,distanceM,tier}}`，**>300m 返回 `poi: null`**
- `GET /api/poi/comments/preview?poiId=` → `{mode:'long'|'short'|'empty', items:[...], stats:{total,long,short,like,love}, myHidden}`
- `GET /api/poi/comments?poiId=&sort=hot|new&page=&size=` → `{total, page, size, hasMore, items:[...]}`
- 留言对象字段：`id, poiId, poiName, poiCategory, userId, username, content, contentLen, isLong, status, likeCount, loveCount, reportCount, createdAt, createdAtMs, isMine, isLiked, isLoved, isReported`
- `POST /api/poi/comments` `{poiId, content}`；`poiName` / `poiCategory` **由服务端反查点位库**，客户端传什么都不作数
- `DELETE /api/poi/comments/<id>`；`POST /api/poi/comments/<id>/reaction` `{type:'like'|'love', action:'add'|'remove'}`
- `POST /api/poi/comments/<id>/report` `{reasonCode, reasonText?}`；不能举报自己；重复举报返回 `duplicate: true` 软提示
- 管理端（需 `X-Review-Token`）：`GET /api/poi/comments/manage?status=reported|hidden|deleted|all` → `{items, summary:{total,hidden,deleted,reported,bannedUsers}, thresholds}`（items 额外带 `reasonBreakdown`）；`POST /api/poi/comments/<id>/moderate` `{action:'restore'|'delete'|'ban_author', note?}`
- `GET /api/session` → `{loggedIn, username}`（前端判登录态用）

### 5. 管理员模式怎么开

1. 在**仓库根 `.env`** 里配 `USERDATA_REVIEW_TOKEN`（≥16 位，已 `.gitignore`，不入库）；
2. 重启后端，进 `/#/comment-review`，在「需要审核密钥」提示处填入同一个 token，前端存进 `localStorage` 的 `campus.userdata.reviewToken`；
3. **未配置或填错一律 403**（`review_guard.py` 的既有约定：未配置即拒绝，而不是放行）。普通用户在留言板上只看得到「举报」，看不到任何管理入口。

### 6. 已知坑（务必先读）

1. **不要在别处手拼评论 SQL，用 `build_list_query()`。** 这里的 SELECT 列表由多个片段拼接，曾漏一个逗号拼出 `c.created_atrl.user_id`，MySQL 直接报 1054；而当时 55 条单测全绿——因为单测整体替换了数据层、从不真的拼 SQL。现在该函数是**纯函数**，有 6 组断言（占位符数量 == 参数数量、列分隔、长 / 短评谓词等）。新增查询请照此加断言。
2. **`stats.total` 只算公开留言，`myHidden` 是作者自己被折叠的条数。** 弹窗「查看全部」按钮靠 `stats.total + myHidden` 判断是否出现。**不要"顺手"把 `myHidden` 去掉**：去掉后作者自己那条被折叠的留言在信息窗里既看不到、也没有入口进列表。
3. **`poi_id` 必须归一为 `plant_<treeId>`。** 植物地图（`map.vue`）的整数树 id、以及 `campus_pois.json` 的 `id` 都要落到同一个字符串上；问答页（`chatbot.vue`）的点位来自 `/api/chat` 的**坐标**，**必须先过 `/api/poi/resolve` 反查**才有 poiId。`resolvePoiRef` 只在离点位 >1km 时才按名称匹配，避免「多个点位坐标被吸附到一起」时名字打架。
4. **信息窗是 `createApp` 挂载的 Vue 应用，关闭 / 清空标记时必须 `unmount`。** `chatbot.vue` 的 `clearMarkers()` 与新建信息窗前都会销毁旧实例；漏掉会造成「旧弹窗的点赞按钮还能点到、且往老 poiId 上写数据」这类幽灵 bug。同时信息窗内的交互不再走 `setTimeout` + `getElementById`，别再往回改。
5. **弹层样式只能写在全局 `src/style.css`。**（既有教训：`el-dialog` / `el-drawer` teleport 到 `body` 后不带 scopeId。）留言板的移动端样式（44px 热区、`font-size:16px` 防 iOS 聚焦放大）也遵循同一规则。
6. **`@rate_limit` 是按 IP 计数的**，留言板 + 审核页共用同一套限额。写自动化脚本连续注册 / 登录 / 发文容易被 429 挡住，压测前先看 `flask_app.rate_limit`。
7. **异常日志不得带留言原文。** `comments_store` 的失败分支只记动作 + `poi_id`；写新代码时不要把 `content` 拼进日志或异常消息（隐私 + 仓库脱敏要求）。
8. **本地已有一个旧后端占 5000 端口时，新起的进程会与它"共存"**（Windows 允许两个进程都 bind `127.0.0.1:5000`），请求可能落到旧代码上，表现为「刚写的接口 404」。验证新接口前先确认 `netstat -ano | findstr :5000` 只有一个 PID。

### 7. 验证方式（可复现）

```powershell
# 后端（无需 MySQL）
python -m pytest webapp/backend/test_api_comments.py -q     # 55 项 + 35 子项

# 前端
cd webapp/frontend
npx vue-tsc --noEmit
npm run test:mobile          # 8 视口 × 12 页面 = 96 项，含「评论审核」页
```

真实链路（真 MySQL）验证过的关键路径：坐标反查命中 `plant_3023`、远处返回 `null`；游客发布 401（带 `needLogin`）；服务端忽略客户端伪造的 `poiName`；点赞重复提交计数不增；3 人举报后自动折叠、游客列表不可见、作者自己仍可见 `status=hidden`；管理端无 / 错 token 均 403，带 token 可恢复 / 删除 / 封禁，封禁后再发 403。**做这类验证时请用临时假 token**，不要把真实密钥写进命令行、截图或聊天记录。

### 8. 未来工作

1. 单点留言上千后 `LIMIT/OFFSET` 深分页会退化，可改游标分页。
2. 发布侧只做了长度 + 限频，**未接敏感词库 / 大模型预审**；接入点就在 `create_comment` 落库之前。
3. 审核页目前只处理「留言」，**没有"被封禁用户列表"的查看 / 解封入口**。
4. 有富余精力可再补：回复（楼中楼）、"我的留言"页、通知中心。本期明确不做，相关取舍见 `PROJECT_STATUS.md` 第六节第 2 小节。

## 八、审核入口与端切换专章（2026-10-17 新增）

### 1. 改动范围

| 文件 | 改动 |
| --- | --- |
| `webapp/backend/api/review_guard.py` | 新增 `review_configured()`；`review_token_ok()` 改为复用它，语义仍是"未配置即拒绝" |
| `webapp/backend/api/auth_api.py` | 新增 `GET /api/review-status` → `{success, configured}`（`@rate_limit(120)`，只回布尔） |
| `webapp/backend/test_api_comments.py` | 新增 `ReviewStatusRouteTests` 4 项（够长 / 空 / 太短 / 不泄露密钥） |
| `webapp/frontend/src/api/review.ts` | 新增：`fetchReviewStatus()`，异常不静默 |
| `webapp/frontend/src/composables/useReviewAccess.ts` | 新增：`enabled` 三态 + inflight 去重 + 30s 失败重试窗口 |
| `webapp/frontend/src/router/index.ts` | `/comment-review` 加 `meta.requiresReview`，`beforeEach` 确认后不合格回 `/index` 并提示 |
| `webapp/frontend/src/views/sidebar.vue`、`App.vue` | 「评论审核」菜单项 / 移动端「更多」按 `enabled !== false` 过滤 |
| `webapp/frontend/src/views/commentReview.vue` | 页头文案改为"配好密钥才出现、首次需贴密钥"；页面外层补白色卡片底板（对齐 `placeReview.vue` 的 `.review-page`：`rgba(255,255,255,.94)` + 圆角 14px + `box-shadow` + `padding:18px 20px 32px`，≤768px 去圆角、收内边距） |
| `webapp/frontend/src/components/PoiCommentPanel.vue`、`style.css` | 留言板与高德外层整体收小（宽度 / 高度 / 字号 / 内边距） |
| `webapp/frontend/src/views/map.vue`、`plantcard.vue`、`plantlibrary.vue` | 恢复认养人 / 单位 / 寄语（含全文折叠） |
| `webapp/frontend/src/composables/useIsMobile.ts` | 支持 `?frame=mobile|desktop`；新增 `isEmbedded()` |
| `webapp/frontend/src/composables/useDevicePreview.ts` | 新增：机型预设、横竖屏、缩放、`frameSrc`、localStorage 记忆 |
| `webapp/frontend/src/components/ViewModeSwitch.vue`、`DevicePreviewFrame.vue` | 新增：端切换胶囊 + 设备框预览层（桌面端放在侧栏左下角，移动端放在顶部品牌栏右侧） |
| `webapp/frontend/src/views/sidebar.vue` | 包一层 `.sidebar-root`（定高 flex 列 + `position: sticky`），菜单放进 `.sidebar-menu` 独立滚动，底部 `.sidebar-foot` 常驻端切换按钮 |
| `webapp/frontend/src/App.vue` | ≤768px 追加一段：`#building` 改 `scrollbar-gutter: auto` + `scrollbar-width: none`，并隐藏 `#building` / `.mobile-main` 的 `::-webkit-scrollbar`（宽度 0），修掉移动端右侧 15px 白边 |
| `webapp/frontend/src/views/chatbot.vue` | 新增 `inputPlaceholder` computed（移动端短文案 / 桌面原文案）；≤768px 输入行 `gap` 8→6px、附件按钮 `padding` 收窄、`.input-field` 加 `min-width: 0`，让占位文字不再被截断 |
| `webapp/frontend/src/views/map.vue` | `buildPlantHeader` 改为「植物名 + 编号 + 认养寄语」块，寄语排在留言板之前；新增 `truncateText`（slogan 40 字 / content 80 字摘要，`content` 与 `slogan` 相同时不重复） |
| `webapp/frontend/src/style.css` | 新增 `.poi-head` / `.poi-blessing*` 全局样式（innerHTML 插入的节点没有 scopeId）；≤768px 给 `.poi-comment-window` 加 `max-height: min(56vh,420px)` + `overflow-y:auto`，弹窗整体可滑动 |
| `webapp/frontend/src/components/PoiCommentPanel.vue` | ≤768px 取消 `.pc-body` 自身固定高度（`height:auto; overflow:visible`），改由弹窗统一滚动，避免嵌套滚动条 |
| `webapp/frontend/src/views/message.vue` | 搜索框下新增「植物种类」筛选 chip 行（`speciesOptions` 由 `plant.plantName` 现算并降序，`filteredData` 合并种类 + 关键词），≤768px 单行横向滚动。**没有用 `habit` 作维度**：现存 153 条寄语全部对应"乔木"（151 条水杉 + 2 条东京樱花），只有 1 个选项没有区分度 |

### 2. 设计思路

1. **入口显隐的判断放在后端**。密钥只存在于根 `.env`，前端不能读；想用 `VITE_*` 变量把密钥带给前端是**绝对禁止**的（会被打进 bundle）。所以后端只回答"开没开"这一个布尔值，密钥本身永不外泄。
2. **三态而不是布尔**。`useReviewAccess` 里 `enabled` 是 `true / false / null`：`null`（首次加载中、请求失败、旧后端没这个路由）**按"显示"处理**。宁可让人点进去看到 403 提示，也不要在网络抖动时把管理入口藏掉——隐藏造成的"功能不见了"比 403 更难排查。
3. **手机预览必须是 iframe**。全站响应式写在 `@media (max-width: 768px)`（index / map / topbar / chatbot / message / plantcard / PoiCommentPanel 等十余处），媒体查询只看**视口宽度**，给根节点加 class 或改容器宽度都不会生效。iframe 天然提供独立视口，等价于浏览器设备模拟，且零 CSS 冲突。
4. **iframe 里用 `?frame=` 而不是靠宽度自适应**。设备框宽度确实是窄的，但"真机看桌面版"需要反过来的强制桌面布局；一个参数同时解决两个方向，也顺便让 `isEmbedded()` 能阻止切换器自我嵌套。
5. **寄语字段口径**（与「植物寄语」页保持一致）：`owner.slogan` = 一句寄语、`owner.content` = 寄语全文、`owner.name` = 寄语人、`owner.title` = 单位；`key_phrase` 是关键词标签（如"满树灿烂 著名"），**不是**寄语，别混用。

### 3. 已知坑

1. **不要试图用"给根节点加 class"模拟移动端**。改完会得到"骨架换了但页面还是桌面宽度"的假象；只有 iframe / 真窄视口才触发媒体查询。
2. **设备框里是第二个应用实例**。登录态靠同源 Cookie 共享，但框内路由不会实时同步到外层，只在关闭时回写一次（`router.replace`）。
3. **`npm run test:mobile` 会复用 `webapp/frontend/build/` 的旧产物**（存在即跳过构建）。改完前端一定先 `npm run build`，否则巡检的是上一版。
4. **改留言板尺寸要同时看 `style.css`**：`.amap-info-content`、`.poi-comment-window` 是高德自己创建的外层，不带组件 scopeId，只写在组件的 `<style scoped>` 里选不中。
5. **「评论审核」页的白色底板写在根节点 `.comment-review` 上**（与「数据审核」页 `.review-page` 同一套参数）。这两个页面以前只有后者带底板、前者直接铺在页面渐变背景上，视觉不一致；再改这两个页面样式时请成对核对，别只改一个。
6. **地图数据里只有 7 棵树有 `owner`**（`assets/try.json` 291 棵）。看到满屏"暂无认养寄语"是数据现状，不是渲染 bug；全量 `data/all_trees.json`（2976 棵）里有 157 条寄语，但地图当前没接这份数据。
7. **入口显隐依赖 `/api/review-status`**：只改了前端而没重启后端时，若服务是旧进程，会走"未知 → 显示入口"分支，属预期行为。
8. **密钥纪律不变**：`USERDATA_REVIEW_TOKEN` 只在根 `.env`；前端只把它存 localStorage（键名 `campus.userdata.reviewToken`，与「数据审核」页共用）。
9. **端切换按钮不要改回 `position: fixed` 右下角**。早期版本挂在右下角，会压住问答页的输入框与「发送」按钮；侧栏左侧 fixed 又会在 600px 高的窗口里压住「评论审核 / 热力图生成」。现在的做法是：侧栏 `.sidebar-root` 为定高 flex 列（`position: sticky`），`.sidebar-menu` 独立滚动，`.sidebar-foot` 单独占一行——改布局时保持这三层结构即可。
10. **移动端右侧那条 15px 白边是 `scrollbar-gutter: stable` 造成的，不是 padding 不对称**。`#building` 为了让桌面端滚动条出现时不抖动，写了 `scrollbar-gutter: stable`，它会**无条件**在右侧留出滚动条槽；窄视口下就成了白边。已在 `@media (max-width:768px)` 里改回 `auto` 并把 `#building` / `.mobile-main` 的滚动条宽度设 0。**别在窄视口重新打开 gutter 或给滚动条设固定宽度**，会把这条白边带回来。
11. **移动端打点弹窗只允许"一层"滚动**：`.poi-comment-window` 限高并 `overflow-y:auto`，所以 `.pc-body` 在 ≤768px 必须回到 `height:auto; overflow:visible`。两边同时给固定高度 / `overflow:auto` 会出现"框里套滚动条"，手指基本滑不动里面那层。寄语 HTML（`.poi-blessing*`）同样是 innerHTML 节点，样式只能写在 `style.css` 全局段，且 `style.css` 是**纯 CSS 文件，不要写嵌套语法**。
12. **侧栏里别用"满宽 + border/padding"的组件**。`Width: 100%` 在 content-box 下会把内边距与边框加在 100% 之外，`el-aside` 默认 `overflow: auto` 就会在按钮下方冒出一条横向滚动条（曾出现过）。`ViewModeSwitch` 根节点已显式 `box-sizing: border-box`，`App.vue` 也给 `.el-aside` 加了 `overflow-x: hidden` 兜底；新增满宽组件时留意同样的问题。

### 4. 未来工作

1. 真要做多管理员：`users` 表加 `role`，或根 `.env` 加 `ADMIN_USERNAMES=admin,队友A` 并在 `/api/session` 返回 `isAdmin`，再把侧栏显隐从"有没有配密钥"升级为"是不是管理员"（本轮按用户要求明确不做）。
2. 设备框可继续加：暗色模式模拟、网速模拟、一键导出当前预览截图。
3. 地图数据源若换成 `data/all_trees.json`（2976 棵），认养寄语会从 7 条增加到 157 条，体验更完整；需要同时核对 marker 数量与首屏性能。

## 九、热力图生成语义解析与空间精度专章（2026-10-19 新增）

### 1. 改动范围

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 解析 | `tools/campus_generator/discovery.py` | 请求清洗、边界锚定校名、显式类别、主题、半径、POI 优先中心、地理码校验、`discovery` 元数据 |
| 采集 | `tools/campus_generator/harvest.py` | 按 `sourceId` / 同名 30m 去重，按距离升序 |
| 评分 | `tools/campus_generator/quality.py` | 只对 `general` 主题修正恒定 0.2 的相关度 |
| 预览 | `tools/campus_generator/preview.py`、`preview_heatmaps.py` | density 场景、更紧的凸包缓冲与核带宽、按点数加权的密度场 |
| API | `webapp/backend/api/campus_build_api.py` | 预览场景白名单 `PREVIEW_SCENE_IDS`（含 density） |
| 前端 | `webapp/frontend/src/views/campusBuild.vue`、`components/heatmapRender.ts`、`components/SceneHeatmapPanel.vue`、`api/campusBuild.ts` | 解析结果、范围圆、距离、Top5 热区、点位开关、分位拉伸、显式 Bounds |
| 测试 | `test_discovery_parser.py`、`test_generator.py`、`test_preview_heatmaps.py`、`test_spatial_alignment.py`（新）、`test_api_campus_build.py`、`tests/heatmapRender.test.mjs` | 长句/混合类别/半径/POI 中心/投影对齐/density/路由白名单 |

### 2. 设计思路

1. **规则先行、边界锚定**：没有为此引入 LLM。先循环剥离“请你/给我/搜索/生成”等前缀，再以“大学/学院”后缀为锚点，在左侧 16 字窗口内按标点与意图词取最后一个边界之后的子串；`_INSTITUTION_NOISE_RE` 拒绝含“请/帮/搜索/热力图/地点/你/我”的候选。这样“你给我搜索北京大学”→ 北京大学，而“上海交通大学”“华东师范大学”不受影响。
2. **身份与意图分离**：`_clean_request` 仍会删“拍照/约会/食堂”等词用于拼采集关键词；主题与显式类别改从 `_request_intent_text`（保留主题词）读取，并先 `_strip_identity_terms` 去掉校名，避免“湖北大学”的“湖”被当成湖泊。
3. **POI 优先、地理码校验**：`request_poi` 默认只在未注入 `request_json` 时启用（离线单测不发网络请求）。POI 只接受与校名对齐、且不是附属中学/小学/分校的点；地理编码地址必须包含校名，否则退到“只编码校名”，再不行用 POI。校区名无法验证时回退校本部，并写 `discovery.warning`。
4. **密度场景只在预览**：`build_scene_heatmaps.py` 属于正式缓存指纹，**一行都不能改**；density 通过 `preview._write_preview_pois` 给每个非植物 POI 的 `scenes` 追加 `"density"` 实现（`pois_for_scene` 原逻辑即可选中），融合策略放在 `preview_heatmaps._density_fields`，按类别点数加权。`SCENE_IDS`、`SceneHeatmapStore`、`heatmap_cache/` 均未动。
5. **显示拉伸与几何对齐**：`renderHeatmapCanvas` 新增 `stretch:"quantile"`（仅 `PREVIEW_HEATMAP_OPTIONS` 使用）；`ImageLayer` 全部改显式 `AMap.Bounds`；预览后 `map.setBounds` + `preview.places` 标签 + “显示已选点位”开关。

### 3. 已知坑（务必先读）

1. **不要修改正式热图的指纹文件**：`webapp/backend/build_scene_heatmaps.py`、`scene_heatmaps.py`、`heatmap_config.json`、`scene_profiles.json`、`semantic_retrieval.py` 与正式 POI 文件一旦改动，`heatmap_cache/manifest.json` 的 `sourceHash` 与 `test_scene_heatmaps.test_repository_cache_manifest_matches_current_sources` 立刻失败。density 必须留在 `tools/campus_generator/preview*.py` 与预览 workspace（本轮曾误改 `build_scene_heatmaps.py`，已回退）。
2. **`request_poi` 的启用条件**：只有“使用默认 `request_json`”时才自动启用；单测/离线调用若同时注入 `request_json` 又想测 POI，必须显式传 `request_poi=`，否则不会发请求（这是故意的）。
3. **校名边界词**：`_LEFT_BOUNDARY_TOKENS` 含单字“在/到/和/与/及/的”，已对正式校名做过枚举核对；“大学城/大学路/大学生”通过后缀后一个字符 `_INSTITUTION_TAIL_STOP` 排除。新增学校名若包含这些字，先跑 `test_discovery_parser.py`。
4. **旧任务回退**：预览缓存按任务目录隔离，旧任务没有 `{slug}_density.json`；前端 `loadPreview` 在 density 读取失败时会自动切 walk 并提示，不要删掉这个兜底。
5. **质量分不驱动热图**：0.68/0.69 是同一来源、同一半径策略下各分项相同造成的同分；`scene_heatmaps` 与 `preview.py` 根本不读 `quality`。不要为了“让热图更亮”改 `quality.py` 的权重，改显示或密度策略。
6. **热区标签锚点**：`scene_heatmaps.hotspot_places` 的 `anchorRadiusMeters=90` 可能锚到“公共厕所/停车场”等设施；本轮未改，若要做必须重建正式缓存并同步更新所有指纹。

### 4. 未来工作

1. 为未登记学校接入真实校界（CAD/SHP/GeoJSON 或 OSM `amenity=university`），替换“中心 + 1.2km 圆”；OSM 坐标需显式 WGS84→GCJ-02 转换并审计。
2. `request_poi` 支持城市限定（同名大学跨城时目前取高德相关度第一）。
3. 质量分增加逐点证据（校名命中、距离分位、跨来源一致性），避免同源点位同分。
4. 热区标签优先选择有中文语义的地标，过滤“厕所/停车场/出入口”。
5. 若把 density 引入问答页，需扩展 `SCENE_IDS`、`validate_payload` 与缓存 manifest，并重建全部正式缓存。

## 十、候选数据概况图表专章（2026-10-19 新增）

### 1. 改动范围

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 统计 | `webapp/frontend/src/components/candidateSummary.ts`（新） | `summarizeQualityScores` / `summarizeSubCategories` / `summarizeDistances` / `radiusSourceLabel`，纯函数，不依赖 Vue 与 ECharts |
| 展示 | `webapp/frontend/src/components/CandidateDataSummary.vue`（新） | 折叠区 + `el-radio-group` 三选一 + 复用 `Echarts.vue`；空态与降级文案集中在此 |
| 挂载 | `webapp/frontend/src/views/campusBuild.vue` | 候选面板 `review-stats` 之后插一行组件 |
| 类型 | `webapp/frontend/src/api/campusBuild.ts` | `CandidatePoi` 补 `evidence` 声明（页面早已在用 `item.evidence.distanceMeters`） |
| 测试 | `webapp/frontend/tests/candidateSummary.test.mjs`（新）、`package.json` | 10 项边界测试，`npm run test:summary` |

**后端、`heatmap_cache/`、`SCENE_IDS`、热图 `algorithm` 版本号一律未动**，26 张正式缓存与指纹测试不受影响。

### 2. 设计思路

1. **只加"视图"，不加"生成能力"**：用户不能自由指定图表类型，三张图的数据全部来自已有的 `GET /api/campus/build/:id/review` 响应，在前端一次 O(n) 统计后渲染。不新增接口、不发额外请求。
2. **统计口径抽成纯 TS 函数**：`candidateSummary.ts` 不 import Vue 与 ECharts，因此可以用 `node:test` + `typescript.transpileModule` 直跑（沿用 `heatmapRender.test.mjs` 的范式，不引入 Vitest），边界断言不写进 `.vue`。
3. **用 `el-radio-group` 而不是 `el-tabs`**：`el-tab-pane` 用 `v-show` 隐藏，非激活页签的容器宽高为 0，ECharts 会初始化成 100×100（本轮实测踩到）。改成 radio 切换 + `v-if` 后，图表每次都在已布局的容器里挂载。
4. **默认展开、可折叠**：`.candidate-panel` 本身是 `max-height: 680px; overflow: auto`，三图平铺会挤掉候选列表；折叠 + 单图渲染把占位控制在约 260px。
5. **统计包含已拒绝候选**：这是"本次采集长什么样"的概况，把被拒绝的样本排除会让质量虚高；区块标题写明"含已拒绝"。

### 3. 已知坑（务必先读）

1. **`category` 只有两类，不能用来分组**：`preview.py` 只放行 `plant` / `scene`，按 `category` 画饼图只会得到两个扇区。类别图必须按 `subCategory`，并把 `地点` / `待抽取地点` / `网页地点` / 空串并入"未分类"。
2. **缺失分项不能当 0**：`summarizeQualityScores` 只对真实存在的键求均值。旧任务的 `quality.scores` 可能是空对象 `{}`，用 `quality.scores || scores` 这种写法不会回退（空对象是 truthy），必须逐键 `??` 读取。
3. **`evidence.distanceMeters` 只有高德链路有**：本地配置、公开网页来源的点位没有该字段，图里必须显示"距离未知 N 个未计入分布"，不能静默丢弃。
4. **半径参考线只是范围近似**：`getCampusPreview` 的 0~100 是相对强度，不可跨校区/跨场景比较（见 `PHASE2_HEATMAPS.md` 第 58 行）。本轮三张图都不做热度对比、不做排行榜、不做时间趋势。
5. **图表高度必须由父容器给定**：`Echarts.vue` 默认 `height: 100%`，父容器没高度就会塌成 0。`CandidateDataSummary.vue` 用 `useIsMobile()` 切换 200px / 170px。
6. **320px 下候选面板的 `scrollWidth - clientWidth = 6px` 是既有垂直滚动条占位**，不是横向溢出；页面级 `documentElement` 无溢出。写回归脚本时不要用面板级差值判溢出，应比较图表 `rect.right` 与面板 `rect.right`。

### 4. 未来工作

1. `classContributions` 后端已产出（`scene_heatmaps.py`），前端至今 0 引用；若要展示"类别参与构成"，只能算 `count × similarity` 的相对构成，**不能叫"贡献率"**——分类 Min-Max 会消去 beta 正值幅度。
2. 若要把图表扩展到问答页，需要注意 `heatmap` 是单场景 payload、没有候选全集，统计口径要重新定义。
3. 距离分布目前固定 8 箱；候选量很大时可考虑按分位数分箱，避免单箱压扁。
4. 可选的下一步是"采集体检建议"：当某分项均值异常（如 `crossSourceAgreement` 全低）时给出一句可操作提示，而不只是画图。

## 十一、采集召回率与候选分组专章（2026-10-19 新增）

### 1. 为什么会有这一轮

真实任务（北京大学，51 个候选）里，高德地图上可见的“外国语学院 / 历史学系 / 哲学系 / 高等人文研究院 / 3 号楼 / 8 号楼 / 人口研究所 / 北京大学医院”一个都没采到。根因不是评分或去重，而是**查询层就没有召回**：请求只带 `keywords`（12 个主题词），名称不含这些词的点永远不会返回；每词最多 3 页 × 25 条，还在中心区域先被占满。

### 2. 改动范围

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 采集 | `tools/campus_generator/harvest.py` | 抽出 `_candidates_from_pois()`；新增 `AmapGridTypeHarvester`（网格 × `types`）、`CAMPUS_POI_TYPES` 类型码表 |
| 编排 | `tools/campus_generator/discovery.py` | `harvesters()` 追加网格扫描；`CAMPUS_GRID_SCAN=0` 关闭 |
| 评分 | `tools/campus_generator/quality.py` | `_AMAP_CATEGORY_RELEVANCE` + `_PASSIVE_NAME_TERMS` / `_SCENIC_NAME_TERMS` 名称微调 |
| 前端 | `webapp/frontend/src/views/campusBuild.vue` | `candidateGroups` 分组、`groupOpenState` 记住手动开合、组头样式 |
| 测试 | `tools/campus_generator/test_amap_grid.py`（新） | 8 项：铺满、types 请求、跨格去重、失败隔离、缓存、缺 key、类型差异化、配套降权 |

### 3. 设计思路

1. **两种召回并存，不替换**：关键词召回负责“主题明确”的点（图书馆、湖、亭子），网格类型扫描负责“名称不含主题词但确实是校内设施”的点。两者共用 `_candidates_from_pois`，保证字段、证据、范围判定完全一致。
2. **网格而非单点放大半径**：单点 around 的结果按距离排序且有上限，范围越大越集中；切成 5×5 后每格半径约 400m，相邻格留 20% 重叠，没有空洞。
3. **类型码必须包含 `120000 商务住宅`**：否则“3 号楼 / 8 号楼 / 新楼”这类楼栋名永远采不到，这是漏采里占比最大的一类。
4. **单格失败不影响整批**：每格独立 try，失败只写 warning；只有完整翻完页的格才写缓存，避免把残缺结果固化。
5. **得分分层的依据是“类型与场景的关系”**，不是对具体地点的猜测：风景名胜 0.92 → 生活服务 0.38，名称含“学院/研究院/图书馆/湖/亭”加 0.06，含“停车场/快递/便利店/宿舍”减 0.10。

### 4. 已知坑（务必先读）

1. **请求量涨 3–5 倍**：5×5 格 × 每格最多 5 页 ≈ 50–75 次/任务，`AMAP_REQUEST_INTERVAL_SECONDS` 默认 0.25s，采集时间约 1–2 分钟。高德个人 key 的日配额与 QPS 3 要自己核对；先跑一个校区再批量用。
2. **候选量会从几十涨到几百**：前端已按类型分组并默认折叠商业配套，但“全选候选”仍作用于全部条目；一次勾选几百个点后生成临时热图会很慢。
3. **缓存键含 `policy`**：改了 `CAMPUS_POI_TYPES` 或网格数必须同时升 `POLICY_VERSION`，否则旧缓存会掩盖新类型。
4. **质量分不驱动热图**：`themeRelevance` 变细只影响审核排序与数据概况图；热图强度仍由点密度 × 类别场景相似度决定（见 `PHASE2_HEATMAPS.md` 第 58 行）。不要因为“想更亮”去改 `quality.py` 的权重。
5. **正式缓存不受影响**：问答页四类场景热图读的是正式 POI 与 26 张缓存，只有生成页的 `preview_heatmap_cache` 会用上新采的点。
6. **`_dedupe_candidates` 现在会跳过缺坐标的条目**：损坏缓存不再让整批采集崩掉，但也意味着坏数据是静默丢弃的，排查时看 warnings。

### 5. 未来工作

1. 真实链路跑一次（先北京/闵行各一次），记录候选总量、类型分布与高德配额消耗，再决定是否调网格数或类型白名单。
2. 类型白名单目前写死在 `CAMPUS_POI_TYPES`，可按学校类型（医学院/艺术院校）配置化。
3. 候选过百时，“全选候选”应改成按分组勾选；现在只能逐组手点。
4. 若要把“类型权重”也带进预览热图，需要同时改 `_density_fields` 与 `test_preview_heatmaps.py` 的等权断言，并升级 `ALGORITHM_VERSION`——本轮没做，保持“点位密度”的等权语义。

## 十二、用户手动补点专章（2026-10-19 新增）

### 1. 改动范围

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 私有层 | `tools/campus_generator/user_pois.py`（新） | `user_added_pois.json` 的读写、校验、去重、上限（20）、自动并入 selection |
| 审核 | `tools/campus_generator/review.py` | `load_review()` 合并补点（`userAdded` 字段）；`apply_review()` 允许管理员转正并从补点层移除 |
| 预览 | `tools/campus_generator/preview.py` | `build_preview()` 候选池加入补点 |
| API | `webapp/backend/api/campus_build_api.py` | `POST /api/campus/build/<id>/user-pois`、`DELETE /api/campus/build/<id>/user-pois/<poi_id>` |
| 前端 | `webapp/frontend/src/views/campusBuild.vue`、`src/api/campusBuild.ts` | 工具栏「手动补点」、地图落点、弹窗表单、「我的添加」分组与删除 |
| 测试 | `tools/campus_generator/test_user_pois.py`（新）、`webapp/backend/test_api_campus_user_pois.py`（新） | 逻辑 5 项 + 路由 3 项 |

### 2. 数据流

```text
地图点击（补点模式）
→ POST /user-pois（校验坐标/校界/去重/上限）
→ 写 userdata/campus_builds/<job>/user_added_pois.json
→ 自动追加进 preview_selection.json
→ build_preview() 的候选池 = normalized_pois.json + user_added_pois.json
→ 只写入 preview_workspace/ 与 preview_heatmap_cache/，正式资产不变
```

管理员路径（唯一转正入口）：

```text
POST /review  {"id": <补点 id>, "action": "approve"}
→ normalize_candidate 再次校验边界与置信度
→ 写 approved_pois.json，并把该点追加到 normalized_pois.json
→ 从 user_added_pois.json 删除（避免审核视图出现两份）
```

### 3. 设计思路

1. **私有层而非候选池**：`normalized_pois.json` 的语义是"采集结果"，有 `source`、`sourceUrls`、`evidence`。用户补点没有这些证据，写进去会让"待审 N 条"和来源占比失真，也无法整层撤销。
2. **补点自动选中**：`add_user_added()` 内部直接改 `preview_selection.json`，用户点完就能生成热图，不必再回头勾选。
3. **15 米去重**：核密度图会把邻近点叠成更亮的斑，用户会误判"多标几次更热"。重复时错误信息直接给出"请直接勾选已有地点"。
4. **不伪造质量分**：`source="manual"` 命中 `quality._SOURCE_RELIABILITY` 的 0.55，`confidence=0.45`、`verified=false`，证据分照常偏低。这是设计而非缺陷。
5. **移动端友好**：不用右键菜单，改为"按钮进入补点模式 + 下一次地图 click 落点"。

### 4. 已知坑

1. **不要改成写 `normalized_pois.json`**：`save_selection()` 只接受候选清单内的 id；把补点混进候选池虽然能让 selection 逻辑少改一处，但会让"机器采集 vs 人工补录"彻底无法区分，也让管理员无法整批拒绝。
2. **`_campus_job_access` 的会话隔离**：补点端点用与会话绑定的 `ownerId`，别人的任务返回 404（不是 403）。改权限时保持这个语义，否则会把别人的临时任务暴露成可探测资源。
3. **转正后要同时改两个文件**：`approved_pois.json` + `normalized_pois.json`（追加），并把补点从 `user_added_pois.json` 移除；漏掉任何一步都会出现"列表里两份"或"正式通过但列表看不到"。
4. **旧预览必须作废**：三个写入口（补点、删除、selection）都要调 `invalidate_preview()`，否则前端会拿旧网格对照新点位。
5. **15 米阈值是经验值**：校园中心区 POI 密集，若真的需要标注相邻两棵树，调 `MIN_SEPARATION_METERS` 而不是删掉去重。

### 5. 未来工作

1. 补点可以考虑加"上传一张照片作为证据"，把 `evidence` 从纯文本变成可核验附件。
2. 若管理员审核面板要区分来源，`userAdded` 已有标记，可直接在列表加筛选。
3. 端到端跑批："3 个候选 + 2 个补点 → 生成临时热图"尚未在浏览器验收，建议下次联调补上。

## 十三、快速自检命令

```powershell
cd 技术报告/build
python figures_tech.py        # 生成新增技术插图
python build_report.py --pdf  # 生成 docx、导出 PDF、统计页数与体积
python check_report.py        # 交付前自检（结构、编号、插图、匿名与凭据）
cd ..; python 字数核对.py      # 核对作品名称与作品简介字数

cd 商业计划书/build
python build_plan.py --pdf      # 会同时执行匿名化检查，输出页数与插图数
python finance_model.py         # 打印财务测算摘要

cd webapp/frontend
npx vue-tsc --noEmit     # 类型检查
npm run test:heatmap     # 热力图渲染测试（10 项）
npm run test:summary     # 候选数据概况统计测试（10 项）
npm run test:assets      # 前端资源测试（3 项）
npm run test:mobile      # 移动端多视口巡检（96 项 = 8 视口 × 12 页面，复用本机浏览器，不下载）
                         # 注意：它复用 build/ 里的产物（存在即跳过构建），改完代码先 npm run build
npm run build            # 生产构建
```

审核入口开关自检（配了密钥应返回 `"configured": true`，没配为 `false`）：

```powershell
curl http://127.0.0.1:5000/api/review-status
```

端切换自检：桌面浏览器打开任意页面 → 右下角点「手机端」→ 设备框内应出现顶部品牌栏与底部标签栏（`.mobile-tabbar`）；也可直接在窄窗口访问 `?frame=mobile` 强制移动骨架。

评论子系统单独跑（**不需要 MySQL**，数据层在单测里被整体替换）：

```powershell
python -m pytest webapp/backend/test_api_comments.py -q
```

换一台新机器首次部署时，先建库再执行 `webapp/backend/poi_comments.sql`（或直接起后端，首次调用评论接口时会幂等建表）；审核相关接口还需要在根 `.env` 配 `USERDATA_REVIEW_TOKEN`（≥16 位），否则一律 403。

`mobile-app/` 是 uni-app 工程，没有 npm 构建步骤，改用 HBuilderX 云打包（步骤见 `mobile-app/README.md`）。
