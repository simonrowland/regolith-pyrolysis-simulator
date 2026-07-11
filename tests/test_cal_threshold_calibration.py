"""Regression tests for the CAL vapor-threshold calibration harness.

Guards the worker arg-resolution contract that previously crashed every
worker subprocess with ``AttributeError: 'Namespace' object has no attribute
'feedstock'`` (the ``lunar_mare_low_ti:C2B=error`` symptom): ``--feedstock`` /
``--campaign`` register with ``action="append"`` (plural dests), so the worker
body must resolve the single scalar it expects and fail loud otherwise.

Internal-analytical backend only -- CAL is golden-neutral and analytical data
is explicitly non-authoritative, but the harness plumbing must still execute
end to end.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


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


def test_worker_payload_internal_analytical_path_runs_and_returns_rows():
    # Regression: this exact call raised AttributeError before the
    # feedstocks->feedstock resolution was added to _worker_payload.
    args = _worker_args("--feedstock", "lunar_mare_low_ti", "--campaign", "C2B")
    payload = cal._worker_payload(args)

    assert payload["case"] == {
        "feedstock": "lunar_mare_low_ti",
        "campaign": "C2B",
    }
    assert payload["backend"]["name"] == "InternalAnalyticalBackend"
    assert payload["rows"], (
        "internal-analytical worker must collect at least one per-target row"
    )
    # C2B targets Fe (CAMPAIGN_TARGETS); every row carries the case identity.
    assert {row["target"] for row in payload["rows"]} == {"Fe"}
    assert all(row["campaign"] == "C2B" for row in payload["rows"])


@pytest.mark.parametrize(
    "backend",
    ("stub", "internal-analytical", " Internal-Analytical ", "INTERNAL_ANALYTICAL"),
)
def test_internal_analytical_equivalent_backend_requires_explicit_opt_in(backend, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cal.main(
            [
                "--backend",
                backend,
                "--output-dir",
                str(tmp_path / "out"),
                "--review-dir",
                str(tmp_path / "review"),
            ]
        )

    assert str(exc.value) == (
        "internal-analytical backend requires --allow-internal-analytical; "
        "not authoritative for CAL"
    )


def test_legacy_allow_stub_flag_alias_is_accepted():
    args = cal._parse_args(["--allow-stub"])

    assert args.allow_internal_analytical is True


@pytest.mark.parametrize(
    "backend",
    ("internal-analytical", " Internal-Analytical ", "INTERNAL_ANALYTICAL"),
)
def test_backend_alias_variants_parse_to_stable_stub_token(backend):
    assert cal._parse_args(["--backend", backend]).backend == "stub"


def test_internal_analytical_run_metadata_is_stub_non_authoritative(
    tmp_path,
    monkeypatch,
):
    def fake_run_worker_case(case, *, backend, **kwargs):
        assert backend == "stub"
        return {
            "case": {"feedstock": case.feedstock, "campaign": case.campaign},
            "backend": {"name": "InternalAnalyticalBackend"},
            "stop_reason": "complete",
            "elapsed_s": 0.0,
            "rows": [
                {
                    "feedstock": case.feedstock,
                    "campaign": case.campaign,
                    "target": "Fe",
                    "campaign_hour": hour,
                    "hour_index": hour - 1,
                    "completeness": completeness,
                }
                for hour, completeness in ((1, 0.2), (2, 0.8), (3, 0.9))
            ],
        }

    monkeypatch.setattr(cal, "_run_worker_case", fake_run_worker_case)

    rc = cal.main(
        [
            "--backend",
            "internal-analytical",
            "--allow-internal-analytical",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C2B",
            "--output-dir",
            str(tmp_path / "out"),
            "--review-dir",
            str(tmp_path / "review"),
        ]
    )

    assert rc == 0
    payload = json.loads((tmp_path / "out" / "raw_curves.json").read_text())
    assert payload["metadata"]["backend"] == "stub"
    assert (
        payload["metadata"]["backend_fidelity"]
        == "stub-non-authoritative"
    )


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


def test_real_backend_blocked_when_curve_stops_at_max_hours_with_ok_summary():
    cases = [
        {
            "case": {"feedstock": "lunar_mare_low_ti", "campaign": "C2B"},
            "rows": [
                {
                    "feedstock": "lunar_mare_low_ti",
                    "campaign": "C2B",
                    "target": "Fe",
                    "completeness": 0.5,
                    "campaign_hour": 1,
                    "hour_index": 0,
                }
            ],
            "stop_reason": "max_hours",
        },
    ]
    summary = _summary_with_ok_threshold()
    assert cal._is_real_backend_calibration_blocked(
        cases,
        summary,
        backend="alphamelts",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B",),
    )


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


def test_c2a_campaign_targets_match_setpoints_contract():
    setpoints = yaml.safe_load((REPO_ROOT / "data" / "setpoints.yaml").read_text())
    expected = tuple(setpoints["campaigns"]["C2A_continuous"]["target_species"])
    assert cal.CAMPAIGN_TARGETS["C2A_continuous"] == expected
    assert "Mg" not in cal.CAMPAIGN_TARGETS["C2A_continuous"]


def test_internal_analytical_backend_never_blocked_by_partial_matrix():
    assert not cal._is_real_backend_calibration_blocked(
        [{"rows": [], "stop_reason": "timeout"}],
        {"row_count": 0, "analysis_by_feedstock_campaign_target": {}},
        backend="stub",
        feedstocks=("lunar_mare_low_ti",),
        campaigns=("C2B",),
    )
