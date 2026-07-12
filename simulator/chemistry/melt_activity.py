"""Single-cation melt-oxide activity coefficients.

This module is intentionally dependency-light so builtin vapor pressure,
metallothermic gating, and tests share the same activity-coefficient table
without importing engine code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping


ALPHAMELTS_CROSS_CHECK_STATUS = "inconclusive_no_activities"
MELT_OXIDE_ACTIVITY_TIER = "UNCERTIFIED"
MELT_OXIDE_ACTIVITY_LIMITATION = (
    "constant_gamma_table_value; gamma is temperature-dependent and the "
    "1773-2173 K recipe band is hotter than the table datum, so constant "
    "gamma likely over-suppresses alkalis at recipe temperature; exact pure "
    "single-cation endpoints are normalized to the Raoultian a=1 reference, "
    "but no continuous near-pure gamma(X) fit is claimed"
)
MELT_OXIDE_ACTIVITY_REFERENCE_STATE = (
    "single_cation_Raoultian_pure_liquid_reference"
)
R_KJ_PER_MOL_K = 8.31446261815324e-3


@dataclass(frozen=True)
class MeltOxideActivityCoefficient:
    parent_oxide: str
    single_cation_component: str
    cations_per_parent_formula: float
    gamma: float
    citation: str
    valid_range_K: tuple[float, float] | None = None
    anchor_T_K: float | None = None


# provenance: gamma_alkali_melt_activity
# Values are Raoultian, single-cation MO_x components. Sossi & Fegley 2018
# RMG 84 Table 2 pp. 409-410, Eq. 24-25 pp. 413, DOI 10.2138/rmg.2018.84.11
# gives the basis and component rows. Na chosen value comes from Sossi et al.
# 2019 GCA 260:204-231 Tables 3-4, DOI 10.1016/j.gca.2019.06.021, as recorded
# in docs/chemistry-provenance.yaml::gamma_alkali_melt_activity.
MELT_OXIDE_ACTIVITY_COEFFICIENTS: dict[str, MeltOxideActivityCoefficient] = {
    "Na2O": MeltOxideActivityCoefficient(
        "Na2O",
        "NaO0.5",
        2.0,
        1.0e-3,
        "Sossi et al. 2019 Tables 3-4, DOI 10.1016/j.gca.2019.06.021; "
        "basis cross-check Sossi & Fegley 2018 Table 2 pp.409-410, "
        "Eq.25 p.413, DOI 10.2138/rmg.2018.84.11",
    ),
    "K2O": MeltOxideActivityCoefficient(
        "K2O",
        "KO0.5",
        2.0,
        3.5e-5,
        "DeMaria et al. 1971 lunar basalt inversion carried by Sossi & "
        "Fegley 2018 Fig.5/source OCR line ~350: gamma_KO0.5=3.5e-5 "
        "at 1500 K for the Apollo 12022/DeMaria composition; "
        "basis cross-check Sossi & Fegley 2018 Table 2 pp.409-410, "
        "Eq.25 p.413, DOI 10.2138/rmg.2018.84.11",
        valid_range_K=(1500.0, 1500.0),
        anchor_T_K=1500.0,
    ),
    "CaO": MeltOxideActivityCoefficient(
        "CaO",
        "CaO",
        1.0,
        1.2e-2,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11 "
        "(CaO envelope 1e-3..0.15)",
    ),
    "Al2O3": MeltOxideActivityCoefficient(
        "Al2O3",
        "AlO1.5",
        2.0,
        0.322,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
    "SiO2": MeltOxideActivityCoefficient(
        "SiO2",
        "SiO2",
        1.0,
        1.0,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
    "TiO2": MeltOxideActivityCoefficient(
        "TiO2",
        "TiO2",
        1.0,
        1.60,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
    "Cr2O3": MeltOxideActivityCoefficient(
        "Cr2O3",
        "CrO1.5",
        2.0,
        31.1,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
    "MgO": MeltOxideActivityCoefficient(
        "MgO",
        "MgO",
        1.0,
        1.0,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
    "MnO": MeltOxideActivityCoefficient(
        "MnO",
        "MnO",
        1.0,
        1.90,
        "Sossi & Fegley 2018 Table 2 pp.409-410, DOI 10.2138/rmg.2018.84.11",
    ),
}

MELT_OXIDE_CATIONS_PER_FORMULA = {
    "SiO2": 1.0,
    "TiO2": 1.0,
    "Al2O3": 2.0,
    "FeO": 1.0,
    "Fe2O3": 2.0,
    "MgO": 1.0,
    "CaO": 1.0,
    "Na2O": 2.0,
    "K2O": 2.0,
    "Cr2O3": 2.0,
    "MnO": 1.0,
    "P2O5": 2.0,
    "NiO": 1.0,
    "CoO": 1.0,
}


@dataclass(frozen=True)
class MeltOxideActivity:
    parent_oxide: str
    single_cation_component: str
    gamma: float
    x_single_cation: float
    activity: float
    citation: str
    warning: str | None = None

    def equivalent_parent_activity(self, parent_activity_exponent: float) -> float:
        """Return parent-oxide activity that yields this activity after exponenting."""

        exponent = float(parent_activity_exponent)
        if exponent <= 0.0:
            raise ValueError("parent_activity_exponent must be positive")
        if self.activity <= 0.0:
            return 0.0
        return self.activity ** (1.0 / exponent)

    def provenance(self) -> dict[str, float | str]:
        payload: dict[str, float | str] = {
            "melt_oxide_component": self.single_cation_component,
            "melt_oxide_gamma": self.gamma,
            "melt_oxide_X_single_cation": self.x_single_cation,
            "melt_oxide_activity": self.activity,
            "melt_oxide_gamma_tier": MELT_OXIDE_ACTIVITY_TIER,
            "melt_oxide_activity_reference_state": MELT_OXIDE_ACTIVITY_REFERENCE_STATE,
            "melt_oxide_gamma_citation": self.citation,
            "melt_oxide_gamma_limitation": MELT_OXIDE_ACTIVITY_LIMITATION,
            "alphamelts_cross_check_status": ALPHAMELTS_CROSS_CHECK_STATUS,
        }
        if self.warning:
            payload["melt_oxide_activity_warning"] = self.warning
        return payload


def single_cation_mole_fractions(
    account_mol: Mapping[str, float],
) -> dict[str, float]:
    """Return X_MOx on the single-cation mole-fraction basis."""

    cation_mol: dict[str, float] = {}
    total = 0.0
    for parent_oxide, mol in account_mol.items():
        mol_value = float(mol)
        cations = MELT_OXIDE_CATIONS_PER_FORMULA.get(str(parent_oxide))
        if cations is None:
            continue
        if not math.isfinite(mol_value) or mol_value < 0.0:
            raise ValueError(
                f"melt inventory for {parent_oxide!r} must be finite "
                "and non-negative"
            )
        if mol_value == 0.0:
            continue
        cation_value = mol_value * cations
        cation_mol[str(parent_oxide)] = cation_value
        total += cation_value
    if total <= 0.0:
        return {}
    return {oxide: cations / total for oxide, cations in cation_mol.items()}


def melt_oxide_activity(
    parent_oxide: str,
    account_mol: Mapping[str, float],
) -> MeltOxideActivity | None:
    """Return a_MOx = gamma_MOx * X_MOx for a parent oxide."""

    parent = str(parent_oxide)
    cation_mol_fraction = single_cation_mole_fractions(account_mol)
    x_single_cation = cation_mol_fraction.get(parent, 0.0)
    if x_single_cation <= 0.0:
        return None

    coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS.get(parent)
    if coeff is None:
        cations = MELT_OXIDE_CATIONS_PER_FORMULA.get(parent, 1.0)
        component = parent if cations == 1.0 else f"{parent}:single_cation"
        warning = (
            "undocumented_melt_oxide_activity_coefficient: "
            f"parent_oxide={parent} gamma=1.0"
        )
        return MeltOxideActivity(
            parent,
            component,
            1.0,
            x_single_cation,
            x_single_cation,
            "ASSUMED unity fallback; no documented non-FeO gamma table row",
            warning,
        )

    # Raoultian standard state requires the pure single-cation component to
    # have activity 1.0. The table coefficient is retained for mixed melts;
    # a fitted gamma(X,T) curve is outside this uncertified constant-gamma model.
    activity = (
        1.0
        if math.isclose(x_single_cation, 1.0, rel_tol=0.0, abs_tol=1e-12)
        else coeff.gamma * x_single_cation
    )
    return MeltOxideActivity(
        parent,
        coeff.single_cation_component,
        coeff.gamma,
        x_single_cation,
        activity,
        coeff.citation,
    )


def na_reductant_activity_shift_kj_per_mol_o2(temperature_K: float) -> float:
    """Na2O Ellingham-row shift from gamma_NaO0.5 on the per-mol-O2 basis."""

    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    return 4.0 * R_KJ_PER_MOL_K * temperature * math.log(gamma)
