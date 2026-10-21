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
            pO2_bar_by_step=(None,), atom_closure_mol={"Mg": 0.0, "O": 0.0},
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


@pytest.fixture
def prescribed_pressure_engine(monkeypatch, fig3_source):
    import sys
    from types import ModuleType
    import simulator.battery.residue as residue
    from simulator.diagnostic_helpers import binary_pot_battery

    state = {"unsourced_ca": False, "pressures": []}
    package = ModuleType("openimcc")
    package.load_gas_datapack = lambda: object()
    monkeypatch.setitem(sys.modules, "openimcc", package)

    def gas_channels(*_):
        channels = [("Mg", ("MgO", 1.0, -0.5)), ("O2", (None, 0.0, 1.0))]
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
                    "Ca": 0.000001 * factor / p**0.5}
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
    assert float(prediction.value) == pytest.approx(0.013952813675583747, rel=1e-3)
    notices = [notice.reason for notice in prediction.notices]
    bound = json.loads(next(note.removeprefix("mass_loss_bound:") for note in notices
                            if note.startswith("mass_loss_bound:")))
    assert 0 < bound["bound_fraction"] <= 0.01
    assert bound["preheat_bound_kg"] > 0
    assert bound["uncertainty_clause"] == "bulk mass-loss uncertainty not stated by source"
    assert any("approximate printed duration" in note for note in notices)
    assert any(note.startswith("alpha_extrapolated:") for note in notices)
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
