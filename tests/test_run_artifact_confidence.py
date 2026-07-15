import copy

import pytest

from simulator.accounting.run_artifact import build_run_artifact


def _runner_payload(
    *,
    status: str = "ok",
    residual_pct: float | None = 1e-13,
    backend: str = "alphamelts",
    backend_status: str = "ok",
    backend_authoritative: bool = True,
    vapor_status: str = "ok",
    vapor_authoritative: bool = True,
    include_vapor_report: bool = True,
) -> dict:
    # Mirrors runner.py:1069-1084,1125-1136 and 1331-1364; do not add
    # confidence-only fixture keys that canonical runner payloads cannot emit.
    payload = {
        "status": status,
        "run_metadata": {
            "backend": backend,
            "backend_status": backend_status,
            "backend_authoritative": backend_authoritative,
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
        },
        "per_hour_summary": [
            {"hour": 0, "campaign": "C0", "mass_balance_pct": residual_pct}
        ],
    }
    if include_vapor_report:
        payload["vapor_pressure_source_report"] = {
            "species": {"SiO": "builtin_authoritative:standard_reaction_term"},
            "summary": {},
            "total_species": 1,
            "vapor_pressure_backend_status": vapor_status,
            "vapor_pressure_backend_status_summary": {},
            "vapor_pressure_backend_status_reason": "",
            "vapor_pressure_fallback_source": "",
            "authoritative_for_requested_vapor_pressure": vapor_authoritative,
        }
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
            "vapor-pressure backend status ok and authoritative",
            "backend identity complete: name, cache_version, "
            "backend_wire_token present",
            "backend status: ok",
            "backend authoritative: alphamelts",
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
            "vapor-pressure backend status ok and authoritative",
            "backend identity incomplete: cache_version absent",
            "backend status: ok",
            "backend authoritative: alphamelts",
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
            "vapor-pressure backend status ok and authoritative",
            "backend identity complete: name, cache_version, "
            "backend_wire_token present",
            "backend status: ok",
            "backend authoritative: alphamelts",
            "execution status: ok",
        ],
    }


def test_failed_vapor_source_forces_low_confidence() -> None:
    artifact = build_run_artifact(
        _runner_payload(vapor_status="failed", vapor_authoritative=False),
        run_id="run-vapor-failed",
    )

    assert artifact["terminal"]["confidence"]["grade"] == "low"
    assert (
        "vapor-pressure backend status failed; authoritative=False"
        in artifact["terminal"]["confidence"]["reasons"]
    )


def test_non_authoritative_backend_caps_confidence_at_medium() -> None:
    artifact = build_run_artifact(
        _runner_payload(
            backend="stub",
            backend_status="unavailable",
            backend_authoritative=False,
        ),
        run_id="run-non-authoritative",
    )

    confidence = artifact["terminal"]["confidence"]
    assert confidence["grade"] == "medium"
    assert "backend not authoritative: stub" in confidence["reasons"]


def test_failed_backend_status_forces_low_confidence() -> None:
    artifact = build_run_artifact(
        _runner_payload(backend_status="failed", backend_authoritative=False),
        run_id="run-backend-failed",
    )

    confidence = artifact["terminal"]["confidence"]
    assert confidence["grade"] == "low"
    assert "backend status failed: alphamelts" in confidence["reasons"]


def test_non_authoritative_vapor_evidence_caps_confidence_at_medium() -> None:
    artifact = build_run_artifact(
        _runner_payload(vapor_status="ok", vapor_authoritative=False),
        run_id="run-vapor-non-authoritative",
    )

    confidence = artifact["terminal"]["confidence"]
    assert confidence["grade"] == "medium"
    assert (
        "vapor-pressure backend status ok; authoritative=False"
        in confidence["reasons"]
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


def test_cancelled_lifecycle_is_recorded_without_extra_grade_penalty() -> None:
    artifact = build_run_artifact(
        _runner_payload(status="partial"),
        run_id="run-cancelled",
        lifecycle="cancelled",
    )

    confidence = artifact["terminal"]["confidence"]
    assert confidence["grade"] == "medium"
    assert "lifecycle cancelled" in confidence["reasons"]


def test_confidence_derivation_is_deterministic() -> None:
    payload = _runner_payload(include_vapor_report=False)

    first = build_run_artifact(copy.deepcopy(payload), run_id="run-deterministic")
    second = build_run_artifact(copy.deepcopy(payload), run_id="run-deterministic")

    assert first["terminal"]["confidence"] == second["terminal"]["confidence"]
    assert first["terminal"]["confidence"]["grade"] == "medium"
    assert "vapor-pressure source report absent" in first["terminal"]["confidence"][
        "reasons"
    ]
