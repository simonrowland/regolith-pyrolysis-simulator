"""W0-SENS screen-runner tests — synthetic bench sets and fake builds only.

Every candidate join, parameter name, bench-set row, and engine response
below is fabricated. No test compiles a build, touches thermoengine, reads a
quarantined W value, or produces a real ranking; the ranking assertions run
against invented metrics.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import re
from pathlib import Path

import pytest
import yaml

from benchmarks.w0_sens import screen, w_mutator
from benchmarks.w0_sens.corpus import read_eligible_corpus
from benchmarks.w0_sens.driver import BootstrapInterval, JoinMetrics
from benchmarks.w0_sens.evaluator import PatchedBuildEvaluator
from benchmarks.w0_sens.screen import (
    CSV_FIELDS,
    FROZEN_CANDIDATES,
    PUBLIC_AGGREGATE_FIELDS,
    AbortEvidenceGrade,
    AbortRankingInvalidated,
    CandidateJoin,
    EvidenceGradeLock,
    ScreenPreconditionRefusal,
    ScreenRunner,
    decide_ranking,
    render_csv,
)
from benchmarks.w0_sens.w_mutator import W0WMutator, observed_value_fingerprint


FAKE_SOURCE = (
    "/* synthetic stand-in for src/LiquidMelts.m */\n"
    "static double referenceValuesOfModelParameters[] = {\n"
    "\t 111.0,  //  0 W(SYNTH-A   ,SYNTH-B   )\n"
    "\t-222.5,  //  1 W(SYNTH-A   ,SYNTH-C   )\n"
    "\t 333.25,  //  2 W(SYNTH-B   ,SYNTH-C   )\n"
    "};\n"
)
FAKE_ENDMEMBERS = ("SYNTH-A", "SYNTH-B", "SYNTH-C", "SYNTH-D")

RANK1 = CandidateJoin(
    expected_rank="1",
    join_id="SA-SB",
    param_name="W(SYNTH-A   ,SYNTH-B)",
    snapshot_row=10,
    I_expected=5,
    R_expected=15,
    disposition="selected",
    na_anchor=True,
)
FOURTH = CandidateJoin(
    expected_rank="4",
    join_id="SA-SC",
    param_name="W(SYNTH-A   ,SYNTH-C)",
    snapshot_row=4,
    I_expected=3,
    R_expected=9,
    disposition="selected",
    is_fourth=True,
)
RESERVE = CandidateJoin(
    expected_rank="5-reserve",
    join_id="SB-SC",
    param_name="W(SYNTH-B   ,SYNTH-C)",
    snapshot_row=None,
    I_expected=2,
    R_expected=6,
    disposition="reserve",
    is_reserve=True,
)
SYNTH_CANDIDATES = (RANK1, FOURTH, RESERVE)

SOURCE_A = "synthlab-a_kems_na2o_sio2"
SOURCE_B = "synthlab-b_emf_na2o_sio2"


# -- synthetic bench set -------------------------------------------------


def _bench_set(tmp_path: Path) -> Path:
    def point(point_id, population, composition_id, temperature_K):
        return {
            "id": point_id,
            "population": population,
            "composition_id": composition_id,
            "material_class": "na_silicate_binary_melt",
            "temperature_K": temperature_K,
            "parent_oxide": "SiO2",
            "species": "SiO",
            "observable": "activity",
            "measured": 0.8,
            "units": "dimensionless",
            "score": True,
            "scoring_status": "SCORED-ELIGIBLE",
            "reduction_class": "experimental_gibbs_duhem_derived",
            "provenance": {"source_sha256": "a" * 64},
        }

    document = {
        "schema_version": "melt-activity-bench.v1",
        "title": "synthetic bench set",
        "compositions": {
            "synth_x080": {
                "material_class": "na_silicate_binary_melt",
                "composition_wt_pct": {"SiO2": 80.0, "Na2O": 20.0},
            },
            "synth_x060": {
                "material_class": "na_silicate_binary_melt",
                "composition_wt_pct": {"SiO2": 60.0, "Na2O": 40.0},
            },
        },
        "points": [
            point("a-1373", SOURCE_A, "synth_x080", 1373.0),
            point("a-1473", SOURCE_A, "synth_x080", 1473.0),
            point("b-1373", SOURCE_B, "synth_x060", 1373.0),
            point("b-1473", SOURCE_B, "synth_x060", 1473.0),
        ],
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _evidence_lock(joins) -> EvidenceGradeLock:
    document = {
        "joins": {
            join.join_id: {
                "independent_sources": ["synthetic source 1", "synthetic source 2"],
                "direct_calorimetry_or_phase_equilibrium_brackets": ["synthetic bracket"],
                "independent_derivation": ["synthetic CALPHAD agreement"],
            }
            for join in joins
        }
    }
    return EvidenceGradeLock.from_bytes(
        json.dumps(document, sort_keys=True).encode("utf-8"),
        required_joins=[join.join_id for join in joins],
    )


# -- fake engine ---------------------------------------------------------


def _fake_pairs(prefix_lib: Path):
    text = (Path(prefix_lib) / "libphaseobjc.dylib").read_bytes().decode()
    pairs = []
    for line in text.splitlines():
        match = w_mutator._INITIALIZER_RE.match(line)
        if match is None:
            continue
        name = w_mutator.PARAM_NAME_RE.fullmatch(match.group("name"))
        pairs.append(
            (f"W({name.group(1):<10},{name.group(2)})", float(match.group("num")))
        )
    return pairs


def _fake_runner(shifts, *, trace=None, gate_silent=False):
    """Worker fake. ``shifts`` maps param_name -> per-row plus-side dex shift."""

    def runner(spec):
        command = spec["command"]
        pairs = _fake_pairs(Path(spec["prefix_lib"]))
        names = [name for name, _ in pairs]
        values = [value for _, value in pairs]
        units = ["joules"] * len(names)
        wanted = spec.get("param_name")
        slot = names.index(wanted) if wanted in names else None
        if trace is not None:
            trace.append((command, wanted))

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
                "vector_sha256": "synthetic",
            }
        if command == "readback-build":
            envelope = json.loads(Path(spec["envelope_path"]).read_text())
            changed = tuple(
                index
                for index, (before, after) in enumerate(
                    zip(envelope["values"], values)
                )
                if float(before) != float(after)
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
                "vector_sha256": "synthetic",
            }
        if command == "synthetic-chem-potential":
            component_mol = {str(k): float(v) for k, v in spec["component_mol"].items()}
            base = float(spec["base_mol"])
            mol = {name: base + component_mol.get(name, 0.0) for name in FAKE_ENDMEMBERS}
            total = sum(mol.values())
            fractions = {name: value / total for name, value in mol.items()}
            target = str(spec["target_endmember"])
            other = next(name for name in component_mol if name != target)
            basis = fractions[other] * (1.0 - fractions[target])
            substituted = 0.0 if gate_silent else float(values[slot])
            return {
                "ok": True,
                "mu_J_per_mol": -1.0e6 + substituted * basis,
                "readback_J": float(values[slot]),
                "slot_index": slot,
                "n_endmembers": len(FAKE_ENDMEMBERS),
                "endmember_names": list(FAKE_ENDMEMBERS),
                "mole_fractions": fractions,
            }
        if command == "evaluate-corpus":
            shift = float(shifts[str(wanted)])
            perturbation = float(spec["expected_value_J"])
            factor = 1.0 if perturbation <= 0.0 else 10.0**shift
            return {
                "ok": True,
                "readback_J": float(values[slot]),
                "slot_index": slot,
                "n_slots": len(names),
                "cells": [
                    {
                        "point_id": point["id"],
                        "value": factor,
                        "status": "ok",
                        "reason": "",
                    }
                    for point in spec["points"]
                ],
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


def _runner(tmp_path: Path, shifts, *, trace=None, gate_silent=False, builder=None) -> ScreenRunner:
    engine_root = _engine_root(tmp_path)
    pristine = _pristine_lib(tmp_path)
    worker_runner = _fake_runner(shifts, trace=trace, gate_silent=gate_silent)

    def factory(param_name: str, quarantine_dir: Path) -> W0WMutator:
        return W0WMutator(
            param_name=param_name,
            quarantine_dir=quarantine_dir,
            engine_root=engine_root,
            pristine_dylib_dir=pristine,
            builder=builder if builder is not None else _fake_builder,
            worker_runner=worker_runner,
        )

    return ScreenRunner(
        corpus=read_eligible_corpus(_bench_set(tmp_path)),
        evidence_lock=_evidence_lock(SYNTH_CANDIDATES),
        quarantine_root=tmp_path / "wq1",
        run_root=tmp_path / "runs",
        evaluator=PatchedBuildEvaluator(worker_runner=worker_runner),
        mutator_factory=factory,
        candidates=SYNTH_CANDIDATES,
    )


# -- the frozen candidate table ------------------------------------------


def test_frozen_candidate_table_matches_the_preregistration() -> None:
    assert [c.join_id for c in FROZEN_CANDIDATES] == [
        "Na2SiO3-SiO2",
        "CaSiO3-SiO2",
        "Mg2SiO4-SiO2",
        "Fe2SiO4-SiO2",
        "CaSiO3-Mg2SiO4",
    ]
    assert [c.R_expected for c in FROZEN_CANDIDATES] == [15, 12, 12, 9, 6]
    by_id = {c.join_id: c for c in FROZEN_CANDIDATES}
    assert by_id["Na2SiO3-SiO2"].na_anchor is True
    assert by_id["Fe2SiO4-SiO2"].is_fourth is True  # AMBIGUITY A-4
    assert by_id["CaSiO3-Mg2SiO4"].is_reserve is True
    assert sum(c.is_reserve for c in FROZEN_CANDIDATES) == 1
    assert sum(c.is_fourth for c in FROZEN_CANDIDATES) == 1
    # Section 8.1 snapshot rows and their exact engine parameter names.
    assert by_id["Fe2SiO4-SiO2"].snapshot_row == 4
    assert by_id["Na2SiO3-SiO2"].param_name == "W(Na2SiO3   ,SiO2)"
    # A-1: the build/row identity is the W snapshot row, not a bench row.
    assert by_id["Na2SiO3-SiO2"].row_id == "snapshot-row-10::Na2SiO3-SiO2"


# -- the ranking decision ------------------------------------------------


def _metrics(R: float) -> JoinMetrics:
    return JoinMetrics(
        evidence_grade=3,
        C=1,
        S=R / 3.0,
        I_measured=R / 3.0,
        R_measured=R,
        n_channels=1,
        n_missing=0,
        n_clusters=1,
        affected_channels=("synthetic",),
    )


def _interval(h: float) -> BootstrapInterval:
    return BootstrapInterval(
        seed=649013, replicates=10_000, lower=-h, upper=h, half_width=h
    )


def test_ranking_invalidated_when_reserve_beats_fourth_by_more_than_max_h() -> None:
    metrics = {"SA-SB": _metrics(30.0), "SA-SC": _metrics(10.0), "SB-SC": _metrics(16.0)}
    intervals = {"SA-SB": _interval(1.0), "SA-SC": _interval(2.0), "SB-SC": _interval(3.0)}
    with pytest.raises(AbortRankingInvalidated) as excinfo:
        decide_ranking(SYNTH_CANDIDATES, metrics, intervals)
    assert excinfo.value.abort_type == "ABORT-RANKING-INVALIDATED"


def test_ranking_threshold_is_strict_and_uses_the_larger_half_width() -> None:
    """``margin > max(h)`` — equality does NOT fire, and it is max, not min."""
    metrics = {"SA-SB": _metrics(30.0), "SA-SC": _metrics(10.0), "SB-SC": _metrics(13.0)}
    # margin = 3.0; max(h) = 3.0 -> not invalidated (strict >).
    boundary = decide_ranking(
        SYNTH_CANDIDATES,
        metrics,
        {"SA-SB": _interval(1.0), "SA-SC": _interval(1.0), "SB-SC": _interval(3.0)},
    )
    assert boundary.invalidated is False
    assert boundary.margin == pytest.approx(3.0)
    assert boundary.threshold == pytest.approx(3.0)
    # min(h) would have been 1.0 and would have fired here.
    assert boundary.reserve_half_width == 3.0
    assert boundary.fourth_half_width == 1.0
    # A hair over the threshold DOES fire.
    with pytest.raises(AbortRankingInvalidated):
        decide_ranking(
            SYNTH_CANDIDATES,
            metrics,
            {
                "SA-SB": _interval(1.0),
                "SA-SC": _interval(1.0),
                "SB-SC": _interval(3.0 - 1.0e-9),
            },
        )


def test_ranking_reorders_execution_but_never_membership() -> None:
    metrics = {"SA-SB": _metrics(4.0), "SA-SC": _metrics(9.0), "SB-SC": _metrics(1.0)}
    intervals = {k: _interval(2.0) for k in metrics}
    decision = decide_ranking(SYNTH_CANDIDATES, metrics, intervals)
    # Measured R reorders execution: the rank-4 join now runs first.
    assert decision.execution_order == ("SA-SC", "SA-SB")
    # Membership is the four selected joins, reserve excluded, unchanged.
    assert decision.membership == ("SA-SB", "SA-SC")
    assert decision.reserve_join == "SB-SC"
    assert decision.fourth_join == "SA-SC"


def test_ranking_refuses_a_partial_screen() -> None:
    with pytest.raises(ScreenPreconditionRefusal) as excinfo:
        decide_ranking(
            SYNTH_CANDIDATES,
            {"SA-SB": _metrics(1.0)},
            {"SA-SB": _interval(1.0)},
        )
    assert "every candidate screened" in str(excinfo.value)


# -- the evidence-grade lock ---------------------------------------------


def test_evidence_grade_lock_requires_all_three_classes() -> None:
    def lock(**overrides):
        entry = {
            "independent_sources": ["s1", "s2"],
            "direct_calorimetry_or_phase_equilibrium_brackets": ["b1"],
            "independent_derivation": ["d1"],
        }
        entry.update(overrides)
        return json.dumps({"joins": {"SA-SB": entry}}).encode()

    assert EvidenceGradeLock.from_bytes(
        lock(), required_joins=["SA-SB"]
    ).grades == {"SA-SB": 3}
    for overrides, fragment in (
        ({"independent_sources": ["s1"]}, "fewer than two independent sources"),
        ({"independent_sources": ["s1", "s1"]}, "repeats one citation"),
        ({"direct_calorimetry_or_phase_equilibrium_brackets": []}, "calorimetry"),
        ({"independent_derivation": []}, "independent derivation"),
    ):
        with pytest.raises(AbortEvidenceGrade) as excinfo:
            EvidenceGradeLock.from_bytes(lock(**overrides), required_joins=["SA-SB"])
        assert excinfo.value.abort_type == "ABORT-EVIDENCE-GRADE"
        assert fragment in str(excinfo.value)
    with pytest.raises(AbortEvidenceGrade):
        EvidenceGradeLock.from_bytes(lock(), required_joins=["SA-SB", "ABSENT"])
    with pytest.raises(AbortEvidenceGrade):
        EvidenceGradeLock.from_bytes(b"not json", required_joins=["SA-SB"])


def test_evidence_grade_lock_rejects_blank_and_non_independent_citations() -> None:
    """HIGH-3 / A-13: blank citations earn nothing, and ONE citation may
    not serve as source, bracket, and/or independent derivation."""

    def lock(entry):
        return json.dumps({"joins": {"SA-SB": entry}}).encode()

    good = {
        "independent_sources": ["s1", "s2"],
        "direct_calorimetry_or_phase_equilibrium_brackets": ["b1"],
        "independent_derivation": ["d1"],
    }
    assert EvidenceGradeLock.from_bytes(
        lock(good), required_joins=["SA-SB"]
    ).grades == {"SA-SB": 3}
    bad_entries = [
        # Blank / whitespace-only citations in each role.
        {**good, "independent_sources": ["", "s2"]},
        {**good, "independent_sources": ["s1", "   "]},
        {**good, "direct_calorimetry_or_phase_equilibrium_brackets": ["  "]},
        {**good, "independent_derivation": [""]},
        # Within-role repeats.
        {**good, "direct_calorimetry_or_phase_equilibrium_brackets": ["b1", "b1"]},
        {**good, "independent_derivation": ["d1", "d1"]},
        # One citation serving two roles.
        {**good, "direct_calorimetry_or_phase_equilibrium_brackets": ["s1"]},
        {**good, "independent_derivation": ["s2"]},
        {**good, "independent_derivation": ["b1"]},
        # The reviewer's fail-open case: ONE citation in all three roles.
        {
            "independent_sources": ["shared", "shared-other"],
            "direct_calorimetry_or_phase_equilibrium_brackets": ["shared"],
            "independent_derivation": ["shared"],
        },
    ]
    for entry in bad_entries:
        with pytest.raises(AbortEvidenceGrade) as excinfo:
            EvidenceGradeLock.from_bytes(lock(entry), required_joins=["SA-SB"])
        assert excinfo.value.abort_type == "ABORT-EVIDENCE-GRADE"


# -- the runner ----------------------------------------------------------


def test_screen_runs_every_gate_before_any_candidate_is_screened(tmp_path) -> None:
    trace: list[tuple[str, str]] = []
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
        trace=trace,
    )
    record = runner.run()
    assert record.abort_type is None, record.abort_detail

    commands = [command for command, _ in trace]
    first_corpus = commands.index("evaluate-corpus")
    gate_commands = commands[:first_corpus]
    # Every candidate's synthetic gate completed before the first corpus
    # evaluation of any candidate (PREREGISTRATION-wave0.md:37, A-6).
    gate_params = {
        param
        for command, param in trace[:first_corpus]
        if command == "synthetic-chem-potential"
    }
    assert gate_params == {c.param_name for c in SYNTH_CANDIDATES}
    assert "evaluate-corpus" not in gate_commands
    # Three signed-response probes per join: 0 J control, +10 kJ, -10 kJ.
    assert gate_commands.count("synthetic-chem-potential") == 3 * len(SYNTH_CANDIDATES)

    for result in record.joins:
        assert result.status == "SCREENED"
        # A-1: exactly three corpus builds per join.
        assert sorted(result.builds) == ["control", "minus", "plus"]
        assert result.gate is not None
        assert len(result.gate["signed_responses"]) == 2
        assert result.restoration["vector_matches_pristine"] is True

    rank1 = record.join("SA-SB")
    assert rank1.metrics.C == 4  # every row shifted 0.20 dex
    assert rank1.metrics.S == pytest.approx(2.0)
    assert rank1.metrics.R_measured == pytest.approx(3 * 4 * 2.0)
    reserve = record.join("SB-SC")
    assert reserve.metrics.C == 0  # frozen C=0 convention
    assert reserve.metrics.R_measured == 0.0
    assert record.ranking is not None
    assert record.ranking.invalidated is False
    assert record.ranking.execution_order == ("SA-SB", "SA-SC")


def test_screen_writes_the_immutable_run_record(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    record = runner.run()
    run_dir = Path(record.run_dir)
    for name in (
        "SCREEN-RESULTS.md",
        "SCREEN-RESULTS.csv",
        "RUN-RECORD.json",
        "W0-SENS-LOCK.json",
        "W0-SENS-ELIGIBLE-MANIFEST.json",
    ):
        assert (run_dir / name).is_file(), name
    # Injected seams: NOT a production screen, so no releasable aggregate.
    assert record.production is False
    assert not (run_dir / "W0-SENS-PUBLIC-AGGREGATE.json").exists()
    with pytest.raises(ScreenPreconditionRefusal):
        record.public_aggregate()

    run_record = json.loads((run_dir / "RUN-RECORD.json").read_text())
    assert run_record["typed_failure_status"] is None
    assert run_record["eligible_corpus"]["eligible_rows"] == 4
    assert run_record["eligible_corpus"]["eligible_clusters"] == 2
    lock = json.loads((run_dir / "W0-SENS-LOCK.json").read_text())
    join = lock["joins"][0]
    # Step-2 lock contents: source and binary hashes, exact parameter name,
    # exact readback, structural diff, both signed responses, restoration.
    assert join["builds"]["plus"]["binary_sha256"]
    assert join["builds"]["plus"]["patched_source_sha256"]
    assert join["builds"]["plus"]["diff_sha256"]
    assert join["builds"]["plus"]["readback_J"] == 10_000.0
    assert join["builds"]["plus"]["changed_slots"] == [join["builds"]["plus"]["slot_index"]]
    assert len(join["synthetic_gate"]["signed_responses"]) == 2
    assert join["restoration"]["vector_matches_pristine"] is True
    assert [d["id"] for d in lock["disclosures"]] == [
        "D1-gibbs-duhem-derived-channel",
        "D2-standard-state-conversion",
        "D3-extrapolation-flagged-held-rows",
    ]
    # The instrument inventory is pinned per attempt, runner included.
    assert "benchmarks/w0_sens/screen.py" in lock["pins"]["files"]
    assert "benchmarks/w0_sens/evaluator.py" in lock["pins"]["files"]
    assert "benchmarks/w0_sens/corpus.py" in lock["pins"]["files"]


def test_run_directory_is_immutable(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    created = runner._create_run_dir("fixed-id")
    assert created.is_dir()
    with pytest.raises(ScreenPreconditionRefusal) as excinfo:
        runner._create_run_dir("fixed-id")
    assert "immutable" in str(excinfo.value)


def test_na_anchor_null_instrument_aborts_and_produces_no_ranking(tmp_path) -> None:
    """C=0 on the rank-1 join is the frozen typed abort, never a ranking."""
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.01,  # below the 0.05 dex threshold
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    record = runner.run()
    assert record.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"
    assert record.ranking is None
    assert record.join("SA-SB").status == "NOT_RUN"
    assert "INCONCLUSIVE/REFUSED" in record.outcome
    # The partial record is still written, never replaced by a summary.
    body = (Path(record.run_dir) / "SCREEN-RESULTS.md").read_text()
    assert "ABORT-RANKING-INSTRUMENT-NULL" in body
    rows = list(csv.DictReader(io.StringIO(render_csv(record))))
    assert {row["status"] for row in rows} == {"NOT_RUN"}
    assert {row["abort_type"] for row in rows} == {"ABORT-RANKING-INSTRUMENT-NULL"}


def test_synthetic_gate_failure_stops_before_any_chemical_evaluation(tmp_path) -> None:
    trace: list[tuple[str, str]] = []
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
        trace=trace,
        gate_silent=True,
    )
    record = runner.run()
    assert record.abort_type == "ABORT-W-MUTATOR"
    assert record.ranking is None
    assert all(result.status == "NOT_RUN" for result in record.joins)
    assert all(not result.builds for result in record.joins)
    assert "evaluate-corpus" not in [command for command, _ in trace]


def test_ranking_invalidated_is_recorded_as_a_typed_abort(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.30,  # the reserve out-scores the fourth
        },
    )
    record = runner.run()
    assert record.abort_type == "ABORT-RANKING-INVALIDATED"
    assert record.ranking is None
    # Every join was still screened; only the decision refused.
    assert all(result.status == "SCREENED" for result in record.joins)


def test_public_aggregate_releases_only_the_restricted_fields(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    record = runner.run()
    released = dataclasses.replace(record, production=True).public_aggregate()
    for row in released["rows"]:
        assert set(row) == set(PUBLIC_AGGREGATE_FIELDS)
    blob = json.dumps(released)
    # A-10: bounds are custodian-record only.
    for forbidden in ("bootstrap", "lower", "upper", "half_width", "prefix", "y_control"):
        assert forbidden not in blob
    assert released["rows"][0]["row_id"] == "snapshot-row-10::SA-SB"
    assert released["rows"][0]["typed_missing_cells"] == 0


def test_csv_header_is_the_frozen_schema(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    record = runner.run()
    text = render_csv(record)
    assert text.splitlines()[0] == ",".join(CSV_FIELDS)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["candidate_join"] for row in rows] == ["SA-SB", "SA-SC", "SB-SC"]
    assert rows[0]["evidence_grade"] == "3"
    assert rows[0]["control_artifact_sha256"]


def test_screen_refuses_a_quarantine_root_inside_the_repository(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    runner.quarantine_root = w_mutator.repo_root() / "instance" / "wq1"
    # HIGH-4/A-11: a precondition refusal is a recorded typed abort, never
    # a bare exception escaping without the immutable record.
    record = runner.run()
    assert record.abort_type == "ABORT-W0-SENS-PRECONDITION"
    assert "OUTSIDE the repository tree" in record.abort_detail
    run_dir = Path(record.run_dir)
    body = json.loads((run_dir / "RUN-RECORD.json").read_text())
    assert body["typed_failure_status"] == "ABORT-W0-SENS-PRECONDITION"
    assert "OUTSIDE the repository tree" in body["typed_failure_detail"]
    assert (run_dir / "SCREEN-RESULTS.md").is_file()


def test_screen_refuses_a_join_with_no_sealed_evidence_grade(tmp_path) -> None:
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
    )
    runner.evidence_lock = _evidence_lock((RANK1, FOURTH))
    # HIGH-4/A-11: ABORT-EVIDENCE-GRADE leaves the immutable run record.
    record = runner.run()
    assert record.abort_type == "ABORT-EVIDENCE-GRADE"
    run_dir = Path(record.run_dir)
    body = json.loads((run_dir / "RUN-RECORD.json").read_text())
    assert body["typed_failure_status"] == "ABORT-EVIDENCE-GRADE"
    assert "SB-SC" in body["typed_failure_detail"]


def test_cli_evidence_lock_abort_leaves_an_immutable_record(tmp_path) -> None:
    """HIGH-4: lock parsing runs before runner construction; the abort must
    still leave a section-9 record under the run root."""
    bench = _bench_set(tmp_path)
    lock_path = tmp_path / "EVIDENCE-GRADE-LOCK.json"
    lock_path.write_text(
        json.dumps(
            {
                "joins": {
                    # One citation repeated as both "independent" sources.
                    join.join_id: {
                        "independent_sources": ["one citation", "one citation"],
                        "direct_calorimetry_or_phase_equilibrium_brackets": ["b"],
                        "independent_derivation": ["d"],
                    }
                    for join in SYNTH_CANDIDATES
                }
            }
        )
    )
    run_root = tmp_path / "cli-runs"
    exit_code = screen.main(
        [
            "--bench-set", str(bench),
            "--evidence-grade-lock", str(lock_path),
            "--quarantine-root", str(tmp_path / "wq1"),
            "--run-root", str(run_root),
        ]
    )
    assert exit_code == 2
    runs = list(run_root.iterdir())
    assert len(runs) == 1
    record = json.loads((runs[0] / "RUN-RECORD.json").read_text())
    assert record["typed_failure_status"] == "ABORT-EVIDENCE-GRADE"
    assert record["production"] is False
    assert (runs[0] / "SCREEN-RESULTS.md").is_file()
    assert "ABORT-EVIDENCE-GRADE" in (runs[0] / "SCREEN-RESULTS.md").read_text()


def test_readback_mismatch_never_discloses_the_observed_value(tmp_path) -> None:
    """HIGH-1 reviewer probe, end to end: a build whose readback mismatches
    (here an invented stand-in for the held W) must produce a MISMATCH FACT
    — slot plus fingerprint — and the value must appear NOWHERE in any
    emitted artifact.
    """
    invented_held_value = 314159.0

    def doctored_builder(build_root: Path) -> None:
        # The patch is applied to the source but the "compiled" dylib
        # carries an invented value at the named slot — the shape of a
        # pristine-resolution failure.
        source = (build_root / "src" / "LiquidMelts.m").read_text()
        lines = source.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if "W(SYNTH-A   ,SYNTH-B   )" in line:
                lines[index] = re.sub(
                    r"-?\d+(?:\.\d+)?", repr(invented_held_value), line, count=1
                )
                break
        else:
            raise AssertionError("slot-0 initializer line not found")
        (build_root / "src" / "libphaseobjc.dylib").write_bytes(
            "".join(lines).encode()
        )

    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
        builder=doctored_builder,
    )
    record = runner.run()
    assert record.abort_type == "ABORT-W-MUTATOR"
    run_dir = Path(record.run_dir)
    blob = ""
    for artifact in run_dir.iterdir():
        if artifact.is_file():
            blob += artifact.read_text(errors="replace")
    for leaked in ("314159", "314159.0", repr(invented_held_value)):
        assert leaked not in blob, leaked
    # The mismatch FACT is recorded: the parameter slot and the fingerprint.
    assert record.abort_detail is not None and "slot" in record.abort_detail
    fingerprint = observed_value_fingerprint(invented_held_value)
    assert fingerprint in record.abort_detail
    assert fingerprint in blob


def test_restoration_is_verified_even_when_the_screen_aborts(tmp_path) -> None:
    """MEDIUM-7: an abort during the synthetic-gate phase must still verify
    pristine restoration for every session that captured the engine."""
    trace: list[tuple[str, str]] = []
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
        trace=trace,
        gate_silent=True,
    )
    record = runner.run()
    assert record.abort_type == "ABORT-W-MUTATOR"
    for result in record.joins:
        assert result.restoration is not None, result.candidate.join_id
        assert result.restoration["vector_matches_pristine"] is True
    commands = [command for command, _ in trace]
    assert commands.count("verify-restoration") == len(SYNTH_CANDIDATES)
    # And the restoration verdicts land in the immutable custodian lock.
    lock = json.loads((Path(record.run_dir) / "W0-SENS-LOCK.json").read_text())
    for join in lock["joins"]:
        assert join["restoration"]["vector_matches_pristine"] is True


def test_restoration_failure_on_an_abort_path_is_recorded(tmp_path) -> None:
    """MEDIUM-7: a restoration failure on an already-aborting run is added
    to the record, not dropped."""
    runner = _runner(
        tmp_path,
        {
            "W(SYNTH-A   ,SYNTH-B)": 0.20,
            "W(SYNTH-A   ,SYNTH-C)": 0.10,
            "W(SYNTH-B   ,SYNTH-C)": 0.01,
        },
        gate_silent=True,
    )
    # Corrupt one session's post-build pristine readback.
    original = W0WMutator.verify_restoration

    def failing_restoration(self):
        if self.param_name == "W(SYNTH-A   ,SYNTH-C)":
            raise w_mutator.AbortWMutator("synthetic restoration failure")
        return original(self)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(W0WMutator, "verify_restoration", failing_restoration)
        record = runner.run()
    assert record.abort_type == "ABORT-W-MUTATOR"
    assert "synthetic restoration failure" in record.abort_detail
