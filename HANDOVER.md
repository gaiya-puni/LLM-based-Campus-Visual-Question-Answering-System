# 交接说明：用户共建数据链路 + 行程意图修复

> 面向下一位接手的队员。本文覆盖 **test/dev 上 `a1ad869` 这一个提交**（相对上一状态 `85e1658`）的全部改动、
> 设计思路、已知坑与未来工作（含"把项目做成智能体"的路线）。
> 组件实现细节另见 `webapp/backend/USER_DATA_PIPELINE.md`（用户共建）与 `webapp/backend/ITINERARY_PLANNING.md`（行程）。

---

## 0. 一句话现状

`test/dev` 现在 = **原有能力**（智能问答 / 场景热图 / 一日行程 / 植物图鉴）
+ **本次新增**：用户共建数据链路（消息评价、地点上报、未收录地点自动抽取、审核页与导出）
+ **本次修复**：行程问答被抢走的意图问题、4 个健壮性缺陷（NaN 坐标等）。

拿到分支后建议按 [§6 上手清单](#6-上手清单) 跑一遍再动手。

---

## 1. 分支与提交状态

| 项 | 值 |
|---|---|
| 本次提交 | `a1ad869` `feat: 用户共建数据链路（反馈 + 未收录地点抽取 + 审核导出）`（55 文件，+7821/−35） |
| 已推送 | `origin/test/dev` = `a1ad869` ✔ |
| 上一状态 | `85e1658`（LLM_PROVIDER 切换） |
| 工作区分支 | `wh_test`（= `85e1658`，**上面还有未提交的队友工作**，见 §5.2） |

拉取：

```powershell
git fetch origin
git checkout test/dev
git pull
```

> 注意：`test/dev` 与 `wh_test` 目前指向同一个提交历史的不同位置，`wh_test` 是"开发中转站"。
> 提交前请确认自己在哪条分支上（`git rev-parse --abbrev-ref HEAD`）。

---

## 2. 第一部分：用户共建数据链路（本次主体）

### 2.1 要解决的问题

系统对"没收录的东西"原本是**直接放弃**的：命中硬编码的"无数据源"清单（咖啡、奶茶、充电桩、校医院…）
就在 `/api/chat` 里回一句"暂无可靠的××数据"然后结束（`server.py` 的 `_UNSUPPORTED_DATA_PATTERNS` →
`unsupported_data_query` 早返回分支）。**用户所有"我们缺什么数据"的信号都被丢掉了**——而这些恰好是最有价值的需求清单。

因此做了两条路径：

- **直接提供**：用户对回答一键评价（有用/没用+理由，可撤回）；用户主动上报一个没收到的地点（表单+地图选点）。
- **间接提供**：用户问到地名而系统答不上来时，**静默**从问句里抽出可疑地名，汇总为待确认清单。

### 2.2 数据流

```
用户问句 /api/chat ──► 未收录判定 ─┬─ 采集点① "无数据类别"分支
                                   └─ 采集点② 问句带方位意图且规则抽到干净地名
                                          │
              place_extraction（通名规则优先 → 大模型兜底 batch/inline）
                                          │
                    ┌─────────────────────┴──────────────────┐
              命中候选                                 判不出来
                    │                                        │
        pending_places.json ◄── 聚合去重 ◄── extracted_places.jsonl   unresolved_queries.jsonl
                    ▲                                        ▲
   评价 ─► ratings.jsonl          上报 ─► place_reports.jsonl   │（审核页"用大模型补齐"批量研判 → llm_scan.jsonl 记账）
                    │
             审核页 /place-review（通过 / 驳回 / 退回）──► review_state.json（人工结论的唯一真相）
                    │
             approved_places.json（字段对齐 POI 契约）──► tools/campus_generator/normalize.py 人工确认 ──► *_pois.json
```

### 2.3 关键设计决策（都踩过坑，别改）

| 决策 | 理由 |
|---|---|
| **事件流 + 派生清单 + 独立审核状态** | `*.jsonl` 是事实来源、`pending_places.json` 可随时重建；审核结论放在 `review_state.json`，**重建不会丢审核结果**（有单测锁定）。若把结论写进清单，一次重建就全丢。 |
| **采集点是旁路** | 全程 `try/except`，失败只写一行日志（不含用户原文），**绝不改变回答内容、不增加延迟**。有单测断言"采集前后回答逐字一致"。 |
| **默认 batch 模式（`USERDATA_LLM_EXTRACT=batch`）** | 在线零额外大模型调用、零延迟；判不出来的问句进队列，由审核页"用大模型补齐"批量研判（带 `llm_scan.jsonl` 台账，不重复付费）。`inline` 模式才会在未收录分支当场判一次（+最多 6s）。 |
| **采集闸门 = 方位词 或 疑问口吻** | 否则"今天天气真好呀"也会因为命中"天气"这个无数据类别被收进清单。 |
| **采集点② 的判据是"抽到干净地名"，不是"没有点位"** | 项目对几乎任何问句都会给兜底点位（实测任意问句都可能拿到 93 个打点），"没有点位"这个信号实际失效。 |
| **上报校区由坐标判定（`campus_config.campus_at`）** | 不采信前端传来的校区名：界面上选普陀、点在闵行会被记错。落在所有校区信任半径外的点直接 400。 |
| **只进待确认，绝不自动写 POI** | 错一个点位会被地图打点、推荐、热图三处同时放大。并入仍走离线人工确认流程。 |
| **审核接口未配密钥即 403（而不是放行）** | 本地工具最常见的坑是"忘了配"，那会让审核写操作裸奔。 |
| **`allow_nan=False` 落盘** | 清单是给浏览器读的；`NaN`/`Infinity` 是**非法 JSON**，`JSON.parse` 直接抛错、审核页整页打不开（详见 §4）。 |

### 2.4 新增文件与接口

**后端**（`webapp/backend/`）

| 文件 | 作用 |
|---|---|
| `userdata_store.py` | 落盘层：JSONL 追加写（线程安全）、清单原子写、脱敏、聚合去重、导出（POI 契约 + Markdown） |
| `place_extraction.py` | 抽取层：60+ 地理通名规则（左侧 6 字窗口、排除已收录 POI/校区停用词/泛指词）+ 大模型兜底解析（回调注入，可离线测） |
| `USER_DATA_PIPELINE.md` | 本模块设计文档（数据流、契约、脱敏口径、验证结果、修复记录） |
| `test_userdata_store.py` / `test_place_extraction.py` / `test_chat_usercapture.py` | 28 / 18 / 27 个用例 |
| `userdata/` | 运行时数据目录（**已被 `.gitignore` 忽略**，只提交 `README.md`） |

**接口**（`server.py`）

| 方法 | 路径 | 限流 | 审核态 | 说明 |
|---|---|---|---|---|
| POST | `/api/feedback` | 20/min | – | `{messageId, rating: up\|down\|none, reason?, query?, messageEngine?, campus?}`；缺 `messageId` → 400 |
| POST | `/api/place_report` | 10/min | – | `{name, lng, lat, category?, note?, querySnippet?}`；名称 2–20 字、坐标必须有限且在校区内 |
| GET | `/api/userdata/pending` | 60/min | ✔ | 过滤（校区/状态/来源/名称）+ 分页 + 计数与统计 |
| POST | `/api/userdata/review` | 30/min | ✔ | `{id, status: pending\|approved\|rejected, note?}` |
| POST | `/api/userdata/extract` | 10/min | ✔ | 批量大模型研判（自动回填原问句的校区） |
| GET | `/api/userdata/export` | 10/min | ✔ | 写 `approved_places.json`（缺坐标条目跳过并报数）+ `pending_places.md` |

**前端**（`webapp/frontend/src/`）

| 文件 | 状态 |
|---|---|
| `api/userdata.ts`、`components/MessageFeedback.vue`、`components/PlaceReportDialog.vue`、`components/PlaceReviewDrawer.vue`、`views/placeReview.vue` | 新增（已入库） |
| `router/index.ts`（新增 `/place-review`）、`views/sidebar.vue`（新增"数据审核"菜单） | 已入库 |
| `views/chatbot.vue`（气泡内的评价按钮、上报引导卡） | **未入库**，原因见 §5.1 |

### 2.5 隐私与安全

- **脱敏**（`userdata_store.mask_sensitive`）：手机号 → `138****5678`；邮箱保留首字符与域名；9 位以上连续数字（学号/身份证）→ 首 3 末 2；"我叫X/姓名：X" 保守处理。**其余文本原样保留**——过度脱敏会毁掉"用户在问哪个地点"这个核心价值。
- 问句样本截断 200 字，理由/备注 300 字，名称 40 字。
- 数据目录不入库（`.gitignore`：`webapp/backend/userdata/*` + 保留 `README.md`）；日志不含用户原文与密钥。
- 审核门禁：请求头 `X-Review-Token` 必须等于本机 `.env` 的 `USERDATA_REVIEW_TOKEN`（**至少 16 位**），用 `hmac.compare_digest` 比较。

### 2.6 验证结果

| 检查 | 结果 |
|---|---|
| 主工作区全量后端测试 | **206 用例通过**（含既有 11 个测试模块） |
| 提交版在独立 worktree 实测 | **168 用例通过** + `npx vue-tsc --noEmit` 0 错误（证明提交内容自洽、不依赖他人未提交代码） |
| `scripts/check_publication.py` | 通过（无密钥入库） |
| 浏览器端到端（真实前后端 + 真实大模型） | 评价落盘 → 上报进清单（`source=user`）→ 静默抽取（`source=rule`）→ 审核页门禁 403 → 填密钥载入 → 通过（行原地变色）→ 大模型补齐（新增 `source=llm`）→ 导出（`已导出 1 条，跳过 1 条缺坐标`）全流程可用 |

### 2.7 已知限制

1. 规则侧召回保守（要求名称≥3 字），两字短名（"北门"）与口语化指代（"那个卖文创的小店"）依赖 batch 大模型兜底；离线 CLI 尚未封装，目前靠审核页按钮触发。
2. 每次上报/审核都全量重放事件流（O(事件数)）；用户量级毫秒级，超过万条应改增量索引或按天分片。
3. `ratings.jsonl` 只有事件流，暂无满意度看板；同一 `messageId` 多条事件以**最新一条**为当前评价（撤回记 `none`）。
4. 审核页只有单一共享密钥，无账号/角色；适合本机或内网小范围使用。

---

## 3. 第二部分：行程/导航问题修复

### 3.1 现象与成因链

用户反馈：在问答页问 **"请从我看到的这个位置出发，请你为我规划一下普陀校区的参观路线"**，助手答
"规划路线我帮不上忙"。排查结论是**意图被抢走**，不是能力缺失：

1. `itinerary.is_itinerary_query` 其实判对了（命中强关键词"参观路线"）；
2. 但同一句里"普陀**校区**"+ 泛词"**位置**/**路线**"让 `_looks_like_institution_location_query` 返回 True，
   请求被当成"机构位置查询"；
3. 行程分支与热图分支的守卫都带 `not institution_location_requested` → 两条链路同时被跳过；
4. 落到普通推荐链路后，`rank_campus_places` 对 `institution_location_requested` 是**主动放弃**的（直接返回空），
   于是没有点位、没有推荐上下文；
5. 模型手上只剩系统提示里的"上下文里没有就说没有""不要编造路线"，只能如实回绝。

### 3.2 修法（都在 `server.py`）

| 改动 | 说明 |
|---|---|
| `_looks_like_institution_location_query` 收窄泛词 | 不再认孤立的"这个位置""参观路线"，只认确切问法：`哪里 / 在哪 / 什么位置 / 位置在哪 / 的位置 / 地址 / 坐标 / 怎么去 / 导航`。避免"回答不了任何东西"的空点位链路被误触发 |
| 新增 `itinerary_intent` 统一口径 | `chat()` 里只算一次 `is_itinerary_query`；**针尖分支**加 `not itinerary_intent`（"怎么安排一天"让给行程，"眼前这个点是啥"仍归针尖）；**行程分支**只认 `itinerary_intent`，不再叠加机构位置标志 |
| 新增 `_itinerary_origin` | 行程起点：用户定位 → **地图针尖（`viewCenter`）** → None（从上午首个候选起步）。针尖必须经 `campus_config.campus_at` 确认**落在行程校区内**才采用，否则会拿另一个校区的坐标当起点、第一站莫名为远 |
| `_INDICATIVE_QUERY_PATTERN` 补词 | 增加"我看到的这个位置 / 这个位置在哪 / 这个位置是 / 这个位置有什么 / 地图上这个位置"等**明确问法**。**故意不收裸词"这个位置"**：针尖分支排在场景分支之前，裸词会让"这个位置适合散步吗"被针尖抢走 |

### 3.3 影响面与测试

- `/api/chat` 响应契约**只增不改**；热图、推荐、针尖三条链路的既有行为不变。
- 新增 `test_chat_itinerary.py`（11 用例：路由优先级、针尖起点归属、跨校区拒绝、脏坐标、模式开关；大模型用桩、离线可跑），
  `test_itinerary.py` 35 用例、`test_chat_needle.py` 13 用例全绿。
- 设计文档 `webapp/backend/ITINERARY_PLANNING.md` 已同步（不变量第 7 条、编排起点、验证结果、修复记录 F1/F2）。

---

## 4. 第三部分：健壮性修复（评审发现）

| 问题 | 影响 | 修复 |
|---|---|---|
| `POST /api/place_report` 接受 `NaN` 坐标 | `float('nan')` 让距离比较恒为假、**骗过校区判定**；NaN 写进清单变非法 JSON → 浏览器 `JSON.parse` 抛错、**审核页整页打不开**（一个构造请求即可瘫痪该页） | 接口层 `math.isfinite` 拒收（400）；落盘层 `_as_float` 拒收非有限值；`append_event`/`_atomic_write_json` 加 `allow_nan=False` 双保险 |
| `POST /api/feedback` 缺 `messageId` 照收 | 评价无法追溯到任何回答，纯脏数据 | 缺 `messageId` → 400 |
| `userLocation` 带 `NaN`/越界坐标 | `/api/chat` **500**（`round(nan)` 抛 `ValueError`）。这是**既有崩溃点**，行程分支同样会吃到 | `_ranking_location_from_request` 与 `viewCenter` 同口径清洗：非有限或超出中国量级一律丢弃，按"没有定位"继续作答 |
| 并发写事件流无回归 | 线程安全只是文档承诺 | 补 8×20 线程并发写 + 并发上报用例，逐行 `json.loads` 校验无半截行 |

> 教训：**测试隔离也要注意**。`/api/place_report` 限流是 10 次/分钟，限流桶按 (地址, 端点) 跨用例累积，
> 测试里不清桶会让后面的用例播种请求被 429 打回（已在 `_UserdataTestCase.setUp` 里 `_rate_limit_buckets.clear()`）。
> 另外新增了 `USERDATA_CAPTURE=0` 总开关，给 5 个会打 `/api/chat` 的既有测试模块加了隔离，避免用例问句写进真实数据目录。

---

## 5. 第四部分：未完成事项与坑（重要）

### 5.1 `webapp/frontend/src/views/chatbot.vue` 没进这次提交

`chatbot.vue` 的 diff 在**行级**把他人改动和本次改动拧在一起（例如 `sendMessage` 那个 hunk 里既有本次新增的
`id/createdAt/query/engine/unsupported` 字段，也有他人的 `images`/`attachments` 图片输入与 `campusPinned` 多校区逻辑）。
硬挑会二选一地出问题：要么把他人的图片输入/多校区代码带进 `test/dev`，要么让本次的消息字段丢失
（那样评价按钮因为缺 `messageId` 会被后端 400）。

**因此分支上的现状**：

- ✅ 生效：未收录问句的自动采集、6 个接口、**数据审核页**（门禁/筛选/通过驳回/大模型补齐/导出）与侧边栏入口；
- ⚠️ 暂缺：对话气泡里的 👍/👎 与"去补充"上报入口（组件文件已在分支上，只差在聊天页挂上去）。

**两条收尾路径**（任选其一）：

1. 等图片输入/多校区前端一并入库时，`chatbot.vue` **整体提交**即可（本次的改动会随之进去）；
2. 若那批工作迟迟不入库，可把本次改动**行级移植**到 HEAD 版 `chatbot.vue` 上单独提交（约 150 行，注意
   `MessageFeedback` 需要 `msg.id` 非空——后端已强制校验）。

### 5.2 工作区里还有他人的未提交工作（**不要误提交**）

`wh_test` 工作区当前有约 100 项未提交改动，其中**不是**本次范围的包括：

- `webapp/backend/server.py` 里的**视觉多模态**（`resolve_vision`、`attachments`、`photo_pois` 图片线索打点）、
  **场景聚类**（`_cluster_waterfront_label`、`_scene_cluster_fit`、`_feature_water`）、**多校区/品牌化**（`_campus_filler_pattern`、
  机构位置提示词、`app.config` 上传上限）等 hunk；
- `webapp/backend/scene_heatmaps.py`、`build_scene_heatmaps.py`、`convert_locations.py`、`heatmap_config.json`；
- 前端 `views/map.vue`、`predict.vue`、`topbar.vue`、`components/SceneHeatmapPanel.vue`、`main.ts`、`index.html`、
  生成的 `auto-imports.d.ts` / `components.d.ts`、`utils/imageAttach.ts`；
- `campuses/harvest/sjtu_minhang/*`（生成流水线产物）。

提交时**用 `git add <具体文件>`，不要 `git add -A`**；若与他人共用一个文件（如 `server.py`），
可用"反向还原非自己 hunk"的办法生成部分提交（本次就是这么做的，见 §7 备注）。

### 5.3 其他注意点

- `test_chat_vision.py`、`test_campus_config.py` 仍是未跟踪状态；前者依赖尚未入库的视觉接口，
  **在本分支上跑会失败**，别误加进测试套件。
- 本机 `.env` 需要配置 `USERDATA_REVIEW_TOKEN`（≥16 位，值不要写进任何文档/代码）才能使用审核页；
  该变量与 `USERDATA_CAPTURE`、`USERDATA_LLM_EXTRACT`、`USERDATA_DIR` 的说明已补进 `.env.example`。
- 演示时保持 `SEMANTIC_ENABLED=false`（语义索引已过期，加载句向量模型会让后端挂起/退出）。
- 系统代理会让后端调不通大模型（表现为整站"AI 服务请求失败"），确需代理用 `LLM_HTTP_PROXY` 显式配置。

---

## 6. 上手清单

```powershell
# 1) 后端（端口 5000）
cd webapp/backend
pip install -r requirements.txt
python server.py

# 2) 前端（端口 80）
cd webapp/frontend
npm install
npm run dev

# 3) 打开：问答页 http://localhost/#/chatbot    审核页 http://localhost/#/place-review
#    审核页密钥 = 本机 .env 的 USERDATA_REVIEW_TOKEN
```

改动后跑这些（本机实测全绿）：

```powershell
cd webapp/backend
$env:SEMANTIC_ENABLED='false'
python -m unittest test_campus_config test_chat_itinerary test_chat_needle test_chat_usercapture `
    test_itinerary test_llm_config test_place_extraction test_scene_heatmaps test_security test_userdata_store
python test_recommendation_v2.py          # 脚本式回归
cd ../frontend; npx vue-tsc --noEmit      # 类型检查
cd ../..; python scripts/check_publication.py   # 提交前脱敏检查（务必）
```

---

## 7. 未来改进工作：把项目做成"智能体"（校园生成流水线）

### 7.1 目标与验收标准

**目标**：把本系统当模板，给定一所新学校 + 一份档案，自动完成
**"采集 → 归一质检 → 资产重建 → 可运行实例"**，产出与现有系统**能力与观感一致**的新校区实例；
人工只负责**审核**，不再负责"找点"。

**验收标准**（建议写成脚本，作为"完成"的定义）：

1. 一条命令跑完一个校区，失败可断点续采；
2. 新校区具备四项能力：地图打点、智能问答、场景热图、一日行程；
3. 既有校区**零回归**（POI 不被覆盖、热图哈希仍校验通过、测试全绿）；
4. 产出"待确认清单 + 采集报告"，条目带来源与置信度；
5. 生成器自身有单测与文档。

### 7.2 现状盘点（截至本次提交）

**已完成**（`tools/campus_generator/`，交大闵行试点已手工端到端跑通）：

| 环节 | 文件 | 状态 |
|---|---|---|
| 校区档案 + 校验 | `profile.py`、`campuses/profiles/sjtu.json` | 可用（`validate_profile`、`runtime_config`、`reserved_symbols`）；能拦住"校区名/slug 与既有校区撞车" |
| 高德采集 | `harvest_amap.py` | 可用（网格周边搜索 + 关键字补漏、分页限速重试、**断点续采** `responses.jsonl`、跨校区过滤、采集报告）。实测 58 请求 0 失败、648 候选、校内 100 条 |
| 公开资料 | `harvest_public.py` | 代码可用；**检索环节仍靠人工/agent skill**（wechat-article-search / 网络检索），产出带 URL 的来源清单，再由大模型抽成带来源与置信度的候选 |
| 归一质检 | `normalize.py`、`validate.py` | 可用（映射 POI 契约、`write_pending` 待确认清单、只读质检）。实测 648 → 80 条正式 POI + 85 条待确认，质检 80 通过 / 0 未通过 |
| 资产重建 | `build_assets.py` + `build_semantic_index.py` + `build_scene_heatmaps.py` | 基本配置驱动（`*_pois.json` 自动发现、按 `heatmap_config.json` 遍历），含"改坏既有校区"的缓存快照保护 |

**缺失**（按"还差什么才能一键产出"排序）：

1. **统一入口 `cli.py` 不存在**：现需手工按序跑 5 个脚本并正确传 `--profile/--against/--harvest-dir`，无分步、续跑、报告聚合。
2. **多校 schema 断层（最危险）**：`profile.runtime_config()` 只产出单数 `school` + `campuses`，不含 `schools` 表、每校区
   `school` 外键与 `brand`；而现行 `campuses.json` 有这些字段，且 `campus_config.check_consistency` **要求**多校下每校区
   必须有 `school`。**直接重跑 `build_assets.py` 会丢字段并挂一致性检查**。
3. **`heatmap_config.json` 的新校区条目仍要手工加**（`slug/center/auditRadiusMeters`），与 `build_assets.py` 的文档承诺不符。
4. **生成器零测试**（全仓没有针对 `tools/campus_generator/*` 的单测）。
5. **缺 `CAMPUS_GENERATOR.md`** 与实例启动脚本；`.codebuddy/plans/campus-system-generator_e5cbd530(未完成).md` 里
   `e2e-sjtu`、`package-cli` 仍为 pending。
6. **植物数据降级未单独验证**：新学校没有 `data/all_trees.json`（学校专有、不可分发），应确认图鉴/寄语/预测按"无内容"降级，
   只保留"打点 + 问答 + 热图 + 行程"。

### 7.3 分阶段路线（建议直接照做）

**Phase 1 — 把"手工能跑"固化成"一条命令"（工程化收尾，工作量最大的是第 2 项）**

1. 写 `tools/campus_generator/cli.py`：`profile|validate|harvest|normalize|check|build|report` 子命令 + `--profile` 统一入口；
   每步可单独重跑（续采），最后输出一份汇总报告（采集多少、保留多少、待确认多少、跳过原因）。
2. **修 `runtime_config()` 的多校 schema**：补齐 `schools` 表、每校区 `school` 外键、`brand`，与 `campus_config.check_consistency`
   对齐；并加"生成前后 diff 既有校区"的保护测试。
3. `build_assets.py` 顺带生成 `heatmap_config.json` 条目（含 `auditRadiusMeters`），不再手工维护。
4. 补生成器单测：档案校验边界（非法 slug、撞名、坐标越界）、归一契约（必填字段、类别映射）、`build_assets` 幂等且不污染既有校区。
5. 写 `CAMPUS_GENERATOR.md`：从"零到新校区可运行"的分步说明 + 依赖的高德/大模型配额要求。

**Phase 2 — 数据来源自动化（减少人工找点）**

6. 把 `harvest_public.py` 的**检索环节**接成可调用流程（公众号检索技能/搜索 API），输出统一进"来源清单 → 大模型抽取 → 待确认"。
7. **把本次的用户共建数据接进同一条流水线**：审核页导出的 `approved_places.json` 已经是 POI 契约字段
   （`id/category/subCategory/name/locationName/lng/lat/text/tags`），可直接喂给 `normalize.py` 的人工确认入口——
   这样"用户上报 + 对话抽取 + 公开资料 + 高德采集"四条来源汇成一个待确认池。
8. 新增"校区接入验收脚本"：POI 数量/类别覆盖度、热图可用性（哈希校验 + 场景非空）、问答抽样回归（若干固定问句断言打点数量与引擎）。

**Phase 3 — 真正的"智能体"形态**

9. **单校试点做成"给学校名就能跑"**：档案半自动生成（大模型联网检索公开信息 → 填 `profile` 草稿 → 人工只审关键字段），
   随后自动串起 Phase 1 的 `cli.py`。
10. **审核即反馈**：把 Phase 2 的待确认池做成一个审核台（本次的 `/place-review` 已具备雏形：筛选/抽屉/通过驳回/导出），
   扩充为"来源管理 + 批量研判 + 并入 POI 前的影响预览（会新增/覆盖哪些点位）"。
11. **多校并存**：校区名与 slug **全局唯一**（华师大已有"闵行/minhang"，故交大闵行用"交大闵行/sjtu_minhang"）；
   植物数据缺失时的降级链路单独验收；热图缓存按 `{slug}_{场景}.json` 命名，避免互相覆盖。

### 7.4 与本次改动的衔接点

- `approved_places.json` → `tools/campus_generator/normalize.py`：**本次已经把"数据自增长"的入口打通**，
  Phase 2 只需把导出结果接进既有确认流程（不要绕开人工确认直接写 `*_pois.json`）。
- 用户上报的坐标判定用 `campus_config.campus_at`，新校区一登记即自动生效（校区配置单一来源）。
- 本次的"待确认清单"契约（`pending_places.json`）与生成流水线的 `pending_*.json` 是同一范式，可合并展示、**不要各自维护两套**。
- 别忘了：`webapp/backend/userdata/` 是**本机运行数据**（含用户问句），永远不入库；交接时不要把它拷进仓库。

### 7.5 风险与注意事项

1. **`.codebuddy/` 目录是项目数据，不要删除**；仓库里的 `scripts/check_publication.py` 必须在每次提交前跑。
2. `.env`、`*.env` 永不入库；密钥只在本机。高德要区分**浏览器 Key**（前端、限域名配额）与 **Web 服务 Key**（后端脚本）。
3. 植物相关数据（`data/all_trees.json`、`data/all_templates.json`）**属学校专有，不可对外分发**。
4. 高德与大模型都有配额：采集器必须限速 + 断点续采；批量大模型研判要带台账（避免重复付费），本次的 `llm_scan.jsonl` 就是范例。
5. 语义索引会随 POI/场景文件变化而 `stale`，重建（`build_semantic_index.py`）后再把 `SEMANTIC_ENABLED` 调回 `auto`。

---

## 附：本次提交（`a1ad869`）文件清单

```
.env.example                                    (改) 新增 USERDATA_* 配置说明
.gitignore                                      (改) 忽略 webapp/backend/userdata/*
PROJECT_STATUS.md                               (新) 项目进度说明（含本次功能与测试数字）
webapp/backend/USER_DATA_PIPELINE.md            (新) 用户共建设计文档
webapp/backend/userdata_store.py                (新) 落盘层
webapp/backend/place_extraction.py              (新) 抽取层
webapp/backend/server.py                        (改) 6 个接口 + 两处旁路采集 + 行程意图/起点修复 + 入参清洗
webapp/backend/campus_config.py / campuses.json (新) 校区配置单一来源（后端启动必需，此前从未入库）
webapp/backend/sjtu_minhang_pois.json           (新) 交大闵行 POI（新校区资产）
webapp/backend/heatmap_cache/**                 (改+新) 热图缓存与 manifest（含 sjtu_* 六份）
webapp/backend/test_userdata_store.py           (新) 28 用例
webapp/backend/test_place_extraction.py         (新) 18 用例
webapp/backend/test_chat_usercapture.py         (新) 27 用例
webapp/backend/test_chat_itinerary.py           (新) 11 用例
webapp/backend/test_chat_needle.py              (新) 13 用例（含 userLocation 清洗）
webapp/backend/test_itinerary.py                (改) 意图回归 +1
webapp/backend/test_recommendation_v2.py        (改) 泛词收窄回归 +1
webapp/backend/ITINERARY_PLANNING.md            (改) 行程设计文档同步
webapp/frontend/src/api/userdata.ts             (新)
webapp/frontend/src/campusConfig.ts             (新) 前端校区配置（此前从未入库）
webapp/frontend/src/components/MessageFeedback.vue      (新)
webapp/frontend/src/components/PlaceReportDialog.vue    (新)
webapp/frontend/src/components/PlaceReviewDrawer.vue    (新)
webapp/frontend/src/views/placeReview.vue               (新)
webapp/frontend/src/router/index.ts             (改) 注册 /place-review
webapp/frontend/src/views/sidebar.vue           (改) 新增"数据审核"入口
```
