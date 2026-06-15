from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    _ELLINGHAM_THERMO,
    vapor_pressure_antoine_coefficients,
    vapor_pressure_source_label,
)
from engines.builtin.metallothermic_step import BuiltinMetallothermicStepProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.state import GAS_CONSTANT, MOLAR_MASS


PA_PER_ATM = 101_325.0
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ALCOCK_SOURCE_LOG10_PA = {
    "Ti": {
        "solid": {
            "range_K": (298.0, 1941.0),
            "A": 16.931,
            "B": -24991.0,
            "C": -1.3376,
            "D": 0.0,
        },
        "liquid": {
            "range_K": (1941.0, 2400.0),
            "A": 11.364,
            "B": -22747.0,
            "C": 0.0,
            "D": 0.0,
        },
    },
    "Mn": {
        "solid": {
            "range_K": (298.0, 1519.0),
            "A": 17.811,
            "B": -15097.0,
            "C": -1.7896,
            "D": 0.0,
        },
    },
}


def _vapor_pressure_data() -> dict:
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        return yaml.safe_load(handle)


def _pure_component_antoine_pa(entry: dict, temperature_K: float) -> float:
    coeff = entry["pure_component_antoine"]
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _coefficient_pa(coeff: dict, temperature_K: float) -> float:
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _alcock_source_pa(species: str, phase: str, temperature_K: float) -> float:
    coeff = ALCOCK_SOURCE_LOG10_PA[species][phase]
    lo, hi = coeff["range_K"]
    assert lo <= temperature_K <= hi
    log10_pa = (
        coeff["A"]
        + coeff["B"] / temperature_K
        + coeff["C"] * math.log10(temperature_K)
        + coeff["D"] * temperature_K * 1e-3
    )
    return 10.0 ** log10_pa


def _runtime_recovered_reference_pressure_pa(
    vapor_data: dict,
    species: str,
    temperature_K: float,
) -> float:
    row = vapor_data["metals"][species]
    parent_oxide = row["parent_oxide"]
    provider = BuiltinVaporPressureProvider(vapor_data)

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {parent_oxide: 1.0}},
                species_formula_registry={},
            ),
            temperature_C=temperature_K - 273.15,
            pressure_bar=1e-6,
            control_inputs={"pO2_bar": 1e-9},
        )
    )

    assert result.status == "ok"
    emitted_pa = result.diagnostic["vapor_pressures_Pa"][species]
    dH_f, dS_f, n_M, _n_ox = _ELLINGHAM_THERMO[species]
    dG_f_kJ = dH_f - temperature_K * dS_f
    k_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * temperature_K))
    activity = min((k_decomp / 1e-9) ** (1.0 / n_M), 1.0)
    return emitted_pa / activity


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
        # No NIST WebBook Antoine row is exposed for these species in SRD 69
        # Mask=4; Mg/Fe remain NBP-anchored CC estimates.
        ("Mg", 1364.15, 0.02),  # CRC/NIST element data: Mg normal boiling point 1091 C.
        ("Fe", 3135.15, 0.02),  # CRC/NIST element data: Fe normal boiling point 2862 C.
        # CRC/CR2 / Alcock-Itkin-Horrigan table converted to pure_component_antoine.
        ("Ti", 3560.15, 1e-6),
        ("Mn", 2334.15, 1e-6),
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
        ("Na", 1118.0, 61_691.685390, 1e-6),
        # NIST Chemistry WebBook SRD 69, potassium Antoine row, Fiock and Rodebush 1926.
        ("K", 1033.0, 104_572.576518, 1e-6),
        # NIST Chemistry WebBook SRD 69, calcium Antoine row, Hartmann and Schneider 1929.
        ("Ca", 1500.0, 21_740.153809, 1e-6),
        # NIST Chemistry WebBook SRD 69, aluminum Antoine row, Stull 1947.
        ("Al", 2200.0, 46_484.884967, 1e-6),
        # NIST Chemistry WebBook SRD 69, silicon Antoine row, Stull 1947.
        ("Si", 2200.0, 2_194.210607, 1e-6),
        # NIST Chemistry WebBook SRD 69, chromium Antoine row, Stull 1947.
        ("Cr", 2200.0, 2_704.347348, 1e-6),
        # CRC/CR2 / Alcock-Itkin-Horrigan source equation, not rounded table anchors.
        ("Ti", 1982.0, _alcock_source_pa("Ti", "liquid", 1982.0), 0.005),
        ("Mn", 1493.0, _alcock_source_pa("Mn", "solid", 1493.0), 0.03),
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
    ("species", "phase", "temperature_K", "rel_tol"),
    [
        ("Ti", "liquid", 1982.0, 0.005),
        ("Ti", "liquid", 2171.0, 0.005),
        ("Ti", "liquid", 2400.0, 0.005),
        ("Mn", "solid", 1228.0, 0.03),
        ("Mn", "solid", 1347.0, 0.03),
        ("Mn", "solid", 1493.0, 0.03),
        ("Mn", "solid", 1519.0, 0.03),
    ],
)
def test_mn_ti_runtime_pure_component_sidecars_match_alcock_source_equation(
    species: str,
    phase: str,
    temperature_K: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()
    row = data["metals"][species]
    expected_pa = _alcock_source_pa(species, phase, temperature_K)

    assert "Alcock-Itkin-Horrigan 1984" in row["pure_component_antoine"]["source"]
    coeff, block = vapor_pressure_antoine_coefficients(row)
    assert block == "pure_component_antoine"
    assert _coefficient_pa(dict(coeff), temperature_K) == pytest.approx(
        expected_pa,
        rel=rel_tol,
    )
    assert _runtime_recovered_reference_pressure_pa(
        data,
        species,
        temperature_K,
    ) == pytest.approx(expected_pa, rel=rel_tol)


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_reference_pa", "rel_tol"),
    [
        ("Na", 1118.0, 61_691.685390, 1e-6),
        ("K", 1033.0, 104_572.576518, 1e-6),
        ("Mg", 1364.15, PA_PER_ATM, 0.02),
        ("Fe", 3135.15, PA_PER_ATM, 0.02),
        ("Ca", 1500.0, 21_740.153809, 1e-6),
        ("Al", 2200.0, 46_484.884967, 1e-6),
        ("Si", 2200.0, 2_194.210607, 1e-6),
        ("Ti", 3560.15, PA_PER_ATM, 1e-6),
        ("Cr", 2200.0, 2_704.347348, 1e-6),
        ("Mn", 2334.15, PA_PER_ATM, 1e-6),
    ],
)
def test_builtin_runtime_provider_uses_pure_component_sidecar_for_reference_pressure(
    species: str,
    temperature_K: float,
    expected_reference_pa: float,
    rel_tol: float,
) -> None:
    data = _vapor_pressure_data()
    if species == "Si":
        data = copy.deepcopy(data)
        data["metals"]["Si"].pop("consumer_status", None)
    row = data["metals"][species]
    parent_oxide = row["parent_oxide"]
    provider = BuiltinVaporPressureProvider(data)

    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {parent_oxide: 1.0}},
                species_formula_registry={},
            ),
            temperature_C=temperature_K - 273.15,
            pressure_bar=1e-6,
            control_inputs={"pO2_bar": 1e-9},
        )
    )

    assert result.status == "ok"
    emitted_pa = result.diagnostic["vapor_pressures_Pa"][species]
    dH_f, dS_f, n_M, n_ox = _ELLINGHAM_THERMO[species]
    dG_f_kJ = dH_f - temperature_K * dS_f
    k_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * temperature_K))
    activity = min((k_decomp / 1e-9) ** (1.0 / n_M), 1.0)
    recovered_reference_pa = emitted_pa / activity

    assert recovered_reference_pa == pytest.approx(expected_reference_pa, rel=rel_tol)

    coeff, block = vapor_pressure_antoine_coefficients(row)
    assert block == "pure_component_antoine"
    assert _coefficient_pa(dict(coeff), temperature_K) == pytest.approx(
        expected_reference_pa,
        rel=rel_tol,
    )


def test_first_principles_label_requires_grounded_selected_sidecar() -> None:
    data = _vapor_pressure_data()

    grounded = data["metals"]["Cr"]
    _, grounded_block = vapor_pressure_antoine_coefficients(grounded)
    assert (
        vapor_pressure_source_label(
            "builtin_fallback",
            grounded,
            coefficient_block=grounded_block,
        )
        == "builtin_fallback:pure_component_first_principles"
    )

    label_cases = [
        ("Fe", 3135.15, "pure_component_first_principles"),
        ("Na", 1118.0, "pure_component_first_principles"),
        ("Mn", 1519.0, "pure_component_first_principles"),
        ("Mn", 1700.0, "pure_component_extrapolated"),
        ("Mn", 2000.0, "pure_component_extrapolated"),
        ("Ti", 2400.0, "pure_component_first_principles"),
        ("Ti", 2500.0, "pure_component_extrapolated"),
    ]
    for species, temperature_K, expected_fragment in label_cases:
        metal = data["metals"][species]
        _, block = vapor_pressure_antoine_coefficients(metal)
        assert block == "pure_component_antoine"
        label = vapor_pressure_source_label(
            "builtin_fallback",
            metal,
            coefficient_block=block,
            temperature_K=temperature_K,
        )
        assert expected_fragment in label
        if expected_fragment == "pure_component_extrapolated":
            assert "pure_component_first_principles" not in label
            assert "extrapolated_beyond_source_equation_range_K" in label

    interval_only = data["foulant_vapor"]["NaF"]
    _, interval_block = vapor_pressure_antoine_coefficients(interval_only)
    assert "pure_component_first_principles" not in vapor_pressure_source_label(
        "builtin_fallback",
        interval_only,
        coefficient_block=interval_block,
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
