from __future__ import annotations

import math
import subprocess
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
    render_change_report,
    render_registry,
)


ROOT = Path(__file__).resolve().parents[1]
CHANGE_REPORT_PATH = ROOT / "docs" / "refractory-vapor-species-generation.md"
REPORT_BASELINE_REV = "c25691fb6c04b1fcb91c4328ad5948dd79d07aa5"


def test_committed_registry_matches_fresh_compilation_regeneration() -> None:
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render_registry()


def test_committed_generation_report_matches_fresh_render() -> None:
    previous = subprocess.check_output(
        ["git", "show", f"{REPORT_BASELINE_REV}:data/refractory_vapor_species.yaml"],
        cwd=ROOT,
        text=True,
    )
    assert CHANGE_REPORT_PATH.read_text(encoding="utf-8") == render_change_report(
        previous, render_registry()
    )


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


def test_alumina_vaporization_reaction_uses_only_nasa9_records() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    gas = data["gas_species"]["Al2O3"]
    condensed = data["condensed_phases"]["Al2O3"]
    provenance = gas["provenance"]
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
    assert condensed["provenance"]["source_tag"] == "nist_janaf"
    assert condensed["provenance"]["table_id"] == "Al-101"
    assert "NG-1286" in provenance["derivation"]
    assert "NG-1287" in provenance["derivation"]
    assert "1500 through 3500 K" in provenance["derivation"]

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

    for temperature in range(1500, 3501, 100):
        condensed_record = "NG-1286" if temperature <= 2327 else "NG-1287"
        expected_vaporization_log_k = -(
            g_over_rt("NG-0071", temperature)
            - g_over_rt(condensed_record, temperature)
        ) / math.log(10.0)
        assert gas["log10_kf"][temperature] == pytest.approx(
            condensed["log10_kf"][temperature] + expected_vaporization_log_k,
            abs=1e-12,
        )
        assert gas["log10_kf"][temperature] - condensed["log10_kf"][temperature] == pytest.approx(
            expected_vaporization_log_k, abs=1e-12
        )


def test_spinel_uses_liquid_table_above_its_transition_without_extrapolation() -> None:
    data = yaml.safe_load(OUTPUT_PATH.read_text(encoding="utf-8"))
    row = data["condensed_phases"]["MgAl2O4"]
    transition_table = load_table_document(
        ROOT / "data/literature/compilations/janaf/tables/Al-091.yaml"
    )["table"]
    liquid_table = load_table_document(
        ROOT / "data/literature/compilations/janaf/tables/Al-090.yaml"
    )["table"]
    transition_value = next(
        item["log10_formation_equilibrium_constant"]["value"]
        for item in transition_table["values"]
        if item.get("temperature", {}).get("value") == 2400
        and item.get("log10_formation_equilibrium_constant", {}).get("value") is not None
    )
    liquid_value = next(
        item["log10_formation_equilibrium_constant"]["value"]
        for item in liquid_table["values"]
        if item.get("temperature", {}).get("value") == 2500
        and item.get("log10_formation_equilibrium_constant", {}).get("value") is not None
    )
    assert row["provenance"]["table_id"] == "Al-091"
    assert row["phase_transitions_K"] == [2408.0]
    assert row["log10_kf"][2400] == transition_value
    assert row["log10_kf"][2500] == liquid_value
    assert max(row["log10_kf"]) == 3500
    assert "out_of_band_notice" not in row
    _, range_use = refractory_log10_kf("MgAl2O4", 2500.0, condensed=True)
    assert range_use.source_temperature_max_K == 3500.0
    assert range_use.status == "in_range"
