"""指标服务（FastAPI）：只暴露预定义接口，任意 SQL 一律拒绝。

这是一个数据中台面向"上层应用/Agent"的出口。设计上刻意收窄：
  * 没有 /query 的自由 SQL 口子（真实中台里这条口子往往是最大的越权面）；
  * 每个响应都带 batch_id，能回溯到具体批次与血缘。
"""
import time

from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from . import config, metrics, store

app = FastAPI(title="GIS 空间数据指标服务", version="0.1.0")

_START = time.time()


def _conn():
    conn = store.connect()
    store.ensure_schema(conn)
    return conn


@app.get("/health")
def health():
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM batch_log").fetchone()[0]
    return {"status": "ok", "batches": n, "uptime_s": round(time.time() - _START, 1)}


@app.get("/datasets")
def datasets():
    conn = _conn()
    out = []
    for name, spec in config.DATASETS.items():
        rows = conn.execute(f"SELECT COUNT(*) FROM dwd_{name}").fetchone()[0]
        rej = conn.execute(f"SELECT COUNT(*) FROM reject_{name}").fetchone()[0]
        latest = conn.execute(
            "SELECT batch_id, quality_status, finished_at FROM batch_log WHERE dataset = ? "
            "ORDER BY finished_at DESC LIMIT 1", (name,)).fetchone()
        out.append({"dataset": name, "dwd_rows": rows, "reject_rows": rej,
                    "latest_batch": dict(latest) if latest else None})
    return {"datasets": out}


@app.get("/quality/latest")
def quality_latest(dataset: str = Query(...)):
    if dataset not in config.DATASETS:
        raise HTTPException(404, f"未知数据集 {dataset}")
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM quality_report WHERE dataset = ? AND batch_id = ("
        "SELECT batch_id FROM batch_log WHERE dataset = ? ORDER BY finished_at DESC LIMIT 1)",
        (dataset, dataset)).fetchall()
    return {"dataset": dataset, "rules": [dict(r) for r in rows]}


@app.get("/metrics/summary")
def metrics_summary(metric: Optional[str] = None):
    conn = _conn()
    rows = metrics.summary(conn, metric)
    return {"metric": metric, "count": len(rows), "items": rows}


@app.get("/lineage/{metric}")
def lineage(metric: str):
    conn = _conn()
    tree = metrics.lineage_tree(conn, metric)
    if tree is None:
        raise HTTPException(404, f"未登记的指标 {metric}")
    return tree


@app.post("/query")
def query(payload: dict):
    """故意拒绝：中台不提供自由 SQL 入口。"""
    raise HTTPException(403, "拒绝：中台只开放预定义指标接口，不接受任意 SQL 查询")
