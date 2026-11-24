"""Policy and inference tokens used below battery and runtime layers."""

from __future__ import annotations

from enum import StrEnum

from simulator.lab_schedule import _SCCM_REFERENCE_STATE_UNREPORTED


class NoticeKind(StrEnum):
    FLOOR_INVERSION = "floor_inversion"
    FALLBACK = "fallback"
    OUT_OF_GAMMA_DOMAIN = "out_of_gamma_domain"
    OUT_OF_CERTIFIED_BAND = "out_of_certified_band"
    SOSSI_GAS_BOUNDARY_UNVERIFIED = "sossi_gas_boundary_unverified"
    SCCM_REFERENCE_STATE_UNREPORTED = _SCCM_REFERENCE_STATE_UNREPORTED
    DERIVATION_USES_COMPILATION = "derivation_uses_compilation"
    COMPOSITION_PROJECTED = "composition_projected"
    COMPOSITION_BINARY_COMPLEMENT = "composition_binary_complement"
    # Oxide mole fractions from printed element mass percent, when those
    # oxides account for the whole sample. Exact stoichiometry, flagged.
    COMPOSITION_OXIDE_FROM_ELEMENT_MASS_PERCENT = (
        "composition_oxide_from_element_mass_percent"
    )
    TOTAL_IRON_REPORTED_AS_FEO = "total_iron_reported_as_feo"
    WUSTITE_REFERENCE_CONVERSION = "wustite_reference_conversion"
    PRESSURE_PROVENANCE_UNKNOWN = "pressure_provenance_unknown"
    TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE = "total_pressure_ambient_open_furnace"
    SOURCE_DISAGREEMENT = "source_disagreement"
    # A nonblocking limitation of an engine model or how it is applied. The
    # reason carries detail; it adds no stratum and does not change authority.
    ENGINE_MODEL_CAVEAT = "engine_model_caveat"
    METHOD_UNSTATED = "method_unstated"
    METHOD_INFERRED = "method_inferred"
    PHASE_INFERRED = "phase_inferred"
    # An input was left out because this row does not take it. The reason
    # says which input and why. Not a blocking qualification.
    INPUT_OMITTED = "input_omitted"
    FO2_FRAME_FROM_PRINTED_ATMOSPHERE = "fo2_frame_from_printed_atmosphere"
    COMPARISON_METHOD_CELL_CONSTANT_CANCELS = "comparison_method_cell_constant_cancels"
    PROBABLE_SOURCE_MISPRINT = "probable_source_misprint"
    PREDICTION_OUTSIDE_PHYSICAL_RANGE = "prediction_outside_physical_range"
    SOURCE_VALUE_OUTSIDE_PHYSICAL_RANGE = "source_value_outside_physical_range"
    UNVERIFIED_APPARATUS = "unverified_apparatus"
    CELL_MATERIAL_INFERRED = "cell_material_inferred"
    COMPOSITION_FROM_SAMPLE_CATALOG = "composition_from_sample_catalog"
    # t-1593: a named material's cited bulk analysis varies by lot
    # (JSC-1 vs JSC-1A). Advisory. Not a flagged stratum.
    LOT_VARIABILITY = "lot_variability"
    IMCC_COMPLEX_SATURATION = "imcc_complex_saturation"
    REFERENCE_PHASE_BY_CONVENTION = "reference_phase_by_convention"
    # Extract omitted admission or said pending/pending_validation; d-056 admits by default, not a reviewer decision.
    ADMISSION_DEFAULTED = "admission_defaulted"
    # admission_status carried an evidence label. The reason names the legacy token.
    # Not a flagged stratum and not a reviewer decision.
    ADMISSION_FROM_LEGACY_TOKEN = "admission_from_legacy_token"
    VALIDATION_HARD_ISSUE_LABEL = "validation_hard_issue_label"
    FIGURE_ONLY = "figure_only"
    EVALUATED_FROM_FIT_ON_DECLARED_GRID = "evaluated_from_fit_on_declared_grid"
    APPROXIMATE_VALUE = "approximate_value"
    # The identity pressure is a band. Distinct from APPROXIMATE_VALUE, which
    # is the measured observable. An exact residue wt% must not take that stratum.
    APPROXIMATE_PRESSURE = "approximate_pressure"
    REACTIVE_CELL_NOT_MODELLED = "reactive_cell_not_modelled"
    # Fe3+/sum Fe from measured oxygen, assigning every excess O atom to Fe.
    # The reason lists any other redox-variable element that was printed.
    REDOX_FROM_MEASURED_OXYGEN_FE_ONLY_ASSUMPTION = (
        "redox_from_measured_oxygen_fe_only_assumption"
    )
    # t-1123a: printed point sits between printed liquidus positions; scored as
    # liquid, flagged (all-numeric line only). Reason carries the stated liquidus.
    LIQUIDUS_POSITION_CONTESTED = "liquidus_position_contested"
    QUENCH_ALTERED = "quench_altered"
    QUENCH_OXIDISING = "quench_oxidising"
    # t-1465: a non-finite gbase token in a phase this read did not request.
    # Advisory. The reason is the parser sentence (phase, field, raw token).
    # It is not a flagged stratum and does not void the decision band.
    UNREQUESTED_PHASE_NONFINITE = "unrequested_phase_nonfinite"
    # t-1598: printed vol% or vol-ppm of an ideal gas, stored as a mole fraction.
    IDEAL_GAS_VOLUME_FRACTION_AS_MOLE_FRACTION = (
        "ideal_gas_volume_fraction_as_mole_fraction"
    )
    # t-1597: p_bulk < p_sat(T_wall). The model predicts zero net deposition
    # (net evaporation). The miss against a measured deposit stays scored.
    # Not a flagged stratum.
    UNDERSATURATED_WALL_NET_EVAPORATION = "undersaturated_wall_net_evaporation"
    # t-1619 / d-324. A printed buffer offset was converted with the
    # calibration the paper names. Advisory. Not a flagged stratum.
    BUFFER_RELATIVE_FO2_CONVERTED = "buffer_relative_fo2_converted"
    # The extract did not name a calibration the buffer owner has, so the
    # canonical curve was used. Advisory. Not a flagged stratum.
    BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED = (
        "buffer_calibration_not_printed_canonical_used"
    )
    # The paper says this ΔIW is ideal FeO and metal Fe (or x_FeO/x_Fe).
    # Advisory. Not a flagged stratum. Extrapolation still uses
    # out_of_certified_band, which is the flagged stratum.
    DELTA_IW_FROM_IDEAL_FEO_FE_RATIO = "delta_iw_from_ideal_feo_fe_ratio"
    # d-326: a printed study-level orifice interval. Advisory. The coded
    # p/d regime check uses both ends of the interval. Case 1 carries no
    # value band; a raw-rate reduction puts the propagated dex band on
    # Notice.band. Not a flagged stratum.
    APPARATUS_GEOMETRY_INTERVAL_PRINTED = "apparatus_geometry_interval_printed"
    # t-1624 / d-324 extension. T is past every table and fit that sizes
    # this buffer. The central curve is still evaluated. The band is not a
    # number. Advisory. out_of_certified_band remains the flagged stratum.
    BUFFER_BAND_UNSIZED_BEYOND_ALL_CALIBRATIONS = (
        "buffer_band_unsized_beyond_all_calibrations"
    )
    # d-460 default-policy flags. Policy metadata is stored separately on
    # Notice.policy; each token names the applied table rule.
    ASSUMED_PRESSURE = "assumed_pressure"
    METHOD_IMPLIED = "method_implied"
    EFFUSION_REGIME_ASSUMED = "effusion_regime_assumed"
    ALPHA_ASSUMED = "alpha_assumed"
    KNOB_DEFAULTED = "knob_defaulted"
    ASSUMED_FO2 = "assumed_fo2"
    METHOD_ASSUMED = "method_assumed"
    KEMS_CALIBRATION_BY_SOURCE = "kems_calibration_by_source"
    BASIS_CONVERTED = "basis_converted"
    INTERVAL_OBSERVATION = "interval_observation"
    COMPOSITION_FROM_RUN_SIBLINGS = "composition_from_run_siblings"
    COMPOSITION_FROM_ANALYSED_PHASE = "composition_from_analysed_phase"


DEFAULT_POLICY_NOTICE_KINDS = frozenset(
    {
        NoticeKind.ASSUMED_PRESSURE,
        NoticeKind.METHOD_IMPLIED,
        NoticeKind.EFFUSION_REGIME_ASSUMED,
        NoticeKind.ALPHA_ASSUMED,
        NoticeKind.KNOB_DEFAULTED,
        NoticeKind.ASSUMED_FO2,
        NoticeKind.METHOD_ASSUMED,
        NoticeKind.KEMS_CALIBRATION_BY_SOURCE,
        NoticeKind.BASIS_CONVERTED,
        NoticeKind.INTERVAL_OBSERVATION,
        NoticeKind.PHASE_INFERRED,
        NoticeKind.METHOD_UNSTATED,
        NoticeKind.METHOD_INFERRED,
        NoticeKind.COMPOSITION_FROM_RUN_SIBLINGS,
        NoticeKind.COMPOSITION_FROM_ANALYSED_PHASE,
    }
)


class Phase(StrEnum):
    CR = "cr"
    L = "l"
    G = "g"
    AQ = "aq"
    GLASS = "glass"
    SUPERCOOLED_L = "supercooled_l"
    UNSPECIFIED = "unspecified"


class MethodToken(StrEnum):
    UNSPECIFIED = "unspecified"
    CALORIMETRY = "calorimetry"
    KNUDSEN_EFFUSION = "knudsen_effusion"
    STATIC_EQUILIBRIUM = "static_equilibrium"
    LANGMUIR_FREE_EVAPORATION = "langmuir_free_evaporation"
    TRANSPIRATION = "transpiration"
    TGA = "tga"
    DTA_DSC = "dta_dsc"
    EVOLVED_GAS_MS = "evolved_gas_ms"
    SOLAR_FURNACE_PYROLYSIS = "solar_furnace_pyrolysis"
    VACUUM_CHAMBER_PYROLYSIS = "vacuum_chamber_pyrolysis"
    EMF_CELL = "emf_cell"
    QUENCH_EQUILIBRATION = "quench_equilibration"
    AERODYNAMIC_LEVITATION_LASER_HEATING = "aerodynamic_levitation_laser_heating"
    ADIABATIC_CALORIMETRY = "adiabatic_calorimetry"
    DROP_CALORIMETRY = "drop_calorimetry"
    SOLUTION_CALORIMETRY = "solution_calorimetry"
    PPMS_HEAT_CAPACITY = "ppms_heat_capacity"
    MOSSBAUER_SPECTROSCOPY = "mossbauer_spectroscopy"
    XANES_SPECTROSCOPY = "xanes_spectroscopy"
    WET_CHEMICAL_ANALYSIS = "wet_chemical_analysis"
    ELECTRON_MICROPROBE = "electron_microprobe"
    PISTON_CYLINDER = "piston_cylinder"
    TABULATION = "tabulation"
    ENGINE_EVALUATION = "engine_evaluation"


__all__ = (
    "DEFAULT_POLICY_NOTICE_KINDS",
    "MethodToken",
    "NoticeKind",
    "Phase",
)
