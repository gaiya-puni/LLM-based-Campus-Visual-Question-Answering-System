# 🌱 LLM-based Campus Visual Question Answering System

华东师范大学校园可视问答系统

---

## 🖥️ 如何查看

### 方式一：直接打开静态图表（无需安装任何环境）

下载仓库后，用浏览器直接打开以下 HTML 文件：

| 图表 | 路径 | 内容 |
|------|------|------|
| 桑基图 | `visualization/charts/sankey/sankey.html` | 领养植物的学院 × 植物种类分布 |
| 旭日图 | `visualization/charts/sunburst/sunburst-only.html` | 全校植物按科目、种类的层级分布 |
| 平行坐标轴 | `visualization/charts/parallel/parallel.html` | 植物树高、胸径、冠幅等维度比较 |
| 词云 | `visualization/wordcloud/res/exp.html` | 领养寄语词云 |

### 方式二：运行 Vue 3 主网站（需要 Node.js 18+）

主网站包含交互式地图、植物图鉴、寄语浏览、领养预测等完整功能。

步骤1.开命令行开启后端
```powershell
Copy-Item .env.example .env
# 编辑 .env，填写已轮换的 DeepSeek/MySQL/讯飞配置
cd webapp/backend
pip install -r requirements.txt
python server.py
```

仓库不包含密钥。`.env` 已被 Git 忽略，禁止把真实凭据写回源码或提交到仓库。
仓库已包含当前数据对应的语义向量索引；只有 POI 或场景语料变化时才需要运行 `build_semantic_index.py` 重新生成。

语义检索相关环境变量：

```text
SEMANTIC_ENABLED=auto
SEMANTIC_MODEL_PATH=BAAI/bge-small-zh-v1.5
SEMANTIC_INDEX_FILE=semantic_index.npz
SEMANTIC_INDEX_META_FILE=semantic_index_meta.json
```

- `SEMANTIC_ENABLED=auto`：存在依赖和索引时启用，否则自动回退到关键词规则。
- `SEMANTIC_ENABLED=false`：强制关闭句向量检索。
- 精确地点查询始终优先于语义推荐；例如“枣阳路460停车场在哪里”只返回一个地点，不进入 Top 排名。

可运行离线评测集检查精确查询、场景识别和无数据保护：

```bash
python evaluate_semantic_queries.py --strict
```

第二阶段新增四类校园场景热图（两个校区共8张），不改变原有精确地点查询。
仓库已包含当前数据对应的离线热力图缓存，启动后端即可在智能问答页面选择“场景热图”。数据或算法变化后，可在后端目录运行 `python build_scene_heatmaps.py` 重新生成。
也可切回“原版推荐”进行对照。算法说明、数据限制和测试步骤见
[第二阶段说明](webapp/backend/PHASE2_HEATMAPS.md)。

步骤2.重开新命令行开启前端
```powershell
cd webapp/frontend
Copy-Item .env.example .env
# 在 .env 中填写受域名和配额限制的高德浏览器 Key
npm install
npm run dev
```

步骤3.启动后在浏览器打开 `http://localhost:80`

> 注意：地图功能依赖高德地图 API。浏览器 Key 对访问者可见，必须在高德控制台限制可用域名、服务和配额。


---

## 📁 项目结构

```
Plants-in-ECNU/
├── data/                        # 数据爬取与处理
│   ├── all_trees.json           # 脱敏后的植物位置与尺寸数据
│   ├── all_templates.json       # 植物种类详情（科、习性）
│   ├── all_trees_for_lib.json   # 附加文化关键词的完整数据
│   ├── get_data_code/           # 爬虫脚本（Charles 抓包 + Python）
│   └── icon/                    # 植物图标图片
│
├── analysis/                    # 数据分析
│   ├── emotion/                 # 领养寄语情感分析（讯飞星火 API）
│   ├── culture/                 # 植物文化关键词提取（NLP）
│   └── prediction/              # 领养预测模型
│       ├── reg/                 # 逻辑回归（AIC 筛变量）
│       └── svm/                 # SVM（准确率 0.75 / AUC 0.99）
│
├── visualization/               # 静态可视化（直接用浏览器打开）
│   ├── charts/                  # 桑基图 / 旭日图 / 平行坐标轴
│   └── wordcloud/               # 词云
│
├── webapp/                      # 主网站
    ├── frontend/                # Vue 3 + TypeScript + ECharts 前端
    └── backend/                 # Python Flask + MySQL 后端         
```

---

## ✨ 功能模块

- **交互式地图**：基于腾讯地图 API 的植物点聚合地图，展示各校区植物分布
- **植物图鉴**：按种类、校区筛选浏览全部在册植物
- **领养寄语**：浏览完整的植物认养者寄语及情感分析结果
- **领养预测**：逻辑回归 + SVM 预测最可能被认养的植物（AUC 0.99）
- **情感分析**：讯飞星火大模型对寄语内容的情感分析
- **静态图表**：桑基图、旭日图、平行坐标轴、词云

---

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 前端 | Vue 3 · TypeScript · Vite · ECharts 6 · Element Plus · D3.js |
| 后端 | Python · Flask · MySQL |
| 数据爬取 | Python · Charles 抓包 |
| 数据分析 | Jupyter Notebook · Pandas · scikit-learn |
| NLP | 讯飞星火大模型 API · Pyecharts |
| 语义检索 | Sentence Transformers · 中文句向量 · 余弦相似度 |
| 地图 | 高德地图 JavaScript API |

---

## 📊 数据说明

数据来源于华东师范大学微信小程序「芳秾」，涵盖**闵行校区**和**中北校区**的全部在册植物。

主要字段：位置（经纬度）、校区、树高 / 胸径 / 冠幅、植物种类（含科名、习性）、领养人信息（姓名、单位、寄语）。

---

## 项目来源

原项目为静态可视化版本，包含植物分布图（HTML + D3.js）、初步数据爬取和词云生成。本项目在此基础上进行了全面重构与扩展：

- 数据规模扩大，覆盖闵行、中北双校区全部在册植物
- 从静态页面升级为 Vue 3 全栈 Web 应用
- 新增 ML 预测模型（逻辑回归 + SVM）
- 新增 NLP 情感分析（讯飞星火 API）
- 新增多种交互式图表（桑基图、旭日图、平行坐标轴）

---

## 团队

华东师范大学
