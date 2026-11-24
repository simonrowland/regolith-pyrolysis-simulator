"""Shared-ladder and intrinsic redox modes (d-180/d-182, t-1352)."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    Authority,
    CellMaterial,
    Engine,
    EvidenceClass,
    ExecutionState,
    FO2Channel,
    Fo2Mode,
    MethodToken,
    NoticeKind,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    RefusalReason,
    ValueKind,
)
from simulator.battery.fo2_ladder import (
    ResolvedFo2,
    resolve_row_fo2,
)
from simulator.battery.records import (
    ApproxPressure,
    BandBasis,
    FO2Control,
    State,
    Value,
)
from simulator.battery.score import (
    FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND,
    ScoreContext,
    _cell_oxygen_class,
    composition_wt_pct,
    compile_residual,
    flagged_strata,
    load_score_context,
    _predict_with_engine,
    predict_with_engine,
    residual_to_plain,
    score_store,
)
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    Waypoint,
    WaypointAuthority,
    WaypointResult,
    oxygen_condition,
)
from simulator.fe_redox import (
    intrinsic_melt_fO2,
    kress91_log10_fO2_from_fe3_over_sigma_fe,
    melt_mol_fractions_for_kress91,
)
from tests.battery import factories as F
from tests.battery.test_melt_activity_requirements import _case, _composition, _melt

_COMPARABLE = (
    "alphamelts",
    "thermoengine",
    "openimcc",
    "internal-analytical",
)


def _fo2_input(resolved):
    if resolved is None:
        return None
    return (
        resolved.route,
        resolved.tier,
        resolved.physics_rung,
        resolved.po2_bar,
        resolved.closure,
        resolved.cell_material,
        None if resolved.closure else resolved.log10_fO2_bar,
    )


def test_fo2_ladder_and_migrate_share_redox_fields_in_fresh_interpreter() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import simulator.battery.fo2_ladder as ladder; "
            "import simulator.battery.migrate as migrate; "
            "from simulator.battery.redox_fields import "
            "FE3_FE2_RATIO_PRINTED_FIELDS, FERRIC_FRACTION_FIELDS; "
            "assert ladder.FE3_FE2_RATIO_PRINTED_FIELDS is "
            "migrate.FE3_FE2_RATIO_PRINTED_FIELDS is "
            "FE3_FE2_RATIO_PRINTED_FIELDS; "
            "assert ladder.FERRIC_FRACTION_FIELDS is "
            "migrate.FERRIC_FRACTION_FIELDS is FERRIC_FRACTION_FIELDS",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr


def _assert_same_fo2(results):
    ready = [item for item in results if item.readiness.engine in _COMPARABLE]
    assert {item.readiness.engine for item in ready} == set(_COMPARABLE)
    assert all(item.readiness.status is ReadinessStatus.READY for item in ready)
    assert all(item.payload is not None for item in ready)
    assert len({item.payload["fO2_log"] for item in ready}) == 1
    assert len({item.payload["fO2_tier"] for item in ready}) == 1
    assert len({item.payload["fO2_route"] for item in ready}) == 1
    assert len({item.payload["fO2_borrowed"] for item in ready}) == 1
    return ready[0].payload


def test_current_composition_and_cell_helpers_are_pinned_before_move() -> None:
    weights = composition_wt_pct(
        _composition(("FeO", "0.2"), ("SiO2", "0.8"))
    )
    assert weights is not None
    assert {name: amount.hex() for name, amount in weights.items()} == {
        "FeO": "0x1.70391a0b77b38p+4",
        "SiO2": "0x1.33f1b97d22132p+6",
    }
    assert _cell_oxygen_class(None) == ("unknown", None)
    assert _cell_oxygen_class((F.located(CellMaterial.PT),)) == ("inert", None)
    assert _cell_oxygen_class((F.located(CellMaterial.MO),)) == (
        "reactive",
        "Mo",
    )
    assert _cell_oxygen_class(
        (F.located(CellMaterial.MO), F.located(CellMaterial.W))
    ) == ("reactive", None)


def _feo_case(**kwargs):
    return _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
        **kwargs,
    )


def test_comparison_path_shares_the_effusion_closure_condition() -> None:
    """Tier-3 effusion shares the closure, not a private number per engine."""

    source = inspect.getsource(_predict_with_engine)
    assert "PO2_OXYGEN_BALANCE_EFFUSION" in source
    assert "_borrow_internal_analytical_effusion" in source


def test_tier1_printed_log_is_bit_identical_on_every_engine() -> None:
    experiment, bench, observation = _feo_case(oxygen=Decimal("-9"))
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    selected = oxygen_condition(experiment, bench, observation).selected
    assert selected is not None
    assert selected.route == "observation_fO2_log"
    assert payload["fO2_log"] == float(selected.value.point)
    assert payload["fO2_log"] == float(Decimal("-9"))
    assert payload["fO2_tier"] == 1
    assert payload["fO2_borrowed"] is False


def test_scoring_modes_keep_distinct_keys_notices_and_store_outputs() -> None:
    experiment, bench, observation = _feo_case(
        oxygen=Decimal("-9"),
        pressure=Value.point_of(Decimal("0.001")),
    )
    observation = replace(
        observation,
        source_id=experiment.work_id,
        evidence=replace(
            observation.evidence,
            class_=State.of(EvidenceClass.MEASURED_DIRECT),
        ),
        identity=replace(
            observation.identity,
            per=State.of(PerBasis.DIMENSIONLESS),
        ),
    )
    context = ScoreContext(
        works={experiment.work_id: F.work(experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
        extract_review={experiment.work_id: "reviewed"},
        hostname="test",
        benches={bench.id: bench},
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )
    assert prediction.value is not None, prediction.refusal_detail
    shared, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    intrinsic, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        fo2_mode=Fo2Mode.ENGINE_INTRINSIC,
    )

    assert shared.numeric is not None, shared.refusal
    assert intrinsic.numeric is not None, intrinsic.refusal
    assert shared.fo2_mode is Fo2Mode.SHARED_LADDER
    assert intrinsic.fo2_mode is Fo2Mode.ENGINE_INTRINSIC
    assert shared.key != intrinsic.key
    assert any(
        notice.fo2_mode is Fo2Mode.SHARED_LADDER for notice in shared.notices
    )
    assert any(
        notice.fo2_mode is Fo2Mode.ENGINE_INTRINSIC
        for notice in intrinsic.notices
    )
    assert residual_to_plain(shared)["fo2_mode"] == "shared_ladder"
    assert residual_to_plain(intrinsic)["fo2_mode"] == "engine_intrinsic"
    assert any(
        notice.get("fo2_mode") == "shared_ladder"
        for notice in residual_to_plain(shared)["notices"]
    )
    assert any(
        notice.get("fo2_mode") == "engine_intrinsic"
        for notice in residual_to_plain(intrinsic)["notices"]
    )

    scored, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.MELT_ACTIVITY,
    )
    paired = [
        item for item in scored if item.reference == observation.observation_id
    ]
    assert {item.fo2_mode for item in paired} == {
        Fo2Mode.ENGINE_INTRINSIC,
        Fo2Mode.SHARED_LADDER,
    }


def test_tier1_printed_identity_pascal_stays_pa_over_1e5() -> None:
    experiment, bench, observation = _feo_case()
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            fO2_Pa=State.of(Decimal("1e-3")),
        ),
    )
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    bar = float(Decimal("1e-3")) / 1.0e5
    assert payload["fO2_tier"] == 1
    assert payload["fO2_route"] == "identity_fO2_Pa"
    assert payload["fO2_bar"] == bar
    assert payload["fO2_borrowed"] is False
    resolved = resolve_row_fo2(
        experiment, bench, observation, oxygen=oxygen_condition(experiment, bench, observation),
    )
    assert resolved is not None
    assert resolved.po2_bar == bar


def test_tier2_printed_ferric_fraction_is_the_kress_inverse() -> None:
    experiment, bench, observation = _feo_case()
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    assert payload["fO2_tier"] == 2
    assert payload["fO2_borrowed"] is False
    assert payload["fO2_route"].startswith("kress_inverse:Fe3_over_sum_Fe;")
    assert "kress_reference_1_bar" in payload["fO2_route"]
    composition = observation.identity.composition.value
    weights = composition_wt_pct(composition)
    expected = kress91_log10_fO2_from_fe3_over_sigma_fe(
        fe3_over_sigma_fe=0.2,
        mol_fractions=melt_mol_fractions_for_kress91(weights),
        T_K=1473.15,
        pressure_bar=1.0,
    )
    assert payload["fO2_log"] == expected
    resolved = resolve_row_fo2(experiment, bench, observation)
    changed = resolve_row_fo2(
        experiment,
        bench,
        replace(observation, value=Value.point_of(observation.value.point * Decimal("1.7"))),
    )
    assert _fo2_input(changed) == _fo2_input(resolved)


def test_tier2_kress_inverse_uses_banded_identity_nominal_pressure() -> None:
    experiment, bench, observation = _feo_case()
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    exact_identity = replace(
        observation.identity,
        total_pressure_Pa=State.of(Decimal("1000000")),
    )
    exact = resolve_row_fo2(
        experiment,
        bench,
        replace(observation, identity=exact_identity),
    )
    banded_identity = replace(
        exact_identity,
        total_pressure_Pa=State.of(
            ApproxPressure(
                Decimal("1000000"),
                Decimal("900000"),
                Decimal("1100000"),
                BandBasis.PRINTED,
            )
        ),
    )
    banded = resolve_row_fo2(
        experiment,
        bench,
        replace(observation, identity=banded_identity),
    )

    assert exact is not None and banded is not None
    assert exact.route.startswith("kress_inverse:Fe3_over_sum_Fe;")
    assert banded.route == exact.route
    assert banded.log10_fO2_bar == exact.log10_fO2_bar


def test_tier3_buffer_matches_the_oxygen_condition_point() -> None:
    experiment, bench, observation = _feo_case(
        temperature=Decimal("1200"),
        pressure=Value.point_of(Decimal("100000")),
    )
    experiment = replace(
        experiment,
        method=F.State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
        fO2_control=FO2Control(
            channel=F.State.of(FO2Channel.BUFFER),
            buffer=F.located("IW"),
        ),
    )
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    selected = oxygen_condition(experiment, bench, observation).selected
    assert selected is not None
    assert selected.route == "buffer_relation"
    assert payload["fO2_tier"] == 3
    assert payload["fO2_route"] == "buffer_relation"
    assert payload["fO2_borrowed"] is False
    assert payload["fO2_log"] == float(selected.value.point)


def test_basu_metal_saturation_activity_targets_are_refused() -> None:
    from pathlib import Path

    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.fo2_ladder import metal_saturation_activity_refusal
    from simulator.battery.generators.bench import melt_activity_requests
    from simulator.battery.migrate import Migrator

    root = Path(__file__).resolve().parents[2]
    migrator = Migrator(root=root, index={}, aliases={})
    migrator._migrate_extract(
        root / "data/literature/extracts/basu-2008-feo-steelmaking.yaml"
    )
    migrator.finalize()
    activity_targets = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "basu-2008-feo-steelmaking"
        and observation.identity.species.formula.strip().casefold() == "feo"
        and observation.identity.species.phase is not None
        and observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.L
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(activity_targets) == 108

    by_id = migrator.result.observations
    for observation in activity_targets:
        experiment = migrator.result.experiments[observation.experiment_id]
        bench = migrator.result.benches[experiment.bench_id]
        refusal_notice = metal_saturation_activity_refusal(
            experiment, observation
        )
        assert refusal_notice is not None
        assert refusal_notice.kind is NoticeKind.INPUT_OMITTED
        assert "d-305" in refusal_notice.reason
        assert "target cannot supply its own fO2" in refusal_notice.reason

        changed = replace(
            observation, value=Value.point_of(observation.value.point * Decimal("1.7"))
        )
        assert resolve_row_fo2(experiment, bench, observation) is None
        assert resolve_row_fo2(experiment, bench, changed) is None

    # Exercise engine readiness in both modes on incomplete and closed
    # compositions; every extracted row above shares the same source form.
    for observation_id in (
        "basu-2008-feo-steelmaking::basu_2008_tii_s01_a_feo_1873k",
        "basu-2008-feo-steelmaking::basu_2008_tii_s02_a_feo_1873k",
    ):
        observation = by_id[observation_id]
        experiment = migrator.result.experiments[observation.experiment_id]
        bench = migrator.result.benches[experiment.bench_id]
        inputs = collect_consumer_inputs(experiment, bench, observation)
        for fo2_mode in (Fo2Mode.SHARED_LADDER, Fo2Mode.ENGINE_INTRINSIC):
            requests = melt_activity_requests(
                inputs,
                experiment=experiment,
                bench=bench,
                observation=observation,
                fo2_mode=fo2_mode,
            )
            blocked = [
                item for item in requests
                if item.readiness.engine in _COMPARABLE
            ]
            assert {item.readiness.engine for item in blocked} == set(_COMPARABLE)
            assert all(item.payload is None for item in blocked)
            applicable = [
                item
                for item in blocked
                if item.readiness.status is not ReadinessStatus.NOT_APPLICABLE
            ]
            assert applicable
            assert all(
                any(
                    gap.waypoint == "metal_saturation_condition"
                    and gap.reason is GapReason.MISSING_EVIDENCE
                    and any("d-305" in detail for detail in gap.missing)
                    for gap in item.readiness.gaps
                )
                for item in applicable
            )

        for fo2_mode in (Fo2Mode.SHARED_LADDER, Fo2Mode.ENGINE_INTRINSIC):
            prediction = predict_with_engine(
                Engine.INTERNAL_ANALYTICAL,
                observation,
                experiment=experiment,
                bench=bench,
                isolated=False,
                fo2_mode=fo2_mode,
            )
            assert prediction.value is None
            assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
            assert "d-305" in str(prediction.refusal_detail)


def test_plante_target_derived_oxygen_is_not_a_fixed_condition() -> None:
    from simulator.battery.fo2_ladder import derived_fo2_condition

    context = load_score_context(sources=("kems-042-plante-1979",))
    observation = context.observations[
        "kems-042-plante-1979::plante1979_table2_k2o_s1104_000_1302K"
    ]
    experiment = context.experiments[observation.experiment_id]
    bench = context.benches[experiment.bench_id]
    assert derived_fo2_condition(observation)

    selected = resolve_row_fo2(
        experiment, bench, observation, quantity=Quantity.P_PARTIAL
    )
    changed = resolve_row_fo2(
        experiment,
        bench,
        replace(
            observation,
            value=Value.point_of(observation.value.point * Decimal("1.7")),
        ),
        quantity=Quantity.P_PARTIAL,
    )
    assert selected is not None and selected.closure
    assert selected.po2_bar is None
    assert _fo2_input(changed) == _fo2_input(selected)


def test_steurer_effusion_row_has_a_numeric_internal_residual() -> None:
    context = load_score_context(
        sources=("steurer-1985-vapor-phase-pyrolysis",)
    )
    observation = context.observations[
        "steurer-1985-vapor-phase-pyrolysis::steurer_1985_table1_sio_psat::T=3000.0:row=sio(g):h=377f06ee0c71"
    ]
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
    )
    assert residual.numeric is not None, residual.refusal
    # OpenIMCC 9c327c2 / d-436 adds AlSiO and Si2O2 to the shared oxygen balance;
    # the prior internal-analytical residual pin was 0.10508901884588326.
    assert float(residual.numeric.value) == pytest.approx(
        0.09892713497480812, abs=1e-12
    )
    assert any(
        notice.origin == "fo2_ladder"
        and "route=effusion_oxygen_balance" in notice.reason
        for notice in residual.notices
    )


def test_magemin_refuses_knudsen_sio_for_missing_vapour_observable() -> None:
    context = load_score_context(
        sources=("steurer-1985-vapor-phase-pyrolysis",)
    )
    observation = context.observations[
        "steurer-1985-vapor-phase-pyrolysis::steurer_1985_table1_sio_psat::T=3000.0:row=sio(g):h=377f06ee0c71"
    ]
    prediction = predict_with_engine(
        Engine.MAGEMIN,
        observation,
        experiment=context.experiments[observation.experiment_id],
    )
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.execution.state is ExecutionState.UNSUPPORTED
    assert prediction.refusal_detail == {
        "reason": "engine_missing_observable",
        "engine": "magemin",
        "observable": "vapour species",
        "species": "SiO",
        "quantity": "p_partial",
    }

    residual, _candidate = compile_residual(
        observation,
        Engine.MAGEMIN,
        context=context,
        prediction=prediction,
    )
    assert residual.numeric is None
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNSUPPORTED
    assert residual.refusal.detail["observable"] == "vapour species"


def test_tier4_borrowed_intrinsic_is_the_same_on_every_engine() -> None:
    experiment, bench, observation = _feo_case()
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    weights = composition_wt_pct(observation.identity.composition.value)
    assert payload["fO2_log"] == float(intrinsic_melt_fO2(weights, 1473.15))
    assert payload["fO2_tier"] == 4
    assert payload["fO2_route"] == "intrinsic_melt_fO2"
    assert payload["fO2_borrowed"] is True
    resolved = resolve_row_fo2(experiment, bench, observation)
    changed = resolve_row_fo2(
        experiment,
        bench,
        replace(observation, value=Value.point_of(observation.value.point * Decimal("1.7"))),
    )
    assert _fo2_input(changed) == _fo2_input(resolved)


def test_unresolved_tier_refuses_every_engine_alike() -> None:
    experiment, bench, observation = _feo_case(
        temperature=Value(
            ValueKind.INTERVAL,
            interval_low=Decimal("1400"),
            interval_high=Decimal("1600"),
        ),
    )
    results = _melt(experiment, bench, observation)
    comparable = [item for item in results if item.readiness.engine in _COMPARABLE]
    assert {item.readiness.engine for item in comparable} == set(_COMPARABLE)
    assert all(item.payload is None for item in comparable)
    assert all(item.readiness.status is ReadinessStatus.GAP for item in comparable)
    signatures = [
        tuple((gap.waypoint, gap.reason, gap.missing) for gap in item.readiness.gaps)
        for item in comparable
    ]
    assert len(set(signatures)) == 1
    names = {gap.waypoint for gap in comparable[0].readiness.gaps}
    assert "oxygen_condition" in names
    assert "temperature_K" in names


def test_non_multivalent_row_without_printed_fo2_omits_oxygen() -> None:
    experiment, bench, observation = _case(
        composition=_composition(("Na2O", "0.4"), ("SiO2", "0.6")),
        formula="SiO2",
    )
    results = _melt(experiment, bench, observation)
    ready = [item for item in results if item.readiness.engine in _COMPARABLE]
    assert all(item.readiness.status is ReadinessStatus.READY for item in ready)
    assert all("fO2_log" not in item.payload for item in ready)


def test_previously_refused_engines_now_predict(monkeypatch) -> None:
    seen: dict[str, float] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, **_kwargs):
        seen[str(handle.name)] = po2.po2_bar
        return type("Cell", (), {
            "status": "ok",
            "refusal_reason": None,
            "melt_activities": {"SiO2": 0.2, "SiO2_Liq": 0.2},
            "melt_activity_coefficients": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "authority": None,
            "engine_reason": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    experiment, bench, observation = _feo_case()
    predictions = []
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.OPENIMCC):
        predictions.append(predict_with_engine(
            engine, observation, experiment=experiment, bench=bench, isolated=False,
        ))
    assert set(seen) == {"alphamelts", "thermoengine", "openimcc"}
    assert len(set(seen.values())) == 1
    assert next(iter(seen.values())) is not None
    assert all(item.value is not None for item in predictions)
    assert all(item.refusal_reason is None for item in predictions)
    assert all(
        any(
            notice.policy is not None
            and notice.policy.id == "DP-010"
            and "fo2_tier=4" in notice.reason
            and "borrowed" in notice.reason
            for notice in item.notices
        )
        for item in predictions
    )
    internal = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )
    assert internal.value is not None
    assert internal.refusal_reason is None
    assert any(
        notice.policy is not None
        and notice.policy.id == "DP-010"
        and notice.reason.startswith("DP-010 physics_rung=4:")
        and "commanded_default_log10_bar=" in notice.reason
        and "original_missing_evidence=" in notice.reason
        and "fo2_tier=4" in notice.reason
        and "borrowed" in notice.reason
        for notice in internal.notices
    )


def test_tier_notice_reaches_the_residual() -> None:
    experiment, bench, observation = _feo_case(
        pressure=Value.point_of(Decimal("1e-6")),
    )
    experiment = replace(
        experiment,
        method=F.State.of(MethodToken.TABULATION),
        bench_id=bench.id,
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )
    assert prediction.value is not None, prediction.refusal_detail
    context = ScoreContext(
        works={experiment.work_id: F.work(experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
        extract_review={observation.source_id or "": "reviewed", "": "reviewed"},
        hostname="test",
        benches={bench.id: bench},
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )
    assert any(
        notice.policy is not None
        and notice.policy.id == "DP-010"
        and notice.reason.startswith("DP-010 physics_rung=4:")
        and "commanded_default_log10_bar=" in notice.reason
        and "original_missing_evidence=" in notice.reason
        and "fo2_tier=4" in notice.reason
        and "borrowed" in notice.reason
        for notice in residual.notices
    )


def test_closable_knudsen_row_is_one_effusion_closure() -> None:
    from simulator.battery.enums import CellMaterial, Quantity
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    observation, experiment = _kems_partial(cell_material=None)
    bench = _cell_material_bench((CellMaterial.PT,))
    resolved = resolve_row_fo2(
        experiment,
        bench,
        observation,
        composition=observation.identity.composition.value,
        temperature_K=float(observation.identity.temperature_K.value),
        quantity=Quantity.P_PARTIAL,
    )
    assert resolved is not None
    assert resolved.closure is True
    assert resolved.tier == 3
    assert resolved.route == "effusion_oxygen_balance"
    assert resolved.borrowed is False
    assert resolved.po2_bar is None
    assert resolved.cell_material is None


def test_unmodelled_knudsen_row_uses_melt_closure_not_intrinsic_fo2() -> None:
    from simulator.battery.enums import CellMaterial, Quantity
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    observation, experiment = _kems_partial(cell_material=None)
    resolved = resolve_row_fo2(
        experiment,
        _cell_material_bench((CellMaterial.RE,)),
        observation,
        composition=observation.identity.composition.value,
        temperature_K=float(observation.identity.temperature_K.value),
        quantity=Quantity.P_PARTIAL,
    )
    assert resolved is not None
    assert resolved.closure
    assert resolved.route == "effusion_oxygen_balance"
    assert resolved.cell_material is None


def test_knudsen_source_oxygen_follows_printed_controls_then_balance() -> None:
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    observation, experiment = _kems_partial(cell_material=None)
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(Decimal("1200")),
        ),
        point_conditions={
            **(observation.point_conditions or {}),
            "temperature_K": F.located(Decimal("1200")),
            "total_pressure_Pa": F.located(Value.point_of("100000")),
        },
    )
    bench = _cell_material_bench((CellMaterial.PT,))
    composition = observation.identity.composition.value
    linked_bar = 1.0e-9  # a same-cell measured O/O2 condition

    printed_pa = Decimal("1e-8")
    printed_row = replace(
        observation,
        identity=replace(
            observation.identity,
            fO2_Pa=State.of(printed_pa),
        ),
    )
    printed = resolve_row_fo2(
        experiment,
        bench,
        printed_row,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    assert printed is not None
    assert printed.route == "identity_fO2_Pa"
    assert printed.po2_bar == float(printed_pa / Decimal("100000"))
    changed_target = replace(
        observation, value=Value.point_of(observation.value.point * Decimal("1.7"))
    )
    changed_printed = resolve_row_fo2(
        experiment,
        bench,
        replace(changed_target, identity=printed_row.identity),
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    assert _fo2_input(changed_printed) == _fo2_input(printed)

    buffer_experiment = replace(
        experiment,
        conditions={
            **experiment.conditions,
            "temperature_K": F.located(Decimal("1200")),
        },
        fO2_control=FO2Control(
            channel=State.of(FO2Channel.BUFFER),
            buffer=F.located("IW"),
        ),
    )
    buffer = resolve_row_fo2(
        buffer_experiment,
        bench,
        observation,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    selected = oxygen_condition(buffer_experiment, bench, observation).selected
    assert selected is not None
    assert buffer is not None
    assert buffer.route == selected.route == "buffer_relation"
    assert buffer.log10_fO2_bar == float(selected.value.point)
    changed_buffer = resolve_row_fo2(
        buffer_experiment,
        bench,
        changed_target,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    assert _fo2_input(changed_buffer) == _fo2_input(buffer)

    linked = resolve_row_fo2(
        experiment,
        bench,
        observation,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    assert linked is not None
    assert linked.route == "source_linked_oxygen_condition"
    assert linked.po2_bar == linked_bar
    changed_linked = resolve_row_fo2(
        experiment,
        bench,
        changed_target,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=linked_bar,
    )
    assert _fo2_input(changed_linked) == _fo2_input(linked)

    balanced = resolve_row_fo2(
        experiment,
        bench,
        observation,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
    )
    assert balanced is not None and balanced.closure
    assert balanced.route == "effusion_oxygen_balance"
    changed_balanced = resolve_row_fo2(
        experiment,
        bench,
        changed_target,
        composition=composition,
        temperature_K=1200.0,
        quantity=Quantity.P_PARTIAL,
    )
    assert _fo2_input(changed_balanced) == _fo2_input(balanced)


@pytest.mark.parametrize("missing", ("composition", "temperature"))
def test_knudsen_missing_balance_input_refuses_before_vacuum_bound(missing) -> None:
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    observation, experiment = _kems_partial(cell_material=None)
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(Decimal("1200")),
        ),
    )
    bench = _cell_material_bench((CellMaterial.PT,))
    vacuum = Waypoint(
        name="oxygen_condition",
        value=Value.point_of("-9"),
        route="vacuum_total_pressure_upper_bound",
        authority=WaypointAuthority.EXTRAPOLATED,
        inputs=("printed run vacuum pressure", "Dalton: pO2 <= P_total"),
        notice="upper bound from printed vacuum 1e-4 Pa; bound, not a measurement",
    )
    oxygen = WaypointResult("oxygen_condition", vacuum, (vacuum,))
    composition = observation.identity.composition.value
    temperature = 1200.0
    if missing == "composition":
        observation = replace(
            observation,
            identity=replace(
                observation.identity,
                composition=State.unknown("not printed"),
            ),
        )
        composition = None
    else:
        temperature = None

    resolved = resolve_row_fo2(
        experiment,
        bench,
        observation,
        composition=composition,
        temperature_K=temperature,
        oxygen=oxygen,
        quantity=Quantity.P_PARTIAL,
    )
    assert resolved is None
    changed_target = replace(
        observation, value=Value.point_of(observation.value.point * Decimal("1.7"))
    )
    changed = resolve_row_fo2(
        experiment,
        bench,
        changed_target,
        composition=composition,
        temperature_K=temperature,
        oxygen=oxygen,
        quantity=Quantity.P_PARTIAL,
    )
    assert _fo2_input(changed) == _fo2_input(resolved)


def test_supported_engines_receive_the_same_effusion_condition(monkeypatch) -> None:
    from simulator.battery.enums import CellMaterial
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    seen: dict[str, tuple[object, object, object]] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, **_kwargs):
        seen[str(handle.name)] = (
            po2.mode,
            po2.po2_bar,
            po2.cell_material,
        )
        return type("Cell", (), {
            "status": "refusal",
            "refusal_reason": "census_stop",
            "melt_activities": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "engine_reason": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    observation, experiment = _kems_partial(cell_material=None)
    observation = replace(
        observation,
        source_id="rys-2007-alkali-oxide-thermodynamics",
    )
    bench = _cell_material_bench((CellMaterial.MO,))

    def engine_conditions(target):
        seen.clear()
        for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
            predict_with_engine(
                engine,
                target,
                experiment=experiment,
                bench=bench,
                isolated=False,
            )
        return dict(seen)

    original = engine_conditions(observation)
    changed = engine_conditions(
        replace(
            observation,
            value=Value.point_of(observation.value.point * Decimal("1.7")),
        )
    )
    assert original["openimcc"] == ("oxygen_balance_effusion", None, "Mo")
    assert original["internal-analytical"] == original["openimcc"]
    assert changed == original


def test_magemin_and_thermoengine_borrow_one_internal_closure(monkeypatch) -> None:
    from simulator.battery.enums import CellMaterial
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    seen: dict[str, object] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, **_kwargs):
        name = str(handle.name)
        if name == "internal-analytical" and po2.mode == "oxygen_balance_effusion":
            return type("Cell", (), {
                "status": "ok",
                "refusal_reason": None,
                "melt_activities": {},
                "melt_activity_coefficients": {},
                "gas_partial_pressures_Pa": {"K": 1.0},
                "hostname": "test",
                "exit_code": 0,
                "exit_signal": None,
                "notices": [{
                    "kind": "fo2_oxygen_balance_effusion_solved",
                    "pO2_bar": 3.5e-9,
                    "relative_residual": 0.0,
                    "bracket_log10_bar": [-12.0, -6.0],
                    "dominant_O_carriers": ["O2"],
                }],
                "engine_reason": None,
                "authority": None,
            })()
        seen[name] = po2.po2_bar
        return type("Cell", (), {
            "status": "refusal",
            "refusal_reason": "census_stop",
            "melt_activities": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "engine_reason": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    observation, experiment = _kems_partial(cell_material=None)
    bench = _cell_material_bench((CellMaterial.PT,))
    predictions = []
    for engine in (Engine.THERMOENGINE, Engine.MAGEMIN):
        predictions.append(predict_with_engine(
            engine, observation, experiment=experiment, bench=bench, isolated=False,
        ))
    assert seen["thermoengine"] == 3.5e-9
    assert "magemin" not in seen
    for prediction in predictions[:1]:
        borrowed_notice = next(
            notice for notice in prediction.notices
            if "effusion closure borrowed from internal-analytical" in notice.reason
        )
        assert borrowed_notice.reason.startswith(
            "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
            "route=effusion_oxygen_balance;"
        )
        assert borrowed_notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        assert borrowed_notice.authority is Authority.BRIDGE
        assert FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND in flagged_strata(
            (borrowed_notice,)
        )
    magemin = predictions[1]
    assert magemin.execution.state is ExecutionState.UNSUPPORTED
    assert magemin.authority is Authority.REFUSED
    assert magemin.refusal_reason is RefusalReason.UNSUPPORTED
    assert magemin.refusal_detail["reason"] == "engine_missing_observable"
    assert magemin.refusal_detail["observable"] == "vapour species"


def test_carrier_gas_row_borrows_one_intrinsic_fo2(monkeypatch) -> None:
    from simulator.battery.records import SweepGas
    from tests.battery.test_silent_fills import _kems_partial

    seen: dict[str, float] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, **_kwargs):
        seen[str(handle.name)] = po2.po2_bar
        return type("Cell", (), {
            "status": "refusal",
            "refusal_reason": "census_stop",
            "melt_activities": {},
            "gas_partial_pressures_Pa": {},
            "hostname": "test",
            "exit_code": 0,
            "exit_signal": None,
            "notices": [],
            "engine_reason": None,
        })()

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    observation, _knudsen = _kems_partial(cell_material=None)
    carrier = F.tabulation_experiment(
        experiment_id=observation.experiment_id,
        total_P=Decimal("100000"),
    )
    experiment = replace(
        carrier,
        pressure_environment=replace(
            carrier.pressure_environment,
            sweep_gas=F.located(SweepGas(
                species="N2",
                flow_sccm=State.of(Decimal("10")),
                partial_pressure_Pa=State.not_applicable("carrier"),
            )),
        ),
    )
    predictions = []
    for engine in (
        Engine.ALPHAMELTS,
        Engine.THERMOENGINE,
        Engine.OPENIMCC,
        Engine.VAPOROCK,
        Engine.INTERNAL_ANALYTICAL,
        Engine.MAGEMIN,
    ):
        predictions.append(predict_with_engine(
            engine, observation, experiment=experiment, bench=None, isolated=False,
        ))
    assert set(seen) == {
        "alphamelts", "thermoengine", "openimcc", "vaporock",
        "internal-analytical",
    }
    assert len(set(seen.values())) == 1
    log = float(intrinsic_melt_fO2(
        composition_wt_pct(observation.identity.composition.value),
        float(observation.identity.temperature_K.value),
    ))
    assert next(iter(seen.values())) == 10.0 ** log
    assert all(
        any(
            notice.policy is not None
            and notice.policy.id == "DP-010"
            and notice.reason.startswith("DP-010 physics_rung=4:")
            and "commanded_default_log10_bar=" in notice.reason
            and "original_missing_evidence=" in notice.reason
            and "fo2_tier=4" in notice.reason
            and "borrowed" in notice.reason
            for notice in prediction.notices
        )
        for prediction in predictions[:-1]
    )
    magemin = predictions[-1]
    assert magemin.execution.state is ExecutionState.UNSUPPORTED
    assert magemin.authority is Authority.REFUSED
    assert magemin.refusal_reason is RefusalReason.UNSUPPORTED
    assert magemin.refusal_detail["reason"] == "engine_missing_observable"
    assert magemin.refusal_detail["observable"] == "vapour species"
