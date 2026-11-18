"""配置：路径、数据源规格、质量规则、指标口径。

一份 JSON/YAML 就能描述一个数据集该长什么样——这是"治理"和"脚本"的区别。
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("HUB_DATA", os.path.join(BASE, "data"))
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.environ.get("HUB_DB", os.path.join(DATA_DIR, "hub.sqlite3"))

# ---------------------------------------------------------------- 数据集规格
# geometry: 是否空间表；crs: 期望坐标系（None 表示不校验）
DATASETS = {
    "parcels": {
        "kind": "vector", "geometry": "Polygon", "crs": "EPSG:4490",
        "pk": "parcel_id", "partition": "landuse",
        "fields": {
            "parcel_id": "str", "owner": "str", "landuse": "str",
            "area_m2": "float",
        },
        "rules": [
            {"type": "not_null", "col": "parcel_id"},
            {"type": "unique", "col": "parcel_id"},
            {"type": "not_null", "col": "landuse"},
            {"type": "range", "col": "area_m2", "min": 0},
            {"type": "geom_valid"},
        ],
    },
    "cities": {
        "kind": "vector", "geometry": "Point", "crs": "EPSG:4326",
        "pk": "city_id", "partition": "province",
        "fields": {
            "city_id": "str", "name": "str", "province": "str",
            "pop_wan": "float", "area_km2": "float",
        },
        "rules": [
            {"type": "not_null", "col": "city_id"},
            {"type": "unique", "col": "city_id"},
            {"type": "not_null", "col": "pop_wan"},
            {"type": "range", "col": "pop_wan", "min": 0},
            {"type": "range", "col": "area_km2", "min": 0},
            {"type": "geom_valid"},
        ],
    },
    "shops": {
        "kind": "table", "geometry": None, "crs": None, "pk": "shop_id", "partition": None,
        "fields": {"shop_id": "str", "city_name": "str", "sales": "float", "category": "str"},
        "rules": [
            {"type": "not_null", "col": "shop_id"},
            {"type": "unique", "col": "shop_id"},
            {"type": "not_null", "col": "sales"},
            {"type": "range", "col": "sales", "min": 0},
        ],
    },
}

# ---------------------------------------------------------------- 指标口径
# 血缘就是从这些表达式反推出来的（ads 字段 ← dwd 字段）
METRICS = {
    "parcels_total_area_km2": {
        "dataset": "parcels", "expr": "sum(area_m2) / 1e6",
        "sources": ["dwd_parcels.area_m2"], "unit": "km2",
        "desc": "地块面积合计（平方千米）",
    },
    "parcels_by_landuse": {
        "dataset": "parcels", "expr": "count(*) group by landuse",
        "sources": ["dwd_parcels.landuse"], "unit": "count",
        "desc": "按用地类型的宗地数量",
    },
    "cities_pop_density_top3": {
        "dataset": "cities", "expr": "pop_wan*10000/area_km2 order by desc limit 3",
        "sources": ["dwd_cities.pop_wan", "dwd_cities.area_km2"], "unit": "人/km2",
        "desc": "人口密度最高的 3 个城市",
    },
    "shops_sales_by_city": {
        "dataset": "shops", "expr": "sum(sales) group by city_name",
        "sources": ["dwd_shops.city_name", "dwd_shops.sales"], "unit": "元",
        "desc": "各城市门店销售额合计",
    },
}

# 中台对外只暴露这些接口，任意 SQL 一律拒绝（越权面收窄）
ALLOWED_QUERIES = {"metrics_summary", "quality_latest", "datasets", "lineage"}

MAX_ROWS_PER_BATCH = 200_000

# 全量批次时跳过的文件（它们是负向用例的素材，由评测/测试显式触发）
SKIP_STEMS = {"parcels_nocrs", "shops_empty", "empty", "broken"}
