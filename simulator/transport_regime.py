"""Pinned VPR-P0a transport-regime formulas and applicability.

Classification (KnudsenRegime / classify_knudsen_regime) is shared with the
live condensation path. Continuum-formula validity is NOT a global fail-closed:
it is load-bearing only where viscous sweep of metal/SiO vapor is the answer
being computed (pyrolysis extraction). Stage 0 volatile bakeout is a different
regime; below the continuum threshold is category-2 compute-and-mark.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from simulator.physical_constants import BOLTZMANN, GAS_CONSTANT
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.silent_zero import (
    CATEGORY_MARK,
    CATEGORY_REFUSE,
    ZeroBecause,
    note_dict,
)
from simulator.transport_constants import (
    CARRIER_GAS_PROPERTIES,
    COLLISION_DIAMETER_SOURCE,
    COLLISION_DIAMETERS_M,
    FREE_MOLECULAR_KNUDSEN_MIN,
    VISCOUS_KNUDSEN_MAX,
)


# Single-sourced from the physical_constants leaf (SC-CONST pass-B); byte-identical
# to the prior local literals (8.31446261815324 / 1.380649e-23).
GAS_CONSTANT_J_MOL_K = GAS_CONSTANT
BOLTZMANN_CONSTANT_J_K = BOLTZMANN

LONG_TUBE_L_OVER_D_MIN = 10.0

FORMULA_FREE_MOLECULAR_APERTURE = "free_molecular_aperture_conductance"
FORMULA_FREE_MOLECULAR_TUBE = "free_molecular_tube_clausing_conductance"
FORMULA_BESKOK_KARNIADAKIS_CIVAN = (
    "beskok_karniadakis_civan_transitional_conductance"
)
FORMULA_SINGLE_SPECIES_MFP = "single_species_hard_sphere_mean_free_path"
FORMULA_MIXTURE_MFP = "carrier_mixture_hard_sphere_mean_free_path"

MOLAR_MASSES_KG_PER_MOL: Mapping[str, float] = MappingProxyType(
    {
        "He": CARRIER_GAS_PROPERTIES["He"].molar_mass_kg_mol,
        "N2": CARRIER_GAS_PROPERTIES["N2"].molar_mass_kg_mol,
        "Ar": CARRIER_GAS_PROPERTIES["Ar"].molar_mass_kg_mol,
        # Kr (t-395): sourced from the same single-source properties table.
        "Kr": CARRIER_GAS_PROPERTIES["Kr"].molar_mass_kg_mol,
        "O2": CARRIER_GAS_PROPERTIES["O2"].molar_mass_kg_mol,
        "CO": 0.0280101,
        "CO2": CARRIER_GAS_PROPERTIES["CO2"].molar_mass_kg_mol,
        "H2": 0.00201588,
        "H2O": 0.01801528,
    }
)


class KnudsenRegime(str, Enum):
    VISCOUS = "viscous"
    TRANSITIONAL = "transitional"
    FREE_MOLECULAR = "free_molecular"


class ProcessRegime(str, Enum):
    """Which process question a transport formula is answering."""

    STAGE0_BAKEOUT = "stage0_bakeout"
    PYROLYSIS_EXTRACTION = "pyrolysis_extraction"
    ELECTROLYSIS = "electrolysis"
    IDLE = "idle"
    UNKNOWN = "unknown"


class ContinuumValidityAction(str, Enum):
    OK = "ok"
    MARK = "mark"
    REFUSE = "refuse"


# Stage 0 / C0 / C0b: drive off H2O, CO2, sulfur, halides, perchlorates, organics.
# Low overhead pressure is the intent. Continuum (Bernoulli) transport is not
# load-bearing for that answer.
_STAGE0_BAKEOUT_CAMPAIGNS = frozenset({
    "C0",
    "C0B",
    "C0b_p_cleanup",
})
# Metal / SiO vapor extraction: the mandate viscous-flow invariant (Kn << 0.01)
# is load-bearing so evolved vapor is swept to a designated condenser.
_PYROLYSIS_EXTRACTION_CAMPAIGNS = frozenset({
    "C2A",
    "C2A_continuous",
    "C2A_STAGED",
    "C2A_staged",
    "C2B",
    "C3",
    "C3_K",
    "C3_NA",
    "C4",
    "C6",
    "C7_CA_ALUMINOTHERMIC",
})
_ELECTROLYSIS_CAMPAIGNS = frozenset({
    "C5",
    "MRE_BASELINE",
})
_IDLE_CAMPAIGNS = frozenset({
    "IDLE",
    "COMPLETE",
})

CONTINUUM_QUESTION = "continuum_conductance"


class TransportRegimeRefusal(ValueError):
    """Named fail-closed refusal for out-of-validity transport inputs."""

    def __init__(
        self,
        category: str,
        detail: str | None = None,
        *,
        stage: str | None = None,
        process_regime: str | None = None,
        asking_site: str | None = None,
        question: str | None = None,
    ) -> None:
        self.category = category
        self.reason = category
        self.stage = None if stage is None else str(stage)
        self.process_regime = (
            None if process_regime is None else str(process_regime)
        )
        self.asking_site = None if asking_site is None else str(asking_site)
        self.question = None if question is None else str(question)
        message = category if detail is None else f"{category}: {detail}"
        if self.stage:
            message = f"{message} (stage={self.stage})"
        if self.asking_site and "asking_site" not in message:
            message = f"{message} [asked by {self.asking_site}]"
        super().__init__(message)


@dataclass(frozen=True)
class CarrierCollision:
    species: str
    mole_fraction: float
    collision_diameter_m: float
    molar_mass_kg_mol: float


@dataclass(frozen=True)
class MeanFreePathResult:
    lambda_m: float
    knudsen_number: float
    regime: KnudsenRegime
    formula_id: str
    test_species: str
    carriers: tuple[CarrierCollision, ...]
    collision_diameter_source: str


def _refuse(
    category: str,
    detail: str | None = None,
    *,
    stage: str | None = None,
    process_regime: str | None = None,
    asking_site: str | None = None,
    question: str | None = None,
) -> None:
    raise TransportRegimeRefusal(
        category,
        detail,
        stage=stage,
        process_regime=process_regime,
        asking_site=asking_site,
        question=question,
    )


def resolve_process_regime(
    campaign_name: str | None = None,
    *,
    process_regime: ProcessRegime | str | None = None,
) -> ProcessRegime:
    """Map a campaign / explicit regime onto the transport applicability set.

    Missing or blank campaign_name is UNKNOWN (category-1 missing input), not
    a silent bakeout carve-out.
    """

    if process_regime is not None:
        if isinstance(process_regime, ProcessRegime):
            return process_regime
        return ProcessRegime(str(process_regime))
    if campaign_name is None:
        return ProcessRegime.UNKNOWN
    name = str(campaign_name).strip()
    if not name:
        return ProcessRegime.UNKNOWN
    if name in _STAGE0_BAKEOUT_CAMPAIGNS:
        return ProcessRegime.STAGE0_BAKEOUT
    if name in _PYROLYSIS_EXTRACTION_CAMPAIGNS:
        return ProcessRegime.PYROLYSIS_EXTRACTION
    if name in _ELECTROLYSIS_CAMPAIGNS:
        return ProcessRegime.ELECTROLYSIS
    if name in _IDLE_CAMPAIGNS:
        return ProcessRegime.IDLE
    return ProcessRegime.UNKNOWN


def continuum_transport_is_load_bearing(
    process_regime: ProcessRegime | str,
) -> bool:
    """True iff leaving Kn < 0.01 must fail-close for the question asked.

    The mandate viscous-flow invariant exists so evolved metal/SiO vapor is
    swept to a designated condenser instead of crossing ballistically to a
    cold wall. The sole carve-out is Stage 0 volatile bakeout, where low
    overhead pressure is the intent. Missing/unknown/idle/electrolysis keep
    the fail-closed default so omitting a campaign cannot drop the guard.
    """

    regime = (
        process_regime
        if isinstance(process_regime, ProcessRegime)
        else ProcessRegime(str(process_regime))
    )
    return regime is not ProcessRegime.STAGE0_BAKEOUT


@dataclass(frozen=True)
class ContinuumFormulaValidity:
    action: ContinuumValidityAction
    process_regime: ProcessRegime
    knudsen_number: float
    campaign_name: str | None
    stage: str | None
    asking_site: str
    in_domain: bool
    load_bearing: bool
    note: dict[str, object] | None
    detail: str

    @property
    def refuses(self) -> bool:
        return self.action is ContinuumValidityAction.REFUSE


def _knudsen_is_transitional(knudsen_number: float) -> bool:
    kn = float(knudsen_number)
    if not math.isfinite(kn):
        return False
    return VISCOUS_KNUDSEN_MAX <= kn < FREE_MOLECULAR_KNUDSEN_MIN


def assess_continuum_formula_validity(
    knudsen_number: float,
    *,
    campaign_name: str | None = None,
    process_regime: ProcessRegime | str | None = None,
    stage: str | None = None,
    asking_site: str,
) -> ContinuumFormulaValidity:
    """Decide ok / mark / refuse for a continuum (Bernoulli/Poiseuille) formula.

    Viscous Kn is in-domain. Free-molecular Kn uses the reconstructible HKL
    path, not continuum P_bulk. Transitional Kn is out-of-domain for viscous
    Poiseuille P_bulk: refuse where that formula is load-bearing, otherwise
    compute-and-mark.
    """

    regime = resolve_process_regime(
        campaign_name, process_regime=process_regime
    )
    asked_stage = (
        None if stage is None and not campaign_name else str(
            stage if stage is not None else campaign_name
        )
    )
    load_bearing = continuum_transport_is_load_bearing(regime)
    kn = float(knudsen_number)
    # b-275: VALIDATE BEFORE CLASSIFYING. This function partitions the VALID Kn
    # domain; it does not check that its input is a Kn at all. Without this guard
    # `_knudsen_is_transitional` returns False for any non-finite value, so NaN
    # fell through to the OK/in_domain branch below and received the SAME answer
    # as a healthy viscous Kn -- an absent measurement reading as an established
    # in-domain state. Both consumers inherited it: the evaporation diagnostic
    # masked it with its own guard, and `refuse_continuum_formula_if_load_bearing`
    # (which raises only on REFUSE) did not refuse a NaN at all.
    #
    # NaN is MISSING INPUT and must refuse. POSITIVE INFINITY IS NOT: Kn = lambda/L
    # -> +inf is the mean free path dwarfing the pipe, i.e. true vacuum and the
    # free-molecular limit -- a DETERMINED physical state and the mandate's own
    # baseline regime. `not math.isfinite(kn)` cannot tell them apart, which is
    # why this asks the two questions separately, matching `classify_knudsen_regime`
    # below (the one idiom in this file that already had it right).
    #
    # Kn == 0 is left in-domain here to match classify_knudsen_regime's `< 0.0`.
    # Note the file is NOT unanimous: the evaporation-side guard refuses `<= 0.0`.
    # That disagreement is real and is deliberately not resolved by this change.
    if math.isnan(kn) or kn < 0.0:
        return ContinuumFormulaValidity(
            action=ContinuumValidityAction.REFUSE,
            process_regime=regime,
            knudsen_number=kn,
            campaign_name=None if campaign_name is None else str(campaign_name),
            stage=asked_stage,
            asking_site=str(asking_site),
            in_domain=False,
            load_bearing=load_bearing,
            note=None,
            detail=(
                "invalid_knudsen_number: Kn must not be NaN or negative; "
                f"received {kn!r}"
            ),
        )
    if not _knudsen_is_transitional(kn):
        return ContinuumFormulaValidity(
            action=ContinuumValidityAction.OK,
            process_regime=regime,
            knudsen_number=kn,
            campaign_name=None if campaign_name is None else str(campaign_name),
            stage=asked_stage,
            asking_site=str(asking_site),
            in_domain=True,
            load_bearing=load_bearing,
            note=None,
            detail="",
        )
    detail = (
        "transitional Kn uses viscous Poiseuille / Bernoulli P_bulk outside "
        f"its validity domain (Kn < {VISCOUS_KNUDSEN_MAX:g}); "
        f"free-molecular Kn >= {FREE_MOLECULAR_KNUDSEN_MIN:g} keeps the "
        "HKL upper-bound path"
    )
    if load_bearing:
        note = note_dict(
            ZeroBecause.REFUSED_UPSTREAM,
            site=str(asking_site),
            field="continuum_p_bulk",
            detail=(
                f"{detail}; process_regime={regime.value}; "
                f"stage={asked_stage!r}; continuum transport is load-bearing"
            ),
            doctrine_category=CATEGORY_REFUSE,
        )
        return ContinuumFormulaValidity(
            action=ContinuumValidityAction.REFUSE,
            process_regime=regime,
            knudsen_number=kn,
            campaign_name=None if campaign_name is None else str(campaign_name),
            stage=asked_stage,
            asking_site=str(asking_site),
            in_domain=False,
            load_bearing=True,
            note=note,
            detail=detail,
        )
    note = note_dict(
        ZeroBecause.OUT_OF_DOMAIN_MARKED,
        site=str(asking_site),
        field="continuum_p_bulk",
        detail=(
            f"{detail}; process_regime={regime.value}; "
            f"stage={asked_stage!r}; continuum formula is not load-bearing "
            "for this stage (category-2 compute-and-mark)"
        ),
        doctrine_category=CATEGORY_MARK,
    )
    return ContinuumFormulaValidity(
        action=ContinuumValidityAction.MARK,
        process_regime=regime,
        knudsen_number=kn,
        campaign_name=None if campaign_name is None else str(campaign_name),
        stage=asked_stage,
        asking_site=str(asking_site),
        in_domain=False,
        load_bearing=False,
        note=note,
        detail=detail,
    )


def continuum_validity_refuses(payload: Mapping[str, object] | None) -> bool:
    """True iff a continuum-validity diagnostic is a fail-closed refusal."""

    if not payload:
        return False
    return str(payload.get("status") or "") == "refused"


def refuse_continuum_formula_if_load_bearing(
    knudsen_number: float,
    *,
    campaign_name: str | None = None,
    process_regime: ProcessRegime | str | None = None,
    stage: str | None = None,
    asking_site: str,
) -> ContinuumFormulaValidity:
    """Raise TransportRegimeRefusal only when continuum transport is load-bearing."""

    assessment = assess_continuum_formula_validity(
        knudsen_number,
        campaign_name=campaign_name,
        process_regime=process_regime,
        stage=stage,
        asking_site=asking_site,
    )
    if assessment.action is ContinuumValidityAction.REFUSE:
        _refuse(
            "continuum_formula_out_of_domain",
            assessment.detail,
            stage=assessment.stage,
            process_regime=assessment.process_regime.value,
            asking_site=assessment.asking_site,
            question=CONTINUUM_QUESTION,
        )
    return assessment


def _require_positive(value: float, *, name: str, category: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        _refuse(category, f"{name} must be finite and > 0")
    return value


def _require_nonnegative(value: float, *, name: str, category: str) -> float:
    if not is_declared_real_scalar(value, allow_numeric_str=True):
        raise TypeError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        _refuse(category, f"{name} must be finite and >= 0")
    return value


def _require_collision_diameter(species: str) -> float:
    try:
        return COLLISION_DIAMETERS_M[species]
    except KeyError:
        _refuse(
            "uncertified_collision_diameter",
            f"no P0a collision diameter certified for {species!r}",
        )


def _require_molar_mass(species: str) -> float:
    try:
        return MOLAR_MASSES_KG_PER_MOL[species]
    except KeyError:
        _refuse(
            "uncertified_molar_mass",
            f"no P0a molar mass available for {species!r}",
        )


def _require_free_molecular_knudsen(
    knudsen_number: float, *, category: str
) -> float:
    knudsen_number = float(knudsen_number)
    # SC-146 / b-283: refuse NaN and negatives, KEEP +inf. The old guard was
    # `not math.isfinite(...)`, which cannot tell the two non-finite values apart
    # even though they mean opposite things here:
    #   NaN  = we do not know Kn -> missing input -> refuse
    #   +inf = Kn = lambda/L with lambda >> L -> TRUE free-molecular flow, i.e.
    #          true vacuum, which is this project's lunar baseline -> keep
    # Nothing downstream needs a finite value: both callers
    # (molecular_aperture_conductance_m3_s, molecular_tube_conductance_m3_s) pass
    # Kn into this precondition ONLY and compute conductance from area,
    # temperature and molar mass -- Kn never enters the arithmetic. And the very
    # next check below WANTS +inf to pass: `inf < FREE_MOLECULAR_KNUDSEN_MIN` is
    # False, so the semantic test already agrees +inf is free-molecular.
    #
    # The explicit isnan is load-bearing and must not be "simplified" away: by
    # IEEE 754 every ordered comparison with NaN is False, so `nan < MIN` is also
    # False and a NaN would sail through the regime check below as if it were a
    # large Kn. Ordered comparisons alone cannot reject NaN.
    #
    # Measured before this change: Kn=1e12 -> OK, Kn=+inf -> REFUSE, while
    # classify_knudsen_regime(+inf) said free_molecular and
    # assess_continuum_formula_validity(+inf) said ok/in_domain. Three functions
    # in this one file disagreed about the same physical state; the helper
    # admitted the large finite limit and refused the limit itself.
    if math.isnan(knudsen_number) or knudsen_number < 0.0:
        _refuse(
            "invalid_knudsen_number",
            "Kn must not be NaN or negative; +inf is the free-molecular limit",
        )
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        _refuse(
            category,
            f"Kn_D must be >= {FREE_MOLECULAR_KNUDSEN_MIN:g}",
        )
    return knudsen_number


def _require_transitional_knudsen(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = float(knudsen_number)
    if not math.isfinite(knudsen_number) or knudsen_number <= 0.0:
        _refuse("invalid_knudsen_number", "Kn must be finite and > 0")
    lower_ok = knudsen_number >= VISCOUS_KNUDSEN_MAX
    near_viscous_ok = (
        allow_near_viscous_cross_check
        and 0.0 < knudsen_number < VISCOUS_KNUDSEN_MAX
    )
    if not (lower_ok or near_viscous_ok) or (
        knudsen_number >= FREE_MOLECULAR_KNUDSEN_MIN
    ):
        _refuse(
            "transitional_correlation_out_of_range",
            "BK/Civan correlation is strict over 0.01 <= Kn_D < 10; "
            "near-viscous values require explicit cross-check mode",
        )
    return knudsen_number


def classify_knudsen_regime(knudsen_number: float) -> KnudsenRegime:
    knudsen_number = float(knudsen_number)
    if math.isnan(knudsen_number) or knudsen_number < 0.0:
        _refuse("invalid_knudsen_number", "Kn must not be NaN or negative")
    if math.isinf(knudsen_number):
        return KnudsenRegime.FREE_MOLECULAR
    if knudsen_number < VISCOUS_KNUDSEN_MAX:
        return KnudsenRegime.VISCOUS
    if knudsen_number < FREE_MOLECULAR_KNUDSEN_MIN:
        return KnudsenRegime.TRANSITIONAL
    return KnudsenRegime.FREE_MOLECULAR


def mean_molecular_speed_m_s(
    temperature_K: float,
    molar_mass_kg_mol: float,
) -> float:
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    molar_mass_kg_mol = _require_positive(
        molar_mass_kg_mol,
        name="molar_mass_kg_mol",
        category="invalid_molar_mass",
    )
    return math.sqrt(
        8.0
        * GAS_CONSTANT_J_MOL_K
        * temperature_K
        / (math.pi * molar_mass_kg_mol)
    )


def molecular_aperture_conductance_m3_s(
    open_area_m2: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    knudsen_number: float,
) -> float:
    open_area_m2 = _require_positive(
        open_area_m2,
        name="open_area_m2",
        category="invalid_geometry",
    )
    _require_free_molecular_knudsen(
        knudsen_number,
        category="aperture_requires_free_molecular",
    )
    return 0.25 * open_area_m2 * mean_molecular_speed_m_s(
        temperature_K,
        molar_mass_kg_mol,
    )


def throughput_pa_m3_s(
    conductance_m3_s: float,
    pressure_delta_pa: float,
) -> float:
    conductance_m3_s = _require_nonnegative(
        conductance_m3_s,
        name="conductance_m3_s",
        category="invalid_conductance",
    )
    pressure_delta_pa = _require_nonnegative(
        pressure_delta_pa,
        name="pressure_delta_pa",
        category="invalid_pressure_delta",
    )
    return conductance_m3_s * pressure_delta_pa


def long_tube_clausing_transmission(
    diameter_m: float,
    length_m: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    length_m = _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    length_over_diameter = length_m / diameter_m
    if length_over_diameter < LONG_TUBE_L_OVER_D_MIN:
        _refuse(
            "clausing_long_tube_asymptote_out_of_range",
            f"L/D must be >= {LONG_TUBE_L_OVER_D_MIN:g}",
        )
    return 4.0 * diameter_m / (3.0 * length_m)


def molecular_tube_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    transmission_probability: float,
    knudsen_number: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    transmission_probability = _require_positive(
        transmission_probability,
        name="transmission_probability",
        category="invalid_transmission_probability",
    )
    if transmission_probability > 1.0:
        _refuse(
            "invalid_transmission_probability",
            "transmission_probability must be <= 1",
        )
    _require_free_molecular_knudsen(
        knudsen_number,
        category="tube_requires_free_molecular",
    )
    area_m2 = math.pi * diameter_m ** 2 / 4.0
    return (
        0.25
        * area_m2
        * mean_molecular_speed_m_s(temperature_K, molar_mass_kg_mol)
        * transmission_probability
    )


def long_tube_molecular_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
    *,
    knudsen_number: float,
) -> float:
    transmission_probability = long_tube_clausing_transmission(
        diameter_m,
        length_m,
    )
    return molecular_tube_conductance_m3_s(
        diameter_m,
        length_m,
        temperature_K,
        molar_mass_kg_mol,
        transmission_probability=transmission_probability,
        knudsen_number=knudsen_number,
    )


def dynamic_viscosity_sutherland_pa_s(
    temperature_K: float,
    *,
    eta0_pa_s: float = 17.81e-6,
    reference_temperature_K: float = 300.55,
    sutherland_temperature_K: float = 111.0,
) -> float:
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    eta0_pa_s = _require_positive(
        eta0_pa_s,
        name="eta0_pa_s",
        category="invalid_viscosity_model",
    )
    reference_temperature_K = _require_positive(
        reference_temperature_K,
        name="reference_temperature_K",
        category="invalid_viscosity_model",
    )
    sutherland_temperature_K = _require_positive(
        sutherland_temperature_K,
        name="sutherland_temperature_K",
        category="invalid_viscosity_model",
    )
    return (
        eta0_pa_s
        * (temperature_K / reference_temperature_K) ** 1.5
        * (reference_temperature_K + sutherland_temperature_K)
        / (temperature_K + sutherland_temperature_K)
    )


def beskok_karniadakis_civan_alpha(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = _require_transitional_knudsen(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )
    return 1.358 / (1.0 + 0.170 * knudsen_number ** (-0.4348))


def beskok_karniadakis_rarefaction_factor(
    knudsen_number: float,
    *,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    knudsen_number = _require_transitional_knudsen(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )
    alpha = 1.358 / (1.0 + 0.170 * knudsen_number ** (-0.4348))
    slip_coefficient_b = -1.0
    return (1.0 + alpha * knudsen_number) * (
        1.0 + 4.0 * knudsen_number / (1.0 - slip_coefficient_b * knudsen_number)
    )


def poiseuille_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    mean_pressure_pa: float,
    dynamic_viscosity_pa_s: float,
) -> float:
    diameter_m = _require_positive(
        diameter_m,
        name="diameter_m",
        category="invalid_geometry",
    )
    length_m = _require_positive(
        length_m,
        name="length_m",
        category="invalid_geometry",
    )
    mean_pressure_pa = _require_positive(
        mean_pressure_pa,
        name="mean_pressure_pa",
        category="invalid_pressure",
    )
    dynamic_viscosity_pa_s = _require_positive(
        dynamic_viscosity_pa_s,
        name="dynamic_viscosity_pa_s",
        category="invalid_dynamic_viscosity",
    )
    radius_m = diameter_m / 2.0
    return (
        math.pi
        * radius_m ** 4
        * mean_pressure_pa
        / (8.0 * dynamic_viscosity_pa_s * length_m)
    )


def beskok_karniadakis_civan_conductance_m3_s(
    diameter_m: float,
    length_m: float,
    mean_pressure_pa: float,
    dynamic_viscosity_pa_s: float,
    *,
    knudsen_number: float,
    allow_near_viscous_cross_check: bool = False,
) -> float:
    return poiseuille_conductance_m3_s(
        diameter_m,
        length_m,
        mean_pressure_pa,
        dynamic_viscosity_pa_s,
    ) * beskok_karniadakis_rarefaction_factor(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )


def single_species_mean_free_path_m(
    pressure_pa: float,
    temperature_K: float,
    collision_diameter_m: float,
) -> float:
    pressure_pa = _require_positive(
        pressure_pa,
        name="pressure_pa",
        category="invalid_pressure",
    )
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    collision_diameter_m = _require_positive(
        collision_diameter_m,
        name="collision_diameter_m",
        category="invalid_collision_diameter",
    )
    denominator = (
        math.sqrt(2.0)
        * math.pi
        * collision_diameter_m ** 2
        * pressure_pa
    )
    return BOLTZMANN_CONSTANT_J_K * temperature_K / denominator


def single_species_mean_free_path(
    carrier_species: str,
    pressure_pa: float,
    temperature_K: float,
    characteristic_length_m: float,
) -> MeanFreePathResult:
    characteristic_length_m = _require_positive(
        characteristic_length_m,
        name="characteristic_length_m",
        category="invalid_characteristic_length",
    )
    sigma_m = _require_collision_diameter(carrier_species)
    molar_mass_kg_mol = _require_molar_mass(carrier_species)
    lambda_m = single_species_mean_free_path_m(
        pressure_pa,
        temperature_K,
        sigma_m,
    )
    knudsen_number = lambda_m / characteristic_length_m
    return MeanFreePathResult(
        lambda_m=lambda_m,
        knudsen_number=knudsen_number,
        regime=classify_knudsen_regime(knudsen_number),
        formula_id=FORMULA_SINGLE_SPECIES_MFP,
        test_species=carrier_species,
        carriers=(
            CarrierCollision(
                species=carrier_species,
                mole_fraction=1.0,
                collision_diameter_m=sigma_m,
                molar_mass_kg_mol=molar_mass_kg_mol,
            ),
        ),
        collision_diameter_source=COLLISION_DIAMETER_SOURCE,
    )


def mixture_mean_free_path_m(
    test_species: str,
    carrier_mole_fractions: Mapping[str, float],
    pressure_pa: float,
    temperature_K: float,
) -> float:
    if not carrier_mole_fractions:
        _refuse(
            "missing_carrier_state",
            "carrier mole fractions are required",
        )
    pressure_pa = _require_positive(
        pressure_pa,
        name="pressure_pa",
        category="invalid_pressure",
    )
    temperature_K = _require_positive(
        temperature_K,
        name="temperature_K",
        category="invalid_temperature",
    )
    sigma_i = _require_collision_diameter(test_species)
    molar_mass_i = _require_molar_mass(test_species)
    fraction_sum = 0.0
    denominator_sum = 0.0
    for carrier_species, raw_fraction in carrier_mole_fractions.items():
        mole_fraction = float(raw_fraction)
        if not math.isfinite(mole_fraction) or mole_fraction <= 0.0:
            _refuse(
                "invalid_carrier_mole_fraction",
                f"{carrier_species!r} mole fraction must be finite and > 0",
            )
        sigma_j = _require_collision_diameter(carrier_species)
        molar_mass_j = _require_molar_mass(carrier_species)
        sigma_ij = (sigma_i + sigma_j) / 2.0
        denominator_sum += (
            mole_fraction
            * math.pi
            * sigma_ij ** 2
            * math.sqrt(1.0 + molar_mass_i / molar_mass_j)
        )
        fraction_sum += mole_fraction
    if not math.isclose(fraction_sum, 1.0, rel_tol=0.0, abs_tol=1e-9):
        _refuse(
            "carrier_mole_fractions_not_normalized",
            "carrier mole fractions must sum to 1.0",
        )
    return BOLTZMANN_CONSTANT_J_K * temperature_K / (
        pressure_pa * denominator_sum
    )


def carrier_mixture_mean_free_path(
    test_species: str,
    carrier_mole_fractions: Mapping[str, float],
    pressure_pa: float,
    temperature_K: float,
    characteristic_length_m: float,
) -> MeanFreePathResult:
    characteristic_length_m = _require_positive(
        characteristic_length_m,
        name="characteristic_length_m",
        category="invalid_characteristic_length",
    )
    lambda_m = mixture_mean_free_path_m(
        test_species,
        carrier_mole_fractions,
        pressure_pa,
        temperature_K,
    )
    carriers = tuple(
        CarrierCollision(
            species=species,
            mole_fraction=float(fraction),
            collision_diameter_m=_require_collision_diameter(species),
            molar_mass_kg_mol=_require_molar_mass(species),
        )
        for species, fraction in carrier_mole_fractions.items()
    )
    knudsen_number = lambda_m / characteristic_length_m
    return MeanFreePathResult(
        lambda_m=lambda_m,
        knudsen_number=knudsen_number,
        regime=classify_knudsen_regime(knudsen_number),
        formula_id=FORMULA_MIXTURE_MFP,
        test_species=test_species,
        carriers=carriers,
        collision_diameter_source=COLLISION_DIAMETER_SOURCE,
    )
