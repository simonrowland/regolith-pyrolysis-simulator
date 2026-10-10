"""Closed tokens for the empirical battery schema (v2.1).

Ambiguity resolutions (simplest-correct; not owner questions):
- Rail IDs are the nine owner-bound tokens from SCHEMA-PROPOSAL-v2.1
  guard_09_05, stored as Residual.rail. Mandate §Prediction posture names
  the same nine. The owner ratified ``residue_composition`` as the ninth
  rail on 2026-10-01.
- Reference-state *convention* tokens reuse the catalog's
  ``raoultian_pure_endmember`` and extend with ``henrian_solid``,
  ``henrian_liquid``, ``hypothetical_1wt_pct``. ``single_cation_oxide`` is
  never a convention (it is a component_id / component_basis). Endmember
  phase remains a separate axis on Species.
- Quantity tokens are the v2.1 ``quantity_units`` storage enum. Aliases are
  resolved before validation, not additional quantities.
- Evidence classes are the ten closed origin classes in v2.1. Mapping from
  extract ``method_class`` is a later migration chunk.
"""

from __future__ import annotations

from enum import StrEnum


class Engine(StrEnum):
    """Closed first-class engine tokens. Not derived from resolve_backend."""

    INTERNAL_ANALYTICAL = "internal-analytical"
    NASA_CEA_9 = "nasa_cea_9"
    ELLINGHAM = "ellingham"
    ANTOINE_SIDECAR = "antoine_sidecar"
    CATALOG_EVALUATOR = "catalog_evaluator"
    ALPHAMELTS = "alphamelts"
    THERMOENGINE = "thermoengine"
    VAPOROCK = "vaporock"
    MAGEMIN = "magemin"
    OPENIMCC = "openimcc"


class Rail(StrEnum):
    """Owner-bound residual reporting rails."""

    VAPOUR = "vapour"
    MELT_ACTIVITY = "melt_activity"
    THERMOCHEMISTRY = "thermochemistry"
    SIO_EVOLUTION = "SiO_evolution"
    PYROLYSIS_YIELD = "pyrolysis_yield"
    WALL_DEPOSITION = "wall_deposition"
    REDOX = "redox"
    ALKALI_SHUTTLE = "alkali_shuttle"
    RESIDUE_COMPOSITION = "residue_composition"


class Quantity(StrEnum):
    """Complete storage enum (v2.1 quantity_units)."""

    P_SAT = "p_sat"
    P_PARTIAL = "p_partial"
    FUGACITY = "fugacity"
    P_REFERENCE = "p_reference"
    LOG10_KF = "log10_Kf"
    LOG10_K_STAR = "log10_K_star"
    ACTIVITY = "activity"
    ACTIVITY_COEFFICIENT = "activity_coefficient"
    ACTIVITY_COEFFICIENT_TEMPERATURE_FIT = "activity_coefficient_temperature_fit"
    EVAPORATION_COEFFICIENT_ALPHA = "evaporation_coefficient_alpha"
    MASS_LOSS_FRACTION = "mass_loss_fraction"
    MASS_LOSS_FRACTION_VS_T = "mass_loss_fraction_vs_T"
    MASS_LOSS_AREAL_DENSITY = "mass_loss_areal_density"
    YIELD_FRACTION = "yield_fraction"
    O2_YIELD = "o2_yield"
    FE3_FE2_RATIO = "fe3_fe2_ratio"
    ION_INTENSITY_RATIO = "ion_intensity_ratio"
    DELTA_FH = "delta_fH"
    DELTA_FG = "delta_fG"
    H_MINUS_H298 = "H_minus_H298"
    PARTIAL_MOLAR_ENTHALPY = "partial_molar_enthalpy"
    ENTHALPY_OF_VAPORIZATION_2ND_LAW = "enthalpy_of_vaporization_2nd_law"
    ENTHALPY_OF_VAPORIZATION_3RD_LAW = "enthalpy_of_vaporization_3rd_law"
    CP = "cp"
    S = "S"
    EVAPORATION_RATE = "evaporation_rate"
    MASS_LOSS_RATE = "mass_loss_rate"
    WALL_DEPOSIT_MASS = "wall_deposit_mass"
    TRANSITION_TEMPERATURE = "transition_temperature"
    VISCOSITY = "viscosity"
    DENSITY = "density"
    ELECTRICAL_CONDUCTIVITY = "electrical_conductivity"
    ISOTOPE_DELTA = "isotope_delta"
    CONDENSATE_COMPOSITION = "condensate_composition"
    LIQUIDUS_COMPOSITION = "liquidus_composition"
    EVOLVED_GAS_YIELD = "evolved_gas_yield"
    ION_INTENSITY = "ion_intensity"
    INTERACTION_PARAMETER = "interaction_parameter"
    RESIDUE_COMPONENT_COMPOSITION = "residue_component_composition"


class EvidenceClass(StrEnum):
    MEASURED_DIRECT = "measured_direct"
    MEASURED_TABULATED = "measured_tabulated"
    MEASURED_REDUCED = "measured_reduced"
    QUOTED_ATTRIBUTED = "quoted_attributed"
    QUOTED_UNATTRIBUTED = "quoted_unattributed"
    MODEL_DERIVED = "model_derived"
    AUTHOR_ESTIMATE = "author_estimate"
    FIGURE_ONLY = "figure_only"
    COMPILATION_ASSESSED = "compilation_assessed"
    ENGINE_PREDICTION = "engine_prediction"


class RefusalReason(StrEnum):
    INVALID_SOURCE = "invalid_source"
    UNDERDETERMINED_APPARATUS = "underdetermined_apparatus"
    METHOD_UNKNOWN = "method_unknown"
    EFFUSION_REGIME_UNVERIFIED = "effusion_regime_unverified"
    BACKGROUND_PRESSURE_HIGH = "background_pressure_high"
    BACKGROUND_PRESSURE_INTERVAL_STRADDLES = "background_pressure_interval_straddles"
    IDENTITY_MISMATCH = "identity_mismatch"
    IDENTITY_UNKNOWN = "identity_unknown"
    INVALID_IDENTITY = "invalid_identity"
    DECISION_RULE_MISSING = "decision_rule_missing"
    METRIC_DOMAIN = "metric_domain"
    UNSUPPORTED = "unsupported"
    NOT_PROBED = "not_probed"
    ATTEMPTED_UNAVAILABLE = "attempted_unavailable"
    ADMISSION_NOT_ADMITTED = "admission_not_admitted"
    LINEAGE_UNKNOWN = "lineage_unknown"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    CYCLIC_DERIVATION = "cyclic_derivation"
    REACTION_UNBALANCED = "reaction_unbalanced"
    POLYMORPH_UNRESOLVED = "polymorph_unresolved"
    INAPPLICABLE_AXIS = "inapplicable_axis_supplied"
    RESERVOIR_RULE = "reservoir_rule"
    IDENTITY_INCOMPLETE = "identity_incomplete"
    CONDITIONAL_FIELD = "conditional_field"
    BULK_NOT_LIQUID_COMPOSITION = "bulk_not_liquid_composition"
    OPENIMCC_NOT_IMPORTABLE = "openimcc_not_importable"
    OUTSIDE_SUPPORTED_SPECIES = "outside_supported_species"
    COEFFICIENT_BASIS_MISMATCH = "coefficient_basis_mismatch"


class CellMaterial(StrEnum):
    """Closed vocabulary for printed Knudsen-cell materials."""

    PT = "Pt"
    IR = "Ir"
    RH = "Rh"
    W = "W"
    MO = "Mo"
    TA = "Ta"
    NB = "Nb"
    RE = "Re"
    NI = "Ni"
    FE = "Fe"
    C_GRAPHITE = "C_graphite"
    AL2O3 = "Al2O3"
    SIO2 = "SiO2"
    MGO = "MgO"
    ZRO2 = "ZrO2"
    Y2O3 = "Y2O3"
    THO2 = "ThO2"
    BEO = "BeO"
    BN = "BN"
    SIC = "SiC"
    OTHER_ALLOY = "other_alloy"
    OTHER = "other"


class BenchAbsenceReason(StrEnum):
    NOT_PUBLISHED = "not_published"
    NOT_NUMERIC = "not_numeric"
    IN_CITED_SOURCE = "in_cited_source"
    ILLEGIBLE = "illegible"
    WOULD_INVENT = "would_invent"
    NOT_APPLICABLE = "not_applicable"


class BenchIdentityBasis(StrEnum):
    DESCRIBED_IN_THIS_WORK = "described_in_this_work"
    CITED_BY_AUTHOR = "cited_by_author"
    INFERRED_FROM_EMBEDDED_EVIDENCE = "inferred_from_embedded_evidence"


class NoticeKind(StrEnum):
    FLOOR_INVERSION = "floor_inversion"
    FALLBACK = "fallback"
    OUT_OF_GAMMA_DOMAIN = "out_of_gamma_domain"
    OUT_OF_CERTIFIED_BAND = "out_of_certified_band"
    DERIVATION_USES_COMPILATION = "derivation_uses_compilation"
    COMPOSITION_PROJECTED = "composition_projected"
    PRESSURE_PROVENANCE_UNKNOWN = "pressure_provenance_unknown"
    SOURCE_DISAGREEMENT = "source_disagreement"
    # An input was left out because this row does not take it. The reason
    # says which input and why. Not a blocking qualification.
    INPUT_OMITTED = "input_omitted"
    COMPARISON_METHOD_CELL_CONSTANT_CANCELS = "comparison_method_cell_constant_cancels"
    PROBABLE_SOURCE_MISPRINT = "probable_source_misprint"
    UNVERIFIED_APPARATUS = "unverified_apparatus"
    CELL_MATERIAL_INFERRED = "cell_material_inferred"
    COMPOSITION_FROM_SAMPLE_CATALOG = "composition_from_sample_catalog"
    IMCC_COMPLEX_SATURATION = "imcc_complex_saturation"
    REFERENCE_PHASE_BY_CONVENTION = "reference_phase_by_convention"
    # Extract omitted admission or said pending/pending_validation; d-056 admits by default, not a reviewer decision.
    ADMISSION_DEFAULTED = "admission_defaulted"
    FIGURE_ONLY = "figure_only"
    REACTIVE_CELL_NOT_MODELLED = "reactive_cell_not_modelled"
    # t-1123a: printed point sits between printed liquidus positions; scored as
    # liquid, flagged (all-numeric line only). Reason carries the stated liquidus.
    LIQUIDUS_POSITION_CONTESTED = "liquidus_position_contested"


class ReferenceStateConvention(StrEnum):
    """Catalog activity_input.standard_state.convention, extended per v2.1."""

    RAOULTIAN_PURE_ENDMEMBER = "raoultian_pure_endmember"
    HENRIAN_SOLID = "henrian_solid"
    HENRIAN_LIQUID = "henrian_liquid"
    HYPOTHETICAL_1WT_PCT = "hypothetical_1wt_pct"


class Phase(StrEnum):
    CR = "cr"
    L = "l"
    G = "g"
    AQ = "aq"
    GLASS = "glass"
    SUPERCOOLED_L = "supercooled_l"


class Polymorph(StrEnum):
    """Closed crystal-type tokens. Compared with formula; never a qualifier alone."""

    ALPHA = "alpha"
    BETA = "beta"
    GAMMA = "gamma"
    DELTA = "delta"
    KAPPA = "kappa"
    I = "i"
    II = "ii"
    III = "iii"
    IV = "iv"
    V = "v"
    ALPHA_DELTA = "alpha_delta"
    BETA_RHOMBOHEDRAL = "beta_rhombohedral"
    QUARTZ = "quartz"
    ALPHA_QUARTZ = "alpha_quartz"
    TRIDYMITE = "tridymite"
    CRISTOBALITE_HIGH = "cristobalite_high"
    CRISTOBALITE_LOW = "cristobalite_low"
    CORUNDUM = "corundum"
    ANDALUSITE = "andalusite"
    KYANITE = "kyanite"
    SILLIMANITE = "sillimanite"
    MULLITE = "mullite"
    WUSTITE = "wustite"
    PYRRHOTITE = "pyrrhotite"
    TROILITE = "troilite"
    MARCASITE = "marcasite"
    PYRITE = "pyrite"
    HEMATITE = "hematite"
    MAGNETITE = "magnetite"
    ANATASE = "anatase"
    RUTILE = "rutile"
    WHITE = "white"
    RED = "red"
    RED_IV = "red_iv"
    RED_V = "red_v"
    BLACK = "black"
    YELLOW = "yellow"
    ORTHORHOMBIC = "orthorhombic"
    MONOCLINIC = "monoclinic"
    MONOHYDRATE = "monohydrate"
    DIHYDRATE = "dihydrate"
    TRIHYDRATE = "trihydrate"
    TETRAHYDRATE = "tetrahydrate"
    HEMIHEXAHYDRATE = "hemihexahydrate"
    DIASPORE = "diaspore"
    BOEHMITE = "boehmite"
    GIBBSITE = "gibbsite"
    KAOLINITE = "kaolinite"
    PYROPHYLLITE = "pyrophyllite"
    ANORTHITE = "anorthite"
    GEHLENITE = "gehlenite"
    GROSSULAR = "grossular"
    CA_AL_PYROXENE = "ca_al_pyroxene"
    MARGARITE = "margarite"
    PREHNITE = "prehnite"
    ZOISITE = "zoisite"
    WOLLASTONITE = "wollastonite"
    CYCLOWOLLASTONITE = "cyclowollastonite"
    LIME = "lime"
    PERICLASE = "periclase"
    DICKITE = "dickite"
    HALLOYSITE = "halloysite"
    LARNITE = "larnite"
    RANKINITE = "rankinite"
    CALCIUM_OLIVINE = "calcium_olivine"
    BCC = "bcc"
    FORSTERITE = "forsterite"
    ARSENOLITE = "arsenolite"
    CRYSTALLINE = "crystalline"
    REFERENCE = "reference"


class PrintedQualifierKind(StrEnum):
    """What a printed source qualifier actually is. Not a crystal token."""

    CRYSTAL = "crystal"
    ION = "ion"
    ISOMER = "isomer"
    PRESSURE = "pressure"
    NONE = "none"


class PerBasis(StrEnum):
    MOL_SPECIES = "mol_species"
    MOL_O2 = "mol_O2"
    MOL_ATOM = "mol_atom"
    KG = "kg"
    AREA_TIME = "area_time"
    INITIAL_MASS = "initial_mass"
    DIMENSIONLESS = "dimensionless"


class AmountBasis(StrEnum):
    MOL_INVENTORY = "mol_inventory"
    MOLE_FRACTION = "mole_fraction"
    MASS_PERCENT = "mass_percent"


class MethodToken(StrEnum):
    KNUDSEN_EFFUSION = "knudsen_effusion"
    LANGMUIR_FREE_EVAPORATION = "langmuir_free_evaporation"
    TRANSPIRATION = "transpiration"
    TGA = "tga"
    DTA_DSC = "dta_dsc"
    EVOLVED_GAS_MS = "evolved_gas_ms"
    SOLAR_FURNACE_PYROLYSIS = "solar_furnace_pyrolysis"
    VACUUM_CHAMBER_PYROLYSIS = "vacuum_chamber_pyrolysis"
    EMF_CELL = "emf_cell"
    QUENCH_EQUILIBRATION = "quench_equilibration"
    TABULATION = "tabulation"
    ENGINE_EVALUATION = "engine_evaluation"


class ExperimentKind(StrEnum):
    LITERATURE = "literature"
    SYNTHETIC = "synthetic"


class FO2Channel(StrEnum):
    COMMANDED = "commanded"
    INTRINSIC = "intrinsic"
    BUFFER = "buffer"
    GAS_MIX = "gas_mix"


class RegimeClass(StrEnum):
    MOLECULAR = "molecular"
    TRANSITIONAL = "transitional"
    VISCOUS = "viscous"


class AdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    PENDING = "pending"


class Authority(StrEnum):
    CERTIFIED = "certified"
    BRIDGE = "bridge"
    EXTRAPOLATED = "extrapolated"
    CONVENTION = "convention"
    REFUSED = "refused"


class ResidualStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NO_BAND = "no_band"
    REFUSED = "refused"


def residual_status_token(status: object) -> ResidualStatus | None:
    """Resolve stored and legacy status spellings without inventing a default."""

    token = str(status or "")
    if token in {"typed-refusal", "typed_refusal"}:
        return ResidualStatus.REFUSED
    try:
        return ResidualStatus(token)
    except ValueError:
        return None


class ExecutionState(StrEnum):
    PRODUCED = "produced"
    ATTEMPTED_UNAVAILABLE = "attempted_unavailable"
    UNSUPPORTED = "unsupported"
    NOT_PROBED = "not_probed"


class SourceRelation(StrEnum):
    INDEPENDENT = "independent"
    SAME_INPUT = "same_input"
    TRAINING = "training"
    QUOTED_OVERLAP = "quoted_overlap"
    CORRELATED = "correlated"
    UNKNOWN = "unknown"


class MetricOperation(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"
    DEX = "dex"


class ValueKind(StrEnum):
    POINT = "point"
    SERIES = "series"
    BOUND = "bound"
    INTERVAL = "interval"
    ORDERING = "ordering"
    CATEGORICAL = "categorical"
    RELATIVE_SERIES = "relative_series"
    EXPRESSION = "expression"
    UNAVAILABLE = "unavailable"


class UncertaintyKind(StrEnum):
    PRINTED = "printed"
    DERIVED = "derived"
    NONE = "none"


class AssetRole(StrEnum):
    PDF = "pdf"
    PAGE_RENDER = "page_render"
    MINERU_MD = "mineru_md"
    PDFTOTEXT = "pdftotext"
    TABLE_CSV = "table_csv"
    COMPILATION_RECORD = "compilation_record"


class IdentityEqualKind(StrEnum):
    EQUAL = "equal"
    IDENTITY_MISMATCH = "identity_mismatch"
    IDENTITY_UNKNOWN = "identity_unknown"
    INVALID_IDENTITY = "invalid_identity"


class StateTag(StrEnum):
    VALUE = "value"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


# Canonical storage unit from quantity (+ per, where the unit names the basis).
# v2.1: unit is derived, not an identity key.
QUANTITY_UNITS: dict[Quantity, str] = {
    Quantity.P_SAT: "Pa",
    Quantity.P_PARTIAL: "Pa",
    Quantity.FUGACITY: "Pa",
    Quantity.P_REFERENCE: "Pa",
    Quantity.LOG10_KF: "dimensionless",
    Quantity.LOG10_K_STAR: "dimensionless",
    Quantity.ACTIVITY: "dimensionless",
    Quantity.ACTIVITY_COEFFICIENT: "dimensionless",
    Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT: "dimensionless",
    Quantity.EVAPORATION_COEFFICIENT_ALPHA: "dimensionless",
    Quantity.MASS_LOSS_FRACTION: "dimensionless",
    Quantity.MASS_LOSS_FRACTION_VS_T: "dimensionless",
    Quantity.MASS_LOSS_AREAL_DENSITY: "kg_per_m2",
    Quantity.YIELD_FRACTION: "dimensionless",
    Quantity.O2_YIELD: "dimensionless",
    Quantity.FE3_FE2_RATIO: "dimensionless",
    Quantity.ION_INTENSITY_RATIO: "dimensionless",
    Quantity.DELTA_FH: "kJ_per_declared_mol_basis",
    Quantity.DELTA_FG: "kJ_per_declared_mol_basis",
    Quantity.H_MINUS_H298: "kJ_per_declared_mol_basis",
    Quantity.PARTIAL_MOLAR_ENTHALPY: "kJ_per_declared_mol_basis",
    Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW: "kJ_per_declared_mol_basis",
    Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW: "kJ_per_declared_mol_basis",
    Quantity.CP: "J_per_declared_mol_basis_per_K",
    Quantity.S: "J_per_declared_mol_basis_per_K",
    Quantity.EVAPORATION_RATE: "kg_per_s",
    Quantity.MASS_LOSS_RATE: "kg_per_s",
    Quantity.WALL_DEPOSIT_MASS: "kg",
    Quantity.TRANSITION_TEMPERATURE: "K",
    Quantity.VISCOSITY: "Pa_s",
    Quantity.DENSITY: "kg_per_m3",
    Quantity.ELECTRICAL_CONDUCTIVITY: "S_per_m",
    Quantity.ISOTOPE_DELTA: "per_mil",
    Quantity.CONDENSATE_COMPOSITION: "component_mole_fraction_vector",
    Quantity.LIQUIDUS_COMPOSITION: "component_mole_fraction_vector",
    Quantity.EVOLVED_GAS_YIELD: "mol_species_per_initial_kg",
    Quantity.ION_INTENSITY: "subtype_defined",
    Quantity.INTERACTION_PARAMETER: "subtype_defined",
    Quantity.RESIDUE_COMPONENT_COMPOSITION: "subtype_defined",
}

MEASURED_EVIDENCE = frozenset(
    {
        EvidenceClass.MEASURED_DIRECT,
        EvidenceClass.MEASURED_TABULATED,
        EvidenceClass.MEASURED_REDUCED,
    }
)

FORMATION_QUANTITIES = frozenset(
    {Quantity.DELTA_FH, Quantity.DELTA_FG, Quantity.LOG10_KF}
)
EQUILIBRIUM_FIT_QUANTITIES = frozenset({Quantity.LOG10_K_STAR})
PURE_STANDARD_THERMO = frozenset(
    {Quantity.CP, Quantity.S, Quantity.H_MINUS_H298}
)
VAPORIZATION_ENTHALPIES = frozenset(
    {
        Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW,
        Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW,
    }
)
MELT_ACTIVITY_QUANTITIES = frozenset(
    {
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.INTERACTION_PARAMETER,
    }
)
KINETIC_YIELD_QUANTITIES = frozenset(
    {
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.EVAPORATION_RATE,
        Quantity.MASS_LOSS_RATE,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.YIELD_FRACTION,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.O2_YIELD,
        Quantity.CONDENSATE_COMPOSITION,
        Quantity.WALL_DEPOSIT_MASS,
    }
)
EXTENSIVE_YIELD_QUANTITIES = frozenset(
    {
        Quantity.EVAPORATION_RATE,
        Quantity.MASS_LOSS_RATE,
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.YIELD_FRACTION,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.O2_YIELD,
        Quantity.WALL_DEPOSIT_MASS,
    }
)
CONDENSED_PHASES = frozenset({Phase.CR, Phase.L, Phase.GLASS, Phase.SUPERCOOLED_L})
