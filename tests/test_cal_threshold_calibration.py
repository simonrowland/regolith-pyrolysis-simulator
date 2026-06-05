"""Regression tests for the CAL vapor-threshold calibration harness.

Guards the worker arg-resolution contract that previously crashed every
worker subprocess with ``AttributeError: 'Namespace' object has no attribute
'feedstock'`` (the ``lunar_mare_low_ti:C2B=error`` symptom): ``--feedstock`` /
``--campaign`` register with ``action="append"`` (plural dests), so the worker
body must resolve the single scalar it expects and fail loud otherwise.

Stub backend only -- CAL is golden-neutral and stub data is explicitly
non-authoritative, but the harness plumbing must still execute end to end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "cal_threshold_calibration",
    REPO_ROOT / "scripts" / "cal_threshold_calibration.py",
)
cal = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
# Register before exec: the module defines a frozen dataclass whose decorator
# resolves cls.__module__ via sys.modules, which is None for an unregistered
# importlib-loaded module (AttributeError at import otherwise).
sys.modules[_SPEC.name] = cal
_SPEC.loader.exec_module(cal)


def _worker_args(*extra: str):
    return cal._parse_args(
        [
            "--worker",
            "--backend",
            "stub",
            "--max-hours",
            "2",
            *extra,
        ]
    )


def test_worker_payload_stub_path_runs_and_returns_rows():
    # Regression: this exact call raised AttributeError before the
    # feedstocks->feedstock resolution was added to _worker_payload.
    args = _worker_args("--feedstock", "lunar_mare_low_ti", "--campaign", "C2B")
    payload = cal._worker_payload(args)

    assert payload["case"] == {
        "feedstock": "lunar_mare_low_ti",
        "campaign": "C2B",
    }
    assert payload["backend"]["name"] == "StubBackend"
    assert payload["rows"], "stub worker must collect at least one per-target row"
    # C2B targets Fe (CAMPAIGN_TARGETS); every row carries the case identity.
    assert {row["target"] for row in payload["rows"]} == {"Fe"}
    assert all(row["campaign"] == "C2B" for row in payload["rows"])


def test_worker_payload_requires_exactly_one_feedstock():
    args = _worker_args("--campaign", "C2B")  # no --feedstock
    with pytest.raises(SystemExit, match="exactly one --feedstock"):
        cal._worker_payload(args)


def test_worker_payload_requires_exactly_one_campaign():
    args = _worker_args("--feedstock", "lunar_mare_low_ti")  # no --campaign
    with pytest.raises(SystemExit, match="exactly one --campaign"):
        cal._worker_payload(args)


def test_worker_payload_rejects_multiple_feedstocks():
    args = _worker_args(
        "--feedstock",
        "lunar_mare_low_ti",
        "--feedstock",
        "mars_perchlorate_rich",
        "--campaign",
        "C2B",
    )
    with pytest.raises(SystemExit, match="exactly one --feedstock"):
        cal._worker_payload(args)


def _summary_with_ok_threshold() -> dict:
    return {
        "row_count": 3,
        "case_count": 1,
        "analysis_by_feedstock_campaign_target": {
            "lunar_mare_low_ti|C2B|Fe": {
                "status": "ok",
                "proposed_threshold": 0.9,
            },
        },
    }


def test_real_backend_blocked_when_any_worker_case_fails():
    cases = [
        {
            "case": {"feedstock": "lunar_mare_low_ti", "campaign": "C2B"},
            "rows": [{"feedstock": "lunar_mare_low_ti", "campaign": "C2B", "target": "Fe", "completeness": 0.5, "campaign_hour": 1, "hour_index": 0}],
            "stop_reason": "max_hours",
        },
        {
            "case": {"feedstock": "lunar_mare_low_ti", "campaign": "C4"},
            "rows": [],
            "stop_reason": "timeout",
        },
    ]
    summary = _summary_with_ok_threshold()
    assert cal._is_real_backend_calibration_blocked(
        cases,
        summary,
        backend="alphamelts",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B", "C4"),
    )


def test_real_backend_blocked_when_curve_insufficient_not_ok():
    summary = {
        "row_count": 1,
        "case_count": 1,
        "analysis_by_feedstock_campaign_target": {
            "lunar_mare_low_ti|C2B|Fe": {
                "status": "insufficient_curve",
                "proposed_threshold": 0.4,
            },
        },
    }
    assert cal._is_real_backend_calibration_blocked(
        [{"rows": [{}], "stop_reason": "max_hours"}],
        summary,
        backend="alphamelts",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B",),
    )


def test_real_backend_blocked_when_expected_target_missing_curve():
    cases = [
        {
            "case": {"feedstock": "lunar_mare_low_ti", "campaign": "C2B"},
            "rows": [{"feedstock": "lunar_mare_low_ti", "campaign": "C2B", "target": "Fe", "completeness": 0.5, "campaign_hour": 1, "hour_index": 0}],
            "stop_reason": "max_hours",
        },
    ]
    summary = _summary_with_ok_threshold()
    assert cal._is_real_backend_calibration_blocked(
        cases,
        summary,
        backend="alphamelts",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B", "C4"),
    )


def test_stub_backend_never_blocked_by_partial_matrix():
    assert not cal._is_real_backend_calibration_blocked(
        [{"rows": [], "stop_reason": "timeout"}],
        {"row_count": 0, "analysis_by_feedstock_campaign_target": {}},
        backend="stub",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B",),
    )
