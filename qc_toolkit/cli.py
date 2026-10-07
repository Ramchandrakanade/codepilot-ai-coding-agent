"""Command line entry point:  python -m qc_toolkit.cli data/sample.csv"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .report import build_report, write_report
from .validators import run_all_checks

DEFAULT_CONFIG = {
    "required_columns": ["id", "score", "playtime_min"],
    "max_missing_pct": 0.0,
    "duplicate_subset": ["id"],
    "ranges": {"score": (0, 100), "playtime_min": (0, 600)},
    "outlier_columns": ["playtime_min"],
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run automated QC checks on a CSV file")
    parser.add_argument("csv_path")
    parser.add_argument("--report", default="qc_report.json")
    args = parser.parse_args(argv)

    df = pd.read_csv(args.csv_path)
    report = build_report(run_all_checks(df, DEFAULT_CONFIG))
    write_report(report, args.report)
    print(f"QC {report['status']}: {report['passed']}/{report['total_checks']} checks passed")
    for name in report["failed_checks"]:
        print(f"  - FAILED: {name}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
