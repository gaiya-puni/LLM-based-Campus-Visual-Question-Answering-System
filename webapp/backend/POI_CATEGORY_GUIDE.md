# 新增 POI 类别规范

本文档用于后续新增停车、咖啡、自习、运动等校园 POI 类别。目标是让新增类别尽量通过 JSON 和少量配置完成，避免在 `server.py` 里继续堆散乱分支。

## 当前统一入口

- POI 数据文件：`webapp/backend/*_pois.json`
- 场景配置：`webapp/backend/scene_profiles.json`
- 类别配置：`webapp/backend/server.py` 中的 `POI_CATEGORY_CONFIGS`
- 推荐入口：`rank_campus_pois()` / `rank_campus_places()`
- 地图输出：`/api/chat` 返回的 `locations`

`server.py` 会自动加载后端目录下所有 `*_pois.json` 文件。新增 `coffee_pois.json`、`parking_pois.json` 这类文件后，不需要再把文件名手动加入 `POI_DATA_FILES`。

## 一、POI 数据文件

新增类别时，优先新建独立文件：

```text
webapp/backend/coffee_pois.json
webapp/backend/parking_pois.json
webapp/backend/study_pois.json
```

文件内容必须是 JSON 数组，每条 POI 记录必须包含以下字段：

```json
{
  "id": "coffee_putuo_example",
  "category": "coffee",
  "subCategory": "咖啡",
  "name": "示例咖啡点",
  "locationName": "示例位置",
  "lng": 121.406,
  "lat": 31.227,
  "campus": "普陀",
  "source": "manual",
  "text": "示例咖啡点 普陀 咖啡 饮品 休息",
  "tags": ["咖啡", "饮品", "休息", "普陀"],
  "meta": {
    "address": "示例地址",
    "aliases": ["示例咖啡"],
    "notes": "可选备注"
  }
}
```

必填字段：

```text
id, category, subCategory, name, locationName, lng, lat, text, tags
```

推荐字段：

```text
campus, source, meta.address, meta.aliases
```

字段要求：

- `id` 全局唯一，建议格式为 `类别_校区_地点名`，例如 `coffee_putuo_liwa`。
- `category` 使用英文短标识，例如 `coffee`、`parking`。
- `subCategory` 使用中文细分类，例如 `咖啡`、`地面停车场`、`地下停车场`。
- `name` 是完整展示名，尽量包含学校和校区。
- `locationName` 是短位置名，适合地图弹窗展示。
- `lng`、`lat` 使用高德坐标系。
- `text` 是检索文本，要包含名称、别名、校区、用途、场景词。
- `tags` 必须是数组，不能是字符串。
- `meta.aliases` 用于精确查询，例如“丽娃咖啡在哪里”。

## 二、场景配置

如果该类别要参与“去哪里/哪里适合”这类场景推荐，需要在 `scene_profiles.json` 加场景。

咖啡示例：

```json
{
  "id": "coffee",
  "name": "咖啡休息",
  "intentKeywords": ["咖啡", "喝咖啡", "饮品", "休息", "坐一会儿"],
  "categories": ["coffee"],
  "terms": ["咖啡", "饮品", "休息", "安静"],
  "placeTerms": [],
  "categoryWeights": {
    "coffee": 55
  },
  "subCategoryWeights": {
    "咖啡": 12,
    "饮品": 8
  },
  "tagWeights": {
    "咖啡": 10,
    "饮品": 8,
    "休息": 6
  },
  "textWeights": {
    "咖啡": 6,
    "饮品": 5,
    "休息": 4
  },
  "campusBonus": 12,
  "nameMatchBonus": 40,
  "scoreCap": 95,
  "defaultTopK": 3,
  "returnAllWhenCampusSpecified": true
}
```

停车示例：

```json
{
  "id": "parking",
  "name": "停车",
  "intentKeywords": ["停车", "停车场", "车位", "开车", "泊车"],
  "categories": ["parking"],
  "terms": ["停车", "车位", "入口", "校门"],
  "placeTerms": ["校门", "入口", "路"],
  "categoryWeights": {
    "parking": 55
  },
  "tagWeights": {
    "停车": 10,
    "车位": 8,
    "入口": 6
  },
  "textWeights": {
    "停车": 6,
    "车位": 5,
    "入口": 4
  },
  "campusBonus": 12,
  "scoreCap": 95,
  "defaultTopK": 3
}
```

## 三、类别配置

场景推荐只依赖 `scene_profiles.json` 时，新类别可以先不改 `POI_CATEGORY_CONFIGS`，会走默认地点配置。

如果需要更好的精确查询、意图识别和回答文案，就在 `server.py` 的 `POI_CATEGORY_CONFIGS` 中增加类别。

咖啡示例：

```python
'coffee': {
    'groupBy': 'id',
    'directLookup': True,
    'intentPattern': r'咖啡|喝咖啡|饮品|坐一会儿',
    'exactMatchScore': 82,
    'exactMatchReason': '命中咖啡地点名称',
    'genericIntentScore': 40,
    'genericIntentReason': '校园咖啡/饮品地点',
    'scoreCap': 70,
    'exactScoreCap': 100,
    'dataReason': '校园咖啡位置数据',
    'contextMode': 'location',
    'contextName': '咖啡地点',
}
```

停车示例：

```python
'parking': {
    'groupBy': 'id',
    'directLookup': True,
    'intentPattern': r'停车|停车场|车位|泊车|开车',
    'exactMatchScore': 82,
    'exactMatchReason': '命中停车地点名称',
    'genericIntentScore': 40,
    'genericIntentReason': '校园停车地点',
    'scoreCap': 70,
    'exactScoreCap': 100,
    'dataReason': '校园停车位置数据',
    'contextMode': 'location',
    'contextName': '停车地点',
}
```

常用配置含义：

- `groupBy: 'id'`：一个 POI 一个推荐点，适合食堂、咖啡、停车。
- `groupBy: 'location'`：按位置聚合，适合大量植物点位。
- `directLookup: True`：允许“某个地点在哪里”直接返回，不加 Top。
- `intentPattern`：不依赖场景配置时，也能识别该类别意图。
- `exactMetaFields`：精确匹配使用的 `meta` 字段，默认包含 `aliases`、`amapName`、`sourceQuery`。
- `genericIntentScore`：泛问该类别时的基础分。
- `dataReason`：回答和推荐理由中的数据来源说明。
- `contextMode: 'location'`：回答中展示位置。
- `contextMode: 'plants'`：回答中展示代表植物，仅植物类使用。

## 四、测试问题

新增类别后至少跑这些问题：

```text
某个具体地点在哪里
当前校区哪里有该类别
我附近哪里有该类别
另一个校区哪里有该类别
完全不存在的同类地点在哪里
```

咖啡示例：

```text
丽娃咖啡在哪里
普陀校区哪里有咖啡
我附近哪里可以喝咖啡
闵行校区哪里有饮品
不存在咖啡店在哪里
```

停车示例：

```text
普陀校区哪里可以停车
闵行校区停车场在哪里
我附近哪里能停车
北门附近能停车吗
不存在停车场在哪里
```

已有轻量版回归问题也要保留：

```text
河西食堂在哪里
普陀校区去哪里吃饭
普陀校区哪里适合看花
校园哪里适合拍照
软件工程学院在哪里
```

## 五、验收标准

新增类别完成后，应满足：

- 新增 `*_pois.json` 能被自动加载。
- 所有 POI 必填字段完整，`tags` 是数组。
- 场景问题返回 `ranked_place`，地图能标注 Top。
- 精确地点查询只返回该地点，不使用 Top。
- 推荐理由中出现合理类别说明和距离说明。
- 没有数据的类别不应退化成植物/食堂推荐。
- 旧测试问题结果不被破坏。

## 六、第一阶段向量语义推荐

系统保留轻量关键词相似度作为兜底，并可通过 `semantic_retrieval.py` 加载本地 Sentence Transformers 模型和预计算索引。

推荐排序由三部分融合：

```text
用户问题 semantic vector
POI text/semanticText vector
scene profile vector
规则分 + 距离分 + 语义分融合排序
```

场景配置除关键词和权重外，还应提供：

```json
{
  "definition": "场景定义",
  "contextTexts": ["典型上下文"],
  "positiveQueries": ["语义相近的用户问法"],
  "negativeQueries": ["容易混淆但不属于该场景的问法"],
  "semanticThreshold": 0.42
}
```

修改 `scene_profiles.json` 或任意 `*_pois.json` 后重新生成索引：

```bash
cd webapp/backend
python build_semantic_index.py
python evaluate_semantic_queries.py --strict
```

索引由 POI 名称、别名、类别、标签、位置和描述构成。运行时先做确定性的精确地点匹配；只有未命中具体地点时，才使用场景向量和 POI 向量参与推荐。

扩展新类别时，优先补：

- `scene_profiles.json`：写清楚场景词、类别、权重。
- `*_pois.json`：在 `text` 和 `tags` 里补足用途、别名、场景词。
- `definition/contextTexts/positiveQueries`：提供比单一关键词更完整的场景语义。
- `server.py` 的 `_SEMANTIC_SYNONYM_GROUPS`：只作为模型不可用时的回退规则。
- `test_recommendation_v2.py`：新增至少 1 个精确查询、1 个场景推荐、1 个语义问法和 1 个不存在/未支持问法。

注意：如果某类 POI 数据还没建立，不要只靠语义相似度把问题退化成植物或食堂推荐。
