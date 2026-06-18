"""Pure-function tests for engines.builtin.cco_redox_buffer (chunk CCO-0)."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from engines.builtin.cco_redox_buffer import (
    CCO_FORMULATION,
    RedoxBufferInterval,
    RedoxBufferPoint,
    cco_buffered_fO2,
    cco_log10_fO2_bar,
    cco_log10_fO2_interval_for_pressure_range,
    qfm_log10_fO2_bar,
    stagno_frost_emog_emod_log10_fO2_interval,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "engines" / "builtin" / "cco_redox_buffer.py"


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
        (1473.15, 1.0, -10.475257950649965),
        (1723.15, 1.0e-6, -8.32808940012709),
        (1873.15, 1.0, -7.314751221204921),
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


def test_certified_cco_point_provenance_credits_jakobsson_oskarsson_not_stagno_frost() -> None:
    """The certified CCO point originates from Jakobsson & Oskarsson 1994 (via
    LEPR/ThermoEngine); Stagno & Frost 2010 only supplies graphite-saturation
    context (EMOG/EMOD). The point's formulation/source must NOT name Stagno &
    Frost as origin (mandate clause: provenance honesty / no over-attribution).
    """
    result = cco_buffered_fO2(1723.15, 1.0, reference_buffer="QFM")
    assert "stagno" not in result.formulation.lower()
    assert "stagnofrost" not in result.formulation.lower()
    assert "jakobsson" in result.formulation.lower()
    # Source string may CITE Stagno & Frost as context, but must credit
    # Jakobsson & Oskarsson for the point formula.
    assert "jakobsson" in result.source.lower()
    assert "inside_cco_reference_range" in result.validity


def test_reference_delta_uses_absolute_log10_minus_qfm() -> None:
    temperature_K = 1723.15
    result = cco_buffered_fO2(temperature_K, 1.0, reference_buffer="QFM")
    qfm = qfm_log10_fO2_bar(temperature_K)

    assert qfm == pytest.approx(8.58 - 25050.0 / temperature_K)
    assert result.reference_log10_fO2_bar == pytest.approx(qfm)
    assert result.delta_log10_fO2_from_reference == pytest.approx(
        result.log10_fO2_bar - qfm
    )
    assert result.delta_log10_fO2_from_reference < 0.0


def test_cco_log10_fO2_increases_with_temperature() -> None:
    low_t = cco_log10_fO2_bar(1473.15, 1.0e-6)
    mid_t = cco_log10_fO2_bar(1723.15, 1.0e-6)
    high_t = cco_log10_fO2_bar(1873.15, 1.0e-6)

    assert low_t < mid_t < high_t


def test_low_overhead_pressure_term_is_small_near_graphite_saturation() -> None:
    temperature_K = 1723.15
    vacuum = cco_log10_fO2_bar(temperature_K, 1.0e-6)
    one_bar = cco_log10_fO2_bar(temperature_K, 1.0)

    assert abs(vacuum - one_bar) < 2e-4


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
        "math",
    }
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
    assert interval.reason == "pressure_range_not_certified_point"
    assert interval.low_log10_fO2_bar < interval.high_log10_fO2_bar
    assert interval.high_log10_fO2_bar - interval.low_log10_fO2_bar < 2e-4


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
