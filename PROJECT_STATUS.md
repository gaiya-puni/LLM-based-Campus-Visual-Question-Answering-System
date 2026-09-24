# 项目进度说明（汇报用）

> 面向助教的进度汇报：技术栈、已完成功能、如何运行、测试与质量、当前进展与下一步。
> 文档更新日期：2026-09-23

---

## 一、一句话介绍

**基于大模型的校园可视可答系统**：把校园数据（建筑、食堂、停车、水景、植物、场景点位）整理成结构化 POI，
配合高德地图与中文句向量检索，让用户用自然语言提问就能得到**"在地图上看得见"**的答案；
并可一键切换"原版推荐 / 场景热图"两套引擎，额外提供**一日行程规划**能力。

- 现有数据覆盖：华东师范大学 **普陀、闵行** 两校区
- 正在进行：把它做成**可复用的生成流水线**，给定一所新学校即可产出同款系统（试点：上海交通大学闵行校区）

---

## 二、技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 · TypeScript · Vite · Element Plus · ECharts · 高德地图 JS API |
| 后端 | Python 3.10 · Flask · NumPy |
| 检索 | Sentence Transformers（`BAAI/bge-small-zh-v1.5`）中文句向量 + 余弦相似度，缺失时自动回退关键词规则 |
| 大模型 | OpenAI 兼容接口，可用 `LLM_PROVIDER` 切换 **DeepSeek** / **ChatECNU** |
| 数据 | 结构化 JSON（POI 契约）+ 预计算热度缓存 + 预计算语义索引 |

---

## 三、如何运行（已验证）

### 0. ⚠️ 演示前必看：`.env` 里两个开关

**① 大模型走 DeepSeek（已配好）。** `.env` 中 `LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY` 已填写，
实测可用于 `master` 与 `test/dev`：
- `master` 只认 DeepSeek（缺服务商切换提交），填好密钥即可用，实测 `resolve_llm()` 可用 = True；
- `test/dev` 额外支持 `LLM_PROVIDER=chatecnu` 切换。

**② 必须保持 `SEMANTIC_ENABLED=false`（本次演示的关键）。** 语义检索索引当前**已过期**
（`sourceHash` 与现有 POI/场景文件不匹配，加载时自报 `semantic index is stale`），
而加载句向量模型会让后端**挂起甚至退出**，表现为"提问响应老半天 / Vite 报 ECONNRESET"。
关闭后回退关键词规则（该索引本来就没在工作，关掉无损失），实测经前端代理：

| 请求 | 耗时 |
|---|---|
| 「普陀校区哪里适合看花」 | 2.8 秒 |
| 「我第一次来普陀校区，帮我规划一天」 | 7.5 秒（5 站点 + 622 字文案） |

修复路线（后续做，不在 `master` 上改）：在后端目录执行 `python build_semantic_index.py`
重建索引，确认稳定后把 `SEMANTIC_ENABLED` 改回 `auto`。

### 1. 后端（端口 5000）

```powershell
cd webapp/backend
pip install -r requirements.txt      # 本机已装齐，可跳过
python server.py
```

启动后会打印当前生效的大模型服务商与模型名（便于排查；**绝不打印密钥**）。

### 2. 前端（端口 80）

```powershell
cd webapp/frontend
npm install                          # node_modules 已存在，可跳过
npm run dev
```

### 3. 打开页面

浏览器访问 **`http://localhost:80`**。
演示可直接进 `http://localhost/#/chatbot` 跳过登录页（登录依赖 MySQL，与问答功能无关）。

### 4. 依赖的配置文件（均在 `.gitignore` 中，不入库）

| 文件 | 作用 |
|---|---|
| `.env`（仓库根目录） | 大模型密钥、`LLM_PROVIDER`、MySQL、`AMAP_WEB_SERVICE_KEY` |
| `webapp/frontend/.env` | `VITE_AMAP_KEY`（高德**浏览器** Key）、`VITE_AMAP_SECURITY_CODE`、`VITE_APP_API_HOST` |

可选环境变量：

- `SEMANTIC_ENABLED=auto|true|false`：句向量检索开关（无模型/无索引时自动回退关键词规则）
- `LLM_HTTP_PROXY`：需要走代理时填写（默认**直连**，避免被系统代理的坏节点带偏）

### 5. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 问答返回「AI 服务尚未配置」 | 分支不对（见第 0 节），或 `.env` 里当前服务商的密钥为空 |
| 地图空白 | `webapp/frontend/.env` 的高德 Key 未填 / 未限制域名配额 |
| 端口被占用 | 前端固定 80、后端固定 5000；先停掉占用进程再启动 |
| 中文日志显示乱码 | 终端编码问题，不影响功能（脚本已强制 UTF-8 输出） |

---

## 四、已完成功能（均可现场演示）

| 模块 | 说明 | 演示入口 |
|---|---|---|
| **智能问答** | 自然语言提问 → 文本回答 + 地图打点；精确地点查询优先于 Top 推荐 | 智能问答页 |
| **交互地图** | 高德地图打点、聚合、信息窗、单点导航（调起高德步行算路） | 智能问答页右侧 |
| **场景热图** | 四类场景（赏花 / 拍照 / 散步休息 / 浪漫约会）× 两校区，为独立热力图，可切回"原版推荐"对照 | 问答页模式开关 |
| **一日行程规划** | "第一次来，帮我规划一天" → 上午/中午/下午三段站点 + 地图多点步行路线 + 行程面板；大模型只写文案，站点与顺序由确定性算法编排 | 问答页（需切到新模式） |
| **用户共建数据** | 消息级评价（有用/没用+理由，可撤回）、地点上报（表单+地图选点）、从"答不上来"的问句自动抽取未收录地名；汇总为待确认清单，审核页可通过/驳回并导出（并入正式数据仍走离线确认流程） | 问答页气泡 + 数据审核页 |
| **植物图鉴** | 按种类/校区浏览在册植物 | 图鉴页 |
| **领养寄语** | 认养者寄语浏览与情感分析结果 | 寄语页 |
| **领养预测** | 逻辑回归 + SVM 预测（AUC 0.99） | 预测页 |
| **静态图表** | 桑基图 / 旭日图 / 平行坐标轴 / 词云 | `visualization/charts/**` 直接打开 HTML |

### 建议的 3 分钟演示动线

1. `/chatbot` 问「**河西食堂在哪里**」→ 精确地点，只返回一个点（体现"精确查询优先"）
2. 问「**普陀校区哪里适合看花**」→ 场景推荐 + 地图打点（也可问「**软件工程学院在哪里**」）
3. 打开**场景热图**，切换四类场景 → 热力分布随场景变化；再切回"原版推荐"对照
4. 问「**我第一次来普陀校区，帮我规划一天**」→ 三段行程 + 地图多点步行路线 + 行程面板
5. 顺带翻一下**植物图鉴**与**静态图表**

---

## 五、数据与资产

| 资产 | 位置 | 说明 |
|---|---|---|
| 统一 POI | `webapp/backend/*_pois.json` | 植物 2976 条 + 学院/食堂/停车/场景等；字段契约见 `POI_CATEGORY_GUIDE.md` |
| 场景配置 | `webapp/backend/scene_profiles.json` | 场景词、权重、语义正负样例 |
| 热度缓存 | `webapp/backend/heatmap_cache/` | 按 `{校区slug}_{场景}[_{季节}].json` 预计算，含来源哈希与清单 |
| 语义索引 | `webapp/backend/semantic_index.npz` | 预计算句向量；POI 或场景语料变化时需重新生成 |
| 原始植物数据 | `data/all_trees.json`、`data/all_templates.json` | 来源为校园小程序，**属学校专有数据** |
| 用户共建数据 | `webapp/backend/userdata/` | 反馈/上报/未收录问句的事件流与待确认清单；**只存本机、已被 .gitignore 忽略**，设计见 `webapp/backend/USER_DATA_PIPELINE.md` |

重新生成资产（仅在数据变化时执行）：

```powershell
cd webapp/backend
python build_semantic_index.py          # 重建语义索引
python build_scene_heatmaps.py          # 重建场景热图（首次可加 --allow-download）
python evaluate_semantic_queries.py --strict   # 离线评测集
```

---

## 六、测试与质量（`test/dev` 实测数字）

| 检查项 | 结果 |
|---|---|
| `test_itinerary`（行程编排/意图判定/契约） | **35 个用例 通过** |
| `test_llm_config`（服务商选择与不静默回退） | **8 个用例 通过** |
| `test_scene_heatmaps`（热图几何/先验/哈希链路） | **22 个用例 通过** |
| `test_security`（安全相关） | **6 个用例 通过** |
| `test_recommendation_v2`（推荐回归，pytest） | **18 passed** |
| `test_chat_itinerary`（行程路由优先级/针尖起点） | **11 个用例 通过** |
| `test_userdata_store`（脱敏/聚合/原子写/导出契约） | **22 个用例 通过** |
| `test_place_extraction`（通名规则/大模型兜底解析） | **18 个用例 通过** |
| `test_chat_usercapture`（采集旁路/反馈与上报/审核门禁） | **24 个用例 通过** |
| `npx vue-tsc --noEmit`（前端类型检查） | **0 错误** |

提交前还会跑仓库自带的脱敏检查，防止密钥入库：

```powershell
python scripts/check_publication.py
```

> 注：以上数字在 `test/dev` 上测得。同一套测试在 `master` 上有 4 项因"AI 服务未配置"（见第三节第 0 点）而失败，另 1 项未能跑完。

---

## 七、分支与协作约定

| 分支 | 状态 | 说明 |
|---|---|---|
| `master` | 稳定 | 已合并 PR #1；**缺** `85e1658`（服务商切换） |
| `test/dev` | **演示/集成** | master + `85e1658`，全部测试通过 |
| `test/optimization` | 优化实验 | ECharts 组件优化等 |
| `wh_test` | 开发中 | `test/dev` 之上承载"校园生成流水线"开发 |

约定：

- 提交信息用 `feat:/fix:/docs:` 前缀，正文说明**改了什么、为什么、怎么验证**
- 提交前跑 `python scripts/check_publication.py`，`.env` 与 `*.env` 永不入库
- 密钥只放本机 `.env`；高德 Key 分**浏览器 Key**（前端，需限制域名配额）与 **Web 服务 Key**（后端脚本）

---

## 八、当前进展：校园生成流水线（进行中）

**目标**：把本系统当模板，给定一所新学校（试点：上海交通大学闵行校区）+ 一份档案，自动完成
"采集 → 归一质检 → 资产重建 → 可运行实例"，让新学校的产出与现有系统**视觉与体验一致**。

**已完成**：

1. **校区配置单一来源化** —— 新增 `webapp/backend/campuses.json` + `campus_config.py`：
   校区名、别名、中心点、半径、水体名不再硬编码在代码里；后端（`server.py`、`scene_heatmaps.py`、
   `convert_locations.py`）与前端（`campusConfig.ts` 驱动 4 个视图）全部改为配置驱动，
   并加了**配置一致性自检**与**产物漂移守卫测试**。
2. **校区档案 + 校验器**（`campuses/profiles/sjtu.json`、`tools/campus_generator/profile.py`）：
   档案是整条流水线的唯一输入；校验器已实测能拦住"校区名/slug 与既有校区撞车"这类致命错误。
3. **高德采集器**（`tools/campus_generator/harvest_amap.py`）：
   网格周边搜索 + 完整校区名关键字补漏、分页限速重试、**断点续采**（已采页落盘）、
   半径与跨校区过滤、产出采集报告。实测：58 次请求、0 失败，保留 **648** 条候选，
   其中**校内点位 100 条**（含图书馆主馆、包玉刚/李政道图书馆、19 个学院、餐饮点、6 个停车场、
   涵泽湖、植物标本园等）。
4. **公开资料链路**（`tools/campus_generator/harvest_public.py`）：
   公众号检索（`wechat-article-search` 技能）与网络检索结果落盘为**带 URL 的来源清单**，
   再由大模型抽成"带来源与置信度"的候选条目，**与高德数据分开存放**，人工确认后才并入。
   已收集 39 份来源（公众号 30、网络 9），并据此校正了档案事实：
   交大四湖为**思源湖、涵泽湖、致远湖、捭阖塘**，校区另有**淡水河**，绿化率 42%。

**下一步**：归一质检（映射到现有 POI 契约、不通过项进待确认清单）→ 重建资产（新增校区条目、
热度缓存与语义索引）→ 浏览器端到端验收 → 固化为 `cli.py` 分步命令与说明文档。

**当前代码位置**：以上工作尚未提交，暂存于 `stash@{0}`（`wh_test` 分支）。
恢复方式：

```powershell
git checkout wh_test
git stash pop
```

---

## 九、已知限制与注意事项

1. **植物数据为学校专有**：`data/all_trees.json`、`data/all_templates.json` 来自校园小程序，
   **不可对外分发**；新学校无此数据时，植物图鉴/寄语/预测按"无内容"降级，收敛到
   "地图打点 + 智能问答 + 场景热图 + 行程规划"四项能力。
2. **文案中的距离口径**：行程面板显示的是高德真实步行算路结果；大模型文案依据后端直线估值生成，
   两者可能不同（已在文档中说明，彻底一致需后端改用高德 Web 服务算路）。
3. **高德配额与域名**：浏览器 Key 对访问者可见，必须限制域名与服务配额；Web 服务 Key 仅后端脚本使用。
4. **网络**：大模型与高德默认直连；如需代理请显式配置 `LLM_HTTP_PROXY`，避免系统代理的失效节点导致失败。
5. **多校共存约定**：同一个实例里校区名与 slug 必须**全局唯一**（华师大已有"闵行/minhang"，
   故交大闵行用"交大闵行/sjtu_minhang"），否则两校 POI 会混检、热度缓存会互相覆盖。
6. **用户共建数据的审核入口需要密钥**：审核态接口要求 `.env` 的 `USERDATA_REVIEW_TOKEN`
   （≥16 位）与请求头 `X-Review-Token` 一致，**未配置时一律 403**；收集到的线索只是待确认清单，
   并入正式 POI 仍需离线人工确认（`tools/campus_generator/normalize.py`），不会自动上图。
   数据目录 `webapp/backend/userdata/` 只存本机、不入库；采集只走旁路，不影响问答内容与耗时。
