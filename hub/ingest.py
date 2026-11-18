"""摄取：把多源文件读成统一的 DataFrame + 源元信息。

关键点：
  * **不猜坐标系**。Shapefile 缺 .prj 就标记 crs=None + crs_source="missing"，
    绝不像很多脚本那样默认 4326——后面指标会因此偏几百万倍。
  * 保留源文件名与行号，出问题能回溯到具体哪一行。
"""
import hashlib
import os

import pandas as pd

from . import config

SPATIAL_EXT = {".geojson": "GeoJSON", ".json": "GeoJSON", ".shp": "Shapefile"}
TABLE_EXT = {".csv": "CSV"}


def _read_vector(path):
    import geopandas as gpd

    gdf = gpd.read_file(path)
    crs = gdf.crs
    return gdf, {
        "format": SPATIAL_EXT.get(os.path.splitext(path)[1].lower(), "vector"),
        "crs": str(crs) if crs is not None else None,
        "crs_source": "prj" if crs is not None else "missing",
        "geometry": gdf.geom_type.dropna().unique().tolist() if len(gdf) else [],
    }


def _read_table(path):
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            df = pd.read_csv(path, encoding=enc)
            return df, {"format": "CSV", "encoding": enc, "crs": None,
                        "crs_source": "n/a", "geometry": []}
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            # 空文件：不在这里崩，返回空表让质量层判"空批次 fail"
            return pd.DataFrame(), {"format": "CSV", "encoding": enc, "crs": None,
                                   "crs_source": "n/a", "geometry": [], "empty": True}
    raise ValueError(f"无法解码 {path}")


def read_source(path):
    """返回 (DataFrame, meta)。空间文件统一附加 `geometry` 列（shapely 对象）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in SPATIAL_EXT:
        obj, meta = _read_vector(path)
        df = pd.DataFrame(obj.drop(columns=obj.geometry.name))
        df["geometry"] = list(obj.geometry)
        df["__src_row"] = range(1, len(df) + 1)
        return df, meta
    if ext in TABLE_EXT:
        df, meta = _read_table(path)
        df["__src_row"] = range(1, len(df) + 1)
        return df, meta
    raise ValueError(f"不支持的格式：{ext}")


def batch_id(path):
    """按文件内容算批次号：同一份文件重跑得到同一个批次（幂等的基础）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def discover(raw_dir=None):
    """扫描目录，按数据集名归集文件（parcels_*.geojson / parcels.shp ...）。"""
    raw_dir = raw_dir or config.RAW_DIR
    out = {}
    for fn in sorted(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else []:
        stem, ext = os.path.splitext(fn)
        if ext.lower() not in set(SPATIAL_EXT) | set(TABLE_EXT):
            continue
        name = stem.split("_")[0]
        if name not in config.DATASETS or stem in config.SKIP_STEMS:
            continue
        out.setdefault(name, []).append(os.path.join(raw_dir, fn))
    return out
