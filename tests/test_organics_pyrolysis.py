"""Organics source term: primary split, Ellingham char fate, tar, H2, closure."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.account_ids import SOLID_CHAR_CARBON_ACCOUNT
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.organics_pyrolysis import (
    REDUCING_SPECIES,
    apply_organics_source,
    boudouard_crossover_K,
    boudouard_kp,
    carbon_activity,
    carbon_delta_g_co_kj_per_mol_o2,
    char_fate,
    char_survival_surface,
    load_organics_pyrolysis_params,
    organic_co2_release_fraction,
    primary_pyrolysis,
    voropaev_release_fraction,
)
from simulator.core import CHAR_SPECIES, PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.silent_zero import CATEGORY_PROVEN_ZERO


DATA = Path(__file__).resolve().parent.parent / "data"
CELSIUS_TO_KELVIN = 273.15


def _load_yaml(name: str) -> dict:
    import yaml

    return yaml.safe_load((DATA / name).read_text())


def _build_sim(feedstock_key: str) -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    setpoints = _load_yaml("setpoints.yaml")
    kernel_cfg = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_cfg["allow_fallback_vapor"] = True
    kernel_cfg["allow_unmeasured_alpha_fallback"] = True
    setpoints["chemistry_kernel"] = kernel_cfg
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch(feedstock_key, mass_kg=1000.0)
    return sim


def _total_atoms(sim: PyrolysisSimulator, element: str) -> float:
    total = 0.0
    for _account, species_mol in sim.atom_ledger.mol_by_account().items():
        for species, mol in species_mol.items():
            total += resolve_species_formula(
                species, sim.species_formula_registry
            ).atom_moles(float(mol)).get(element, 0.0)
    return total


def _raw_feed_atoms(sim: PyrolysisSimulator, element: str) -> float:
    total = 0.0
    for species, kg in sim.inventory.raw_components_kg.items():
        formula = resolve_species_formula(species, sim.species_formula_registry)
        species_mol = float(kg) / formula.molar_mass_kg_per_mol()
        total += formula.atom_moles(species_mol).get(element, 0.0)
    return total


def test_boudouard_crossover_is_voropaev_697_c() -> None:
    t_k = boudouard_crossover_K()
    assert t_k == pytest.approx(9001.0 / 9.28, rel=1e-12)
    assert t_k - 273.15 == pytest.approx(696.79, abs=0.05)
    assert boudouard_kp(t_k) == pytest.approx(1.0, rel=1e-6)
    assert boudouard_kp(773.15) < 0.05
    assert boudouard_kp(1273.15) > 20.0


def test_c_to_co_ellingham_line_falls_with_temperature() -> None:
    low = carbon_delta_g_co_kj_per_mol_o2(800.0)
    high = carbon_delta_g_co_kj_per_mol_o2(1500.0)
    assert high < low
    # Sanity vs Richardson–Jeffes ~ -400 kJ/mol O2 at 1000 K.
    assert carbon_delta_g_co_kj_per_mol_o2(1000.0) == pytest.approx(
        -399.8, abs=2.0
    )


def test_voropaev_co2_organic_population_is_half_at_500c() -> None:
    raw = voropaev_release_fraction("CO2", 500.0)
    assert raw["fraction"] == pytest.approx(0.532, abs=0.001)
    organic = organic_co2_release_fraction(500.0)
    assert organic["fraction"] == pytest.approx(1.0)
    assert organic["adsorbed"] == "proven_zero"
    assert organic["carbonate"] == "not_speciated"
    early = organic_co2_release_fraction(200.0)
    assert 0.0 < early["fraction"] < 0.2


def test_primary_pyrolysis_has_no_co_and_emits_h2() -> None:
    atoms = {"C": 100.0, "H": 75.0, "O": 15.0, "N": 3.5, "S": 2.0}
    result = primary_pyrolysis(atoms, 800.0)
    assert "CO" not in result.gas_mol
    assert result.gas_mol.get("H2", 0.0) > 0.0
    assert result.gas_mol.get("CH4", 0.0) > 0.0
    assert result.char_c_mol == pytest.approx(50.75, rel=1e-6)
    proven = [
        note
        for note in result.notes
        if note.get("doctrine_category") == CATEGORY_PROVEN_ZERO
        and note.get("species") == "CO"
    ]
    assert proven


def test_missing_s_is_proven_zero_not_silent_zero() -> None:
    atoms = {"C": 100.0, "H": 75.0, "O": 15.0, "N": 3.5}
    result = primary_pyrolysis(atoms, 800.0)
    assert result.gas_mol.get("H2S", 0.0) == 0.0
    assert result.gas_mol.get("COS", 0.0) == 0.0
    notes = [note for note in result.notes if note.get("species") == "H2S"]
    assert notes
    assert notes[0]["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert notes[0]["zero_because"] == "proven_empty_inventory"


def test_apply_organics_source_closes_atoms() -> None:
    atoms = {"C": 61.61, "H": 89.29, "O": 7.50, "N": 3.57}
    result = apply_organics_source(atoms, 1050.0, 1e-12, 1500.0)
    assert result.closed
    for element, delta in result.atom_closure.items():
        assert abs(delta) < 1e-9, element
    gas = result.evolved_gas_combined
    assert "H2" in gas.reducing_species_mol
    assert "CH4" in gas.reducing_species_mol
    # CO is char-fate output at 1050 C because Boudouard is irreversible.
    assert gas.reducing_species_mol.get("CO", 0.0) >= 0.0
    assert result.primary.evolved_gas.species_mol.get("CO", 0.0) == 0.0


def test_char_fate_lance_vs_vacuum_and_high_t_weakening() -> None:
    n_char = 1.0
    n_co2 = 0.4
    vacuum_500 = char_fate(n_char, 500.0, 1e-12, co2_mol=n_co2)
    vacuum_1050 = char_fate(n_char, 1050.0, 1e-12, co2_mol=n_co2)
    lance_500 = char_fate(n_char, 500.0, 0.21, co2_mol=n_co2)
    lance_1050 = char_fate(n_char, 1050.0, 0.21, co2_mol=n_co2)
    assert vacuum_500.survive_fraction > 0.85
    assert vacuum_1050.survive_fraction < vacuum_500.survive_fraction
    assert vacuum_1050.produced_co_mol > vacuum_500.produced_co_mol
    assert lance_500.survive_fraction == pytest.approx(0.0, abs=1e-12)
    assert lance_1050.survive_fraction == pytest.approx(0.0, abs=1e-12)
    assert carbon_activity(773.15, 0.21)["a_C_clamped"] < 1e-6


def test_char_survival_surface_is_two_variable() -> None:
    rows = char_survival_surface(
        [500.0, 800.0, 1200.0],
        [1e-12, 1e-6, 0.21],
        char_c_mol=1.0,
        co2_mol=0.3,
    )
    by_key = {(r["temperature_C"], r["pO2_bar"]): r for r in rows}
    assert by_key[(500.0, 1e-12)]["survive_fraction"] > by_key[(1200.0, 1e-12)][
        "survive_fraction"
    ]
    assert by_key[(500.0, 0.21)]["survive_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert by_key[(1200.0, 0.21)]["survive_fraction"] == pytest.approx(0.0, abs=1e-12)


def test_ci_source_term_commits_h2_tar_char_and_closes() -> None:
    sim = _build_sim("ci_carbonaceous_chondrite")
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12
    assert sim._last_organics_source["closed"]
    for element, delta in sim._last_organics_source["atom_closure"].items():
        assert abs(delta) < 1e-9, element
    # C/H/N/S from the whole batch match raw feed. O is not a raw-feed
    # identity: other Stage-0 reactions may add reagent oxidant.
    for element in ("C", "H", "N", "S"):
        assert _total_atoms(sim, element) == pytest.approx(
            _raw_feed_atoms(sim, element), rel=1e-9, abs=1e-9
        )
    h2 = float(
        sim.atom_ledger.mol_by_account("terminal.offgas").get("H2", 0.0) or 0.0
    )
    assert h2 > 0.0
    assert "H2" in sim.species_formula_registry
    char = float(
        sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
            CHAR_SPECIES, 0.0
        )
        or 0.0
    )
    assert char > 0.0
    record = sim._last_organics_source["evolved_gas_combined"]
    for species in REDUCING_SPECIES:
        assert species in record["reducing_species_mol"] or species == "H2S"
    assert "H2" in record["reducing_species_mol"]
    assert "CH4" in record["reducing_species_mol"]
    # Primary has no CO; combined may have CO from Boudouard at 1050 C.
    assert sim._last_organics_source["evolved_gas_primary"]["species_mol"].get(
        "CO", 0.0
    ) == 0.0
    tar = float(
        sim.atom_ledger.mol_by_account("process.overhead_gas").get(
            "organic_tar", 0.0
        )
        or 0.0
    )
    assert tar > 0.0
    # Default hot walls: no tar coating.
    assert (
        sim.atom_ledger.mol_by_account("process.wall_deposit").get(
            "organic_tar", 0.0
        )
        or 0.0
    ) == pytest.approx(0.0)


def test_lunar_organics_free_has_no_char_or_h2() -> None:
    sim = _build_sim("lunar_mare_low_ti")
    assert (
        sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
            CHAR_SPECIES, 0.0
        )
        or 0.0
    ) == pytest.approx(0.0)
    assert (
        sim.atom_ledger.mol_by_account("terminal.offgas").get("H2", 0.0) or 0.0
    ) == pytest.approx(0.0)


def test_params_mark_class_level_not_measurement() -> None:
    params = load_organics_pyrolysis_params()
    assert params["output_status"] == "status_bearing_non_authoritative"
    char = params["nascent_char"]["f_char_of_bulk_organic_C"]
    assert char["certified_point"] is None
    assert char["status"] == "status_bearing_non_authoritative"
    assert params["primary_gas_yields_of_mobilized_C"]["CO"]["status"] == (
        "proven_zero"
    )
    tar = params["tar"]
    for key in (
        "crack_above_C",
        "coke_below_C",
        "crack_frac_above_crack_T",
        "coke_frac_above_crack_T",
        "crack_frac_below_coke_T",
        "coke_frac_below_coke_T",
    ):
        assert tar[key]["certified_point"] is None
    assert tar["coke_frac_above_crack_T"]["value"] == pytest.approx(0.20)


def test_comet_nucleus_refuses_ungrounded_char_and_does_not_crash() -> None:
    """comet_nucleus is not_speciated: no invented reductant, no TypeError."""
    sim = _build_sim("comet_nucleus")
    assert not getattr(sim, "_last_organics_source", None)
    char = float(
        sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
            CHAR_SPECIES, 0.0
        )
        or 0.0
    )
    assert char == pytest.approx(0.0)
    from simulator.runner import PyrolysisRun

    payload = PyrolysisRun(
        feedstock_id="comet_nucleus",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    ).run()
    assert "c0_char_diagnostic" not in payload["run_metadata"]


SHIPPED_VACUUM_PYROLYSIS_ROWS = (
    ("ci_carbonaceous_chondrite", "carbonaceous_organic"),
    ("ci_carbonaceous_chondrite", "hydrocarbons"),
    ("cm_carbonaceous_chondrite", "carbonaceous_organic"),
    ("ceres_regolith", "carbonaceous_organic"),
)
MASS_BEARING_VACUUM_PYROLYSIS_ROWS = (
    ("ci_carbonaceous_chondrite", "carbonaceous_organic"),
    ("cm_carbonaceous_chondrite", "carbonaceous_organic"),
    ("ceres_regolith", "carbonaceous_organic"),
)


@pytest.mark.parametrize("feedstock_key,species", SHIPPED_VACUUM_PYROLYSIS_ROWS)
def test_shipped_rows_declare_vacuum_pyrolysis(feedstock_key: str, species: str) -> None:
    catalog = _load_yaml("feedstocks.yaml")
    entry = catalog[feedstock_key]["stage0_formula_inventory"][species]
    assert entry["offgas_mode"] == "vacuum_pyrolysis"
    assert not PyrolysisSimulator._stage0_entry_declares_oxidant(entry)
    assert "Omit oxygen_source" not in str(entry.get("source") or "")


@pytest.mark.parametrize("feedstock_key,species", MASS_BEARING_VACUUM_PYROLYSIS_ROWS)
def test_shipped_row_reaches_source_term_via_declared_mode(
    feedstock_key: str, species: str
) -> None:
    sim = _build_sim(feedstock_key)
    names = [transition.name for transition in sim.atom_ledger.transitions]
    assert f"stage0_organic_source_term_{species}" in names
    assert f"stage0_complete_oxidation_{species}" not in names
    if feedstock_key == "ci_carbonaceous_chondrite":
        char = float(
            sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
                CHAR_SPECIES, 0.0
            )
            or 0.0
        )
        assert char == pytest.approx(1305.7985635282805)


def test_ci_hydrocarbons_vacuum_pyrolysis_reaches_source_term_when_mass_present() -> None:
    """CI hydrocarbons is a declared vacuum_pyrolysis row with no shipped mass."""
    from engines.builtin.stage0_pretreatment import (
        REACTION_FAMILY_ORGANIC_SOURCE_TERM,
    )

    catalog = _load_yaml("feedstocks.yaml")
    feedstock = catalog["ci_carbonaceous_chondrite"]
    entry = feedstock["stage0_formula_inventory"]["hydrocarbons"]
    assert entry["offgas_mode"] == "vacuum_pyrolysis"
    sim = _build_sim("ci_carbonaceous_chondrite")
    spec = sim._organic_source_spec("hydrocarbons", 10.0, entry, feedstock)
    assert spec is not None
    assert spec["reaction_family"] == REACTION_FAMILY_ORGANIC_SOURCE_TERM
    assert spec["species"] == "hydrocarbons"
