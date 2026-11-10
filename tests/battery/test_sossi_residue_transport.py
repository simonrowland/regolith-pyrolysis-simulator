from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.residue import _sossi_transport_conditions


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"


def _source_experiment(run_id: str):
    source = yaml.safe_load(SOURCE.read_text())
    experiment = next(
        row
        for row in source["experiments"]
        if row["experiment_id"] == f"sossi-2019-run-{run_id}"
    )
    bench = next(row for row in source["benches"] if row["id"] == experiment["bench_id"])
    dimensions = next(
        fact["value"]["state"]["value"]["categorical"]
        for fact in bench["other_facts"]
        if fact["name"] == "furnace_tube_dimensions"
    )
    return {**experiment, "_bench_furnace_tube_dimensions": dimensions}


def test_sossi_series_conditions_read_primary_table_and_shared_geometry():
    experiment = _source_experiment("2m-15-07-16g")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["locator"]["page"] == 54
    assert experiment["locator"]["table"] == "2"
    assert conditions == {
        "total_pressure_pa": 101325.0,
        "carrier_gas_mixture": {"CO": 1.8, "CO2": 200.0},
        "flow_rate_sccm": 201.8,
        "pipe_diameter_m": 0.042,
        "transport_length_m": 0.03,
        "transport_assumption_flags": (),
    }

    mutated = {
        **experiment,
        "pressure_environment": {
            **experiment["pressure_environment"],
            "sweep_gas_mixture": {
                "state": {
                    "tag": "value",
                    "value": {"CO": "1.8", "CO2": "400"},
                }
            },
            "flow_rate_sccm": {
                "state": {"tag": "value", "value": {"kind": "point", "point": "401.8"}}
            },
        },
    }
    mutated_conditions = _sossi_transport_conditions(mutated, 101325.0)
    assert mutated_conditions["carrier_gas_mixture"]["CO2"] == 400.0
    assert mutated_conditions["flow_rate_sccm"] == 401.8
    assert mutated_conditions != conditions


def test_sossi_unprinted_flow_remains_unknown_for_series_defaults():
    from simulator.chemistry.langmuir_knudsen import series_flux

    experiment = _source_experiment("2m-15-07-16c")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["pressure_environment"]["flow_rate_sccm"]["state"]["tag"] == "unknown"
    assert conditions["carrier_gas_mixture"] is None
    assert conditions["flow_rate_sccm"] is None
    assert conditions["pipe_diameter_m"] == pytest.approx(0.042)
    assert conditions["transport_length_m"] == pytest.approx(0.03)
    result = series_flux(
        species="Mn",
        p_eq_pa=10.0,
        p_bulk_pa=0.0,
        T_surface_K=1673.15,
        molar_mass_kg_mol=0.05494,
        alpha=1.0,
        **{
            key: conditions[key]
            for key in (
                "total_pressure_pa",
                "carrier_gas_mixture",
                "flow_rate_sccm",
                "pipe_diameter_m",
                "transport_length_m",
            )
        },
    )
    assert "gas_flow_unprinted_default_radial_stir_factor_1" in (
        result.transport_assumption_flags
    )
    assert "sweep_gas_unprinted_default_N2" in result.transport_assumption_flags


def test_sossi_open_furnace_sends_printed_pressure_to_series_law(monkeypatch):
    from types import SimpleNamespace

    from simulator.battery.residue import (
        ResidueChannel,
        _ChannelTerms,
        _finite_step_fluxes,
    )

    calls = []

    def capture_series_flux(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(flux_kg_s_m2=4.0)

    monkeypatch.setattr(
        "simulator.chemistry.langmuir_knudsen.series_flux", capture_series_flux
    )
    experiment = _source_experiment("2m-15-07-16g")
    conditions = _sossi_transport_conditions(experiment, 101325.0)
    term = _ChannelTerms(
        source=ResidueChannel("Mn", "Mn", "MnO", 0.85, "test"),
        molar_mass_kg_mol=0.05494,
        atom_counts={"Mn": 1.0},
        parent_moles_per_product=1.0,
        parent_oxygen_demand=0.0,
        al_sio_sio2_moles_per_product=0.0,
        oxide_per_product_kg=0.0,
        o2_per_product_kg=0.0,
    )

    rates = _finite_step_fluxes(
        [term],
        {"MnO": 1.0},
        temperature_K=1673.15,
        area_m2=2.0e-5,
        pressures_Pa={"Mn": 100.0},
        transport_conditions=conditions,
    )

    assert rates["Mn"] == pytest.approx(8.0e-5)
    assert len(calls) == 1
    assert calls[0]["total_pressure_pa"] == 101325.0
    assert calls[0]["carrier_gas_mixture"] == {"CO": 1.8, "CO2": 200.0}
    assert calls[0]["flow_rate_sccm"] == 201.8
    assert calls[0]["gas_resistance_enabled"] is True

    mutated_conditions = {**conditions, "total_pressure_pa": 202650.0}
    _finite_step_fluxes(
        [term],
        {"MnO": 1.0},
        temperature_K=1673.15,
        area_m2=2.0e-5,
        pressures_Pa={"Mn": 100.0},
        transport_conditions=mutated_conditions,
    )
    assert calls[-1]["total_pressure_pa"] == 202650.0


def test_sossi_single_dash_keeps_component_unknown_and_flags_carrier_approximation():
    experiment = _source_experiment("2m-15-07-16d")

    conditions = _sossi_transport_conditions(experiment, 101325.0)

    assert experiment["pressure_environment"]["sweep_gas_mixture"]["state"]["value"] == {
        "CO": "-",
        "CO2": "200",
    }
    assert conditions["carrier_gas_mixture"] == {"CO2": 200.0}
    assert conditions["flow_rate_sccm"] == 200.0
    assert conditions["transport_assumption_flags"] == (
        "sweep_gas_CO_unprinted_CO2_carrier_approximation",
    )

    # A literal printed zero is different from an unprinted dash.
    printed_zero = _sossi_transport_conditions(
        _source_experiment("c18-12-15"), 101325.0
    )
    assert printed_zero["carrier_gas_mixture"] == {"CO": 0.0, "CO2": 27.0}
    assert printed_zero["transport_assumption_flags"] == ()

    mutated = {
        **experiment,
        "pressure_environment": {
            **experiment["pressure_environment"],
            "sweep_gas_mixture": {
                "state": {
                    "tag": "value",
                    "value": {"CO": "0", "CO2": "200"},
                }
            },
        },
    }
    mutated_conditions = _sossi_transport_conditions(mutated, 101325.0)
    assert mutated_conditions["carrier_gas_mixture"] == {"CO": 0.0, "CO2": 200.0}
    assert mutated_conditions["transport_assumption_flags"] == ()
    assert mutated_conditions != conditions


def test_sossi_table_and_bead_area_locators_use_physical_pdf_pages():
    source = yaml.safe_load(SOURCE.read_text())
    experiments = [
        row
        for row in source["experiments"]
        if row["experiment_id"].startswith("sossi-2019-run-")
    ]
    pages = [row["locator"]["page"] for row in experiments]

    assert len(pages) == 43
    assert pages.count(54) == 26
    assert pages.count(55) == 17
    assert _source_experiment("c17-12-15b")["locator"]["page"] == 55

    area_record = next(
        observation["equipment"]["sample_surface_area"]
        for observation in source["species"]["K"]["observations"]
        if "sample_surface_area" in observation.get("equipment", {})
    )
    assert area_record["locator"]["page"] == 6
    assert area_record["locator"]["section"] == "2.1 Experimental Methods"


def test_missing_tio2_transport_uses_flagged_langmuir_limit_only_for_tio2():
    from simulator.battery import residue
    from simulator.chemistry.langmuir_knudsen import langmuir_flux
    from simulator.battery.residue import _formula_terms

    experiment = _source_experiment("2m-15-07-16c")
    conditions = _sossi_transport_conditions(experiment, 101325.0)
    kwargs = {
        "species": "TiO2",
        "p_eq_pa": 100.0,
        "p_bulk_pa": 0.0,
        "T_surface_K": 1673.15,
        "molar_mass_kg_mol": _formula_terms("TiO2")[1],
        "alpha": 0.85,
        "carrier_gas": "N2",
        "T_gas_K": 1673.15,
        "melt_resistance_enabled": False,
        "gas_resistance_enabled": True,
        **{
            key: conditions[key]
            for key in (
                "total_pressure_pa",
                "carrier_gas_mixture",
                "flow_rate_sccm",
                "pipe_diameter_m",
                "transport_length_m",
            )
        },
    }

    result, fallback_flag = residue._series_flux_with_missing_ce_fallback(**kwargs)

    expected = langmuir_flux(
        "TiO2",
        1673.15,
        100.0,
        0.0,
        0.85,
        molar_mass_kg_mol=_formula_terms("TiO2")[1],
    )
    assert result.flux_kg_s_m2 == pytest.approx(expected)
    assert fallback_flag is not None
    assert "TiO2" in fallback_flag
    assert "N2" in fallback_flag
    assert "missing_chapman_enskog_parameters" in fallback_flag
    assert "langmuir_limit" in fallback_flag
    assert "low_authority" in fallback_flag


def test_parameterless_tio2_does_not_refuse_supported_mn_flux(monkeypatch):
    from simulator.battery.residue import (
        ResidueChannel,
        _ChannelTerms,
        _finite_step_fluxes,
        _formula_terms,
    )
    from simulator.chemistry.langmuir_knudsen import series_flux

    calls = []
    original_series_flux = series_flux

    def capture_series_flux(**kwargs):
        calls.append((kwargs["species"], kwargs["gas_resistance_enabled"]))
        return original_series_flux(**kwargs)

    monkeypatch.setattr(
        "simulator.chemistry.langmuir_knudsen.series_flux", capture_series_flux
    )

    def channel(species, formula, parent):
        return _ChannelTerms(
            source=ResidueChannel(species, formula, parent, 0.85, "test"),
            molar_mass_kg_mol=_formula_terms(formula)[1],
            atom_counts={},
            parent_moles_per_product=1.0,
            parent_oxygen_demand=0.0,
            al_sio_sio2_moles_per_product=0.0,
            oxide_per_product_kg=0.0,
            o2_per_product_kg=0.0,
        )

    conditions = _sossi_transport_conditions(
        _source_experiment("2m-15-07-16c"), 101325.0
    )
    rates = _finite_step_fluxes(
        [channel("Mn", "Mn", "MnO"), channel("TiO2", "TiO2", "TiO2")],
        {"MnO": 1.0, "TiO2": 1.0},
        temperature_K=1673.15,
        area_m2=2.0e-5,
        pressures_Pa={"Mn": 100.0, "TiO2": 100.0},
        transport_conditions=conditions,
    )

    assert rates["Mn"] > 0.0
    assert rates["TiO2"] > 0.0
    assert calls == [("Mn", True), ("TiO2", True), ("TiO2", False)]
