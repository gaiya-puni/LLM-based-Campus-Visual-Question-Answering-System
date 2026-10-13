# 2026-10-10 更新汇总（一册看全）

> 本文把 **2026-10-10 这一轮的全部改动**集中到一份文档里，方便助教/接手人一次性了解。
> 分支：`test/dev`；未提交 commit（改动都在工作区）。
> 配套文档：[`PROJECT_STATUS.md`](PROJECT_STATUS.md)（进度汇报）、[`HANDOVER.md`](HANDOVER.md)（交接细节）、
> [`docs/FEATURE_FEASIBILITY.md`](docs/FEATURE_FEASIBILITY.md)（可行性与安全性评估）。

## 0. 一句话概览

这一轮做了 **7 件事**：把 4544 行的 `server.py` 拆成模块化门面、把"可视可答"里**从未被调用**的图片问答链路
真正接通、重做场景热图渲染、升级热图 KDE 带宽算法、修掉高德采集被系统代理拦死的问题、
补齐测试与 CI 基线、并把文档里两处**失实结论**改正。

**验证基线**：`python -m pytest -q` → **400 passed / 1 skipped**；`ruff check .` 全绿；
`npx vue-tsc --noEmit` → 0 错误；`python scripts/check_publication.py` → passed。

---

## 1. 后端入口模块化：`server.py` 4544 行 → 91 行

### 1.1 改动
`server.py` 收敛为**门面**（只做导入、re-export 与启动），业务按职责拆开：

| 原来都在 `server.py` | 现在在 |
|---|---|
| `/api/chat`、指哪问哪、行程、图片链路 | `api/chat_api.py` |
| `/api/campus/*` 生成流水线 | `api/campus_build_api.py` |
| `/api/feedback`、`/api/place_report`、待审/审核/导出 | `api/userdata_api.py` |
| `/api/health`、注册/登录/改密、`/api/emotions` | `api/auth_api.py` |
| app 对象、配置、CORS、限流、413 JSON 错误 | `flask_app.py` |
| 服务商选择、视觉能力探测 | `llm_config.py` |
| POI / 植物模板加载 | `poi_data.py` |
| 检索与排序引擎 | `retrieval_engine.py` |
| MySQL 连接与口令 | `auth_db.py` |
| 路径与 `.env` 引导 | `paths.py` |

### 1.2 两个关键设计（否则会出玄学 bug）
1. **`sys.modules.setdefault('server', __main__)`**：让 `python server.py` 的 `__main__` 也能以 `server` 被查到。
   不做这一步会出现"两份 `app`、两份任务管理器"（路由重复注册、任务状态互相看不见）。
2. **门面解析器 `runtime.facade()`**：被测试 monkeypatch 的模块级符号
   （`_CAMPUS_BUILD_MANAGER`、`CampusProfileResolver`、`WebSearchClient`、`plan_day`、`_itinerary_origin` 等）
   **在调用时**解析，而不是导入时绑定。

### 1.3 兼容性
- `python server.py` 与 `import server` 两种入口都可用（已实测启动 + `/api/health` 正常）。
- `server.xxx` 形式的旧引用（含 `__getattr__` 透出）全部照旧；
- 对外 URL、端口（5000/80）、返回结构、`.env` 配置项**零变化**；
- 现有测试**一行未改**即可通过。

---

## 2. 图片问答链路：把"死代码"接通

### 2.1 之前的问题
后端 `parse_attachments` / `build_llm_messages` / `extract_places_from_photo` / `build_photo_poi_context`
**全都实现了，但没有任何调用方**：`/api/chat` 从不读 `attachments`，前端也没有上传入口
（注释引用的 `imageAttach.ts` 全仓不存在）。项目名叫"可视可答"，用户却**没有任何入口传一张校园照片问"这是哪儿"**。

更糟的是两处上限自相矛盾：单张允许 **4MB**、最多 2 张，而 Flask 全局 `MAX_CONTENT_LENGTH` 默认 **1MB**
→ 即便补了前端入口，稍大的照片也会在进入业务逻辑前被 413 拒绝，且返回的是前端解析不了的 HTML 错误页。

### 2.2 现在的链路
```
输入区（点选 / 拖拽 / 粘贴 / 移动端拍照）
  → imageAttach.ts：类型白名单 + 长边压到 1600px（>900KB 才重编码，避免截图二次有损）
  → payload 带 attachments: [{mime, dataUrl}]
  → /api/chat：parse_attachments（张数/类型/单张体积，声明 mime 必须与 dataUrl 前缀一致）
  → 已配置视觉模型时：extract_places_from_photo 抽"图中可读地点名"（失败返回 []，不影响主回答）
     → match_pois_by_names 命中校园 POI → 写进 prompt + 地图打点（kind = photo_poi）
  → build_llm_messages：附件只并入**最后一条**用户消息；历史轮次保持字符串（否则每轮重发 base64）
  → 按能力如实生成文案（未启用视觉就说"本部署未启用图片理解"，不假装看过图）
```

### 2.3 必须保持的口径（改一处要改多处）
| 约束 | 值 | 位置 |
|---|---|---|
| 单轮最多张数 | 2 | `api/chat_api.py` `MAX_ATTACHMENTS` ↔ `imageAttach.ts` |
| 单张上限 | 4MB（base64 解码后） | `MAX_ATTACHMENT_BYTES` ↔ `imageAttach.ts` |
| 允许类型 | jpeg / png / webp | `ATTACHMENT_MIMES` ↔ `imageAttach.ts` `ALLOWED_MIMES` |
| 请求体总上限 | 12MB | `flask_app.ATTACHMENTS_BODY_BUDGET` ↔ `.env` `MAX_CONTENT_LENGTH` |

> ⚠️ 本机 `.env` 的 `MAX_CONTENT_LENGTH` 已从 1MB 调整为 **12582912（12MB）**；
> 启动时若检测到上限低于预算，后端会打印中文警告。

### 2.4 安全约定
图片**不落盘**、日志只记张数与体积；校验失败一律 **400 + 中文原因**（不静默丢弃）；
**未配置视觉模型时不把图片转发给上游**（纯文本模型收到 `image_url` 会直接报错）；
`/api/health` 新增 `vision` 字段，前端据此提示但不阻断上传。

### 2.5 验证
- `test_chat_vision.py`：**21 个用例**（附件校验 10 / 多模态消息构造 3 / 能力文案 2 / 端到端接线 6）。
- 浏览器实测（Playwright）：注入 3 张图仅保留 2 张并提示"已忽略超出上限的 1 张"；1.43MB 原图压到 **283KB**；
  粘贴与拖拽均生效（拖拽有高亮）；请求体确实带 2 条 `image/jpeg` 附件；用户气泡回显缩略图；
  失败时图片自动放回输入栏，不必重选。

---

## 3. 场景热图渲染重做

### 3.1 三个实测病灶（都在渲染层，不在算法层）
栅格量程本身是用满的（`p50≈0.33`、`max=1.0`），丑在渲染：

| 病灶 | 证据 | 修法 |
|---|---|---|
| alpha 有下限 → 整片被均匀蒙版 | 原 `70+150t` / `90+165t`；实测 **87.2% 的有效格 t≥0.1、68.5% t≥0.2** | alpha 从 **0** 起，低于阈值 0.12 完全不画，以上用 `t^1.5` 抬升 |
| RGB 线性插值中段发灰 | 原两段式在 **t≈0.5 得 `rgb(128,123,120)`（纯灰）**；两处渲染色带还不一致 | 统一 **HSL 插值**（色相 214→190→48→26→356） |
| 低分辨率栅格直接拉伸 | 60×69 铺满整个 bounds | **数值**双线性上采样 3× 再着色 + **等值带边界** |

### 3.2 落地
- 新增 `webapp/frontend/src/components/heatmapRender.ts`（色带 / alpha / 上采样 / 等值带 / **图例 CSS 单一来源**），
  `SceneHeatmapPanel.vue` 与 `campusBuild.vue` 都改用它；生成页**补上了此前完全缺失的图例**。
- `heatmap_config.json` 的 `cellMeters` 由 **20m 细化到 12m**，重建 26 张缓存
  （栅格 60×69 → 98×113 / 134×121 / 60×84）。
- 清掉 4 个**永远取不到**的旧版无季节缓存（`minhang_flower_viewing.json`、`minhang_photo.json`、
  `putuo_flower_viewing.json`、`putuo_photo.json`）——`flower_viewing`/`photo` 运行时一定走季节文件。
- **视觉验收**：同一任务下，修复前是"整片脏灰绿蒙版、底图发暗"，修复后底图道路/楼名清晰可读、
  只有热点区显色（交大闵行散步集中在东湖一带，红→橙→黄→青呈同心等值带）。

---

## 4. 热图 KDE 带宽算法升级（本轮唯一的算法改动）

### 4.1 先发现的问题：原"自适应带宽"在真实数据上失效
原公式按 `zoneMeters`（200m）分格统计相对丰度，再乘全局近邻尺度。对全部 **846 个**（校区×场景×子类）分组实测：

| 现象 | 数量 | 原因 |
|---|---|---|
| 组内带宽**完全相等**（自适应项失效） | **256 / 846（30.3%）** | 植物子类常常只有 2~4 棵，全落进同一格 → `abundance` 恒等 |
| 被 `maxInfluence` **钳成常数 100m** | **151 / 846（17.9%）** | `exp(ab)·max(annd,80)` 普遍超过 300，`min()` 一刀切平 |
| 组内带宽跨度中位 | 仅 **5.85 m** | 上述两条的直接结果 |

### 4.2 现在的公式（**尺度保持**）
```python
h_target = clip( geomean(旧规则逐点带宽), h_lo, h_hi )   # 只借用量级，避免尺度漂移
ρ_i      = 1 / max(d_k(x_i), 1m)²                         # k 近邻连续局部密度
z_i      = (logρ_i − median(logρ)) / (1.4826·MAD)         # 稳健标准化
w_i      = 0.5 + 0.5·tanh(β·z_i)                          # β = densityExponent
h_i      = clip( h_target · (h_hi/h_lo)^(w_i − 0.5), h_lo, h_hi )
# 再一步尺度回正，抵消钳位造成的几何均值漂移
```

三条设计理由：
1. **只改组内分布、不改整体量级**——锚点取旧规则带宽的几何均值。第一版用 `median(d1)` 做锚点时
   踩了坑：Top-1 在 13/26 张图上变化、带宽中位从 33.55m 掉到 28.04m；改成几何均值锚点后降到 10/26、中位回到 35.62m。
2. **连续 + 不饱和**——用 k 近邻距离替代格内计数，消除"跨格边界跳变"与小组退化；调制在 `[h_lo, h_hi]`
   内做几何伸缩，不会被硬钳成常数。
3. **方向可配**——`β>0` 越密→带宽越大（**本项目既有语义，默认**）；`β=0` 退化为固定带宽（A/B 对照）；
   `β<0` 即教科书 **Abramson** 方向（越密→带宽越小）。
   > ⚠️ `test_scene_heatmaps.py` 有一条测试把"越密→越宽"锁成了契约。把默认改成 Abramson 方向会
   > **破测试且改变产品观感**，必须是有意识的产品决策，不能当静默的"算法升级"。

### 4.3 配置与回退
```json
"adaptiveBandwidth": { "method": "knn-density", "neighbourK": 4, "densityExponent": 1.0 }
```
- `method = "legacy"` → 完全回到旧算法。**已实测：legacy 模式与升级前逐点一致（Top-1 变化 0/26、
  覆盖占比完全相同）**，所以回退安全。
- `ALGORITHM_VERSION` 已 bump 到 `sakde-campus-9-knn-adaptive-bandwidth`；改该文件或 `scene_heatmaps.py`
  都会变更 `fingerprint`，**必须重建缓存**，否则 `/api/heatmaps` 回"缓存已过期"。

### 4.4 实测对比（升级前 → 升级后）
| 指标 | 升级前 | 升级后 | 判读 |
|---|---|---|---|
| 组内带宽跨度中位 | 5.85 m | **25.05 m** | 自适应项真正生效（4.3×） |
| 带宽量级中位 | 33.55 m | 35.62 m | 量级保持（+6%） |
| 有效格覆盖占比 | 0.5817 | **0.5817** | 完全不变 |
| 场景区分度（相关系数均值） | 0.687 | **0.686** | 无退化，略优 |
| 场景覆盖重叠（Jaccard 均值） | 0.891 | **0.891** | 完全一致 |
| 平均绝对差 | 0.156 | 0.154 | 略优 |
| Top-1 点位 | — | 10/26 变化 | 场形状变化的预期结果，**无标注无法判定孰优** |

> **结论口径**：能证明的是"**修好了失效机制、没有制造回归**"（覆盖不变、场景区分度不降、旧模式可逐点复现）；
> **不能证明"推荐更准"**——那需要人工标注（见 `docs/FEATURE_FEASIBILITY.md` §9）。

---

## 5. 采集侧出站代理修复（高德 `ProxyError`）

### 5.1 现象与根因
一次采集里 13 个关键词 × 3 次重试全部失败：
`AMap keyword '...' page 1: failed after 3 attempt(s): ProxyError; ...; failed result not cached`，
看起来像"高德查不到数据"。

实测根因：Windows 注册表里配了系统代理（`ProxyEnable=1`、`ProxyServer=127.0.0.1:7897`），
`requests` 会读取它；而 `harvest.py` 的 `requests.get()` **没有传 `proxies`** → 高德（国内域名）走该代理 → 失败。
**大模型调用没受影响**，因为 `llm_config.py` 显式传了 `{'http': None, 'https': None}` 绕开系统代理。

> ⚠️ **踩坑点**：表示"直连"**不能**用 `proxies={}`——`requests` 会用 `setdefault` 把系统代理补回来。
> 必须显式写成 `{'http': None, 'https': None}`。

### 5.2 修法
新增 `tools/campus_generator/outbound.py`：
- `direct_proxies()`：**默认直连**，用于高德（可用 `AMAP_HTTP_PROXY` 覆盖）；
- `system_proxies()`：**默认沿用系统设置**，用于公开网页与境外搜索（Tavily / Brave，可用 `WEB_HTTP_PROXY` 覆盖）。

`harvest.py`（高德采集、公开网页抓取）与 `web_search.py`（Tavily / Brave）全部接上；
新增 `tools/campus_generator/test_outbound.py`（**9 例**，含"真实调用点确实传了 proxies"的断言）守住契约；
`.env.example` 补上两个变量的说明。

### 5.3 想启用"自动搜索公开网页"，`.env` 需要补这几行
本机 `.env` 是早于该功能创建的，**没有** `TAVILY_API_KEY`（它只存在于 `.env.example`）。追加：
```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-你的真实密钥
```
另注：旧任务里持久化的 `ProxyError` 提示不会自动消失，**新建一个采集任务**才会是干净的。

---

## 6. 测试与工程基线

| 项 | 内容 |
|---|---|
| 依赖锁定 | `requirements.txt` 按实测版本 pin（不再 `>=`）；新增 `requirements-dev.txt`（pytest/ruff/black）与 `requirements-ci.txt`（CI 用，**不含** sentence-transformers/torch） |
| 静态检查 | `pyproject.toml` 配 `ruff`：`select = ["F", "E9"]`（只查未定义名、未使用变量、语法级问题；行宽 120），测试文件豁免 `F401/F811`。规则集**刻意保守**——先接上 CI，等清理完成再逐步扩大 |
| CI | `.github/workflows/ci.yml`：push/PR 跑 `python -m ruff check .` + `python -m pytest`（全量），与既有 `security-check.yml` 并行 |
| 新增测试文件 | `test_api_auth` 18 / `test_api_userdata` 18 / `test_api_campus_build` 8 / `test_chat_vision` 21 / `test_outbound` 9 = **74 个用例** |
| 既有测试扩充 | `test_scene_heatmaps` **+7 例**（自适应带宽：方向保持 / 教科书方向 / β=0 退化 / 重复坐标 / 未知 method 回退 / 非法配置被拒 / 尺度保持） |
| 全仓回归 | **400 passed / 1 skipped**（跳过项为 Windows 无符号链接权限） |
| 前端 | `npx vue-tsc --noEmit` → 0 错误 |
| 清理 | 删除游离日志与空 `build/`；`.gitignore` 补 `.pytest_cache/`、`.ruff_cache/`、`.codebuddy/`、`.playwright-cli/` |

---

## 7. 文档与元信息纠偏

### 7.1 两处**失实结论**已更正
| 原文档说法 | 事实 | 处理 |
|---|---|---|
| "`tools/campus_generator/` 未出现在当前仓库、`git stash` 为空、无法恢复" | 该目录**完整存在**：20 个 `.py`（15 个模块 + 5 个测试文件）+ `CAMPUS_GENERATOR.md`。真正缺的只是 `campuses/profiles/*.json` 这类**档案文件** | 已改写 `PROJECT_STATUS.md` §八 与 `HANDOVER.md` §7.2 |
| "嵌入检索在本机 `SEMANTIC_ENABLED=true` 时段错误崩溃 `0xC0000005`，是 reranker 的硬前置" | **误报**。复测：`import OK → load OK → encode OK, dim = 512`；嵌入模式 `passed=50/50 accuracy=100.0%`、**退出码 0** | 已在 `docs/FEATURE_FEASIBILITY.md` §3/§9 标注勘误并调整依赖链 |

> 顺带发现**更值得注意的真相**：现有 50 例评测对**规则路径与嵌入路径都是满分**，
> 区分不出两套后端 —— 它无法度量任何检索侧优化。§9 的前置因此从"修可运行性"改为"**让评测集有区分度**"。

### 7.2 其它
- 新增 [`docs/FEATURE_FEASIBILITY.md`](docs/FEATURE_FEASIBILITY.md)：**8 项候选功能**（§2~§9）
  + **4 项中长期升级方向**（§12）的可行性、安全风险、成本与优先级，结论均由实测数据支撑。
- `index.html`：`lang` 改 `zh-CN`、标题改 **ECNU·校园可视可答系统**、favicon 换 `ecnu_logo.png`、
  补 `description` 与 `theme-color`。
- `.env.example` 补出站代理与请求体上限说明。

---

## 8. 合规边界（重要，请勿误改）

- 植物认养人的**姓名 / 单位 / 寄语**（`data/all_trees*.json`、`webapp/backend/campus_pois.json`、
  `visualization/charts/sankey/data_for_sankey.json`、`webapp/frontend/src/assets/emotion_analysis.json`）
  是**经本人同意**在华东师范大学小程序「芳秾」及官网上公开的内容。
- **必须原样保留，严禁删改、脱敏或移出版本库。**
- `scripts/sanitize_public_data.py` **不应**作为常规清理步骤执行。
  （本轮曾误按"专有数据不可分发"理解运行过，**已全部回滚还原并逐项校验**，最终 `git status` 对这些路径为空。）
- 另注：**密钥脱敏**（`.env`、高德 Key 占位符等）与此无关，仍然必须做。

---

## 9. 验证结果汇总

| 检查 | 结果 |
|---|---|
| 全仓 `python -m pytest -q` | **400 passed / 1 skipped** |
| `python -m ruff check .` | All checks passed |
| `npx vue-tsc --noEmit` | 0 错误 |
| `python scripts/check_publication.py` | passed |
| 后端启动 | `cd webapp/backend && python server.py` → `/api/health` 返回 `{"database":"ok","status":"ok","vision":false}`；加载 3122/3122 条 POI、6/6 场景配置 |
| 热图缓存一致性 | 重建后 **26 张地图，与 manifest 哈希 100% 一致、0 过期残留** |
| 出图正确性 | 闵行/散步 98×113、交大闵行/散步 134×121、普陀/拍照 60×84；交大赏花按预期降级（无植物点位） |
| 带宽回退路径 | `method="legacy"` 与升级前**逐点一致**（Top-1 变化 0/26） |
| 界面 | 问答页场景热图与生成页临时热图均实测正常，图例齐全 |
| 认养人数据 | `git status` 对 `data/`、`campus_pois.json`、`assets/` **全为空** —— 一行未动 |

---

## 10. 未完成 / 下一步

1. **浏览器端三校区端到端验收**仍未做（本轮只验了问答页与生成页的关键路径）。
2. 升级方向的两个**硬前置**（详见 `docs/FEATURE_FEASIBILITY.md` §12.5）：
   - **边界数据**：`campuses.json` 三校区只有中心点与信任半径，缺 `boundary` 矢量；
   - **多校 schema 对齐**：`profile.runtime_config()` 与 `campuses.json` 的 `schools` / `school` 外键 / `brand`
     未对齐前，生成器不能重跑资产生成。
3. **评测基线要有区分度**：现有 50 例对规则与嵌入两种后端都是满分，需加难例 + 补热图人工标注 +
   引入排序指标（Recall@k / MRR / nDCG），否则任何检索侧优化都无法度量。
4. 需要向校方/公开 GIS 确认矢量边界来源，并确认在小程序/扫码页**二次展示认养人信息**的同意范围。

---

## 11. 附：算法升级评估（仅评估，未实现）

除本轮已落地的带宽升级外，对全仓算法点做了逐项评估，结论如下（按性价比排序）。

### 11.1 值得做

| 项 | 现状证据 | 升级方向 | 结论 |
|---|---|---|---|
| **检索融合** | `retrieval_engine` 的"混合"只是 `max(lexical, embedding)` 取大；全线性扫描 | BM25（`rank_bm25` 纯 Python）+ 稠密 → **RRF 融合** → Cross-Encoder 精排 Top-N | **性价比最高**：零重依赖、可立即用现有 50 例跑回归验证不倒退 |
| **排序权重校准** | `rank_campus_places` 用**手工常量**（12/8/7/2、28/18/16/10…） | 用标注数据做网格搜索/贝叶斯优化 | 零新依赖、可解释、可复现 |
| **地名模糊匹配** | `place_extraction` 只有正则 + NFKC 归一，**无编辑距离/拼音/别名纠错** | 拼音 + 编辑距离 + 别名表；重方案可用 MGeo（阿里开源中文地址相似度模型，需 GPU/ModelScope） | 轻量方案先做，MGeo 作为可选增强 |
| **行程全局优化** | `itinerary.plan_day` 是**贪心最近邻** | 小规模（≤12 站）用 2-opt / Held-Karp，并加入**时间窗/开放时间**约束 | 改动小、可解释；时间窗是真实痛点 |

### 11.2 明确**不该做**（避免过度工程）

| 项 | 为什么不做 |
|---|---|
| ANN 索引（HNSW / IVF / FAISS） | 3122 条 POI 的稠密点积已是**毫秒级**，加索引只增复杂度 |
| LTR / LambdaMART | 小样本（50 例）下不划算，且不可解释 |
| BGE-M3 / Qwen3-Reranker-4B / MGeo 作为默认依赖 | 本机是**纯 CPU**（现在本地就设着 `SEMANTIC_ENABLED=false` 省内存）；这类模型必须做成"可选安装、缺失即降级"，且需先用带区分度的评测证明收益 |
| 教科书 Abramson 方向作为默认 | 会翻转项目既有的"越密→越宽"语义、破锁定测试、改变产品观感（现以 `β` 参数提供，可按需切换） |

> 联网调研要点（2026-10-10 检索）：RAG 当前主流配方是 **BM25 + 稠密 → RRF → Cross-Encoder 精排**；
> MMTEB-R 上 Qwen3-Reranker-4B 72.74 vs bge-reranker-v2-m3 58.36，但 CPU 上跑 reranker 慢到不可用；
> BGE-M3 三合一（dense/sparse/ColBERT）在 CPU 上可用但比 GPU 慢 2–3 倍；
> KDE 带宽的学术主流是**数据驱动/自适应带宽**。厂商给出的"召回率 +19 点"等数字需打折看待。

---

## 12. 附：本轮改动文件清单

**新增**
```
.github/workflows/ci.yml                      pyproject.toml
webapp/backend/api/{campus_build,chat,userdata,auth}_api.py
webapp/backend/{flask_app,runtime,llm_config,poi_data,retrieval_engine,auth_db,paths}.py
webapp/backend/{requirements-ci,requirements-dev}.txt
webapp/backend/test_api_{auth,userdata,campus_build}.py
webapp/backend/test_chat_vision.py
webapp/frontend/src/components/imageAttach.ts
webapp/frontend/src/components/heatmapRender.ts
tools/campus_generator/outbound.py
tools/campus_generator/test_outbound.py
docs/FEATURE_FEASIBILITY.md                   PROJECT_UPDATE_2026-10-10.md（本文）
```

**修改**
```
PROJECT_STATUS.md  HANDOVER.md  .env.example  .gitignore
webapp/backend/server.py                     （4544 → 91 行门面）
webapp/backend/scene_heatmaps.py             （k 近邻自适应带宽 + 版本 bump）
webapp/backend/heatmap_config.json           （cellMeters 20→12、adaptiveBandwidth）
webapp/backend/heatmap_cache/*                （重建 26 张；删除 4 个不可达旧缓存）
webapp/backend/test_scene_heatmaps.py        （+7 例）
webapp/backend/place_extraction.py  webapp/backend/test_llm_config.py
webapp/backend/requirements.txt
tools/campus_generator/{harvest,discovery,web_search}.py
webapp/frontend/index.html
webapp/frontend/src/views/{chatbot,campusBuild}.vue
webapp/frontend/src/components/SceneHeatmapPanel.vue
webapp/frontend/src/{auto-imports,components}.d.ts
```
