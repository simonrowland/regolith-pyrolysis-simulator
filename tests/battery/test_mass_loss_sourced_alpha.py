from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

import simulator.battery.score as score_module
from simulator.battery.enums import Engine, Quantity, RefusalReason
from simulator.battery.migrate import LITERATURE, Migrator
from simulator.battery.residue import ResidueInventoryRefusal, _mass_loss_sourced_alphas
from simulator.battery.score import predict_with_engine, quantity_token


@pytest.fixture
def fig3_source():
    # Exact executable fields of 04e03ba08's Fig. 3 context, restricted to R-11.
    # The source domain includes the six Richter 2002 H2 runs at 1500 C;
    # its local applicability list covers vacuum runs and excludes R-7.
    document = {"source_id": "kems-010-richter-2007", "species": {}}
    for formula, gas, prefix, energy, sigma in (
        ("Mg", "Mg(g)", 143.0, 121000.0, 53000.0),
        ("SiO", "SiO(g)", 25.3, 92000.0, 37000.0),
    ):
        document["species"][formula] = {"context": [{
            "observation_id": f"richter_2007_{formula.lower()}_cai_langmuir_alpha_arrhenius",
            "type": "best_fit_model_context",
            "locator": {
                "source_path": "docs/references/pdfs/99-kems-langmuir/kems-010-richter-2007.pdf",
                "published_page": 5553,
                "figure": "3",
            },
            "T_range_K": [1773.15, 2173.15],
            "applies_to_experiments": ["cai-r-11"],
            "units": "dimensionless",
            "uncertainty_convention": "2_sigma",
            "values": {"gas_species": gas, "alpha_form": {
                "type": "arrhenius", "gamma_o": prefix, "c0": prefix,
                "E_J_per_mol": energy, "E_uncertainty_J_per_mol": sigma,
                "R_J_per_mol_K": 8.314,
            }},
        }]}
    document["species"]["Mg"]["observations"] = [{
        "equipment": {"sample_surface_area": {
            "value": [{"sample": "R-11", "diameter_mm": 6.0,
                       "initial_area_mm2": 91.3, "final_area_mm2": 80.0}],
            "uncertainty": {"one_mm_percent": 20.0, "larger_samples_percent": 10.0,
                            "locator": {"published_page": 5550, "table": "1"}},
        }},
    }]
    return document


def test_fig3_alphas_use_printed_fit_and_locator(fig3_source):
    experiment = SimpleNamespace(experiment_id="work::experiment::cai-r-11")
    fits = _mass_loss_sourced_alphas(experiment, fig3_source, 2073.15)
    # Independent 50-digit Decimal evaluation of the printed fits, not engine output.
    assert fits["Mg"]["alpha"] == pytest.approx(0.12780152611500154, rel=1e-13)
    assert fits["SiO"]["alpha"] == pytest.approx(0.12162550464734534, rel=1e-13)
    assert fits["Mg"]["locator"]["figure"] == "3"
    assert fits["Mg"]["T_range_K"] == [1773.15, 2173.15]
    assert not fits["Mg"]["extrapolated"]


@pytest.mark.parametrize("temperature,extrapolated", [(1773.15, False), (2473.0, True)])
def test_fit_domain_includes_1500C_and_labels_extrapolation(
    fig3_source, temperature, extrapolated
):
    experiment = SimpleNamespace(experiment_id="work::experiment::cai-r-11")
    fits = _mass_loss_sourced_alphas(experiment, fig3_source, temperature)
    assert all(fit["extrapolated"] is extrapolated for fit in fits.values())


@pytest.mark.parametrize("local_id,document_present", [("cai-r-7", True), ("cai-r-11", False)])
def test_absent_or_inapplicable_fits_keep_d204(fig3_source, local_id, document_present):
    experiment = SimpleNamespace(experiment_id=f"work::experiment::{local_id}")
    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_sourced_alphas(
            experiment, fig3_source if document_present else {}, 2073.15
        )
    assert failure.value.reason == "evaporation_coefficients_not_sourced"



def test_shrinking_area_numeric_pin_before_shared_formula_move(monkeypatch):
    import simulator.battery.residue as residue

    areas = []

    def half_inventory(inventory, _channels, _pressure, **kwargs):
        areas.append(kwargs["area_evolution_m2"][0])
        removed = inventory["MgO"] / 2.0
        return residue.ResidueInventoryResult(
            residue_mol={"MgO": removed},
            evaporated_mol={"Mg": removed, "O2": removed / 2.0},
            pO2_bar_by_step=(None,),
            pO2_balance_bracket_boundary_by_step=(None,),
            pO2_balance_floor_steps=0,
            pO2_balance_floor_first_step=None,
            pO2_balance_floor_time_fraction=0.0,
            pO2_balance_floor_time_s=0.0,
            pO2_balance_floor_pO2_bar=None,
            atom_closure_mol={"Mg": 0.0, "O": 0.0},
        )

    monkeypatch.setattr(residue, "integrate_residue_inventory", half_inventory)
    residue._hashimoto_integrate_geometry(
        {"MgO": 1.0}, (), lambda *_: {}, temperature_K=2073.15,
        duration_s=2.0, steps=2, geometry_policy_id="sphere_shrinking",
        initial_area_m2=0.0000913,
    )
    assert areas == pytest.approx([0.0000913, 0.00005751539592770096], rel=1e-14)


def test_vacuum_oxygen_root_pin_before_flux_refactor():
    from simulator.battery.oxygen_balance import _VacuumOxygenChannel, _solve_vacuum_oxygen_balance

    channels = (
        _VacuumOxygenChannel("Mg", 0.024305, 0.0, 1.0, -0.5, 0.5, "synthetic anchor"),
        _VacuumOxygenChannel("O2", 0.0319988, 2.0, 0.0, 1.0, 1.0, "oxygen coproduct"),
    )
    result = _solve_vacuum_oxygen_balance(
        lambda logp: {"Mg": 0.001 / (10.0**logp)**0.5, "O2": 100000.0 * 10.0**logp},
        channels, temperature_K=2073.15,
    )
    # Analytic oxygen stoichiometry: 2*J_O2=J_Mg. Independent 50-digit
    # evaluation gives this pO2 for the declared power-law pressures/masses.
    assert result.pO2_bar == pytest.approx(0.0000020188591846109878, rel=1e-10)


@pytest.mark.parametrize("with_sio,expected_po2,expected_mg,expected_sio", [
    (False, 8.131046268682460e-7, 2.762438138455805e-7, 0.0),
    (True, 1.462909125212295e-6, 2.059477816151990e-7, 2.910603362049900e-7),
], ids=["mg-only", "mg-and-sio"])
def test_sourced_channels_close_oxygen_with_declared_unity_escape(
    fig3_source, with_sio, expected_po2, expected_mg, expected_sio,
):
    from simulator.battery.residue import ResidueChannel, integrate_residue_inventory

    fits = _mass_loss_sourced_alphas(
        SimpleNamespace(experiment_id="work::experiment::cai-r-11"), fig3_source, 2073.15
    )
    channels = [
        ResidueChannel("Mg", "Mg", "MgO", fits["Mg"]["alpha"], "Richter Fig. 3"),
        ResidueChannel("O2", "O2", None, 1.0, "declared unity oxygen escape"),
    ]
    if with_sio:
        channels.append(ResidueChannel(
            "SiO", "SiO", "SiO2", fits["SiO"]["alpha"], "Richter Fig. 3"
        ))
    result = integrate_residue_inventory(
        {"MgO": 1.0, "SiO2": 1.0}, channels,
        lambda _inventory, logp: {
            "Mg": 0.001 / (10.0**logp)**0.5,
            "SiO": 0.002 / (10.0**logp)**0.5,
            "O2": 100000.0 * 10.0**logp,
        },
        temperature_K=2073.15, duration_s=1.0, area_evolution_m2=(0.0001,),
    )
    # Independent 70-digit Decimal solution of O conservation with the printed
    # Fig. 3 alphas and CIAAW masses: 2*J_O2=J_Mg+J_SiO. One shared pO2,
    # then the analytic one-second depletion of each one-mol parent at 1 cm2.
    assert result.pO2_bar_by_step == pytest.approx((expected_po2,), rel=1e-10, abs=0.0)
    assert result.evaporated_mol["Mg"] == pytest.approx(expected_mg, rel=1e-10, abs=0.0)
    assert result.evaporated_mol.get("SiO", 0.0) == pytest.approx(expected_sio, rel=1e-10, abs=0.0)
    assert result.evaporated_mol["O2"] == pytest.approx(
        (expected_mg + expected_sio) / 2.0, rel=1e-10, abs=0.0
    )
    assert max(abs(value) for value in result.atom_closure_mol.values()) < 1e-14
    # Binary pins captured at 64a59b4f5 before sharing the residue-channel DTO
    # construction; the independent physical expectations above remain in force.
    assert result.pO2_bar_by_step[0].hex() == (
        "0x1.88b2597f2ed39p-20" if with_sio else "0x1.b4883fbd20146p-21"
    )
    assert result.evaporated_mol["Mg"].hex() == (
        "0x1.ba44fd84636a8p-23" if with_sio else "0x1.289d524472f78p-22"
    )


def test_r11_supplier_free_corner_has_zero_oxygen_limit(monkeypatch):
    import simulator.battery.residue as residue
    from simulator.battery.residue import (
        ResidueChannel, _mass_loss_corner_bounds, integrate_residue_inventory,
    )

    # R-11 at its first ramp midpoint, 1183.15 K; real OpenIMCC 66ec4a3d8
    # composition/pressure snapshot. The supplier-free corner has neutral MgO active.
    inventory = {"MgO": 0.0005423805580240438, "SiO2": 0.0014578626113198004,
                 "Al2O3": 0.0003621246012321325, "CaO": 0.0007850791320550105}
    channels = (
        ResidueChannel("MgO", "MgO", "MgO", 1.0, "supplier-free oxygen corner"),
        ResidueChannel("O", "O", None, 1.0, "declared unity oxygen escape"),
        ResidueChannel("O2", "O2", None, 1.0, "declared unity oxygen escape"),
    )

    def pressure(_inventory, logp):
        return {"MgO": 1.8527138089578604e-16,
                "O": 0.00174657993789618 * (10.0**logp)**0.5,
                "O2": 100000.0 * 10.0**logp}

    balances = []
    original = residue._solve_vacuum_oxygen_balance

    def record_balance(*args, **kwargs):
        balance = original(*args, **kwargs)
        balances.append(balance)
        return balance

    monkeypatch.setattr(residue, "_solve_vacuum_oxygen_balance", record_balance)
    result = integrate_residue_inventory(
        inventory, channels, pressure, temperature_K=1183.15,
        duration_s=60.0, area_evolution_m2=(9.13e-5,),
    )
    assert result.pO2_bar_by_step == (0.0,)
    # Independent HKL flux; all oxygen leaves in MgO(g). The existing flux
    # provider threshold suppresses this tiny rate, so the inventory stays put.
    assert balances[0].channel_fluxes_mol_m2_s["MgO"] == pytest.approx(
        3.711994573534765e-18, rel=1e-10, abs=0.0
    )
    assert balances[0].channel_fluxes_mol_m2_s["O"] == 0.0
    assert balances[0].channel_fluxes_mol_m2_s["O2"] == 0.0
    assert result.evaporated_mol == {}
    assert result.residue_mol == inventory
    assert result.buffer_oxygen_exchange_mol == 0.0
    assert max(abs(value) for value in result.atom_closure_mol.values()) < 1e-14
    bound, corners = _mass_loss_corner_bounds(
        inventory, channels, pressure, unsourced=("MgO",), temperature_K=1183.15,
        duration_s=60.0, area_m2=9.13e-5,
    )
    assert bound["MgO"] == pytest.approx(8.1955388006018e-22, rel=1e-10, abs=0.0)
    assert corners["pO2_low_bar"] == corners["pO2_high_bar"] == 0.0

    def invalid_pressure(inventory, logp):
        return dict(pressure(inventory, logp), MgO=1.8527138089578604e-16 * 10.0**logp)

    with pytest.raises(ResidueInventoryRefusal, match="exponent mismatch"):
        integrate_residue_inventory(
            inventory, channels, invalid_pressure, temperature_K=1183.15,
            duration_s=60.0, area_evolution_m2=(9.13e-5,),
        )

    oxygen_rich_channels = (
        ResidueChannel("AlO2", "AlO2", "Al2O3", 1.0, "supplier-free oxygen corner"),
        *channels[1:],
    )

    def oxygen_rich_pressure(inventory, logp):
        return dict(pressure(inventory, logp), AlO2=3.9402330720917226e-20 * (10.0**logp)**0.25)

    # A supplier-free consumer corner has zero flux. Its upper bound is
    # evaluated at the opposite, oxygen-supplying corner, not at this root.
    consumer = integrate_residue_inventory(
        inventory, oxygen_rich_channels, oxygen_rich_pressure, temperature_K=1183.15,
        duration_s=60.0, area_evolution_m2=(9.13e-5,),
    )
    assert consumer.pO2_bar_by_step == (0.0,)
    assert consumer.evaporated_mol == {}


@pytest.mark.parametrize("kind,expected_low,expected_high,expected_bounds", [
    ("consumer", 2.018796086752308e-6, 2.018842360013321e-6,
     {"AlO2": 2.404377673527403e-12}),
    ("releaser", 2.018842360013321e-6, 2.418878686949343e-6,
     {"Ca": 1.198086440164288e-8}),
    ("both", 2.018796086752308e-6, 2.418878686949343e-6,
     {"Ca": 1.198100170884603e-8, "AlO2": 2.515536402267817e-12}),
    ("supplier-free", 0.0, 9.276958156134232e-7,
     {"Ca": 0.056077, "AlO2": 1.979604734527376e-12}),
    ("supplier-free-consumer", 0.0, 0.0, {"AlO2": 0.0}),
], ids=["consumer", "releaser", "both", "supplier-free", "supplier-free-consumer"])
def test_mass_loss_bounds_use_each_channels_worst_oxygen_corner(
    kind, expected_low, expected_high, expected_bounds,
):
    from simulator.battery.residue import ResidueChannel, _mass_loss_corner_bounds

    channels = [ResidueChannel("Mg", "Mg", "MgO", 0.0 if kind.startswith("supplier-free") else 0.5, "declared fixture"),
                ResidueChannel("O2", "O2", None, 1.0, "declared unity escape")]
    if kind not in {"consumer", "supplier-free-consumer"}:
        channels.append(ResidueChannel("Ca", "Ca", "CaO", 0.0, "unsourced"))
    if kind != "releaser":
        channels.append(ResidueChannel("AlO2", "AlO2", "Al2O3", 0.0, "unsourced"))
    bounds, corners = _mass_loss_corner_bounds(
        {"MgO": 1.0, "CaO": 1.0, "Al2O3": 1.0}, channels,
        lambda _inventory, logp: {
            "Mg": 0.001 / (10.0**logp)**0.5,
            "Ca": 0.0002 / (10.0**logp)**0.5,
            "AlO2": 0.001 * (10.0**logp)**0.25,
            "O2": 100000.0 * 10.0**logp,
        },
        unsourced=tuple(expected_bounds), temperature_K=2073.15,
        area_m2=0.0001, duration_s=1.0,
    )
    # Independent 70-digit solution: x=pO2/bar; multiplying the low-corner
    # equation by sqrt(x) gives a quadratic in x**(3/4). The high corner is
    # the Mg+Ca cubic in sqrt(x). HKL at the maximizing root gives each
    # channel's own parent-oxide kg, including its signed O coproduct once.
    assert corners["pO2_low_bar"] <= corners["pO2_high_bar"]
    assert corners["pO2_low_bar"] == pytest.approx(expected_low, rel=1e-10, abs=0.0)
    assert corners["pO2_high_bar"] == pytest.approx(expected_high, rel=1e-10, abs=0.0)
    assert bounds == pytest.approx(expected_bounds, rel=1e-10, abs=0.0)
    if kind == "supplier-free":
        assert corners["inventory_capped_channels"] == ["Ca"]


@pytest.fixture
def r11_inputs():
    from simulator.battery.records import (
        Located, Locator, State, ThermalRamp, ThermalSchedule,
        ThermalSetpoint, Value,
    )
    from simulator.battery.enums import ValueKind

    migrator = Migrator()
    migrator._migrate_extract(LITERATURE / "extracts/kems-010-richter-2007.yaml")
    row = next(
        row for row in migrator.result.observations.values()
        if quantity_token(row.identity) is Quantity.MASS_LOSS_FRACTION
        and "row=r-11:" in row.observation_id
    )
    experiment = migrator.result.experiments[row.experiment_id]
    locator = Locator(published_page=5548, section="3.1")

    def point(value):
        return Located(State.of(Value(ValueKind.POINT, point=Decimal(str(value)))), locator)

    # Source schedule at 04e03ba08, including the explicitly approximate preheat.
    schedule = ThermalSchedule(
        method=Located(State.of("The furnace was preheated to 900 C in about an hour"), locator),
        ramps=(
            ThermalRamp(point("0.3333333333333333333333333333"), point("1173.15"), point("1673.15")),
            ThermalRamp(point("0.3333333333333333333333333333"), point("1673.15"), point("2073.15")),
        ),
        setpoints_and_holds=(ThermalSetpoint(point("1673.15"), point("1200")),),
        total_duration_s=point("5400"),
    )
    return row, replace(experiment, thermal_schedule=schedule)


def test_r11_area_shape_and_documented_schedule(fig3_source, r11_inputs):
    from simulator.battery.residue import _mass_loss_area_inputs, _mass_loss_thermal_segments

    row, experiment = r11_inputs
    assert _mass_loss_area_inputs(experiment, row, fig3_source) == pytest.approx(
        (0.0000913, 0.0000800, 0.10)
    )
    maximum, duration, segments = _mass_loss_thermal_segments(experiment, 2073.15)
    assert (maximum, duration) == (1173.15, 3600.0)
    assert sum(seconds for _, seconds in segments) == pytest.approx(9300.0)
    assert (1673.15, 1200.0) in segments
    assert segments[-1] == (2073.15, 5400.0)


def test_area_outside_printed_uncertainty_refuses(fig3_source, r11_inputs):
    from simulator.battery.residue import _mass_loss_area_inputs

    row, experiment = r11_inputs
    fig3_source["species"]["Mg"]["observations"][0]["equipment"]["sample_surface_area"]["value"][0]["final_area_mm2"] = 50
    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_area_inputs(experiment, row, fig3_source)
    assert failure.value.reason == "area_shape_not_self_similar"


@pytest.mark.parametrize("uncertainty", [None, 0.001])
def test_bound_above_one_percent_refuses_and_names_channels(uncertainty):
    from simulator.battery.residue import _mass_loss_bound_gate

    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_bound_gate(0.011, 1.0, uncertainty, channels=("Ca",), preheat_bound_kg=0.001)
    assert failure.value.reason == "alpha_not_sourced"
    detail = json.loads(failure.value.detail)
    assert detail["bound_fraction"] == 0.011
    assert detail["unsourced_channels"] == ["Ca"]


def test_missing_bulk_uncertainty_drops_only_that_clause():
    from simulator.battery.residue import _mass_loss_bound_gate

    result = _mass_loss_bound_gate(0.01, 1.0, None, channels=("Al",), preheat_bound_kg=0)
    assert result["bound_fraction"] == 0.01
    assert result["uncertainty_clause"] == "bulk mass-loss uncertainty not stated by source"
    with pytest.raises(ResidueInventoryRefusal):
        _mass_loss_bound_gate(0.005, 1.0, 0.004, channels=("Al",), preheat_bound_kg=0)


def test_r11_hand_computed_hkl_and_shrinking_area(fig3_source, r11_inputs):
    from simulator.battery.residue import (
        ResidueChannel, _formula_terms, _hashimoto_integrate_geometry,
        _initial_oxide_wt_percent, _mass_loss_area_inputs,
    )

    row, experiment = r11_inputs
    m0 = 0.0001904
    wt = _initial_oxide_wt_percent(experiment.sample)
    inventory = {oxide: m0 * amount / sum(wt.values()) / _formula_terms(oxide)[1]
                 for oxide, amount in wt.items()}
    alpha = _mass_loss_sourced_alphas(experiment, fig3_source, 2073.15)["Mg"]["alpha"]
    area, _, _ = _mass_loss_area_inputs(experiment, row, fig3_source)
    channels = (
        ResidueChannel("Mg", "Mg", "MgO", alpha, "Richter Fig. 3"),
        ResidueChannel("O2", "O2", None, 1.0, "stoichiometric coproduct"),
    )
    result = _hashimoto_integrate_geometry(
        inventory, channels,
        lambda _inventory, logp: {"Mg": 0.001 / (10.0**logp)**0.5, "O2": 100000.0 * 10.0**logp},
        temperature_K=2073.15, duration_s=60.0, steps=256,
        geometry_policy_id="sphere_shrinking", initial_area_m2=area,
        background_pressure_Pa=0.00013332236842105263,
    )
    loss = result.evaporated_mol["Mg"] * _formula_terms("MgO")[1] / m0
    # Independent law anchor: a prescribed equilibrium-pressure fixture (not
    # a claim about OpenIMCC) gives q=0.00011126757518159585 kg/(m2 s).
    # Integrating dm/dt=-q*A0*(m/m0)^(2/3) for 60 s gives the pinned fraction.
    # 256 finite inventory steps approach this continuous-law limit.
    assert loss == pytest.approx(0.0031978654903853137, rel=1e-4)
    assert result.atom_closure["Mg"] == pytest.approx(0.0, abs=1e-14)
    assert result.atom_closure["O"] == pytest.approx(0.0, abs=1e-14)


@pytest.fixture(params=[None, ""], ids=["none-parent", "openimcc-empty-parent"])
def prescribed_pressure_engine(monkeypatch, fig3_source, request):
    import sys
    from types import ModuleType
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery

    state = {"unsourced_ca": False, "ca_coefficient": 0.000001, "pressures": []}
    package = ModuleType("openimcc")
    package.load_gas_datapack = lambda: object()
    monkeypatch.setitem(sys.modules, "openimcc", package)

    def gas_channels(*_):
        channels = [("Mg", ("MgO", 1.0, -0.5)), ("O2", (request.param, 0.0, 1.0))]
        if state["unsourced_ca"]:
            channels.insert(1, ("Ca", ("CaO", 1.0, -0.5)))
        return tuple(channels), ()

    def pressure_model(_engine, _channels, _pack, **kwargs):
        at = kwargs["temperature_K"]
        state["pressures"].append(kwargs["total_pressure_Pa"])

        def pressure(_inventory, logp):
            p = 10.0**logp
            factor = (at / 2073.15)**20
            return {"Mg": 0.00001 * factor / p**0.5, "O2": 100000.0 * p,
                    "Ca": state["ca_coefficient"] * factor / p**0.5}
        return pressure

    original = score_module.load_yaml
    monkeypatch.setattr(score_module, "load_yaml", lambda path: (
        deepcopy(fig3_source) if path.name == "kems-010-richter-2007.yaml" else original(path)
    ))
    monkeypatch.setattr(binary_pot_battery, "_openimcc_gas_channels_and_omission_notices", gas_channels)
    monkeypatch.setattr(residue, "_make_residue_pressure_model", pressure_model)
    return state


def test_sourced_r11_integrates_schedule_and_exposes_bounds(
    r11_inputs, prescribed_pressure_engine,
):
    row, experiment = r11_inputs
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.value is not None, prediction.refusal_detail
    # Independent continuous-law integration of the prescribed pressure fixture,
    # the printed alpha, the two source ramps/holds and shrinking area. Its
    # 0.1% tolerance covers the documented 60 s finite inventory intervals.
    assert float(prediction.value) == pytest.approx(0.013952648580481508, rel=1e-3)
    notices = [notice.reason for notice in prediction.notices]
    bound = json.loads(next(note.removeprefix("mass_loss_bound:") for note in notices
                            if note.startswith("mass_loss_bound:")))
    assert 0 < bound["bound_fraction"] <= 0.01
    assert bound["preheat_bound_kg"] > 0
    assert bound["point_loss_kg"] == pytest.approx(float(prediction.value) * 0.0001904)
    assert bound["bound_kg"] == bound["preheat_bound_kg"]
    # The fixture's vacuum joint diagnostic exceeds 1%; it cannot gate this
    # healthy sourced point whose preheat envelope is below the threshold.
    assert (bound["joint_arm_loss_kg_non_gating"] - bound["point_loss_kg"]
            > 0.01 * bound["point_loss_kg"])
    assert bound["uncertainty_clause"] == "bulk mass-loss uncertainty not stated by source"
    assert any("approximate printed duration" in note and "excluded from point loss" in note
               for note in notices)
    assert any(note.startswith("alpha_extrapolated:") for note in notices)
    oxygen_note = next(note for note in notices
                       if note.startswith("mass_loss_oxygen_escape_assumption:"))
    assert "alpha_O=1.0; alpha_O2=1.0" in oxygen_note
    assert "not independently sourced" in oxygen_note
    assert "sensitivity:" in oxygen_note
    assert all(p == pytest.approx(0.00013332236842105263)
               for p in prescribed_pressure_engine["pressures"])


def test_full_prediction_refuses_non_negligible_unsourced_channel(
    r11_inputs, prescribed_pressure_engine,
):
    row, experiment = r11_inputs
    prescribed_pressure_engine["unsourced_ca"] = True
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.UNDERDETERMINED_APPARATUS
    assert prediction.refusal_detail["reason"] == "alpha_not_sourced"
    detail = json.loads(prediction.refusal_detail["detail"])
    assert detail["bound_fraction"] > 0.01
    assert detail["unsourced_channels"] == ["Ca"]


@pytest.mark.parametrize("gases", [("Ca",), ("Ca", "Ca2")],
                         ids=["single-gas", "shared-parent-gases"])
def test_inventory_ceiling_is_shared_across_steps_segments_preheat_and_gases(
    gases, prescribed_pressure_engine, fig3_source, r11_inputs, monkeypatch,
):
    import simulator.battery.residue as residue
    from simulator.accounting.formulas import parse_formula
    from simulator.battery.enums import ValueKind
    from simulator.battery.records import State, Value
    from simulator.diagnostic_helpers import binary_pot_battery

    row, experiment = r11_inputs
    # One mol of CaO, with its mass from the production formula resolver,
    # independently of the corner helper or its per-step substitutions.
    ceiling = float(parse_formula("CaO").molar_mass_kg_per_mol())
    mass = replace(experiment.sample.mass_kg, state=State.of(
        Value(ValueKind.POINT, point=Decimal(str(ceiling)))
    ))
    experiment = replace(experiment, sample=replace(experiment.sample, mass_kg=mass))
    channels = [(name, ("CaO", 1 if name == "Ca" else 2, -0.5 if name == "Ca" else -1.0))
                for name in gases] + [("O2", (None, 0.0, 1.0))]
    monkeypatch.setattr(binary_pot_battery, "_openimcc_gas_channels_and_omission_notices",
                        lambda *_: (channels, ()))
    monkeypatch.setattr(residue, "_initial_oxide_wt_percent", lambda _: {"CaO": 100.0})
    monkeypatch.setattr(residue, "_mass_loss_thermal_segments", lambda *_: (
        1173.15, 120.0, ((2073.15, 120.0), (2073.15, 120.0)),
    ))
    # Positive pressures place the high root inside the existing bracket but
    # below the provider's flux threshold, so all three trajectories retain
    # the parent. Both gases diverge at the supplier-free maximizing corner.
    monkeypatch.setattr(residue, "_make_residue_pressure_model", lambda *_, **__: (
        lambda _inventory, logp: {"Ca": 1e-25 / (10.0**logp)**0.5,
                                  "Ca2": 1e-40 / 10.0**logp, "O2": 1e5 * 10.0**logp}
    ))
    integrations = []
    original = residue._hashimoto_integrate_geometry

    def integrate(*args, **kwargs):
        if "on_step" in kwargs:
            integrations.append((kwargs["geometry_policy_id"], kwargs["steps"], kwargs["duration_s"]))
        result = original(*args, **kwargs)
        assert result.inventory_mol == {"CaO": 1.0}
        assert result.evaporated_mol == {}
        return result

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", integrate)
    with pytest.raises(ResidueInventoryRefusal) as failure:
        residue.predict_mass_loss_fraction(
            "openimcc", experiment, temperature_K=2073.15,
            source_document=fig3_source, observation=row,
        )
    assert failure.value.reason == "alpha_not_sourced"
    bound = json.loads(failure.value.detail)
    assert integrations == [("sphere_constant", 2, 120.0),
                            ("sphere_shrinking", 2, 120.0), ("sphere_shrinking", 2, 120.0)]
    assert bound["bound_kg"] == pytest.approx(ceiling, rel=1e-14, abs=0.0)
    assert bound["inventory_capped_channels"] == list(gases)
    cap = bound["inventory_bound_kg_by_parent"]["CaO"]
    assert cap["uncapped_bound_kg"] == pytest.approx(6 * len(gases) * ceiling, rel=1e-14, abs=0.0)
    assert cap["ceiling_kg"] == cap["applied_bound_kg"] == ceiling
    assert 0.0 < bound["preheat_bound_kg"] < ceiling
    assert bound["bound_kg"] == pytest.approx(
        sum(bound["unsourced_bound_kg_by_channel"].values()) + bound["preheat_bound_kg"],
        rel=1e-14, abs=0.0,
    )


def test_real_r11_corner_bounds_reach_gate(fig3_source, r11_inputs, monkeypatch):
    pytest.importorskip("openimcc")
    row, experiment = r11_inputs
    original = score_module.load_yaml
    monkeypatch.setattr(score_module, "load_yaml", lambda path: (
        deepcopy(fig3_source) if path.name == "kems-010-richter-2007.yaml" else original(path)
    ))
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.refusal_detail["reason"] == "alpha_not_sourced", prediction.refusal_detail
    bound = json.loads(prediction.refusal_detail["detail"])
    assert bound["point_loss_kg"] > 0.0
    assert bound["bound_fraction"] > 0.01
    assert bound["corner_ordering_verified"]
    assert bound["corner_steps"] > 0
    assert bound["unsourced_bound_kg_by_channel"]["AlO2"] > 0.0
    assert bound["inventory_bound_kg_by_parent"] == {}
    assert bound["preheat_bound_kg"] > 0.0
    assert bound["joint_arm_loss_kg_non_gating"] is not None


def test_pressure_ceiling_uses_net_flux_and_preserves_equilibrium_guard(fig3_source):
    from simulator.battery.oxygen_balance import _VacuumOxygenChannel, _solve_vacuum_oxygen_balance

    alpha = _mass_loss_sourced_alphas(
        SimpleNamespace(experiment_id="work::experiment::cai-r-11"), fig3_source, 2073.15
    )["Mg"]["alpha"]
    channels = (
        _VacuumOxygenChannel("Mg", 0.024305, 0.0, 1.0, -0.5, alpha, "Richter Fig. 3"),
        _VacuumOxygenChannel("O2", 0.031998, 2.0, 0.0, 1.0, 1.0, "coproduct"),
    )
    result = _solve_vacuum_oxygen_balance(
        lambda logp: {"Mg": 0.001 / (10.0**logp)**0.5, "O2": 100000.0 * 10.0**logp},
        channels, temperature_K=2073.15, background_pressure_Pa=0.00013332236842105263,
    )
    # Independent cubic obtained from 2*J_O2=J_Mg with the same finite
    # bulk-pressure ceiling on each channel; equilibrium Peq stays unmodified.
    assert result.pO2_bar == pytest.approx(8.139284836170614e-7, rel=1e-10)
    assert 2 * result.channel_fluxes_mol_m2_s["O2"] == pytest.approx(
        result.channel_fluxes_mol_m2_s["Mg"], rel=1e-9
    )
    with pytest.raises(ValueError, match="exponent mismatch"):
        _solve_vacuum_oxygen_balance(
            lambda logp: {"Mg": 0.001 / (10.0**logp)**0.4, "O2": 100000.0 * 10.0**logp},
            channels, temperature_K=2073.15,
            background_pressure_Pa=0.00013332236842105263,
        )


def test_non_negligible_preheat_is_not_mislabelled_missing_alpha():
    from simulator.battery.residue import _mass_loss_bound_gate

    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_bound_gate(0.02, 1.0, None, channels=(), preheat_bound_kg=0.02)
    assert failure.value.reason == "preheat_bound_not_negligible"


def test_stated_bulk_uncertainty_refuses_even_below_one_percent(
    r11_inputs, prescribed_pressure_engine,
):
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty

    row, experiment = r11_inputs
    # Synthetic uncertainty tests the stated branch; Richter itself states none.
    row = replace(row, uncertainty=Uncertainty(
        kind=UncertaintyKind.PRINTED, verbatim="synthetic absolute fraction 1e-9",
        value=Decimal("1e-9"), basis="absolute_fraction_of_initial_mass",
    ))
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "preheat_bound_not_negligible"
    detail = json.loads(prediction.refusal_detail["detail"])
    assert detail["fraction_clause_passed"] is True
    assert detail["uncertainty_clause_passed"] is False


@pytest.mark.parametrize("form,alpha_hex,sigma_hex", [
    ({"gamma_o": 143, "E_J_per_mol": 121000, "R_J_per_mol_K": 8.314,
      "E_uncertainty_J_per_mol": 53000},
     ("0x1.eec3fbf5ff535p-5", "0x1.b818c322ef810p-4", "0x1.698b064c7fb81p-3"),
     ("0x1.a4f3c23ed36b9p-3", "0x1.5aad7e22815d5p-2", "0x1.0923fc8a275e8p-1")),
    ({"c0": 25.3, "E_J_per_mol": 92000},
     ("0x1.19d9db6ff80cbp-4", "0x1.b4be9afd749d5p-4", "0x1.3e87259e3b9b5p-3"), None),
    ({"A": 0.52, "B_K": 1000, "gamma_o": 143, "E_J_per_mol": 121000},
     ("0x1.38367f4e2cfeep-2", "0x1.44d18a45bd852p-2", "0x1.501774da11cc7p-2"), None),
])
def test_reproduction_arrhenius_numeric_pin_before_owner_move(form, alpha_hex, sigma_hex):
    from simulator.diagnostic_helpers.extract_reproduction import _literature_alpha_points

    # Golden binary outputs captured before moving the existing evaluator.
    points, reason, drops = _literature_alpha_points(SimpleNamespace(
        values={"alpha_form": form}, T_range_K=(1873.15, 2173.15),
    ))
    assert reason is None and drops == []
    assert [p["T_K"] for p in points] == [1873.15, 2023.15, 2173.15]
    for i, point in enumerate(points):
        assert point["alpha"] == float.fromhex(alpha_hex[i])
        assert point["sigma"] == (None if sigma_hex is None else float.fromhex(sigma_hex[i]))
        assert point["T_provenance"] == "alpha_form_synthetic"
        assert point["series_name"] == "alpha_form"


def test_small_unsourced_bound_passes_without_entering_point_loss(
    r11_inputs, prescribed_pressure_engine,
):
    row, experiment = r11_inputs
    sourced_only = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    prescribed_pressure_engine.update(unsourced_ca=True, ca_coefficient=1e-10)
    small_bound = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert sourced_only.value is not None, sourced_only.refusal_detail
    assert small_bound.value is not None, small_bound.refusal_detail
    assert small_bound.value == sourced_only.value
    bound = json.loads(next(n.reason.removeprefix("mass_loss_bound:") for n in small_bound.notices
                            if n.reason.startswith("mass_loss_bound:")))
    assert bound["point_loss_kg"] == pytest.approx(float(small_bound.value) * 0.0001904)
    assert bound["unsourced_channels"] == ["Ca"]
    ca_bound = bound["unsourced_bound_kg_by_channel"]["Ca"]
    assert ca_bound > 0.0
    assert bound["corner_ordering_verified"]
    assert bound["corner_steps"] > 0
    assert bound["joint_arm_loss_kg_non_gating"] is not None
    assert bound["bound_kg"] == pytest.approx(ca_bound + bound["preheat_bound_kg"])
    assert bound["bound_fraction"] == pytest.approx(bound["bound_kg"] / bound["point_loss_kg"])
    assert 0 < bound["bound_fraction"] <= 0.01


def test_preheat_rectangle_changes_only_bound_not_point_loss(
    r11_inputs, prescribed_pressure_engine, monkeypatch,
):
    import simulator.battery.residue as residue

    row, experiment = r11_inputs
    original = residue._mass_loss_thermal_segments
    first = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)

    def shorter_bound(*args):
        maximum, _duration, segments = original(*args)
        return maximum, 60.0, segments

    # Perturb only the bound duration; the documented point schedule is identical.
    monkeypatch.setattr(residue, "_mass_loss_thermal_segments", shorter_bound)
    shorter = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment)
    assert first.value is not None, first.refusal_detail
    assert shorter.value is not None, shorter.refusal_detail
    assert first.value == shorter.value
    bounds = [json.loads(next(n.reason.removeprefix("mass_loss_bound:") for n in p.notices
                             if n.reason.startswith("mass_loss_bound:"))) for p in (first, shorter)]
    assert bounds[0]["point_loss_kg"] == bounds[1]["point_loss_kg"]
    assert bounds[0]["preheat_bound_kg"] == pytest.approx(60 * bounds[1]["preheat_bound_kg"])
    assert bounds[0]["bound_kg"] > bounds[1]["bound_kg"]


def test_positive_bound_with_zero_point_loss_refuses():
    from simulator.battery.residue import _mass_loss_bound_gate

    with pytest.raises(ResidueInventoryRefusal) as failure:
        _mass_loss_bound_gate(1e-9, 0.0, None, channels=("Ca",), preheat_bound_kg=0)
    detail = json.loads(failure.value.detail)
    assert failure.value.reason == "alpha_not_sourced"
    assert detail["point_loss_kg"] == 0.0
    assert detail["bound_fraction"] is None
    assert detail["fraction_clause_passed"] is False
    assert _mass_loss_bound_gate(0, 0, None, channels=(), preheat_bound_kg=0)["bound_fraction"] == 0


def test_mass_loss_keeps_provider_recorded_omission_on_produced_and_refused_predictions(
    monkeypatch, fig3_source, r11_inputs,
):
    """A proved below-threshold channel stays on the mass-loss result and refusal."""
    import sys
    from types import ModuleType

    import simulator.battery.residue as residue
    from simulator.battery.enums import NoticeKind
    from simulator.diagnostic_helpers import binary_pot_battery

    row, experiment = r11_inputs
    temperature_K = float(score_module.temperature_of(row.identity))
    package = ModuleType("openimcc")
    package.load_gas_datapack = lambda: object()
    monkeypatch.setitem(sys.modules, "openimcc", package)
    original_yaml = score_module.load_yaml
    monkeypatch.setattr(score_module, "load_yaml", lambda path: (
        deepcopy(fig3_source) if path.name == "kems-010-richter-2007.yaml" else original_yaml(path)
    ))
    segment_temperatures = (1173.15, 1673.15, 2073.15)
    monkeypatch.setattr(residue, "_mass_loss_thermal_segments", lambda *_args: (
        segment_temperatures[0], 1.0e-3,
        tuple((temperature, 60.0) for temperature in segment_temperatures[1:]),
    ))
    monkeypatch.setattr(binary_pot_battery, "_new_internal_analytical_core", lambda: object())
    state = {"missing": ()}

    def adapter(**kwargs):
        logp = float(kwargs["fO2_log"])
        computed = 1e-30 * float(kwargs["temperature_C"])
        return SimpleNamespace(
            vapor_pressures_Pa={"Mg": 0.001 / (10.0**logp)**0.5},
            diagnostics={
                "vapor_pressure_diagnostic": {
                    "below_reporting_threshold_omissions": {
                        "Al": {
                            "species": "Al",
                            "reporting_threshold_Pa": 1e-15,
                            "computed_pressure_Pa": computed,
                            "field": "vapor_pressure_Pa",
                            "pressure_rail": "metal",
                        },
                    },
                },
            },
        )

    def gas_channels(*_args):
        channels = [
            ("Mg", ("MgO", 1.0, -0.5)),
            ("Al", ("Al2O3", 0.5, -0.75)),
            ("O2", (None, 0.0, 1.0)),
        ]
        channels.extend(state["missing"])
        return tuple(channels), ()

    monkeypatch.setattr(
        binary_pot_battery, "_internal_analytical_vapor_pressure_adapter", adapter,
    )
    monkeypatch.setattr(
        binary_pot_battery, "_openimcc_gas_channels_and_omission_notices", gas_channels,
    )

    def assert_omission(notice, computed):
        assert notice.kind is NoticeKind.INPUT_OMITTED
        assert notice.affected_quantities == (Quantity.MASS_LOSS_FRACTION,)
        assert notice.origin == "Knudsen residue pressure adapter"
        assert notice.reason.startswith("Al ")
        assert "1e-15" in notice.reason
        assert f"{computed!r}" in notice.reason
        assert "vapor_pressure_Pa" in notice.reason
        assert "metal" in notice.reason
        assert (
            "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
            in notice.reason
        )

    expected_computed = {
        1e-30 * (temperature - 273.15) for temperature in segment_temperatures
    }
    result = residue.predict_mass_loss_fraction(
        "internal-analytical", experiment, temperature_K=temperature_K,
        source_document=fig3_source, observation=row,
    )
    payload = json.loads(result.engine_metadata["below_threshold_omissions"])
    recorded = {
        record["computed_pressure_Pa"] for record in payload["records"]["Al"]
    }
    assert recorded == expected_computed
    assert payload["records"]["Al"][0]["species"] == "Al"
    assert payload["records"]["Al"][0]["reporting_threshold_Pa"] == 1e-15
    assert payload["records"]["Al"][0]["provenance"] == (
        "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
    )
    produced = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, row, experiment=experiment,
    )
    assert produced.value is not None, produced.refusal_detail
    produced_omissions = [
        notice for notice in produced.notices
        if notice.kind is NoticeKind.INPUT_OMITTED
    ]
    assert {
        computed for computed in expected_computed
        for notice in produced_omissions
        if f"{computed!r}" in notice.reason
    } == expected_computed
    for notice in produced_omissions:
        matched = next(
            computed for computed in expected_computed
            if f"{computed!r}" in notice.reason
        )
        assert_omission(notice, matched)

    state["missing"] = (("Zr", ("ZrO2", 1.0, -1.0)),)
    with pytest.raises(ResidueInventoryRefusal) as failure:
        residue.predict_mass_loss_fraction(
            "internal-analytical", experiment, temperature_K=temperature_K,
            source_document=fig3_source, observation=row,
        )
    assert failure.value.reason == "residue_pressure_channels_missing"
    assert failure.value.detail == "Zr"
    preheat_computed = 1e-30 * (segment_temperatures[0] - 273.15)
    assert len(failure.value.notices) == 1
    assert_omission(failure.value.notices[0], preheat_computed)
    refused_payload = json.loads(failure.value.below_threshold_omissions)
    assert refused_payload["records"]["Al"][0]["computed_pressure_Pa"] == preheat_computed
    assert refused_payload["records"]["Al"][0]["provenance"] == (
        "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
    )
    refused = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, row, experiment=experiment,
    )
    assert refused.value is None
    assert refused.refusal_detail["reason"] == "residue_pressure_channels_missing"
    assert refused.refusal_detail["detail"] == "Zr"
    refused_omissions = [
        notice for notice in refused.notices
        if notice.kind is NoticeKind.INPUT_OMITTED
    ]
    assert len(refused_omissions) == 1
    assert_omission(refused_omissions[0], preheat_computed)
    scored_payload = json.loads(refused.refusal_detail["below_threshold_omissions"])
    assert scored_payload["records"]["Al"][0]["species"] == "Al"
    assert scored_payload["records"]["Al"][0]["reporting_threshold_Pa"] == 1e-15
    assert scored_payload["records"]["Al"][0]["computed_pressure_Pa"] == preheat_computed
    assert scored_payload["records"]["Al"][0]["provenance"] == (
        "BuiltinVaporPressureProvider.below_reporting_threshold_omissions"
    )
