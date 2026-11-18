"""样例数据生成：故意注入质量问题，让治理逻辑有活干。

注入的缺陷（与 cases 一一对应）：
  parcels  : 重复 parcel_id ×1、空几何 ×1、负面积 ×1
  cities   : pop_wan 缺失 ×1
  shops    : 重复 shop_id ×1、sales 非数值 ×1
  parcels_nocrs : 丢了 .prj 的 Shapefile（CRS 未知）
  parcels_inc   : 增量批次（2 条新地块，用于 upsert 测试）
  shops_empty.csv : 空文件（用于 fail-fast 测试）
"""
import os

import numpy as np
import pandas as pd

from . import config


def _poly(x, y, size=0.01):
    from shapely.geometry import Polygon

    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def parcels():
    from shapely.geometry import Polygon

    rows = [
        {"parcel_id": "P001", "owner": "张三", "landuse": "住宅", "area_m2": 1200.0, "geometry": _poly(116.30, 39.90)},
        {"parcel_id": "P002", "owner": "李四", "landuse": "商业", "area_m2": 2400.0, "geometry": _poly(116.31, 39.90)},
        {"parcel_id": "P002", "owner": "李四(重复)", "landuse": "商业", "area_m2": 2400.0, "geometry": _poly(116.31, 39.90)},
        {"parcel_id": "P003", "owner": "王五", "landuse": "工业", "area_m2": 5000.0, "geometry": Polygon()},
        {"parcel_id": "P004", "owner": "赵六", "landuse": "住宅", "area_m2": -100.0, "geometry": _poly(116.32, 39.91)},
        {"parcel_id": "P005", "owner": "孙七", "landuse": "绿地", "area_m2": 800.0, "geometry": _poly(116.33, 39.91)},
    ]
    return rows


def cities():
    return [
        {"city_id": "C001", "name": "南昌", "province": "江西", "pop_wan": 643.0, "area_km2": 7195.0,
         "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(115.89, 28.68)},
        {"city_id": "C002", "name": "上海", "province": "上海", "pop_wan": 2487.0, "area_km2": 6340.0,
         "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(121.47, 31.23)},
        {"city_id": "C003", "name": "深圳", "province": "广东", "pop_wan": 1768.0, "area_km2": 1997.0,
         "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(114.06, 22.55)},
        {"city_id": "C004", "name": "拉萨", "province": "西藏", "pop_wan": np.nan, "area_km2": 29518.0,
         "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(91.11, 29.97)},
        {"city_id": "C005", "name": "杭州", "province": "浙江", "pop_wan": 1237.0, "area_km2": 16850.0,
         "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(120.15, 30.28)},
    ]


def shops():
    return [
        {"shop_id": "S001", "city_name": "南昌", "sales": 12000.0, "category": "数码"},
        {"shop_id": "S002", "city_name": "南昌", "sales": 8000.0, "category": "服饰"},
        {"shop_id": "S002", "city_name": "南昌", "sales": 8000.0, "category": "服饰"},
        {"shop_id": "S003", "city_name": "上海", "sales": "abc", "category": "家电"},
        {"shop_id": "S004", "city_name": "杭州", "sales": 25000.0, "category": "数码"},
    ]


def write_all(raw_dir=None):
    import geopandas as gpd

    raw_dir = raw_dir or config.RAW_DIR
    os.makedirs(raw_dir, exist_ok=True)

    gpd.GeoDataFrame(parcels(), geometry="geometry", crs="EPSG:4490").to_file(
        os.path.join(raw_dir, "parcels.geojson"), driver="GeoJSON")
    gpd.GeoDataFrame(cities(), geometry="geometry", crs="EPSG:4326").to_file(
        os.path.join(raw_dir, "cities.geojson"), driver="GeoJSON")
    pd.DataFrame(shops()).to_csv(os.path.join(raw_dir, "shops.csv"), index=False, encoding="utf-8-sig")

    # 增量批次：2 条新地块
    inc = [
        {"parcel_id": "P006", "owner": "周八", "landuse": "住宅", "area_m2": 1500.0, "geometry": _poly(116.34, 39.92)},
        {"parcel_id": "P007", "owner": "吴九", "landuse": "商业", "area_m2": 3100.0, "geometry": _poly(116.35, 39.92)},
    ]
    gpd.GeoDataFrame(inc, geometry="geometry", crs="EPSG:4490").to_file(
        os.path.join(raw_dir, "parcels_inc.geojson"), driver="GeoJSON")

    # 丢了 .prj 的 Shapefile：写出来再删投影文件
    shp = os.path.join(raw_dir, "parcels_nocrs")
    gpd.GeoDataFrame(parcels()[:3], geometry="geometry", crs="EPSG:4490").to_file(shp + ".shp", driver="ESRI Shapefile")
    for ext in (".prj",):
        p = shp + ext
        if os.path.exists(p):
            os.remove(p)

    # 空文件也要挂在已知数据集名下，才能测「空批次 fail-fast」而不是「未知数据集」
    open(os.path.join(raw_dir, "shops_empty.csv"), "w", encoding="utf-8").close()
    return raw_dir


if __name__ == "__main__":
    print("样例数据已写入", write_all())
