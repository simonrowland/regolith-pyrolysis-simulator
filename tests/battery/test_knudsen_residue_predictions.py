"""Knudsen extent anchors and a pre-refactor Langmuir numeric pin."""

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


def test_langmuir_pressure_refactor_numeric_pin():
    result = integrate_residue_inventory(
        {"FeO": 1.0, "MgO": 2.0},
        (
            ResidueChannel("Fe", "Fe", "FeO", 1.0, "pin"),
            ResidueChannel("Mg", "Mg", "MgO", 1.0, "pin"),
            ResidueChannel("O2", "O2", None, 1.0, "pin"),
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


def _channels(gas="FeO"):
    return (ResidueChannel(gas, gas, "FeO", 1.0, "equilibrium effusion"),
            ResidueChannel("O2", "O2", None, 1.0, "equilibrium effusion"))


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


def test_each_row_uses_own_extent_and_never_measured_composition(extent_point):
    reference, experiment, rows = extent_point
    result = predict_knudsen_residue_composition("internal-analytical", reference, experiment, rows)
    earlier = replace(reference, identity=replace(reference.identity, temperature_K=State.of(Decimal(2000))))
    first = predict_knudsen_residue_composition("internal-analytical", earlier, experiment, rows)
    changed = replace(reference, value=Value.point_of(1), point_conditions={"composition": Located(State.unknown("must not read"))})
    same = predict_knudsen_residue_composition("internal-analytical", changed, experiment, rows)
    assert result["escaped_mass_fraction"] == pytest.approx(0.2)
    assert first["escaped_mass_fraction"] == pytest.approx(0.1)
    assert first["composition_wt_pct"] != result["composition_wt_pct"]
    assert same["composition_wt_pct"] == result["composition_wt_pct"]
    assert result["linear_temperature_diagnostic"]["difference_from_step_hold_wt_pct"] == pytest.approx({"FeO": 0, "MgO": 0})
    reasons = [n.reason for n in result["notices"]]
    assert any("SC-360" in r and "3 dex (W) / 2.5 dex (Mo)" in r and "pO2^-nu" in r for r in reasons)
    assert any("printed step temperatures held" in r for r in reasons)
    assert {r["source"] for r in result["oxygen_sources"]} == {"d142_closure"}


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
    from simulator.battery.identity import identity_equal, _same_row_knudsen_residue_identity_equal
    reference, _, _ = extent_point
    identity = reference.identity
    assert identity_equal(identity, identity).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert _same_row_knudsen_residue_identity_equal(identity, identity).kind is IdentityEqualKind.EQUAL
    for field in ("composition", "temperature_K"):
        missing = replace(identity, **{field: State.unknown("missing")})
        assert _same_row_knudsen_residue_identity_equal(missing, missing).kind is IdentityEqualKind.IDENTITY_UNKNOWN
    changed = replace(identity, species=Species("MgO", Phase.L))
    assert _same_row_knudsen_residue_identity_equal(identity, changed).kind is IdentityEqualKind.IDENTITY_MISMATCH
    changed = replace(identity, temperature_K=State.of(Decimal(2200)))
    assert _same_row_knudsen_residue_identity_equal(identity, changed).kind is IdentityEqualKind.IDENTITY_MISMATCH
    left = replace(identity, fO2_Pa=State.of(Decimal("1e-5")))
    right = replace(identity, fO2_Pa=State.of(Decimal("1e-6")))
    assert _same_row_knudsen_residue_identity_equal(left, right).kind is IdentityEqualKind.IDENTITY_MISMATCH


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
