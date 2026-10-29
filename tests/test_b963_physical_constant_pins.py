"""b-963: bit pins for AlphaMELTS O/O2 and offgas CEA constants."""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

from simulator.chemistry.offgas_fo2 import (
    _load_cea_polynomials_from_text,
    imposed_fo2,
    load_buffer_polynomials,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import EquilibriumResult


def test_alphamelt_oxygen_projection_grid_is_bit_pinned() -> None:
    backend = AlphaMELTSBackend()
    expected_hex = (
        ("0x1.a36e2eb1c432dp-14", "0x1.bbe8dffcb7269p-14"),
        ("0x1.0624dd2f1a9fcp-10", "0x1.ecc418a18ed20p-11"),
        ("0x1.4f8b588e368f1p-17", "0x1.f0240f14f8618p-13"),
    )
    for (temperature_C, fO2_log), expected in zip(
        ((1400.0, -9.0), (1500.0, -8.0), (1600.0, -10.0)),
        expected_hex,
    ):
        equilibrium = EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=1.0,
            fO2_log=fO2_log,
            phases_present=["liquid"],
            phase_masses_kg={"liquid": 1.0},
            liquid_fraction=1.0,
            liquid_composition_wt_pct={
                "SiO2": 50.0,
                "Al2O3": 15.0,
                "FeO": 10.0,
                "MgO": 10.0,
                "CaO": 10.0,
                "Na2O": 5.0,
            },
            status="ok",
        )
        pressures, _sources, _diagnostics = (
            backend._builtin_vapor_projection_for_subprocess(equilibrium)
        )
        assert (pressures["O2"].hex(), pressures["O"].hex()) == expected


def test_offgas_fo2_grid_is_bit_pinned() -> None:
    polynomials = load_buffer_polynomials()
    grid = (
        (800.0, {"H2": 1.0, "H2O": 1.0}, "-0x1.a9283fb13425ap+4", None),
        (1100.0, {"H2": 1.0, "H2O": 1.0}, "-0x1.1c2fc932c8e7dp+4", None),
        (1200.0, {"H2": 1.0, "H2O": 1.0}, "-0x1.f96266db00679p+3", None),
        (
            1473.15,
            {"H2": 3.0, "H2O": 0.4, "CH4": 1.2, "H2S": 0.2, "CO2": 0.15},
            "-0x1.a6da5836e29a5p+3",
            "-0x1.1d75e137677b3p-3",
        ),
    )
    for temperature_K, gas, expected_log_fO2, expected_extent in grid:
        result = imposed_fo2(gas, temperature_K, polynomials)
        assert result.log10_fO2.hex() == expected_log_fO2
        assert (
            None if result.extent_mol is None else result.extent_mol.hex()
        ) == expected_extent


def test_cea_loader_default_reference_pressure_is_bit_pinned() -> None:
    source_text = """
species:
  O:
    observations:
    - type: gibbs_table
      phase: gas
      values:
        evaluator_family: nasa_cea_9
        segments:
        - T_min_K: 300.0
          T_max_K: 6000.0
          a_coefficients: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
          b1: 0.0
          b2: 0.0
          exponents: [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 0.0]
"""
    polynomials = dict(
        _load_cea_polynomials_from_text(("O",), source_text, "b-963 pin")
    )
    assert polynomials["O"].reference_pressure_Pa.hex() == "0x1.86a0000000000p+16"


def test_melt_modules_do_not_reintroduce_bare_bar_or_kelvin_constants() -> None:
    repo = Path(__file__).resolve().parents[1]
    targets = (
        (repo / "simulator/melt_backend/alphamelts.py", "alphamelts"),
        (repo / "simulator/chemistry/offgas_fo2.py", "offgas_fo2"),
    )
    for path, module in targets:
        source = path.read_text(encoding="utf-8")
        code_lines = source.splitlines()
        bare_literals = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.NUMBER:
                continue
            try:
                value = float(token.string.replace("_", ""))
            except ValueError:
                continue
            if value not in {273.15, 100_000.0}:
                continue
            line = code_lines[token.start[0] - 1].strip()
            if module == "alphamelts" and line == (
                "if pressure_bar == 0.0 and failed_temperature_C == -273.15:"
            ):
                continue
            bare_literals.append((token.start[0], token.string))
        assert bare_literals == [], f"{path.relative_to(repo)}: {bare_literals}"
