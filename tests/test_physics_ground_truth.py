from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.metallothermic_step import BuiltinMetallothermicStepProvider
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.state import MOLAR_MASS


PA_PER_ATM = 101_325.0
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _vapor_pressure_data() -> dict:
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        return yaml.safe_load(handle)


def _antoine_pa(entry: dict, temperature_K: float) -> float:
    coeff = entry["antoine"]
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _pure_component_antoine_pa(entry: dict, temperature_K: float) -> float:
    coeff = entry["pure_component_antoine"]
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _antoine_pa(entry: dict, temperature_K: float) -> float:
    coeff = entry["antoine"]
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _chi_escape_equilibrium(p_sat_pa: float, p_total_pa: float) -> float:
    return p_sat_pa / (p_sat_pa + p_total_pa)


def _require_certified_pure_component_antoine(entry: dict, temperature_K: float) -> float:
    if entry.get("interval_required"):
        raise ValueError(
            f"certified-point request refused for interval-only row "
            f"(confidence={entry.get('confidence')!r})"
        )
    if "pure_component_antoine" not in entry:
        raise KeyError("pure_component_antoine")
    return _pure_component_antoine_pa(entry, temperature_K)


@pytest.mark.parametrize(
    ("species", "temperature_K", "rel_tol"),
    [
        ("Na", 1156.15, 0.02),  # NIST TN 2273 / CRC: Na normal boiling point 883 C.
        ("K", 1032.15, 0.02),  # NIST TN 2273 / CRC: K normal boiling point 759 C.
        ("Mg", 1364.15, 0.02),  # CRC/NIST element data: Mg normal boiling point 1091 C.
        ("Fe", 3135.15, 0.02),  # CRC/NIST element data: Fe normal boiling point 2862 C.
        ("Ca", 1757.15, 0.02),  # NIST WebBook / CRC: Ca normal boiling point 1484 C.
        ("Al", 2792.15, 0.02),  # CRC/CR2: Al normal boiling point 2519 C in local table.
        ("Si", 3538.15, 0.02),  # CRC/Safarian-Engh pure-Si branch: Si normal boiling point 3265 C.
        ("Ti", 3560.15, 0.02),  # CRC/CR2: Ti normal boiling point 3287 C in local table.
    ],
)
def test_pure_component_antoine_reaches_one_atm_at_normal_boiling_point(
    species: str,
    temperature_K: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()["metals"][species]

    assert data["pure_component_antoine"]["source"]
    assert _pure_component_antoine_pa(data, temperature_K) == pytest.approx(
        PA_PER_ATM,
        rel=rel_tol,
    )


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_pa", "rel_tol"),
    [
        # NIST Chemistry WebBook SRD 69, sodium Antoine row, Rodebush and Walters 1930.
        ("Na", 1118.0, 61_691.7, 0.25),
        # NIST Chemistry WebBook SRD 69, potassium Antoine row, Fiock and Rodebush 1926.
        ("K", 1033.0, 104_572.6, 0.05),
        # NIST Chemistry WebBook SRD 69, calcium Antoine row, Hartmann and Schneider 1929.
        ("Ca", 1712.0, 98_023.9, 0.35),
    ],
)
def test_pure_component_antoine_matches_published_vapor_pressure_points(
    species: str,
    temperature_K: float,
    expected_pa: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()["metals"][species]

    assert _pure_component_antoine_pa(data, temperature_K) == pytest.approx(
        expected_pa,
        rel=rel_tol,
    )


@pytest.mark.parametrize(
    ("element", "expected_g_mol", "abs_tol"),
    [
        ("O", 15.999, 0.0005),  # CIAAW interval / NIST abridged atomic weight.
        ("Na", 22.98976928, 0.00000005),  # NIST atomic weight for mononuclidic Na.
        ("Mg", 24.305, 0.001),  # CIAAW / NIST abridged atomic weight.
        ("Al", 26.9815385, 0.000001),  # NIST periodic table atomic weight.
        ("Si", 28.085, 0.001),  # CIAAW interval / NIST abridged atomic weight.
        ("S", 32.06, 0.01),  # CIAAW interval / NIST abridged atomic weight.
        ("K", 39.0983, 0.0005),  # NIST atomic weight for mononuclidic K.
        ("Ca", 40.078, 0.001),  # CIAAW / NIST abridged atomic weight.
        ("Ti", 47.867, 0.001),  # CIAAW / NIST abridged atomic weight.
        ("Cr", 51.9961, 0.0005),  # CIAAW / NIST abridged atomic weight.
        ("Mn", 54.938044, 0.000005),  # NIST periodic table atomic weight.
        ("Fe", 55.845, 0.002),  # CIAAW Fe = 55.845(2).
        ("Co", 58.933194, 0.000005),  # NIST periodic table atomic weight.
        ("Ni", 58.6934, 0.0005),  # CIAAW / NIST abridged atomic weight.
    ],
)
def test_atomic_weights_match_nist_iupac_standard_values(
    element: str,
    expected_g_mol: float,
    abs_tol: float,
) -> None:
    assert ATOMIC_WEIGHTS_G_PER_MOL[element] == pytest.approx(
        expected_g_mol,
        abs=abs_tol,
    )


def test_molar_mass_ledger_derives_from_canonical_atomic_weight_table() -> None:
    # CIAAW/NIST atomic weights above are the single source of truth; oxide masses
    # must derive from them rather than carrying independent rounded constants.
    assert MOLAR_MASS["FeO"] == pytest.approx(
        ATOMIC_WEIGHTS_G_PER_MOL["Fe"] + ATOMIC_WEIGHTS_G_PER_MOL["O"],
        abs=1e-12,
    )
    assert MOLAR_MASS["SiO2"] == pytest.approx(
        ATOMIC_WEIGHTS_G_PER_MOL["Si"] + 2 * ATOMIC_WEIGHTS_G_PER_MOL["O"],
        abs=1e-12,
    )


@pytest.mark.parametrize(
    ("reductant", "target", "expected_c", "abs_tol_c"),
    [
        # NIST-JANAF high-T oxide refit: Na2O/FeO crossover cited by Mandate.
        ("Na", "Fe", 1173.4, 1.0),
        # NIST-JANAF high-T oxide refit: K2O/FeO crossover cited by Mandate.
        ("K", "Fe", 832.0, 1.0),
    ],
)
def test_janaf_alkali_fe_ellingham_crossovers_match_literature_window(
    reductant: str,
    target: str,
    expected_c: float,
    abs_tol_c: float,
) -> None:
    assert BuiltinMetallothermicStepProvider._crossover_temperature_C(
        reductant,
        target,
    ) == pytest.approx(expected_c, abs=abs_tol_c)


def test_janaf_alkali_fe_reduction_margin_changes_sign_at_crossover() -> None:
    # NIST-JANAF high-T refit: below Na/Fe crossover Na2O is more stable than
    # FeO; above it, Na->FeO reduction must be thermodynamically refused.
    assert BuiltinMetallothermicStepProvider._reduction_margin_kj_per_mol_o2(
        "Na",
        "FeO",
        1150.0,
    ) > 0.0
    assert BuiltinMetallothermicStepProvider._reduction_margin_kj_per_mol_o2(
        "Na",
        "FeO",
        1200.0,
    ) < 0.0

    # NIST-JANAF high-T refit: K/Fe crossover is near 832 C, so practical melt
    # temperatures above that cannot use K to reduce FeO.
    assert BuiltinMetallothermicStepProvider._reduction_margin_kj_per_mol_o2(
        "K",
        "FeO",
        800.0,
    ) > 0.0
    assert BuiltinMetallothermicStepProvider._reduction_margin_kj_per_mol_o2(
        "K",
        "FeO",
        900.0,
    ) < 0.0


@pytest.mark.parametrize(
    ("species", "temperature_K", "rel_tol"),
    [
        ("NaCl", 1738.15, 0.02),  # CRC/NIST: NaCl normal boiling point 1465 C.
        ("KCl", 1693.15, 0.02),  # CRC/NIST: KCl normal boiling point 1420 C.
    ],
)
def test_foulant_vapor_pure_component_antoine_reaches_one_atm_at_normal_boiling_point(
    species: str,
    temperature_K: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()["foulant_vapor"][species]

    assert data["pure_component_antoine"]["source"]
    assert "parent_oxide" not in data
    assert data.get("carrier_is_own_vapor") is True
    assert _pure_component_antoine_pa(data, temperature_K) == pytest.approx(
        PA_PER_ATM,
        rel=rel_tol,
    )


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_pa", "rel_tol"),
    [
        # NIST Chemistry WebBook SRD 69, Stull 1947 NaCl row at 1200 K.
        ("NaCl", 1200.0, 366.8, 0.01),
        # NIST Chemistry WebBook SRD 69, Stull 1947 KCl row at 1200 K (ground-truth coeffs).
        ("KCl", 1200.0, 747.85, 0.01),
    ],
)
def test_foulant_vapor_pure_component_antoine_matches_published_vapor_pressure_points(
    species: str,
    temperature_K: float,
    expected_pa: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()["foulant_vapor"][species]

    assert _pure_component_antoine_pa(data, temperature_K) == pytest.approx(
        expected_pa,
        rel=rel_tol,
    )


@pytest.mark.parametrize(
    ("species", "temperature_C", "p_total_pa", "expected_chi", "abs_tol"),
    [
        ("NaCl", 1200.0, 20_000.0, 0.354, 0.01),
        ("KCl", 1200.0, 20_000.0, 0.459, 0.01),
        ("NaCl", 1200.0, 100.0, 0.99, 0.01),
        ("KCl", 1200.0, 100.0, 0.99, 0.01),
    ],
)
def test_foulant_vapor_chi_escape_matches_equilibrium_partition(
    species: str,
    temperature_C: float,
    p_total_pa: float,
    expected_chi: float,
    abs_tol: float,
) -> None:
    data = _vapor_pressure_data()["foulant_vapor"][species]
    temperature_K = temperature_C + 273.15
    p_sat = _pure_component_antoine_pa(data, temperature_K)
    chi = _chi_escape_equilibrium(p_sat, p_total_pa)
    assert chi == pytest.approx(expected_chi, abs=abs_tol)


def test_naf_foulant_vapor_certified_point_request_fails_loud() -> None:
    data = _vapor_pressure_data()["foulant_vapor"]["NaF"]

    assert data.get("confidence") == "partly_grounded"
    assert data.get("interval_required") is True
    assert data.get("certified_point") is None
    assert "pure_component_antoine" not in data
    with pytest.raises(ValueError, match="certified-point request refused"):
        _require_certified_pure_component_antoine(data, 1977.15)


def test_caf2_mgf2_absent_from_foulant_vapor() -> None:
    foulant_vapor = _vapor_pressure_data()["foulant_vapor"]
    assert "CaF2" not in foulant_vapor
    assert "MgF2" not in foulant_vapor


def test_antoine_values_are_finite_positive_ground_truth_numbers() -> None:
    # Guard against future parity-only rewrites that silently insert NaN/inf or
    # backsolved zero-pressure placeholders in the pure-component table.
    for species, entry in _vapor_pressure_data()["metals"].items():
        if "pure_component_antoine" not in entry:
            continue
        temperature_K = float(entry["boiling_point_C"]) + 273.15
        pressure_pa = _pure_component_antoine_pa(entry, temperature_K)
        assert math.isfinite(pressure_pa)
        assert pressure_pa > 0.0

    for species, entry in _vapor_pressure_data()["foulant_vapor"].items():
        if "pure_component_antoine" not in entry:
            continue
        temperature_K = float(entry["boiling_point_C"]) + 273.15
        pressure_pa = _pure_component_antoine_pa(entry, temperature_K)
        assert math.isfinite(pressure_pa)
        assert pressure_pa > 0.0
