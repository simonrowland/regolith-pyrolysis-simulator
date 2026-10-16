from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.reference_data.janaf import load_table_document
from simulator.reference_data.nasa_glenn import load_record_document
from simulator.refractory_vaporization import refractory_log10_kf
from simulator.vapour_rail.nasa_cea import Nasa9Segment
from tools.generate_refractory_vapor_species import (
    CONDENSED_TABLES,
    GAS_TABLES,
    OUTPUT_PATH,
    render_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def test_committed_registry_matches_fresh_compilation_regeneration() -> None:
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render_registry()


def test_every_thermochemical_row_carries_source_provenance() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    for section in ("gas_species", "condensed_phases"):
        for row in data[section].values():
            provenance = row["provenance"]
            assert provenance["source_tag"] in {"nist_janaf", "nasa_glenn"}
            assert len(provenance["source_temperature_range_K"]) == 2
            assert provenance["phase"]
            assert provenance["derivation"]
            assert (
                provenance.get("table_id")
                if provenance["source_tag"] == "nist_janaf"
                else provenance.get("record_id")
            )


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


def test_alumina_gas_uses_nasa9_against_janaf_element_reference_phases() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    row = data["gas_species"]["Al2O3"]
    provenance = row["provenance"]
    assert provenance["source_tag"] == "nasa_glenn"
    assert provenance["record_id"] == "NG-0071"
    assert provenance["species_as_published"] == "AL2O3"
    assert provenance["coefficient_set"] == "NASA-9"
    assert provenance["source_temperature_range_K"] == [300.0, 6000.0]
    assert provenance["phase"] == "gas"
    assert provenance["element_reference_states"] == {
        "Al": {
            "record_id": "NG-1273",
            "species_as_published": "AL(L)",
            "phase": "L",
            "coefficient_set": "NASA-9",
            "source_temperature_range_K": [933.61, 6000.0],
        },
        "O2": {
            "record_id": "NG-0937",
            "species_as_published": "O2",
            "phase": "gas",
            "coefficient_set": "NASA-9",
            "source_temperature_range_K": [200.0, 20000.0],
        },
    }
    assert provenance["coefficient_intervals_K"] == [[300.0, 1000.0], [1000.0, 6000.0]]

    def g_over_rt(record_id: str, temperature_K: int) -> float:
        document = load_record_document(
            ROOT / "data/literature/compilations/nasa-glenn/records" / f"{record_id}.json"
        )
        interval = next(
            item
            for item in document["intervals"]
            if item["T_min_K"]["value"] <= temperature_K <= item["T_max_K"]["value"]
        )
        segment = Nasa9Segment(
            interval["T_min_K"]["value"],
            interval["T_max_K"]["value"],
            tuple(item["value"] for item in interval["a_coefficients"]),
            interval["b1"]["value"],
            interval["b2"]["value"],
        )
        return segment.evaluate_ratios(float(temperature_K))[3]

    for temperature in (2000, 2500):
        expected = -(
            g_over_rt("NG-0071", temperature)
            - 2 * g_over_rt("NG-1273", temperature)
            - 1.5 * g_over_rt("NG-0937", temperature)
        ) / math.log(10.0)
        assert row["log10_kf"][temperature] == pytest.approx(expected, abs=1e-12)


def test_crystalline_only_row_stops_before_melting_and_flags_out_of_band_use() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    row = data["condensed_phases"]["MgAl2O4"]
    assert max(row["log10_kf"]) == 2400
    assert row["out_of_band_notice"]["type"] == "liquid_table_unavailable_after_phase_boundary"
    assert row["out_of_band_notice"]["temperature_K"] == 2408.0
    _, range_use = refractory_log10_kf("MgAl2O4", 2500.0, condensed=True)
    assert range_use.source_temperature_max_K == 2400.0
    assert range_use.status.startswith("extrapolated_")
