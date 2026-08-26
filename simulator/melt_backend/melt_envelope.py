"""H2 melt-leg extrapolation envelope (diagnostic instrument).

Instrument-before-gate: this module is **diagnostic-only and non-authoritative**.
It never writes the AtomLedger, never certifies chemistry claims, and never
retunes MELTS liquid interaction parameters (W). Callers attach the returned
fields as metadata on VapoRock-coupled results; authority gates must treat
extrapolated rows as status-bearing / non-authoritative.

Published formula (HT-PLAN r2 §H2 — authoritative)::

    sigma_mu(T)          = S_ex_bound * max(0, T - T_calib_max_K)   [J/mol]
    sigma_log10P_i(T)    = sigma_mu(T) / (ln(10) * R * T)           [dex]

R = 8.314462618 J mol⁻¹ K⁻¹.  S_ex_bound and T_calib_max_K are versioned
constants (no per-species knobs). The log10-P line is exactly the melt-μ
projection the instrument computes; it is not a residual after subtracting
a gas-side Gibbs error (this module has no gas-G error term).

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
    Neglecting excess heat capacity, a first-order estimate of the excess
    chemical-potential error for a T-independent-W liquid is

        |δμ_i^{ex}|  ≈  |S_i^{ex}| · max(0, T − T_calib,max)

    Versioned surrogate |S^{ex}| → S_ex_bound = 5 J mol⁻¹ K⁻¹
    (HT-PLAN r2 §H2 init). That 5 is the lower end of the HT1-audit §2.2
    silicate excess-mixing scale quoted as 5–15 J mol⁻¹ K⁻¹. It is the
    slope of σ_μ = S_ex_bound · ΔT, not an upper bound on |S^{ex}|.
    An inequality-≲ bound on the module's own 5–15 scale would use 15,
    which would triple σ_μ and σ_log10P at every extrapolated T. The
    registered slope is the HT-PLAN init of 5.

    Ideal-gas projection of that melt-μ term into log10 partial pressure:

        δ ln p_i  =  δμ_i / (R T)
        δ log10 p_i = δ ln p_i / ln(10)
                    = δμ_i / (ln(10) · R · T)

    This instrument has no gas-side Gibbs-error input, model, or residual.
    Setting an omitted gas term to zero cannot establish that melt-μ error
    dominates gas-G error; that comparison is unestablished here.

Unit check
    [S]·[ΔT] = (J mol⁻¹ K⁻¹)·K = J mol⁻¹  ✓  (energy / mol)
    σ_μ / (R T ln 10): (J/mol) / ((J mol⁻¹ K⁻¹)·K · 1) = dimensionless dex  ✓

Sanity check (live envelope points for MELTS-v1.0: T_calib=1700 K, S=5)
    ln(10) = 2.302585092994046… ; R = 8.314462618 J mol⁻¹ K⁻¹
    (1) T = 1950 K = T_calib + 250 K:
            σ_μ = 5 · 250 = 1250 J/mol
            denom = ln(10) · R · 1950 ≈ 37332.28 J/mol
            σ_log10P = 1250 / denom ≈ 0.033483 dex
    (2) T = 2200 K = T_calib + 500 K:
            σ_μ = 5 · 500 = 2500 J/mol
            denom = ln(10) · R · 2200 ≈ 42118.47 J/mol
            σ_log10P = 2500 / denom ≈ 0.059356 dex
    (3) Limiting case T → ∞:
            σ_log10P → S_ex_bound / (ln(10) · R) ≈ 5 / 19.14476 ≈ 0.26117 dex
            (asymptote; does not reverse)
    Detached identity, not a live MELTS-v1.0 point: 1250 J/mol at 2200 K
    would be σ_log10P ≈ 0.029678 dex, which is the 1950 K numerator over
    a 2200 K denominator. The live 2200 K instrument uses σ_μ = 2500 J/mol.
    Live points (1)–(2) and the detached identity are locked by
    tests/test_melt_envelope.py.
-----------------------------------------------------------------------
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, TypedDict

from simulator.fidelity_vocabulary import STATUS_BEARING_NON_AUTHORITATIVE

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
#   HT-PLAN r2 §H2 init = 5 J mol⁻¹ K⁻¹, the lower end of the HT1-audit
#   §2.2 silicate excess-mixing scale quoted as 5–15 J mol⁻¹ K⁻¹. This is
#   a versioned surrogate SLOPE for σ_μ = S_ex_bound · ΔT, not an
#   upper-bound |S^{ex}|. An inequality-≲ envelope on that 5–15 scale
#   would use 15 J mol⁻¹ K⁻¹ (3× this slope). Not a calorimetry-grounded
#   |S^{ex}| measurement. Replace only with calorimetry-grounded evidence
#   (HT-PLAN decision point); do not retune W.
MELT_ENVELOPE_CONSTANTS: Dict[str, MeltModelConstants] = {
    "MELTS-v1.0": {
        "T_calib_max_K": 1700.0,
        "S_ex_bound_J_molK": 5.0,
        "constants_version": "2026-08-10.ht-c3.1",
    },
}

MELT_EXTRAPOLATION_ENVELOPE_FIELDS = (
    "melt_model_id",
    "T_calib_max_K",
    "melt_model_extrapolation_K",
    "melt_extrap_sigma_mu_J_mol",
    "melt_extrap_sigma_log10_P",
    "melt_extrap_status",
    "constants_version",
)
MELT_EXTRAPOLATION_ENVELOPE_VOCABULARY = frozenset(
    (*MELT_EXTRAPOLATION_ENVELOPE_FIELDS, "instrument_status")
)


def has_melt_extrapolation_envelope(diagnostic: Mapping[str, Any]) -> bool:
    return any(
        field in diagnostic
        for field in MELT_EXTRAPOLATION_ENVELOPE_VOCABULARY
    )


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


class MeltEnvelopeValidationError(ValueError):
    """H2 envelope input or persisted record failed semantic validation.

    Raised from melt_extrapolation_envelope, melt_extrapolation_diagnostic,
    and consume_melt_extrapolation_envelope for unparseable T_K and for
    missing, partial, or internally inconsistent persisted fields.
    """


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
    melt_extrap_status: str  # in_calibration | extrapolated | out_of_domain
    constants_version: str


def _lookup_constants(melt_model_id: str) -> MeltModelConstants:
    try:
        return MELT_ENVELOPE_CONSTANTS[melt_model_id]
    except KeyError as exc:
        raise UnknownMeltModelIdError(melt_model_id) from exc


def _parse_evaluation_temperature_K(
    value: Any,
    field: str = "T_K",
) -> float:
    """Coerce an evaluation temperature to float.

    bool is rejected before float() because bool is a subclass of int and
    float(True) == 1.0 would otherwise be classified as 1 K. Other
    unparseable values raise MeltEnvelopeValidationError from this helper;
    finite non-positive and non-finite floats are returned for the caller
    to classify as out_of_domain.
    """
    if isinstance(value, bool):
        raise MeltEnvelopeValidationError(
            f"{field} must be a real temperature in kelvin, not a boolean"
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MeltEnvelopeValidationError(
            f"{field} must be a real temperature in kelvin"
        ) from exc


def _out_of_domain_envelope(
    melt_model_id: str,
    t_calib: float,
    version: str,
) -> MeltExtrapolationEnvelope:
    return MeltExtrapolationEnvelope(
        melt_model_id=melt_model_id,
        T_calib_max_K=t_calib,
        melt_model_extrapolation_K=0.0,
        melt_extrap_sigma_mu_J_mol=0.0,
        melt_extrap_sigma_log10_P=0.0,
        melt_extrap_status="out_of_domain",
        constants_version=version,
    )


def melt_extrapolation_envelope(
    T_K: float,
    melt_model_id: str,
) -> MeltExtrapolationEnvelope:
    """Return the diagnostic melt-extrapolation envelope at temperature T_K.

    Parameters
    ----------
    T_K:
        Absolute temperature in kelvin. Must be coercible to float (bool
        is refused). Finite T_K > 0 K is evaluated; non-finite and
        non-positive T_K are ``out_of_domain``.
    melt_model_id:
        Key into :data:`MELT_ENVELOPE_CONSTANTS`. Unknown ids raise
        :class:`UnknownMeltModelIdError` (no silent default).

    Returns
    -------
    MeltExtrapolationEnvelope
        Pure diagnostic record. Does not mutate any ledger or backend state.

    Notes
    -----
    On the path through this function, status is ``\"in_calibration\"``
    when 0 < T_K <= T_calib_max_K, ``\"extrapolated\"`` when T_K >
    T_calib_max_K and the σ fields stay finite, and ``\"out_of_domain\"``
    when T_K is non-finite, T_K <= 0, or the σ arithmetic overflows to
    inf/nan. Unparseable T_K (non-numeric types, bool) raises
    :class:`MeltEnvelopeValidationError`. Both σ fields are exactly 0.0
    inside calibration and out of domain.
    """
    consts: Mapping[str, object] = _lookup_constants(melt_model_id)
    t_calib = float(consts["T_calib_max_K"])
    s_ex = float(consts["S_ex_bound_J_molK"])
    version = str(consts["constants_version"])

    temperature_K = _parse_evaluation_temperature_K(T_K)
    if not math.isfinite(temperature_K) or temperature_K <= 0.0:
        return _out_of_domain_envelope(melt_model_id, t_calib, version)

    extrap_k = max(0.0, temperature_K - t_calib)
    sigma_mu = s_ex * extrap_k

    if extrap_k == 0.0:
        status = "in_calibration"
        sigma_log10_p = 0.0
    else:
        status = "extrapolated"
        # Projection: σ_log10P = σ_μ / (ln(10) · R · T).
        # This branch has extrap_k > 0, so temperature_K > t_calib.
        # The gate above already required finite temperature_K > 0, so
        # ln(10)*R*T is finite and nonzero on this path.
        sigma_log10_p = sigma_mu / (_LN10 * R_J_MOL_K * temperature_K)

    if not (
        math.isfinite(extrap_k)
        and math.isfinite(sigma_mu)
        and math.isfinite(sigma_log10_p)
    ):
        return _out_of_domain_envelope(melt_model_id, t_calib, version)

    return MeltExtrapolationEnvelope(
        melt_model_id=melt_model_id,
        T_calib_max_K=t_calib,
        melt_model_extrapolation_K=extrap_k,
        melt_extrap_sigma_mu_J_mol=sigma_mu,
        melt_extrap_sigma_log10_P=sigma_log10_p,
        melt_extrap_status=status,
        constants_version=version,
    )


def melt_extrapolation_diagnostic(
    T_K: float,
    melt_model_id: str,
) -> dict[str, Any]:
    """Build the persisted H2 diagnostic projection and consume it.

    Unparseable T_K raises MeltEnvelopeValidationError from
    melt_extrapolation_envelope (bool / non-numeric types). Non-finite
    and non-positive T_K become an out_of_domain envelope, which
    consume_melt_extrapolation_envelope then accepts.
    """

    envelope = melt_extrapolation_envelope(T_K, melt_model_id)
    diagnostic = asdict(envelope)
    diagnostic["instrument_status"] = (
        "non_authoritative"
        if envelope.melt_extrap_status == "in_calibration"
        else STATUS_BEARING_NON_AUTHORITATIVE
    )
    consume_melt_extrapolation_envelope(
        diagnostic,
        temperature_K=T_K,
    )
    return diagnostic


def consume_melt_extrapolation_envelope(
    diagnostic: Mapping[str, Any],
    *,
    temperature_K: float | None = None,
    require_instrument_status: bool = True,
) -> MeltExtrapolationEnvelope:
    """Parse and semantically validate each persisted H2 envelope field.

    Fields checked are those in MELT_EXTRAPOLATION_ENVELOPE_FIELDS, plus
    instrument_status when require_instrument_status is true. When
    temperature_K is supplied, it is parsed by the same T_K helper as
    melt_extrapolation_envelope; unparseable values raise
    MeltEnvelopeValidationError on this path (None still means "derive
    comparison T from the persisted status fields").
    """

    present = {
        field
        for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
        if field in diagnostic
    }
    required = set(MELT_EXTRAPOLATION_ENVELOPE_FIELDS)
    if present != required:
        missing = sorted(required - present)
        kind = (
            "partial"
            if has_melt_extrapolation_envelope(diagnostic)
            else "missing"
        )
        raise MeltEnvelopeValidationError(
            f"{kind} H2 melt envelope; missing fields: {missing}"
        )

    model_id = str(diagnostic["melt_model_id"])
    try:
        constants = _lookup_constants(model_id)
    except UnknownMeltModelIdError as exc:
        raise MeltEnvelopeValidationError(
            f"melt_model_id is not registered: {model_id!r}"
        ) from exc

    numeric = {
        field: _finite_envelope_float(diagnostic[field], field)
        for field in (
            "T_calib_max_K",
            "melt_model_extrapolation_K",
            "melt_extrap_sigma_mu_J_mol",
            "melt_extrap_sigma_log10_P",
        )
    }
    status = str(diagnostic["melt_extrap_status"])
    version = str(diagnostic["constants_version"])
    if status not in {"in_calibration", "extrapolated", "out_of_domain"}:
        raise MeltEnvelopeValidationError(
            f"melt_extrap_status is invalid: {status!r}"
        )

    _require_envelope_close(
        "T_calib_max_K",
        numeric["T_calib_max_K"],
        float(constants["T_calib_max_K"]),
    )
    if version != str(constants["constants_version"]):
        raise MeltEnvelopeValidationError(
            "constants_version does not match the registered melt model"
        )

    if temperature_K is None:
        if status == "out_of_domain":
            comparison_temperature_K = math.nan
        elif status == "extrapolated":
            comparison_temperature_K = (
                numeric["T_calib_max_K"]
                + numeric["melt_model_extrapolation_K"]
            )
        else:
            comparison_temperature_K = numeric["T_calib_max_K"]
    else:
        comparison_temperature_K = _parse_evaluation_temperature_K(
            temperature_K,
            field="temperature_K",
        )
    expected = melt_extrapolation_envelope(comparison_temperature_K, model_id)
    expected_values = asdict(expected)
    for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS:
        actual = diagnostic[field]
        wanted = expected_values[field]
        if field in numeric:
            _require_envelope_close(field, numeric[field], float(wanted))
        elif str(actual) != str(wanted):
            raise MeltEnvelopeValidationError(
                f"{field} is inconsistent with the melt model and temperature"
            )

    expected_instrument_status = (
        "non_authoritative"
        if status == "in_calibration"
        else STATUS_BEARING_NON_AUTHORITATIVE
    )
    if require_instrument_status:
        instrument_status = diagnostic.get("instrument_status")
        if str(instrument_status or "") != expected_instrument_status:
            raise MeltEnvelopeValidationError(
                "instrument_status is inconsistent with melt_extrap_status"
            )

    return MeltExtrapolationEnvelope(
        melt_model_id=model_id,
        T_calib_max_K=numeric["T_calib_max_K"],
        melt_model_extrapolation_K=numeric["melt_model_extrapolation_K"],
        melt_extrap_sigma_mu_J_mol=numeric["melt_extrap_sigma_mu_J_mol"],
        melt_extrap_sigma_log10_P=numeric["melt_extrap_sigma_log10_P"],
        melt_extrap_status=status,
        constants_version=version,
    )


def _finite_envelope_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MeltEnvelopeValidationError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MeltEnvelopeValidationError(
            f"{field} must be a finite number"
        ) from exc
    if not math.isfinite(number):
        raise MeltEnvelopeValidationError(f"{field} must be finite")
    return number


def _require_envelope_close(field: str, actual: float, expected: float) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise MeltEnvelopeValidationError(
            f"{field} is inconsistent with the melt model and temperature"
        )
