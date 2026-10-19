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

from simulator.battery.enums import Engine, FO2Channel, MethodToken, ValueKind
from simulator.battery.fo2_ladder import resolve_row_fo2
from simulator.battery.records import FO2Control, State, Value
from simulator.battery.score import (
    ScoreContext,
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


def _feo_case(**kwargs):
    return _case(
        composition=_composition(("FeO", "0.2"), ("SiO2", "0.8")),
        formula="SiO2",
        **kwargs,
    )


def test_comparison_path_does_not_solve_a_private_effusion_fo2() -> None:
    source = inspect.getsource(predict_with_engine)
    assert "PO2_OXYGEN_BALANCE_EFFUSION" not in source
    assert "oxygen_balance_effusion_unsupported_engine" not in source


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


@pytest.mark.parametrize(
    "engine",
    (Engine.ALPHAMELTS, Engine.THERMOENGINE, Engine.OPENIMCC, Engine.VAPOROCK),
)
def test_knudsen_row_without_printed_fo2_commands_one_borrowed_value(
    engine, monkeypatch,
) -> None:
    from simulator.battery.enums import CellMaterial
    from tests.battery.test_silent_fills import _cell_material_bench, _kems_partial

    seen: dict[str, object] = {}

    def _open(name):
        return type("Handle", (), {
            "name": name,
            "available": True,
            "unavailable_reason": None,
        })()

    def _cell(_handle, _pot, *, po2, **_kwargs):
        seen["po2_bar"] = po2.po2_bar
        seen["mode"] = po2.mode
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
    predict_with_engine(
        engine,
        observation,
        experiment=experiment,
        bench=_cell_material_bench((CellMaterial.PT,)),
        isolated=False,
    )
    log = float(intrinsic_melt_fO2(
        composition_wt_pct(observation.identity.composition.value),
        float(observation.identity.temperature_K.value),
    ))
    assert seen["mode"] == "commanded"
    assert seen["po2_bar"] == 10.0 ** log
