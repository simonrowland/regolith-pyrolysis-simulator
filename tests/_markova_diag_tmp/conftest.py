from __future__ import annotations

import json
from pathlib import Path

import pytest


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    report_dir = Path(__file__).with_name("_markova_results")
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(report_dir.glob("*.json"))]
    rows = [row for report in reports for row in report["rows"]]
    warmth = [report["warmth"] for report in reports]
    print("MARKOVA_WARMTH " + json.dumps({
        "engine_result_cache": "disabled",
        "parsed_yaml_cache": "fresh empty private directory per test",
        "parsed_cache_files_created_min": min((item["parsed_cache_files_created_during_score"] for item in warmth), default=None),
        "parsed_cache_files_created_max": max((item["parsed_cache_files_created_during_score"] for item in warmth), default=None),
        "test_cases_with_reports": len(reports),
    }, sort_keys=True))
    print("MARKOVA_ROWS " + json.dumps(rows, sort_keys=True, separators=(",", ":")))
    crossovers = [report["k_crossover"] for report in reports if "k_crossover" in report]
    if crossovers:
        print("MARKOVA_CROSSOVER " + json.dumps(crossovers, sort_keys=True))


@pytest.fixture(scope="session", autouse=True)
def markova_report_dir():
    report_dir = Path(__file__).with_name("_markova_results")
    report_dir.mkdir(exist_ok=True)
    yield
