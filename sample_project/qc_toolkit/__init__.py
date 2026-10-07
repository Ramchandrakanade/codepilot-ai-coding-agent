"""QC Automation Toolkit - reusable data-quality checks for automated testing."""
from .validators import (
    check_schema,
    check_missing,
    check_duplicates,
    check_ranges,
    detect_outliers_iqr,
    run_all_checks,
)
from .report import build_report, write_report

__all__ = [
    "check_schema", "check_missing", "check_duplicates", "check_ranges",
    "detect_outliers_iqr", "run_all_checks", "build_report", "write_report",
]
