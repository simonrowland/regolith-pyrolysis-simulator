"""Knudsen extent anchors and a pre-refactor Langmuir numeric pin."""

import multiprocessing
import os
import pickle
import sys
import math
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AdmissionStatus, AmountBasis, Engine, EvidenceClass, ExperimentKind, MethodToken, Phase, Quantity,
    RegimeClass, UncertaintyKind,
)
from simulator.battery.identity import Identity
from simulator.battery.records import (
    Admission, Apparatus, ApparatusGeometry, Composition, Evidence, Experiment, FlowRegime, Located, Locator, Observation,
    PressureEnvironment, Sample, Species, State, ThermalSchedule, Uncertainty, Value,
)
from simulator.battery.residue import (
    ResidueChannel, ResidueInventoryRefusal, integrate_knudsen_residue_inventory,
    integrate_residue_inventory, predict_knudsen_residue_composition,
)


@pytest.mark.parametrize("oxygen_parent", (None, ""))
def test_langmuir_pressure_refactor_numeric_pin(oxygen_parent):
    result = integrate_residue_inventory(
        {"FeO": 1.0, "MgO": 2.0},
        (
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "pin"),
            ResidueChannel("Mg", "Mg", "MgO", 1.0, "pin"),
            ResidueChannel("O2", "O2", oxygen_parent, 1.0, "pin"),
        ),
        lambda n, p: {
            "Fe": 5e-8 * 10 ** (-p / 2),
            "Mg": 2e-8 * 10 ** (-p / 2),
            "O2": 10**p * 1e5,
        },
        temperature_K=2000, duration_s=10, area_evolution_m2=(0.001,) * 3,
    )
    assert {k: v.hex() for k, v in result.residue_mol.items()} == {
        "FeO": "0x1.fffffcbbc23c7p-1", "MgO": "0x1.ffffff027bd22p+0",
    }
    assert {k: v.hex() for k, v in result.evaporated_mol.items()} == {
        "Fe": "0x1.a21ee1c66bbd4p-24", "Mg": "0x1.fb085bb1c9290p-25",
        "O2": "0x1.4fd187cfa828ep-24",
    }
    assert result.pO2_bar_by_step == (float.fromhex("0x1.36ad1d112f1c7p-28"),) * 3
    assert result.atom_closure_mol == {"Fe": 0.0, "Mg": 0.0, "O": 0.0}


def test_hashimoto_exhaustion_closure_refactor_numeric_pin():
    from simulator.battery.residue import _hashimoto_integrate_geometry
    result = _hashimoto_integrate_geometry(
        {"FeO": 6.84e-10, "MgO": 1.0},
        tuple(ResidueChannel(s, s, p, 1.0, "pin")
              for s, p in (("Fe", "FeO"), ("Mg", "MgO"), ("O2", None))),
        lambda n, p: {"Fe": 5e-8 * 10**(-p / 2) * n.get("FeO", 0),
                      "Mg": 2e-8 * 10**(-p / 2) * n.get("MgO", 0), "O2": 10**p * 1e5},
        temperature_K=2000, duration_s=1, steps=1,
        geometry_policy_id="sphere_constant", initial_area_m2=0.0001,
    )
    assert {k: v.hex() for k, v in result.inventory_mol.items()} == {
        "FeO": "0x0.0p+0", "MgO": "0x1.fffffff8fbf2dp-1"}
    assert {k: v.hex() for k, v in result.atom_closure.items()} == {
        "Fe": "0x0.0p+0", "O": "-0x1.29356b3000000p-55", "Mg": "0x0.0p+0"}
    assert result.exhaustion_notices[0]["remaining_moles"] == 6.84e-10


def _channels(gas="FeO"):
    return (ResidueChannel(gas, gas, "FeO", 1.0, "equilibrium effusion"),
            ResidueChannel("O2", "O2", None, 1.0, "equilibrium effusion"))


def _preserve_pressure_contract(source, wrapper):
    from simulator.battery.residue import _inherit_residue_pressure_contract

    _inherit_residue_pressure_contract(wrapper, source)
    return wrapper


@pytest.mark.parametrize("pressure", (1e-9, 1e6))
def test_single_volatile_limit_is_exact_and_scale_free(pressure):
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels(),
        lambda n, p, t: {"FeO": pressure, "O2": 0.0},
        temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # One volatile only: a quarter of the original mass is a quarter mole.
    assert result.residue_mol["FeO"] == pytest.approx(0.75, abs=2e-14)
    assert result.evaporated_mol["FeO"] == pytest.approx(0.25, abs=2e-14)
    assert result.retained_oxygen_atoms_mol == pytest.approx(0.75)


def test_knudsen_extent_refuses_non_equilibrium_channel_alpha():
    channels = (replace(_channels()[0], alpha=0.5), *_channels()[1:])
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, channels,
            lambda n, p, t: {"FeO": 1.0, "O2": 0.0},
            temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
            near_saturated=True, source_fO2_log_by_interval=(-10.0,),
        )
    assert exc.value.reason == "knudsen_equilibrium_channels_required"


def test_knudsen_extent_limits_rhs_evaluations(monkeypatch):
    from simulator.battery import residue

    monkeypatch.setattr(residue, "_KNUDSEN_MAX_RHS_EVALUATIONS", 1)
    with pytest.raises(ResidueInventoryRefusal, match="RHS evaluation limit in 2000 K") as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, _channels(),
            lambda n, p, t: {"FeO": 1.0, "O2": 0.0},
            temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
            near_saturated=True, source_fO2_log_by_interval=(-10.0,),
        )
    assert exc.value.reason == "knudsen_extent_evaluation_limit"


def test_oxygen_only_loss_advances_xi_without_debiting_cations():
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels("Fe"),
        lambda n, p, t: {"Fe": 0.0, "O2": 1.0},
        temperature_path=((0.1, 2000.0),), measured_mass_loss_fraction=0.1,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # CIAAW: 1 mol FeO is 71.844 g. 10% loss as O2 is 7.1844 g,
    # or 0.22452653290830677 mol O2. No Fe atoms escape.
    assert result.evaporated_mol["O2"] == pytest.approx(0.22452653290830677)
    assert result.residue_mol == {"FeO": 1.0}
    assert result.retained_oxygen_atoms_mol == pytest.approx(0.5509469341833865)
    assert max(abs(v) for v in result.atom_closure_mol.values()) < 1e-13


def test_closure_loss_counts_fe_and_oxygen_mass():
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0}, _channels("Fe"),
        lambda n, p, t: {"Fe": 1e-8 * 10**(-p / 2), "O2": 1e5 * 10**p},
        temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
        near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    # FeO -> Fe + 1/2 O2: 25% loss is 0.25 mol Fe, 0.125 mol O2.
    assert result.evaporated_mol["Fe"] == pytest.approx(0.25, abs=1e-12)
    assert result.evaporated_mol["O2"] == pytest.approx(0.125, abs=1e-12)
    assert result.residue_mol["FeO"] == pytest.approx(0.75, abs=1e-12)


def test_composition_dependent_high_loss_matches_independent_analytic_solution():
    channels = (*_channels(), ResidueChannel("MgO", "MgO", "MgO", 1.0, "effusion"))
    result = integrate_knudsen_residue_inventory(
        {"FeO": 1.0, "MgO": 1.0}, channels,
        lambda n, p, t: {"FeO": 5 * n.get("FeO", 0) * math.sqrt(0.071844),
                         "MgO": math.sqrt(0.040304), "O2": 0.0},
        temperature_path=((0.98, 2000.0),), measured_mass_loss_fraction=0.98,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # Independent integration in gas-packet coordinate s: nMgO=1-s,
    # nFeO=exp(-5s). At 98% total mass loss, solving
    # 71.844*exp(-5s)+40.304*(1-s)=0.02*(71.844+40.304)
    # gives s=.9590860932527849 and FeO=26.481341729689376 wt%.
    from simulator.battery.residue import _hashimoto_project_oxide_wt_pct
    assert _hashimoto_project_oxide_wt_pct(result.residue_mol)["FeO"] == pytest.approx(26.481341729689376, abs=1e-5)


def test_trace_parent_pressure_tracks_actual_inventory():
    channels = (*_channels(), ResidueChannel("MgO", "MgO", "MgO", 1.0, "effusion"))
    initial_feo = 1e-10
    iron_pressures = []

    def pressure(inventory, _logp, _temperature):
        feo = inventory.get("FeO", 0.0)
        iron_pressures.append(feo)
        return {"FeO": feo, "MgO": 1.0, "O2": 0.0}

    result = integrate_knudsen_residue_inventory(
        {"FeO": initial_feo, "MgO": 1.0}, channels, pressure,
        temperature_path=((0.2, 2000.0),), measured_mass_loss_fraction=0.2,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
    )
    # Per mol MgO lost, dnFeO/ds = -sqrt(M_MgO/M_FeO) * nFeO.
    # The trace contributes < 2e-10 of the charge mass, so at s ≈ 0.2
    # the exponential retains 86.0881% of FeO and never depletes in finite s.
    retained = initial_feo * math.exp(-0.2 * math.sqrt(40.304 / 71.844))
    assert iron_pressures[0] == initial_feo
    assert all(0 < p <= initial_feo for p in iron_pressures)
    assert result.residue_mol["FeO"] == pytest.approx(retained, rel=1e-7, abs=0)
    assert result.evaporated_mol["FeO"] == pytest.approx(initial_feo - retained, rel=1e-7, abs=0)


@pytest.mark.parametrize("changes,reason", (
    ({"near_saturated": False}, "knudsen_near_saturation_invalid"),
    ({"temperature_path": None}, "printed_temperature_path_missing_or_invalid"),
    ({"measured_mass_loss_fraction": None}, "measured_mass_loss_fraction_missing_or_invalid"),
    ({"initial_inventory_mol": {}}, "initial_composition_invalid"),
))
def test_extent_inputs_refuse_typed(changes, reason):
    kwargs = dict(initial_inventory_mol={"FeO": 1.0}, channels=_channels(),
                  pressure_model=lambda n, p, t: {"FeO": 1.0, "O2": 0.0},
                  temperature_path=((0.25, 2000.0),), measured_mass_loss_fraction=0.25,
                  near_saturated=True, source_fO2_log_by_interval=(-10.0,))
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(**(kwargs | changes))
    assert exc.value.reason == reason


def test_source_condition_cannot_supply_unweighed_oxygen():
    with pytest.raises(ResidueInventoryRefusal, match="weighed charge") as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, _channels("Fe"), lambda n, p, t: {"Fe": 0.0, "O2": 1.0},
            temperature_path=((0.5, 2000.0),), measured_mass_loss_fraction=0.5,
            near_saturated=True, source_fO2_log_by_interval=(-10.0,),
        )
    assert exc.value.reason == "knudsen_near_saturation_oxygen_exhausted"


@pytest.fixture
def extent_point(monkeypatch):
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    composition = Composition("synthetic", (("FeO", Decimal("0.5")), ("MgO", Decimal("0.5"))), AmountBasis.MOLE_FRACTION)
    locator = Locator(table="2", published_page=510)
    reference = Observation(
        "residue", "experiment", Identity(
            Quantity.RESIDUE_COMPONENT_COMPOSITION, Species("FeO", Phase.L),
            subtype=State.of("oxide_wt_percent"), temperature_K=State.of(Decimal(2100)),
            composition=State.of(composition),
        ), Value.point_of(99), Uncertainty(UncertaintyKind.NONE),
        Evidence(State.of(EvidenceClass.MEASURED_DIRECT)),
        Admission(AdmissionStatus.ADMITTED, "synthetic anchor"), (),
        source_id="synthetic", locator=locator,
    )
    rows = tuple(replace(reference, observation_id=f"mass-{t}",
                         identity=replace(reference.identity, quantity=Quantity.MASS_LOSS_FRACTION,
                                          temperature_K=State.of(Decimal(t))), value=Value.point_of(x))
                 for t, x in ((2000, "0.1"), (2100, "0.2")))
    experiment = Experiment(
        "experiment", ExperimentKind.LITERATURE, State.of(MethodToken.KNUDSEN_EFFUSION),
        Sample(initial_composition=Located(State.of(composition), locator)),
        {"temperature_K": Located(State.unknown("point temperatures are printed per row"), locator)},
        PressureEnvironment(
            Located(State.unknown("not published")), Located(State.unknown("no sweep")),
            FlowRegime(State.unknown("not published")),
        ),
        thermal_schedule=ThermalSchedule(method=Located(State.of("70 K steps, 15 minutes each"), locator)),
    )
    monkeypatch.setitem(sys.modules, "openimcc", SimpleNamespace(load_gas_datapack=lambda: object()))
    monkeypatch.setattr(battery, "_openimcc_gas_channels_and_omission_notices",
                        lambda *a: ([("Fe", ("FeO", 1, 0.5)), ("O2", (None, 1, -1))], ()))
    # Synthetic law, exact oxygen-balanced packets at every temperature.
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k:
                        lambda n, p: {"Fe": 1e-8 * 10**(-p / 2), "O2": 1e5 * 10**p})
    return reference, experiment, rows


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_each_row_uses_own_extent_and_never_measured_composition(extent_point):
    reference, experiment, rows = extent_point
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    earlier = replace(reference, identity=replace(reference.identity, temperature_K=State.of(Decimal(2000))))
    first = predict_knudsen_residue_composition("internal-analytical", earlier, experiment, rows)
    changed = replace(reference, value=Value.point_of(1), point_conditions={"composition": Located(State.unknown("must not read"))})
    same = predict_knudsen_residue_composition("internal-analytical", changed, experiment, rows)
    assert result["escaped_mass_fraction"] == pytest.approx(0.2)
    # Golden pin for the Knudsen adapter's wt%-to-mol inventory conversion.
    assert result["composition_wt_pct"] == pytest.approx({
        "FeO": 55.077219388664986,
        "MgO": 44.92278061133502,
    }, abs=1e-12)
    assert first["escaped_mass_fraction"] == pytest.approx(0.1)
    assert first["composition_wt_pct"] != result["composition_wt_pct"]
    assert same["composition_wt_pct"] == result["composition_wt_pct"]
    assert result["linear_temperature_diagnostic"]["difference_from_step_hold_wt_pct"] == pytest.approx({"FeO": 0, "MgO": 0})
    reasons = [n.reason for n in result["notices"]]
    assert any("SC-360" in r and "3 dex (W) / 2.5 dex (Mo)" in r and "pO2^-nu" in r for r in reasons)
    assert any("printed step temperatures held" in r for r in reasons)
    assert {r["source"] for r in result["oxygen_sources"]} == {"d142_closure"}


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_printed_oxygen_precedes_selector(extent_point, monkeypatch):
    from simulator.battery import source_oxygen
    reference, experiment, rows = extent_point
    pressure = Located(State.of(Value.point_of("0.00001")), reference.locator)
    reference = replace(reference, point_conditions={"fO2_Pa": pressure})
    rows = tuple(replace(row, point_conditions={"fO2_Pa": pressure}) for row in rows)
    def must_not_resolve(*args):
        raise AssertionError("printed condition must bypass selector")
    monkeypatch.setattr(source_oxygen, "resolve_source_oxygen", must_not_resolve)
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert {r["source"] for r in result["oxygen_sources"]} == {"source_printed"}
    assert not any("SC-360" in n.reason for n in result["notices"])


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_cell_ratio_precedes_flagged_closure(extent_point, monkeypatch):
    from simulator.battery import source_oxygen
    from simulator.battery.records import Derivation
    reference, experiment, rows = extent_point
    oxygen = replace(reference, value=Value.point_of("0.00001"),
                     derivation=Derivation("source_oxygen_condition; cell-metal activity as assumed by the source", (), (), "Pa"))
    monkeypatch.setattr(source_oxygen, "resolve_source_oxygen",
                        lambda *a: source_oxygen.SourceOxygenCondition(oxygen, frozenset({"cell-ratio"})))
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert {r["source"] for r in result["oxygen_sources"]} == {"cell_material_gas_ratio"}
    assert not any("SC-360" in n.reason for n in result["notices"])


def test_step_hold_and_linear_are_distinct_temperature_paths():
    seen = []
    def pressure(n, p, t):
        seen.append(t)
        return {"FeO": 1.0, "O2": 0.0}
    kwargs = dict(temperature_path=((0.1, 2000), (0.2, 2100)),
                  measured_mass_loss_fraction=0.2, near_saturated=True,
                  source_fO2_log_by_interval=(-10.0, -10.0))
    integrate_knudsen_residue_inventory({"FeO": 1.0}, _channels(), pressure, **kwargs)
    assert set(seen) == {2000, 2100}
    seen.clear()
    integrate_knudsen_residue_inventory({"FeO": 1.0}, _channels(), pressure, **kwargs, linear_temperature=True)
    assert min(seen) == 2000
    assert any(2000 < t < 2100 for t in seen)


def test_extent_comparison_preserves_ordinary_identity_guards(extent_point):
    from simulator.battery.enums import IdentityEqualKind
    from simulator.battery.identity import (
        _same_row_residue_extent_identity_equal,
        identity_equal,
    )
    reference, _, _ = extent_point
    identity = reference.identity
    assert identity_equal(identity, identity).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert (
        _same_row_residue_extent_identity_equal(identity, identity).kind
        is IdentityEqualKind.EQUAL
    )
    for field in ("composition", "temperature_K"):
        missing = replace(identity, **{field: State.unknown("missing")})
        assert (
            _same_row_residue_extent_identity_equal(missing, missing).kind
            is IdentityEqualKind.IDENTITY_UNKNOWN
        )
    changed = replace(identity, species=Species("MgO", Phase.L))
    assert (
        _same_row_residue_extent_identity_equal(identity, changed).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )
    changed = replace(identity, temperature_K=State.of(Decimal(2200)))
    assert (
        _same_row_residue_extent_identity_equal(identity, changed).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )
    left = replace(identity, fO2_Pa=State.of(Decimal("1e-5")))
    right = replace(identity, fO2_Pa=State.of(Decimal("1e-6")))
    assert (
        _same_row_residue_extent_identity_equal(left, right).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_compile_residual_produces_extent_numeric_without_time_mass_or_pressure(extent_point):
    from simulator.battery.score import ScoreContext, compile_residual
    reference, experiment, rows = extent_point
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {r.observation_id: r for r in (reference, *rows)})
    residual, candidate = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context)
    assert candidate is not None
    assert residual.numeric is not None, residual.refusal
    assert residual.numeric.operation.value == "absolute"
    assert any("SC-360" in n.reason for n in residual.notices)


@pytest.mark.parametrize("mass_kg", (None, "0.002"))
def test_analytical_knudsen_mass_default_reports_dp008(extent_point, mass_kg):
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import NoticePolicy
    from simulator.battery.residue import _formula_terms
    from simulator.battery.score import ScoreContext, compile_residual

    reference, experiment, rows = extent_point
    if mass_kg is not None:
        experiment = replace(experiment, sample=replace(
            experiment.sample, mass_kg=Located(State.of(Value.point_of(mass_kg)), reference.locator),
        ))
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {row.observation_id: row for row in (reference, *rows)})
    residual, candidate = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context)
    assert residual.numeric is not None and candidate is not None
    notices = tuple(notice for notice in residual.notices if notice.kind is NoticeKind.KNOB_DEFAULTED)
    expected = () if mass_kg is not None else (NoticePolicy("DP-008", 1, "canonical", "0.001", "kg"),)
    assert tuple(notice.policy for notice in notices) == expected
    inventory = candidate.provenance["initial_inventory_mol"]
    assert sum(amount * _formula_terms(oxide)[1] for oxide, amount in inventory.items()) == pytest.approx(
        float(mass_kg or "0.001")
    )


def test_analytical_knudsen_inventory_mass_equals_weighed_charge(extent_point):
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.score import ScoreContext, compile_residual

    reference, experiment, rows = extent_point
    charge_kg = 0.002
    experiment = replace(experiment, sample=replace(
        experiment.sample,
        mass_kg=Located(State.of(Value.point_of(str(charge_kg))), reference.locator),
    ))
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {row.observation_id: row for row in (reference, *rows)})
    residual, candidate = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context)

    assert residual.numeric is not None and candidate is not None
    inventory = candidate.provenance["initial_inventory_mol"]
    inventory_mass_kg = math.fsum(
        amount * float(parse_formula(oxide).molar_mass_kg_per_mol())
        for oxide, amount in inventory.items()
    )
    assert inventory_mass_kg == pytest.approx(charge_kg)


@pytest.mark.parametrize(
    ("printed_resolution", "precision_multiplier"),
    ((True, 1), (False, 1), ("component-exact", 1),
     ("component-approximate", 1), ("component-approximate", 2)),
    ids=("exact-mass-loss", "approximate-mass-loss", "exact-component",
         "approximate-component", "approximate-component-shared-rule-mutation"),
)
def test_microgram_loader_tail_stays_numeric_in_flagged_tier(
    extent_point, monkeypatch, printed_resolution, precision_multiplier,
):
    from simulator.battery import residue, score

    if precision_multiplier != 1:
        precision_rule = residue._trace_tail_precision_threshold
        monkeypatch.setattr(
            residue, "_trace_tail_precision_threshold",
            lambda value, **kwargs: precision_multiplier * precision_rule(value, **kwargs),
        )

    reference, experiment, rows = extent_point
    component_case = isinstance(printed_resolution, str)
    approximate_component = printed_resolution == "component-approximate"
    if approximate_component:
        reference = replace(reference, value=replace(reference.value, approximate=True))
    locator = reference.locator
    composition = Composition(
        "trace", (("FeO", Decimal("0.0005")), ("MgO", Decimal("0.9995"))),
        AmountBasis.MOLE_FRACTION,
    )
    reference = replace(reference, identity=replace(reference.identity, composition=State.of(composition)))
    experiment = replace(experiment, sample=replace(
        experiment.sample,
        mass_kg=Located(State.of(Value.point_of("1e-9")), locator),
        initial_composition=Located(State.of(composition), locator),
    ))
    rows = (*rows[:1], replace(rows[-1], value=replace(
        Value.point_of("0.2" if component_case or printed_resolution else "0.20000"),
        approximate=not printed_resolution if not component_case else False,
    )))
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    monkeypatch.setattr(battery, "_openimcc_gas_channels_and_omission_notices",
                        lambda *a: ([("FeO", ("FeO", 1, 0)), ("Mg", ("MgO", 1, 0)),
                                     ("O2", (None, 1, -1))], ()))
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k:
                        lambda n, p: {"FeO": 1.0, "Mg": 1.0, "O2": 0.0})
    oxygen = Located(State.of(Value.point_of("0.00001")), locator)
    reference = replace(reference, point_conditions={"fO2_Pa": oxygen})
    rows = tuple(replace(row, point_conditions={"fO2_Pa": oxygen}) for row in rows)
    context = score.ScoreContext({}, {experiment.experiment_id: experiment},
                                 {row.observation_id: row for row in (reference, *rows)})
    residual, candidate = score.compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context)
    assert residual.numeric is not None and candidate is not None
    assert not residual.score_eligible
    assert "trace_tail_below_loader_cutoff" in score.flagged_strata(residual.notices)
    tail = candidate.provenance["trace_tail"]
    # FeO accounts for 0.0005 mol/mol with these two formula masses.
    feo_kg = 1e-9 * (0.0005 * 71.844) / (0.0005 * 71.844 + 0.9995 * 40.304)
    assert tail["retained_kg_by_parent"] == pytest.approx({"FeO": feo_kg}, rel=1e-10, abs=0)
    assert tail["fraction_of_measured_mass_loss"] == pytest.approx(feo_kg / 2e-10)
    # Mg escapes as metal. Oxygen stays in the physical residue, while the
    # oxide projection removes one MgO equivalent per Mg atom lost.
    oxide_residue_kg = 1e-9 - 2e-10 * 40.304 / 24.305
    expected_component_bound = 100 * feo_kg / oxide_residue_kg
    assert tail["bound_wt_pct_by_component"] == pytest.approx({
        "FeO": expected_component_bound,
        "MgO": expected_component_bound,
    })
    bound = tail["bound_wt_pct_by_component"]["FeO"]
    assert bound >= float(candidate.value.point)  # removing the FeO tail gives 0 wt% FeO
    assert bound > 100 * feo_kg / 8e-10
    if printed_resolution is True:
        expected_threshold = 0.1 * float(Decimal("0.1")) / 0.2
    elif printed_resolution is False:
        expected_threshold = 1e-6
    else:
        expected_threshold = 0.1 * float(Decimal("0.1")) / 0.2
    expected_threshold *= precision_multiplier
    assert tail["material_threshold"] == expected_threshold
    if component_case:
        expected_component_threshold = (
            99e-6 if approximate_component else 0.1
        ) * precision_multiplier
        assert tail["component_material_threshold_wt_pct"] == pytest.approx(
            expected_component_threshold
        )
        assert tail["fraction_of_measured_mass_loss"] < expected_threshold
        assert bound > expected_component_threshold
    assert candidate.provenance["atom_closure_mol"]["Fe"] == 0.0
    assert candidate.provenance["final_inventory_mol"]["FeO"] == candidate.provenance["initial_inventory_mol"]["FeO"]


def test_approximate_component_tail_uses_relative_effect_threshold():
    from simulator.battery.enums import NoticeKind
    from simulator.battery.residue import _knudsen_trace_tail_notices

    component_bound = 2.2271715e-6
    trace_tail = {
        "fraction_of_measured_mass_loss": 1.0470108e-9,
        "material_threshold": 1e-6,
        "bound_wt_pct_by_component": {"FeO": component_bound, "MgO": component_bound},
        "retained_kg_by_parent": {"FeO": 1e-12},
        "measured_loss_kg": 9.551e-4,
        "residue_oxide_mass_kg": 4.49e-5,
    }
    reference = SimpleNamespace(
        value=SimpleNamespace(point=Decimal("0.08"), approximate=True),
        identity=SimpleNamespace(species=SimpleNamespace(formula="FeO")),
    )

    notices = _knudsen_trace_tail_notices(trace_tail, reference, oxide_wt_pct=True)

    assert trace_tail["fraction_of_measured_mass_loss"] < trace_tail["material_threshold"]
    assert trace_tail["bound_wt_pct_by_component"] == {"FeO": component_bound, "MgO": component_bound}
    assert trace_tail["component_material_threshold_wt_pct"] == pytest.approx(0.08e-6)
    assert component_bound / 0.08 > 1e-6
    assert trace_tail["material"]
    assert notices[0].kind is NoticeKind.ENGINE_MODEL_CAVEAT
    assert notices[0].certification == "material"


def _predict_and_classify_mass_loss_tail(extent_point, monkeypatch, *, approximate, loss_fraction):
    from simulator.battery import residue

    reference, experiment, rows = extent_point
    # Set the trace FeO mass directly through its mole fraction; Mg carries the
    # printed loss while FeO remains below the analytical loader cutoff.
    feo_mass_fraction = loss_fraction * 0.2
    feo_mole_fraction = (
        feo_mass_fraction * 40.304
        / (71.844 * (1.0 - feo_mass_fraction) + feo_mass_fraction * 40.304)
    )
    composition = Composition(
        "trace", (("FeO", Decimal(str(feo_mole_fraction))),
                  ("MgO", Decimal(str(1.0 - feo_mole_fraction)))),
        AmountBasis.MOLE_FRACTION,
    )
    reference = replace(reference, identity=replace(
        reference.identity, composition=State.of(composition),
    ))
    experiment = replace(experiment, sample=replace(
        experiment.sample,
        mass_kg=Located(State.of(Value.point_of("1e-9")), reference.locator),
        initial_composition=Located(State.of(composition), reference.locator),
    ))
    rows = (*rows[:1], replace(rows[-1], value=replace(
        Value.point_of("0.2"), approximate=approximate,
    )))
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    monkeypatch.setattr(battery, "_openimcc_gas_channels_and_omission_notices",
                        lambda *a: ([("FeO", ("FeO", 1, 0)), ("Mg", ("MgO", 1, 0)),
                                     ("O2", (None, 1, -1))], ()))
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k:
                        lambda n, p: {"FeO": 1.0, "Mg": 1.0, "O2": 0.0})
    oxygen = Located(State.of(Value.point_of("0.00001")), reference.locator)
    reference = replace(reference, point_conditions={"fO2_Pa": oxygen})
    rows = tuple(replace(row, point_conditions={"fO2_Pa": oxygen}) for row in rows)
    prediction = predict_knudsen_residue_composition(
        "internal-analytical", reference, experiment, rows,
    )
    tail = prediction["trace_tail"]
    assert tail["fraction_of_measured_mass_loss"] == pytest.approx(loss_fraction, rel=1e-7)
    notices = residue._knudsen_trace_tail_notices(tail, reference, oxide_wt_pct=False)
    return notices, tail


def test_approximate_mass_loss_tail_between_tolerances_is_material(extent_point, monkeypatch):
    notices, tail = _predict_and_classify_mass_loss_tail(
        extent_point, monkeypatch, approximate=True, loss_fraction=2e-6,
    )

    assert tail["material_threshold"] == 1e-6
    assert tail["material"]
    assert notices[0].certification == "material"


def test_exact_component_tail_uses_tenth_of_last_printed_digit():
    from simulator.battery.residue import _knudsen_trace_tail_notices

    trace_tail = {
        "fraction_of_measured_mass_loss": 0.0,
        "material_threshold": 1e-6,
        "bound_wt_pct_by_component": {"FeO": 1e-8},
        "retained_kg_by_parent": {"FeO": 1e-12},
        "measured_loss_kg": 1e-3,
        "residue_oxide_mass_kg": 1e-2,
    }
    reference = SimpleNamespace(
        value=SimpleNamespace(point=Decimal("0.080000"), approximate=False),
        identity=SimpleNamespace(species=SimpleNamespace(formula="FeO")),
    )

    _knudsen_trace_tail_notices(trace_tail, reference, oxide_wt_pct=True)

    assert trace_tail["component_material_threshold_wt_pct"] == pytest.approx(1e-7)
    assert not trace_tail["material"]


def test_exact_mass_loss_anchor_keeps_strict_boundary_arithmetic():
    from simulator.battery import residue
    from simulator.battery.enums import NoticeKind

    value = Value.point_of("0.2")
    divisor = 0.2
    production_threshold = residue._trace_tail_precision_threshold(value, divisor=divisor)
    round_eight_threshold = residue._trace_tail_precision_threshold(value) / divisor
    chosen_tail = Decimal("0.05")

    # Round 8 divided the helper value after computing it. The exact decimal
    # 0.05 lies strictly between that result and the production threshold, so
    # only the old arithmetic classifies this tail as material.
    assert Decimal(str(round_eight_threshold)) < chosen_tail
    assert chosen_tail < Decimal(str(production_threshold))

    def classify(threshold):
        tail = {
            "fraction_of_measured_mass_loss": float(chosen_tail),
            "material_threshold": threshold,
            "retained_kg_by_parent": {"FeO": 1e-12},
            "measured_loss_kg": 1e-3,
            "residue_oxide_mass_kg": 1e-2,
        }
        notices = residue._knudsen_trace_tail_notices(
            tail,
            SimpleNamespace(value=value, identity=SimpleNamespace(
                species=SimpleNamespace(formula="FeO"),
            )),
            oxide_wt_pct=False,
        )
        return tail, notices

    production_tail, production_notices = classify(production_threshold)
    round_eight_tail, round_eight_notices = classify(round_eight_threshold)
    assert not production_tail["material"]
    assert production_notices[0].kind is NoticeKind.INPUT_OMITTED
    assert production_notices[0].certification == "immaterial"
    assert round_eight_tail["material"]
    assert round_eight_notices[0].kind is NoticeKind.ENGINE_MODEL_CAVEAT
    assert round_eight_notices[0].certification == "material"


def test_knudsen_tail_uses_the_real_ledger_loader_cutoff(monkeypatch):
    from simulator import core
    from simulator.battery.residue import _formula_terms

    monkeypatch.setattr(core, "LEDGER_LOADER_SPECIES_CUTOFF_KG", 4e-12)
    charge_kg = {"FeO": 3e-12, "MgO": 1e-9}
    charge = {oxide: mass / _formula_terms(oxide)[1] for oxide, mass in charge_kg.items()}
    admitted = core.PyrolysisSimulator._positive_species_kg(charge_kg)
    assert admitted == {"MgO": 1e-9}
    assert core.PyrolysisSimulator._positive_species_kg(
        {"FeO": core.LEDGER_LOADER_SPECIES_CUTOFF_KG}
    ) == {}
    calls = []

    def pressure(inventory, _logp, _temperature):
        calls.append(inventory)
        return {"MgO": 1.0, "O2": 0.0}

    result = integrate_knudsen_residue_inventory(
        charge, (*_channels(), ResidueChannel("MgO", "MgO", "MgO", 1.0, "effusion")),
        pressure, temperature_path=((0.2, 2000.0),), measured_mass_loss_fraction=0.2,
        near_saturated=True, source_fO2_log_by_interval=(-10.0,),
        analytical_loader_cutoff=True,
    )
    assert all(inventory.keys() == admitted.keys() for inventory in calls)
    assert result.trace_tail_kg == {"FeO": pytest.approx(3e-12, rel=1e-10, abs=0)}
    assert result.residue_mol["FeO"] == charge["FeO"]
    assert result.atom_closure_mol["Fe"] == 0.0


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
@pytest.mark.parametrize("change,reason", (
    ("no_path", "printed_temperature_path_missing_or_invalid"),
    ("no_extent", "measured_mass_loss_fraction_missing_or_invalid"),
    ("no_initial", "initial_composition_not_stated"),
    ("nonmolecular", "knudsen_near_saturation_invalid"),
    ("bad_orifice_kn", "knudsen_near_saturation_invalid"),
    ("bad_printed_pressure", "knudsen_near_saturation_invalid"),
))
def test_prediction_boundary_refuses_missing_inputs(extent_point, change, reason):
    reference, experiment, rows = extent_point
    if change == "no_path":
        experiment = replace(experiment, thermal_schedule=None)
    elif change == "no_extent":
        rows = rows[:1]
    elif change == "no_initial":
        experiment = replace(experiment, sample=replace(experiment.sample, initial_composition=None))
    elif change == "nonmolecular":
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment, regime=FlowRegime(State.of(RegimeClass.VISCOUS))))
    elif change == "bad_orifice_kn":
        experiment = replace(experiment, pressure_environment=replace(
            experiment.pressure_environment, regime=FlowRegime(
                State.unknown("no class"), Located(State.of(Value.point_of(1)), reference.locator))))
    else:
        experiment = replace(experiment, apparatus=Apparatus(geometry=ApparatusGeometry(
            orifice_diameter_m=Located(State.of(Value.point_of("0.001")), reference.locator))))
        reference = replace(reference, point_conditions={
            "total_pressure_Pa": Located(State.of(Value.point_of(10)), reference.locator)})
    with pytest.raises(ResidueInventoryRefusal) as exc:
        predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    assert exc.value.reason == reason


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_printed_oxygen_is_identical_on_shared_trajectory_prefix(extent_point):
    reference, experiment, rows = extent_point
    earlier = replace(reference, observation_id="earlier-residue",
                      identity=replace(reference.identity, temperature_K=State.of(Decimal(2000))),
                      point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.00001")), reference.locator)})
    observations = (*rows, earlier, reference)
    first = predict_knudsen_residue_composition("internal-analytical", earlier, experiment, observations)
    later = predict_knudsen_residue_composition("internal-analytical", reference, experiment, observations)
    assert first["oxygen_sources"][0] == later["oxygen_sources"][0]
    assert later["oxygen_sources"][0]["source"] == "source_printed"
    assert "earlier-residue" in later["oxygen_sources"][0]["used_observations"]


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
def test_conflicting_printed_interval_oxygen_is_flagged(extent_point):
    reference, experiment, rows = extent_point
    conflict = replace(reference, observation_id="other-column",
                       point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.0001")), reference.locator)})
    reference = replace(reference, point_conditions={"fO2_Pa": Located(State.of(Value.point_of("0.00001")), reference.locator)})
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, (*rows, conflict))
    assert result["oxygen_sources"][-1]["source"] == "d142_closure"
    assert any("several_candidates_survive" in n.reason for n in result["notices"])


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
@pytest.mark.parametrize("engine_failure", (False, True))
def test_post_selection_refusal_preserves_oxygen_caveat_and_extent(extent_point, monkeypatch, engine_failure):
    import simulator.battery.residue as residue
    from simulator.battery.residue import ResidueEngineNonconvergence
    from simulator.battery.score import ScoreContext, _knudsen_residue_prediction, compile_residual
    reference, experiment, rows = extent_point
    def fail(*args):
        if engine_failure:
            raise ResidueEngineNonconvergence("internal-analytical", "after oxygen selection")
        raise ResidueInventoryRefusal("residue_pressure_channels_missing", "original channel detail")
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *a, **k: fail)
    context = ScoreContext({}, {experiment.experiment_id: experiment},
                           {r.observation_id: r for r in (*rows, reference)})
    prediction = _knudsen_residue_prediction(context, reference, Engine.INTERNAL_ANALYTICAL)
    detail = prediction.refusal_detail
    assert detail["reason"] == ("engine_nonconvergence" if engine_failure else "residue_pressure_channels_missing")
    assert detail["measured_mass_loss_fraction"] == 0.2
    assert detail["temperature_path"] == [(0.1, 2000), (0.2, 2100)]
    assert all(r["source"] == "d142_closure" for r in detail["oxygen_sources"])
    assert any("SC-360" in n.reason for n in prediction.notices)
    residual, _ = compile_residual(reference, Engine.INTERNAL_ANALYTICAL, context=context, prediction=prediction)
    assert residual.refusal is not None
    assert any("SC-360" in n.reason for n in residual.notices)


def _real_analytical_cmas_pressure(temperature_K, inventory=None, extra_channels=()):
    openimcc = pytest.importorskip("openimcc")
    from simulator.battery.enums import NoticeKind
    from simulator.battery.residue import (
        _make_residue_pressure_model,
        _openimcc_residue_channel_set,
    )
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    pack = openimcc.load_gas_datapack()
    if inventory is None:
        inventory = {"SiO2": 0.44, "Al2O3": 0.05, "FeO": 0.08, "MgO": 0.36, "CaO": 0.07}
    raw_channels, source_omissions = battery._openimcc_gas_channels_and_omission_notices(tuple(inventory), pack)
    assert not source_omissions
    channels, _residue_channels, omissions = _openimcc_residue_channel_set(
        raw_channels,
        source_omissions,
        engine="internal-analytical",
        alpha=1.0,
        alpha_source="Knudsen regression",
    )
    catalog_omissions = {
        omission["channel"]: omission
        for omission in omissions
        if omission["reason"] == "internal_analytical_vapor_pressure_catalog_missing"
    }
    assert set(catalog_omissions) == {"AlSiO", "Si2O2"}
    assert all(
        omission["kind"] == NoticeKind.INPUT_OMITTED.value
        for omission in catalog_omissions.values()
    )
    channels = tuple(channels) + tuple(extra_channels)
    pressure_model = _make_residue_pressure_model(
        "internal-analytical", channels, pack, temperature_K=temperature_K, total_pressure_Pa=0,
        engine_metadata={}, engine_flags=set(), starting_inventory=inventory,
        missing_channels_reason="residue_pressure_channels_missing",
        caller="Knudsen regression",
    )
    return inventory, channels, pressure_model


def _read_markova_residue_cache_in_fresh_process(
    cache_path: str,
    result_path: str,
    temperature_K: float,
    inventory: dict[str, float],
) -> None:
    # This child reads the cache file the parent stored. Same request, same
    # answer. Put the previous value back so the assignment does not leak.
    previous = os.environ.get("REGOLITH_ENGINE_CACHE")
    os.environ["REGOLITH_ENGINE_CACHE"] = cache_path
    try:
        _read_markova_residue_cache(result_path, temperature_K, inventory)
    finally:
        if previous is None:
            os.environ.pop("REGOLITH_ENGINE_CACHE", None)
        else:
            os.environ["REGOLITH_ENGINE_CACHE"] = previous


def _read_markova_residue_cache(
    result_path: str,
    temperature_K: float,
    inventory: dict[str, float],
) -> None:
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )

    def unexpected_solve(**_kwargs):
        raise AssertionError("fresh-process Markova request must hit the disk cache")

    battery._internal_analytical_vapor_pressure_adapter = unexpected_solve
    engine_cache.engine_cache_counters(reset=True)
    result = internal_analytical_residue_vapor_pressure(
        core=battery._new_internal_analytical_core(),
        temperature_C=temperature_K - 273.15,
        pressure_bar=0.0,
        composition_kg=None,
        composition_mol=inventory,
        fO2_log=0.0,
        po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1.0),
        include_diagnostic_shadows=False,
    )
    counts = engine_cache.engine_cache_counters()["internal-analytical"]
    assert counts["hits"] == 1
    assert counts["misses"] == 0
    with open(result_path, "wb") as stream:
        pickle.dump(result, stream)


def test_internal_analytical_pressure_callback_covers_real_channels_and_oxygen():
    from pathlib import Path
    from simulator.chemistry.offgas_fo2 import _oxygen_atom_partial_pressure_bar
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.physical_constants import PA_PER_BAR
    inventory, channels, pressure_model = _real_analytical_cmas_pressure(2000)
    predicted = pressure_model(inventory, -10)
    assert set(predicted) == {str(s) for s, _ in channels}
    response = battery._internal_analytical_vapor_pressure_adapter(
        core=battery._new_internal_analytical_core(), temperature_C=1726.85,
        pressure_bar=0, composition_kg=None, composition_mol=inventory, fO2_log=-10,
        po2_request=battery.Po2Request(mode=battery.PO2_COMMANDED, po2_bar=1e-10),
        include_diagnostic_shadows=False,
    )
    # Expected names come from the existing species resolver, not a test alias table.
    for channel, value in response.vapor_pressures_Pa.items():
        formula = battery._gas_formula_for_oxygen_balance(channel)
        if formula in predicted:
            assert predicted[formula] == pytest.approx(value)
    assert predicted["O2"] == pytest.approx(1e-5)
    assert predicted["O"] == pytest.approx(_oxygen_atom_partial_pressure_bar(
        2000, 1e-10, extract_path=Path(__file__).resolve().parents[2] / "data/literature/extracts/nasa-cea-thermo.yaml",
    ) * PA_PER_BAR)


@pytest.mark.parametrize("failure_kind", ("domain", "extract"))
def test_oxygen_projection_failure_has_typed_pressure_reason(monkeypatch, failure_kind):
    from simulator.battery.residue import _make_residue_pressure_model
    from simulator.chemistry import offgas_fo2
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.vapour_rail.nasa_cea import NasaCeaError
    monkeypatch.setattr(battery, "_new_internal_analytical_core", lambda: object())
    monkeypatch.setattr(battery, "_internal_analytical_vapor_pressure_adapter",
                        lambda **kwargs: SimpleNamespace(
                            vapor_pressures_Pa={}, diagnostics={}
                        ))
    def fail(*args, **kwargs):
        raise (NasaCeaError("CEA domain") if failure_kind == "domain" else OSError("CEA extract"))
    monkeypatch.setattr(offgas_fo2, "_oxygen_atom_partial_pressure_bar", fail)
    pressure = _make_residue_pressure_model(
        "internal-analytical", [("O", (None, 1, -0.5)), ("O2", (None, 1, -1))], None,
        temperature_K=2000, total_pressure_Pa=0, engine_metadata={}, engine_flags=set(),
        starting_inventory={"FeO": 1.0},
        missing_channels_reason="residue_pressure_channels_missing", caller="oxygen failure regression",
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        pressure({"FeO": 1.0}, -10)
    assert exc.value.reason == "residue_oxygen_pressure_unavailable"


@pytest.mark.parametrize("oxygen_parent", (None, ""), ids=("none-parent", "empty-parent"))
@pytest.mark.parametrize("initial_feo", (0.01, 1e-10), ids=("bulk-iron", "trace-iron"))
def test_closure_continues_after_parent_exhaustion_with_atom_and_mass_closure(oxygen_parent, initial_feo):
    from simulator.accounting.formulas import parse_formula
    channels = tuple(ResidueChannel(s, s, p, 1.0, "stoichiometric packets")
                     for s, p in (("Fe", "FeO"), ("Mg", "MgO"),
                                  ("O", oxygen_parent), ("O2", oxygen_parent)))
    iron_active = []

    def pressure(n, p, temperature):
        assert n["MgO"] > 0.0
        iron_active.append(n.get("FeO", 0.0) > 0.0)
        return {"Fe": 1e-8 * 10**(-p / 2) if n.get("FeO", 0) > 0 else 0.0,
                "Mg": 1e-10 * 10**(-p / 2) if n.get("MgO", 0) > 0 else 0.0,
                "O": 1e-6 * 10**(p / 2), "O2": 1e5 * 10**p}
    initial = {"FeO": initial_feo, "MgO": 1.0}
    result = integrate_knudsen_residue_inventory(
        initial, channels, pressure, temperature_path=((0.2, 2000),),
        measured_mass_loss_fraction=0.2, near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    # After Fe exhaustion all remaining condensed mass is MgO.
    # Rejected solver stages may evaluate both sides of the event.
    assert iron_active[0] and not iron_active[-1]
    assert result.residue_mol["FeO"] == 0.0
    assert result.evaporated_mol["Fe"] == pytest.approx(initial_feo, rel=1e-5, abs=1e-15)
    expected_mgo = 0.8 * (initial_feo * 0.071844 + 0.040304) / 0.040304
    assert result.evaporated_mol["O"] > 0.0
    assert result.evaporated_mol["O2"] > 0.0
    assert result.residue_mol["MgO"] == pytest.approx(expected_mgo, abs=1e-10)
    assert result.retained_oxygen_atoms_mol == pytest.approx(expected_mgo, abs=1e-10)
    assert max(abs(v) for v in result.atom_closure_mol.values()) < 1e-12
    def mass(amounts):
        return sum(n * float(parse_formula(s).molar_mass_kg_per_mol()) for s, n in amounts.items())
    assert mass(result.evaporated_mol) / mass(initial) == pytest.approx(0.2, abs=1e-10)


def test_knudsen_extent_fires_secondary_parent_exhaustion_event(monkeypatch):
    from scipy import integrate as scipy_integrate

    real_solve_ivp = scipy_integrate.solve_ivp
    event_hits_by_solve = []

    def record_solve_ivp(*args, **kwargs):
        solution = real_solve_ivp(*args, **kwargs)
        event_hits_by_solve.append(
            tuple(tuple(float(hit) for hit in hits) for hits in solution.t_events)
        )
        return solution

    monkeypatch.setattr(scipy_integrate, "solve_ivp", record_solve_ivp)
    result = integrate_knudsen_residue_inventory(
        {"Al2O3": 1.0, "SiO2": 0.01},
        (
            ResidueChannel(
                "AlSiO", "AlSiO", "Al2O3", 1.0, "mixed-parent event",
                parent_oxide_stoich={"Al2O3": 0.5, "SiO2": 1.0},
            ),
            ResidueChannel("O2", "O2", None, 1.0, "oxygen extent"),
        ),
        lambda _inventory, _logp, _temperature: {"AlSiO": 1.0e6, "O2": 1.0},
        temperature_path=((0.02, 2000.0),),
        measured_mass_loss_fraction=0.02,
        near_saturated=True,
        source_fO2_log_by_interval=(-10.0,),
    )

    # Parents follow inventory order, so event index 1 is secondary SiO2.
    assert any(len(events) > 1 and events[1] for events in event_hits_by_solve)
    assert result.residue_mol["SiO2"] == 0.0
    assert result.residue_mol["Al2O3"] > 0.0


@pytest.mark.parametrize("pressures,reason", (
    ({"O2": 0.0}, "channel_missing"),
    ({"FeO": float("nan"), "O2": 0.0}, "residue_pressure_invalid"),
    ({"FeO": -1.0, "O2": 0.0}, "residue_pressure_invalid"),
))
def test_active_parent_pressure_is_still_required_and_validated(pressures, reason):
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, _channels(), lambda *args: pressures,
            temperature_path=((0.2, 2000),), measured_mass_loss_fraction=0.2,
            near_saturated=True, source_fO2_log_by_interval=(-10,),
        )
    assert exc.value.reason == reason


def test_real_analytical_closure_and_extent_carry_redox_diagnostic():
    from simulator.battery.residue import _channel_terms, _residue_oxygen_balance
    inventory, gas_channels, pressure = _real_analytical_cmas_pressure(1905.15)
    channels = tuple(ResidueChannel(s, s, parent, 1.0, "real analytical closure")
                     for s, (parent, _, _) in gas_channels)
    pressure_at = _preserve_pressure_contract(
        pressure, lambda p: pressure(inventory, p),
    )
    balance = _residue_oxygen_balance(
        _channel_terms(channels), inventory, pressure_at, 1905.15,
    )
    iron = [row for row in balance.pressure_law_diagnostics if row.species == "Fe"]
    assert len(iron) == 1
    assert iron[0].reason == "residue_pressure_law_redox_active"
    assert iron[0].declared == -0.5
    integrate_pressure = _preserve_pressure_contract(
        pressure, lambda n, p, temperature: pressure(n, p),
    )
    result = integrate_knudsen_residue_inventory(
        inventory, channels, integrate_pressure,
        temperature_path=((0.01, 1905.15),), measured_mass_loss_fraction=0.01,
        near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    assert any(
        row.species == "Fe" and row.reason == "residue_pressure_law_redox_active"
        for row in result.pressure_law_diagnostics
    )
    endpoint_pressure_at = _preserve_pressure_contract(
        pressure, lambda p: pressure(result.residue_mol, p),
    )
    endpoint_balance = _residue_oxygen_balance(
        _channel_terms(channels), result.residue_mol,
        endpoint_pressure_at, 1905.15,
    )
    endpoint_iron = [
        row for row in endpoint_balance.pressure_law_diagnostics
        if row.species == "Fe"
    ]
    extent_iron = [
        row for row in result.pressure_law_diagnostics
        if row.species == "Fe"
    ]
    assert len(endpoint_iron) == len(extent_iron) == 1
    assert extent_iron[0] == endpoint_iron[0]
    assert max(abs(value) for value in result.atom_closure_mol.values()) < 1.0e-9


@pytest.mark.parametrize("oxygen_parent", (None, ""))
@pytest.mark.parametrize("oxygen_species", ("O", "O2"))
@pytest.mark.parametrize("invalid", (None, float("nan"), -1.0))
def test_parentless_oxygen_pressure_is_required_and_validated(oxygen_parent, oxygen_species, invalid):
    channels = (ResidueChannel("FeO", "FeO", "FeO", 1.0, "pressure guard"),
                ResidueChannel("O", "O", oxygen_parent, 1.0, "pressure guard"),
                ResidueChannel("O2", "O2", oxygen_parent, 1.0, "pressure guard"))
    pressures = {"FeO": 1.0, "O": 0.0, "O2": 0.0}
    if invalid is None:
        del pressures[oxygen_species]
    else:
        pressures[oxygen_species] = invalid
    with pytest.raises(ResidueInventoryRefusal) as exc:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0}, channels, lambda *args: pressures,
            temperature_path=((0.05, 2000),), measured_mass_loss_fraction=0.05,
            near_saturated=True, source_fO2_log_by_interval=(-10,),
        )
    assert exc.value.reason == ("channel_missing" if invalid is None else "residue_pressure_invalid")
    assert exc.value.detail == oxygen_species


@pytest.mark.parametrize("temperature_K,extent,wt", (
    (1748.15, 0.0146, {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}),
    (1749.15, 0.0117, {"SiO2": 52.32, "Al2O3": 19.13, "FeO": 9.37, "MgO": 6.25, "CaO": 12.93}),
))
def test_real_markova_first_interval_zeroes_omitted_al_and_flags_redox(temperature_K, extent, wt):
    """Al stays a below-threshold omission. Iron's exponent mismatch is now a redox diagnostic."""
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.residue import _channel_terms, _residue_oxygen_balance
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, gas_channels, pressure = _real_analytical_cmas_pressure(temperature_K, inventory)
    channels = tuple(ResidueChannel(s, s, parent, 1.0, "printed Markova first interval")
                     for s, (parent, _, _) in gas_channels)
    pressure_at = _preserve_pressure_contract(
        pressure, lambda p: pressure(inventory, p),
    )
    balance = _residue_oxygen_balance(
        _channel_terms(channels), inventory, pressure_at, temperature_K,
    )
    iron = [row for row in balance.pressure_law_diagnostics if row.species == "Fe"]
    assert len(iron) == 1
    assert iron[0].reason == "residue_pressure_law_redox_active"
    assert iron[0].declared == -0.5
    al_notices = [
        notice for notice in pressure.below_threshold_omission_notices
        if notice.reason.startswith("Al ")
    ]
    assert len(al_notices) == 1
    assert "below_reporting_threshold_omissions" in al_notices[0].reason
    integrate_pressure = _preserve_pressure_contract(
        pressure, lambda n, p, t: pressure(n, p),
    )
    result = integrate_knudsen_residue_inventory(
        inventory, channels, integrate_pressure,
        temperature_path=((extent, temperature_K),), measured_mass_loss_fraction=extent,
        near_saturated=True, source_fO2_log_by_interval=(None,),
    )
    assert any(
        row.species == "Fe" and row.reason == "residue_pressure_law_redox_active"
        for row in result.pressure_law_diagnostics
    )
    endpoint_pressure_at = _preserve_pressure_contract(
        pressure, lambda p: pressure(result.residue_mol, p),
    )
    endpoint_balance = _residue_oxygen_balance(
        _channel_terms(channels), result.residue_mol,
        endpoint_pressure_at, temperature_K,
    )
    endpoint_iron = [
        row for row in endpoint_balance.pressure_law_diagnostics
        if row.species == "Fe"
    ]
    extent_iron = [
        row for row in result.pressure_law_diagnostics
        if row.species == "Fe"
    ]
    assert len(endpoint_iron) == len(extent_iron) == 1
    assert extent_iron[0] == endpoint_iron[0]
    assert max(abs(value) for value in result.atom_closure_mol.values()) < 1.0e-9


@pytest.mark.parametrize("temperature_K,wt", (
    (1748.15, {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}),
    (1749.15, {"SiO2": 52.32, "Al2O3": 19.13, "FeO": 9.37, "MgO": 6.25, "CaO": 12.93}),
))
@pytest.mark.engine_cache_off
def test_markova_bracket_top_callback_zeroes_recorded_al_omission(
    temperature_K,
    wt,
    tmp_path,
    monkeypatch,
):
    """A legacy hit lacks Fe speciation. The current cold and warm paths retain it."""
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.enums import NoticeKind
    from simulator import engine_cache
    from simulator.diagnostic_helpers import binary_pot_battery as battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_COMMANDED,
        Po2Request,
        internal_analytical_residue_vapor_pressure,
    )
    from simulator.fe_redox import _engine_fe_speciation
    from simulator.melt_backend.base import equilibrium_to_hot

    # First call is the live solve. The file below is this test's own cache.
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, _channels, pressure = _real_analytical_cmas_pressure(temperature_K, inventory)
    predicted = pressure(inventory, 0.0)
    residue_request = {
        "core": battery._new_internal_analytical_core(),
        "temperature_C": temperature_K - 273.15,
        "pressure_bar": 0.0,
        "composition_kg": None,
        "composition_mol": inventory,
        "fO2_log": 0.0,
        "po2_request": Po2Request(mode=PO2_COMMANDED, po2_bar=1.0),
        "include_diagnostic_shadows": False,
    }
    response = internal_analytical_residue_vapor_pressure(**residue_request)
    database = tmp_path / "markova-engine-cache.sqlite"
    database.unlink(missing_ok=True)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    engine_name = "internal-analytical"
    legacy_request = {
        "call": "residue_pressure",
        "temperature_C": residue_request["temperature_C"],
        "pressure_bar": residue_request["pressure_bar"],
        "composition_kg": residue_request["composition_kg"],
        "composition_mol": inventory,
        "po2": residue_request["po2_request"].as_payload(),
        "engine_settings": battery._internal_analytical_cache_settings(residue_request["core"]),
    }
    legacy_fields = {name: value for name, value in vars(response).items() if name != "fe_redox_split"}
    with monkeypatch.context() as legacy:
        legacy.setitem(engine_cache.ENGINE_CHEMISTRY_VERSION, engine_name, 3)
        engine_cache.cached_engine_call(
            engine_name, legacy_request,
            lambda: equilibrium_to_hot(legacy_fields, engine_id=engine_name),
        )
        old_warm = internal_analytical_residue_vapor_pressure(**residue_request)
        assert _engine_fe_speciation(old_warm) == (None, None)
        assert old_warm.vapor_pressures_Pa == response.vapor_pressures_Pa

    engine_cache.engine_cache_counters(reset=True)
    cold = internal_analytical_residue_vapor_pressure(**residue_request)
    counts = engine_cache.engine_cache_counters(reset=True)["internal-analytical"]
    assert counts["misses"] == counts["stores"] == 1
    assert counts["hits"] == counts["read_error"] == counts["store_error"] == 0
    assert database.exists()

    result_path = tmp_path / "markova-warm-result.pkl"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_read_markova_residue_cache_in_fresh_process,
        args=(str(database), str(result_path), temperature_K, inventory),
    )
    process.start()
    process.join(timeout=180)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("fresh-process Markova cache hit timed out")
    assert process.exitcode == 0
    with result_path.open("rb") as stream:
        warm = pickle.load(stream)

    from simulator.melt_backend.base import equilibrium_cache_value

    consumed = [equilibrium_cache_value(vars(result), engine_id="internal-analytical") for result in (response, cold, warm)]
    assert consumed[0] == consumed[1] == consumed[2]
    assert response.vapor_pressures_Pa == cold.vapor_pressures_Pa == warm.vapor_pressures_Pa
    fe_speciation = _engine_fe_speciation(response)
    assert None not in fe_speciation
    assert _engine_fe_speciation(cold) == _engine_fe_speciation(warm) == fe_speciation
    omissions = response.diagnostics["vapor_pressure_diagnostic"]["below_reporting_threshold_omissions"]
    record = omissions["Al"]
    assert "Al" not in response.vapor_pressures_Pa
    assert record["species"] == "Al"
    assert record["computed_pressure_Pa"] <= record["reporting_threshold_Pa"]
    assert predicted["Al"] == 0.0
    translated = pressure.below_threshold_omission_records["Al"]
    assert translated["computed_pressure_Pa"] == record["computed_pressure_Pa"]
    assert translated["reporting_threshold_Pa"] == record["reporting_threshold_Pa"]
    assert translated["field"] == record["field"]
    assert translated["pressure_rail"] == record["pressure_rail"]
    assert translated["provenance"] == (
        "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
    )
    notices = [
        notice for notice in pressure.below_threshold_omission_notices
        if notice.reason.startswith("Al ")
    ]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.kind is NoticeKind.INPUT_OMITTED
    assert f"{record['reporting_threshold_Pa']!r}" in notice.reason
    assert f"{record['computed_pressure_Pa']!r}" in notice.reason
    assert record["field"] in notice.reason
    assert record["pressure_rail"] in notice.reason
    assert "BuiltinVaporPressureProvider.below_reporting_threshold_omissions" in notice.reason


def test_real_callback_refuses_channel_the_provider_did_not_omit():
    """A required key with no omission record is still a missing channel."""
    from simulator.accounting.formulas import parse_formula
    wt = {"SiO2": 44.02, "Al2O3": 4.56, "FeO": 8.04, "MgO": 36.44, "CaO": 6.95}
    inventory = {s: w / 100 / float(parse_formula(s).molar_mass_kg_per_mol()) for s, w in wt.items()}
    inventory, _channels, pressure = _real_analytical_cmas_pressure(
        1748.15, inventory, extra_channels=(("Zr", (None, 1, 0)),),
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        pressure(inventory, 0.0)
    assert exc.value.reason == "residue_pressure_channels_missing"
    assert exc.value.detail == "Zr"
    assert "Zr" not in pressure.below_threshold_omission_records
    assert "Al" in pressure.below_threshold_omission_records


def test_analytical_callback_at_2000k_returns_finite_al():
    """Passing real callback: Al is reported, so there is no omission record."""
    inventory, _channels, pressure = _real_analytical_cmas_pressure(2000)
    predicted = pressure(inventory, -10)
    assert "Al" in predicted
    assert math.isfinite(predicted["Al"])
    assert predicted["Al"] > 0.0
    assert pressure.below_threshold_omission_records == {}
    assert pressure.below_threshold_omission_notices == []
