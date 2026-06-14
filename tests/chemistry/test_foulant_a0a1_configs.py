"""Ground-truth tests for A0/A1 foulant config YAML (chunk foulant-A0A1-configs)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engines.builtin.foulant_disposition import (
    NOT_SPECIFIED,
    chi_decomp,
    load_foulant_registry,
    partition_carbon,
)
from engines.builtin.foulant_disposition import (
    _derive_sigmoid_width_C,
    _interpolate_onset_K,
    _parse_dg_points,
)
from simulator.optimize.evalspec import REQUIRED_DATA_DIGEST_KEYS

REPO_ROOT = Path(__file__).resolve().parents[2]
FOULANT_THERMO = REPO_ROOT / "data" / "foulant_thermo.yaml"
CARBON_PARTITION = REPO_ROOT / "data" / "stage0_carbon_partition.yaml"

DG_SIGMOID_CARRIERS = (
    "CaSO4",
    "MgSO4",
    "FeSO4",
    "CaCO3",
    "MgCO3",
    "Na2CO3",
    "Mg_ClO4_2",
    "Ca_ClO4_2",
    "FeS",
    "CaS",
    "NiS",
)

# C0-corrected evidence anchors (evidence-E1-dissociation.md, impl-notes-C0-corrections.md)
E1_CORRECTED_ANCHORS = {
    "CaSO4_thermal_decomp": {1573.15: 57.0, 1673.15: 28.0},
    "Na2CO3_silicate_displacement": {1173.15: -84.0},
    "MgSO4_thermal_decomp": {1573.15: -64.0},
    "FeSO4_thermal_decomp": {1173.15: -20.0},
}


def _load_carbon_partition() -> dict:
    with CARBON_PARTITION.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_foulant_yaml_not_in_required_digest_keys() -> None:
    assert "foulant_thermo" not in REQUIRED_DATA_DIGEST_KEYS
    assert "stage0_carbon_partition" not in REQUIRED_DATA_DIGEST_KEYS


def test_load_foulant_registry_parses_all_carrier_blocks() -> None:
    registry = load_foulant_registry(FOULANT_THERMO)
    expected_keys = {
        "NaCl",
        "KCl",
        "NaF",
        "CaSO4",
        "MgSO4",
        "FeSO4",
        "CaCO3",
        "MgCO3",
        "Na2CO3",
        "Mg_ClO4_2",
        "Ca_ClO4_2",
        "FeS",
        "CaS",
        "NiS",
    }
    assert set(registry.carriers) == expected_keys
    assert registry.alias_to_carrier["halide"] == "NaCl"
    assert registry.alias_to_carrier["oldhamite"] == "CaS"
    assert registry.alias_to_carrier["troilite"] == "FeS"
    assert registry.alias_to_carrier["perchlorate"] == "Mg_ClO4_2"


@pytest.mark.parametrize("dg_key", list(E1_CORRECTED_ANCHORS))
def test_foulant_dg_rows_match_c0_corrected_evidence(dg_key: str) -> None:
    with FOULANT_THERMO.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    row = payload["foulant_dG"][dg_key]
    points = {float(p["T_K"]): float(p["dG_kJ_per_mol"]) for p in row["points"]}
    for t_k, expected_dg in E1_CORRECTED_ANCHORS[dg_key].items():
        assert points[t_k] == pytest.approx(expected_dg, abs=1e-9)


def test_each_foulant_dg_row_has_zero_crossing_and_derivable_slope() -> None:
    with FOULANT_THERMO.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    for dg_key, row in payload["foulant_dG"].items():
        points = _parse_dg_points(row)
        onset_k = _interpolate_onset_K(points)
        width_c = _derive_sigmoid_width_C(points, onset_k)
        assert onset_k > 0.0
        assert width_c > 0.0


@pytest.mark.parametrize("carrier", DG_SIGMOID_CARRIERS)
def test_chi_decomp_extent_rises_above_onset_and_falls_below(carrier: str) -> None:
    registry = load_foulant_registry(FOULANT_THERMO)
    low_t = chi_decomp(carrier, 200.0, 0.2, 0.0, registry)
    high_t = chi_decomp(carrier, 1600.0, 0.2, 0.0, registry)
    assert high_t.extent > low_t.extent
    assert 0.0 <= low_t.extent <= 1.0
    assert 0.0 <= high_t.extent <= 1.0


def test_caso4_o2_suppression_uses_corrected_dg_onset() -> None:
    registry = load_foulant_registry(FOULANT_THERMO)
    low_pO2 = chi_decomp("CaSO4", 1450.0, 0.01, 0.0, registry)
    high_pO2 = chi_decomp("CaSO4", 1450.0, 0.2, 0.0, registry)
    assert low_pO2.extent > high_pO2.extent
    assert 1450.0 < low_pO2.onset_K - 273.15 < 1550.0


@pytest.mark.parametrize(
    "feedstock_key",
    ["ci_carbonaceous_chondrite", "cm_carbonaceous_chondrite", "ceres_regolith"],
)
def test_partition_carbon_sephton_anchors_not_speciated_carbonate(
    feedstock_key: str,
) -> None:
    payload = _load_carbon_partition()
    row = payload["phase_partitions"][feedstock_key]
    result = partition_carbon("carbonaceous_organic", 100.0, row)
    assert result.refractory_mol == pytest.approx(39.0)
    assert result.labile_mol == pytest.approx(61.0)
    assert result.carbonate_mol == NOT_SPECIFIED
    assert result.carbonate_mol != 0.0
    assert "f_carbonate_C" in result.not_speciated


def test_partition_carbon_comet_refractory_interval_not_coerced_to_zero() -> None:
    payload = _load_carbon_partition()
    row = payload["phase_partitions"]["comet_nucleus"]
    refractory = row["f_refractory_organic_C"]
    assert refractory.get("interval") == [0.0, 1.0]
    assert refractory.get("status") == NOT_SPECIFIED
    result = partition_carbon("organics", 100.0, row)
    assert result.refractory_mol == NOT_SPECIFIED
    assert result.labile_mol == NOT_SPECIFIED
    assert "f_refractory_organic_C" in result.not_speciated
    assert result.refractory_mol != 0.0


def test_sephton_floor_is_product_not_tuned_constant() -> None:
    payload = _load_carbon_partition()
    row = payload["phase_partitions"]["cm_carbonaceous_chondrite"]
    floor = row["f_refractory_organic_C"]["floor"]
    iom = row["f_refractory_organic_C"]["iom_anchor"]
    assert iom == pytest.approx(0.56)
    assert floor == pytest.approx(0.39)
    assert floor == pytest.approx(0.70 * iom, rel=0.01)