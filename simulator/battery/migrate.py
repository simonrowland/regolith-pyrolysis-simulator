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

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

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
    Quantity,
    Rail,
    ReferenceStateConvention,
    RegimeClass,
    StateTag,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    Exposure,
    Identity,
    StandardState,
    SweepIdentity,
    WallIdentity,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    profile_for,
)
from simulator.battery.records import (
    Admission,
    AdmissionDecision,
    Annotations,
    Apparatus,
    ApparatusGeometry,
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
    Uncertainty,
    Value,
    Work,
    as_decimal,
)
from simulator.battery.validate import ValidationReport, validate_corpus
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
EXTRACTS_V2_DIR = LITERATURE / "extracts-v2"
OBSERVATIONS_V2_DIR = LITERATURE / "observations-v2"
BATTERY_DIR = REPO_ROOT / "data" / "battery"
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
    "model": EvidenceClass.MODEL_DERIVED,
    "model_derived": EvidenceClass.MODEL_DERIVED,
    "model_derived_assumption": EvidenceClass.MODEL_DERIVED,
    "model_derived_from_Kstar_and_external_gamma": EvidenceClass.MODEL_DERIVED,
    "model_derived_from_Kstar_with_alpha_e_adopted_unity": EvidenceClass.MODEL_DERIVED,
    "model_derived_inverse_fit": EvidenceClass.MODEL_DERIVED,
    "model_derived_second_law_fit": EvidenceClass.MODEL_DERIVED,
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

# Closed automatic phase map (v2.1 §Migration) plus already-canonical Phase
# spellings (g/cr/l/aq/glass/supercooled_l). Extra rows are explicit
# source-spelling entries only — no title inference, no substring heuristics.
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
    # Reviewed source spelling: extract states solid arsenolite + polymorph.
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

QUANTITY_ALIASES = {
    "pure_Psat": Quantity.P_SAT,
    "vapor_pressure": Quantity.P_SAT,
    "partial_pressure": Quantity.P_PARTIAL,
    "potassium_partial_pressure_as_published": Quantity.P_PARTIAL,
    "deltafG": Quantity.DELTA_FG,
    "delta_fG": Quantity.DELTA_FG,
    "delta_fG_kJ_mol": Quantity.DELTA_FG,
    "log10_Kf": Quantity.LOG10_KF,
    "log10_kf": Quantity.LOG10_KF,
    "activity": Quantity.ACTIVITY,
    "activity_coefficient": Quantity.ACTIVITY_COEFFICIENT,
    "activity_coefficient_this_work": Quantity.ACTIVITY_COEFFICIENT,
    "wagner_interaction_parameter": Quantity.INTERACTION_PARAMETER,
    "literature_vaporization_coefficient": Quantity.EVAPORATION_COEFFICIENT_ALPHA,
    "o2_yield": Quantity.O2_YIELD,
    "mass_loss_fraction": Quantity.MASS_LOSS_FRACTION,
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
    reason = str(payload.get("reason") or tag.value)
    if tag is StateTag.NOT_APPLICABLE:
        return State.not_applicable(reason)
    return State.unknown(reason)


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
    return Derivation(
        relation=str(payload.get("relation") or "identity"),
        inputs=tuple(str(x) for x in inputs),
        parameters=tuple(parameters),
        output_unit=str(payload.get("output_unit") or "as_published"),
    )


def _located_from_plain(payload: object, cast) -> Located:
    if isinstance(payload, Located):
        return payload
    if not isinstance(payload, Mapping) or "state" not in payload:
        return Located(_state_from_plain(payload, cast))
    inference = None
    if payload.get("inference"):
        inference = _derivation_from_plain(payload.get("inference"))
    return Located(
        _state_from_plain(payload.get("state"), cast),
        locator=_locator_from_plain(payload.get("locator")),
        inference=inference,
    )


def _species_from_plain(payload: object) -> Species:
    if not isinstance(payload, Mapping):
        raise TypeError(f"species payload must be a mapping, not {payload!r}")
    polymorph = payload.get("polymorph")
    return Species(
        str(payload.get("formula") or "unknown"),
        _state_from_plain(payload.get("phase"), lambda v: Phase(str(v))),
        polymorph=None
        if polymorph is None
        else _state_from_plain(polymorph, str),
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
    return Value(**kwargs)


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
        reason=str(payload.get("reason") or "source does not state admission_status"),
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
    return Sample(
        mass_kg=None if mass is None else _located_from_plain(mass, as_decimal),
        form=None if form is None else _located_from_plain(form, str),
        container=None if container is None else _located_from_plain(container, str),
        printed_composition=None
        if printed is None
        else _located_from_plain(printed, lambda v: v),
        initial_composition=None
        if initial is None
        else _located_from_plain(initial, _composition_from_plain),
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
            kwargs[name] = _located_from_plain(payload[name], as_decimal)
    return ApparatusGeometry(**kwargs) if kwargs else None


def _apparatus_from_plain(payload: object) -> Apparatus | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    cell = payload.get("cell_material_and_liner")
    return Apparatus(
        cell_material_and_liner=None
        if cell is None
        else _located_from_plain(cell, str),
        geometry=_geometry_from_plain(payload.get("geometry")),
    )


def _sweep_gas_from_plain(payload: object) -> SweepGas:
    assert isinstance(payload, Mapping)
    return SweepGas(
        species=str(payload.get("species") or ""),
        flow_sccm=_state_from_plain(payload["flow_sccm"], as_decimal),
        partial_pressure_Pa=_state_from_plain(payload["partial_pressure_Pa"], as_decimal),
    )


def _pressure_env_from_plain(payload: object) -> PressureEnvironment:
    assert isinstance(payload, Mapping)
    regime_raw = payload.get("regime") or {}
    return PressureEnvironment(
        total_pressure_Pa=_located_from_plain(payload["total_pressure_Pa"], as_decimal),
        sweep_gas=_located_from_plain(
            payload["sweep_gas"],
            lambda v: _sweep_gas_from_plain(v) if isinstance(v, Mapping) else v,
        ),
        regime=FlowRegime(
            regime_class=_state_from_plain(
                (regime_raw or {}).get("regime_class"),
                lambda v: RegimeClass(str(v)),
            )
        ),
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
    )


def observation_from_plain(payload: object) -> Observation:
    assert isinstance(payload, Mapping)
    notices = tuple(_notice_from_plain(n) for n in (payload.get("notices") or ()))
    point_conditions = None
    raw_pc = payload.get("point_conditions")
    if isinstance(raw_pc, Mapping):
        point_conditions = {
            str(k): _located_from_plain(v, as_decimal) for k, v in raw_pc.items()
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
        for path in sorted(directory.glob("*.yaml")):
            doc = load_yaml(path)
            if not isinstance(doc, Mapping):
                continue
            for raw_obs in doc.get("observations") or []:
                obs = observation_from_plain(raw_obs)
                observations[obs.observation_id] = obs
    return works, experiments, observations


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
    experiments: dict[str, Experiment] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    experiments_by_work: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    observations_by_source: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    queue: list[QueueEntry] = field(default_factory=list)
    source_counts: dict[str, SourceCount] = field(default_factory=dict)
    measured: MeasuredCounts = field(default_factory=MeasuredCounts)
    aliases: dict[str, str] = field(default_factory=dict)
    dedupe_aliases: list[DedupeAlias] = field(default_factory=list)
    evidence_fallthrough: dict[str, int] = field(default_factory=dict)
    validation: ValidationReport | None = None

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
}


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


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def unknown_pressure_environment(reason: str = "source does not state pressure") -> PressureEnvironment:
    return PressureEnvironment(
        total_pressure_Pa=located_unknown(reason),
        sweep_gas=located_unknown(reason),
        regime=FlowRegime(regime_class=State.unknown(reason)),
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


def map_quantity(
    obs_type: str | None, values: Mapping[str, Any] | None
) -> tuple[State[Quantity], str | None]:
    raw = None
    if isinstance(values, Mapping):
        raw = values.get("quantity")
        if isinstance(raw, str) and raw in QUANTITY_ALIASES:
            return State.of(QUANTITY_ALIASES[raw]), None
        if isinstance(raw, str) and raw in {q.value for q in Quantity}:
            return State.of(Quantity(raw)), None
    if obs_type in TYPE_QUANTITY:
        return State.of(TYPE_QUANTITY[obs_type]), None
    if isinstance(raw, str) and raw:
        return (
            State.unknown(f"unsupported quantity {raw!r}"),
            f"unsupported quantity {raw!r}",
        )
    return State.unknown("source does not state a closed quantity"), "missing quantity"


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
            model=model or (original if mapped is EvidenceClass.AUTHOR_ESTIMATE else None),
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
            reason="source does not state admission_status",
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


def uncertainty_for(raw: object) -> Uncertainty:
    if raw is None or raw == "":
        return Uncertainty(kind=UncertaintyKind.NONE)
    if isinstance(raw, (str, Mapping)):
        return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=raw)
    return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=str(raw))


def make_species(
    formula: str,
    phase: Phase | State[Phase],
    polymorph: State[str] | None = None,
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
    return Species(formula=formula, phase=phase_state, polymorph=polymorph)


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
                payload[name] = State.unknown(f"source does not state {name}")
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


def empty_value_from_payload(
    values: Mapping[str, Any] | None,
    obs_type: str | None,
    units: str | None,
) -> tuple[Value, list[dict[str, Any]]]:
    """Return the archival Value plus printed-point dicts to explode.

    Explosion list items are ``{coord, value, unit, extra}``.
    """

    exploded: list[dict[str, Any]] = []
    if not isinstance(values, Mapping):
        return Value(ValueKind.UNAVAILABLE, unavailable_reason="empty values"), exploded

    series = values.get("series")
    if isinstance(series, list) and series:
        for i, item in enumerate(series):
            exploded.append({"index": i, "item": item, "units": units})
        # Parent keeps a series Value of converted points when possible; the
        # caller may replace this with exploded point Observations.
        return _series_value(series, units), exploded

    tabulated = values.get("tabulated_delta_fG_kJ_mol")
    if isinstance(tabulated, list) and tabulated:
        points: list[tuple[Decimal, Decimal]] = []
        for i, item in enumerate(tabulated):
            if not isinstance(item, Mapping):
                continue
            t = _as_dec_or_none(item.get("T_K") or item.get("T"))
            g = _as_dec_or_none(item.get("delta_fG") or item.get("value"))
            if t is None or g is None:
                continue
            points.append((t, g))
            exploded.append({"index": i, "item": item, "units": "kJ_per_mol"})
        if points:
            return Value(ValueKind.SERIES, series=tuple(points)), exploded

    for key, mapped in (
        ("alpha", Quantity.EVAPORATION_COEFFICIENT_ALPHA),
        ("activity", Quantity.ACTIVITY),
        ("activity_coefficient", Quantity.ACTIVITY_COEFFICIENT),
        ("delta_fG_298_kJ_mol", Quantity.DELTA_FG),
        ("deltafG", Quantity.DELTA_FG),
    ):
        if key in values and _as_dec_or_none(values.get(key)) is not None:
            del mapped
            return Value.point_of(values[key]), exploded

    if values.get("segments"):
        return (
            Value(
                ValueKind.EXPRESSION,
                expression_text="evaluator_segments_as_published",
                expression_parameters=None,
                expression_domain=str(values.get("evaluator_family") or "segments"),
            ),
            exploded,
        )

    if obs_type == "gibbs_table" and values.get("reference_pressure_Pa") is not None:
        return (
            Value(
                ValueKind.EXPRESSION,
                expression_text="gibbs_table_as_published",
                expression_domain=str(values.get("evaluator_family") or "gibbs_table"),
            ),
            exploded,
        )

    # Qualitative / bound / range payloads stay structured, never a midpoint.
    if values.get("semantics") in {"bound_not_point_ordering", "bound_not_point"}:
        return Value(ValueKind.CATEGORICAL, categorical=str(values.get("semantics"))), exploded

    t_range = values.get("T_range_K")
    if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
        lo, hi = _as_dec_or_none(t_range[0]), _as_dec_or_none(t_range[1])
        if lo is not None and hi is not None:
            return Value(ValueKind.INTERVAL, interval_low=lo, interval_high=hi), exploded

    return (
        Value(
            ValueKind.UNAVAILABLE,
            unavailable_reason="source values have no printed scalar/series point",
        ),
        exploded,
    )


_PRESSURE_SERIES_KEYS = (
    ("pressure_atm", "atm"),
    ("p_atm", "atm"),
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


def _series_point_value(
    raw_item: Mapping[str, Any],
    q_token: Quantity | None,
    units: str | None,
) -> tuple[Decimal | None, str, tuple[str, ...]]:
    """Pick the printed value from the declared quantity, not the first numeric key."""

    unused: list[str] = []
    if q_token is Quantity.ACTIVITY_COEFFICIENT:
        for vk in ("gamma", "activity_coefficient"):
            if vk in raw_item:
                val = _as_dec_or_none(raw_item.get(vk))
                unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
                return val, "as_published", tuple(unused)
        unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
        return None, "as_published", tuple(unused)
    if q_token is Quantity.EVAPORATION_COEFFICIENT_ALPHA and "alpha" in raw_item:
        unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
        return _as_dec_or_none(raw_item.get("alpha")), "as_published", tuple(unused)
    if q_token is Quantity.DELTA_FG:
        for vk in ("delta_fG", "value"):
            if vk in raw_item:
                unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
                return _as_dec_or_none(raw_item.get(vk)), "as_published", tuple(unused)
    if q_token in {Quantity.P_SAT, Quantity.P_PARTIAL, None}:
        for key, unit in _PRESSURE_SERIES_KEYS:
            if key in raw_item:
                val, trail = convert_pressure_to_pa(raw_item.get(key), unit)
                unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
                return val, trail or "identity", tuple(unused)
        # Species-labelled pressures are p_partial only when that is the declared quantity.
        if q_token is Quantity.P_PARTIAL:
            for key in ("p_Ga_Pa", "p_In_Pa", "p_O2_calc_Pa"):
                if key in raw_item:
                    val, trail = convert_pressure_to_pa(raw_item.get(key), "Pa")
                    unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item and k != key]
                    return val, trail or "identity:Pa", tuple(unused)
    for vk in ("alpha", "gamma", "value", "delta_fG"):
        if vk in raw_item:
            unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
            return _as_dec_or_none(raw_item.get(vk)), "as_published", tuple(unused)
    unused = [k for k in _ANCILLARY_SERIES_KEYS if k in raw_item]
    return None, "identity", tuple(unused)


def _series_value(series: list[Any], units: str | None) -> Value:
    points: list[tuple[Decimal, Decimal]] = []
    for item in series:
        if isinstance(item, Mapping):
            coord = None
            val = None
            for ck in ("T_K", "T", "temperature_K", "t_s"):
                if ck in item:
                    coord = _as_dec_or_none(item.get(ck))
                    break
            for vk in (
                "P_Pa",
                "P",
                "pressure_Pa",
                "pressure_atm",
                "alpha",
                "value",
                "delta_fG",
                "p",
            ):
                if vk in item:
                    val = _as_dec_or_none(item.get(vk))
                    if val is not None and vk == "pressure_atm":
                        val = atm_to_pa(val)
                    break
            if coord is not None and val is not None:
                points.append((coord, val))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            c, v = _as_dec_or_none(item[0]), _as_dec_or_none(item[1])
            if c is not None and v is not None:
                points.append((c, v))
    if points:
        return Value(ValueKind.SERIES, series=tuple(points))
    return Value(
        ValueKind.UNAVAILABLE,
        unavailable_reason="series list had no numeric coordinate/value pairs",
    )


def apparatus_from_equipment(equipment: object) -> Apparatus | None:
    if not isinstance(equipment, Mapping) or not equipment:
        return None
    cell = None
    geometry_kwargs: dict[str, Located[Decimal]] = {}
    raw_cell = equipment.get("cell_material")
    if isinstance(raw_cell, Mapping) and raw_cell.get("value") is not None:
        loc = locator_from_mapping(raw_cell.get("locator"))
        cell = located_value(str(raw_cell["value"]), loc)
    field_map = {
        "orifice_area": "orifice_area_m2",
        "clausing_factor": "clausing_factor",
        "sample_surface_area": "exposed_area_m2",
    }
    for src, dest in field_map.items():
        payload = equipment.get(src)
        if not isinstance(payload, Mapping):
            continue
        amount = _as_dec_or_none(payload.get("value"))
        if amount is None:
            continue
        loc = locator_from_mapping(payload.get("locator"))
        if src in {"orifice_area", "sample_surface_area"}:
            converted, trail = convert_area_to_m2(amount, payload.get("units"))
            if converted is None:
                geometry_kwargs[dest] = located_unknown(trail or "unmapped area unit")
            else:
                inference = None
                if payload.get("inferred") or payload.get("inference"):
                    inference = Derivation(
                        relation=str(payload.get("inference") or "inferred"),
                        inputs=(str(payload.get("locator") or dest),),
                        parameters=(),
                        output_unit="m2",
                    )
                elif trail:
                    inference = conversion_derivation(trail, amount, loc)
                geometry_kwargs[dest] = Located(
                    State.of(converted), locator=loc, inference=inference
                )
            continue
        geometry_kwargs[dest] = located_value(amount, loc)
    geometry = ApparatusGeometry(**geometry_kwargs) if geometry_kwargs else None
    if cell is None and geometry is None:
        return None
    return Apparatus(cell_material_and_liner=cell, geometry=geometry)


def sample_from_equipment(equipment: object) -> Sample:
    """Transfer a stated sample payload; never invent mass, form, or units."""

    if not isinstance(equipment, Mapping) or not equipment:
        return Sample()
    mass_src: Mapping[str, Any] | None = None
    implied_unit: str | None = None
    form_located: Located[str] | None = None
    container_located: Located[str] | None = None
    raw_sample = equipment.get("sample")
    if isinstance(raw_sample, Mapping):
        nested = raw_sample.get("mass") or raw_sample.get("mass_kg")
        if isinstance(nested, Mapping):
            mass_src = nested
        form = raw_sample.get("form")
        container = raw_sample.get("container")
        loc = locator_from_mapping(raw_sample.get("locator"))
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
    for key, unit in (
        ("sample_mass", None),
        ("sample_mass_kg", "kg"),
        ("sample_mass_g", "g"),
        ("sample_mass_mg", "mg"),
    ):
        payload = equipment.get(key)
        if isinstance(payload, Mapping) and payload.get("value") is not None:
            mass_src = payload
            implied_unit = unit
            break
    mass_located: Located[Decimal] | None = None
    if isinstance(mass_src, Mapping) and mass_src.get("value") is not None:
        units = mass_src.get("units") or implied_unit
        loc = locator_from_mapping(mass_src.get("locator"))
        kg, trail = convert_mass_to_kg(mass_src.get("value"), units)
        if kg is None:
            mass_located = located_unknown(trail or "missing mass unit")
        else:
            mass_located = Located(
                State.of(kg),
                locator=loc,
                inference=conversion_derivation(trail, mass_src.get("value"), loc),
            )
    if mass_located is None and form_located is None and container_located is None:
        return Sample()
    return Sample(
        mass_kg=mass_located,
        form=form_located,
        container=container_located,
    )


def pressure_from_equipment(equipment: object) -> PressureEnvironment:
    reason = "source does not state pressure"
    if not isinstance(equipment, Mapping):
        return unknown_pressure_environment(reason)
    payload = equipment.get("chamber_pressure")
    if not isinstance(payload, Mapping) or payload.get("value") is None:
        return unknown_pressure_environment(reason)
    pa, trail = convert_pressure_to_pa(payload.get("value"), payload.get("units"))
    loc = locator_from_mapping(payload.get("locator"))
    if pa is None:
        return unknown_pressure_environment(
            trail or "chamber_pressure is not a numeric pressure"
        )
    return PressureEnvironment(
        total_pressure_Pa=Located(
            State.of(pa),
            locator=loc,
            inference=conversion_derivation(trail, payload.get("value"), loc),
        ),
        sweep_gas=located_unknown("source does not state sweep gas"),
        regime=FlowRegime(
            regime_class=State.unknown("source does not state flow regime")
        ),
    )


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
            return "unknown"
        if layer == "ocr":
            for asset in files:
                if asset.role is AssetRole.MINERU_MD and asset.path != "unknown":
                    return asset.asset_id
            # Stated OCR/md layer with no matching INDEX asset: never PDF.
            return "unknown"
        if layer == "pdf":
            for asset in files:
                if asset.role is AssetRole.PDF and asset.path != "unknown":
                    return asset.asset_id
            return "unknown"
    for asset in files:
        if asset.role is AssetRole.PDF and asset.path != "unknown":
            return asset.asset_id
    if files:
        return files[0].asset_id
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
    if not files:
        files.append(
            SourceFile(
                asset_id=f"unknown:{source_id}",
                role=AssetRole.PDFTOTEXT,
                path="unknown",
                sha256=State.unknown("INDEX does not give an asset"),
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
        self.result = MigrationResult(aliases=dict(self.aliases))
        self._work_citations: dict[str, str] = {}
        self._work_dois: dict[str, str | None] = {}
        self._work_source_ids: dict[str, list[str]] = defaultdict(list)
        self._work_index_row: dict[str, Mapping[str, Any] | None] = {}
        self._obs_source: dict[str, str] = {}
        self._obs_row_index: dict[str, int] = {}
        self._pending_supersedes: list[tuple[str, str, str, Locator, str]] = []

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

    def _experiment_id(self, work_id: str, locator: Locator | None, fallback: str) -> str:
        if locator is not None:
            key = locator.table or locator.record or locator.figure or locator.source_path
            if key:
                return f"{work_id}::{key}"
        return f"{work_id}::{fallback}"

    def _ensure_experiment(
        self,
        *,
        work_id: str,
        experiment_id: str,
        locator: Locator | None,
        method: State[MethodToken],
        equipment: object,
        conditions: dict[str, Located[Decimal]] | None = None,
        observation_id: str | None = None,
        source: str | None = None,
    ) -> Experiment:
        existing = self.result.experiments.get(experiment_id)
        if existing is not None:
            return existing
        cond = conditions or {
            "temperature_K": located_unknown("source does not state a point temperature")
        }
        pressure_env = pressure_from_equipment(equipment)
        apparatus = apparatus_from_equipment(equipment)
        sample = sample_from_equipment(equipment)
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

    def migrate_extracts(self, directory: Path | None = None) -> None:
        for path in discover_extracts(directory or self.extracts_dir):
            self._migrate_extract(path)

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
        extraction = doc.get("extraction") if isinstance(doc.get("extraction"), Mapping) else {}
        rows = list(iter_extract_observations(doc))
        count.rows_in += len(rows)
        self.result.measured.citations += 1
        local_ids = {str(obs.get("observation_id")) for _, obs in rows if obs.get("observation_id")}
        for formula, obs in rows:
            self._migrate_extract_observation(
                formula=formula,
                obs=obs,
                work=work,
                source_id=source_id,
                source_key=rel,
                extraction=extraction or {},
                local_ids=local_ids,
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
    ) -> None:
        measured = self.result.measured
        raw_obs_id = str(obs.get("observation_id") or f"{source_id}:missing")
        obs_id = f"{source_id}::{raw_obs_id}"
        values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
        values = values or {}
        locator = locator_from_mapping(
            obs.get("locator"), fallback=f"extract:{source_id}:{obs_id}"
        )
        assert locator is not None
        obs_type = obs.get("type") if isinstance(obs.get("type"), str) else None
        phase_raw = values.get("phase", obs.get("phase")) if values else obs.get("phase")
        phase, unmapped_phase = map_phase(phase_raw)
        if phase_raw is None or phase_raw == "":
            measured.missing_phases += 1
            self.result.add_queue(
                work.work_id,
                locator,
                ["phase"],
                "missing phase",
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
        quantity, q_reason = map_quantity(obs_type, values)
        if q_reason:
            self.result.add_queue(
                work.work_id,
                locator,
                ["quantity"],
                q_reason,
                source=source_key,
                observation_id=obs_id,
            )

        t_known: Decimal | None = None
        t_range = obs.get("T_range_K") or values.get("T_range_K")
        if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
            measured.range_only_T += 1
            self.result.add_queue(
                work.work_id,
                locator,
                ["temperature_K"],
                "range-only T; no midpoint invented",
                source=source_key,
                observation_id=obs_id,
            )
        t_point = values.get("T_K") or obs.get("T_K")
        t_known = _as_dec_or_none(t_point)

        p_std = _as_dec_or_none(values.get("reference_pressure_Pa"))
        if p_std is not None:
            measured.gibbs_reference_pressures += 1
            if p_std == Decimal("100000") or p_std == Decimal("100000.0"):
                measured.gibbs_reference_100000 += 1
            elif p_std == Decimal("101325") or p_std == Decimal("101325.0"):
                measured.gibbs_reference_101325 += 1

        method_class = values.get("method_class")
        if method_class is None:
            measured.absent_classes += 1
        evidence, ev_reason = evidence_for(
            method_class,
            evaluator_family=values.get("evaluator_family"),
            attribution=obs.get("quote") if isinstance(obs.get("quote"), str) else None,
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
            if (
                ev_reason.startswith("PAGE")
                or ev_reason.startswith("unmapped")
                or ev_reason.startswith("fall-through")
                or ev_reason == "absent method_class"
            ):
                token = str(method_class or "absent")
                self.result.evidence_fallthrough[token] = (
                    self.result.evidence_fallthrough.get(token, 0) + 1
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

        value, exploded = empty_value_from_payload(values, obs_type, obs.get("units"))
        if isinstance(values.get("series"), list) and values.get("series"):
            measured.series += 1
        if isinstance(values.get("tabulated_delta_fG_kJ_mol"), list) and values.get(
            "tabulated_delta_fG_kJ_mol"
        ):
            measured.tabulated_lists += 1

        ident_kwargs: dict[str, Any] = {}
        q_token = quantity.value if quantity.is_value else None
        if t_known is not None and q_token is not Quantity.TRANSITION_TEMPERATURE:
            ident_kwargs["temperature_K"] = State.of(t_known)
        if p_std is not None:
            ident_kwargs["standard_pressure_Pa"] = State.of(p_std)
        if q_token is Quantity.TRANSITION_TEMPERATURE and isinstance(values.get("quantity"), str):
            ident_kwargs["subtype"] = State.of(str(values["quantity"]))
        if (
            q_token is Quantity.TRANSITION_TEMPERATURE
            and t_known is not None
            and value.kind in {ValueKind.UNAVAILABLE, ValueKind.INTERVAL}
        ):
            value = Value.point_of(t_known)
        identity = fill_identity(quantity, species, **ident_kwargs)

        experiment_id = self._experiment_id(work.work_id, locator, source_id)
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
            point_conditions = {"temperature_K": located_value(t_known, locator)}
        self._ensure_experiment(
            work_id=work.work_id,
            experiment_id=experiment_id,
            locator=locator,
            method=method,
            equipment=obs.get("equipment"),
            observation_id=obs_id,
            source=source_key,
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
                )
            if self._count(source_key).observations_out > before:
                return
            self.result.add_queue(
                work.work_id,
                locator,
                ["value"],
                "printed series had no numeric coordinate/value pairs; parent retained",
                source=source_key,
                observation_id=obs_id,
            )

        observation = Observation(
            observation_id=obs_id,
            experiment_id=experiment_id,
            identity=identity,
            value=value,
            uncertainty=uncertainty_for(obs.get("uncertainty")),
            evidence=evidence,
            admission=admission,
            notices=(),
            source_id=source_id,
            locator=locator,
            read_from=read_from,
            point_conditions=point_conditions,
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
        if isinstance(raw_item, Mapping):
            if raw_item.get("locator"):
                point_locator = (
                    locator_from_mapping(
                        raw_item.get("locator"), fallback=f"{parent_id}:point:{index}"
                    )
                    or locator
                )
            if "T_K" in raw_item or "temperature_K" in raw_item:
                t_original = raw_item.get("T_K", raw_item.get("temperature_K"))
                coord, t_trail = convert_temperature_to_k(t_original, "K")
                if coord is None:
                    self.result.add_queue(
                        work.work_id,
                        point_locator,
                        ["temperature_K"],
                        t_trail or "series temperature_K is not numeric",
                        source=source_key,
                        observation_id=f"{parent_id}::point:{index}",
                    )
            elif "T_C" in raw_item:
                t_original = raw_item.get("T_C")
                coord, t_trail = convert_temperature_to_k(t_original, "C")
                if coord is None:
                    self.result.add_queue(
                        work.work_id,
                        point_locator,
                        ["temperature_K"],
                        t_trail or "series T_C is not a grounded temperature",
                        source=source_key,
                        observation_id=f"{parent_id}::point:{index}",
                    )
            elif "T" in raw_item:
                t_original = raw_item.get("T")
                t_unit = raw_item.get("T_units") or raw_item.get("temperature_units")
                coord, t_trail = convert_temperature_to_k(t_original, t_unit)
                if coord is None:
                    self.result.add_queue(
                        work.work_id,
                        point_locator,
                        ["temperature_K"],
                        t_trail
                        or "series T is not grounded in a source unit (T_K / T_C required)",
                        source=source_key,
                        observation_id=f"{parent_id}::point:{index}",
                    )
            q_token = quantity.value if isinstance(quantity, State) and quantity.is_value else (
                quantity if isinstance(quantity, Quantity) else None
            )
            val, trail, unused_ancillary = _series_point_value(
                raw_item, q_token, units
            )
            if val is None and ("P" in raw_item or "p" in raw_item) and q_token in {
                Quantity.P_SAT,
                Quantity.P_PARTIAL,
                None,
            }:
                raw_p = raw_item.get("P", raw_item.get("p"))
                val, trail_or_why = convert_pressure_to_pa(raw_p, units)
                if val is None:
                    self.result.add_queue(
                        work.work_id,
                        point_locator,
                        ["value"],
                        trail_or_why
                        or "series P is not grounded in a source pressure unit",
                        source=source_key,
                        observation_id=f"{parent_id}::point:{index}",
                    )
                    trail = trail_or_why or "missing pressure unit"
                else:
                    trail = trail_or_why or "identity"
            for key in unused_ancillary:
                self.result.add_queue(
                    work.work_id,
                    point_locator,
                    ["value"],
                    (
                        f"ancillary column {key} is not the declared quantity; "
                        "left out (own-quantity identity incomplete)"
                    ),
                    source=source_key,
                    observation_id=f"{parent_id}::point:{index}",
                )
            extra_unc = raw_item.get("sigma") or raw_item.get("gamma_SD")
        point_id = f"{parent_id}::point:{index}"
        ident_kwargs = dict(ident_kwargs)
        if coord is not None:
            ident_kwargs["temperature_K"] = State.of(coord)
        identity = fill_identity(quantity, species, **ident_kwargs)
        if val is None:
            emitted = Value(
                ValueKind.UNAVAILABLE,
                unavailable_reason=f"series point {index} has no liftable numeric value",
            )
            self.result.add_queue(
                work.work_id,
                point_locator,
                ["value"],
                f"series point {index} stored with unknown value",
                source=source_key,
                observation_id=point_id,
            )
            derivation = None
        else:
            emitted = Value.point_of(val)
            converted = conversion_derivation(trail, None, point_locator)
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
        unc = uncertainty
        if extra_unc is not None:
            unc = Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim={"sigma": extra_unc, "parent": parent_id},
            )
        point_conditions = None
        if coord is not None:
            point_conditions = {
                "temperature_K": Located(
                    State.of(coord),
                    locator=point_locator,
                    inference=conversion_derivation(t_trail, t_original, point_locator),
                )
            }
        observation = Observation(
            observation_id=point_id,
            experiment_id=experiment_id,
            identity=identity,
            value=emitted,
            uncertainty=unc,
            evidence=evidence,
            admission=admission,
            notices=(),
            source_id=source_id,
            locator=point_locator,
            read_from=read_from,
            point_conditions=point_conditions,
            derivation=derivation,
        )
        self._add_observation(observation, source_key)

    # ------------------------------------------------------------------
    # Named sidecars + ledgers + compilations
    # ------------------------------------------------------------------

    def migrate_named_sources(self) -> None:
        lit = self.literature
        self._migrate_kems(lit / "kems_measurements.yaml")
        self._migrate_mre(lit / "mre_measurements.yaml")
        self._migrate_langmuir(lit / "langmuir_knudsen_flux_validation.yaml")
        self._migrate_refractory(lit / "refractory_vaporization_validation.yaml")
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
        temperature_K: Decimal | None = None,
        standard_pressure_Pa: Decimal | None = None,
        uncertainty: Uncertainty | None = None,
        method: State[MethodToken] | None = None,
        equipment: object = None,
        source_row_index: int | None = None,
        derivation: Derivation | None = None,
    ) -> Observation:
        ident_kwargs: dict[str, Any] = {}
        if temperature_K is not None:
            ident_kwargs["temperature_K"] = State.of(temperature_K)
        if standard_pressure_Pa is not None:
            ident_kwargs["standard_pressure_Pa"] = State.of(standard_pressure_Pa)
        identity = fill_identity(quantity, species, **ident_kwargs)
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
        if temperature_K is not None:
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
                reason="source does not state admission_status",
            ),
            notices=(),
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
                phase, unmapped = map_phase(point.get("phase"))
                if unmapped:
                    self.result.add_queue(
                        work.work_id,
                        loc,
                        ["phase"],
                        "kems sidecar does not state a closed phase token",
                        source=rel,
                        observation_id=str(point.get("observable_id")),
                    )
                species = make_species(formula, phase)
                t = _as_dec_or_none((point.get("coordinate") or {}).get("temperature_K"))
                p = point.get("partial_pressure_pa")
                if p is None:
                    value = Value(
                        ValueKind.UNAVAILABLE,
                        unavailable_reason=str(
                            point.get("extraction_method")
                            or "partial_pressure_pa is null; absence is not a measured zero"
                        ),
                    )
                    self.result.add_queue(
                        work.work_id,
                        loc,
                        ["value"],
                        "kems point has null partial_pressure_pa",
                        source=rel,
                        observation_id=str(point.get("observable_id")),
                    )
                else:
                    value = Value.point_of(p)
                self._generic_obs(
                    work=work,
                    source_id=src_id,
                    source_key=rel,
                    observation_id=str(point.get("observable_id") or case_id),
                    locator=loc,
                    quantity=Quantity.P_PARTIAL,
                    species=species,
                    value=value,
                    evidence=evidence_for(point.get("method_class"))[0],
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
                        None, {"quantity": stated_q} if stated_q else None
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
                    expected = point.get("expected_value")
                    value = (
                        Value.point_of(expected)
                        if _as_dec_or_none(expected) is not None
                        else Value(
                            ValueKind.UNAVAILABLE,
                            unavailable_reason="mre point has no expected_value",
                        )
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
                        evidence=Evidence(
                            class_=State.unknown("mre sidecar does not state method_class")
                        ),
                        method=State.unknown("mre method not a closed token"),
                        uncertainty=uncertainty_for(point.get("uncertainty")),
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
            phase, unmapped = map_phase(meas.get("phase"))
            if unmapped:
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["phase"],
                    "langmuir sidecar does not state a closed phase token",
                    source=rel,
                    observation_id=str(meas_id),
                )
            payload = meas.get("measured_langmuir_to_effusion_flux_ratio") or {}
            if isinstance(payload, Mapping) and _as_dec_or_none(payload.get("value")) is not None:
                value = Value.point_of(payload["value"])
                unc = Uncertainty(
                    kind=UncertaintyKind.PRINTED,
                    verbatim=payload,
                )
            elif isinstance(payload, Mapping) and isinstance(payload.get("range"), list):
                lo, hi = payload["range"][:2]
                value = Value(
                    ValueKind.INTERVAL,
                    interval_low=as_decimal(lo),
                    interval_high=as_decimal(hi),
                )
                unc = Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=payload)
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["value"],
                    "alpha reported as range; no midpoint invented",
                    source=rel,
                    observation_id=str(meas_id),
                )
            else:
                value = Value(ValueKind.UNAVAILABLE, unavailable_reason="no alpha value")
                unc = Uncertainty(kind=UncertaintyKind.NONE)
            t_range = meas.get("temperature_range_k")
            t = None
            if isinstance(t_range, list) and len(t_range) == 2 and t_range[0] == t_range[1]:
                t = _as_dec_or_none(t_range[0])
            elif isinstance(t_range, list) and len(t_range) == 2:
                self.result.measured.range_only_T += 1
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["temperature_K"],
                    "range-only T; no midpoint invented",
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
                evidence=evidence_for(meas.get("method_class"))[0],
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
            t = _as_dec_or_none(nodes.get("temperature_K"))
            p_std = _as_dec_or_none((doc.get("standard_pressure") or {}).get("nist_janaf_pa"))
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
                    phase, unmapped = map_phase(payload.get("phase"))
                    if unmapped:
                        self.result.add_queue(
                            work.work_id,
                            loc,
                            ["phase"],
                            "refractory node does not state a closed phase token",
                            source=rel,
                            observation_id=f"refractory:{bucket}:{formula}",
                        )
                    val = payload.get("value")
                    self._generic_obs(
                        work=work,
                        source_id="janaf-4th",
                        source_key=rel,
                        observation_id=f"refractory:{bucket}:{formula}",
                        locator=loc,
                        quantity=Quantity.LOG10_KF,
                        species=make_species(str(formula), phase),
                        value=Value.point_of(val) if _as_dec_or_none(val) is not None else Value(
                            ValueKind.UNAVAILABLE, unavailable_reason="missing log10_Kf"
                        ),
                        evidence=evidence_for(payload.get("method_class"))[0],
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
                loc = Locator(record=f"raw_pCa[{i}]")
                t = _as_dec_or_none(row.get("temperature_K"))
                p_atm = _as_dec_or_none(row.get("pressure_atm"))
                pa = atm_to_pa(p_atm) if p_atm is not None else None
                from simulator.battery.records import Derivation

                derivation = None
                if pa is not None:
                    derivation = Derivation(
                        relation="atm_to_Pa",
                        inputs=(choose_read_from(work, loc),),
                        parameters=(("pressure_atm", located_value(p_atm, loc)),),
                        output_unit="Pa",
                    )
                self._generic_obs(
                    work=work,
                    source_id="shornikov-2025-cao",
                    source_key=rel,
                    observation_id=f"cao_raw_pCa_{i}",
                    locator=loc,
                    quantity=Quantity.P_PARTIAL,
                    species=make_species("Ca", map_phase(row.get("phase"))[0]),
                    value=Value.point_of(pa) if pa is not None else Value(
                        ValueKind.UNAVAILABLE, unavailable_reason="missing pressure_atm"
                    ),
                    evidence=evidence_for(row.get("method_class"))[0],
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
        points = doc.get("points") or []
        if not isinstance(points, list):
            return
        for row_index, point in enumerate(points):
            if not isinstance(point, Mapping):
                continue
            require_rail_if_stated(point)
            count.rows_in += 1
            source_id = str(point.get("source_id") or path.stem)
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
            ledger_quantity, q_reason = map_quantity(
                None, {"quantity": stated_q} if stated_q else None
            )
            if q_reason:
                self.result.add_queue(
                    work.work_id,
                    loc,
                    ["quantity"],
                    q_reason,
                    source=rel,
                    observation_id=str(point.get("key") or point.get("observation_id")),
                )
            t = _as_dec_or_none(point.get("temperature_K"))
            table_val = point.get("table_kJ_mol")
            if table_val is None:
                value = Value(
                    ValueKind.UNAVAILABLE,
                    unavailable_reason=str(point.get("skip_reason") or "table_kJ_mol is null"),
                )
            else:
                value = Value.point_of(table_val)
            obs_id = str(point.get("key") or f"{source_id}:{point.get('observation_id')}:{t}")
            self._generic_obs(
                work=work,
                source_id=source_id,
                source_key=rel,
                observation_id=obs_id,
                locator=loc,
                quantity=ledger_quantity,
                species=make_species(formula, map_phase(point.get("phase"))[0]),
                value=value,
                evidence=evidence_for(point.get("method_class") or point.get("provenance_class"))[0],
                temperature_K=t if t is not None and t > 0 else None,
                method=map_method(point.get("method") or point.get("regime")),
                source_row_index=row_index,
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
        table = doc.get("table") if isinstance(doc.get("table"), Mapping) else None
        if table is not None:
            self._lift_janaf_table(work, source_id, rel, count, table, evidence, doc)
            return
        require_rail_if_stated(doc)
        count.rows_in += 1
        record_id = str(doc.get("record_id") or path.stem)
        formula = str(doc.get("formula") or record_id)
        phase, unmapped = map_phase(doc.get("phase"))
        if unmapped:
            self.result.add_queue(
                work.work_id,
                {"record": record_id},
                ["phase"],
                f"phase string {doc.get('phase')!r} not in closed map",
                source=rel,
                observation_id=f"{source_id}:{record_id}",
            )
        loc_raw = doc.get("source_locator")
        locator = locator_from_mapping(loc_raw, fallback=record_id) or Locator(record=record_id)
        quantity: Quantity | State[Quantity] = State.unknown(
            "compilation record does not state a closed quantity"
        )
        value: Value
        t = None
        p_std = None
        if "formation_gibbs_energy" in doc or "delta_f_H_298_15" in doc:
            payload = doc.get("delta_f_H_298_15")
            if isinstance(payload, Mapping) and _as_dec_or_none(payload.get("value")) is not None:
                # Formation enthalpy is not a v2.1 Quantity; archive as expression.
                value = Value(
                    ValueKind.EXPRESSION,
                    expression_text="delta_fH_298.15_as_published",
                    expression_domain="J_per_mol",
                )
                self.result.add_queue(
                    work.work_id,
                    locator,
                    ["quantity"],
                    "delta_fH is not a v2.1 Quantity token; stored as expression",
                    source=rel,
                    observation_id=f"{source_id}:{record_id}",
                )
            elif doc.get("intervals"):
                value = Value(
                    ValueKind.EXPRESSION,
                    expression_text="cea_intervals_as_published",
                    expression_domain=str(doc.get("cea_section") or "intervals"),
                )
            else:
                value = Value(
                    ValueKind.UNAVAILABLE,
                    unavailable_reason="compilation record has no printed Gibbs point",
                )
        elif "formation_enthalpy_298_15_K_as_published" in doc:
            raw = doc.get("formation_enthalpy_298_15_K_as_published")
            amount = _as_dec_or_none(raw if not isinstance(raw, Mapping) else raw.get("value"))
            value = (
                Value(
                    ValueKind.EXPRESSION,
                    expression_text="delta_fH_298.15_as_published",
                    expression_domain=str(doc.get("units_as_published") or ""),
                )
                if amount is not None
                else Value(ValueKind.UNAVAILABLE, unavailable_reason="ATcT enthalpy missing")
            )
            self.result.add_queue(
                work.work_id,
                locator,
                ["quantity"],
                "ATcT formation enthalpy is not a v2.1 Quantity token",
                source=rel,
                observation_id=f"{source_id}:{record_id}",
            )
        elif doc.get("g_parameter") or doc.get("functions") or doc.get("intervals"):
            value = Value(
                ValueKind.EXPRESSION,
                expression_text="compilation_coefficient_record",
                expression_domain=str(doc.get("phase") or ""),
            )
        elif isinstance(doc.get("rows"), list):
            series_points: list[tuple[Decimal, Decimal]] = []
            for row in doc["rows"]:
                if not isinstance(row, Mapping):
                    continue
                t_row = _as_dec_or_none(row.get("T") or row.get("temperature") or row.get("T_K"))
                g_row = _as_dec_or_none(
                    row.get("delta_fG")
                    or row.get("Gf")
                    or row.get("delta_fG_kJ_mol")
                )
                if t_row is not None and g_row is not None:
                    series_points.append((t_row, g_row))
            if series_points:
                quantity = Quantity.DELTA_FG
                value = Value(ValueKind.SERIES, series=tuple(series_points))
            else:
                value = Value(
                    ValueKind.UNAVAILABLE,
                    unavailable_reason="printed rows had no T/delta_fG pair",
                )
        else:
            value = Value(
                ValueKind.UNAVAILABLE,
                unavailable_reason="compilation record has no mapped numeric payload",
            )
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

    def _lift_janaf_table(
        self,
        work: Work,
        source_id: str,
        rel: str,
        count: SourceCount,
        table: Mapping[str, Any],
        evidence: Evidence,
        doc: Mapping[str, Any],
    ) -> None:
        table_id = str(table.get("table_id") or "table")
        index_entry = table.get("index_entry") if isinstance(table.get("index_entry"), Mapping) else {}
        formula = str((index_entry or {}).get("formula") or table_id)
        state_token = str((index_entry or {}).get("state") or "")
        phase, unmapped = map_phase(state_token or None)
        if unmapped:
            self.result.add_queue(
                work.work_id,
                {"table": table_id},
                ["phase"],
                (
                    f"JANAF state {state_token!r} is not in the closed automatic map"
                    if state_token
                    else "JANAF table does not state a closed phase"
                ),
                source=rel,
                observation_id=f"{source_id}:{table_id}",
            )
        rows = table.get("values") or []
        count.rows_in += 1
        dg_points: list[tuple[Decimal, Decimal]] = []
        log_points: list[tuple[Decimal, Decimal]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                require_rail_if_stated(row)
                t_payload = row.get("temperature")
                t = None
                if isinstance(t_payload, Mapping):
                    t = _as_dec_or_none(t_payload.get("value"))
                g_payload = row.get("formation_gibbs_energy")
                if isinstance(g_payload, Mapping) and t is not None:
                    g = _as_dec_or_none(g_payload.get("value"))
                    if g is not None:
                        dg_points.append((t, g))
                k_payload = row.get("log10_formation_equilibrium_constant")
                if isinstance(k_payload, Mapping) and t is not None:
                    k = _as_dec_or_none(k_payload.get("value"))
                    if k is not None:
                        log_points.append((t, k))
        loc = Locator(table=table_id, source_path=rel, record=table_id)
        p_std = None
        std = str(table.get("standard_state_as_published") or "")
        if "0.1 MPa" in std or "1 bar" in std:
            p_std = bar_to_pa("1")
        species = make_species(formula, phase)
        if dg_points:
            self._generic_obs(
                work=work,
                source_id=source_id,
                source_key=rel,
                observation_id=f"{source_id}:{table_id}:delta_fG",
                locator=loc,
                quantity=Quantity.DELTA_FG,
                species=species,
                value=Value(ValueKind.SERIES, series=tuple(dg_points)),
                evidence=evidence,
                standard_pressure_Pa=p_std,
                method=State.of(MethodToken.TABULATION),
            )
        if log_points:
            self._generic_obs(
                work=work,
                source_id=source_id,
                source_key=rel,
                observation_id=f"{source_id}:{table_id}:log10_Kf",
                locator=loc,
                quantity=Quantity.LOG10_KF,
                species=species,
                value=Value(ValueKind.SERIES, series=tuple(log_points)),
                evidence=evidence,
                standard_pressure_Pa=p_std,
                method=State.of(MethodToken.TABULATION),
            )
        if not dg_points and not log_points:
            self._generic_obs(
                work=work,
                source_id=source_id,
                source_key=rel,
                observation_id=f"{source_id}:{table_id}",
                locator=loc,
                quantity=Quantity.DELTA_FG,
                species=species,
                value=Value(
                    ValueKind.UNAVAILABLE,
                    unavailable_reason="JANAF table had no numeric ΔfG/log10_Kf points",
                ),
                evidence=evidence,
                standard_pressure_Pa=p_std,
                method=State.of(MethodToken.TABULATION),
            )

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
        self.result.validation = validate_corpus(
            self.result.works,
            self.result.experiments,
            self.result.observations,
            residuals=None,
        )

    def run(self) -> MigrationResult:
        self.migrate_index_only_sources()
        self.migrate_extracts()
        self.migrate_named_sources()
        self.migrate_compilations()
        self.finalize()
        return self.result


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
        dump_yaml(payload, works_dir / work_filename(work_id))

    live_work_files = {work_filename(wid) for wid in live_work_ids}
    live_work_files.add("ALIASES.yaml")
    for path in works_dir.glob("*.yaml"):
        if path.name not in live_work_files:
            path.unlink()

    extract_stems = {p.stem for p in discover_extracts(root / "data" / "literature" / "extracts")}
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

    if observations_v2.exists():
        for stale in observations_v2.glob("*.yaml"):
            stale.unlink()
    for _, (dest, observations, sources) in sorted(family_groups.items()):
        payload = {
            "schema_version": "battery_observations.v2.1",
            "sources": sorted(set(sources)),
            "observations": [
                to_plain(o) for o in sorted(observations, key=lambda x: x.observation_id)
            ],
        }
        dump_yaml(payload, dest)

    dump_yaml(
        {
            "schema_version": "battery_migration_queue.v1",
            "entries": [
                {
                    "work_id": e.work_id,
                    "locator": e.locator,
                    "axes": e.axes,
                    "why": e.why,
                    "source": e.source,
                    "observation_id": e.observation_id,
                }
                for e in result.queue
            ],
            "dedupe_aliases": [
                {
                    "observation_id": alias.observation_id,
                    "source": alias.source,
                    "row_indices": list(alias.row_indices),
                }
                for alias in result.dedupe_aliases
            ],
        },
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
    lines.extend(["", "## Per source", "", "| source | rows in | observations out | queued |", "|---|---:|---:|---:|"])
    queued_by_source: dict[str, int] = defaultdict(int)
    for entry in result.queue:
        queued_by_source[entry.source or ""] += 1
    for src, rec in sorted(result.source_counts.items()):
        lines.append(
            f"| `{src}` | {rec.rows_in} | {rec.observations_out} | {queued_by_source.get(src, 0)} |"
        )
    if result.validation is not None and result.validation.hard_issues:
        lines.extend(["", "## Hard issues (first 50)", ""])
        for issue in result.validation.hard_issues[:50]:
            lines.append(f"- `{issue.path}` {issue.reason.value}: {issue.detail}")
    if result.validation is not None and result.validation.issues:
        census: dict[str, int] = defaultdict(int)
        for issue in result.validation.issues:
            census[issue.reason.value] += 1
        lines.extend(
            [
                "",
                "## Advisory issue census",
                "",
                f"advisory issues: {len(result.validation.issues)}",
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
