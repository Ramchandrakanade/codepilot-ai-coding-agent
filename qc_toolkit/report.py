"""Build and save a QC report from validator results."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def build_report(results: list[dict]) -> dict:
    failed = [r["name"] for r in results if not r["passed"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_checks": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failed_checks": failed,
        "status": "PASS" if not failed else "FAIL",
        "results": results,
    }


def write_report(report: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
