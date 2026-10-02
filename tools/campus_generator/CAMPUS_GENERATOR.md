# 校园资产生成器（第一阶段）

`tools/campus_generator` 是一个与 Flask 运行时解耦的离线生成器。第一阶段不直接联网，
而是接收学校/校区档案和候选地点，输出可审计的资产包，为后续地图搜索、网页检索和异步任务提供稳定契约。

## 输入

校区 profile：

```json
{
  "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
  "campus": {
    "name": "示例校区",
    "slug": "demo_main",
    "center": [121.0, 31.0],
    "trustRadiusM": 1200
  }
}
```

候选地点是 JSON 数组，至少包含 `name/lng/lat`；可选 `category/subCategory/tags/scenes/source/sourceUrls/confidence`。

## 命令

在仓库根目录运行：

```powershell
python -m tools.campus_generator.cli profile `
  --school-id demo --school-name 示例大学 `
  --campus-name 示例校区 --campus-slug demo_main `
  --lng 121.0 --lat 31.0 --output profile.json

python -m tools.campus_generator.cli build `
  --profile profile.json --candidates candidates.json `
  --output build/demo

python -m tools.campus_generator.cli check --bundle build/demo
```

采集器目前提供一个离线 JSON adapter，用于接入人工审核导出或未来网络采集器的中间产物：

```powershell
python -m tools.campus_generator.cli harvest `
  --input raw_candidates.json --output build/demo/raw_candidates.json
```

也可以直接使用高德 Web Service 和公开网页结构化数据：

```powershell
python -m tools.campus_generator.cli harvest-amap `
  --profile profile.json --keyword 图书馆 --keyword 湖泊 `
  --cache build/cache --output build/demo/amap.json

python -m tools.campus_generator.cli harvest-web `
  --url https://example.edu/campus `
  --cache build/cache --output build/demo/web.json
```

高德读取环境变量 `AMAP_WEB_SERVICE_KEY`；网页采集只接受公开 HTTPS 页面，
不跟随重定向、不访问私有 IP、不把普通网页文本直接当成有坐标地点。

`jobs.py` 提供进程内 `BuildJobManager`，任务状态为 `queued/running/completed/blocked/failed`，
输出始终写入独立任务目录。它可以由后续 Flask 路由或队列 worker 调用。

生成目录包含：

- `campus_profile.json`：原始校区档案；
- `runtime_config.json`：与现有多校区配置兼容的合并结果；
- `normalized_pois.json`：通过归一化的 POI；
- `pending_pois.json`：重复、缺字段或坐标非法的候选及原因；
- `build_report.json`：数量、状态和质检结果。

## 自然语言发现与多来源采集

后端提供一个按用户主题生成校园场景候选的异步入口：

```http
POST /api/campus/discover
Content-Type: application/json

{
  "query": "帮我做华东师范大学闵行校区的约会热力图",
  "webUrls": ["https://example.edu/campus"]
}
```

接口会完成：

1. 从本地 `campuses.json` 解析已登记学校/校区；未知校区才使用高德地理编码补齐中心点。
2. 根据自然语言主题生成有限关键词。
3. 载入已登记的真实植物和场景点位；高德和公开网页按主题补充步道、运动场、体育馆、河岸、亭子、桥、草坪和植物等候选，并排除明显商业噪声。
4. 返回 `jobId`，通过 `GET /api/campus/build/<job_id>` 查询状态。

任务输出写入 `webapp/backend/userdata/campus_builds/<job_id>/`，包括发现 profile、规范化 POI、待审核 POI 和构建报告；不会自动发布到正式校区配置或热图缓存。

## 候选审核与临时热图预览

发现任务完成后，先读取候选：

```http
GET /api/campus/build/<job_id>/review
```

再提交审核决定：

```http
POST /api/campus/build/<job_id>/review
Content-Type: application/json

{
  "decisions": [
    {"id": "candidate-id", "action": "approve"},
    {"id": "another-id", "action": "reject"},
    {"id": "candidate-id", "action": "approve",
     "patch": {"lat": 31.0315, "lng": 121.4538}}
  ]
}
```

通过的地点会写入任务目录的 `approved_pois.json`。修改坐标或名称时会重新归一化和校验，不能绕过质量检查。

确认至少有 3 个通过地点后，可提交临时热图预览：

```http
POST /api/campus/build/<job_id>/preview
```

预览使用任务目录中的独立工作区和缓存，不会覆盖正式 `webapp/backend/heatmap_cache`、校区配置或语义索引。

构建完成后按场景读取预览：

```http
GET /api/campus/build/<job_id>/preview/walk
GET /api/campus/build/<job_id>/preview/photo?season=autumn
```

## 正式发布

正式发布与临时预览分离，并且只支持已经登记到 `campuses.json` 的校区。先读取变更预览：

```http
GET /api/campus/build/<job_id>/publish-plan
```

确认 diff 没有问题后，再显式提交；请求必须携带与用户共建审核接口相同的
`X-Review-Token`，并提交预览中的 `afterHash`，防止确认后数据发生变化：

```http
POST /api/campus/build/<job_id>/publish
X-Review-Token: <USERDATA_REVIEW_TOKEN>
Content-Type: application/json

{"confirm": true, "expectedAfterHash": "..."}
```

发布前会同时备份 `campus_pois.json` 和 `scene_pois.json`，植物与场景候选分别写入对应正式数据源。发布不会悄悄重建语义索引和正式热图；
发布成功后必须显式运行现有构建脚本并通过测试。未知校区需要先单独审核校区配置。

## 设计边界

生成器不会把大模型猜测直接写入正式 POI。后续应增加更多 `Harvester` adapter，分别接入高德地图、公开网页和用户共建数据，
统一产出候选后再进入本目录的 `normalize → validate → build` 链路。语义索引和热图构建仍由现有后端脚本负责，
待资产目录和输出路径稳定后再接入统一构建命令。
