# gis-datahub

**GIS 空间数据中台（mini）**：多源摄取 → 质量治理 → 分层落库 → 空间指标服务 → 字段级血缘。

规模刻意做小（一台 16G 笔记本能跑通、零外部依赖），但把数据中台最要紧的几件事都做对了：
**分层、质量门禁、幂等、口径可复算、血缘可追、越权面收窄**。

```
data/raw/*.geojson|*.shp|*.csv
        │
        ├─ ingest    读入 + 不猜坐标系（缺 .prj 就标 unknown）
        ├─ quality   规则是数据（JSON）；不合格行进 reject 表，带 rule + 源行号
        ├─ store     ods / reject / dwd / ads 四层 + 批次账本（SQLite）
        ├─ metrics   只读 dwd 复算指标，口径写在 metric_lineage
        └─ api       FastAPI：只开预定义指标接口，任意 SQL 一律 403
```

## 快速开始

```bash
python -m hub.sampledata        # 生成带缺陷的样例数据（重复主键 / 空几何 / 负面积 / 缺 CRS / 空文件）
python -m hub.pipeline --all    # 跑全量批次
python -m pytest tests -q       # 9 条单测

uvicorn hub.api:app --port 8020 # 指标服务
curl "127.0.0.1:8020/metrics/summary"
```

一次批次的返回就是**可断言的摘要**（评测和运维看的是同一份事实）：

```json
{"dataset": "parcels", "batch_id": "61b82bf563de331f", "rows_in": 6, "rows_good": 3,
 "rows_rejected": 3, "quality_status": "warn",
 "reasons": ["parcel_id 重复（1 行）", "area_m2 超出 0~+inf（1 行）", "几何为空或非法（1 行）"],
 "crs": "EPSG:4490", "crs_source": "prj", "dwd_rows": 3,
 "metrics": [["parcels_total_area_km2", "__all__", 0.0044, "km2"]]}
```

## 四层各自负责什么

| 层 | 表 | 作用 |
|---|---|---|
| ODS | `ods_<dataset>` | 原样落地（payload + 批次 + 源行号），一行不改，可回溯 |
| REJECT | `reject_<dataset>` | 不合格行 + `__rule` / `__reason`，质量问题永远可定位 |
| DWD | `dwd_<dataset>` | 类型统一、主键唯一、几何合法、CRS 明确；主键 UPSERT |
| ADS | `ads_metrics` | 指标宽表，API 直接查 |
| 账本 | `batch_log` / `quality_report` / `metric_lineage` / `field_lineage` | 批次、质量、血缘 |

## 三处"看起来能省、其实不能省"的设计

**1. 不猜坐标系。** Shapefile 缺 `.prj` 时 `crs=None`，该批次直接判 `fail` 拒绝入库。
真实教训：默认按 WGS84 算面积，结果会差几个数量级，而且错得很安静。

**2. 幂等靠内容哈希。** `batch_id = sha256(文件)`，`batch_log` 里已存在就跳过；dwd 是主键 UPSERT。
调度重试、手工补跑、上游重推文件都不会导致重复入账。

**3. 指标只读 dwd。** 指标不复算源文件、不依赖入库顺序。任何人拿同一份 dwd 都该算出同一个数——
`test_metric_recomputable` 就是守这条。

## 质量规则（数据驱动）

`hub/config.py` 里每个数据集声明字段、主键、分区键和规则：

```python
"rules": [
    {"type": "not_null", "col": "parcel_id"},
    {"type": "unique",   "col": "parcel_id"},
    {"type": "range",    "col": "area_m2", "min": 0},
    {"type": "geom_valid"},          # 空几何与非法几何都拦（is_empty / is_valid）
]
```

单条规则失败率超过 `FAIL_RATE_LIMIT`（35%）时整批判 `fail`：宁可不出数，也不出错数。

## 接口

| 接口 | 说明 |
|---|---|
| `GET /health` | 存活 + 批次数 |
| `GET /datasets` | 各数据集 dwd 行数、拒绝行数、最近批次 |
| `GET /quality/latest?dataset=parcels` | 最近批次逐条规则结果 |
| `GET /metrics/summary[?metric=]` | 指标值（带 batch_id） |
| `GET /lineage/{metric}` | 指标 → dwd 字段 → 源字段 |
| `POST /query` | **恒返回 403**：不提供自由 SQL |

## 接入评测平台

这个中台是 [`agent-eval`](https://github.com/Whybeyou/agent-eval) 的第 5 个被测系统，
suite 在那边：`suites/gis_datahub/`（11 条用例，数据事实断言：行数、拒绝分布、指标值、幂等、
血缘、接口契约、越权拒绝）。

```bash
export GIS_DATAHUB_HOME=$(pwd)
python -m evalkit.cli run /path/to/agent-eval/suites/gis_datahub
```

评测在开发期抓到的 4 个真实缺陷（都已修，并留了回归）：

1. 空 CSV → pandas `EmptyDataError` 直接崩，应该在质量层判 `fail` 而不是抛异常；
2. `api.py` 用了 `str | None`，Python 3.9 下 `TypeError`（接口 500）；
3. `Polygon()` 的 `is_valid=True`，空几何漏过校验（必须单独判 `is_empty`）；
4. 拒绝原因只在返回值里，没有进产物/答案 —— 人看日志和断言都看不到，等于没定位信息。

## 目录

```
hub/{config,ingest,quality,store,metrics,pipeline,api,sampledata}.py
tests/test_hub.py        9 条：分流 / 幂等 / upsert / CRS 门禁 / 指标复算 / 血缘 / 越权 / 空批
data/raw/                样例数据（带缺陷）
```

## 许可

MIT
