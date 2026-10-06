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
    "trustRadiusM": 1200,
    "coordinateSystem": "GCJ-02",
    "boundary": {
      "type": "Polygon",
      "coordinates": [[
        [120.995, 30.995],
        [121.005, 30.995],
        [121.005, 31.005],
        [120.995, 31.005],
        [120.995, 30.995]
      ]]
    },
    "boundarySource": "学校官方校园地图（请填写可复核来源）",
    "boundaryConfidence": 0.95
  }
}
```

`boundary` 是可选的可信校界，当前 MVP 只接受一个显式闭合、无自交且面积非零的
GeoJSON `Polygon` 外环（最多 2000 个位置），不接受洞和 `MultiPolygon`。所有坐标均严格使用 `[lng, lat]`，
且必须与 `center` 和采集源处于同一坐标系。可信边界必须显式声明
`coordinateSystem: "GCJ-02"`；高德候选会标记为 `GCJ-02`，公开网页 JSON-LD 坐标会标记为
`WGS84`，两者不会被静默混用或转换。不要把 POI 凸包
当成可信校界。

边界判定规则：

1. 存在合法 `boundary` 时，只接受多边形内部或边界上的点；圆半径不会把多边形外的点重新放行。
2. 没有 `boundary` 的旧 profile 继续使用 `trustRadiusM`；旧 profile 未声明坐标系时按
   `GCJ-02` 兼容历史无标签候选，并在规范化结果中补写该坐标系，但会拒绝显式标为
   `WGS84` / `BD-09` 的候选。一旦 profile
   声明 `coordinateSystem`，候选也必须携带完全相同的值。
3. 提供了非法边界、缺少 `boundarySource` / `coordinateSystem`，或 `boundaryConfidence` 不在 0 到 1 之间时，构建直接阻断，不会静默退回半径。
4. 高德的圆形查询半径会自动扩大到覆盖整个多边形；若所需半径超过高德接口的 50 公里上限，则明确报错。

高德候选的 `evidence` 会记录扁平的 `campusMembershipMethod`、
`campusMembershipRelation`、`boundaryHash`、边界来源、坐标系和置信度；半径回退则记录
`campusMembershipMethod=radius_fallback` 与使用的半径，便于审核页直接解释候选为何被接纳。

所有入口（JSON 导入、注册表、公开网页、高德、人工坐标编辑）都会在归一化时再次执行
校内判定；预览和正式发布还会做终点复核。为旧的无边界校区保留半径兼容模式；一旦配置
可信边界或其他显式坐标系策略，候选必须携带一致的 `coordinateSystem`。公开网页的 `WGS84`
坐标需经过显式、可审计的转换后才能进入 `GCJ-02` 校界，不能靠审核时改标签直接批准；
当前热力图预览也只接受 `GCJ-02`。

### 正式校界来源状态

截至 2026-10-06，华东师范大学地理科学学院发布的[闵行校区官方校园地图](https://geo.ecnu.edu.cn/2c/a4/c43032a601252/page.htm)
仅提供 JPG 栅格图，页面没有声明 CRS、经纬网、控制点、world file 或矢量下载。该资料可用于人工核对，但不能直接审计为
GCJ-02 `Polygon`。因此当前正式配置继续使用 `trustRadiusM` 回退，不把手工描边或 POI 凸包写成可信校界。后续应向学校
资产/基建部门或地理学院索取带 CRS 的 CAD、SHP 或 GeoJSON；若边界包含互不相连的地块，还需先扩展当前仅支持单外环
`Polygon` 的数据契约。

候选地点是 JSON 数组，至少包含 `name/lng/lat`；可选
`coordinateSystem/category/subCategory/tags/scenes/source/sourceUrls/confidence`。使用可信边界时，
`coordinateSystem` 不再可选。

规范化结果会保留采集器给出的 `confidence`，并复制为语义更明确的 `sourceConfidence`；同时新增独立的
`quality` 对象。`quality.scores` 分别解释校园证据、边界证据、主题相关性、来源可靠性、跨来源一致性、
商业场所风险和重复风险，`quality.confidence` 是 0 到 1 的综合质量分，`quality.confidenceReasons` 给出
可供审核员阅读的中文理由。质量分只辅助排序和人工判断，不会绕过校界、坐标系或正式发布校验。

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
输出始终写入独立任务目录。每次状态变化都会原子写入 `job_state.json`；后端重启后，已完成任务仍可按原
`jobId` 载入，重启时尚未结束的构建或预览会明确恢复为 `failed/interrupted`，提示用户重新提交，避免页面
永久停在“排队中”。只有稳定完成的 `completed/blocked` bundle 才能进入审核、预览和发布；这些操作按
job 互斥。待执行任务和保留任务总量分别受 `CAMPUS_BUILD_MAX_PENDING`（默认 16）与
`CAMPUS_BUILD_MAX_JOBS`（默认 256）限制，超限请求会被拒绝，已有目录不会自动删除。

该管理器**仅支持单进程本地开发**。不要用 Gunicorn/uWSGI 多 worker 或多个后端进程同时指向同一个
`campus_builds` 目录：进程内互斥和容量计数不会跨进程共享。需要多 worker/生产部署时，应先替换为
持久化队列与跨进程锁，而不是扩展当前本地执行器。

生成目录包含：

- `campus_profile.json`：原始校区档案；
- `runtime_config.json`：与现有多校区配置兼容的合并结果；
- `normalized_pois.json`：通过归一化的 POI；
- `pending_pois.json`：重复、缺字段或坐标非法的候选及原因；
- `build_report.json`：数量、状态和质检结果。
- `job_state.json`：可跨后端重启恢复的任务与预览状态。

## 自然语言发现与多来源采集

后端提供一个按用户主题生成校园场景候选的异步入口：

创建任务、提交审核和生成预览都属于受保护的写操作，必须在项目根目录 `.env` 配置至少 16 位的
`USERDATA_REVIEW_TOKEN`，并通过 `X-Review-Token` 请求头提交。校园生成页面与用户共建审核页复用同一枚
本机浏览器令牌；接口同时按客户端与路由限流。未配置令牌时保持关闭，不允许匿名请求占满本地任务池。

```http
POST /api/campus/discover
Content-Type: application/json
X-Review-Token: <USERDATA_REVIEW_TOKEN>

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
X-Review-Token: <USERDATA_REVIEW_TOKEN>

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
X-Review-Token: <USERDATA_REVIEW_TOKEN>
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

`expectedAfterHash` 是必填的非空确认令牌；它同时绑定植物/场景资产和正式
`campuses.json` 中的学校归属、中心点、可信半径、坐标系与边界策略。发布过程使用跨进程
共享文件锁重新生成计划，正式 JSON 通过同目录临时文件原子替换；准备写入前还会持久化
事务日志。进程中断后，下一次发布会根据前后哈希完成提交或从成对备份恢复，避免植物与
场景资产长期停留在半更新状态。后端启动加载、发布计划和数据脱敏脚本也复用同一恢复锁，
因此读取方不会接收半提交的数据。恢复会先校验两份备份的 JSON 结构和内容哈希，再覆盖正式
文件；正式资产文件及其直接父目录不支持符号链接。并发任务、旧计划、损坏 JSON 或策略变化
都会停止写入并要求处理后重新预览。该协议面向进程异常退出；原子替换会刷新文件，并在支持
目录 `fsync` 的平台刷新目录项，同时保留已有目标文件的标准权限位。Windows 上建议后端、
发布和离线构建使用同一服务账号；这里不承诺整机断电时的目录项落盘顺序。
正式 `campuses.json` 的修改也属于发布事务依赖：只能在后端和发布任务停止时修改，或由代码
持有同一个 `formal_assets_guard` 后原子替换。发布会在写入前后核对目标校区策略哈希；中途
变化时自动回滚 POI，恢复日志也不会把旧策略下的资产误判为已提交。

发布前会同时备份 `campus_pois.json` 和 `scene_pois.json`，植物与场景候选分别写入对应正式数据源。发布不会悄悄重建语义索引和正式热图；
发布成功后必须显式运行现有构建脚本并通过测试。未知校区需要先单独审核校区配置。

## 设计边界

生成器不会把大模型猜测直接写入正式 POI。后续应增加更多 `Harvester` adapter，分别接入高德地图、公开网页和用户共建数据，
统一产出候选后再进入本目录的 `normalize → validate → build` 链路。语义索引和热图构建仍由现有后端脚本负责，
待资产目录和输出路径稳定后再接入统一构建命令。
