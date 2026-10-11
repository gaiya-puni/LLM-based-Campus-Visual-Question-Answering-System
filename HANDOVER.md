# 交接说明：用户共建数据链路 + 行程意图修复

> 面向下一位接手的队员。本文覆盖 **test/dev 上 `a1ad869` 这一个提交**（相对上一状态 `85e1658`）的全部改动、
> 设计思路、已知坑与未来工作（含"把项目做成智能体"的路线）。
> 组件实现细节另见 `webapp/backend/USER_DATA_PIPELINE.md`（用户共建）与 `webapp/backend/ITINERARY_PLANNING.md`（行程）。

> **2026-09-28 接手补充**：三校区运行时与资产链已接通。热图构建和语义索引现在统一自动发现
> `*_pois.json`，缓存已按当前源码重建；语义索引含 3122 条 POI。交大闵行的拍照/散步/约会热图可用，
> 赏花因没有植物点位而明确降级，不影响其他场景。

> **2026-10-10 本轮改动（接手前必读）**
>
> 1. **`tools/campus_generator/` 其实一直在仓库里**（20 个 `.py`：15 个模块 + 5 个测试，另有
>    `CAMPUS_GENERATOR.md`）。本文 2026-09-28 那条"未出现在当前仓库、需向原同事取回"的结论**是错的**，
>    已更正；§7.2 的现状盘点也同步纠正（旧版把文件名写成了 `harvest_amap.py` / `build_assets.py` 等，
>    与仓库实际不符）。真正还缺的是 `campuses/profiles/sjtu.json` 这类**档案文件**（该目录不存在）。
> 2. **后端入口已模块化**：`server.py` 从 4544 行收敛为 **91 行门面**（只做导入、re-export、启动）。
>    业务在 `api/{campus_build,chat,userdata,auth}_api.py`；公共设施在 `flask_app.py`（app/配置/CORS/
>    限流/请求体超限 JSON 错误）、`runtime.py`（**门面解析器**，兼容 `python server.py` 与 `import server`）、
>    `llm_config.py`（服务商与视觉能力）、`poi_data.py`、`retrieval_engine.py`、`auth_db.py`、`paths.py`。
>    **本文后续章节里所有"在 `server.py` 里改的 XXX"一律理解成"已搬到上述模块"**，符号名不变。
> 3. **图片（多模态）链路已接通**：此前 `parse_attachments` / `build_llm_messages` /
>    `extract_places_from_photo` 只有实现、没有调用方（前端也没有入口），本轮补全为可用功能；
>    同时修掉"单张允许 4MB 但全局请求体只有 1MB"的自相矛盾（现为 12MB + JSON 413）。
>    细节见 §8。
> 4. **测试与工程基线**：新增 5 个测试文件 74 个用例（`test_api_auth` 18 / `test_api_userdata` 18 /
>    `test_api_campus_build` 8 / `test_chat_vision` 21 / `test_outbound` 9）；全仓 `python -m pytest -q` → **400 passed / 1 skipped**；
>    引入 `ruff`（`pyproject.toml`）与 `.github/workflows/ci.yml`（push/PR 自动跑 ruff + pytest）。
> 5. ⚠️ **认养人个人信息不得删改**：`data/*.json`、`campus_pois.json`、`data_for_sankey.json`、
>    `emotion_analysis.json` 里的认养人姓名/单位/寄语，是经本人同意在校园小程序「芳秾」与官网上公开的内容，
>    **原样保留**；`scripts/sanitize_public_data.py` **不要**当常规清理跑（本轮曾误运行，已全部回滚还原）。
> 6. 新增评估文档 [`docs/FEATURE_FEASIBILITY.md`](docs/FEATURE_FEASIBILITY.md)：**8 项候选功能**（§2~§9）
>    + **4 项中长期升级方向**（§12）的可行性、安全风险、成本与优先级，结论均由实测数据支撑。
>    升级方向的结论一句话：**4 项都没有卡在难度，全部卡在前置条件**——边界数据缺失
>    （`campuses.json` 只有 `center` + `trustRadiusM`，运行时 `campus_at()` 仍是纯半径判定）、
>    多校 schema 未对齐（生成器不能重跑）、确定性路由被测试锁定（智能体化只能做加法）、
>    小程序涉及"二次展示认养人信息"的同意范围（需先有合规结论）。依赖顺序见该文档 §12.5。
> 7. **同日追加的两处修复 + 一处勘误**：见 §10（场景热图渲染重做、采集侧出站代理修复）
>    与 §11（热图 KDE 带宽算法升级）；
>    并**更正**上一版本文档里"嵌入检索在本机崩溃 `0xC0000005`"的**误报**——复测模型加载正常、
>    嵌入模式 `passed=50/50`、退出码 0。真正的评测短板是**现有 50 例对规则与嵌入两种后端都是满分**（无区分度）。

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

### 3.2 修法（原都在 `server.py`；2026-10-10 起拆到 `api/chat_api.py`，见文首更新块）

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
- 语义索引已按当前 3122 条 POI 重建并通过运行时加载验证；建议 `SEMANTIC_ENABLED=auto`。
  缺少模型或依赖时会自动回退关键词规则，只有排障时才需要显式设为 `false`。
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
python -m unittest test_campus_config test_chat_itinerary test_chat_needle test_chat_usercapture `
    test_itinerary test_llm_config test_place_extraction test_scene_heatmaps test_security `
    test_semantic_retrieval test_userdata_store
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

### 7.2 现状盘点（截止 2026-10-10 更正）

> ⚠️ **本节原版有误**：旧版把模块名写成 `harvest_amap.py` / `harvest_public.py` / `build_assets.py`（仓库里没有这些文件），
> 并声称"生成器零测试""缺 `CAMPUS_GENERATOR.md`""`cli.py` 不存在"。实际仓库中的文件与状态如下。

**已完成**（`tools/campus_generator/`，共 20 个 `.py`）：

| 环节 | 文件 | 状态 |
|---|---|---|
| 校区档案 + 校验 | `profile.py`、`validate.py`、`boundary.py` | 可用（档案校验、校区边界与可信半径、`reserved_symbols` 拦住校区名/slug 撞车） |
| 采集 | `harvest.py`、`discovery.py`、`web_search.py` | 可用（高德 POI 采集 + 公开网页采集 + Tavily/Brave 自动检索、缓存、限速与安全校验） |
| 归一质检 | `normalize.py`、`quality.py`、`formal_asset_io.py` | 可用（映射 POI 契约、可解释候选质量评分、待确认清单、原子写） |
| 任务与发布 | `jobs.py`、`review.py`、`preview.py`、`publish.py` | 可用（有界任务队列与状态恢复、人工审核、临时热图预览、差异计划 + 哈希校验 + 双备份 + 回滚） |
| 统一入口 | `cli.py` | **已存在**（旧版写的"不存在"已过期） |
| 资产重建 | `build_semantic_index.py` + `build_scene_heatmaps.py` | 配置驱动（`*_pois.json` 自动发现、按 `heatmap_config.json` 遍历），含 stale 保护 |
| 测试 | `test_generator.py`、`test_jobs.py`、`test_discovery_parser.py`、`test_web_search.py`、`test_amap_resilience.py` | **5 个测试文件，非"零测试"** |
| 文档 | `CAMPUS_GENERATOR.md` | **已存在** |

**真正还缺的**（按"还差什么才能一键产出"排序）：

1. **档案文件缺失**：`campuses/profiles/sjtu.json` 所在目录 `campuses/` **当前不存在**，新校区试点只能靠参数临时传入，
   不能重跑出与现有三校区一致的产物。
2. **多校 schema 对齐**：`profile.runtime_config()` 产出的结构与现行 `campuses.json`（含 `schools` 表、每校区
   `school` 外键、`brand`）需要核对；`campus_config.check_consistency` 对多校有强校验，重跑资产生成前必须先对齐，
   否则会丢字段并挂在一致性检查上。
3. **`heatmap_config.json` 的新校区条目仍要手工加**（`slug/center/auditRadiusMeters`）。
4. **新校区植物数据降级未单独验证**：新学校没有 `data/all_trees.json` 时，应确认图鉴/寄语/预测按"无内容"降级，
   只保留"打点 + 问答 + 热图 + 行程"。
5. `.codebuddy/plans/` 里 `e2e-sjtu`、`package-cli` 仍为 pending。

> 关于植物数据的说明（**已更正**）：`data/all_trees.json` 等文件中的认养人姓名/单位/寄语是**经本人同意**在
> 校园小程序「芳秾」与官网上公开的内容，本项目**原样保留**，不是需要清理的"专有敏感数据"。

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
3. 植物相关数据（`data/all_trees.json`、`data/all_templates.json`、`campus_pois.json` 等）中，认养人的
   姓名/单位/寄语是**经本人同意**在校园小程序「芳秾」与官网上公开的内容，**原样保留、不做删改、不做脱敏**；
   引用时注明来源即可。`scripts/sanitize_public_data.py` **不要**当常规清理步骤执行。
4. 高德与大模型都有配额：采集器必须限速 + 断点续采；批量大模型研判要带台账（避免重复付费），本次的 `llm_scan.jsonl` 就是范例。
5. 语义索引会随 POI/场景文件变化而 `stale`，重建（`build_semantic_index.py`）后再把 `SEMANTIC_ENABLED` 调回 `auto`。

---

## 8. 图片（多模态）问答链路：本轮补全的"死代码"

### 8.1 之前的状态（坑）
后端 `parse_attachments` / `build_llm_messages` / `extract_places_from_photo` /
`build_photo_poi_context` / `_image_capability_clause` **全都有实现，但没有任何调用方**：
`/api/chat` 从不读 `attachments` 字段，前端也没有上传入口（注释里提到的 `imageAttach.ts` 全仓不存在）。
更糟的是两处上限自相矛盾：单张允许 **4MB**、最多 2 张，而 Flask 全局 `MAX_CONTENT_LENGTH` 默认 **1MB** ——
即使补了前端入口，稍大的照片也会在进入业务逻辑前被 Flask 以 413 拒绝，且返回的是前端解析不了的 HTML 错误页。

### 8.2 现在的链路
```
前端输入区（点选 / 拖拽 / 粘贴 / 移动端拍照）
  → imageAttach.ts：类型白名单 + 长边压缩到 1600px（>900KB 才重编码，避免截图二次有损）
  → payload 带 attachments: [{mime, dataUrl}]
  → /api/chat：parse_attachments（张数/类型/单张体积，声明 mime 必须与 dataUrl 前缀一致）
  → 若已配置视觉模型：extract_places_from_photo 抽"图中可读地点名"（失败只返回 []，不影响主回答）
     → match_pois_by_names 命中校园 POI → 写进 system prompt + 追加地图打点（kind = photo_poi）
  → build_llm_messages：附件只并入**最后一条**用户消息；历史轮次保持字符串（否则每轮重发 base64）
  → 回答按能力如实生成文案（未启用视觉时说"本部署未启用图片理解"，不假装看过图）
```

### 8.3 必须保持的口径（改一处要改多处）
| 约束 | 值 | 位置 |
|---|---|---|
| 单轮最多张数 | 2 | `api/chat_api.py` `MAX_ATTACHMENTS` ↔ `imageAttach.ts` `MAX_ATTACHMENTS` |
| 单张上限 | 4MB（base64 解码后） | `MAX_ATTACHMENT_BYTES` ↔ `imageAttach.ts` |
| 允许类型 | jpeg / png / webp | `ATTACHMENT_MIMES` ↔ `imageAttach.ts` `ALLOWED_MIMES` |
| 请求体总上限 | 12MB | `flask_app.ATTACHMENTS_BODY_BUDGET`（2×4MB base64 ≈ 10.7MB）↔ `.env` `MAX_CONTENT_LENGTH` |

> ⚠️ 本地 `.env` 若仍写着 `MAX_CONTENT_LENGTH=1048576`，上传大图会 413；删掉该行或用 12582912。
> 启动时若检测到上限低于预算，后端会打印一条中文警告。

### 8.4 安全约定
图片**不落盘**、日志只记张数与体积；校验失败一律 **400 并给出中文原因**（不静默丢弃，否则用户以为图传上去了）；
**未配置视觉模型时不把图片转发给上游**（纯文本模型收到 `image_url` 会直接报错）；
`/api/health` 新增 `vision` 字段，前端据此提示，但不阻断上传（换了 `.env` 不必刷新即可试）。

### 8.5 测试与验证
- `test_chat_vision.py`：**21 个用例**（附件校验 10、多模态消息构造 3、能力文案 2、端到端接线 6）。
- 浏览器实测（Playwright）：注入 3 张图仅保留 2 张并提示"已忽略超出上限的 1 张"；1.43MB 原图压到 **283KB**；
  粘贴与拖拽均生效（拖拽有高亮）；请求体确实带 2 条 `image/jpeg` 附件；用户气泡回显缩略图。

---

## 9. 本轮工程化基线（2026-10-10）

| 项 | 内容 |
|---|---|
| 静态检查 | `pyproject.toml` 配 `ruff`：`select = ["F", "E9"]`（只查未定义名、未使用变量、语法级问题；行宽 120），测试文件豁免 `F401/F811`。规则集**刻意保守**——先接上 CI，等清理完成再逐步扩大（如加 `I` 做 import 排序）。`python -m ruff check .` 当前全绿 |
| 依赖锁定 | `requirements.txt` 按本机（Python 3.10）实测版本 pin，**不要退回 `>=` 写法**；新增 `requirements-dev.txt`（`-r requirements.txt` + pytest/ruff/black）与 `requirements-ci.txt`（**不含 sentence-transformers/torch**） |
| CI | `.github/workflows/ci.yml`：push/PR 跑 `python -m ruff check .` + `python -m pytest`（全量，不做标记过滤）。用 `requirements-ci.txt` 装依赖，靠"语义依赖缺失时自动回退关键词规则"的既有契约在无模型环境下跑通，与既有 `security-check.yml` 并行 |
| 测试补齐 | 5 个新文件 **74** 个用例：路由层状态码契约（400/403/404/409/413）、问答入参校验、图片链路、出站代理策略 |
| 热图渲染 | 新增 `heatmapRender.ts`（HSL 色带 / alpha 从 0 / 数值上采样 / 等值带 / 图例同源）；`cellMeters` 20→12 并重建 26 张缓存；清掉 4 个取不到的旧缓存 |
| 出站代理 | 新增 `tools/campus_generator/outbound.py`；高德默认直连（修 `ProxyError`）、公开网页与境外搜索沿用系统设置 |
| 清理 | 删除游离日志、空 `build/`；`.gitignore` 补 `.pytest_cache/`、`.ruff_cache/`、`.codebuddy/`、`.playwright-cli/` |
| 文档 | 本文 + `PROJECT_STATUS.md` 纠偏；新增 `docs/FEATURE_FEASIBILITY.md`（8 项候选功能 + 4 项升级方向）。升级方向的两个硬前置：**边界数据缺失**与**多校 schema 未对齐**；唯一可立即开工的是移动端响应式 + 扫码入口 |

> 拆分 `server.py` 时的关键决策：被测试 monkeypatch 的模块级符号（`_CAMPUS_BUILD_MANAGER`、
> `CampusProfileResolver`、`WebSearchClient`、`plan_day`、`_itinerary_origin` 等）通过 `runtime.facade()`
> **在调用时**解析，而不是导入时绑定；门面同时兼容 `python server.py`（`__main__`）与 `import server` 两种入口，
> 否则会出现"第二份 app / 第二份任务管理器"。

---

## 10. 场景热图渲染重做 + 采集侧出站代理修复（2026-10-10 同日追加）

### 10.1 热图渲染：从"糊一层脏灰绿"到"只显热点"

**症状**：校园生成页的临时热图预览（以及问答页的场景热图）像是给地图蒙了一层均匀灰绿色，
底图被压暗、热点也不突出。

**三个实测病灶**（都在渲染层，不在算法层——栅格量程是用满的：`p50≈0.33`、`max=1.0`）：

| 病灶 | 证据 | 修法 |
|---|---|---|
| alpha 有下限 → 整片区域被均匀上色 | 原 `campusBuild.vue` 用 `alpha=70+150t`、`SceneHeatmapPanel.vue` 用 `90+165t`；而**实测 87.2% 的有效格 t≥0.1、68.5% t≥0.2**（`t<0.1` 的格子只占 12.8%），等于"没什么热度"的区域也全都在上色 | alpha 从 **0** 起，低于阈值（0.12）完全不画，阈值以上用 `t^1.5` 抬升 |
| RGB 线性插值中段发灰 | 原两段式 `r=41+174t, g=103+40(1-t), b=195-150t`，**t≈0.5 得 `rgb(128,123,120)`（纯灰）**；两处渲染色带还不一致 | 统一改 **HSL 插值**（色相单调 214→190→48→26→356），中段始终饱和 |
| 低分辨率栅格直接拉伸 | 60×69 的栅格铺满整个 bounds | 先对**数值**做双线性上采样（3×）再着色，并叠加**等值带边界** |

**落地**：
- 新增 `webapp/frontend/src/components/heatmapRender.ts`（色带 / alpha / 上采样 / 等值带 / 图例 CSS 单一来源），
  `SceneHeatmapPanel.vue` 与 `campusBuild.vue` 都改用它；
- 生成页**补上了此前完全缺失的图例**（右下角，含"仅在同一校区、同一场景内比较"的说明）；
- `heatmap_config.json` 的 `cellMeters` 从 **20 → 12**，并重建缓存：`python build_scene_heatmaps.py`
  → `Ready: 26 scene maps`，栅格从 60×69 提升到 98×113 / 134×121 / 60×84；
- 顺手删掉 4 个**永远取不到**的旧版无季节缓存（`minhang_flower_viewing.json`、`minhang_photo.json`、
  `putuo_flower_viewing.json`、`putuo_photo.json`）——`flower_viewing`/`photo` 在 `HeatmapStore.get()`
  里一定走季节文件（`season = current_season() if scene in ("flower_viewing","photo")`），这 4 个只会造成"缓存过期"的误判。

> ⚠️ 改 `cellMeters` 会让 `fingerprint` 变化，**必须重建缓存**，否则 `/api/heatmaps` 会回
> "热力图缓存已过期"。重建需要 BGE 模型（本机可正常加载）。

### 10.2 高德采集全部 `ProxyError`：出站代理口径不一致

**症状**：一次采集里 13 个关键词 × 3 次重试全部失败，
`AMap keyword '...' page 1: failed after 3 attempt(s): ProxyError; ...; failed result not cached`，
但看起来像"高德查不到数据"。

**根因**（实测）：Windows 注册表里配了系统代理（`ProxyEnable=1`、`ProxyServer=127.0.0.1:7897`），
`requests` 会读取它；而 `harvest.py` 的 `requests.get()` **没有传 `proxies`** → 高德（国内域名）走该代理 → `ProxyError`。
**大模型调用没受影响**，因为 `llm_config.py` 显式传了 `{'http': None, 'https': None}` 绕开系统代理。

> ⚠️ **踩坑点**：表示"直连"**不能**用 `proxies={}`——
> `requests.Session.merge_environment_settings()` 会用 `setdefault` 把系统代理补回来。
> 必须显式写成 `{'http': None, 'https': None}`。

**修法**：新增 `tools/campus_generator/outbound.py`
- `direct_proxies()`：**默认直连**，用于高德（可用 `AMAP_HTTP_PROXY` 覆盖）；
- `system_proxies()`：**默认沿用系统设置**，用于公开网页与境外搜索（Tavily / Brave，可用 `WEB_HTTP_PROXY` 覆盖）。

`harvest.py`（高德采集、公开网页抓取）与 `web_search.py`（Tavily / Brave）全部接上；
新增 `tools/campus_generator/test_outbound.py`（9 例，含"真实调用点确实传了 proxies"的断言）守住这条契约。

### 10.3 想启用"自动搜索公开网页"，`.env` 需要补这几行

本机 `.env` 是早于该功能创建的，**没有** `TAVILY_API_KEY` 这一项（它只存在于 `.env.example`）。追加：

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-你的真实密钥
```

`AMAP_HTTP_PROXY` / `WEB_HTTP_PROXY` 默认不用配（不写就是"高德直连、其余跟随系统"）。

---

## 11. 热图 KDE 带宽算法升级（2026-10-10 同日追加）

### 11.1 先发现的问题：原"自适应带宽"其实是失效的

`scene_heatmaps.class_bandwidth()` 原公式：

```python
annd = mean(最近邻距离)                       # 全局标量
zones = floor((points - origin) / zoneMeters)  # 按 200m 分格
abundance_i = 该格点数 / n
radius = exp(abundance_i) * max(annd, minInfluence)
h_i = min(radius, maxInfluence) / 3
```

对全部 846 个（校区×场景×子类）分组实测：

| 现象 | 数量 | 原因 |
|---|---|---|
| 组内带宽**完全相等**（自适应项失效） | **256 / 846（30.3%）** | 植物子类常常只有 2~4 棵，全落进同一格 → `abundance` 恒等 |
| 被 `maxInfluence` **钳成常数 100m** | **151 / 846（17.9%）** | `exp(ab)·max(annd,80)` 普遍超过 300，`min()` 一刀切平 |
| 组内带宽跨度中位 | 仅 **5.85 m** | 上述两条的直接结果 |

也就是说：这个"自适应"机制在真实数据上基本没起作用。

### 11.2 现在的公式（**尺度保持**）

```python
h_target = clip( geomean(旧规则逐点带宽), h_lo, h_hi )      # 只借用量级
ρ_i      = 1 / max(d_k(x_i), 1m)²                            # k 近邻连续局部密度
z_i      = (logρ_i − median(logρ)) / (1.4826·MAD)            # 稳健标准化
w_i      = 0.5 + 0.5·tanh(β·z_i)                             # β = densityExponent
h_i      = clip( h_target · (h_hi/h_lo)^(w_i − 0.5), h_lo, h_hi )
# 再一步尺度回正，抵消钳位造成的几何均值漂移
```

三条设计理由（**改之前先读完，否则容易好心办坏事**）：

1. **只改组内分布、不改整体量级**。锚点取旧规则逐点带宽的**几何均值**——这样热力场的整体尺度不变，
   推荐点位不会因尺度漂移大面积换名。第一版（锚点用 `median(d1)`）就踩了这个坑：Top-1 在 13/26 张图上变化、
   带宽中位从 33.55m 掉到 28.04m。改成几何均值锚点后降到 10/26、中位回到 35.62m。
2. **连续 + 不饱和**。用 k 近邻距离代替 `zoneMeters` 格内计数，不再有"跨格边界跳变"，
   也没有小组退化；调制在 `[h_lo, h_hi]` 内做**几何**伸缩，不会被硬钳成常数。
3. **方向可配**（这点最容易搞错）：
   - `β > 0`：**越密 → 带宽越大**（本项目既有语义，密集区连成一片宽热区）——**默认**；
   - `β = 0`：精确退化为固定带宽（做 A/B 对照）；
   - `β < 0`：教科书 **Abramson** 方向（越密 → 带宽越小，锐化密集区）。

   > ⚠️ `test_scene_heatmaps.py::test_annd_and_relative_abundance` 把"越密→越宽"锁成了契约。
   > 直接把默认改成 Abramson 方向会**破测试且改变产品观感**，那必须是一次有意识的产品决策。

### 11.3 配置与开关

```json
"adaptiveBandwidth": { "method": "knn-density", "neighbourK": 4, "densityExponent": 1.0 }
```

- `method = "legacy"` → 完全回到旧算法。**已实测：legacy 模式与升级前逐点一致（Top-1 变化 0/26、
  覆盖占比完全相同）**，所以回退是安全的。
- `ALGORITHM_VERSION` 已 bump 到 `sakde-campus-9-knn-adaptive-bandwidth`；改这个文件会变更
  `fingerprint`，**必须重建缓存**（`python build_scene_heatmaps.py`），否则 `/api/heatmaps` 回"缓存已过期"。
- 改 `scene_heatmaps.py` 本身也会变更指纹（`source_paths` 含该文件），同样需要重建。

### 11.4 实测对比（升级前 → 升级后）

| 指标 | 升级前 | 升级后 | 判读 |
|---|---|---|---|
| 组内带宽跨度中位 | 5.85 m | **25.05 m** | 自适应项真正生效（4.3×） |
| 带宽量级中位 | 33.55 m | 35.62 m | 量级保持（+6%） |
| 有效格覆盖占比 | 0.5817 | **0.5817** | 完全不变（不改变数据覆盖范围） |
| 场景区分度（相关系数均值） | 0.687 | **0.686** | 无退化，略优 |
| 场景覆盖重叠（Jaccard 均值） | 0.891 | **0.891** | 完全一致 |
| 平均绝对差 | 0.156 | 0.154 | 略优 |
| Top-1 点位 | — | 10/26 变化 | 场形状变化的预期结果，**无标注无法判定孰优** |

> 结论口径要诚实：**能证明的是"修好了失效机制、没有制造回归"**（覆盖不变、场景区分度不降、
> 旧模式可逐点复现），**不能证明"推荐更准"**——那需要人工标注（见 §7.5 与
> `docs/FEATURE_FEASIBILITY.md` §9）。

### 11.5 相关测试

`webapp/backend/test_scene_heatmaps.py` 新增 7 例：方向保持（β>0）、教科书方向可复现（β<0）、
β=0 退化为固定带宽、重复坐标仍有限、未知 method 回退旧规则、非法配置在加载期被拒、
以及**尺度保持**（组内几何均值漂移 ≤ 5%）。

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
