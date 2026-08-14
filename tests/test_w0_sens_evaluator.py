"""W0-SENS patched-build evaluator + synthetic-gate callback tests.

Every engine here is a fake: an invented source tree with invented
parameter names, an injected builder (no compiler) and an injected worker
runner (no subprocess, no thermoengine). The fake "engine" implements the
regular-solution response analytically, so the gate's PASS and its two
distinct FAILURE modes (a numerically silent substitution, and a wrong
analytic premise) are both exercised without touching a real build. No test
reads a quarantined W value or produces a ranking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.w0_sens import _custodian_worker as worker
from benchmarks.w0_sens import w_mutator
from benchmarks.w0_sens.corpus import EligiblePoint
from benchmarks.w0_sens.evaluator import (
    MIN_ANALYTIC_BASIS,
    PatchedBuildEvaluator,
    SyntheticGateCallbacks,
    SyntheticGateState,
    default_synthetic_state,
)
from benchmarks.w0_sens.w_mutator import (
    CONTROL_J,
    MINUS_J,
    PLUS_J,
    SYNTHETIC_GATE_ROW_ID,
    AbortWMutator,
    W0WMutator,
    observed_value_fingerprint,
    synthetic_response_gate,
)

SYNTH_PARAM = "W(SYNTH-A   ,SYNTH-B)"
FAKE_ENDMEMBERS = ("SYNTH-A", "SYNTH-B", "SYNTH-C", "SYNTH-D")
FAKE_MU_BASE = -1.234e6

FAKE_SOURCE = (
    "/* synthetic stand-in for src/LiquidMelts.m */\n"
    "static double referenceValuesOfModelParameters[] = {\n"
    "\t 111.0,  //  0 W(SYNTH-A   ,SYNTH-B   )\n"
    "\t-222.5,  //  1 W(SYNTH-A   ,SYNTH-C   )\n"
    "\t 333.25,  //  2 W(SYNTH-B   ,SYNTH-C   )\n"
    "};\n"
)


def _fake_pairs(prefix_lib: Path) -> list[tuple[str, float]]:
    text = (Path(prefix_lib) / "libphaseobjc.dylib").read_bytes().decode()
    pairs: list[tuple[str, float]] = []
    for line in text.splitlines():
        match = w_mutator._INITIALIZER_RE.match(line)
        if match is None:
            continue
        name = w_mutator.PARAM_NAME_RE.fullmatch(match.group("name"))
        assert name is not None
        pairs.append(
            (f"W({name.group(1):<10},{name.group(2)})", float(match.group("num")))
        )
    return pairs


def _fake_fractions(base_mol: float, component_mol: dict[str, float]) -> dict[str, float]:
    mol = {name: base_mol + component_mol.get(name, 0.0) for name in FAKE_ENDMEMBERS}
    total = sum(mol.values())
    return {name: value / total for name, value in mol.items()}


def _fake_basis(spec, component_i: str, component_j: str) -> float:
    fractions = _fake_fractions(
        float(spec["base_mol"]), {str(k): float(v) for k, v in spec["component_mol"].items()}
    )
    x_i, x_j = fractions[component_i], fractions[component_j]
    target = str(spec["target_endmember"])
    return (
        (x_j if target == component_i else 0.0)
        + (x_i if target == component_j else 0.0)
        - x_i * x_j
    )


def _make_runner(
    *,
    silent: bool = False,
    basis_scale: float = 1.0,
    corpus_values=None,
    drift_fractions: bool = False,
    lie_readback: bool = False,
    drop_point: str | None = None,
):
    """A worker-contract fake with switchable failure modes."""

    def runner(spec):
        command = spec["command"]
        pairs = _fake_pairs(Path(spec["prefix_lib"]))
        names = [name for name, _ in pairs]
        values = [value for _, value in pairs]
        units = ["joules"] * len(names)
        wanted = spec.get("param_name")
        slot = names.index(wanted) if wanted in names else None

        if command == "capture-pristine":
            Path(spec["envelope_path"]).write_text(
                json.dumps(
                    {
                        "param_name": wanted,
                        "slot_index": slot,
                        "names": names,
                        "units": units,
                        "values": values,
                    }
                )
            )
            return {
                "ok": True,
                "n_slots": len(names),
                "slot_index": slot,
                "vector_sha256": worker._vector_sha256(values),
            }
        if command == "readback-build":
            envelope = json.loads(Path(spec["envelope_path"]).read_text())
            changed = worker.vector_diff_slots(
                envelope["names"], envelope["units"], envelope["values"],
                names, units, values,
            )
            return {
                "ok": True,
                "readback_J": values[slot],
                "changed_slots": list(changed),
                "n_slots": len(names),
                "slot_index": slot,
            }
        if command == "verify-restoration":
            envelope = json.loads(Path(spec["envelope_path"]).read_text())
            return {
                "ok": True,
                "vector_matches_pristine": tuple(envelope["values"]) == tuple(values),
                "vector_sha256": worker._vector_sha256(values),
            }
        if command == "synthetic-chem-potential":
            substituted = 0.0 if silent else float(values[slot])
            basis = basis_scale * _fake_basis(spec, "SYNTH-A", "SYNTH-B")
            fractions = _fake_fractions(
                float(spec["base_mol"]),
                {str(k): float(v) for k, v in spec["component_mol"].items()},
            )
            if drift_fractions:
                first = FAKE_ENDMEMBERS[0]
                fractions = dict(fractions)
                fractions[first] = fractions[first] + 1.0e-6
            return {
                "ok": True,
                "mu_J_per_mol": FAKE_MU_BASE + substituted * basis,
                "readback_J": (
                    float(values[slot]) + 1.0 if lie_readback else float(values[slot])
                ),
                "slot_index": slot,
                "n_endmembers": len(FAKE_ENDMEMBERS),
                "endmember_names": list(FAKE_ENDMEMBERS),
                "mole_fractions": fractions,
            }
        if command == "evaluate-corpus":
            cells = []
            for point in spec["points"]:
                if drop_point is not None and str(point["id"]) == drop_point:
                    continue
                value = (
                    None
                    if corpus_values is None
                    else corpus_values(str(point["id"]), float(spec["expected_value_J"]))
                )
                cells.append(
                    {
                        "point_id": point["id"],
                        "value": value,
                        "status": "ok" if value is not None else "refused",
                        "reason": "" if value is not None else "synthetic refusal",
                    }
                )
            return {
                "ok": True,
                "readback_J": (
                    float(values[slot]) + 1.0 if lie_readback else float(values[slot])
                ),
                "slot_index": slot,
                "n_slots": len(names),
                "cells": cells,
            }
        raise AssertionError(f"unknown worker command {command!r}")

    return runner


def _fake_builder(build_root: Path) -> None:
    source = build_root / "src" / "LiquidMelts.m"
    (build_root / "src" / "libphaseobjc.dylib").write_bytes(source.read_bytes())


def _engine_root(tmp_path: Path) -> Path:
    root = tmp_path / "engine"
    (root / "src").mkdir(parents=True)
    (root / "Makefile").write_text("# synthetic makefile\n")
    (root / "src" / "LiquidMelts.m").write_text(FAKE_SOURCE)
    return root


def _pristine_lib(tmp_path: Path) -> Path:
    lib = tmp_path / "pristine-lib"
    lib.mkdir()
    (lib / "libphaseobjc.dylib").write_bytes(FAKE_SOURCE.encode())
    (lib / "libswimdew.dylib").write_bytes(b"synthetic-swimdew")
    (lib / "libspeciation.dylib").write_bytes(b"synthetic-speciation")
    return lib


def _mutator(tmp_path: Path, runner) -> W0WMutator:
    return W0WMutator(
        param_name=SYNTH_PARAM,
        quarantine_dir=tmp_path / "wq1",
        engine_root=_engine_root(tmp_path),
        pristine_dylib_dir=_pristine_lib(tmp_path),
        builder=_fake_builder,
        worker_runner=runner,
    )


def _point(point_id: str) -> EligiblePoint:
    return EligiblePoint(
        point_id=point_id,
        source="synthlab",
        composition_id="synth-comp",
        temperature_K=1473.0,
        channel="a(SiO2)@1473K",
        observable="activity",
        parent_oxide="SiO2",
        species="SiO",
        measured=0.8,
        fO2_bar=None,
        composition_wt_pct={"SiO2": 80.0, "Na2O": 20.0},
        na2o_wt_pct=20.0,
        sio2_wt_pct=80.0,
        evidence_class="experimental_synthetic",
        source_evidence_class=None,
        measurement_type="experimental",
        source_sha256="a" * 64,
    )


# -- the analytic basis --------------------------------------------------


def test_analytic_basis_matches_the_regular_solution_derivative() -> None:
    """``b_k = [A==k] X_B + [B==k] X_A - X_A X_B`` (evaluator docstring (3))."""
    state = default_synthetic_state(SYNTH_PARAM)
    assert state.component_i == "SYNTH-A"
    assert state.component_j == "SYNTH-B"
    assert state.target_endmember == "SYNTH-A"
    fractions = state.mole_fractions(15)
    total = 15 * 0.01 + 0.30 + 0.55
    assert fractions["SYNTH-A"] == pytest.approx((0.01 + 0.30) / total)
    assert fractions["SYNTH-B"] == pytest.approx((0.01 + 0.55) / total)
    x_i, x_j = fractions["SYNTH-A"], fractions["SYNTH-B"]
    # Target is component i: b = X_j - X_i X_j.
    assert state.analytic_basis(15) == pytest.approx(x_j - x_i * x_j)

    # Target is component j: b = X_i - X_i X_j.
    swapped = SyntheticGateState(
        label="t", component_i="SYNTH-A", component_j="SYNTH-B",
        target_endmember="SYNTH-B",
    )
    assert swapped.analytic_basis(15) == pytest.approx(x_i - x_i * x_j)


def test_vacuous_analytic_basis_refuses() -> None:
    """A basis that cannot move the model would make the gate vacuous."""
    state = SyntheticGateState(
        label="t",
        component_i="SYNTH-A",
        component_j="SYNTH-B",
        target_endmember="SYNTH-C",  # neither component: b = -X_i X_j
        component_i_mol=0.0,
        component_j_mol=0.0,
    )
    with pytest.raises(AbortWMutator) as excinfo:
        state.analytic_basis(15)
    assert "vacuous" in str(excinfo.value)
    # And the guard is a real threshold, not decoration.
    assert MIN_ANALYTIC_BASIS > 0.0


def test_analytic_basis_refuses_a_degenerate_phase() -> None:
    with pytest.raises(AbortWMutator):
        default_synthetic_state(SYNTH_PARAM).mole_fractions(1)


# -- the mandatory signed synthetic gate ---------------------------------


def test_production_gate_passes_against_a_faithful_build(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner())
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    outcomes = synthetic_response_gate(
        mutator, callbacks.evaluate, callbacks.analytic
    )
    assert [value for value, _, _ in outcomes] == [PLUS_J, MINUS_J]
    for value, measured, expected in outcomes:
        assert expected == pytest.approx(value * callbacks.analytic_basis)
        assert measured == pytest.approx(expected, rel=1e-12, abs=1e-9)
    record = callbacks.as_record()
    assert record["analytic_form"] == "mu_k(W) - mu_k(0) = W * b_k"
    assert record["target_endmember"] == "SYNTH-A"
    assert record["control_build_prefix"] is not None


def test_gate_record_discloses_the_premise_validation_limit(tmp_path) -> None:
    """MEDIUM-6: the emitted gate record states what the two frozen probes
    do NOT establish — no general validation of the symmetric
    regular-solution premise — and why no third probe narrows the gap."""
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner())
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    synthetic_response_gate(mutator, callbacks.evaluate, callbacks.analytic)
    record = callbacks.as_record()
    assert record["probe_magnitudes_J"] == [PLUS_J, MINUS_J]
    limit = record["premise_validation_limit"]
    assert "does NOT generally validate" in limit
    assert "frozen perturbation set" in limit


def test_gate_catches_a_nonlinear_response_matching_only_at_the_probes(tmp_path) -> None:
    """MEDIUM-6, the reviewer's constructed attack: a response equal to
    W*b at exactly +/-10,000 J but nonlinear elsewhere passes the gate —
    documented behaviour, proven here so the limit record is not a hollow
    claim. What the gate DOES catch is any deviation AT a probe point."""
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner())
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    callbacks.build_control()  # defines the analytic basis

    def nonlinear_evaluate(build) -> float:
        # Matches W*b at both frozen probes; departs elsewhere (never
        # evaluated elsewhere under the frozen perturbation set).
        return float(build.perturbation_J) * callbacks.analytic_basis

    outcomes = synthetic_response_gate(
        mutator, nonlinear_evaluate, callbacks.analytic
    )
    # The attack PASSES: this is exactly why the record carries the limit.
    assert len(outcomes) == 2


def test_gate_catches_a_numerically_silent_substitution(tmp_path) -> None:
    """The failure step 2 exists to catch: the build ignores the constant."""
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner(silent=True))
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    with pytest.raises(AbortWMutator) as excinfo:
        synthetic_response_gate(mutator, callbacks.evaluate, callbacks.analytic)
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_gate_catches_a_wrong_analytic_premise(tmp_path) -> None:
    """A build whose response is not W*b fails, at a 1e-8 relative bar."""
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(basis_scale=1.0 + 1.0e-6)
    )
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    with pytest.raises(AbortWMutator) as excinfo:
        synthetic_response_gate(mutator, callbacks.evaluate, callbacks.analytic)
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_gate_control_build_is_bound_to_the_reserved_row(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner())
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    callbacks.build_control()
    build = callbacks.control_build
    assert build.row_id == SYNTHETIC_GATE_ROW_ID
    assert build.perturbation_J == CONTROL_J
    # Idempotent: the control is built once per session.
    assert callbacks.build_control() is not None
    assert callbacks.control_build is build


def test_mole_fraction_drift_between_custodian_and_build_refuses(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(drift_fractions=True)
    )
    callbacks = SyntheticGateCallbacks(mutator=mutator, evaluator=evaluator)
    with pytest.raises(AbortWMutator) as excinfo:
        callbacks.build_control()
    assert "drifted" in str(excinfo.value)


# -- the patched-build corpus evaluator ----------------------------------


def test_evaluate_corpus_requires_the_build_s_own_row(tmp_path) -> None:
    """``require_row`` at the evaluation seam — nothing else enforces it."""
    mutator = _mutator(tmp_path, _make_runner())
    build = mutator.make_build(PLUS_J, row_id="row-1")
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(corpus_values=lambda pid, value: 1.0)
    )
    cells = evaluator.evaluate_corpus(build, row_id="row-1", points=[_point("p1")])
    assert cells["p1"].value == 1.0
    with pytest.raises(AbortWMutator) as excinfo:
        evaluator.evaluate_corpus(build, row_id="row-2", points=[_point("p1")])
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_evaluate_corpus_rechecks_the_substituted_value(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    build = mutator.make_build(PLUS_J, row_id="row-1")
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(
            corpus_values=lambda pid, value: 1.0, lie_readback=True
        )
    )
    with pytest.raises(AbortWMutator) as excinfo:
        evaluator.evaluate_corpus(build, row_id="row-1", points=[_point("p1")])
    assert "evaluation-time readback" in str(excinfo.value)
    # HIGH-1: the mismatch is a FACT with a fingerprint, never the value —
    # the lying readback (10001.0) may stand in for a quarantined held W.
    assert "10001" not in str(excinfo.value)
    assert observed_value_fingerprint(10_001.0) in str(excinfo.value)


def test_evaluate_corpus_refuses_an_unreported_row(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    build = mutator.make_build(MINUS_J, row_id="row-1")
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(
            corpus_values=lambda pid, value: 1.0, drop_point="p2"
        )
    )
    with pytest.raises(AbortWMutator) as excinfo:
        evaluator.evaluate_corpus(
            build, row_id="row-1", points=[_point("p1"), _point("p2")]
        )
    assert "no cell for eligible rows" in str(excinfo.value)


def test_evaluate_corpus_refuses_an_empty_corpus(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    build = mutator.make_build(CONTROL_J, row_id="row-1")
    evaluator = PatchedBuildEvaluator(worker_runner=_make_runner())
    with pytest.raises(AbortWMutator):
        evaluator.evaluate_corpus(build, row_id="row-1", points=[])


def test_typed_missing_cells_carry_the_engine_status(tmp_path) -> None:
    mutator = _mutator(tmp_path, _make_runner())
    build = mutator.make_build(PLUS_J, row_id="row-1")
    evaluator = PatchedBuildEvaluator(
        worker_runner=_make_runner(corpus_values=lambda pid, value: None)
    )
    cells = evaluator.evaluate_corpus(build, row_id="row-1", points=[_point("p1")])
    assert cells["p1"].value is None
    assert cells["p1"].typed_reason == "refused: synthetic refusal"


def test_injected_evaluator_seam_is_not_production() -> None:
    assert PatchedBuildEvaluator(worker_runner=lambda spec: {"ok": True}).production is False
    assert PatchedBuildEvaluator().production is True


# -- worker-side build-identity guards -----------------------------------


def test_build_prefix_resolution_guard(tmp_path, monkeypatch) -> None:
    prefix = tmp_path / "lib"
    prefix.mkdir()
    dylib = prefix / "libphaseobjc.dylib"
    dylib.write_bytes(b"synthetic")
    other = tmp_path / "pristine"
    other.mkdir()
    (other / "libphaseobjc.dylib").write_bytes(b"pristine")

    monkeypatch.setattr("ctypes.util.find_library", lambda name: str(dylib))
    assert worker.assert_build_prefix_resolves(str(prefix)) == prefix.resolve()

    # The exact silent-perturbation route this guard exists to block: the
    # pristine staged dylib winning resolution.
    monkeypatch.setattr(
        "ctypes.util.find_library",
        lambda name: str(other / "libphaseobjc.dylib"),
    )
    with pytest.raises(worker._WorkerAbort) as excinfo:
        worker.assert_build_prefix_resolves(str(prefix))
    assert "does not point at the requested build prefix" in str(excinfo.value)

    monkeypatch.setattr("ctypes.util.find_library", lambda name: None)
    with pytest.raises(worker._WorkerAbort):
        worker.assert_build_prefix_resolves(str(prefix))

    with pytest.raises(worker._WorkerAbort):
        worker.assert_build_prefix_resolves(str(tmp_path / "absent"))


def test_live_substitution_guard() -> None:
    names = [SYNTH_PARAM, "W(SYNTH-A   ,SYNTH-C)"]
    slot, readback = worker._assert_substitution_live(
        names, [10_000.0, -222.5], SYNTH_PARAM, 10_000.0
    )
    assert (slot, readback) == (0, 10_000.0)
    with pytest.raises(worker._WorkerAbort) as excinfo:
        worker._assert_substitution_live(
            names, [111.0, -222.5], SYNTH_PARAM, 10_000.0
        )
    assert "numerically silent" in str(excinfo.value)
    # HIGH-1: the mismatch message carries the slot and a fingerprint of
    # the observed value, never the value itself.
    assert "111.0" not in str(excinfo.value)
    assert "slot 0" in str(excinfo.value)
    assert observed_value_fingerprint(111.0) in str(excinfo.value)


def test_evaluating_commands_are_registered() -> None:
    assert "evaluate-corpus" in worker._COMMANDS
    assert "synthetic-chem-potential" in worker._COMMANDS
