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
raise. Unmapped phase strings cannot be stored as ``State.unknown`` because
``Species.phase`` is a closed enum — they are queued and the Observation is
still emitted with a schema-required placeholder documented in the queue.
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
    AssetRole,
    EvidenceClass,
    ExperimentKind,
    MethodToken,
    PerBasis,
    Phase,
    Quantity,
    Rail,
    RegimeClass,
    StateTag,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import (
    Identity,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    profile_for,
)
from simulator.battery.records import (
    Admission,
    AdmissionDecision,
    Apparatus,
    ApparatusGeometry,
    Evidence,
    Experiment,
    FlowRegime,
    Located,
    Locator,
    Observation,
    PressureEnvironment,
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

# Closed automatic phase map only (v2.1 §Migration). Already-canonical
# Phase enum values are accepted as themselves; everything else is queued.
PHASE_MAP = {
    "gas": Phase.G,
    "condensed_solid": Phase.CR,
    "condensed_liquid": Phase.L,
}
for _phase in Phase:
    PHASE_MAP.setdefault(_phase.value, _phase)

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
class SourceCount:
    path: str
    rows_in: int = 0
    observations_out: int = 0
    works_out: int = 0
    experiments_out: int = 0
    queued: int = 0


@dataclass
class MeasuredCounts:
    citations: int = 0
    doi_works: int = 0
    no_doi_works: int = 0
    admission_statuses: int = 0
    supersedes: int = 0
    series: int = 0
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
    unit = (units or "Pa").strip()
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
    unit = (units or "K").strip().lower()
    if unit in {"k", "kelvin"}:
        return amount, "identity:K"
    if unit in {"c", "celsius", "degc", "°c"}:
        return celsius_to_kelvin(amount), "celsius_to_kelvin"
    return None, f"unmapped temperature unit {units!r}"


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def unknown_pressure_environment(reason: str = "source does not state pressure") -> PressureEnvironment:
    return PressureEnvironment(
        total_pressure_Pa=located_unknown(reason),
        sweep_gas=located_unknown(reason),
        regime=FlowRegime(regime_class=State.unknown(reason)),
    )


def map_phase(raw: object) -> tuple[Phase | None, str | None]:
    if raw is None or raw == "":
        return None, "missing"
    text = str(raw).strip()
    mapped = PHASE_MAP.get(text) or PHASE_MAP.get(text.lower())
    if mapped is not None:
        return mapped, None
    return None, text


def map_quantity(obs_type: str | None, values: Mapping[str, Any] | None) -> Quantity:
    if isinstance(values, Mapping):
        raw = values.get("quantity")
        if isinstance(raw, str) and raw in QUANTITY_ALIASES:
            return QUANTITY_ALIASES[raw]
    if obs_type in TYPE_QUANTITY:
        return TYPE_QUANTITY[obs_type]
    return Quantity.DELTA_FG


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
    if mapped is EvidenceClass.QUOTED_ATTRIBUTED and not attribution:
        mapped = EvidenceClass.QUOTED_UNATTRIBUTED
    needs_lineage = mapped in {
        EvidenceClass.MODEL_DERIVED,
        EvidenceClass.MEASURED_REDUCED,
    }
    if needs_lineage:
        # derived_from/derivation would have to be invented; keep class unknown.
        return (
            Evidence(
                class_=State.unknown(
                    f"mapped {mapped.value} but source does not supply lineage"
                ),
                original_method_class=original,
                model=model or original,
            ),
            f"{mapped.value} requires lineage",
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
            date = str(extraction.get("date") or "1970-01-01")
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
                date=str(extraction.get("date") or "1970-01-01"),
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
                date=str(extraction.get("date") or "1970-01-01"),
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


def make_species(formula: str, phase: Phase) -> Species:
    if phase is Phase.CR:
        return Species(
            formula=formula,
            phase=phase,
            polymorph=State.unknown("source does not state polymorph"),
        )
    return Species(
        formula=formula,
        phase=phase,
        polymorph=State.not_applicable("not crystal"),
    )


def fill_identity(
    quantity: Quantity,
    species: Species,
    **known: Any,
) -> Identity:
    """Fill required axes as unknown and permitted-N/A as not_applicable.

    A VALUE on an axis the profile does not require is invalid (v2.1), not an
    extra key. Transition temperatures store T as the observable, never as
    ``identity.temperature_K``.
    """

    if quantity is Quantity.TRANSITION_TEMPERATURE:
        known.pop("temperature_K", None)
    identity = Identity(quantity=quantity, species=species, **known)
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
                    f"profile {quantity.value} does not use {name}"
                )
                changed = True
            elif isinstance(state, State) and state.is_value:
                payload[name] = State.not_applicable(
                    f"profile {quantity.value} does not use {name}"
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
        units = str(payload.get("units") or "")
        if src == "orifice_area" and units.lower() in {"cm2", "cm^2"}:
            # Premise: 1 m² = 10⁴ cm² exactly (SI).
            # Algebra: A_m2 = A_cm2 / 10000.
            amount = amount / Decimal("10000")
        elif src == "sample_surface_area" and units.lower() in {"cm2", "cm^2"}:
            amount = amount / Decimal("10000")
        geometry_kwargs[dest] = located_value(amount, loc)
    geometry = ApparatusGeometry(**geometry_kwargs) if geometry_kwargs else None
    if cell is None and geometry is None:
        return None
    return Apparatus(cell_material_and_liner=cell, geometry=geometry)


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
        total_pressure_Pa=located_value(pa, loc),
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


def source_files_for(
    source_id: str,
    index_row: Mapping[str, Any] | None,
    *,
    extra_assets: tuple[SourceFile, ...] = (),
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
    asset_id = f"pdf:{source_id}"
    files.append(
        SourceFile(
            asset_id=asset_id,
            role=AssetRole.PDF,
            path=str(pdf_path) if pdf_path else "unknown",
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
    files.extend(extra_assets)
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
        self._pending_supersedes: list[tuple[str, str, str]] = []

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
    ) -> Experiment:
        existing = self.result.experiments.get(experiment_id)
        if existing is not None:
            return existing
        cond = conditions or {
            "temperature_K": located_unknown("source does not state a point temperature")
        }
        experiment = Experiment(
            experiment_id=experiment_id,
            kind=ExperimentKind.LITERATURE,
            method=method,
            sample=Sample(),
            conditions=cond,
            pressure_environment=pressure_from_equipment(equipment),
            work_id=work_id,
            locator=locator or Locator(record=experiment_id),
            apparatus=apparatus_from_equipment(equipment),
        )
        self.result.experiments[experiment_id] = experiment
        self.result.experiments_by_work[work_id].append(experiment_id)
        return experiment

    def _add_observation(self, observation: Observation, source_key: str) -> None:
        self.result.observations[observation.observation_id] = observation
        self.result.observations_by_source[source_key].append(observation.observation_id)
        self._obs_source[observation.observation_id] = source_key
        self._count(source_key).observations_out += 1

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
            phase = Phase.G
        elif unmapped_phase:
            measured.system_like_phases += 1
            self.result.add_queue(
                work.work_id,
                locator,
                ["phase"],
                f"phase string {unmapped_phase!r} is not in the closed automatic map; "
                "Species.phase cannot be unknown so g is a schema placeholder",
                source=source_key,
                observation_id=obs_id,
            )
            phase = Phase.G
        species_formula = str(values.get("formula") or formula)
        if values.get("formula"):
            measured.formulas += 1
        species = make_species(species_formula, phase)
        quantity = map_quantity(obs_type, values)
        if values.get("quantity") and str(values.get("quantity")) not in QUANTITY_ALIASES:
            if obs_type not in TYPE_QUANTITY:
                self.result.add_queue(
                    work.work_id,
                    locator,
                    ["quantity"],
                    f"unmapped values.quantity {values.get('quantity')!r}",
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

        raw_adm = values.get("admission_status")
        if raw_adm is None and obs.get("admission_status") is None:
            measured.absent_admissions += 1
        else:
            measured.admission_statuses += 1
        superseded_target = None
        if obs.get("supersedes"):
            measured.supersedes += 1
            target = obs.get("supersedes")
            if isinstance(target, list) and target:
                target = target[0]
            if isinstance(target, str) and target in local_ids:
                superseded_target = f"{source_id}::{target}"
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
            superseded_by=superseded_target,
            extraction=extraction,
            locator=locator,
        )

        if obs.get("equipment"):
            measured.equipment_payloads += 1

        value, exploded = empty_value_from_payload(values, obs_type, obs.get("units"))
        if exploded:
            measured.series += 1

        ident_kwargs: dict[str, Any] = {}
        if t_known is not None and quantity is not Quantity.TRANSITION_TEMPERATURE:
            ident_kwargs["temperature_K"] = State.of(t_known)
        if p_std is not None:
            ident_kwargs["standard_pressure_Pa"] = State.of(p_std)
        if quantity is Quantity.TRANSITION_TEMPERATURE and isinstance(values.get("quantity"), str):
            ident_kwargs["subtype"] = State.of(str(values["quantity"]))
        if (
            quantity is Quantity.TRANSITION_TEMPERATURE
            and t_known is not None
            and value.kind in {ValueKind.UNAVAILABLE, ValueKind.INTERVAL}
        ):
            value = Value.point_of(t_known)
        identity = fill_identity(quantity, species, **ident_kwargs)

        experiment_id = self._experiment_id(work.work_id, locator, source_id)
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
        )
        read_from = work.source_files.files[0].asset_id if work.source_files.files else "unknown"
        if exploded and isinstance(values.get("series"), list):
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
            return

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
        identity_base: tuple[Quantity, Species, dict[str, Any]],
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
        if isinstance(raw_item, Mapping):
            for ck in ("T_K", "T", "temperature_K"):
                if ck in raw_item:
                    coord = _as_dec_or_none(raw_item.get(ck))
                    break
            if "pressure_atm" in raw_item or (
                "P" in raw_item and "atm" in units.lower()
            ):
                raw_p = raw_item.get("pressure_atm", raw_item.get("P"))
                if _as_dec_or_none(raw_p) is not None:
                    val = atm_to_pa(raw_p)
                    trail = "atm_to_Pa"
            elif "pressure_bar" in raw_item or "P_bar" in raw_item:
                raw_p = raw_item.get("pressure_bar", raw_item.get("P_bar"))
                val = bar_to_pa(raw_p) if _as_dec_or_none(raw_p) is not None else None
                trail = "bar_to_Pa"
            else:
                for vk in ("P_Pa", "pressure_Pa", "alpha", "value", "delta_fG", "P", "p"):
                    if vk in raw_item:
                        val = _as_dec_or_none(raw_item.get(vk))
                        break
            extra_unc = raw_item.get("sigma")
        if coord is None or val is None:
            self.result.add_queue(
                work.work_id,
                locator,
                ["value"],
                f"series point {index} missing numeric coordinate/value",
                source=source_key,
                observation_id=parent_id,
            )
            return
        ident_kwargs = dict(ident_kwargs)
        ident_kwargs["temperature_K"] = State.of(coord)
        identity = fill_identity(quantity, species, **ident_kwargs)
        point_id = f"{parent_id}::point:{index}"
        unc = uncertainty
        if extra_unc is not None:
            unc = Uncertainty(
                kind=UncertaintyKind.PRINTED,
                verbatim={"sigma": extra_unc, "parent": parent_id},
            )
        from simulator.battery.records import Derivation

        observation = Observation(
            observation_id=point_id,
            experiment_id=experiment_id,
            identity=identity,
            value=Value.point_of(val),
            uncertainty=unc,
            evidence=evidence,
            admission=admission,
            notices=(),
            source_id=source_id,
            locator=locator,
            read_from=read_from,
            point_conditions={"temperature_K": located_value(coord, locator)},
            derivation=Derivation(
                relation=trail,
                inputs=(read_from,),
                parameters=(),
                output_unit="Pa" if trail.endswith("Pa") else (units or "as_published"),
            ),
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
        quantity: Quantity,
        species: Species,
        value: Value,
        evidence: Evidence,
        temperature_K: Decimal | None = None,
        standard_pressure_Pa: Decimal | None = None,
        uncertainty: Uncertainty | None = None,
        method: State[MethodToken] | None = None,
        equipment: object = None,
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
        )
        point_conditions = None
        if temperature_K is not None:
            point_conditions = {"temperature_K": located_value(temperature_K, locator)}
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
            read_from=work.source_files.files[0].asset_id,
            point_conditions=point_conditions,
        )
        self._add_observation(observation, source_key)
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
                count.rows_in += 1
                loc = locator_from_mapping(
                    point.get("source_locator"), fallback=str(point.get("observable_id"))
                )
                assert loc is not None
                formula = str(point.get("species") or "unknown")
                phase, unmapped = map_phase("gas")
                species = make_species(formula, phase or Phase.G)
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
                    evidence=Evidence(
                        class_=State.of(EvidenceClass.FIGURE_ONLY)
                        if point.get("status") == "absent"
                        else State.unknown("kems sidecar does not state method_class"),
                        original_method_class=str(point.get("extraction_method") or ""),
                    ),
                    temperature_K=t,
                    method=State.of(MethodToken.KNUDSEN_EFFUSION),
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
                    count.rows_in += 1
                    loc = locator_from_mapping(
                        point.get("source_locator"), fallback=str(point.get("observable_id"))
                    )
                    assert loc is not None
                    formula = str(point.get("species") or "O2")
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
                        quantity=Quantity.O2_YIELD
                        if "o2" in str(point.get("observable_id", "")).lower()
                        else Quantity.MASS_LOSS_FRACTION,
                        species=make_species(formula, Phase.G),
                        value=value,
                        evidence=Evidence(
                            class_=State.unknown("mre sidecar does not state method_class")
                        ),
                        method=State.unknown("mre method not a closed token"),
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
            count.rows_in += 1
            src = meas.get("source") if isinstance(meas.get("source"), Mapping) else {}
            citation = str((src or {}).get("citation") or meas_id)
            doi = extract_doi((src or {}).get("doi"), citation)
            work = self._work_from_citation(citation, doi, str((src or {}).get("citation_id") or meas_id))
            loc = Locator(record=str(meas_id), note=citation)
            formula = str(meas.get("species") or "unknown")
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
                species=make_species(formula, Phase.CR if "solid" in str(meas.get("material", "")).lower() else Phase.L),
                value=value,
                evidence=Evidence(class_=State.of(EvidenceClass.MEASURED_DIRECT)),
                temperature_K=t,
                uncertainty=unc,
                method=State.of(MethodToken.LANGMUIR_FREE_EVAPORATION),
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
                    count.rows_in += 1
                    loc = Locator(
                        table=str(payload.get("table") or formula),
                        record=str(payload.get("table") or formula),
                    )
                    phase = Phase.G if bucket == "log10_kf" else Phase.CR
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
                        evidence=Evidence(class_=State.of(EvidenceClass.COMPILATION_ASSESSED)),
                        temperature_K=t,
                        standard_pressure_Pa=p_std,
                        method=State.of(MethodToken.TABULATION),
                    )
        cao = doc.get("cao_reducing_cell_kems") or {}
        if isinstance(cao, Mapping):
            citation = str(cao.get("source") or "Shornikov 2025")
            doi = extract_doi(cao.get("doi"), citation)
            work = self._work_from_citation(citation, doi, "shornikov-2025-cao")
            for i, row in enumerate(cao.get("raw_pCa") or []):
                if not isinstance(row, Mapping):
                    continue
                count.rows_in += 1
                loc = Locator(record=f"raw_pCa[{i}]")
                t = _as_dec_or_none(row.get("temperature_K"))
                p_atm = _as_dec_or_none(row.get("pressure_atm"))
                pa = atm_to_pa(p_atm) if p_atm is not None else None
                from simulator.battery.records import Derivation

                self._generic_obs(
                    work=work,
                    source_id="shornikov-2025-cao",
                    source_key=rel,
                    observation_id=f"cao_raw_pCa_{i}",
                    locator=loc,
                    quantity=Quantity.P_PARTIAL,
                    species=make_species("Ca", Phase.G),
                    value=Value.point_of(pa) if pa is not None else Value(
                        ValueKind.UNAVAILABLE, unavailable_reason="missing pressure_atm"
                    ),
                    evidence=Evidence(class_=State.of(EvidenceClass.MEASURED_TABULATED)),
                    temperature_K=t,
                    method=State.of(MethodToken.KNUDSEN_EFFUSION),
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
        for point in points:
            if not isinstance(point, Mapping):
                continue
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
                quantity=Quantity.DELTA_FG,
                species=make_species(formula, Phase.G),
                value=value,
                evidence=Evidence(
                    class_=State.of(EvidenceClass.COMPILATION_ASSESSED),
                    original_method_class=str(point.get("provenance_class") or ""),
                ),
                temperature_K=t if t is not None and t > 0 else None,
                method=State.of(MethodToken.TABULATION),
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
            count.rows_in += 1
            self.result.add_queue(
                path.stem,
                {"source_path": rel},
                ["document"],
                f"failed to parse: {exc}",
                source=rel,
            )
            return
        if not isinstance(doc, Mapping):
            count.rows_in += 1
            self.result.add_queue(
                path.stem,
                {"source_path": rel},
                ["document"],
                "compilation file is not a mapping",
                source=rel,
            )
            return
        schema = str(doc.get("schema_version") or "")
        if path.name in {"access-status.yaml", "html-era-txt-divergence.yaml"} or path.name == "manifest.yaml":
            count.rows_in += 1
            source_id = str(doc.get("source_id") or path.parent.name)
            src = doc.get("source") if isinstance(doc.get("source"), Mapping) else {}
            citation = str((src or {}).get("citation") or source_id)
            doi = extract_doi((src or {}).get("doi"), citation)
            self._work_from_citation(citation, doi, source_id)
            count.works_out = 1
            return
        if schema != "literature_compilation.v1" and "table" not in doc and "record_id" not in doc:
            count.rows_in += 1
            self.result.add_queue(
                path.stem,
                {"source_path": rel},
                ["document"],
                f"unrecognized compilation document schema {schema!r}",
                source=rel,
            )
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
                observation_id=record_id,
            )
        loc_raw = doc.get("source_locator")
        locator = locator_from_mapping(loc_raw, fallback=record_id) or Locator(record=record_id)
        quantity = Quantity.DELTA_FG
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
                    observation_id=record_id,
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
                observation_id=record_id,
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
            value = (
                Value(ValueKind.SERIES, series=tuple(series_points))
                if series_points
                else Value(
                    ValueKind.UNAVAILABLE,
                    unavailable_reason="printed rows had no T/delta_fG pair",
                )
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
            species=make_species(formula, phase or Phase.G),
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
        phase, unmapped = map_phase(state_token if state_token in PHASE_MAP else None)
        title = str(table.get("title_as_published") or "")
        if phase is None:
            if "(g)" in title or "gas" in title.lower() or state_token in {"g", "ref"}:
                # "ref" is JANAF's elemental reference, not a closed phase.
                if state_token == "ref":
                    self.result.add_queue(
                        work.work_id,
                        {"table": table_id},
                        ["phase"],
                        "JANAF state=ref is not in the closed automatic map",
                        source=rel,
                        observation_id=table_id,
                    )
                phase = Phase.CR if state_token == "ref" else Phase.G
            elif unmapped or state_token:
                self.result.add_queue(
                    work.work_id,
                    {"table": table_id},
                    ["phase"],
                    f"JANAF state {state_token!r} not in closed map",
                    source=rel,
                    observation_id=table_id,
                )
                phase = Phase.G
            else:
                phase = Phase.G
        rows = table.get("values") or []
        count.rows_in += 1
        dg_points: list[tuple[Decimal, Decimal]] = []
        log_points: list[tuple[Decimal, Decimal]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
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
            count.rows_in += 1
            citation = str(row.get("citation") or source_id)
            doi = extract_doi(row.get("doi"), citation)
            self._ensure_work(
                citation=citation,
                doi=doi,
                source_id=source_id,
                index_row=row,
            )

    def finalize(self) -> None:
        self._rebuild_works()
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def migrate(
    root: Path | None = None,
    *,
    write: bool = True,
    validate: bool = True,
) -> MigrationResult:
    migrator = Migrator(root)
    result = migrator.run()
    if not validate:
        result.validation = None
    if write:
        write_outputs(result, root)
    return result
