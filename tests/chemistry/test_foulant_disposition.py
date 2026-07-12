"""Pure-function tests for engines.builtin.foulant_disposition (chunk H0)."""

from __future__ import annotations

import ast
import math
import textwrap
from pathlib import Path

import pytest

from engines.builtin.foulant_disposition import (
    GAS_CONSTANT_J_PER_MOL_K,
    NOT_SPECIFIED,
    UNGROUNDABLE_PROCESS_EXTENT,
    CarbonPartition,
    DispositionExtent,
    DispositionInterval,
    EscapeSplit,
    FoulantRegistry,
    _derive_sigmoid_width_C,
    _interpolate_onset_K,
    _parse_dg_points,
    _sigmoid_extent,
    chi_decomp,
    chi_escape_salt,
    chi_refractory,
    load_foulant_registry,
    partition_carbon,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "engines" / "builtin" / "foulant_disposition.py"


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


@pytest.fixture
def foulant_registry_yaml(tmp_path: Path) -> Path:
    content = textwrap.dedent(
        """
        foulant_dG:
          CaSO4_thermal_decomp:
            points:
              - {T_K: 1373.15, dG_kJ_per_mol: 50.0}
              - {T_K: 1473.15, dG_kJ_per_mol: -50.0}
              - {T_K: 1573.15, dG_kJ_per_mol: -120.0}
        CaSO4:
          carrier:
            species: CaSO4
            aliases: [caso4]
            molar_mass_g_mol: 136.14
          group: other_mineral_contaminant
          reaction_family: sulfate_decomp
          thermo:
            dG_row: foulant_dG.CaSO4_thermal_decomp
            source: JANAF tier-2 fixture
          gating:
            chi_model: dG_sigmoid
            o2_dependence: suppresses
            o2_reference_bar: 0.2
            requires_reagent: null
            carbothermic_onset_C: 1000
          fate:
            on_decompose_offgas: {account: terminal.offgas, species: [SO2]}
          warning_flags:
            confidence: well_grounded
        NaCl:
          carrier:
            species: NaCl
            aliases: [nacl, halide]
            molar_mass_g_mol: 58.44
          group: other_mineral_contaminant
          reaction_family: volatilization
          thermo:
            antoine_row: foulant_vapor.NaCl
          gating:
            chi_model: vapor_psat_vs_overhead
          fate:
            on_escape: {account: evaporation, residual_target: NaCl}
          warning_flags:
            confidence: partly_grounded
        """
    ).strip()
    path = tmp_path / "foulant_thermo.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_layer_guard_no_melt_backend_or_inventory_imports() -> None:
    modules = _imported_modules(MODULE_PATH)
    names = _referenced_names(MODULE_PATH)
    assert "simulator.melt_backend" not in modules
    assert "simulator.inventory" not in modules
    assert all("inventory" not in module for module in modules)
    assert "simulator.accounting.ledger" not in modules
    assert all("ledger" not in module for module in modules)
    assert "LedgerTransitionProposal" not in names
    assert "AtomLedger" not in names


def test_chi_escape_salt_returns_escape_split_dataclass() -> None:
    result = chi_escape_salt("NaCl", 1200.0, 0.2)
    assert isinstance(result, EscapeSplit)
    assert result.escaped_frac == pytest.approx(0.354, abs=0.02)
    assert result.retained_frac == pytest.approx(1.0 - result.escaped_frac, abs=1e-9)
    assert 0.0 <= result.escaped_frac <= 1.0
    assert result.status == "ok"
    assert result.confidence == "partly_grounded"
    assert result.warning is None


def test_chi_escape_salt_flags_out_of_range_non_authoritative_result() -> None:
    result = chi_escape_salt("NaCl", 1500.0, 0.01)
    assert isinstance(result, EscapeSplit)
    assert result.escaped_frac == pytest.approx(0.992, abs=0.002)
    assert result.retained_frac == pytest.approx(1.0 - result.escaped_frac, abs=1e-9)
    assert result.status == "out_of_range"
    assert result.confidence == "extrapolated"
    assert result.warning is not None
    assert "outside valid_range_K [1138, 1738]" in result.warning
    assert "non-authoritative extrapolation" in result.warning


def test_chi_escape_salt_high_vacuum_fraction() -> None:
    result = chi_escape_salt("KCl", 1200.0, 0.001)
    assert isinstance(result, EscapeSplit)
    assert result.escaped_frac > 0.9


def test_chi_decomp_returns_disposition_extent_with_derived_width(
    foulant_registry_yaml: Path,
) -> None:
    registry = load_foulant_registry(foulant_registry_yaml)
    assert isinstance(registry, FoulantRegistry)

    low_pO2 = chi_decomp("CaSO4", 1300.0, 0.01, 0.0, registry)
    high_pO2 = chi_decomp("CaSO4", 1300.0, 0.2, 0.0, registry)

    assert isinstance(low_pO2, DispositionExtent)
    assert isinstance(high_pO2, DispositionExtent)
    assert low_pO2.path == "thermal"
    assert low_pO2.extent > high_pO2.extent
    assert 1100.0 < low_pO2.onset_K - 273.15 < 1300.0

    carb = chi_decomp("CaSO4", 1100.0, 0.01, 10.0, registry)
    assert carb.path == "carbothermic"
    assert carb.extent > 0.0
    assert carb.onset_K == pytest.approx(1000.0 + 273.15)


def test_sigmoid_width_is_physical_logistic_not_step(
    foulant_registry_yaml: Path,
) -> None:
    registry = load_foulant_registry(foulant_registry_yaml)
    dg_row = registry.foulant_dG["CaSO4_thermal_decomp"]
    dg_points = _parse_dg_points(dg_row)
    onset_k = _interpolate_onset_K(dg_points)
    onset_c = onset_k - 273.15
    width_c = _derive_sigmoid_width_C(dg_points, onset_k)

    nearest = sorted(dg_points, key=lambda row: abs(row[0] - onset_k))[:2]
    (t0, dg0), (t1, dg1) = sorted(nearest, key=lambda row: row[0])
    slope_j_per_mol_k = abs((dg1 - dg0) / (t1 - t0)) * 1000.0
    assert width_c == pytest.approx(
        GAS_CONSTANT_J_PER_MOL_K * onset_k / slope_j_per_mol_k
    )
    extent_plus = _sigmoid_extent(onset_c + width_c, onset_c, width_c)
    extent_minus = _sigmoid_extent(onset_c - width_c, onset_c, width_c)
    assert extent_plus == pytest.approx(1.0 / (1.0 + math.e ** -1), rel=1e-6)
    assert extent_minus == pytest.approx(1.0 / (1.0 + math.e), rel=1e-6)
    assert extent_plus < 0.95
    assert extent_minus > 0.05


def test_parse_dg_points_fails_loud_on_invalid_data() -> None:
    with pytest.raises(ValueError, match="at least two points"):
        _parse_dg_points({"points": [{"T_K": 1000.0, "dG_kJ_per_mol": 1.0}]})
    with pytest.raises(ValueError, match="finite"):
        _parse_dg_points(
            {
                "points": [
                    {"T_K": 1000.0, "dG_kJ_per_mol": float("nan")},
                    {"T_K": 1100.0, "dG_kJ_per_mol": -1.0},
                ]
            }
        )
    with pytest.raises(ValueError, match="duplicate"):
        _parse_dg_points(
            {
                "points": [
                    {"T_K": 1000.0, "dG_kJ_per_mol": 1.0},
                    {"T_K": 1000.0, "dG_kJ_per_mol": -1.0},
                ]
            }
        )


def test_chi_refractory_ungrounded_without_scenario() -> None:
    result = chi_refractory([], 0.2, None)
    assert isinstance(result, DispositionInterval)
    assert result.low == 0.0
    assert result.high == 1.0
    assert result.certified_point is None
    assert result.reason == UNGROUNDABLE_PROCESS_EXTENT


def test_chi_refractory_certified_point_request_fails_loud() -> None:
    with pytest.raises(ValueError, match="certified-point refractory request refused"):
        chi_refractory([], 0.2, "certified_point")


def test_chi_refractory_named_scenario_returns_band() -> None:
    result = chi_refractory([(1050.0, 3600.0)], 0.2, "exposed_fine_powder_air_TGA")
    assert isinstance(result, DispositionInterval)
    assert result.low == pytest.approx(0.9)
    assert result.high == pytest.approx(1.0)
    assert result.reason is None


def test_partition_carbon_returns_dataclass_with_not_speciated_carbonate() -> None:
    source_row = {
        "f_refractory_organic_C": {
            "floor": 0.39,
            "iom_anchor": 0.56,
            "source": "sephton_2004_murchison_hydropyrolysis",
        },
        "f_carbonate_C": {
            "value": None,
            "status": NOT_SPECIFIED,
        },
    }
    result = partition_carbon("carbonaceous_organic", 100.0, source_row)
    assert isinstance(result, CarbonPartition)
    assert result.refractory_mol == pytest.approx(39.0)
    assert result.labile_mol == pytest.approx(61.0)
    assert result.carbonate_mol == NOT_SPECIFIED
    assert "f_carbonate_C" in result.not_speciated
    assert result.carbonate_mol != 0.0


def test_partition_carbon_missing_process_share_is_not_speciated() -> None:
    source_row = {
        "f_refractory_organic_C": {"floor": 0.39, "iom_anchor": 0.56},
        "f_carbonate_C": {"value": 0.1},
        "f_process_reductant_C": {"value": None, "status": NOT_SPECIFIED},
    }
    result = partition_carbon("carbonaceous_organic", 100.0, source_row)
    assert result.process_reductant_mol == NOT_SPECIFIED
    assert "f_process_reductant_C" in result.not_speciated
    assert result.process_reductant_mol != 0.0


def test_partition_carbon_subtracts_every_declared_bucket() -> None:
    source_row = {
        "f_refractory_organic_C": {"floor": 0.39},
        "f_carbonate_C": {"value": 0.10},
        "f_process_reductant_C": {"value": 0.20},
    }

    result = partition_carbon("carbonaceous_organic", 100.0, source_row)

    assert result.labile_mol == pytest.approx(31.0)
    assert sum(
        (
            result.labile_mol,
            result.refractory_mol,
            result.carbonate_mol,
            result.process_reductant_mol,
        )
    ) == pytest.approx(100.0)


def test_partition_carbon_rejects_overspecified_fractions() -> None:
    source_row = {
        "f_refractory_organic_C": {"floor": 0.7},
        "f_carbonate_C": {"value": 0.2},
        "f_process_reductant_C": {"value": 0.2},
    }

    with pytest.raises(ValueError, match="exceed declared carbon"):
        partition_carbon("carbonaceous_organic", 100.0, source_row)


def test_load_foulant_registry_builds_alias_index(foulant_registry_yaml: Path) -> None:
    registry = load_foulant_registry(foulant_registry_yaml)
    assert registry.alias_to_carrier["halide"] == "NaCl"
    assert registry.alias_to_carrier["caso4"] == "CaSO4"
    assert registry.carriers["NaCl"].reaction_family == "volatilization"
    assert registry.carriers["NaCl"].fate["on_escape"]["account"] == "evaporation"
