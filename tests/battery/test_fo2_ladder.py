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
    AmountBasis,
    AmbientPressureEvidenceClass,
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
    fo2_tier_notice,
    resolve_row_fo2,
)
from simulator.battery.records import (
    AmbientPressureEvidence,
    ApproxPressure,
    BandBasis,
    Composition,
    FO2Control,
    State,
    Value,
)
from simulator.battery.score import (
    FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND,
    FLAGGED_STRATUM_PROPOSED_POLICY,
    ScoreContext,
    _cell_oxygen_class,
    _headline_payload_admits,
    composition_wt_pct,
    compile_residual,
    flagged_strata,
    load_score_context,
    _predict_engine_from_supplied_inputs,
    _resolve_score_total_pressure,
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

    source = inspect.getsource(_predict_engine_from_supplied_inputs)
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
        identity=replace(
            observation.identity,
            total_pressure_Pa=State.of(Decimal("100000")),
        ),
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    assert payload["fO2_tier"] == 2
    assert payload["fO2_borrowed"] is False
    assert payload["fO2_route"].startswith("kress_inverse:Fe3_over_sum_Fe;")
    assert "identity.total_pressure_Pa" in payload["fO2_route"]
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


def test_tier2_knudsen_ferric_inverse_deliberately_omits_pressure_term() -> None:
    experiment, bench, observation = _feo_case()
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    resolved = resolve_row_fo2(
        experiment, bench, observation, quantity=Quantity.ACTIVITY
    )
    assert resolved is not None
    assert resolved.route.startswith("kress_inverse:Fe3_over_sum_Fe;")
    assert "pressure_term_omitted" in resolved.route
    assert "pressure_bar=None" in resolved.route
    # Published inverse at Fe3+/sumFe=0.2, identity 1473.15 K, pressure omitted
    # on FeO 0.2 / SiO2 0.8. This pin does not call the inverse.
    assert resolved.log10_fO2_bar.hex() == "-0x1.96bd50d7b1045p+2"
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert notice.kind is NoticeKind.FO2_FROM_PRINTED_FERRIC_RATIO
    assert "pressure_term_omitted" in notice.reason
    assert "pressure_bar=None" in notice.reason


def test_tier2_out_of_band_kress_inverse_is_predicted_and_flagged() -> None:
    experiment, bench, observation = _feo_case(
        temperature=Value.point_of(Decimal("1973.15")),
        pressure=Value.point_of(Decimal("1e-6")),
    )
    observation = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.of(Decimal("1973.15")),
            per=State.of(PerBasis.DIMENSIONLESS),
        ),
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    resolved = resolve_row_fo2(
        experiment, bench, observation, quantity=Quantity.ACTIVITY
    )
    assert resolved is not None
    assert resolved.temperature_band_status == "extrapolation_experimentally_confirmed"
    assert resolved.log10_fO2_bar == -2.008303300370294
    assert resolved.log10_fO2_bar.hex() == "-0x1.01101521c5071p+1"
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    assert notice.authority is Authority.EXTRAPOLATED
    assert "fo2_from_printed_ferric_ratio" in notice.reason
    assert "Kress91 temperature band" in (notice.band or "")
    experiment = replace(experiment, bench_id=bench.id)
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
    scored = next(
        item
        for item in residual.notices
        if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    )
    assert "extrapolation_experimentally_confirmed" in (scored.band or "")
    assert (
        "kress91_temperature_band_status=extrapolation_experimentally_confirmed"
        in scored.reason
    )
    assert FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND in flagged_strata(residual.notices)


def test_scored_ferric_quantity_does_not_invert_its_own_fraction() -> None:
    """Tier 2 stays for omitted quantity and yields to an independent buffer."""

    _experiment, _bench, observation = _feo_case()
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    weights = {"SiO2": 50.0, "FeO": 9.94, "Fe2O3": 1.60}
    temperature_K = 1673.15
    inverted = resolve_row_fo2(
        None,
        None,
        observation,
        composition_wt=weights,
        temperature_K=temperature_K,
    )
    assert inverted is not None
    assert inverted.tier == 2
    assert inverted.route.startswith("kress_inverse:Fe3_over_sum_Fe")
    assert inverted.borrowed is False
    ferric_target = resolve_row_fo2(
        None,
        None,
        observation,
        composition_wt=weights,
        temperature_K=temperature_K,
        quantity=Quantity.FE3_FE2_RATIO,
    )
    assert ferric_target is not None
    assert ferric_target.tier == 4
    named_non_fe_couple = resolve_row_fo2(
        None,
        None,
        observation,
        composition_wt=weights,
        temperature_K=temperature_K,
        quantity=Quantity.VALENCE_RATIO,
    )
    assert named_non_fe_couple is not None
    assert named_non_fe_couple.tier == 2
    assert named_non_fe_couple.route.startswith("kress_inverse:")

    experiment, bench, buffer_observation = _feo_case(
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
    buffer_observation = replace(
        buffer_observation,
        point_conditions={
            **buffer_observation.point_conditions,
            "Fe3_over_sum_Fe": F.located(Decimal("0.2")),
        },
    )
    kept = resolve_row_fo2(
        experiment,
        bench,
        buffer_observation,
        quantity=Quantity.FE3_FE2_RATIO,
    )
    assert kept is not None
    assert kept.tier == 3
    assert kept.route == "buffer_relation"
    assert kept.borrowed is False


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


def test_dp010_ambient_air_default_is_pinned_on_every_engine() -> None:
    experiment, bench, observation = _feo_case()
    experiment = replace(experiment, method=State.of(MethodToken.TABULATION))
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    assert payload["fO2_log"] == pytest.approx(-0.672064092852, abs=5e-13)
    assert payload["fO2_tier"] == 4
    assert payload["fO2_route"] == "dp010_ambient_air_unprinted_atmosphere"
    assert payload["fO2_borrowed"] is False
    resolved = resolve_row_fo2(experiment, bench, observation)
    assert resolved is not None
    assert resolved.log10_fO2_bar == pytest.approx(-0.672064092852, abs=5e-13)
    assert resolved.po2_bar == pytest.approx(0.2127825, abs=1e-15)
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert notice.kind is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    assert notice.policy is not None
    assert (notice.policy.id, notice.policy.version, notice.policy.status) == (
        "DP-010",
        2,
        "proposed",
    )
    assert "pO2=0.21 atm" in notice.reason
    assert "log10(fO2/bar)=-0.672064092852" in notice.reason
    changed = resolve_row_fo2(
        experiment,
        bench,
        replace(observation, value=Value.point_of(observation.value.point * Decimal("1.7"))),
    )
    assert _fo2_input(changed) == _fo2_input(resolved)


def test_printed_gas_mix_uses_dp002_when_total_pressure_is_unprinted() -> None:
    experiment, bench, observation = _feo_case()
    pressure_environment = replace(
        experiment.pressure_environment,
        total_pressure_Pa=replace(
            experiment.pressure_environment.total_pressure_Pa,
            state=State.unknown("total pressure is not printed"),
        ),
    )
    experiment = replace(
        experiment,
        method=State.of(MethodToken.TABULATION),
        pressure_environment=pressure_environment,
        ambient_pressure_evidence=AmbientPressureEvidence(
            evidence_class=AmbientPressureEvidenceClass.GAS_MIXING,
            quote="O2/Ar gas-mixing furnace",
            locator=F.loc(page=1, paragraph="Experimental setup"),
        ),
    )
    mixture = Composition(
        basis="printed_gas_mixture",
        components=(("O2", Decimal("0.2")), ("Ar", Decimal("0.8"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    observation = replace(
        observation,
        point_conditions={
            **(observation.point_conditions or {}),
            "gas_composition": F.located(mixture),
        },
    )

    resolved = resolve_row_fo2(
        experiment, bench, observation, quantity=Quantity.ACTIVITY
    )

    assert resolved is not None
    assert resolved.route == "observation_gas_composition_method_implied_pressure"
    assert resolved.log10_fO2_bar == pytest.approx(-0.693253391922288, abs=5e-13)
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert notice.kind is NoticeKind.METHOD_IMPLIED
    assert notice.policy is not None
    assert notice.policy.id == "DP-002"
    assert notice.policy.value == "101325"
    assert "log10(fO2/bar)=-0.693253391922" in notice.reason


def test_printed_gas_mix_rejects_unregistered_pressure_evidence() -> None:
    from simulator.battery import waypoints

    experiment, bench, observation = _feo_case()
    pressure_environment = replace(
        experiment.pressure_environment,
        total_pressure_Pa=replace(
            experiment.pressure_environment.total_pressure_Pa,
            state=State.unknown("total pressure is not printed"),
        ),
    )
    experiment = replace(
        experiment,
        method=State.of(MethodToken.TABULATION),
        pressure_environment=pressure_environment,
        ambient_pressure_evidence=AmbientPressureEvidence(
            evidence_class=AmbientPressureEvidenceClass.GAS_FLOW,
            quote="Argon flowed through the furnace at 5 sccm.",
            locator=F.loc(page=1, paragraph="Experimental setup"),
        ),
    )
    mixture = Composition(
        basis="printed_gas_mixture",
        components=(("O2", Decimal("0.2")), ("Ar", Decimal("0.8"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    observation = replace(
        observation,
        point_conditions={
            **(observation.point_conditions or {}),
            "gas_composition": F.located(mixture),
        },
    )

    oxygen = waypoints.oxygen_condition(experiment, bench, observation)

    assert not any(
        route.route == "observation_gas_composition_method_implied_pressure"
        for route in oxygen.routes
    )


def test_vacuum_dp019_boundary_is_explicitly_flagged() -> None:
    experiment, bench, observation = _feo_case()
    experiment = replace(experiment, method=State.of(MethodToken.VACUUM_CHAMBER_PYROLYSIS))
    resolved = resolve_row_fo2(experiment, bench, observation, quantity=Quantity.ACTIVITY)
    assert resolved is not None
    assert resolved.route == "dp019_vacuum_boundary"
    assert resolved.borrowed is False
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert notice.kind is NoticeKind.METHOD_IMPLIED
    assert notice.policy is not None and notice.policy.id == "DP-019"
    assert "DP-019 method-implied high-vacuum boundary" in notice.reason
    assert resolved.log10_fO2_bar == pytest.approx(-6.0, abs=5e-13)


def test_container_buffers_use_the_existing_frost_owner() -> None:
    from benchmarks.buffer_reproduction import PUBLISHED_BUFFERS
    from simulator.oxygen_buffers import frost_condensed_buffer_log10_fO2_bar

    temperature_K = Decimal("1200")
    pressure_Pa = Decimal("100000")
    _base_experiment, bench, _base_observation = _feo_case()
    for container, token in (
        ("Fe-metal capsule", "IW"),
        ("Ni/NiO capsule", "NNO"),
        ("wüstite-magnetite capsule", "WM"),
        ("quartz-fayalite-magnetite buffer capsule", "QFM"),
    ):
        experiment = F.tabulation_experiment(
            method=MethodToken.QUENCH_EQUILIBRATION,
            T_K=temperature_K,
            total_P=pressure_Pa,
        )
        experiment = replace(
            experiment,
            sample=replace(
                experiment.sample,
                container=F.located(container),
            ),
        )
        oxygen = oxygen_condition(experiment, bench, None)
        assert oxygen.selected is not None
        assert oxygen.selected.route == "container_buffer_relation"
        assert "experiment.sample.container" in oxygen.selected.inputs
        buffer = PUBLISHED_BUFFERS[token]
        expected = frost_condensed_buffer_log10_fO2_bar(
            Decimal(str(buffer.A_K)),
            Decimal(str(buffer.B)),
            Decimal(str(buffer.pressure_coefficient_K_per_bar)),
            temperature_K,
            pressure_Pa / Decimal("100000"),
        )
        resolved = resolve_row_fo2(
            experiment,
            bench,
            None,
            temperature_K=float(temperature_K),
            oxygen=oxygen,
            quantity=Quantity.ACTIVITY,
        )
        assert resolved is not None
        assert float(resolved.log10_fO2_bar) == float(expected)
        assert resolved.route == "container_buffer_relation"
        assert fo2_tier_notice(Quantity.ACTIVITY, resolved).kind is (
            NoticeKind.FO2_BUFFER_FROM_CONTAINER
        )


def test_molybdenum_capsule_is_a_typed_gap_even_for_knudsen() -> None:
    _base_experiment, bench, observation = _feo_case()
    for container in (
        "rhenium boat",
        "ruthenium capsule",
        "molybdenum capsule",
        "tungsten cell",
        "Re wire loop",
        "Re/Ru capsule",
        "Mo/W cell",
        "MoO2 capsule",
    ):
        for method in (
            MethodToken.QUENCH_EQUILIBRATION,
            MethodToken.KNUDSEN_EFFUSION,
        ):
            experiment = F.tabulation_experiment(
                method=method, T_K=Decimal("1473.15")
            )
            experiment = replace(
                experiment,
                sample=replace(
                    experiment.sample,
                    container=F.located(container),
                ),
            )
            row = replace(observation, experiment_id=experiment.experiment_id)
            oxygen = oxygen_condition(experiment, bench, row)
            assert oxygen.selected is None, container
            assert oxygen.absence is not None, container
            assert oxygen.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
            resolved = resolve_row_fo2(
                experiment,
                bench,
                row,
                composition=observation.identity.composition.value,
                temperature_K=float(observation.identity.temperature_K.value),
                oxygen=oxygen,
                quantity=Quantity.ACTIVITY,
            )
            assert resolved is not None, (container, method)
            assert resolved.route == "dp010_ambient_air_unprinted_atmosphere", (
                container, method, resolved.route,
            )
            assert resolved.policy is not None and resolved.policy.id == "DP-010"


def test_unknown_explicit_buffer_is_flagged_dp010_not_a_vacuum_proxy() -> None:
    _base_experiment, bench, observation = _feo_case()
    for method in (
        MethodToken.QUENCH_EQUILIBRATION,
        MethodToken.KNUDSEN_EFFUSION,
        MethodToken.VACUUM_CHAMBER_PYROLYSIS,
    ):
        experiment = F.tabulation_experiment(
            method=method, T_K=Decimal("1473.15")
        )
        experiment = replace(
            experiment,
            fO2_control=FO2Control(
                channel=F.State.of(FO2Channel.BUFFER),
                buffer=F.located("NOT_A_REAL_BUFFER"),
            ),
        )
        row = replace(observation, experiment_id=experiment.experiment_id)
        oxygen = oxygen_condition(experiment, bench, row)
        assert oxygen.absence is not None
        assert oxygen.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
        resolved = resolve_row_fo2(
            experiment,
            bench,
            row,
            composition=observation.identity.composition.value,
            temperature_K=float(observation.identity.temperature_K.value),
            oxygen=oxygen,
            quantity=Quantity.ACTIVITY,
        )
        assert resolved is not None, method
        assert resolved.route == "dp010_ambient_air_unprinted_atmosphere"
        assert resolved.route != "fo2_vacuum_cell_proxy"
        assert resolved.policy is not None and resolved.policy.id == "DP-010"


def test_vacuum_bound_does_not_hide_an_unsupported_container() -> None:
    _base_experiment, bench, observation = _feo_case()
    experiment = F.tabulation_experiment(
        method=MethodToken.KNUDSEN_EFFUSION,
        T_K=Decimal("1473.15"),
    )
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            container=F.located("molybdenum capsule"),
        ),
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=F.located(
                Decimal("1e-6"), page=1, note="chamber vacuum"
            ),
        ),
    )
    row = replace(observation, experiment_id=experiment.experiment_id)
    oxygen = oxygen_condition(experiment, bench, row)
    assert oxygen.selected is None
    assert oxygen.absence is not None
    assert oxygen.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert any("molybdenum capsule" in item for item in oxygen.absence.missing)
    resolved = resolve_row_fo2(
        experiment,
        bench,
        row,
        composition=row.identity.composition.value,
        temperature_K=float(row.identity.temperature_K.value),
        oxygen=oxygen,
        quantity=Quantity.ACTIVITY,
    )
    assert resolved is not None
    assert resolved.route == "dp010_ambient_air_unprinted_atmosphere"
    assert resolved.route != "vacuum_total_pressure_upper_bound"
    assert resolved.route != "fo2_vacuum_cell_proxy"
    assert resolved.policy is not None and resolved.policy.id == "DP-010"

    printed = replace(
        row,
        point_conditions={
            **(row.point_conditions or {}),
            "fO2_log": F.located(Decimal("-8")),
        },
    )
    printed_oxygen = oxygen_condition(experiment, bench, printed)
    assert printed_oxygen.selected is not None
    assert printed_oxygen.selected.route == "observation_fO2_log"

    plain = F.tabulation_experiment(
        method=MethodToken.QUENCH_EQUILIBRATION,
        T_K=Decimal("1473.15"),
    )
    plain = replace(
        plain,
        pressure_environment=replace(
            plain.pressure_environment,
            total_pressure_Pa=F.located(
                Decimal("1e-6"), page=1, note="chamber vacuum"
            ),
        ),
    )
    plain_row = replace(observation, experiment_id=plain.experiment_id)
    vacuum = oxygen_condition(plain, bench, plain_row)
    assert vacuum.selected is not None
    assert vacuum.selected.route == "vacuum_total_pressure_upper_bound"


@pytest.mark.parametrize(
    ("method", "column_note", "located", "qualifies"),
    [
        (MethodToken.WET_CHEMICAL_ANALYSIS, "ferrous oxide", True, True),
        (MethodToken.MOSSBAUER_SPECTROSCOPY, "ferrous oxide", True, True),
        (MethodToken.TABULATION, "FeO by wet-chemical analysis", True, True),
        (MethodToken.TABULATION, "ferrous oxide", True, False),
        (MethodToken.KNUDSEN_EFFUSION, "ferrous oxide", True, False),
        (MethodToken.WET_CHEMICAL_ANALYSIS, "ferrous oxide", False, False),
    ],
)
def test_printed_feo_fe2o3_pair_is_the_kress_inverse(
    method: MethodToken, column_note: str, located: bool, qualifies: bool,
) -> None:
    experiment, bench, observation = _feo_case()
    experiment = replace(experiment, method=State.of(method))
    ferric = F.located(Decimal("5"))
    observation = replace(
        observation,
        point_conditions={
            **observation.point_conditions,
            "FeO_wt_pct": F.located(Decimal("10"), page=1, note=column_note),
            "Fe2O3_wt_pct": ferric if located else replace(ferric, locator=None),
        },
    )
    resolved = resolve_row_fo2(
        experiment, bench, observation, quantity=Quantity.ACTIVITY
    )
    assert resolved is not None
    assert resolved.route.startswith(
        "kress_inverse:FeO_wt_pct+Fe2O3_wt_pct;"
    ) is qualifies


def test_graphite_container_uses_closed_cco_at_printed_or_resolved_pressure() -> None:
    from simulator.oxygen_buffers import cco_buffered_fO2

    temperature_K = Decimal("1473.15")
    _base_experiment, bench, _base_observation = _feo_case()
    experiment = F.tabulation_experiment(
        method=MethodToken.QUENCH_EQUILIBRATION,
        T_K=temperature_K,
        total_P=Decimal("100000"),
    )
    experiment = replace(
        experiment,
        sample=replace(experiment.sample, container=F.located("graphite capsule")),
    )
    oxygen = oxygen_condition(experiment, bench, None)
    assert oxygen.selected is not None
    assert oxygen.selected.route == "container_graphite_c_co_buffer"
    expected = cco_buffered_fO2(
        float(temperature_K), 1.0, reference_buffer=None
    ).log10_fO2_bar
    assert float(oxygen.selected.value.point) == float(expected)

    unknown_pressure = replace(
        experiment,
        pressure_environment=replace(
            experiment.pressure_environment,
            total_pressure_Pa=replace(
                experiment.pressure_environment.total_pressure_Pa,
                state=State.unknown("run pressure not printed"),
            ),
        ),
    )
    unresolved = oxygen_condition(unknown_pressure, bench, None)
    assert unresolved.selected is None

    identity = replace(F.pref_identity(T_K=temperature_K), fO2_Pa=None)
    pressure = _resolve_score_total_pressure(
        identity,
        Quantity.ACTIVITY,
        experiment=unknown_pressure,
    )
    assert pressure[0] == 1.01325
    assert pressure[1] == State.of(Decimal("101325"))
    assert pressure[2] is not None
    assert pressure[2].policy is not None
    assert pressure[2].policy.id == "DP-001"
    observation = F.observation(
        "graphite-buffer-pressure-default",
        unknown_pressure.experiment_id,
        replace(identity, total_pressure_Pa=pressure[1]),
        Decimal("1"),
    )
    observation = replace(observation, point_conditions={
        field: control for field, control in observation.point_conditions.items()
        if field != "total_pressure_Pa"
    })
    oxygen = oxygen_condition(unknown_pressure, bench, observation)
    assert oxygen.selected is not None
    assert oxygen.selected.route == "container_graphite_c_co_buffer"
    assert "observation[graphite-buffer-pressure-default].identity.total_pressure_Pa" in oxygen.selected.inputs
    expected_default_pressure = cco_buffered_fO2(
        float(temperature_K), 1.01325, reference_buffer=None
    ).log10_fO2_bar
    assert float(oxygen.selected.value.point) == float(expected_default_pressure)


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
    assert "temperature_K" in names
    assert "oxygen_condition" not in names


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
    physical_pressures: dict[str, float] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, physical_pressure_bar, **_kwargs):
        seen[str(handle.name)] = po2.po2_bar
        physical_pressures[str(handle.name)] = physical_pressure_bar
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
    low, high = Decimal("1"), Decimal("10")
    experiment, bench, observation = _feo_case(
        pressure=Value(ValueKind.INTERVAL, interval_low=low, interval_high=high),
    )
    experiment = replace(experiment, method=State.of(MethodToken.TABULATION))
    predictions = []
    for engine in (Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.OPENIMCC):
        predictions.append(predict_with_engine(
            engine, observation, experiment=experiment, bench=bench, isolated=False,
        ))
    assert set(seen) == {"alphamelts", "thermoengine", "openimcc"}
    assert physical_pressures == dict.fromkeys(seen, 3.1622776601683795e-5)
    assert len(set(seen.values())) == 1
    assert next(iter(seen.values())) is not None
    assert all(item.value is not None for item in predictions)
    assert all(item.refusal_reason is None for item in predictions)
    assert all(
        any(
            notice.kind is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
            and notice.policy is not None
            and notice.policy.id == "DP-010"
            and "log10(fO2/bar)=-0.672064092852" in notice.reason
            and "fo2_tier=4" in notice.reason
            and "original_reason=fO2 unprinted" in notice.reason
            and "borrowed" not in notice.reason
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
    for prediction in (*predictions, internal):
        assert "total_pressure_interval_nominal" in flagged_strata(prediction.notices)
        notice = next(n for n in prediction.notices if n.kind is NoticeKind.TOTAL_PRESSURE_INTERVAL_NOMINAL)
        assert notice.policy.id == "DP-114"
        assert notice.policy.status == "proposed"
        assert "[1, 10] Pa" in notice.reason
    pressure = predictions[0].identity.total_pressure_Pa.value
    assert pressure == ApproxPressure(Decimal("3.162277660168379331998893544"), low, high, BandBasis.PRINTED)
    assert any(
        notice.kind is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
        and notice.policy is not None
        and notice.policy.id == "DP-010"
        and "log10(fO2/bar)=-0.672064092852" in notice.reason
        and "original_reason=fO2 unprinted" in notice.reason
        and "borrowed" not in notice.reason
        for notice in internal.notices
    )


@pytest.mark.parametrize(("low", "high"), (
    ("0", "10"), ("-1", "10"), ("10", "1"), ("1", "Infinity"),
), ids=("zero", "negative", "inverted", "nonfinite"))
def test_invalid_total_pressure_interval_refuses_before_adapter(monkeypatch, low, high):
    recorded = Value(ValueKind.INTERVAL, interval_low=Decimal("1"), interval_high=Decimal("10"))
    # Test the scorer's defense independently of Value's finite-edge validation.
    object.__setattr__(recorded, "interval_low", Decimal(low))
    object.__setattr__(recorded, "interval_high", Decimal(high))
    experiment, bench, observation = _feo_case()
    experiment = replace(
        experiment, method=State.of(MethodToken.TABULATION),
        pressure_environment=replace(
            experiment.pressure_environment, total_pressure_Pa=F.located(recorded),
        ),
    )

    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid recorded pressure reached the adapter")

    monkeypatch.setattr("simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine", unexpected)
    prediction = predict_with_engine(
        Engine.ALPHAMELTS, observation, experiment=experiment, bench=bench, isolated=False,
    )
    assert prediction.refusal_detail["reason"] == "total_pressure_invalid"
    assert "total_pressure_interval_nominal" not in flagged_strata(prediction.notices)


def test_tier_notice_reaches_the_residual() -> None:
    experiment, bench, observation = _feo_case(
        pressure=Value.point_of(Decimal("1e-6")),
    )
    experiment = replace(
        experiment,
        method=F.State.of(MethodToken.TABULATION),
        bench_id=bench.id,
    )
    observation = replace(
        observation, identity=replace(observation.identity, per=State.of(PerBasis.DIMENSIONLESS)),
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
    ambient_notice = next(
        notice
        for notice in residual.notices
        if notice.kind is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    )
    assert ambient_notice.policy is not None
    assert ambient_notice.policy.id == "DP-010"
    assert ambient_notice.policy.status == "proposed"
    assert ambient_notice.policy.version == 2
    assert "log10(fO2/bar)=-0.672064092852" in ambient_notice.reason
    assert "fo2_tier=4" in ambient_notice.reason
    assert "original_reason=fO2 unprinted" in ambient_notice.reason
    assert "borrowed" not in ambient_notice.reason
    assert prediction.value is not None and prediction.value.is_finite()
    assert residual.numeric is not None
    assert residual.numeric.value.is_finite()
    assert FLAGGED_STRATUM_PROPOSED_POLICY in flagged_strata(residual.notices)
    plain = residual_to_plain(residual)
    assert _headline_payload_admits(plain, tier="measured") is False
    plain_notice = next(
        item
        for item in plain["notices"]
        if isinstance(item, dict)
        and item.get("kind") == ambient_notice.kind.value
    )
    assert plain_notice["policy"]["id"] == "DP-010"
    assert plain_notice["policy"]["status"] == "proposed"


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


def test_inert_carrier_row_uses_dp010_ambient_air(monkeypatch) -> None:
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
    assert next(iter(seen.values())) == pytest.approx(0.2127825, abs=1e-15)
    assert all(
        any(
            notice.kind is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
            and notice.policy is not None
            and notice.policy.id == "DP-010"
            and "log10(fO2/bar)=-0.672064092852" in notice.reason
            and "fo2_tier=4" in notice.reason
            and "original_reason=fO2 unprinted" in notice.reason
            and "borrowed" not in notice.reason
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
