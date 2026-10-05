"""Post-refit numeric pins and JANAF residual checks for Al and Si."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from engines.antoine import _antoine_log10_pressure
from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients
from simulator.diagnostic_helpers.species_rail_differential import (
    KeyedTablePoint,
    log10_psat_over_P0_from_dfg,
)


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_hex"),
    [
        ("Al", 1000.0, "0x1.5181486a8527ep-18"),
        ("Al", 1300.0, "0x1.ea6a010b62ec5p-6"),
        ("Al", 1500.0, "0x1.58b4a63950985p+0"),
        ("Al", 2000.0, "0x1.30022ce844b6fp+9"),
        ("Al", 2500.0, "0x1.648df73bbdc97p+14"),
        ("Si", 1700.0, "0x1.17cbb32585176p-4"),
        ("Si", 1300.0, "0x1.0df20233d469ep-18"),
        ("Si", 1500.0, "0x1.0c42a9fce740bp-10"),
        ("Si", 2000.0, "0x1.1a3f712996198p+2"),
        ("Si", 2500.0, "0x1.e0ff6e56f9f89p+8"),
        ("Mn", 1600.0, "0x1.6e6d7e7b3b8abp+8"),
        ("Mn", 1300.0, "0x1.dc7f92d270a8bp+1"),
        ("Mn", 1500.0, "0x1.a13ea9f204004p+6"),
        ("Mn", 2000.0, "0x1.aaeac8e781922p+13"),
        ("Mn", 2500.0, "0x1.af6ce960ee404p+17"),
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


def _janaf_gibbs_nodes(monkeypatch):
    from pathlib import Path

    from simulator.diagnostic_helpers import species_rail_differential as differential

    tables_dir = (
        Path(__file__).resolve().parents[2]
        / "data/literature/compilations/janaf/tables"
    )
    table_ids = ("Al-003", "Al-005", "Si-002", "Si-003", "Si-005")
    monkeypatch.setattr(
        differential,
        "iter_table_paths",
        lambda: [tables_dir / f"{table_id}.yaml" for table_id in table_ids],
    )
    rail = SimpleNamespace(in_rail=lambda formula: formula in {"Al", "Si"})
    points = differential.iter_janaf_keyed_points(rail)
    return {
        (point.record_id, point.T_K): point.delta_fG_kJ_mol
        for point in points
        if isinstance(point, KeyedTablePoint)
        and point.record_id in {"Al-003", "Al-005", "Si-002", "Si-003", "Si-005"}
    }


@pytest.mark.parametrize(
    (
        "species",
        "condensed_table",
        "gas_table",
        "fit_range_K",
        "fit_name",
        "expected_count",
    ),
    [
        ("Al", "Al-003", "Al-005", (1000.0, 2700.0), None, 18),
        ("Si", "Si-003", "Si-005", (1700.0, 3500.0), "liquid_janaf", 19),
        ("Si", "Si-002", "Si-005", (100.0, 1600.0), "crystal_janaf", 20),
    ],
)
def test_t1127_reported_residual_is_measured_on_full_janaf_node_set(
    vapor_pressure_data,
    species,
    condensed_table,
    gas_table,
    fit_range_K,
    fit_name,
    expected_count,
    monkeypatch,
):
    nodes = _janaf_gibbs_nodes(monkeypatch)
    temperatures = sorted(
        temperature
        for table, temperature in nodes
        if table == condensed_table
        and (gas_table, temperature) in nodes
        and fit_range_K[0] <= temperature <= fit_range_K[1]
    )
    row = vapor_pressure_data["metals"][species]
    if fit_name is None:
        fit = row["pure_component_antoine"]
    else:
        fit = next(
            segment
            for segment in row["pure_component_antoine"]["segments"]
            if segment["name"] == fit_name
        )
    residuals = []
    for temperature_K in temperatures:
        coefficients, _ = vapor_pressure_antoine_coefficients(row, temperature_K)
        log10_fit_pa = _antoine_log10_pressure(
            float(coefficients["A"]),
            float(coefficients["B"]),
            float(coefficients.get("C", 0.0)),
            temperature_K,
        )
        log10_source_pa = 5.0 + log10_psat_over_P0_from_dfg(
            nodes[(gas_table, temperature_K)],
            nodes[(condensed_table, temperature_K)],
            temperature_K,
        )
        residuals.append(log10_fit_pa - log10_source_pa)

    assert len(temperatures) == expected_count == fit["fit_node_count"]
    assert max(abs(residual) for residual in residuals) == pytest.approx(
        fit["max_abs_log10_residual_vs_source"], abs=5e-10
    )


def test_si_crystal_sidecar_joins_the_liquid_segment_at_melting_point(
    vapor_pressure_data,
):
    row = vapor_pressure_data["metals"]["Si"]
    pure = row["pure_component_antoine"]
    crystal = next(segment for segment in pure["segments"] if segment["phase"] == "crystal")
    liquid = next(segment for segment in pure["segments"] if segment["phase"] == "liquid")
    selected_crystal, _ = vapor_pressure_antoine_coefficients(row, 1500.0)
    selected_liquid, _ = vapor_pressure_antoine_coefficients(row, 1800.0)
    crystal_log_pa = _antoine_log10_pressure(
        crystal["A"], crystal["B"], crystal["C"], 1685.0
    )
    liquid_log_pa = _antoine_log10_pressure(
        liquid["A"], liquid["B"], liquid["C"], 1685.0
    )

    assert selected_crystal["phase"] == "crystal"
    assert selected_liquid["phase"] == "liquid"
    assert crystal_log_pa == pytest.approx(liquid_log_pa, abs=1e-12)
    assert crystal["join_step_log10_dex"] == pytest.approx(0.0, abs=1e-12)
