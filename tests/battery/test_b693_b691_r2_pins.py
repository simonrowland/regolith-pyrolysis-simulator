"""Round-2 review-of-record pins for b-693/b-691 (review of 043759ba3).

Each pin goes through production entry points: ``score_store`` /
``_score_store_to_jsonl`` and the public report writers (typed JSON,
streamed JSON, report-only payload JSON, and both Markdown renderers).
Injected predictions isolate admission, band and reporting policy; they are
not claims about engine accuracy. Expected values come from the review of
record (findings 1, 2, 3, 4, 6) and the owner mandate, not from score.py.
"""

from __future__ import annotations

import json
import types
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import simulator.battery.score as score_mod
from simulator.battery.enums import (
    AdmissionStatus,
    Authority,
    CellMaterial,
    Engine,
    EvidenceClass,
    ExecutionState,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    ResidualStatus,
    UncertaintyKind,
)
from simulator.battery.enums import QUANTITY_UNITS
from simulator.battery.identity import quantity_token
from simulator.battery.records import (
    Execution,
    Notice,
    Species,
    State,
    Uncertainty,
)
from simulator.battery.score import (
    ENGINE_CHANNELS,
    EnginePrediction,
    ScoreContext,
    headline_rows,
    point_magnitude,
    predict_with_engine,
    score_store,
)
from tests.battery import factories as F
from tests.battery.test_b693_b691_scorer_gates import (
    _demaria_context,
    _demaria_shape_figure_rows,
    _partial_identity,
)

INTERNAL = Engine.INTERNAL_ANALYTICAL
OPENIMCC = Engine.OPENIMCC


def _prediction(engine: Engine, obs, value: Decimal, notices=()) -> EnginePrediction:
    quantity = quantity_token(obs.identity)
    return EnginePrediction(
        engine=engine,
        channel=ENGINE_CHANNELS[engine],
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="test:predict"),
        value=value,
        unit=QUANTITY_UNITS[quantity] if quantity is not None else "Pa",
        authority=Authority.CERTIFIED,
        notices=tuple(notices),
        coefficient_sources=("test",),
        lineage_complete=True,
        identity=obs.identity,
    )


def _context(observations, *, experiments, benches=None) -> ScoreContext:
    work = F.work()
    review = {"": "reviewed"}
    for obs in observations:
        if obs.source_id:
            review[obs.source_id] = "reviewed"
    return ScoreContext(
        works={work.work_id: work},
        experiments={e.experiment_id: e for e in experiments},
        observations={o.observation_id: o for o in observations},
        benches=benches or {},
        extract_review=review,
        hostname="test",
    )


def _all_writers(context, engines, predict, tmp_path: Path) -> dict[str, object]:
    """Run every production writer over one scored fixture."""

    root = score_mod.REPO_ROOT
    stamp = score_mod.derive_store_stamp(root)
    residuals, _candidates = score_store(
        context, engines=engines, include_diagnostics=True, predict=predict
    )
    typed_json = tmp_path / "typed-summary.json"
    score_mod.write_headline_summary_json(
        residuals, typed_json, context=context, engines=engines, root=root
    )
    typed_md = score_mod.render_score_report(
        residuals, context=context, engines=engines, root=root
    )
    stream_path = tmp_path / "residuals.jsonl"
    _written, metadata = score_mod._score_store_to_jsonl(
        context, stream_path, root=root, engines=engines, predict=predict
    )
    partial = stream_path.with_name(stream_path.name + ".partial")
    assert metadata.aggregate is not None
    stream_json = tmp_path / "stream-summary.json"
    score_mod._write_headline_summary_from_accumulator_json(
        metadata.aggregate, stream_json, store_stamp=stamp
    )
    stream_md = score_mod._render_score_report_from_payloads_legacy(
        score_mod._ResidualJsonlRows(partial, metadata=metadata),
        context=context,
        engines=engines,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    # Report-only production path: replayed JSONL without typed metadata.
    payload_rows = score_mod._ResidualJsonlRows(partial)
    payload_json = tmp_path / "payload-summary.json"
    score_mod.write_headline_summary_from_payloads_json(
        payload_rows,
        payload_json,
        engines=engines,
        observations=context.observations,
        origins=context.origins,
        store_stamp=stamp,
    )
    payload_md = score_mod.render_score_report_from_payloads(
        payload_rows,
        engines=engines,
        hostname="test",
        store_stamp=stamp,
        observations=context.observations,
        origins=context.origins,
    )

    def records(path: Path) -> list[dict[str, object]]:
        return json.loads(path.read_text(encoding="utf-8"))["records"]

    return {
        "residuals": residuals,
        "json": {
            "typed": records(typed_json),
            "stream": records(stream_json),
            "payload": records(payload_json),
        },
        "md": {"typed": typed_md, "stream": stream_md, "payload": payload_md},
    }


def _record(records, *, tier: str, rail: Rail, engine: Engine) -> dict[str, object]:
    matches = [
        row
        for row in records
        if row["tier"] == tier and row["rail"] == rail.value and row["engine"] == engine.value
    ]
    assert len(matches) == 1, (tier, rail, engine, len(matches))
    return matches[0]


def _section(report: str, heading: str) -> str:
    start = report.index(heading)
    rest = report[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def _a_section(report: str) -> str:
    return _section(report, "## All-numeric tier")


def _b_section(report: str) -> str:
    if "## Measured tier" in report:
        return _section(report, "## Measured tier")
    return _section(report, "## Per rail × engine headline")


# ---------------------------------------------------------------------------
# Finding 1 [P1]: payload JSON / Markdown omit all-numeric SOURCE accounting.
# ---------------------------------------------------------------------------


def test_r2_f1_every_writer_carries_the_same_all_numeric_source_rows(tmp_path) -> None:
    rows, experiments, bench = _demaria_shape_figure_rows()
    context = _demaria_context(rows, experiments, bench)

    def predict(engine, obs, **_kwargs):
        return _prediction(engine, obs, Decimal("1e-5"))

    out = _all_writers(context, (INTERNAL,), predict, tmp_path)
    expected_source = {
        "source_id": "kems-022-demaria-1971",
        "n_numeric": 30,
        "n_flagged": 30,
    }
    for writer, records in out["json"].items():
        vapour = _record(records, tier="all_numeric", rail=Rail.VAPOUR, engine=INTERNAL)
        assert vapour["n"] == 30, writer
        sources = vapour["sources"]
        assert len(sources) == 1, (writer, sources)
        assert {k: sources[0][k] for k in expected_source} == expected_source, writer
        assert sources[0]["n_certified"] == 0, writer
        assert sources[0]["flag_class_counts"].get("figure_only") == 30, writer
        certified = _record(records, tier="measured", rail=Rail.VAPOUR, engine=INTERNAL)
        assert certified["n"] == 0, writer
    # All three JSON writers consume one source aggregation: identical lists.
    typed_sources = _record(
        out["json"]["typed"], tier="all_numeric", rail=Rail.VAPOUR, engine=INTERNAL
    )["sources"]
    for writer in ("stream", "payload"):
        assert (
            _record(
                out["json"][writer], tier="all_numeric", rail=Rail.VAPOUR, engine=INTERNAL
            )["sources"]
            == typed_sources
        ), writer
    for writer, report in out["md"].items():
        assert "kems-022-demaria-1971(n=30,cert=0,flag=30)" in _a_section(report), writer


# ---------------------------------------------------------------------------
# Finding 2 [P1]: A gated by B's engine set; A n = 0 rows hidden.
# ---------------------------------------------------------------------------


def _two_engine_fixture():
    experiment = F.kems_experiment("two-engine-exp")
    unflagged = F.observation(
        "two-engine-unflagged",
        experiment.experiment_id,
        _partial_identity(formula="K", T_K="1500"),
        Decimal("1e-3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="two-engine-src",
    )
    flagged = replace(
        F.observation(
            "two-engine-flagged",
            experiment.experiment_id,
            _partial_identity(formula="K", T_K="1510"),
            Decimal("2e-3"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="two-engine-src",
        ),
        notices=(
            Notice(
                kind=NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG,
                affected_quantities=(Quantity.P_PARTIAL,),
                reason="catalogue composition fixture",
                origin="two-engine-flagged",
            ),
        ),
    )
    context = _context((unflagged, flagged), experiments=(experiment,))

    def predict(engine, obs, **_kwargs):
        notices = ()
        if engine is OPENIMCC:
            notices = (
                Notice(
                    kind=NoticeKind.IMCC_COMPLEX_SATURATION,
                    affected_quantities=(Quantity.P_PARTIAL,),
                    reason="imcc complex saturation fixture",
                    origin="test",
                ),
            )
        return _prediction(engine, obs, point_magnitude(obs.value) * 2, notices)

    return context, predict


def test_r2_f2_all_numeric_has_full_engine_rail_grid_and_prints_zero_rows(tmp_path) -> None:
    context, predict = _two_engine_fixture()
    engines = (INTERNAL, OPENIMCC)
    out = _all_writers(context, engines, predict, tmp_path)
    for writer, records in out["json"].items():
        a_internal = _record(records, tier="all_numeric", rail=Rail.VAPOUR, engine=INTERNAL)
        a_imcc = _record(records, tier="all_numeric", rail=Rail.VAPOUR, engine=OPENIMCC)
        b_internal = _record(records, tier="measured", rail=Rail.VAPOUR, engine=INTERNAL)
        b_imcc = _record(records, tier="measured", rail=Rail.VAPOUR, engine=OPENIMCC)
        assert (a_internal["n"], b_internal["n"]) == (2, 1), writer
        assert (a_imcc["n"], b_imcc["n"]) == (2, 0), writer
        for rail in Rail:
            for engine in engines:
                _record(records, tier="all_numeric", rail=rail, engine=engine)
    for writer, report in out["md"].items():
        a_lines = _a_section(report).splitlines()
        assert any(line.startswith("| vapour | openimcc | 2 |") for line in a_lines), writer
        assert any(
            line.startswith("| vapour | internal-analytical | 2 |") for line in a_lines
        ), writer
        # Both lines always printed: every A grid cell, zero rows included.
        for rail in Rail:
            for engine in engines:
                prefix = f"| {rail.value} | {engine.value} | "
                assert any(line.startswith(prefix) for line in a_lines), (
                    writer,
                    prefix,
                )
    # B's engine membership stays exactly as before: the typed/stream
    # renderers grid B by engines with measured rows only.
    for writer in ("typed", "stream"):
        b_lines = _b_section(out["md"][writer]).splitlines()
        assert any(
            line.startswith("| vapour | internal-analytical | 1 | 0 | 1 |") for line in b_lines
        ), writer
        assert not any("| openimcc |" in line for line in b_lines), writer
    # Certified membership inside A is B's own decision, per engine.
    for writer, records in out["json"].items():
        a_internal = _record(records, tier="all_numeric", rail=Rail.VAPOUR, engine=INTERNAL)
        a_imcc = _record(records, tier="all_numeric", rail=Rail.VAPOUR, engine=OPENIMCC)
        assert [s["n_certified"] for s in a_internal["sources"]] == [1], writer
        assert [s["n_certified"] for s in a_imcc["sources"]] == [0], writer


# ---------------------------------------------------------------------------
# Finding 3 [P1]: all-numeric statistics pool units and label them dex.
# ---------------------------------------------------------------------------


def _thermo_fixture():
    experiment = F.tabulation_experiment()
    pure = {
        "reaction": State.not_applicable("pure standard thermo"),
        "formation_elements": State.not_applicable("pure standard thermo"),
    }
    enthalpy = F.observation(
        "thermo-h",
        experiment.experiment_id,
        replace(
            F.o2_identity(T_K=Decimal("1000")),
            quantity=Quantity.H_MINUS_H298,
            subtype=State.of("298.15"),
            **pure,
        ),
        Decimal("10"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="calorimetry-fixture",
    )
    heat_capacity = F.observation(
        "thermo-cp",
        experiment.experiment_id,
        replace(F.o2_identity(T_K=Decimal("1000")), quantity=Quantity.CP, **pure),
        Decimal("30"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="calorimetry-fixture",
    )
    context = _context((enthalpy, heat_capacity), experiments=(experiment,))
    predicted = {"thermo-h": Decimal("11"), "thermo-cp": Decimal("130")}

    def predict(engine, obs, **_kwargs):
        return _prediction(engine, obs, predicted[obs.observation_id])

    return context, predict


def test_r2_f3_all_numeric_statistics_are_unit_stratified_never_dex_labelled(
    tmp_path,
) -> None:
    context, predict = _thermo_fixture()
    out = _all_writers(context, (INTERNAL,), predict, tmp_path)
    numeric = sorted(
        (r.numeric.unit, r.numeric.value)
        for r in out["residuals"]
        if r.numeric is not None
    )
    assert numeric == [
        ("J_per_declared_mol_basis_per_K", Decimal("100")),
        ("kJ_per_declared_mol_basis", Decimal("1")),
    ]
    for writer, records in out["json"].items():
        row = _record(records, tier="all_numeric", rail=Rail.THERMOCHEMISTRY, engine=INTERNAL)
        assert row["n"] == 2, writer
        # No dex statistic exists for two absolute residuals in unlike units.
        for key in ("median_dex", "iqr_dex", "rms_dex", "median_abs_dex"):
            assert row.get(key) is None, (writer, key, row.get(key))
        strata = {
            (s["quantity"], s["operation"], s["unit"]): s for s in row["metric_strata"]
        }
        assert set(strata) == {
            ("H_minus_H298", "absolute", "kJ_per_declared_mol_basis"),
            ("cp", "absolute", "J_per_declared_mol_basis_per_K"),
        }, writer
        h = strata[("H_minus_H298", "absolute", "kJ_per_declared_mol_basis")]
        cp = strata[("cp", "absolute", "J_per_declared_mol_basis_per_K")]
        assert (h["n"], Decimal(h["median"])) == (1, Decimal("1")), writer
        assert (cp["n"], Decimal(cp["median"])) == (1, Decimal("100")), writer
        assert all("dex" not in str(s["label"]) for s in row["metric_strata"]), writer
        # B (certified) keeps its DEX-only schema unchanged.
        certified = _record(records, tier="measured", rail=Rail.THERMOCHEMISTRY, engine=INTERNAL)
        assert certified["n"] == 2 and certified["median_dex"] is None, writer
    for writer, report in out["md"].items():
        assert "50.5" not in _a_section(report), writer


# ---------------------------------------------------------------------------
# Finding 4 [P2]: figure reading band lost on residue element-ppm DEX metric.
# ---------------------------------------------------------------------------


def test_r2_f4_residue_element_ppm_figure_band_follows_identity_metric(tmp_path) -> None:
    experiment = F.tabulation_experiment()
    base = F.wall_deposit_identity()
    not_residue = "not a residue identity axis"
    identity = replace(
        base,
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        species=Species("Mn", Phase.L),
        subtype=State.of("element_ppm_by_mass"),
        per=State.not_applicable(not_residue),
        reservoir=State.not_applicable(not_residue),
        sweep_gas=State.not_applicable(not_residue),
        wall=State.not_applicable(not_residue),
        fO2_Pa=State.not_applicable("vacuum run"),
    )
    figure = replace(
        F.observation(
            "residue-element-ppm-figure",
            experiment.experiment_id,
            identity,
            Decimal("5"),
            evidence=EvidenceClass.FIGURE_ONLY,
            admission=AdmissionStatus.ADMITTED,
            source_id="residue-figure-fixture",
        ),
        uncertainty=Uncertainty(kind=UncertaintyKind.PRINTED, verbatim={"dex": "0.3"}),
    )
    context = _context((figure,), experiments=(experiment,))

    def predict(engine, obs, **_kwargs):
        return _prediction(engine, obs, Decimal("10"))

    out = _all_writers(context, (INTERNAL,), predict, tmp_path)
    [residual] = [r for r in out["residuals"] if r.numeric is not None]
    assert residual.rail is Rail.RESIDUE_COMPOSITION
    assert residual.numeric.operation is MetricOperation.DEX
    assert residual.numeric.value == Decimal("0.3010299956639812")
    assert residual.numeric.decision_band is not None
    assert residual.numeric.decision_band.value == Decimal("0.3")
    assert residual.numeric.decision_band.unit == "dimensionless"
    assert residual.status is not ResidualStatus.NO_BAND
    assert residual.score_eligible is False
    assert any(n.kind is NoticeKind.FIGURE_ONLY for n in residual.notices)


# ---------------------------------------------------------------------------
# Finding 6 [P1]: explicit predict=predict_with_engine drops reactive label.
# ---------------------------------------------------------------------------


def _reactive_cell_fixture(monkeypatch):
    from tests.battery import test_silent_fills as sf

    base, experiment = sf._kems_partial(cell_material=None, fO2_Pa=Decimal("1e-4"))
    identity = replace(
        _partial_identity(formula="K", T_K="1500"),
        composition=base.identity.composition,
        fO2_Pa=State.of(Decimal("1e-4")),
    )
    observation = F.observation(
        "re-cell-measured",
        experiment.experiment_id,
        identity,
        Decimal("1e-3"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="re-cell-fixture",
    )
    bench = sf._cell_material_bench((CellMaterial.RE,))
    experiment = replace(experiment, bench_id=bench.id)
    reference = point_magnitude(observation.value)

    def _open(name: str) -> object:
        return types.SimpleNamespace(
            name=name, available=True, unavailable_reason=None, supports_intrinsic_fo2=False
        )

    def _cell(_handle, _pot, *, po2, physical_pressure_bar=None, **_kwargs):
        # Controlled successful engine output equal to the reference.
        return types.SimpleNamespace(
            status="ok",
            refusal_reason=None,
            hostname="test",
            exit_code=0,
            exit_signal=None,
            notices=[],
            engine_reason=None,
            melt_activities={},
            gas_partial_pressures_Pa={"K": float(reference)},
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine", _open
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell", _cell
    )
    return _context((observation,), experiments=(experiment,), benches={bench.id: bench})


@pytest.mark.parametrize("explicit", (False, True), ids=("implicit", "explicit"))
def test_r2_f6_reactive_label_follows_the_bench_not_the_call_style(
    monkeypatch, explicit: bool
) -> None:
    context = _reactive_cell_fixture(monkeypatch)
    kwargs = {"predict": predict_with_engine} if explicit else {}
    residuals, _ = score_store(
        context, engines=(OPENIMCC,), include_diagnostics=True, **kwargs
    )
    [residual] = residuals
    assert residual.numeric is not None
    assert any(
        n.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED for n in residual.notices
    ), [n.kind for n in residual.notices]
    certified = _record(
        headline_rows(residuals, context=context, tier="measured", engines=(OPENIMCC,)),
        tier="measured",
        rail=Rail.VAPOUR,
        engine=OPENIMCC,
    )
    assert certified["n"] == 0


def test_r2_f6_custom_predictor_keyword_contract_is_unchanged() -> None:
    """A caller-supplied predictor still receives only handles/experiment."""

    rows, experiments, bench = _demaria_shape_figure_rows()
    context = _demaria_context(rows[:1], experiments, bench)
    seen: list[set[str]] = []

    def custom(engine, obs, *, handles=None, experiment=None):
        seen.append({"handles", "experiment"})
        return _prediction(engine, obs, Decimal("1e-5"))

    residuals, _ = score_store(
        context, engines=(INTERNAL,), include_diagnostics=True, predict=custom
    )
    assert seen and all(r.numeric is not None for r in residuals)


# ---------------------------------------------------------------------------
# Finding 5 [P2] (d-062) behaviour pin, committed before the one-owner move:
# the oxygen-input decision (commanded / missing_fO2 / omitted) must be the
# same policy on the unmodelled-reactive Knudsen branch and the general
# branch; only the reactive-cell label differs.
# ---------------------------------------------------------------------------


def _oxygen_outcome(prediction, seen) -> tuple[object, ...]:
    return (
        None if prediction.refusal_reason is None else prediction.refusal_reason.value,
        dict(prediction.refusal_detail or {}),
        seen.get("mode"),
        seen.get("po2_bar"),
        tuple(
            sorted(
                n.reason
                for n in prediction.notices
                if n.kind is not NoticeKind.REACTIVE_CELL_NOT_MODELLED
            )
        ),
    )


@pytest.mark.parametrize(
    "case", ("p_partial_no_o2", "p_partial_printed_o2", "p_sat_pure_reservoir")
)
def test_r2_f5_oxygen_input_decision_is_one_policy_on_both_cell_branches(
    monkeypatch, case: str
) -> None:
    from tests.battery import test_silent_fills as sf

    seen = sf._capture_cell(monkeypatch)
    if case == "p_sat_pure_reservoir":
        observation = F.observation("na-psat", "kems-1", F.psat_identity("Na"), Decimal("1"))
        kems = F.kems_experiment()
    else:
        observation, kems = sf._kems_partial(
            cell_material=None,
            fO2_Pa=None if case == "p_partial_no_o2" else Decimal("1e-4"),
        )
    reactive = sf._cell_material_bench((CellMaterial.RE,))
    seen.clear()
    on_reactive_knudsen = predict_with_engine(
        OPENIMCC, observation, experiment=kems, bench=reactive, isolated=False
    )
    reactive_outcome = _oxygen_outcome(on_reactive_knudsen, seen)
    seen.clear()
    general = predict_with_engine(
        OPENIMCC, observation, experiment=F.tabulation_experiment(), isolated=False
    )
    general_outcome = _oxygen_outcome(general, seen)
    assert reactive_outcome == general_outcome
    assert any(
        n.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED for n in on_reactive_knudsen.notices
    )
    assert not any(n.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED for n in general.notices)
    expected = {
        "p_partial_no_o2": ("identity_incomplete", "missing_fO2", None),
        "p_partial_printed_o2": ("unsupported", None, "commanded"),
        "p_sat_pure_reservoir": ("unsupported", None, "not_an_input"),
    }[case]
    assert (
        reactive_outcome[0],
        reactive_outcome[1].get("reason"),
        reactive_outcome[2],
    ) == expected


def test_r2_f4_guard_borrowed_envelope_never_attaches_to_residue_dex_rows() -> None:
    """B guard for the F4 fix: only a row's OWN band uses its identity metric.

    score_store hands every row the KEMS p_partial pressure envelope as
    derived_band. On a measured residue element-ppm (DEX) row that envelope
    must stay rejected by the quantity-default dimension check, exactly as
    at green 8089eadbf (Sossi 2019 rows stay NO_BAND, B unchanged). Only the
    row's own printed reading band (band_operation set) may attach.
    """

    from simulator.battery.enums import SourceRelation
    from simulator.battery.records import DecisionBand
    from simulator.battery.score import populate_numeric

    envelope = DecisionBand(
        Decimal("0.146"), "dimensionless", "KEMS p_partial measured uncertainty"
    )
    common = dict(
        quantity=Quantity.RESIDUE_COMPONENT_COMPOSITION,
        candidate=Decimal("10"),
        reference=Decimal("5"),
        source_relation=SourceRelation.INDEPENDENT,
        rail=Rail.RESIDUE_COMPOSITION,
        observations={},
        experiments={},
        derived_band=envelope,
        operation_override=MetricOperation.DEX,
    )
    borrowed, _reason, _detail = populate_numeric(**common)
    assert borrowed is not None
    assert borrowed.operation is MetricOperation.DEX
    assert borrowed.decision_band is None
    own, _reason, _detail = populate_numeric(
        **common, band_operation=MetricOperation.DEX
    )
    assert own is not None
    assert own.decision_band == envelope
