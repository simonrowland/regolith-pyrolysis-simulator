"""Identity + identity_equal (v2.1 §Exact equality and observable profiles).

Gas-constant policy (b-511)
---------------------------
Two different jobs, two different R values. Do not unify them.

1. **Transcription of a source's own printed column** uses the constant
   that source's era and publisher used. That is a check against the
   page, not a modern-physics evaluation. Per-source constants live on
   the generator that reads that source, never here:

   - NIST-JANAF 4th ed. (Chase 1998, Monograph 9, p. 10 §3.4 table):
     ``JANAF_R_J_PER_MOL_K = 8.31441`` J/(mol·K). The printed
     "Fundamental constants" table lists the molar gas constant as
     ``8.314 41(26)``, Cohen & Taylor 1973 / CODATA 1973. A new JANAF
     table's log Kf identity uses that value.
   - USGS B1544 (Hemingway, Haas & Robinson 1982):
     ``B1544_R_J_PER_MOL_K = 8.3143`` J/(mol·K). B1544 prints no R;
     the value is inherited from the parent compilation USGS B1452
     Table 1 (PDF p. 9 / printed p. 3).

   A new source's transcription constant is declared next to that
   source's generator (or loader), with the page locus, and is passed
   explicitly into ``log10K_from_delta_fG_kJ_mol``. It does not belong
   in this module.

2. **Cross-source physics** — identity between observations from
   different sources — uses CODATA / SI 2019 ``R = N_A k_B`` from
   ``simulator.physical_constants.GAS_CONSTANT``. That is
   ``R_J_PER_MOL_K`` below. Engine modules that are not doing a
   page-transcription check should import that leaf; ``simulator.state``
   still carries a six-significant-figure truncation and must not be
   silently unified (it moves pinned flux heads).

Unifying (1) onto (2) is a defect: B1544 corundum at 298.15 K
reproduces printed log Kf 277.203 only with R = 8.3143; CODATA
gives 277.1968 and does not round to the printed value.

Compared axes are quantity-class profiles, never "every field present on
either side". Annotations, engine versions, fO2 channel and buffer labels
never enter equality. fO2 *value* is identity; channel is provenance.

Ambiguity resolutions:
- Identity stores every profile axis as ``State | None``. ``None`` is
  absent-from-storage; equality treats a missing *required* axis as
  ``unknown`` (never equals another hole). A VALUE on an axis the profile
  forbids is ``invalid_identity``, not an override.
- ``p_sat`` vs ``p_reference`` vs ``p_partial`` are distinct quantities.
  Pure ``p_sat`` reservoir must be condensed of the same formula as the
  gas species. ``p_reference`` is unit-endmember Pref (no fictional pot
  composition). ``p_partial`` carries the actual mixture pot.
- Formation quantities require ``reaction`` + ``formation_elements``
  (element → Species with the elemental reference phase).
- Standard pressure is compared only where the quantity needs it. 1 bar
  and 100000 Pa are equal *after* ``bar_to_pa``; they are not guessed
  from a bare ``1``. 1 atm (101325 Pa) stays distinct from 0.1 MPa.
- Decimal comparison is numeric (``Decimal.__eq__``), so ``1e5`` equals
  ``100000``. 1643 K does not equal 1643.15 K; no rounding band.
- ``canonicalize_with_recorded_conversion`` applies only the unit helpers
  below (calorie, bar/atm, C→K, per-mol-O2 rescaling). It does not invent
  a ΔfG(p°) rewrite without a recorded Derivation on an Observation.
- No physical-alias table: FeOT vs FeO+Fe2O3 is a mismatch until a
  recorded conversion exists (chunk 2/3). Crystal-system synonyms are
  annotations, not keys.
- ``henrian_solid`` / ``henrian_liquid`` are convention tokens *and*
  ``reference_state.endmember.phase`` is compared, matching M10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

from simulator.battery.enums import (
    CONDENSED_PHASES,
    EXTENSIVE_YIELD_QUANTITIES,
    FORMATION_QUANTITIES,
    KINETIC_YIELD_QUANTITIES,
    MELT_ACTIVITY_QUANTITIES,
    PURE_STANDARD_THERMO,
    VAPORIZATION_ENTHALPIES,
    IdentityEqualKind,
    PerBasis,
    Phase,
    Quantity,
    StateTag,
)
from simulator.battery.records import (
    Composition,
    Reaction,
    Species,
    StandardState,
    State,
    as_decimal,
    as_fraction,
    phase_token,
)
from simulator.physical_constants import (
    CELSIUS_TO_KELVIN_OFFSET,
    GAS_CONSTANT,
    PA_PER_BAR,
    STANDARD_ATMOSPHERE_PA,
)

# ---------------------------------------------------------------------------
# Unit / standard-state conversion helpers
# Each helper states premise → algebra → unit check → sanity.
# ---------------------------------------------------------------------------

# Premise: the thermochemical calorie is defined as 4.184 J exactly
# (CIPM / ISO 31-4). USBM B689 prints ΔfG as kcal/mol using that calorie,
# not the I.T. calorie (4.1868 J).
# Algebra: 1 kcal_th/mol = 4.184 kJ/mol.
# Unit check: kcal/mol × 4.184 kJ/kcal = kJ/mol.
# Sanity: B689 AgS(g) 298.15 K ΔfG = 80.700 kcal/mol → 337.6488 kJ/mol;
# recomputed log10 Kf = −337.6488 / (R T ln 10) = −59.153, matching the
# printed −59.154 to the 0.001 table grain.
THERMOCHEMICAL_CALORIE_J = Decimal("4.184")

# Premise: 1 bar is defined as 10^5 Pa exactly (IUPAC). JANAF p° = 0.1 MPa
# = 1 bar = 100000 Pa. 1 standard atmosphere is defined as 101325 Pa
# exactly (NASA-CEA condensed records; B689). These are different p°.
# Algebra: P_Pa = P_bar × 100000; P_Pa = P_atm × 101325.
# Unit check: bar × (Pa/bar) = Pa.
# Sanity: 1 bar = 100000 Pa; 1 atm = 101325 Pa; they are not equal.
# A 1 atm vs 1 bar slip in ΔfG is Δn_g RT ln(1.01325) ≈ 0.033 kJ/mol at
# 298 K per mol of gas — inside the 1 kJ/mol independent band, noted, not
# silently corrected by identity_equal.
PA_PER_BAR_DEC = Decimal(str(int(PA_PER_BAR))) if PA_PER_BAR == 1e5 else Decimal(str(PA_PER_BAR))
PA_PER_ATM_DEC = Decimal(str(int(STANDARD_ATMOSPHERE_PA)))
CELSIUS_OFFSET_DEC = Decimal(str(CELSIUS_TO_KELVIN_OFFSET))

# R as used for ΔfG ↔ log10 Kf in *cross-source* identity. CODATA / SI 2019
# R = N_A k_B = 8.314462618… J/(mol·K) from physical_constants.GAS_CONSTANT.
# Source-era transcription constants (JANAF 8.31441, B1544 8.3143) stay on
# their generators; see this module's Gas-constant policy docstring.
# Algebra: log10 Kf = −ΔfG / (R T ln 10), ΔfG in J/mol, T in K.
# Unit check: J/mol / (J/(mol·K) · K) is dimensionless.
# Sanity: ΔfG = 0 → log10 Kf = 0; at 298.15 K, 1 kJ/mol ≈ 0.1752 dex.
LN10 = Decimal(str(math.log(10.0)))
R_J_PER_MOL_K = Decimal(str(GAS_CONSTANT))
R_KJ_PER_MOL_K = R_J_PER_MOL_K / Decimal("1000")

# Per-mol-O2 rescaling.
# Premise: Ellingham / mol-O2 tables report ΔG for the reaction written on
# one mole of O2. A per-mol-species value scales by 1 / |ν_O2|.
# Algebra: ΔG_per_mol_O2 = ΔG_per_mol_species × (1 / |ν_O2|).
# For 2 Ni + O2 → 2 NiO, |ν_O2| = 1 on the mol-O2 write-up and
# |ν_NiO| = 2, so ΔG[kJ/mol O2] = 2 × ΔGf[kJ/mol NiO]. Combined with the
# thermochemical calorie: ΔG[kJ/mol O2] = 2 × 4.184 × ΔGf[kcal/mol NiO].
# Unit check: (kJ/mol-species) × (mol-species / mol-O2) = kJ/mol-O2.
# Sanity: NiO source ΔGf in kcal/mol NiO times 2 × 4.184 recovers the
# catalog mol-O2 rail (see ellingham_thermo.py Ni header).


def kcal_th_to_kJ_per_mol(kcal_per_mol: object) -> Decimal:
    return as_decimal(kcal_per_mol) * THERMOCHEMICAL_CALORIE_J


def bar_to_pa(pressure_bar: object) -> Decimal:
    return as_decimal(pressure_bar) * PA_PER_BAR_DEC


def atm_to_pa(pressure_atm: object) -> Decimal:
    return as_decimal(pressure_atm) * PA_PER_ATM_DEC


def celsius_to_kelvin(temperature_C: object) -> Decimal:
    return as_decimal(temperature_C) + CELSIUS_OFFSET_DEC


def log10K_from_delta_fG_kJ_mol(
    delta_fG_kJ_mol: object,
    T_K: object,
    *,
    gas_constant_J_per_mol_K: object = R_J_PER_MOL_K,
) -> Decimal:
    """log10 Kf = −ΔfG / (R T ln 10) with ΔfG in kJ/mol, T in K."""

    t = as_decimal(T_K)
    if t <= 0:
        raise ValueError("T_K must be positive")
    r_kJ_per_mol_K = as_decimal(gas_constant_J_per_mol_K) / Decimal("1000")
    return -as_decimal(delta_fG_kJ_mol) / (r_kJ_per_mol_K * t * LN10)


def standard_pressure_delta_g_kJ_per_mol(
    delta_n_gas: object,
    T_K: object,
    p_from_Pa: object,
    p_to_Pa: object,
) -> Decimal:
    """ΔfG correction for a change of p°: Δn_g RT ln(p_to / p_from).

    Premise: Kp is defined at a stated p°. Changing p° at fixed T shifts
    ΔfG by the gas-count term only.
    Algebra: Δ(ΔG) = Δn_g R T ln(P2/P1); R in J/(mol·K), result / 1000 → kJ/mol.
    Unit check: (1) · (J/(mol·K)) · K = J/mol.
    Sanity: Δn_g = 1, 298.15 K, 101325/100000 → ≈ 0.033 kJ/mol.
    identity_equal does not apply this unless a Derivation records it.
    """

    p1 = as_decimal(p_from_Pa)
    p2 = as_decimal(p_to_Pa)
    if p1 <= 0 or p2 <= 0:
        raise ValueError("standard pressures must be positive")
    ratio = p2 / p1
    ln_ratio = Decimal(str(math.log(float(ratio))))
    joule = as_decimal(delta_n_gas) * R_J_PER_MOL_K * as_decimal(T_K) * ln_ratio
    return joule / Decimal("1000")


def o2_coefficient(reaction: Reaction) -> Fraction:
    """Signed O2 stoichiometric coefficient (products positive)."""

    total = Fraction(0)
    for term in reaction.terms:
        if term.species.formula == "O2":
            total += term.coefficient
    return total


def product_coefficient(reaction: Reaction) -> Fraction:
    """Positive coefficient of the single non-O2 product."""

    total = Fraction(0)
    count = 0
    for term in reaction.terms:
        if term.species.formula == "O2":
            continue
        if term.coefficient > 0:
            total += term.coefficient
            count += 1
    if count != 1:
        raise ValueError("rescale requires exactly one non-O2 product species")
    return total


def rescale_energy_per_basis(
    value_kJ: object,
    from_per: PerBasis,
    to_per: PerBasis,
    reaction: Reaction,
) -> Decimal:
    """Rescale a molar energy between mol_species and mol_O2.

    ΔG_per_mol_O2 = ΔG_per_mol_species × |ν_product| / |ν_O2|.
    Does not invent a conversion for other ``per`` tokens.
    """

    if from_per is to_per:
        return as_decimal(value_kJ)
    nu_o2 = abs(o2_coefficient(reaction))
    if nu_o2 == 0:
        raise ValueError("reaction has no O2 term; cannot rescale to/from mol_O2")
    nu_product = abs(product_coefficient(reaction))
    factor = as_decimal(nu_product) / as_decimal(nu_o2)
    if from_per is PerBasis.MOL_SPECIES and to_per is PerBasis.MOL_O2:
        return as_decimal(value_kJ) * factor
    if from_per is PerBasis.MOL_O2 and to_per is PerBasis.MOL_SPECIES:
        return as_decimal(value_kJ) / factor
    raise ValueError(f"no recorded conversion {from_per} → {to_per}")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

_AXIS_NAMES = (
    "subtype",
    "per",
    "temperature_K",
    "standard_pressure_Pa",
    "reaction",
    "formation_elements",
    "reference_state",
    "reservoir",
    "composition",
    "fO2_Pa",
    "total_pressure_Pa",
    "sweep_gas",
    "exposure",
    "sample_mass_kg",
    "wall",
)


@dataclass(frozen=True)
class SweepIdentity:
    species: str
    flow_sccm: State[Decimal]
    partial_pressure_Pa: State[Decimal]


@dataclass(frozen=True)
class Exposure:
    area_m2: State[Decimal]
    duration_s: State[Decimal]
    schedule: State[str] | None = None


@dataclass(frozen=True)
class WallIdentity:
    temperature_K: State[Decimal]
    material: State[str]
    area_m2: State[Decimal] | None = None
    location: State[str] | None = None


@dataclass(frozen=True)
class QuantityProfile:
    required: frozenset[str]
    permitted_not_applicable: frozenset[str]


@dataclass(frozen=True)
class Identity:
    quantity: State[Quantity] | Quantity
    species: Species
    subtype: State[str] | None = None
    per: State[PerBasis] | None = None
    temperature_K: State[Decimal] | None = None
    standard_pressure_Pa: State[Decimal] | None = None
    reaction: State[Reaction] | None = None
    formation_elements: State[tuple[tuple[str, Species], ...]] | None = None
    reference_state: State[StandardState] | None = None
    reservoir: State[Species] | None = None
    composition: State[Composition] | None = None
    fO2_Pa: State[Decimal] | None = None
    total_pressure_Pa: State[Decimal] | None = None
    sweep_gas: State[SweepIdentity] | None = None
    exposure: State[Exposure] | None = None
    sample_mass_kg: State[Decimal] | None = None
    wall: State[WallIdentity] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.quantity, Quantity):
            object.__setattr__(self, "quantity", State.of(self.quantity))
        elif not isinstance(self.quantity, State):
            raise ValueError(
                f"Identity.quantity must be a Quantity token or State[Quantity], not {self.quantity!r}"
            )
        elif self.quantity.is_value and self.quantity.value not in Quantity:
            raise ValueError(
                f"Identity.quantity must be a closed Quantity token, not {self.quantity.value!r}"
            )
        if self.temperature_K is not None and self.temperature_K.is_value:
            object.__setattr__(
                self,
                "temperature_K",
                State.of(as_decimal(self.temperature_K.value)),
            )
        if (
            self.standard_pressure_Pa is not None
            and self.standard_pressure_Pa.is_value
        ):
            object.__setattr__(
                self,
                "standard_pressure_Pa",
                State.of(as_decimal(self.standard_pressure_Pa.value)),
            )
        if self.fO2_Pa is not None and self.fO2_Pa.is_value:
            object.__setattr__(self, "fO2_Pa", State.of(as_decimal(self.fO2_Pa.value)))
        if self.total_pressure_Pa is not None and self.total_pressure_Pa.is_value:
            object.__setattr__(
                self,
                "total_pressure_Pa",
                State.of(as_decimal(self.total_pressure_Pa.value)),
            )
        if self.sample_mass_kg is not None and self.sample_mass_kg.is_value:
            object.__setattr__(
                self,
                "sample_mass_kg",
                State.of(as_decimal(self.sample_mass_kg.value)),
            )


@dataclass(frozen=True)
class IdentityEqualOutcome:
    kind: IdentityEqualKind
    fields: tuple[str, ...] = ()
    detail: str = ""

    @property
    def equal(self) -> bool:
        return self.kind is IdentityEqualKind.EQUAL


def profile_for(identity: Identity) -> QuantityProfile:
    """Closed compared-axis profile for the declared quantity.

    Applicability is determined by the quantity (and reaction where the
    spec says so), never by whether a field happens to be filled.
    """

    q = quantity_token(identity)
    if q is None:
        return QuantityProfile(required=frozenset(), permitted_not_applicable=frozenset())
    required: set[str] = set()
    permitted_na: set[str] = set()

    def req(*names: str) -> None:
        required.update(names)

    def na(*names: str) -> None:
        permitted_na.update(names)

    # Always compared: quantity + species (+ polymorph via species).
    if q in FORMATION_QUANTITIES:
        req("per", "temperature_K", "standard_pressure_Pa", "reaction", "formation_elements")
        na(
            "composition",
            "fO2_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "reservoir",
            "reference_state",
            "total_pressure_Pa",
            "subtype",
        )
    elif q in PURE_STANDARD_THERMO:
        req("per", "temperature_K", "standard_pressure_Pa")
        if q is Quantity.H_MINUS_H298:
            req("subtype")  # reference T lives in subtype
        else:
            na("subtype")
        na(
            "composition",
            "fO2_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "reservoir",
            "reference_state",
            "reaction",
            "formation_elements",
            "total_pressure_Pa",
        )
    elif q is Quantity.PARTIAL_MOLAR_ENTHALPY:
        req("per", "temperature_K", "standard_pressure_Pa", "composition", "total_pressure_Pa", "reference_state")
        na("sweep_gas", "exposure", "sample_mass_kg", "wall", "reservoir", "formation_elements", "subtype")
        reaction_state = identity.reaction
        if reaction_state is not None and reaction_state.is_unknown:
            req("fO2_Pa")
            req("reaction")
        elif _reaction_includes_redox(identity):
            req("fO2_Pa")
            req("reaction")
        else:
            na("fO2_Pa", "reaction")
    elif q in VAPORIZATION_ENTHALPIES:
        req("per", "temperature_K", "standard_pressure_Pa", "reaction", "subtype", "reservoir")
        na(
            "composition",
            "fO2_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "formation_elements",
            "reference_state",
            "total_pressure_Pa",
        )
    elif q is Quantity.P_SAT:
        req("temperature_K", "reservoir")
        na(
            "per",
            "standard_pressure_Pa",
            "composition",
            "fO2_Pa",
            "total_pressure_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "reaction",
            "formation_elements",
            "reference_state",
            "subtype",
        )
    elif q is Quantity.P_REFERENCE:
        # Unit endmember Pref: no fictional pot composition.
        req(
            "temperature_K",
            "reservoir",
            "reaction",
            "reference_state",
            "fO2_Pa",
        )
        na(
            "composition",
            "per",
            "standard_pressure_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "formation_elements",
            "subtype",
            "total_pressure_Pa",
        )
    elif q is Quantity.P_PARTIAL:
        req(
            "temperature_K",
            "reservoir",
            "reaction",
            "reference_state",
            "fO2_Pa",
            "total_pressure_Pa",
            "composition",
        )
        na(
            "per",
            "standard_pressure_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "formation_elements",
            "subtype",
        )
    elif q in MELT_ACTIVITY_QUANTITIES:
        req(
            "temperature_K",
            "per",
            "reference_state",
            "composition",
            "fO2_Pa",
            "total_pressure_Pa",
        )
        if q is Quantity.INTERACTION_PARAMETER:
            req("subtype")
        else:
            na("subtype")
        na(
            "standard_pressure_Pa",
            "reaction",
            "formation_elements",
            "reservoir",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
        )
    elif q in KINETIC_YIELD_QUANTITIES:
        req(
            "temperature_K",
            "per",
            "reservoir",
            "composition",
            "fO2_Pa",
            "total_pressure_Pa",
            "sweep_gas",
            "exposure",
        )
        if q is Quantity.EVAPORATION_COEFFICIENT_ALPHA:
            req("subtype")
        else:
            na("subtype")
        if q is Quantity.WALL_DEPOSIT_MASS:
            req("wall")
        else:
            na("wall")
        if q in EXTENSIVE_YIELD_QUANTITIES and q is not Quantity.EVAPORATION_COEFFICIENT_ALPHA:
            req("sample_mass_kg")
        else:
            na("sample_mass_kg")
        na("standard_pressure_Pa", "reaction", "formation_elements", "reference_state")
        if q is Quantity.EVAPORATION_RATE:
            # flux per area may omit area from equality only after recorded
            # normalization; sample mass still required for extensive rates.
            pass
    elif q is Quantity.TRANSITION_TEMPERATURE:
        req("subtype", "total_pressure_Pa")
        # no duplicate T coordinate
        na("temperature_K")
        na(
            "per",
            "standard_pressure_Pa",
            "reaction",
            "formation_elements",
            "reference_state",
            "reservoir",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
        )
        subtype = ""
        if identity.subtype is not None and identity.subtype.is_value and identity.subtype.value is not None:
            subtype = str(identity.subtype.value)
        if subtype in {"solidus", "liquidus", "glass"}:
            req("composition")
        else:
            na("composition")
        na("fO2_Pa")
    elif q in {
        Quantity.VISCOSITY,
        Quantity.DENSITY,
        Quantity.ELECTRICAL_CONDUCTIVITY,
        Quantity.LIQUIDUS_COMPOSITION,
        Quantity.FE3_FE2_RATIO,
    }:
        req("temperature_K", "total_pressure_Pa", "composition")
        if q is Quantity.FE3_FE2_RATIO:
            req("fO2_Pa")
        else:
            na("fO2_Pa")
        na(
            "per",
            "standard_pressure_Pa",
            "reaction",
            "formation_elements",
            "reference_state",
            "reservoir",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "subtype",
        )
    elif q in {Quantity.ION_INTENSITY, Quantity.ION_INTENSITY_RATIO, Quantity.ISOTOPE_DELTA}:
        req("temperature_K", "subtype")
        na(
            "per",
            "standard_pressure_Pa",
            "reaction",
            "formation_elements",
            "reference_state",
            "composition",
            "fO2_Pa",
            "total_pressure_Pa",
            "sweep_gas",
            "exposure",
            "sample_mass_kg",
            "wall",
            "reservoir",
        )
    else:  # pragma: no cover — closed enum
        req("temperature_K")

    # Axes neither required nor permitted-n/a are forbidden-as-value.
    return QuantityProfile(
        required=frozenset(required),
        permitted_not_applicable=frozenset(permitted_na),
    )


def _reaction_includes_redox(identity: Identity) -> bool:
    reaction_state = identity.reaction
    if reaction_state is None or not reaction_state.is_value or reaction_state.value is None:
        return False
    return any(term.species.formula == "O2" for term in reaction_state.value.terms)


def quantity_token(identity: Identity) -> Quantity | None:
    """Closed Quantity token when the axis is a value; None for unknown / n/a."""

    q = identity.quantity
    if isinstance(q, Quantity):
        return q
    if isinstance(q, State) and q.is_value:
        return q.value
    return None


def _axis_state(identity: Identity, name: str) -> State[Any] | None:
    return getattr(identity, name)


def _phase_state(species: Species) -> State[Phase]:
    phase = species.phase
    if isinstance(phase, State):
        return phase
    return State.of(phase)


def _charge_state(species: Species) -> State[int]:
    charge = species.charge
    if isinstance(charge, State):
        return charge
    if isinstance(charge, int) and not isinstance(charge, bool):
        return State.of(charge)
    return State.of(0)


def _species_equal(a: Species, b: Species) -> IdentityEqualOutcome:
    fields: list[str] = []
    unknown: list[str] = []
    if a.formula != b.formula:
        fields.append("species.formula")
    phase_cmp = _state_compare(
        "species.phase", _phase_state(a), _phase_state(b), required=True
    )
    if phase_cmp.kind is IdentityEqualKind.IDENTITY_UNKNOWN:
        unknown.append("species.phase")
    elif phase_cmp.kind is IdentityEqualKind.IDENTITY_MISMATCH:
        fields.append("species.phase")
    elif phase_cmp.kind is IdentityEqualKind.INVALID_IDENTITY:
        return phase_cmp
    charge_cmp = _state_compare(
        "species.charge", _charge_state(a), _charge_state(b), required=True
    )
    if charge_cmp.kind is IdentityEqualKind.IDENTITY_UNKNOWN:
        unknown.append("species.charge")
    elif charge_cmp.kind is IdentityEqualKind.IDENTITY_MISMATCH:
        fields.append("species.charge")
    elif charge_cmp.kind is IdentityEqualKind.INVALID_IDENTITY:
        return charge_cmp
    a_token = phase_token(a)
    poly = _state_compare(
        "species.polymorph",
        a.polymorph,
        b.polymorph,
        required=a_token is Phase.CR,
    )
    if a_token is Phase.CR:
        if poly.kind is not IdentityEqualKind.EQUAL:
            return poly
        # Crystal phase requires a physically resolved polymorph value.
        if a.polymorph is None or not a.polymorph.is_value:
            return IdentityEqualOutcome(
                IdentityEqualKind.IDENTITY_UNKNOWN,
                ("species.polymorph",),
                "crystal phase requires a resolved polymorph",
            )
    elif a_token is not None and poly.kind is IdentityEqualKind.INVALID_IDENTITY:
        return poly
    if fields:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, tuple(fields))
    if unknown:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_UNKNOWN, tuple(unknown))
    return IdentityEqualOutcome(IdentityEqualKind.EQUAL)


def _state_compare(
    name: str,
    left: State[Any] | None,
    right: State[Any] | None,
    *,
    required: bool,
    permitted_na: bool = False,
) -> IdentityEqualOutcome:
    """v2.1 state_truth_table on one axis."""

    def as_state(item: State[Any] | None) -> State[Any]:
        if item is not None:
            return item
        if required:
            return State.unknown(f"missing required axis {name}")
        return State.not_applicable(f"axis {name} not stored")

    a = as_state(left)
    b = as_state(right)
    if a.tag is StateTag.UNKNOWN or b.tag is StateTag.UNKNOWN:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_UNKNOWN, (name,))
    if a.tag is StateTag.VALUE and b.tag is StateTag.NOT_APPLICABLE:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, (name,))
    if a.tag is StateTag.NOT_APPLICABLE and b.tag is StateTag.VALUE:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, (name,))
    if a.tag is StateTag.NOT_APPLICABLE and b.tag is StateTag.NOT_APPLICABLE:
        if permitted_na or not required:
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            (name,),
            f"{name} is required; not_applicable is not permitted",
        )
    # value vs value — preserve nested three-valued outcomes
    return _values_compare(name, a.value, b.value)


def _mismatch(name: str) -> IdentityEqualOutcome:
    return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, (name,))


def _values_compare(name: str, left: Any, right: Any) -> IdentityEqualOutcome:
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        if as_decimal(left) == as_decimal(right):
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        return _mismatch(name)
    if isinstance(left, Fraction) or isinstance(right, Fraction):
        if as_fraction(left) == as_fraction(right):
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        return _mismatch(name)
    if isinstance(left, Species) and isinstance(right, Species):
        return _species_equal(left, right)
    if isinstance(left, StandardState) and isinstance(right, StandardState):
        if left.convention is not right.convention:
            return _mismatch(name)
        if left.component_basis != right.component_basis:
            return _mismatch(name)
        if left.reference_pressure_bar != right.reference_pressure_bar:
            return _mismatch(name)
        nested = _species_equal(left.endmember, right.endmember)
        if nested.kind is IdentityEqualKind.EQUAL:
            return nested
        if nested.kind is IdentityEqualKind.IDENTITY_MISMATCH:
            return _mismatch(name)
        return IdentityEqualOutcome(nested.kind, nested.fields or (name,), nested.detail)
    if isinstance(left, Composition) and isinstance(right, Composition):
        if (
            left.basis == right.basis
            and left.amount_basis is right.amount_basis
            and left.components == right.components
        ):
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        return _mismatch(name)
    if isinstance(left, Reaction) and isinstance(right, Reaction):
        if len(left.terms) != len(right.terms):
            return _mismatch(name)
        for a, b in zip(left.terms, right.terms, strict=True):
            if a.coefficient != b.coefficient:
                return _mismatch(name)
            nested = _species_equal(a.species, b.species)
            if nested.kind is IdentityEqualKind.IDENTITY_MISMATCH:
                return _mismatch(name)
            if nested.kind is not IdentityEqualKind.EQUAL:
                return IdentityEqualOutcome(nested.kind, nested.fields or (name,), nested.detail)
        return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
    if isinstance(left, tuple) and isinstance(right, tuple):
        if len(left) != len(right):
            return _mismatch(name)
        if left and isinstance(left[0], tuple) and len(left[0]) == 2:
            if [k for k, _ in left] != [k for k, _ in right]:
                return _mismatch(name)
            for (_ka, sa), (_kb, sb) in zip(left, right, strict=True):
                if isinstance(sa, Species) and isinstance(sb, Species):
                    nested = _species_equal(sa, sb)
                    if nested.kind is IdentityEqualKind.IDENTITY_MISMATCH:
                        return _mismatch(name)
                    if nested.kind is not IdentityEqualKind.EQUAL:
                        return IdentityEqualOutcome(
                            nested.kind, nested.fields or (name,), nested.detail
                        )
                elif sa != sb:
                    return _mismatch(name)
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        if left == right:
            return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
        return _mismatch(name)
    if isinstance(left, SweepIdentity) and isinstance(right, SweepIdentity):
        if left.species != right.species:
            return _mismatch(name)
        flow = _state_compare("sweep_gas.flow_sccm", left.flow_sccm, right.flow_sccm, required=left.species != "none", permitted_na=left.species == "none")
        if flow.kind is not IdentityEqualKind.EQUAL:
            return flow
        pp = _state_compare(
            "sweep_gas.partial_pressure_Pa",
            left.partial_pressure_Pa,
            right.partial_pressure_Pa,
            required=left.species != "none",
            permitted_na=left.species == "none",
        )
        return pp
    if isinstance(left, Exposure) and isinstance(right, Exposure):
        for field_name in ("area_m2", "duration_s", "schedule"):
            outcome = _state_compare(
                f"exposure.{field_name}",
                getattr(left, field_name),
                getattr(right, field_name),
                required=field_name != "schedule",
                permitted_na=field_name == "schedule",
            )
            if outcome.kind is not IdentityEqualKind.EQUAL:
                return outcome
        return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
    if isinstance(left, WallIdentity) and isinstance(right, WallIdentity):
        for field_name in ("temperature_K", "material", "area_m2", "location"):
            required = field_name in {"temperature_K", "material"}
            outcome = _state_compare(
                f"wall.{field_name}",
                getattr(left, field_name),
                getattr(right, field_name),
                required=required,
                permitted_na=not required,
            )
            if outcome.kind is not IdentityEqualKind.EQUAL:
                return outcome
        return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
    if left == right:
        return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
    return _mismatch(name)


def _quantity_state(identity: Identity) -> State[Quantity]:
    q = identity.quantity
    if isinstance(q, State):
        return q
    return State.of(q)


def validate_quantity_profile(identity: Identity) -> IdentityEqualOutcome:
    """Reject a supplied VALUE on an inapplicable axis; check reservoir rule."""

    q_state = _quantity_state(identity)
    if q_state.is_not_applicable:
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            ("quantity",),
            "quantity cannot be not_applicable",
        )
    if quantity_token(identity) is None:
        return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
    profile = profile_for(identity)
    bad: list[str] = []
    for name in _AXIS_NAMES:
        state = _axis_state(identity, name)
        if state is None:
            continue
        # permitted_not_applicable means N/A or absent — a VALUE is invalid,
        # not an extra equality key (v2.1: supplied inapplicable value is
        # invalid, not an override).
        if state.is_value and name not in profile.required:
            bad.append(name)
        if (
            state.is_not_applicable
            and name in profile.required
            and name not in profile.permitted_not_applicable
        ):
            bad.append(name)
    if bad:
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            tuple(bad),
            "inapplicable axis supplied as value, or required axis marked not_applicable",
        )
    reservoir_issue = _reservoir_rule(identity)
    if reservoir_issue is not None:
        return reservoir_issue
    return IdentityEqualOutcome(IdentityEqualKind.EQUAL)


def _reservoir_rule(identity: Identity) -> IdentityEqualOutcome | None:
    if quantity_token(identity) is not Quantity.P_SAT:
        return None
    reservoir = identity.reservoir
    if reservoir is None or not reservoir.is_value or reservoir.value is None:
        return None
    src = reservoir.value
    if src.formula != identity.species.formula:
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            ("reservoir",),
            "p_sat reservoir must be the same formula as the gas species",
        )
    token = phase_token(src)
    if token is None:
        if _phase_state(src).is_unknown:
            return IdentityEqualOutcome(
                IdentityEqualKind.IDENTITY_UNKNOWN,
                ("reservoir",),
                "reservoir phase unknown",
            )
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            ("reservoir",),
            "p_sat reservoir must be condensed",
        )
    if token not in CONDENSED_PHASES:
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            ("reservoir",),
            "p_sat reservoir must be condensed",
        )
    return None


def identity_equal(left: Identity, right: Identity) -> IdentityEqualOutcome:
    """Exact equality on the quantity-class compared axes."""

    left_q_state = _quantity_state(left)
    right_q_state = _quantity_state(right)
    if left_q_state.is_not_applicable and right_q_state.is_not_applicable:
        return IdentityEqualOutcome(
            IdentityEqualKind.INVALID_IDENTITY,
            ("quantity",),
            "quantity cannot be not_applicable",
        )
    if left_q_state.is_not_applicable or right_q_state.is_not_applicable:
        if left_q_state.is_unknown or right_q_state.is_unknown:
            return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_UNKNOWN, ("quantity",))
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, ("quantity",))
    left_profile = validate_quantity_profile(left)
    right_profile = validate_quantity_profile(right)
    if left_profile.kind is IdentityEqualKind.INVALID_IDENTITY:
        return left_profile
    if right_profile.kind is IdentityEqualKind.INVALID_IDENTITY:
        return right_profile
    left_q = quantity_token(left)
    right_q = quantity_token(right)
    if left_q is None or right_q is None:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_UNKNOWN, ("quantity",))
    if left_q is not right_q:
        return IdentityEqualOutcome(
            IdentityEqualKind.IDENTITY_MISMATCH, ("quantity",)
        )
    species_outcome = _species_equal(left.species, right.species)
    if species_outcome.kind is not IdentityEqualKind.EQUAL:
        return species_outcome

    profile = profile_for(left)
    mismatch: list[str] = []
    unknown: list[str] = []
    invalid: list[str] = []
    for name in _AXIS_NAMES:
        required = name in profile.required
        permitted_na = name in profile.permitted_not_applicable
        if not required and not permitted_na:
            # Forbidden as value; validate_quantity_profile already refused
            # filled values. Absent/n/a on both sides is ignored.
            continue
        outcome = _state_compare(
            name,
            _axis_state(left, name),
            _axis_state(right, name),
            required=required,
            permitted_na=permitted_na,
        )
        if outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH:
            mismatch.extend(outcome.fields)
        elif outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN:
            unknown.extend(outcome.fields)
        elif outcome.kind is IdentityEqualKind.INVALID_IDENTITY:
            invalid.extend(outcome.fields)
    if invalid:
        return IdentityEqualOutcome(IdentityEqualKind.INVALID_IDENTITY, tuple(invalid))
    if mismatch:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_MISMATCH, tuple(mismatch))
    if unknown:
        return IdentityEqualOutcome(IdentityEqualKind.IDENTITY_UNKNOWN, tuple(unknown))
    return IdentityEqualOutcome(IdentityEqualKind.EQUAL)
