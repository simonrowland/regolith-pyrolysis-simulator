from __future__ import annotations

import math
from collections.abc import Mapping


KRESS91_MOL_FRACTION_OXIDES = (
    'SiO2',
    'TiO2',
    'Al2O3',
    'MnO',
    'MgO',
    'CaO',
    'Na2O',
    'K2O',
    'P2O5',
)


def feot_equivalent_wt_pct(comp_wt: Mapping[str, float]) -> float:
    feo = max(0.0, float(comp_wt.get('FeO', 0.0) or 0.0))
    fe2o3 = max(0.0, float(comp_wt.get('Fe2O3', 0.0) or 0.0))
    return feo + fe2o3 * (2.0 * 71.844 / 159.687)


def melt_mol_fractions_for_kress91(comp_wt: Mapping[str, float]) -> dict[str, float]:
    # Lazy import: this module is imported by engines/builtin providers (R2.1b),
    # whose import guard (engines/builtin/__init__.py) forbids provider top-level
    # simulator.state imports. vapor_pressure.py uses the same lazy pattern for
    # GAS_CONSTANT. Keeping fe_redox.py a true leaf avoids that cycle.
    from simulator.state import MOLAR_MASS

    feot_wt = feot_equivalent_wt_pct(comp_wt)
    mol_counts: dict[str, float] = {}
    for oxide in KRESS91_MOL_FRACTION_OXIDES:
        wt = max(0.0, float(comp_wt.get(oxide, 0.0) or 0.0))
        molar_mass = float(MOLAR_MASS.get(oxide, 0.0) or 0.0)
        if wt > 0.0 and molar_mass > 0.0:
            mol_counts[oxide] = wt / molar_mass
        else:
            mol_counts[oxide] = 0.0
    mol_counts['FeOt'] = feot_wt / 71.844 if feot_wt > 0.0 else 0.0
    total_mol = sum(mol_counts.values())
    if total_mol <= 0.0:
        return {}
    return {oxide: mol / total_mol for oxide, mol in mol_counts.items()}


def _kress91_fe2o3_over_feo_molar(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    x = mol_fractions
    p_pa = max(float(pressure_bar), 1.0e-9) * 100000.0
    to_K = 1673.0
    ln_ratio = (
        0.196 * math.log(10.0 ** float(fO2_log))
        + 11492.0 / float(T_K)
        - 6.675
        - 2.243 * x.get('Al2O3', 0.0)
        - 1.828 * x.get('FeOt', 0.0)
        + 3.201 * x.get('CaO', 0.0)
        + 5.854 * x.get('Na2O', 0.0)
        + 6.215 * x.get('K2O', 0.0)
        - 3.36 * (1.0 - (to_K / T_K) - math.log(T_K / to_K))
        - 0.000000701 * (p_pa / T_K)
        - 0.000000000154 * (((T_K - 1673.0) * p_pa) / T_K)
        + 0.0000000000000000385 * ((p_pa ** 2.0) / T_K)
    )
    return math.exp(max(-745.0, min(709.0, ln_ratio)))


def kress91_fe3_over_sigma_fe(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> float:
    ratio = _kress91_fe2o3_over_feo_molar(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    return 2.0 * ratio / (2.0 * ratio + 1.0)


def kress91_ferrous_feo_activity(
    *,
    comp_wt: Mapping[str, float],
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
) -> float:
    feot = feot_equivalent_wt_pct(comp_wt)
    if feot <= 0.0:
        return 0.0
    mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
    if not mol_fractions:
        return 0.0
    fe3 = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    return (feot / 100.0) * (1.0 - fe3)


def kress91_split(
    *,
    fO2_log: float,
    mol_fractions: Mapping[str, float],
    T_K: float,
    pressure_bar: float,
) -> dict[str, float]:
    ratio = _kress91_fe2o3_over_feo_molar(
        fO2_log=fO2_log,
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    fe3 = 2.0 * ratio / (2.0 * ratio + 1.0)
    x_fe2o3 = ratio * mol_fractions['FeOt'] / (2.0 * ratio + 1.0)
    x_feo = max(0.0, mol_fractions['FeOt'] - 2.0 * x_fe2o3)
    return {
        'fe3': fe3,
        'ratio': ratio,
        'x_fe2o3': x_fe2o3,
        'x_feo': x_feo,
    }
