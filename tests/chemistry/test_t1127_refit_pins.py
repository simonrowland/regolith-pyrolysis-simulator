"""Pre-refit numeric pins for the Al, Si and Mn(l) Antoine sidecars."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.antoine import _antoine_log10_pressure
from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients

JANAF_TABLES = Path(__file__).resolve().parents[2] / "data/literature/compilations/janaf/tables"
R_KJ_PER_MOL_K = 8.314462618e-3


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_hex"),
    [
        ("Al", 1000.0, "0x1.5181486a8527ep-18"),
        ("Al", 1300.0, "0x1.ea6a010b62ec5p-6"),
        ("Al", 1500.0, "0x1.58b4a63950985p+0"),
        ("Al", 2000.0, "0x1.30022ce844b6fp+9"),
        ("Al", 2500.0, "0x1.648df73bbdc97p+14"),
        ("Si", 1700.0, "0x1.17cbb32585176p-4"),
        ("Si", 1300.0, "0x1.990ee4d9b02a2p-17"),
        ("Si", 1500.0, "0x1.af247338619d5p-10"),
        ("Si", 2000.0, "0x1.1a3f712996198p+2"),
        ("Si", 2500.0, "0x1.e0ff6e56f9f89p+8"),
        ("Mn", 1600.0, "0x1.7579b3631de68p+8"),
        ("Mn", 1300.0, "0x1.dc7f92d270a8bp+1"),
        ("Mn", 1500.0, "0x1.a13ea9f204004p+6"),
        ("Mn", 2000.0, "0x1.ac875c0b751b2p+13"),
        ("Mn", 2500.0, "0x1.af2f2d235d00dp+17"),
    ],
)
def test_t1127_current_pure_component_pressure_pin(
    vapor_pressure_data, species, temperature_K, expected_hex
):
    row = vapor_pressure_data["metals"][species]
    coefficients, _ = vapor_pressure_antoine_coefficients(row, temperature_K)
    pressure_pa = 10.0 ** _antoine_log10_pressure(
        float(coefficients["A"]),
        float(coefficients["B"]),
        float(coefficients.get("C", 0.0)),
        temperature_K,
    )

    assert pressure_pa.hex() == expected_hex


@pytest.mark.parametrize(
    ("species", "condensed_table", "gas_table", "fit_range_K"),
    [
        ("Al", "Al-003", "Al-005", (1000.0, 2700.0)),
        ("Si", "Si-003", "Si-005", (1700.0, 3500.0)),
        ("Mn", "Mn-003", "Mn-005", (1519.0, 2334.526)),
    ],
)
def test_t1127_reported_residual_is_measured_on_full_janaf_node_set(
    vapor_pressure_data,
    species,
    condensed_table,
    gas_table,
    fit_range_K,
):
    def gibbs_nodes(table_id):
        table = yaml.safe_load(
            (JANAF_TABLES / f"{table_id}.yaml").read_text()
        )["table"]
        return {
            float(row["temperature"]["value"]): float(
                row["formation_gibbs_energy"]["value"]
            )
            for row in table["values"]
            if row.get("temperature", {}).get("value") is not None
            and row.get("formation_gibbs_energy", {}).get("value") is not None
        }

    condensed = gibbs_nodes(condensed_table)
    gas = gibbs_nodes(gas_table)
    temperatures = sorted(
        temperature
        for temperature in condensed.keys() & gas.keys()
        if fit_range_K[0] <= temperature <= fit_range_K[1]
    )
    row = vapor_pressure_data["metals"][species]
    if species == "Mn":
        fit = row["pure_component_antoine"]["segments"][1]
    else:
        fit = row["pure_component_antoine"]
    residuals = []
    for temperature_K in temperatures:
        coefficients, _ = vapor_pressure_antoine_coefficients(row, temperature_K)
        log10_fit_pa = _antoine_log10_pressure(
            float(coefficients["A"]),
            float(coefficients["B"]),
            float(coefficients.get("C", 0.0)),
            temperature_K,
        )
        log10_source_pa = 5.0 - (
            gas[temperature_K] - condensed[temperature_K]
        ) / (R_KJ_PER_MOL_K * temperature_K * math.log(10.0))
        residuals.append(log10_fit_pa - log10_source_pa)

    assert len(temperatures) == fit["fit_node_count"]
    assert max(abs(residual) for residual in residuals) == pytest.approx(
        fit["max_abs_log10_residual_vs_source"], abs=5e-10
    )
