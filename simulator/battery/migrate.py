"""Mechanical lift of the empirical battery into schema v2.1 records.

Storage choice
--------------
Sibling files, not an in-place ``battery_v2`` block:

* ``data/literature/extracts-v2/<source_id>.yaml`` — Observation payloads
  for each extract. The original extract files are never rewritten.
* ``data/literature/observations-v2/<stem>.yaml`` — Observation payloads
  for every other listed source (compilations, measurement sidecars,
  residual ledgers).
* ``data/literature/works/<fs_safe(work_id)>.yaml`` — Work + its Experiments.
* ``data/literature/works/ALIASES.yaml`` — explicit alias registry
  (source_id / raw DOI → canonical work_id). Never title-only merging.
* ``data/battery/migration-queue.yaml`` — page_grounded work list.
  Entries that share a work, source, reason, and axes are one group;
  each observation id and locator is kept on that group.
* ``data/battery/migration-report.md`` — counts (measured vs spec).

Why sibling files: the brief requires old extract *rows* to stay
byte-identical so existing consumers (fidelity samples, extract validators,
INDEX builder) keep working. Injecting a top-level key would retokenize the
YAML and risk fidelity-sample drift. Sibling files leave extracts untouched.

Residuals are not generated (chunk 2). Pins are not touched.

Absence is never a measured zero: missing admission/class/pressure/phase/
method become ``unknown`` (or ``pending`` for admission), never admitted /
certified / 101325 Pa / liquid / knudsen_effusion. Unknown rail spellings
raise. Unmapped phase strings are ``State.unknown`` on ``Species.phase``
and queued; they are never stored as gas.
"""

from __future__ import annotations

import collections.abc
import fnmatch
import hashlib
import inspect
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from fractions import Fraction
from pathlib import Path
from types import UnionType
from typing import Any, Iterable, Iterator, Mapping, Union, get_args, get_origin, get_type_hints

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    AssetRole,
    EvidenceClass,
    ExperimentKind,
    FO2Channel,
    MethodToken,
    NoticeKind,
    PerBasis,
    Phase,
    Polymorph,
    Quantity,
    Rail,
    ReferenceStateConvention,
    RefusalReason,
    RegimeClass,
    StateTag,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    Exposure,
    Identity,
    PA_PER_ATM_DEC,
    StandardState,
    SweepIdentity,
    WallIdentity,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    profile_for,
)
from simulator.battery.stable_ids import (
    cao_raw_pca_id,
    series_point_id,
    series_row_extra,
    temperature_token,
)
from simulator.battery.records import (
    Admission,
    AdmissionDecision,
    Annotations,
    Apparatus,
    ApparatusGeometry,
    Bench,
    BenchFact,
    BenchIdentity,
    BenchReference,
    Composition,
    Derivation,
    Evidence,
    Experiment,
    FlowRegime,
    FO2Control,
    Located,
    Locator,
    Notice,
    Observation,
    PressureEnvironment,
    Reaction,
    ReactionTerm,
    Sample,
    SourceFile,
    SourceFiles,
    Species,
    State,
    SweepGas,
    SweepGasComponent,
    ThermalPoint,
    ThermalRamp,
    ThermalSchedule,
    ThermalSetpoint,
    Uncertainty,
    Value,
    Work,
    as_decimal,
)
from simulator.battery.enums import BenchIdentityBasis
from simulator.battery.validate import (
    ValidationIssue,
    ValidationReport,
    validate_corpus,
)
from simulator.physical_constants import STANDARD_ATMOSPHERE_PA

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
LITERATURE = REPO_ROOT / "data" / "literature"
EXTRACTS_DIR = LITERATURE / "extracts"
COMPILATIONS_DIR = LITERATURE / "compilations"
INDEX_PATH = LITERATURE / "INDEX.yaml"
WORKS_DIR = LITERATURE / "works"
ALIASES_PATH = WORKS_DIR / "ALIASES.yaml"

# Explicit reviewed aliases only — never title-only merging.
# Costa 2015 olivine KEMS appears as three source_ids with slightly different
# citation strings; they are one work. Canonical id is the kems-007 citation hash.
REVIEWED_ALIASES: dict[str, str] = {
    "costa-jacobson-2015": "a0a717aca2ebe8d209b17f883521efc828bd737ee63ca1fb99e23743c79ddeab",
    "kems-007-costa-2015": "a0a717aca2ebe8d209b17f883521efc828bd737ee63ca1fb99e23743c79ddeab",
    "REF-016": "a0a717aca2ebe8d209b17f883521efc828bd737ee63ca1fb99e23743c79ddeab",
}
EXTRACTS_V2_DIR = LITERATURE / "extracts-v2"
OBSERVATIONS_V2_DIR = LITERATURE / "observations-v2"
BATTERY_DIR = REPO_ROOT / "data" / "battery"


def iter_observation_store_paths(
    directory: Path,
    pattern: str = "*.yaml",
) -> list[Path]:
    """Observation YAML files, including compilation shard directories.

    A family may live as ``compilations-janaf.yaml`` or as
    ``compilations-janaf/janaf-Al.yaml``. Both are loaded when both exist.
    Directories named ``*-reports`` are audit output, not observation payloads.
    """

    if not directory.is_dir():
        return []
    paths = [path for path in sorted(directory.glob(pattern)) if path.is_file()]
    dir_pattern = pattern[: -len(".yaml")] if pattern.endswith(".yaml") else pattern
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith((".", "_")) or child.name.endswith("-reports"):
            continue
        if not fnmatch.fnmatch(child.name, dir_pattern):
            continue
        paths.extend(path for path in sorted(child.glob("*.yaml")) if path.is_file())
    return paths


def compilation_family_from_store_path(path: Path) -> str | None:
    """Return the compilation family stem, or None when path is not a compilation payload."""

    if path.name.startswith("compilations-") and path.suffix in {".yaml", ".yml"}:
        return path.stem.removeprefix("compilations-")
    parent = path.parent.name
    if parent.startswith("compilations-") and not parent.endswith("-reports"):
        return parent.removeprefix("compilations-")
    return None
QUEUE_PATH = BATTERY_DIR / "migration-queue.yaml"
REPORT_PATH = BATTERY_DIR / "migration-report.md"

NAMED_SOURCES = (
    "kems_measurements.yaml",
    "mre_measurements.yaml",
    "langmuir_knudsen_flux_validation.yaml",
    "refractory_vaporization_validation.yaml",
    "gibbs_battery_residual_ledger.yaml",
    "species_rail_differential_ledger.yaml",
)

CORPUS_REPO_DEFAULT = "regolith-corpus"

# Premise: 1 standard atmosphere = 101325 Pa = 760 Torr exactly (CGPM 1954).
# Algebra: P_Pa = P_Torr × 101325 / 760.
# Unit check: Torr × (Pa/Torr) = Pa.
# Sanity: 760 Torr = 101325 Pa; 1 Torr = 101325/760 ≈ 133.322368421 Pa.
PA_PER_TORR = Decimal(str(int(STANDARD_ATMOSPHERE_PA))) / Decimal("760")
PA_PER_MMHG = PA_PER_TORR  # millimetre of mercury is identified with Torr here

DOI_URL_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
    re.IGNORECASE,
)
DOI_IN_TEXT_RE = re.compile(
    r"(?:doi[:\s]\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\s,;]+)",
    re.IGNORECASE,
)
WS_RE = re.compile(r"\s+")


class UnknownRailSpellingError(ValueError):
    """Legacy rail / key spelling is not in the guard_09_05 map."""


class DuplicateObservationIdError(ValueError):
    """A second source row reused an observation id with a different payload."""


class DuplicateContextIdError(ValueError):
    """A second context row under a work reused a context_id."""


# ---------------------------------------------------------------------------
# Maps (v2.1 §Evidence classes / §Migration / guard_09_05)
# ---------------------------------------------------------------------------

# Automatic method_class → evidence.class. PAGE tokens are absent here.
METHOD_CLASS_MAP: dict[str, EvidenceClass] = {
    "authors_estimate": EvidenceClass.AUTHOR_ESTIMATE,
    "authors_hypothesis": EvidenceClass.AUTHOR_ESTIMATE,
    "authors_preferred_average_of_kems_derived_gammas": EvidenceClass.MEASURED_REDUCED,
    "derived_from_measured_kems_hertz_knudsen": EvidenceClass.MEASURED_REDUCED,
    "derived_gibbs_duhem": EvidenceClass.MODEL_DERIVED,
    "derived_gibbs_duhem_integration": EvidenceClass.MODEL_DERIVED,
    "derived_least_squares": EvidenceClass.MODEL_DERIVED,
    "derived_third_law_from_measured_kems_and_janaf_fef": EvidenceClass.MEASURED_REDUCED,
    "directly_reduced_measurement": EvidenceClass.MEASURED_REDUCED,
    "figure_only": EvidenceClass.FIGURE_ONLY,
    "figure_only_measured_kems": EvidenceClass.FIGURE_ONLY,
    "measured": EvidenceClass.MEASURED_DIRECT,
    "measured KEMS": EvidenceClass.MEASURED_DIRECT,
    "measured_LA_ICP_MS": EvidenceClass.MEASURED_DIRECT,
    "measured_direct": EvidenceClass.MEASURED_DIRECT,
    "measured_direct_qualitative": EvidenceClass.MEASURED_DIRECT,
    "measured_direct_qualitative_with_thermodynamic_crosscheck": EvidenceClass.MEASURED_DIRECT,
    "measured_kems": EvidenceClass.MEASURED_DIRECT,
    "solar_vacuum_pyrolysis": EvidenceClass.MEASURED_DIRECT,
    "solar_vacuum_pyrolysis_free_evaporation": EvidenceClass.MEASURED_DIRECT,
    "solar_fresnel_vacuum_pyrolysis": EvidenceClass.MEASURED_DIRECT,
    "solar_fresnel_continuously_pumped_vacuum_pyrolysis": EvidenceClass.MEASURED_DIRECT,
    "model": EvidenceClass.MODEL_DERIVED,
    "model_derived": EvidenceClass.MODEL_DERIVED,
    "model_derived_assumption": EvidenceClass.MODEL_DERIVED,
    "model_derived_from_Kstar_and_external_gamma": EvidenceClass.MODEL_DERIVED,
    "model_derived_from_Kstar_with_alpha_e_adopted_unity": EvidenceClass.MODEL_DERIVED,
    "model_derived_inverse_fit": EvidenceClass.MODEL_DERIVED,
    "model_derived_second_law_fit": EvidenceClass.MODEL_DERIVED,
    "magma_model_companion_workbook": EvidenceClass.MODEL_DERIVED,
    "compilation_calculated_table": EvidenceClass.COMPILATION_ASSESSED,
    "compilation_derived": EvidenceClass.COMPILATION_ASSESSED,
    "compilation_foreign": EvidenceClass.COMPILATION_ASSESSED,
    "qualitative_review_compilation": EvidenceClass.COMPILATION_ASSESSED,
    "review_compilation": EvidenceClass.COMPILATION_ASSESSED,
    "secondary_compilation": EvidenceClass.COMPILATION_ASSESSED,
    "secondary_compilation_reprinted_as_feedstock": EvidenceClass.COMPILATION_ASSESSED,
    "quoted_unattributed": EvidenceClass.QUOTED_UNATTRIBUTED,
    "quoted_from_other_workers": EvidenceClass.QUOTED_UNATTRIBUTED,
    "quoted_literature": EvidenceClass.QUOTED_UNATTRIBUTED,
    "quoted_measurement": EvidenceClass.QUOTED_UNATTRIBUTED,
    "quoted_prior_work": EvidenceClass.QUOTED_UNATTRIBUTED,
    "literature_psat_not_this_work": EvidenceClass.QUOTED_UNATTRIBUTED,
}

# PAGE tokens: class stays unknown pending page adjudication.
PAGE_METHOD_CLASSES = frozenset(
    {
        "author_derived",
        "author_reported_envelope",
        "authors_reduced_from_ion_intensities",
        "derived",
        "derived_from_figure_8_linear_portion",
        "derived_from_kems_equilibrium_constants",
        "measured_and_compiled_calorimetry",
        "method_only",
        "mixed",
        "mixed_measured_and_model_curves",
        "mixed_quoted_literature_and_model_derived",
        "model_derived_and_compiled",
        "proxy",
        "qualitative_comparison",
        "second_law_kems",
        "third_law_kems",
    }
)

# Closed automatic phase map (v2.1 §Migration) plus reviewed extras.
# Automatic: the three legacy extract strings only.
# Reviewed identity: source already printed a closed Phase enum token
# (g/cr/l/aq/glass/supercooled_l). These are identity, not heuristics —
# a source that wrote "aq" is Phase.AQ. Chosen over queueing the 38 aq
# rows: inventing an archival hole where the source used the closed
# vocabulary is the wrong absence. glass/supercooled_l have no current
# rows but are the same identity class.
# Reviewed source spelling: solid_arsenolite (explicit extract + polymorph).
# No title inference, no substring heuristics.
PHASE_MAP: dict[str, Phase] = {
    "gas": Phase.G,
    "condensed_solid": Phase.CR,
    "condensed_liquid": Phase.L,
    "g": Phase.G,
    "cr": Phase.CR,
    "l": Phase.L,
    "aq": Phase.AQ,
    "glass": Phase.GLASS,
    "supercooled_l": Phase.SUPERCOOLED_L,
    "solid_arsenolite": Phase.CR,
}

TYPE_QUANTITY = {
    "psat_series": Quantity.P_SAT,
    "gibbs_table": Quantity.DELTA_FG,
    "activity_coefficient": Quantity.ACTIVITY_COEFFICIENT,
    "alpha": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "rate_series": Quantity.MASS_LOSS_RATE,
    "transition_point": Quantity.TRANSITION_TEMPERATURE,
}

# Unit strings that name a closed quantity. Consulted only when values.quantity
# is absent; never overrides an explicit quantity token.
UNIT_DECLARED_QUANTITY = {
    "dimensionless alpha vs t_k": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "dimensionless activity": Quantity.ACTIVITY,
}

QUANTITY_ALIASES = {
    "pure_Psat": Quantity.P_SAT,
    "vapor_pressure": Quantity.P_SAT,
    "partial_pressure": Quantity.P_PARTIAL,
    "potassium_partial_pressure_as_published": Quantity.P_PARTIAL,
    "deltafG": Quantity.DELTA_FG,
    "delta_fG": Quantity.DELTA_FG,
    "delta_fG_kJ_mol": Quantity.DELTA_FG,
    "delta_fH": Quantity.DELTA_FH,
    "deltafH": Quantity.DELTA_FH,
    "delta_f_H": Quantity.DELTA_FH,
    "log10_Kf": Quantity.LOG10_KF,
    "log10_kf": Quantity.LOG10_KF,
    "activity": Quantity.ACTIVITY,
    "activity_coefficient": Quantity.ACTIVITY_COEFFICIENT,
    "activity_coefficient_this_work": Quantity.ACTIVITY_COEFFICIENT,
    "wagner_interaction_parameter": Quantity.INTERACTION_PARAMETER,
    "literature_vaporization_coefficient": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "alpha": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "evaporation_coefficient_alpha": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "o2_yield": Quantity.O2_YIELD,
    "mass_loss_fraction": Quantity.MASS_LOSS_FRACTION,
    "bulk_mass_loss_wt_pct": Quantity.MASS_LOSS_FRACTION,
    "non_condensed_mass_loss_fraction": Quantity.MASS_LOSS_FRACTION,
    "ion_current_ratio": Quantity.ION_INTENSITY_RATIO,
    "ion_intensity_ratio": Quantity.ION_INTENSITY_RATIO,
}

# guard_09_05: legacy rail names including underscored aliases.
RAIL_MAP = {
    "vapour": Rail.VAPOUR,
    "vapor": Rail.VAPOUR,
    "melt activities": Rail.MELT_ACTIVITY,
    "melt_activities": Rail.MELT_ACTIVITY,
    "melt_activity": Rail.MELT_ACTIVITY,
    "thermochemistry": Rail.THERMOCHEMISTRY,
    "SiO evolution": Rail.SIO_EVOLUTION,
    "sio evolution": Rail.SIO_EVOLUTION,
    "SiO_evolution": Rail.SIO_EVOLUTION,
    "sio_evolution": Rail.SIO_EVOLUTION,
    "integrated bench": Rail.PYROLYSIS_YIELD,
    "integrated_bench": Rail.PYROLYSIS_YIELD,
    "pyrolysis_yield": Rail.PYROLYSIS_YIELD,
    "SiO/Fe wall deposition": Rail.WALL_DEPOSITION,
    "sio/fe wall deposition": Rail.WALL_DEPOSITION,
    "wall_deposition": Rail.WALL_DEPOSITION,
    "redox": Rail.REDOX,
    "alkali shuttle": Rail.ALKALI_SHUTTLE,
    "alkali_shuttle": Rail.ALKALI_SHUTTLE,
}

METHOD_TOKENS = {m.value: m for m in MethodToken}
REGIME_TO_METHOD = {
    "kems_effusion": None,  # insufficient by spec; page_grounded
    "knudsen_effusion": MethodToken.KNUDSEN_EFFUSION,
    "knudsen_effusion_mass_spectrometry": MethodToken.KNUDSEN_EFFUSION,
    "langmuir_free_evaporation": MethodToken.LANGMUIR_FREE_EVAPORATION,
    "transpiration": MethodToken.TRANSPIRATION,
    "tga": MethodToken.TGA,
    "dta_dsc": MethodToken.DTA_DSC,
    "evolved_gas_ms": MethodToken.EVOLVED_GAS_MS,
    "solar_furnace_pyrolysis": MethodToken.SOLAR_FURNACE_PYROLYSIS,
    "solar_vacuum_pyrolysis": MethodToken.SOLAR_FURNACE_PYROLYSIS,
    "solar_vacuum_pyrolysis_free_evaporation": MethodToken.SOLAR_FURNACE_PYROLYSIS,
    "solar_fresnel_vacuum_pyrolysis": MethodToken.SOLAR_FURNACE_PYROLYSIS,
    "solar_fresnel_continuously_pumped_vacuum_pyrolysis": MethodToken.SOLAR_FURNACE_PYROLYSIS,
    "vacuum_chamber_pyrolysis": MethodToken.VACUUM_CHAMBER_PYROLYSIS,
    "emf_cell": MethodToken.EMF_CELL,
    "quench_equilibration": MethodToken.QUENCH_EQUILIBRATION,
    "tabulation": MethodToken.TABULATION,
    "engine_evaluation": MethodToken.ENGINE_EVALUATION,
    "gas_standard_state_thermo": MethodToken.TABULATION,
}

LOCATOR_FIELDS = {
    "page",
    "published_page",
    "pdf_page_index",
    "table",
    "figure",
    "paragraph",
    "section",
    "equation",
    "line_range",
    "note",
    "source_path",
    "record",
}

SPEC_COUNTS = {
    "citations": 147,
    "doi_works": 56,
    "no_doi_works": 91,
    "admission_statuses": 374,
    "supersedes": 422,
    "series": 60,
    "gibbs_reference_pressures": 1617,
    "gibbs_reference_100000": 1023,
    "gibbs_reference_101325": 594,
    "formulas": 1625,
    "equipment_payloads": 670,
    "absent_admissions": 3511,
    "absent_classes": 2174,
    "range_only_T": 3125,
    "system_like_phases": 1065,
    "missing_phases": 237,
}


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def canonicalize_doi(raw: str) -> str:
    """Canonical DOI: strip resolver prefixes, NFC, lowercase.

    Crossref treats DOI as case-insensitive; the canonical storage form is
    lowercase with the ``10.`` prefix and no resolver URL.
    """

    text = unicodedata.normalize("NFC", str(raw).strip())
    text = DOI_URL_RE.sub("", text)
    text = text.strip().rstrip(").,;")
    return text.lower()


def extract_doi(*texts: object) -> str | None:
    for raw in texts:
        if raw is None:
            continue
        if isinstance(raw, str) and raw.strip():
            lowered = raw.strip()
            if lowered.lower() in {"null", "none", "n/a"}:
                continue
            if lowered.lower().startswith("10."):
                return canonicalize_doi(lowered)
            match = DOI_IN_TEXT_RE.search(lowered)
            if match:
                return canonicalize_doi(match.group(1))
            if "doi.org/" in lowered.lower():
                return canonicalize_doi(lowered)
    return None


def citation_hash(citation: str) -> str:
    """SHA256 of UTF-8 NFC citation with collapsed whitespace.

    The Work.citation field retains the exact original string; only the
    identifier hashes the collapsed form.
    """

    nfc = unicodedata.normalize("NFC", citation)
    collapsed = WS_RE.sub(" ", nfc).strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def canonicalize_rail(name: str) -> Rail:
    key = str(name).strip()
    mapped = RAIL_MAP.get(key) or RAIL_MAP.get(key.lower())
    if mapped is None:
        raise UnknownRailSpellingError(
            f"unknown rail spelling {name!r}; guard_09_05 refuses silent drop"
        )
    return mapped


def require_rail_if_stated(row: Mapping[str, Any] | None) -> None:
    if not isinstance(row, Mapping):
        return
    raw = row.get("rail")
    if raw:
        canonicalize_rail(str(raw))


def work_filename(work_id: str) -> str:
    """Filesystem-safe name. DOI slash → underscore; work_id inside YAML is exact."""

    return work_id.replace("/", "_") + ".yaml"


def _dec_str(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _as_dec_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return as_decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


# ---------------------------------------------------------------------------
# YAML plain codec (deterministic; no timestamps)
# ---------------------------------------------------------------------------


def to_plain(obj: object) -> object:
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        return _dec_str(obj)
    if isinstance(obj, Fraction):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, State):
        payload: dict[str, object] = {"tag": obj.tag.value}
        if obj.value is not None:
            payload["value"] = to_plain(obj.value)
        if obj.reason is not None:
            payload["reason"] = obj.reason
        return payload
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, object] = {}
        for item in fields(obj):
            value = getattr(obj, item.name)
            if value is None:
                continue
            key = "class" if item.name == "class_" else item.name
            out[key] = to_plain(value)
        return out
    if isinstance(obj, Mapping):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [to_plain(v) for v in obj]
    if isinstance(obj, list):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return _dec_str(as_decimal(obj))
    return obj


def dump_yaml(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        width=120,
        default_flow_style=False,
    )
    path.write_text(text, encoding="utf-8")


_LOCATOR_FIELD_NAMES = {item.name for item in fields(Locator)}


def _enum(enum_cls: type, value: object) -> object:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


def _state_from_plain(payload: object, cast) -> State:
    if isinstance(payload, State):
        return payload
    if not isinstance(payload, Mapping):
        return State.of(cast(payload))
    tag = StateTag(str(payload.get("tag") or "value"))
    if tag is StateTag.VALUE:
        return State.of(cast(payload.get("value")))
    reason = payload.get("reason")
    if tag is StateTag.NOT_APPLICABLE:
        return State.not_applicable(reason)  # type: ignore[arg-type]
    return State.unknown(reason)  # type: ignore[arg-type]


def _locator_from_plain(payload: object) -> Locator | None:
    if payload is None:
        return None
    if isinstance(payload, Locator):
        return payload
    if not isinstance(payload, Mapping):
        return Locator(note=str(payload))
    kwargs = {k: v for k, v in payload.items() if k in _LOCATOR_FIELD_NAMES and v is not None}
    if not kwargs:
        return None
    return Locator(**kwargs)


def _derivation_from_plain(payload: object) -> Derivation | None:
    if not isinstance(payload, Mapping):
        return None
    params_raw = payload.get("parameters") or ()
    parameters: list[tuple[str, Located[Decimal]]] = []
    for item in params_raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            parameters.append((str(item[0]), _located_from_plain(item[1], as_decimal)))
    inputs = payload.get("inputs") or ()
    raw_marker = payload.get("pure_substance_reference")
    return Derivation(
        relation=str(payload.get("relation") or "identity"),
        inputs=tuple(str(x) for x in inputs),
        parameters=tuple(parameters),
        output_unit=str(payload.get("output_unit") or "as_published"),
        pure_substance_reference=raw_marker if isinstance(raw_marker, bool) else None,
    )


def _point_condition_from_plain(payload: object) -> Located[Any]:
    """Decimal lab axes, or a printed/derived composition map."""

    if isinstance(payload, Located):
        return payload
    if not isinstance(payload, Mapping) or "state" not in payload:
        return _located_from_plain(payload, as_decimal)
    state_payload = payload.get("state")
    value = (
        state_payload.get("value") if isinstance(state_payload, Mapping) else None
    )
    if isinstance(value, Mapping) and (
        "amount_basis" in value or "components" in value
    ):
        return _located_from_plain(payload, _composition_from_plain)

    def _as_printed_map(raw: object) -> dict[str, Any]:
        assert isinstance(raw, Mapping)
        out: dict[str, Any] = {}
        for key, item in raw.items():
            amount = _as_dec_or_none(item)
            out[str(key)] = as_decimal(amount) if amount is not None else item
        return out

    if isinstance(value, Mapping):
        return _located_from_plain(payload, _as_printed_map)
    return _located_from_plain(payload, as_decimal)


def _located_from_plain(payload: object, cast) -> Located:
    if isinstance(payload, Located):
        return payload
    if not isinstance(payload, Mapping) or "state" not in payload:
        return Located(_state_from_plain(payload, cast))
    inference = None
    if payload.get("inference"):
        inference = _derivation_from_plain(payload.get("inference"))
    else:
        inference = _inference_for_printed_conversion(payload)
    return Located(
        _state_from_plain(payload.get("state"), cast),
        locator=_locator_from_plain(payload.get("locator")),
        inference=inference,
    )


def _polymorph_from_plain(value: object) -> Polymorph:
    from simulator.battery.polymorph_dictionary import coerce_polymorph_token

    return coerce_polymorph_token(value)


def _charge_from_plain(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return int(str(value))
    return value


def _species_from_plain(payload: object) -> Species:
    if not isinstance(payload, Mapping):
        raise TypeError(f"species payload must be a mapping, not {payload!r}")
    return migrate_species_payload(payload)


def migrate_species_payload(payload: Mapping[str, Any]) -> Species:
    """Lift a stored Species: closed polymorph token + charge axis.

    Legacy free-form polymorph strings are aliased to the closed enum when
    they match a known token or printed name. Unrecognised strings become
    unknown, never a second free-form axis. Charge is read from the stored
    axis, else from a trailing ``+/-`` on formula, else 0 (neutral as a
    value). Two consecutive dumps of the result are byte-identical.
    """

    polymorph = payload.get("polymorph")
    polymorph_state: State[Polymorph] | None
    if polymorph is None:
        polymorph_state = None
    elif isinstance(polymorph, Mapping) and str(polymorph.get("tag") or "") != "value":
        polymorph_state = _state_from_plain(polymorph, _polymorph_from_plain)
    else:
        try:
            polymorph_state = _state_from_plain(polymorph, _polymorph_from_plain)
        except ValueError:
            from simulator.battery.polymorph_dictionary import unrecognised_polymorph_reason

            raw = polymorph.get("value") if isinstance(polymorph, Mapping) else polymorph
            polymorph_state = State.unknown(unrecognised_polymorph_reason(raw))
    charge_payload = payload.get("charge")
    charge_state: State[int] | None
    if charge_payload is None:
        charge_state = None
    else:
        charge_state = _state_from_plain(charge_payload, _charge_from_plain)
    return Species(
        str(payload.get("formula") or "unknown"),
        _state_from_plain(payload.get("phase"), lambda v: Phase(str(v))),
        polymorph=polymorph_state,
        charge=charge_state,
    )


def _composition_from_plain(payload: object) -> Composition:
    assert isinstance(payload, Mapping)
    components = payload.get("components") or ()
    pairs = tuple((str(k), as_decimal(v)) for k, v in components)
    return Composition(
        basis=str(payload.get("basis") or "unknown"),
        components=pairs,
        amount_basis=_enum(AmountBasis, payload.get("amount_basis")) or AmountBasis.MOLE_FRACTION,
    )


def _reaction_from_plain(payload: object) -> Reaction:
    assert isinstance(payload, Mapping)
    terms = []
    for item in payload.get("terms") or ():
        if not isinstance(item, Mapping):
            continue
        terms.append(
            ReactionTerm(
                species=_species_from_plain(item["species"]),
                coefficient=item["coefficient"],
            )
        )
    return Reaction(terms=tuple(terms))


def _standard_state_from_plain(payload: object) -> StandardState:
    assert isinstance(payload, Mapping)
    return StandardState(
        convention=_enum(ReferenceStateConvention, payload["convention"]),
        endmember=_species_from_plain(payload["endmember"]),
        component_basis=str(payload.get("component_basis") or ""),
        reference_pressure_bar=as_decimal(payload.get("reference_pressure_bar") or 1),
    )


def _identity_from_plain(payload: object) -> Identity:
    assert isinstance(payload, Mapping)
    kwargs: dict[str, Any] = {
        "quantity": _state_from_plain(payload["quantity"], lambda v: Quantity(str(v))),
        "species": _species_from_plain(payload["species"]),
    }
    if payload.get("subtype") is not None:
        kwargs["subtype"] = _state_from_plain(payload["subtype"], str)
    if payload.get("per") is not None:
        kwargs["per"] = _state_from_plain(payload["per"], lambda v: PerBasis(str(v)))
    if payload.get("temperature_K") is not None:
        kwargs["temperature_K"] = _state_from_plain(payload["temperature_K"], as_decimal)
    if payload.get("standard_pressure_Pa") is not None:
        kwargs["standard_pressure_Pa"] = _state_from_plain(
            payload["standard_pressure_Pa"], as_decimal
        )
    if payload.get("reaction") is not None:
        kwargs["reaction"] = _state_from_plain(payload["reaction"], _reaction_from_plain)
    if payload.get("formation_elements") is not None:
        def _elements(raw: object) -> tuple[tuple[str, Species], ...]:
            pairs = []
            for item in raw or ():
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    pairs.append((str(item[0]), _species_from_plain(item[1])))
            return tuple(pairs)
        kwargs["formation_elements"] = _state_from_plain(
            payload["formation_elements"], _elements
        )
    if payload.get("reference_state") is not None:
        kwargs["reference_state"] = _state_from_plain(
            payload["reference_state"], _standard_state_from_plain
        )
    if payload.get("reservoir") is not None:
        kwargs["reservoir"] = _state_from_plain(payload["reservoir"], _species_from_plain)
    if payload.get("composition") is not None:
        kwargs["composition"] = _state_from_plain(
            payload["composition"], _composition_from_plain
        )
    if payload.get("fO2_Pa") is not None:
        kwargs["fO2_Pa"] = _state_from_plain(payload["fO2_Pa"], as_decimal)
    if payload.get("total_pressure_Pa") is not None:
        kwargs["total_pressure_Pa"] = _state_from_plain(
            payload["total_pressure_Pa"], as_decimal
        )
    if payload.get("sweep_gas") is not None:
        kwargs["sweep_gas"] = _state_from_plain(
            payload["sweep_gas"], _sweep_identity_from_plain
        )
    if payload.get("exposure") is not None:
        kwargs["exposure"] = _state_from_plain(payload["exposure"], _exposure_from_plain)
    if payload.get("sample_mass_kg") is not None:
        kwargs["sample_mass_kg"] = _state_from_plain(payload["sample_mass_kg"], as_decimal)
    if payload.get("wall") is not None:
        kwargs["wall"] = _state_from_plain(payload["wall"], _wall_from_plain)
    return Identity(**kwargs)


def _sweep_identity_from_plain(payload: object) -> SweepIdentity:
    assert isinstance(payload, Mapping)
    return SweepIdentity(
        species=str(payload.get("species") or ""),
        flow_sccm=_state_from_plain(payload["flow_sccm"], as_decimal),
        partial_pressure_Pa=_state_from_plain(payload["partial_pressure_Pa"], as_decimal),
    )


def _exposure_from_plain(payload: object) -> Exposure:
    assert isinstance(payload, Mapping)
    schedule = payload.get("schedule")
    return Exposure(
        area_m2=_state_from_plain(payload["area_m2"], as_decimal),
        duration_s=_state_from_plain(payload["duration_s"], as_decimal),
        schedule=None if schedule is None else _state_from_plain(schedule, str),
    )


def _wall_from_plain(payload: object) -> WallIdentity:
    assert isinstance(payload, Mapping)
    area = payload.get("area_m2")
    location = payload.get("location")
    return WallIdentity(
        temperature_K=_state_from_plain(payload["temperature_K"], as_decimal),
        material=_state_from_plain(payload["material"], str),
        area_m2=None if area is None else _state_from_plain(area, as_decimal),
        location=None if location is None else _state_from_plain(location, str),
    )


def _value_from_plain(payload: object) -> Value:
    assert isinstance(payload, Mapping)
    kind = ValueKind(str(payload["kind"]))
    kwargs: dict[str, Any] = {"kind": kind}
    if payload.get("point") is not None:
        kwargs["point"] = as_decimal(payload["point"])
    if payload.get("series") is not None:
        kwargs["series"] = tuple(
            (as_decimal(a), as_decimal(b)) for a, b in payload["series"]
        )
    if payload.get("bound_operator") is not None:
        kwargs["bound_operator"] = str(payload["bound_operator"])
    if payload.get("bound_value") is not None:
        kwargs["bound_value"] = as_decimal(payload["bound_value"])
    if payload.get("interval_low") is not None:
        kwargs["interval_low"] = as_decimal(payload["interval_low"])
    if payload.get("interval_high") is not None:
        kwargs["interval_high"] = as_decimal(payload["interval_high"])
    if payload.get("ordering") is not None:
        kwargs["ordering"] = tuple(str(x) for x in payload["ordering"])
    if payload.get("categorical") is not None:
        kwargs["categorical"] = str(payload["categorical"])
    if payload.get("relative_series") is not None:
        kwargs["relative_series"] = tuple(
            (as_decimal(a), as_decimal(b)) for a, b in payload["relative_series"]
        )
    if payload.get("relative_normalization") is not None:
        kwargs["relative_normalization"] = str(payload["relative_normalization"])
    if payload.get("expression_text") is not None:
        kwargs["expression_text"] = str(payload["expression_text"])
    if payload.get("expression_parameters") is not None:
        kwargs["expression_parameters"] = tuple(
            (str(k), as_decimal(v)) for k, v in payload["expression_parameters"]
        )
    if payload.get("expression_domain") is not None:
        kwargs["expression_domain"] = str(payload["expression_domain"])
    if payload.get("unavailable_reason") is not None:
        kwargs["unavailable_reason"] = str(payload["unavailable_reason"])
    kwargs["approximate"] = bool(payload.get("approximate", False))
    return Value(**kwargs)


def _value_or_point_from_plain(payload: object) -> Value:
    if isinstance(payload, Value):
        return payload
    if isinstance(payload, Mapping) and payload.get("kind") is not None:
        return _value_from_plain(payload)
    return _value_from_plain({"kind": ValueKind.POINT.value, "point": payload})


def _uncertainty_from_plain(payload: object) -> Uncertainty:
    if not isinstance(payload, Mapping):
        return Uncertainty(kind=UncertaintyKind.NONE)
    value = payload.get("value")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        value = (as_decimal(value[0]), as_decimal(value[1]))
    elif value is not None:
        value = as_decimal(value)
    derivation = None
    if payload.get("derivation"):
        derivation = _derivation_from_plain(payload["derivation"])
    return Uncertainty(
        kind=UncertaintyKind(str(payload.get("kind") or "none")),
        verbatim=payload.get("verbatim"),
        value=value,
        basis=None if payload.get("basis") is None else str(payload["basis"]),
        derivation=derivation,
    )


def _evidence_from_plain(payload: object) -> Evidence:
    assert isinstance(payload, Mapping)
    class_payload = payload.get("class") or payload.get("class_")
    return Evidence(
        class_=_state_from_plain(class_payload, lambda v: EvidenceClass(str(v))),
        original_method_class=payload.get("original_method_class"),
        attribution=payload.get("attribution"),
        model=payload.get("model"),
        correlation_group=payload.get("correlation_group"),
    )


def _admission_from_plain(payload: object) -> Admission:
    assert isinstance(payload, Mapping)
    decided = None
    raw_decided = payload.get("decided_by")
    if isinstance(raw_decided, Mapping):
        decided = AdmissionDecision(
            worker=str(raw_decided.get("worker") or ""),
            date=str(raw_decided.get("date") or "unspecified"),
            evidence=_locator_from_plain(raw_decided.get("evidence"))
            or Locator(note="admission-decision"),
        )
    return Admission(
        status=AdmissionStatus(str(payload.get("status") or "pending")),
        reason=str(payload.get("reason") or "no observation admission_status mapped from source"),
        superseded_by=payload.get("superseded_by"),
        decided_by=decided,
    )


def _notice_from_plain(payload: object) -> Notice:
    assert isinstance(payload, Mapping)
    affected = tuple(Quantity(str(q)) for q in (payload.get("affected_quantities") or ()))
    dropped = payload.get("dropped")
    return Notice(
        kind=NoticeKind(str(payload["kind"])),
        affected_quantities=affected,
        reason=str(payload.get("reason") or ""),
        origin=str(payload.get("origin") or ""),
        original=None if payload.get("original") is None else as_decimal(payload["original"]),
        source=payload.get("source"),
        destination=payload.get("destination"),
        band=payload.get("band"),
        dropped=None if dropped is None else tuple(str(x) for x in dropped),
        dropped_mass_fraction=None
        if payload.get("dropped_mass_fraction") is None
        else as_decimal(payload["dropped_mass_fraction"]),
    )


def _source_file_from_plain(payload: object) -> SourceFile:
    assert isinstance(payload, Mapping)
    return SourceFile(
        asset_id=str(payload["asset_id"]),
        role=AssetRole(str(payload["role"])),
        path=str(payload["path"]),
        sha256=_state_from_plain(payload["sha256"], str),
        provenance_asset=payload.get("provenance_asset"),
    )


def work_from_plain(payload: object) -> Work:
    assert isinstance(payload, Mapping)
    files_payload = payload.get("source_files") or {}
    files = tuple(_source_file_from_plain(f) for f in (files_payload.get("files") or ()))
    commit = files_payload.get("corpus_commit")
    return Work(
        work_id=str(payload["work_id"]),
        citation=str(payload["citation"]),
        source_ids=tuple(str(s) for s in payload.get("source_ids") or ()),
        source_files=SourceFiles(
            corpus_repo=str(files_payload.get("corpus_repo") or CORPUS_REPO_DEFAULT),
            corpus_commit=_state_from_plain(commit, str)
            if commit is not None
            else State.unknown("INDEX does not give corpus commit"),
            files=files,
        ),
        doi=payload.get("doi"),
    )


def _sample_from_plain(payload: object) -> Sample:
    if not isinstance(payload, Mapping) or not payload:
        return Sample()
    mass = payload.get("mass_kg")
    form = payload.get("form")
    container = payload.get("container")
    printed = payload.get("printed_composition")
    initial = payload.get("initial_composition")
    composition_class = payload.get("composition_class")
    characterization = payload.get("characterization")
    surface_area = payload.get("surface_area_m2")
    pretreatment = payload.get("pretreatment")
    return Sample(
        mass_kg=None
        if mass is None
        else _located_from_plain(mass, _value_or_point_from_plain),
        form=None if form is None else _located_from_plain(form, str),
        container=None if container is None else _located_from_plain(container, str),
        printed_composition=None
        if printed is None
        else _located_from_plain(printed, lambda v: v),
        initial_composition=None
        if initial is None
        else _located_from_plain(initial, _composition_from_plain),
        composition_class=None
        if composition_class is None
        else _located_from_plain(composition_class, str),
        characterization=None
        if characterization is None
        else _located_from_plain(characterization, str),
        surface_area_m2=None
        if surface_area is None
        else _located_from_plain(surface_area, _value_or_point_from_plain),
        pretreatment=None
        if pretreatment is None
        else _located_from_plain(pretreatment, str),
    )


def _geometry_from_plain(payload: object) -> ApparatusGeometry | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    kwargs = {}
    for name in (
        "orifice_area_m2",
        "orifice_diameter_m",
        "clausing_factor",
        "orifice_to_sample_area_ratio",
        "exposed_area_m2",
        "chamber_length_m",
    ):
        if payload.get(name) is not None:
            kwargs[name] = _located_from_plain(
                payload[name], _value_or_point_from_plain
            )
    for name in ("cell_internal_dimensions", "chamber_dimensions"):
        if isinstance(payload.get(name), Mapping):
            kwargs[name] = {
                str(key): _located_from_plain(value, _value_or_point_from_plain)
                for key, value in payload[name].items()
            }
    for name in (
        "chamber_volume_m3",
        "orifice_channel_length_m",
        "orifice_count",
    ):
        if payload.get(name) is not None:
            kwargs[name] = _located_from_plain(
                payload[name], _value_or_point_from_plain
            )
    if payload.get("orifice_shape") is not None:
        kwargs["orifice_shape"] = _located_from_plain(payload["orifice_shape"], str)
    return ApparatusGeometry(**kwargs) if kwargs else None


def bench_from_plain(payload: object) -> Bench:
    """Load a Bench from the same deterministic plain form emitted by ``to_plain``."""

    if not isinstance(payload, Mapping):
        raise TypeError("bench must be a mapping")
    raw_identity = payload.get("identity")
    if not isinstance(raw_identity, Mapping):
        raise TypeError("bench identity must be a mapping")
    if raw_identity.get("basis") is None:
        raise ValueError("bench identity requires basis")
    basis = BenchIdentityBasis(str(raw_identity["basis"]))
    raw_ref = raw_identity.get("ref")
    ref = None
    if raw_ref is not None:
        if not isinstance(raw_ref, Mapping):
            raise TypeError("bench identity ref must be a mapping")
        ref_locator = _locator_from_plain(raw_ref.get("locator"))
        if ref_locator is None:
            raise ValueError("bench identity ref requires locator")
        ref = BenchReference(
            work_id=None
            if raw_ref.get("work_id") is None
            else str(raw_ref["work_id"]),
            cited_as=str(raw_ref.get("cited_as") or ""),
            for_parameters=tuple(
                str(item) for item in (raw_ref.get("for_parameters") or ())
            ),
            locator=ref_locator,
        )

    def located_text(name: str) -> Located[str] | None:
        raw = payload.get(name)
        return None if raw is None else _located_from_plain(raw, str)

    def value_or_text(raw: object) -> object:
        if isinstance(raw, Mapping) and raw.get("kind") is not None:
            return _value_or_point_from_plain(raw)
        return _any_from_plain(raw)

    other_facts = []
    for raw in payload.get("other_facts") or ():
        if not isinstance(raw, Mapping):
            raise TypeError("bench other_facts entries must be mappings")
        other_facts.append(
            BenchFact(
                name=str(raw.get("name") or ""),
                value=_located_from_plain(
                    raw.get("value"), _value_or_point_from_plain
                ),
                unit=None if raw.get("unit") is None else str(raw["unit"]),
            )
        )
    pumping_speed = payload.get("pumping_speed_m3_s")
    return Bench(
        id=str(payload.get("id") or ""),
        work_id=str(payload.get("work_id") or ""),
        identity=BenchIdentity(basis=basis, ref=ref, reason=raw_identity.get("reason")),
        apparatus_family=located_text("apparatus_family"),
        method=located_text("method"),
        cell_material_and_liner=located_text("cell_material_and_liner"),
        geometry=_geometry_from_plain(payload.get("geometry")),
        pumping_type=located_text("pumping_type"),
        pumping_speed_m3_s=None
        if pumping_speed is None
        else _located_from_plain(pumping_speed, _value_or_point_from_plain),
        gauges=_located_mapping_from_plain(payload.get("gauges"), str),
        detector=located_text("detector"),
        ionization=_located_mapping_from_plain(payload.get("ionization"), value_or_text),
        temperature_measurement=_located_mapping_from_plain(
            payload.get("temperature_measurement"), str
        ),
        temperature_calibration=_located_mapping_from_plain(
            payload.get("temperature_calibration"), str
        ),
        heating_method=located_text("heating_method"),
        other_facts=tuple(other_facts),
    )


def _any_from_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    amount = _as_dec_or_none(value)
    if amount is not None:
        return amount
    if value is None:
        return None
    return str(value)


def _located_mapping_from_plain(payload: object, cast) -> dict[str, Located] | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    return {str(k): _located_from_plain(v, cast) for k, v in payload.items()}


def _apparatus_from_plain(payload: object) -> Apparatus | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    cell = payload.get("cell_material_and_liner")
    return Apparatus(
        cell_material_and_liner=None
        if cell is None
        else _located_from_plain(cell, str),
        geometry=_geometry_from_plain(payload.get("geometry")),
        ionization=_located_mapping_from_plain(
            payload.get("ionization"), _any_from_plain
        ),
        calibration=_located_mapping_from_plain(
            payload.get("calibration"), _any_from_plain
        ),
        temperature_measurement=_located_mapping_from_plain(
            payload.get("temperature_measurement"), str
        ),
        wall=_located_mapping_from_plain(payload.get("wall"), _any_from_plain),
    )


def _sweep_gas_from_plain(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return payload
    try:
        if set(payload) - {item.name for item in fields(SweepGas)}:
            return payload
        components = None
        if payload.get("components") is not None:
            if not isinstance(payload["components"], (list, tuple)):
                return payload
            # An empty optional collection is ABSENT, not a populated empty tuple.
            if payload["components"]:
                components = []
                for component in payload["components"]:
                    if not isinstance(component, Mapping) or set(component) != {
                        item.name for item in fields(SweepGasComponent)
                    }:
                        return payload
                    components.append(SweepGasComponent(
                        species=component["species"],
                        mole_fraction=_state_from_plain(component["mole_fraction"], as_decimal),
                        flow_sccm=_state_from_plain(component["flow_sccm"], as_decimal),
                        partial_pressure_Pa=_state_from_plain(component["partial_pressure_Pa"], as_decimal),
                    ))
                components = tuple(components)
        alternatives = None
        if payload.get("alternatives") is not None:
            if not isinstance(payload["alternatives"], (list, tuple)):
                return payload
            # An empty optional collection is ABSENT, not a populated empty tuple.
            if payload["alternatives"]:
                alternatives = tuple(_sweep_gas_from_plain(v) for v in payload["alternatives"])
        return SweepGas(
            species=payload.get("species"),
            flow_sccm=_state_from_plain(payload["flow_sccm"], as_decimal),
            partial_pressure_Pa=_state_from_plain(payload["partial_pressure_Pa"], as_decimal),
            components=components,
            alternatives=alternatives,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        # Keep malformed evidence intact for the store's typed refusal report.
        return payload


def _pressure_env_from_plain(payload: object) -> PressureEnvironment:
    assert isinstance(payload, Mapping)
    regime_raw = payload.get("regime") or {}
    note = payload.get("cell_internal_pressure_note")
    return PressureEnvironment(
        total_pressure_Pa=_located_from_plain(
            payload["total_pressure_Pa"], _value_or_point_from_plain
        ),
        sweep_gas=_located_from_plain(
            payload["sweep_gas"],
            _sweep_gas_from_plain,
        ),
        regime=FlowRegime(
            regime_class=_state_from_plain(
                (regime_raw or {}).get("regime_class"),
                lambda v: RegimeClass(str(v)),
            ),
            knudsen_number_orifice=None
            if (regime_raw or {}).get("knudsen_number_orifice") is None
            else _located_from_plain(
                regime_raw["knudsen_number_orifice"], _value_or_point_from_plain
            ),
            knudsen_number_chamber=None
            if (regime_raw or {}).get("knudsen_number_chamber") is None
            else _located_from_plain(
                regime_raw["knudsen_number_chamber"], _value_or_point_from_plain
            ),
        ),
        gauge=_located_mapping_from_plain(payload.get("gauge"), str),
        pumping=_located_mapping_from_plain(payload.get("pumping"), _any_from_plain),
        cell_internal_pressure_note=None
        if note is None
        else _located_from_plain(note, str),
    )


def _thermal_schedule_from_plain(payload: object) -> ThermalSchedule | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError("thermal schedule must be a mapping")
    method = payload.get("method")
    duration = payload.get("total_duration_s")
    cooling = payload.get("cooling_or_quench")
    ramps = []
    for raw in payload.get("ramps") or ():
        if not isinstance(raw, Mapping):
            raise TypeError("thermal schedule ramp must be a mapping")
        if raw.get("rate_K_s") is None:
            raise ValueError("thermal schedule ramp requires rate_K_s")
        ramps.append(
            ThermalRamp(
                rate_K_s=_located_from_plain(
                    raw["rate_K_s"], _value_or_point_from_plain
                ),
                start_temperature_K=None
                if raw.get("start_temperature_K") is None
                else _located_from_plain(
                    raw["start_temperature_K"], _value_or_point_from_plain
                ),
                end_temperature_K=None
                if raw.get("end_temperature_K") is None
                else _located_from_plain(
                    raw["end_temperature_K"], _value_or_point_from_plain
                ),
            )
        )
    setpoints = []
    for raw in payload.get("setpoints_and_holds") or ():
        if not isinstance(raw, Mapping):
            raise TypeError("thermal schedule setpoint must be a mapping")
        if raw.get("temperature_K") is None:
            raise ValueError("thermal schedule setpoint requires temperature_K")
        setpoints.append(
            ThermalSetpoint(
                temperature_K=_located_from_plain(
                    raw["temperature_K"], _value_or_point_from_plain
                ),
                hold_duration_s=None
                if raw.get("hold_duration_s") is None
                else _located_from_plain(
                    raw["hold_duration_s"], _value_or_point_from_plain
                ),
            )
        )
    points = None
    if payload.get("points") is not None:
        parsed_points = []
        for raw in payload["points"]:
            if not isinstance(raw, Mapping):
                raise TypeError("thermal schedule point must be a mapping")
            if raw.get("time_s") is None or raw.get("temperature_K") is None:
                raise ValueError(
                    "thermal schedule point requires time_s and temperature_K"
                )
            parsed_points.append(
                ThermalPoint(
                    time_s=_located_from_plain(
                        raw["time_s"], _value_or_point_from_plain
                    ),
                    temperature_K=_located_from_plain(
                        raw["temperature_K"], _value_or_point_from_plain
                    ),
                )
            )
        points = tuple(parsed_points)
    return ThermalSchedule(
        method=None if method is None else _located_from_plain(method, str),
        ramps=tuple(ramps) or None,
        setpoints_and_holds=tuple(setpoints) or None,
        total_duration_s=None
        if duration is None
        else _located_from_plain(duration, _value_or_point_from_plain),
        cooling_or_quench=None
        if cooling is None
        else _located_from_plain(cooling, str),
        points=points,
    )


def experiment_from_plain(payload: object) -> Experiment:
    assert isinstance(payload, Mapping)
    conditions = {}
    for key, value in (payload.get("conditions") or {}).items():
        conditions[str(key)] = _located_from_plain(value, as_decimal)
    fo2 = payload.get("fO2_control")
    fo2_control = None
    if isinstance(fo2, Mapping):
        fo2_control = FO2Control(
            channel=_state_from_plain(fo2["channel"], lambda v: FO2Channel(str(v))),
            buffer=None
            if fo2.get("buffer") is None
            else _located_from_plain(fo2["buffer"], str),
            oxygen_partial_pressure_Pa=None
            if fo2.get("oxygen_partial_pressure_Pa") is None
            else _located_from_plain(
                fo2["oxygen_partial_pressure_Pa"], _value_or_point_from_plain
            ),
        )
    return Experiment(
        experiment_id=str(payload["experiment_id"]),
        kind=ExperimentKind(str(payload.get("kind") or "literature")),
        method=_state_from_plain(payload["method"], lambda v: MethodToken(str(v))),
        sample=_sample_from_plain(payload.get("sample")),
        conditions=conditions,
        pressure_environment=_pressure_env_from_plain(payload["pressure_environment"]),
        work_id=payload.get("work_id"),
        simulated_experiment_id=payload.get("simulated_experiment_id"),
        locator=_locator_from_plain(payload.get("locator")),
        apparatus=_apparatus_from_plain(payload.get("apparatus")),
        fO2_control=fo2_control,
        bench_id=None if payload.get("bench_id") is None else str(payload["bench_id"]),
        equipment_context_id=None
        if payload.get("equipment_context_id") is None
        else str(payload["equipment_context_id"]),
        thermal_schedule=_thermal_schedule_from_plain(payload.get("thermal_schedule")),
    )


def observation_from_plain(payload: object) -> Observation:
    assert isinstance(payload, Mapping)
    notices = tuple(_notice_from_plain(n) for n in (payload.get("notices") or ()))
    point_conditions = None
    raw_pc = payload.get("point_conditions")
    if isinstance(raw_pc, Mapping):
        point_conditions = {
            str(k): _point_condition_from_plain(v) for k, v in raw_pc.items()
        }
    derived_from = payload.get("derived_from")
    annotations = None
    if isinstance(payload.get("annotations"), Mapping):
        annotations = Annotations(**{
            k: payload["annotations"].get(k)
            for k in ("crystal_system", "space_group", "transition_note")
        })
    return Observation(
        observation_id=str(payload["observation_id"]),
        experiment_id=str(payload["experiment_id"]),
        identity=_identity_from_plain(payload["identity"]),
        value=_value_from_plain(payload["value"]),
        uncertainty=_uncertainty_from_plain(payload.get("uncertainty")),
        evidence=_evidence_from_plain(payload["evidence"]),
        admission=_admission_from_plain(payload["admission"]),
        notices=notices,
        source_id=payload.get("source_id"),
        locator=_locator_from_plain(payload.get("locator")),
        read_from=payload.get("read_from"),
        point_conditions=point_conditions,
        derived_from=None if derived_from is None else tuple(str(x) for x in derived_from),
        derivation=_derivation_from_plain(payload["derivation"])
        if payload.get("derivation")
        else None,
        annotations=annotations,
        authority=payload.get("authority"),
    )


def load_migrated_store(
    root: Path | None = None,
) -> tuple[dict[str, Work], dict[str, Experiment], dict[str, Observation]]:
    """Deserialize the persisted v2.1 YAML store into typed records."""

    root = root or REPO_ROOT
    works: dict[str, Work] = {}
    experiments: dict[str, Experiment] = {}
    observations: dict[str, Observation] = {}
    works_dir = root / "data" / "literature" / "works"
    for path in sorted(works_dir.glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        doc = load_yaml(path)
        if not isinstance(doc, Mapping) or "work" not in doc:
            continue
        work = work_from_plain(doc["work"])
        works[work.work_id] = work
        for raw_exp in doc.get("experiments") or []:
            exp = experiment_from_plain(raw_exp)
            experiments[exp.experiment_id] = exp
    for directory in (
        root / "data" / "literature" / "extracts-v2",
        root / "data" / "literature" / "observations-v2",
    ):
        if not directory.is_dir():
            continue
        for path in iter_observation_store_paths(directory):
            doc = load_yaml(path)
            if not isinstance(doc, Mapping):
                continue
            for raw_obs in doc.get("observations") or []:
                obs = observation_from_plain(raw_obs)
                observations[obs.observation_id] = obs
    return works, experiments, observations


def load_migrated_benches(root: Path | None = None) -> dict[str, Bench]:
    """Deserialize Bench records without changing ``load_migrated_store``'s API."""

    root = root or REPO_ROOT
    benches: dict[str, Bench] = {}
    for path in sorted((root / "data" / "literature" / "works").glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            continue
        for raw_bench in doc.get("benches") or ():
            bench = bench_from_plain(raw_bench)
            benches[bench.id] = bench
    return benches


class UnresolvedEquipmentContextError(ValueError):
    """Experiment.equipment_context_id names a context row the store does not carry."""


def load_migrated_context(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Deserialize the work records' ``context`` rows, keyed by ``context_id``.

    Context rows are verbatim extract payloads (d-032), not typed records.
    """

    root = root or REPO_ROOT
    context: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "data" / "literature" / "works").glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            continue
        for row in doc.get("context") or ():
            if isinstance(row, Mapping) and row.get("context_id"):
                context[str(row["context_id"])] = dict(row)
    return context


def resolve_equipment_context(
    experiment: Experiment,
    context: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Resolve ``Experiment.equipment_context_id`` to its context row.

    ``None`` when the experiment carries no reference (absence is not an
    error). A reference that names no stored row is a dangling FK: typed
    refusal naming the missing id, never a silent skip.
    """

    ref = experiment.equipment_context_id
    if ref is None:
        return None
    row = context.get(ref)
    if row is None:
        raise UnresolvedEquipmentContextError(
            f"equipment_context_id {ref!r} does not resolve"
        )
    return row


_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(path: Path) -> object:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Queue / report
# ---------------------------------------------------------------------------


@dataclass
class QueueEntry:
    work_id: str
    locator: dict[str, object]
    axes: list[str]
    why: str
    source: str | None = None
    observation_id: str | None = None


@dataclass
class DedupeAlias:
    observation_id: str
    source: str
    row_indices: tuple[int, ...]


@dataclass
class SourceCount:
    path: str
    rows_in: int = 0
    observations_out: int = 0
    works_out: int = 0
    experiments_out: int = 0
    queued: int = 0
    metadata_in: int = 0
    index_works_in: int = 0


@dataclass
class MeasuredCounts:
    citations: int = 0
    doi_works: int = 0
    no_doi_works: int = 0
    admission_statuses: int = 0
    supersedes: int = 0
    series: int = 0
    tabulated_lists: int = 0
    gibbs_reference_pressures: int = 0
    gibbs_reference_100000: int = 0
    gibbs_reference_101325: int = 0
    formulas: int = 0
    equipment_payloads: int = 0
    absent_admissions: int = 0
    absent_classes: int = 0
    range_only_T: int = 0
    system_like_phases: int = 0
    missing_phases: int = 0


@dataclass
class MigrationResult:
    works: dict[str, Work] = field(default_factory=dict)
    benches: dict[str, Bench] = field(default_factory=dict)
    experiments: dict[str, Experiment] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    experiments_by_work: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    benches_by_work: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    observations_by_source: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # d-032: extract ``context`` rows carried verbatim per work. They are
    # experiment context, never Observation records (that is the ruling).
    context_by_work: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    queue: list[QueueEntry] = field(default_factory=list)
    source_counts: dict[str, SourceCount] = field(default_factory=dict)
    measured: MeasuredCounts = field(default_factory=MeasuredCounts)
    aliases: dict[str, str] = field(default_factory=dict)
    dedupe_aliases: list[DedupeAlias] = field(default_factory=list)
    evidence_fallthrough: dict[str, int] = field(default_factory=dict)
    unrecognised_polymorphs: dict[str, int] = field(default_factory=dict)
    validation: ValidationReport | None = None
    registry_issues: list[ValidationIssue] = field(default_factory=list)

    def add_queue(
        self,
        work_id: str,
        locator: Mapping[str, object] | Locator | None,
        axes: Iterable[str],
        why: str,
        source: str | None = None,
        observation_id: str | None = None,
    ) -> None:
        loc_plain: dict[str, object]
        if isinstance(locator, Locator):
            loc_plain = {k: v for k, v in to_plain(locator).items() if v is not None}  # type: ignore[union-attr]
        elif isinstance(locator, Mapping):
            loc_plain = dict(locator)
        else:
            loc_plain = {}
        self.queue.append(
            QueueEntry(
                work_id=work_id,
                locator=loc_plain,
                axes=list(axes),
                why=why,
                source=source,
                observation_id=observation_id,
            )
        )


# ---------------------------------------------------------------------------
# Locator / equipment / pressure conversions
# ---------------------------------------------------------------------------


def locator_from_mapping(raw: object, fallback: str | None = None) -> Locator | None:
    if not isinstance(raw, Mapping):
        if fallback:
            return Locator(note=fallback)
        return None
    kwargs: dict[str, object] = {}
    extras: list[str] = []
    for key, value in raw.items():
        name = str(key)
        if value is None or value == "":
            continue
        if name not in LOCATOR_FIELDS:
            extras.append(f"{name}={value}")
            continue
        if name == "pdf_page_index":
            try:
                kwargs[name] = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                extras.append(f"{name}={value}")
            continue
        kwargs[name] = value
    if extras:
        extra_note = "; ".join(extras)
        existing = str(kwargs.get("note") or "")
        kwargs["note"] = f"{existing}; {extra_note}".strip("; ")
    if not kwargs:
        if fallback:
            return Locator(note=fallback)
        return None
    try:
        return Locator(**kwargs)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Locator(note=fallback or "unparsed locator")


def located_unknown(reason: str) -> Located[Any]:
    return Located(State.unknown(reason))


def located_value(value: object, locator: Locator | None) -> Located[Any]:
    return Located(State.of(value), locator=locator)


def torr_to_pa(pressure_torr: object) -> Decimal:
    """P_Pa = P_Torr × 101325 / 760 (CGPM 1954 atmosphere)."""

    return as_decimal(pressure_torr) * PA_PER_TORR


def convert_pressure_to_pa(
    value: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    """Return (Pa, conversion relation name) or (None, why)."""

    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "pressure value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing pressure unit"
    unit = str(units).strip()
    lowered = unit.lower().replace(" ", "")
    if lowered in {"pa", "pascal", "pascals"}:
        return amount, "identity:Pa"
    if lowered in {"kpa"}:
        # Premise: 1 kPa = 1000 Pa exactly (SI).
        return amount * Decimal("1000"), "kPa_to_Pa"
    if lowered in {"bar"}:
        return bar_to_pa(amount), "bar_to_Pa"
    if lowered in {"atm", "atmosphere", "atmospheres"}:
        return atm_to_pa(amount), "atm_to_Pa"
    if lowered in {"torr", "mmhg", "mmhg"}:
        return torr_to_pa(amount), "Torr_to_Pa"
    if lowered in {"mbar", "millibar"}:
        # Premise: 1 mbar = 100 Pa = 0.001 bar (IUPAC bar = 1e5 Pa).
        return amount * Decimal("100"), "mbar_to_Pa"
    return None, f"unmapped pressure unit {unit!r}"


def convert_temperature_to_k(
    value: object, units: str | None = None
) -> tuple[Decimal | None, str | None]:
    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "temperature value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing temperature unit"
    unit = str(units).strip().lower()
    if unit in {"k", "kelvin"}:
        return amount, "identity:K"
    if unit in {"c", "celsius", "degc", "°c"}:
        return celsius_to_kelvin(amount), "celsius_to_kelvin"
    return None, f"unmapped temperature unit {units!r}"


def convert_area_to_m2(
    value: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "area value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing area unit"
    lowered = str(units).strip().lower().replace(" ", "")
    if lowered in {"m2", "m^2", "m²"}:
        return amount, "identity:m2"
    if lowered in {"cm2", "cm^2", "cm²"}:
        # Premise: 1 m² = 10⁴ cm² exactly (SI).
        return amount / Decimal("10000"), "cm2_to_m2"
    if lowered in {"mm2", "mm^2", "mm²"}:
        return amount / Decimal("1000000"), "mm2_to_m2"
    return None, f"unmapped area unit {units!r}"


def convert_mass_to_kg(
    value: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "mass value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing mass unit"
    lowered = str(units).strip().lower().replace(" ", "")
    if lowered in {"kg"}:
        return amount, "identity:kg"
    if lowered in {"g", "gram", "grams"}:
        return amount / Decimal("1000"), "g_to_kg"
    if lowered in {"mg"}:
        return amount / Decimal("1000000"), "mg_to_kg"
    return None, f"unmapped mass unit {units!r}"


def convert_length_to_m(
    value: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "length value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing length unit"
    lowered = str(units).strip().lower().replace(" ", "")
    if lowered in {"m", "meter", "metre", "meters", "metres"}:
        return amount, "identity:m"
    if lowered in {"cm"}:
        # Premise: 1 m = 100 cm exactly (SI).
        return amount / Decimal("100"), "cm_to_m"
    if lowered in {"mm"}:
        return amount / Decimal("1000"), "mm_to_m"
    return None, f"unmapped length unit {units!r}"


def convert_volumetric_flow_to_m3_s(
    value: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    """Return (m³/s, conversion relation name) or (None, why).

    Premise: 1 L = 1 dm³ = 10⁻³ m³ exactly (SI litre).
    """

    amount = _as_dec_or_none(value)
    if amount is None:
        return None, "volumetric-flow value is not numeric"
    if units is None or not str(units).strip():
        return None, "missing volumetric-flow unit"
    lowered = (
        str(units)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("³", "3")
        .replace("^3", "3")
    )
    if lowered in {"m3/s", "m3s", "m3_s", "m^3/s", "m3s-1", "m3s^-1"}:
        return amount, "identity:m3/s"
    if lowered in {"l/s", "l_s", "ls", "liter/s", "litre/s", "l/sec"}:
        return amount / Decimal("1000"), "L_s_to_m3_s"
    return None, f"unmapped volumetric-flow unit {units!r}"


# trail -> (factor, arithmetic, output_unit, original_unit)
_CONVERSION_META: dict[str, tuple[Decimal, str, str, str]] = {
    "Torr_to_Pa": (
        PA_PER_TORR,
        "P_Pa = P_Torr × 101325 / 760",
        "Pa",
        "Torr",
    ),
    "atm_to_Pa": (
        Decimal(str(int(STANDARD_ATMOSPHERE_PA))),
        "P_Pa = P_atm × 101325",
        "Pa",
        "atm",
    ),
    "bar_to_Pa": (Decimal("100000"), "P_Pa = P_bar × 1e5", "Pa", "bar"),
    "kPa_to_Pa": (Decimal("1000"), "P_Pa = P_kPa × 1000", "Pa", "kPa"),
    "mbar_to_Pa": (Decimal("100"), "P_Pa = P_mbar × 100", "Pa", "mbar"),
    "celsius_to_kelvin": (
        Decimal("273.15"),
        "T_K = T_C + 273.15",
        "K",
        "C",
    ),
    "cm2_to_m2": (Decimal("10000"), "A_m2 = A_cm2 / 10000", "m2", "cm2"),
    "mm2_to_m2": (Decimal("1000000"), "A_m2 = A_mm2 / 1e6", "m2", "mm2"),
    "g_to_kg": (Decimal("1000"), "m_kg = m_g / 1000", "kg", "g"),
    "mg_to_kg": (Decimal("1000000"), "m_kg = m_mg / 1e6", "kg", "mg"),
    "cm_to_m": (Decimal("100"), "L_m = L_cm / 100", "m", "cm"),
    "mm_to_m": (Decimal("1000"), "L_m = L_mm / 1000", "m", "mm"),
    "L_s_to_m3_s": (
        Decimal("1000"),
        "Q_m3_s = Q_L_s / 1000",
        "m3/s",
        "L/s",
    ),
    "min_to_s": (Decimal("60"), "t_s = t_min × 60", "s", "min"),
    "h_to_s": (Decimal("3600"), "t_s = t_h × 3600", "s", "h"),
    "ks_to_s": (Decimal("1000"), "t_s = t_ks × 1000", "s", "ks"),
    "C_per_min_to_K_per_s": (
        Decimal("60"),
        "rate_K_s = rate_C_per_min / 60",
        "K/s",
        "C/min",
    ),
    "cm3_to_m3": (
        Decimal("1000000"),
        "V_m3 = V_cm3 / 1e6",
        "m3",
        "cm3",
    ),
    "MPa_to_Pa": (
        Decimal("1000000"),
        "P_Pa = P_MPa × 1e6",
        "Pa",
        "MPa",
    ),
    "percent_to_fraction": (
        Decimal("100"),
        "x = pct / 100",
        "dimensionless",
        "percent",
    ),
}


_OXIDE_COMPONENT_KEYS = frozenset(
    {
        "SiO2",
        "TiO2",
        "Al2O3",
        "FeO",
        "Fe2O3",
        "MgO",
        "CaO",
        "Na2O",
        "K2O",
        "Cr2O3",
        "MnO",
        "P2O5",
        "NiO",
        "CoO",
    }
)
_PRINTED_COMPOSITION_MAP_KEYS = (
    "oxides_wt_pct",
    "major_oxide_wt_pct",
    "composition_wt_pct",
    "sample_oxide_composition_wt_pct",
    "starting_glass_wt_pct",
    "printed_composition",
)
_CHARGE_PRINTED_COMPOSITION_NAMES = frozenset(
    {
        "printed_composition",
        "starting_glass_wt_pct",
        "sample_oxide_composition_wt_pct",
    }
)
_SAMPLE_CODE_FORMULA_RE = re.compile(r"(?i)^(MLS[-_]?|MS)\d")
_BULK_PROPERTY_QUANTITIES = frozenset(
    {
        Quantity.MASS_LOSS_FRACTION,
        Quantity.MASS_LOSS_FRACTION_VS_T,
        Quantity.YIELD_FRACTION,
        Quantity.O2_YIELD,
        Quantity.EVOLVED_GAS_YIELD,
        Quantity.MASS_LOSS_RATE,
    }
)
_COMPOSITION_LOOKED_FOR = (
    "oxides_wt_pct / major_oxide_wt_pct / composition_wt_pct / "
    "sample_oxide_composition_wt_pct / starting_glass_wt_pct / "
    "SiO2 Al2O3 FeO Fe2O3 MgO CaO Na2O K2O TiO2 MnO P2O5"
)
_WT_PCT_TO_MOLE_FRACTION_ARITHMETIC = "x_i = (w_i / M_i) / Σ_j (w_j / M_j)"


def oxide_molar_mass(oxide: str) -> Decimal:
    """Molar mass (g/mol) from the CIAAW/NIST atomic-weight table."""

    from simulator.state import MOLAR_MASS

    if oxide in MOLAR_MASS:
        return as_decimal(str(MOLAR_MASS[oxide]))
    from simulator.accounting.formulas import parse_formula

    return as_decimal(str(parse_formula(oxide, species=oxide).molar_mass_g_mol))


def wt_pct_to_mole_fraction(wt: Mapping[str, Decimal]) -> Composition:
    """Convert a printed oxide wt% map to mole fraction. Does not renormalize wt%."""

    moles: list[tuple[str, Decimal]] = []
    total = Decimal("0")
    for oxide, weight in wt.items():
        name = str(oxide)
        if name not in _OXIDE_COMPONENT_KEYS:
            continue
        amount = as_decimal(weight)
        if amount < 0:
            raise ValueError(f"oxide {name!r} wt% is negative")
        n = amount / oxide_molar_mass(name)
        moles.append((name, n))
        total += n
    if total <= 0:
        raise ValueError("printed oxide wt% map has no positive mass")
    return Composition(
        basis="printed_oxides",
        components=tuple((oxide, n / total) for oxide, n in moles),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


def is_sample_code_formula(text: str) -> bool:
    return bool(_SAMPLE_CODE_FORMULA_RE.match(str(text).strip()))


def _oxide_map_from_mapping(obj: object) -> dict[str, Decimal] | None:
    if not isinstance(obj, Mapping):
        return None
    for key in _PRINTED_COMPOSITION_MAP_KEYS:
        nested = obj.get(key)
        if isinstance(nested, Mapping):
            got = _oxide_map_from_mapping(nested)
            if got:
                return got
    comps: dict[str, Decimal] = {}
    for key, value in obj.items():
        name = str(key)
        if name not in _OXIDE_COMPONENT_KEYS:
            continue
        amount = _as_dec_or_none(value)
        if amount is None:
            continue
        comps[name] = amount
    if len(comps) < 2:
        return None
    return comps


def _initial_oxide_map_from_values(
    values: object,
) -> dict[str, Decimal] | None:
    if not isinstance(values, Mapping):
        return None
    points = values.get("points")
    if not isinstance(points, list):
        points = values.get("tests")
    if isinstance(points, list):
        ranked: list[Mapping[str, Any]] = [
            item for item in points if isinstance(item, Mapping)
        ]
        for item in ranked:
            if item.get("T_C_is_initial_composition") is True:
                got = _oxide_map_from_mapping(item)
                if got:
                    return got
        for item in ranked:
            t_c = _as_dec_or_none(item.get("T_C"))
            loss = _as_dec_or_none(
                item.get("mass_loss_pct") or item.get("mass_loss_wt_pct")
            )
            if t_c == 0 and loss == 0:
                got = _oxide_map_from_mapping(item)
                if got:
                    return got
        for item in ranked:
            got = _oxide_map_from_mapping(item)
            if got:
                return got
    return _oxide_map_from_mapping(values)


def _printed_map_payload(wt: Mapping[str, Decimal]) -> dict[str, str]:
    return {str(k): _dec_str(as_decimal(v)) for k, v in wt.items()}


def wt_pct_to_mole_fraction_derivation(
    wt: Mapping[str, Decimal],
    locator: Locator | None,
) -> Derivation:
    params: list[tuple[str, Located[Decimal]]] = []
    for oxide, weight in wt.items():
        params.append(
            (
                f"original_{oxide}_wt_pct",
                Located(State.of(as_decimal(weight)), locator=locator),
            )
        )
        params.append(
            (
                f"M_{oxide}_g_mol",
                Located(State.of(oxide_molar_mass(oxide)), locator=locator),
            )
        )
    return Derivation(
        relation="wt_pct_to_mole_fraction",
        inputs=(_WT_PCT_TO_MOLE_FRACTION_ARITHMETIC, "original_unit=wt_pct"),
        parameters=tuple(params),
        output_unit="mole_fraction",
    )


def composition_unknown_reason() -> str:
    return f"no composition field under keys {_COMPOSITION_LOOKED_FOR} in this extract"


def bulk_property_species_formula(
    *,
    quantity: Quantity | None,
    parent_formula: str,
    sample_label: str | None,
    oxide_map: Mapping[str, Decimal] | None,
) -> str:
    """Species formula for a bulk-property row.

    A multi-oxide map is a mixture: no single formula is meaningful.
    Sample codes (MLS-*, MS[0-9]) are not chemical formulas. A sample
    that *is* ilmenite / enstatite / silica / O2 keeps that formula.
    """

    parent = str(parent_formula or "").strip() or "unknown"
    if quantity in {Quantity.O2_YIELD, Quantity.YIELD_FRACTION, Quantity.EVOLVED_GAS_YIELD}:
        if parent in {"O2", "O2(g)"}:
            return parent
    if oxide_map and len(oxide_map) >= 2:
        return "unknown"
    label = str(sample_label or "").strip()
    if label:
        if is_sample_code_formula(label):
            return "unknown"
        return label
    if is_sample_code_formula(parent):
        return "unknown"
    return parent


def _located_printed_and_initial(
    wt: Mapping[str, Decimal] | None,
    locator: Locator | None,
) -> tuple[Located[Mapping[str, Any]] | None, Located[Composition] | None]:
    if not wt or len(wt) < 2:
        return None, None
    printed = located_value(_printed_map_payload(wt), locator)
    initial = Located(
        State.of(wt_pct_to_mole_fraction(wt)),
        locator=locator,
        inference=wt_pct_to_mole_fraction_derivation(wt, locator),
    )
    return printed, initial


def conversion_derivation(
    trail: str | None,
    original_value: object,
    locator: Locator | None,
) -> Derivation | None:
    """Attach original unit, factor, and arithmetic on a unit conversion."""

    if not trail or str(trail).startswith("identity"):
        return None
    meta = _CONVERSION_META.get(str(trail))
    if meta is None:
        return None
    factor, arithmetic, output_unit, original_unit = meta
    params: list[tuple[str, Located[Decimal]]] = [
        ("factor", Located(State.of(factor), locator=locator)),
    ]
    original = _as_dec_or_none(original_value)
    if original is not None:
        params.insert(0, ("original", Located(State.of(original), locator=locator)))
    return Derivation(
        relation=str(trail),
        inputs=(arithmetic, f"original_unit={original_unit}"),
        parameters=tuple(params),
        output_unit=output_unit,
    )


def _converted_temperature(
    amount: Decimal,
    trail: str | None,
    original: object,
    locator: Locator | None,
) -> Located[Decimal]:
    """Kelvin point condition. A unit conversion keeps its printed original."""

    return Located(
        State.of(amount),
        locator=locator,
        inference=conversion_derivation(trail, original, locator),
    )


def _temperature_field_raw(payload: Mapping[str, Any], field_name: str | None) -> object:
    if not field_name:
        return None
    raw = payload.get(field_name)
    if isinstance(raw, Mapping):
        return raw.get("value")
    return raw


# Extractor-written Kelvin sitting next to the printed Celsius. A field named
# as published is the printed Kelvin and is not a twin.
_TWIN_KELVIN_KEYS = frozenset({"T_K", "temperature_K", "Tmax_K"})


def _prefer_printed_temperature(
    found: list[tuple[str, object, Decimal, str]],
) -> tuple[str, object, Decimal, str]:
    """Prefer printed Celsius when its conversion equals the Kelvin twin.

    The landed number is identical either way. A twin that does not match
    keeps the historical first-key choice, so a +273 convention is not
    rewritten as +273.15.
    """

    celsius = [item for item in found if item[3] == "celsius_to_kelvin"]
    for c_item in celsius:
        for k_item in found:
            if k_item[0] in _TWIN_KELVIN_KEYS and k_item[2] == c_item[2]:
                return c_item
    return found[0]


# Printed-unit recovery for a Located whose note (or as_published block)
# records a conversion. The stored magnitude is never replaced.
_PRINTED_WORD_NUMBERS = {"one": "1", "two": "2", "three": "3", "six": "6"}
_PRINTED_SCI = (
    r"(?:~?\s*\d+(?:\.\d+)?\s*[x×]\s*10\s*\^\s*\{?\s*[+-]?\d+\s*\}?"
    r"|~?\s*\d+(?:\.\d+)?[eE][+-]?\d+"
    r"|~?\s*10\s*\^\s*\{?\s*[+-]?\d+\s*\}?)"
)
_PRINTED_NUM = rf"(?:{_PRINTED_SCI}|~?\s*\d+(?:\.\d+)?)"
_PRINTED_UNIT = (
    r"C/min|°C|degC|(?<![A-Za-z])C(?![A-Za-z])|Torr|torr|mmHg|"
    r"atmospheres|atmosphere|atm|mbar|millibar|MPa|bars|bar|mm|cm3|cm³|cm|"
    r"mg|kg|grams|gram|g|ks|minutes|minute|min|hours|hour|hr|h|"
    r"seconds|second|sec|s|L/s|Liter\s+s-1"
)
_CONVERSION_NOTE_RE = re.compile(r"unit conversion|\bconverted\b", re.IGNORECASE)
_NOT_A_CONVERSION_RE = re.compile(r"no unit conversion", re.IGNORECASE)


def _agrees_stored(converted: Decimal, stored: Decimal) -> bool:
    """True when converted is the stored number, or its rounding."""

    if converted == stored:
        return True
    exponent = stored.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    quantum = Decimal(1).scaleb(exponent)
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[InvalidOperation] = False
        rounded = converted.quantize(quantum)
    return bool(rounded.is_finite() and rounded == stored)


def _normalize_printed_unit(unit: str) -> str:
    token = unit.strip().lower().replace("³", "3")
    token = re.sub(r"\s+", "", token)
    return {
        "c": "C",
        "°c": "C",
        "degc": "C",
        "c/min": "C/min",
        "torr": "Torr",
        "mmhg": "Torr",
        "atm": "atm",
        "atmosphere": "atm",
        "atmospheres": "atm",
        "bar": "bar",
        "bars": "bar",
        "mbar": "mbar",
        "millibar": "mbar",
        "mpa": "MPa",
        "mm": "mm",
        "cm": "cm",
        "cm3": "cm3",
        "mg": "mg",
        "g": "g",
        "gram": "g",
        "grams": "g",
        "kg": "kg",
        "s": "s",
        "sec": "s",
        "second": "s",
        "seconds": "s",
        "min": "min",
        "minute": "min",
        "minutes": "min",
        "h": "h",
        "hr": "h",
        "hour": "h",
        "hours": "h",
        "ks": "ks",
        "l/s": "L/s",
        "liters-1": "L/s",
    }.get(token, unit)


def _convert_printed_amount(amount: Decimal, unit: str) -> tuple[Decimal, str] | None:
    """Return (stored-unit amount, trail) at high precision, or None."""

    normalized = _normalize_printed_unit(unit)
    with localcontext() as ctx:
        ctx.prec = 80
        if normalized == "C":
            return amount + Decimal("273.15"), "celsius_to_kelvin"
        if normalized == "C/min":
            return amount / Decimal(60), "C_per_min_to_K_per_s"
        if normalized == "Torr":
            return amount * Decimal(101325) / Decimal(760), "Torr_to_Pa"
        if normalized == "atm":
            return amount * Decimal(101325), "atm_to_Pa"
        if normalized == "bar":
            return amount * Decimal(100000), "bar_to_Pa"
        if normalized == "mbar":
            return amount * Decimal(100), "mbar_to_Pa"
        if normalized == "MPa":
            return amount * Decimal(1000000), "MPa_to_Pa"
        if normalized == "mm":
            return amount / Decimal(1000), "mm_to_m"
        if normalized == "cm":
            return amount / Decimal(100), "cm_to_m"
        if normalized == "cm3":
            return amount / Decimal(1000000), "cm3_to_m3"
        if normalized == "mg":
            return amount / Decimal(1000000), "mg_to_kg"
        if normalized == "g":
            return amount / Decimal(1000), "g_to_kg"
        if normalized == "kg":
            return amount, "identity:kg"
        if normalized == "s":
            return amount, "identity:s"
        if normalized == "min":
            return amount * Decimal(60), "min_to_s"
        if normalized == "h":
            return amount * Decimal(3600), "h_to_s"
        if normalized == "ks":
            return amount * Decimal(1000), "ks_to_s"
        if normalized == "L/s":
            return amount / Decimal(1000), "L_s_to_m3_s"
    return None


def _sci_to_decimal(text: str) -> Decimal:
    token = (
        text.strip()
        .lstrip("~")
        .replace(" ", "")
        .replace("×", "x")
        .replace("{", "")
        .replace("}", "")
    )
    powered = re.fullmatch(r"(\d+(?:\.\d+)?)x10\^([+-]?\d+)", token, re.IGNORECASE)
    if powered:
        return Decimal(powered.group(1)) * (Decimal(10) ** int(powered.group(2)))
    bare = re.fullmatch(r"10\^([+-]?\d+)", token, re.IGNORECASE)
    if bare:
        return Decimal(10) ** int(bare.group(1))
    return Decimal(token)


def _prepare_conversion_note(note: str) -> str:
    text = re.sub(
        r"\b(one|two|three|six)\b",
        lambda match: _PRINTED_WORD_NUMBERS[match.group(1).lower()],
        note,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(\d)\s*-\s*(hour|hours)\b", r"\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\d+(?:\.\d+)?\s*\+/-\s*\d+(?:\.\d+)?",
        lambda match: match.group(0).split("+/-")[0],
        text,
    )
    # A stated factor ("1 Torr = 101325/760 Pa") is not an operand.
    return re.sub(r",?\s*\d+(?:\.\d+)?\s*[A-Za-z°/]+\s*=\s*[^.;]+", " ", text)


def _parse_printed_operands(note: str) -> list[tuple[Decimal, str]] | None:
    """Printed (amount, unit) operands, or a clock as h/min/s. None if unread."""

    if _NOT_A_CONVERSION_RE.search(note):
        return None
    text = _prepare_conversion_note(note)
    clock = re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", text)
    if clock:
        hours, minutes, seconds = (Decimal(clock.group(i)) for i in (1, 2, 3))
        return [(hours, "h"), (minutes, "min"), (seconds, "s")]
    shared = re.search(
        rf"({_PRINTED_NUM})\s*(?:-|to|and)\s*({_PRINTED_NUM})\s*[x×]\s*10\s*\^\s*"
        rf"{{?\s*([+-]?\d+)\s*}}?\s*({_PRINTED_UNIT})\b",
        text,
        re.IGNORECASE,
    )
    if shared:
        exponent = int(shared.group(3))
        unit = shared.group(4)
        scale = Decimal(10) ** exponent
        return [
            (_sci_to_decimal(shared.group(1)) * scale, unit),
            (_sci_to_decimal(shared.group(2)) * scale, unit),
        ]
    ranged = re.search(
        rf"({_PRINTED_NUM})\s*(?:-|to|and)\s*({_PRINTED_NUM})\s*-?\s*({_PRINTED_UNIT})\b",
        text,
        re.IGNORECASE,
    )
    if ranged:
        unit = ranged.group(3)
        return [
            (_sci_to_decimal(ranged.group(1)), unit),
            (_sci_to_decimal(ranged.group(2)), unit),
        ]
    spans = [
        (_sci_to_decimal(match.group(1)), match.group(2))
        for match in re.finditer(
            rf"({_PRINTED_NUM})\s*-?\s*({_PRINTED_UNIT})\b", text, re.IGNORECASE
        )
    ]
    if not spans:
        return None
    return spans[:2]


def _stored_magnitudes(raw: object) -> tuple[str, list[Decimal], str | None] | None:
    """(kind, magnitudes, bound operator) from a Located state value."""

    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (str, int, float, Decimal)):
        try:
            return "point", [Decimal(str(raw))], None
        except (ArithmeticError, ValueError):
            return None
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    try:
        if kind == "point":
            return "point", [Decimal(str(raw["point"]))], None
        if kind == "interval":
            return "interval", [
                Decimal(str(raw["interval_low"])),
                Decimal(str(raw["interval_high"])),
            ], None
        if kind == "bound":
            return "bound", [Decimal(str(raw["bound_value"]))], (
                str(raw.get("bound_operator")) if raw.get("bound_operator") else None
            )
    except (KeyError, ArithmeticError, ValueError):
        return None
    return None


def _note_bound_operator(note: str) -> str | None:
    if re.search(r"up to|<=|≤", note, re.IGNORECASE):
        return "<="
    if re.search(r"<|>|below|less than", note, re.IGNORECASE):
        return "<"
    return None


def _clock_derivation(
    operands: list[tuple[Decimal, str]], locator: Locator | None
) -> Derivation:
    hours, minutes, seconds = (item[0] for item in operands)
    return Derivation(
        relation="hms_to_s",
        inputs=("t_s = 3600*h + 60*m + s", "original_unit=h:m:s"),
        parameters=(
            ("hours", Located(State.of(hours), locator=locator)),
            ("minutes", Located(State.of(minutes), locator=locator)),
            ("seconds", Located(State.of(seconds), locator=locator)),
        ),
        output_unit="s",
    )


def _interval_derivation(
    operands: list[tuple[Decimal, str]],
    trails: list[str],
    locator: Locator | None,
) -> Derivation | None:
    if any(trail.startswith("identity") for trail in trails):
        return None
    metas = [_CONVERSION_META.get(trail) for trail in trails]
    if any(meta is None for meta in metas):
        return None
    if trails[0] == trails[1]:
        factor, arithmetic, output_unit, original_unit = metas[0]
        return Derivation(
            relation=trails[0],
            inputs=(arithmetic, f"original_unit={original_unit}"),
            parameters=(
                ("original_low", Located(State.of(operands[0][0]), locator=locator)),
                ("original_high", Located(State.of(operands[1][0]), locator=locator)),
                ("factor", Located(State.of(factor), locator=locator)),
            ),
            output_unit=output_unit,
        )
    inputs: list[str] = []
    parameters: list[tuple[str, Located[Decimal]]] = []
    output_unit = None
    for label, operand, meta in zip(("low", "high"), operands, metas):
        factor, arithmetic, output_unit, original_unit = meta
        inputs.append(arithmetic)
        inputs.append(f"original_unit_{label}={original_unit}")
        parameters.append((f"original_{label}", Located(State.of(operand[0]), locator=locator)))
        parameters.append((f"factor_{label}", Located(State.of(factor), locator=locator)))
    return Derivation(
        relation=f"{trails[0]}+{trails[1]}",
        inputs=tuple(inputs),
        parameters=tuple(parameters),
        output_unit=output_unit or "s",
    )


def _derivation_for_operands(
    operands: list[tuple[Decimal, str]],
    kind: str,
    locator: Locator | None,
) -> Derivation | None:
    if len(operands) == 3 and [unit for _, unit in operands] == ["h", "min", "s"]:
        return _clock_derivation(operands, locator)
    converted: list[tuple[Decimal, str]] = []
    for amount, unit in operands:
        got = _convert_printed_amount(amount, unit)
        if got is None:
            return None
        converted.append(got)
    trails = [trail for _, trail in converted]
    if kind == "interval":
        if len(operands) != 2:
            return None
        return _interval_derivation(operands, trails, locator)
    if len(operands) != 1:
        return None
    if trails[0].startswith("identity"):
        return None
    return conversion_derivation(trails[0], operands[0][0], locator)


def _operands_match_stored(
    operands: list[tuple[Decimal, str]],
    kind: str,
    stored: list[Decimal],
) -> list[tuple[Decimal, str]] | None:
    """Operands whose conversion reproduces the stored magnitudes, in order."""

    if kind == "interval":
        if len(operands) != 2 or len(stored) != 2:
            return None
        converted = []
        for amount, unit in operands:
            got = _convert_printed_amount(amount, unit)
            if got is None or not _agrees_stored(got[0], stored[len(converted)]):
                return None
            converted.append(got[0])
        return operands
    if len(stored) != 1:
        return None
    matching = []
    for amount, unit in operands:
        got = _convert_printed_amount(amount, unit)
        if got is not None and _agrees_stored(got[0], stored[0]):
            matching.append((amount, unit))
    if len(matching) != 1:
        return None
    return matching


def _published_operands(published: Mapping[str, Any]) -> list[tuple[Decimal, str]] | None:
    units = published.get("units")
    if published.get("low") is not None and published.get("high") is not None:
        low_unit = published.get("low_units") or units
        high_unit = published.get("high_units") or units
        if not low_unit or not high_unit:
            return None
        return [
            (Decimal(str(published["low"])), str(low_unit)),
            (Decimal(str(published["high"])), str(high_unit)),
        ]
    if published.get("value") is not None and units:
        return [(Decimal(str(published["value"])), str(units))]
    return None


def _inference_for_printed_conversion(payload: Mapping[str, Any]) -> Derivation | None:
    """Derivation for a converted Located. None when the printed original is not verified.

    A structured ``as_published`` block that does not reproduce the stored
    number is refused. A prose note that does not reproduce it is left
    printed: the note is not a machine claim we can safely complete.
    """

    state = payload.get("state")
    raw_value = state.get("value") if isinstance(state, Mapping) else None
    parsed = _stored_magnitudes(raw_value)
    if parsed is None:
        if isinstance(payload.get("as_published"), Mapping):
            raise ValueError(
                "as_published conversion does not apply to a non-numeric located value"
            )
        return None
    kind, stored, operator = parsed
    locator = _locator_from_plain(payload.get("locator"))
    published = payload.get("as_published")
    if isinstance(published, Mapping) and (
        published.get("units") or published.get("low_units") or published.get("value")
        or published.get("low")
    ):
        operands = _published_operands(published)
        if operands is None or _operands_match_stored(operands, kind, stored) is None:
            raise ValueError(
                "as_published conversion does not reproduce the stored value"
            )
        return _derivation_for_operands(operands, kind, locator)
    locator_raw = payload.get("locator")
    note = ""
    if isinstance(locator_raw, Mapping):
        note = str(locator_raw.get("note") or "")
    if not note or not _CONVERSION_NOTE_RE.search(note) or _NOT_A_CONVERSION_RE.search(note):
        return None
    if kind == "bound":
        claimed = _note_bound_operator(note)
        if claimed is None or claimed != operator:
            return None
    operands = _parse_printed_operands(note)
    if not operands:
        return None
    # A clock is three fields, not one magnitude. Check the sum.
    if len(operands) == 3 and [unit for _, unit in operands] == ["h", "min", "s"]:
        if kind != "point":
            return None
        total = operands[0][0] * 3600 + operands[1][0] * 60 + operands[2][0]
        if not _agrees_stored(total, stored[0]):
            return None
        return _clock_derivation(operands, locator)
    matched = _operands_match_stored(operands, kind, stored)
    if matched is None:
        return None
    return _derivation_for_operands(matched, kind, locator)


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def unknown_pressure_environment(reason: str) -> PressureEnvironment:
    """Unknown pressure payload. ``reason`` must name what was looked for."""

    if not reason:
        raise ValueError("unknown_pressure_environment requires a reason")
    return PressureEnvironment(
        total_pressure_Pa=located_unknown(reason),
        sweep_gas=located_unknown(
            "no sweep-gas field under keys sweep_gas / carrier_gas / buffer_gas in this extract"
        ),
        regime=FlowRegime(
            regime_class=State.unknown(
                "no flow-regime field under keys regime_class / knudsen_number_orifice "
                "/ knudsen_number_chamber in this extract"
            )
        ),
    )


def map_phase(raw: object) -> tuple[State[Phase], str | None]:
    if raw is None or raw == "":
        return State.unknown("source does not state phase"), "missing phase"
    text = str(raw).strip()
    if not text:
        return State.unknown("source does not state phase"), "missing phase"
    mapped = PHASE_MAP.get(text) or PHASE_MAP.get(text.lower())
    if mapped is not None:
        return State.of(mapped), None
    return (
        State.unknown(f"phase string {text!r} is not in the closed automatic map"),
        text,
    )


def _printed_field_text(raw: object) -> str | None:
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    if isinstance(raw, (list, tuple)):
        parts = [_printed_field_text(item) for item in raw]
        text = "; ".join(part for part in parts if part)
        return text or None
    if isinstance(raw, Mapping):
        parts = [_printed_field_text(item) for item in raw.values()]
        text = "; ".join(part for part in parts if part)
        return text or None
    return None


def compilation_phase_text(doc: Mapping[str, Any]) -> object:
    """First nonempty printed phase field. Empty string is absence, not a token."""

    for key in ("phase", "phase_as_published"):
        raw = doc.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
        if raw not in (None, "", [], {}):
            return raw
    raw = doc.get("phase_state_as_published")
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if x not in (None, "")]
        if parts:
            return " ".join(parts)
    if isinstance(raw, str) and raw.strip():
        return raw
    raw = doc.get("state_note_as_published")
    if isinstance(raw, str) and raw.strip():
        return raw
    rows = doc.get("rows")
    if isinstance(rows, list):
        phases = list(dict.fromkeys(
            str(row["phase_as_published"]).strip()
            for row in rows if isinstance(row, Mapping) and row.get("phase_as_published")
        ))
        if phases:
            return "multi-phase table: " + "; ".join(phases)
    return None


_TRANSITION_PHASES = {
    "melting_point": "solid -> liquid",
    "normal_boiling_point": "liquid -> gas",
    "triple_point_temperature": "solid / liquid / gas coexistence",
    "solid_solid_transition": "solid -> solid",
}


def transition_phase_reason(
    obs_type: str | None,
    values: Mapping[str, Any],
) -> str | None:
    if obs_type != "transition_point":
        return None
    kind = values.get("property_kind")
    if not isinstance(kind, str) or not kind:
        return None
    phase_from = _printed_field_text(values.get("phase_from"))
    phase_to = _printed_field_text(values.get("phase_to"))
    if phase_from and phase_to:
        statement = (
            f"source states transition property_kind {kind!r} and phases "
            f"{phase_from!r} -> {phase_to!r} in values.phase_from/phase_to"
        )
    else:
        pair = _TRANSITION_PHASES.get(kind)
        if pair is None:
            return None
        statement = (
            f"source states transition property_kind {kind!r} "
            f"({pair}) in values.property_kind"
        )
    citation = values.get("citation")
    if isinstance(citation, Mapping):
        citation = citation.get("primary")
    citation_text = _printed_field_text(citation)
    if citation_text:
        statement += f"; citation.primary prints {citation_text!r}"
    return (
        statement
        + "; v2.1 species.phase has one phase axis and cannot hold a transition's phases"
    )


# A source field that uniquely names one closed quantity. Pressure columns are
# omitted: they do not distinguish p_sat from p_partial.
_UNIQUE_QUANTITY_FIELDS: dict[str, Quantity] = {
    "activity": Quantity.ACTIVITY,
    "activity_coefficient": Quantity.ACTIVITY_COEFFICIENT,
    "gamma": Quantity.ACTIVITY_COEFFICIENT,
    "alpha": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "delta_fG": Quantity.DELTA_FG,
    "delta_fG_298_kJ_mol": Quantity.DELTA_FG,
    "Delta_f_G_298_kJ_mol": Quantity.DELTA_FG,
    "deltafG": Quantity.DELTA_FG,
    "delta_fG_kJ_mol": Quantity.DELTA_FG,
    "table_kJ_mol": Quantity.DELTA_FG,
    "Gf": Quantity.DELTA_FG,
    "formation_gibbs_energy": Quantity.DELTA_FG,
    "delta_f_H_298_15": Quantity.DELTA_FH,
    "delta_f_H": Quantity.DELTA_FH,
    "delta_fH": Quantity.DELTA_FH,
    "deltafH": Quantity.DELTA_FH,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_enthalpy_298_15_K_as_published": Quantity.DELTA_FH,
    "log10_Kf": Quantity.LOG10_KF,
    "log10_kf": Quantity.LOG10_KF,
    "log10_formation_equilibrium_constant": Quantity.LOG10_KF,
}


def _quantities_named_by_payload(
    values: Mapping[str, Any] | None,
) -> frozenset[Quantity]:
    if not isinstance(values, Mapping):
        return frozenset()
    named: set[Quantity] = set()
    for key, quantity in _UNIQUE_QUANTITY_FIELDS.items():
        if key not in values:
            continue
        raw = values.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, Mapping) and raw.get("value") is None and "value" in raw:
            continue
        named.add(quantity)
    return frozenset(named)


def _row_text_blob(
    obs_type: str | None,
    values: Mapping[str, Any] | None,
    units: str | None,
    row: Mapping[str, Any] | None,
) -> str:
    parts: list[str] = [str(obs_type or ""), str(units or "")]
    for src in (values, row):
        if not isinstance(src, Mapping):
            continue
        for key in (
            "note",
            "semantics",
            "standard_state",
            "regime",
            "quote",
            "header_quote",
            "cell_quote",
            "method",
            "form",
            "alpha_note",
        ):
            raw = src.get(key)
            if raw not in (None, ""):
                parts.append(str(raw))
        loc = src.get("locator")
        if isinstance(loc, Mapping):
            for loc_key in ("note", "paragraph", "section"):
                if loc.get(loc_key):
                    parts.append(str(loc[loc_key]))
    return " ".join(parts).lower()


def _alpha_numeric(values: Mapping[str, Any] | None) -> Decimal | None:
    if not isinstance(values, Mapping) or "alpha" not in values:
        return None
    return _numeric_field(values, "alpha")


def _bulk_composition_pressure_fence(values: Mapping[str, Any] | None) -> str | None:
    if not isinstance(values, Mapping):
        return None
    if (
        values.get("quantity") == "potassium_partial_pressure_as_published"
        and _numeric_field(values, "P_K_atm_as_published") is not None
        and values.get("reason") == (
            "Printed pressure retained; no equilibrium comparator claimed for bulk "
            "composition in the two-phase region."
        )
    ):
        return str(values["reason"])
    return None


def _pressure_evaluator(values: Mapping[str, Any], units: str | None) -> bool:
    form = str(values.get("source_form") or "")
    if not form or not values.get("coefficients"):
        return False
    output = form.split("=", 1)[0].strip()
    function = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)", output)
    if function and function.group(1).lower() not in {"ln", "log", "log10"}:
        output = function.group(1)
    if "=" in form and not re.search(r"\bP(?:_|\b)|pressure", output, re.I):
        return False
    return bool(re.search(r"\bP_(?:bar|mmHg|Pa|atm|Torr)\b", output, re.I)) or (
        bool(re.search(r"\bP\b|pressure", output, re.I))
        and str(units or "").lower() in {"bar", "mmhg", "pa", "atm", "torr"}
    )


def _quantity_contradiction(
    candidate: Quantity,
    obs_type: str | None,
    values: Mapping[str, Any] | None,
    units: str | None,
    row: Mapping[str, Any] | None,
) -> str | None:
    """Why this row is not the inferred quantity. None if nothing contradicts it."""

    blob = _row_text_blob(obs_type, values, units, row)
    units_l = str(units or "").strip().lower()
    semantics = None
    flag_not_hkl = False
    if isinstance(values, Mapping):
        semantics = values.get("semantics")
        flag_not_hkl = values.get("not_hkl_langmuir_coefficient") is True
    if isinstance(row, Mapping) and row.get("not_hkl_langmuir_coefficient") is True:
        flag_not_hkl = True

    if candidate in {Quantity.P_SAT, Quantity.P_PARTIAL}:
        if isinstance(values, Mapping):
            if values.get("gas_basis") == "TOTAL_PRESSURE_not_species":
                return (
                    "source states total vapour pressure over a multi-species vapour, "
                    "not a single Sen monomer; v2.1 has no total-vapour-pressure identity"
                )
            if values.get("source_form") and values.get("coefficients") and not _pressure_evaluator(values, units):
                return f"source evaluator {values['source_form']!r} does not produce a corroborated pressure"
        if candidate is Quantity.P_SAT and (
            "not pure-component saturation pressure" in blob
            or "partial vapor pressure over silicate melt" in blob
        ):
            return "source names partial pressure over a melt, not pure-component saturation pressure"
        if "dimensionless" in units_l and "pressure" not in units_l:
            return f"source units {units!r} do not denote pressure"

    if candidate is Quantity.EVAPORATION_COEFFICIENT_ALPHA:
        if flag_not_hkl:
            return "source flags not_hkl_langmuir_coefficient; not an HKL/Langmuir alpha"
        regime = ""
        if isinstance(row, Mapping):
            regime = str(row.get("regime") or "")
        if isinstance(values, Mapping) and values.get("regime"):
            regime = str(values.get("regime") or regime)
        if "olette" in regime.lower() or re.search(
            r"relative volatil|relative evaporation coefficient", blob
        ):
            return "source names Olette relative volatility, not an HKL/Langmuir alpha"
        if re.search(
            r"\bcondensation\b|film growth|growth coefficient|growth/condensation|"
            r"alpha\(cond\)|α\(cond\)",
            blob,
        ):
            return "source names a condensation or film-growth coefficient, not evaporation_coefficient_alpha"
        amount = _alpha_numeric(values)
        if amount is not None and not (Decimal("0") < amount <= Decimal("1")):
            return (
                f"source alpha {amount} lies outside (0, 1]; "
                "not a Langmuir/HKL evaporation coefficient"
            )
        if semantics in {"bound_not_point_ordering", "bound_not_point"}:
            return f"source semantics {semantics} is ordering/categorical, not a numeric alpha"

    if candidate is Quantity.ACTIVITY_COEFFICIENT:
        if "not a numeric gamma" in units_l or "not a numeric gamma" in blob:
            return "source units are dimensionless ordering, not a numeric activity coefficient"
        if "ordering" in units_l or semantics in {
            "bound_not_point_ordering",
            "bound_not_point",
        }:
            return "source is an ordering or categorical statement, not an activity coefficient"
        if "speciation" in blob and "gamma" not in blob:
            return "source is gas-species speciation/dominance ordering, not an activity coefficient"

    if candidate is Quantity.DELTA_FG:
        if "dissociation energ" in blob or "numeric d0" in blob:
            return "source note names dissociation energy, not delta_fG"
        if "equations for partial vapor" in blob or "partial vapor pressure" in blob:
            return "source note names partial vapor-pressure equations, not delta_fG"
        if "critical pressure" in blob or "critical temperature" in blob or (
            isinstance(values, Mapping) and ("Tc_K" in values or "Pc_bar" in values)
        ):
            return "source reports critical constants, not delta_fG"
        if "calorimetr" in blob or "heat of fusion" in blob or "heat content" in blob:
            if not (
                isinstance(values, Mapping)
                and (
                    values.get("evaluator_family")
                    or values.get("tabulated_delta_fG_kJ_mol")
                    or any(
                        key in values
                        for key in _UNIQUE_QUANTITY_FIELDS
                        if _UNIQUE_QUANTITY_FIELDS[key] is Quantity.DELTA_FG
                    )
                )
            ):
                return "source is calorimetric enthalpy/heat content, not delta_fG"
        if isinstance(values, Mapping) and (
            values.get("enthalpy_kcal_mol") is not None
            or "enthalpy" in str(values.get("header_quote") or "").lower()
            or "Δh" in str(values.get("header_quote") or "").lower()
        ):
            if not any(
                key in values
                for key in _UNIQUE_QUANTITY_FIELDS
                if _UNIQUE_QUANTITY_FIELDS[key] is Quantity.DELTA_FG
            ):
                return "source cells/headers report enthalpy, not delta_fG"
        if "kd" in blob and ("mol/l" in blob or "log10(kd" in blob or "kp_atm" in blob):
            return "source is a Kd/Kp dissociation equilibrium, not delta_fG"
        if "sublimation enthalpy" in blob or "boiling-point summary" in blob:
            return "source is sublimation enthalpy / boiling-point summary, not delta_fG"

    if candidate is Quantity.MASS_LOSS_RATE:
        if "partial pressure" in units_l or "lg p" in units_l or "lg p" in blob:
            return "source units name partial pressure, not mass_loss_rate"

    if semantics in {"bound_not_point_ordering", "bound_not_point"} and candidate not in {
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        Quantity.ACTIVITY_COEFFICIENT,
    }:
        if not (candidate is Quantity.P_PARTIAL and _bulk_composition_pressure_fence(values)):
            return f"source semantics {semantics} is ordering/categorical, not {candidate.value}"
    return None


def _quantity_corroborated(
    candidate: Quantity,
    values: Mapping[str, Any] | None,
    units: str | None,
    row: Mapping[str, Any] | None,
) -> bool:
    """A numeric field, evaluator, series, or parametric producer of this quantity."""

    named = _quantities_named_by_payload(values)
    if candidate in named:
        return True
    if not isinstance(values, Mapping):
        values = {}
    if candidate is Quantity.DELTA_FG:
        if values.get("evaluator_family"):
            return True
        if values.get("tabulated_delta_fG_kJ_mol"):
            return True
        if values.get("segments") and values.get("reference_pressure_Pa") is not None:
            return True
        if values.get("functions") or values.get("g_parameter"):
            return True
        return False
    if candidate is Quantity.EVAPORATION_COEFFICIENT_ALPHA:
        raw_range = values.get("alpha_range")
        if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
            lo, hi = _as_dec_or_none(raw_range[0]), _as_dec_or_none(raw_range[1])
            if lo is not None and hi is not None:
                return True
        form = values.get("alpha_form")
        if isinstance(form, Mapping) and str(form.get("type") or "").lower() == "arrhenius":
            blob = _row_text_blob(None, values, units, row)
            if re.search(
                r"\bcondensation\b|film growth|growth coefficient|growth/condensation",
                blob,
            ):
                return False
            return True
        return False
    if candidate is Quantity.TRANSITION_TEMPERATURE:
        return any(
            key in values
            for key in ("value_K", "T_m_K", "T_K", "temperature_K", "T_C", "T")
        )
    if candidate is Quantity.P_SAT:
        if _pressure_evaluator(values, units):
            return True
        if _is_pressure_point_list(values.get("points")):
            return True
        return any(key in values for key, _unit in _PRESSURE_SERIES_KEYS) or any(
            key in values for key in ("P", "p", "series")
        )
    if candidate is Quantity.ACTIVITY_COEFFICIENT:
        return "gamma" in values or "activity_coefficient" in values
    if candidate is Quantity.MASS_LOSS_RATE:
        return "mass_loss_rate" in values
    return False


_NEVER_QUALIFY_QUANTITY = frozenset(
    {
        "condensation_coefficient",
        "log10_Psat_over_P0",
    }
)
_FORMULA_SUFFIX_RE = re.compile(
    r"^([A-Z][a-z]?(?:\d+)?(?:[A-Z][a-z]?(?:\d+)?)*)(?:_(.*))?$"
)
_DERIVATION_SUFFIX_WORDS = ("gibbs_duhem", "ideal_mixing")


def split_qualified_quantity(raw: str) -> tuple[Quantity | None, str | None]:
    """Split activity_CsBO2 into (ACTIVITY, 'CsBO2'). Never cross quantities."""

    if not raw or raw in _NEVER_QUALIFY_QUANTITY:
        return None, None
    closed = sorted(((q.value, q) for q in Quantity), key=lambda kv: -len(kv[0]))
    for prefix, quantity in closed:
        if raw.startswith(prefix + "_"):
            return quantity, raw[len(prefix) + 1 :]
    for alias, quantity in sorted(QUANTITY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if raw.startswith(alias + "_"):
            return quantity, raw[len(alias) + 1 :]
    return None, None


def _co_present_quantity_field(quantity: Quantity, values: Mapping[str, Any]) -> bool:
    keys = QUANTITY_SOURCE_FIELDS.get(quantity, ())
    if quantity is Quantity.ACTIVITY:
        keys = ("activity",)
    for key in keys:
        if key == "value":
            continue
        if key in values and values.get(key) not in (None, ""):
            return True
    return False


def parse_quantity_suffix(suffix: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (species_formula, derivation_word, reference_reason)."""

    if not suffix:
        return None, None, None
    formula = None
    rest = suffix
    match = _FORMULA_SUFFIX_RE.match(suffix)
    if match:
        formula = match.group(1)
        rest = match.group(2) or ""
    derivation = None
    for word in _DERIVATION_SUFFIX_WORDS:
        if word == rest or rest.startswith(word + "_") or word in rest.split("_"):
            derivation = word
            break
    reference = None
    if derivation is None and formula is None:
        reference = suffix
    elif derivation is None and rest:
        reference = rest
    return formula, derivation, reference


_PERCENT_FRACTION_UNITS = frozenset(
    {"percent", "pct", "wt_percent", "wt%", "wt_pct", "%"}
)
_MASS_LOSS_YIELD_FIELDS = (
    "mass_loss_wt_pct",
    "mass_loss_pct",
    "mass_loss_fraction",
    "non_condensed_mass_loss_fraction",
)
_MEASURED_OXYGEN_YIELD_FIELDS: tuple[tuple[str, Quantity], ...] = (
    ("mass_yield_percent", Quantity.YIELD_FRACTION),
    ("fraction_of_feedstock_oxygen_percent", Quantity.O2_YIELD),
)



_ORDINAL_POINT_SUFFIX_RE = re.compile(r"^(?P<stem>.*)::point:(?P<index>\d+)$")


def _rekey_ordinal_point_observation_id(
    raw_obs_id: str,
    *,
    temperature: object | None = None,
    field: str | None = None,
) -> str:
    """Replace author ``::point:N`` with a content-stable suffix when possible."""

    match = _ORDINAL_POINT_SUFFIX_RE.fullmatch(str(raw_obs_id))
    if match is None:
        return str(raw_obs_id)
    stem = match.group("stem")
    if field:
        return series_point_id(stem, field=field)
    if temperature is not None:
        return series_point_id(stem, temperature=temperature)
    return str(raw_obs_id)

def _percent_named_fraction_field(key: str, units: str | None) -> bool:
    lowered = str(key or "").strip().lower()
    if lowered.endswith(("_pct", "_percent", "_wt_pct")):
        return True
    unit = str(units or "").strip().lower().replace(" ", "")
    return unit in _PERCENT_FRACTION_UNITS


def _item_mass_loss_field(item: Mapping[str, Any]) -> str | None:
    for key in _MASS_LOSS_YIELD_FIELDS:
        if key in item and _numeric_field(item, key) is not None:
            return key
    return None


def _yield_table_items(
    values: Mapping[str, Any],
) -> tuple[str, list[Any]] | tuple[None, None]:
    for key in ("points", "tests"):
        items = values.get(key)
        if not isinstance(items, list) or not items:
            continue
        if any(
            isinstance(item, Mapping) and _item_mass_loss_field(item)
            for item in items
        ):
            return key, items
    return None, None


_VACUUM_PYROLYSIS_SIDECAR_QUANTITY = {
    "pomeroy_non_condensed_mass_loss_fraction": Quantity.MASS_LOSS_FRACTION,
    "non_condensed_mass_loss_fraction": Quantity.MASS_LOSS_FRACTION,
}
_VACUUM_PYROLYSIS_SIDECAR_METHOD = {
    "pomeroy_cardiff_2006_measurements": MethodToken.VACUUM_CHAMBER_PYROLYSIS,
}


def _vacuum_pyrolysis_sidecar_quantity(observable: str) -> Quantity | None:
    if observable in _VACUUM_PYROLYSIS_SIDECAR_QUANTITY:
        return _VACUUM_PYROLYSIS_SIDECAR_QUANTITY[observable]
    lowered = observable.lower()
    if lowered.endswith("mass_loss_fraction"):
        return Quantity.MASS_LOSS_FRACTION
    return None


def _vacuum_pyrolysis_sidecar_method(meas_id: str) -> State[MethodToken]:
    token = _VACUUM_PYROLYSIS_SIDECAR_METHOD.get(meas_id)
    if token is not None:
        return State.of(token)
    return State.unknown("vacuum pyrolysis sidecar does not state a closed method token")


def _vacuum_pyrolysis_sidecar_temperature(meas: Mapping[str, Any]) -> dict[str, Any]:
    cond = meas.get("conditions_reported")
    if not isinstance(cond, Mapping):
        return {}
    hold = cond.get("peak_hold_temperature_C")
    if isinstance(hold, Mapping) and hold.get("reported_value") is not None:
        qualifier = str(hold.get("qualifier") or "")
        if "approximate" not in qualifier and "about" not in qualifier:
            return {"Tmax_C": hold.get("reported_value")}
    temp = cond.get("temperature_C")
    if isinstance(temp, Mapping) and temp.get("reported_value") is not None:
        qualifier = str(temp.get("qualifier") or "")
        if "approximate" in qualifier or "about" in qualifier or "peak" in qualifier:
            return {}
        return {"Tmax_C": temp.get("reported_value")}
    return {}


def _measured_oxygen_yield_fields(
    values: Mapping[str, Any],
) -> tuple[tuple[Quantity, str], ...] | None:
    if values.get("quantity") != "measured_oxygen_yield":
        return None
    found: list[tuple[Quantity, str]] = []
    for field, quantity in _MEASURED_OXYGEN_YIELD_FIELDS:
        if _numeric_field(values, field) is not None:
            found.append((quantity, field))
    return tuple(found) or None


def _is_pressure_point_list(items: object) -> bool:
    """True when a points list is tabulated T plus a pressure column."""

    if not isinstance(items, list) or not items or not isinstance(items[0], Mapping):
        return False
    first = items[0]
    has_t = any(
        key in first
        for key in ("T_K", "temperature_K", "T", "temperature", "T_C")
    )
    has_p = any(key in first for key, _unit in _PRESSURE_SERIES_KEYS) or any(
        key in first for key in ("P", "p")
    )
    return has_t and has_p


def _pressure_series_quantity(
    values: object,
    units: str | None,
    row: Mapping[str, Any] | None,
) -> Quantity | None:
    """Quantity for a tabulated T/P list. Row type cannot decide p_sat vs p_partial."""

    items: list[Any] | None = None
    values_map = values if isinstance(values, Mapping) else None
    if isinstance(values, list) and values:
        items = values
    elif isinstance(values_map, Mapping):
        if isinstance(values_map.get("points"), list) and values_map.get("points"):
            items = values_map.get("points")  # type: ignore[assignment]
        elif isinstance(values_map.get("series"), list) and values_map.get("series"):
            items = values_map.get("series")  # type: ignore[assignment]
    if not items or not isinstance(items[0], Mapping):
        return None
    first = items[0]
    has_p = any(key in first for key, _unit in _PRESSURE_SERIES_KEYS) or any(
        key in first for key in ("P", "p", "pressure_bar", "p_Pa")
    )
    if not has_p:
        return None
    blob = _row_text_blob(None, values_map, units, row)
    if "equilibrium vapor" in blob or "zero fractional vaporization" in blob:
        return Quantity.P_PARTIAL
    return None


def map_quantity(
    obs_type: str | None,
    values: Mapping[str, Any] | list[Any] | None,
    units: str | None = None,
    row: Mapping[str, Any] | None = None,
) -> tuple[State[Quantity], str | None]:
    if isinstance(values, list):
        values = (
            {"points": values} if _is_pressure_point_list(values) else {"series": values}
        )
    raw = None
    if isinstance(values, Mapping):
        raw = values.get("quantity")
        if isinstance(raw, str) and raw in QUANTITY_ALIASES:
            inferred = QUANTITY_ALIASES[raw]
            contradiction = _quantity_contradiction(inferred, obs_type, values, units, row)
            if contradiction:
                return State.unknown(contradiction), contradiction
            return State.of(inferred), None
        if isinstance(raw, str) and raw in {q.value for q in Quantity}:
            inferred = Quantity(raw)
            contradiction = _quantity_contradiction(inferred, obs_type, values, units, row)
            if contradiction:
                return State.unknown(contradiction), contradiction
            return State.of(inferred), None
        if isinstance(raw, str) and raw:
            qualified, _suffix = split_qualified_quantity(raw)
            if qualified is not None and _co_present_quantity_field(qualified, values):
                contradiction = _quantity_contradiction(
                    qualified, obs_type, values, units, row
                )
                if contradiction:
                    return State.unknown(contradiction), contradiction
                return State.of(qualified), None
    quantity_absent = raw is None or raw == ""
    if quantity_absent and units is not None and str(units).strip():
        unit_mapped = UNIT_DECLARED_QUANTITY.get(str(units).strip().lower())
        if unit_mapped is not None:
            contradiction = _quantity_contradiction(
                unit_mapped, obs_type, values, units, row
            )
            if contradiction:
                return State.unknown(contradiction), contradiction
            return State.of(unit_mapped), None
    named = _quantities_named_by_payload(values)
    if quantity_absent and len(named) == 1:
        inferred = next(iter(named))
        contradiction = _quantity_contradiction(inferred, obs_type, values, units, row)
        if contradiction:
            return State.unknown(contradiction), contradiction
        return State.of(inferred), None
    if quantity_absent and len(named) > 1:
        labels = ", ".join(sorted(q.value for q in named))
        return (
            State.unknown(f"payload names conflicting quantities {labels}"),
            f"conflicting quantity fields {labels}",
        )
    if quantity_absent:
        from_series = _pressure_series_quantity(values, units, row)
        if from_series is not None:
            contradiction = _quantity_contradiction(
                from_series, obs_type, values, units, row
            )
            if contradiction:
                return State.unknown(contradiction), contradiction
            return State.of(from_series), None
    if quantity_absent and obs_type in TYPE_QUANTITY:
        inferred = TYPE_QUANTITY[obs_type]
        if not _quantity_corroborated(inferred, values, units, row):
            reason = (
                "row type is a curation label, not a statement of the observable"
            )
            contradiction = _quantity_contradiction(
                inferred, obs_type, values, units, row
            )
            if contradiction:
                reason = contradiction
            return State.unknown(reason), reason
        contradiction = _quantity_contradiction(inferred, obs_type, values, units, row)
        if contradiction:
            return State.unknown(contradiction), contradiction
        return State.of(inferred), None
    if isinstance(raw, str) and raw:
        return (
            State.unknown(f"unsupported quantity {raw!r}"),
            f"unsupported quantity {raw!r}",
        )
    return State.unknown("source does not state a closed quantity"), "missing quantity"


_COMPILATION_CELL_QUANTITY: dict[str, Quantity] = {
    "delta_f_G": Quantity.DELTA_FG,
    "delta_fG": Quantity.DELTA_FG,
    "delta_fG_kJ_mol": Quantity.DELTA_FG,
    "deltafG": Quantity.DELTA_FG,
    "Gf": Quantity.DELTA_FG,
    "formation_gibbs_energy": Quantity.DELTA_FG,
    "formation_gibbs": Quantity.DELTA_FG,
    "delta_f_H": Quantity.DELTA_FH,
    "delta_f_H_298_15": Quantity.DELTA_FH,
    "delta_fH": Quantity.DELTA_FH,
    "deltafH": Quantity.DELTA_FH,
    "formation_enthalpy": Quantity.DELTA_FH,
    "formation_enthalpy_298_15_K_as_published": Quantity.DELTA_FH,
    "log_kf": Quantity.LOG10_KF,
    "log_Kf": Quantity.LOG10_KF,
    "log10_Kf": Quantity.LOG10_KF,
    "log10_kf": Quantity.LOG10_KF,
    "log10_formation_equilibrium_constant": Quantity.LOG10_KF,
}
_COMPILATION_KIND_NOT_QUANTITY = frozenset(
    {
        "atomic_weight",
        "atomic_weights_1963",
        "heat_content_and_entropy",
        "heat_capacity_coefficients",
        "symbols_constants",
        "symbols_and_constants",
        "critical_summaries_bibliography",
        "formula_continuation_prefix",
        "formula_continuation_suffix",
        "section_continuation_header",
        "unverified_identity",
        "auxiliary_numeric_table",
    }
)
_COMPILATION_UNKNOWN_REASON = "printed compilation columns are not mapped to a closed quantity"


def _compilation_cell_quantities(payload: Mapping[str, Any]) -> set[Quantity]:
    named: set[Quantity] = set()
    for key, quantity in _COMPILATION_CELL_QUANTITY.items():
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        named.add(quantity)
    cells = payload.get("cells")
    if isinstance(cells, Mapping):
        named.update(_compilation_cell_quantities(cells))
    return named


def compilation_quantity_from_record(
    doc: Mapping[str, Any],
) -> tuple[State[Quantity], str | None]:
    """Quantity only from record_kind, table kind, column labels, or cell keys."""

    kind = str(doc.get("record_kind") or "")
    table_kind = str(doc.get("table_kind") or "")
    if kind in _COMPILATION_KIND_NOT_QUANTITY or table_kind in _COMPILATION_KIND_NOT_QUANTITY:
        return State.unknown(_COMPILATION_UNKNOWN_REASON), _COMPILATION_UNKNOWN_REASON
    named: set[Quantity] = set()
    named.update(_compilation_cell_quantities(doc))
    labels = doc.get("column_labels_as_published") or doc.get("column_labels") or ()
    if isinstance(labels, (list, tuple)):
        joined = " ".join(str(x) for x in labels).lower()
        compact = joined.replace(" ", "")
        if "delta_fg" in compact or "δfg" in joined or "dfg" in compact:
            named.add(Quantity.DELTA_FG)
        if "delta_fh" in compact or "δfh" in joined or "dfh" in compact:
            named.add(Quantity.DELTA_FH)
        if "log_kf" in compact or "log10_kf" in compact:
            named.add(Quantity.LOG10_KF)
    units = doc.get("units_as_published")
    if isinstance(units, Mapping):
        for key, quantity in _COMPILATION_CELL_QUANTITY.items():
            if key in units:
                named.add(quantity)
    rows = doc.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                named.update(_compilation_cell_quantities(row))
    # Preserve historical DELTA_FG preference when co-named; otherwise admit a
    # single closed quantity (including DELTA_FH from printed formation enthalpy).
    if Quantity.DELTA_FG in named:
        return State.of(Quantity.DELTA_FG), None
    if len(named) == 1:
        return State.of(next(iter(named))), None
    if len(named) > 1:
        labels_txt = ", ".join(sorted(q.value for q in named))
        return (
            State.unknown(f"compilation record names conflicting quantities {labels_txt}"),
            f"compilation record names conflicting quantities {labels_txt}",
        )
    return State.unknown(_COMPILATION_UNKNOWN_REASON), _COMPILATION_UNKNOWN_REASON


def lineage_parents_from_source(
    obs: Mapping[str, Any],
    values: Mapping[str, Any],
    source_id: str,
    local_ids: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Source-stated lineage as (parents, prose). Never invents a pointer.

    A stated parent resolves only against a local observation id or an
    already-qualified ``source::id`` reference. Bare text that names no
    resolvable id is prose about how the value was obtained; it is returned
    in the second tuple so the caller can route it to the queue rather than
    minting a ``source::prose`` pointer that cannot resolve by construction.
    """

    raw = values.get("derived_from")
    if raw is None:
        raw = obs.get("derived_from")
    if raw is None or raw == "":
        return (), ()
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw if x]
    else:
        return (), ()
    parents: list[str] = []
    prose: list[str] = []
    prefix = f"{source_id}::"
    for item in items:
        local = item[len(prefix):] if item.startswith(prefix) else item
        if local in local_ids:
            parents.append(f"{prefix}{local}")
        elif "::" in item:
            # Source-stated qualified pointer; kept as written. If it
            # dangles, the store validator flags the extract, not us.
            parents.append(item)
        else:
            prose.append(item)
    return tuple(parents), tuple(prose)


def map_method(regime: object) -> State[MethodToken]:
    if not isinstance(regime, str) or not regime.strip():
        return State.unknown("source does not state method")
    token = METHOD_TOKENS.get(regime) or REGIME_TO_METHOD.get(regime)
    if token is None:
        return State.unknown(
            f"regime {regime!r} is not a closed method token "
            "(kems_effusion alone is insufficient)"
        )
    return State.of(token)


def evidence_for(
    method_class: object,
    *,
    evaluator_family: object = None,
    attribution: str | None = None,
    model: str | None = None,
    regime: object = None,
) -> tuple[Evidence, str | None]:
    """Return Evidence and an optional queue reason."""

    original = None if method_class is None else str(method_class)
    if evaluator_family:
        return (
            Evidence(
                class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
                original_method_class=original,
                model=str(evaluator_family),
            ),
            None,
        )
    if original is None or original == "":
        regime_text = str(regime).strip() if isinstance(regime, str) else ""
        if regime_text:
            mapped_regime = METHOD_CLASS_MAP.get(regime_text)
            if mapped_regime is not None:
                return (
                    Evidence(
                        class_=State.of(mapped_regime),
                        original_method_class=regime_text,
                        model=(
                            regime_text
                            if mapped_regime is EvidenceClass.MODEL_DERIVED
                            else model
                        ),
                    ),
                    None,
                )
            return (
                Evidence(
                    class_=State.unknown(f"unmapped regime {regime_text!r}"),
                    original_method_class=regime_text,
                ),
                f"unmapped regime {regime_text}",
            )
        return (
            Evidence(class_=State.unknown("source does not state method_class")),
            "absent method_class",
        )
    if original in PAGE_METHOD_CLASSES:
        return (
            Evidence(
                class_=State.unknown(f"page_grounded: method_class {original}"),
                original_method_class=original,
            ),
            f"PAGE method_class {original}",
        )
    mapped = METHOD_CLASS_MAP.get(original)
    if mapped is None:
        return (
            Evidence(
                class_=State.unknown(f"unmapped method_class {original}"),
                original_method_class=original,
            ),
            f"unmapped method_class {original}",
        )
    if mapped in {
        EvidenceClass.QUOTED_UNATTRIBUTED,
        EvidenceClass.QUOTED_ATTRIBUTED,
    }:
        mapped = (
            EvidenceClass.QUOTED_ATTRIBUTED
            if attribution
            else EvidenceClass.QUOTED_UNATTRIBUTED
        )
    if mapped is EvidenceClass.AUTHOR_ESTIMATE and not (model or original):
        return (
            Evidence(
                class_=State.unknown("author_estimate requires model"),
                original_method_class=original,
            ),
            "author_estimate requires model",
        )
    return (
        Evidence(
            class_=State.of(mapped),
            original_method_class=original,
            attribution=attribution,
            model=model
            or (
                original
                if mapped in {EvidenceClass.AUTHOR_ESTIMATE, EvidenceClass.MODEL_DERIVED}
                else None
            ),
        ),
        None,
    )


def admission_for(
    raw_status: object,
    *,
    superseded_by: str | None,
    extraction: Mapping[str, Any] | None,
    locator: Locator | None,
) -> Admission:
    if superseded_by:
        decided = None
        if isinstance(extraction, Mapping) and locator is not None:
            worker = str(extraction.get("worker") or extraction.get("method") or "extract")
            date = str(extraction.get("date") or "unspecified")
            decided = AdmissionDecision(worker=worker, date=date, evidence=locator)
        return Admission(
            status=AdmissionStatus.SUPERSEDED,
            reason="source supersedes link resolved",
            superseded_by=superseded_by,
            decided_by=decided,
        )
    if raw_status is None or raw_status == "":
        return Admission(
            status=AdmissionStatus.PENDING,
            reason="no observation admission_status mapped from source",
        )
    text = str(raw_status)
    closed = {s.value: s for s in AdmissionStatus}
    if text in closed:
        status = closed[text]
        if status is AdmissionStatus.PENDING:
            return Admission(status=status, reason=f"source admission_status={text}")
        decided = None
        if isinstance(extraction, Mapping) and locator is not None:
            decided = AdmissionDecision(
                worker=str(extraction.get("worker") or "extract"),
                date=str(extraction.get("date") or "unspecified"),
                evidence=locator,
            )
        return Admission(
            status=status,
            reason=f"source admission_status={text}",
            decided_by=decided,
        )
    if text.startswith("rejected") or text in {"authors_rejected"}:
        decided = None
        if isinstance(extraction, Mapping) and locator is not None:
            decided = AdmissionDecision(
                worker=str(extraction.get("worker") or "extract"),
                date=str(extraction.get("date") or "unspecified"),
                evidence=locator,
            )
        return Admission(
            status=AdmissionStatus.REJECTED,
            reason=f"source admission_status={text}",
            decided_by=decided,
        )
    return Admission(
        status=AdmissionStatus.PENDING,
        reason=f"source admission_status={text} is not a closed v2.1 token",
    )


def _locator_is_page_grounded(locator: Locator | None) -> bool:
    if locator is None:
        return False
    return any(
        getattr(locator, key) not in (None, "")
        for key in ("page", "published_page", "table", "pdf_page_index")
    )


def _cardiff_matchett_disagreement_reason(parent_values: Mapping[str, Any]) -> str | None:
    if any(str(key).startswith("contradiction_vs_") for key in parent_values):
        return (
            "Cardiff Table 1 prints 16.00/37.00 where Matchett Table 3 prints "
            "0.16/0.37; both left as printed"
        )
    return None


def _yield_point_admission(
    *,
    item: Mapping[str, Any],
    parent_values: Mapping[str, Any],
    evidence: Evidence,
    locator: Locator | None,
    extraction: Mapping[str, Any] | None,
    t_is_point: bool,
    parent_admission: Admission,
) -> Admission:
    if parent_admission.status is AdmissionStatus.SUPERSEDED:
        return parent_admission
    test_id = str(item.get("test") or "")
    if test_id in {"2b", "3"}:
        disagreement = _cardiff_matchett_disagreement_reason(parent_values)
        if disagreement:
            return Admission(
                status=AdmissionStatus.PENDING,
                reason=disagreement,
            )
    measured = (
        evidence.class_.is_value
        and evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    )
    if not measured or not t_is_point or not _locator_is_page_grounded(locator):
        return parent_admission
    decided = None
    if isinstance(extraction, Mapping) and locator is not None:
        decided = AdmissionDecision(
            worker=str(extraction.get("worker") or "extract"),
            date=str(extraction.get("date") or "unspecified"),
            evidence=locator,
        )
    return Admission(
        status=AdmissionStatus.ADMITTED,
        reason="printed numeric mass-loss cell with page locator",
        decided_by=decided,
    )


def uncertainty_for(raw: object) -> Uncertainty:
    if raw is None or raw == "":
        return Uncertainty(kind=UncertaintyKind.NONE)
    if isinstance(raw, (str, Mapping)):
        return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=raw)
    return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=str(raw))


def make_species(
    formula: str,
    phase: Phase | State[Phase],
    polymorph: State[Polymorph] | State[str] | None = None,
    charge: State[int] | int | None = None,
) -> Species:
    phase_state = phase if isinstance(phase, State) else State.of(phase)
    token = phase_state.value if phase_state.is_value else None
    if polymorph is None:
        if token is Phase.CR:
            polymorph = State.unknown("source does not state polymorph")
        elif token is None:
            polymorph = State.unknown("phase unknown; polymorph unresolved")
        else:
            polymorph = State.not_applicable("not crystal")
    return Species(
        formula=formula,
        phase=phase_state,
        polymorph=polymorph,
        charge=charge,
    )


def polymorph_from_extract(obs: Mapping[str, Any]) -> State[str] | None:
    form = obs.get("condensed_form")
    if isinstance(form, Mapping) and form.get("polymorph"):
        return State.of(str(form["polymorph"]))
    return None


def fill_identity(
    quantity: Quantity | State[Quantity],
    species: Species,
    **known: Any,
) -> Identity:
    """Fill required axes as unknown and permitted-N/A as not_applicable.

    A VALUE on an axis the profile does not require is invalid (v2.1), not an
    extra key. Transition temperatures store T as the observable, never as
    ``identity.temperature_K``.
    """

    q_state = quantity if isinstance(quantity, State) else State.of(quantity)
    token = q_state.value if q_state.is_value else None
    if token is Quantity.TRANSITION_TEMPERATURE:
        known.pop("temperature_K", None)
    identity = Identity(quantity=q_state, species=species, **known)
    if token is None:
        return identity
    from simulator.battery.identity import _AXIS_NAMES

    for _ in range(4):
        profile = profile_for(identity)
        payload = {item.name: getattr(identity, item.name) for item in fields(identity)}
        changed = False
        for name in profile.required:
            state = payload.get(name)
            if state is None or (isinstance(state, State) and state.is_not_applicable):
                payload[name] = State.unknown(
                    "no single temperature_K mapped from source" if name == "temperature_K"
                    else f"no {name} mapped from source"
                )
                changed = True
        for name in profile.permitted_not_applicable:
            state = payload.get(name)
            if state is None:
                payload[name] = State.not_applicable(
                    f"profile {token.value} does not use {name}"
                )
                changed = True
            elif isinstance(state, State) and state.is_value:
                payload[name] = State.not_applicable(
                    f"profile {token.value} does not use {name}"
                )
                changed = True
        for name in _AXIS_NAMES:
            if name in profile.required or name in profile.permitted_not_applicable:
                continue
            state = payload.get(name)
            if isinstance(state, State) and state.is_value:
                payload[name] = None
                changed = True
        if not changed:
            break
        identity = Identity(**payload)
    return identity


_PRESSURE_SERIES_KEYS = (
    ("pressure_atm", "atm"),
    ("p_atm", "atm"),
    ("P_K_atm_as_published", "atm"),
    ("P_atm", "atm"),
    ("pressure_bar", "bar"),
    ("P_bar", "bar"),
    ("p_bar", "bar"),
    ("P_Pa", "Pa"),
    ("pressure_Pa", "Pa"),
    ("p_Pa", "Pa"),
    ("Pb_Torr", "Torr"),
    ("Pbar_Torr", "Torr"),
    ("Po_Torr", "Torr"),
)

# Species-labelled pressure columns are never the activity_coefficient value.
_ANCILLARY_SERIES_KEYS = (
    "p_Ga_Pa",
    "p_In_Pa",
    "p_O2_calc_Pa",
    "K_Ga",
    "K_In",
    "delta_IW",
)

_PARTIAL_PRESSURE_FIELDS = ("partial_pressure_pa", "p_Ga_Pa", "p_In_Pa", "p_O2_calc_Pa")

QUANTITY_SOURCE_FIELDS: dict[Quantity, tuple[str, ...]] = {
    Quantity.ACTIVITY: ("activity",),
    Quantity.ACTIVITY_COEFFICIENT: ("gamma", "activity_coefficient"),
    Quantity.EVAPORATION_COEFFICIENT_ALPHA: ("alpha",),
    Quantity.DELTA_FG: (
        "delta_fG",
        "delta_fG_298_kJ_mol",
        "Delta_f_G_298_kJ_mol",
        "deltafG",
        "delta_fG_kJ_mol",
        "table_kJ_mol",
        "Gf",
        "formation_gibbs_energy",
        "value",
    ),
    Quantity.DELTA_FH: (
        "delta_f_H_298_15",
        "delta_f_H",
        "delta_fH",
        "deltafH",
        "formation_enthalpy_298_15_K_as_published",
        "formation_enthalpy",
        "value",
    ),
    Quantity.LOG10_KF: (
        "table_log10_Kf",
        "log10_Kf",
        "log10_kf",
        "log10_formation_equilibrium_constant",
        "value",
    ),
    Quantity.P_SAT: tuple(k for k, _u in _PRESSURE_SERIES_KEYS) + ("P", "p"),
    Quantity.P_PARTIAL: tuple(k for k, _u in _PRESSURE_SERIES_KEYS)
    + ("P", "p")
    + _PARTIAL_PRESSURE_FIELDS,
    Quantity.MASS_LOSS_RATE: ("mass_loss_rate",),
    Quantity.EVAPORATION_RATE: ("evaporation_rate",),
    Quantity.ION_INTENSITY: ("ion_intensity",),
    Quantity.ION_INTENSITY_RATIO: ("ion_intensity_ratio", "ion_current_ratio"),
    Quantity.O2_YIELD: (
        "o2_yield",
        "fraction_of_feedstock_oxygen_percent",
    ),
    Quantity.MASS_LOSS_FRACTION: (
        "mass_loss_fraction",
        "non_condensed_mass_loss_fraction",
        "mass_loss_wt_pct",
        "mass_loss_pct",
    ),
    Quantity.YIELD_FRACTION: (
        "yield_fraction",
        "mass_yield_percent",
    ),
    Quantity.INTERACTION_PARAMETER: ("wagner_interaction_parameter", "epsilon"),
    Quantity.TRANSITION_TEMPERATURE: (
        "value_K",
        "T_m_K",
        "T_K",
        "temperature_K",
        "T_C",
        "T",
    ),
}

_CONDITION_RANGE_KEYS = ("T_range_K", "temperature_range_k", "temperature_range_K")
AXIS_TEMPERATURE_K = "temperature_K"
AXIS_STANDARD_PRESSURE_PA = "standard_pressure_Pa"

_PRESSURE_UNIT_BY_KEY = dict(_PRESSURE_SERIES_KEYS)
_PRESSURE_UNIT_BY_KEY.update({key: "Pa" for key in _PARTIAL_PRESSURE_FIELDS})


@dataclass(frozen=True)
class SourceSelection:
    """Declared-quantity read of a source payload. Never a borrowed number."""

    value: Value
    field_name: str | None = None
    unit_trail: str = "identity"
    amount: Decimal | None = None
    reason: str | None = None
    unused_ancillary: tuple[str, ...] = ()
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...] = ()

    @property
    def available(self) -> bool:
        return self.value.kind is not ValueKind.UNAVAILABLE


_BOUNDARY_SERVED: list[tuple[str, int, str]] = []
_BOUNDARY_WRAPPERS = {
    "select_declared_source",
    "_record_boundary_caller",
    "empty_value_from_payload",
    "_series_point_value",
    "_selection_from_named_field",
    "_condition_ranges_from_payload",
    "_numeric_field",
    "_unavailable_selection",
    "_point_selection",
    "_interval_selection",
    "_series_selection_from_items",
}

# Ingestion functions allowed to call select_declared_source. Wrappers above
# are excluded from the served-caller record; everything else must be here.
BOUNDARY_INGEST_CALLERS = frozenset(
    {
        "_migrate_extract_observation",
        "_emit_exploded_point",
        "_migrate_kems",
        "_migrate_mre",
        "_migrate_langmuir",
        "_migrate_refractory",
        "_migrate_vacuum_pyrolysis",
        "_migrate_ledger",
        "_migrate_compilation_file",
    }
)


def reset_boundary_served() -> None:
    _BOUNDARY_SERVED.clear()


def boundary_served_callers() -> tuple[tuple[str, int, str], ...]:
    return tuple(_BOUNDARY_SERVED)


def _record_boundary_caller() -> None:
    frame = inspect.currentframe()
    while frame is not None:
        frame = frame.f_back
        if frame is None:
            break
        name = frame.f_code.co_name
        if name in _BOUNDARY_WRAPPERS:
            continue
        _BOUNDARY_SERVED.append(
            (Path(frame.f_code.co_filename).name, frame.f_lineno, name)
        )
        break


def _quantity_token(declared: Quantity | State[Quantity] | str | None) -> Quantity | None:
    if isinstance(declared, Quantity):
        return declared
    if isinstance(declared, State):
        return declared.value if declared.is_value else None
    return None


def _condition_ranges_from_payload(
    payload: Mapping[str, Any],
) -> tuple[tuple[str, Decimal, Decimal], ...]:
    found: list[tuple[str, Decimal, Decimal]] = []
    for key in _CONDITION_RANGE_KEYS:
        raw = payload.get(key)
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        lo, hi = _as_dec_or_none(raw[0]), _as_dec_or_none(raw[1])
        if lo is not None and hi is not None:
            found.append((key, lo, hi))
    return tuple(found)


def _numeric_field(payload: Mapping[str, Any], key: str) -> Decimal | None:
    raw = payload.get(key)
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    return _as_dec_or_none(raw)


def _formation_gibbs_cell_flags(cell: object) -> tuple[bool, bool, bool]:
    """Return (numeric, printed, ocr_suspect_with_no_parsed_value)."""

    if cell is None or cell == "":
        return False, False, False
    if isinstance(cell, Mapping):
        amount = _as_dec_or_none(cell.get("value"))
        as_published = cell.get("as_published")
        printed = as_published not in (None, "") or cell.get("value") not in (None, "")
        ocr_nv = bool(cell.get("ocr_suspect")) and amount is None
        return amount is not None, printed, ocr_nv
    amount = _as_dec_or_none(cell)
    if amount is not None:
        return True, True, False
    if isinstance(cell, str) and cell.strip():
        return False, True, False
    return False, False, False


def _unit_as_printed(unit_map: object, column: str) -> str | None:
    if isinstance(unit_map, Mapping):
        if column.startswith("formation."):
            raw = unit_map.get("formation_gibbs_energy")
        else:
            raw = (
                unit_map.get(column)
                or unit_map.get("formation_gibbs_energy")
                or unit_map.get("delta_f_G")
            )
        return _printed_field_text(raw)
    text = _printed_field_text(unit_map)
    if text is None:
        return None
    return (
        f"{text!r} (printed units_as_published not mapped to column {column})"
    )


def _iter_formation_gibbs_cells(
    payload: Mapping[str, Any],
    unit_map: object = None,
) -> list[tuple[str, object, str | None]]:
    """Every formation-Gibbs spelling this source uses, with the printed unit."""

    if unit_map is None:
        unit_map = payload.get("units_as_published")
    found: list[tuple[str, object, str | None]] = []
    if "delta_f_G" in payload:
        found.append(
            ("delta_f_G", payload.get("delta_f_G"), _unit_as_printed(unit_map, "delta_f_G"))
        )
    if "formation_gibbs_energy" in payload:
        found.append(
            (
                "formation_gibbs_energy",
                payload.get("formation_gibbs_energy"),
                _unit_as_printed(unit_map, "formation_gibbs_energy"),
            )
        )
    cells = payload.get("cells")
    if isinstance(cells, Mapping):
        for column in ("formation_gibbs_energy", "formation_gibbs"):
            if column in cells:
                found.append((column, cells[column], _unit_as_printed(unit_map, column)))
    formation = payload.get("formation")
    if isinstance(formation, Mapping):
        for basis, body in formation.items():
            if not isinstance(body, Mapping) or "gibbs_energy" not in body:
                continue
            column = f"formation.{basis}.gibbs_energy"
            found.append(
                (column, body.get("gibbs_energy"), _unit_as_printed(unit_map, column))
            )
    for key in ("series", "rows"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping):
                found.extend(_iter_formation_gibbs_cells(item, unit_map))
    return found


def _census_formation_gibbs(
    payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Per-column counts of printed formation Gibbs cells."""

    by_column: dict[str, dict[str, Any]] = {}
    for column, cell, unit in _iter_formation_gibbs_cells(payload):
        info = by_column.setdefault(
            column,
            {"count": 0, "numeric": 0, "printed": 0, "ocr_nv": 0, "unit": unit},
        )
        if info.get("unit") in (None, "") and unit:
            info["unit"] = unit
        numeric, printed, ocr_nv = _formation_gibbs_cell_flags(cell)
        info["count"] += 1
        if numeric:
            info["numeric"] += 1
        if printed:
            info["printed"] += 1
        if ocr_nv:
            info["ocr_nv"] += 1
    return by_column


def _delta_fg_unavailable_reason(
    payload: Mapping[str, Any],
    q_token: Quantity | None,
) -> str | None:
    """Honest reason when formation Gibbs is printed but not imported.

    Does not lift a number. Returns None when this source does not use a
    formation-Gibbs spelling, so the caller may then claim absence.
    """

    if q_token not in {None, Quantity.DELTA_FG}:
        return None
    census = _census_formation_gibbs(payload)
    if not census:
        return None
    numeric_bits: list[str] = []
    ocr_bits: list[str] = []
    named_bits: list[str] = []
    for column, info in census.items():
        unit = info.get("unit")
        unit_clause = (
            f", unit as printed {unit}" if unit else ", unit as printed not stated"
        )
        if info["numeric"]:
            numeric_bits.append(
                f"{info['numeric']} numeric formation Gibbs cells "
                f"(column {column}{unit_clause})"
            )
        elif info["ocr_nv"]:
            n = info["printed"] or info["count"]
            ocr_bits.append(
                f"all {n} printed {column} cells are OCR-suspect with no parsed value"
            )
        else:
            named_bits.append(
                f"source names {info['count']} {column} cells with no parsed numeric value"
            )
    if numeric_bits:
        return (
            "source prints "
            + " and ".join(numeric_bits)
            + "; not imported by the generic migrator; "
            "awaiting the per-source compilation generator"
        )
    if ocr_bits:
        return "; ".join(ocr_bits)
    if named_bits:
        return "; ".join(named_bits)
    return None


def _unused_ancillary(payload: Mapping[str, Any], used: str | None) -> tuple[str, ...]:
    return tuple(k for k in _ANCILLARY_SERIES_KEYS if k in payload and k != used)


def _printed_column_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    columns = payload.get("columns") or []
    headings = [str(c.get("heading_as_published") or c.get("heading_raw") or i)
                for i, c in enumerate(columns) if isinstance(c, Mapping)]
    items = payload.get("rows") or payload.get("series") or payload.get("points") or []
    if not isinstance(items, list):
        return counts

    def visit(cell: object, name: str) -> None:
        if isinstance(cell, Mapping) and not any(k in cell for k in ("value", "raw", "as_published")):
            for key, value in cell.items():
                visit(value, f"{name}.{key}" if name else str(key))
        elif isinstance(cell, list):
            for i, value in enumerate(cell):
                label = headings[i] if i < len(headings) else f"column[{i}]"
                visit(value, label)
        else:
            raw = cell.get("value") if isinstance(cell, Mapping) else cell
            counts.setdefault(name, 0)
            if not isinstance(raw, bool) and _as_dec_or_none(raw) is not None:
                counts[name] += 1

    for item in items:
        if not isinstance(item, Mapping):
            continue
        if "cells" in item:
            visit(item["cells"], "")
        else:
            for key, cell in item.items():
                if key in {"source_row_index", "source_line", "raw", "locator", "index"}:
                    continue
                visit(cell, key)
    return counts


def _unmapped_columns_reason(payload: Mapping[str, Any], q_token: Quantity | None) -> str:
    counts = _printed_column_counts(payload)
    columns = ", ".join(f"{name}: {n} numeric cells" for name, n in counts.items())
    cause = "declared quantity is unknown" if q_token is None else f"columns not mapped to {q_token.value}"
    if set(counts) == {"atomic_weight"}:
        return (
            f"source prints scalar-only atomic_weight: {counts['atomic_weight']} numeric cells "
            "and no coordinate; " + cause + "; awaiting the per-source compilation generator"
        )
    return (
        f"source column census ({columns or 'no parsed columns'}); {cause}; "
        "no mapped coordinate/value pair selected"
    )


def _compilation_temperature_state(doc: Mapping[str, Any]) -> State[Decimal]:
    counts = _printed_column_counts(doc)
    coordinates = [name for name in counts if re.search(r"temperature|^T(?:_|$)|^cp_\d+_k", name)]
    if coordinates or doc.get("temperature_grid"):
        return State.unknown(
            "source prints a temperature grid (" + ", ".join(coordinates or ["temperature_grid"])
            + "); series coordinate, not a single identity temperature_K"
        )
    return State.unknown("no single temperature_K mapped from compilation record")


def _unavailable_selection(
    reason: str,
    *,
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...] = (),
    unused_ancillary: tuple[str, ...] = (),
    field_name: str | None = None,
    unit_trail: str = "identity",
) -> SourceSelection:
    return SourceSelection(
        value=Value(ValueKind.UNAVAILABLE, unavailable_reason=reason),
        field_name=field_name,
        unit_trail=unit_trail,
        reason=reason,
        unused_ancillary=unused_ancillary,
        condition_ranges=condition_ranges,
    )


def _point_selection(
    amount: Decimal,
    field_name: str,
    unit_trail: str,
    payload: Mapping[str, Any],
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...],
) -> SourceSelection:
    return SourceSelection(
        value=Value.point_of(amount),
        field_name=field_name,
        unit_trail=unit_trail,
        amount=amount,
        unused_ancillary=_unused_ancillary(payload, field_name),
        condition_ranges=condition_ranges,
    )


def _interval_selection(
    lo: Decimal,
    hi: Decimal,
    field_name: str,
    payload: Mapping[str, Any],
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...],
    reason: str | None = None,
) -> SourceSelection:
    return SourceSelection(
        value=Value(ValueKind.INTERVAL, interval_low=lo, interval_high=hi),
        field_name=field_name,
        unit_trail="as_published",
        reason=reason,
        unused_ancillary=_unused_ancillary(payload, field_name),
        condition_ranges=condition_ranges,
    )


def _selection_from_named_field(
    payload: Mapping[str, Any],
    q_token: Quantity,
    units: str | None,
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...],
) -> SourceSelection | None:
    if q_token in {Quantity.P_SAT, Quantity.P_PARTIAL}:
        for key, unit in _PRESSURE_SERIES_KEYS:
            if key not in payload:
                continue
            val, trail = convert_pressure_to_pa(payload.get(key), unit)
            if val is not None:
                return _point_selection(
                    val, key, trail or "identity", payload, condition_ranges
                )
            return _unavailable_selection(
                trail or f"{key} is not a grounded pressure",
                condition_ranges=condition_ranges,
                unused_ancillary=_unused_ancillary(payload, key),
                field_name=key,
                unit_trail=trail or "identity",
            )
        if q_token is Quantity.P_PARTIAL:
            for key in _PARTIAL_PRESSURE_FIELDS:
                if key not in payload:
                    continue
                val, trail = convert_pressure_to_pa(payload.get(key), "Pa")
                if val is not None:
                    return _point_selection(
                        val, key, trail or "identity:Pa", payload, condition_ranges
                    )
                if payload.get(key) is None or payload.get(key) == "":
                    return _unavailable_selection(
                        f"{key} is null; absence is not a measured zero",
                        condition_ranges=condition_ranges,
                        unused_ancillary=_unused_ancillary(payload, key),
                        field_name=key,
                    )
        for key in ("P", "p"):
            if key not in payload:
                continue
            val, trail = convert_pressure_to_pa(payload.get(key), units)
            if val is not None:
                return _point_selection(
                    val, key, trail or "identity", payload, condition_ranges
                )
            return _unavailable_selection(
                trail or "series P is not grounded in a source pressure unit",
                condition_ranges=condition_ranges,
                unused_ancillary=_unused_ancillary(payload, key),
                field_name=key,
                unit_trail=trail or "identity",
            )
        return None
    for key in QUANTITY_SOURCE_FIELDS.get(q_token, ()):
        if key not in payload:
            continue
        if q_token is Quantity.TRANSITION_TEMPERATURE:
            unit = (
                "K"
                if key in {"T_K", "temperature_K", "value_K", "T_m_K"}
                else ("C" if key == "T_C" else units)
            )
            amount, trail = convert_temperature_to_k(payload.get(key), unit)
            if amount is not None:
                return _point_selection(
                    amount, key, trail or "identity:K", payload, condition_ranges
                )
            continue
        amount = _numeric_field(payload, key)
        if amount is None:
            continue
        trail = "as_published"
        if q_token is Quantity.LOG10_KF and key == "value":
            trail = "identity"
        if q_token in {
            Quantity.MASS_LOSS_FRACTION,
            Quantity.MASS_LOSS_FRACTION_VS_T,
            Quantity.YIELD_FRACTION,
            Quantity.O2_YIELD,
        } and _percent_named_fraction_field(key, units):
            amount = amount / Decimal("100")
            trail = "percent_to_fraction"
        return _point_selection(amount, key, trail, payload, condition_ranges)
    decorated_prefix = f"{q_token.value}_"
    decorated = [
        (key, _numeric_field(payload, key))
        for key in payload
        if isinstance(key, str)
        and key.startswith(decorated_prefix)
        and _numeric_field(payload, key) is not None
    ]
    if len(decorated) == 1:
        key, amount = decorated[0]
        assert amount is not None
        trail = "as_published"
        if q_token in {
            Quantity.MASS_LOSS_FRACTION,
            Quantity.MASS_LOSS_FRACTION_VS_T,
            Quantity.YIELD_FRACTION,
            Quantity.O2_YIELD,
        } and _percent_named_fraction_field(key, units):
            amount /= Decimal("100")
            trail = "percent_to_fraction"
        return _point_selection(amount, key, trail, payload, condition_ranges)
    range_key = None
    raw_range = None
    if q_token is Quantity.EVAPORATION_COEFFICIENT_ALPHA and "alpha_range" in payload:
        range_key = "alpha_range"
        raw_range = payload.get("alpha_range")
    elif "range" in payload:
        range_key = "range"
        raw_range = payload.get("range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
        lo, hi = _as_dec_or_none(raw_range[0]), _as_dec_or_none(raw_range[1])
        if lo is not None and hi is not None:
            return _interval_selection(
                lo, hi, range_key or "range", payload, condition_ranges
            )
    return None


def _series_selection_from_items(
    series: list[Any],
    q_token: Quantity | None,
    units: str | None,
    condition_ranges: tuple[tuple[str, Decimal, Decimal], ...],
    parent: Mapping[str, Any] | None = None,
) -> SourceSelection:
    points: list[tuple[Decimal, Decimal]] = []
    for item in series:
        if not isinstance(item, Mapping):
            continue
        t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, item)
        v_sel = select_declared_source(q_token, units, item)
        if t_sel.amount is not None and v_sel.amount is not None:
            points.append((t_sel.amount, v_sel.amount))
    if points:
        return SourceSelection(
            value=Value(ValueKind.SERIES, series=tuple(points)),
            field_name="series",
            unit_trail="as_published",
            condition_ranges=condition_ranges,
        )
    census_payload: dict[str, Any] = dict(parent or {})
    if "rows" in census_payload:
        census_payload.pop("series", None)
    else:
        census_payload["series"] = series
    gibbs_reason = _delta_fg_unavailable_reason(census_payload, q_token)
    if gibbs_reason:
        return _unavailable_selection(
            gibbs_reason,
            condition_ranges=condition_ranges,
            field_name="series",
        )
    return _unavailable_selection(
        _unmapped_columns_reason(census_payload, q_token),
        condition_ranges=condition_ranges,
        field_name="series",
    )


def select_declared_source(
    declared: Quantity | State[Quantity] | str | None,
    units: str | None,
    payload: Mapping[str, Any] | None,
) -> SourceSelection:
    """The only way source fields become an Observation value or identity axis.

    In: declared quantity token (or axis name), declared units, source payload.
    Out: the field/unit trail that named that token, or unknown-with-reason.
    Refuses to return a number the declared token did not name.
    """

    _record_boundary_caller()
    if not isinstance(payload, Mapping):
        return _unavailable_selection("empty values")
    condition_ranges = _condition_ranges_from_payload(payload)
    unused = _unused_ancillary(payload, None)

    if declared == AXIS_TEMPERATURE_K:
        found_temperatures: list[tuple[str, object, Decimal, str]] = []
        for key, unit in (
            ("T_K", "K"),
            ("T_K_as_published", "K"),
            ("temperature_K", "K"),
            ("Tmax_K", "K"),
            ("T_C", "C"),
            ("Tmax_C", "C"),
        ):
            if key not in payload:
                continue
            raw = payload.get(key)
            use_unit = unit
            if isinstance(raw, Mapping):
                use_unit = str(raw.get("units") or unit)
                raw = raw.get("value")
            amount, trail = convert_temperature_to_k(raw, use_unit)
            if amount is None:
                return _unavailable_selection(
                    trail or f"{key} is not numeric",
                    condition_ranges=condition_ranges,
                    field_name=key,
                    unit_trail=trail or "identity",
                )
            found_temperatures.append((key, raw, amount, trail or "identity:K"))
        if found_temperatures:
            key, _raw, amount, trail = _prefer_printed_temperature(found_temperatures)
            return _point_selection(amount, key, trail, payload, condition_ranges)
        if "temperature" in payload:
            raw = payload.get("temperature")
            unit = "K"
            if isinstance(raw, Mapping):
                unit = str(raw.get("units") or "K")
                raw = raw.get("value")
            amount, trail = convert_temperature_to_k(raw, unit)
            if amount is not None:
                return _point_selection(
                    amount, "temperature", trail or "identity:K", payload, condition_ranges
                )
        if "T" in payload:
            t_unit = payload.get("T_units") or payload.get("temperature_units") or units
            amount, trail = convert_temperature_to_k(payload.get("T"), t_unit)
            if amount is not None:
                return _point_selection(
                    amount, "T", trail or "identity:K", payload, condition_ranges
                )
            return _unavailable_selection(
                trail
                or "series T is not grounded in a source unit (T_K / T_C required)",
                condition_ranges=condition_ranges,
                field_name="T",
                unit_trail=trail or "identity",
            )
        if condition_ranges:
            name, lo, hi = condition_ranges[0]
            return _unavailable_selection(
                f"source {name} [{lo}, {hi}] is a temperature domain, not a point",
                condition_ranges=condition_ranges,
                unused_ancillary=unused,
                field_name=name,
            )
        return _unavailable_selection(
            "source does not state temperature_K",
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
        )

    if declared == AXIS_STANDARD_PRESSURE_PA:
        for key, unit in (
            ("reference_pressure_Pa", "Pa"),
            ("standard_pressure_Pa", "Pa"),
            ("nist_janaf_pa", "Pa"),
            ("P_bar", "bar"),
            ("pressure_bar", "bar"),
        ):
            if key not in payload:
                continue
            amount, trail = convert_pressure_to_pa(payload.get(key), unit)
            if amount is not None:
                return _point_selection(
                    amount, key, trail or "identity:Pa", payload, condition_ranges
                )
        return _unavailable_selection(
            "source does not state standard_pressure_Pa",
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
        )

    q_token = _quantity_token(declared)
    if q_token is None and payload.get("semantics") in {"bound_not_point_ordering", "bound_not_point"}:
        reason = (declared.reason if isinstance(declared, State) else None) or (
            f"unsupported quantity {payload['quantity']!r}" if payload.get("quantity")
            else "declared quantity is unknown"
        )
        return SourceSelection(
            value=Value(ValueKind.CATEGORICAL, categorical=str(payload["semantics"])),
            field_name="semantics", reason=reason, condition_ranges=condition_ranges,
        )
    series = payload.get("series")
    if not (isinstance(series, list) and series):
        points_list = payload.get("points")
        series = points_list if _is_pressure_point_list(points_list) else None
    if isinstance(series, list) and series:
        return _series_selection_from_items(
            series, q_token, units, condition_ranges, parent=payload
        )

    tabulated = payload.get("tabulated_delta_fG_kJ_mol")
    if isinstance(tabulated, list) and tabulated and q_token in {None, Quantity.DELTA_FG}:
        if q_token is Quantity.DELTA_FG:
            points: list[tuple[Decimal, Decimal]] = []
            for item in tabulated:
                if not isinstance(item, Mapping):
                    continue
                t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, item)
                v_sel = select_declared_source(Quantity.DELTA_FG, "kJ_per_mol", item)
                if t_sel.amount is not None and v_sel.amount is not None:
                    points.append((t_sel.amount, v_sel.amount))
            if points:
                return SourceSelection(
                    value=Value(ValueKind.SERIES, series=tuple(points)),
                    field_name="tabulated_delta_fG_kJ_mol",
                    unit_trail="as_published",
                    condition_ranges=condition_ranges,
                )
        elif q_token is None:
            return _unavailable_selection(
                "tabulated_delta_fG_kJ_mol present but declared quantity is unknown",
                condition_ranges=condition_ranges,
                field_name="tabulated_delta_fG_kJ_mol",
            )

    if q_token is None:
        if payload.get("source_form") and payload.get("coefficients"):
            reason = declared.reason if isinstance(declared, State) else "declared quantity is unknown"
            return _unavailable_selection(
                f"{reason}; source prints evaluator {payload['source_form']}; no observable selected",
                condition_ranges=condition_ranges, field_name="source_form",
            )
        if payload.get("segments"):
            return SourceSelection(
                value=Value(
                    ValueKind.EXPRESSION,
                    expression_text="evaluator_segments_as_published",
                    expression_domain=str(payload.get("evaluator_family") or "segments"),
                ),
                field_name="segments",
                condition_ranges=condition_ranges,
            )
        if payload.get("delta_f_H_298_15") or payload.get(
            "formation_enthalpy_298_15_K_as_published"
        ):
            domain = "J_per_mol"
            raw = payload.get("formation_enthalpy_298_15_K_as_published")
            if isinstance(raw, Mapping):
                domain = str(payload.get("units_as_published") or domain)
            elif payload.get("units_as_published"):
                domain = str(payload.get("units_as_published"))
            return SourceSelection(
                value=Value(
                    ValueKind.EXPRESSION,
                    expression_text="delta_fH_298.15_as_published",
                    expression_domain=domain,
                ),
                field_name="delta_f_H_298_15",
                condition_ranges=condition_ranges,
            )
        if payload.get("g_parameter") or payload.get("functions"):
            return SourceSelection(
                value=Value(
                    ValueKind.EXPRESSION,
                    expression_text="compilation_coefficient_record",
                    expression_domain=str(payload.get("phase") or ""),
                ),
                field_name="g_parameter",
                condition_ranges=condition_ranges,
            )
        if payload.get("intervals"):
            return SourceSelection(
                value=Value(
                    ValueKind.EXPRESSION,
                    expression_text="cea_intervals_as_published",
                    expression_domain=str(payload.get("cea_section") or "intervals"),
                ),
                field_name="intervals",
                condition_ranges=condition_ranges,
            )
        if condition_ranges:
            name, lo, hi = condition_ranges[0]
            return _unavailable_selection(
                (
                    f"declared quantity is unknown; no observable selected; "
                    f"{name} [{lo}, {hi}] is a temperature domain, not the observable"
                ),
                condition_ranges=condition_ranges,
                unused_ancillary=unused,
                field_name=name,
            )
        return _unavailable_selection(
            "declared quantity is unknown; "
            + ((declared.reason + "; ") if isinstance(declared, State) and declared.reason else "")
            + "refusing to pick a number",
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
        )

    named = _selection_from_named_field(payload, q_token, units, condition_ranges)
    if named is not None:
        return named

    if payload.get("source_form") and payload.get("coefficients"):
        form = str(payload.get("source_form") or "").strip()
        return _unavailable_selection(
            f"source prints evaluator {form}; form is not a stored series or point",
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
            field_name="source_form",
        )

    if payload.get("segments"):
        return SourceSelection(
            value=Value(
                ValueKind.EXPRESSION,
                expression_text="evaluator_segments_as_published",
                expression_domain=str(payload.get("evaluator_family") or "segments"),
            ),
            field_name="segments",
            condition_ranges=condition_ranges,
        )
    if payload.get("reference_pressure_Pa") is not None and q_token is Quantity.DELTA_FG:
        return SourceSelection(
            value=Value(
                ValueKind.EXPRESSION,
                expression_text="gibbs_table_as_published",
                expression_domain=str(payload.get("evaluator_family") or "gibbs_table"),
            ),
            field_name="reference_pressure_Pa",
            condition_ranges=condition_ranges,
        )
    if payload.get("semantics") in {"bound_not_point_ordering", "bound_not_point"}:
        return SourceSelection(
            value=Value(ValueKind.CATEGORICAL, categorical=str(payload.get("semantics"))),
            field_name="semantics",
            condition_ranges=condition_ranges,
        )
    gibbs_reason = _delta_fg_unavailable_reason(payload, q_token)
    if gibbs_reason:
        return _unavailable_selection(
            gibbs_reason,
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
        )
    present = [key for key in QUANTITY_SOURCE_FIELDS.get(q_token, ()) if key in payload]
    if present:
        reason = "; ".join(
            f"printed field {key} is null; absence is not a measured zero"
            if payload[key] is None else f"printed field {key} is not numeric: {payload[key]!r}"
            for key in present
        )
        return _unavailable_selection(reason, condition_ranges=condition_ranges, unused_ancillary=unused)
    if condition_ranges:
        name, lo, hi = condition_ranges[0]
        return _unavailable_selection(
            (
                f"no mapped numeric {q_token.value} selected; "
                f"{name} [{lo}, {hi}] is a temperature domain, not the observable"
            ),
            condition_ranges=condition_ranges,
            unused_ancillary=unused,
            field_name=name,
        )
    return _unavailable_selection(
        f"no mapped {q_token.value} field selected; source fields: " + ", ".join(payload),
        condition_ranges=condition_ranges,
        unused_ancillary=unused,
    )


def empty_value_from_payload(
    values: Mapping[str, Any] | list[Any] | None,
    obs_type: str | None,
    units: str | None,
    quantity: Quantity | State[Quantity] | None = None,
) -> tuple[Value, list[dict[str, Any]], SourceSelection]:
    """Return the archival Value plus printed-point dicts to explode.

    Explosion list items are ``{coord, value, unit, extra}``. Every numeric
    lift goes through ``select_declared_source``.
    """

    exploded: list[dict[str, Any]] = []
    if isinstance(values, list):
        values = (
            {"points": values} if _is_pressure_point_list(values) else {"series": values}
        )
    if not isinstance(values, Mapping):
        sel = select_declared_source(quantity, units, None)
        return sel.value, exploded, sel

    series = values.get("series")
    if isinstance(series, list) and series:
        for i, item in enumerate(series):
            exploded.append({"index": i, "item": item, "units": units})
        sel = select_declared_source(quantity, units, values)
        return sel.value, exploded, sel

    tabulated = values.get("tabulated_delta_fG_kJ_mol")
    if isinstance(tabulated, list) and tabulated:
        for i, item in enumerate(tabulated):
            exploded.append({"index": i, "item": item, "units": "kJ_per_mol"})
        sel = select_declared_source(quantity, "kJ_per_mol", values)
        return sel.value, exploded, sel

    sel = select_declared_source(quantity, units, values)
    return sel.value, exploded, sel


def _series_point_value(
    raw_item: Mapping[str, Any],
    q_token: Quantity | None,
    units: str | None,
) -> tuple[Decimal | None, str, tuple[str, ...]]:
    """Pick the printed value from the declared quantity, not the first numeric key."""

    sel = select_declared_source(q_token, units, raw_item)
    return sel.amount, sel.unit_trail, sel.unused_ancillary


# ---------------------------------------------------------------------------
# Lab-parameter vocabulary (printed extract name → schema field + unit)
# ---------------------------------------------------------------------------

LAB_PARAMETER_VOCAB_PATH = LITERATURE / "lab_parameter_vocabulary.yaml"
_VOCAB_CACHE: dict[Path, tuple["VocabEntry", ...]] = {}
_WALK_SKIP_KEYS = {
    "quote",
    "note",
    "headers_as_published",
    "fidelity_samples",
    "supersedes",
    "supersession_note",
    "missing_for_motzfeldt",
    "missing_note",
}


@dataclass(frozen=True)
class VocabEntry:
    printed: str
    field: str
    unit: str | None


@dataclass(frozen=True)
class LabHit:
    entry: VocabEntry
    amount: Decimal | None
    units: str
    locator: Locator
    as_published: str
    path: str
    mapping: Mapping[str, Any] | None = None
    text: str | None = None


def load_lab_parameter_vocabulary(path: Path | None = None) -> tuple[VocabEntry, ...]:
    target = (path or LAB_PARAMETER_VOCAB_PATH).resolve()
    cached = _VOCAB_CACHE.get(target)
    if cached is not None:
        return cached
    if not target.is_file():
        entries: tuple[VocabEntry, ...] = ()
        _VOCAB_CACHE[target] = entries
        return entries
    doc = load_yaml(target)
    rows = doc.get("entries") if isinstance(doc, Mapping) else None
    parsed: list[VocabEntry] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("printed") or not row.get("field"):
                continue
            unit = row.get("unit")
            parsed.append(
                VocabEntry(
                    printed=str(row["printed"]),
                    field=str(row["field"]),
                    unit=None if unit in (None, "") else str(unit),
                )
            )
    entries = tuple(parsed)
    _VOCAB_CACHE[target] = entries
    return entries


def _vocab_for_root(root: Path | None) -> tuple[VocabEntry, ...]:
    if root is not None:
        candidate = Path(root) / "data" / "literature" / "lab_parameter_vocabulary.yaml"
        if candidate.is_file():
            return load_lab_parameter_vocabulary(candidate)
    return load_lab_parameter_vocabulary()


def printed_names_for(field: str, vocabulary: tuple[VocabEntry, ...] | None = None) -> tuple[str, ...]:
    vocab = vocabulary if vocabulary is not None else load_lab_parameter_vocabulary()
    return tuple(e.printed for e in vocab if e.field == field)


def _lab_kind(field: str) -> str:
    if field.endswith("printed_composition") or field.endswith("initial_composition"):
        return "composition"
    if field.endswith("mass_kg"):
        return "mass"
    if field.endswith("area_m2"):
        return "area"
    if field.endswith("_Pa"):
        return "pressure"
    if field.endswith("_m3_s"):
        return "volumetric_flow"
    if (
        field.endswith("diameter_m")
        or field.endswith("length_m")
        or field.endswith("thickness_m")
    ):
        return "length"
    return "factor"


def _convert_lab_value(
    field: str, amount: object, units: str | None
) -> tuple[Decimal | None, str | None]:
    kind = _lab_kind(field)
    if kind == "mass":
        return convert_mass_to_kg(amount, units)
    if kind == "area":
        return convert_area_to_m2(amount, units)
    if kind == "length":
        return convert_length_to_m(amount, units)
    if kind == "pressure":
        return convert_pressure_to_pa(amount, units)
    if kind == "volumetric_flow":
        return convert_volumetric_flow_to_m3_s(amount, units)
    if kind == "factor":
        value = _as_dec_or_none(amount)
        if value is None:
            return None, "factor value is not numeric"
        return value, "identity:1"
    return None, f"unmapped lab field {field}"


def _output_unit_for(field: str) -> str:
    kind = _lab_kind(field)
    return {
        "mass": "kg",
        "area": "m2",
        "length": "m",
        "pressure": "Pa",
        "volumetric_flow": "m3/s",
        "factor": "1",
        "composition": "as_published",
    }.get(kind, "as_published")


def _looked_for_reason(field: str, vocabulary: tuple[VocabEntry, ...]) -> str:
    keys = printed_names_for(field, vocabulary)
    listed = " / ".join(keys) if keys else field
    kind = _lab_kind(field)
    if kind == "pressure":
        noun = "pressure"
    elif kind == "mass":
        noun = "mass"
    elif kind == "area":
        noun = "area"
    elif kind == "length":
        noun = "length"
    elif kind == "composition":
        noun = "composition"
    else:
        noun = field.rsplit(".", 1)[-1]
    return f"no {noun} field under keys {listed} in this extract"


_DEST_CACHE: dict[str, tuple[str, str | None]] = {}
_EXPERIMENT_SECTIONS: dict[str, type] = {
    "apparatus": Apparatus,
    "sample": Sample,
    "pressure_environment": PressureEnvironment,
}


def _unwrap_optional(tp: object) -> object:
    origin = get_origin(tp)
    if origin is Union or origin is UnionType:
        args = [item for item in get_args(tp) if item is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _is_located_type(tp: object) -> bool:
    origin = get_origin(tp) or tp
    return origin is Located


def _located_inner_type(tp: object) -> object:
    args = get_args(tp)
    return args[0] if args else Any


def _is_mapping_of_located(tp: object) -> bool:
    origin = get_origin(tp)
    if origin is None:
        return False
    try:
        if not issubclass(origin, collections.abc.Mapping):
            return False
    except TypeError:
        return False
    args = get_args(tp)
    return len(args) == 2 and _is_located_type(args[1])


def _destination_spec(field: str) -> tuple[str, str | None]:
    """Classify a vocab field path: scalar, string, mapping leaf, or composition."""

    cached = _DEST_CACHE.get(field)
    if cached is not None:
        return cached
    parts = field.split(".")
    if len(parts) < 2:
        _DEST_CACHE[field] = ("unknown", None)
        return _DEST_CACHE[field]
    section = _EXPERIMENT_SECTIONS.get(parts[0])
    if section is None:
        _DEST_CACHE[field] = ("unknown", None)
        return _DEST_CACHE[field]
    current: type = section
    path_so_far = [parts[0]]
    for index, name in enumerate(parts[1:]):
        hints = get_type_hints(current)
        if name not in hints:
            _DEST_CACHE[field] = ("unknown", None)
            return _DEST_CACHE[field]
        tp = _unwrap_optional(hints[name])
        path_so_far.append(name)
        if _is_mapping_of_located(tp):
            leaf = parts[index + 2 :]
            if not leaf:
                _DEST_CACHE[field] = ("unknown", None)
                return _DEST_CACHE[field]
            container = ".".join(path_so_far)
            inner = _located_inner_type(get_args(tp)[1])
            kind = "mapping_string_leaf" if inner is str else "mapping_any_leaf"
            _DEST_CACHE[field] = (kind, container)
            return _DEST_CACHE[field]
        if _is_located_type(tp):
            if index != len(parts) - 2:
                _DEST_CACHE[field] = ("unknown", None)
                return _DEST_CACHE[field]
            inner = _located_inner_type(tp)
            if field.endswith("printed_composition") or field.endswith(
                "initial_composition"
            ):
                kind = "composition"
            elif inner is str:
                kind = "string"
            else:
                kind = "scalar"
            _DEST_CACHE[field] = (kind, None)
            return _DEST_CACHE[field]
        origin = get_origin(tp) or tp
        if isinstance(origin, type) and is_dataclass(origin):
            current = origin
            continue
        _DEST_CACHE[field] = ("unknown", None)
        return _DEST_CACHE[field]
    _DEST_CACHE[field] = ("unknown", None)
    return _DEST_CACHE[field]


def _hit_from_value(
    entry: VocabEntry,
    value: object,
    parent_locator: Locator | None,
    path: str,
) -> LabHit | None:
    kind, _container = _destination_spec(entry.field)
    if kind == "composition" or _lab_kind(entry.field) == "composition":
        return None
    units = entry.unit
    loc = parent_locator
    raw = value
    mapping: Mapping[str, Any] | None = None
    if isinstance(value, Mapping):
        mapping = value
        if "value" not in value or value.get("value") is None:
            return None
        raw = value.get("value")
        if value.get("units") not in (None, ""):
            units = str(value.get("units"))
        loc = locator_from_mapping(value.get("locator")) or loc
    if loc is None:
        return None
    wants_string = kind in {"string", "mapping_string_leaf"}
    if kind == "mapping_any_leaf" and _as_dec_or_none(raw) is None:
        wants_string = True
    unit_text = str(units) if units else ""
    if wants_string:
        if raw is None or isinstance(raw, (bool, Mapping)):
            return None
        if isinstance(raw, (list, tuple)):
            text = "; ".join(str(item) for item in raw if item not in (None, ""))
        else:
            text = str(raw).strip()
        if not text:
            return None
        published = text if not unit_text else f"{text} {unit_text}".strip()
        return LabHit(
            entry=entry,
            amount=None,
            units=unit_text,
            locator=loc,
            as_published=published,
            path=path,
            mapping=mapping,
            text=text,
        )
    amount = _as_dec_or_none(raw)
    if amount is None:
        return None
    published = f"{raw} {unit_text}".strip()
    return LabHit(
        entry=entry,
        amount=amount,
        units=unit_text,
        locator=loc,
        as_published=published,
        path=path,
        mapping=mapping,
    )


def _walk_lab_hits(
    obj: object,
    by_printed: Mapping[str, VocabEntry],
    *,
    parent_locator: Locator | None = None,
    path: str = "",
    depth: int = 0,
) -> list[LabHit]:
    if depth > 14:
        return []
    hits: list[LabHit] = []
    if isinstance(obj, Mapping):
        loc = locator_from_mapping(obj.get("locator")) or parent_locator
        for key, value in obj.items():
            name = str(key)
            if name in _WALK_SKIP_KEYS:
                continue
            child = f"{path}.{name}" if path else name
            entry = by_printed.get(name)
            if entry is not None:
                hit = _hit_from_value(entry, value, loc, child)
                if hit is not None:
                    hits.append(hit)
            if name == "locator":
                continue
            hits.extend(
                _walk_lab_hits(
                    value,
                    by_printed,
                    parent_locator=loc,
                    path=child,
                    depth=depth + 1,
                )
            )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hits.extend(
                _walk_lab_hits(
                    item,
                    by_printed,
                    parent_locator=parent_locator,
                    path=f"{path}[{i}]",
                    depth=depth + 1,
                )
            )
    return hits


def collect_lab_hits(
    roots: Iterable[object],
    vocabulary: tuple[VocabEntry, ...],
    *,
    fallback_locator: Locator | None = None,
) -> list[LabHit]:
    by_printed = {e.printed: e for e in vocabulary}
    hits: list[LabHit] = []
    for root in roots:
        prefix = ""
        obj = root
        if isinstance(root, tuple) and len(root) == 2 and isinstance(root[0], str):
            prefix, obj = root
        hits.extend(
            _walk_lab_hits(
                obj, by_printed, parent_locator=fallback_locator, path=prefix
            )
        )
    return hits


def _located_from_hit(hit: LabHit, si: Decimal, trail: str | None) -> Located[Decimal]:
    converted = conversion_derivation(trail, hit.amount, hit.locator)
    mapping = hit.mapping or {}
    inferred = mapping.get("inferred") is True
    if inferred:
        extra = (
            "inferred=true",
            str(mapping.get("inference") or "extract marks inferred; derivation not supplied"),
            f"extract_value={hit.as_published}",
            f"extract_field={hit.entry.printed}",
        )
    else:
        extra = (f"as_published={hit.as_published}", f"printed={hit.entry.printed}")
    qualification = " ".join(
        [f"{key}=true" for key in ("upper_bound", "lower_bound") if mapping.get(key) is True]
        + [str(mapping.get(key) or "") for key in ("inference", "qualifier", "note", "quote")]
    )
    non_point = any(mapping.get(key) is True for key in ("upper_bound", "lower_bound")) or any(
        re.search(
            r"\b(?:less than|better than|did not exceed|about|approximately)\b"
            r"|[~≈]"
            r"|^\s*(?:(?:pressure|vacuum)\s*)?[<>≤≥](?:\s*\d|\s*$)",
            str(mapping.get(key) or ""), re.I,
        )
        for key in ("inference", "qualifier", "note", "quote")
    )
    state = State.of(si)
    if non_point:
        state = State.unknown(
            f"extract {hit.entry.printed} is a bound or approximate value, not a point; "
            f"extract_value={hit.as_published}; {qualification.strip()}"
        )
    return Located(
        state,
        locator=hit.locator,
        inference=Derivation(
            relation=(
                "extract_limit" if non_point else
                "extract_inference" if inferred else str(trail or "identity")
            ),
            inputs=(converted.inputs if converted else ()) + extra + (
                (f"unit_conversion={trail}",) if inferred else ()
            ),
            parameters=converted.parameters if converted else (
                ("original", Located(State.of(hit.amount), locator=hit.locator)),
            ),
            output_unit=_output_unit_for(hit.entry.field),
        ),
    )


def _unique_located(
    hits: list[LabHit],
) -> Located[Decimal] | None:
    converted: list[tuple[Decimal, LabHit, str | None]] = []
    numeric_hits = [hit for hit in hits if hit.amount is not None]
    for hit in numeric_hits:
        si, trail = _convert_lab_value(hit.entry.field, hit.amount, hit.units)
        if si is None:
            continue
        converted.append((si, hit, trail))
    if not converted:
        if not numeric_hits:
            return None
        _, why = _convert_lab_value(
            numeric_hits[0].entry.field, numeric_hits[0].amount, numeric_hits[0].units
        )
        return located_unknown(why or "missing unit")
    values = {item[0] for item in converted}
    if len(values) > 1:
        return None
    located = None
    for si, hit, trail in converted:
        located = _prefer_located(located, _located_from_hit(hit, si, trail))
    return located


def _hits_for(hits: list[LabHit], field: str) -> list[LabHit]:
    return [h for h in hits if h.entry.field == field]


def _unique_text_located(hits: list[LabHit]) -> Located[str] | None:
    ordered: list[tuple[str, LabHit]] = []
    for hit in hits:
        if hit.text is None:
            continue
        ordered.append((hit.text, hit))
    if not ordered:
        return None
    by_printed: dict[str, list[tuple[str, LabHit]]] = {}
    order: list[str] = []
    for text, hit in ordered:
        name = hit.entry.printed
        if name not in by_printed:
            order.append(name)
            by_printed[name] = []
        by_printed[name].append((text, hit))
    for name in order:
        group = by_printed[name]
        values = {item[0] for item in group}
        if len(values) == 1:
            text, hit = group[0]
            return located_value(text, hit.locator)
        equipment_group = [
            item for item in group if item[1].path.startswith("equipment.")
        ]
        equipment_values = {item[0] for item in equipment_group}
        if len(equipment_values) == 1:
            text, hit = equipment_group[0]
            return located_value(text, hit.locator)
    return None


def _mapping_located_from_hits(
    hits: list[LabHit], container: str
) -> dict[str, Located[Any]] | None:
    prefix = container + "."
    grouped: dict[str, list[LabHit]] = defaultdict(list)
    for hit in hits:
        field = hit.entry.field
        if field.startswith(prefix) and field != container:
            leaf = field[len(prefix) :]
            if leaf:
                grouped[leaf].append(hit)
    out: dict[str, Located[Any]] = {}
    for leaf, leaf_hits in grouped.items():
        text_hits = [hit for hit in leaf_hits if hit.text is not None]
        num_hits = [hit for hit in leaf_hits if hit.amount is not None]
        located: Located[Any] | None
        if text_hits and not num_hits:
            located = _unique_text_located(text_hits)
        elif num_hits and not text_hits:
            located = _unique_located(num_hits)
        else:
            located = None
        if located is not None:
            out[leaf] = located
    return out or None


def _merge_located_mapping(
    old: Mapping[str, Located[Any]] | None,
    new: Mapping[str, Located[Any]] | None,
) -> dict[str, Located[Any]] | None:
    if old is None:
        return dict(new) if new else None
    if new is None:
        return dict(old)
    out: dict[str, Located[Any]] = {}
    # Insertion-ordered union: existing keys keep their positions, new keys
    # append. set(old) | set(new) would leak PYTHONHASHSEED-dependent order
    # into serialized output.
    for key in dict.fromkeys((*old, *new)):
        merged = _prefer_located(old.get(key), new.get(key))
        if merged is not None:
            out[key] = merged
    return out or None


def _printed_composition_from_roots(
    roots: Iterable[object],
    vocabulary: tuple[VocabEntry, ...],
    *,
    fallback_locator: Locator | None = None,
) -> Located[Mapping[str, Any]] | None:
    names = {e.printed for e in vocabulary if e.field == "sample.printed_composition"}
    found: list[tuple[tuple[tuple[str, str], ...], Locator, str]] = []

    def walk(obj: object, parent_loc: Locator | None, depth: int) -> None:
        if depth > 14:
            return
        if isinstance(obj, Mapping):
            loc = locator_from_mapping(obj.get("locator")) or parent_loc
            for key, value in obj.items():
                name = str(key)
                if name in names and isinstance(value, Mapping):
                    comps = {
                        str(k): v
                        for k, v in value.items()
                        if k not in _WALK_SKIP_KEYS
                        and k != "locator"
                        and _as_dec_or_none(v) is not None
                    }
                    if comps and loc is not None:
                        fingerprint = tuple(
                            sorted((k, _dec_str(as_decimal(v))) for k, v in comps.items())
                        )
                        found.append((fingerprint, loc, name))
                if name not in _WALK_SKIP_KEYS and name != "locator":
                    walk(value, loc, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent_loc, depth + 1)

    for root in roots:
        obj = (
            root[1]
            if isinstance(root, tuple) and len(root) == 2 and isinstance(root[0], str)
            else root
        )
        walk(obj, fallback_locator, 0)
    if not found:
        return None
    fingerprints = {item[0] for item in found}
    if len(fingerprints) > 1:
        # Charge keys beat residual composition_wt_pct of the same melt.
        preferred = [
            item for item in found if item[2] in _CHARGE_PRINTED_COMPOSITION_NAMES
        ]
        preferred_fps = {item[0] for item in preferred}
        if len(preferred_fps) != 1:
            return None
        fingerprint, loc, _name = preferred[0]
        return located_value({k: v for k, v in fingerprint}, loc)
    fingerprint, loc, _name = found[0]
    return located_value({k: v for k, v in fingerprint}, loc)


def _lab_roots(
    equipment: object,
    values: object = None,
    extra: object = None,
) -> list[object]:
    roots: list[object] = []
    if isinstance(equipment, Mapping) and equipment:
        roots.append(("equipment", equipment))
    if isinstance(values, Mapping) and values:
        roots.append(("values", values))
    if extra is not None and extra is not equipment and extra is not values:
        roots.append(("extra", extra))
    return roots


def _form_and_container(
    equipment: object,
) -> tuple[Located[str] | None, Located[str] | None]:
    if not isinstance(equipment, Mapping):
        return None, None
    form_located: Located[str] | None = None
    container_located: Located[str] | None = None
    raw_sample = equipment.get("sample")
    if not isinstance(raw_sample, Mapping):
        return None, None
    loc = locator_from_mapping(raw_sample.get("locator"))
    form = raw_sample.get("form")
    container = raw_sample.get("container")
    if isinstance(form, str) and form:
        form_located = located_value(form, loc)
    elif isinstance(form, Mapping) and form.get("value") is not None:
        form_located = located_value(
            str(form["value"]), locator_from_mapping(form.get("locator")) or loc
        )
    if isinstance(container, str) and container:
        container_located = located_value(container, loc)
    elif isinstance(container, Mapping) and container.get("value") is not None:
        container_located = located_value(
            str(container["value"]),
            locator_from_mapping(container.get("locator")) or loc,
        )
    return form_located, container_located


def _prefer_located(
    old: Located[Any] | None, new: Located[Any] | None
) -> Located[Any] | None:
    if new is None:
        return old
    if old is None:
        return new
    if old.inference is not None and old.inference.relation == "extract_limit":
        return old
    if new.inference is not None and new.inference.relation == "extract_limit":
        return new
    if old.state.is_unknown and new.state.is_value:
        return new
    if old.state.is_value and new.state.is_value and old.state.value != new.state.value:
        return None
    if (
        old.state.is_value and new.state.is_value and new.inference is not None
        and "inferred=true" in new.inference.inputs
    ):
        return new
    return old


def _merge_geometry(
    old: ApparatusGeometry | None, new: ApparatusGeometry | None
) -> ApparatusGeometry | None:
    if old is None:
        return new
    if new is None:
        return old
    kwargs = {
        name: _prefer_located(getattr(old, name), getattr(new, name))
        for name in (
            "orifice_area_m2",
            "orifice_diameter_m",
            "clausing_factor",
            "orifice_to_sample_area_ratio",
            "exposed_area_m2",
            "chamber_length_m",
            "chamber_volume_m3",
            "orifice_channel_length_m",
            "orifice_count",
            "orifice_shape",
        )
    }
    kwargs["cell_internal_dimensions"] = _merge_located_mapping(
        old.cell_internal_dimensions, new.cell_internal_dimensions
    )
    kwargs["chamber_dimensions"] = _merge_located_mapping(
        old.chamber_dimensions, new.chamber_dimensions
    )
    if not any(v is not None for v in kwargs.values()):
        return None
    return ApparatusGeometry(**kwargs)


def _merge_apparatus(
    old: Apparatus | None, new: Apparatus | None
) -> Apparatus | None:
    if old is None:
        return new
    if new is None:
        return old
    cell = _prefer_located(old.cell_material_and_liner, new.cell_material_and_liner)
    geometry = _merge_geometry(old.geometry, new.geometry)
    ionization = _merge_located_mapping(old.ionization, new.ionization)
    calibration = _merge_located_mapping(old.calibration, new.calibration)
    temperature_measurement = _merge_located_mapping(
        old.temperature_measurement, new.temperature_measurement
    )
    wall = _merge_located_mapping(old.wall, new.wall)
    if all(
        item is None
        for item in (
            cell,
            geometry,
            ionization,
            calibration,
            temperature_measurement,
            wall,
        )
    ):
        return None
    return Apparatus(
        cell_material_and_liner=cell,
        geometry=geometry,
        ionization=ionization,
        calibration=calibration,
        temperature_measurement=temperature_measurement,
        wall=wall,
    )


def _merge_pressure(
    old: PressureEnvironment, new: PressureEnvironment
) -> PressureEnvironment:
    total = _prefer_located(old.total_pressure_Pa, new.total_pressure_Pa)
    if total is None:
        total = located_unknown(
            "conflicting printed pressures; not collapsed into one number"
        )
    kn_old = old.regime.knudsen_number_orifice if old.regime else None
    kn_new = new.regime.knudsen_number_orifice if new.regime else None
    knc_old = old.regime.knudsen_number_chamber if old.regime else None
    knc_new = new.regime.knudsen_number_chamber if new.regime else None
    regime_class = old.regime.regime_class
    if regime_class.is_unknown and new.regime.regime_class.is_value:
        regime_class = new.regime.regime_class
    sweep = old.sweep_gas
    if sweep.state.is_unknown and new.sweep_gas.state.is_value:
        sweep = new.sweep_gas
    return PressureEnvironment(
        total_pressure_Pa=total,
        sweep_gas=sweep,
        regime=FlowRegime(
            regime_class=regime_class,
            knudsen_number_orifice=_prefer_located(kn_old, kn_new),
            knudsen_number_chamber=_prefer_located(knc_old, knc_new),
        ),
        gauge=_merge_located_mapping(old.gauge, new.gauge),
        pressure_profile=_prefer_located(old.pressure_profile, new.pressure_profile),
        pumping=_merge_located_mapping(old.pumping, new.pumping),
        cell_internal_pressure_note=_prefer_located(
            old.cell_internal_pressure_note, new.cell_internal_pressure_note
        ),
    )


def _merge_experiment_lab_params(
    existing: Experiment,
    sample: Sample,
    apparatus: Apparatus | None,
    pressure_env: PressureEnvironment,
) -> Experiment:
    merged_sample = Sample(
        mass_kg=_prefer_located(existing.sample.mass_kg, sample.mass_kg),
        initial_composition=_prefer_located(
            existing.sample.initial_composition, sample.initial_composition
        ),
        printed_composition=_prefer_located(
            existing.sample.printed_composition, sample.printed_composition
        ),
        form=_prefer_located(existing.sample.form, sample.form),
        container=_prefer_located(existing.sample.container, sample.container),
        composition_class=_prefer_located(
            existing.sample.composition_class, sample.composition_class
        ),
        characterization=_prefer_located(
            existing.sample.characterization, sample.characterization
        ),
        surface_area_m2=_prefer_located(
            existing.sample.surface_area_m2, sample.surface_area_m2
        ),
        pretreatment=_prefer_located(
            existing.sample.pretreatment, sample.pretreatment
        ),
    )
    return replace(
        existing,
        sample=merged_sample,
        apparatus=_merge_apparatus(existing.apparatus, apparatus),
        pressure_environment=_merge_pressure(existing.pressure_environment, pressure_env),
    )


def apparatus_from_equipment(
    equipment: object,
    *,
    vocabulary: tuple[VocabEntry, ...] | None = None,
    values: object = None,
    locator: Locator | None = None,
) -> Apparatus | None:
    vocab = vocabulary if vocabulary is not None else load_lab_parameter_vocabulary()
    roots = _lab_roots(equipment, values)
    hits = collect_lab_hits(roots, vocab, fallback_locator=locator)
    geometry_kwargs: dict[str, Located[Decimal]] = {}
    for dest, field in (
        ("orifice_area_m2", "apparatus.geometry.orifice_area_m2"),
        ("orifice_diameter_m", "apparatus.geometry.orifice_diameter_m"),
        ("clausing_factor", "apparatus.geometry.clausing_factor"),
        ("orifice_to_sample_area_ratio", "apparatus.geometry.orifice_to_sample_area_ratio"),
        ("exposed_area_m2", "apparatus.geometry.exposed_area_m2"),
        ("chamber_length_m", "apparatus.geometry.chamber_length_m"),
    ):
        located = _unique_located(_hits_for(hits, field))
        if located is not None:
            geometry_kwargs[dest] = located
    cell = _unique_text_located(_hits_for(hits, "apparatus.cell_material_and_liner"))
    ionization = _mapping_located_from_hits(hits, "apparatus.ionization")
    calibration = _mapping_located_from_hits(hits, "apparatus.calibration")
    temperature_measurement = _mapping_located_from_hits(
        hits, "apparatus.temperature_measurement"
    )
    wall = _mapping_located_from_hits(hits, "apparatus.wall")
    geometry = ApparatusGeometry(**geometry_kwargs) if geometry_kwargs else None
    if all(
        item is None
        for item in (
            cell,
            geometry,
            ionization,
            calibration,
            temperature_measurement,
            wall,
        )
    ):
        return None
    return Apparatus(
        cell_material_and_liner=cell,
        geometry=geometry,
        ionization=ionization,
        calibration=calibration,
        temperature_measurement=temperature_measurement,
        wall=wall,
    )


def sample_from_equipment(
    equipment: object,
    *,
    vocabulary: tuple[VocabEntry, ...] | None = None,
    values: object = None,
    locator: Locator | None = None,
) -> Sample:
    """Transfer a stated sample payload; never invent mass, form, or units."""

    vocab = vocabulary if vocabulary is not None else load_lab_parameter_vocabulary()
    roots = _lab_roots(equipment, values)
    hits = collect_lab_hits(roots, vocab, fallback_locator=locator)
    mass_located = _unique_located(_hits_for(hits, "sample.mass_kg"))
    form_located = _unique_text_located(_hits_for(hits, "sample.form"))
    container_located = _unique_text_located(_hits_for(hits, "sample.container"))
    hard_form, hard_container = _form_and_container(equipment)
    form_located = _prefer_located(form_located, hard_form)
    container_located = _prefer_located(container_located, hard_container)
    printed = _printed_composition_from_roots(
        roots, vocab, fallback_locator=locator
    )
    if printed is None:
        oxide_map = _initial_oxide_map_from_values(values)
        printed, _ = _located_printed_and_initial(oxide_map, locator)
    initial = None
    if printed is not None and printed.state.is_value:
        raw = printed.state.value
        if isinstance(raw, Mapping):
            wt = {
                str(k): as_decimal(v)
                for k, v in raw.items()
                if str(k) in _OXIDE_COMPONENT_KEYS and _as_dec_or_none(v) is not None
            }
            if len(wt) >= 2:
                _, initial = _located_printed_and_initial(wt, printed.locator or locator)
    if (
        mass_located is None
        and form_located is None
        and container_located is None
        and printed is None
        and initial is None
    ):
        return Sample()
    return Sample(
        mass_kg=mass_located,
        form=form_located,
        container=container_located,
        printed_composition=printed,
        initial_composition=initial,
    )


def pressure_from_equipment(
    equipment: object,
    *,
    vocabulary: tuple[VocabEntry, ...] | None = None,
    values: object = None,
    locator: Locator | None = None,
) -> PressureEnvironment:
    vocab = vocabulary if vocabulary is not None else load_lab_parameter_vocabulary()
    roots = _lab_roots(equipment, values)
    hits = collect_lab_hits(roots, vocab, fallback_locator=locator)
    field = "pressure_environment.total_pressure_Pa"
    pressure_hits = _hits_for(hits, field)
    located = _unique_located(pressure_hits)
    pumping = _mapping_located_from_hits(hits, "pressure_environment.pumping")
    gauge = _mapping_located_from_hits(hits, "pressure_environment.gauge")
    note = _unique_text_located(
        _hits_for(hits, "pressure_environment.cell_internal_pressure_note")
    )
    kn_orifice = _unique_located(
        _hits_for(hits, "pressure_environment.regime.knudsen_number_orifice")
    )
    kn_chamber = _unique_located(
        _hits_for(hits, "pressure_environment.regime.knudsen_number_chamber")
    )

    def _with_mappings(env: PressureEnvironment) -> PressureEnvironment:
        return replace(
            env,
            pumping=pumping,
            gauge=gauge,
            cell_internal_pressure_note=note,
            regime=FlowRegime(
                regime_class=env.regime.regime_class,
                knudsen_number_orifice=kn_orifice or env.regime.knudsen_number_orifice,
                knudsen_number_chamber=kn_chamber or env.regime.knudsen_number_chamber,
            ),
        )

    if located is None:
        converted_si = []
        unit_why = None
        for hit in pressure_hits:
            if hit.amount is None:
                continue
            si, trail = _convert_lab_value(hit.entry.field, hit.amount, hit.units)
            if si is None:
                unit_why = trail
            else:
                converted_si.append(si)
        if len(set(converted_si)) > 1:
            return _with_mappings(
                unknown_pressure_environment(
                    "conflicting printed pressures; not collapsed into one number"
                )
            )
        if unit_why:
            return _with_mappings(unknown_pressure_environment(unit_why))
        return _with_mappings(
            unknown_pressure_environment(_looked_for_reason(field, vocab))
        )
    return PressureEnvironment(
        total_pressure_Pa=located,
        sweep_gas=located_unknown(
            "no sweep-gas field under keys sweep_gas / carrier_gas / buffer_gas in this extract"
        ),
        regime=FlowRegime(
            regime_class=State.unknown(
                "no flow-regime field under keys regime_class / knudsen_number_orifice "
                "/ knudsen_number_chamber in this extract"
            ),
            knudsen_number_orifice=kn_orifice,
            knudsen_number_chamber=kn_chamber,
        ),
        gauge=gauge,
        pumping=pumping,
        cell_internal_pressure_note=note,
    )


def _sample_matched_roots(equipment: object, series_item: object) -> list[object]:
    if not isinstance(series_item, Mapping):
        return []
    sid = series_item.get("sample") or series_item.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return []
    matched: list[object] = []

    def walk(obj: object) -> None:
        if isinstance(obj, Mapping):
            if obj.get("sample") == sid or obj.get("id") == sid:
                matched.append(obj)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(equipment)
    return matched


# Printed oxygen keys. Exact names only: a sibling such as
# ``log10_pO2_bar`` next to ``pO2_inference`` is a calculated buffer,
# not a printed fugacity, and must not be relabelled printed (b-526).
_PRINTED_LOG_FO2_KEYS = frozenset({"log_fO2", "log10_fO2", "logfO2"})
_PRINTED_PO2_KEYS = frozenset({"oxygen_partial_pressure"})
_OXYGEN_TABLE_KEYS = frozenset({"series", "rows", "points"})
_OXYGEN_LOG_UNITS = frozenset({
    "",
    "log10",
    "log10(bar)",
    "log10 bar",
    "log10(atm)",
    "log10 atm",
    "log",
    "dex",
})
_POWER_OF_TEN = re.compile(r"^10\^([+-]?(?:\d+(?:\.\d+)?|\.\d+))$")
_POINT_REJECT = re.compile(
    r"^\s*(?:[<>≤≥~≈]|10\s*\^\s*[+-]?\d.*\s+to\s+)",
    re.I,
)
# Half a tenth of a dex: wide enough for the 0.0057 atm/bar offset and for
# a one-decimal printed place, tight enough that -9.1 and -8 are not one fact.
_OXYGEN_FRAME_TOLERANCE = Decimal("0.05")
_BAR_PA = Decimal("100000")


@dataclass(frozen=True)
class PrintedOxygenFacts:
    log_fO2: Located[Decimal] | None = None
    oxygen_partial_pressure_Pa: Located[Value] | None = None


def _normalize_printed_token(raw: object) -> str:
    return str(raw).strip().replace("−", "-").replace("–", "-").replace(" ", "")


def _power_of_ten(text: str) -> Decimal | None:
    match = _POWER_OF_TEN.match(text)
    if match is None:
        return None
    # 10^n for a printed exponent. Decimal(10)**n logs back to n
    # (10^-9.1 -> exactly -9.1), so the atm-frame log is the exponent.
    return Decimal(10) ** Decimal(match.group(1))


def _printed_decimal(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool) or isinstance(raw, (Mapping, list, tuple)):
        return None
    if isinstance(raw, str):
        text = _normalize_printed_token(raw)
        if not text or _POINT_REJECT.match(text):
            return None
        powered = _power_of_ten(text)
        if powered is not None:
            return powered
    try:
        value = as_decimal(raw)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not value.is_finite():
        return None
    return value


def _oxygen_node_rejected(node: Mapping[str, Any], raw: object) -> bool:
    if node.get("inferred") is True:
        return True
    if any(node.get(key) is True for key in ("upper_bound", "lower_bound")):
        return True
    semantics = str(node.get("semantics") or "").lower()
    if "bound" in semantics or "range" in semantics or "not_point" in semantics:
        return True
    qualifier = str(node.get("qualifier") or "")
    if re.search(r"\b(?:about|approximately)\b|[~≈]", qualifier, re.I):
        return True
    if isinstance(raw, str) and _POINT_REJECT.match(raw.strip()):
        return True
    if isinstance(raw, (list, tuple)):
        return True
    return False


def _locator_with_note(locator: Locator | None, extra: str | None) -> Locator | None:
    text = str(extra or "").strip()
    if locator is None or not text:
        return locator
    existing = str(locator.note or "")
    if text in existing:
        return locator
    note = f"{existing}; {text}".strip("; ")
    return replace(locator, note=note)


def _pressure_located(
    amount_in_printed_unit: Decimal,
    units: str,
    locator: Locator,
    *,
    printed_key: str,
    published: str,
    quote: str | None,
) -> Located[Value] | None:
    si, trail = convert_pressure_to_pa(amount_in_printed_unit, units)
    if si is None or si <= 0 or trail is None:
        return None
    # Identity units stay printed (no derivation). A real conversion keeps
    # the printed key on the derivation so it cannot be relabelled inferred.
    base = conversion_derivation(trail, amount_in_printed_unit, locator)
    inference = None
    if base is not None:
        extras = [f"as_published={published}", f"printed={printed_key}"]
        if quote:
            extras.append(f"quote={quote}")
        inference = Derivation(
            relation=base.relation,
            inputs=base.inputs + tuple(extras),
            parameters=base.parameters,
            output_unit=base.output_unit,
        )
    # Point pressures are built only inside _point_selection. This caller
    # supplies the already-converted pascal amount; it does not construct Value.
    point = _point_selection(si, printed_key, trail, {}, ())
    return Located(
        State.of(point.value),
        locator=_locator_with_note(locator, f"quote={quote}" if quote else None),
        inference=inference,
    )


def _annotate_atm_bar_offset(
    log_located: Located[Decimal],
    pressure_pa: Decimal,
    pressure_units: str,
) -> Located[Decimal]:
    # Premise: oxygen_condition is log10(fO2 / 1 bar). _log_pressure uses
    # fO2/bar = pO2_Pa / 100000. A paper that also prints pO2 = 10^L atm
    # has stated log10(pO2 / 1 atm) = L.
    # Algebra: log10(fO2/bar) = L + log10(101325/100000) = L + log10(1.01325).
    # Unit check: 1 atm = 101325 Pa, 1 bar = 100000 Pa.
    #   10^-9.1 atm -> log10(p_Pa/100000) = -9.1 + 0.0057166... = -9.094283...
    # Sanity: L = 0 (exactly 1 atm) stays printed 0. The pressure route
    #   yields +0.0057166..., which is log10(1.01325), not a second measurement.
    # The printed log is kept. The offset is recorded here and is not applied,
    # because rewriting -9.1 to -9.094 would replace the author's stated fugacity.
    # The companion pressure still lands, in pascals, via atm_to_Pa.
    if "atm" not in pressure_units.lower():
        return log_located
    log_value = log_located.state.value
    if not isinstance(log_value, Decimal):
        return log_located
    bar_log = (pressure_pa / _BAR_PA).log10()
    if abs(bar_log - log_value) <= Decimal("0.001"):
        return log_located
    offset = (PA_PER_ATM_DEC / _BAR_PA).log10()
    note = (
        "printed log kept as stated; "
        f"log10(fO2/bar)=log10(p_Pa/100000)={bar_log} "
        f"= printed_log + log10(101325/100000) (offset {offset} dex) "
        "because the companion pressure is in atm, not bar"
    )
    return Located(
        log_located.state,
        locator=_locator_with_note(log_located.locator, note),
        inference=None,
    )


def _frames_agree(log_value: Decimal, pressure_pa: Decimal) -> bool:
    if pressure_pa <= 0:
        return False
    atm_log = (pressure_pa / PA_PER_ATM_DEC).log10()
    bar_log = (pressure_pa / _BAR_PA).log10()
    return (
        abs(atm_log - log_value) <= _OXYGEN_FRAME_TOLERANCE
        or abs(bar_log - log_value) <= _OXYGEN_FRAME_TOLERANCE
    )


def _walk_printed_oxygen(
    obj: object,
    fallback: Locator | None,
    logs: list[tuple[Decimal, Located[Decimal]]],
    pressures: list[tuple[Decimal, Located[Value], str]],
    *,
    skip_tables: bool,
    depth: int = 0,
) -> None:
    if depth > 12 or fallback is None:
        return
    if isinstance(obj, list):
        for item in obj:
            _walk_printed_oxygen(
                item, fallback, logs, pressures, skip_tables=skip_tables, depth=depth + 1
            )
        return
    if not isinstance(obj, Mapping):
        return
    local = locator_from_mapping(obj.get("locator")) or fallback
    for key, value in obj.items():
        name = str(key)
        if skip_tables and name in _OXYGEN_TABLE_KEYS:
            continue
        if name in _PRINTED_LOG_FO2_KEYS or name in _PRINTED_PO2_KEYS:
            node = value if isinstance(value, Mapping) else {}
            raw = value.get("value") if isinstance(value, Mapping) else value
            if isinstance(value, Mapping) and _oxygen_node_rejected(value, raw):
                continue
            if not isinstance(value, Mapping) and isinstance(raw, str) and _POINT_REJECT.match(raw.strip()):
                continue
            amount = _printed_decimal(raw)
            if amount is None:
                continue
            locator = locator_from_mapping(node.get("locator")) or local
            if locator is None:
                continue
            quote = node.get("quote") if isinstance(node, Mapping) else None
            quote_text = str(quote).strip() if quote else None
            if name in _PRINTED_LOG_FO2_KEYS:
                # 10^n strings are pressure amounts (e.g. "10^-9.1" atm), not
                # log values. Feeding them through _printed_decimal would land
                # ~1e-9 as fO2_log and stamp oxygen_condition near zero.
                if isinstance(raw, str) and _power_of_ten(_normalize_printed_token(raw)) is not None:
                    continue
                units = str(node.get("units") or node.get("unit") or "").strip().lower()
                if units not in _OXYGEN_LOG_UNITS:
                    continue
                logs.append((
                    amount,
                    Located(
                        State.of(amount),
                        locator=_locator_with_note(
                            locator, f"quote={quote_text}" if quote_text else None
                        ),
                        inference=None,
                    ),
                ))
            else:
                units = str(node.get("units") or node.get("unit") or "").strip()
                if not units:
                    continue
                published = f"{raw} {units}".strip()
                located = _pressure_located(
                    amount, units, locator,
                    printed_key=name, published=published, quote=quote_text,
                )
                if located is None or not located.state.is_value:
                    continue
                pa = located.state.value
                if not isinstance(pa, Value) or pa.kind is not ValueKind.POINT or pa.point is None:
                    continue
                pressures.append((pa.point, located, units))
            continue
        _walk_printed_oxygen(
            value, local, logs, pressures, skip_tables=skip_tables, depth=depth + 1
        )


def _unique_oxygen(candidates: list[tuple[Decimal, object]]) -> object | None:
    if not candidates:
        return None
    values = {item[0] for item in candidates}
    if len(values) != 1:
        return None
    return candidates[0][1]


def collect_printed_oxygen(
    payloads: Iterable[object],
    fallback_locator: Locator | None,
    *,
    skip_tables: bool = False,
) -> PrintedOxygenFacts:
    """Land a printed log fO2 and/or oxygen partial pressure. Nothing else.

    A bare printed log is stored as the author wrote it. It is not shifted by
    log10(1.01325). See ``_annotate_atm_bar_offset`` for the atm-versus-bar
    algebra. Two printed forms that disagree by more than half a tenth of a
    dex in both the atm frame and the bar frame are not a single fact: neither
    is landed. Bounds, ranges, and ``inferred: true`` stay refusals.
    """

    logs: list[tuple[Decimal, Located[Decimal]]] = []
    pressures: list[tuple[Decimal, Located[Value], str]] = []
    if fallback_locator is None:
        return PrintedOxygenFacts()
    for payload in payloads:
        _walk_printed_oxygen(
            payload, fallback_locator, logs, pressures, skip_tables=skip_tables
        )
    log_located = _unique_oxygen([(value, located) for value, located in logs])
    pressure_located = _unique_oxygen(
        [(value, (located, units)) for value, located, units in pressures]
    )
    if isinstance(log_located, Located) and isinstance(pressure_located, tuple):
        located_pressure, units = pressure_located
        pa = located_pressure.state.value
        point = pa.point if isinstance(pa, Value) else None
        log_point = log_located.state.value
        if (
            isinstance(point, Decimal)
            and isinstance(log_point, Decimal)
            and not _frames_agree(log_point, point)
        ):
            return PrintedOxygenFacts()
        if isinstance(point, Decimal) and isinstance(log_point, Decimal):
            log_located = _annotate_atm_bar_offset(log_located, point, units)
    return PrintedOxygenFacts(
        log_fO2=log_located if isinstance(log_located, Located) else None,
        oxygen_partial_pressure_Pa=(
            pressure_located[0] if isinstance(pressure_located, tuple) else None
        ),
    )


def point_lab_conditions(
    *,
    series_item: object,
    equipment: object,
    vocabulary: tuple[VocabEntry, ...],
    locator: Locator | None,
) -> dict[str, Located[Decimal]]:
    roots: list[object] = []
    if isinstance(series_item, Mapping):
        roots.append(series_item)
    roots.extend(_sample_matched_roots(equipment, series_item))
    if not roots:
        return {}
    hits = collect_lab_hits(roots, vocabulary, fallback_locator=locator)
    out: dict[str, Located[Decimal]] = {}
    for key, field in (
        ("mass_kg", "sample.mass_kg"),
        ("exposed_area_m2", "apparatus.geometry.exposed_area_m2"),
        ("total_pressure_Pa", "pressure_environment.total_pressure_Pa"),
        ("orifice_area_m2", "apparatus.geometry.orifice_area_m2"),
        ("orifice_diameter_m", "apparatus.geometry.orifice_diameter_m"),
    ):
        located = _unique_located(_hits_for(hits, field))
        if located is not None and located.state.is_value:
            out[key] = located
    return out


# ---------------------------------------------------------------------------
# Index / alias / work identity
# ---------------------------------------------------------------------------


def load_index(path: Path | None = None) -> dict[str, dict[str, Any]]:
    doc = load_yaml(path or INDEX_PATH)
    if not isinstance(doc, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("sources") or []:
        if isinstance(row, Mapping) and row.get("source_id"):
            out[str(row["source_id"])] = dict(row)
    return out


def load_aliases(path: Path | None = None) -> dict[str, str]:
    target = path or ALIASES_PATH
    if not target.is_file():
        return {}
    doc = load_yaml(target)
    if not isinstance(doc, Mapping):
        return {}
    raw = doc.get("aliases") or {}
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def work_id_for(
    *,
    citation: str,
    doi: str | None,
    source_id: str | None,
    aliases: Mapping[str, str],
) -> tuple[str, str | None]:
    canonical = canonicalize_doi(doi) if doi else None
    if source_id and source_id in aliases:
        return aliases[source_id], canonical
    if canonical:
        if canonical in aliases:
            return aliases[canonical], canonical
        return canonical, canonical
    if not citation:
        fallback = source_id or "missing-citation"
        return citation_hash(fallback), None
    return citation_hash(citation), None


def _locator_layer(loc_path: str) -> str | None:
    lowered = loc_path.lower().replace("\\", "/")
    if "tables/" in lowered or lowered.endswith(".csv"):
        return "table"
    if (
        "ocr/" in lowered
        or "/vlm/" in lowered
        or "mineru" in lowered
        or "ocr-artifacts" in lowered
        or lowered.endswith(".md")
    ):
        return "ocr"
    if lowered.endswith(".pdf") or "/raw/" in lowered:
        return "pdf"
    return None


def choose_read_from(work: Work, locator: Locator | None) -> str:
    files = work.source_files.files
    loc_path = locator.source_path if locator is not None else None
    if loc_path:
        for asset in files:
            if asset.path != "unknown" and (
                asset.path in str(loc_path) or str(loc_path) in asset.path
            ):
                return asset.asset_id
        layer = _locator_layer(str(loc_path))
        if layer == "table":
            for asset in files:
                if asset.role is AssetRole.TABLE_CSV:
                    return asset.asset_id
            return _unknown_asset_id(files)
        if layer == "ocr":
            for asset in files:
                if asset.role is AssetRole.MINERU_MD and asset.path != "unknown":
                    return asset.asset_id
            # Stated OCR/md layer with no matching INDEX asset: never PDF.
            return _unknown_asset_id(files)
        if layer == "pdf":
            for asset in files:
                if asset.role is AssetRole.PDF and asset.path != "unknown":
                    return asset.asset_id
            return _unknown_asset_id(files)
    for asset in files:
        if asset.role is AssetRole.PDF and asset.path != "unknown":
            return asset.asset_id
    if files:
        return files[0].asset_id
    return "unknown"


def _unknown_asset_id(files: tuple[SourceFile, ...] | list[SourceFile]) -> str:
    for asset in files:
        if asset.asset_id.startswith("unknown:"):
            return asset.asset_id
    return "unknown"


def unmatched_read_from_reason(locator: Locator | None, read_from: str) -> str | None:
    loc_path = locator.source_path if locator is not None else None
    if loc_path and (read_from == "unknown" or str(read_from).startswith("unknown:")):
        return f"locator source_path {loc_path!r} has no matching INDEX asset"
    return None


def source_files_for(
    source_id: str,
    index_row: Mapping[str, Any] | None,
) -> SourceFiles:
    files: list[SourceFile] = []
    corpus = (index_row or {}).get("corpus") or {}
    commit_raw = corpus.get("commit")
    commit = (
        State.of(str(commit_raw))
        if commit_raw
        else State.unknown("INDEX does not give corpus commit")
    )
    raw = corpus.get("raw") or {}
    pdf_path = raw.get("path") or (index_row or {}).get("pdf_path")
    sha = raw.get("sha256") or (index_row or {}).get("pdf_sha256")
    if pdf_path:
        files.append(
            SourceFile(
                asset_id=f"pdf:{source_id}",
                role=AssetRole.PDF,
                path=str(pdf_path),
                sha256=State.of(str(sha)) if sha else State.unknown("INDEX does not give sha256"),
            )
        )
    text = corpus.get("text") or {}
    if text.get("exists") or text.get("path"):
        files.append(
            SourceFile(
                asset_id=f"pdftotext:{source_id}",
                role=AssetRole.PDFTOTEXT,
                path=str(text.get("path") or "unknown"),
                sha256=State.unknown("INDEX does not give text sha256"),
            )
        )
    tables = corpus.get("tables") or {}
    if tables.get("exists") or tables.get("path"):
        files.append(
            SourceFile(
                asset_id=f"tables:{source_id}",
                role=AssetRole.TABLE_CSV,
                path=str(tables.get("path") or "unknown"),
                sha256=State.unknown("INDEX does not give table sha256"),
            )
        )
    ocr = corpus.get("ocr") or corpus.get("mineru") or corpus.get("vlm")
    if isinstance(ocr, Mapping) and (ocr.get("exists") or ocr.get("path")):
        files.append(
            SourceFile(
                asset_id=f"mineru_md:{source_id}",
                role=AssetRole.MINERU_MD,
                path=str(ocr.get("path") or "unknown"),
                sha256=State.unknown("INDEX does not give OCR sha256"),
            )
        )
    if not any(f.asset_id == f"unknown:{source_id}" for f in files):
        files.append(
            SourceFile(
                asset_id=f"unknown:{source_id}",
                role=AssetRole.PDFTOTEXT,
                path="unknown",
                sha256=State.unknown("INDEX does not give an unmatched-layer asset"),
            )
        )
    repo = CORPUS_REPO_DEFAULT
    return SourceFiles(corpus_repo=repo, corpus_commit=commit, files=tuple(files))


# ---------------------------------------------------------------------------
# Extract walk
# ---------------------------------------------------------------------------


def iter_extract_observations(
    doc: Mapping[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    species = doc.get("species") or {}
    if isinstance(species, Mapping):
        for formula, body in species.items():
            if not isinstance(body, Mapping):
                continue
            for obs in body.get("observations") or []:
                if isinstance(obs, Mapping):
                    yield str(formula), dict(obs)


def _is_nist_janaf_table(path: Path, doc: Mapping[str, Any]) -> bool:
    if not isinstance(doc.get("table"), Mapping):
        return False
    if str(doc.get("source_id") or "") == "nist-janaf-4th":
        return True
    parts = path.parts
    try:
        index = parts.index("compilations")
    except ValueError:
        return False
    return index + 1 < len(parts) and parts[index + 1] == "janaf"


_B1544_SOURCE_ID = "hemingway-haas-robinson-1982-usgs-b1544"
_B1452_SOURCE_ID = "robie-hemingway-fisher-1978-usgs-b1452"
_B1259_SOURCE_ID = "robie-waldbaum-1968-usgs-b1259"


def _is_usgs_b1544_record(path: Path, doc: Mapping[str, Any]) -> bool:
    if not doc.get("record_id"):
        return False
    if str(doc.get("source_id") or "") == _B1544_SOURCE_ID:
        return True
    parts = path.parts
    try:
        index = parts.index("compilations")
    except ValueError:
        return False
    return index + 1 < len(parts) and parts[index + 1] == _B1544_SOURCE_ID


def _is_usgs_b1452_record(path: Path, doc: Mapping[str, Any]) -> bool:
    if not doc.get("record_id"):
        return False
    if str(doc.get("source_id") or "") == _B1452_SOURCE_ID:
        return True
    parts = path.parts
    try:
        index = parts.index("compilations")
    except ValueError:
        return False
    return index + 1 < len(parts) and parts[index + 1] == _B1452_SOURCE_ID


def _is_usgs_b1259_record(path: Path, doc: Mapping[str, Any]) -> bool:
    if not doc.get("record_id"):
        return False
    if str(doc.get("source_id") or "") == _B1259_SOURCE_ID:
        return True
    parts = path.parts
    try:
        index = parts.index("compilations")
    except ValueError:
        return False
    return index + 1 < len(parts) and parts[index + 1] == _B1259_SOURCE_ID


def _is_compilation_metadata(path: Path) -> bool:
    name = path.name
    if name in {
        "manifest.yaml",
        "access-status.yaml",
        "html-era-txt-divergence.yaml",
        "sidecar.yaml",
        "census.json",
    }:
        return True
    if name.startswith("image-verified-fixture"):
        return True
    return False


def discover_extracts(directory: Path | None = None) -> list[Path]:
    d = directory or EXTRACTS_DIR
    return sorted(
        p
        for p in d.glob("*.yaml")
        if p.is_file()
        and not p.name.startswith("_")
        and not p.name.upper().startswith("SCHEMA")
    )


# ---------------------------------------------------------------------------
# Migrator
# ---------------------------------------------------------------------------


class Migrator:
    def __init__(
        self,
        root: Path | None = None,
        *,
        aliases: Mapping[str, str] | None = None,
        index: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.root = root or REPO_ROOT
        self.literature = self.root / "data" / "literature"
        self.extracts_dir = self.literature / "extracts"
        self.index = dict(index) if index is not None else load_index(
            self.literature / "INDEX.yaml"
        )
        self.aliases = dict(aliases) if aliases is not None else load_aliases(
            self.literature / "works" / "ALIASES.yaml"
        )
        self.aliases.update(REVIEWED_ALIASES)
        self.result = MigrationResult(aliases=dict(self.aliases))
        self._vocab = _vocab_for_root(self.root)
        self._work_citations: dict[str, str] = {}
        self._work_dois: dict[str, str | None] = {}
        self._work_source_ids: dict[str, list[str]] = defaultdict(list)
        self._work_index_row: dict[str, Mapping[str, Any] | None] = {}
        self._obs_source: dict[str, str] = {}
        self._obs_row_index: dict[str, int] = {}
        self._pending_supersedes: list[tuple[str, str, str, Locator, str]] = []
        self._oxygen_pressure_landed: dict[str, Decimal] = {}
        self._oxygen_pressure_conflict: set[str] = set()

    def _count(self, path: str) -> SourceCount:
        rec = self.result.source_counts.get(path)
        if rec is None:
            rec = SourceCount(path=path)
            self.result.source_counts[path] = rec
        return rec

    def _ensure_work(
        self,
        *,
        citation: str,
        doi: str | None,
        source_id: str,
        index_row: Mapping[str, Any] | None,
    ) -> Work:
        work_id, canonical_doi = work_id_for(
            citation=citation,
            doi=doi,
            source_id=source_id,
            aliases=self.aliases,
        )
        if source_id not in self.aliases:
            self.aliases[source_id] = work_id
            self.result.aliases[source_id] = work_id
        if canonical_doi and canonical_doi not in self.aliases:
            self.aliases[canonical_doi] = work_id
            self.result.aliases[canonical_doi] = work_id
        if work_id not in self.result.works:
            self._work_citations[work_id] = citation
            self._work_dois[work_id] = canonical_doi
            self._work_index_row[work_id] = index_row
        if source_id not in self._work_source_ids[work_id]:
            self._work_source_ids[work_id].append(source_id)
        files = source_files_for(source_id, index_row)
        work = Work(
            work_id=work_id,
            citation=self._work_citations[work_id] or citation,
            source_ids=tuple(self._work_source_ids[work_id]),
            source_files=files,
            doi=self._work_dois[work_id],
        )
        self.result.works[work_id] = work
        return work

    def _rebuild_works(self) -> None:
        rebuilt: dict[str, Work] = {}
        for work_id, work in self.result.works.items():
            source_ids = tuple(self._work_source_ids[work_id] or work.source_ids)
            files: list[SourceFile] = []
            seen_assets: set[str] = set()
            commit = State.unknown("INDEX does not give corpus commit")
            for sid in source_ids:
                pack = source_files_for(sid, self.index.get(sid))
                if pack.corpus_commit.is_value:
                    commit = pack.corpus_commit
                for asset in pack.files:
                    if asset.asset_id in seen_assets:
                        continue
                    seen_assets.add(asset.asset_id)
                    files.append(asset)
            if not files:
                files = list(
                    source_files_for(work_id, self._work_index_row.get(work_id)).files
                )
            rebuilt[work_id] = Work(
                work_id=work_id,
                citation=work.citation,
                source_ids=source_ids,
                source_files=SourceFiles(
                    corpus_repo=CORPUS_REPO_DEFAULT,
                    corpus_commit=commit,
                    files=tuple(files),
                ),
                doi=work.doi,
            )
        self.result.works = rebuilt

    def _experiment_id(
        self,
        work_id: str,
        locator: Locator | None,
        fallback: str,
        distinguisher: str | None = None,
    ) -> str:
        """Mint experiment_id without printed locator labels (F4 R-label).

        Locator table/figure/record strings are display metadata. Identity comes
        from a declared experiment id at the call site, else ``fallback`` (and
        optional distinguisher). ``locator`` is kept for call compatibility.
        """

        _ = locator  # display only; not part of durable identity
        if distinguisher:
            return f"{work_id}::{fallback}::{distinguisher}"
        return f"{work_id}::{fallback}"

    def _ensure_experiment(
        self,
        *,
        work_id: str,
        experiment_id: str,
        locator: Locator | None,
        method: State[MethodToken],
        equipment: object,
        values: object = None,
        conditions: dict[str, Located[Decimal]] | None = None,
        observation_id: str | None = None,
        source: str | None = None,
    ) -> Experiment:
        existing = self.result.experiments.get(experiment_id)
        cond = conditions or {
            "temperature_K": located_unknown(
                "no temperature field under keys T_K / T_C / temperature_K in this extract"
            )
        }
        pressure_env = pressure_from_equipment(
            equipment,
            vocabulary=self._vocab,
            values=values,
            locator=locator,
        )
        apparatus = apparatus_from_equipment(
            equipment,
            vocabulary=self._vocab,
            values=values,
            locator=locator,
        )
        sample = sample_from_equipment(
            equipment,
            vocabulary=self._vocab,
            values=values,
            locator=locator,
        )
        if existing is not None:
            merged = _merge_experiment_lab_params(
                existing, sample, apparatus, pressure_env
            )
            self.result.experiments[experiment_id] = merged
            return merged
        if (
            isinstance(equipment, Mapping)
            and isinstance(equipment.get("chamber_pressure"), Mapping)
            and pressure_env.total_pressure_Pa.state.is_unknown
        ):
            self.result.add_queue(
                work_id,
                locator,
                ["total_pressure_Pa"],
                pressure_env.total_pressure_Pa.state.reason or "missing pressure unit",
                source=source,
                observation_id=observation_id,
            )
        if (
            apparatus is not None
            and apparatus.geometry is not None
            and apparatus.geometry.exposed_area_m2 is not None
            and apparatus.geometry.exposed_area_m2.state.is_unknown
        ):
            self.result.add_queue(
                work_id,
                locator,
                ["exposed_area_m2"],
                apparatus.geometry.exposed_area_m2.state.reason or "missing area unit",
                source=source,
                observation_id=observation_id,
            )
        if sample.mass_kg is not None and sample.mass_kg.state.is_unknown:
            self.result.add_queue(
                work_id,
                locator,
                ["sample.mass_kg"],
                sample.mass_kg.state.reason or "missing mass unit",
                source=source,
                observation_id=observation_id,
            )
        experiment = Experiment(
            experiment_id=experiment_id,
            kind=ExperimentKind.LITERATURE,
            method=method,
            sample=sample,
            conditions=cond,
            pressure_environment=pressure_env,
            work_id=work_id,
            locator=locator or Locator(record=experiment_id),
            apparatus=apparatus,
        )
        self.result.experiments[experiment_id] = experiment
        self.result.experiments_by_work[work_id].append(experiment_id)
        return experiment

    def _land_experiment_oxygen_pressure(
        self, experiment_id: str, located: Located[Value]
    ) -> None:
        if experiment_id in self._oxygen_pressure_conflict:
            return
        experiment = self.result.experiments.get(experiment_id)
        if experiment is None or not located.state.is_value:
            return
        raw = located.state.value
        if not isinstance(raw, Value) or raw.kind is not ValueKind.POINT or raw.point is None:
            return
        control = experiment.fO2_control
        existing = None
        if (
            control is not None
            and control.oxygen_partial_pressure_Pa is not None
            and control.oxygen_partial_pressure_Pa.state.is_value
        ):
            previous = control.oxygen_partial_pressure_Pa.state.value
            if isinstance(previous, Value):
                if previous.kind is ValueKind.POINT:
                    existing = previous.point
                else:
                    # Interval/bound already present (e.g. printed log range
                    # landed as pO2_Pa). Do not collapse it to one point.
                    self._oxygen_pressure_conflict.add(experiment_id)
                    return
        if existing is not None and existing != raw.point:
            self._oxygen_pressure_conflict.add(experiment_id)
            if experiment_id in self._oxygen_pressure_landed and control is not None:
                # Two different printed pressures. Do not pick one for the run.
                self.result.experiments[experiment_id] = replace(
                    experiment,
                    fO2_control=replace(control, oxygen_partial_pressure_Pa=None),
                )
            return
        if existing is not None:
            return
        if control is None:
            control = FO2Control(
                channel=State.unknown(
                    "printed oxygen partial pressure; extract does not classify an fO2 channel"
                ),
                oxygen_partial_pressure_Pa=located,
            )
        else:
            control = replace(control, oxygen_partial_pressure_Pa=located)
        self._oxygen_pressure_landed[experiment_id] = raw.point
        self.result.experiments[experiment_id] = replace(
            experiment, fO2_control=control
        )

    def _merge_printed_oxygen(
        self,
        experiment_id: str,
        point_conditions: dict[str, Located[Any]] | None,
        specific: Iterable[object],
        locator: Locator | None,
        fallback: Iterable[object] = (),
        *,
        skip_tables: bool = False,
    ) -> dict[str, Located[Any]] | None:
        facts = collect_printed_oxygen(specific, locator, skip_tables=skip_tables)
        if facts.log_fO2 is None and facts.oxygen_partial_pressure_Pa is None and fallback:
            facts = collect_printed_oxygen(fallback, locator, skip_tables=True)
        if facts.log_fO2 is None and facts.oxygen_partial_pressure_Pa is None:
            return point_conditions
        merged = dict(point_conditions or {})
        if facts.log_fO2 is not None and "fO2_log" not in merged:
            merged["fO2_log"] = facts.log_fO2
        if (
            facts.oxygen_partial_pressure_Pa is not None
            and "fO2_Pa" not in merged
        ):
            merged["fO2_Pa"] = facts.oxygen_partial_pressure_Pa
            self._land_experiment_oxygen_pressure(
                experiment_id, facts.oxygen_partial_pressure_Pa
            )
        return merged or None

    def _add_observation(
        self,
        observation: Observation,
        source_key: str,
        *,
        source_row_index: int | None = None,
    ) -> None:
        oid = observation.observation_id
        existing = self.result.observations.get(oid)
        if existing is not None:
            if to_plain(existing) == to_plain(observation):
                first = self._obs_row_index.get(oid)
                indices = tuple(
                    i for i in (first, source_row_index) if i is not None
                )
                self.result.dedupe_aliases.append(
                    DedupeAlias(observation_id=oid, source=source_key, row_indices=indices)
                )
                return
            raise DuplicateObservationIdError(
                f"duplicate observation_id {oid!r} with a different payload"
            )
        self.result.observations[oid] = observation
        self.result.observations_by_source[source_key].append(oid)
        self._obs_source[oid] = source_key
        self._count(source_key).observations_out += 1
        if source_row_index is not None:
            self._obs_row_index[oid] = source_row_index
        poly = observation.identity.species.polymorph
        if poly is not None and poly.is_unknown:
            from simulator.battery.polymorph_dictionary import (
                unrecognised_polymorph_reason,
                unrecognised_polymorph_spelling,
            )

            spelling = unrecognised_polymorph_spelling(poly.reason)
            if spelling is not None:
                self.result.unrecognised_polymorphs[spelling] = (
                    self.result.unrecognised_polymorphs.get(spelling, 0) + 1
                )
                exp = self.result.experiments.get(observation.experiment_id)
                work_id = (
                    exp.work_id
                    if exp is not None and exp.work_id
                    else observation.experiment_id.split("::", 1)[0]
                )
                self.result.add_queue(
                    work_id,
                    observation.locator,
                    ["species.polymorph"],
                    poly.reason or unrecognised_polymorph_reason(spelling),
                    source=source_key,
                    observation_id=oid,
                )

    def migrate_extracts(self, directory: Path | None = None) -> None:
        for path in discover_extracts(directory or self.extracts_dir):
            self._migrate_extract(path)

    @staticmethod
    def _qualify_registry_id(work_id: str, kind: str, raw: object) -> str:
        token = str(raw or "").strip()
        if not token:
            raise ValueError(f"{kind} registry entry requires an id")
        if "::" in token:
            return token
        return f"{work_id}::{kind}::{token}"

    def _registry_id(
        self, work_id: str, kind: str, raw: object, *, path: str
    ) -> str | None:
        """Qualify a registry id, or refuse one outside this work.

        A local id is minted as ``<work_id>::<kind>::<id>``. A token that
        already contains ``::`` is accepted only when it carries that same
        prefix. Any other qualified id names another work or kind: record
        referential_integrity and return None so the caller writes no entry
        and no link. Aliases of one work share ``work_id``, so a qualified
        id minted for this work still resolves.
        """
        qualified = self._qualify_registry_id(work_id, kind, raw)
        token = str(raw or "").strip()
        if "::" in token and not qualified.startswith(f"{work_id}::{kind}::"):
            self.result.registry_issues.append(
                ValidationIssue(
                    path=path,
                    reason=RefusalReason.REFERENTIAL_INTEGRITY,
                    detail=(
                        f"qualified {kind} id {qualified!r} is outside work "
                        f"{work_id!r}; no link written"
                    ),
                )
            )
            return None
        return qualified

    def _lift_extract_registries(
        self,
        doc: Mapping[str, Any],
        *,
        work: Work,
        source_key: str,
    ) -> dict[str, str]:
        bench_refs: dict[str, str] = {}
        raw_benches = doc.get("benches") or ()
        if isinstance(raw_benches, Mapping):
            raw_benches = [dict(value, id=key) for key, value in raw_benches.items()]
        if not isinstance(raw_benches, (list, tuple)):
            raise TypeError("extract benches registry must be a list or mapping")
        for raw in raw_benches:
            if not isinstance(raw, Mapping):
                raise TypeError("extract benches entries must be mappings")
            if (raw.get("identity") or {}).get("basis") == BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE.value:
                raise ValueError("extract cannot declare migration-only inferred_from_embedded_evidence")
            local_id = raw.get("id") or raw.get("bench_id")
            bench_id = self._registry_id(
                work.work_id, "bench", local_id, path=f"bench[{local_id}]"
            )
            if bench_id is None:
                continue
            payload = dict(raw)
            payload["id"] = bench_id
            payload["work_id"] = work.work_id
            bench = bench_from_plain(payload)
            existing_bench = self.result.benches.get(bench.id)
            if existing_bench is not None:
                if existing_bench != bench:
                    self.result.registry_issues.append(
                        ValidationIssue(
                            path=f"bench[{bench.id}]",
                            reason=RefusalReason.REFERENTIAL_INTEGRITY,
                            detail=f"duplicate bench id {bench.id!r} has conflicting payloads",
                        )
                    )
                continue
            self.result.benches[bench.id] = bench
            if bench.id not in self.result.benches_by_work[work.work_id]:
                self.result.benches_by_work[work.work_id].append(bench.id)
            bench_refs[str(local_id)] = bench.id
            bench_refs[bench.id] = bench.id

        experiment_refs: dict[str, str] = {}
        raw_experiments = doc.get("experiments") or ()
        if isinstance(raw_experiments, Mapping):
            raw_experiments = [
                dict(value, experiment_id=key)
                for key, value in raw_experiments.items()
            ]
        if not isinstance(raw_experiments, (list, tuple)):
            raise TypeError("extract experiments registry must be a list or mapping")
        for raw in raw_experiments:
            if not isinstance(raw, Mapping):
                raise TypeError("extract experiments entries must be mappings")
            local_id = raw.get("experiment_id") or raw.get("id")
            experiment_id = self._registry_id(
                work.work_id,
                "experiment",
                local_id,
                path=f"experiment[{local_id}]",
            )
            if experiment_id is None:
                continue
            raw_bench_id = raw.get("bench_id") or raw.get("bench")
            bench_id = None
            if raw_bench_id is not None:
                key = str(raw_bench_id)
                if key in bench_refs:
                    bench_id = bench_refs[key]
                else:
                    bench_id = self._registry_id(
                        work.work_id,
                        "bench",
                        raw_bench_id,
                        path=f"experiment[{experiment_id}].bench_id",
                    )
            locator = locator_from_mapping(
                raw.get("locator"), fallback=f"{source_key}:experiments:{local_id}"
            )
            method_raw = raw.get("method")
            if isinstance(method_raw, Mapping) and "tag" in method_raw:
                method = method_raw
            elif method_raw is None:
                method = to_plain(State.unknown("experiment method not published"))
            else:
                method = to_plain(map_method(method_raw))
            pressure = raw.get("pressure_environment")
            if pressure is None:
                pressure = to_plain(
                    pressure_from_equipment(
                        None, vocabulary=self._vocab, locator=locator
                    )
                )
            conditions = raw.get("conditions")
            if not conditions:
                conditions = {
                    "temperature_K": to_plain(
                        Located(State.unknown("experiment temperature not published"))
                    )
                }
            payload = dict(raw)
            payload.update(
                {
                    "experiment_id": experiment_id,
                    "kind": ExperimentKind.LITERATURE.value,
                    "method": method,
                    "sample": raw.get("sample") or raw.get("charge") or {},
                    "conditions": conditions,
                    "pressure_environment": pressure,
                    "work_id": work.work_id,
                    "bench_id": bench_id,
                    "locator": to_plain(locator),
                }
            )
            experiment = experiment_from_plain(payload)
            existing_experiment = self.result.experiments.get(
                experiment.experiment_id
            )
            if existing_experiment is not None:
                if existing_experiment != experiment:
                    self.result.registry_issues.append(
                        ValidationIssue(
                            path=f"experiment[{experiment.experiment_id}]",
                            reason=RefusalReason.REFERENTIAL_INTEGRITY,
                            detail=(
                                f"duplicate experiment id {experiment.experiment_id!r} "
                                "has conflicting payloads"
                            ),
                        )
                    )
                continue
            self.result.experiments[experiment.experiment_id] = experiment
            if experiment.experiment_id not in self.result.experiments_by_work[work.work_id]:
                self.result.experiments_by_work[work.work_id].append(
                    experiment.experiment_id
                )
            experiment_refs[str(local_id)] = experiment.experiment_id
            experiment_refs[experiment.experiment_id] = experiment.experiment_id
        return experiment_refs

    def _lift_extract_context(
        self,
        doc: Mapping[str, Any],
        *,
        work: Work,
        source_id: str,
        experiment_refs: Mapping[str, str],
    ) -> None:
        species = doc.get("species")
        if not isinstance(species, Mapping):
            return
        for formula, body in species.items():
            if not isinstance(body, Mapping):
                continue
            rows = body.get("context")
            if rows is None:
                continue
            if not isinstance(rows, (list, tuple)):
                raise TypeError("extract context container must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise TypeError("extract context entries must be mappings")
                raw_id = str(row.get("observation_id") or "").strip()
                if not raw_id:
                    raise ValueError(
                        "extract context entry requires an observation_id"
                    )
                context_id = f"{source_id}::context::{raw_id}"
                if any(
                    str(existing.get("context_id")) == context_id
                    for existing in self.result.context_by_work[work.work_id]
                ):
                    raise DuplicateContextIdError(
                        f"duplicate context_id {context_id!r} under work {work.work_id!r}"
                    )
                record = dict(row)
                record["context_id"] = context_id
                record["work_id"] = work.work_id
                record["source_id"] = source_id
                record["species"] = str(formula)
                self.result.context_by_work[work.work_id].append(record)
                self._link_equipment_context(work, record, experiment_refs)

    def _link_equipment_context(
        self,
        work: Work,
        record: Mapping[str, Any],
        experiment_refs: Mapping[str, str],
    ) -> None:
        """d-036 ruling: a context row that names an ``experiment:`` and holds
        equipment evidence (an ``equipment`` block or ``values.apparatus``) is
        the experiment's equipment record BY REFERENCE. The experiment carries
        the FK; the row's content is never copied onto it."""
        raw_experiment = record.get("experiment")
        if raw_experiment is None:
            return
        values = record.get("values")
        holds_equipment = isinstance(record.get("equipment"), Mapping) or (
            isinstance(values, Mapping) and values.get("apparatus") is not None
        )
        if not holds_equipment:
            return
        experiment_id = experiment_refs.get(
            str(raw_experiment),
            self._qualify_registry_id(work.work_id, "experiment", raw_experiment),
        )
        experiment = self.result.experiments.get(experiment_id)
        if experiment is not None and experiment.work_id != work.work_id:
            self.result.registry_issues.append(
                ValidationIssue(
                    path=f"context[{record['context_id']}].experiment",
                    reason=RefusalReason.REFERENTIAL_INTEGRITY,
                    detail=(
                        f"equipment context row names experiment {experiment_id!r} "
                        f"owned by work {experiment.work_id!r}, not {work.work_id!r}; "
                        "no equipment_context_id written"
                    ),
                )
            )
            return
        if experiment is None:
            self.result.registry_issues.append(
                ValidationIssue(
                    path=f"context[{record['context_id']}].experiment",
                    reason=RefusalReason.REFERENTIAL_INTEGRITY,
                    detail=(
                        f"equipment context row names experiment {experiment_id!r} "
                        "which is not declared; no equipment_context_id written"
                    ),
                )
            )
            return
        existing = experiment.equipment_context_id
        if existing is not None:
            if existing != record["context_id"]:
                self.result.registry_issues.append(
                    ValidationIssue(
                        path=f"experiment[{experiment_id}].equipment_context_id",
                        reason=RefusalReason.REFERENTIAL_INTEGRITY,
                        detail=(
                            f"experiment {experiment_id!r} has two equipment context "
                            f"rows {existing!r} and {record['context_id']!r}; keeping {existing!r}"
                        ),
                    )
                )
            return
        self.result.experiments[experiment_id] = replace(
            experiment, equipment_context_id=record["context_id"]
        )

    def _migrate_extract(self, path: Path) -> None:
        rel = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            count.rows_in += 1
            self.result.add_queue(
                work_id=path.stem,
                locator={"source_path": rel},
                axes=["document"],
                why="extract is not a mapping",
                source=rel,
            )
            return
        source = doc.get("source") or {}
        if not isinstance(source, Mapping):
            source = {}
        citation = str(source.get("citation") or "")
        source_id = str(doc.get("source_id") or path.stem)
        index_row = self.index.get(source_id)
        doi = extract_doi(
            source.get("doi"),
            (index_row or {}).get("doi"),
            citation,
        )
        work = self._ensure_work(
            citation=citation or str((index_row or {}).get("citation") or source_id),
            doi=doi,
            source_id=source_id,
            index_row=index_row,
        )
        experiment_refs = self._lift_extract_registries(
            doc, work=work, source_key=rel
        )
        self._lift_extract_context(
            doc, work=work, source_id=source_id, experiment_refs=experiment_refs
        )
        extraction = doc.get("extraction") if isinstance(doc.get("extraction"), Mapping) else {}
        rows = list(iter_extract_observations(doc))
        count.rows_in += len(rows)
        self.result.measured.citations += 1
        local_ids = {str(obs.get("observation_id")) for _, obs in rows if obs.get("observation_id")}
        for formula, obs in rows:
            raw_experiment = obs.get("experiment")
            declared_experiment_id = None
            if raw_experiment is not None:
                key = str(raw_experiment)
                if key in experiment_refs:
                    declared_experiment_id = experiment_refs[key]
                else:
                    raw_obs_id = str(
                        obs.get("observation_id") or f"{source_id}:missing"
                    )
                    declared_experiment_id = self._registry_id(
                        work.work_id,
                        "experiment",
                        raw_experiment,
                        path=f"observation[{source_id}::{raw_obs_id}].experiment",
                    )
            self._migrate_extract_observation(
                formula=formula,
                obs=obs,
                work=work,
                source_id=source_id,
                source_key=rel,
                extraction=extraction or {},
                local_ids=local_ids,
                declared_experiment_id=declared_experiment_id,
            )

    def _migrate_extract_observation(
        self,
        *,
        formula: str,
        obs: Mapping[str, Any],
        work: Work,
        source_id: str,
        source_key: str,
        extraction: Mapping[str, Any],
        local_ids: set[str],
        declared_experiment_id: str | None = None,
    ) -> None:
        measured = self.result.measured
        raw_obs_id = str(obs.get("observation_id") or f"{source_id}:missing")
        obs_id = f"{source_id}::{raw_obs_id}"
        raw_values = obs.get("values")
        if isinstance(raw_values, list):
            values = (
                {"points": raw_values}
                if _is_pressure_point_list(raw_values)
                else {"series": raw_values}
            )
        elif isinstance(raw_values, Mapping):
            values = dict(raw_values)
        else:
            values = {}
        t_for_rekey = None
        if isinstance(values, Mapping):
            t_sel_rekey = select_declared_source(AXIS_TEMPERATURE_K, None, values)
            if t_sel_rekey.available:
                t_for_rekey = t_sel_rekey.amount
        if t_for_rekey is None and isinstance(obs.get("identity"), Mapping):
            ident_t = obs["identity"].get("temperature_K")
            if isinstance(ident_t, Mapping) and ident_t.get("tag") == "value":
                t_for_rekey = ident_t.get("value")
            elif ident_t is not None and not isinstance(ident_t, Mapping):
                t_for_rekey = ident_t
        raw_obs_id = _rekey_ordinal_point_observation_id(
            raw_obs_id, temperature=t_for_rekey
        )
        obs_id = f"{source_id}::{raw_obs_id}"
        oxygen_fields = _measured_oxygen_yield_fields(values)
        if oxygen_fields:
            for index, (child_quantity, field) in enumerate(oxygen_fields):
                child_values = dict(values)
                child_values["quantity"] = child_quantity.value
                if (
                    field in {"mass_yield_percent", "fraction_of_feedstock_oxygen_percent"}
                    and "comparable" not in raw_obs_id.lower()
                    and "1p17" not in raw_obs_id.lower()
                ):
                    child_values["admission_status"] = "admitted"
                child_obs = dict(obs)
                if len(oxygen_fields) == 1:
                    child_obs["observation_id"] = raw_obs_id
                else:
                    child_obs["observation_id"] = series_point_id(
                        raw_obs_id, field=field
                    )
                child_obs["values"] = child_values
                self._migrate_extract_observation(
                    formula=formula,
                    obs=child_obs,
                    work=work,
                    source_id=source_id,
                    source_key=source_key,
                    extraction=extraction,
                    local_ids=local_ids,
                    declared_experiment_id=declared_experiment_id,
                )
            return
        locator = locator_from_mapping(
            obs.get("locator"), fallback=f"extract:{source_id}:{obs_id}"
        )
        assert locator is not None
        obs_type = obs.get("type") if isinstance(obs.get("type"), str) else None
        phase_raw = compilation_phase_text(values) or compilation_phase_text(obs)
        transition_reason = transition_phase_reason(obs_type, values)
        if (phase_raw is None or phase_raw == "") and transition_reason:
            phase, unmapped_phase = State.unknown(transition_reason), None
        else:
            phase, unmapped_phase = map_phase(phase_raw)
            if unmapped_phase and source_id == "janaf-4th":
                from simulator.battery.generators.janaf import janaf_extract_phase

                token = janaf_extract_phase(source_id, unmapped_phase)
                if token is not None:
                    phase, unmapped_phase = State.of(token), None
        if phase_raw is None or phase_raw == "":
            measured.missing_phases += 1
            self.result.add_queue(
                work.work_id,
                locator,
                ["phase"],
                transition_reason or "missing phase",
                source=source_key,
                observation_id=obs_id,
            )
        elif unmapped_phase:
            measured.system_like_phases += 1
            self.result.add_queue(
                work.work_id,
                locator,
                ["phase"],
                f"phase string {unmapped_phase!r} is not in the closed automatic map",
                source=source_key,
                observation_id=obs_id,
            )
        species_formula = str(values.get("formula") or formula)
        if values.get("formula"):
            measured.formulas += 1
        species = make_species(
            species_formula, phase, polymorph=polymorph_from_extract(obs)
        )
        quantity, q_reason = map_quantity(
            obs_type, values, units=obs.get("units"), row=obs
        )
        if q_reason:
            self.result.add_queue(
                work.work_id,
                locator,
                ["quantity"],
                q_reason,
                source=source_key,
                observation_id=obs_id,
            )
        suffix_formula, suffix_derivation, suffix_reference = (None, None, None)
        raw_quantity = values.get("quantity") if isinstance(values, Mapping) else None
        if isinstance(raw_quantity, str):
            _qualified, suffix = split_qualified_quantity(raw_quantity)
            suffix_formula, suffix_derivation, suffix_reference = parse_quantity_suffix(
                suffix
            )
            if suffix_formula:
                species = make_species(
                    suffix_formula, phase, polymorph=polymorph_from_extract(obs)
                )
        q_token = quantity.value if isinstance(quantity, State) and quantity.is_value else (
            quantity if isinstance(quantity, Quantity) else None
        )
        initial_oxide_map = _initial_oxide_map_from_values(values)
        if q_token in _BULK_PROPERTY_QUANTITIES:
            species_formula = bulk_property_species_formula(
                quantity=q_token,
                parent_formula=species.formula,
                sample_label=None,
                oxide_map=initial_oxide_map,
            )
            species = make_species(
                species_formula, phase, polymorph=polymorph_from_extract(obs)
            )

        t_payload = dict(values)
        if obs.get("T_K") is not None:
            t_payload.setdefault("T_K", obs.get("T_K"))
        if obs.get("T_range_K") is not None:
            t_payload.setdefault("T_range_K", obs.get("T_range_K"))
        t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, t_payload)
        t_known = t_sel.amount if t_sel.available else None
        if t_sel.condition_ranges and t_known is None:
            measured.range_only_T += 1
            name, lo, hi = t_sel.condition_ranges[0]
            self.result.add_queue(
                work.work_id,
                locator,
                ["temperature_K"],
                f"source {name} [{lo}, {hi}]; no midpoint invented",
                source=source_key,
                observation_id=obs_id,
            )

        p_sel = select_declared_source(AXIS_STANDARD_PRESSURE_PA, None, values)
        p_std = p_sel.amount if p_sel.available else None
        if p_std is not None:
            measured.gibbs_reference_pressures += 1
            if p_std == Decimal("100000") or p_std == Decimal("100000.0"):
                measured.gibbs_reference_100000 += 1
            elif p_std == Decimal("101325") or p_std == Decimal("101325.0"):
                measured.gibbs_reference_101325 += 1

        method_class = values.get("method_class")
        regime = obs.get("regime") or values.get("regime")
        if method_class is None:
            measured.absent_classes += 1
        evidence, ev_reason = self._evidence_for(
            method_class,
            evaluator_family=values.get("evaluator_family"),
            attribution=obs.get("quote") if isinstance(obs.get("quote"), str) else None,
            model=suffix_derivation,
            regime=regime,
        )
        if suffix_derivation and (
            not evidence.class_.is_value
            or evidence.class_.value is not EvidenceClass.MODEL_DERIVED
        ):
            evidence = Evidence(
                class_=State.of(EvidenceClass.MODEL_DERIVED),
                original_method_class=evidence.original_method_class
                or (str(method_class) if method_class else None),
                model=suffix_derivation,
                attribution=evidence.attribution,
            )
        if ev_reason:
            self.result.add_queue(
                work.work_id,
                locator,
                ["evidence.class"],
                ev_reason,
                source=source_key,
                observation_id=obs_id,
            )

        raw_adm = values.get("admission_status")
        if raw_adm is None and obs.get("admission_status") is None:
            measured.absent_admissions += 1
        else:
            measured.admission_statuses += 1
        if obs.get("supersedes"):
            measured.supersedes += 1
            targets = obs.get("supersedes")
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(targets, list):
                targets = [targets]
            for target in targets:
                if not isinstance(target, str) or not target:
                    continue
                old_id = f"{source_id}::{target}"
                if target in local_ids:
                    self._pending_supersedes.append(
                        (old_id, obs_id, work.work_id, locator, source_key)
                    )
                else:
                    self.result.add_queue(
                        work.work_id,
                        locator,
                        ["admission.superseded_by"],
                        f"supersedes target {target!r} unresolved in this extract",
                        source=source_key,
                        observation_id=obs_id,
                    )
        admission = admission_for(
            raw_adm if raw_adm is not None else obs.get("admission_status"),
            superseded_by=None,
            extraction=extraction,
            locator=locator,
        )

        if obs.get("equipment"):
            measured.equipment_payloads += 1

        value, exploded, value_sel = empty_value_from_payload(
            values, obs_type, obs.get("units"), quantity=quantity
        )
        if isinstance(values.get("series"), list) and values.get("series"):
            measured.series += 1
        if isinstance(values.get("tabulated_delta_fG_kJ_mol"), list) and values.get(
            "tabulated_delta_fG_kJ_mol"
        ):
            measured.tabulated_lists += 1

        ident_kwargs: dict[str, Any] = {}
        q_token = quantity.value if quantity.is_value else None
        if initial_oxide_map:
            ident_kwargs["composition"] = State.of(
                wt_pct_to_mole_fraction(initial_oxide_map)
            )
        elif q_token in _BULK_PROPERTY_QUANTITIES:
            ident_kwargs["composition"] = State.unknown(composition_unknown_reason())
        if t_known is not None and q_token is not Quantity.TRANSITION_TEMPERATURE:
            ident_kwargs["temperature_K"] = State.of(t_known)
        elif t_known is None and t_sel.condition_ranges:
            name, lo, hi = t_sel.condition_ranges[0]
            ident_kwargs["temperature_K"] = State.unknown(
                f"source {name} [{lo}, {hi}]; no midpoint invented"
            )
        if p_std is not None:
            ident_kwargs["standard_pressure_Pa"] = State.of(p_std)
        if (
            q_token is Quantity.DELTA_FG
            and t_known is None
            and isinstance(values, Mapping)
            and values.get("Delta_f_G_298_kJ_mol") is not None
        ):
            ident_kwargs["temperature_K"] = State.of(Decimal("298.15"))
        if suffix_reference:
            ident_kwargs["reference_state"] = State.unknown(
                f"qualifier {suffix_reference} does not name a reference_state"
            )
            self.result.add_queue(
                work.work_id,
                locator,
                ["reference_state"],
                f"qualifier {suffix_reference} does not name a reference_state",
                source=source_key,
                observation_id=obs_id,
            )
        if q_token is Quantity.TRANSITION_TEMPERATURE:
            kind = values.get("property_kind") or values.get("quantity")
            if isinstance(kind, str) and kind:
                ident_kwargs["subtype"] = State.of(kind)
            p_amt = _as_dec_or_none(
                values.get("pressure_basis_Pa") or values.get("target_pressure_Pa")
            )
            if p_amt is not None:
                ident_kwargs["total_pressure_Pa"] = State.of(p_amt)
            else:
                textual = values.get("pressure_basis")
                if textual not in (None, ""):
                    ident_kwargs["total_pressure_Pa"] = State.unknown(
                        f"source pressure_basis {textual!r} is not a numeric pressure"
                    )
                    self.result.add_queue(
                        work.work_id,
                        locator,
                        ["total_pressure_Pa"],
                        f"source pressure_basis {textual!r} is not a numeric pressure",
                        source=source_key,
                        observation_id=obs_id,
                    )
                else:
                    ident_kwargs["total_pressure_Pa"] = State.unknown(
                        "source does not state a numeric total_pressure_Pa"
                    )
                    self.result.add_queue(
                        work.work_id,
                        locator,
                        ["total_pressure_Pa"],
                        "source does not state a numeric total_pressure_Pa",
                        source=source_key,
                        observation_id=obs_id,
                    )
        if q_token is Quantity.TRANSITION_TEMPERATURE and (
            value.kind in {ValueKind.UNAVAILABLE, ValueKind.INTERVAL}
        ):
            t_as_value = select_declared_source(
                Quantity.TRANSITION_TEMPERATURE, None, t_payload
            )
            if t_as_value.available:
                value = t_as_value.value
                value_sel = t_as_value
        identity = fill_identity(quantity, species, **ident_kwargs)

        distinguisher = None
        if isinstance(values, Mapping):
            cid = values.get("composition_id") or values.get("composition_name")
            if cid not in (None, ""):
                distinguisher = str(cid)
        experiment_id = declared_experiment_id or self._experiment_id(
            work.work_id, locator, source_id, distinguisher=distinguisher
        )
        rail_raw = obs.get("rail") or values.get("rail")
        if rail_raw:
            canonicalize_rail(str(rail_raw))
        method = map_method(obs.get("regime"))
        if method.is_unknown:
            self.result.add_queue(
                work.work_id,
                locator,
                ["method"],
                method.reason or "unknown method",
                source=source_key,
                observation_id=obs_id,
            )
        point_conditions = None
        if t_known is not None:
            point_conditions = {
                "temperature_K": _converted_temperature(
                    t_known,
                    t_sel.unit_trail,
                    _temperature_field_raw(t_payload, t_sel.field_name),
                    locator,
                )
            }
        if declared_experiment_id is None or (
            declared_experiment_id in self.result.experiments
            and bool(obs.get("equipment"))
        ):
            self._ensure_experiment(
                work_id=work.work_id,
                experiment_id=experiment_id,
                locator=locator,
                method=method,
                equipment=obs.get("equipment"),
                values=values if isinstance(values, Mapping) else None,
                observation_id=obs_id,
                source=source_key,
            )
        oxygen_roots: list[object] = []
        if isinstance(values, Mapping):
            oxygen_roots.append(values)
        if isinstance(obs.get("equipment"), Mapping):
            oxygen_roots.append(obs["equipment"])
        point_conditions = self._merge_printed_oxygen(
            experiment_id,
            point_conditions,
            oxygen_roots,
            locator,
            skip_tables=True,
        )
        read_from = choose_read_from(work, locator)
        unmatched = unmatched_read_from_reason(locator, read_from)
        if unmatched:
            self.result.add_queue(
                work.work_id,
                locator,
                ["read_from"],
                unmatched,
                source=source_key,
                observation_id=obs_id,
            )
        derived_parents, derived_prose = lineage_parents_from_source(
            obs, values, source_id, local_ids
        )
        derived_from = derived_parents or None
        for prose_item in derived_prose:
            self.result.add_queue(
                work.work_id,
                locator,
                ["derived_from"],
                (
                    f"derived_from {prose_item!r} is prose, not a resolvable "
                    "observation id; retained here, never minted as a pointer"
                ),
                source=source_key,
                observation_id=obs_id,
            )
        yield_key, yield_items = _yield_table_items(values)
        if yield_items:
            yield_quantity: Quantity | State[Quantity] = (
                quantity.value if quantity.is_value else Quantity.MASS_LOSS_FRACTION
            )
            before = self._count(source_key).observations_out
            for index, item in enumerate(yield_items):
                if not isinstance(item, Mapping) or _item_mass_loss_field(item) is None:
                    continue
                point_locator = locator
                if item.get("locator"):
                    point_locator = (
                        locator_from_mapping(
                            item.get("locator"), fallback=f"{obs_id}:point:{index}"
                        )
                        or locator
                    )
                t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, item)
                point_admission = _yield_point_admission(
                    item=item,
                    parent_values=values,
                    evidence=evidence,
                    locator=point_locator,
                    extraction=extraction,
                    t_is_point=t_sel.available,
                    parent_admission=admission,
                )
                point_notices: tuple[Notice, ...] = ()
                disagreement = _cardiff_matchett_disagreement_reason(values)
                test_id = str(item.get("test") or "")
                if disagreement and test_id in {"2b", "3"}:
                    q_token = (
                        yield_quantity.value
                        if isinstance(yield_quantity, State) and yield_quantity.is_value
                        else (
                            yield_quantity
                            if isinstance(yield_quantity, Quantity)
                            else Quantity.MASS_LOSS_FRACTION
                        )
                    )
                    point_notices = (
                        Notice(
                            kind=NoticeKind.SOURCE_DISAGREEMENT,
                            affected_quantities=(q_token,),
                            reason=disagreement,
                            origin=f"{obs_id}::point:{index}",
                            source="cardiff-2007-vacuum-pyrolysis-gsfc",
                            destination="kems-038-matchett-2006",
                        ),
                    )
                self._emit_exploded_point(
                    parent_id=obs_id,
                    item={"index": index, "item": item, "units": obs.get("units")},
                    work=work,
                    source_id=source_id,
                    source_key=source_key,
                    experiment_id=experiment_id,
                    locator=locator,
                    identity_base=(yield_quantity, species, ident_kwargs),
                    evidence=evidence,
                    admission=point_admission,
                    uncertainty=uncertainty_for(obs.get("uncertainty")),
                    units=str(obs.get("units") or ""),
                    read_from=read_from,
                    derived_from=derived_from,
                    notices=point_notices,
                    equipment=obs.get("equipment"),
                    parent_values=values,
                )
            if self._count(source_key).observations_out > before:
                return
        if exploded and isinstance(values.get("series"), list):
            before = self._count(source_key).observations_out
            for item in exploded:
                self._emit_exploded_point(
                    parent_id=obs_id,
                    item=item,
                    work=work,
                    source_id=source_id,
                    source_key=source_key,
                    experiment_id=experiment_id,
                    locator=locator,
                    identity_base=(quantity, species, ident_kwargs),
                    evidence=evidence,
                    admission=admission,
                    uncertainty=uncertainty_for(obs.get("uncertainty")),
                    units=str(obs.get("units") or ""),
                    read_from=read_from,
                    derived_from=derived_from,
                    equipment=obs.get("equipment"),
                    parent_values=values,
                )
            if self._count(source_key).observations_out > before:
                return
            self.result.add_queue(
                work.work_id,
                locator,
                ["value"],
                value_sel.reason
                or "printed series had no numeric coordinate/value pairs; parent retained",
                source=source_key,
                observation_id=obs_id,
            )
        elif not value_sel.available:
            self.result.add_queue(
                work.work_id,
                locator,
                ["value"],
                value_sel.reason or "source values have no printed scalar/series point",
                source=source_key,
                observation_id=obs_id,
            )

        uncertainty = uncertainty_for(obs.get("uncertainty"))
        t_m_unc = values.get("T_m_uncertainty_K") if isinstance(values, Mapping) else None
        if q_token is Quantity.TRANSITION_TEMPERATURE and t_m_unc is not None:
            verbatim = {"T_m_uncertainty_K": t_m_unc}
            if isinstance(obs.get("uncertainty"), Mapping):
                verbatim.update(obs.get("uncertainty"))
            elif obs.get("uncertainty"):
                verbatim["source_uncertainty"] = obs.get("uncertainty")
            uncertainty = Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=verbatim)
        observation = Observation(
            observation_id=obs_id,
            experiment_id=experiment_id,
            identity=identity,
            value=value,
            uncertainty=uncertainty,
            evidence=evidence,
            admission=admission,
            notices=(Notice(
                kind=NoticeKind.OUT_OF_CERTIFIED_BAND,
                affected_quantities=(Quantity.P_PARTIAL,),
                reason=_bulk_composition_pressure_fence(values),
                origin=obs_id,
                band=str(values["equilibrium_status"]),
            ),) if _bulk_composition_pressure_fence(values) else (),
            source_id=source_id,
            locator=locator,
            read_from=read_from,
            point_conditions=point_conditions,
            derived_from=derived_from,
        )
        self._queue_unstated_derived_lineage(
            work.work_id,
            locator,
            source_key,
            obs_id,
            evidence,
            derived_from,
            None,
        )
        self._add_observation(observation, source_key)

    def _emit_exploded_point(
        self,
        *,
        parent_id: str,
        item: Mapping[str, Any],
        work: Work,
        source_id: str,
        source_key: str,
        experiment_id: str,
        locator: Locator,
        identity_base: tuple[Quantity | State[Quantity], Species, dict[str, Any]],
        evidence: Evidence,
        admission: Admission,
        uncertainty: Uncertainty,
        units: str,
        read_from: str,
        derived_from: tuple[str, ...] | None = None,
        notices: tuple[Notice, ...] = (),
        equipment: object = None,
        parent_values: object = None,
    ) -> None:
        raw_item = item.get("item")
        index = item.get("index", 0)
        quantity, species, ident_kwargs = identity_base
        coord = None
        val = None
        trail = "identity"
        extra_unc = None
        point_locator = locator
        t_trail: str | None = None
        t_original: object = None
        value_sel: SourceSelection | None = None
        point_oxide_map: dict[str, Decimal] | None = None
        if isinstance(raw_item, Mapping):
            q_for_species = (
                quantity.value
                if isinstance(quantity, State) and quantity.is_value
                else (quantity if isinstance(quantity, Quantity) else None)
            )
            sample = raw_item.get("sample") or raw_item.get("id")
            sample_label = sample.strip() if isinstance(sample, str) else None
            point_oxide_map = _oxide_map_from_mapping(raw_item)
            if q_for_species in _BULK_PROPERTY_QUANTITIES:
                species_formula = bulk_property_species_formula(
                    quantity=q_for_species,
                    parent_formula=species.formula,
                    sample_label=sample_label,
                    oxide_map=point_oxide_map,
                )
                species = make_species(
                    species_formula,
                    species.phase,
                    polymorph=species.polymorph,
                    charge=species.charge,
                )
            elif sample_label:
                species = make_species(
                    sample_label,
                    species.phase,
                    polymorph=species.polymorph,
                    charge=species.charge,
                )
            if raw_item.get("locator"):
                point_locator = (
                    locator_from_mapping(
                        raw_item.get("locator"), fallback=f"{parent_id}:point:{index}"
                    )
                    or locator
                )
            t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, raw_item)
            t_trail = t_sel.unit_trail
            t_original = _temperature_field_raw(raw_item, t_sel.field_name)
            coord = t_sel.amount
            if t_sel.field_name and not t_sel.available:
                self.result.add_queue(
                    work.work_id,
                    point_locator,
                    ["temperature_K"],
                    t_sel.reason or "series temperature_K is not numeric",
                    source=source_key,
                    observation_id=(series_point_id(parent_id, temperature=coord, extra=series_row_extra(raw_item) if isinstance(raw_item, Mapping) else None) if coord is not None else f"{parent_id}::point:{index}"),
                )
            q_token = quantity.value if isinstance(quantity, State) and quantity.is_value else (
                quantity if isinstance(quantity, Quantity) else None
            )
            value_sel = select_declared_source(q_token, units, raw_item)
            val = value_sel.amount
            trail = value_sel.unit_trail
            if not value_sel.available and value_sel.field_name in {"P", "p"}:
                self.result.add_queue(
                    work.work_id,
                    point_locator,
                    ["value"],
                    value_sel.reason
                    or "series P is not grounded in a source pressure unit",
                    source=source_key,
                    observation_id=(series_point_id(parent_id, temperature=coord, extra=series_row_extra(raw_item) if isinstance(raw_item, Mapping) else None) if coord is not None else f"{parent_id}::point:{index}"),
                )
            for key in value_sel.unused_ancillary:
                self.result.add_queue(
                    work.work_id,
                    point_locator,
                    ["value"],
                    (
                        f"ancillary column {key} is not the declared quantity; "
                        "left out (own-quantity identity incomplete)"
                    ),
                    source=source_key,
                    observation_id=(series_point_id(parent_id, temperature=coord, extra=series_row_extra(raw_item) if isinstance(raw_item, Mapping) else None) if coord is not None else f"{parent_id}::point:{index}"),
                )
            extra_unc = raw_item.get("sigma") or raw_item.get("gamma_SD")
        row_extra = series_row_extra(raw_item) if isinstance(raw_item, Mapping) else None
        if coord is not None:
            point_id = series_point_id(parent_id, temperature=coord, extra=row_extra)
        else:
            point_id = _rekey_ordinal_point_observation_id(
                f"{parent_id}::point:{index}",
                temperature=None,
            )
            # No T on the point: keep a deterministic content key from printed
            # payload fields rather than the encounter index alone.
            if point_id.endswith(f"::point:{index}") and isinstance(raw_item, Mapping):
                printed = raw_item.get("as_published") or raw_item.get("value")
                if printed is not None:
                    point_id = f"{parent_id}::printed:{printed}"
                else:
                    point_id = f"{parent_id}::point:{index}"

        ident_kwargs = dict(ident_kwargs)
        if coord is not None:
            ident_kwargs["temperature_K"] = State.of(coord)
        q_token_point = (
            quantity.value
            if isinstance(quantity, State) and quantity.is_value
            else (quantity if isinstance(quantity, Quantity) else None)
        )
        if ident_kwargs.get("composition") is None:
            initial_map = _initial_oxide_map_from_values(parent_values)
            if initial_map:
                ident_kwargs["composition"] = State.of(
                    wt_pct_to_mole_fraction(initial_map)
                )
            elif q_token_point in _BULK_PROPERTY_QUANTITIES:
                ident_kwargs["composition"] = State.unknown(
                    composition_unknown_reason()
                )
        identity = fill_identity(quantity, species, **ident_kwargs)
        if value_sel is not None and value_sel.available:
            emitted = value_sel.value
            original_raw = None
            if isinstance(raw_item, Mapping) and value_sel.field_name:
                original_raw = raw_item.get(value_sel.field_name)
            converted = conversion_derivation(trail, original_raw, point_locator)
            derivation = Derivation(
                relation=trail if converted is None else converted.relation,
                inputs=(read_from,),
                parameters=() if converted is None else converted.parameters,
                output_unit=(
                    converted.output_unit
                    if converted is not None
                    else ("Pa" if str(trail).endswith("Pa") else (units or "as_published"))
                ),
            )
        else:
            if value_sel is None:
                q_token = quantity.value if isinstance(quantity, State) and quantity.is_value else (
                    quantity if isinstance(quantity, Quantity) else None
                )
                value_sel = select_declared_source(
                    q_token, units, raw_item if isinstance(raw_item, Mapping) else None
                )
            emitted = value_sel.value
            self.result.add_queue(
                work.work_id,
                point_locator,
                ["value"],
                f"series point {index} stored with unknown value",
                source=source_key,
                observation_id=point_id,
            )
            derivation = None
        unc = uncertainty
        if extra_unc is not None:
            unc = Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim={"sigma": extra_unc, "parent": parent_id},
            )
        point_conditions = None
        if coord is not None:
            point_conditions = {
                "temperature_K": _converted_temperature(
                    coord, t_trail, t_original, point_locator
                )
            }
        lab_pc = point_lab_conditions(
            series_item=raw_item if isinstance(raw_item, Mapping) else None,
            equipment=equipment,
            vocabulary=self._vocab,
            locator=point_locator,
        )
        if lab_pc:
            point_conditions = {**(point_conditions or {}), **lab_pc}
        point_conditions = self._merge_printed_oxygen(
            experiment_id,
            point_conditions,
            (raw_item,) if isinstance(raw_item, Mapping) else (),
            point_locator,
            fallback=(parent_values,) if isinstance(parent_values, Mapping) else (),
        )
        if point_oxide_map:
            printed, residual = _located_printed_and_initial(
                point_oxide_map, point_locator
            )
            extra_pc: dict[str, Located[Any]] = {}
            if printed is not None:
                extra_pc["printed_composition"] = printed
            if residual is not None:
                extra_pc["composition"] = residual
            if extra_pc:
                point_conditions = {**(point_conditions or {}), **extra_pc}
        observation = Observation(
            observation_id=point_id,
            experiment_id=experiment_id,
            identity=identity,
            value=emitted,
            uncertainty=unc,
            evidence=evidence,
            admission=admission,
            notices=notices,
            source_id=source_id,
            locator=point_locator,
            read_from=read_from,
            point_conditions=point_conditions,
            derived_from=derived_from,
            derivation=derivation,
        )
        self._queue_unstated_derived_lineage(
            work.work_id,
            point_locator,
            source_key,
            point_id,
            evidence,
            derived_from,
            derivation,
        )
        self._add_observation(observation, source_key)

    def _queue_unstated_derived_lineage(
        self,
        work_id: str,
        locator: Locator,
        source_key: str,
        observation_id: str,
        evidence: Evidence,
        derived_from: tuple[str, ...] | None,
        derivation: Derivation | None,
    ) -> None:
        if not evidence.class_.is_value:
            return
        if evidence.class_.value not in {
            EvidenceClass.MODEL_DERIVED,
            EvidenceClass.MEASURED_REDUCED,
        }:
            return
        if not derived_from:
            self.result.add_queue(
                work_id,
                locator,
                ["derived_from"],
                "derived evidence class with unstated ancestry; queued for page-grounding",
                source=source_key,
                observation_id=observation_id,
            )
        if derivation is None:
            self.result.add_queue(
                work_id,
                locator,
                ["derivation"],
                "derived evidence class with unstated derivation; queued for page-grounding",
                source=source_key,
                observation_id=observation_id,
            )

    # ------------------------------------------------------------------
    # Named sidecars + ledgers + compilations
    # ------------------------------------------------------------------

    def migrate_named_sources(self) -> None:
        lit = self.literature
        self._migrate_kems(lit / "kems_measurements.yaml")
        self._migrate_mre(lit / "mre_measurements.yaml")
        self._migrate_langmuir(lit / "langmuir_knudsen_flux_validation.yaml")
        self._migrate_refractory(lit / "refractory_vaporization_validation.yaml")
        self._migrate_vacuum_pyrolysis(lit / "vacuum_pyrolysis_measurements.yaml")
        self._migrate_ledger(lit / "gibbs_battery_residual_ledger.yaml")
        self._migrate_ledger(lit / "species_rail_differential_ledger.yaml")

    def _work_from_citation(
        self, citation: str, doi: str | None, source_id: str
    ) -> Work:
        index_row = self.index.get(source_id)
        return self._ensure_work(
            citation=citation or str((index_row or {}).get("citation") or source_id),
            doi=doi or extract_doi((index_row or {}).get("doi"), citation),
            source_id=source_id,
            index_row=index_row,
        )

    def _evidence_for(self, method_class: object, **kwargs: Any) -> tuple[Evidence, str | None]:
        evidence, ev_reason = evidence_for(method_class, **kwargs)
        if ev_reason and (
            ev_reason.startswith("PAGE")
            or ev_reason.startswith("unmapped")
            or ev_reason.startswith("fall-through")
            or ev_reason == "absent method_class"
        ):
            token = str(method_class or "absent")
            self.result.evidence_fallthrough[token] = (
                self.result.evidence_fallthrough.get(token, 0) + 1
            )
        return evidence, ev_reason

    def _generic_obs(
        self,
        *,
        work: Work,
        source_id: str,
        source_key: str,
        observation_id: str,
        locator: Locator,
        quantity: Quantity | State[Quantity],
        species: Species,
        value: Value,
        evidence: Evidence,
        temperature_K: Decimal | State[Decimal] | None = None,
        standard_pressure_Pa: Decimal | None = None,
        uncertainty: Uncertainty | None = None,
        method: State[MethodToken] | None = None,
        equipment: object = None,
        source_row_index: int | None = None,
        derivation: Derivation | None = None,
        per: PerBasis | State[PerBasis] | None = None,
        notices: tuple[Notice, ...] = (),
        value_reason: str | None = None,
    ) -> Observation:
        ident_kwargs: dict[str, Any] = {}
        if isinstance(temperature_K, State):
            ident_kwargs["temperature_K"] = temperature_K
        elif temperature_K is not None:
            ident_kwargs["temperature_K"] = State.of(temperature_K)
        if standard_pressure_Pa is not None:
            ident_kwargs["standard_pressure_Pa"] = State.of(standard_pressure_Pa)
        if per is not None:
            ident_kwargs["per"] = per if isinstance(per, State) else State.of(per)
        q_for_comp = (
            quantity
            if isinstance(quantity, Quantity)
            else (quantity.value if isinstance(quantity, State) and quantity.is_value else None)
        )
        if q_for_comp in _BULK_PROPERTY_QUANTITIES and ident_kwargs.get("composition") is None:
            ident_kwargs["composition"] = State.unknown(composition_unknown_reason())
        identity = fill_identity(quantity, species, **ident_kwargs)
        if species.phase.is_unknown:
            self.result.add_queue(
                work.work_id,
                locator,
                ["phase"],
                species.phase.reason or "missing phase",
                source=source_key,
                observation_id=observation_id,
            )
        if value.kind is ValueKind.UNAVAILABLE:
            q_label = (
                quantity.value
                if isinstance(quantity, Quantity)
                else (quantity.value.value if quantity.is_value else "unknown")
            )
            why = value_reason or value.unavailable_reason or (
                f"{q_label} value is unavailable"
            )
            if str(q_label) not in why:
                why = f"{q_label}: {why}"
            self.result.add_queue(
                work.work_id,
                locator,
                ["value"],
                why,
                source=source_key,
                observation_id=observation_id,
            )
        experiment_id = self._experiment_id(work.work_id, locator, source_id)
        self._ensure_experiment(
            work_id=work.work_id,
            experiment_id=experiment_id,
            locator=locator,
            method=method or State.unknown("source does not state method"),
            equipment=equipment,
            observation_id=observation_id,
            source=source_key,
        )
        point_conditions = None
        if temperature_K is not None and not isinstance(temperature_K, State):
            point_conditions = {"temperature_K": located_value(temperature_K, locator)}
        read_from = choose_read_from(work, locator)
        unmatched = unmatched_read_from_reason(locator, read_from)
        if unmatched:
            self.result.add_queue(
                work.work_id,
                locator,
                ["read_from"],
                unmatched,
                source=source_key,
                observation_id=observation_id,
            )
        observation = Observation(
            observation_id=observation_id,
            experiment_id=experiment_id,
            identity=identity,
            value=value,
            uncertainty=uncertainty or Uncertainty(kind=UncertaintyKind.NONE),
            evidence=evidence,
            admission=Admission(
                status=AdmissionStatus.PENDING,
                reason="no observation admission_status mapped from source",
            ),
            notices=notices,
            source_id=source_id,
            locator=locator,
            read_from=read_from,
            point_conditions=point_conditions,
            derivation=derivation,
        )
        self._add_observation(
            observation, source_key, source_row_index=source_row_index
        )
        return observation

    def _migrate_kems(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        sources = doc.get("sources") if isinstance(doc.get("sources"), Mapping) else {}
        cases = doc.get("cases") if isinstance(doc.get("cases"), Mapping) else {}
        for case_id, case in (cases or {}).items():
            if not isinstance(case, Mapping):
                continue
            src_id = str(case.get("source_id") or case_id)
            src = sources.get(src_id) if isinstance(sources, Mapping) else {}
            src = src if isinstance(src, Mapping) else {}
            citation = str(src.get("citation") or "")
            doi = extract_doi(src.get("doi"), citation)
            work = self._work_from_citation(citation, doi, src_id)
            points = case.get("points") or []
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, Mapping):
                    continue
                require_rail_if_stated(point)
                count.rows_in += 1
                loc = locator_from_mapping(
                    point.get("source_locator"), fallback=str(point.get("observable_id"))
                )
                assert loc is not None
                formula = str(point.get("species") or "unknown")
                phase, _unmapped = map_phase(point.get("phase"))
                species = make_species(formula, phase)
                coord = point.get("coordinate") if isinstance(point.get("coordinate"), Mapping) else {}
                t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, coord or {})
                t = t_sel.amount if t_sel.available else None
                p_sel = select_declared_source(
                    Quantity.P_PARTIAL, "Pa", point
                )
                value = p_sel.value
                if not p_sel.available:
                    self.result.add_queue(
                        work.work_id,
                        loc,
                        ["value"],
                        "kems point has null partial_pressure_pa",
                        source=rel,
                        observation_id=str(point.get("observable_id")),
                    )
                self._generic_obs(
                    work=work,
                    source_id=src_id,
                    source_key=rel,
                    observation_id=str(point.get("observable_id") or case_id),
                    locator=loc,
                    quantity=Quantity.P_PARTIAL,
                    species=species,
                    value=value,
                    evidence=self._evidence_for(point.get("method_class"))[0],
                    temperature_K=t,
                    method=map_method(point.get("regime") or point.get("method")),
                    uncertainty=uncertainty_for(point.get("uncertainty")),
                )

    def _migrate_mre(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        measurements = doc.get("measurements") or {}
        if not isinstance(measurements, Mapping):
            return
        for meas_id, meas in measurements.items():
            if not isinstance(meas, Mapping):
                continue
            paper = meas.get("paper_citation") or {}
            citation = str(
                (paper.get("title") if isinstance(paper, Mapping) else None) or meas_id
            )
            doi = extract_doi(paper.get("doi") if isinstance(paper, Mapping) else None)
            work = self._work_from_citation(citation, doi, str(meas_id))
            cases = meas.get("cases") or {}
            if not isinstance(cases, Mapping):
                continue
            for case_id, case in cases.items():
                if not isinstance(case, Mapping):
                    continue
                for point in case.get("comparison_points") or []:
                    if not isinstance(point, Mapping):
                        continue
                    require_rail_if_stated(point)
                    count.rows_in += 1
                    loc = locator_from_mapping(
                        point.get("source_locator"), fallback=str(point.get("observable_id"))
                    )
                    assert loc is not None
                    formula = str(point.get("species") or "unknown")
                    stated_q = point.get("quantity") or point.get("observable_id")
                    mre_quantity, q_reason = map_quantity(
                        None,
                        {"quantity": stated_q} if stated_q else None,
                        row=point,
                    )
                    if q_reason:
                        self.result.add_queue(
                            work.work_id,
                            loc,
                            ["quantity"],
                            q_reason,
                            source=rel,
                            observation_id=f"{meas_id}:{case_id}:{point.get('observable_id')}",
                        )
                    mre_sel = select_declared_source(mre_quantity, point.get("units"), point)
                    value = mre_sel.value
                    if not mre_sel.available:
                        self.result.add_queue(
                            work.work_id,
                            loc,
                            ["value"],
                            mre_sel.reason or "mre point has no expected_value",
                            source=rel,
                            observation_id=f"{meas_id}:{case_id}:{point.get('observable_id')}",
                        )
                    self._generic_obs(
                        work=work,
                        source_id=str(meas_id),
                        source_key=rel,
                        observation_id=f"{meas_id}:{case_id}:{point.get('observable_id')}",
                        locator=loc,
                        quantity=mre_quantity,
                        species=make_species(
                            formula, map_phase(point.get("phase"))[0]
                        ),
                        value=value,
                        evidence=(
                            evidence_for(
                                point.get("method_class")
                                or point.get("method")
                                or point.get("regime")
                            )[0]
                            if (
                                point.get("method_class")
                                or point.get("method")
                                or point.get("regime")
                            )
                            else Evidence(
                                class_=State.unknown(
                                    "mre sidecar does not state method_class"
                                )
                            )
                        ),
                        method=State.unknown("mre method not a closed token"),
                        uncertainty=uncertainty_for(point.get("uncertainty")),
                    )

    def _migrate_vacuum_pyrolysis(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        measurements = doc.get("measurements") or {}
        if not isinstance(measurements, Mapping):
            return
        for meas_id, meas in measurements.items():
            if not isinstance(meas, Mapping):
                continue
            # Same paper as kems-044-robinot-2026. The extract already stores
            # these yields against the PDF Work asset. Emitting them here
            # would set read_from unknown:robinot_2026_deposit_measurements,
            # which is not an asset of that Work.
            if str(meas_id) == "robinot_2026_deposit_measurements":
                continue
            paper = meas.get("paper_citation") if isinstance(meas.get("paper_citation"), Mapping) else {}
            citation = str(
                (paper or {}).get("label")
                or (paper or {}).get("citation_id")
                or meas_id
            )
            doi = extract_doi(meas.get("doi"), (paper or {}).get("doi"), citation)
            work = self._work_from_citation(citation, doi, str(meas_id))
            method = _vacuum_pyrolysis_sidecar_method(str(meas_id))
            t_payload = _vacuum_pyrolysis_sidecar_temperature(meas)
            evidence_token = (
                "measured_direct"
                if meas.get("evidence_class") == "experiment-grade"
                else meas.get("evidence_class")
            )
            evidence, _ev_reason = self._evidence_for(evidence_token)
            for point in meas.get("comparison_points") or []:
                if not isinstance(point, Mapping):
                    continue
                observable = str(point.get("observable_id") or point.get("observable") or "")
                quantity = _vacuum_pyrolysis_sidecar_quantity(observable)
                if quantity is None:
                    continue
                require_rail_if_stated(point)
                count.rows_in += 1
                loc = locator_from_mapping(
                    point.get("source_locator"), fallback=observable or str(meas_id)
                )
                assert loc is not None
                payload = dict(point)
                payload["quantity"] = quantity.value
                payload.setdefault(quantity.value, point.get("expected_value"))
                payload.update(t_payload)
                value_sel = select_declared_source(quantity, point.get("units"), payload)
                t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, payload)
                t_state: Decimal | State[Decimal] | None
                if t_sel.available:
                    t_state = t_sel.amount
                elif t_sel.condition_ranges:
                    name, lo, hi = t_sel.condition_ranges[0]
                    t_state = State.unknown(
                        f"source {name} [{lo}, {hi}]; no midpoint invented"
                    )
                else:
                    t_state = State.unknown(
                        t_sel.reason or "source does not state temperature_K"
                    )
                formula = "unknown"
                cond = meas.get("conditions_reported")
                if isinstance(cond, Mapping):
                    feed = cond.get("feedstock")
                    if isinstance(feed, Mapping) and feed.get("species"):
                        formula = str(feed.get("species"))
                material = meas.get("material_context")
                if formula == "unknown" and isinstance(material, Mapping):
                    formula = str(
                        material.get("feedstock_label_reported")
                        or material.get("feedstock_id")
                        or "unknown"
                    )
                formula = bulk_property_species_formula(
                    quantity=quantity,
                    parent_formula=formula,
                    sample_label=formula,
                    oxide_map=None,
                )
                self._generic_obs(
                    work=work,
                    source_id=str(meas_id),
                    source_key=rel,
                    observation_id=f"{meas_id}:{observable}",
                    locator=loc,
                    quantity=quantity,
                    species=make_species(formula, State.unknown("sidecar does not state phase")),
                    value=value_sel.value,
                    evidence=evidence,
                    temperature_K=t_state,
                    method=method,
                    uncertainty=uncertainty_for(point.get("uncertainty")),
                    value_reason=value_sel.reason,
                )

    def _migrate_langmuir(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        measurements = doc.get("measurements") or {}
        if not isinstance(measurements, Mapping):
            return
        for meas_id, meas in measurements.items():
            if not isinstance(meas, Mapping):
                continue
            require_rail_if_stated(meas)
            count.rows_in += 1
            src = meas.get("source") if isinstance(meas.get("source"), Mapping) else {}
            citation = str((src or {}).get("citation") or meas_id)
            doi = extract_doi((src or {}).get("doi"), citation)
            work = self._work_from_citation(citation, doi, str((src or {}).get("citation_id") or meas_id))
            loc = Locator(record=str(meas_id), note=citation)
            formula = str(meas.get("species") or "unknown")
            phase, _unmapped = map_phase(meas.get("phase"))
            payload = meas.get("measured_langmuir_to_effusion_flux_ratio") or {}
            alpha_payload: dict[str, Any] = {}
            if isinstance(payload, Mapping):
                if payload.get("value") is not None:
                    alpha_payload["alpha"] = payload.get("value")
                if payload.get("range") is not None:
                    alpha_payload["range"] = payload.get("range")
            alpha_sel = select_declared_source(
                Quantity.EVAPORATION_COEFFICIENT_ALPHA, None, alpha_payload
            )
            value = alpha_sel.value
            if alpha_sel.value.kind is ValueKind.INTERVAL:
                unc = Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=payload)
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["value"],
                    "alpha reported as range; no midpoint invented",
                    source=rel,
                    observation_id=str(meas_id),
                )
            elif alpha_sel.available:
                unc = Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=payload)
            else:
                unc = Uncertainty(kind=UncertaintyKind.NONE)
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["value"],
                    alpha_sel.reason or "no alpha value",
                    source=rel,
                    observation_id=str(meas_id),
                )
            t_range = meas.get("temperature_range_k")
            t_payload: dict[str, Any] = {"temperature_range_k": t_range}
            if isinstance(t_range, list) and len(t_range) == 2 and t_range[0] == t_range[1]:
                t_payload["T_K"] = t_range[0]
            t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, t_payload)
            t: Decimal | State[Decimal] | None = t_sel.amount if t_sel.available else None
            if t_sel.condition_ranges and t is None:
                self.result.measured.range_only_T += 1
                name, lo, hi = t_sel.condition_ranges[0]
                reason = f"source {name} [{lo}, {hi}]; no midpoint invented"
                t = State.unknown(reason)
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["temperature_K"],
                    reason,
                    source=rel,
                    observation_id=str(meas_id),
                )
            self._generic_obs(
                work=work,
                source_id=str((src or {}).get("citation_id") or meas_id),
                source_key=rel,
                observation_id=str(meas_id),
                locator=loc,
                quantity=Quantity.EVAPORATION_COEFFICIENT_ALPHA,
                species=make_species(formula, phase),
                value=value,
                evidence=self._evidence_for(meas.get("method_class"))[0],
                temperature_K=t,
                uncertainty=unc,
                method=map_method(meas.get("regime") or meas.get("method")),
            )

    def _migrate_refractory(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        nodes = doc.get("nist_janaf_named_nodes") or {}
        if isinstance(nodes, Mapping):
            citation = str(nodes.get("source") or "NIST-JANAF")
            doi = extract_doi(nodes.get("doi"), citation)
            work = self._work_from_citation(citation, doi, "janaf-4th")
            t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, nodes)
            t = t_sel.amount if t_sel.available else None
            std_block = doc.get("standard_pressure") if isinstance(doc.get("standard_pressure"), Mapping) else {}
            p_sel = select_declared_source(
                AXIS_STANDARD_PRESSURE_PA, None, std_block or {}
            )
            p_std = p_sel.amount if p_sel.available else None
            for bucket in ("log10_kf", "condensed_log10_kf"):
                block = nodes.get(bucket)
                if not isinstance(block, Mapping):
                    continue
                for formula, payload in block.items():
                    if not isinstance(payload, Mapping):
                        continue
                    require_rail_if_stated(payload)
                    count.rows_in += 1
                    loc = Locator(
                        table=str(payload.get("table") or formula),
                        record=str(payload.get("table") or formula),
                    )
                    phase, _unmapped = map_phase(payload.get("phase"))
                    kf_sel = select_declared_source(Quantity.LOG10_KF, None, payload)
                    self._generic_obs(
                        work=work,
                        source_id="janaf-4th",
                        source_key=rel,
                        observation_id=f"refractory:{bucket}:{formula}",
                        locator=loc,
                        quantity=Quantity.LOG10_KF,
                        species=make_species(str(formula), phase),
                        value=kf_sel.value,
                        evidence=self._evidence_for(payload.get("method_class"))[0],
                        temperature_K=t,
                        standard_pressure_Pa=p_std,
                        method=map_method(payload.get("method") or payload.get("regime")),
                    )
        cao = doc.get("cao_reducing_cell_kems") or {}
        if isinstance(cao, Mapping):
            citation = str(cao.get("source") or "Shornikov 2025")
            doi = extract_doi(cao.get("doi"), citation)
            work = self._work_from_citation(citation, doi, "shornikov-2025-cao")
            for i, row in enumerate(cao.get("raw_pCa") or []):
                if not isinstance(row, Mapping):
                    continue
                require_rail_if_stated(row)
                count.rows_in += 1
                t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, row)
                t = t_sel.amount if t_sel.available else None
                loc = Locator(
                    record=(
                        f"raw_pCa:T={t}"
                        if t is not None
                        else f"raw_pCa[{i}]"
                    )
                )
                p_sel = select_declared_source(Quantity.P_PARTIAL, None, row)
                from simulator.battery.records import Derivation

                derivation = None
                if p_sel.available and p_sel.field_name == "pressure_atm":
                    derivation = Derivation(
                        relation="atm_to_Pa",
                        inputs=(choose_read_from(work, loc),),
                        parameters=(
                            (
                                "pressure_atm",
                                located_value(row.get("pressure_atm"), loc),
                            ),
                        ),
                        output_unit="Pa",
                    )
                self._generic_obs(
                    work=work,
                    source_id="shornikov-2025-cao",
                    source_key=rel,
                    observation_id=(
                        cao_raw_pca_id(temperature=t)
                        if t is not None
                        else f"cao_raw_pCa_row:{row.get('as_published') or row.get('pressure_atm') or i}"
                    ),

                    locator=loc,
                    quantity=Quantity.P_PARTIAL,
                    species=make_species("Ca", map_phase(row.get("phase"))[0]),
                    value=p_sel.value,
                    evidence=self._evidence_for(row.get("method_class"))[0],
                    temperature_K=t,
                    method=map_method(row.get("method") or row.get("regime")),
                    derivation=derivation,
                )

    def _migrate_ledger(self, path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(self.root).as_posix()
        count = self._count(rel)
        doc = load_yaml(path)
        if not isinstance(doc, Mapping):
            return
        # battery field is a ledger name, not an 8-rail token — do not rail-map it.
        metric_units = str(doc.get("metric_units") or "").strip()
        energy_unit_ok = metric_units in {"", "kJ/mol", "kJ_per_mol"}
        points = doc.get("points") or []
        if not isinstance(points, list):
            return
        for row_index, point in enumerate(points):
            if not isinstance(point, Mapping):
                continue
            require_rail_if_stated(point)
            count.rows_in += 1
            source_id = str(point.get("source_id") or path.stem)
            if path.name == "species_rail_differential_ledger.yaml":
                # Every point's source_id names the compilation compared against
                # (janaf, pankratz-1987-usbm-b689, …), not a battery work. The
                # work migrated from this file is always the differential
                # ledger. works/ALIASES.yaml pins species-rail-differential to
                # citation_hash("janaf") so historical janaf-token experiment
                # ids stay stable; pankratz-token ledger experiments move onto
                # that same ledger work (off the real USBM B689 work).
                source_id = "species-rail-differential"
            citation = source_id
            index_row = self.index.get(source_id)
            work = self._work_from_citation(
                str((index_row or {}).get("citation") or citation),
                extract_doi((index_row or {}).get("doi")),
                source_id,
            )
            loc = Locator(
                record=str(point.get("observation_id") or point.get("key")),
                table=str(point.get("observation_id") or ""),
            )
            formula = str(point.get("species") or "unknown")
            stated_q = point.get("comparison_quantity") or point.get("quantity")
            obs_id = str(
                point.get("key")
                or f"{source_id}:{point.get('observation_id')}:{point.get('temperature_K')}"
            )
            log10_psat_reason = (
                "the table value is the Gibbs energy of the vaporization "
                "reaction and the reaction identity is not lifted"
            )
            per: PerBasis | None = None
            derivation = None
            notices: tuple[Notice, ...] = ()
            value_reason = None
            if stated_q == "log10_Psat_over_P0":
                ledger_quantity = State.unknown(log10_psat_reason)
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["quantity", "value"],
                    log10_psat_reason,
                    source=rel,
                    observation_id=obs_id,
                )
                table_sel = _unavailable_selection(log10_psat_reason)
                value_reason = log10_psat_reason
            else:
                quantity_payload = dict(point)
                if stated_q and not quantity_payload.get("quantity"):
                    quantity_payload["quantity"] = stated_q
                units = "kJ_per_mol" if energy_unit_ok else None
                if not energy_unit_ok and stated_q not in {"log10_Kf", "log10_kf"}:
                    ledger_quantity = State.unknown(
                        f"ledger header metric_units {metric_units!r} is not kJ/mol"
                    )
                    q_reason = ledger_quantity.reason
                    table_sel = _unavailable_selection(
                        f"ledger header metric_units {metric_units!r} is not kJ/mol"
                    )
                    value_reason = table_sel.reason
                else:
                    ledger_quantity, q_reason = map_quantity(
                        None, quantity_payload, units=units, row=point
                    )
                    select_q = ledger_quantity
                    if stated_q == "log10_Kf":
                        select_q = Quantity.LOG10_KF
                        ledger_quantity = State.of(Quantity.LOG10_KF)
                        q_reason = None
                    table_sel = select_declared_source(
                        select_q,
                        None if stated_q == "log10_Kf" else units,
                        point,
                    )
                    if (
                        stated_q == "log10_Kf"
                        and table_sel.field_name == "table_kJ_mol"
                    ):
                        table_sel = _unavailable_selection(
                            "table_kJ_mol is Gibbs energy, not log10_Kf"
                        )
                if q_reason:
                    self.result.add_queue(
                        work.work_id,
                        loc,
                        ["quantity"],
                        q_reason,
                        source=rel,
                        observation_id=obs_id,
                    )
                if stated_q == "delta_fG_kJ_per_mol_O2":
                    ledger_quantity = State.of(Quantity.DELTA_FG)
                    per = PerBasis.MOL_O2
                    if energy_unit_ok:
                        table_sel = select_declared_source(
                            Quantity.DELTA_FG, units, point
                        )
                    note = str(point.get("note") or "")
                    if note:
                        derivation = Derivation(
                            relation=note,
                            inputs=(choose_read_from(work, loc),),
                            parameters=(),
                            output_unit="kJ/mol",
                        )
            t_sel = select_declared_source(AXIS_TEMPERATURE_K, None, point)
            t = t_sel.amount if t_sel.available else None
            value = table_sel.value
            finding = str(point.get("finding_class") or "")
            if finding == "compilation_table_self_check":
                q_for_notice = (
                    ledger_quantity.value
                    if isinstance(ledger_quantity, Quantity)
                    else (
                        ledger_quantity.value
                        if isinstance(ledger_quantity, State) and ledger_quantity.is_value
                        else Quantity.LOG10_KF
                    )
                )
                notices = (
                    Notice(
                        kind=NoticeKind.DERIVATION_USES_COMPILATION,
                        affected_quantities=(q_for_notice,),
                        reason=(
                            "diagnostic compilation_table_self_check; "
                            f"provenance_class={point.get('provenance_class')} "
                            f"status={point.get('status')}; not a scoring residual"
                        ),
                        origin=obs_id,
                    ),
                )
            evidence = Evidence(
                class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
                original_method_class=str(
                    point.get("method_class")
                    or point.get("provenance_class")
                    or "compilation"
                ),
                model=str(point.get("finding_class") or point.get("provenance_class") or ""),
            )
            self._generic_obs(
                work=work,
                source_id=source_id,
                source_key=rel,
                observation_id=obs_id,
                locator=loc,
                quantity=ledger_quantity,
                species=make_species(formula, map_phase(point.get("phase"))[0]),
                value=value,
                evidence=evidence,
                temperature_K=t if t is not None and t > 0 else None,
                method=map_method(point.get("method") or point.get("regime")),
                source_row_index=row_index,
                derivation=derivation,
                per=per,
                notices=notices,
                value_reason=value_reason,
            )
            if t is not None and t <= 0:
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["temperature_K"],
                    "nonpositive temperature is not a physical T; stored unknown",
                    source=rel,
                    observation_id=obs_id,
                )

    def migrate_compilations(self, directory: Path | None = None) -> None:
        root = directory or (self.literature / "compilations")
        if not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in {".yaml", ".yml", ".json"}:
                continue
            if path.name in {"README.md"} or "__pycache__" in path.parts:
                continue
            self._migrate_compilation_file(path)

    def _migrate_compilation_file(self, path: Path) -> None:
        rel = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)
        count = self._count(rel)
        try:
            doc = load_json(path) if path.suffix == ".json" else load_yaml(path)
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            count.metadata_in += 1
            return
        if not isinstance(doc, Mapping):
            count.metadata_in += 1
            return
        schema = str(doc.get("schema_version") or "")
        if _is_compilation_metadata(path):
            count.metadata_in += 1
            if path.name == "manifest.yaml":
                source_id = str(doc.get("source_id") or path.parent.name)
                src = doc.get("source") if isinstance(doc.get("source"), Mapping) else {}
                citation = str((src or {}).get("citation") or source_id)
                doi = extract_doi((src or {}).get("doi"), citation)
                self._work_from_citation(citation, doi, source_id)
                count.works_out = 1
            return
        if schema != "literature_compilation.v1" and "table" not in doc and "record_id" not in doc:
            count.metadata_in += 1
            return
        source_id = str(doc.get("source_id") or path.parent.parent.name)
        src = doc.get("source") if isinstance(doc.get("source"), Mapping) else {}
        citation = str((src or {}).get("citation") or source_id)
        doi = extract_doi((src or {}).get("doi"), citation)
        work = self._work_from_citation(citation, doi, source_id)
        role = doc.get("compilation_role") if isinstance(doc.get("compilation_role"), Mapping) else {}
        evidence = Evidence(
            class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
            original_method_class=str((role or {}).get("kind") or "compilation"),
            model=str((role or {}).get("kind") or ""),
        )
        require_rail_if_stated(doc)
        table = doc.get("table") if isinstance(doc.get("table"), Mapping) else None
        if table is not None:
            require_rail_if_stated(table)
            rows = table.get("values") or []
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, Mapping):
                        require_rail_if_stated(row)
            if _is_nist_janaf_table(path, doc):
                self._lift_janaf_from_generator(work, source_id, rel, count, doc)
                return
        if _is_usgs_b1544_record(path, doc):
            self._lift_b1544_from_generator(work, source_id, rel, count, doc)
            return
        if _is_usgs_b1452_record(path, doc):
            self._lift_b1452_from_generator(work, source_id, rel, count, doc)
            return
        if _is_usgs_b1259_record(path, doc):
            self._lift_b1259_from_generator(work, source_id, rel, count, doc)
            return
        count.rows_in += 1
        record_id = str(doc.get("record_id") or path.stem)
        formula = str(doc.get("formula") or record_id)
        phase, _unmapped = map_phase(compilation_phase_text(doc))
        loc_raw = doc.get("source_locator")
        locator = locator_from_mapping(loc_raw, fallback=record_id) or Locator(record=record_id)
        if not locator.source_path:
            locator = replace(locator, source_path=rel)
        t = _compilation_temperature_state(doc)
        p_std = None
        rows = doc.get("rows")
        series_items: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                item = dict(row)
                if "T_K" not in item:
                    if row.get("T") is not None:
                        item["T_K"] = row.get("T")
                    elif isinstance(row.get("temperature"), Mapping):
                        item["T_K"] = row["temperature"].get("value")
                    elif row.get("temperature") is not None:
                        item["T_K"] = row.get("temperature")
                series_items.append(item)
        quantity, q_reason = compilation_quantity_from_record(doc)
        if q_reason:
            self.result.add_queue(
                work.work_id,
                locator,
                ["quantity"],
                q_reason,
                source=rel,
                observation_id=f"{source_id}:{record_id}",
            )
        if series_items:
            sel = select_declared_source(
                quantity,
                None,
                {
                    "series": series_items,
                    "rows": rows,
                    "units_as_published": doc.get("units_as_published"),
                    "column_ids": doc.get("column_ids"),
                    "columns": doc.get("columns"),
                },
            )
        else:
            sel = select_declared_source(quantity, None, doc)
        value = sel.value
        # Quantity.DELTA_FH is a closed v2.1 token; formation-enthalpy fields are
        # projected above via compilation_quantity_from_record / QUANTITY_SOURCE_FIELDS.
        # Do not re-queue a "not a v2.1 Quantity token" refusal here.
        self._generic_obs(
            work=work,
            source_id=source_id,
            source_key=rel,
            observation_id=f"{source_id}:{record_id}",
            locator=locator,
            quantity=quantity,
            species=make_species(formula, phase),
            value=value,
            evidence=evidence,
            temperature_K=t,
            standard_pressure_Pa=p_std,
            method=State.of(MethodToken.TABULATION),
        )

    def _lift_janaf_from_generator(
        self,
        work: Work,
        source_id: str,
        rel: str,
        count: SourceCount,
        doc: Mapping[str, Any],
    ) -> None:
        from simulator.battery.generators.janaf import generate_table

        generated = generate_table(doc, source_path=rel)
        count.rows_in += 1
        for observation in generated.observations:
            read_from = choose_read_from(work, observation.locator)
            updates: dict[str, Any] = {}
            if read_from != observation.read_from:
                updates["read_from"] = read_from
            if observation.derivation is not None and observation.derivation.inputs != (
                read_from,
            ):
                # Generator staging records the table path. The store requires
                # derivation.inputs to resolve as a Work asset or observation id.
                updates["derivation"] = replace(
                    observation.derivation, inputs=(read_from,)
                )
            if updates:
                observation = replace(observation, **updates)
            self._ensure_experiment(
                work_id=work.work_id,
                experiment_id=observation.experiment_id,
                locator=observation.locator,
                method=State.of(MethodToken.TABULATION),
                equipment=None,
                observation_id=observation.observation_id,
                source=rel,
            )
            if observation.identity.species.phase.is_unknown:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["phase"],
                    observation.identity.species.phase.reason or "missing phase",
                    source=rel,
                    observation_id=observation.observation_id,
                )
            if observation.value.kind is ValueKind.UNAVAILABLE:
                q_label = (
                    observation.identity.quantity.value
                    if observation.identity.quantity.is_value
                    else "unknown"
                )
                why = observation.value.unavailable_reason or (
                    f"{q_label} value is unavailable"
                )
                if str(q_label) not in why:
                    why = f"{q_label}: {why}"
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["value"],
                    why,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            unmatched = unmatched_read_from_reason(
                observation.locator, observation.read_from
            )
            if unmatched:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["read_from"],
                    unmatched,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            self._add_observation(observation, rel)

    def _lift_b1544_from_generator(
        self,
        work: Work,
        source_id: str,
        rel: str,
        count: SourceCount,
        doc: Mapping[str, Any],
    ) -> None:
        from simulator.battery.generators.usgs_b1544 import generate_record

        generated = generate_record(doc)
        count.rows_in += 1
        for observation in generated.observations:
            read_from = choose_read_from(work, observation.locator)
            updates: dict[str, Any] = {}
            if read_from != observation.read_from:
                updates["read_from"] = read_from
            if observation.derivation is not None and observation.derivation.inputs != (
                read_from,
            ):
                # Generator staging records the JSON path. The store requires
                # derivation.inputs to resolve as a Work asset or observation id.
                updates["derivation"] = replace(
                    observation.derivation, inputs=(read_from,)
                )
            if updates:
                observation = replace(observation, **updates)
            self._ensure_experiment(
                work_id=work.work_id,
                experiment_id=observation.experiment_id,
                locator=observation.locator,
                method=State.of(MethodToken.TABULATION),
                equipment=None,
                observation_id=observation.observation_id,
                source=rel,
            )
            if observation.identity.species.phase.is_unknown:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["phase"],
                    observation.identity.species.phase.reason or "missing phase",
                    source=rel,
                    observation_id=observation.observation_id,
                )
            if observation.value.kind is ValueKind.UNAVAILABLE:
                q_label = (
                    observation.identity.quantity.value
                    if observation.identity.quantity.is_value
                    else "unknown"
                )
                why = observation.value.unavailable_reason or (
                    f"{q_label} value is unavailable"
                )
                if str(q_label) not in why:
                    why = f"{q_label}: {why}"
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["value"],
                    why,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            unmatched = unmatched_read_from_reason(
                observation.locator, observation.read_from
            )
            if unmatched:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["read_from"],
                    unmatched,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            self._add_observation(observation, rel)

    def _lift_b1452_from_generator(
        self,
        work: Work,
        source_id: str,
        rel: str,
        count: SourceCount,
        doc: Mapping[str, Any],
    ) -> None:
        from simulator.battery.generators.usgs_b1452 import generate_record

        generated = generate_record(doc)
        count.rows_in += 1
        for observation in generated.observations:
            read_from = choose_read_from(work, observation.locator)
            updates: dict[str, Any] = {}
            if read_from != observation.read_from:
                updates["read_from"] = read_from
            if observation.derivation is not None and observation.derivation.inputs != (
                read_from,
            ):
                # Generator staging records the JSON path. The store requires
                # derivation.inputs to resolve as a Work asset or observation id.
                updates["derivation"] = replace(
                    observation.derivation, inputs=(read_from,)
                )
            if updates:
                observation = replace(observation, **updates)
            self._ensure_experiment(
                work_id=work.work_id,
                experiment_id=observation.experiment_id,
                locator=observation.locator,
                method=State.of(MethodToken.TABULATION),
                equipment=None,
                observation_id=observation.observation_id,
                source=rel,
            )
            if observation.identity.species.phase.is_unknown:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["phase"],
                    observation.identity.species.phase.reason or "missing phase",
                    source=rel,
                    observation_id=observation.observation_id,
                )
            if observation.value.kind is ValueKind.UNAVAILABLE:
                q_label = (
                    observation.identity.quantity.value
                    if observation.identity.quantity.is_value
                    else "unknown"
                )
                why = observation.value.unavailable_reason or (
                    f"{q_label} value is unavailable"
                )
                if str(q_label) not in why:
                    why = f"{q_label}: {why}"
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["value"],
                    why,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            unmatched = unmatched_read_from_reason(
                observation.locator, observation.read_from
            )
            if unmatched:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["read_from"],
                    unmatched,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            self._add_observation(observation, rel)

    def _lift_b1259_from_generator(
        self,
        work: Work,
        source_id: str,
        rel: str,
        count: SourceCount,
        doc: Mapping[str, Any],
    ) -> None:
        from simulator.battery.generators.usgs_b1259 import generate_record

        generated = generate_record(doc)
        count.rows_in += 1
        for observation in generated.observations:
            read_from = choose_read_from(work, observation.locator)
            updates: dict[str, Any] = {}
            if read_from != observation.read_from:
                updates["read_from"] = read_from
            if observation.derivation is not None and observation.derivation.inputs != (
                read_from,
            ):
                # Generator staging records the JSON path. The store requires
                # derivation.inputs to resolve as a Work asset or observation id.
                updates["derivation"] = replace(
                    observation.derivation, inputs=(read_from,)
                )
            if updates:
                observation = replace(observation, **updates)
            self._ensure_experiment(
                work_id=work.work_id,
                experiment_id=observation.experiment_id,
                locator=observation.locator,
                method=State.of(MethodToken.TABULATION),
                equipment=None,
                observation_id=observation.observation_id,
                source=rel,
            )
            if observation.identity.species.phase.is_unknown:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["phase"],
                    observation.identity.species.phase.reason or "missing phase",
                    source=rel,
                    observation_id=observation.observation_id,
                )
            if observation.value.kind is ValueKind.UNAVAILABLE:
                q_label = (
                    observation.identity.quantity.value
                    if observation.identity.quantity.is_value
                    else "unknown"
                )
                why = observation.value.unavailable_reason or (
                    f"{q_label} value is unavailable"
                )
                if str(q_label) not in why:
                    why = f"{q_label}: {why}"
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["value"],
                    why,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            unmatched = unmatched_read_from_reason(
                observation.locator, observation.read_from
            )
            if unmatched:
                self.result.add_queue(
                    work.work_id,
                    observation.locator,
                    ["read_from"],
                    unmatched,
                    source=rel,
                    observation_id=observation.observation_id,
                )
            self._add_observation(observation, rel)

    def migrate_index_only_sources(self) -> None:
        rel = "data/literature/INDEX.yaml"
        count = self._count(rel)
        for source_id, row in self.index.items():
            count.index_works_in += 1
            citation = str(row.get("citation") or source_id)
            doi = extract_doi(row.get("doi"), citation)
            self._ensure_work(
                citation=citation,
                doi=doi,
                source_id=source_id,
                index_row=row,
            )

    def _apply_supersedes(self) -> None:
        for old_id, new_id, work_id, locator, source_key in self._pending_supersedes:
            old = self.result.observations.get(old_id)
            if old is None:
                matches = [
                    oid
                    for oid in self.result.observations
                    if oid == old_id or oid.startswith(old_id + "::")
                ]
                if not matches:
                    self.result.add_queue(
                        work_id,
                        locator,
                        ["admission.superseded_by"],
                        f"supersedes target {old_id!r} unresolved in this extract",
                        source=source_key,
                        observation_id=new_id,
                    )
                    continue
            else:
                matches = [old_id]
            surviving_new = new_id if new_id in self.result.observations else None
            if surviving_new is None:
                children = [
                    oid
                    for oid in self.result.observations
                    if oid.startswith(new_id + "::")
                ]
                surviving_new = children[0] if children else None
            if surviving_new is None:
                self.result.add_queue(
                    work_id,
                    locator,
                    ["admission.superseded_by"],
                    f"superseding observation {new_id!r} was not retained",
                    source=source_key,
                    observation_id=new_id,
                )
                continue
            new_obs = self.result.observations[surviving_new]
            for match_id in matches:
                old_obs = self.result.observations[match_id]
                decided = old_obs.admission.decided_by or new_obs.admission.decided_by
                if decided is None and old_obs.locator is not None:
                    decided = AdmissionDecision(
                        worker="extract-supersedes",
                        date="unspecified",
                        evidence=old_obs.locator,
                    )
                object.__setattr__(
                    old_obs,
                    "admission",
                    Admission(
                        status=AdmissionStatus.SUPERSEDED,
                        reason="superseded by a later observation in the same extract",
                        superseded_by=surviving_new,
                        decided_by=decided,
                    ),
                )

    def _resolve_queue_ids(self) -> None:
        observations = self.result.observations
        works = self.result.works
        for entry in self.result.queue:
            oid = entry.observation_id
            if oid and oid not in observations:
                matches = [
                    key
                    for key in observations
                    if key == oid
                    or key.startswith(oid + "::")
                    or key.startswith(oid + ":")
                    or key.endswith(":" + oid)
                ]
                if matches:
                    entry.observation_id = sorted(matches)[0]
            if entry.work_id not in works:
                aliased = self.aliases.get(entry.work_id)
                if aliased and aliased in works:
                    entry.work_id = aliased
                elif entry.observation_id and entry.observation_id in observations:
                    obs = observations[entry.observation_id]
                    exp = self.result.experiments.get(obs.experiment_id)
                    if exp is not None and exp.work_id in works:
                        entry.work_id = exp.work_id

    def finalize(self) -> None:
        self._rebuild_works()
        self._apply_supersedes()
        self._resolve_queue_ids()
        # Drop superseded_by pointers that do not resolve in the corpus.
        for obs in list(self.result.observations.values()):
            target = obs.admission.superseded_by
            if target and target not in self.result.observations:
                self.result.add_queue(
                    self.result.experiments[obs.experiment_id].work_id or "",
                    obs.locator,
                    ["admission.superseded_by"],
                    f"superseded_by {target!r} does not resolve; admission left pending",
                    observation_id=obs.observation_id,
                )
                object.__setattr__(
                    obs,
                    "admission",
                    Admission(
                        status=AdmissionStatus.PENDING,
                        reason=f"unresolved supersedes target {target}",
                    ),
                )
        for work in self.result.works.values():
            if work.doi:
                self.result.measured.doi_works += 1
            else:
                self.result.measured.no_doi_works += 1
        validation = validate_corpus(
            self.result.works,
            self.result.experiments,
            self.result.observations,
            residuals=None,
            benches=self.result.benches,
            context_rows={
                str(row["context_id"]): row
                for rows in self.result.context_by_work.values()
                for row in rows
            },
        )
        self.result.validation = ValidationReport(
            validation.issues + tuple(self.result.registry_issues)
        )

    def run(self) -> MigrationResult:
        self.migrate_index_only_sources()
        self.migrate_extracts()
        self.migrate_named_sources()
        self.migrate_compilations()
        self.finalize()
        return self.result


# Flat queue files repeated the same reason once per observation. Grouping
# keeps that reason once. Locator key order is fixed so two writes match.
QUEUE_SCHEMA_VERSION = "battery_migration_queue.v2"
_QUEUE_LOCATOR_KEY_ORDER = (
    "page",
    "published_page",
    "pdf_page_index",
    "table",
    "figure",
    "paragraph",
    "section",
    "equation",
    "line_range",
    "note",
    "source_path",
    "record",
)


def _queue_sort_token(value: object) -> tuple[int, str]:
    if value is None:
        return (1, "")
    if isinstance(value, str):
        return (0, value)
    return (0, json.dumps(value, sort_keys=True, default=str))


def _canonicalize_locator(locator: object) -> dict[str, object]:
    if locator is None:
        return {}
    if not isinstance(locator, Mapping):
        raise TypeError(
            f"queue locator must be a mapping or null, got {type(locator).__name__}"
        )
    present = {str(key): value for key, value in locator.items()}
    ordered: dict[str, object] = {}
    for key in _QUEUE_LOCATOR_KEY_ORDER:
        if key in present:
            ordered[key] = present[key]
    for key in sorted(set(present) - set(_QUEUE_LOCATOR_KEY_ORDER)):
        ordered[key] = present[key]
    return ordered


def _flat_queue_entry(entry: QueueEntry | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(entry, QueueEntry):
        work_id: object = entry.work_id
        locator: object = entry.locator
        axes: object = entry.axes
        why: object = entry.why
        source: object = entry.source
        observation_id: object = entry.observation_id
    elif isinstance(entry, Mapping):
        work_id = entry.get("work_id")
        locator = entry.get("locator")
        axes = entry.get("axes") or []
        why = entry.get("why")
        source = entry.get("source")
        observation_id = entry.get("observation_id")
    else:
        raise TypeError(
            f"queue entry must be a QueueEntry or mapping, got {type(entry).__name__}"
        )
    if isinstance(axes, str) or not isinstance(axes, Iterable):
        raise TypeError(f"queue axes must be a list, got {type(axes).__name__}")
    return {
        "work_id": work_id,
        "locator": _canonicalize_locator(locator),
        "axes": list(axes),
        "why": why,
        "source": source,
        "observation_id": observation_id,
    }


def _observation_sort_key(observation: Mapping[str, Any]) -> tuple:
    locator = observation.get("locator") or {}
    return (
        _queue_sort_token(observation.get("observation_id")),
        json.dumps(locator, sort_keys=True, ensure_ascii=False, default=str),
    )


def _group_sort_key(key: tuple[object, object, object, tuple[object, ...]]) -> tuple:
    work_id, source, why, axes = key
    return (
        _queue_sort_token(work_id),
        _queue_sort_token(source),
        _queue_sort_token(why),
        tuple(_queue_sort_token(axis) for axis in axes),
    )


def group_queue_entries(
    entries: Iterable[QueueEntry | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group flat queue entries by work, source, reason, and axes.

    Each group lists every observation id with its locator. Duplicate flat
    entries stay duplicated so a reason count does not change.
    """

    grouped: dict[
        tuple[object, object, object, tuple[object, ...]],
        list[dict[str, Any]],
    ] = {}
    for raw in entries:
        flat = _flat_queue_entry(raw)
        key = (flat["work_id"], flat["source"], flat["why"], tuple(flat["axes"]))
        grouped.setdefault(key, []).append(
            {
                "observation_id": flat["observation_id"],
                "locator": flat["locator"],
            }
        )
    groups: list[dict[str, Any]] = []
    for key in sorted(grouped, key=_group_sort_key):
        work_id, source, why, axes = key
        observations = sorted(grouped[key], key=_observation_sort_key)
        groups.append(
            {
                "work_id": work_id,
                "source": source,
                "why": why,
                "axes": list(axes),
                "observations": observations,
            }
        )
    return groups


def expand_queue_entries(entries: Iterable[object]) -> list[dict[str, Any]]:
    """Expand grouped queue entries to the flat work/locator/axes/why list.

    A legacy flat entry (one observation id on the entry itself) is returned
    unchanged, so a reader can ask the same questions of either shape.
    """

    flat: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError(
                f"queue entry must be a mapping, got {type(entry).__name__}"
            )
        if "observations" in entry and "observation_id" not in entry:
            observations = entry.get("observations")
            if not isinstance(observations, list):
                raise TypeError(
                    "grouped queue entry observations must be a list, "
                    f"got {type(observations).__name__}"
                )
            for observation in observations:
                if not isinstance(observation, Mapping):
                    raise TypeError(
                        "grouped queue observation must be a mapping, "
                        f"got {type(observation).__name__}"
                    )
                flat.append(
                    {
                        "work_id": entry.get("work_id"),
                        "locator": _canonicalize_locator(observation.get("locator")),
                        "axes": list(entry.get("axes") or []),
                        "why": entry.get("why"),
                        "source": entry.get("source"),
                        "observation_id": observation.get("observation_id"),
                    }
                )
            continue
        flat.append(_flat_queue_entry(entry))
    return flat


def migration_queue_document(
    entries: Iterable[QueueEntry | Mapping[str, Any]],
    dedupe_aliases: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Grouped migration-queue document. Alias order is the caller's order."""

    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "entries": group_queue_entries(entries),
        "dedupe_aliases": [dict(alias) for alias in (dedupe_aliases or ())],
    }


def write_outputs(result: MigrationResult, root: Path | None = None) -> None:
    root = root or REPO_ROOT
    works_dir = root / "data" / "literature" / "works"
    extracts_v2 = root / "data" / "literature" / "extracts-v2"
    observations_v2 = root / "data" / "literature" / "observations-v2"
    battery = root / "data" / "battery"
    works_dir.mkdir(parents=True, exist_ok=True)
    extracts_v2.mkdir(parents=True, exist_ok=True)
    observations_v2.mkdir(parents=True, exist_ok=True)
    battery.mkdir(parents=True, exist_ok=True)

    live_work_ids = set(result.works)
    result.aliases = {k: v for k, v in result.aliases.items() if v in live_work_ids}
    dump_yaml(
        {
            "schema_version": "battery_work_aliases.v1",
            "aliases": dict(sorted(result.aliases.items())),
        },
        works_dir / "ALIASES.yaml",
    )

    experiments_by_id = result.experiments
    for work_id, work in sorted(result.works.items()):
        exp_ids = result.experiments_by_work.get(work_id, [])
        payload = {
            "schema_version": "battery_work.v2.1",
            "work": to_plain(work),
            "experiments": [
                to_plain(experiments_by_id[eid])
                for eid in sorted(set(exp_ids))
                if eid in experiments_by_id
            ],
        }
        bench_ids = result.benches_by_work.get(work_id, [])
        if bench_ids:
            payload["benches"] = [
                to_plain(result.benches[bench_id])
                for bench_id in sorted(set(bench_ids))
                if bench_id in result.benches
            ]
        context_rows = result.context_by_work.get(work_id, [])
        if context_rows:
            payload["context"] = [
                to_plain(row)
                for row in sorted(context_rows, key=lambda r: str(r.get("context_id")))
            ]
        dump_yaml(payload, works_dir / work_filename(work_id))

    live_work_files = {work_filename(wid) for wid in live_work_ids}
    live_work_files.add("ALIASES.yaml")
    for path in works_dir.glob("*.yaml"):
        if path.name not in live_work_files:
            path.unlink()

    extract_stems = {p.stem for p in discover_extracts(root / "data" / "literature" / "extracts")}
    # extracts-v2 has no wipe-all path (unlike observations-v2). Unlink
    # siblings whose stem is no longer a live extract (delete / rename) so an
    # orphan cannot re-enter the store — a renamed extract's stale sibling
    # would double-count the same printed data. Runs before any extracts-v2
    # write; zero-observation extracts that still exist are empty-rewritten
    # below, not unlinked.
    for stale in list(extracts_v2.glob("*.yaml")):
        if stale.stem not in extract_stems:
            stale.unlink()
    source_of: dict[str, str] = {}
    for src, ids in result.observations_by_source.items():
        for oid in ids:
            source_of[oid] = src

    grouped: dict[str, list[Observation]] = defaultdict(list)
    for oid, obs in result.observations.items():
        grouped[source_of.get(oid, "unknown")].append(obs)

    # Extracts: one sibling file per extract. Compilations: one file per
    # family (not per harvested JSON record). Named sidecars keep their stem.
    family_groups: dict[str, tuple[Path, list[Observation], list[str]]] = {}
    for src, observations in grouped.items():
        src_path = Path(src)
        if src.startswith("data/literature/extracts/") or src_path.stem in extract_stems:
            dest = extracts_v2 / f"{src_path.stem}.yaml"
            key = f"extract:{src_path.stem}"
        elif src.startswith("data/literature/compilations/"):
            parts = src_path.parts
            family = parts[3] if len(parts) > 3 else src_path.stem
            if family == "janaf":
                element = src_path.stem.split("-", 1)[0]
                dest = observations_v2 / "compilations-janaf" / f"janaf-{element}.yaml"
                key = f"compilation:janaf:{element}"
            elif family in {
                "hemingway-haas-robinson-1982-usgs-b1544",
                "robie-hemingway-fisher-1978-usgs-b1452",
                "robie-waldbaum-1968-usgs-b1259",
            }:
                dest = (
                    observations_v2
                    / f"compilations-{family}"
                    / f"{src_path.stem}.yaml"
                )
                key = f"compilation:{family}:{src_path.stem}"
            else:
                dest = observations_v2 / f"compilations-{family}.yaml"
                key = f"compilation:{family}"
        else:
            dest = observations_v2 / f"{src_path.stem}.yaml"
            key = f"named:{src_path.stem}"
        if key not in family_groups:
            family_groups[key] = (dest, [], [])
        dest_path, obs_list, sources = family_groups[key]
        obs_list.extend(observations)
        sources.append(src)

    written_extract_stems = {
        dest.stem
        for key, (dest, _observations, _sources) in family_groups.items()
        if key.startswith("extract:")
    }

    if observations_v2.exists():
        for stale in iter_observation_store_paths(observations_v2):
            stale.unlink()
        for child in observations_v2.iterdir():
            if (
                child.is_dir()
                and child.name.startswith("compilations-")
                and not child.name.endswith("-reports")
                and not any(child.iterdir())
            ):
                child.rmdir()
    for _, (dest, observations, sources) in sorted(family_groups.items()):
        payload = {
            "schema_version": "battery_observations.v2.1",
            "sources": sorted(set(sources)),
            "observations": [
                to_plain(o) for o in sorted(observations, key=lambda x: x.observation_id)
            ],
        }
        dump_yaml(payload, dest)

    # A zero-observation extract is absent from family_groups. Its previous
    # sibling would otherwise survive and keep rows that now live in context
    # (d-032: context is never an Observation). Replace that sibling only.
    for extract_path in discover_extracts(root / "data" / "literature" / "extracts"):
        if extract_path.stem in written_extract_stems:
            continue
        dest = extracts_v2 / f"{extract_path.stem}.yaml"
        if not dest.is_file():
            continue
        rel = (
            extract_path.relative_to(root).as_posix()
            if extract_path.is_relative_to(root)
            else extract_path.as_posix()
        )
        dump_yaml(
            {
                "schema_version": "battery_observations.v2.1",
                "sources": [rel],
                "observations": [],
            },
            dest,
        )

    dump_yaml(
        migration_queue_document(
            result.queue,
            (
                {
                    "observation_id": alias.observation_id,
                    "source": alias.source,
                    "row_indices": list(alias.row_indices),
                }
                for alias in result.dedupe_aliases
            ),
        ),
        battery / "migration-queue.yaml",
    )
    write_report(result, battery / "migration-report.md")


def write_report(result: MigrationResult, path: Path) -> None:
    measured = result.measured
    hard = 0 if result.validation is None else len(result.validation.hard_issues)
    rows_in = sum(c.rows_in for c in result.source_counts.values())
    obs_out = len(result.observations)
    lines = [
        "# Battery v2.1 migration report",
        "",
        f"rows in: {rows_in}",
        f"records out (observations): {obs_out}",
        f"works: {len(result.works)}",
        f"experiments: {len(result.experiments)}",
        f"queue size: {len(result.queue)}",
        f"identical-payload dedupe aliases: {len(result.dedupe_aliases)}",
        f"metadata files: {sum(c.metadata_in for c in result.source_counts.values())}",
        f"index sources: {sum(c.index_works_in for c in result.source_counts.values())}",
        f"hard issues: {hard}",
        "",
        "## Spec vs measured",
        "",
        "| count | spec | measured |",
        "|---|---:|---:|",
    ]
    mapping = [
        ("citations", "citations"),
        ("doi_works", "doi_works"),
        ("no_doi_works", "no_doi_works"),
        ("admission_statuses", "admission_statuses"),
        ("supersedes", "supersedes"),
        ("series", "series"),
        ("gibbs_reference_pressures", "gibbs_reference_pressures"),
        ("gibbs_reference_100000", "gibbs_reference_100000"),
        ("gibbs_reference_101325", "gibbs_reference_101325"),
        ("formulas", "formulas"),
        ("equipment_payloads", "equipment_payloads"),
        ("absent_admissions", "absent_admissions"),
        ("absent_classes", "absent_classes"),
        ("range_only_T", "range_only_T"),
        ("system_like_phases", "system_like_phases"),
        ("missing_phases", "missing_phases"),
    ]
    for spec_key, attr in mapping:
        spec = SPEC_COUNTS[spec_key]
        got = getattr(measured, attr)
        mark = "" if spec == got else " (mismatch)"
        lines.append(f"| {spec_key} | {spec} | {got}{mark} |")
    lines.append(
        f"| tabulated_lists | — | {measured.tabulated_lists} |"
    )
    if result.evidence_fallthrough:
        lines.extend(
            [
                "",
                "## Evidence-class fall-throughs",
                "",
                "| source method_class | count |",
                "|---|---:|",
            ]
        )
        for token, n in sorted(result.evidence_fallthrough.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{token}` | {n} |")
    unrecognised = result.unrecognised_polymorphs
    lines.extend(
        [
            "",
            "## Unrecognised polymorph tokens",
            "",
            f"degradations: {sum(unrecognised.values())}",
            "",
            "Unrecognised printed spellings become State.unknown with the original "
            "spelling in the reason; they are never dropped and never invented. "
            "Each degradation is queued on species.polymorph.",
        ]
    )
    if unrecognised:
        lines.extend(["", "| token | count |", "|---|---:|"])
        for token, n in sorted(unrecognised.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{token}` | {n} |")
    lines.extend(["", "## Per source", "", "| source | rows in | observations out | queued |", "|---|---:|---:|---:|"])
    queued_by_source: dict[str, int] = defaultdict(int)
    for entry in result.queue:
        queued_by_source[entry.source or ""] += 1
    for src, rec in sorted(result.source_counts.items()):
        lines.append(
            f"| `{src}` | {rec.rows_in} | {rec.observations_out} | {queued_by_source.get(src, 0)} |"
        )
    if result.validation is not None and result.validation.hard_issues:
        hard_census: dict[str, int] = defaultdict(int)
        for issue in result.validation.hard_issues:
            axis = issue.path.rsplit(".", 1)[-1]
            hard_census[f"{issue.reason.value}:{axis}"] += 1
        lines.extend(
            [
                "",
                "## Hard issue census",
                "",
                f"hard issues: {len(result.validation.hard_issues)}",
                "",
                "MODEL_DERIVED / MEASURED_REDUCED keep their mapped evidence class. "
                "Parent pointers are not fabricated. Unstated derived_from / derivation "
                "is a hard conditional_field queued for page-grounding.",
                "",
                "| kind | count |",
                "|---|---:|",
            ]
        )
        for kind, n in sorted(hard_census.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{kind}` | {n} |")
        lines.extend(["", "## Hard issues (first 50)", ""])
        for issue in result.validation.hard_issues[:50]:
            lines.append(f"- `{issue.path}` {issue.reason.value}: {issue.detail}")
    if result.validation is not None and result.validation.issues:
        census: dict[str, int] = defaultdict(int)
        advisory_n = 0
        hard_ids = {id(issue) for issue in result.validation.hard_issues}
        for issue in result.validation.issues:
            if id(issue) in hard_ids:
                continue
            census[issue.reason.value] += 1
            advisory_n += 1
        lines.extend(
            [
                "",
                "## Advisory issue census",
                "",
                f"advisory issues: {advisory_n}",
                "",
                "| kind | count |",
                "|---|---:|",
            ]
        )
        for kind, n in sorted(census.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{kind}` | {n} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def migrate(
    root: Path | None = None,
    *,
    write: bool = True,
) -> MigrationResult:
    migrator = Migrator(root)
    result = migrator.run()
    if write:
        write_outputs(result, root)
    return result


def main(argv: list[str] | None = None) -> int:
    """Lift the empirical battery into schema v2.1 records.

    Writes works, extract-v2 / observations-v2 siblings, the alias registry,
    migration-queue.yaml, and migration-report.md. Does not rewrite extract
    sources, generate residuals, or touch pins.

    The process exits 1 whenever hard_issues is non-zero, including on a
    healthy run against the long-standing baseline. Assert the printed
    hard_issues count against that baseline, not the exit status.

    Landing a store change is three steps:

    1. .venv/bin/python scripts/battery_migrate.py
    2. .venv/bin/python data/literature/build_index.py --write-store-summary
    3. the battery gate
    """

    import argparse

    parser = argparse.ArgumentParser(
        description=inspect.cleandoc(main.__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the lift without writing outputs",
    )
    args = parser.parse_args(argv)
    result = migrate(args.root, write=not args.dry_run)
    hard = 0 if result.validation is None else len(result.validation.hard_issues)
    print(
        f"rows_in={sum(c.rows_in for c in result.source_counts.values())} "
        f"observations={len(result.observations)} works={len(result.works)} "
        f"experiments={len(result.experiments)} queue={len(result.queue)} "
        f"hard_issues={hard}"
    )
    return 0 if hard == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
