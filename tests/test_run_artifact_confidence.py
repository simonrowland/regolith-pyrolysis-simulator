import copy

import pytest

from simulator.accounting.run_artifact import build_run_artifact


def _runner_payload(
    *,
    status: str = "ok",
    residual_pct: float | None = 1e-13,
    vapor_status: str | None = "ok",
) -> dict:
    payload = {
        "status": status,
        "run_metadata": {
            "backend": "stub",
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
        },
        "per_hour_summary": [
            {"hour": 0, "campaign": "C0", "mass_balance_pct": residual_pct}
        ],
        "vapor_pressure_source_report": {},
    }
    if vapor_status is not None:
        payload["vapor_pressure_source_report"]["status"] = vapor_status
    return payload


@pytest.fixture(autouse=True)
def _stable_cache_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.accounting.run_artifact.cache_version_for",
        lambda backend: "cache-v1",
    )


def test_confidence_high_lists_every_passing_signal() -> None:
    artifact = build_run_artifact(_runner_payload(), run_id="run-high")

    assert artifact["terminal"]["confidence"] == {
        "grade": "high",
        "reasons": [
            "mass-balance residual 1e-13% within 5e-12% closure gate",
            "vapor-pressure sources: ok",
            "backend identity complete: name, cache_version, "
            "backend_wire_token present",
            "execution status: ok",
        ],
    }


def test_confidence_medium_names_incomplete_backend_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.accounting.run_artifact.cache_version_for",
        lambda backend: None,
    )

    artifact = build_run_artifact(_runner_payload(), run_id="run-medium")

    assert artifact["terminal"]["confidence"] == {
        "grade": "medium",
        "reasons": [
            "mass-balance residual 1e-13% within 5e-12% closure gate",
            "vapor-pressure sources: ok",
            "backend identity incomplete: cache_version absent",
            "execution status: ok",
        ],
    }


def test_confidence_low_names_mass_balance_gate_breach() -> None:
    artifact = build_run_artifact(
        _runner_payload(residual_pct=1e-11), run_id="run-low"
    )

    assert artifact["terminal"]["confidence"] == {
        "grade": "low",
        "reasons": [
            "mass-balance residual 1e-11% exceeds 5e-12% closure gate",
            "vapor-pressure sources: ok",
            "backend identity complete: name, cache_version, "
            "backend_wire_token present",
            "execution status: ok",
        ],
    }


def test_failed_vapor_source_forces_low_confidence() -> None:
    artifact = build_run_artifact(
        _runner_payload(vapor_status="failed"), run_id="run-vapor-failed"
    )

    assert artifact["terminal"]["confidence"]["grade"] == "low"
    assert (
        "vapor-pressure sources: failed"
        in artifact["terminal"]["confidence"]["reasons"]
    )


def test_confidence_is_omitted_without_numeric_mass_balance_residual() -> None:
    artifact = build_run_artifact(
        _runner_payload(residual_pct=None), run_id="run-ungradeable"
    )

    assert "confidence" not in artifact["terminal"]


@pytest.mark.parametrize(
    ("status", "expected_grade"),
    [("partial", "medium"), ("refused", "low"), ("failed", "low")],
)
def test_execution_status_caps_confidence(status: str, expected_grade: str) -> None:
    artifact = build_run_artifact(
        _runner_payload(status=status), run_id=f"run-{status}"
    )

    confidence = artifact["terminal"]["confidence"]
    assert confidence["grade"] == expected_grade
    expected_reason = f"execution status {status} caps confidence at {expected_grade}"
    assert expected_reason in confidence["reasons"]


def test_confidence_derivation_is_deterministic() -> None:
    payload = _runner_payload(vapor_status=None)

    first = build_run_artifact(copy.deepcopy(payload), run_id="run-deterministic")
    second = build_run_artifact(copy.deepcopy(payload), run_id="run-deterministic")

    assert first["terminal"]["confidence"] == second["terminal"]["confidence"]
    assert first["terminal"]["confidence"]["grade"] == "medium"
    assert "vapor-pressure source status absent" in first["terminal"]["confidence"][
        "reasons"
    ]
