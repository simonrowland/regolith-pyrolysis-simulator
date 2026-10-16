from __future__ import annotations

from pathlib import Path

import yaml

from simulator.reference_data.janaf import load_table_document
from simulator.refractory_vaporization import refractory_log10_kf
from tools.generate_refractory_vapor_species import (
    CONDENSED_TABLES,
    GAS_TABLES,
    OUTPUT_PATH,
    render_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def test_committed_registry_matches_fresh_janaf_regeneration() -> None:
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render_registry()


def test_every_thermochemical_row_carries_source_provenance() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    for section in ("gas_species", "condensed_phases"):
        for row in data[section].values():
            provenance = row["provenance"]
            assert provenance["table_id"]
            assert len(provenance["source_temperature_range_K"]) == 2
            assert provenance["phase"]
            assert provenance["derivation"]


def test_five_rows_match_janaf_printed_log_kf_at_2000_and_2500_K() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    checks = (
        ("gas_species", "AlO", GAS_TABLES["AlO"]),
        ("gas_species", "NaO", GAS_TABLES["NaO"]),
        ("gas_species", "KO", GAS_TABLES["KO"]),
        ("condensed_phases", "Al2O3", CONDENSED_TABLES["Al2O3"]),
        ("condensed_phases", "MgO", CONDENSED_TABLES["MgO"]),
    )
    # Per-species sanity anchor: every generated row agrees with its printed
    # JANAF log Kf cell at 2000 K.
    all_rows = [
        ("gas_species", species, table_id)
        for species, table_id in GAS_TABLES.items()
    ] + [
        ("condensed_phases", species, table_id)
        for species, table_id in CONDENSED_TABLES.items()
    ]
    for section, species, table_id in all_rows:
        table_path = ROOT / "data/literature/compilations/janaf/tables" / f"{table_id}.yaml"
        table = load_table_document(table_path)["table"]
        source = next(
            row["log10_formation_equilibrium_constant"]["value"]
            for row in table["values"]
            if row.get("temperature", {}).get("value") == 2000
            and row.get("log10_formation_equilibrium_constant", {}).get("value") is not None
        )
        assert data[section][species]["log10_kf"][2000] == source

    for section, species, table_id in checks:
        table_path = ROOT / "data/literature/compilations/janaf/tables" / f"{table_id}.yaml"
        table = load_table_document(table_path)["table"]
        source = {
            int(row["temperature"]["value"]): row["log10_formation_equilibrium_constant"]["value"]
            for row in table["values"]
            if row.get("temperature", {}).get("value") in (2000, 2500)
            and row.get("log10_formation_equilibrium_constant", {}).get("value") is not None
        }
        for temperature in (2000, 2500):
            assert data[section][species]["log10_kf"][temperature] == source[temperature]


def test_crystalline_only_row_stops_before_melting_and_flags_out_of_band_use() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    row = data["condensed_phases"]["MgAl2O4"]
    assert max(row["log10_kf"]) == 2400
    assert row["out_of_band_notice"]["type"] == "liquid_table_unavailable_after_phase_boundary"
    assert row["out_of_band_notice"]["temperature_K"] == 2408.0
    _, range_use = refractory_log10_kf("MgAl2O4", 2500.0, condensed=True)
    assert range_use.source_temperature_max_K == 2400.0
    assert range_use.status.startswith("extrapolated_")
