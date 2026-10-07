import pandas as pd
import pytest

from qc_toolkit import (
    check_duplicates,
    check_missing,
    check_ranges,
    check_schema,
    detect_outliers_iqr,
    run_all_checks,
)


def make_df():
    return pd.DataFrame(
        {"id": [1, 2, 3, 4], "score": [50, 60, 70, 80], "playtime_min": [30, 45, 60, 75]}
    )


def test_schema_passes_when_columns_present():
    assert check_schema(make_df(), ["id", "score"])["passed"] is True


def test_schema_fails_and_lists_missing_columns():
    result = check_schema(make_df(), ["id", "level"])
    assert result["passed"] is False
    assert result["details"]["missing_columns"] == ["level"]


def test_missing_values_detected():
    df = make_df()
    df.loc[0, "score"] = None
    result = check_missing(df)
    assert result["passed"] is False
    assert "score" in result["details"]["columns_over_limit"]


def test_missing_values_allowed_under_threshold():
    df = make_df()
    df.loc[0, "score"] = None  # 25% missing
    assert check_missing(df, max_missing_pct=30)["passed"] is True


def test_empty_dataframe_fails_missing_check():
    assert check_missing(pd.DataFrame())["passed"] is False


def test_duplicates_detected_on_subset():
    df = pd.concat([make_df(), make_df().iloc[[0]]], ignore_index=True)
    result = check_duplicates(df, subset=["id"])
    assert result["passed"] is False
    assert result["details"]["duplicate_rows"] == 1


@pytest.mark.parametrize(
    "value, expected",
    [(0, True), (100, True), (101, False), (-1, False)],
)
def test_range_boundaries(value, expected):
    df = pd.DataFrame({"score": [value]})
    assert check_ranges(df, {"score": (0, 100)})["passed"] is expected


def test_range_reports_unknown_column():
    result = check_ranges(make_df(), {"level": (0, 10)})
    assert result["passed"] is False


def test_outlier_detected():
    df = pd.DataFrame({"x": [10, 11, 12, 11, 10, 12, 500]})
    result = detect_outliers_iqr(df, "x")
    assert result["passed"] is False
    assert result["details"]["count"] == 1


def test_no_outliers_in_clean_data():
    assert detect_outliers_iqr(make_df(), "score")["passed"] is True


def test_outlier_unknown_column_raises():
    with pytest.raises(KeyError):
        detect_outliers_iqr(make_df(), "nope")


def test_run_all_checks_on_clean_data_passes():
    config = {
        "required_columns": ["id", "score"],
        "duplicate_subset": ["id"],
        "ranges": {"score": (0, 100)},
        "outlier_columns": ["playtime_min"],
    }
    results = run_all_checks(make_df(), config)
    assert all(r["passed"] for r in results)
