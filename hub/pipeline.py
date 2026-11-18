"""编排：一个文件 = 一个批次，走 摄取 → 质量 → 落库 → 指标 → 血缘。

刻意做对的三件事：
  * 批次幂等：同一份文件重跑直接跳过（返回 skipped=True），不重复入账；
  * 失败不静默：质量 fail / CRS 缺失时写 reject 与账本，不写 dwd、不产指标；
  * 每次返回**可断言的摘要**（行数、状态、指标值），评测与运维看的是同一份事实。

CLI：
    python -m hub.pipeline --all                 # 跑 data/raw 下所有数据集
    python -m hub.pipeline --file data/raw/parcels_inc.geojson
"""
import argparse
import json
import os
import sys

from . import config, ingest, metrics, quality, store

TRANSFORMS = {
    "geometry": "WKT（WGS84/CGCS2000 原样保留，不做隐式转换）",
    "area_m2": "去负值（<0 进 reject）",
    "pop_wan": "缺失进 reject",
}


def run_file(path, db=None, force=False):
    """处理一个源文件，返回摘要 dict。"""
    dataset = os.path.splitext(os.path.basename(path))[0].split("_")[0]
    if dataset not in config.DATASETS:
        raise ValueError(f"未知数据集：{dataset}（可用：{list(config.DATASETS)}）")
    spec = config.DATASETS[dataset]

    conn = store.connect(db) if isinstance(db, str) else (db or store.connect())
    store.ensure_schema(conn)
    metrics.register_lineage(conn)

    bid = ingest.batch_id(path)
    if not force and store.batch_seen(conn, bid):
        return {"dataset": dataset, "batch_id": bid, "skipped": True, "reason": "批次已处理（幂等跳过）"}

    df, meta = ingest.read_source(path)
    result = quality.run(df, spec, meta)

    store.write_ods(conn, dataset, df, bid, os.path.basename(path))
    rejects = store.write_reject(conn, dataset, result["reject"], bid)
    store.log_quality(conn, bid, dataset, result["report"])

    ok = result["status"] != "fail"
    dwd_total = store.counts(conn, dataset)["dwd"]
    if ok:
        dwd_total = store.write_dwd(conn, dataset, result["good"], bid, spec)
        computed = metrics.compute(conn, dataset, bid)
        store.write_field_lineage(conn, dataset, "dwd_" + dataset,
                                  [f for f in spec["fields"]], os.path.basename(path), TRANSFORMS)

    store.log_batch(conn, batch_id=bid, dataset=dataset, source=os.path.basename(path),
                    rows_in=len(df), rows_good=len(result["good"]), rows_rejected=rejects,
                    quality_status=result["status"], crs=meta.get("crs"),
                    crs_source=meta.get("crs_source"), reasons=result["reasons"])

    return {
        "dataset": dataset, "batch_id": bid, "skipped": False,
        "file": os.path.basename(path), "rows_in": len(df),
        "rows_good": len(result["good"]), "rows_rejected": rejects,
        "quality_status": result["status"], "reasons": result["reasons"],
        "crs": meta.get("crs"), "crs_source": meta.get("crs_source"),
        "dwd_rows": dwd_total, "metrics": computed if ok else [],
        "load_ok": ok,
    }


def run_all(db=None, raw_dir=None):
    out = []
    for dataset, files in ingest.discover(raw_dir).items():
        for f in files:
            out.append(run_file(f, db=db))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="GIS 数据中台：跑一批数据")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--db", default=None)
    ap.add_argument("--force", action="store_true", help="忽略幂等，强制重跑")
    args = ap.parse_args(argv)

    results = []
    if args.all:
        results = run_all(db=args.db)
    for f in args.file:
        results.append(run_file(f, db=args.db, force=args.force))
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
