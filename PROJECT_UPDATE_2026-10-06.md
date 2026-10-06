# 校园热力图智能体项目更新与体验指南

> 更新日期：2026-10-06  
> 交付分支：`test/dev`  
> 适用环境：Windows PowerShell、本地 Flask + Vue 3

## 1. 本次更新概览

本轮将校园资产生成流程从“能生成候选”推进为一条更安全、可审核、可恢复的本地开发链路：

```text
自然语言需求
  → 识别学校、校区和主题
  → 已登记数据 + 高德 POI + 自动网页搜索
  → 校区空间校验与坐标系检查
  → 可解释质量评分
  → 人工审核
  → 临时热图预览
  → 带备份和一致性校验的正式发布
```

本次交付对应以下功能提交：

| 提交 | 内容 |
|---|---|
| `0a4e0c2` | 加固校界、候选评分、任务生命周期和正式发布链路 |
| `d1672fb` | 按最新数据重建语义索引与场景热图缓存 |
| `9f03e02` | 新增 Tavily / Brave 自动发现公开网页能力 |

## 2. 更新功能

### 2.1 自动搜索公开网页

- 支持 `tavily` 和 `brave` 两个搜索服务商，默认使用 Tavily。
- 根据“学校 + 校区 + 场景主题”自动生成最多 2 条查询。
- 默认最多接收 10 个 HTTPS 网页，同一域名最多 2 个结果。
- 自动去重并缓存搜索结果，减少重复调用和额度消耗。
- 搜索结果会在校园生成页面显示，可展开核对标题、网址和来源查询。
- 手工填写的公开网页 URL 仍然保留，可与自动搜索结果合并使用。
- 搜索审计摘要随任务持久化，后端重启后仍可查看。

“自动搜索全网”在本项目中准确指：**在所选搜索服务商索引覆盖范围内发现公开网页**，不是无限制爬取互联网。

### 2.2 网页采集安全限制

自动发现的网址不会直接成为正式 POI，必须继续经过网页采集器和人工审核：

- 仅允许公开 HTTPS URL；
- 禁止 URL 用户名、密码和非标准 HTTPS 端口；
- 拒绝本机、内网、链路本地和其他非公网 IP；
- 不自动跟随跳转；
- 仅接收 HTML/XHTML；
- 单页最大 2 MB，请求超时受配置限制；
- 搜索摘要只记录为 `searchLeads`，不作为独立来源提高质量分；
- 只有已成功抓取的网页候选才记录网页证据；
- 网页 JSON-LD 坐标按 WGS84 处理，不会改标签冒充高德 GCJ-02 坐标。

### 2.3 校区边界与坐标质量控制

- 新增统一的校区成员关系计算器，支持正式多边形边界。
- 尚未录入正式矢量边界的校区继续使用可信半径降级，并在评分原因中明确标识。
- AMap 候选必须满足校园空间策略，校外结果自动排除。
- 坐标系不一致、缺坐标、越界和重复候选进入待处理区，不会直接发布。
- 正式发布、预览和归一化阶段复用同一套空间判断，避免各环节口径不一致。

### 2.4 可解释候选质量评分

每个可用候选会生成质量分和原因，主要维度包括：

- 校园身份依据；
- 校界依据；
- 与请求主题的相关性；
- 来源可靠性；
- 多来源一致性；
- 商业场所风险；
- 重复候选风险。

评分用于辅助人工审核，不代替审核，也不会自动批准搜索结果。

### 2.5 任务生命周期和恢复

- 构建任务状态持久化到独立 `job_state.json`。
- Flask 重启后可以重新载入已完成任务。
- 重启时尚未结束的任务会明确标记为中断失败，不会永久停在“排队中”。
- 构建、审核、预览和发布使用任务级互斥，避免同一任务并发改写。
- 队列和保留任务总量均有限制；容量不足时返回 503。
- 调用计费搜索 API 前先原子预留任务容量，队列已满时不会浪费搜索额度。
- 当前管理器仅用于单进程本地开发，不支持多个 Flask/Gunicorn worker 共享同一任务目录。

### 2.6 审核、预览与正式发布

- 所有创建、审核、预览和发布写操作都要求 `USERDATA_REVIEW_TOKEN`。
- 只有稳定完成的任务才能进入审核和预览。
- 修改审核结论后，旧预览会自动标记为过期。
- 正式发布前会生成差异计划，显示新增和修改数量。
- 发布会校验预期哈希，并同时备份两份正式 POI 文件。
- 任一文件写入失败会回滚，避免只更新一半的数据。
- 发布后仍需按提示重建语义索引和正式热图缓存。

### 2.7 前端体验更新

校园热力图生成页新增：

- “自动搜索公开网页”开关；
- 搜索状态和命中网页数量；
- 可展开的搜索结果列表；
- 候选质量总分、分项得分和解释；
- 审核令牌输入和本机保存；
- 预览过期提示及安全发布确认。

## 3. 环境配置

### 3.1 根目录 `.env`

从示例文件复制一次：

```powershell
Set-Location D:\I_love_studying\快乐数据大三上\campusVisual
Copy-Item .env.example .env
```

如果 `.env` 已存在，请直接编辑，不要覆盖已有的 MySQL、DeepSeek 或高德配置。

#### 必需：审核令牌

```dotenv
USERDATA_REVIEW_TOKEN=请替换为至少16位的本机随机字符串
```

该令牌只用于本机写操作鉴权，不能提交到 Git。

#### 自动网页搜索：推荐 Tavily

在 [Tavily API 控制台](https://app.tavily.com/home) 创建 Key：

```dotenv
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-你的真实密钥

WEB_SEARCH_MAX_RESULTS=10
WEB_SEARCH_PER_DOMAIN_LIMIT=2
WEB_SEARCH_MAX_QUERIES=2
WEB_SEARCH_TIMEOUT=8
```

也可以使用 [Brave Search API](https://api-dashboard.search.brave.com/app/keys)：

```dotenv
WEB_SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=你的真实密钥
```

只需要填写所选服务商的 Key。Key 只由 Flask 后端读取，不得放入 Vue 前端环境变量。

#### 完整 POI 体验：高德 Web 服务 Key

```dotenv
AMAP_WEB_SERVICE_KEY=你的高德Web服务Key
```

它用于后端地理编码和 POI 搜索。它和浏览器地图 Key 不是同一类 Key，不能互相替代。

#### 可选：MySQL 登录

MySQL 只影响登录、注册等数据库功能。直接打开校园生成页面不依赖 MySQL登录。

```dotenv
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=test
MYSQL_CHARSET=utf8mb4
```

若 `/api/health` 返回 `{"status":"ok","database":"error"}`，说明 Flask 已启动，只是 MySQL 尚未连接。

### 3.2 前端地图 `.env`

文件位置：`webapp/frontend/.env`。

```dotenv
VITE_AMAP_KEY=你的高德浏览器Key
VITE_AMAP_SECURITY_CODE=你的安全密钥
VITE_APP_API_HOST=http://127.0.0.1:5000
```

浏览器 Key 会出现在前端请求中，必须在高德控制台限制可用域名、服务和配额。

## 4. 启动步骤

### 4.1 启动后端

打开第一个 PowerShell：

```powershell
Set-Location D:\I_love_studying\快乐数据大三上\campusVisual
& .\.venv\Scripts\Activate.ps1
Set-Location webapp\backend
python server.py
```

看到 Flask 监听 `http://127.0.0.1:5000` 即表示后端启动成功。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health
```

### 4.2 启动前端

打开第二个 PowerShell：

```powershell
Set-Location D:\I_love_studying\快乐数据大三上\campusVisual\webapp\frontend
npm.cmd install
npm.cmd run dev
```

使用 `npm.cmd` 可以避开部分 Windows 机器禁止执行 `npm.ps1` 的策略问题。

### 4.3 打开页面

```text
http://localhost/#/campus-build
```

## 5. 首次体验：自动搜索网页

### 5.1 最快的网页搜索验证

适合只配置了 Tavily/Brave Key、暂时没有高德 Web 服务 Key 的情况：

1. 打开“校园热力图生成”页面。
2. 输入根目录 `.env` 中的 `USERDATA_REVIEW_TOKEN`。
3. 输入：`帮我做上海交通大学闵行校区适合拍照的热力图`。
4. 取消勾选“搜索高德 POI”。
5. 保持“采集公开网页”和“自动搜索公开网页”勾选。
6. 点击“开始发现”。
7. 查看任务卡片中的“网页搜索：已完成 · N 页”。
8. 展开“查看自动发现的网页”，核对标题和 URL。

预期结果：已登记校园数据仍会正常载入；搜索服务会自动发现网页并进行安全抓取。没有结构化坐标的网页只作为线索，不会自动成为正式地图点。

### 5.2 完整多来源体验

配置 `AMAP_WEB_SERVICE_KEY` 后：

1. 同时勾选“搜索高德 POI”“采集公开网页”“自动搜索公开网页”。
2. 提交同一个查询。
3. 等待任务从“排队中/采集中”进入“待审核”。
4. 查看候选的质量总分和分项原因。
5. 对新增候选执行通过、拒绝或保留待审。
6. 点击生成临时热图预览。
7. 仅在确认差异计划和备份提示后执行正式发布。

网页搜索负责发现资料，高德负责提供 GCJ-02 POI 坐标；系统不会把 WGS84 网页坐标直接冒充为高德坐标。

### 5.3 PowerShell 直接调用接口

```powershell
$reviewToken = Read-Host '请输入 USERDATA_REVIEW_TOKEN'
$headers = @{ 'X-Review-Token' = $reviewToken }
$payload = @{
  query = '帮我做上海交通大学闵行校区适合拍照的热力图'
  includeAmap = $false
  includeWeb = $true
  autoSearch = $true
} | ConvertTo-Json

$response = Invoke-RestMethod `
  -Method Post `
  -Uri 'http://127.0.0.1:5000/api/campus/discover' `
  -Headers $headers `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))

$response.webSearch
$response.jobId
```

查询任务状态：

```powershell
Invoke-RestMethod "http://127.0.0.1:5000/api/campus/build/$($response.jobId)"
```

## 6. 验收标准

| 检查项 | 通过标准 |
|---|---|
| 搜索配置 | 页面显示 `completed`，且 `acceptedUrlCount` 大于 0 |
| 搜索审计 | 可展开网页列表；任务重载后仍存在 |
| 安全降级 | 缺 Key 时显示 `unconfigured` 警告，已登记数据仍继续运行 |
| 高德采集 | 已配 Web 服务 Key 时可产生校内 GCJ-02 候选 |
| 数据质量 | 搜索摘要只显示为线索，不提高跨来源一致性评分 |
| 审核 | 新候选默认不会自动写入正式 POI |
| 预览 | 仅稳定任务可生成；审核变化后旧预览变为过期 |
| 发布 | 显示差异计划，校验哈希并生成备份 |

## 7. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `自动搜索未启用` | 根目录 `.env` 缺少当前服务商 Key；填写后必须重启 Flask |
| `WEB_SEARCH_PROVIDER 不受支持` | 只允许 `tavily` 或 `brave` |
| 高德 POI 未配置 | 填写 `AMAP_WEB_SERVICE_KEY`，或首次体验时取消“搜索高德 POI” |
| HTTP 403 | `USERDATA_REVIEW_TOKEN` 未配置、少于 16 位或前端输入不一致 |
| HTTP 503 | 本地任务队列或任务保留上限已满；等待现有任务完成后再试 |
| 搜索有结果但没有新地图点 | 页面可能没有 JSON-LD 坐标，或坐标系/校界不满足策略；这是安全降级 |
| 某网页被跳过 | 页面跳转、非 HTML、超限、不可访问或解析到非公网地址 |
| 地图空白 | 检查 `webapp/frontend/.env` 的浏览器 Key、安全密钥和域名限制 |
| `/api/health` 的 database 为 error | MySQL 未连接；不影响公开网页搜索和已登记数据生成，但影响登录 |
| PowerShell 无法运行 `npm.ps1` | 使用 `npm.cmd run dev` |

## 8. 测试与交付状态

本轮在 `test/dev` 验证：

| 验证 | 结果 |
|---|---|
| 网页搜索、校园生成器和任务生命周期测试 | `84/84 OK` |
| Flask 后端完整回归 | `191/191 OK` |
| Vue TypeScript + Vite 生产构建 | 通过 |
| Python 语法检查 | 通过 |
| Git diff 格式检查 | 通过 |
| 发布脱敏检查 `scripts/check_publication.py` | 通过 |

尚未执行真实搜索服务联网验收，因为真实 API Key 不进入仓库。填入本机 `.env` 后按第 5 节即可完成最后一步验证。

## 9. 关键文件

| 文件 | 作用 |
|---|---|
| `.env.example` | 搜索、高德、审核、MySQL 和大模型环境变量示例 |
| `tools/campus_generator/web_search.py` | Tavily/Brave 搜索适配、URL 清洗、缓存和限额 |
| `tools/campus_generator/discovery.py` | 校园主题查询规划、多来源采集和搜索线索绑定 |
| `tools/campus_generator/boundary.py` | 校区边界和可信半径计算 |
| `tools/campus_generator/quality.py` | 可解释候选质量评分 |
| `tools/campus_generator/jobs.py` | 有界任务队列、状态恢复和容量预留 |
| `tools/campus_generator/publish.py` | 发布计划、哈希校验、备份、提交与回滚 |
| `webapp/backend/server.py` | 校园发现、任务、审核、预览和发布 API |
| `webapp/frontend/src/views/campusBuild.vue` | 校园生成、搜索审计、质量审核与预览界面 |
| `tools/campus_generator/CAMPUS_GENERATOR.md` | 生成器设计、CLI 和接口详细说明 |

## 10. 下一步建议

1. 为三个校区补充可审计的正式矢量边界，逐步替换可信半径降级。
2. 使用真实 Tavily/Brave Key 做中文校园查询召回评测，记录有效网页比例和平均额度消耗。
3. 为生产部署补充 `robots.txt`/站点政策处理、持久化队列和跨进程锁。
4. 将安全抓取成功的网页正文与高德候选做可验证实体对齐，再考虑把它计入跨来源一致性评分。
5. 增加自动清理或归档策略，控制本地 `campus_builds` 任务目录增长。
