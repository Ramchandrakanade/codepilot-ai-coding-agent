import json
import os
import tempfile

from qc_toolkit.cli import main
from qc_toolkit.report import build_report

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "sample.csv")


def test_build_report_counts_and_status():
    results = [
        {"name": "a", "passed": True, "details": {}},
        {"name": "b", "passed": False, "details": {}},
    ]
    report = build_report(results)
    assert report["total_checks"] == 2
    assert report["failed_checks"] == ["b"]
    assert report["status"] == "FAIL"


def test_cli_passes_on_sample_data_and_writes_report():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "report.json")
        code = main([SAMPLE, "--report", out])
        assert code == 0
        with open(out, encoding="utf-8") as f:
            assert json.load(f)["status"] == "PASS"


def test_cli_returns_nonzero_on_bad_data():
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.csv")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("id,score,playtime_min\n1,150,30\n1,60,45\n")
        out = os.path.join(tmp, "report.json")
        assert main([bad, "--report", out]) == 1
