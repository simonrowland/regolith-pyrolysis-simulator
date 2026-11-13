"""Pure-function tests for the shared oxygen-buffer owner (chunk CCO-0)."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from simulator.oxygen_buffers import (
    CCO_FORMULATION,
    RedoxBufferInterval,
    RedoxBufferPoint,
    cco_buffered_fO2,
    cco_log10_fO2_bar,
    cco_log10_fO2_interval_for_pressure_range,
    CCO_OUT_OF_BAND_NOTICE,
    CCO_PRESSURE_RANGE_BAR,
    CCO_TEMPERATURE_RANGE_K,
    qfm_log10_fO2_bar,
    stagno_frost_emog_emod_log10_fO2_interval,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "simulator" / "oxygen_buffers.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
    return modules


def _referenced_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


@pytest.mark.parametrize(
    ("temperature_K", "pressure_bar", "expected_log10_fO2"),
    (
        (1473.15, 1.0, -17.12178614753933),
        (1723.15, 1.0e-6, -27.92892926108145),
        (1873.15, 1.0, -15.359251023327852),
    ),
)
def test_cco_anchor_values(
    temperature_K: float,
    pressure_bar: float,
    expected_log10_fO2: float,
) -> None:
    result = cco_buffered_fO2(temperature_K, pressure_bar, reference_buffer="QFM")
    assert isinstance(result, RedoxBufferPoint)
    assert result.formulation == CCO_FORMULATION
    assert result.log10_fO2_bar == pytest.approx(expected_log10_fO2, abs=1e-6)
    assert result.fO2_bar == pytest.approx(10.0**expected_log10_fO2)


def test_cco_fit_point_provenance_credits_jakobsson_oskarsson() -> None:
    """The fitted point inside the cited field credits Jakobsson & Oskarsson."""
    result = cco_buffered_fO2(1500.0, 10_000.0, reference_buffer="QFM")
    assert "stagno" not in result.formulation.lower()
    assert "stagnofrost" not in result.formulation.lower()
    assert "jakobsson" in result.formulation.lower()
    assert "jakobsson" in result.source.lower()
    assert result.validity == "inside_cco_jakobsson_oskarsson_calibration"


def test_reference_delta_uses_absolute_log10_minus_qfm() -> None:
    temperature_K = 1723.15
    result = cco_buffered_fO2(temperature_K, 1.0, reference_buffer="QFM")
    qfm = qfm_log10_fO2_bar(temperature_K)

    assert result.reference_log10_fO2_bar == pytest.approx(qfm)
    assert result.delta_log10_fO2_from_reference == pytest.approx(
        result.log10_fO2_bar - qfm
    )
    assert result.delta_log10_fO2_from_reference < 0.0


def test_oneill_qfm_reference_anchor() -> None:
    assert qfm_log10_fO2_bar(1473.15) == -8.424378372874452


def test_cco_log10_fO2_increases_with_temperature() -> None:
    with pytest.warns(RuntimeWarning, match=CCO_OUT_OF_BAND_NOTICE):
        low_t = cco_log10_fO2_bar(1473.15, 1.0e-6)
        mid_t = cco_log10_fO2_bar(1723.15, 1.0e-6)
        high_t = cco_log10_fO2_bar(1873.15, 1.0e-6)

    assert low_t < mid_t < high_t


def test_cco_fit_outputs_are_bit_pinned_inside_calibrated_field() -> None:
    output_grid = (
        (1373.15, 7_500.0, "-0x1.53d0bdd6d2af9p+3"),
        (1473.15, 10_000.0, "-0x1.2a112bd90b710p+3"),
        (1723.15, 15_000.0, "-0x1.b5baf72138a33p+2"),
        (1773.15, 20_000.0, "-0x1.82b8acb1bc62ep+2"),
    )

    assert CCO_TEMPERATURE_RANGE_K == (1373.15, 1773.15)
    assert CCO_PRESSURE_RANGE_BAR == (7500.0, 20000.0)
    for temperature_K, pressure_bar, expected in output_grid:
        assert cco_log10_fO2_bar(temperature_K, pressure_bar).hex() == expected


def test_closed_cco_fluid_uses_janaf_positive_root_and_flags_fit_fallback() -> None:
    # NIST-JANAF C-093 rows 1600/1700 K: -252.284/-260.784 kJ/mol;
    # C-095 rows 1600/1700 K: -396.323/-396.344 kJ/mol. Their interpolated
    # formation Gibbs energies give the positive C–O fluid root at 1673.15 K.
    point = cco_buffered_fO2(1673.15, 1.0, reference_buffer=None)
    assert point.log10_fO2_bar == pytest.approx(-16.140341787742, abs=1e-12)
    assert point.validity == (
        "cco_fit_out_of_band_ideal_gas_used; T_K=[1373.15,1773.15]; "
        "P_bar=[7500,20000]"
    )
    assert point.fO2_bar == pytest.approx(10.0 ** point.log10_fO2_bar)


def test_unsupported_reference_buffer_fails_loud() -> None:
    with pytest.raises(ValueError, match="unsupported reference_buffer"):
        cco_buffered_fO2(1723.15, reference_buffer="IW")


def test_purity_no_ledger_provider_or_melt_imports() -> None:
    modules = _imported_modules(MODULE_PATH)
    names = _referenced_names(MODULE_PATH)

    assert modules <= {
        "__future__",
        "__future__.annotations",
        "dataclasses",
        "dataclasses.dataclass",
        "decimal",
        "decimal.Decimal",
        "math",
        "warnings",
        "simulator.environment",
        "simulator.environment.DEFAULT_VACUUM_FLOOR_BAR",
        # The dependency-free constants owner (t-1748): stdlib-only, so it
        # cannot pull ledger, provider, inventory or melt code into the buffer.
        "simulator.physical_constants",
        "simulator.physical_constants.GAS_CONSTANT",
        "simulator.reference_data.janaf",
        "simulator.reference_data.janaf._formation_gibbs_energy_kj_per_mol_at",
        "simulator.scalar_boundary",
        "simulator.scalar_boundary.is_declared_real_scalar",
    }
    assert not (REPO_ROOT / "simulator" / "chemistry" / "qfm_oneill.py").exists()
    assert not (REPO_ROOT / "engines" / "builtin" / "cco_redox_buffer.py").exists()
    assert all("melt_backend" not in module for module in modules)
    assert all("inventory" not in module for module in modules)
    assert all("ledger" not in module for module in modules)
    assert all("provider" not in module for module in modules)
    assert "LedgerTransitionProposal" not in names
    assert "AtomLedger" not in names


def test_pressure_range_query_returns_interval_not_fabricated_point() -> None:
    interval = cco_log10_fO2_interval_for_pressure_range(1723.15, (1.0e-6, 1.0))

    assert isinstance(interval, RedoxBufferInterval)
    assert interval.certified_point is None
    assert "pressure_range_not_certified_point" in interval.reason
    assert CCO_OUT_OF_BAND_NOTICE in interval.reason
    assert "P_bar=[7500,20000]" in interval.reason
    assert interval.low_log10_fO2_bar < interval.high_log10_fO2_bar
    assert 10.0 < interval.high_log10_fO2_bar - interval.low_log10_fO2_bar < 12.0


def test_emog_emod_uncertainty_is_interval_only() -> None:
    interval = stagno_frost_emog_emod_log10_fO2_interval(-9.2, -8.7)

    assert isinstance(interval, RedoxBufferInterval)
    assert interval.low_log10_fO2_bar == pytest.approx(-9.2)
    assert interval.high_log10_fO2_bar == pytest.approx(-8.7)
    assert interval.certified_point is None
    assert interval.reason == "emog_emod_coefficients_not_certified_for_point_use"


def test_invalid_inputs_fail_loud() -> None:
    for bad in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="temperature_K"):
            cco_log10_fO2_bar(bad, 1.0)
        with pytest.raises(ValueError, match="pressure_bar"):
            cco_log10_fO2_bar(1723.15, bad)
