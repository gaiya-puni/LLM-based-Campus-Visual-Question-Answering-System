# PROJECT_STATUS

**项目**：基于大模型的校园可视可答系统
**分支 / HEAD**：`test/dev` @ `5dd1ea5`（工作区另有未提交改动；本文档更新于 2026-10-19）
**本次改动主题**：**生成页用户手动补点**——系统漏采少量或大量地点时，用户可以在审核地图上「手动补点」：点工具栏按钮 → 点地图落点 → 填名称/类别/类型/参与场景 → 加入本次热图。补点写入任务私有层 `user_added_pois.json`，自动勾选并参与自己的临时热图，**不写 `normalized_pois.json` / `approved_pois.json`**，因此既不混进采集候选池、也无法进入正式数据；管理员在审核面板显式通过后才转正，并同时从补点层移除。每任务上限 20 个、15 米内拒绝重复。上一轮（采集召回率与候选分组）见第六节
**验证状态**：`python -m pytest -q` **521 通过 + 1 跳过（符号链接权限）+ 100 子项**（新增 `test_user_pois.py` 5 项、`test_api_campus_user_pois.py` 3 项）、`python -m ruff check .` 干净；前端 `npx vue-tsc --noEmit`、`npm run build` 通过（仅既有 chunk 体积告警）。**未做浏览器人工验收**与「补点 + 3 个候选 → 临时热图」端到端跑批，需要登录态与后端环境
**上一轮主题**：**采集召回率（网格 × 类型）+ 候选分组 + 得分差异化**——关键词召回只能命中名称含主题词的点，院系、楼栋、校医院永远采不到；新增 `AmapGridTypeHarvester` 把范围切成 5×5 格、用高德 `types` 分类码（含 **120000 商务住宅**）逐格扫描，跨格去重、单格失败隔离、逐格缓存，`CAMPUS_GRID_SCAN=0` 可关；`quality.py` 按高德大类给 `general` 主题分层相关度（风景名胜 0.92 → 生活服务 0.38）并对停车场/快递等降权，同一来源的点不再同分；生成页候选按 `subCategory` 分组，校园相关类型默认展开、商业配套默认折叠。上一轮（生成页数据概况三图）见第六节
**上一轮验证**：`python -m pytest -q` **512 通过 + 1 跳过（符号链接权限）+ 100 子项**（新增 `test_amap_grid.py` 8 项）、`python -m ruff check .` 干净；前端 `npx vue-tsc --noEmit`、`npm run test:summary`（10 项）、`test:heatmap`（10 项）、`test:assets`（3 项）、`test:mobile`（8 视口 × 12 页 96 项）、`npm run build` 全部通过；Playwright 实测 320/1440 下候选分组顺序、默认展开 33 条 / 折叠后 13 条、无横向溢出。**未做真实高德全量调用**（请求量约为原来的 3–5 倍，约 50–75 次/任务），建议先用一个校区跑一次确认配额与候选规模。上一轮真实高德链路：用户原句 → 学校/校区“北京大学”、中心 `116.310918, 39.992873`、半径 1200m、主题 `general`、关键词含“建筑/地标建筑/树木/植物”；“北京大学海淀校区”不再落到学院路 `116.3658, 39.9649`，回退校本部并提示；2 个关键词采集 27 点、最远 1183m、按距离排序、去重 1 条。浏览器实测（390×844 / 1440×1200）：解析结果完整、范围圆可见、点位密度热图有明确热点、Top5 标签正常、移动端工具栏不溢出

---

## 一、技术栈

| 层次 | 选型 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite；Element Plus、ECharts、高德地图 JavaScript API |
| 后端 | Python 3.10 + Flask，按业务域拆分为多个 Blueprint 模块 |
| 检索 | 中文小模型语义向量（512 维）+ 关键词规则双通道，可离线降级 |
| 大模型 | OpenAI 兼容接口，支持多服务商切换；文本与视觉能力分离配置 |
| 数据 | MySQL（用户与鉴权）+ 版本化 JSON 资产文件（可审阅、可回滚） |
| 质量保障 | pytest 自动化测试、ruff 静态检查、GitHub Actions 持续集成（含凭据脱敏检查） |

## 二、已完成功能

- **校园智能问答**：自然语言提问即得文字答案与地图打点，精确地点优先于模糊推荐。
- **图片问答**：从照片中提取可读文字线索再与点位库匹配，识别不出如实告知。
- **场景热力图**：拍照 / 散步 / 赏花 / 约会 4 类场景 × 季节，共 26 张离线缓存，12 米栅格并配等值带渲染。
- **一日行程规划**：按“上午—中午—下午”编排，接真实步行算路。
- **校园资产生成流水线**：一句需求触发八步流程（档案、采集、校验、评分、审核、预览、发布），七步全自动。
- **热力图生成语义解析与点位密度预览（2026-10-19 第七轮新增）**：长句请求先剥离“请你/给我/搜索/生成”等外壳，校名按“大学/学院后缀 + 左侧边界”抽取；显式类别（建筑、花草树木、地标、亭子、河岸…）直接进入采集关键词；未登记学校用高德 POI 校正中心、默认 1.2km 半径（支持“1.5公里内”覆盖），页面显示中心/来源/半径并在审核地图画范围圆；混合类请求默认「点位密度」预览，Top5 热区带名称、自动框住热区，可勾选“显示已选点位”核对坐标。
- **生成页候选数据概况（2026-10-19 第八轮新增）**：候选面板内折叠式「数据概况」，三张只读图（质量分项均值 / 类别构成 / 距离分布）由 review 响应在前端统计，帮助用户在勾选前判断本次采集是否偏科、是否被搜索半径截断；旧任务只有总置信度时降级为单条，全部无距离时给出明确空态。
- **采集召回率与候选分组（2026-10-19 第九轮新增）**：在关键词召回之外增加「网格 × POI 类型码」扫描（5×5 格、10 个大类、含商务住宅楼栋），补回院系、研究所、楼栋、校医院等名称不含主题词的地点；候选按类型分组，校园相关类型默认展开、商业配套默认折叠；质量分按高德大类与设施性质分层，同一来源的点不再同分。
- **用户手动补点（2026-10-19 第十轮新增）**：生成页点「手动补点」→ 点地图落点 → 填写名称/类别/类型/参与场景即可加入本次热图；补点在候选列表中单独成组「我的添加」、地图用橙色标记、可逐个删除（每任务上限 20 个，15 米内拒绝重复）。补点只进任务私有层并自动勾选进临时热图，**不会写入正式数据**，管理员审核通过后才转正。
- **用户共建与审核**：问答中识别到未收录地点即进入待审清单，审核后归一化入库。
- **植物图鉴与认养文化**：2976 条植物与认养记录、寄语情感分析、认养倾向预测。
- **点位留言板与评论审核（2026-10-16 第三轮新增）**：校园全部 3122 个点都有自己的留言空间；地图打点弹窗内可下滑浏览（精选 1 条长评，无长评时取 2 条短评），登录后可发留言、点赞、送爱心、举报；同一留言被 ≥3 人举报自动折叠，进入「评论审核」页（管理员模式，需 `USERDATA_REVIEW_TOKEN`）可恢复 / 删除 / 封禁作者并留痕。2026-10-17 起该入口**只在后端配好密钥时出现**，未配置则侧栏不显示、直达网址被守卫送回首页。
- **植物认养寄语（2026-10-17 恢复）**：地图点选或植物库「查看」后，右侧信息卡展示认养人、寄语者单位、寄语标语与寄语全文（超 90 字折叠，可展开）；未认养的树显示占位并引导去留言板。
- **桌面端 / 手机端预览切换（2026-10-17 新增）**：右下角常驻胶囊开关；手机端以 iPhone SE / 14-15 / 15 Pro Max、Android 360、Pixel 7 或「自适应窗口」的设备框呈现（iframe 真实窄视口，因此全站 `@media` 响应式真实生效），支持横竖屏、适应窗口 / 1:1 缩放与 Esc 关闭；真机上同一按钮反向提供 1280×800 桌面版预览；关闭时把框内最新路由回写外层。
- **多校区品牌化**：支持多学校多校区，名称与校区标题可配置。
- **移动端（2026-10-15 新增）**：全站响应式布局（≤768px 顶部品牌栏 + 底部标签导航 + "更多"抽屉）、移动端专属交互（问答页"对话/地图"双视图切换、面板改底部抽屉）、PWA（iOS Safari"添加到主屏幕"即全屏运行）、安卓 APK 套壳工程 `mobile-app/`（web-view 承载线上站点，HBuilderX 云打包）。移动端复用同一份前端代码，桌面端不受影响。
- **移动端测试能力（2026-10-15 第二轮新增）**：一条命令拉起前后端做真机局域网联调（`npm run dev:mobile`，打印手机可访问地址与二维码）；一条命令在 8 种视口下巡检全部页面与图表分支，判定页面横向溢出与宽图表被裁切并逐页截图归档（`npm run test:mobile`，复用本机已装 Edge/Chrome，不下载任何浏览器）；配套 `docs/移动端测试手册.md`（含人工验收清单与给队长的线上部署自检）。

数据资产规模：校园点位 3122 条（其中植物 2976 条）、语义索引 3122 × 512 维、场景热图缓存 26 张。系统构成：前端 15 个页面（新增「评论审核」）+ 2 个数据概况模块、后端 11 个评论/会话接口、生成流水线 15 个模块、评论子系统 5 张表。

## 三、运行方式

1. 后端：`cd webapp/backend && python server.py`（Flask + MySQL，默认 5000 端口）。
2. 前端：`cd webapp/frontend && npm run dev`（默认 80 端口）。
3. 配置：根目录 `.env` 选择大模型服务商并填写密钥；需要图片问答时另行配置视觉模型。
4. 手机访问（真机联调）：`cd webapp/frontend && npm run dev:mobile`——一条命令拉起后端与前端 dev，打印手机可访问的局域网地址与二维码，手机连同一 Wi-Fi 即可打开含地图/相机/定位的开发版；用 5173 高位端口，避免 80 端口在 Windows 下的管理员权限问题（不改动 `vite.config.ts`，桌面端 `npm run dev` 不受影响）。
5. 手机访问（线上）：部署后浏览器直接访问站点即可（已全站响应式）；iOS 用 Safari →"添加到主屏幕"获得全屏 App 体验。
6. 安卓 APK：用 HBuilderX 导入 `mobile-app/` 目录 → 发行 → 原生 App-云打包（步骤见 `mobile-app/README.md`，不需要 Mac 与 Apple 开发者账号）。

## 四、测试与质量

```powershell
python -m pytest -q
python -m ruff check .
cd webapp/frontend; npx vue-tsc --noEmit; npm run test:summary; npm run test:heatmap; npm run test:assets; npm run test:mobile; npm run build
python scripts/check_publication.py
```

513 项后端自动化测试（512 通过 + 1 项因环境缺符号链接权限跳过，另有 100 个子项）全部纳入 CI；后端入口已从 4544 行单文件重构为 91 行门面；测试套件不依赖开发者本机配置（评论子系统单测整体替换数据层，**无 MySQL 也能跑**）。前端为 `node:test` 直跑 TS 源码：`test:summary` 10 项（统计口径与降级分支）、`test:heatmap` 10 项、`test:assets` 3 项，另有 `test:mobile` 96 项视口巡检。

## 五、本次进展（2026-10-19 第十轮）：用户手动补点

### 1. 诉求

用户实测北京大学任务时指出：**"当系统漏掉很多或者小部分点的时候，用户应该可以自行添加。"** 当时生成页只有三条路径——勾选系统已发现的候选、改候选的名称/坐标、补充网页 URL 让系统再找一轮，**没有任何创建入口**：`review.py` 的 `apply_review()` 明确拒绝未知 `candidate_id`（`decision references an unknown candidate`），地图也没有 click 加点的事件绑定。

### 2. 改动

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 私有层 | `tools/campus_generator/user_pois.py`（新） | `user_added_pois.json`：字段校验（名称 ≤40 字、类别 plant/scene、场景白名单）、复用 `normalize_candidate` 做校区边界与坐标校验、15 米去重、每任务 20 个上限；写入后自动并入 `preview_selection.json` |
| 审核视图 | `tools/campus_generator/review.py` | `load_review()` 合并补点（`userAdded: true`、默认 `pending`）并新增 `userAdded` 字段；`apply_review()` 允许管理员把补点转正：通过后写 `approved_pois.json`、追加进 `normalized_pois.json`，并从补点层删除 |
| 预览 | `tools/campus_generator/preview.py` | `build_preview()` 的候选池加入补点，勾选后即可参与临时热图 |
| API | `webapp/backend/api/campus_build_api.py` | `POST /api/campus/build/<id>/user-pois`、`DELETE /api/campus/build/<id>/user-pois/<poi_id>`；沿用 `_campus_job_access` 会话隔离（**无需令牌**），写后 `invalidate_preview` |
| 前端 | `webapp/frontend/src/views/campusBuild.vue` | 工具栏「手动补点」→ 点击地图落点 → 弹窗表单；候选列表新增「我的添加」分组（置顶、橙色标记、可删除）；补点后自动作废旧预览 |
| 类型/接口 | `webapp/frontend/src/api/campusBuild.ts` | `CandidatePoi.userAdded`、`UserPoiPayload`、`addCampusUserPoi` / `removeCampusUserPoi`、`ReviewBundle.userAdded` |
| 测试 | `tools/campus_generator/test_user_pois.py`（新）、`webapp/backend/test_api_campus_user_pois.py`（新） | 5 项：私有层隔离+自动选中、近距重复与校外拒绝、上限与必填、审核视图与管理员转正、删除只动私有层；3 项：无令牌增删、非法载荷 400 与未知点位 404、他人任务 404 与未就绪 409 |

### 3. 设计取舍

1. **独立文件，不写 `normalized_pois.json`**：候选池代表"机器采集结果"，带采集时间、来源 URL、证据分；用户补点没有这些证据，混进去会打乱"待审 N 条"的口径与来源统计。独立层还能整层不参与发布。
2. **补点默认自动勾选**：添加完还要再勾一次才能看到效果，等于把成本转回用户；直接并入 selection，重新生成热图即刻生效。
3. **15 米内拒绝重复**：热图是核密度图，两个相隔几米的点会叠成更亮的斑，用户会误以为"标两次更热"。提示文案直接引导去勾选已有地点。
4. **转正仍要管理员**：补点 `source="manual"`、`confidence=0.45`、`verified=false`，质量分由现有打分器如实给出（来源可靠性 0.55），**不伪造**"校园证据/跨源一致性"。普通用户无论怎么点都触不到正式数据。
5. **不用地图右键加点**：移动端没有右键。改为"按钮进入补点模式 + 下一次点击落点"，桌面与手机同一套交互。

### 4. 验证

- 后端：`python -m pytest -q` **521 通过 + 1 跳过（符号链接权限）+ 100 子项**；`python -m ruff check .` 干净。
- 前端：`npx vue-tsc --noEmit`、`npm run build` 通过（仅既有 chunk 体积告警）。
- **未做**：浏览器人工验收（需登录态与后端），以及"补点 + 3 个候选 → 临时热图"的端到端跑批。

### 5. 边界与已知限制

- 补点不参与问答页的 26 张正式热图缓存，只有管理员转正并重建索引/缓存后才生效。
- 补点没有外部证据，"数据概况"里的质量分项会因此偏低；这是如实反映，不是缺陷。
- 20 个上限是为了防止把"临时热图"变成手工画图；需要大规模补录时应走管理员的数据审核页。

## 六、本次进展（2026-10-19 第九轮）：采集召回率与候选分组

### 1. 问题

用户实测北京大学的生成任务：精度可以，但**漏采严重**——高德地图上看得见的“外国语学院 / 历史学系 / 中国语言文学系 / 高等人文研究院 / 经济学院 / 哲学系 / 3 号楼 / 8 号楼 / 新楼 / 人口研究所 / 北京大学医院”一个都没进候选，51 个候选集中在少数几个点。

### 2. 根因

采集只做了**关键词召回**，没有做类型召回：

| 事实 | 位置 |
| --- | --- |
| 请求只有 `keywords`，没有 `types` | `harvest.py` 的 `_collect_keyword` |
| 关键词池只有 12 个主题词（图书馆/湖泊/花园/食堂/体育场/咖啡店/河岸/桥/亭子/体育馆/建筑/广场） | `discovery.THEME_KEYWORDS["general"]` |
| 每词最多 3 页 × 25 条，around 按距离排序 | `AmapPoiHarvester(max_pages=3, page_size=25)` |
| 关键词还被拼成“北京大学 图书馆”（空格连接，高德多词用 `\|`） | `discovery._scoped_keyword` |

名称里不含这 12 个词的地点，**发请求时就永远不会返回**——不是被评分或去重筛掉的。

### 3. 改动

| 文件 | 改动 |
| --- | --- |
| `tools/campus_generator/harvest.py` | 抽出 `_candidates_from_pois()`（关键词召回与网格扫描共用同一套字段/证据/范围判定）；新增 `AmapGridTypeHarvester`：把范围切成 `grid × grid` 格，每格用高德 `types` 分类码扫描（不带 `keywords`），10 个大类含 **120000 商务住宅**（楼栋名靠它才采得到）；跨格按 `sourceId` 去重；单格失败只记 warning 不影响其他格；逐格缓存 |
| `tools/campus_generator/discovery.py` | `harvesters()` 追加网格扫描，`CAMPUS_GRID_SCAN=0` 可关闭 |
| `tools/campus_generator/quality.py` | 新增 `_AMAP_CATEGORY_RELEVANCE`：`general` 主题按高德大类给基础相关度（风景名胜 0.92 → 生活服务 0.38），名称含“学院/研究院/图书馆/湖/亭”等 +0.06，含“停车场/快递/便利店/宿舍”等 −0.10 |
| `webapp/frontend/src/views/campusBuild.vue` | 候选按 `subCategory` 分组；科教文化服务/商务住宅/体育休闲/风景名胜/医疗默认展开，餐饮/购物/生活/交通/政府默认折叠；组头显示数量，可单独开合 |
| `tools/campus_generator/test_amap_grid.py`（新） | 8 项：网格铺满、请求用 types、跨格去重、单格失败隔离、缓存命中、缺 key 报错、类型差异化、配套设施降权 |

### 4. 得分差异化（回应“热图要有区分度”）

- **质量分**：同一来源、同一范围的点不再同分。`themeRelevance` 由高德大类决定，`confidence` 会随类型拉开约 0.1（风景名胜 vs 生活服务）。
- **热图**：强度仍由**点密度 × 类别场景相似度**决定，**不读质量分**（`PHASE2_HEATMAPS.md` 第 58 行的口径不能破）。全量采集后类别数变多、点数分布更不均，预览热图的层次会自然增强；本次没有给核密度加权重，避免把“点位密度”改成加权密度后语义失真。
- 正式问答页的四类场景热图用的是正式 POI 文件与 26 张缓存，**不受本次采集改动影响**，只有生成页的临时预览会用上新采的点。

### 5. 验证

- `python -m pytest -q`：**512 通过 + 1 跳过 + 100 子项**（新增 8 项）；`ruff` 干净。
- 前端：`vue-tsc`、`build`、`test:mobile` 96/96 通过；Playwright 实测 320/1440 下分组顺序为“科教文化服务 → 商务住宅 → 风景名胜 → 生活服务”，默认展开 33 条、折叠后 13 条，无横向溢出。
- 未做真实高德全量调用：请求量约为原来的 3–5 倍（约 50–75 次/任务），建议先用一个校区跑一次，确认配额与候选规模。

## 七、上一轮进展（2026-10-19 第八轮）：生成页候选数据概况图表

### 1. 需求与判断

问题来自一个产品取舍：除了热力图，还有没有必要生成其他图表？

结论是把「生成能力」和「视图」分开：**不做开放式图表生成**（自然语言指定图表类型，没有独立数据支撑），但**同一份采集数据应该有多种固定视图**——热力图回答"哪里热"，缺的是"这批数据凭什么可信"。因此本轮在候选面板内补三张只读图，让用户在勾选点位、生成热图**之前**就能判断数据是否偏科、是否被搜索半径截断。

### 2. 改动

| 文件 | 改动 |
| --- | --- |
| `webapp/frontend/src/components/candidateSummary.ts`（新） | 三个纯函数：`summarizeQualityScores`（7 分项均值，只算存在的键并回传样本数）、`summarizeSubCategories`（`subCategory` 归一 + 前 8 + 其他）、`summarizeDistances`（8 箱直方图 + 未知计数 + 半径）；`radiusSourceLabel` 区分已登记/显式/默认 |
| `webapp/frontend/src/components/CandidateDataSummary.vue`（新） | 折叠区 + `el-radio-group` 三选一 + 复用 `Echarts.vue`；面积约 260px；空态与降级文案集中在此 |
| `webapp/frontend/src/views/campusBuild.vue` | `review-stats` 之后挂载组件，传 `review.candidates` 与 `review.profile?.discovery` |
| `webapp/frontend/src/api/campusBuild.ts` | `CandidatePoi` 补 `evidence` 声明（页面早已在用 `item.evidence.distanceMeters`） |
| `webapp/frontend/tests/candidateSummary.test.mjs`（新）、`package.json` | 10 项边界测试；新增 `npm run test:summary` |

**后端未改动**：不新增接口、不读 `heatmap_cache`、不改 `SCENE_IDS` 与热图 `algorithm` 版本号，26 张正式缓存与指纹测试保持通过。

### 3. 关键取舍

1. **类别必须按 `subCategory` 分组**：`preview.py` 只放行 `plant` / `scene`，按 `category` 只会得到两个扇区。
2. **缺失分项不当 0**：逐键读取，`quality.scores` 为空对象时不挡旧字段；旧任务只剩 `confidence` 时降级为单条「总体置信度」。
3. **距离缺失要显示**：`evidence.distanceMeters` 只有高德链路写入，图注显式给出"距离未知 N 个未计入分布"。
4. **不用 `el-tabs`**：`el-tab-pane` 用 `v-show` 隐藏，非激活页签容器为 0 宽，ECharts 会初始化成 100×100（实测踩到）；改 radio 切换 + `v-if` 后每次都在已布局容器挂载。
5. **统计含已拒绝候选**：这是本次采集的概况，排除被拒样本会让质量虚高，标题写明"含已拒绝"。

### 4. 验证

- `npx vue-tsc --noEmit` 通过；`npm run test:summary` 10 项、`test:heatmap` 10 项、`test:assets` 3 项、`test:mobile` 96 项、`npm run build` 全部通过。
- Playwright 实测（320×568 / 390×844 / 1440×900）：三张图均正常渲染、候选面板无横向溢出；降级分支（仅 `confidence`、无距离）文案正确。
- 已知口径：320px 下 `.candidate-panel` 的 `scrollWidth - clientWidth = 6px` 是垂直滚动条占位（既有行为），页面级无横向溢出。

## 八、上一轮进展（2026-10-19 第七轮）：热力图生成语义解析与空间精度

### 1. 问题与复现

1. **校名污染**：输入“请你给我搜索北京大学的建筑、花草树木、地标等一系列地点并生成热力图”，历史任务 `userdata/campus_builds/15bccac…` 落盘为 `school.name = "你帮我生成北京大学"`、`candidateCount = 0`，采集关键词全是“你帮我生成北京大学 树木”。根因是旧正则从字符串开头一路吃到“大学”。
2. **校区中心错误**：“北京大学海淀校区”被地理编码到 `116.3658, 39.9649`（学院路一带，离北大本部约 5km），任务只采到 1 个点；而“北京大学”中心是 `116.310918, 39.992873`。
3. **范围偏大 / 热图不明显**：旧实现固定 2km 圆，越靠外越容易混入非本校点位；预览又被强制映射到单一场景，类别相似度集中在 0.46–0.56，融合后是一层淡色。**热图管线不读取 `quality`**，0.67 同分不是热图弱的原因。

### 2. 改动

| 文件 | 改动 |
| --- | --- |
| `tools/campus_generator/discovery.py` | 请求前缀循环剥离（你/请/给我/搜索/生成…）；校名改为“大学/学院后缀 + 左侧边界锚定”，拒绝“你帮我搜索北京大学”；显式类别解析（建筑/花草树木/地标/亭子/河岸…）；`infer_theme` 去掉单字“花”触发，混合类归 `general`；`500米/1.5公里/2km内` 半径解析；未登记学校默认 1200m（可用 `CAMPUS_DISCOVERY_RADIUS_M` 覆盖）；新增 `request_poi`（高德 place/text）+ 地理编码校名校验 + 校本部回退；`discovery` 增加 `cleanedQuery / center / centerSource / radiusMeters / radiusSource / warning` |
| `tools/campus_generator/harvest.py` | 候选按高德 `sourceId` 与“同名 30m 内”去重，并按 `distanceMeters` 升序返回 |
| `tools/campus_generator/quality.py` | 仅 `general` 主题的 `themeRelevance` 不再恒定 0.2：有 `sceneFilter/plantFilter` 给 0.9、有场景标签 0.78、植物 0.65、其他 0.55 |
| `tools/campus_generator/preview.py` | 收紧 `supportMeters` 200→120、`maxInfluenceMeters` 450→300；预览 workspace 增加 `scenes.density = "点位密度"` 与语料；非植物候选的 `scenes` 追加 `density` |
| `tools/campus_generator/preview_heatmaps.py` | `scene == "density"` 时按类别点数加权、等权点位密度场；算法版本 `campus-preview-11-…` |
| `webapp/backend/api/campus_build_api.py` | 预览场景白名单改为 `PREVIEW_SCENE_IDS`（含 `density`，仅预览端点；`SCENE_IDS` / 问答缓存不动） |
| `webapp/frontend/src/components/heatmapRender.ts` | 新增 `stretch: "quantile"`（p10→0、p95→1，仅预览）；`PREVIEW_HEATMAP_OPTIONS.blurPx` 18→10 |
| `webapp/frontend/src/views/campusBuild.vue` | 解析结果显示中心/中心来源/半径/清洗后请求/回退提示；审核地图虚线范围圆；候选显示“距中心 x m”；预览自动 `setBounds`；Top5 热区标签；“显示已选点位”开关；图例说明热力与质量分无关；混合请求默认 `点位密度`，旧任务无缓存时回退 walk |
| `webapp/frontend/src/api/campusBuild.ts`、`SceneHeatmapPanel.vue` | 扩展 discovery/preview 类型；`ImageLayer` 改用显式 `AMap.Bounds` |

### 3. 验证

- 后端：`python -m pytest -q` 505 通过 + 1 跳过（符号链接权限）+ 100 子项；`ruff` 干净。
- 新增测试：`test_discovery_parser.py`（长句、混合类别、半径、POI 中心校正、离线不联网）、`test_preview_heatmaps.py`（density 等权/单类别）、`test_generator.py`（general 质量分、预览配置）、`test_spatial_alignment.py`（投影往返、bounds 包含点位、落格误差 ≤ 半格）、`test_api_campus_build.py`（density 路由白名单）。
- 真实高德：用户原句解析正确；海淀校区回退校本部；1.2km 采集 27 点（最远 1183m，已排序去重）。
- 前端：`vue-tsc`、`test:heatmap` 10 项、`build`、`test:mobile` 96 项；Playwright 实测桌面/移动端解析结果、范围圆、点位密度热图与 Top5 标签。

### 4. 已知限制

1. 未登记学校仍用“中心 + 圆形半径”近似校园范围，不是正式校界；已登记校区继续使用原有 `trustRadiusM/boundary`。
2. `density` 只存在于生成页预览；问答页四类场景、`heatmap_cache` 与 `SCENE_IDS` 未改，正式热图指纹测试保持通过。
3. 质量分仍集中在 0.68/0.69（同一来源、同一半径策略各分项相同），本轮只修了 `general` 的恒定 0.2；热力强度不由此决定。
4. 热区标签的锚点可能选到“公共厕所/停车场”等设施（`scene_heatmaps.hotspot_places` 既有策略），本轮未改，避免影响正式缓存指纹。

## 九、上一轮进展（2026-10-17 第四轮）：审核入口显隐、留言板收紧、植物寄语回归与端切换

### 1. 需求

1. **「评论审核」页只应被"管理员"看到**；把页面分享给队友后，队友也要能看到同一页。
2. 地图打点弹窗里的**留言板再小一些**。
3. 点开植物时的**植物寄语与寄语人**此前有、现在没了，需要**恢复**。
4. 留言**数据是否会长久保存**。
5. 全站要有**「桌面端 / 手机端」切换按钮**，方便自测与演示。

### 2. 产品取舍（用户已确认）

| 决策 | 结论 |
| --- | --- |
| 管理员判定 | **不引入管理员角色**、不改 `users` 表；沿用 `USERDATA_REVIEW_TOKEN` 单因子，队友由管理员口头告知密钥 |
| 评论审核入口 | **只有后端配好密钥（≥16 位）时**才在侧栏 / 移动端「更多」里出现；未配置时直接输网址会被路由守卫送回首页并提示配置方法 |
| 热力图生成 | 保持**所有人可见可用**，不加任何门禁 |
| 数据审核页 | 维持现状（入口常显 + 密钥门禁） |
| 端切换 | **设备框 + iframe 内嵌**（真实窄视口才能命中 `@media (max-width:768px)`），真机反向提供「桌面版预览」 |
| 数据备份 | **不新增备份脚本**，只在文档说明持久化现状 |

### 3. 实现要点

1. **后端「是否开着审核」只有一个口径**：`api/review_guard.py` 抽出 `review_configured()`（`USERDATA_REVIEW_TOKEN` 去空白后 ≥ 16 位），`review_token_ok()` 复用它；新增公开只读接口 `GET /api/review-status` → `{success, configured}`（`api/auth_api.py`），**只回布尔、不回密钥**。真正的门禁仍是请求头 `X-Review-Token`，前端隐藏入口只是体验层。
2. **前端三态显隐**：`api/review.ts` + `composables/useReviewAccess.ts` 做进程内缓存 + inflight 去重，`enabled` 为 `true / false / null`；**`null`（未知、旧后端、探测失败）按"显示"处理**，避免一次网络抖动把管理入口藏掉。`views/sidebar.vue` 与 `App.vue` 的移动端「更多」抽屉按 `enabled !== false` 过滤，`router/index.ts` 给 `/comment-review` 加 `meta.requiresReview` 并在 `beforeEach` 里确认一次，确认为未配置则 `next('/index')` + 提示。
3. **留言板整体收小一档**（`components/PoiCommentPanel.vue` + `style.css` 里的高德外层）：容器 320 → **276px**、列表可视高 216 → **160px**、头像 26 → 22px、正文 12.5 → 12px、按钮 11 → 10.5px、输入框与内边距同步收紧；移动端列表高 34vh → 30vh（最小 140px），触控热区仍保留 44px。`commentInfoWindow.ts` 的"单弹窗 + 每次 open 重建 Vue 应用"生命周期与 `offsetY = -34` 未动。
4. **植物寄语回归**（`views/map.vue` / `views/plantcard.vue` / `views/plantlibrary.vue`）：marker props 增补 `ownerName / ownerTitle / slogan / content`，右侧「植物信息」卡新增信笺式「认养与寄语」区块（寄语标语 + "—— 姓名（单位）" + 全文默认折 4 行、超 90 字给「展开全文」）；未认养显示虚线占位并引导去留言板。植物库「查看」与地图点选共用同一份字段。
5. **端切换**（`composables/useIsMobile.ts` / `useDevicePreview.ts` / `components/ViewModeSwitch.vue` / `DevicePreviewFrame.vue`）：桌面端胶囊位于**侧栏左下角**（`sidebar.vue` 改为整列定高 flex + 菜单独立滚动，按钮单独占一行并居中，实测 **147×30px**，既不遮菜单项、也不会挡住问答页右下角的输入框与发送按钮；根节点用 `box-sizing: border-box` 避免侧栏出现多余横向滚动条），移动端位于**顶部品牌栏右侧**（只放"桌面版"单按钮，避免压住底部输入区与标签栏）；切手机端弹出设备框，机型覆盖 iPhone SE 375×667、iPhone 14/15 390×844、iPhone 15 Pro Max 430×932、Android 360×800、Pixel 7 412×915 与「自适应窗口」，支持横竖屏、适应窗口 / 1:1、Esc 关闭；iframe 通过 `?frame=mobile|desktop` 强制端型，窗口缩放时按 `min(1, 可用宽/框宽, 可用高/(框高+状态栏))` 等比缩放；关闭时把框内最新路由回写外层。真机（窄视口）上同一按钮反向提供 1280×800 的「桌面版预览」。

### 4. 数据会不会长久保存

会。留言落在 MySQL 的 `poi_comments` / `comment_reactions` / `comment_reports` / `comment_moderation_log` / `comment_banned_users` 五张表（首次访问幂等建表，`MYSQL_*` 从根 `.env` 读，线程本地连接）。**重启服务不丢**；"删除 / 折叠"都是**软删除**（`status` 改值），审核动作另记 `comment_moderation_log` 可追溯；举报达 3 人只改可见性、不删数据。只要本机 MySQL 库不删、不换机器，留言就一直保留；正式版把这几张表 mysqldump 到服务器即可（本期不写脚本）。

### 5. 验证

- 后端：`python -m pytest webapp/backend/test_api_comments.py -q` 通过（新增 4 项 `/api/review-status` 用例，含"不泄露密钥"断言）。
- 前端：`npx vue-tsc --noEmit` 通过；`npm run test:mobile` 巡检 8 种视口（含 320 宽）无横向溢出。
- 人工：桌面端点「手机端」→ 390×844 框内为移动骨架（底部标签栏、顶部品牌栏），留言板不裁切；植物卡在 7 棵有认养记录的树上显示寄语、其余显示占位。

### 6. 已知限制

1. `try.json`（地图 291 棵）里只有 **7 棵**带 `owner`，因此多数树只能显示"暂无认养寄语"占位（全量 `data/all_trees.json` 2976 棵中有 157 棵带寄语，本轮未扩数据源）。
2. 入口显隐依赖新增的 `/api/review-status`；旧后端没有该路由时前端按"未知"处理（入口照常显示，点进去仍是 403 提示），**不会把页面变成死链**。
3. 设备框是 iframe，里面是**独立的第二个应用实例**：登录态因同源 Cookie 共享而保留，但页面内跳转不会自动同步到外层（关闭时统一回写一次）。

## 十、上一轮进展（2026-10-16 第三轮）：校园点位留言板与评论审核

### 1. 需求

高德的「评分 + 评价」只对景点开放。本项目要求**校园里每一个点**（建筑 / 地标 / 花草树木，共 3122 条）都能留言：点开地图打点，弹窗除原有介绍外可下滑看留言，并按「1 条长评 / 无长评时 2 条短评」精选；留言支持点赞与爱心两种独立表态，并可举报。

### 2. 产品取舍（用户已确认）

| 决策 | 结论 |
| --- | --- |
| 评分形态 | **不做星级评分**，只保留文字留言 + 点赞 + 爱心；点赞与爱心互相独立、可同时存在、可再点取消 |
| 鉴权 | 全部 3122 个点开放；**必须登录**才能发布 / 表态 / 举报，未登录仍可读，弹窗内给「去登录」引导 |
| 举报机制 | 阈值自动折叠 + 管理员模式人工复核；同一留言被 **≥3 人**举报即折叠，折叠只改可见性、不删数据 |
| 管理入口 | 举报按钮对所有登录用户可见，**复核与处置只在管理员模式下出现**（复用 `X-Review-Token`） |
| 本期不做 | 楼中楼回复 / @、收藏转发、编辑已发留言、通知中心、「我的留言」页、拉黑用户、敏感词词库后台 |

### 3. 关键判定规则（`comments_store.py` 模块常量）

```python
REPORT_HIDE_THRESHOLD = 3   # 同一留言被举报达此人数即自动折叠
LONG_COMMENT_MIN_CHARS = 80 # 达此字数视为长评
MAX_COMMENT_CHARS = 500     # 单条留言字数上限
PAGE_SIZE = 20 / MAX_PAGE_SIZE = 50
```

弹窗预览取用规则：**有长评就取热度最高（点赞 + 爱心，同分按 id 倒序）的 1 条长评；没有长评才退回取 2 条短评。** 热度排序 `(like_count + love_count) DESC, id DESC`。

### 4. 后端：5 张表 + 11 个接口

新增 `webapp/backend/poi_comments.sql`（`CREATE TABLE IF NOT EXISTS`，对齐 `users.sql` 的手写风格）与 `comments_store.py`（数据访问层，另带一份内联 DDL 供首启自检；`auth_db` 缺失/连不上时自动跳过，不影响既有功能）：

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `poi_comments` | 留言主表（含 `poi_id` / 作者 / 正文 / `is_long` / 冗余计数 / `status`） | `status ∈ visible / hidden / deleted`；`(poi_id, status, created_at)`、`(poi_id, status, like_count)` 索引 |
| `comment_reactions` | 点赞与爱心 | `UNIQUE(comment_id, user_id, reaction)` 保证幂等 |
| `comment_reports` | 举报 | `UNIQUE(comment_id, user_id)` 保证一人一评一举报 |
| `comment_moderation_log` | 管理操作留痕（动作 + 备注 + 时间） | — |
| `comment_banned_users` | 被封禁作者（封禁后禁止再发） | **比计划多建了这张表**：封禁作者必须落库，否则重启即失效 |

新增蓝图 `api/comments_api.py`（结构照 `api/userdata_api.py`，10 个路由），另在 `api/auth_api.py` 补了 `GET /api/session`（前端判登录态，此前只能靠 `/api/health` 猜），共 11 个新接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/poi/comments/meta` | 字数上限、阈值、举报原因枚举（前端不写死文案） |
| `GET /api/poi/resolve?lng&lat&name` | 坐标反查点位（**问答页的点来自 `/api/chat` 的坐标而非 poi_id，必须靠它归一**），>300m 返回 `null` |
| `GET /api/poi/comments/preview?poiId` | 弹窗预览 + 统计 + `myHidden` |
| `GET /api/poi/comments?poiId&sort&page&size` | 列表（热度 / 最新、分页、带当前用户表态） |
| `POST /api/poi/comments` | 发布（登录、限频、长度校验；`poiName`/`poiCategory` 由服务端从点位库反查，**不信客户端**） |
| `DELETE /api/poi/comments/<id>` | 作者或管理员删除（软删） |
| `POST /api/poi/comments/<id>/reaction` | `{type: like\|love, action: add\|remove}`，幂等 |
| `POST /api/poi/comments/<id>/report` | 举报；达阈值同事务置 `hidden` 并写台账；不能举报自己 |
| `GET /api/poi/comments/manage?status=` | 管理端列表（`reported/hidden/deleted/all`，带原因分布） |
| `POST /api/poi/comments/<id>/moderate` | `restore / delete / ban_author` + 备注 |
| `GET /api/session` | 前端判登录态（此前只能靠 `/api/health` 猜） |

设计取舍：**用 MySQL 表而不是沿用 `userdata_store` 的 JSONL 事件流。** 留言存在强关系（留言↔用户↔表态↔举报）与状态流转，需要事务 + 唯一约束保证幂等（点赞不重复、一人一评一举报），JSONL 适合 append-only 审计但不适合高频读 + 聚合。

### 5. 前端：弹窗内评论区（核心改动）

| 文件 | 改动 |
| --- | --- |
| `src/components/commentInfoWindow.ts` | **[新增]** 把「信息窗里塞字符串 HTML」换成一个 DOM 容器 + `createApp` 挂载 Vue 组件、关闭时 `unmount` 的小封装。原方案（`chatbot.vue` 拼 HTML + `setTimeout` 绑事件）**无法承载有状态交互**（滚动、分页、点赞、发布） |
| `src/components/PoiCommentPanel.vue` | **[新增]** 弹窗内留言板：预览 / 查看全部 / 最新·最热切换 / 加载更多 / 发布框（字数计数）/ 点赞·爱心（乐观更新 + 失败回滚）/ 举报 / 折叠占位条 / 未登录引导 |
| `src/components/CommentReportDialog.vue` | **[新增]** 举报视图：原因单选 + 补充说明 + 提交后置「已举报」 |
| `src/views/commentReview.vue` | **[新增]** 评论审核页（管理员模式），参考 `placeReview.vue`；未配 token 时只显示「需要审核密钥」 |
| `src/views/chatbot.vue` | 改造 `buildInfoContent`：信息区改 DOM + Vue，**步行导航按钮与行程路线绘制逻辑未动** |
| `src/views/map.vue` | 植物地图信息窗同样接入；点位 id 归一为 `plant_<treeId>`，**与 `plantcard.vue` 的 `plantvalueChange` 联动保持不变** |
| `src/api/comments.ts` / `session.ts` / `relativeTime.ts` | **[新增]** 接口封装；拆出独立的 session 与相对时间模块，避免 `comments.ts` 反向依赖视图层 |
| `src/router/index.ts` / `src/style.css` / `src/views/sidebar.vue` | 新增 `/comment-review` 路由与侧边栏入口；移动端样式按既有教训写在全局 `style.css` |

`resolvePoiRef` 只在坐标离点位较远（>1km）时按名称匹配，避免「几个点位的坐标被后端吸附成同一个」时名字打架。

### 6. 实测抓出来的两个真问题（都只有真跑才会暴露）

1. **SELECT 列表少一个逗号 → MySQL 1054。** 拼接 `c.created_at` 与 `rl.user_id IS NOT NULL` 时漏了分隔符，变成 `c.created_atrl.user_id`。**此前 55 条单测全绿**，因为它们整体替换了数据层、从不真的拼 SQL。修法是把 SQL 构造抽成纯函数 `build_list_query()`，补 6 组断言（占位符数量 == 参数数量、列分隔、长/短评谓词等），**无数据库也能拦住同类错误**。
2. **作者自己「被折叠」的留言在弹窗里根本进不去。** 预览与头部统计都只算公开留言，于是 `stats.total == 预览条数`，**「查看全部」按钮永远不出现**，作者无从展开自己的折叠留言。修法是预览接口新增 `myHidden`（当前用户自己被折叠的条数），前端用它参与「查看全部」的出现判断。

### 7. 验证

- 后端：`python -m pytest -q` **485 项通过 + 1 项跳过（共 486，另有 100 个子项）**，其中 `test_api_comments.py` 55 项 + 35 子项（鉴权 401、长度校验、表态幂等与计数、重复举报、阈值折叠、折叠对普通用户不可见、管理端门禁 403、恢复 / 删除 / 封禁）；`python -m ruff check .` 干净。
- 真实链路（真 MySQL + 假审核密钥，直连后端）：`/api/poi/resolve` 精确命中 `plant_3023`（距离 0.0m）、远处返回 `null`；游客发留言 401 且带 `needLogin`；长评落 `isLong=1`、`poiName` 由服务端反查（伪造名被忽略）；点赞重复提交计数不增；3 人举报后 `hidden`、游客列表看不到、作者自己仍能看到 `status=hidden`；管理端无 token / 错 token 均 403，带 token 可恢复 / 删除 / 封禁，封禁后再发被 403。
- 前端：`npx vue-tsc --noEmit` 通过；`npm run test:heatmap` 9 项、`npm run test:assets` 3 项、`npm run test:mobile` **8 种视口 × 12 个页面 96 项全通过**（含新增评论审核页，`readRoutes()` 自动纳入）。
- `playwright-cli` 实测（桌面 1440×900 / 移动 400×851）：信息窗内留言板 320×292、横向溢出 0；发布长评后弹窗预览切到长评并向「查看全部 2 条」引导；点赞与爱心各自独立开合且计数正确；举报视图 7 个原因、未选原因时提交置灰、提交后按钮变「已举报（禁用）」；折叠占位条点击可展开并显示「已折叠（3 人举报）」；问答页由坐标反查 `plant_3023` 成功挂载留言板且「步行导航到这里」按钮保留；评论审核页门禁态与（拦截注入样例数据后的）列表、统计、原因分布、筛选、处置弹窗全部正常。截图留档于 `webapp/frontend/tests/mobile-snapshots/`（已忽略、不入库）。

### 8. 遗留问题

1. 单点留言量上千后 `LIMIT/OFFSET` 深分页会退化；当前量级（百条内）无影响，未来可改游标分页。
2. 发布侧只做了长度与限频，**未接敏感词库或大模型预审**；接口位置已留出，需要时可在 `create_comment` 前加一道。
3. 评论审核页目前只处理「留言」，**没有"按用户封禁列表"的查看入口**（`comment_banned_users` 只能解封后从库里看）。
4. 植物地图那 2976 个点位的 `plant_<treeId>` 与 `campus_pois.json` 的 id 靠 `treeId` 对齐；若将来换数据源，需重跑对齐校验。

## 十一、同日上一轮（2026-10-16）：teleport 弹窗宽度修复 + 寄语列表改造

本节含同一天的两轮改动：先修弹窗宽度（第 1—6 小节），再做寄语列表改造（第 7 小节）。

### 1. 问题

手机上点开「寄语」页的留言板，弹窗只有半屏宽、内容被压成窄条。第二轮曾把弹窗宽度写成组件内的 `:deep(.el-dialog)` 并记为"已修复"，实际并未生效。

### 2. 根因（实测确认）

`el-dialog` 会 teleport 到 `body`，渲染后的链路是 `body > .el-overlay > .el-dialog`，**这些元素一个都不带组件 scopeId**。在 400×851 视口下实测：

```
.el-overlay 的属性: ["class","style"]        <- 无 data-v-*
.el-dialog  的属性: ["class","tabindex"]     <- 无 data-v-*
.el-dialog  实测宽度: 200px                  <- 即 Element Plus 默认的 50%
```

因此组件里的 `.el-dialog { … }` 与 `:deep(.el-dialog) { … }`（编译为 `[data-v-xxx] .el-dialog`）**都匹配不上**。它之所以逃过自动化，是因为 `npm run test:mobile` 只判定「页面横向溢出」与「宽图表被裁切」，不检查弹窗宽度。

### 3. 改法

| 文件 | 改动 |
| --- | --- |
| `src/style.css` | 在全局移动端块新增 `.el-dialog { width: calc(100vw - 24px) !important; max-width: 560px; }`——弹窗尺寸规则只能写在全局样式里 |
| `views/message.vue` | 删除失效的 `:deep(.el-dialog)` 宽度规则，原地注明去向 |
| `views/predict.vue` | 同上（该页原有两个弹窗，其中一个还写死 `width="40%"`，现由全局规则接管） |

### 4. 验证

`playwright-cli`（本机 Edge）在 400×851 视口打开 `http://localhost:5173/?r=2#/plantmessage`，点第一行表格行触发弹窗后实测：`.el-dialog` 宽度 **200 → 376**、`left/right = 12/388`（左右各 12px 边距），截图 `tests/mobile-snapshots/dialog-fix-400x851.png`。桌面端不受影响（全局规则包在 `@media (max-width: 768px)` 内）。

### 5. 顺带修正的文档错误结论

`HANDOVER.md` 已知坑第 10 条与 `docs/移动端测试手册.md` 第五节第 4 条原先写"必须使用 `:deep(.el-dialog)` 才能命中"，这是错的，已改为"teleport 弹层的尺寸规则只能写在全局样式里"。

### 6. 遗留的测试盲区

弹窗 / 抽屉这类 teleport 出去的元素，其宽度目前不在巡检脚本覆盖范围内，属人工验收项（手册第三节清单）。

### 7. 寄语列表改造与植物关联（同日第二轮）

#### 问题

「寄语」页原先是 `el-table` 三列（序号 / 留言者 / 标语）：序号列固定 150px 白占宽度，横向只剩两列却仍被挤得换行；**最关键的是看不出"这位寄语者认养的是哪棵植物"**，也没有院系、栽种地点、日期等信息，观感单调。且该页右侧栏只有 420px 宽（移动端整屏 400px），多列表格在这个宽度下先天不适配。

#### 数据关联

`data/all_trees.json`（2976 条）里每条记录同时含植物信息（`template.name/branch/habit/icon`）与认养人（`owner.name/title`），其 `id` 与寄语数据的 `id` 一一对应——**153 条寄语的 id 全部能匹配到植物**。

#### 改动

| 文件 | 改动 |
| --- | --- |
| `webapp/backend/poi_data.py` | 建树时顺带构建 `_TREE_BY_ID`（id → 精简植物信息），并在寄语加载后生成 `_EMOTION_PLANTS`，**只保留寄语涉及的那些 id**（153 条），不把 2976 条全量数据推给前端 |
| `webapp/backend/api/auth_api.py` | 新增 `GET /api/emotion-plants`（`{寄语 id: 植物信息}`）与 `GET /api/plant-stats`（`{species: 193, trees: 2976}`）。**都单独成接口而不塞进 `/api/emotions`**：后者被测试与 `build_emotion_context` 当作纯留言数据使用，返回值契约不宜改变 |
| `webapp/backend/test_api_auth.py` | 新增 `EmotionPlantsRouteTests`：facade 透传 + 真实数据下的结构约束（键为寄语 id 字符串、每条都有植物名） |
| `webapp/frontend/src/views/message.vue` | 三列表格整体重写为**卡片列表**：植物图标 + 植物名 + 科/习性标签 + 栽种地点 + 点赞数 + 标语 + 寄语者 + 院系标签 + 栽种日期；顶部加关键词搜索（寄语者/标语/植物名/地点）与概览统计；点击卡片打开详情弹窗（补上植物信息块） |

设计取舍：**不用传统表格**。理由是该页可用宽度只有 400—420px，多列表格必然挤压；卡片能在同一宽度下同时承载 6—8 项信息，且天然适配移动端单列。`el-table` 及 `ElTable` 引用已移除。

细节：`icon` 字段是 OSS 完整 URL，按文件名改用项目已镜像的本地 `/icons/<name>.png`；树叶的 `number` 字段一半是地点描述、一半是编号（135/153 为纯编号），纯数字时显示为"编号 08688"。

#### 字段与统计口径（本轮踩过的坑，务必看）

| 字段 | 真实含义 | 坑 |
| --- | --- | --- |
| `location`（取自树的 `number`） | 树自身的位置描述或编号 | 135/153 是纯编号，需渲染成"编号 08688"，否则看起来像乱码 |
| `place`（取自树的 `place`） | **原始地点分组**，粒度细到"华东师大中山北路校区数学馆东北(友谊路)" | 首版把它当"校区"直接计数，页面上出现了"9 个校区"——9 其实只是 `place` 的不同取值数，**真实校区只有 2 个**（中北 140 / 闵行 7 / 无法从文本判定 6）。现已在前端用 `campusOf()` 归并后再统计，后端字段名也从 `campus` 改正为 `place` |
| `likeCount` | **原始数据快照里的点赞数**（来自"芳秾"小程序抓取结果），不是本站统计、也不会随本站使用变化 | 153 条里 89 条为 0，卡片对 0 不显示爱心 |

另：那 7 条闵行寄语的 id 恰好排在数据最前面（列表位置 0—6），所以页面开头几张卡片都显示"闵行校区"，属数据顺序而非统计错误。

**概览口径调整**：第三项一度写作"2 种认养树木"——数字本身没错（有认养人的 157 棵树里 155 棵水杉、2 棵东京樱花），但放在页面上像是"系统只认得出 2 种植物"，观感不佳。现改为展示**校园植物种类总数**（取自 `/api/plant-stats` 的 `species`，即植物模板库 `data/all_templates.json` 的种类数 193，与 `data/all_trees.json` 的 2976 棵树去重后一致）。最终文案：`共 153 条寄语 · 涉及 2 个校区 · 校园植物 193 种`。

前端 `placeOf()` 同时读 `place` 与旧字段名 `campus`，避免"前端已更新、后端未重启"时统计静默变成 0。**若看到"0 个校区"，先 `Ctrl+Shift+R` 硬刷新，再确认后端进程是最新代码**——正确值是 2 个校区。

#### 验证

```powershell
cd webapp/backend; python -m pytest -q                     # 303 项 + 45 子项通过
cd webapp/frontend; npx vue-tsc --noEmit                   # 通过
cd webapp/frontend; npm run test:mobile -- --only plantmessage   # 8 视口 × 全路由 88 项通过
```

`playwright-cli` 实测（400×851 与 1440×900）：153 张卡片、首张为"东京樱花 / 蔷薇科 / 乔木 / 大学生活动中心桥头"、页面横向溢出为 0、详情弹窗宽度 376。

> 注意：该接口是本次新增的，**旧后端进程需重启**才有 `/api/emotion-plants`；前端对该接口做了降级（取不到时只显示寄语、不显示植物信息），所以不会因此报错。

## 十二、上一轮进展（2026-10-15 第二轮）：移动端缺陷修复与测试能力建设

### 1. 修复的 5 类移动端缺陷

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| 1 | `components/PlaceReviewDrawer.vue` | 详情抽屉写死 `size="420px"`，375/390px 手机上被推出屏幕 | 改为随 `useIsMobile()` 响应式（手机 `100%`、桌面 `420px`），并放大触控热区、输入字号提到 16px |
| 2 | `charts/charts_A.vue`、`charts/charts_C.vue` | 画布固定 900px，而横向滚动只在 ≤768px 生效；容器比画布窄时（含 769–900px 桌面骨架窄容器与 1024px 小桌面）图表被 `el-card` 的 `overflow:hidden` 直接裁掉，既看不全也滑不到 | `overflow-x: auto` 改为无条件生效（宽屏容器恰为 900px，不产生滚动条，观感不变）；滑动提示断点放宽到 900px |
| 3 | `views/campusBuild.vue` | 侧抽屉写死 `size="380px"`，360–375px 溢出 | 抽屉宽度改为响应式；同时补 `ResizeObserver` 触发 `map.resize()`，容器尺寸变化后地图能重排 |
| 4 | `components/Echarts.vue` | 只监听 `window.resize`（500ms 防抖），侧栏折叠、路由切换、断点变化时不重绘 | 增加容器 `ResizeObserver`（保留防抖，尺寸未变时不动作以避免回调循环，卸载时断开） |
| 5 | `components/MessageFeedback.vue`、`views/message.vue`、`views/predict.vue` | 反馈组件无任何小屏处理、语义只靠 `title` 悬停；留言弹窗未设宽度（Element Plus 默认 50%）；预测页弹窗宽度选择器写成普通 scoped，teleport 到 body 后失效 | 反馈按钮热区 ≥44×44、补 `aria-label` 与移动端可见的「再点一次可撤回」提示；留言弹窗小屏改为贴近屏宽；选择器改为 `:deep(.el-dialog)` |

另：`style.css` 的移动端输入字号规则补上了 `.el-drawer` / `.el-dialog` / `.el-message-box`——这些弹层会 teleport 到 `body`，不在 `.mobile-shell` 内，原先的 16px 规则覆盖不到，手机上聚焦输入框仍会被 iOS Safari 强制放大页面。

### 2. 新增测试能力（三套手段）

| 文件 / 命令 | 作用 |
| --- | --- |
| `webapp/frontend/scripts/mobile-dev.mjs`（`npm run dev:mobile`） | 探测本机局域网 IPv4 → 校验 `.env` 的 `VITE_APP_API_HOST` → 拉起后端（已在运行则复用）与前端 dev（`vite --host --port 5173`）→ 打印手机可访问地址与二维码 → 提示防火墙与高德域名白名单问题；`Ctrl+C` 一并收尾。手机与后端经 Vite 的 `/api` 代理通信，无需直连 5000 端口 |
| `webapp/frontend/scripts/mobile-regression.mjs`（`npm run test:mobile`） | 8 种视口（320/375/390/414/768/800/1024/1440）× 全部路由页面（自动读 `src/router/index.ts`，新增路由无需改脚本），逐页判定**页面横向溢出**与**宽图表被裁切**，截图归档到 `tests/mobile-snapshots/`（已加入 `.gitignore`）、输出 `report.json`；有问题时以非零退出码结束 |
| `docs/移动端测试手册.md` | 三套手段的用法、常见问题排查、人工验收可勾选清单（相机 / 定位 / 安全区 / 横竖屏 / PWA / 抽屉与弹窗 / 触控热区），以及给队长的「部署后 5 分钟自检」 |

巡检的两处工程要点：① 用 `playwright-core` 搭配本机已装 Edge / Chrome（启动参数禁用 HTTP 缓存），**不下载任何浏览器**；② 植物图表页的桑基图与平行坐标图藏在页内 radio 切换之后，脚本会逐一走到三个分支再判定——这两张图正是最容易出问题的部分，只测首屏会漏掉。

### 3. 本轮验证记录

```powershell
cd webapp/frontend
npm run build           # vue-tsc 类型检查 + 生产构建，通过
npm run test:heatmap    # 9 项通过
npm run test:assets     # 3 项通过
npm run test:mobile     # 88 项全部通过（含 1440×900 桌面零回归对照）
```

- **反向验证**：临时移除 `.chart-scroll` 的 `overflow-x` 后重跑，巡检在全部 8 个视口的桑基图分支都报出「宽图表被裁切」，恢复后全绿——说明该断言确实能抓到问题，不是空跑。
- **交叉验证**：用 `playwright-cli`（本机 Edge）独立复测，桌面 1440×900 与移动 390×844 的 `scrollWidth - innerWidth` 均为 0；移动端桑基图容器实测 `overflow-x: auto`、可视 335px / 画布 900px、可横向滑动，与脚本结论一致。
- 过程中发现并规避的两个坑：① 巡检首个视口曾命中旧构建产物的 HTTP 缓存，导致「改了没生效」的假结论；② 页内分支若按 nth 索引点击会点到别处，改为按文案定位并校验分支内容是否真的出现。

## 十三、移动端第一轮（2026-10-15）：响应式 Web + PWA + 安卓套壳

**目标**：让师生用手机即可完成校园导航、拍照识花与自然语言问答。技术路线为"响应式 Web + PWA + 安卓 APK 套壳"三合一，**一份前端代码覆盖两端**。

### 1. 全站响应式适配（`webapp/frontend`）

| 区域 | 移动端做法 |
| --- | --- |
| 全局骨架 | `src/composables/useIsMobile.ts` 统一断点（`matchMedia('(max-width: 768px)')`，全局单例共享）；`App.vue` 在 ≤768px 切换为"顶部品牌栏 + 内容区 + 底部标签导航（主页/地图/问答/寄语/更多）"，"更多"为底部抽屉（植物图表、数据审核、热力图生成） |
| 植物地图 | 地图通栏铺满、高度按视口计算；筛选按钮换行排布并缩小；植物信息卡片落到地图下方通栏 |
| 智能问答 | 新增"对话 / 地图"双视图切换胶囊；两个面板均铺满并保持真实尺寸（用 `visibility` 而非 `display:none` 切换）；回答带出地图标注或热图时自动切到地图视图 |
| 面板组件 | `SceneHeatmapPanel`、`ItineraryPanel` 在小屏改为底部抽屉（圆角上沿、限高 45vh、可内部滚动） |
| 图表页 | 旭日图按容器比例自适应并降低高度；桑基图、平行坐标图信息密度高，改为容器横向滑动查看（附滑动提示），不做压缩裁切 |
| 答疑/词云/表格 | 识花弹窗表单控件通栏、弹窗 92% 宽、结果卡片单列；词云取消固定高度避免上下裁行；留言表格高度贴合视口 |
| 审核页 | 页头改纵向、操作按钮换行铺满、统计卡单列、筛选控件通栏、密钥输入行纵向 |

拍照上传沿用现有 `capture="environment"` 相机输入与相册选择（移动端点击即调起后置相机），输入框字号 ≥16px 以避免 iOS Safari 聚焦缩放；点击元素保留 44×44 最小触控热区。

### 2. PWA（iOS 兜底方案）

原生 iOS 上架需 Apple 开发者账号（$99/年）、证书与 App Store 审核，纯 web-view 套壳应用也难过审，因此 iOS 走 PWA：Safari 打开站点 →"添加到主屏幕"→ 全屏独立运行。

| 文件 | 作用 |
| --- | --- |
| `webapp/frontend/public/manifest.webmanifest` | 应用名/短名、`standalone` 显示、主题色 `#c20a1c`、192/512 图标（含 maskable） |
| `webapp/frontend/public/sw.js` | Service Worker：同源静态资源"网络优先、缓存兜底"，`/api/` 永不缓存（保证问答实时性），跨域资源（高德 SDK 与瓦片）不拦截，`skipWaiting` + `clients.claim` 避免旧缓存卡死 |
| `webapp/frontend/public/icons/pwa-180/192/512.png` | 由现有 `ecnu_logo.png` 导出的主屏幕图标 |
| `webapp/frontend/index.html` | 补 `manifest` 引用、`apple-touch-icon`、`apple-mobile-web-app-capable` 等 iOS meta；注册 Service Worker（localhost 下不注册，避免干扰开发） |

### 3. 安卓 APK 套壳工程（`mobile-app/`，新增）

- 单页 `web-view` 指向 `https://ecnucampusvqa.cn/`，业务全部由 H5 承载：**改业务只需重新部署 H5，不必重打 APK**。
- `manifest.json` 已声明网络、相机、定位权限与 Android abiFilters；`static/logo.png` 与 PWA 图标同源。
- 用 HBuilderX 导入该目录 → 发行 → 原生 App-云打包即可得到 APK（不需要 Mac、不需要 Apple 开发者账号），详细步骤见 `mobile-app/README.md`。
- 壳工程不含任何密钥：高德密钥仍由 H5 在构建时注入。

### 4. 验证记录

```powershell
cd webapp/frontend
npm run build          # vue-tsc 类型检查 + 生产构建，通过
npm run test:heatmap   # 9 项通过
npm run test:assets    # 3 项通过
```

浏览器实测（Chromium 390×844，`vite preview` 构建产物）：`/#/index`、`/#/map`、`/#/chatbot`、`/#/plantmessage`、`/#/classify`、`/#/campus-build`、`/#/place-review`、`/#/`（登录）共 8 个页面均无横向溢出；实测交互：底部标签导航、底部"更多"抽屉（3 项，248px 高）、问答页"对话 ↔ 地图"切换、地图页校区/习性/认领/地图-植物库筛选、植物库表格滚动。

自检中发现并修掉 5 个移动端缺陷：① 地图页 `.outline` 因 `flex: 1`（basis 0）被挤成 0 宽导致地图不可见；② 问答页切换胶囊压住页面标题；③ 用 `display:none` 隐藏地图面板会让高德容器尺寸归零；④ `el-drawer size="auto"` 在底部方向高度会塌陷；⑤ Service Worker 在本地开发也注册会缓存 dev 模块。

## 十四、更早进展（2026-10-14）：技术报告与商业计划书

新增交付物 **`技术报告/`**，用于第八届全球校园人工智能算法精英大赛（算法创新赛·AI+场景创新赛题）的**作品提交（技术方案）**，替换原拟提交的商业计划书；`商业计划书/` 保留、未做任何改动。

| 文件 | 说明 |
| --- | --- |
| `基于大模型的校园可视可答系统-技术报告_v1.docx` | 主交付物，21 页，11 图 17 表，含自动目录域与分节页脚页码 |
| `基于大模型的校园可视可答系统-技术报告_v1.pdf` | 提交件，1.28 MB（官方上限 10 MB） |
| `作品简介.md` | 报名系统字段：作品名称 14 字、作品简介 285 字（上限为 20 / 300 字） |
| `字数核对.py` | 核对作品名称与作品简介字数 |
| `数据来源与参考文献.md` | 报告内全部外部依据 [R1]—[R13] 登记（含登记编号↔正文上标序号映射），含官方匿名要求原文与本次核实留痕 |
| `build/build_report.py` | 生成入口（内容层 → 渲染层 → 导出层） |
| `build/content/*.py` | 按官方八章大纲拆分的正文内容模块（含独立的 `references.py`：引用序号 + 参考文献章） |
| `build/content/facts.py` + `build/data/report_facts.json` | 报告数字的单一来源，每条附仓库内复核路径 |
| `build/figures_tech.py` | 新增技术插图（分层架构、问答主链路、核密度与自适应带宽、评测对照、工程规模） |
| `build/check_report.py` | 交付前自检（分节页码、目录域、图表编号、插图完整性、匿名与凭据） |
| `build/docx_render.py` | 渲染层：排版常量、图表编号、目录域、分节页码、匿名与凭据双重安全网 |
| `build/export_pdf.py` | 用 Word COM 导出 PDF 并统计页数与体积 |

重新生成与自检：

```powershell
cd 技术报告/build
python figures_tech.py        # 生成新增技术插图
python build_report.py --pdf  # 生成 docx、导出 PDF、统计页数与体积
python check_report.py        # 交付前自检
cd ..; python 字数核对.py      # 核对作品名称与作品简介字数
```

**v1 修订（同日，按评审意见）**：① 参考文献独立成章，置于正文之后、附录之前，与正文、附录均另起一页；② 正文引用去掉 `（[R#]）` 形式，改为按首次引用顺序编排的**上标方括号序号**（[1]、[2]、[3]…），序号唯一来源收敛到 `build/content/references.py` 的 `CITE_INDEX`，渲染层新增 `^[n]^` 行内上标标记；③ 删除“评分项与章节对照”一节与作品简介中的说明性文字，去掉 AI 生成与人工校对的痕迹，保持正式报告体例；④ 附录另起一页，原 8.2 参考文献移出附录后 8.3 顺延为 8.2；⑤ 表格内文本（表头与数据行）**统一居中**，去掉原先“≤14 字才居中”的规则，`check_report.py` 新增表格居中自检（17 张表、368 个文本单元格）；⑥ **修正标题字体**——python-docx 模板的 Heading 样式带有主题字体属性（`w:eastAsiaTheme` 等），其优先级高于字体名，导致标题实际渲染成主题字体（等线一类）而非黑体；渲染层现在统一删除主题属性，并把 docDefaults 锁成显式字体，标题为黑体 + Times New Roman、不加粗。修订后 21 页、约 1.79 万字、PDF 1.28 MB，`check_report.py` 的一级标题判据同步更新为“前置 1 + 正文 7 + 参考文献 1 + 附录 1”，另新增标题字体自检。

**重要合规结论（2026-10-14 核实）**：官方《2026AIC 算法创新赛赛题规则汇总》"三、作品基本要求 →（五）公平性"明确要求作品材料（作品方案、演示视频、答辩 PPT 等）**不得出现参赛团队所在学校名称及 Logo、指导教师信息**，违者可能扣分或取消资格。技术报告据此匿名撰写（校区以"试点校区 A/B、外部验证校区"指代），渲染层保留匿名检查、并新增凭据检查，命中即拒绝生成文档。

此前交付物 `商业计划书/`（40 页、18 图 25 表）仍保留在仓库中，生成方式不变：

```powershell
cd 商业计划书/build
python figures.py; python fetch_chart_pngs.py; python build_plan.py --pdf
```

## 十五、下一步

0. **移动端收尾**：把前端改动部署到 `https://ecnucampusvqa.cn/`（**部署需队长操作**，完成后按 `docs/移动端测试手册.md` 第四节的 5 分钟自检清单确认；PWA 首次上线后用户需手动「添加到主屏幕」）；用 HBuilderX 云打包出安卓 APK 并真机验收（相机调起、定位授权、地图手势）；按手册第三节的人工验收清单在真机上走一遍（除部署外均可在本地完成：`npm run dev:mobile` 后用手机访问即可）；上线后按真机反馈微调（底部标签栏高度、iOS 安全区、横竖屏切换）。
1. **补技术报告的配套材料**：演示视频（MP4，3—5 分钟）、答辩 PPT（PDF）、佐证材料（试点校区运行记录与用户反馈，合并为一个 PDF）、完整代码包（网盘链接，命名格式"参赛团队编号-赛题名称-作品名称-材料名称"）。
2. **确认成员分工表**：报告 4.3 节按能力域给出分工，请各成员核对后定稿。
3. **补齐两项硬前置**：校区矢量边界数据、多校配置文件外键对齐（详见 `校园热力图智能体近期开发交付文档.md` 第四、五节）。
4. 完成三校区浏览器端端到端验收。
5. 面向试点高校开展正式需求调研，把报告中的定性判断升级为有统计依据的结论。
6. 技术报告的封面主视觉与版式美化可由队友另行补充（本次交付的是文字与图表齐全的版本）。
7. **留言板持续运营**：本轮把能力做齐了，但要有人用才有内容。上线后建议在校园活动节点（花期、开学）引导留言，并定期清理审核页里的待审留言；发布侧敏感词 / 大模型预审是下一个值得补的口子（接入点见 `HANDOVER.md` 第七节第 8 条）。
