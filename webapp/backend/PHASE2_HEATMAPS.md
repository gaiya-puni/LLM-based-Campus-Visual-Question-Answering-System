# 第二阶段：校园场景热力图

## 范围与启动

只实现赏花、拍照、散步休息、浪漫约会，普陀/闵行各一套，共8张。
不修改原有POI、场景语料、向量索引或规则排序函数；不包含分时停车。

在 `webapp/backend` 执行：

```powershell
python build_scene_heatmaps.py
python server.py
```

默认只使用本机已有BGE模型。没有模型时，可显式允许一次下载：

```powershell
python build_scene_heatmaps.py --allow-download
```

前端仍按原方式启动。进入“智能问答”，左上角可切换“场景热图/原版推荐”，
选择四类场景查看，或输入“闵行校区哪里适合散步”。
“查看热图”是独立地图浏览，不改写右侧已有对话；“隐藏热图”保留参考标记。
修改代码后须手动重启后端（原项目已关闭自动重载）。

## 对第一阶段的隔离

- 请求不传 `recommendationMode` 时，保持原有规则推荐；传 `rule` 也相同。
- 仅传 `sakde` 且为宽泛四类场景时，才接管回答。精确停车、特定植物、学院附近、餐饮等继续走原逻辑。
- 新回答根据热图返回的真实POI和解释确定性组装，不通过LLM改写地名，保证文字和标记一致。
- 原版入口及其LLM调用不变；新模块无实时网络调用。
- 缓存缺失/损坏/过期时返回明确状态，聊天回退原逻辑；不会冒充向量热图。
- `HEATMAP_ENABLED=false` 可关闭新服务。运行时只读取缓存，不触发重建或下载。
- 生成目录 `heatmap_cache/` 独立于 `semantic_index.npz`。打包ZIP可以包含它；使用Git分发需运行构建脚本。

## 方法及论文适配

参考 SenseMap (IEEE TVCG 2024) IV-C/D/E，方法名称为 `sakde-campus-1`，
不是宣称逐项复现原文城市实验。

1. POI细分类使用植物物种 `subCategory`。类语料使用已有植物模板的名称、科、习性、形态、文化与生长描述，
   不使用私人认养留言或坐标推断场景。使用BGE中文向量计算细分类与场景定义的余弦相似度。
2. 两校区独立计算。现有坐标沿用高德GCJ-02约定，在校区中心附近转成米制局部平面坐标，避免用经纬度角度计算带宽。
   实际源数据坐标系仍需数据维护者确认；不擅自混做WGS84转换。
3. 以200米规则网格替代论文的交通分析区TAZ。统计同类POI在分区中的数量占该校区同类总数的比例p，
   分区固定锚定校区中心，不随显示网格的分辨率或边缘移动。
   以及同类POI的平均最近邻距离ANND。影响半径 `r=exp(p)*max(ANND, d_min)`，带宽 `h=r/3`。
   校园默认 `d_min=80m`、`r<=300m`，这些是可调参数，非实测最优值。单点类使用最小影响半径作为基值。
4. 类语义系数 `beta=log(lambda*classCount/rank)`，默认 `lambda=0.75`。
   校园版本默认截断负值：与散步不相关不等于有害，不能仅凭排名将植物判为负面因素。
   可在配置中设 `negativeContribution=paper_signed` 对照原文正负系数；重建后生效。
5. 分类核密度使用论文径向公式 `mean(G(distance/h)/h)`，3倍带宽以外截断。
   各类密度图分别Min-Max到0~1，再按非负语义相似度归一化加权融合。常量/空字段置0。
   注意：分类Min-Max会消去beta正值的幅度，保留正负方向；实现和解释均不把幅度再算一次。
6. 最终每校区、每场景单独归一化。显示0~100相对强度，不是置信概率，不能跨校区/跨场景比较绝对高低。

20米显示网格与200米统计分区是不同概念。参数集中在 `heatmap_config.json`。
配置、场景语料、POI、模板、算法源码和模型标识被哈希绑定；这些内容变更必须重新构建。
缓存先逐张写入，最后提交manifest，避免读到混合版本。

## 数据与推荐限制

- 去除重复ID、同物种同坐标记录、非法坐标、远离所属校区的点以及已知跨校区错名。
  仅第二阶段排除，不改原始文件。检查结果见manifest的audit。
- 未获得正式校界/TAZ/路网数据：当前采用POI凸包与100米邻近支持区交集作为**数据覆盖掩膜**，
  不能称其为正式校界或可通行范围。图像外和无数据区域透明。
- 对热图做局部高值搜索和最小间距去重，90米内关联真实POI作为参考标记，标记不放在算法生成的虚构点。
- 地点名称来自该POI的 `locationName`，不能从“河边、桥”推断出特定河名。
- 无路网情况下不保证参考POI可直接步行到达，界面明确提示，不能把热图等同于导航路线。
- 首批数据主要是植物，散步和约会仍是植物景观的代理指标，无噪声、人流、座椅、照明、实时花期等证据。
  不伪造这些属性；不同场景热图可能相近，须用评测和后续数据完善而非强行制造差异。

## 接口

`GET /api/heatmaps?campus=闵行&scene=walk`

成功返回 `algorithm/campus/scene/grid/places/classContributions/disclaimer/sourceHash`。
grid包含宽高、图像边界 `[west,south,east,north]`、北向南逐行的0~1000值及无数据值-1。
前端将这个栅格直接绘成PNG并用高德ImageLayer叠加，不再使用高德热力插件进行二次KDE。
参数错误400，缓存不可用503；不接受任意文件路径。

`POST /api/chat` 新增可选 `recommendationMode: "sakde" | "rule"`。
命中时附加 `heatmap`，`locations`与`ranked_places`同源。
回退时标记 `recommendation_engine=rule` 与 `heatmap_status`。

## 验证与对照

```powershell
python test_recommendation_v2.py
python test_scene_heatmaps.py
python evaluate_semantic_queries.py --strict
python evaluate_scene_heatmaps.py --sensitivity
```

评测输出 `heatmap_cache/evaluation.json`，含原版/KDE/SAKDE地点列表、各场景差异、
读取耗时、10/20/30米网格敏感性。纯KDE使用统一带宽和同权POI，不使用语义。
这些是技术与结构对照，不是推荐正确率。

补充人工标注后，可用 `--labels labels.json` 统计Top2是否命中认可的POI：

```json
[{"campus":"闵行","scene":"walk","acceptablePoiIds":["由组员核实后填写的POI ID"]}]
```

不要把未标注数据当作准确率100%。正式实验仍应由组员核对推荐地点、可达性、场景适宜度，
并与原版和普通KDE进行盲评。
