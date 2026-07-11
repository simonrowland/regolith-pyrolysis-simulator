from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.accounting import resolve_species_formula
from simulator.backends import BackendSelectionPolicy, InternalAnalyticalBackend
from simulator.core import PyrolysisSimulator
from simulator.session import SimSessionConfig
from simulator.stage0_harness import run_stage0_harness_from_config


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _real_sim(feedstock_key: str, *, diagnostics_enabled: bool) -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        _load_yaml("setpoints.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim._foulant_diagnostics_enabled = diagnostics_enabled
    feedstock = sim.feedstocks[feedstock_key]
    additives: dict[str, float] = {}
    if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstock):
        required_c = PyrolysisSimulator._carbon_reductant_required_kg(
            feedstock,
            1000.0,
        )
        if required_c > 1e-12:
            additives["C"] = required_c
    sim.load_batch(
        feedstock_key,
        mass_kg=1000.0,
        additives_kg=additives or None,
    )
    return sim


def _session_config(feedstock_key: str) -> SimSessionConfig:
    feedstocks = _load_yaml("feedstocks.yaml")
    values: dict[str, Any] = {
        "feedstock_id": feedstock_key,
        "feedstocks": feedstocks,
        "setpoints": _load_yaml("setpoints.yaml"),
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C0",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    feedstock = feedstocks[feedstock_key]
    if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstock):
        required_c = PyrolysisSimulator._carbon_reductant_required_kg(
            feedstock,
            1000.0,
        )
        if required_c > 1e-12:
            values["additives_kg"] = {"C": required_c}
    return SimSessionConfig(**values)


def _molar_mass(sim: PyrolysisSimulator, species: str) -> float:
    return resolve_species_formula(
        species,
        sim.species_formula_registry,
    ).molar_mass_kg_per_mol()


def test_mars_perchlorate_rich_emits_phase1_perchlorate_diagnostic():
    sim = _real_sim("mars_perchlorate_rich", diagnostics_enabled=True)
    sim_off = _real_sim("mars_perchlorate_rich", diagnostics_enabled=False)

    diagnostics = [
        diag
        for diag in sim._stage0_foulant_diagnostics
        if diag.get("reaction_family") == "perchlorate"
    ]
    assert diagnostics
    assert len(diagnostics) == 1
    diag = diagnostics[0]

    clo4_kg = sim.inventory.raw_components_kg["ClO4"]
    extent_mol = clo4_kg / _molar_mass(sim, "ClO4")
    expected_cl_kg = extent_mol * _molar_mass(sim, "Cl")
    expected_o2_kg = 2.0 * extent_mol * _molar_mass(sim, "O2")

    assert diag["carrier"] == "ClO4_pseudo"
    assert diag["source_component"] == "ClO4"
    assert diag["source_basis"] == "pseudo_ClO4"
    assert diag["feed_kg"] == pytest.approx(clo4_kg)
    assert diag["salt_products_kg"] == {"Cl": pytest.approx(expected_cl_kg)}
    assert diag["oxygen_products_kg"] == {"O2": pytest.approx(expected_o2_kg)}
    assert diag["stage0_phase"] == "phase_1_oxidizing"
    assert "no Mg/Ca cation route" in diag["pseudo_species_caveat"]

    assert sim.atom_ledger.mol_by_account() == sim_off.atom_ledger.mol_by_account()
    assert sim.inventory.melt_oxide_kg == sim_off.inventory.melt_oxide_kg
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)

    result = run_stage0_harness_from_config(
        _session_config("mars_perchlorate_rich"),
    )
    timeline_hits = [
        (entry, event)
        for entry in result.disposition_timeline
        for events in entry.by_group.values()
        for event in events
        if event.get("reaction_family") == "perchlorate"
    ]
    assert timeline_hits
    assert any(
        entry.stage0_phase == "phase_1_oxidizing"
        and event.get("raw", {}).get("oxygen_products_kg", {}).get("O2")
        == pytest.approx(expected_o2_kg)
        for entry, event in timeline_hits
    )


def test_perchlorate_registry_fate_is_single_routed():
    payload = _load_yaml("foulant_thermo.yaml")

    for carrier_key, chloride_species in (
        ("Mg_ClO4_2", "MgCl2"),
        ("Ca_ClO4_2", "CaCl2"),
    ):
        fate = payload[carrier_key]["fate"]
        assert "on_decompose_melt" not in fate
        assert fate["on_decompose_offgas"]["species"] == ["O2"]
        assert fate["on_decompose_chloride"]["species"] == [chloride_species]

    pseudo = payload["ClO4_pseudo"]
    assert pseudo["carrier"]["species"] == "ClO4"
    assert "clo4" in pseudo["carrier"]["aliases"]
    assert pseudo["fate"]["on_decompose_chloride"]["species"] == ["Cl"]
    assert "on_decompose_melt" not in pseudo["fate"]


def test_elemental_chloride_diagnostic_converts_to_salt_mass_basis():
    sim = _real_sim("mars_perchlorate_rich", diagnostics_enabled=True)
    feedstock = sim.feedstocks["mars_perchlorate_rich"]

    chloride_diags = [
        diag
        for diag in sim._stage0_foulant_diagnostics
        if diag.get("reaction_family") == "volatilization"
        and diag.get("source_component") == "Cl"
        and diag.get("carrier") in {"NaCl", "KCl"}
    ]
    assert {diag["carrier"] for diag in chloride_diags} == {"NaCl", "KCl"}

    raw_cl_kg = sim.inventory.raw_components_kg["Cl"]
    comp = feedstock["composition_wt_pct"]
    na_frac = comp["Na2O"] / (comp["Na2O"] + comp["K2O"])
    k_frac = comp["K2O"] / (comp["Na2O"] + comp["K2O"])
    expected = {
        "NaCl": raw_cl_kg * na_frac * _molar_mass(sim, "NaCl") / _molar_mass(sim, "Cl"),
        "KCl": raw_cl_kg * k_frac * _molar_mass(sim, "KCl") / _molar_mass(sim, "Cl"),
    }

    observed = {diag["carrier"]: diag for diag in chloride_diags}
    for carrier, diag in observed.items():
        assert diag["feed_basis"] == "elemental_Cl"
        assert diag["source_feed_kg"] == pytest.approx(raw_cl_kg)
        assert diag["source_cl_kg"] < diag["feed_kg"]
        assert diag["feed_kg"] == pytest.approx(expected[carrier])

    pass_through = sim._expand_chloride_foulant_feed(
        "NaCl",
        3.0,
        feedstock,
    )
    assert pass_through == [(
        "NaCl",
        pytest.approx(3.0),
        {
            "feed_basis": "salt_mass",
            "source_feed_kg": pytest.approx(3.0),
        },
    )]
