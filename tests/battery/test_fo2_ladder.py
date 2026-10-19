"""One fO2 per row, shared by every engine (d-180, t-1352).

The equality assertion is the mutation target: restoring the
internal-analytical exemption (drop its oxygen gap, and skip attaching a
ladder fO2 when the printed oxygen waypoint is empty) makes that engine's
fO2 differ from the others.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    CellMaterial,
    Engine,
    FO2Channel,
    MethodToken,
    Quantity,
    ValueKind,
)
from simulator.battery.fo2_ladder import fo2_tier_notice, resolve_row_fo2
from simulator.battery.records import FO2Control, State, Value
from simulator.battery.score import (
    ScoreContext,
    _cell_oxygen_class,
    composition_wt_pct,
    compile_residual,
    predict_with_engine,
)
from simulator.battery.waypoints import ReadinessStatus, oxygen_condition
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

    source = inspect.getsource(predict_with_engine)
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


def test_basu_metal_saturation_uses_printed_activity_and_marks_circularity() -> None:
    from simulator.battery.consumer_inputs import collect_consumer_inputs
    from simulator.battery.generators.bench import melt_activity_requests
    from simulator.battery.migrate import load_migrated_benches, load_migrated_store

    _works, experiments, observations = load_migrated_store(
        sources=("basu-2008-feo-steelmaking",)
    )
    observation = observations[
        "basu-2008-feo-steelmaking::basu_2008_tii_s01_a_feo_1873k"
    ]
    experiment = experiments[observation.experiment_id]
    bench = load_migrated_benches()[experiment.bench_id]
    resolved = resolve_row_fo2(experiment, bench, observation)
    assert resolved is not None
    assert resolved.tier == 3
    assert resolved.route == (
        "metal_saturation; fo2 from printed a_FeO (circular for a_FeO rows)"
    )
    assert resolved.log10_fO2_bar == pytest.approx(-9.68612316558128, abs=1e-12)
    notice = fo2_tier_notice(Quantity.ACTIVITY, resolved)
    assert "fo2 from printed a_FeO (circular for a_FeO rows)" in notice.reason

    requests = melt_activity_requests(
        collect_consumer_inputs(experiment, bench, observation),
        experiment=experiment,
        bench=bench,
        observation=observation,
    )
    comparable = [
        item for item in requests
        if item.readiness.engine in _COMPARABLE and item.payload is not None
    ]
    assert comparable
    assert {item.payload["fO2_log"] for item in comparable} == {
        resolved.log10_fO2_bar
    }
    assert {item.payload["fO2_tier"] for item in comparable} == {3}
    assert {item.payload["fO2_route"] for item in comparable} == {resolved.route}

    unavailable = replace(
        observation,
        value=Value(ValueKind.UNAVAILABLE, unavailable_reason="test row without a_FeO"),
    )
    assert resolve_row_fo2(experiment, bench, unavailable) is None

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        isolated=False,
    )
    context = ScoreContext(
        works=_works,
        experiments=experiments,
        observations=observations,
        extract_review={observation.source_id: "reviewed"},
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
        "fo2 from printed a_FeO (circular for a_FeO rows)" in notice.reason
        for notice in residual.notices
    )


def test_tier4_borrowed_intrinsic_is_the_same_on_every_engine() -> None:
    experiment, bench, observation = _feo_case()
    payload = _assert_same_fo2(_melt(experiment, bench, observation))
    weights = composition_wt_pct(observation.identity.composition.value)
    assert payload["fO2_log"] == float(intrinsic_melt_fO2(weights, 1473.15))
    assert payload["fO2_tier"] == 4
    assert payload["fO2_route"] == "intrinsic_melt_fO2"
    assert payload["fO2_borrowed"] is True


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
            notice.origin == "fo2_ladder" and "borrowed" in notice.reason
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
        notice.origin == "fo2_ladder"
        and notice.reason.startswith("fo2_tier=4;")
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
        notice.origin == "fo2_ladder"
        and notice.reason.startswith("fo2_tier=4;")
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


def test_unmodelled_knudsen_row_does_not_borrow_intrinsic_fo2() -> None:
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
    assert resolved is None


def test_supported_engines_receive_the_same_effusion_condition(monkeypatch) -> None:
    from simulator.battery.enums import CellMaterial
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    seen: dict[str, tuple[object, object]] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(handle, _pot, *, po2, **_kwargs):
        seen[str(handle.name)] = (po2.mode, po2.cell_material)
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
    bench = _cell_material_bench((CellMaterial.MO,))
    for engine in (Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL):
        predict_with_engine(
            engine, observation, experiment=experiment, bench=bench, isolated=False,
        )
    assert seen["openimcc"] == ("oxygen_balance_effusion", "Mo")
    assert seen["internal-analytical"] == seen["openimcc"]


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
    assert seen["magemin"] == 3.5e-9
    for prediction in predictions:
        assert any(
            "effusion closure borrowed from internal-analytical" in notice.reason
            and notice.reason.startswith("fo2_tier=3; route=effusion_oxygen_balance;")
            for notice in prediction.notices
        )


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
    carrier = F.tabulation_experiment(total_P=Decimal("100000"))
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
        "internal-analytical", "magemin",
    }
    assert len(set(seen.values())) == 1
    log = float(intrinsic_melt_fO2(
        composition_wt_pct(observation.identity.composition.value),
        float(observation.identity.temperature_K.value),
    ))
    assert next(iter(seen.values())) == 10.0 ** log
    assert all(
        any(
            notice.reason.startswith("fo2_tier=4;") and "borrowed" in notice.reason
            for notice in prediction.notices
        )
        for prediction in predictions
    )
