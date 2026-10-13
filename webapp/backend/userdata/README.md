# 用户共建数据目录（运行时生成，不入库）

本目录存放**用户在使用问答系统时贡献的数据**，全部由 `webapp/backend/userdata_store.py`
在本机读写。目录内容已被根 `.gitignore` 忽略（只保留本说明文件），**不要提交任何数据文件**。

## 文件说明

| 文件 | 类型 | 内容 |
|---|---|---|
| `ratings.jsonl` | 事件流 | 消息级评价（👍/👎 + 可选文字理由） |
| `place_reports.jsonl` | 事件流 | 用户主动上报的地点（名称/类型/坐标/说明） |
| `unresolved_queries.jsonl` | 事件流 | 未收录且规则判不出来的问句（等大模型批量研判） |
| `extracted_places.jsonl` | 事件流 | 规则/大模型抽取出的地点候选事件 |
| `review_state.json` | 人工结论 | 审核页的通过/驳回决定与备注 |
| `pending_places.json` | 派生 | **待确认清单**（前端审核页与生成流水线的契约） |
| `pending_places.md` | 派生 | 人类可读汇总表 |
| `approved_places.json` | 派生 | 已通过条目，字段对齐 POI 契约，交离线流程并入 |

## 约定

1. 事件流是 append-only 的事实来源；`pending_places.json` 等派生文件可由
   `userdata_store.rebuild_pending()` 随时重建，重建不会丢掉审核结论。
2. 保存前自动脱敏（手机号、邮箱、9 位以上连续数字、"我叫X"）。
3. **只进待确认清单，绝不自动写入 `*_pois.json` 正式数据**；并入仍走
   `tools/campus_generator/normalize.py` 的人工确认流程。
4. 换目录：设置环境变量 `USERDATA_DIR` 即可（测试用临时目录）。
