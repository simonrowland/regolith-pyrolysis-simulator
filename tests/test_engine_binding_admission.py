from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.engine_binding_admission import (
    BindingAssessmentCandidate,
    BindingIdentity,
    EngineBindingAdmissionError,
    assess_bindings,
    authorize_binding,
    version_getter_provenance,
)


FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_PINS = FIXTURES / "binding_admission_synthetic"
SYNTHETIC_IDENTITY = BindingIdentity(
    engine_id="synthetic-fake",
    model_id="fixture-model-v1",
    binding_revision="synthetic-r1",
    transport="fixture",
)
SYNTHETIC_CURVE = {
    "source": "gate_liquid_fraction:authoritative:synthetic-fake",
    "solidus_T_C": 1000.0,
    "liquidus_T_C": 1300.0,
    "path": ((1000.0, 0.0), (1300.0, 1.0)),
}


def _candidate(
    identity: BindingIdentity = SYNTHETIC_IDENTITY,
    *,
    curve: dict | None = None,
    provenance: dict | None = None,
) -> BindingAssessmentCandidate:
    return BindingAssessmentCandidate(
        identity=identity,
        artifact="freeze_gate_curve",
        simulator=None,
        result=curve or SYNTHETIC_CURVE,
        provenance=provenance or {"runtime_artifact": "test-only"},
    )


def _assess(tmp_path: Path, candidate: BindingAssessmentCandidate):
    receipt_path = tmp_path / "engines.local.binding-admission.json"
    results = assess_bindings(
        [candidate],
        pin_directory=SYNTHETIC_PINS,
        receipt_path=receipt_path,
    )
    return receipt_path, results


def test_synthetic_reviewed_pin_admits_matching_projection(tmp_path: Path) -> None:
    candidate = _candidate()
    receipt_path, results = _assess(tmp_path, candidate)

    assert results[0].status == "admitted"
    assert authorize_binding(
        SYNTHETIC_IDENTITY,
        candidate.provenance,
        receipt_path=receipt_path,
    ).identity == SYNTHETIC_IDENTITY
    receipt = json.loads(receipt_path.read_text())
    assert receipt["entries"][0]["comparison"]["status"] == "matched"


def test_projection_mismatch_is_recorded_but_not_admitted(tmp_path: Path) -> None:
    candidate = _candidate(curve={**SYNTHETIC_CURVE, "liquidus_T_C": 1301.0})
    receipt_path, results = _assess(tmp_path, candidate)

    assert results[0].status == "failed"
    with pytest.raises(EngineBindingAdmissionError, match="comparison failed"):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_missing_receipt_is_a_typed_refusal(tmp_path: Path) -> None:
    with pytest.raises(EngineBindingAdmissionError, match="receipt missing"):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            {"runtime_artifact": "test-only"},
            receipt_path=tmp_path / "missing.json",
        )


@pytest.mark.parametrize("failure", ["failed", "stale", "transport-mismatched"])
def test_failed_stale_and_transport_mismatched_receipts_refuse(
    tmp_path: Path,
    failure: str,
) -> None:
    candidate = _candidate()
    receipt_path, results = _assess(tmp_path, candidate)
    receipt = json.loads(receipt_path.read_text())
    entry = receipt["entries"][0]
    if failure == "failed":
        entry["status"] = "failed"
        entry["reason"] = "reviewed projection did not match"
    elif failure == "stale":
        entry["provenance"]["runtime_artifact"] = "different-install"
    else:
        entry["identity"]["transport"] = "python_api"
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(EngineBindingAdmissionError):
        authorize_binding(
            SYNTHETIC_IDENTITY,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_alpha_subprocess_receipt_does_not_admit_python_api(tmp_path: Path) -> None:
    candidate = _candidate(
        BindingIdentity(
            engine_id="alphamelts",
            model_id="MELTSv1.0.2",
            binding_revision="alphamelts-r1",
            transport="subprocess",
        )
    )
    receipt_path, results = _assess(tmp_path, candidate)

    assert results[0].status == "admitted"
    with pytest.raises(EngineBindingAdmissionError, match="transport mismatch"):
        authorize_binding(
            BindingIdentity(
                engine_id="alphamelts",
                model_id="MELTSv1.0.2",
                binding_revision="alphamelts-r1",
                transport="python_api",
            ),
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_missing_real_pin_reports_no_reviewed_pins_and_admits_nothing(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        BindingIdentity(
            engine_id="thermoengine",
            model_id="MELTSv1.0.2",
            binding_revision="thermoengine-r1",
            transport="native",
        )
    )
    receipt_path, results = assess_bindings(
        [candidate],
        pin_directory=tmp_path / "no-pins",
        receipt_path=tmp_path / "receipt.json",
    )

    assert results[0].status == "failed"
    assert results[0].reason == "no reviewed pins for thermoengine/native"
    with pytest.raises(EngineBindingAdmissionError, match="no reviewed pins"):
        authorize_binding(
            candidate.identity,
            candidate.provenance,
            receipt_path=receipt_path,
        )


def test_failed_version_getter_records_typed_unknown_not_unavailable() -> None:
    state = version_getter_provenance(
        lambda: (_ for _ in ()).throw(RuntimeError("getter failed"))
    )

    assert state == {
        "tag": "unknown",
        "value": None,
        "reason": "version getter failed: RuntimeError",
    }
    assert state["value"] != "unavailable"

