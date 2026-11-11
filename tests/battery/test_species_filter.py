from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import battery_score
from simulator.battery import score as score_mod
from simulator.battery.enums import (
    Engine,
    EvidenceClass,
    ExecutionState,
    Phase,
    Quantity,
    Rail,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Execution, Residual, Species

from . import factories as F


def _context_with_species():
    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    observations = (
        F.observation(
            "mn-gas",
            experiment.experiment_id,
            F.psat_identity("Mn"),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        ),
        F.observation(
            "mn-solid",
            experiment.experiment_id,
            Identity(Quantity.DELTA_FG, Species("Mn", Phase.CR)),
            Decimal("2"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        ),
        F.observation(
            "mno-gas",
            experiment.experiment_id,
            F.psat_identity("MnO"),
            Decimal("3"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        ),
        F.observation(
            "co-gas",
            experiment.experiment_id,
            F.psat_identity("Co"),
            Decimal("4"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        ),
        F.observation(
            "carbon-monoxide-gas",
            experiment.experiment_id,
            F.psat_identity("CO"),
            Decimal("5"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        ),
    )
    context = score_mod.ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row for row in observations},
        hostname="test",
    )
    return context


def test_species_queries_resolve_exact_formula_phase_and_case() -> None:
    context = _context_with_species()

    assert context.observations["mn-gas"].identity.species.phase.value is Phase.G
    assert score_mod.normalize_species_filters(context, [" Mn "]) == ("Mn",)
    assert score_mod.normalize_species_filters(context, ["mno"]) == ("MnO",)
    assert score_mod.normalize_species_filters(context, ["CO"]) == ("CO",)
    with pytest.raises(score_mod.AmbiguousSpeciesFilterError) as ambiguous:
        score_mod.normalize_species_filters(context, ["cO"])
    assert ambiguous.value.matches == ("CO", "Co")
    with pytest.raises(score_mod.UnknownSpeciesFilterError) as unknown:
        score_mod.normalize_species_filters(context, ["Mnn"])
    assert "Mn" in unknown.value.near_matches
    assert "near matches" in str(unknown.value)


def test_species_scoring_keeps_full_context_and_emits_identical_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context_with_species()
    original_ids = set(context.observations)
    engine_calls: list[str] = []

    def compile_stub(reference, engine, **_kwargs):
        engine_calls.append(reference.observation_id)
        assert set(_kwargs["context"].observations) == original_ids
        formula = reference.identity.species.formula
        residual = Residual(
            key=f"fixture::{reference.observation_id}::{engine.value}",
            reference=reference.observation_id,
            execution=Execution(state=ExecutionState.NOT_PROBED),
            rail=score_mod.rail_for_quantity(
                reference.identity.quantity.value,
                species_formula=formula,
            ),
            status=ResidualStatus.NO_BAND,
            source_relation=SourceRelation.UNKNOWN,
            score_eligible=False,
            exclusions=(),
            notices=(),
        )
        return residual, None

    monkeypatch.setattr(score_mod, "_compile_residual_with_lineage", compile_stub)
    all_path = tmp_path / "all.jsonl"
    species_path = tmp_path / "species.jsonl"
    all_count, _ = score_mod._score_store_to_jsonl(
        context,
        all_path,
        root=score_mod.REPO_ROOT,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=False,
    )
    assert set(engine_calls) == original_ids
    engine_calls.clear()
    formulas = score_mod.normalize_species_filters(context, ["Mn"])
    selected_count, metadata = score_mod._score_store_to_jsonl(
        context,
        species_path,
        root=score_mod.REPO_ROOT,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        species_formulas=formulas,
        include_diagnostics=False,
    )
    assert set(engine_calls) == {"mn-gas", "mn-solid"}
    engine_calls.clear()
    combined_path = tmp_path / "combined.jsonl"
    combined_count, combined_metadata = score_mod._score_store_to_jsonl(
        context,
        combined_path,
        root=score_mod.REPO_ROOT,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.VAPOUR,
        species_formulas=formulas,
        work_id="work-1",
        limit=1,
        include_diagnostics=False,
    )
    assert engine_calls == ["mn-gas"]

    def row_bytes(path: Path) -> dict[str, bytes]:
        return {
            str(row["reference"]): line
            for line in path.with_name(path.name + ".partial").read_bytes().splitlines(
                keepends=True
            )[1:]
            if (row := json.loads(line))
        }

    all_rows = row_bytes(all_path)
    selected_rows = row_bytes(species_path)
    combined_rows = row_bytes(combined_path)
    expected_ids = {
        observation.observation_id
        for observation in context.observations.values()
        if observation.identity.species is not None
        and observation.identity.species.formula == "Mn"
    }
    assert set(all_rows) == original_ids
    assert set(selected_rows) == expected_ids == {"mn-gas", "mn-solid"}
    assert {key: all_rows[key] for key in expected_ids} == selected_rows
    assert set(combined_rows) == {"mn-gas"}
    assert combined_rows["mn-gas"] == all_rows["mn-gas"]
    assert all_count == len(original_ids)
    assert selected_count == metadata.selected_reference_count == len(expected_ids)
    assert combined_count == combined_metadata.selected_reference_count == 1
    assert set(context.observations) == original_ids


def test_filtered_cli_keeps_published_artifacts_and_labels_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context_with_species()
    battery = tmp_path / "data" / "battery"
    battery.mkdir(parents=True)
    published = {
        "residuals.jsonl": b"published residuals\n",
        "score-summary.json": b"published summary\n",
        "score-report.md": b"published report\n",
        "compilation-comparisons.jsonl": b"published comparisons\n",
    }
    for name, content in published.items():
        (battery / name).write_bytes(content)
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    monkeypatch.setattr(score_mod, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "load_score_context", lambda _root: context)
    monkeypatch.setattr(
        battery_score, "emit_store_stamp_mismatch_warning", lambda *_args: None
    )
    monkeypatch.setattr(battery_score, "load_legacy_score_rows", lambda _root: [])

    def fake_score(_context, path, **_kwargs):
        score_mod.write_residuals_jsonl((), {}, path.with_name(path.name + ".partial"))
        aggregate = SimpleNamespace(
            scored_count=0,
            summary_payload=lambda _store_stamp: {
                "records": [{"tier": "measured", "n": 2}]
            },
        )
        metadata = SimpleNamespace(
            family_band_values={},
            headline_band_values={},
            tier_band_values={},
            aggregate=aggregate,
            static_refusals=(),
            selected_reference_count=2,
        )
        return 0, metadata

    def fake_compilation(_context, path, **_kwargs):
        observation_filter = _kwargs.get("observation_filter")
        assert callable(observation_filter)
        assert {
            observation.observation_id
            for observation in _context.observations.values()
            if observation_filter(observation)
        } == {"mn-gas", "mn-solid"}
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(battery_score, "_score_store_to_jsonl", fake_score)
    monkeypatch.setattr(
        battery_score, "_write_compilation_comparisons_jsonl", fake_compilation
    )
    monkeypatch.setattr(
        battery_score,
        "_render_score_report_from_payloads_legacy",
        lambda *_args, **_kwargs: "# Battery score report\n",
    )
    monkeypatch.setattr(battery_score, "_append_nasa9_fit_comparison_stratum", lambda report, _rows: report)

    assert battery_score.main(
        [
            "--root",
            str(tmp_path),
            "--engines",
            Engine.INTERNAL_ANALYTICAL.value,
            "--species",
            " mn ",
            "--rail",
            Rail.VAPOUR.value,
            "--work",
            "work-1",
            "--limit",
            "3",
        ]
    ) == 0

    assert {name: (battery / name).read_bytes() for name in published} == published
    subset_dirs = tuple((battery / "subsets").iterdir())
    assert len(subset_dirs) == 1
    subset = subset_dirs[0]
    summary = json.loads((subset / "score-summary.json").read_text(encoding="utf-8"))
    assert summary["subset"] is True
    assert summary["selected_references"] == 2
    assert summary["filters"] == {
        "species": ["Mn"],
        "rail": Rail.VAPOUR.value,
        "work": "work-1",
        "limit": 3,
        "engines": [Engine.INTERNAL_ANALYTICAL.value],
    }
    assert summary["headline_aggregate_scope"] == "subset"
    assert summary["records"][0]["aggregate_scope"] == "subset"
    assert "SUBSET AGGREGATES" in (subset / "score-report.md").read_text(
        encoding="utf-8"
    )
    assert (subset / "residuals.jsonl").is_file()
    assert (subset / "compilation-comparisons.jsonl").is_file()


def test_unfiltered_cli_preserves_summary_bytes_and_completion_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    context = _context_with_species()
    battery = tmp_path / "data" / "battery"
    battery.mkdir(parents=True)
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    payload = {"records": [{"citation": "Münter 1972"}], "store_stamp": stamp}
    expected_summary = (
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(score_mod, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "derive_store_stamp", lambda _root: stamp)
    monkeypatch.setattr(battery_score, "load_score_context", lambda _root: context)
    monkeypatch.setattr(
        battery_score, "emit_store_stamp_mismatch_warning", lambda *_args: None
    )
    monkeypatch.setattr(battery_score, "load_legacy_score_rows", lambda _root: [])

    def fake_score(_context, path, **_kwargs):
        score_mod.write_residuals_jsonl(
            (), {}, path.with_name(path.name + ".partial")
        )
        aggregate = SimpleNamespace(
            scored_count=0, summary_payload=lambda _store_stamp: payload
        )
        metadata = SimpleNamespace(
            family_band_values={},
            headline_band_values={},
            tier_band_values={},
            aggregate=aggregate,
            static_refusals=(),
            selected_reference_count=0,
        )
        return 0, metadata

    def fake_compilation(_context, path, **_kwargs):
        assert "observation_filter" not in _kwargs
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(battery_score, "_score_store_to_jsonl", fake_score)
    monkeypatch.setattr(
        battery_score, "_write_compilation_comparisons_jsonl", fake_compilation
    )
    monkeypatch.setattr(
        battery_score,
        "_render_score_report_from_payloads_legacy",
        lambda *_args, **_kwargs: "# Battery score report\n",
    )
    monkeypatch.setattr(
        battery_score, "_append_nasa9_fit_comparison_stratum", lambda report, _rows: report
    )

    assert battery_score.main(["--root", str(tmp_path)]) == 0

    assert (battery / "score-summary.json").read_bytes() == expected_summary
    assert capsys.readouterr().out == (
        "residuals=0 scored=0 pin_failures=0 host=test\n"
    )


def test_cli_help_explains_filtered_artifact_location(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        battery_score.main(["--help"])

    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert "data/battery/subsets/<filter-hash>/" in help_text
    assert "run an unfiltered score to refresh the published headline" in help_text


def test_compilation_comparison_writer_filters_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from simulator.battery import compilation_tier

    context = _context_with_species()
    visited: list[str] = []
    filters: list[object] = []

    monkeypatch.setattr(
        compilation_tier,
        "_is_janaf_compilation_observation",
        lambda *_args: True,
    )

    def series_cells(observation, _origin):
        visited.append(observation.observation_id)
        return ()

    def nasa9_comparisons(
        _context, *, engines, invoke_pure_phase, observation_filter=None
    ):
        assert engines == ()
        assert invoke_pure_phase is False
        filters.append(observation_filter)
        return []

    monkeypatch.setattr(compilation_tier, "_iter_compilation_series_cells", series_cells)
    monkeypatch.setattr(
        compilation_tier, "_nasa9_fit_comparisons", nasa9_comparisons
    )

    formulas = score_mod.normalize_species_filters(context, ["Mn"])
    observation_filter = lambda observation: score_mod._matches_species_filter(
        observation, set(formulas)
    )
    assert compilation_tier._write_compilation_comparisons_jsonl(
        context,
        tmp_path / "comparisons.jsonl",
        engines=(),
        invoke_pure_phase=False,
        observation_filter=observation_filter,
    ) == 0
    assert visited == ["mn-gas", "mn-solid"]
    assert filters == [observation_filter]
