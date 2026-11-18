"""指标层：从 dwd 算 ads 指标，口径与来源写在 metric_lineage 里。

指标必须**可复算**：任何人拿同一份 dwd 都能算出同一个数，所以这里不读源文件、
不依赖入库顺序，只读 dwd。
"""
import pandas as pd

from . import config, store


def _finite(v):
    try:
        return round(float(v), 6)
    except Exception:
        return None


def compute(conn, dataset, batch_id):
    """返回 [(metric, key, value, unit)]；空表返回空列表。"""
    spec = config.DATASETS[dataset]
    dwd = store.read_dwd(conn, dataset)
    out = []
    if dwd.empty:
        return out

    if dataset == "parcels":
        total = pd.to_numeric(dwd["area_m2"], errors="coerce").sum() / 1e6
        out.append(("parcels_total_area_km2", "__all__", _finite(total), "km2"))
        for lu, n in dwd["landuse"].fillna("未分类").value_counts().items():
            out.append(("parcels_by_landuse", str(lu), int(n), "count"))

    elif dataset == "cities":
        d = dwd.copy()
        d["density"] = pd.to_numeric(d["pop_wan"], errors="coerce") * 1e4 / \
            pd.to_numeric(d["area_km2"], errors="coerce").replace(0, pd.NA)
        for _, r in d.nlargest(3, "density").iterrows():
            out.append(("cities_pop_density_top3", str(r["name"]), _finite(r["density"]), "人/km2"))

    elif dataset == "shops":
        store_dwd = dwd.copy()
        for city, g in store_dwd.groupby("city_name"):
            out.append(("shops_sales_by_city", str(city),
                        _finite(pd.to_numeric(g["sales"], errors="coerce").sum()), "元"))

    for metric, key, value, unit in out:
        store.write_metric(conn, metric, key, value, unit, batch_id)
    return out


def register_lineage(conn):
    for m, spec in config.METRICS.items():
        store.write_metric_lineage(conn, m, spec)


def summary(conn, metric=None):
    sql = "SELECT metric, key, value, unit, batch_id, computed_at FROM ads_metrics"
    params = ()
    if metric:
        sql += " WHERE metric = ?"
        params = (metric,)
    sql += " ORDER BY metric, key"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def lineage_tree(conn, metric):
    """指标体系的血缘：指标 → dwd 字段 → 源字段。"""
    row = conn.execute("SELECT * FROM metric_lineage WHERE metric = ?", (metric,)).fetchone()
    if row is None:
        return None
    import json
    sources = json.loads(row["sources"])
    fields = []
    for s in sources:
        layer, _, field = s.partition(".")
        fields.append({"layer": layer, "field": field, "from": f"ods_{row['dataset']}.{field}"})
    return {"metric": metric, "dataset": row["dataset"], "unit": row["unit"],
            "desc": row["descr"], "fields": fields}
