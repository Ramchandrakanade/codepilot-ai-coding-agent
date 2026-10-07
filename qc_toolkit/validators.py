"""Data-quality validators. Each check returns a dict: {name, passed, details}."""
from __future__ import annotations

import pandas as pd


def _result(name: str, passed: bool, details: dict | None = None) -> dict:
    return {"name": name, "passed": bool(passed), "details": details or {}}


def check_schema(df: pd.DataFrame, required_columns: list[str]) -> dict:
    """Pass if every required column is present."""
    missing = [c for c in required_columns if c not in df.columns]
    return _result("schema", not missing, {"missing_columns": missing})


def check_missing(df: pd.DataFrame, max_missing_pct: float = 0.0) -> dict:
    """Pass if no column has more than `max_missing_pct` percent missing values."""
    if len(df) == 0:
        return _result("missing_values", False, {"reason": "empty dataframe"})
    pct = (df.isna().mean() * 100).round(2)
    offenders = {k: float(v) for k, v in pct[pct > max_missing_pct].items()}
    return _result("missing_values", not offenders, {"columns_over_limit": offenders})


def check_duplicates(df: pd.DataFrame, subset: list[str] | None = None) -> dict:
    """Pass if there are no duplicate rows (optionally on a subset of columns)."""
    count = int(df.duplicated(subset=subset).sum())
    return _result("duplicates", count == 0, {"duplicate_rows": count})


def check_ranges(df: pd.DataFrame, ranges: dict[str, tuple[float, float]]) -> dict:
    """Pass if all values in each column fall within (min, max), inclusive."""
    violations = {}
    for col, (low, high) in ranges.items():
        if col not in df.columns:
            violations[col] = "column not found"
            continue
        bad = int(((df[col] < low) | (df[col] > high)).sum())
        if bad:
            violations[col] = bad
    return _result("ranges", not violations, {"violations": violations})


def detect_outliers_iqr(df: pd.DataFrame, column: str, factor: float = 1.5) -> dict:
    """Flag values outside [Q1 - factor*IQR, Q3 + factor*IQR]. Passes when none found."""
    if column not in df.columns:
        raise KeyError(f"column '{column}' not found")
    q1, q3 = df[column].quantile(0.25), df[column].quantile(0.75)
    iqr = q3 - q1
    low, high = q1 - factor * iqr, q3 + factor * iqr
    outliers = df[(df[column] < low) | (df[column] > high)]
    return _result(
        f"outliers_{column}",
        outliers.empty,
        {"count": int(len(outliers)), "bounds": [float(low), float(high)]},
    )


def run_all_checks(df: pd.DataFrame, config: dict) -> list[dict]:
    """Run every check described in `config` and return the list of results."""
    results = []
    if "required_columns" in config:
        results.append(check_schema(df, config["required_columns"]))
    results.append(check_missing(df, config.get("max_missing_pct", 0.0)))
    results.append(check_duplicates(df, config.get("duplicate_subset")))
    if "ranges" in config:
        results.append(check_ranges(df, config["ranges"]))
    for col in config.get("outlier_columns", []):
        results.append(detect_outliers_iqr(df, col))
    return results
