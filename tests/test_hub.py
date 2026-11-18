"""中台自身的单测（不依赖评测框架，纯 pytest）。

守的是管道语义：分流、幂等、upsert、CRS 门禁、指标口径、血缘、越权。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hub import config, metrics, pipeline, quality, sampledata, store  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    raw = tmp_path / "raw"
    sampledata.write_all(str(raw))
    db = str(tmp_path / "hub.sqlite3")
    return {"raw": str(raw), "db": db, "p": lambda n: str(raw / n)}


def test_quality_split_and_reasons(env):
    res = pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    assert (res["rows_in"], res["rows_good"], res["rows_rejected"]) == (6, 3, 3)
    assert res["quality_status"] == "warn"
    text = "；".join(res["reasons"])
    assert "重复" in text and "几何为空或非法" in text and "超出" in text


def test_empty_geometry_is_rejected():
    """空几何的 is_valid 是 True，必须单独判 is_empty。"""
    import pandas as pd
    from shapely.geometry import Point, Polygon

    df = pd.DataFrame({"parcel_id": ["A", "B", "C"], "landuse": ["住宅"] * 3, "area_m2": [1.0, 2.0, 3.0],
                       "geometry": [Polygon(), Point(0, 0), None]})
    out = quality.run(df, config.DATASETS["parcels"], {"crs_source": "prj"})
    # 空几何 + 无几何被拒；合法的 Point 必须留下（不能因为判定写宽了把好数据也拦掉）
    assert len(out["reject"]) == 2
    assert len(out["good"]) == 1 and out["good"].iloc[0]["parcel_id"] == "B"


def test_idempotent_rerun(env):
    a = pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    b = pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    assert b.get("skipped") is True
    conn = store.connect(env["db"])
    assert store.counts(conn, "parcels")["dwd"] == a["dwd_rows"] == 3
    conn.close()


def test_incremental_upsert(env):
    pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    res = pipeline.run_file(env["p"]("parcels_inc.geojson"), db=env["db"])
    assert res["dwd_rows"] == 5            # 3 + 2，不重复
    assert res["rows_rejected"] == 0


def test_crs_missing_blocks_load(env):
    pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    res = pipeline.run_file(env["p"]("parcels_nocrs.shp"), db=env["db"])
    assert res["quality_status"] == "fail"
    assert res["load_ok"] is False
    assert any("坐标系" in r for r in res["reasons"])
    conn = store.connect(env["db"])
    assert store.counts(conn, "parcels")["dwd"] == 3     # 没有多写入
    conn.close()


def test_metric_recomputable(env):
    res = pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    got = {m[0]: m[2] for m in res["metrics"]}
    assert got["parcels_total_area_km2"] == pytest.approx(0.0044, rel=1e-6)
    conn = store.connect(env["db"])
    dwd = store.read_dwd(conn, "parcels")
    assert dwd["area_m2"].sum() / 1e6 == pytest.approx(got["parcels_total_area_km2"], rel=1e-6)
    conn.close()


def test_lineage_traceable(env):
    pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    conn = store.connect(env["db"])
    tree = metrics.lineage_tree(conn, "parcels_total_area_km2")
    layers = {(f["layer"], f["field"]) for f in tree["fields"]}
    assert ("dwd_parcels", "area_m2") in layers
    conn.close()


def test_api_denies_free_sql(env, monkeypatch):
    from fastapi.testclient import TestClient

    import hub.api as api_mod

    pipeline.run_file(env["p"]("parcels.geojson"), db=env["db"])
    monkeypatch.setenv("HUB_DB", env["db"])
    client = TestClient(api_mod.app)
    assert client.post("/query", json={"sql": "select 1"}).status_code == 403
    body = client.get("/metrics/summary").json()
    assert {"metric", "count", "items"} <= set(body)


def test_empty_file_fails_fast(env):
    res = pipeline.run_file(env["p"]("shops_empty.csv"), db=env["db"])
    assert res["quality_status"] == "fail"
    assert res["load_ok"] is False
    assert res["metrics"] == []
