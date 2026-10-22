"""Pinned VPR-P0a transport-regime formulas and applicability.

Classification (KnudsenRegime / classify_knudsen_regime) is shared with the
live condensation path. Out-of-domain continuum formulas compute and flag their
extension in every campaign (d-209); invalid inputs still refuse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from simulator.physical_constants import BOLTZMANN, GAS_CONSTANT
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.silent_zero import (
    CATEGORY_MARK,
    ZeroBecause,
    note_dict,
)
from simulator.transport_constants import (
    CARRIER_GAS_PROPERTIES,
    CarrierGasProperties,
    COLLISION_DIAMETER_SOURCE,
    COLLISION_DIAMETERS_M,
    FREE_MOLECULAR_KNUDSEN_MIN,
    VISCOUS_KNUDSEN_MAX,
)


# Single-sourced from the physical_constants leaf (SC-CONST pass-B); byte-identical
# to the prior local literals (8.31446261815324 / 1.380649e-23).
GAS_CONSTANT_J_MOL_K = GAS_CONSTANT
BOLTZMANN_CONSTANT_J_K = BOLTZMANN

# Keep the runtime pipe law's historical six-significant-figure R, derived
# from the physical-constants owner: rounding 8.31446261815324 to 5 decimal
# places gives 8.31446 J/(mol K). The relocation must preserve float.hex pins.
PIPE_GAS_CONSTANT_J_MOL_K = round(GAS_CONSTANT_J_MOL_K, 5)

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


# DERIVATION: premise — steady isothermal ideal-gas flow keeps molar flow
# constant while local volumetric flow varies as 1/P. Integrating circular
# Poiseuille along the pipe gives pV throughput
# π*d^4*(P1^2-P2^2)/(256*eta*L): the incompressible 128 denominator gains
# the factor 2 from P1^2-P2^2 = 2*p_mean*(P1-P2). Dividing by R*T and
# multiplying by molar mass M gives kg/s; units are
# [m^4*Pa^2*kg/mol]/[(Pa*s)*m*(Pa*m^3/(mol*K))*K] = kg/s. Worked check:
# d=0.08 m, L=2 m, T=1473.15 K, M(SiO)=0.0440845 kg/mol and m_dot=0.003
# kg/s require P1=426.45 Pa against vacuum; the forward law returns
# 0.003000 kg/s. Both capacity and pressure inversion use this one factor.
COMPRESSIBLE_POISEUILLE_DENOMINATOR = 256.0  # dimensionless — integrated mean-pressure factor


def gas_dynamic_viscosity_Pa_s(T_K: float) -> float:
    """Return the provisional N₂-like dynamic viscosity at temperature T_K."""
    return 1.8e-5 * (T_K / 300.0) ** 0.7


def compressible_poiseuille_mass_flow(
    d: float,
    L: float,
    M_avg: float,
    eta: float,
    T_K: float,
    p_upstream_Pa: float,
    p_downstream_Pa: float,
) -> float:
    """Return compressible Poiseuille mass flow in kg/s."""
    pressure_square_delta_Pa2 = max(
        0.0,
        p_upstream_Pa**2 - p_downstream_Pa**2,
    )
    numerator = math.pi * d**4 * M_avg * pressure_square_delta_Pa2
    denominator = (
        COMPRESSIBLE_POISEUILLE_DENOMINATOR
        * eta
        * L
        * PIPE_GAS_CONSTANT_J_MOL_K
        * T_K
    )
    return numerator / denominator


def pipe_mass_flow_kg_s(d, L, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa):
    """Isothermal circular pipe capacity, kg/s; diffuse, non-sticking walls.

    Premise: Knudsen's semi-empirical long-capillary conductance adds viscous
    and rarefied transport, with a measured transitional correction. Knudsen
    (1909), Ann. Phys. 333, 75-130, doi:10.1002/andp.19093330106;
    explicit SI form and constants: Odnoralov (2005), RBAV 24(2), 110-116,
    Eqs. (2)-(5), https://www.sbvacuo.org.br/rbav/article/download/101/101.
    Algebra: Q = Q_v + C_m*(P1-P2)*(1+2.507*x)/(1+3.095*x),
    x = P_mean*d/eta * sqrt(M/(2*pi*R*T)). Q is pV throughput, NOT
    volumetric flow: [Pa*m^3/s]. Since n_dot=Q/(RT), m_dot=M*Q/(RT).
    x is dimensionless: [Pa*m/(Pa*s)] * [s/m] = 1.

    Molecular C_m = (A*v_mean/4)*tau [m^2*m/s = m^3/s], where tau
    is the Clausing transmission probability for a straight circular tube.
    Use Santeler's published approximation at every aspect ratio, avoiding
    a discontinuous short/long switch. For L/d -> infinity, tau -> 4d/(3L)
    and C_m -> pi*d^3*v_mean/(12L). For L/d -> 0, tau -> 1 (aperture).
    At P_mean -> 0, x -> 0, Q_v=O(P^2), Q_m=O(P), hence C -> C_m;
    at high P, the correction -> 2.507/3.095 and Q_v dominates exactly
    as the existing compressible Poiseuille limit (relative error O(1/P)).

    The bridge is INTERPOLATED authority, not a kinetic-theory solution.
    Finite-length molecular correction + long-capillary Knudsen bridge is
    an engineering extension for short tubes, not a validated short-tube
    transitional solution. Viscous flow remains laminar/fully developed;
    choking, turbulent and entrance losses are outside this model. The
    caller supplies viscosity (runtime retains its provisional N2-like law).
    """
    pressure_delta = max(0.0, p_upstream_Pa - p_downstream_Pa)
    if pressure_delta == 0.0:
        return 0.0
    molecular_conductance = molecular_tube_conductance_m3_s(
        d, L, T_K, M_avg,
        transmission_probability=clausing_transmission_probability(d, L),
        # This is the molecular COMPONENT's limiting conductance, irrespective
        # of the actual regime; the Knudsen bridge supplies the pressure term.
        knudsen_number=math.inf,
    )
    x = _pipe_knudsen_parameter(d, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa)
    molecular_throughput = molecular_conductance * pressure_delta
    correction = (1.0 + 2.507 * x) / (1.0 + 3.095 * x)
    return compressible_poiseuille_mass_flow(
        d, L, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa,
    ) + molecular_throughput * correction * M_avg / (PIPE_GAS_CONSTANT_J_MOL_K * T_K)


def _pipe_knudsen_parameter(d, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa):
    """The published bridge parameter, shared by flow and authority classification."""
    return (0.5 * (p_upstream_Pa + p_downstream_Pa) * d / eta
            * math.sqrt(M_avg / (2.0 * math.pi * PIPE_GAS_CONSTANT_J_MOL_K * T_K)))


MOLECULAR_MIXTURE_ROUTING_APPROXIMATION = "molecular_mixture_routing_approximation"


def pipe_conductance_authority_notice(d, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa):
    """Classify the actual pipe calculation, not a condensation wall segment.

    Viscosity-based mean free path lambda_eta = eta/P*sqrt(pi*RT/(2M));
    multiplying by x/d gives 1/2, so Kn_eta=lambda_eta/d=1/(2x).
    Both are dimensionless and use the pipe's arithmetic MEAN pressure.
    """
    x = _pipe_knudsen_parameter(d, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa)
    knudsen_number = 0.5 / x if x > 0.0 else math.inf
    if ((p_upstream_Pa > 0.0 or p_downstream_Pa > 0.0)
            and mean_mixture_bulk_fraction_routing_is_approximation(knudsen_number)):
        return (
            f"{MOLECULAR_MIXTURE_ROUTING_APPROXIMATION}: mean-mixture total capacity "
            "with nonfractionating bulk-fraction species removal; oxygen attainability "
            "and outlet composition are not species-certified; opposed partial-pressure "
            "exchange at equal total pressures is unresolved."
        )
    if (p_upstream_Pa > p_downstream_Pa
            and classify_knudsen_regime(knudsen_number) is KnudsenRegime.TRANSITIONAL):
        return (
            'Pipe conductance authority=interpolated: Knudsen (1909) '
            'semi-empirical bridge with Santeler/Clausing molecular transmission; '
            'short-tube transition and provisional N2-like viscosity are unvalidated.'
        )
    return None


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
# Metal / SiO extraction is recorded for transport-model provenance.
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
    """Whether the process answer depends on transport-model accuracy."""

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
    Poiseuille P_bulk: compute-and-mark its existing extension, including
    where transport accuracy is load-bearing. Regime alone never refuses.
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
    note = note_dict(
        ZeroBecause.OUT_OF_DOMAIN_MARKED,
        site=str(asking_site),
        field="continuum_p_bulk",
        detail=(
            f"{detail}; process_regime={regime.value}; "
            f"stage={asked_stage!r}; flagged continuum extension "
            "(d-209; kinetic lane t-1396/t-1397)"
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
        load_bearing=load_bearing,
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
    """Raise TransportRegimeRefusal for invalid transport inputs only."""

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


def _require_pressure_allowing_vacuum(
    value: float, *, name: str, category: str
) -> float:
    """Validate a pressure where ZERO is a physical state, not a missing input.

    b-287 / SC-146. Mean-free-path takes lambda = kT / (sqrt(2) pi d^2 P), so as
    P -> 0 lambda -> +inf and Kn = lambda/L -> +inf: true vacuum, the
    free-molecular limit, and this project's lunar baseline. P = 0 is therefore a
    KEEP-THE-ZERO value here, distinct from an unknown pressure.

    Deliberately a SEPARATE helper rather than a relaxation of _require_positive.
    That helper also guards temperature_K, molar mass, collision diameter, tube
    diameter and length, characteristic length, and the Poiseuille
    mean_pressure_pa -- none of which have a meaningful zero, and Poiseuille flow
    at P = 0 is not a limit but a nonsense. Widening the shared helper to admit
    this one legitimate zero would admit six illegitimate ones. Shared form,
    different verdicts: fix the call site, not the helper.
    """

    value = float(value)
    if math.isnan(value) or value < 0.0:
        _refuse(category, f"{name} must not be NaN or negative")
    return value


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


def mean_mixture_bulk_fraction_routing_is_approximation(
    knudsen_number: float,
) -> bool:
    """Whether molecular bulk-fraction routing invokes the F3 approximation."""
    return classify_knudsen_regime(knudsen_number) is KnudsenRegime.FREE_MOLECULAR


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


def clausing_transmission_probability(diameter_m: float, length_m: float) -> float:
    """Santeler approximation to Clausing's diffuse-wall integral equation.

    Chiggiato, CERN vacuum-technology notes, Eq. (21), reports <0.7% error:
    https://indico.cern.ch/event/471931/contributions/1149644/attachments/
    1226014/1794769/JUAS-Notes_Vacuum_Tech-Chiggiato.pdf
    Santeler, J. Vac. Sci. Technol. A 4, 338 (1986).
    With radius r=d/2, tau = 1/[1+3L/(8r)*(1+1/(3*(1+L/(7r))))].
    All terms are dimensionless. tau -> 1 at zero length; tau -> 4d/(3L)
    for long tubes. Thus 0 < tau <= 1, including short tubes where the
    long-tube asymptote would give an impossible transmission > 1.
    """
    diameter_m = _require_positive(
        diameter_m, name="diameter_m", category="invalid_geometry",
    )
    length_m = _require_nonnegative(
        length_m, name="length_m", category="invalid_geometry",
    )
    length_over_radius = 2.0 * length_m / diameter_m
    return 1.0 / (1.0 + 3.0 * length_over_radius / 8.0 * (
        1.0 + 1.0 / (3.0 * (1.0 + length_over_radius / 7.0))
    ))


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


def independent_molecular_species_molar_flows_mol_s(
    *,
    area_m2: float,
    transmission_probability: float,
    temperature_K: float,
    molar_masses_kg_mol: Mapping[str, float],
    upstream_partial_pressures_Pa: Mapping[str, float],
    downstream_partial_pressures_Pa: Mapping[str, float],
) -> dict[str, float]:
    """Return signed species flows in the independent free-molecular limit.

    Each species has its own Clausing conductance and partial-pressure drop;
    positive flow is upstream-to-downstream. This is a reference calculation
    for F3 mixtures, not the mean-mixture pipe model used for runtime capacity.
    """
    species = set(molar_masses_kg_mol)
    if not species:
        _refuse(
            "missing_molecular_species",
            "at least one species is required for molecular reference flow",
        )
    if species != set(upstream_partial_pressures_Pa) or species != set(
        downstream_partial_pressures_Pa
    ):
        _refuse(
            "molecular_species_mismatch",
            "molar masses and both partial-pressure maps must name the same species",
        )

    area_m2 = _require_positive(
        area_m2, name="area_m2", category="invalid_geometry"
    )
    temperature_K = _require_positive(
        temperature_K, name="temperature_K", category="invalid_temperature"
    )
    transmission_probability = _require_nonnegative(
        transmission_probability,
        name="transmission_probability",
        category="invalid_transmission_probability",
    )
    if transmission_probability > 1.0:
        _refuse(
            "invalid_transmission_probability",
            "transmission_probability must be <= 1",
        )

    flows: dict[str, float] = {}
    for species_name, molar_mass in molar_masses_kg_mol.items():
        molar_mass = _require_positive(
            molar_mass,
            name=f"{species_name}.molar_mass_kg_mol",
            category="invalid_molar_mass",
        )
        upstream_pressure = _require_nonnegative(
            upstream_partial_pressures_Pa[species_name],
            name=f"{species_name}.upstream_partial_pressure_Pa",
            category="invalid_pressure",
        )
        downstream_pressure = _require_nonnegative(
            downstream_partial_pressures_Pa[species_name],
            name=f"{species_name}.downstream_partial_pressure_Pa",
            category="invalid_pressure",
        )
        conductance_m3_s = (
            0.25
            * area_m2
            * mean_molecular_speed_m_s(temperature_K, molar_mass)
            * transmission_probability
        )
        # Premise: in the independent molecular limit each species flows
        # through its own Clausing conductance C_i=A*tau*cbar_i/4, so
        # n_dot_i=C_i*(p_up,i-p_down,i)/(R*T). C_i has units m^3/s and
        # pressure/(R*T) has units mol/m^3, yielding signed mol/s. Equal total
        # pressures can still produce opposed species flows; equal partial
        # pressures give zero for that species, the correct equilibrium limit.
        flows[species_name] = (
            conductance_m3_s
            * (upstream_pressure - downstream_pressure)
            / (GAS_CONSTANT_J_MOL_K * temperature_K)
        )
    return flows


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
    alpha = beskok_karniadakis_civan_alpha(
        knudsen_number,
        allow_near_viscous_cross_check=allow_near_viscous_cross_check,
    )
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


def _minimum_pressure_pa_for_knudsen(
    temperature_K: float,
    collision_diameter_m: float,
    characteristic_length_m: float,
    knudsen_ceiling: float,
) -> float:
    """Invert the hard-sphere mean-free-path limit in its existing order."""
    return BOLTZMANN_CONSTANT_J_K * temperature_K / (
        math.sqrt(2.0)
        * math.pi
        * collision_diameter_m ** 2
        * characteristic_length_m
        * knudsen_ceiling
    )


def _single_species_mean_free_path_m_unchecked(
    pressure_pa: float,
    temperature_K: float,
    collision_diameter_m: float,
) -> float:
    """Apply the mean-free-path arithmetic after caller-specific guards."""
    denominator = (
        math.sqrt(2.0)
        * math.pi
        * collision_diameter_m ** 2
        * pressure_pa
    )
    return BOLTZMANN_CONSTANT_J_K * temperature_K / denominator


def single_species_mean_free_path_m(
    pressure_pa: float,
    temperature_K: float,
    collision_diameter_m: float,
) -> float:
    pressure_pa = _require_pressure_allowing_vacuum(
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
    if pressure_pa == 0.0:
        # lambda = kT / (sqrt(2) pi d^2 P). The denominator is linear in P, so
        # lambda diverges as P -> 0 and the limit is +inf, not an error. Returned
        # explicitly because the float division below would raise
        # ZeroDivisionError instead of producing the limit. Downstream this gives
        # Kn = lambda/L = +inf, which classify_knudsen_regime already calls
        # FREE_MOLECULAR and which b-283 taught the free-molecular guard to accept
        # -- before this change that limit was unreachable through this path.
        # Continuity check: 1e-9 Pa -> 4.25e7 m, 1e-12 Pa -> 4.25e10 m, monotonic
        # increase by the same factor as the pressure decrease.
        return math.inf
    return _single_species_mean_free_path_m_unchecked(
        pressure_pa,
        temperature_K,
        collision_diameter_m,
    )


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
    pressure_pa = _require_pressure_allowing_vacuum(
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
    if pressure_pa == 0.0:
        # Same limit as the single-species case: the denominator is linear in P,
        # so lambda -> +inf as P -> 0. Placed AFTER the carrier-mole-fraction and
        # normalisation checks on purpose -- a malformed carrier spec must still
        # refuse at true vacuum. Only the arithmetic changes here, not any other
        # refusal.
        return math.inf
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


def pipe_pressure_pa_for_mass_flow(d, L, M_avg, eta, T_K, F_kg_s, p_downstream_Pa):
    """Invert the pipe mass-flow law; inputs are validated by the caller."""
    numerator = (
        COMPRESSIBLE_POISEUILLE_DENOMINATOR
        * eta
        * L
        * PIPE_GAS_CONSTANT_J_MOL_K
        * T_K
        * F_kg_s
    )
    denominator = math.pi * M_avg * d**4
    if denominator <= 0.0:
        return 0.0
    # F-112 inverse derivation:
    # premise: F = C * (P_up^2 - P_down^2).
    # algebra: P_up = sqrt(F / C + P_down^2).
    # unit check: numerator/denominator is Pa^2, and P_down^2 is Pa^2.
    # sanity: zero downstream reproduces the legacy sqrt(F/C); non-zero
    # downstream raises the required upstream pressure for the same flow.
    pressure_Pa = math.sqrt(  # Pa — upstream pressure from pressure-square balance
        max(0.0, numerator / denominator + p_downstream_Pa**2)
    )
    # The viscous-only pressure is an upper bound: the molecular term is
    # nonnegative. Bracket from P_down (zero forward flow) and invert the ONE
    # forward law. Its molecular term is monotone: with x=k*(P1+P2)/2 and
    # f=(1+a*x)/(1+b*x), d[(P1-P2)*f]/dP1 equals
    # a/b + (1-a/b)*(1+b*k*P2)/(1+b*x)^2 > 0 (0<a=2.507<b=3.095).
    # Both bounds have units Pa. At vanishing rarefaction this recovers the
    # Poiseuille root; in molecular flow pressure drop becomes linear in flow.
    low, high = p_downstream_Pa, pressure_Pa
    for _ in range(64):  # binary64 pressure resolution, not a physics tolerance
        middle = low + (high - low) / 2.0
        if middle == low or middle == high:
            break
        flow = pipe_mass_flow_kg_s(d, L, M_avg, eta, T_K, middle, p_downstream_Pa)
        if flow < F_kg_s:
            low = middle
        else:
            high = middle
    return low + (high - low) / 2.0


def pipe_downstream_pressure_pa_for_flow_fraction(d, L, M_avg, eta, T_K, p_upstream_Pa, fraction):
    """Invert the same pipe owner for a fraction of its vacuum capacity."""
    # Premise: at fixed P1 the owner F(P1,P2) decreases continuously from
    # F(P1,0) to zero. Solve F(P1,P2)=fraction*F(P1,0) on [0,P1]. Using
    # a dimensionless fraction also preserves explicitly scaled hardware
    # capacities. Both bounds are Pa; the target and forward values are kg/s.
    # With x=k(P1+P2)/2, a=2.507, b=3.095, the molecular derivative in P2
    # is -a/b-(1-a/b)*(1+b*k*P1)/(1+b*x)^2 < 0; the viscous derivative
    # is nonpositive too. Hence this inverse has a unique physical root.
    # Molecular limit: P2=P1*(1-fraction); viscous limit:
    # P2=P1*sqrt(1-fraction). No pressure-square proxy survives in callers.
    if fraction <= 0.0:
        return p_upstream_Pa
    if fraction >= 1.0:
        return 0.0
    target = fraction * pipe_mass_flow_kg_s(
        d, L, M_avg, eta, T_K, p_upstream_Pa, 0.0,
    )
    low, high = 0.0, p_upstream_Pa
    for _ in range(64):
        middle = low + (high - low) / 2.0
        if middle == low or middle == high:
            break
        if pipe_mass_flow_kg_s(d, L, M_avg, eta, T_K, p_upstream_Pa, middle) > target:
            low = middle
        else:
            high = middle
    return low + (high - low) / 2.0


def pipe_pressure_capacity_fraction(d, L, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa):
    """Finite-pressure / vacuum capacity, with the same gas and geometry."""
    vacuum = pipe_mass_flow_kg_s(d, L, M_avg, eta, T_K, p_upstream_Pa, 0.0)
    # F(P1,P2)/F(P1,0) is dimensionless. Empty vacuum has zero throughput,
    # not an undefined capacity ratio. Limits: molecular 1-P2/P1; viscous
    # 1-(P2/P1)^2. A constant square-law fraction cannot span both regimes.
    if vacuum <= 0.0:
        return 0.0
    return pipe_mass_flow_kg_s(
        d, L, M_avg, eta, T_K, p_upstream_Pa, p_downstream_Pa,
    ) / vacuum


# Historical source/deposition R is intentionally retained for bit identity.
SURFACE_GAS_CONSTANT_J_MOL_K = 8.314462618
DEFAULT_SHERWOOD_LAMINAR = 3.66
DEFAULT_CARRIER_GAS = 'N2'
SUPPORTED_CARRIER_GAS_LABELS = 'He/pHe, N2/pN2, Kr/pKr, Ar/pAr (legacy), O2/pO2, CO2/pCO2'


def pipe_wall_sherwood(operator_value, sherwood_laminar=DEFAULT_SHERWOOD_LAMINAR):
    """Legacy wall/duct proxy; caller supplies the operator-bounded value."""
    return float(sherwood_laminar) * math.sqrt(max(1.0, operator_value))


def film_pressure_conductance(sh, diffusivity_m2_s, length_m, temperature_K):
    """Sh*D/(L*R*T), mol/(m² s Pa); multiply by species M for kg basis."""
    return sh * diffusivity_m2_s / (length_m * SURFACE_GAS_CONSTANT_J_MOL_K * temperature_K)


def hertz_knudsen_mass_conductance(molar_mass_kg_mol, temperature_K):
    """Unit-alpha mass-flux coefficient, sqrt(M/(2*pi*R*T)) [s/m]."""
    return math.sqrt(
        molar_mass_kg_mol / (2.0 * math.pi * SURFACE_GAS_CONSTANT_J_MOL_K * temperature_K)
    )


def _carrier_collision_diameter_angstrom(species: str) -> float:
    return round(COLLISION_DIAMETERS_M[species] * 1e10, 3)


def _carrier_lennard_jones_params(species: str) -> tuple[float, float, float]:
    properties = CARRIER_GAS_PROPERTIES[species]
    sigma_angstrom = (
        properties.collision_diameter_m * 1e10
        if species == 'N2'
        else _carrier_collision_diameter_angstrom(species)
    )
    return (
        sigma_angstrom,
        properties.lennard_jones_epsilon_over_k_K,
        properties.lennard_jones_molar_mass_g_mol,
    )


# Chapman-Enskog Lennard-Jones parameters (vapor species + carrier gas).
# Species: collision diameter (Angstrom), ε/k_B (K), molecular mass (g/mol).
# Primary source: Bird/Stewart/Lightfoot "Transport Phenomena" 2nd ed.
# Table E.1 for noble gases + Na/K/Ca; remaining species use the Svehla
# 1962 (NASA TR R-132) or Hirschfelder/Curtiss/Bird canonical estimates.
# Vapor-phase Fe/Cr/Mn/Al/Ti are proxy estimates because direct kinetic-
# theory measurements for transition-metal vapor are sparse. Their exact
# derivation is not established here: in particular, the stored epsilon/k_B
# rows do not reproduce ``1.3 * T_boiling`` and must not be described as if
# they do. The Chapman-Enskog result is moderately sensitive to sigma (Ω_D
# ~constant in the high-T limit, D_AB ∝ 1/σ_AB²) and weakly sensitive
# to ε (collision integral Ω_D varies <30% across the simulator's T
# range). At the typical C2A operating point (10 mbar = 1000 Pa,
# 1973.15 K) ``_chapman_enskog_d_ab_m2_s('SiO', ...)`` returns
# 0.0491 m²/s vs the legacy 0.01 constant (about 5×). The previously
# quoted ~0.042 m²/s is the same formula at 1773 K (1500 C), not at
# 1973 K.
_LENNARD_JONES_PARAMS: dict[str, tuple[float, float, float]] = {
    # (sigma Angstrom, eps/k_B K, M g/mol)
    # N2 sigma derives from N2_COLLISION_DIAMETER_M (one grounded source, BUG-013)
    'N2':  _carrier_lennard_jones_params('N2'),  # BSL Table E.1
    'Ar':  _carrier_lennard_jones_params('Ar'),  # BSL Table E.1
    'Kr':  _carrier_lennard_jones_params('Kr'),  # BSL Table E.1 (t-395)
    'CO2': _carrier_lennard_jones_params('CO2'),  # BSL Table E.1
    'O2':  _carrier_lennard_jones_params('O2'),  # BSL Table E.1
    'He':  _carrier_lennard_jones_params('He'),  # BSL Table E.1
    'Na':  (3.567, 1375.0, 22.990),  # Svehla 1962 vapor
    'K':   (3.987, 1305.0, 39.098),  # Svehla 1962 vapor
    'Ca':  (3.880, 1224.0, 40.078),  # BSL extension
    'Fe':  (2.940, 6026.0, 55.845),  # Estimated (transition-metal vapor)
    'Mg':  (3.060, 1614.0, 24.305),  # Estimated
    'Mn':  (2.950, 1100.0, 54.938),  # Estimated
    'Cr':  (2.880, 6000.0, 51.996),  # Estimated
    'Al':  (2.940, 3093.0, 26.982),  # Estimated
    'Ti':  (2.890, 6000.0, 47.867),  # Estimated
    'SiO': (3.374, 71.4,   44.085),  # Estimated; sparse direct data
}

def _unsupported_carrier_gas_error(carrier_gas: Any) -> ValueError:
    return ValueError(
        f'Unsupported condensation carrier_gas {carrier_gas!r}; '
        f'supported carrier gases: {SUPPORTED_CARRIER_GAS_LABELS}'
    )


def _canonical_carrier_gas_key(
    carrier_gas: Any,
    *,
    allow_unset: bool = False,
) -> str:
    if carrier_gas is None:
        return '' if allow_unset else DEFAULT_CARRIER_GAS
    text = str(carrier_gas).strip()
    if not text:
        if allow_unset:
            return ''
        raise ValueError(
            'condensation carrier_gas must be non-empty when provided'
        )
    upper = text.upper().replace(' ', '').replace('_', '').replace('-', '')
    if upper in {'N2', 'PN2', 'N2SWEEP', 'PN2SWEEP'}:
        return 'N2'
    if upper in {'HE', 'PHE'}:
        return 'He'
    if upper in {'AR', 'PAR'}:
        return 'Ar'
    # Kr (t-395): recommended fallback buffer replacing Ar (legacy kept).
    if upper in {'KR', 'PKR'}:
        return 'Kr'
    if upper in {'O2', 'PO2', 'O2BACKPRESSURE', 'CONTROLLEDO2'}:
        return 'O2'
    if upper in {'CO2', 'PCO2', 'CO2BACKPRESSURE'}:
        return 'CO2'
    if upper.endswith('%CO2'):
        try:
            co2_percent = float(upper[:-4])
        except ValueError:
            co2_percent = 0.0
        if 0.0 < co2_percent <= 100.0:
            return 'CO2'
    raise _unsupported_carrier_gas_error(carrier_gas)


def carrier_gas_properties(carrier_gas: str | None) -> CarrierGasProperties:
    key = _canonical_carrier_gas_key(carrier_gas)
    try:
        return CARRIER_GAS_PROPERTIES[key]
    except KeyError:
        raise _unsupported_carrier_gas_error(carrier_gas) from None


def _carrier_collision_diameter_m(carrier_gas: str) -> float:
    return carrier_gas_properties(carrier_gas).collision_diameter_m


def _neufeld_collision_integral_omega_d(T_star: float) -> float:
    """Neufeld 1972 correlation for the dimensionless collision integral
    ``Ω_D`` as a function of the reduced temperature
    ``T* = k_B * T / ε_AB``.

    Premise: ``T* = T / sqrt((ε_A/k_B)(ε_B/k_B))`` with this module's
    ``_LENNARD_JONES_PARAMS`` and N2 ``ε/k_B = 71.4 K``. At 1500-2000 K
    that gives T* ≈ 2.3-3.1 for Fe/Cr/Ti, ≈ 3.2-4.3 for Al, ≈ 5.4-7.1
    for Mn, and 21-28 for SiO. T* = 50 is unreachable on this table
    (would need ε_AB/k_B ≈ 30-40 K, below N2's own 71.4 K).

    The Neufeld, Janzen & Aziz (1972) correlation interval is
    ``0.3 <= T* <= 100`` (J. Chem. Phys. 57, 1100), with probable error
    below 0.1% inside that interval. This helper still evaluates T* <= 0
    as 1.0 and does not refuse out-of-interval T*.

    Reference: Neufeld, P.D., Janzen, A.R., Aziz, R.A.,
    "Empirical equations to calculate 16 of the transport collision
    integrals for the Lennard-Jones (12-6) potential",
    J. Chem. Phys. 57, 1100 (1972).
    """
    if T_star <= 0.0:
        return 1.0
    return (
        1.06036 / (T_star ** 0.1561)
        + 0.193 / math.exp(0.47635 * T_star)
        + 1.03587 / math.exp(1.52996 * T_star)
        + 1.76474 / math.exp(3.89411 * T_star)
    )


# Bird, Stewart & Lightfoot, Transport Phenomena, 2nd ed., the working
# Chapman–Enskog equation (their Eq. 17.3-10): D_AB in cm²/s with the
# sqrt(1/M_A + 1/M_B) mass grouping and P in atm. The reduced-mass form
# below multiplies by √2. See _chapman_enskog_d_ab_m2_s.
_CHAPMAN_ENSKOG_BSL_SQRT_MASS_PREFACTOR = 0.0018583
_CHAPMAN_ENSKOG_REDUCED_MASS_PREFACTOR = (
    _CHAPMAN_ENSKOG_BSL_SQRT_MASS_PREFACTOR * math.sqrt(2.0)
)


def _chapman_enskog_d_ab_m2_s(
    species: str,
    T_K: float,
    pressure_pa: float,
    carrier: str = DEFAULT_CARRIER_GAS,
    *,
    species_params: tuple[float, float, float] | None = None,
) -> float:
    """Binary diffusion coefficient ``D_AB`` for ``species`` in
    ``carrier`` gas at ``T_K``, ``pressure_pa``. Returns m²/s.

    Premise. Bird, Stewart & Lightfoot, *Transport Phenomena*, 2nd ed.,
    Eq. 17.3-10 (the Hirschfelder / Chapman–Enskog working equation):

        D_AB [cm²/s] = 0.0018583 * T[K]^{3/2} * sqrt(1/M_A + 1/M_B)
                       / (P[atm] * σ_AB[Å]² * Ω_D)

    M_A and M_B are g/mol. Ω_D is the dimensionless Neufeld collision
    integral (Neufeld, Janzen & Aziz, J. Chem. Phys. 57, 1100, 1972).
    The constant 0.0018583 is defined for pressure in atmospheres, not bar.

    Algebra. This function groups the mass as the Reid reduced mass

        M_AB = 2 / (1/M_A + 1/M_B)          [g/mol]

    so sqrt(1/M_A + 1/M_B) = sqrt(2/M_AB) = √2 / sqrt(M_AB). Substituting:

        D_AB [cm²/s] = (0.0018583 * √2) * T^{3/2}
                       / (P[atm] * sqrt(M_AB) * σ_AB² * Ω_D)

        prefactor = 0.0018583 * √2 = 0.002628033

        P[atm] = P[Pa] / 101325
        D_AB [m²/s] = D_AB [cm²/s] * 1e-4

    σ_AB = (σ_A + σ_B) / 2 in Å. Ω_D is evaluated at T* = T / ε_AB.

    Unit check. With T in K, P in atm, M in g/mol and σ in Å, the
    0.0018583 quotient is cm²/s. Multiplying by √2 only rewrites the
    mass grouping; it does not change dimensions. The trailing ×1e-4
    converts cm² to m² (1 m² = 10^4 cm²).

    The constant 0.00266 is that same prefactor rounded for pressure in
    bar: 0.002628033 * (101325/100000) = 0.00266285 ≈ 0.00266 (Reid,
    Prausnitz & Poling, *The Properties of Gases and Liquids*, Eq. 11-3.2).
    Using 0.00266 together with P in atm overstated D_AB by
    0.00266 / 0.002628033 = 1.216%.

    Sanity. O2 self-diffusion at 1 atm from these BSL Table E.1 Lennard-Jones
    parameters (σ = 3.467 Å, ε/k = 106.7 K, M = 31.998 g/mol): 0.2054 cm²/s
    at 298.15 K and 1.630 cm²/s at 1000 K. NIST TN 2279 (Burgess 2024),
    ln(D/cm² s⁻¹) = −10.787 − 44.220/T + 1.649 ln(T), fit to Hellmann's
    O2–O2 ab initio values over 55–2000 K, gives 0.2144 cm²/s and
    1.750 cm²/s. The helper is 4.2% and 6.9% low. That is inside
    Chapman–Enskog accuracy for a Lennard-Jones pair; the collision
    diameter is not adjusted to close the gap.

    ``species_params`` lets a caller supply a path-local proxy without adding
    it to the shared condensation table. Returns 0 on unknown species (caller
    falls back to the legacy constant via the explicit
    ``diffusion_coefficient_m2_s`` parameter on the flux callers).
    """
    if species_params is None:
        species_params = _LENNARD_JONES_PARAMS.get(species)
    carrier_key = _canonical_carrier_gas_key(carrier)
    carrier_params = _LENNARD_JONES_PARAMS.get(carrier_key)
    if species_params is None or carrier_params is None:
        return 0.0
    if T_K <= 0.0 or pressure_pa <= 0.0:
        return 0.0
    sigma_a, eps_a, M_a = species_params
    sigma_b, eps_b, M_b = carrier_params
    sigma_ab = 0.5 * (sigma_a + sigma_b)            # Angstrom
    eps_ab = math.sqrt(eps_a * eps_b)               # K
    M_ab_reduced = 2.0 / (1.0 / M_a + 1.0 / M_b)    # g/mol
    T_star = T_K / eps_ab
    omega_d = _neufeld_collision_integral_omega_d(T_star)
    pressure_atm = pressure_pa / 101325.0
    if pressure_atm <= 0.0:
        return 0.0
    # cm²/s by the reduced-mass BSL form, then convert to m²/s (1 cm² = 1e-4 m²)
    D_AB_cm2_s = (
        _CHAPMAN_ENSKOG_REDUCED_MASS_PREFACTOR * (T_K ** 1.5)
        / (pressure_atm * math.sqrt(M_ab_reduced)
           * (sigma_ab ** 2) * omega_d)
    )
    return D_AB_cm2_s * 1.0e-4


def _mean_free_path_m(
    pressure_pa: float,
    T_K: float,
    molecular_diameter_m: float | None = None,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
) -> float:
    if molecular_diameter_m is None:
        molecular_diameter_m = _carrier_collision_diameter_m(carrier_gas)
    if pressure_pa <= 0.0:
        return math.inf
    if T_K <= 0.0 or molecular_diameter_m <= 0.0:
        return 0.0
    try:
        return _single_species_mean_free_path_m_unchecked(
            pressure_pa,
            T_K,
            molecular_diameter_m,
        )
    except ZeroDivisionError:
        # Preserve the prior limit when positive finite inputs underflow to a
        # zero denominator.
        return math.inf


def _knudsen_number(
    pressure_pa: float,
    T_K: float,
    characteristic_length_m: float,
    *,
    carrier_gas: str = DEFAULT_CARRIER_GAS,
    molecular_diameter_m: float | None = None,
) -> float:
    if characteristic_length_m <= 0.0:
        return math.inf
    return (
        _mean_free_path_m(
            pressure_pa,
            T_K,
            molecular_diameter_m=molecular_diameter_m,
            carrier_gas=carrier_gas,
        )
        / characteristic_length_m
    )


DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA = 0.0
DEFAULT_MELT_SURFACE_RENEWAL_SOURCE = (
    "disabled:missing-species-state-dependent-melt-transfer-inputs"
)
# EVAPORATION-only class proxies: systematically composed refractory carriers borrow
# SiO sigma and epsilon/k because direct high-temperature transport data are
# absent, but each retains its formula molar mass. Premise: Chapman-Enskog
# D_AB scales as sqrt(1/M_A+1/M_B)/(sigma_AB^2*Omega_D); copying SiO's
# (sigma, epsilon/k) therefore changes only the unknown collision class while
# preserving the exactly derivable mass term. Units are Angstrom, K, g/mol.
# Sanity: SiO itself is the closest measured rail carrier; this proxy affects
# only continuum gas resistance, never K(T), P_eq, stoichiometry, or the
# condensation/deposition transport table. Outputs remain status-bearing and
# direct collision data remain acquisition targets.
_EVAPORATION_LJ_PROXY_PARAMS = {
    # t-609/t-622: keep the diagnostic monoxide upper-screen flux computable without
    # pretending direct transport authority. The SiO collision class is a
    # status-bearing continuum-resistance proxy; exact formula masses remain
    # species-specific, and the request gate never admits inventory debit.
    "FeO_association_gas": (3.374, 71.4, 71.8440),
    "NiO_gas": (3.374, 71.4, 74.6924),
    "MnO_gas": (3.374, 71.4, 70.937044),
    "CoO_gas": (3.374, 71.4, 74.932194),
    "CrO2": (3.374, 71.4, 83.9941),
    "CrO": (3.374, 71.4, 67.9951),
    "Al2": (3.374, 71.4, 53.9630770),
    "AlO": (3.374, 71.4, 42.9805385),
    "Al2O": (3.374, 71.4, 69.9620770),
    "Al2O2": (3.374, 71.4, 85.9610770),
    "Al2O3_gas": (3.374, 71.4, 101.9600770),
    "AlO2": (3.374, 71.4, 58.9795385),
    "Ca2": (3.374, 71.4, 80.1560),
    "CaO_gas": (3.374, 71.4, 56.0770),
    "TiO": (3.374, 71.4, 63.8660),
    "TiO2_gas": (3.374, 71.4, 79.8650),
    "CrO3": (3.374, 71.4, 99.9931),
    "K2": (3.374, 71.4, 78.1966),
    "K2O_gas": (3.374, 71.4, 94.1956),
    "Mg2": (3.374, 71.4, 48.6100),
    "MgO_gas": (3.374, 71.4, 40.3040),
    "Na2": (3.374, 71.4, 45.97953856),
    "Na2O_gas": (3.374, 71.4, 61.97853856),
    "P2O5_gas": (3.374, 71.4, 141.942523996),
    "Si2": (3.374, 71.4, 56.1700),
    "Si3": (3.374, 71.4, 84.2550),
    "SiO2_gas": (3.374, 71.4, 60.0830),
}


class EvaporationFluxConfigurationError(ValueError):
    """Invalid or unsupported transport inputs for authoritative flux."""


@dataclass(frozen=True)
class SeriesEvaporationFlux:
    flux_kg_s_m2: float
    alpha_intrinsic: float
    alpha_effective: float
    k_hk_kg_s_m2_pa: float
    r_interface: float
    r_gas: float
    r_melt: float
    knudsen_number: float
    gas_resistance_weight: float
    axial_stir_applied: float
    radial_stir_applied: float
    axial_stir_clamped: bool
    radial_stir_clamped: bool
    frozen_skull_stir_clamped: bool
    frozen_skull_stir_ceiling: float | str
    melt_resistance_enabled: bool
    melt_surface_renewal_base_kg_s_m2_pa: float
    melt_surface_renewal_source: str
    k_mt_kg_s_m2_pa: float
    d_ab_m2_s: float
    transport_length_m: float

    def as_diagnostic(self) -> dict[str, float | str | bool]:
        denominator = self.r_interface + self.r_gas + self.r_melt
        if math.isfinite(denominator) and denominator > 0.0:
            r_interface_fraction = self.r_interface / denominator
            r_gas_fraction = self.r_gas / denominator
            r_melt_fraction = self.r_melt / denominator
            limiting_resistance_label = max(
                (
                    ("interface", r_interface_fraction),
                    ("gas", r_gas_fraction),
                    ("melt", r_melt_fraction),
                ),
                key=lambda item: item[1],
            )[0]
        elif math.isinf(self.r_melt):
            r_interface_fraction = 0.0
            r_gas_fraction = 0.0
            r_melt_fraction = 1.0
            limiting_resistance_label = "melt"
        else:
            r_interface_fraction = 0.0
            r_gas_fraction = 0.0
            r_melt_fraction = 0.0
            limiting_resistance_label = "none"
        return {
            "flux_kg_s_m2": self.flux_kg_s_m2,
            "alpha_intrinsic": self.alpha_intrinsic,
            "alpha_effective": self.alpha_effective,
            "alpha_eff": self.alpha_effective,
            "k_hk_kg_s_m2_pa": self.k_hk_kg_s_m2_pa,
            "r_interface": self.r_interface,
            "r_gas": self.r_gas,
            "r_melt": self.r_melt,
            "R_interface_fraction": r_interface_fraction,
            "R_gas_fraction": r_gas_fraction,
            "R_melt_fraction": r_melt_fraction,
            "limiting_resistance_label": limiting_resistance_label,
            "knudsen_number": self.knudsen_number,
            "Kn": self.knudsen_number,
            "gas_resistance_weight": self.gas_resistance_weight,
            "axial_stir_applied": self.axial_stir_applied,
            "radial_stir_applied": self.radial_stir_applied,
            "axial_stir_clamped": self.axial_stir_clamped,
            "radial_stir_clamped": self.radial_stir_clamped,
            "frozen_skull_stir_clamped": self.frozen_skull_stir_clamped,
            "frozen_skull_stir_ceiling": self.frozen_skull_stir_ceiling,
            "melt_resistance_enabled": self.melt_resistance_enabled,
            "melt_surface_renewal_base_kg_s_m2_pa": (
                self.melt_surface_renewal_base_kg_s_m2_pa
            ),
            "melt_surface_renewal_source": self.melt_surface_renewal_source,
            "k_mt_kg_s_m2_pa": self.k_mt_kg_s_m2_pa,
            "d_ab_m2_s": self.d_ab_m2_s,
            "transport_length_m": self.transport_length_m,
            # HKL upper-bound authority (matrix policy i): R_m=0 until
            # species/state melt-transfer inputs exist. Not certified flux.
            "authority_class": "upper-bound",
            "authority_reason": (
                "missing-species-state-dependent-melt-transfer-inputs"
            ),
        }


def _finite_float(value, default: float) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        raw = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(raw):
        return default
    return raw


def _zero_series_evaporation_flux(
    *,
    alpha_intrinsic: float,
    k_hk_kg_s_m2_pa: float = 0.0,
    r_interface: float = math.inf,
    r_gas: float = 0.0,
    r_melt: float = 0.0,
    knudsen_number: float = math.inf,
    gas_resistance_weight: float = 0.0,
    axial_stir_applied: float = 0.0,
    radial_stir_applied: float = 1.0,
    axial_stir_clamped: bool = False,
    radial_stir_clamped: bool = False,
    frozen_skull_stir_clamped: bool = False,
    frozen_skull_stir_ceiling: float | str = "not_certified",
    melt_resistance_enabled: bool = False,
    melt_surface_renewal_base_kg_s_m2_pa: float = (
        DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
    ),
    melt_surface_renewal_source: str = DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    k_mt_kg_s_m2_pa: float = 0.0,
    d_ab_m2_s: float = 0.0,
    transport_length_m: float = 0.0,
) -> SeriesEvaporationFlux:
    return SeriesEvaporationFlux(
        flux_kg_s_m2=0.0,
        alpha_intrinsic=max(0.0, alpha_intrinsic),
        alpha_effective=0.0,
        k_hk_kg_s_m2_pa=max(0.0, k_hk_kg_s_m2_pa),
        r_interface=r_interface,
        r_gas=max(0.0, r_gas),
        r_melt=max(0.0, r_melt),
        knudsen_number=knudsen_number,
        gas_resistance_weight=max(0.0, min(1.0, gas_resistance_weight)),
        axial_stir_applied=max(0.0, axial_stir_applied),
        radial_stir_applied=max(0.0, radial_stir_applied),
        axial_stir_clamped=axial_stir_clamped,
        radial_stir_clamped=radial_stir_clamped,
        frozen_skull_stir_clamped=frozen_skull_stir_clamped,
        frozen_skull_stir_ceiling=frozen_skull_stir_ceiling,
        melt_resistance_enabled=bool(melt_resistance_enabled),
        melt_surface_renewal_base_kg_s_m2_pa=(
            max(0.0, melt_surface_renewal_base_kg_s_m2_pa)
        ),
        melt_surface_renewal_source=melt_surface_renewal_source,
        k_mt_kg_s_m2_pa=max(0.0, k_mt_kg_s_m2_pa),
        d_ab_m2_s=max(0.0, d_ab_m2_s),
        transport_length_m=max(0.0, transport_length_m),
    )


def series_evaporation_flux(
    species: str,
    P_eq_pa: float,
    P_bulk_pa: float,
    T_surface_K: float,
    molar_mass_kg_mol: float,
    alpha_i: float,
    knudsen_number: float | None = None,
    pipe_diameter_m: float = 0.12,
    overhead_pressure_pa: float | None = None,
    axial_stir_factor: float | None = 0.0,
    radial_stir_factor: float | None = 1.0,
    cold_skull_envelope: Mapping[str, float] | None = None,
    carrier_gas: str = "N2",
    *,
    T_gas_K: float | None = None,
    melt_resistance_enabled: bool = False,
    melt_surface_renewal_base_kg_s_m2_pa: float = (
        DEFAULT_MELT_SURFACE_RENEWAL_BASE_KG_S_M2_PA
    ),
    melt_surface_renewal_source: str = DEFAULT_MELT_SURFACE_RENEWAL_SOURCE,
    gas_resistance_enabled: bool = True,
    extrapolate_continuum: bool = False,
    stir_diagnostic: Mapping[str, object],
) -> SeriesEvaporationFlux:
    """Series-resistance evaporation source in kg/(m^2*s).

    The sole driving pressure is ``max(0, P_eq_pa - P_bulk_pa)``. External
    pressure regime and stirring terms only add or remove resistances; they
    never inflate the intrinsic Hertz-Knudsen alpha.
    """

    alpha_intrinsic = max(0.0, _finite_float(alpha_i, 0.0))
    P_eq = _finite_float(P_eq_pa, 0.0)
    P_bulk = _finite_float(P_bulk_pa, 0.0)
    T_surface = _finite_float(T_surface_K, 0.0)
    M_kg_mol = _finite_float(molar_mass_kg_mol, 0.0)
    diameter_m = _finite_float(pipe_diameter_m, 0.12)
    overhead_pa = _finite_float(overhead_pressure_pa, 0.0)
    effective_T_gas_K = _finite_float(T_gas_K, T_surface)

    axial_stir_applied = stir_diagnostic["axial_stir_applied"]
    radial_stir_applied = stir_diagnostic["radial_stir_applied"]
    axial_was_clamped = stir_diagnostic["axial_stir_clamped"]
    radial_was_clamped = stir_diagnostic["radial_stir_clamped"]
    frozen_skull_clamped = stir_diagnostic["frozen_skull_stir_clamped"]
    ceiling_diag = stir_diagnostic["frozen_skull_stir_ceiling"]

    if (
        P_eq <= 0.0
        or T_surface <= 0.0
        or M_kg_mol <= 0.0
        or alpha_intrinsic <= 0.0
        or diameter_m <= 0.0
    ):
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=(
                melt_surface_renewal_base_kg_s_m2_pa
            ),
            melt_surface_renewal_source=melt_surface_renewal_source,
            transport_length_m=max(0.0, diameter_m),
        )

    delta_p_pa = max(0.0, P_eq - P_bulk)
    k_hk_kg_s_m2_pa = hertz_knudsen_mass_conductance(M_kg_mol, T_surface)
    r_interface = 1.0 / (alpha_intrinsic * k_hk_kg_s_m2_pa)
    if delta_p_pa <= 0.0:
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
            r_interface=r_interface,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=(
                melt_surface_renewal_base_kg_s_m2_pa
            ),
            melt_surface_renewal_source=melt_surface_renewal_source,
            transport_length_m=max(0.0, diameter_m),
        )

    base_k_melt = _finite_float(melt_surface_renewal_base_kg_s_m2_pa, 0.0)
    if melt_resistance_enabled:
        # Premise: liquid transport is J=M_i*k_L,i*(C_b-C_s). Linearising
        # p_eq(C) with H_i=dp_eq/dC_i gives the pressure-basis conductance
        # k_m,i=M_i*k_L,i/H_i [kg Pa^-1 m^-2 s^-1 = s/m], hence
        # R_m,i=H_i/(M_i*k_L,i) [m/s]. k_L,i itself requires a species- and
        # composition-dependent D_i plus measured melt renewal/geometry.
        # None of D_i, H_i, C_i, or a physical renewal rate exists in this
        # request, so any positive universal coefficient (formerly 1e-4 s/m)
        # is dimensionally shaped but ungrounded. Refuse it; R_m=0 below is
        # explicitly the reconstructible HKL upper bound, consistent with
        # Hashimoto/Fedkin vacuum free-evaporation being interface-controlled.
        raise EvaporationFluxConfigurationError(
            "authoritative melt resistance requires species- and state-specific "
            "D_i, k_L,i, and dp_eq/dC_i; universal pressure conductance disabled"
        )

    if knudsen_number is None:
        knudsen = _knudsen_number(
            overhead_pa,
            max(effective_T_gas_K, 1.0),
            diameter_m,
            carrier_gas=str(carrier_gas or DEFAULT_CARRIER_GAS),
        )
    else:
        try:
            knudsen = float(knudsen_number)
        except (TypeError, ValueError):
            knudsen = math.inf

    if math.isnan(knudsen) or knudsen < 0.0:
        raise EvaporationFluxConfigurationError(
            "knudsen_number must be finite non-negative or positive infinity"
        )

    gas_weight = 0.0
    if gas_resistance_enabled:
        if knudsen < VISCOUS_KNUDSEN_MAX or (
            extrapolate_continuum
            and VISCOUS_KNUDSEN_MAX <= knudsen < FREE_MOLECULAR_KNUDSEN_MIN
        ):
            gas_weight = 1.0
        # Premise: an open free-molecular vacuum has no stagnant carrier-gas
        # film. Therefore R_g=0 locally. Continuum branch: k_g=M*Sh*D/(L*R*T)
        # [s/m], R_g=1/k_g [m/s], only for Kn < VISCOUS_KNUDSEN_MAX (0.01).
        # The provider and its Pareto replay may continue this same finite
        # continuum expression into transitional Kn, carrying extrapolated
        # authority. This is not a transitional conductance fit. Keep the
        # existing free-molecular HKL branch and unflagged diagnostic sweeps.

    r_gas = 0.0
    k_mt_kg_s_m2_pa = 0.0
    d_ab_m2_s = 0.0
    if gas_weight > 0.0:
        sherwood_eff = pipe_wall_sherwood(radial_stir_applied)
        if overhead_pa <= 0.0:
            raise EvaporationFluxConfigurationError(
                "gas resistance requires positive overhead_pressure_pa"
            )
        d_ab_m2_s = _chapman_enskog_d_ab_m2_s(
            species,
            max(effective_T_gas_K, 1.0),
            overhead_pa,
            carrier=str(carrier_gas or DEFAULT_CARRIER_GAS),
            species_params=_EVAPORATION_LJ_PROXY_PARAMS.get(species),
        )
        if not math.isfinite(d_ab_m2_s) or d_ab_m2_s <= 0.0:
            raise EvaporationFluxConfigurationError(
                "missing Chapman-Enskog transport parameters for "
                f"species={species!r} carrier_gas={carrier_gas!r}"
            )
        k_mt_mol_s_m2_pa = (
            film_pressure_conductance(sherwood_eff, d_ab_m2_s, diameter_m,
                                      max(effective_T_gas_K, 1.0))
        )
        k_mt_kg_s_m2_pa = k_mt_mol_s_m2_pa * M_kg_mol
        if not math.isfinite(k_mt_kg_s_m2_pa) or k_mt_kg_s_m2_pa <= 0.0:
            return _zero_series_evaporation_flux(
                alpha_intrinsic=alpha_intrinsic,
                k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
                r_interface=r_interface,
                knudsen_number=knudsen,
                gas_resistance_weight=gas_weight,
                axial_stir_applied=axial_stir_applied,
                radial_stir_applied=radial_stir_applied,
                axial_stir_clamped=axial_was_clamped,
                radial_stir_clamped=radial_was_clamped,
                frozen_skull_stir_clamped=frozen_skull_clamped,
                frozen_skull_stir_ceiling=ceiling_diag,
                melt_resistance_enabled=melt_resistance_enabled,
                melt_surface_renewal_base_kg_s_m2_pa=(
                    melt_surface_renewal_base_kg_s_m2_pa
                ),
                melt_surface_renewal_source=melt_surface_renewal_source,
                d_ab_m2_s=d_ab_m2_s,
                transport_length_m=diameter_m,
            )
        r_gas = gas_weight / k_mt_kg_s_m2_pa

    r_melt = 0.0

    denominator = r_interface + r_gas + r_melt
    if not math.isfinite(denominator) or denominator <= 0.0:
        return _zero_series_evaporation_flux(
            alpha_intrinsic=alpha_intrinsic,
            k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
            r_interface=r_interface,
            r_gas=r_gas,
            r_melt=r_melt,
            knudsen_number=knudsen,
            gas_resistance_weight=gas_weight,
            axial_stir_applied=axial_stir_applied,
            radial_stir_applied=radial_stir_applied,
            axial_stir_clamped=axial_was_clamped,
            radial_stir_clamped=radial_was_clamped,
            frozen_skull_stir_clamped=frozen_skull_clamped,
            frozen_skull_stir_ceiling=ceiling_diag,
            melt_resistance_enabled=melt_resistance_enabled,
            melt_surface_renewal_base_kg_s_m2_pa=base_k_melt,
            melt_surface_renewal_source=melt_surface_renewal_source,
            k_mt_kg_s_m2_pa=k_mt_kg_s_m2_pa,
            d_ab_m2_s=d_ab_m2_s,
            transport_length_m=diameter_m,
        )

    k_total_kg_s_m2_pa = 1.0 / denominator
    flux_kg_s_m2 = delta_p_pa * k_total_kg_s_m2_pa
    alpha_effective = k_total_kg_s_m2_pa / k_hk_kg_s_m2_pa
    if not math.isfinite(flux_kg_s_m2) or not math.isfinite(alpha_effective):
        flux_kg_s_m2 = 0.0
        alpha_effective = 0.0

    return SeriesEvaporationFlux(
        flux_kg_s_m2=max(0.0, flux_kg_s_m2),
        alpha_intrinsic=alpha_intrinsic,
        alpha_effective=max(0.0, min(alpha_intrinsic, alpha_effective)),
        k_hk_kg_s_m2_pa=k_hk_kg_s_m2_pa,
        r_interface=r_interface,
        r_gas=r_gas,
        r_melt=r_melt,
        knudsen_number=knudsen,
        gas_resistance_weight=gas_weight,
        axial_stir_applied=axial_stir_applied,
        radial_stir_applied=radial_stir_applied,
        axial_stir_clamped=axial_was_clamped,
        radial_stir_clamped=radial_was_clamped,
        frozen_skull_stir_clamped=frozen_skull_clamped,
        frozen_skull_stir_ceiling=ceiling_diag,
        melt_resistance_enabled=bool(melt_resistance_enabled),
        melt_surface_renewal_base_kg_s_m2_pa=base_k_melt,
        melt_surface_renewal_source=melt_surface_renewal_source,
        k_mt_kg_s_m2_pa=k_mt_kg_s_m2_pa,
        d_ab_m2_s=d_ab_m2_s,
        transport_length_m=diameter_m,
    )
