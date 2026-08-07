"""Single-cation melt-oxide activity coefficients.

This module is intentionally dependency-light so builtin vapor pressure,
metallothermic gating, and tests share the same activity-coefficient table
without importing engine code.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


ALPHAMELTS_CROSS_CHECK_STATUS = "inconclusive_no_activities"
MELT_OXIDE_ACTIVITY_TIER = "UNCERTIFIED"
MELT_OXIDE_IDEAL_ASSERTION_TIER = "ASSUMED_IDEAL_SOLUTION"
# Constant table gamma for mid-range composition, with a thin pure-endmember
# continuity shell. The one-parameter pseudo-binary regular solution
# (ln gamma = ln(gamma*)*(T*/T)*(1-X)^2) is intentionally NOT used: monkeypatch
# bisection (docs-private/research/2026-08-05-chemact-root/findings.md) proved
# that mid-range (1-X)^2 term alone caused the Ca median regression (+0.418 dex
# over 30 low-T enghar cells) and the T*/T term drove the K median regression.
MELT_OXIDE_TABLE_GAMMA_MODEL = "constant_gamma_table_with_endmember_continuity"
MELT_OXIDE_IDEAL_SOLUTION_MODEL = "declared_ideal_solution"
MELT_OXIDE_ACTIVITY_LIMITATION = (
    "constant_gamma_table_value with local pure-endmember continuity shell; "
    "gamma is the cited table coefficient for mid-range composition; no "
    "one-parameter pseudo-binary mid-range regular solution and no A*T*/T "
    "temperature scaling of gamma (held for multi-component G^E / MC-5); as "
    "X approaches 1 a thin composition shell enforces Raoultian a->1 "
    "continuously so the former gamma*X to a=1 step (31.1x for Cr) is absent; "
    "outside a declared gamma temperature domain the numeric value remains "
    "flux-driving but status-bearing"
)
MELT_OXIDE_ACTIVITY_REFERENCE_STATE = (
    "single_cation_Raoultian_pure_liquid_reference"
)
# Single-cation mole fraction at which the pure-endmember continuity shell
# begins. Below this floor, gamma_eff is exactly the table gamma (constant-
# gamma baseline; lunar-mare X_Ca ~0.12 is far below). Above it, ln(gamma) is
# reparameterized so gamma->1 as X->1. See table_gamma_effective.
MELT_OXIDE_ENDMEMBER_CONTINUITY_BLEND_START = 0.99
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
        valid_range_K=(1673.0, 1673.0),
        anchor_T_K=1673.0,
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

# P2O5 is intentionally separate from the legacy coefficient table. Several
# pre-b-133 consumers treat membership in that table as permission to activate
# an activity correction without a temperature or authority check. Real P
# carrier evaluation selects this coefficient explicitly below and supplies T.
# Composed into the chemact-split landing: numeric gamma path uses the same
# constant-table + endmember-continuity model as other oxides (INCOMING
# activity semantics); temperature-gating remains HEAD b-133 rail discipline.
P2O5_ACTIVITY_COEFFICIENT = MeltOxideActivityCoefficient(
    "P2O5",
    "PO2.5",
    2.0,
    1.0e-6,
    "Turkdogan 2000 ISIJ Int. 40:964-970, DOI "
    "10.2355/isijinternational.40.964, as compiled by Sossi & Fegley "
    "2018 Table 2: gamma_PO2.5=1e-6..1e-10 in CMFS melts",
    valid_range_K=(1823.0, 1923.0),
    anchor_T_K=1873.0,
)


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
    effective_gamma: float | None = None
    activity_model: str = MELT_OXIDE_TABLE_GAMMA_MODEL
    evidence_tier: str = MELT_OXIDE_ACTIVITY_TIER
    temperature_K: float | None = None
    # b-133 P-carrier authority fields (HEAD rail); non-P rows leave defaults.
    valid_range_K: tuple[float, float] | None = None
    authority_status: str = "temperature_not_supplied"

    def equivalent_parent_activity(self, parent_activity_exponent: float) -> float:
        """Return parent-oxide activity that yields this activity after exponenting."""

        exponent = float(parent_activity_exponent)
        if exponent <= 0.0:
            raise ValueError("parent_activity_exponent must be positive")
        if self.activity <= 0.0:
            return 0.0
        return self.activity ** (1.0 / exponent)

    def thermodynamic_parent_activity(self) -> float:
        """Return activity on the parent-oxide formula basis."""

        cations = MELT_OXIDE_CATIONS_PER_FORMULA.get(self.parent_oxide, 1.0)
        if self.activity <= 0.0:
            return 0.0
        return self.activity ** float(cations)

    def provenance(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "melt_oxide_component": self.single_cation_component,
            "melt_oxide_gamma": self.gamma,
            "melt_oxide_effective_gamma": (
                self.gamma if self.effective_gamma is None else self.effective_gamma
            ),
            "melt_oxide_X_single_cation": self.x_single_cation,
            "melt_oxide_activity": self.activity,
            "melt_oxide_gamma_tier": self.evidence_tier,
            "melt_oxide_activity_evidence_tier": self.evidence_tier,
            "melt_oxide_activity_model": self.activity_model,
            "melt_oxide_activity_reference_state": MELT_OXIDE_ACTIVITY_REFERENCE_STATE,
            "melt_oxide_gamma_citation": self.citation,
            "melt_oxide_gamma_limitation": MELT_OXIDE_ACTIVITY_LIMITATION,
            "alphamelts_cross_check_status": ALPHAMELTS_CROSS_CHECK_STATUS,
        }
        if self.temperature_K is not None:
            payload["melt_oxide_activity_temperature_K"] = self.temperature_K
            domain_authority = melt_oxide_gamma_domain_authority(
                self.parent_oxide,
                self.temperature_K,
                gamma=self.gamma,
            )
            if domain_authority is not None:
                payload["gamma_domain_authority"] = domain_authority
        # b-133 P-carrier overlay: catalog + evaporation evidence keys.
        if self.parent_oxide == "P2O5":
            payload["melt_parent_oxide_activity"] = (
                self.thermodynamic_parent_activity()
            )
            payload["melt_oxide_activity_authority_status"] = self.authority_status
            if self.valid_range_K is not None:
                payload["melt_oxide_gamma_valid_range_K"] = self.valid_range_K
        if self.warning:
            payload["melt_oxide_activity_warning"] = self.warning
        return payload


# AtomLedger keeps sub-tolerance signed dust rather than pruning it
# (ledger.py). Multi-carrier parent debits (e.g. Cr / CrO / CrO2 / CrO3
# against Cr2O3) can therefore leave residual mol of order 1e-15 after a
# near-zero parent is fully consumed — not a mass-balance breach (HI-2
# stays closed). Activity reads must treat that dust as empty inventory
# rather than hard-fail the vapour batch. Raise only on true negatives
# outside the dust floor. (b-145: Cr2O3 = -1.76e-15 mol after physical-
# composite OOR changed the Cr-family branch schedule on the web full run.)
_MELT_INVENTORY_NUMERICAL_DUST_MOL = 1.0e-12


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
        if not math.isfinite(mol_value):
            raise ValueError(
                f"melt inventory for {parent_oxide!r} must be finite "
                "and non-negative"
            )
        if mol_value < 0.0:
            if mol_value >= -_MELT_INVENTORY_NUMERICAL_DUST_MOL:
                # Signed dust from float cancellation on a depleted parent —
                # treat as zero inventory for the activity projection.
                continue
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


def melt_oxide_activity_coefficient(
    component_id: str,
) -> MeltOxideActivityCoefficient | None:
    """Resolve a coefficient by parent oxide or single-cation component ID."""

    component = str(component_id)
    if component in {"P2O5", P2O5_ACTIVITY_COEFFICIENT.single_cation_component}:
        return P2O5_ACTIVITY_COEFFICIENT
    direct = MELT_OXIDE_ACTIVITY_COEFFICIENTS.get(component)
    if direct is not None:
        return direct
    return next(
        (
            coeff
            for coeff in MELT_OXIDE_ACTIVITY_COEFFICIENTS.values()
            if coeff.single_cation_component == component
        ),
        None,
    )


def melt_oxide_gamma_domain_authority(
    component_id: str,
    temperature_K: float,
    *,
    gamma: float | None = None,
) -> dict[str, Any] | None:
    """Return the shared temperature-domain verdict for a gamma anchor."""

    coeff = melt_oxide_activity_coefficient(component_id)
    if coeff is None or coeff.valid_range_K is None:
        return None
    temperature = float(temperature_K)
    low, high = (float(coeff.valid_range_K[0]), float(coeff.valid_range_K[1]))
    payload: dict[str, Any] = {
        "authority_status": (
            "in_domain"
            if low <= temperature <= high
            else "out_of_gamma_domain"
        ),
        "gamma_domain_K": (low, high),
        "temperature_K": temperature,
        "gamma": float(coeff.gamma if gamma is None else gamma),
    }
    if payload["authority_status"] == "out_of_gamma_domain":
        payload["anchor_T_K"] = coeff.anchor_T_K
    return payload


def table_gamma_effective(
    gamma_anchor: float,
    x_single_cation: float,
    *,
    blend_start: float = MELT_OXIDE_ENDMEMBER_CONTINUITY_BLEND_START,
) -> float:
    """Return table gamma with a local Raoultian pure-endmember shell.

    Mid-range (``X <= X_blend``):
        ``gamma_eff = gamma*``  (constant table path).

    Near pure (``X_blend < X <= 1``):
        ``ln(gamma_eff) = ln(gamma*) * ((1-X)/(1-X_blend))^2``

    so at ``X = X_blend`` the factor is 1 (value-continuous with mid-range),
    and as ``X -> 1`` the factor -> 0 so ``gamma_eff -> 1`` and
    ``a = gamma_eff * X -> 1`` continuously.

    Derivation
    ----------
    1. Premise: Raoultian pure-liquid reference requires ``a(X=1) = 1``.
    2. Premise: table ``gamma*`` is a dilute/measured mid-composition anchor,
       not a composition-independent identity on the full ``[0, 1]`` range.
    3. The one-parameter pseudo-binary
       ``ln gamma = ln(gamma*) * (T*/T) * (1-X)^2`` enforces (1) globally but
       invents mid-range curvature. Monkeypatch bisection against stored
       VapoRock enghar cells showed that mid-range ``(1-X)^2`` alone moved
       every Ca pressure by ``+log10(gamma_eff/gamma*) = +0.418`` dex at
       lunar ``X_Ca ~ 0.116``, raising the Ca median ``|Delta|``
       (findings.md §2). The ``T*/T`` factor separately regressed K at
       ``T >= 1500 K``. Both are therefore held (MC-5 multi-component G^E).
    4. Local reparameterization: replace the global ``(1-X)`` with the
       shell-local ``(1-X)/(1-X_blend)`` so the continuity factor is identity
       on ``X <= X_blend`` and only acts inside the pure-endmember
       neighborhood. No temperature factor — ``anchor_T_K`` remains for
       domain-authority labeling only (b-121), not gamma scaling.
    5. Unit check: ``gamma_eff`` dimensionless; ``a = gamma_eff * X``
       dimensionless.
    6. Sanity:
       - ``gamma*=31.1``, ``X=0.116`` -> ``gamma_eff=31.1`` (baseline)
       - ``gamma*=31.1``, ``X=1-1e-9`` -> ``a ≈ 1`` (continuity; was 31.1x)
       - ``X=1`` -> ``a=1``
       - ``gamma*=1`` -> ``gamma_eff=1`` for all X (ideal)
    """

    gamma = float(gamma_anchor)
    x_value = float(x_single_cation)
    x_blend = float(blend_start)
    if not math.isfinite(gamma) or gamma <= 0.0:
        raise ValueError("gamma_anchor must be finite and positive")
    if not math.isfinite(x_value) or not 0.0 <= x_value <= 1.0:
        raise ValueError("x_single_cation must be finite and within [0, 1]")
    if not math.isfinite(x_blend) or not 0.0 < x_blend < 1.0:
        raise ValueError("blend_start must be finite and within (0, 1)")

    if x_value <= x_blend:
        return gamma

    # Shell-local factor: 1 at X=X_blend, 0 at X=1.
    # ((1-X)/(1-X_blend))^2 at X=1-1e-9, X_blend=0.99:
    #   (1e-9/0.01)^2 = 1e-14; for |ln gamma*|~3.4, gamma_eff~1+3e-14, a~1.
    shell_span = 1.0 - x_blend
    local_factor = ((1.0 - x_value) / shell_span) ** 2
    effective_gamma = math.exp(math.log(gamma) * local_factor)
    if not math.isfinite(effective_gamma) or effective_gamma <= 0.0:
        raise ValueError("table-gamma effective gamma must be finite and positive")
    return effective_gamma


def melt_oxide_activity(
    parent_oxide: str,
    account_mol: Mapping[str, float],
    *,
    cation_mol_fraction: Mapping[str, float] | None = None,
    temperature_K: float | None = None,
) -> MeltOxideActivity | None:
    """Return ``a_MOx = gamma_eff(X) * X_MOx`` (constant table gamma mid-range).

    P2O5 is temperature-gated (HEAD b-133): without temperature_K the
    literature gamma stays inert and a non-authoritative unity fallback is
    returned so legacy equilibrium callers cannot silently activate the
    P envelope. With temperature_K supplied, the Turkdogan/Sossi-Fegley
    gamma is used under the same constant-table + endmember shell as every
    other oxide (INCOMING activity semantics).
    """

    parent = str(parent_oxide)
    resolved_temperature_K = None
    if temperature_K is not None:
        resolved_temperature_K = float(temperature_K)
        if not math.isfinite(resolved_temperature_K) or resolved_temperature_K <= 0.0:
            raise ValueError("temperature_K must be finite and positive")
    if cation_mol_fraction is None:
        cation_mol_fraction = single_cation_mole_fractions(account_mol)
    if not cation_mol_fraction:
        return None
    x_single_cation = cation_mol_fraction.get(parent, 0.0)

    # b-133 owns a temperature-qualified P2O5 activity for the real carrier
    # rail. Legacy equilibrium callers do not supply temperature; silently
    # activating the new coefficient there perturbs established redox.
    p2o5_temperature_required = (
        parent == "P2O5" and resolved_temperature_K is None
    )
    if parent == "P2O5":
        coeff = None if p2o5_temperature_required else P2O5_ACTIVITY_COEFFICIENT
    else:
        coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS.get(parent)

    if coeff is None:
        if x_single_cation <= 0.0:
            return None
        cations = MELT_OXIDE_CATIONS_PER_FORMULA.get(parent, 1.0)
        component = parent if cations == 1.0 else f"{parent}:single_cation"
        if p2o5_temperature_required:
            warning = (
                "melt_oxide_activity unity-gamma fallback: P2O5 literature "
                "gamma requires temperature_K; result is non-authoritative"
            )
            citation = (
                "ASSUMED unity fallback; temperature_K required for the "
                "Turkdogan/Sossi-Fegley P2O5 gamma envelope"
            )
            return MeltOxideActivity(
                parent,
                component,
                1.0,
                x_single_cation,
                x_single_cation,
                citation,
                warning,
                effective_gamma=1.0,
                activity_model=MELT_OXIDE_IDEAL_SOLUTION_MODEL,
                evidence_tier=MELT_OXIDE_IDEAL_ASSERTION_TIER,
                temperature_K=resolved_temperature_K,
                authority_status="assumed_unity_fallback_non_authoritative",
            )
        warning = (
            "declared_ideal_solution_activity_assertion: "
            f"parent_oxide={parent} gamma=1.0 "
            f"evidence_tier={MELT_OXIDE_IDEAL_ASSERTION_TIER}"
        )
        return MeltOxideActivity(
            parent,
            component,
            1.0,
            x_single_cation,
            x_single_cation,
            "No external gamma coefficient; explicit ideal-solution assertion",
            warning,
            effective_gamma=1.0,
            activity_model=MELT_OXIDE_IDEAL_SOLUTION_MODEL,
            evidence_tier=MELT_OXIDE_IDEAL_ASSERTION_TIER,
            temperature_K=resolved_temperature_K,
        )

    activity_authority_status = "temperature_not_supplied"
    activity_warning = None
    if resolved_temperature_K is not None and coeff.valid_range_K is not None:
        low, high = (float(coeff.valid_range_K[0]), float(coeff.valid_range_K[1]))
        if low <= resolved_temperature_K <= high:
            activity_authority_status = "in_gamma_domain"
        else:
            activity_authority_status = (
                "out_of_gamma_domain_status_bearing_non_authoritative"
            )
            activity_warning = (
                "constant_gamma_extrapolated_out_of_domain: "
                f"parent_oxide={parent} temperature_K={resolved_temperature_K:.3f} "
                f"valid_range_K=[{low:g}, {high:g}] cannot_certify"
            )

    if x_single_cation <= 0.0:
        return MeltOxideActivity(
            parent,
            coeff.single_cation_component,
            coeff.gamma,
            0.0,
            0.0,
            coeff.citation,
            activity_warning,
            effective_gamma=table_gamma_effective(coeff.gamma, 0.0),
            temperature_K=resolved_temperature_K,
            valid_range_K=coeff.valid_range_K,
            authority_status=activity_authority_status,
        )

    effective_gamma = table_gamma_effective(coeff.gamma, x_single_cation)
    activity = effective_gamma * x_single_cation
    return MeltOxideActivity(
        parent,
        coeff.single_cation_component,
        coeff.gamma,
        x_single_cation,
        activity,
        coeff.citation,
        activity_warning,
        effective_gamma=effective_gamma,
        temperature_K=resolved_temperature_K,
        valid_range_K=coeff.valid_range_K,
        authority_status=activity_authority_status,
    )


def na_reductant_activity_shift_kj_per_mol_o2(
    temperature_K: float,
    account_mol: Mapping[str, float] | None = None,
) -> float:
    """Na2O Ellingham-row shift from a_NaO0.5 on the per-mol-O2 basis."""

    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_K must be finite and positive")
    gamma = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"].gamma
    if account_mol is None:
        activity = gamma
    else:
        resolved = melt_oxide_activity(
            "Na2O", account_mol, temperature_K=temperature
        )
        activity = gamma if resolved is None else resolved.activity
    if activity <= 0.0:
        return float("-inf")

    # For 4 Na + O2 -> 2 Na2O = 4 NaO0.5, the product activity term is
    # RT ln(a^4) = 4 RT ln(a) on the per-mol-O2 Ellingham basis.
    return 4.0 * R_KJ_PER_MOL_K * temperature * math.log(activity)
