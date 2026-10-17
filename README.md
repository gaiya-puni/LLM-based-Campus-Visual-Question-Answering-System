# LLM-based Campus Visual Question Answering System

基于大模型、校园 POI 数据和高德地图的校园可视问答系统。项目当前覆盖华东师范大学普陀校区、华东师范大学闵行校区，以及用于资产生成和内部验证的上海交通大学闵行校区。

系统把自然语言问答、地点检索、植物数据、场景热力图、图片地点识别、行程规划和用户共建数据汇总到一个 Vue + Flask 应用中，并保留了原有的静态数据可视化页面。

## 功能概览

- **校园智能问答**：查询植物、学院、食堂、停车场和校园地标，并在地图上标记结果。
- **语义检索**：优先使用本地中文句向量索引；模型或索引不可用时自动回退到关键词检索。
- **指哪问哪**：结合地图当前中心点回答“这个位置是什么”“我看到的地方在哪里”等问题。
- **场景热力图**：支持赏花、拍照、散步休息、浪漫约会四类场景；运行时只读取离线缓存，不在线重建。
- **图片问答**：可上传或粘贴图片，从路牌、楼名等可读文字中提取校园地点。需要单独配置视觉模型。
- **一日行程规划**：确定性算法负责站点和顺序，大模型负责自然语言文案；大模型不可用时使用模板兜底。
- **用户共建**：支持消息反馈、地点上报、未收录地点抽取、人工审核和导出。
- **校园资产生成器**：从 JSON、高德 POI 和公开网页候选生成待审核校园资产包，发布前需要审核和确认。
- **植物图鉴与寄语**：浏览植物数据、认养寄语和情感分析结果。
- **静态可视化**：提供桑基图、旭日图、平行坐标轴和词云 HTML 页面。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、ECharts、Marked、DOMPurify |
| 后端 | Python 3.10+、Flask、NumPy、PyMySQL |
| 大模型 | OpenAI 兼容接口，可选 DeepSeek 或 ChatECNU |
| 语义检索 | Sentence Transformers、`BAAI/bge-small-zh-v1.5`、NumPy |
| 地图 | 高德地图 JavaScript API；高德 Web 服务仅用于后端采集/转换 |
| 数据处理 | Python、Pandas、scikit-learn、Jupyter Notebook |

## 快速启动

### 1. 配置后端

在项目根目录执行：

```powershell
Copy-Item .env.example .env
```

至少填写一个大模型服务商的密钥。默认配置为 DeepSeek：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的密钥
```

也可以改用 ChatECNU：

```dotenv
LLM_PROVIDER=chatecnu
CHATECNU_API_KEY=你的密钥
```

然后安装依赖并启动 Flask：

```powershell
cd webapp/backend
python -m pip install -r requirements.txt
python server.py
```

后端默认运行在 `http://127.0.0.1:5000`。

没有配置大模型时，后端仍可启动；`/api/chat` 会明确返回“AI 服务尚未配置”，不会静默切换服务商。

### 2. 配置并启动前端

另开一个终端：

```powershell
cd webapp/frontend
Copy-Item .env.example .env
```

填写高德浏览器 Key：

```dotenv
VITE_APP_API_HOST=http://127.0.0.1:5000
VITE_AMAP_KEY=你的高德浏览器Key
VITE_AMAP_SECURITY_CODE=你的安全密钥
```

安装依赖并启动：

```powershell
npm install
npm run dev
```

前端默认使用 Vite 端口 `80`，浏览器访问 `http://localhost:80`。

如果本机没有权限监听 80 端口，可以在 `webapp/frontend/vite.config.ts` 中修改端口。

### 3. 可选配置

#### 语义检索

```dotenv
SEMANTIC_ENABLED=auto
SEMANTIC_MODEL_PATH=BAAI/bge-small-zh-v1.5
SEMANTIC_INDEX_FILE=semantic_index.npz
SEMANTIC_INDEX_META_FILE=semantic_index_meta.json
```

- `auto`：有依赖和有效索引时启用，否则回退关键词规则。
- `false`：强制关闭句向量检索。
- 修改 POI 文件或场景语料后，重新生成索引：

```powershell
cd webapp/backend
python build_semantic_index.py
```

首次没有本地模型时，显式使用 `--allow-download`。

#### 图片问答

纯文本模型不能直接处理图片，需要单独配置视觉模型，例如：

```dotenv
VISION_ENABLED=auto
VISION_API_KEY=你的视觉模型密钥
VISION_API_URL=视觉模型接口地址
VISION_MODEL=视觉模型名称
```

未启用视觉模型时，前端上传入口仍可显示，但系统会明确提示当前部署不支持图片理解。

#### 用户共建和审核

```dotenv
USERDATA_REVIEW_TOKEN=至少16位的本地审核密钥
```

用户共建数据默认写入 `webapp/backend/userdata/`，该目录已被 Git 忽略。没有审核密钥时，审核相关接口返回 403。

同一枚密钥也决定「评论审核」页**是否出现在侧栏**：配好并重启后端后，`/api/review-status` 返回 `configured: true`，侧栏与移动端"更多"里才显示该入口（未配置时直达 `/#/comment-review` 会被守卫送回首页）。队友要用同一页时，把这枚密钥私下告知，他们首次打开页面粘贴一次即可（只存本机浏览器 localStorage，不入库）。后端接口始终校验请求头 `X-Review-Token`，密钥不对一律 403。

#### 校园资产自动发现

自动使用高德 POI 或公开网页搜索时，需要在根目录 `.env` 配置：

```dotenv
AMAP_WEB_SERVICE_KEY=高德Web服务Key
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=你的Tavily密钥
```

也可将 `WEB_SEARCH_PROVIDER` 改为 `brave` 并填写 `BRAVE_SEARCH_API_KEY`。搜索结果只作为候选，必须经过人工审核后才能发布。详细说明见 [`tools/campus_generator/CAMPUS_GENERATOR.md`](tools/campus_generator/CAMPUS_GENERATOR.md)。

## 页面和路由

| 路由 | 功能 |
| --- | --- |
| `/#/index` | 首页和植物预测演示 |
| `/#/map` | 植物地图与植物图鉴 |
| `/#/classify` | 植物分类图表 |
| `/#/plantmessage` | 认养寄语和词云 |
| `/#/chatbot` | 智能问答、地图、热力图、行程规划和图片问答 |
| `/#/place-review` | 用户共建数据审核 |
| `/#/campus-build` | 校园资产发现、预览、审核和发布 |
| `/#/comment-review` | 评论审核（**仅在后端配好审核密钥时出现**） |

桌面端在**侧栏左下角**有「桌面端 / 手机端」胶囊开关（移动端为顶部品牌栏右侧的「桌面版」按钮）：点手机端会在 iPhone / Android 设备框里以真实窄视口加载本站（可切机型、横竖屏、缩放），因此在桌面浏览器里也能看到真实的移动端布局；真机访问时同一个按钮反向提供桌面版预览。也可直接用 `?frame=mobile` / `?frame=desktop` 强制端型。

## 后端 API

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 健康检查和能力状态 |
| `GET /api/review-status` | 审核态开关（`configured` 布尔值，供前端决定审核入口显隐） |
| `POST /api/chat` | 智能问答主接口 |
| `GET /api/heatmaps` | 读取场景热力图缓存 |
| `GET /api/emotions` | 读取认养寄语情感分析结果 |
| `POST /api/feedback` | 提交消息反馈 |
| `POST /api/place_report` | 上报校园地点 |
| `GET /api/userdata/pending` | 获取待审核数据 |
| `POST /api/userdata/review` | 通过或驳回候选地点 |
| `POST /api/userdata/extract` | 批量使用大模型补齐地点候选 |
| `GET /api/userdata/export` | 导出已审核地点 |
| `/api/campus/build/*` | 校园资产构建、预览和发布 |
| `POST /api/campus/discover` | 自动发现校园资料和候选地点 |

## 场景热力图

热力图算法和参数位于：

- [`webapp/backend/scene_heatmaps.py`](webapp/backend/scene_heatmaps.py)
- [`webapp/backend/heatmap_config.json`](webapp/backend/heatmap_config.json)
- [`webapp/backend/heatmap_cache/`](webapp/backend/heatmap_cache/)

当前正式热力图配置覆盖华师大普陀和闵行两个校区，四类场景均有缓存。交大闵行保留在多校区配置和资产生成链路中，但在当前问答页面中标记为非正式问答校区；其赏花场景点位不足时会明确降级。

数据或算法发生变化后，重新构建：

```powershell
cd webapp/backend
python build_scene_heatmaps.py
```

评测脚本不参与网站启动，仅用于离线对照：

```powershell
python evaluate_scene_heatmaps.py --sensitivity
```

评测结果写入 `heatmap_cache/evaluation.json`。该文件可以删除，不影响网站运行；需要时可重新生成。

算法说明见 [`webapp/backend/PHASE2_HEATMAPS.md`](webapp/backend/PHASE2_HEATMAPS.md)。

## 项目结构

```text
.
├── data/                         # 原始植物数据、模板和数据处理脚本
├── analysis/                     # 情感、文化关键词和预测模型的历史分析材料
├── visualization/                # 可直接打开的静态图表
├── tools/campus_generator/       # 校园资产发现、规范化、审核和发布工具
├── scripts/                      # 数据脱敏和发布检查脚本
├── webapp/frontend/              # Vue 3 + TypeScript 前端
│   ├── src/views/                # 页面和路由视图
│   ├── src/components/           # 热力图、行程、审核、图片附件等组件
│   └── src/api/                  # 前端 API 封装
└── webapp/backend/               # Flask 后端
    ├── api/                      # Flask Blueprint 接口
    ├── *_pois.json               # 统一校园 POI 数据
    ├── heatmap_cache/            # 离线热力图缓存
    ├── semantic_index.npz        # 语义检索向量索引
    └── userdata/                 # 本地用户共建数据，默认不入库
```

## 数据文件说明

- `data/all_trees.json`：植物原始记录，后端用于构建植物位置和坐标索引。
- `data/all_templates.json`：植物种类、科属、习性等模板数据。
- `webapp/backend/tree_locations.json`：植物名称到建筑/位置描述的预处理映射。
- `webapp/backend/college_locations.json`：学院和楼宇位置数据。
- `webapp/backend/*_pois.json`：食堂、停车、校园场景等结构化 POI。
- `webapp/frontend/src/assets/try.json`：旧版植物地图、图鉴和预测演示使用的前端数据切片。
- `webapp/frontend/src/assets/emotion_analysis.json`：寄语情感分析展示数据。
- `webapp/backend/semantic_index.npz`：预计算语义向量；源数据变化后必须重建。

认养人姓名、单位和寄语属于项目公开数据的一部分。若需发布脱敏版本，应使用 [`scripts/sanitize_public_data.py`](scripts/sanitize_public_data.py)，不要手工修改运行时数据。

## 测试与质量检查

后端完整开发依赖：

```powershell
cd webapp/backend
python -m pip install -r requirements-dev.txt
```

在仓库根目录运行：

```powershell
python -m pytest -q
python -m ruff check .
```

前端检查和构建：

```powershell
cd webapp/frontend
npx vue-tsc --noEmit
npm run test:heatmap
npm run build
```

发布前建议检查敏感信息：

```powershell
python scripts/check_publication.py
```

CI 使用 `webapp/backend/requirements-ci.txt`，不安装体积较大的 Sentence Transformers 依赖；后端在缺少该依赖时会自动使用关键词检索回退路径。

## 已知限制

1. 地图和步行路线依赖有效的高德浏览器 Key；Key 会暴露在浏览器端，必须在高德控制台限制域名、服务和配额。
2. 语义检索依赖本地模型和索引，首次安装可能需要较大的 PyTorch/Sentence Transformers 依赖。
3. 热力图是离线 POI 和植物分布模型，不等同于实时人流、正式校界、导航路线或真实花期预测。
4. 行程中的距离和耗时在后端默认是直线估算，前端会尝试用高德步行算路结果覆盖。
5. 图片问答只有在显式配置视觉模型后才会真正调用图片理解能力。
6. 用户共建数据默认保存在本机文件中，当前审核机制是单一共享密钥，适合本机或小范围内网使用。
7. 首页的“植物预测”目前是随机植物展示演示，不是实时调用 `analysis/prediction/` 下训练模型的在线预测接口。

## 静态可视化

无需启动前后端即可打开：

| 图表 | 文件 |
| --- | --- |
| 桑基图 | `visualization/charts/sankey/sankey.html` |
| 旭日图 | `visualization/charts/sunburst/sunburst-only.html` |
| 平行坐标轴 | `visualization/charts/parallel/parallel.html` |
| 词云 | `visualization/wordcloud/res/exp.html` |

## 安全与提交约定

- `.env`、数据库密码、模型密钥和审核密钥禁止提交。
- 前端 `VITE_*` 变量会进入浏览器，不能放置后端密钥。
- 用户共建数据目录默认被 `.gitignore` 忽略。
- 修改正式 POI 或热力图资产前，先运行测试和发布检查。

## 项目许可

详见 [`LICENSE`](LICENSE)。
