"""落库：SQLite 上的 ods / reject / dwd / ads + 批次账本。

为什么强调**幂等**：调度器重试、手工补跑、上游重推文件都是日常。
做法是三层保险：
  1. batch_id 用文件内容哈希 —— 同文件重跑得到同一批次，`batch_log` 里已有就直接跳过；
  2. dwd 用主键 UPSERT（INSERT OR REPLACE），重复投递不产生重复行；
  3. 写完立刻回读行数，跟批次账本核对，不一致算失败。
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from . import config


def connect(path=None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn, datasets=None):
    datasets = datasets or config.DATASETS
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS batch_log (
        batch_id TEXT PRIMARY KEY, dataset TEXT, source TEXT,
        rows_in INTEGER, rows_good INTEGER, rows_rejected INTEGER,
        quality_status TEXT, crs TEXT, crs_source TEXT, reasons TEXT, finished_at TEXT
    );
    CREATE TABLE IF NOT EXISTS quality_report (
        batch_id TEXT, dataset TEXT, rule TEXT, col TEXT,
        checked INTEGER, failed INTEGER, rate REAL, status TEXT
    );
    CREATE TABLE IF NOT EXISTS ads_metrics (
        metric TEXT, key TEXT, value REAL, unit TEXT, batch_id TEXT, computed_at TEXT,
        PRIMARY KEY (metric, key)
    );
    CREATE TABLE IF NOT EXISTS field_lineage (
        dataset TEXT, layer TEXT, field TEXT, source_file TEXT, source_field TEXT, transform TEXT
    );
    CREATE TABLE IF NOT EXISTS metric_lineage (
        metric TEXT PRIMARY KEY, dataset TEXT, sources TEXT, unit TEXT, descr TEXT
    );
    """)
    for name in datasets:
        conn.execute(f"CREATE TABLE IF NOT EXISTS ods_{name} (batch_id TEXT, source TEXT, __src_row INTEGER, payload TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS reject_{name} (batch_id TEXT, __src_row INTEGER, __rule TEXT, __reason TEXT, payload TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS dwd_{name} (batch_id TEXT, payload TEXT)")
    # dwd 加主键索引（UPSERT 的基础）
    for name, spec in datasets.items():
        pk = spec["pk"]
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_dwd_{name} ON dwd_{name} (json_extract(payload, '$.{pk}'))")
    conn.commit()


def _row_payload(row):
    d = {}
    for k, v in dict(row).items():
        if k == "geometry":
            d[k] = v.wkt if v is not None and hasattr(v, "wkt") else None
        elif isinstance(v, float) and pd.isna(v):
            d[k] = None
        elif v is None or (isinstance(v, float) and pd.isna(v)):
            d[k] = None
        else:
            d[k] = round(v, 6) if isinstance(v, float) else (v.item() if hasattr(v, "item") else v)
    return json.dumps(d, ensure_ascii=False)


def write_ods(conn, dataset, df, batch_id, source):
    rows = [(batch_id, source, int(r.get("__src_row", i)), _row_payload(r)) for i, r in df.iterrows()]
    conn.executemany(f"INSERT INTO ods_{dataset} (batch_id, source, __src_row, payload) VALUES (?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def write_reject(conn, dataset, reject, batch_id):
    if not len(reject):
        return 0
    rows = [(batch_id, int(r.get("__src_row", i)), r.get("__rule"), r.get("__reason"), _row_payload(r))
            for i, r in reject.iterrows()]
    conn.executemany(
        f"INSERT INTO reject_{dataset} (batch_id, __src_row, __rule, __reason, payload) VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def write_dwd(conn, dataset, good, batch_id, spec):
    """主键 UPSERT：同主键重复投递只更新，不新增。"""
    if not len(good):
        return 0
    pk = spec["pk"]
    rows = [(batch_id, _row_payload(r)) for _, r in good.iterrows()]
    conn.executemany(
        f"INSERT OR REPLACE INTO dwd_{dataset} (batch_id, payload) VALUES (?, ?)", rows)
    conn.commit()
    return conn.execute(f"SELECT COUNT(*) FROM dwd_{dataset}").fetchone()[0]


def read_dwd(conn, dataset):
    df = pd.read_sql_query(f"SELECT payload FROM dwd_{dataset}", conn)
    if df.empty:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(p) for p in df["payload"]])


def batch_seen(conn, batch_id):
    row = conn.execute("SELECT 1 FROM batch_log WHERE batch_id = ?", (batch_id,)).fetchone()
    return row is not None


def log_batch(conn, **kw):
    kw["reasons"] = json.dumps(kw.get("reasons") or [], ensure_ascii=False)
    kw["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = ",".join(kw)
    marks = ",".join("?" * len(kw))
    conn.execute(f"INSERT OR REPLACE INTO batch_log ({cols}) VALUES ({marks})", tuple(kw.values()))
    conn.commit()


def log_quality(conn, batch_id, dataset, report):
    conn.executemany(
        "INSERT INTO quality_report (batch_id, dataset, rule, col, checked, failed, rate, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(batch_id, dataset, r["rule"], r.get("col"), r["checked"], r["failed"], r["rate"], r["status"])
         for r in report])
    conn.commit()


def write_metric(conn, metric, key, value, unit, batch_id):
    conn.execute("INSERT OR REPLACE INTO ads_metrics (metric, key, value, unit, batch_id, computed_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (metric, key, float(value), unit, batch_id,
                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def write_metric_lineage(conn, metric, spec):
    conn.execute("INSERT OR REPLACE INTO metric_lineage (metric, dataset, sources, unit, descr) "
                 "VALUES (?,?,?,?,?)",
                 (metric, spec["dataset"], json.dumps(spec["sources"], ensure_ascii=False),
                  spec["unit"], spec["desc"]))
    conn.commit()


def write_field_lineage(conn, dataset, layer, fields, source_file, transforms):
    conn.execute("DELETE FROM field_lineage WHERE dataset = ? AND layer = ?", (dataset, layer))
    conn.executemany(
        "INSERT INTO field_lineage (dataset, layer, field, source_file, source_field, transform) "
        "VALUES (?,?,?,?,?,?)",
        [(dataset, layer, f, source_file, f, transforms.get(f, "直传")) for f in fields])
    conn.commit()


def counts(conn, dataset):
    q = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}_{dataset}").fetchone()[0]
    return {"ods": q("ods"), "reject": q("reject"), "dwd": q("dwd")}
