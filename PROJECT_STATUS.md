# 项目进度说明（汇报用）

> 面向助教的进度汇报：技术栈、已完成功能、如何运行、测试与质量、当前进展与下一步。
> 文档更新日期：**2026-10-11**（本轮：后端入口模块化、图片问答链路补全、路由测试与 CI、文档纠偏；
> 同日续修：依赖高危漏洞、CI 在无凭据/LF 检出环境下的两类失败）

---

## 一、一句话介绍

**基于大模型的校园可视可答系统**：把校园数据（建筑、食堂、停车、水景、植物、场景点位）整理成结构化 POI，
配合高德地图与中文句向量检索，让用户用自然语言提问就能得到**"在地图上看得见"**的答案；
并可一键切换"原版推荐 / 场景热图"两套引擎，额外提供**一日行程规划**能力。

- 现有数据覆盖：华东师范大学 **普陀、闵行** + 上海交通大学 **闵行**，共三个校区
- 正在进行：把它做成**可复用的生成流水线**，给定一所新学校即可产出同款系统（试点：上海交通大学闵行校区）

---

## 二、技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 · TypeScript · Vite · Element Plus · ECharts · 高德地图 JS API |
| 后端 | Python 3.10 · Flask · NumPy |
| 后端结构 | Flask Blueprint：`server.py` 仅为门面，业务在 `api/{campus_build,chat,userdata,auth}_api.py`，公共设施在 `flask_app` / `runtime` / `llm_config` / `poi_data` / `retrieval_engine` / `auth_db` |
| 检索 | Sentence Transformers（`BAAI/bge-small-zh-v1.5`）中文句向量 + 余弦相似度，缺失时自动回退关键词规则 |
| 大模型 | OpenAI 兼容接口，可用 `LLM_PROVIDER` 切换 **DeepSeek** / **ChatECNU** |
| 数据 | 结构化 JSON（POI 契约）+ 预计算热度缓存 + 预计算语义索引 |

---

## 三、如何运行（已验证）

### 0. ⚠️ 演示前必看：`.env` 里的关键配置

**① 大模型走 DeepSeek（已配好）。** `.env` 中 `LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY` 已填写，
实测可用于 `master` 与 `test/dev`：
- `master` 只认 DeepSeek（缺服务商切换提交），填好密钥即可用，实测 `resolve_llm()` 可用 = True；
- `test/dev` 额外支持 `LLM_PROVIDER=chatecnu` 切换。

**② 语义索引已恢复，建议 `SEMANTIC_ENABLED=auto`。** 当前索引覆盖 3122 条 POI（含交大 80 条），
源文件集合和内容都会参与过期校验；本机 BGE 模型已通过离线加载验证。缺少模型或依赖时会自动回退关键词规则，
需要排障或节省内存时才显式设为 `false`。

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
| **图片问答**（本轮新增） | 输入区支持**点击选择 / 拖拽 / 直接粘贴 / 移动端拍照**上传图片（单轮最多 2 张，带缩略图与逐张删除）；从图中可读文字（路牌、楼名、截图文字）识别校园地点并在地图上打点；未配置视觉模型时如实回答"本部署未启用图片理解" | 问答页输入区 |
| **交互地图** | 高德地图打点、聚合、信息窗、单点导航（调起高德步行算路） | 智能问答页右侧 |
| **场景热图** | 三校区配置驱动；华师两校区四场景均可用，交大闵行支持拍照/散步/约会，赏花因无植物点位明确降级 | 问答页模式开关 |
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
| 热度缓存 | `webapp/backend/heatmap_cache/` | 按 `{校区slug}_{场景}[_{季节}].json` 预计算，含来源哈希与清单。本轮把栅格从 **20m 细化到 12m**（`heatmap_config.json` 的 `cellMeters`）并重建 **26 张**地图；同时清掉 4 个**永远取不到**的旧版无季节变体（`flower_viewing`/`photo` 运行时一定走季节文件） |
| 语义索引 | `webapp/backend/semantic_index.npz` | 预计算句向量；POI 或场景语料变化时需重新生成 |
| 原始植物数据 | `data/all_trees.json`、`data/all_templates.json` | 来源为校园小程序「芳秾」；其中的认养人姓名/单位/寄语是**经本人同意公开**的内容，**原样保留、不得删改**（见第九节第 1 条） |
| 用户共建数据 | `webapp/backend/userdata/` | 反馈/上报/未收录问句的事件流与待确认清单；**只存本机、已被 .gitignore 忽略**，设计见 `webapp/backend/USER_DATA_PIPELINE.md` |

重新生成资产（仅在数据变化时执行）：

```powershell
cd webapp/backend
python build_semantic_index.py          # 重建语义索引；首次下载模型时加 --allow-download
python build_scene_heatmaps.py          # 重建场景热图；可用 --output-dir 先临时验收
python evaluate_semantic_queries.py --strict   # 离线评测集
```

---

## 六、测试与质量（`test/dev` 实测数字）

| 检查项 | 结果 |
|---|---|
| `test_itinerary`（行程编排/意图判定/契约） | **35 个用例 通过** |
| `test_llm_config`（服务商选择与不静默回退） | **8 个用例 通过** |
| `test_scene_heatmaps`（热图几何/先验/哈希链路 + 本轮新增自适应带宽 7 例） | **35 个用例通过** |
| `test_semantic_retrieval`（源文件集合/哈希漂移） | **5 个用例通过** |
| `test_security`（安全相关） | **6 个用例 通过** |
| `test_recommendation_v2`（推荐回归，pytest） | **18 passed** |
| `test_chat_itinerary`（行程路由优先级/针尖起点） | **11 个用例 通过** |
| `test_userdata_store`（脱敏/聚合/原子写/导出契约） | **22 个用例 通过** |
| `test_place_extraction`（通名规则/大模型兜底解析） | **18 个用例 通过** |
| `test_chat_usercapture`（采集旁路/反馈与上报/审核门禁） | **24 个用例 通过** |
| `test_api_auth`（本轮新增：健康检查/注册/登录/改密，含数据库不可用降级） | **18 个用例 通过** |
| `test_api_userdata`（本轮新增：反馈/上报/待审清单/审核/导出 + 令牌 403） | **18 个用例 通过** |
| `test_api_campus_build`（本轮新增：校园生成路由 400/403/404/409/413） | **8 个用例 通过** |
| `test_chat_vision`（本轮新增：附件校验/多模态消息构造/能力文案/端到端接线） | **21 个用例 通过** |
| `npx vue-tsc --noEmit`（前端类型检查） | **0 错误** |
| `python -m ruff check .`（静态检查，本轮新增） | **All checks passed** |
| `test_outbound`（本轮新增：出站代理策略与真实调用点，守住"直连必须写成 `{http:None,https:None}`"） | **9 个用例 通过** |
| 全仓回归 `python -m pytest -q` | **400 passed / 1 skipped** |
| 同上，但按 **CI 条件**复跑（无 `.env`、统一 LF 检出、只装 `requirements-ci.txt`） | **400 passed / 1 skipped**（修复前为 20 failed） |

提交前还会跑仓库自带的脱敏检查，防止密钥入库：

```powershell
python scripts/check_publication.py
```

> 注：以上数字在 `test/dev` 上测得。同一套测试在 `master` 上有 4 项因"AI 服务未配置"（见第三节第 0 点）而失败，另 1 项未能跑完。
>
> 本轮起 `.github/workflows/ci.yml` 会在每次 push / PR 自动跑 `ruff check` 与 `pytest`
> （CI 用 `requirements-ci.txt`，不装重型语义模型依赖；本机仍跑全量）。
> 提交前请在本地跑 `python -m pytest -q` 与 `python -m ruff check .`。

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
- 本轮起 CI 自动跑静态检查与测试；本地提交前请同样跑一遍 `ruff` 与 `pytest`
- ⚠️ **不要把认养人个人信息当作"待脱敏数据"清理**（详见第九节第 1 条）

---

## 八、当前进展：校园生成流水线（进行中）

**目标**：把本系统当模板，给定一所新学校（试点：上海交通大学闵行校区）+ 一份档案，自动完成
"采集 → 归一质检 → 资产重建 → 可运行实例"，让新学校的产出与现有系统**视觉与体验一致**。

**当前仓库已完成**：

1. **校区配置单一来源化** —— 新增 `webapp/backend/campuses.json` + `campus_config.py`：
   校区名、别名、中心点、半径、水体名不再硬编码在代码里；后端（`scene_heatmaps.py`、
   `convert_locations.py`）与前端（`campusConfig.ts` 驱动 4 个视图）全部改为配置驱动，
   并加了**配置一致性自检**与**产物漂移守卫测试**。
2. **交大归一 POI 与运行时接入** —— `sjtu_minhang_pois.json` 含 80 条已归一点位；地图打点、精确问答、
   场景推荐与行程均按配置识别“交大闵行”，不会与华师闵行混检。
3. **资产构建闭环** —— 热图和语义索引共用确定性的 `*_pois.json` 自动发现；新增/删除数据文件会触发
   stale 保护。已重建 **33 份**有效热图缓存与 **3122 条** POI 的语义索引。交大赏花因没有植物数据单独跳过，
   其余场景和校区正常生成。
4. **生成器源码确实在仓库里（本轮纠偏）** —— `tools/campus_generator/` **完整存在**：
   20 个 `.py` 文件（15 个模块：`profile` / `harvest` / `discovery` / `boundary` / `quality` / `jobs` /
   `review` / `publish` / `preview` / `normalize` / `validate` / `formal_asset_io` / `web_search` / `cli`
   等 + 5 个测试文件）与设计文档 `CAMPUS_GENERATOR.md`。
   **此前本文档与 `HANDOVER.md` 写成"该目录缺失、`git stash` 为空、无法恢复"，属过期信息，本轮已更正。**
   真正还缺的是 `campuses/profiles/sjtu.json` 这类**档案文件**（该目录当前不存在），而不是生成器本身。

### 本轮（2026-10-10）整改

1. **后端入口模块化** —— `server.py` 从 4544 行收敛为 **91 行门面**（只做导入、re-export 与启动），
   业务拆入 `api/{campus_build,chat,userdata,auth}_api.py`，公共设施拆入 `flask_app.py`、`runtime.py`、
   `llm_config.py`、`poi_data.py`、`retrieval_engine.py`、`auth_db.py`、`paths.py`。
   `import server` 与 `python server.py` 两种入口、以及测试对模块级符号的 monkeypatch **全部保持可用**，
   对外接口与返回结构零变化。
2. **图片问答链路打通** —— 见第四节；顺带修掉"单张允许 4MB、但全局请求体只有 1MB"的自相矛盾
   （现为 12MB 上限 + 超限返回 JSON 413，而不是前端解析不了的 HTML 错误页）。
3. **测试与工程基线** —— 新增 4 个测试文件、65 个用例；引入 `ruff` + `pyproject.toml`；
   新增 `.github/workflows/ci.yml`；`requirements.txt` 锁定版本并新增 `requirements-dev.txt` / `requirements-ci.txt`。
4. **文档纠偏** —— 更正本节第 4 条与第九节第 1 条两处过期/错误描述；
   新增 [`docs/FEATURE_FEASIBILITY.md`](docs/FEATURE_FEASIBILITY.md)
   （**8 项候选功能**（§2~§9）+ **4 项中长期升级方向**（§12）的可行性与安全性评估，结论均由实测数据支撑）。
5. **场景热图渲染重做（同日追加）** —— 抽出共用渲染模块
   `webapp/frontend/src/components/heatmapRender.ts`，修掉三个实测病灶：alpha 下限导致整片区域被
   均匀蒙版（原 `70+150t` / `90+165t`，而 87% 的有效格 t<0.2）、RGB 线性插值中段发灰
   （t≈0.5 得到 `rgb(128,123,120)`）、低分辨率栅格直接拉伸。现在改为 HSL 色带 + alpha 从 0 起 +
   数值双线性上采样 + 等值带边界；栅格细化到 **12m**（原 20m）并重建 26 张缓存；
   两处渲染共用同一色带，生成页补上了此前缺失的图例。
6. **采集侧出站代理修复（同日追加）** —— 新增 `tools/campus_generator/outbound.py`：
   **高德默认直连**（Windows 系统代理会让 `restapi.amap.com` 全部 `ProxyError`，
   此前 13 个关键词 × 3 次重试全军覆没且被误读成"高德没数据"）、
   **公开网页与境外搜索服务默认沿用系统设置**；两者都可用 `AMAP_HTTP_PROXY` / `WEB_HTTP_PROXY` 显式覆盖。
   顺带更正文档里"嵌入检索本机崩溃"的**误报**（复测：模型加载正常、嵌入模式 50/50、退出码 0）。
7. **热图 KDE 带宽算法升级（同日追加）** —— 实测发现原"自适应带宽"在 846 个分组中有 **256 组
   （30.3%）带宽在组内完全相等**（自适应项失效）、**151 组被 `maxInfluence` 钳成常数 100m**：
   根因是按 `zoneMeters` 分格计数，而植物子类常常只有 2~4 棵、全落进同一格。
   现改为 **k 近邻局部密度驱动的连续变量带宽**（median+MAD 稳健标准化，β 可配），
   并把"整体量级"锚定在旧规则的几何均值上以免制造尺度漂移：
   组内带宽跨度中位 **5.85m → 25.05m（4.3×）**，带宽量级中位 33.55m → 35.62m（+6%），
   有效格覆盖占比**完全不变**（0.5817），场景区分度**无退化**（相关系数均值 0.687 → 0.686）。
   `heatmap_config.json` 的 `adaptiveBandwidth.method` 可在 `knn-density` / `legacy` 之间切换，
   **已实测 legacy 模式与改动前逐点一致（Top-1 变化 0/26）**，回退路径可用。
8. **依赖高危漏洞修复（同日追加）** —— `Security check` 因新披露的安全公告变红（挂掉的是
   `npm audit --omit=dev --audit-level=high`，退出 1，命中 3 个 high）：`vue` 3.5.33→3.5.43
   （属性名黑名单缺 CR 导致 XSS）、`dompurify` 3.4.15→3.4.16（`IN_PLACE` 清理后残留事件处理器
   导致 DOM XSS）、`source-map-js` 1.2.1→1.2.2（索引式 source-map 造成事件循环 DoS）。
   三者的修复版本都落在 `package.json` 的声明范围内，因此**只动 `package-lock.json`**。
9. **CI 稳定性修复（同日追加）** —— 新增的 pytest CI 第一次在「无凭据 + LF 检出」环境里跑，
   暴露出两类**早已存在、只是从未在 CI 里跑过**的问题：
   ① **18 个测试隐式依赖本机 `.env`**：`resolve_llm()` 读不到密钥即返回 `None`，`/api/chat`
   直接走 503「AI 服务尚未配置」→ 这些测试只在"配了 `.env`"的机器上才是绿的。修法是新增仓库
   根目录 `conftest.py`，在 `pytest_configure` 阶段用 `setdefault` 补一组**占位**配置；
   ② **2 个指纹测试对行尾敏感**：`core.autocrlf=true` 且无 `.gitattributes`，Windows 工作区是 CRLF、
   CI 检出是 LF，而 `semantic_source_hash()` / `scene_heatmaps.fingerprint()` 都用 `read_bytes()`
   原始字节（`campus_pois.json` 一个文件就差 139,213 个 `\r`）。修法是新增
   `semantic_retrieval.canonical_source_bytes()`（读取时 CRLF→LF），并刷新 28 个热图缓存与
   `semantic_index_meta.json` 的存量哈希。排查与复现步骤见 `HANDOVER.md` §12。

**下一步**：浏览器端三校区端到端验收；按
[`docs/FEATURE_FEASIBILITY.md`](docs/FEATURE_FEASIBILITY.md) 的优先级推进。

两个**硬前置**（升级方向的依赖顺序见该文档 §12.5）：

1. **边界数据**：`campuses.json` 三个校区只有中心点与信任半径，缺 `boundary` 矢量，矢量边界方向无法开工；
2. **多校 schema 对齐**：`profile.runtime_config()` 与 `campuses.json` 的 `schools` / `school` 外键 / `brand`
   未对齐前，生成器不能重跑资产生成。
   另：评测基线需先"有区分度"——现有 50 例对规则与嵌入两种后端都是满分，无法度量任何检索侧优化。

---

## 九、已知限制与注意事项

1. **认养人个人信息：经同意公开，必须原样保留** —— `data/all_trees.json`、`data/all_trees_for_lib.json`、
   `webapp/backend/campus_pois.json`、`visualization/charts/sankey/data_for_sankey.json`、
   `webapp/frontend/src/assets/emotion_analysis.json` 中的认养人**姓名、单位、寄语**，是经本人同意
   在华东师范大学小程序「**芳秾**」及官网上公开展示的内容，本项目**原样引用、不做删改、不做脱敏、不移出版本库**。
   ⚠️ `scripts/sanitize_public_data.py` **不应**作为常规清理步骤执行（本轮曾误按"专有数据不可分发"理解去清理，
   已全部回滚还原）。新学校无同类数据时，植物图鉴/寄语/预测按"无内容"降级，收敛到
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
