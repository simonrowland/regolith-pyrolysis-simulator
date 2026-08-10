"""H2 melt-leg extrapolation envelope (diagnostic instrument).

Instrument-before-gate: this module is **diagnostic-only and non-authoritative**.
It never writes the AtomLedger, never certifies chemistry claims, and never
retunes MELTS liquid interaction parameters (W). Callers attach the returned
fields as metadata on VapoRock-coupled results; authority gates must treat
extrapolated rows as status-bearing / non-authoritative.

Published formula (HT-PLAN r2 §H2 — authoritative)::

    sigma_mu(T)          = S_ex_bound * max(0, T - T_calib_max_K)   [J/mol]
    sigma_log10P_i(T)   ~= sigma_mu(T) / (ln(10) * R * T)           [dex]

R = 8.314462618 J mol⁻¹ K⁻¹.  S_ex_bound and T_calib_max_K are versioned
constants (no per-species knobs).

-----------------------------------------------------------------------
Derivation (premise → algebra → unit check → sanity check)
-----------------------------------------------------------------------

Premise
    MELTS liquid Margules-class interaction parameters W are T-independent
    in the shipped model (Ghiorso lineage). Excess free energy of mixing is
    effectively frozen at the calibration temperature band. Extrapolating
    past that band therefore incurs an uncompensated excess-entropy error
    in chemical potential. Recalibrating W is out of scope; we measure and
    mark (HT1-audit §2.1–2.2; HT-PLAN H2).

Algebra
    Neglecting excess heat capacity, a first-order bound on the excess
    chemical-potential error for a T-independent-W liquid is

        |δμ_i^{ex}|  ≲  |S_i^{ex}| · max(0, T − T_calib,max)

    Versioned surrogate |S^{ex}| → S_ex_bound (init 5 J mol⁻¹ K⁻¹).
    Ideal-gas projection of μ-error into log10 partial pressure
    (melt-μ error dominates gas-G error at superliquidus T):

        δ ln p_i  ≈  δμ_i / (R T)
        δ log10 p_i = δ ln p_i / ln(10)
                    = δμ_i / (ln(10) · R · T)

Unit check
    [S]·[ΔT] = (J mol⁻¹ K⁻¹)·K = J mol⁻¹  ✓  (energy / mol)
    σ_μ / (R T ln 10): (J/mol) / ((J mol⁻¹ K⁻¹)·K · 1) = dimensionless dex  ✓

Sanity check (worked numbers; pure arithmetic of the two formulas)
    (1) At T = T_calib_max + 250 K with S_ex_bound = 5 J mol⁻¹ K⁻¹:
            σ_μ = 5 · 250 = 1250 J/mol
    (2) Projection of that 1250 J/mol at T = 2200 K:
            ln(10) = 2.302585092994046…  (use 2.302585 for hand check)
            denom  = 2.302585 · 8.314462618 · 2200
                   = 2.302585 · 18291.8177596
                   ≈ 42121.55
            σ_log10P = 1250 / (ln(10) · R · 2200)
                     = 1250 / (2.302585092994046 · 8.314462618 · 2200)
                     ≈ 0.029678 ≈ 0.0297 dex
    Both checks are locked by tests/test_melt_envelope.py.
-----------------------------------------------------------------------
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, TypedDict

# CODATA / SI gas constant used by HT-PLAN r2 §H2 (exact for this instrument).
R_J_MOL_K: float = 8.314462618
_LN10: float = math.log(10.0)


class MeltModelConstants(TypedDict):
    """Versioned per-melt-model calibration envelope constants."""

    T_calib_max_K: float
    S_ex_bound_J_molK: float
    constants_version: str


# Versioned constants table: melt_model_id → calibration envelope.
#
# T_calib_max_K provenance (initial entry):
#   HT1-audit.md §2.2: "pMELTS/rhyolite-MELTS liquid experiments cluster
#   below ~1400–1500 °C for many systems; treat 1700 K as a conservative
#   'top of calibration' placeholder until versioned."
#   This is the MELTS *liquid* calibration ceiling, NOT the fork's VapoRock
#   evidence/backend gate (VAPOROCK_T_MAX_K = 1950 K in vaporock.py), which
#   is a separate rail-domain clamp (docs/model-limitations.md 1350–1950 K
#   external validation envelope for vapor cross-checks).
#   Source that would refine this further: Ghiorso & Sack 1995 / Ghiorso et al.
#   pMELTS / rhyolite-MELTS calibration papers' experimental T maxima for the
#   liquid-solution dataset actually used by the ThermoEngine / alphaMELTS
#   build pinned in this repo (per melt_model_id tag).
#
# S_ex_bound_J_molK provenance:
#   HT-PLAN r2 §H2 init = 5 J mol⁻¹ K⁻¹ (lower end of HT1-audit §2.2
#   silicate excess-mixing scale 5–15 J mol⁻¹ K⁻¹). Surrogate bound only —
#   not a calorimetry-grounded |S^{ex}| measurement. Replace only with
#   calorimetry-grounded evidence (HT-PLAN decision point); do not retune W.
MELT_ENVELOPE_CONSTANTS: Dict[str, MeltModelConstants] = {
    "MELTS-v1.0": {
        "T_calib_max_K": 1700.0,
        "S_ex_bound_J_molK": 5.0,
        "constants_version": "2026-08-10.ht-c3.1",
    },
}


class UnknownMeltModelIdError(KeyError):
    """Raised when melt_model_id is not in the versioned constants table.

    Fail loud: no silent default T_calib_max or S_ex_bound.
    """

    def __init__(self, melt_model_id: str) -> None:
        self.melt_model_id = melt_model_id
        known = sorted(MELT_ENVELOPE_CONSTANTS)
        super().__init__(
            f"unknown melt_model_id {melt_model_id!r}; "
            f"known ids: {known}. No silent default (HT-C3 / HT-PLAN H2)."
        )


@dataclass(frozen=True)
class MeltExtrapolationEnvelope:
    """Diagnostic H2 melt-leg envelope at one evaluation temperature.

    Six emission fields from HT-PLAN r2 §H2 plus ``constants_version`` so
    callers can pin which constants table produced the numbers.
    """

    melt_model_id: str
    T_calib_max_K: float
    melt_model_extrapolation_K: float
    melt_extrap_sigma_mu_J_mol: float
    melt_extrap_sigma_log10_P: float
    melt_extrap_status: str  # "in_calibration" | "extrapolated"
    constants_version: str


def _lookup_constants(melt_model_id: str) -> MeltModelConstants:
    try:
        return MELT_ENVELOPE_CONSTANTS[melt_model_id]
    except KeyError as exc:
        raise UnknownMeltModelIdError(melt_model_id) from exc


def melt_extrapolation_envelope(
    T_K: float,
    melt_model_id: str,
) -> MeltExtrapolationEnvelope:
    """Return the diagnostic melt-extrapolation envelope at temperature T_K.

    Parameters
    ----------
    T_K:
        Absolute temperature in kelvin.
    melt_model_id:
        Key into :data:`MELT_ENVELOPE_CONSTANTS`. Unknown ids raise
        :class:`UnknownMeltModelIdError` (no silent default).

    Returns
    -------
    MeltExtrapolationEnvelope
        Pure diagnostic record. Does not mutate any ledger or backend state.

    Notes
    -----
    status is ``\"in_calibration\"`` when T_K <= T_calib_max_K, else
    ``\"extrapolated\"``. Both σ fields are exactly 0.0 inside calibration.
    """
    consts: Mapping[str, object] = _lookup_constants(melt_model_id)
    t_calib = float(consts["T_calib_max_K"])
    s_ex = float(consts["S_ex_bound_J_molK"])
    version = str(consts["constants_version"])

    extrap_k = max(0.0, float(T_K) - t_calib)
    sigma_mu = s_ex * extrap_k

    if extrap_k == 0.0:
        status = "in_calibration"
        sigma_log10_p = 0.0
    else:
        status = "extrapolated"
        # Projection: σ_log10P = σ_μ / (ln(10) · R · T).  T_K is above
        # T_calib_max (1700 K for MELTS-v1.0), so T_K > 0 is guaranteed for
        # any registered model; keep the division explicit.
        sigma_log10_p = sigma_mu / (_LN10 * R_J_MOL_K * float(T_K))

    return MeltExtrapolationEnvelope(
        melt_model_id=melt_model_id,
        T_calib_max_K=t_calib,
        melt_model_extrapolation_K=extrap_k,
        melt_extrap_sigma_mu_J_mol=sigma_mu,
        melt_extrap_sigma_log10_P=sigma_log10_p,
        melt_extrap_status=status,
        constants_version=version,
    )
