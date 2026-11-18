"""质量规则引擎：规则是数据，结果是可回溯的行级证据。

产出三样东西：
  * report：每条规则检查了多少行、失败多少行、状态 pass/warn/fail
  * good / reject：可分流的行（reject 带 rule + reason，能定位到源文件行号）
  * 门禁：失败率超过阈值就把整批判为 fail，不进入 dwd（宁可不出数，也不出错数）
"""
import pandas as pd

FAIL_RATE_LIMIT = 0.35     # 单条规则失败率超这个比例 → 整批 fail（防止把源数据错当指标）


def _check_rule(df, rule):
    """返回 (bad_mask, msg)；bad_mask 为 True 表示该行违反规则。"""
    t = rule["type"]
    n = len(df)

    if t == "not_null":
        col = rule["col"]
        if col not in df.columns:
            return pd.Series([True] * n, index=df.index), f"缺字段 {col}"
        return df[col].isna(), f"{col} 为空"

    if t == "unique":
        col = rule["col"]
        if col not in df.columns:
            return pd.Series([True] * n, index=df.index), f"缺字段 {col}"
        # 第一行保留，其余重复判为坏
        return df[col].duplicated(keep="first"), f"{col} 重复"

    if t == "range":
        col = rule["col"]
        if col not in df.columns:
            return pd.Series([True] * n, index=df.index), f"缺字段 {col}"
        s = pd.to_numeric(df[col], errors="coerce")
        bad = s.isna()
        if "min" in rule:
            bad = bad | (s < rule["min"])
        if "max" in rule:
            bad = bad | (s > rule["max"])
        return bad, f"{col} 超出 {rule.get('min', '-inf')}~{rule.get('max', '+inf')}"

    if t == "geom_valid":
        if "geometry" not in df.columns:
            return pd.Series([True] * n, index=df.index), "缺 geometry 列"
        geo = df["geometry"]
        na = geo.isna().to_numpy()
        try:
            import shapely  # shapely 2.x 的向量化函数，能一次判数组
            arr = geo.to_numpy()
            empty = shapely.is_empty(arr)
            valid = shapely.is_valid(arr)
            bad_arr = na | empty | (~valid)
        except Exception:
            # 兜底：逐行判断（pandas Series 上没有 is_empty/is_valid 这两个属性）
            bad_arr = [True if g is None else (bool(g.is_empty) or not bool(g.is_valid)) for g in geo]
        return pd.Series(bad_arr, index=df.index), "几何为空或非法"

    if t == "crs_required":
        # 批量层面规则，由调用方处理
        return pd.Series([False] * n, index=df.index), ""

    raise ValueError(f"未知规则类型：{t}")


def run(df, spec, meta=None):
    """执行全部规则；返回 dict(good, reject, report, status, reasons)。"""
    meta = meta or {}
    if not len(df):
        return {"good": df, "reject": df.assign(__rule="empty", __reason="空文件"),
                "report": [], "status": "fail", "reasons": ["空文件"]}

    bad_any = pd.Series([False] * len(df), index=df.index)
    first_rule = pd.Series([""] * len(df), index=df.index)
    first_reason = pd.Series([""] * len(df), index=df.index)
    report, reasons, status = [], [], "pass"

    for rule in spec.get("rules", []):
        bad, msg = _check_rule(df, rule)
        bad = bad.fillna(False).astype(bool)
        new_bad = bad & ~bad_any
        first_rule[new_bad] = rule["type"] + (":" + str(rule.get("col", "")) if rule.get("col") else "")
        first_reason[new_bad] = msg
        bad_any = bad_any | bad

        checked, failed = len(df), int(bad.sum())
        rate = failed / checked if checked else 0
        st = "pass" if failed == 0 else ("fail" if rate > FAIL_RATE_LIMIT else "warn")
        if st == "fail":
            status = "fail"
        elif st == "warn" and status == "pass":
            status = "warn"
        report.append({"rule": rule["type"], "col": rule.get("col"), "checked": checked,
                       "failed": failed, "rate": round(rate, 4), "status": st})
        if failed:
            reasons.append(f"{msg}（{failed} 行）")

    # CRS 治理：缺 CRS 的空间数据不能进 dwd（否则面积/距离全错）
    if spec.get("crs") and meta.get("crs_source") == "missing":
        status = "fail"
        reasons.append("缺坐标系信息（.prj 缺失），拒绝入库")

    good = df[~bad_any].copy()
    reject = df[bad_any].copy()
    reject["__rule"] = first_rule[bad_any]
    reject["__reason"] = first_reason[bad_any]
    return {"good": good, "reject": reject, "report": report, "status": status,
            "reasons": reasons or ["无质量问题"]}
