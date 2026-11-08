"""Render a streaming, per-engine battery residual-versus-temperature plot.

Example::

    python scripts/battery_hero_plot.py \
      --input data/battery/residuals.jsonl \
      --simulator-commit 79cbaadb7 \
      --openimcc-commit <score-run-openimcc-commit>

The JSONL is read one line at a time. A temporary SQLite file collapses
same-experiment duplicates without retaining the input rows in memory.

Hero history is one append-only row per green store in
``docs-private/research/2026-10-19-three-hero-charts/hero-history.jsonl``;
pass it explicitly with ``--history-path``. Each ``battery_hero_history`` row
has ``schema_version`` and, per rail and engine, n, RMS, population SD, median
|dex|, temperature views, attempted-refusal count and scorer-reason counts,
non-observable count, and open predict-and-flag notice counts by kind. Each row
also carries all-rail attempted-refusal and non-observable counts per engine.
Same-row comparisons are stored for openimcc and internal-analytical against
every outside engine with shared rows, including both engines' metrics and the
shared-row count.
"""

from __future__ import annotations

import argparse
import base64
import csv
from contextlib import contextmanager
import gzip
import hashlib
import html
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import (
    FuncFormatter,
    FormatStrFormatter,
    MaxNLocator,
    MultipleLocator,
)

from simulator.battery.enums import (  # noqa: E402
    NoticeKind,
    Rail,
    ResidualStatus,
    residual_status_token,
)
from simulator.config import load_config_bundle  # noqa: E402
from simulator.diagnostic_helpers.species_rail import (  # noqa: E402
    lowest_tier,
    parse_formula_elements,
)
from simulator.feedstock_composition import resolve_feedstock_composition  # noqa: E402
from simulator.battery.score import (  # noqa: E402
    _BATTERY_HERO_DECK_CHART_IDS,
    _BATTERY_HERO_DECK_SOURCE_RAILS,
    BATTERY_HERO_FINGERPRINT_BAND_BOUNDARIES,
    BATTERY_HERO_FINGERPRINT_SIO2_BIN_WIDTH,
    BATTERY_HERO_FINGERPRINT_TEMPERATURE_BIN_K,
    BATTERY_HERO_MINIMUM_REPORT_N as HERO_MINIMUM_REPORT_N,
    BATTERY_HERO_TEMPERATURE_FLOOR_K as HERO_TEMPERATURE_FLOOR_K,
    BATTERY_HERO_TEMPERATURE_VIEWS as HERO_TEMPERATURE_VIEWS,
    _battery_hero_binned_score_records,
    _battery_hero_metric_records_from_database,
    _battery_hero_pair_comparison_records_from_database,
    _battery_hero_deck_payload,
    _battery_hero_deck_records,
    _battery_hero_deck_store_observations,
    _battery_hero_facet,
    _battery_hero_create_metric_rows,
    _battery_hero_fingerprint_band,
    _battery_hero_minor_foulant_facets,
    _flagged_payload_strata,
    _battery_hero_sio2_mole_fraction,
    _battery_hero_create_fingerprint_values,
    _battery_hero_sweet_spot_bin_indices,
    _battery_hero_temperature_bin_index,
    _battery_hero_unknown_sio2_records_from_database,
    _battery_hero_sweet_spot_records_from_database,
    _residual_engine_observable,
)


DEFAULT_BIN_WIDTH_K = 100.0
# SiO pressure comparisons have their own canonical rail in the scorer.
DEFAULT_RAILS = (
    Rail.VAPOUR.value,
    Rail.MELT_ACTIVITY.value,
    Rail.SIO_EVOLUTION.value,
)
HERO_RAILS = (Rail.VAPOUR.value, Rail.REDOX.value, Rail.MELT_ACTIVITY.value)
HERO_AND_DECK_SOURCE_RAILS = frozenset(HERO_RAILS) | frozenset(
    _BATTERY_HERO_DECK_SOURCE_RAILS
)
HERO_PRIMARY_ENGINES = ("openimcc", "internal-analytical")
HERO_COMPARISON_ENGINES = ("vaporock", "thermoengine", "alphamelts", "magemin")
HERO_TEMPERATURE_FIELD = "candidate_observation.identity.temperature_K"
HERO_SCORECARD_FONT_SIZE = 3.5
HERO_SCORECARD_LINE_HEIGHT_IN = 0.085
HERO_SCORECARD_TABLE_HEIGHT_FRACTION = 0.88
HERO_SCORECARD_TITLE_HEIGHT_IN = 0.16
COLLECTION_OBSERVABLES = (
    (Rail.VAPOUR.value, "Vapour pressure"),
    (Rail.MELT_ACTIVITY.value, "Melt activity"),
    (Rail.REDOX.value, "Fe³⁺/Fe²⁺"),
    (Rail.SIO_EVOLUTION.value, "SiO evolution"),
    (Rail.RESIDUE_COMPOSITION.value, "Residue composition"),
)
COLLECTION_SCORE_RAILS = tuple(rail for rail, _label in COLLECTION_OBSERVABLES)
COLLECTION_HTML_SAMPLE_LIMIT = 100
COLLECTION_EXPORT_DPI = 150
FINGERPRINT_BANDS = (
    ("good", "#1a9641"),
    ("okay", "#f6c90e"),
    ("needs work", "#d7191c"),
)
FINGERPRINT_COLOURS = dict(FINGERPRINT_BANDS)
GOAL_COLOUR = "#0ca30c"


@dataclass(frozen=True)
class _HeroExposurePolicy:
    census_observation_ids: frozenset[str]
    exposed_source_ids: frozenset[str]
    baseline_observation_ids: frozenset[str]
    minimum_held_out_n: int = HERO_MINIMUM_REPORT_N

    def __post_init__(self) -> None:
        if self.minimum_held_out_n < 1:
            raise ValueError("minimum held-out n must be positive")


HERO_MINOR_FACETS = (
    ("minor:P", "P"),
    ("minor:S", "S"),
    ("minor:Cl", "Cl"),
    ("minor:C", "C"),
    ("minor:alkali_salts", "Alkali salts"),
)
DEFAULT_GROUP_BY = ("species", "element")
DEFAULT_ABUNDANCE_FEEDSTOCK = "lunar_mare_low_ti"
Y_LIMITS = (-3.0, 3.0)
TIER_MAJOR = "major"
TIER_MINOR = "minor"
TIER_TRACE = "trace"
FLAGGED_MARKER_LABEL = "flagged / extrapolated"
TIER_STYLES: dict[str, dict[str, Any]] = {
    TIER_MAJOR: {"color": "#111111", "linewidth": 2.0, "markersize": 3.5},
    TIER_MINOR: {"color": "#777777", "linewidth": 1.2, "markersize": 3.0},
    TIER_TRACE: {"color": "#b9b9b9", "linewidth": 0.5, "markersize": 2.5},
}


@dataclass
class EngineSummary:
    scored_rows: int = 0
    unique_points: int = 0
    rms_dex: float | None = None
    species: set[str] = field(default_factory=set)
    min_temperature_K: float | None = None
    max_temperature_K: float | None = None
    refusals: Counter[str] = field(default_factory=Counter)
    refused_by_rail: Counter[str] = field(default_factory=Counter)
    not_observable_by_rail: Counter[str] = field(default_factory=Counter)
    excluded_d139_rows: int = 0
    excluded_d139_by_rail: Counter[str] = field(default_factory=Counter)
    non_dex_rows: int = 0
    missing_identity_rows: int = 0
    source_relations: Counter[str] = field(default_factory=Counter)
    versions: set[str] = field(default_factory=set)
    default_packs: set[str] = field(default_factory=set)

@dataclass(frozen=True)
class BinPoint:
    bin_index: int
    temperature_K: float
    residual_dex: float
    n: int
    flagged: bool = False
    composition_key: str = "unknown"
    composition_label: str = "unknown composition"
    observable: str = "unknown"
    composition_sio2_mole_fraction: float | None = None


@dataclass(frozen=True)
class PanelSeries:
    subject: str
    rail: str
    points: tuple[BinPoint, ...]
    tier: str
    gid: str
    major_elements: tuple[str, ...]
    composition_key: str = "unknown"
    composition_label: str = "unknown composition"
    observable: str = "unknown"


@dataclass(frozen=True)
class ScorePlotData:
    stamp: Mapping[str, Any]
    engines: tuple[str, ...]
    rails_seen: tuple[str, ...]
    selected_rails: tuple[str, ...]
    summaries: Mapping[str, EngineSummary]
    series: Mapping[tuple[str, str, str], tuple[BinPoint, ...]]
    hero_series: Mapping[tuple[str, str, str, str], tuple[BinPoint, ...]]
    hero_refusals: Mapping[tuple[str, str, str, str, str], int]
    hero_refusal_totals: Mapping[tuple[str, str, str], int]
    hero_diagnostics: Mapping[tuple[str, str], Mapping[str, object]]
    hero_comparisons: Mapping[tuple[str, str], Mapping[str, object]]
    hero_sweet_spot_cells: Mapping[
        tuple[str, str], tuple[Mapping[str, object], ...]
    ]
    hero_sio2_unknown_cells: Mapping[
        tuple[str, str], tuple[Mapping[str, object], ...]
    ]
    hero_all_numeric_metrics: Mapping[tuple[str, str], Mapping[str, object]]
    element_series: Mapping[tuple[str, str], tuple[BinPoint, ...]]
    x_limits: tuple[float, float]
    bin_width_K: float
    hero_openimcc_vaporock_species: tuple[Mapping[str, object], ...] = ()
    hero_deck_points: tuple[Mapping[str, object], ...] = ()
    hero_deck_metrics: Mapping[tuple[str, str], Mapping[str, object]] = field(
        default_factory=dict
    )
    hero_deck_comparisons: Mapping[tuple[str, str], Mapping[str, object]] = field(
        default_factory=dict
    )
    hero_deck_unavailable_axis_counts: Mapping[tuple[str, str], int] = field(
        default_factory=dict
    )
    hero_deck_axis_refusals: Mapping[tuple[str, str, str], int] = field(
        default_factory=dict
    )
    hero_deck_axis_refusal_details: Mapping[
        tuple[str, str, str], tuple[Mapping[str, str], ...]
    ] = field(default_factory=dict)
    hero_source_disagreement_reasons: tuple[str, ...] = ()
    hero_component_comparisons: tuple[Mapping[str, object], ...] = ()
    rails_explicit: bool = False


class _SQLiteCompositionSio2(Mapping[str, float]):
    """Look up scorer-computed SiO₂ fractions without retaining every formula."""

    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database

    def __getitem__(self, key: str) -> float:
        row = self.database.execute(
            "SELECT sio2_fraction FROM composition_sio2 WHERE composition_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return float(row[0])

    def __iter__(self) -> Iterator[str]:
        for (key,) in self.database.execute(
            "SELECT composition_key FROM composition_sio2 ORDER BY composition_key"
        ):
            yield str(key)

    def __len__(self) -> int:
        row = self.database.execute("SELECT COUNT(*) FROM composition_sio2").fetchone()
        return 0 if row is None else int(row[0])


def _d139_exclusion_counts(
    data: ScorePlotData, *, rail: str | None = None
) -> tuple[tuple[str, int], ...]:
    counts: list[tuple[str, int]] = []
    for engine in _ordered_hero_engines(data.engines):
        summary = data.summaries[engine]
        count = (
            summary.excluded_d139_rows
            if rail is None
            else summary.excluded_d139_by_rail[rail]
        )
        if count:
            counts.append((engine, count))
    return tuple(counts)


@dataclass(frozen=True)
class ElementAbundance:
    feedstock_id: str
    oxide_wt_pct: Mapping[str, float]
    feedstock_elements: frozenset[str]


def _bin_point_from_score_record(record: Mapping[str, object]) -> BinPoint:
    return BinPoint(
        bin_index=int(record["bin_index"]),
        temperature_K=float(record["temperature_K"]),
        residual_dex=float(record["residual_dex"]),
        n=int(record["n"]),
        flagged=bool(record["flagged"]),
        composition_key=str(record["composition_key"]),
        composition_label=str(record["composition_label"]),
        observable=str(record["observable"]),
        composition_sio2_mole_fraction=_finite_float(
            record.get("composition_sio2_mole_fraction")
        ),
    )


def _bin_points_by_key(
    records: Mapping[tuple[str, ...], Sequence[Mapping[str, object]]],
) -> dict[tuple[str, ...], tuple[BinPoint, ...]]:
    return {
        key: tuple(_bin_point_from_score_record(record) for record in points)
        for key, points in records.items()
    }


def _state_value(value: object) -> object | None:
    if isinstance(value, Mapping):
        if value.get("tag") != "value":
            return None
        return value.get("value")
    return value


@contextmanager
def _open_residual_jsonl(path: Path) -> Iterator[BinaryIO]:
    """Open residuals directly, or stream the residual member from a battery archive."""

    if path.name.endswith(".tgz"):
        with tarfile.open(path, "r:gz") as archive:
            try:
                member = archive.getmember("data/battery/residuals.jsonl")
            except KeyError as exc:
                raise ValueError(f"{path}: missing data/battery/residuals.jsonl") from exc
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{path}: cannot read data/battery/residuals.jsonl")
            try:
                yield source
            finally:
                source.close()
    elif path.suffix == ".gz":
        with gzip.open(path, "rb") as source:
            yield source
    else:
        with path.open("rb") as source:
            yield source


def _identity_from_observation(
    observation: Mapping[str, Any],
) -> Mapping[str, Any]:
    identity = observation.get("identity")
    return identity if isinstance(identity, Mapping) else {}


def _species_formula(identity: Mapping[str, Any]) -> str:
    species_value = identity.get("species")
    species = (
        _state_value(species_value)
        if isinstance(species_value, Mapping) and "tag" in species_value
        else species_value
    )
    if isinstance(species, Mapping):
        return str(species.get("formula") or "")
    return ""


def _composition_fields(identity: Mapping[str, Any]) -> tuple[str, str]:
    composition = _state_value(identity.get("composition"))
    if composition is None:
        return "unknown", "unknown composition"
    key = json.dumps(composition, sort_keys=True, separators=(",", ":"))
    if isinstance(composition, Mapping):
        components = composition.get("components")
        if isinstance(components, Sequence) and not isinstance(components, (str, bytes)):
            labels = [
                f"{item[0]}={item[1]}"
                for item in components
                if isinstance(item, Sequence)
                and not isinstance(item, (str, bytes))
                and len(item) == 2
            ]
            if labels:
                basis = composition.get("amount_basis") or composition.get("basis")
                prefix = f"{basis}: " if basis else ""
                return key, prefix + "; ".join(labels)
    return key, str(composition)


def _observable(identity: Mapping[str, Any]) -> str:
    value = _state_value(identity.get("quantity"))
    return str(value) if value is not None else "unknown"


def _row_identity(row: Mapping[str, Any]) -> Mapping[str, Any]:
    observation = row.get("candidate_observation")
    if isinstance(observation, Mapping):
        identity = observation.get("identity")
        if isinstance(identity, Mapping):
            return identity
    return {}


def _is_d139_uncalibrated_kems_row(row: Mapping[str, Any]) -> bool:
    numeric = row.get("numeric")
    if not isinstance(numeric, Mapping) or numeric.get("decision_band") is not None:
        return False
    if _state_value(_row_identity(row).get("quantity")) != "p_partial":
        return False
    candidate = row.get("candidate_observation")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    notice_sources = (row.get("notices"), candidate.get("notices"))
    return any(
        isinstance(notice, Mapping)
        and notice.get("kind") == NoticeKind.UNVERIFIED_APPARATUS.value
        and isinstance(notice.get("reason"), str)
        and notice["reason"].startswith("calibration_not_grounded:")
        for source in notice_sources
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes))
        for notice in source
    )


def _row_formula(row: Mapping[str, Any]) -> str:
    formula = _species_formula(_row_identity(row))
    if formula:
        return formula
    refusal = row.get("refusal")
    detail = refusal.get("detail") if isinstance(refusal, Mapping) else None
    if isinstance(detail, Mapping) and detail.get("formula"):
        return str(detail["formula"])
    request = row.get("candidate_request")
    if isinstance(request, Mapping):
        species = request.get("species")
        if isinstance(species, Mapping) and species.get("formula"):
            return str(species["formula"])
        if request.get("formula"):
            return str(request["formula"])
    return ""


def _row_observable(row: Mapping[str, Any]) -> str:
    value = _observable(_row_identity(row))
    if value == "unknown":
        request = row.get("candidate_request")
        if isinstance(request, Mapping) and request.get("quantity") is not None:
            value = str(request["quantity"])
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if "fo2" in normalized or "oxygenfugacity" in normalized:
        return "log fO2"
    if normalized == "fe3fe2ratio":
        return "Fe3+/Fe2+ ratio"
    if "fe3" in normalized and ("sumfe" in normalized or "totalfe" in normalized):
        return "Fe3+/sumFe"
    return value


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _decimal_key(value: object) -> str | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return format(parsed.normalize(), "f")


def _engine_name(row: Mapping[str, Any]) -> str | None:
    request = row.get("candidate_request")
    if isinstance(request, Mapping) and request.get("engine"):
        return str(request["engine"])
    observation = row.get("candidate_observation")
    if isinstance(observation, Mapping):
        engine = observation.get("engine")
        if isinstance(engine, Mapping) and engine.get("name"):
            return str(engine["name"])
    return None


def _version_text(raw: object) -> str | None:
    if isinstance(raw, Mapping):
        parsed: object = raw
    elif isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value if len(value) <= 100 else None
    else:
        return None
    if isinstance(parsed, Mapping):
        for key in (
            "version",
            "package_version",
            "installed_version",
        ):
            value = parsed.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        return None
    if isinstance(parsed, (str, int, float)):
        return str(parsed)
    return None


def _openimcc_pack_names(engine_metadata: object) -> set[str]:
    if not isinstance(engine_metadata, Mapping):
        return set()
    sources = engine_metadata.get("coefficient_sources")
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except json.JSONDecodeError:
            return set()
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        return set()
    pack_prefix = "openimcc-pack-version:"
    names: set[str] = set()
    for source in sources:
        if not isinstance(source, str) or not source.startswith(pack_prefix):
            continue
        match = re.search(
            r"\b(imcc[-_]sf\d+[-_]d\d+[-_]v\d+)\b",
            source[len(pack_prefix):],
            flags=re.IGNORECASE,
        )
        if match:
            names.add(match.group(1).casefold().replace("_", "-"))
    return names


def _requested_rails(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return DEFAULT_RAILS
    rails = {
        token.strip()
        for value in values
        for token in value.split(",")
        if token.strip()
    }
    if not rails:
        raise ValueError("at least one non-empty --rail value is required")
    return tuple(sorted(rails))


def _exposure_policy_from_files(
    *,
    census_path: Path | None,
    exposed_sources_path: Path | None,
    baseline_observation_ids_path: Path | None,
) -> _HeroExposurePolicy | None:
    paths = (census_path, exposed_sources_path, baseline_observation_ids_path)
    if all(path is None for path in paths):
        return None
    if any(path is None for path in paths):
        raise ValueError(
            "--exposure-census, --exposed-sources, and "
            "--pre-cutoff-observation-ids must be supplied together"
        )
    assert census_path is not None
    assert exposed_sources_path is not None
    assert baseline_observation_ids_path is not None

    with census_path.open("r", encoding="utf-8", newline="") as source:
        census = csv.DictReader(source)
        if "observation_id" not in (census.fieldnames or ()):
            raise ValueError(f"{census_path}: missing observation_id column")
        census_ids = frozenset(
            row["observation_id"].strip()
            for row in census
            if row.get("observation_id") and row["observation_id"].strip()
        )
    with exposed_sources_path.open("r", encoding="utf-8", newline="") as source:
        exposed_sources = csv.DictReader(source)
        if "source_id" not in (exposed_sources.fieldnames or ()):
            raise ValueError(f"{exposed_sources_path}: missing source_id column")
        source_ids = frozenset(
            row["source_id"].strip()
            for row in exposed_sources
            if row.get("source_id") and row["source_id"].strip()
        )
    baseline_ids = frozenset(
        line.strip()
        for line in baseline_observation_ids_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return _HeroExposurePolicy(
        census_observation_ids=census_ids,
        exposed_source_ids=source_ids,
        baseline_observation_ids=baseline_ids,
    )


def _hero_decimal(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError, ArithmeticError):
        return None
    return value if value.is_finite() else None


def _hero_metric_temperature(row: Mapping[str, object]) -> Decimal | None:
    # Temperature grouping keys on the measured residual field, not the request:
    # candidate_observation.identity.temperature_K.
    candidate = row.get("candidate_observation")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    identity = candidate.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    temperature = _state_value(identity.get("temperature_K"))
    return _hero_decimal(temperature)


def _hero_source_disagreement_notices(
    row: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    notices = row.get("notices")
    if not isinstance(notices, Sequence) or isinstance(notices, (str, bytes)):
        return ()
    return tuple(
        notice
        for notice in notices
        if isinstance(notice, Mapping)
        and notice.get("kind") == NoticeKind.SOURCE_DISAGREEMENT.value
    )


def _hero_source_disagreement_flagged(row: Mapping[str, object]) -> bool:
    return bool(_hero_source_disagreement_notices(row))


def _hero_source_disagreement_reasons(
    row: Mapping[str, object],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                reason
                for notice in _hero_source_disagreement_notices(row)
                if isinstance((reason := notice.get("reason")), str)
                and reason.strip()
            }
        )
    )


def _hero_exposure_status(
    row: Mapping[str, object], policy: _HeroExposurePolicy
) -> str:
    # The scored candidate ID is engine-prefixed; `reference` is the observed
    # row's original, unprefixed observation ID used by the exposure inputs.
    observation_id = str(row.get("reference") or "")
    candidate = row.get("candidate_observation")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    source_id = str(candidate.get("source_id") or row.get("source_id") or "")
    if (
        observation_id in policy.census_observation_ids
        or source_id in policy.exposed_source_ids
    ):
        return "exposed"
    # A missing reference cannot prove that the row landed after the cutoff.
    if not observation_id or observation_id in policy.baseline_observation_ids:
        return "exposed"
    return "held_out"


def _hero_metric_rows_database(
    residuals: Iterable[Mapping[str, object]],
    *,
    exposure_policy: _HeroExposurePolicy | None,
) -> sqlite3.Connection:
    """Adapt payload rows to the scorer-owned SQLite metric input."""

    database = sqlite3.connect(":memory:")
    _battery_hero_create_metric_rows(database)
    pending: list[tuple[object, ...]] = []
    for row in residuals:
        rail = str(row.get("rail") or "")
        engine = _engine_name(row)
        numeric = row.get("numeric")
        if (
            not rail
            or not engine
            or not isinstance(numeric, Mapping)
            or numeric.get("operation") != "dex"
            or numeric.get("value") is None
        ):
            continue
        value = _hero_decimal(numeric["value"])
        if value is None:
            continue
        temperature = _hero_metric_temperature(row)
        pending.append(
            (
                rail,
                engine,
                str(row.get("reference") or ""),
                _row_formula(row),
                str(value),
                None if temperature is None else str(temperature),
                int(_hero_source_disagreement_flagged(row)),
                _hero_exposure_status(row, exposure_policy)
                if exposure_policy is not None
                else "not_used",
            )
        )
        if len(pending) >= 5000:
            database.executemany(
                "INSERT INTO hero_rows "
                "(rail, engine, reference, species, residual_text, temperature_text, "
                "source_flag, exposure_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                pending,
            )
            pending.clear()
    if pending:
        database.executemany(
            "INSERT INTO hero_rows "
            "(rail, engine, reference, species, residual_text, temperature_text, "
            "source_flag, exposure_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            pending,
        )
    return database


def _hero_metric_records(
    residuals: Iterable[Mapping[str, object]],
    *,
    engines: Sequence[str] | None = None,
    rails: Sequence[str] | None = None,
    exposure_policy: _HeroExposurePolicy | None = None,
) -> dict[tuple[str, str], dict[str, object]]:
    database = _hero_metric_rows_database(
        residuals, exposure_policy=exposure_policy
    )
    try:
        selected_engines = tuple(
            sorted(
                set(engines or ())
                | {
                    str(row[0])
                    for row in database.execute(
                        "SELECT DISTINCT engine FROM hero_rows"
                    )
                }
            )
        )
        selected_rails = tuple(
            sorted(
                set(rails or ())
                | {
                    str(row[0])
                    for row in database.execute(
                        "SELECT DISTINCT rail FROM hero_rows"
                    )
                }
            )
        )
        return _battery_hero_metric_records_from_database(
            database,
            engines=selected_engines,
            rails=selected_rails,
            minimum_held_out_n=(
                exposure_policy.minimum_held_out_n
                if exposure_policy is not None
                else None
            ),
        )
    finally:
        database.close()


def _hero_paired_engine_rail_comparisons(
    residuals: Iterable[Mapping[str, object]],
    *,
    primary_engines: Sequence[str],
    comparison_engines: Sequence[str],
    rails: Sequence[str],
    exposure_policy: _HeroExposurePolicy | None = None,
    include_species: bool = False,
) -> list[dict[str, object]]:
    database = _hero_metric_rows_database(
        residuals, exposure_policy=exposure_policy
    )
    try:
        return _battery_hero_pair_comparison_records_from_database(
            database,
            primary_engines=primary_engines,
            comparison_engines=comparison_engines,
            rails=rails,
            minimum_held_out_n=(
                exposure_policy.minimum_held_out_n
                if exposure_policy is not None
                else None
            ),
            include_species=include_species,
            minimum_report_n=HERO_MINIMUM_REPORT_N,
        )
    finally:
        database.close()


def _record_hero_diagnostic(
    diagnostics: dict[tuple[str, str], dict[str, object]],
    row: Mapping[str, Any],
    *,
    rail: str,
    engine: str,
    status: ResidualStatus,
    engine_observable: bool,
) -> None:
    bucket = diagnostics.setdefault(
        (rail, engine),
        {
            "refused_count": 0,
            "not_observable_count": 0,
            "refused_by_reason": Counter(),
            "open_predict_and_flag_notice_counts_by_kind": Counter(),
        },
    )
    if status is ResidualStatus.REFUSED:
        if not engine_observable:
            bucket["not_observable_count"] = int(
                bucket["not_observable_count"]
            ) + 1
            return
        reason = _refusal_reason(row)
        bucket["refused_count"] = int(bucket["refused_count"]) + 1
        refusal_counts = bucket["refused_by_reason"]
        assert isinstance(refusal_counts, Counter)
        refusal_counts[reason] += 1
        return

    numeric = row.get("numeric")
    if (
        not isinstance(numeric, Mapping)
        or numeric.get("operation") != "dex"
        or numeric.get("value") is None
    ):
        return
    notice_counts = bucket["open_predict_and_flag_notice_counts_by_kind"]
    assert isinstance(notice_counts, Counter)
    notices = row.get("notices")
    if isinstance(notices, Sequence) and not isinstance(notices, (str, bytes)):
        for notice in notices:
            if (
                isinstance(notice, Mapping)
                and notice.get("kind")
                and _flagged_payload_strata({"notices": [notice]})
            ):
                notice_counts[str(notice["kind"])] += 1


def _refusal_reason(row: Mapping[str, Any]) -> str:
    refusal = row.get("refusal")
    return (
        str(refusal.get("reason") or "untyped_refusal")
        if isinstance(refusal, Mapping)
        else "untyped_refusal"
    )


def _normalized_hero_diagnostics(
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]],
) -> dict[tuple[str, str], Mapping[str, object]]:
    return {
        key: {
            "refused_count": value.get("refused_count", 0),
            "not_observable_count": value.get("not_observable_count", 0),
            "refused_by_reason": dict(
                sorted(value.get("refused_by_reason", {}).items())
            ),
            "open_predict_and_flag_notice_counts_by_kind": dict(
                sorted(
                    value.get(
                        "open_predict_and_flag_notice_counts_by_kind", {}
                    ).items()
                )
            ),
        }
        for key, value in diagnostics.items()
    }


@lru_cache(maxsize=8)
def load_element_abundance(
    feedstock_id: str = DEFAULT_ABUNDANCE_FEEDSTOCK,
) -> ElementAbundance:
    """Load cation oxide wt% for one feedstock using the simulator's loaders."""

    feedstocks = load_config_bundle(ROOT / "data").feedstocks
    try:
        feedstock = feedstocks[feedstock_id]
    except KeyError as exc:
        raise ValueError(f"unknown abundance feedstock {feedstock_id!r}") from exc
    resolved = resolve_feedstock_composition(feedstock)
    oxide_wt_pct: dict[str, float] = defaultdict(float)
    feedstock_elements: set[str] = set()
    for component, raw_amount in resolved.canonical_wt_pct.items():
        amount = _finite_float(raw_amount)
        elements = set(parse_formula_elements(str(component)) or ())
        feedstock_elements.update(elements)
        cations = elements - {"O"}
        # The feedstock entries are already oxide wt%; only a binary oxide has
        # one unambiguous cation oxide basis. Oxygen is major by definition.
        if (
            amount is not None
            and amount > 0.0
            and "O" in elements
            and len(cations) == 1
        ):
            oxide_wt_pct[next(iter(cations))] += amount
    return ElementAbundance(
        feedstock_id=feedstock_id,
        oxide_wt_pct=dict(oxide_wt_pct),
        feedstock_elements=frozenset(feedstock_elements),
    )


def element_tier(element: str, abundance: ElementAbundance) -> str:
    # Oxygen is major by definition, independent of the feedstock's oxide totals.
    if element == "O":
        return TIER_MAJOR
    oxide_wt_pct = abundance.oxide_wt_pct.get(element, 0.0)
    if oxide_wt_pct >= 1.0:
        return TIER_MAJOR
    if oxide_wt_pct >= 0.1:
        return TIER_MINOR
    return TIER_TRACE


def abundance_class_table(
    data: ScorePlotData,
    abundance: ElementAbundance,
) -> list[dict[str, object]]:
    elements = set(abundance.feedstock_elements) | {"O"}
    for _engine, formula, _rail in data.series:
        elements.update(parse_formula_elements(formula) or ())
    rows: list[dict[str, object]] = []
    for element in sorted(elements):
        oxide_wt_pct = abundance.oxide_wt_pct.get(element)
        if element == "O":
            basis = "major by definition"
        elif oxide_wt_pct is not None:
            basis = "simple oxide wt%"
        elif element in abundance.feedstock_elements:
            basis = "no binary oxide wt% declared"
        else:
            basis = "absent from feedstock"
        rows.append(
            {
                "element": element,
                "oxide_wt_pct": oxide_wt_pct,
                "class": element_tier(element, abundance),
                "basis": basis,
            }
        )
    return rows


def read_scores(
    path: Path,
    *,
    bin_width_K: float = DEFAULT_BIN_WIDTH_K,
    rails: Sequence[str] | None = None,
    scratch_dir: Path | None = None,
    exposure_policy: _HeroExposurePolicy | None = None,
    _collection_mode: bool = False,
) -> ScorePlotData:
    """Stream the score JSONL and return binned, duplicate-collapsed data."""

    if not math.isfinite(bin_width_K) or bin_width_K <= 0:
        raise ValueError("bin width must be a finite positive number of kelvin")
    selected_rails = _requested_rails(rails)
    selected = set(selected_rails)
    retained_rails = selected | HERO_AND_DECK_SOURCE_RAILS
    summaries: dict[str, EngineSummary] = defaultdict(EngineSummary)
    rails_seen: set[str] = set()
    source_disagreement_reasons: set[str] = set()
    hero_refusals: Counter[tuple[str, str, str, str, str]] = Counter()
    hero_refusal_totals: Counter[tuple[str, str, str]] = Counter()
    hero_diagnostics: dict[tuple[str, str], dict[str, object]] = {}
    stamp: Mapping[str, Any] | None = None

    if scratch_dir is None:
        configured_tmp = os.environ.get("TMPDIR")
        scratch_dir = Path(configured_tmp) if configured_tmp else None
    if scratch_dir is not None:
        scratch_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="battery-hero-", dir=scratch_dir) as tmp:
        database = sqlite3.connect(Path(tmp) / "points.sqlite3")
        database.execute("PRAGMA journal_mode = OFF")
        database.execute("PRAGMA synchronous = OFF")
        database.execute("PRAGMA temp_store = FILE")
        database.execute("PRAGMA cache_size = -32768")
        database.execute(
            """CREATE TABLE scored (
                engine TEXT NOT NULL,
                species TEXT NOT NULL,
                rail TEXT NOT NULL,
                experiment TEXT NOT NULL,
                temperature_key TEXT NOT NULL,
                temperature_K REAL NOT NULL,
                flagged INTEGER NOT NULL,
                residual_dex REAL NOT NULL,
                composition_key TEXT NOT NULL,
                composition_label TEXT NOT NULL,
                observable TEXT NOT NULL
            )"""
        )
        database.execute(
            "CREATE TABLE composition_sio2 "
            "(composition_key TEXT PRIMARY KEY, sio2_fraction REAL NOT NULL)"
        )
        _battery_hero_create_metric_rows(database)
        database.execute(
            "CREATE TABLE hero_deck_payloads "
            "(id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        _battery_hero_create_fingerprint_values(database)

        pending: list[
            tuple[str, str, str, str, str, float, int, float, str, str, str]
        ] = []
        hero_rows_pending: list[tuple[object, ...]] = []
        deck_payloads_pending: list[tuple[str]] = []
        fingerprint_pending: list[tuple[object, ...]] = []
        unknown_fingerprint_pending: list[tuple[object, ...]] = []
        composition_pending: dict[str, float] = {}
        with _open_residual_jsonl(path) as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
                if not isinstance(row, Mapping):
                    continue
                if stamp is None:
                    if row.get("kind") != "battery_store_stamp":
                        raise ValueError(f"{path}: line 1 is not a battery_store_stamp")
                    stamp = row
                    continue

                engine = _engine_name(row)
                if not engine:
                    continue
                summary = summaries[engine]
                observation = row.get("candidate_observation")
                candidate_observation = observation if isinstance(observation, Mapping) else {}
                recorded_engine = candidate_observation.get("engine")
                if isinstance(recorded_engine, Mapping):
                    version = _version_text(recorded_engine.get("version"))
                    if version:
                        summary.versions.add(version)
                    if engine == "openimcc":
                        summary.default_packs.update(
                            _openimcc_pack_names(recorded_engine)
                        )

                rail_value = row.get("rail")
                rail = str(rail_value) if rail_value is not None else ""
                if rail:
                    rails_seen.add(rail)

                status = residual_status_token(row.get("status"))
                engine_observable = (
                    status is not ResidualStatus.REFUSED
                    or _residual_engine_observable(row)
                )
                if rail in HERO_AND_DECK_SOURCE_RAILS:
                    _record_hero_diagnostic(
                        hero_diagnostics,
                        row,
                        rail=rail,
                        engine=engine,
                        status=status,
                        engine_observable=engine_observable,
                    )
                if status is ResidualStatus.REFUSED:
                    reason = _refusal_reason(row)
                    if engine_observable:
                        summary.refusals[reason] += 1
                        summary.refused_by_rail[rail] += 1
                    else:
                        summary.not_observable_by_rail[rail] += 1
                    if rail in HERO_AND_DECK_SOURCE_RAILS and engine_observable:
                        formula = _row_formula(row)
                        identity = _row_identity(row)
                        composition_key, _composition_label = _composition_fields(identity)
                        observable = _row_observable(row)
                        facets = [_battery_hero_facet(rail, formula, observable)]
                        if rail == Rail.VAPOUR.value:
                            facets.extend(_battery_hero_minor_foulant_facets(formula))
                        for facet in facets:
                            hero_refusals[
                                (rail, facet, composition_key, engine, reason)
                            ] += 1
                        hero_refusal_totals[(rail, engine, reason)] += 1
                    continue

                numeric = row.get("numeric")
                if not isinstance(numeric, Mapping) or numeric.get("value") is None:
                    continue
                selected_rail = rail in selected
                if rail not in retained_rails:
                    continue
                if numeric.get("operation") != "dex":
                    if selected_rail:
                        summary.non_dex_rows += 1
                    continue
                if rail in HERO_AND_DECK_SOURCE_RAILS:
                    if rail == Rail.VAPOUR.value:
                        source_disagreement_reasons.update(
                            _hero_source_disagreement_reasons(row)
                        )
                    metric_value = _hero_decimal(numeric.get("value"))
                    if metric_value is not None:
                        metric_identity = _row_identity(row)
                        raw_species = (
                            metric_identity.get("species")
                            if isinstance(metric_identity, Mapping)
                            else None
                        )
                        species_value = _state_value(raw_species)
                        if species_value is None and isinstance(raw_species, Mapping):
                            species_value = raw_species
                        species = (
                            str(species_value.get("formula") or "unknown")
                            if isinstance(species_value, Mapping)
                            else "unknown"
                        )
                        metric_temperature = _hero_metric_temperature(row)
                        metric_reference = str(row.get("reference") or "")
                        metric_exposure = (
                            _hero_exposure_status(row, exposure_policy)
                            if exposure_policy is not None
                            else "not_used"
                        )
                        hero_rows_pending.append(
                            (
                                rail,
                                engine,
                                metric_reference,
                                species,
                                str(metric_value),
                                None if metric_temperature is None else str(metric_temperature),
                                int(_hero_source_disagreement_flagged(row)),
                                metric_exposure,
                            )
                        )
                        if len(hero_rows_pending) >= 5000:
                            database.executemany(
                                "INSERT INTO hero_rows "
                                "(rail, engine, reference, species, residual_text, temperature_text, "
                                "source_flag, exposure_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                hero_rows_pending,
                            )
                            hero_rows_pending.clear()
                if rail in _BATTERY_HERO_DECK_SOURCE_RAILS and not _collection_mode:
                    deck_payload = _battery_hero_deck_payload(row)
                    deck_candidate = deck_payload.get("candidate_observation")
                    deck_candidate = (
                        deck_candidate if isinstance(deck_candidate, dict) else {}
                    )
                    source_id = candidate_observation.get("source_id")
                    if source_id:
                        deck_candidate["source_id"] = source_id
                    deck_payloads_pending.append(
                        (
                            json.dumps(
                                deck_payload,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                    )
                    if len(deck_payloads_pending) >= 5000:
                        database.executemany(
                            "INSERT INTO hero_deck_payloads (payload_json) VALUES (?)",
                            deck_payloads_pending,
                        )
                        deck_payloads_pending.clear()

                identity = _row_identity(row)
                if not identity:
                    if selected_rail:
                        summary.missing_identity_rows += 1
                    continue
                if _is_d139_uncalibrated_kems_row(row):
                    # Use the scorer's d-139 output marker; do not infer calibration here.
                    summary.excluded_d139_rows += 1
                    summary.excluded_d139_by_rail[rail] += 1
                    continue
                formula = _species_formula(identity)
                temperature_raw = _state_value(identity.get("temperature_K"))
                temperature = _finite_float(temperature_raw)
                temperature_key = _decimal_key(temperature_raw)
                residual = _finite_float(numeric.get("value"))
                if not formula or temperature is None or temperature_key is None or residual is None:
                    if rail in selected:
                        summary.missing_identity_rows += 1
                    continue

                experiment = candidate_observation.get("experiment_id")
                request = row.get("candidate_request")
                if not experiment and isinstance(request, Mapping):
                    experiment = request.get("experiment_id")
                if not experiment:
                    experiment = row.get("reference") or row.get("key") or f"line:{line_number}"

                flagged = bool(_flagged_payload_strata(row))
                source_relation = str(row.get("source_relation") or "unknown")
                if rail in selected:
                    summary.source_relations[source_relation] += 1
                    summary.scored_rows += 1
                    summary.species.add(formula)
                    summary.min_temperature_K = (
                        temperature
                        if summary.min_temperature_K is None
                        else min(summary.min_temperature_K, temperature)
                    )
                    summary.max_temperature_K = (
                        temperature
                        if summary.max_temperature_K is None
                        else max(summary.max_temperature_K, temperature)
                    )
                composition_key, composition_label = _composition_fields(identity)
                if rail in HERO_AND_DECK_SOURCE_RAILS:
                    composition_raw = _state_value(identity.get("composition"))
                    sio2_fraction = _battery_hero_sio2_mole_fraction(composition_raw)
                    if sio2_fraction is not None:
                        composition_pending[composition_key] = float(sio2_fraction)
                        if len(composition_pending) >= 5000:
                            database.executemany(
                                "INSERT OR REPLACE INTO composition_sio2 "
                                "(composition_key, sio2_fraction) VALUES (?, ?)",
                                composition_pending.items(),
                            )
                            composition_pending.clear()
                        bins = _battery_hero_sweet_spot_bin_indices(
                            temperature_raw, sio2_fraction
                        )
                        fingerprint_value = _hero_decimal(numeric.get("value"))
                        if bins is not None and fingerprint_value is not None:
                            fingerprint_pending.append(
                                (
                                    rail,
                                    engine,
                                    bins[0],
                                    bins[1],
                                    str(fingerprint_value),
                                    str(abs(fingerprint_value)),
                                )
                            )
                            if len(fingerprint_pending) >= 5000:
                                database.executemany(
                                    "INSERT INTO battery_hero_fingerprint_values "
                                    "(rail, engine, temperature_bin, sio2_bin, residual_text, "
                                    "absolute_text) VALUES (?, ?, ?, ?, ?, ?)",
                                    fingerprint_pending,
                                )
                                fingerprint_pending.clear()
                    else:
                        temperature_bin = _battery_hero_temperature_bin_index(
                            temperature_raw
                        )
                        fingerprint_value = _hero_decimal(numeric.get("value"))
                        if temperature_bin is not None and fingerprint_value is not None:
                            unknown_fingerprint_pending.append(
                                (rail, engine, temperature_bin, str(fingerprint_value))
                            )
                            if len(unknown_fingerprint_pending) >= 5000:
                                database.executemany(
                                    "INSERT INTO battery_hero_unknown_sio2_values "
                                    "(rail, engine, temperature_bin, residual_text) "
                                    "VALUES (?, ?, ?, ?)",
                                    unknown_fingerprint_pending,
                                )
                                unknown_fingerprint_pending.clear()
                observable = _row_observable(row)
                pending.append(
                    (
                        engine,
                        formula,
                        rail,
                        str(experiment),
                        temperature_key,
                        temperature,
                        int(flagged),
                        residual,
                        composition_key,
                        composition_label,
                        observable,
                    )
                )
                if len(pending) >= 5000:
                    database.executemany(
                        "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        pending,
                    )
                    pending.clear()

        if pending:
            database.executemany(
                "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                pending,
            )
        if hero_rows_pending:
            database.executemany(
                "INSERT INTO hero_rows "
                "(rail, engine, reference, species, residual_text, temperature_text, "
                "source_flag, exposure_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                hero_rows_pending,
            )
        if deck_payloads_pending:
            database.executemany(
                "INSERT INTO hero_deck_payloads (payload_json) VALUES (?)",
                deck_payloads_pending,
            )
        if fingerprint_pending:
            database.executemany(
                "INSERT INTO battery_hero_fingerprint_values "
                "(rail, engine, temperature_bin, sio2_bin, residual_text, absolute_text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                fingerprint_pending,
            )
        if unknown_fingerprint_pending:
            database.executemany(
                "INSERT INTO battery_hero_unknown_sio2_values "
                "(rail, engine, temperature_bin, residual_text) VALUES (?, ?, ?, ?)",
                unknown_fingerprint_pending,
            )
        if composition_pending:
            database.executemany(
                "INSERT OR REPLACE INTO composition_sio2 "
                "(composition_key, sio2_fraction) VALUES (?, ?)",
                composition_pending.items(),
            )
        database.execute(
            "CREATE INDEX hero_pair_lookup ON hero_rows(rail, reference, species, engine, id)"
        )
        database.commit()
        if stamp is None:
            raise ValueError(f"{path}: empty score file")
        binned_records = _battery_hero_binned_score_records(
            database,
            selected_rails=selected_rails,
            hero_rails=HERO_RAILS,
            bin_width_K=bin_width_K,
            composition_sio2_by_key=_SQLiteCompositionSio2(database),
        )
        summary_records = binned_records["summaries"]
        for engine, record in summary_records.items():
            summaries[engine].unique_points = int(record["unique_points"])
            summaries[engine].rms_dex = float(record["rms_dex"])
        series = _bin_points_by_key(binned_records["series"])
        hero_series = _bin_points_by_key(binned_records["hero_series"])
        element_series = _bin_points_by_key(binned_records["element_series"])
        engines = tuple(
            sorted(summaries, key=lambda name: (name != "openimcc", name.casefold()))
        )
        hero_all_numeric_metrics = _battery_hero_metric_records_from_database(
            database,
            engines=engines,
            rails=COLLECTION_SCORE_RAILS,
            minimum_held_out_n=(
                exposure_policy.minimum_held_out_n
                if exposure_policy is not None
                else None
            ),
        )
        hero_comparisons = {
            (str(record["rail"]), str(record["primary_engine"])): record
            for record in _battery_hero_pair_comparison_records_from_database(
                database,
                primary_engines=HERO_PRIMARY_ENGINES,
                comparison_engines=HERO_COMPARISON_ENGINES,
                rails=COLLECTION_SCORE_RAILS,
                minimum_held_out_n=(
                    exposure_policy.minimum_held_out_n
                    if exposure_policy is not None
                    else None
                ),
            )
        }
        hero_openimcc_vaporock_species = tuple(
            _battery_hero_pair_comparison_records_from_database(
                database,
                primary_engines=("openimcc",),
                comparison_engines=("vaporock",),
                rails=(Rail.VAPOUR.value,),
                minimum_held_out_n=(
                    exposure_policy.minimum_held_out_n
                    if exposure_policy is not None
                    else None
                ),
                include_species=True,
                minimum_report_n=HERO_MINIMUM_REPORT_N,
            )
        )
        hero_component_comparisons = tuple(
            _battery_hero_pair_comparison_records_from_database(
                database,
                primary_engines=("openimcc",),
                comparison_engines=tuple(
                    engine for engine in engines if engine != "openimcc"
                ),
                rails=(Rail.VAPOUR.value, Rail.MELT_ACTIVITY.value),
                minimum_held_out_n=(
                    exposure_policy.minimum_held_out_n
                    if exposure_policy is not None
                    else None
                ),
                include_species=True,
                minimum_report_n=HERO_MINIMUM_REPORT_N,
            )
        )
        hero_sweet_spot_cells = _battery_hero_sweet_spot_records_from_database(
            database
        )
        hero_sio2_unknown_cells = _battery_hero_unknown_sio2_records_from_database(
            database
        )

        if _collection_mode:
            deck_records = {
                "points": (),
                "metrics": {},
                "comparisons": {},
                "unavailable_axis_counts": {},
                "axis_refusals": {},
                "axis_refusal_details": {},
            }
            hero_deck_metrics: dict[tuple[str, str], Mapping[str, object]] = {}
            hero_deck_comparisons: dict[
                tuple[str, str], Mapping[str, object]
            ] = {}
        else:
            def deck_payload_rows() -> Iterator[Mapping[str, object]]:
                for (payload_json,) in database.execute(
                    "SELECT payload_json FROM hero_deck_payloads ORDER BY id"
                ):
                    payload = json.loads(payload_json)
                    if isinstance(payload, Mapping):
                        yield payload

            deck_observations = _battery_hero_deck_store_observations(
                deck_payload_rows()
            )
            deck_records = _battery_hero_deck_records(
                deck_payload_rows(),
                primary_engines=HERO_PRIMARY_ENGINES,
                comparison_engines=HERO_COMPARISON_ENGINES,
                observations=deck_observations,
            )
            hero_deck_metrics = {
                (str(chart), str(engine)): {
                    **dict(metric),
                    "views": {"all": dict(metric)},
                }
                for (chart, engine), metric in deck_records["metrics"].items()
            }
            hero_deck_comparisons = dict(deck_records["comparisons"])

        database.close()

    if stamp is None:
        raise ValueError(f"{path}: missing battery store stamp")
    all_temperatures = [
        summary.min_temperature_K
        for summary in summaries.values()
        if summary.min_temperature_K is not None
    ] + [
        summary.max_temperature_K
        for summary in summaries.values()
        if summary.max_temperature_K is not None
    ]
    if all_temperatures:
        low, high = min(all_temperatures), max(all_temperatures)
        padding = max(25.0, (high - low) * 0.02)
        x_limits = (low - padding, high + padding)
    else:
        x_limits = (0.0, bin_width_K)

    hero_sweet_spot_groups: dict[
        tuple[str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    for cell in hero_sweet_spot_cells:
        hero_sweet_spot_groups[(str(cell["rail"]), str(cell["engine"]))].append(
            cell
        )
    hero_sio2_unknown_groups: dict[
        tuple[str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    for cell in hero_sio2_unknown_cells:
        hero_sio2_unknown_groups[(str(cell["rail"]), str(cell["engine"]))].append(
            cell
        )

    return ScorePlotData(
        stamp=stamp,
        engines=engines,
        rails_seen=tuple(sorted(rails_seen)),
        selected_rails=selected_rails,
        summaries=dict(summaries),
        series=series,
        hero_series=hero_series,
        hero_refusals=dict(hero_refusals),
        hero_refusal_totals=dict(hero_refusal_totals),
        hero_diagnostics=_normalized_hero_diagnostics(hero_diagnostics),
        hero_comparisons=hero_comparisons,
        hero_sweet_spot_cells={
            key: tuple(cells)
            for key, cells in sorted(hero_sweet_spot_groups.items())
        },
        hero_sio2_unknown_cells={
            key: tuple(cells)
            for key, cells in sorted(hero_sio2_unknown_groups.items())
        },
        hero_all_numeric_metrics=hero_all_numeric_metrics,
        hero_openimcc_vaporock_species=hero_openimcc_vaporock_species,
        element_series=element_series,
        x_limits=x_limits,
        bin_width_K=bin_width_K,
        hero_deck_points=tuple(deck_records["points"]),
        hero_deck_metrics=hero_deck_metrics,
        hero_deck_comparisons=hero_deck_comparisons,
        hero_deck_unavailable_axis_counts=deck_records[
            "unavailable_axis_counts"
        ],
        hero_deck_axis_refusals=deck_records["axis_refusals"],
        hero_deck_axis_refusal_details=deck_records["axis_refusal_details"],
        hero_source_disagreement_reasons=tuple(
            sorted(source_disagreement_reasons)
        ),
        hero_component_comparisons=hero_component_comparisons,
        rails_explicit=rails is not None,
    )


def _archive_json_member(path: Path, member_name: str) -> Mapping[str, Any]:
    with tarfile.open(path, "r:gz") as archive:
        member = archive.getmember(member_name)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"{path}: cannot read {member_name}")
        with source:
            value = json.load(source)
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}: {member_name} is not a JSON object")
    return value


def _history_fields_from_scores(
    path: Path,
    *,
    exposure_policy: _HeroExposurePolicy | None = None,
) -> tuple[
    dict[tuple[str, str], Mapping[str, object]],
    dict[tuple[str, str], Mapping[str, object]],
    list[dict[str, object]],
    dict[tuple[str, str], Mapping[str, object]],
    tuple[Mapping[str, object], ...],
]:
    plotted = read_scores(path, exposure_policy=exposure_policy, _collection_mode=True)
    metric_records = {
        **plotted.hero_all_numeric_metrics,
        **plotted.hero_deck_metrics,
    }
    return (
        dict(plotted.hero_comparisons),
        plotted.hero_diagnostics,
        _refusal_coverage_from_summaries(plotted.summaries),
        metric_records,
        plotted.hero_openimcc_vaporock_species,
    )


def _refusal_coverage_from_summaries(
    summaries: Mapping[str, EngineSummary],
) -> list[dict[str, object]]:
    rails = {rail.value for rail in Rail}
    for summary in summaries.values():
        rails.update(summary.refused_by_rail)
        rails.update(summary.not_observable_by_rail)
    return [
        {
            "rail": rail,
            "engine": engine,
            "refused_count": summaries[engine].refused_by_rail.get(rail, 0),
            "not_observable_count": summaries[engine].not_observable_by_rail.get(
                rail, 0
            ),
        }
        for rail in sorted(rails)
        for engine in sorted(summaries)
    ]


def _git_commit_date(commit: str) -> str:
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _history_record(
    summary: Mapping[str, Any],
    *,
    landing_sha: str,
    comparisons: Mapping[tuple[str, str], Mapping[str, object]],
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]],
    refusal_coverage: Sequence[Mapping[str, object]] = (),
    commit_date: str | None = None,
    hero_metrics: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    openimcc_score_commit: str | None = None,
    openimcc_vaporock_species: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    records = summary.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("score-summary.json has no records list")
    # Hero chart rails always take their all-numeric summary. A deck rail is
    # written only when this caller already diagnosed it, and then the summary
    # n is the count. Dropping those rows is what zeroed SiO_evolution.
    diagnostic_keys = {(str(rail), str(engine)) for rail, engine in diagnostics}
    metrics_by_key = {
        (str(record.get("rail")), str(record.get("engine"))): {
            "rail": record.get("rail"),
            "engine": record.get("engine"),
            "n": record.get("n"),
            "rms_dex": record.get("rms_dex"),
            "median_abs_dex": record.get("median_abs_dex"),
        }
        for record in records
        if isinstance(record, Mapping)
        and record.get("tier") == "all_numeric"
        and (
            record.get("rail") in HERO_RAILS
            or (str(record.get("rail")), str(record.get("engine")))
            in diagnostic_keys
            or (str(record.get("rail")), str(record.get("engine")))
            in (hero_metrics or {})
        )
    }
    for key, hero_metric in (hero_metrics or {}).items():
        metrics_by_key[key] = {
            **metrics_by_key.get(key, {}),
            **dict(hero_metric),
        }
    for rail, engine in set(metrics_by_key) | set(diagnostics):
        metric = metrics_by_key.setdefault(
            (rail, engine),
            {
                "rail": rail,
                "engine": engine,
                "n": 0,
                "rms_dex": None,
                "median_abs_dex": None,
            },
        )
        diagnostic = diagnostics.get((rail, engine), {})
        metric.update(
            {
                "numeric_count": metric.get("n") or 0,
                "refused_count": diagnostic.get("refused_count", 0),
                "not_observable_count": diagnostic.get(
                    "not_observable_count", 0
                ),
                "refused_by_reason": diagnostic.get("refused_by_reason", {}),
                "open_predict_and_flag_notice_counts_by_kind": diagnostic.get(
                    "open_predict_and_flag_notice_counts_by_kind", {}
                ),
            }
        )
    metrics = [
        metrics_by_key[key]
        for key in sorted(metrics_by_key)
    ]
    store = summary.get("store")
    store = store if isinstance(store, Mapping) else {}
    return {
        "kind": "battery_hero_history",
        "schema_version": 2,
        "store_sha": landing_sha,
        "store_revision": store.get("revision"),
        "commit_date": commit_date or _git_commit_date(landing_sha),
        "openimcc_score_commit": openimcc_score_commit,
        "openimcc_vaporock_shared_by_species": [
            dict(record) for record in openimcc_vaporock_species
        ],
        "metrics": metrics,
        "comparisons": [comparisons[key] for key in sorted(comparisons)],
        "refusal_coverage": [dict(item) for item in refusal_coverage],
    }


def history_record_from_archive(
    archive_path: Path,
    *,
    landing_sha: str,
    comparisons: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    refusal_coverage: Sequence[Mapping[str, object]] | None = None,
    exposure_policy: _HeroExposurePolicy | None = None,
    openimcc_score_commit: str | None = None,
) -> dict[str, Any]:
    summary = _archive_json_member(archive_path, "data/battery/score-summary.json")
    if comparisons is None or diagnostics is None or refusal_coverage is None:
        (
            streamed_comparisons,
            streamed_diagnostics,
            streamed_coverage,
            streamed_metrics,
            streamed_species,
        ) = _history_fields_from_scores(
            archive_path,
            exposure_policy=exposure_policy,
        )
        if comparisons is None:
            comparisons = streamed_comparisons
        if diagnostics is None:
            diagnostics = streamed_diagnostics
        if refusal_coverage is None:
            refusal_coverage = streamed_coverage
    else:
        streamed_metrics = {}
        streamed_species = ()
    if not streamed_metrics:
        plotted = read_scores(
            archive_path, exposure_policy=exposure_policy, _collection_mode=True
        )
        streamed_metrics = {
            **plotted.hero_all_numeric_metrics,
            **plotted.hero_deck_metrics,
        }
        streamed_species = plotted.hero_openimcc_vaporock_species
    return _history_record(
        summary,
        landing_sha=landing_sha,
        comparisons=comparisons,
        diagnostics=diagnostics,
        refusal_coverage=refusal_coverage,
        hero_metrics=streamed_metrics,
        openimcc_score_commit=openimcc_score_commit,
        openimcc_vaporock_species=streamed_species,
    )


def append_history(path: Path, record: Mapping[str, Any]) -> bool:
    """Append a store row once, preserving every byte already in the JSONL."""

    store_sha = str(record.get("store_sha") or "")
    if not store_sha:
        raise ValueError("history row needs store_sha")
    if path.exists():
        identity = (
            store_sha,
            str(record.get("openimcc_score_commit") or ""),
        )
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    previous = json.loads(line)
                    previous_identity = (
                        str(previous.get("store_sha") or ""),
                        str(previous.get("openimcc_score_commit") or ""),
                    )
                    if previous_identity == identity:
                        return False
        if path.stat().st_size:
            with path.open("rb") as source:
                source.seek(-1, os.SEEK_END)
                if source.read(1) != b"\n":
                    raise ValueError(f"{path}: existing JSONL must end with a newline")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
        target.write("\n")
    return True


def append_archive_history(
    history_path: Path,
    archive_path: Path,
    *,
    landing_sha: str,
    openimcc_score_commit: str | None = None,
    exposure_policy: _HeroExposurePolicy | None = None,
) -> bool:
    return append_history(
        history_path,
        history_record_from_archive(
            archive_path,
            landing_sha=landing_sha,
            openimcc_score_commit=openimcc_score_commit,
            exposure_policy=exposure_policy,
        ),
    )


def read_history(path: Path | None) -> tuple[Mapping[str, Any], ...]:
    if path is None or not path.is_file():
        return ()
    rows: list[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"{path}:{line_number}: history row is not an object")
            rows.append(row)
    return tuple(rows)


def species_tier(formula: str, abundance: ElementAbundance) -> str:
    """Classify by the least abundant element carried by the formula."""

    elements = parse_formula_elements(formula) or ()
    if not elements:
        return TIER_TRACE
    return lowest_tier(element_tier(element, abundance) for element in elements)


def style_for_tier(tier: str) -> dict[str, Any]:
    return dict(TIER_STYLES.get(tier, TIER_STYLES[TIER_TRACE]))


def _legend_handles() -> list[Line2D]:
    handles = [
        Line2D([0], [0], color=style["color"], linewidth=style["linewidth"], label=tier)
        for tier, style in TIER_STYLES.items()
    ]
    handles.append(
        Line2D([0], [0], color="#333333", linewidth=1.0, linestyle="-", label="vapour / SiO pressure")
    )
    handles.append(
        Line2D([0], [0], color="#333333", linewidth=1.0, linestyle="--", label="melt activity")
    )
    handles.append(
        Line2D(
            [0],
            [0],
            color="#333333",
            linewidth=1.0,
            marker="o",
            markerfacecolor="none",
            label=FLAGGED_MARKER_LABEL,
        )
    )
    return handles


def _plot_y(value: float) -> float:
    return min(Y_LIMITS[1], max(Y_LIMITS[0], value))


def element_view_series(
    data: ScorePlotData,
) -> dict[tuple[str, str], tuple[BinPoint, ...]]:
    """Return the scorer-owned pooled element rows for drawing."""

    return dict(data.element_series)


def _major_elements(formula: str, abundance: ElementAbundance) -> tuple[str, ...]:
    return tuple(
        element
        for element in parse_formula_elements(formula) or ()
        if element_tier(element, abundance) == TIER_MAJOR
    )


def _composition_slug(composition_key: str) -> str:
    return hashlib.sha1(composition_key.encode("utf-8")).hexdigest()[:10]


def _composition_groups(
    points: Sequence[BinPoint],
) -> tuple[tuple[str, str, str, tuple[BinPoint, ...]], ...]:
    groups: dict[tuple[str, str, str], list[BinPoint]] = defaultdict(list)
    for point in points:
        groups[(point.composition_key, point.composition_label, point.observable)].append(
            point
        )
    return tuple(
        (
            composition_key,
            composition_label,
            observable,
            tuple(sorted(group, key=lambda point: point.bin_index)),
        )
        for (composition_key, composition_label, observable), group in sorted(
            groups.items()
        )
    )


def _panel_series(
    data: ScorePlotData,
    engine: str,
    view: str,
    abundance: ElementAbundance,
) -> list[PanelSeries]:
    items: list[PanelSeries] = []
    if view == "species":
        for key, points in data.series.items():
            if key[0] != engine:
                continue
            _engine, formula, rail = key
            for (
                composition_key,
                composition_label,
                observable,
                composition_points,
            ) in _composition_groups(points):
                gid = f"series:{engine}:{formula}:{rail}"
                if composition_key != "unknown":
                    gid += f":composition:{_composition_slug(composition_key)}"
                items.append(
                    PanelSeries(
                        subject=formula,
                        rail=rail,
                        points=tuple(
                            sorted(composition_points, key=lambda point: point.bin_index)
                        ),
                        tier=species_tier(formula, abundance),
                        gid=gid,
                        major_elements=_major_elements(formula, abundance),
                        composition_key=composition_key,
                        composition_label=composition_label,
                        observable=observable,
                    )
                )
    elif view == "element":
        for (series_engine, element), points in element_view_series(data).items():
            if series_engine != engine:
                continue
            for (
                composition_key,
                composition_label,
                observable,
                composition_points,
            ) in _composition_groups(points):
                gid = f"series:{engine}:{element}:pooled:element"
                if composition_key != "unknown":
                    gid += f":composition:{_composition_slug(composition_key)}"
                items.append(
                    PanelSeries(
                        subject=element,
                        rail="pooled",
                        points=composition_points,
                        tier=element_tier(element, abundance),
                        gid=gid,
                        major_elements=(
                            (element,)
                            if element_tier(element, abundance) == TIER_MAJOR
                            else ()
                        ),
                        composition_key=composition_key,
                        composition_label=composition_label,
                        observable=observable,
                    )
                )
    else:
        raise ValueError(f"unknown plot view {view!r}")
    return sorted(items, key=lambda item: (item.subject, item.rail, item.gid))


def _spaced_axis_labels(candidates: Mapping[str, tuple[float, float]]) -> tuple[list[float], list[str]]:
    ordered = sorted(candidates.items(), key=lambda item: item[1][1])
    if not ordered:
        return [], []
    low, high = Y_LIMITS[0] + 0.12, Y_LIMITS[1] - 0.12
    gap = min(0.36, (high - low) / max(1, len(ordered) - 1))
    positions: list[float] = []
    for _element, (_temperature, residual) in ordered:
        requested = min(high, max(low, _plot_y(residual)))
        positions.append(requested if not positions else max(requested, positions[-1] + gap))
    overflow = positions[-1] - high
    if overflow > 0:
        positions = [position - overflow for position in positions]
    underflow = low - positions[0]
    if underflow > 0:
        positions = [position + underflow for position in positions]
    return positions, [element for element, _candidate in ordered]


def draw_panel(
    axis: Axes,
    data: ScorePlotData,
    engine: str,
    abundance: ElementAbundance,
    *,
    view: str = "species",
) -> Axes:
    summary = data.summaries[engine]
    items = _panel_series(data, engine, view, abundance)
    label_candidates: dict[str, tuple[float, float]] = {}
    species_label_candidates: dict[str, tuple[float, float]] = {}
    species_label_items: dict[str, PanelSeries] = {}
    major_formula_counts = Counter(
        item.subject for item in items if item.tier == TIER_MAJOR
    )
    composition_markers = {
        composition: _HERO_MARKERS[index % len(_HERO_MARKERS)]
        for index, composition in enumerate(
            sorted({item.composition_key for item in items})
        )
    }

    for item in items:
        style = style_for_tier(item.tier)
        marker_style = composition_markers[item.composition_key]
        base_linestyle = "--" if item.rail == Rail.MELT_ACTIVITY.value else "-"
        for previous, point in zip(item.points, item.points[1:]):
            if (
                previous.bin_index + 1 == point.bin_index
                and previous.n >= 3
                and point.n >= 3
            ):
                linestyle = base_linestyle
                if previous.flagged or point.flagged:
                    linestyle = "--"
                line, = axis.plot(
                    [previous.temperature_K, point.temperature_K],
                    [_plot_y(previous.residual_dex), _plot_y(point.residual_dex)],
                    color=style["color"],
                    linewidth=style["linewidth"],
                    linestyle=linestyle,
                    zorder=3,
                )
                line.set_gid(item.gid)

        for point in item.points:
            y_value = point.residual_dex
            y_visible = _plot_y(y_value)
            clipped = y_value < Y_LIMITS[0] or y_value > Y_LIMITS[1]
            if clipped:
                direction = 1 if y_value > Y_LIMITS[1] else -1
                start = y_visible - direction * 0.22
                arrow = axis.annotate(
                    "",
                    xy=(point.temperature_K, y_visible),
                    xytext=(point.temperature_K, start),
                    arrowprops={"arrowstyle": "-|>", "color": style["color"], "linewidth": 0.8},
                    zorder=6,
                    annotation_clip=False,
                )
                arrow.set_gid(f"clipped-arrow:{item.gid}")
                if point.flagged:
                    marker = axis.scatter(
                        [point.temperature_K],
                        [y_visible],
                        s=style["markersize"] ** 2 * 2.0,
                        marker=marker_style,
                        facecolors="none",
                        edgecolors=style["color"],
                        linewidths=max(0.45, style["linewidth"] * 0.55),
                        zorder=7,
                    )
                    marker.set_gid(f"clipped-flag-marker:{item.gid}")
                continue
            if point.n < 3:
                marker_gid = f"low-count-marker:{item.gid}"
            else:
                marker_gid = f"binned-marker:{item.gid}"
            artist = axis.scatter(
                [point.temperature_K],
                [y_visible],
                s=style["markersize"] ** 2 * 2.0,
                marker=marker_style,
                facecolors="none" if point.flagged else style["color"],
                edgecolors=style["color"],
                linewidths=max(0.45, style["linewidth"] * 0.55),
                zorder=5,
            )
            artist.set_gid(marker_gid)

        if view == "species" and item.tier == TIER_MAJOR and item.points:
            endpoint = item.points[-1]
            species_label_candidates[item.gid] = (
                endpoint.temperature_K,
                endpoint.residual_dex,
            )
            species_label_items[item.gid] = item
        elif view == "element" and item.major_elements and item.points:
            endpoint = item.points[-1]
            for element in item.major_elements:
                current = label_candidates.get(element)
                if current is None or endpoint.temperature_K >= current[0]:
                    label_candidates[element] = (
                        endpoint.temperature_K,
                        endpoint.residual_dex,
                    )

    right_axis = axis.twinx()
    right_axis.set_ylim(*Y_LIMITS)
    if view == "element":
        label_positions, label_names = _spaced_axis_labels(label_candidates)
        right_axis.set_yticks(label_positions)
        right_axis.set_yticklabels(label_names)
        right_axis.tick_params(axis="y", labelsize=7, length=0, pad=2)
        if label_names:
            right_axis.set_ylabel("Major element", fontsize=7, labelpad=5)
    else:
        right_axis.set_yticks([])
        right_axis.spines["right"].set_visible(False)
        label_positions, label_gids = _spaced_axis_labels(species_label_candidates)
        x_low, x_high = data.x_limits
        x_offset = max(1.0, data.bin_width_K * 0.18)
        for label_y, gid in zip(label_positions, label_gids):
            item = species_label_items[gid]
            endpoint = item.points[-1]
            if item.rail == Rail.MELT_ACTIVITY.value:
                label = f"{item.subject} activity"
            elif item.rail == Rail.SIO_EVOLUTION.value:
                label = f"{item.subject} pressure"
            elif major_formula_counts[item.subject] > 1:
                label = f"{item.subject} vapour"
            else:
                label = item.subject
            put_to_right = endpoint.temperature_K + x_offset <= x_high
            label_x = (
                endpoint.temperature_K + x_offset
                if put_to_right
                else max(x_low, endpoint.temperature_K - x_offset)
            )
            style = style_for_tier(item.tier)
            annotation = axis.annotate(
                label,
                xy=(endpoint.temperature_K, _plot_y(endpoint.residual_dex)),
                xytext=(label_x, label_y),
                xycoords="data",
                textcoords="data",
                ha="left" if put_to_right else "right",
                va="center",
                fontsize=6.5,
                color=style["color"],
                arrowprops={
                    "arrowstyle": "-",
                    "color": style["color"],
                    "linewidth": 0.55,
                    "shrinkA": 0,
                    "shrinkB": 0,
                },
                annotation_clip=False,
                zorder=7,
            )
            annotation.set_gid(f"major-label:{gid}")

    axis.axhspan(-0.3, 0.3, color="#718096", alpha=0.08, zorder=0)
    axis.axhline(0.0, color="#4b5563", linewidth=0.7, zorder=1)
    axis.set_xlim(*data.x_limits)
    axis.set_ylim(*Y_LIMITS)
    axis.grid(True, color="#999999", alpha=0.16, linewidth=0.5)
    axis.set_xlabel("Temperature (K)", fontsize=8)
    axis.set_ylabel("Mean signed residual (dex)", fontsize=8)

    low = summary.min_temperature_K
    high = summary.max_temperature_K
    span = "—" if low is None or high is None else f"{low:g}–{high:g} K"
    rms = "—" if summary.rms_dex is None else f"{summary.rms_dex:.3g} dex"
    versions = ", ".join(sorted(summary.versions)) or "version not recorded"
    axis.set_title(
        f"{engine} · {versions}\n"
        f"n={summary.scored_rows:,} rows · {len(summary.species)} species · T={span} · RMS={rms}",
        fontsize=7,
        loc="left",
    )
    if not any(key[0] == engine for key in data.series):
        axis.text(
            0.5,
            0.5,
            "No scored dex rows\non selected rails",
            transform=axis.transAxes,
            ha="center",
            va="center",
            color="#777777",
            fontsize=8,
        )
    secondary = axis.secondary_xaxis(
        "top",
        functions=(lambda kelvin: kelvin - 273.15, lambda celsius: celsius + 273.15),
    )
    secondary.set_xlabel("Temperature (°C)", fontsize=8)
    secondary.tick_params(labelsize=7)
    axis.tick_params(labelsize=7)
    return right_axis


def _patch_labels(
    stamp: Mapping[str, Any],
    *,
    simulator_commit: str,
    openimcc_commit: str,
) -> list[str]:
    labels: list[str] = []
    simulator_sha = stamp.get("simulator_patch_sha256") or stamp.get("patch_sha256")
    same_simulator_commit = (
        stamp.get("simulator_tree_commit")
        and stamp.get("simulator_tree_commit") == simulator_commit
    )
    simulator_modified = stamp.get("simulator_modified")
    if (
        simulator_modified is not False
        and (simulator_modified is True or not same_simulator_commit)
        and simulator_sha
    ):
        value = str(simulator_sha)
        if value not in {"unknown", "none"}:
            labels.append(f"patch {value[:12]}")

    openimcc_sha = stamp.get("openimcc_patch_sha256")
    installed_commit = stamp.get("openimcc_installed_commit") or stamp.get("openimcc_commit")
    same_reported_commit = (
        installed_commit
        and (
            installed_commit == stamp.get("openimcc_reported_commit")
            or installed_commit == openimcc_commit
        )
    )
    openimcc_modified = stamp.get("openimcc_modified")
    if (
        openimcc_modified is not False
        and (openimcc_modified is True or not same_reported_commit)
        and openimcc_sha
    ):
        value = str(openimcc_sha)
        if value not in {"unknown", "none"}:
            labels.append(f"openimcc patch {value[:12]}")
    return labels


def stamp_text(
    data: ScorePlotData,
    *,
    simulator_commit: str,
    openimcc_commit: str,
    render_date: str,
) -> str:
    openimcc_summary = data.summaries.get("openimcc")
    openimcc_version = (
        ", ".join(sorted(openimcc_summary.versions))
        if openimcc_summary and openimcc_summary.versions
        else "version not recorded"
    )
    revision = str(data.stamp.get("revision") or "unknown")
    actual_simulator_commit = str(data.stamp.get("simulator_commit") or simulator_commit)
    actual_openimcc_commit = openimcc_commit
    openimcc_pack = _openimcc_default_pack(data)
    parts = [
        f"OpenIMCC {openimcc_version} · SCORE {actual_openimcc_commit}",
        *(
            (f"default pack at scored pin: {openimcc_pack}",)
            if openimcc_pack
            else ()
        ),
        f"simulator {actual_simulator_commit}",
        f"store {revision}",
        f"rendered {render_date} UTC",
        *_patch_labels(
            data.stamp,
            simulator_commit=actual_simulator_commit,
            openimcc_commit=actual_openimcc_commit,
        ),
    ]
    return "  |  ".join(parts)


def _openimcc_default_pack(data: ScorePlotData) -> str | None:
    summary = data.summaries.get("openimcc")
    if summary is None or not summary.default_packs:
        return None
    return ", ".join(sorted(summary.default_packs))


def _revision_slug(revision: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(revision or "unknown"))


def _empty_engine_names(data: ScorePlotData) -> tuple[str, ...]:
    return tuple(
        engine for engine in data.engines if data.summaries[engine].scored_rows == 0
    )


def _no_scored_rows_note(data: ScorePlotData, engines: Sequence[str]) -> str:
    if not engines:
        return ""
    entries: list[str] = []
    for engine in engines:
        refusals = data.summaries[engine].refusals
        not_observable = sum(
            data.summaries[engine].not_observable_by_rail.values()
        )
        details: list[str] = []
        if not_observable:
            details.append(
                f"not an observable of this engine ×{not_observable:,}"
            )
        if refusals:
            reason, count = sorted(
                refusals.items(), key=lambda item: (-item[1], item[0])
            )[0]
            details.append(f"{reason} ×{count:,}")
        if not details:
            details.append("no refusal reason recorded")
        entries.append(f"{engine} ({'; '.join(details)})")
    return "no scored rows: " + ", ".join(entries)


_HERO_TITLES = {
    Rail.VAPOUR.value: "Vaporisation",
    Rail.REDOX.value: "Redox convergence",
    Rail.MELT_ACTIVITY.value: "Melt activity",
}
_HERO_MARKERS = ("o", "s", "^", "D", "v", "P", "X", "<", ">", "*")
_HERO_OXYGEN_MODE_MARKERS = {
    "printed": "o",
    "commanded": "s",
    "solved": "^",
}
_HERO_OXYGEN_MODE_LABELS = {
    "printed": "printed scorer oxygen condition",
    "commanded": "commanded scorer oxygen condition",
    "solved": "solved cell oxygen balance",
}
_HERO_ENGINE_COLORS = {
    "openimcc": "#2a78d6",
    "vaporock": "#eb6834",
    "thermoengine": "#1baf7a",
    "internal-analytical": "#eda100",
    "magemin": "#e87ba4",
    "alphamelts": "#6250d6",
}
_HERO_ENGINE_ORDER = tuple(_HERO_ENGINE_COLORS)


def _hero_engine_colors(engines: Sequence[str]) -> dict[str, str]:
    unknown = set(engines) - _HERO_ENGINE_COLORS.keys()
    if unknown:
        raise ValueError(f"no fixed hero colour for engine(s): {', '.join(sorted(unknown))}")
    return {
        engine: _HERO_ENGINE_COLORS[engine]
        for engine in _HERO_ENGINE_ORDER
        if engine in engines
    }


def _ordered_hero_engines(engines: Sequence[str]) -> tuple[str, ...]:
    return tuple(_hero_engine_colors(engines))


def _compact_count(value: object) -> str:
    number = max(0, int(value or 0))
    if number < 1_000:
        return str(number)
    if number < 10_000:
        return f"{number / 1_000:.1f}k"
    if number < 1_000_000:
        return f"{number / 1_000:.0f}k"
    if number < 10_000_000:
        return f"{number / 1_000_000:.1f}M"
    return f"{number / 1_000_000:.0f}M"


def _format_dex(value: object) -> str:
    number = _finite_float(value)
    return "unavailable" if number is None else f"{number:.2f}"


def _hero_metric_view_text(label: str, view: Mapping[str, object]) -> str:
    count = _compact_count(view.get("n"))
    result = (
        f"{label} RMS {_format_dex(view.get('rms_dex'))} dex (n={count}), "
        f"SD {_format_dex(view.get('sd_dex'))} dex (n={count}), "
        f"median |dex| {_format_dex(view.get('median_abs_dex'))} dex (n={count})"
    )
    if label == "all":
        result += f", no T n={_compact_count(view.get('no_temperature_n'))}"
    for key, flag_label in (
        ("source_disagreement_flagged", "source-disagreement flagged"),
        ("unflagged", "unflagged"),
    ):
        flag_stats = view.get(key)
        if isinstance(flag_stats, Mapping):
            result += "; " + _hero_metric_view_text(flag_label, flag_stats)
    if "exposed_n" in view:
        held_out = view.get("held_out")
        held_out = held_out if isinstance(held_out, Mapping) else {}
        held_count = _compact_count(held_out.get("n"))
        result += (
            f", exposed n={_compact_count(view.get('exposed_n'))}, "
            f"held-out n={held_count}"
        )
        if int(held_out.get("n") or 0) >= int(
            held_out.get("minimum_n") or HERO_MINIMUM_REPORT_N
        ):
            result += (
                f", held-out RMS {_format_dex(held_out.get('rms_dex'))} dex "
                f"(n={held_count}), SD {_format_dex(held_out.get('sd_dex'))} dex "
                f"(n={held_count}), median |dex| "
                f"{_format_dex(held_out.get('median_abs_dex'))} dex (n={held_count})"
            )
    return result


def _hero_scorecard_cell_text(
    view: Mapping[str, object], *, include_no_temperature: bool = False
) -> str:
    count = _compact_count(view.get("n"))
    lines = [
        f"RMS {_format_dex(view.get('rms_dex'))} dex (n={count}); "
        f"SD {_format_dex(view.get('sd_dex'))} dex (n={count}); "
        f"median |dex| {_format_dex(view.get('median_abs_dex'))} dex (n={count})"
    ]
    if include_no_temperature:
        lines.append(f"no T n={_compact_count(view.get('no_temperature_n'))}")
    for key, label in (
        ("source_disagreement_flagged", "source-disagreement flagged"),
        ("unflagged", "unflagged"),
    ):
        flag_stats = view.get(key)
        if isinstance(flag_stats, Mapping):
            lines.append(
                f"{label}: " + _hero_scorecard_cell_text(flag_stats)
            )
    if "exposed_n" in view:
        held_out = view.get("held_out")
        held_out = held_out if isinstance(held_out, Mapping) else {}
        held_count = _compact_count(held_out.get("n"))
        lines.append(
            f"exposed n={_compact_count(view.get('exposed_n'))}; "
            f"held-out n={held_count}"
        )
        minimum_n = int(held_out.get("minimum_n") or HERO_MINIMUM_REPORT_N)
        if int(held_out.get("n") or 0) >= minimum_n:
            lines.append(
                f"held-out RMS {_format_dex(held_out.get('rms_dex'))} dex "
                f"(n={held_count}); SD {_format_dex(held_out.get('sd_dex'))} dex "
                f"(n={held_count}); median |dex| "
                f"{_format_dex(held_out.get('median_abs_dex'))} dex (n={held_count})"
            )
    return "\n".join(lines)


def _hero_scorecard_rows(data: ScorePlotData, rail: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for engine in _ordered_hero_engines(data.engines):
        metric = data.hero_all_numeric_metrics.get((rail, engine), {})
        views = metric.get("views", {})
        views = views if isinstance(views, Mapping) else {}
        row = [engine]
        for view_name, _view_label in HERO_TEMPERATURE_VIEWS:
            view = views.get(view_name, {})
            view = view if isinstance(view, Mapping) else {}
            row.append(
                _hero_scorecard_cell_text(
                    view,
                    include_no_temperature=view_name == "all",
                )
            )
        rows.append(row)
    return rows


def _hero_scorecard_row_weights(
    rows: Sequence[Sequence[str]],
) -> tuple[float, ...]:
    # Each unit reserves one rendered scorecard text line at the configured font size.
    return (1.0,) + tuple(
        float(max(1, max(text.count("\n") + 1 for text in row)))
        for row in rows
    )


def _hero_scorecard_section_height(rows: Sequence[Sequence[str]]) -> float:
    line_count = sum(_hero_scorecard_row_weights(rows))
    table_height = (
        line_count
        * HERO_SCORECARD_LINE_HEIGHT_IN
        / HERO_SCORECARD_TABLE_HEIGHT_FRACTION
    )
    return HERO_SCORECARD_TITLE_HEIGHT_IN + table_height


def _draw_hero_scorecard_table(axis: Axes, rows: Sequence[Sequence[str]]) -> None:
    table = axis.table(
        cellText=rows,
        colLabels=(
            "Engine",
            "All",
            f"T ≥ {HERO_TEMPERATURE_FLOOR_K:g} K",
            f"T < {HERO_TEMPERATURE_FLOOR_K:g} K",
        ),
        cellLoc="left",
        colLoc="left",
        colWidths=(0.10, 0.30, 0.30, 0.30),
        bbox=(0.0, 0.0, 1.0, HERO_SCORECARD_TABLE_HEIGHT_FRACTION),
    )
    table.auto_set_font_size(False)
    row_weights = _hero_scorecard_row_weights(rows)
    for (table_row, _column), cell in table.get_celld().items():
        cell.set_height(row_weights[table_row])
        cell.set_edgecolor("#cccccc")
        cell.set_linewidth(0.25)
        cell.PAD = 0.02
        cell.set_text_props(fontsize=HERO_SCORECARD_FONT_SIZE, va="center")
        if table_row == 0:
            cell.set_facecolor("#eeeeee")
            cell.set_text_props(fontsize=4.0, va="center", weight="bold")


def _reserve_hero_status_space(
    figure: Any,
    grid: Any,
    title_artist: Any,
    status_artist: Any,
    legend: Any | None,
    scatter_axes: Sequence[Axes],
) -> None:
    if not scatter_axes:
        return

    gap_inches = 0.10
    dpi = figure.get_dpi()
    height_inches = figure.get_size_inches()[1]
    grid_top_inches = grid.top * height_inches
    grid_bottom_inches = grid.bottom * height_inches
    for _attempt in range(4):
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        figure_bbox = figure.bbox
        height_pixels = figure_bbox.height
        if title_artist is not None:
            title_bbox = title_artist.get_window_extent(renderer)
            status_artist.set_y(
                (title_bbox.y0 - gap_inches * dpi) / height_pixels
            )
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
        status_bbox = status_artist.get_window_extent(renderer)
        if legend is not None:
            legend.set_bbox_to_anchor(
                (
                    0.5,
                    (status_bbox.y0 - gap_inches * dpi) / height_pixels,
                ),
                transform=figure.transFigure,
            )
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()

        scatter_top = max(
            bbox.y1
            for axis in scatter_axes
            if (bbox := axis.get_tightbbox(renderer)) is not None
        )
        content_bottom = (
            legend.get_window_extent(renderer).y0
            if legend is not None
            else status_bbox.y0
        )
        gap_pixels = content_bottom - scatter_top
        if gap_pixels >= gap_inches * dpi:
            return

        height_inches = figure.get_size_inches()[1]
        extra_inches = (gap_inches - gap_pixels / dpi) / 0.994 + 0.02
        height_inches += extra_inches
        figure.set_size_inches(figure.get_size_inches()[0], height_inches, forward=True)
        grid.update(
            top=grid_top_inches / height_inches,
            bottom=grid_bottom_inches / height_inches,
        )


def _hero_comparison_view_text(
    engine: str,
    primary: str,
    label: str,
    view: Mapping[str, object],
) -> str:
    count = _compact_count(view.get("n_shared"))
    result = (
        f"{label} {engine} RMS {_format_dex(view.get('outside_rms_dex'))} dex "
        f"(n_shared={count}), SD {_format_dex(view.get('outside_sd_dex'))} dex "
        f"(n_shared={count}), median |dex| "
        f"{_format_dex(view.get('outside_median_abs_dex'))} dex (n_shared={count}) "
        f"vs {primary} RMS {_format_dex(view.get('primary_rms_dex_on_shared'))} dex "
        f"(n_shared={count}), SD "
        f"{_format_dex(view.get('primary_sd_dex_on_shared'))} dex (n_shared={count}), "
        f"median |dex| "
        f"{_format_dex(view.get('primary_median_abs_dex_on_shared'))} dex "
        f"(n_shared={count})"
    )
    if label == "all" and "no_temperature_n" in view:
        result += f", no T n={_compact_count(view.get('no_temperature_n'))}"
    for key, flag_label in (
        ("source_disagreement_flagged", "source-disagreement flagged"),
        ("unflagged", "unflagged"),
    ):
        flag_stats = view.get(key)
        if isinstance(flag_stats, Mapping):
            result += "; " + _hero_comparison_view_text(
                engine,
                primary,
                flag_label,
                flag_stats,
            )
    if "exposed_n" in view:
        held_out = view.get("held_out")
        held_out = held_out if isinstance(held_out, Mapping) else {}
        held_count = _compact_count(held_out.get("n"))
        result += (
            f", exposed n={_compact_count(view.get('exposed_n'))}, "
            f"held-out n={held_count}"
        )
        if int(held_out.get("n") or 0) >= int(
            held_out.get("minimum_n") or HERO_MINIMUM_REPORT_N
        ):
            held_out_view = {
                "n_shared": held_out.get("n"),
                "outside_rms_dex": held_out.get("outside_rms_dex"),
                "outside_sd_dex": held_out.get("outside_sd_dex"),
                "outside_median_abs_dex": held_out.get(
                    "outside_median_abs_dex"
                ),
                "primary_rms_dex_on_shared": held_out.get(
                    "primary_rms_dex_on_shared"
                ),
                "primary_sd_dex_on_shared": held_out.get(
                    "primary_sd_dex_on_shared"
                ),
                "primary_median_abs_dex_on_shared": held_out.get(
                    "primary_median_abs_dex_on_shared"
                ),
            }
            result += "; held-out " + _hero_comparison_view_text(
                engine,
                primary,
                "all",
                held_out_view,
            )
    return result


def _temperature_tick(value: float, _position: int | None = None) -> str:
    return f"{value / 1_000:.3g}k" if abs(value) >= 1_000 else f"{value:.0f}"


def _temperature_tick_step(limits: tuple[float, float]) -> float:
    """Pick ticks coarse enough to stay distinct at three significant figures."""

    low, high = limits
    maximum = max(abs(low), abs(high))
    display_step = (
        10.0 ** (math.floor(math.log10(maximum)) - 2)
        if maximum >= 1_000
        else 1.0
    )
    desired_step = max(display_step, abs(high - low) / 6)
    scale = 10.0 ** math.floor(math.log10(desired_step))
    return next(
        factor * scale
        for factor in (1, 2, 5, 10)
        if factor * scale >= desired_step
    )


def _set_temperature_tick_locator(axis: Any, limits: tuple[float, float]) -> None:
    axis.set_major_locator(MultipleLocator(_temperature_tick_step(limits)))


def _hero_facet_counts(data: ScorePlotData, rail: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for (series_rail, facet, _engine, _composition), points in data.hero_series.items():
        if series_rail == rail:
            counts[facet] += sum(point.n for point in points)
    return counts


def _hero_facets(data: ScorePlotData, rail: str) -> tuple[str, ...]:
    facets = {
        facet
        for series_rail, facet, _engine, _composition in data.hero_series
        if series_rail == rail
    }
    facets.update(
        facet
        for refusal_rail, facet, _composition, _engine, _reason in data.hero_refusals
        if refusal_rail == rail
    )
    if rail == Rail.VAPOUR.value:
        minor = [facet for facet, _label in HERO_MINOR_FACETS]
        return tuple(sorted(facets - set(minor))) + tuple(minor)
    if rail == Rail.REDOX.value:
        preferred = ("Fe3+/Fe2+ ratio", "Fe3+/sumFe", "log fO2")
        return tuple(facet for facet in preferred if facet in facets) + tuple(
            sorted(facets - set(preferred))
        )
    return tuple(sorted(facets)) or ("unknown species",)


def _hero_empty_facet_text(
    data: ScorePlotData,
    rail: str,
    facets: Sequence[str],
    *,
    minor_group: bool = False,
) -> str:
    parts: list[str] = []
    for facet in facets:
        display_facet = next(
            (label for key, label in HERO_MINOR_FACETS if key == facet), facet
        )
        entries = _hero_refusal_entries(data, rail, facet)
        by_reason: Counter[str] = Counter()
        for _engine, reason, count in entries:
            by_reason[reason] += count
        total = sum(by_reason.values())
        reasons = []
        for reason, count in sorted(
            by_reason.items(), key=lambda item: (-item[1], item[0])
        )[:2]:
            reasons.append(f"{reason} {_compact_count(count)}")
        reason_text = "; ".join(reasons) if reasons else "no refusal reason recorded"
        parts.append(
            f"{display_facet} (refused {_compact_count(total)}: {reason_text})"
        )
    if minor_group:
        heading = "Minor species / foulants (no numeric predictions yet):"
    else:
        heading = "No numeric predictions yet:"
    return heading + ("\n" + "\n".join(parts) if parts else "")


def _hero_refusal_entries(
    data: ScorePlotData,
    rail: str,
    facet: str,
) -> tuple[tuple[str, str, int], ...]:
    counts: Counter[tuple[str, str]] = Counter()
    for (series_rail, series_facet, _composition, engine, reason), count in (
        data.hero_refusals.items()
    ):
        if series_rail == rail and series_facet == facet:
            counts[(engine, reason)] += count
    return tuple(
        (engine, reason, count)
        for (engine, reason), count in sorted(
            counts.items(), key=lambda item: (item[0][1], item[0][0])
        )
    )


def _hero_legend_handles(
    engine_names: Iterable[str],
    engine_colors: Mapping[str, Any],
    *,
    flagged: bool = False,
    group_labels: Sequence[tuple[str, str]] = (),
    source_labels: Sequence[tuple[str, str]] = (),
    size_labels: Sequence[tuple[float, str]] = (),
) -> list[Line2D]:
    engines = sorted(set(engine_names))
    handles = [
        Line2D(
            [0],
            [0],
            color=engine_colors.get(engine, "#555555"),
            linewidth=0.0,
            linestyle="None",
            marker="o",
            markerfacecolor=engine_colors.get(engine, "#555555"),
            markeredgecolor=engine_colors.get(engine, "#555555"),
            markersize=4,
            label=engine,
        )
        for engine in engines
    ]
    if flagged:
        handles.append(
            Line2D(
                [0],
                [0],
                color="#444444",
                linewidth=0.0,
                linestyle="None",
                marker="o",
                markerfacecolor="#ffffff",
                markeredgecolor="#444444",
                label=FLAGGED_MARKER_LABEL,
            )
        )
    handles.extend(
        Line2D(
            [0],
            [0],
            color="#555555",
            linestyle="None",
            marker=marker,
            markerfacecolor="#555555",
            markeredgecolor="#555555",
            markersize=4,
            label=label,
        )
        for marker, label in source_labels
    )
    handles.extend(
        Line2D(
            [0],
            [0],
            color="#555555",
            linestyle="None",
            marker=marker,
            markerfacecolor="#ffffff",
            markeredgecolor="#555555",
            markersize=4,
            label=label,
        )
        for marker, label in group_labels
    )
    handles.extend(
        Line2D(
            [0],
            [0],
            color="#555555",
            linestyle="None",
            marker="o",
            markerfacecolor="#ffffff",
            markeredgecolor="#555555",
            markersize=math.sqrt(size),
            label=label,
        )
        for size, label in size_labels
    )
    return handles


def _draw_hero_deck_scatter(
    axis: Axes,
    points: Sequence[Mapping[str, object]],
    *,
    engine_colors: Mapping[str, Any],
) -> None:
    """Draw scorer-produced deck points, with hollow faces only for scorer flags."""

    grouped: dict[tuple[str, bool, str, float], list[tuple[float, float]]] = defaultdict(list)
    for point in points:
        x_value = _finite_float(point.get("x_value"))
        y_value = _finite_float(point.get("y_value"))
        engine = str(point.get("engine") or "unknown")
        if x_value is None or y_value is None:
            continue
        marker = str(point.get("marker") or "o")
        marker_size = _finite_float(point.get("marker_size")) or 24.0
        grouped[(engine, bool(point.get("flagged")), marker, marker_size)].append(
            (x_value, y_value)
        )

    for (engine, flagged, marker, marker_size), values in sorted(grouped.items()):
        color = engine_colors.get(engine, "#555555")
        artist = axis.scatter(
            [value[0] for value in values],
            [value[1] for value in values],
            marker=marker,
            s=marker_size,
            facecolors="#ffffff" if flagged else color,
            edgecolors=color,
            linewidths=1.0,
            zorder=3,
        )
        artist.set_gid(
            f"hero-deck-marker:{engine}:{'flagged' if flagged else 'ordinary'}:{marker}"
        )
    axis.tick_params(labelsize=6)
    axis.grid(True, color="#999999", alpha=0.18, linewidth=0.5)
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))


def _draw_hero_current(
    axis: Axes,
    data: ScorePlotData,
    rail: str,
    facet: str,
    *,
    engine_colors: Mapping[str, Any],
) -> None:
    series = [
        (engine, composition, points)
        for (series_rail, series_facet, engine, composition), points in data.hero_series.items()
        if series_rail == rail and series_facet == facet
    ]
    series.sort(
        key=lambda item: (
            item[0],
            item[2][0].composition_label if item[2] else "",
            item[1],
        )
    )
    for engine, composition, points in series:
        if not points:
            continue
        color = engine_colors.get(engine, "#555555")
        for flagged in (False, True):
            flagged_points = [item for item in points if item.flagged is flagged]
            if not flagged_points:
                continue
            artist = axis.scatter(
                [item.temperature_K for item in flagged_points],
                [item.residual_dex for item in flagged_points],
                marker="o",
                s=25,
                facecolors="#ffffff" if flagged else color,
                edgecolors=color,
                linewidths=1.2,
                zorder=3,
            )
            artist.set_gid(
                f"hero-marker:{rail}:{facet}:{engine}:{_composition_slug(composition)}:"
                f"{'flagged' if flagged else 'ordinary'}"
            )
    axis.set_ylabel("signed dex", fontsize=7)
    axis.set_xlabel("Temperature (K)", fontsize=7)
    axis.tick_params(labelsize=6)
    axis.grid(True, color="#999999", alpha=0.18, linewidth=0.5)
    _set_temperature_tick_locator(axis.xaxis, axis.get_xlim())
    axis.xaxis.set_major_formatter(FuncFormatter(_temperature_tick))
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))


def _draw_hero_refusals(
    axis: Axes,
    entries: Sequence[tuple[str, str, int]],
    *,
    engine_colors: Mapping[str, Any],
) -> None:
    if not entries:
        axis.text(0.01, 0.55, "refusals: 0", transform=axis.transAxes, fontsize=5.5)
        axis.set_axis_off()
        return
    reasons = sorted({reason for _engine, reason, _count in entries})
    positions = list(range(len(reasons)))
    engines = sorted({engine for engine, _reason, _count in entries})
    for position, reason in zip(positions, reasons):
        left = 0
        for engine in engines:
            count = sum(
                row_count
                for row_engine, row_reason, row_count in entries
                if row_engine == engine and row_reason == reason
            )
            if not count:
                continue
            axis.barh(
                [position],
                [count],
                left=[left],
                color=[engine_colors.get(engine, "#777777")],
                height=0.72,
            )
            left += count
    axis.set_yticks(positions)
    axis.set_yticklabels(reasons, fontsize=4.8)
    axis.invert_yaxis()
    axis.xaxis.set_major_locator(MaxNLocator(integer=True))
    axis.xaxis.set_major_formatter(
        FuncFormatter(lambda value, _position: _compact_count(round(value)))
    )
    axis.tick_params(axis="x", labelsize=5)
    axis.tick_params(axis="y", length=0)
    axis.grid(axis="x", color="#999999", alpha=0.18, linewidth=0.5)
    axis.set_axisbelow(True)


def _history_date(entry: Mapping[str, Any]) -> float | None:
    raw = entry.get("commit_date")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return mdates.date2num(parsed)


def _draw_hero_sweet_spot(
    axis: Axes,
    engine: str,
    cells: Sequence[Mapping[str, object]],
    *,
    x_limits: tuple[float, float],
    temperature_limits: tuple[float, float],
    export_dpi: float | None = None,
) -> bool:
    """Draw the scorer's temperature × SiO₂ cells as three status bands."""

    axis.set_xlim(*x_limits)
    axis.set_ylim(*temperature_limits)
    axis.figure.canvas.draw()
    dpi = float(export_dpi or axis.figure.dpi)
    position = axis.get_position()
    axis_width_pixels = position.width * axis.figure.get_figwidth() * dpi
    axis_height_pixels = position.height * axis.figure.get_figheight() * dpi
    x_gap = 2 * (x_limits[1] - x_limits[0]) / max(axis_width_pixels, 1)
    y_gap = 2 * (temperature_limits[1] - temperature_limits[0]) / max(axis_height_pixels, 1)

    for cell in cells:
        x_low = _finite_float(cell.get("sio2_lower"))
        x_high = _finite_float(cell.get("sio2_upper"))
        y_low = _finite_float(cell.get("temperature_lower_K"))
        y_high = _finite_float(cell.get("temperature_upper_K"))
        if None in (x_low, x_high, y_low, y_high):
            continue
        assert x_low is not None and x_high is not None
        assert y_low is not None and y_high is not None
        count = int(cell.get("n") or 0)
        if count < 3:
            continue
        value = _finite_float(cell.get("median_abs_dex"))
        if value is None:
            continue
        fill = str(
            cell.get("colour")
            or FINGERPRINT_COLOURS[_battery_hero_fingerprint_band(value)]
        )
        axis.add_patch(
            Rectangle(
                (x_low + x_gap / 2, y_low + y_gap / 2),
                max(0.0, x_high - x_low - x_gap),
                max(0.0, y_high - y_low - y_gap),
                facecolor=fill,
                edgecolor="none",
                linewidth=0,
            )
        )

    if not any(int(cell.get("n") or 0) for cell in cells):
        axis.set_axis_off()
        return False
    axis.set_xticks([index / 10 for index in range(11)])
    axis.set_xticklabels([f"{index / 10:.1f}" for index in range(11)], fontsize=5)
    _set_temperature_tick_locator(axis.yaxis, temperature_limits)
    axis.tick_params(axis="y", labelsize=5)
    axis.yaxis.set_major_formatter(FuncFormatter(_temperature_tick))
    axis.set_xlabel("SiO₂ mole fraction", fontsize=6)
    axis.set_ylabel("T (K)", fontsize=6)
    axis.set_title(
        f"{engine}: temperature × SiO₂ fingerprint",
        loc="left",
        fontsize=6.5,
    )
    return True


def _draw_hero_history(
    axis: Axes,
    rail: str,
    history: Sequence[Mapping[str, Any]],
    *,
    engine_colors: Mapping[str, Any],
) -> set[str]:
    metrics_by_engine: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"rms_dex": [], "sd_dex": [], "median_abs_dex": []}
    )
    comparison_points: dict[
        tuple[str, str], list[tuple[float, float, float, int]]
    ] = defaultdict(list)
    history_engines: set[str] = set()
    history_dates: list[float] = []
    history_rows = 0
    for entry in history:
        date = _history_date(entry)
        if date is None:
            continue
        history_rows += 1
        history_dates.append(date)
        metrics = entry.get("metrics")
        if isinstance(metrics, Sequence) and not isinstance(metrics, (str, bytes)):
            for metric in metrics:
                if not isinstance(metric, Mapping) or metric.get("rail") != rail:
                    continue
                engine = str(metric.get("engine") or "unknown")
                for field_name in ("rms_dex", "sd_dex", "median_abs_dex"):
                    value = _finite_float(metric.get(field_name))
                    if value is not None:
                        metrics_by_engine[engine][field_name].append((date, value))
                        history_engines.add(engine)
        comparisons = entry.get("comparisons")
        if isinstance(comparisons, Sequence) and not isinstance(comparisons, (str, bytes)):
            for record in comparisons:
                if not isinstance(record, Mapping) or record.get("rail") != rail:
                    continue
                primary = str(record.get("primary_engine") or "unknown")
                rows = record.get("comparisons")
                if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                    continue
                for comparison in rows:
                    if not isinstance(comparison, Mapping):
                        continue
                    engine = str(comparison.get("engine") or "unknown")
                    primary_value = _finite_float(
                        comparison.get("primary_rms_dex_on_shared")
                    )
                    outside_value = _finite_float(
                        comparison.get("outside_rms_dex")
                    )
                    n_shared = int(comparison.get("n_shared") or 0)
                    if primary_value is None or outside_value is None or n_shared < 1:
                        continue
                    comparison_points[(primary, engine)].append(
                        (date, primary_value, outside_value, n_shared)
                    )
                    history_engines.add(primary)
                    history_engines.add(engine)

    if not metrics_by_engine and not comparison_points:
        axis.set_axis_off()
        axis.text(
            0.5,
            0.5,
            "no history rows",
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=7,
        )
        axis.set_title("History by green landing", loc="left", fontsize=8)
        return history_engines

    engine_names = sorted(metrics_by_engine)
    for engine in engine_names:
        color = engine_colors.get(engine, "#555555")
        for field_name, linestyle, label in (
            ("rms_dex", "-", "RMS"),
            ("sd_dex", "-.", "SD"),
            ("median_abs_dex", "--", "median |dex|"),
        ):
            points = sorted(metrics_by_engine[engine][field_name])
            if not points:
                continue
            axis.plot(
                [date for date, _value in points],
                [value for _date, value in points],
                color=color,
                linestyle=linestyle,
                marker="o",
                markersize=3,
                linewidth=0.9,
                label=f"{engine} {label}",
            )
    for primary in HERO_PRIMARY_ENGINES:
        for outside_engine in HERO_COMPARISON_ENGINES:
            points = sorted(comparison_points[(primary, outside_engine)])
            if not points:
                continue
            dates = [date for date, _primary, _outside, _n in points]
            axis.plot(
                dates,
                [value for _date, value, _outside, _n in points],
                color=engine_colors.get(primary, "#333333"),
                linestyle="-.",
                marker="s",
                markersize=3,
                linewidth=1.0,
                label=f"{primary} paired with {outside_engine}",
            )
            axis.plot(
                dates,
                [value for _date, _primary, value, _n in points],
                color=engine_colors.get(outside_engine, "#555555"),
                linestyle=":",
                marker="D",
                markersize=3,
                linewidth=1.1,
                label=f"{outside_engine} vs {primary}",
            )
            for index, (date, _primary, value, n_shared) in enumerate(points):
                axis.annotate(
                    f"n_shared={_compact_count(n_shared)}",
                    (date, value),
                    textcoords="offset points",
                    xytext=(0, 4 + index % 3 * 4),
                    ha="center",
                    fontsize=4.5,
                )

    if history_rows == 0:
        axis.text(
            0.5,
            0.5,
            "no history rows",
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=7,
        )
    axis.set_title("History by green landing", loc="left", fontsize=8)
    axis.set_ylabel("RMS / SD / median |dex|", fontsize=7)
    axis.set_xlabel("Green landing date", fontsize=7)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    if history_dates:
        date_span = max(history_dates) - min(history_dates)
        axis.xaxis.set_major_locator(
            mdates.DayLocator(interval=max(1, math.ceil(date_span / 6)))
        )
    axis.tick_params(axis="x", rotation=20, labelsize=5.5)
    axis.tick_params(axis="y", labelsize=6)
    axis.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    axis.grid(True, color="#999999", alpha=0.18, linewidth=0.5)
    return history_engines


def _hero_summary_payload(
    data: ScorePlotData,
) -> dict[str, Any]:
    charts: dict[str, Any] = {}
    exposure_enabled = any(
        "exposed_n" in metric.get("views", {}).get("all", {})
        for metric in data.hero_all_numeric_metrics.values()
    )
    for rail in HERO_RAILS:
        rows_by_engine: Counter[str] = Counter()
        for (series_rail, facet, engine, _composition), points in data.hero_series.items():
            if series_rail == rail and not (
                rail == Rail.VAPOUR.value and facet.startswith("minor:")
            ):
                rows_by_engine[engine] += sum(point.n for point in points)
        refusals: dict[str, dict[str, int]] = defaultdict(dict)
        for (refusal_rail, engine, reason), count in data.hero_refusal_totals.items():
            if refusal_rail == rail:
                refusals[engine][reason] = count
        current_metrics = {
            engine: data.hero_all_numeric_metrics.get((rail, engine))
            for engine in sorted(data.engines)
        }
        openimcc_vaporock = next(
            (
                comparison
                for comparison in data.hero_comparisons[
                    (rail, "openimcc")
                ].get("comparisons", ())
                if isinstance(comparison, Mapping)
                and comparison.get("engine") == "vaporock"
            ),
            None,
        ) if rail == Rail.VAPOUR.value else None
        charts[rail] = {
            "temperature_split": {
                "field": HERO_TEMPERATURE_FIELD,
                "floor_K": str(HERO_TEMPERATURE_FLOOR_K),
                "boundary": (
                    f"{HERO_TEMPERATURE_FLOOR_K:g} K is included in "
                    "temperature_ge_1700"
                ),
                "missing_temperature": "all only; counted as no_temperature_n",
            },
            "source_disagreement_split": (
                {
                    "field": "Residual.notices",
                    "notice_kind": NoticeKind.SOURCE_DISAGREEMENT.value,
                    "rule": (
                        "notice.kind == "
                        f"{NoticeKind.SOURCE_DISAGREEMENT.value}"
                    ),
                    "enum": NoticeKind.SOURCE_DISAGREEMENT.name,
                    "reason_strings": list(data.hero_source_disagreement_reasons),
                    "groups": ["source_disagreement_flagged", "unflagged"],
                }
                if rail == Rail.VAPOUR.value
                else None
            ),
            "exposure_policy": (
                {
                    "rule": (
                        "exposed if row reference is in the census or source_id is "
                        "in the exposed-source list; held_out only if reference is "
                        "absent from both and absent from pre-cutoff store IDs"
                    ),
                    "minimum_held_out_n": HERO_MINIMUM_REPORT_N,
                }
                if exposure_enabled
                else None
            ),
            "plotted_observations_by_engine": dict(sorted(rows_by_engine.items())),
            "not_observable_by_engine": {
                engine: int(
                    data.hero_diagnostics.get((rail, engine), {}).get(
                        "not_observable_count", 0
                    )
                )
                for engine in sorted(data.engines)
            },
            "refused_by_engine_reason": {
                engine: dict(sorted(reasons.items()))
                for engine, reasons in sorted(refusals.items())
            },
            "current_all_numeric_rms_dex_by_primary_engine": {
                engine: (
                    None if metric is None else metric.get("rms_dex")
                )
                for engine, metric in current_metrics.items()
                if engine in HERO_PRIMARY_ENGINES
            },
            "all_numeric_metrics_by_engine": current_metrics,
            "same_row_comparisons_by_primary_engine": {
                engine: data.hero_comparisons[(rail, engine)]
                for engine in HERO_PRIMARY_ENGINES
            },
            "openimcc_vs_vaporock_shared_rows": openimcc_vaporock,
            "openimcc_vs_vaporock_shared_by_species": (
                [
                    record
                    for record in data.hero_openimcc_vaporock_species
                    if record.get("rail") == rail
                ]
                if rail == Rail.VAPOUR.value
                else []
            ),
        }
    return charts


def _hero_deck_summary_payload(data: ScorePlotData) -> dict[str, Any]:
    charts = sorted({chart for chart, _engine in data.hero_deck_metrics})
    return {
        chart: {
            "temperature_split": {
                "field": HERO_TEMPERATURE_FIELD,
                "floor_K": str(HERO_TEMPERATURE_FLOOR_K),
                "boundary": (
                    f"{HERO_TEMPERATURE_FLOOR_K:g} K is included in "
                    "temperature_ge_1700"
                ),
                "missing_temperature": "all only; counted as no_temperature_n",
            },
            "metrics_by_engine": {
                engine: data.hero_deck_metrics[(chart, engine)]
                for metric_chart, engine in sorted(data.hero_deck_metrics)
                if metric_chart == chart
            },
            "same_row_comparisons_by_primary_engine": {
                engine: data.hero_deck_comparisons[(chart, engine)]
                for comparison_chart, engine in sorted(data.hero_deck_comparisons)
                if comparison_chart == chart
            },
        }
        for chart in charts
    }


def _collection_row_database(
    source_path: Path,
    database: sqlite3.Connection,
    *,
    bin_width_K: float,
) -> None:
    """Stream only scored rows used by the five hero observables to disk."""

    database.execute(
        """CREATE TABLE collection_rows (
            rail TEXT NOT NULL,
            engine TEXT NOT NULL,
            experiment TEXT NOT NULL,
            temperature_key TEXT NOT NULL,
            composition_key TEXT NOT NULL,
            observable TEXT NOT NULL,
            source TEXT NOT NULL,
            reference TEXT NOT NULL,
            temperature_K REAL NOT NULL,
            temperature_bin INTEGER NOT NULL,
            sio2 REAL,
            sio2_bin INTEGER,
            composition TEXT NOT NULL,
            species TEXT NOT NULL,
            residual_dex REAL NOT NULL,
            flagged INTEGER NOT NULL
        )"""
    )
    pending: list[tuple[object, ...]] = []
    with _open_residual_jsonl(source_path) as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source_path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, Mapping) or row.get("kind") == "battery_store_stamp":
                continue
            rail = str(row.get("rail") or "")
            if rail not in COLLECTION_SCORE_RAILS:
                continue
            status = residual_status_token(row.get("status"))
            numeric = row.get("numeric")
            if status is ResidualStatus.REFUSED or not isinstance(numeric, Mapping):
                continue
            if numeric.get("operation") != "dex":
                continue
            if _is_d139_uncalibrated_kems_row(row):
                continue
            residual = _finite_float(numeric.get("value"))
            identity = _row_identity(row)
            temperature_raw = _state_value(identity.get("temperature_K"))
            temperature = _finite_float(temperature_raw)
            if residual is None or temperature is None:
                continue
            temperature_key = _decimal_key(temperature_raw)
            if temperature_key is None:
                continue
            engine = _engine_name(row)
            if engine is None:
                continue
            composition_key, composition_label = _composition_fields(identity)
            composition = _state_value(identity.get("composition"))
            sio2_decimal = _battery_hero_sio2_mole_fraction(composition)
            sio2 = None if sio2_decimal is None else float(sio2_decimal)
            fingerprint_bins = _battery_hero_sweet_spot_bin_indices(
                temperature_raw, sio2_decimal
            )
            sio2_bin = None if fingerprint_bins is None else fingerprint_bins[1]
            candidate = row.get("candidate_observation")
            candidate = candidate if isinstance(candidate, Mapping) else {}
            source_name = str(
                candidate.get("source_id")
                or row.get("source_id")
                or row.get("reference")
                or row.get("key")
                or "unknown source"
            )
            reference = str(row.get("reference") or row.get("key") or "")
            request = row.get("candidate_request")
            request = request if isinstance(request, Mapping) else {}
            experiment = str(
                candidate.get("experiment_id")
                or request.get("experiment_id")
                or reference
                or f"line:{line_number}"
            )
            pending.append(
                (
                    rail,
                    engine,
                    experiment,
                    temperature_key,
                    composition_key,
                    _row_observable(row),
                    source_name,
                    reference,
                    temperature,
                    math.floor(temperature / bin_width_K),
                    sio2,
                    sio2_bin,
                    composition_label,
                    _row_formula(row),
                    residual,
                    int(bool(_flagged_payload_strata(row))),
                )
            )
            if len(pending) >= 5000:
                database.executemany(
                    "INSERT INTO collection_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    pending,
                )
                pending.clear()
    if pending:
        database.executemany(
            "INSERT INTO collection_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            pending,
        )
    database.execute(
        "CREATE INDEX collection_rows_rail_engine ON collection_rows(rail, engine)"
    )
    database.execute(
        "CREATE INDEX collection_rows_temperature ON collection_rows(rail, engine, temperature_bin, residual_dex)"
    )
    database.execute(
        "CREATE INDEX collection_rows_fingerprint ON collection_rows(rail, engine, temperature_bin, sio2_bin)"
    )
    database.execute(
        """CREATE VIEW collection_unique_rows AS
           SELECT rail, engine,
                  MIN(experiment) AS experiment,
                  MIN(temperature_key) AS temperature_key,
                  composition_key, MIN(observable) AS observable,
                  MIN(source) AS source, MIN(reference) AS reference,
                  MIN(temperature_K) AS temperature_K,
                  MIN(temperature_bin) AS temperature_bin,
                  MIN(sio2) AS sio2, MIN(sio2_bin) AS sio2_bin,
                  MIN(composition) AS composition, MIN(species) AS species,
                  AVG(residual_dex) AS residual_dex,
                  MAX(flagged) AS flagged
             FROM collection_rows
            GROUP BY engine, species, rail, composition_key, composition,
                     observable, experiment, temperature_key"""
    )
    database.commit()


def _collection_stratified_sample(
    database: sqlite3.Connection,
    *,
    limit: int,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    groups = database.execute(
        """SELECT rail, engine, COUNT(*)
             FROM collection_unique_rows GROUP BY rail, engine"""
    )
    for rail, engine, total in groups:
        if int(total) <= limit:
            strata = list(
                database.execute(
                    """SELECT temperature_bin, sio2_bin, COUNT(*)
                         FROM collection_unique_rows
                        WHERE rail = ? AND engine = ?
                        GROUP BY temperature_bin, sio2_bin
                        ORDER BY temperature_bin, sio2_bin""",
                    (rail, engine),
                )
            )
            quotas = {(temperature, sio2): count for temperature, sio2, count in strata}
        else:
            strata = list(
                database.execute(
                    """SELECT temperature_bin, sio2_bin, COUNT(*)
                         FROM collection_unique_rows
                        WHERE rail = ? AND engine = ?
                        GROUP BY temperature_bin, sio2_bin""",
                    (rail, engine),
                )
            )
            strata.sort(
                key=lambda row: hashlib.sha256(
                    f"{rail}|{engine}|{row[0]}|{row[1]}".encode()
                ).digest()
            )
            selected = strata[:limit]
            base, remainder = divmod(limit, len(selected))
            quotas = {
                (temperature, sio2): base + int(index < remainder)
                for index, (temperature, sio2, _count) in enumerate(selected)
            }
        for (temperature_bin, sio2_bin), quota in quotas.items():
            query = database.execute(
                """SELECT source, reference, temperature_K, sio2, composition,
                          species, residual_dex, flagged, temperature_bin, sio2_bin
                     FROM collection_unique_rows
                    WHERE rail = ? AND engine = ?
                      AND temperature_bin = ? AND sio2_bin IS ?
                    ORDER BY temperature_K, reference""",
                (rail, engine, temperature_bin, sio2_bin),
            )
            seed_text = f"{rail}|{engine}|{temperature_bin}|{sio2_bin}"
            seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
            generator = random.Random(seed)
            reservoir: list[tuple[object, ...]] = []
            for index, row in enumerate(query):
                if index < quota:
                    reservoir.append(row)
                else:
                    replacement = generator.randrange(index + 1)
                    if replacement < quota:
                        reservoir[replacement] = row
            samples.extend(
                {
                    "rail": rail,
                    "engine": engine,
                    "source": row[0],
                    "reference": row[1],
                    "temperature_K": row[2],
                    "sio2": row[3],
                    "composition": row[4],
                    "species": row[5],
                    "residual_dex": row[6],
                    "flagged": int(row[7]),
                    "clipped": float(row[6]) < -3 or float(row[6]) > 3,
                    "temperature_bin": row[8],
                    "sio2_bin": row[9],
                }
                for row in reservoir
            )
    return samples


def _collection_raw_tables(
    database: sqlite3.Connection,
    data: ScorePlotData,
    *,
    bin_width_K: float,
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> dict[str, list[dict[str, object]]]:
    panel_counts: dict[tuple[str, str], dict[str, object]] = {}
    for row in database.execute(
        """SELECT rail, engine, COUNT(*), MIN(temperature_K), MAX(temperature_K),
                  SUM(flagged), SUM(residual_dex < -3), SUM(residual_dex > 3)
             FROM collection_unique_rows GROUP BY rail, engine"""
    ):
        rail, engine, count, t_min, t_max, flagged, below, above = row
        panel_counts[(str(rail), str(engine))] = {
            "plotted_n": int(count),
            "temperature_min_K": t_min,
            "temperature_max_K": t_max,
            "flagged_n": int(flagged or 0),
            "clipped_low_n": int(below or 0),
            "clipped_high_n": int(above or 0),
        }
    panel_rows: list[dict[str, object]] = []
    rail_limits: dict[str, tuple[float, float]] = {}
    engines = _ordered_hero_engines(data.engines)
    for rail, label in COLLECTION_OBSERVABLES:
        rail_panels = [
            panel_counts.get((rail, engine), {}) for engine in engines
        ]
        temperatures = [
            float(value)
            for panel in rail_panels
            for key in ("temperature_min_K", "temperature_max_K")
            if (value := panel.get(key)) is not None
        ]
        if temperatures:
            low, high = min(temperatures), max(temperatures)
            padding = max(25.0, (high - low) * 0.02)
            rail_limits[rail] = (low - padding, high + padding)
        else:
            rail_limits[rail] = data.x_limits
        for engine in engines:
            panel = panel_counts.get((rail, engine), {})
            metric = _collection_metric(data, rail, engine)
            all_view = _view_metric(metric, "all")
            panel_rows.append(
                {
                    "rail": rail,
                    "observable": label,
                    "engine": engine,
                    "n": metric.get("n", 0),
                    "plotted_n": panel.get("plotted_n", 0),
                    "rms_dex": all_view.get("rms_dex", metric.get("rms_dex", "")),
                    "flagged_n": panel.get("flagged_n", 0),
                    "clipped_low_n": panel.get("clipped_low_n", 0),
                    "clipped_high_n": panel.get("clipped_high_n", 0),
                    "temperature_min_K": panel.get("temperature_min_K", ""),
                    "temperature_max_K": panel.get("temperature_max_K", ""),
                }
            )

    bins_by_key: dict[tuple[str, str, int], int] = {}
    for rail, engine, temperature_bin, count in database.execute(
        """SELECT rail, engine, temperature_bin, COUNT(*)
             FROM collection_unique_rows
            GROUP BY rail, engine, temperature_bin"""
    ):
        bins_by_key[(str(rail), str(engine), int(temperature_bin))] = int(count)
    bin_values: dict[tuple[str, str, int], dict[int, float]] = {}
    current_key: tuple[str, str, int] | None = None
    current_index = 0
    current_targets: set[int] = set()
    current_values: dict[int, float] = {}

    def save_quantiles(key: tuple[str, str, int], values: Mapping[int, float]) -> None:
        count = bins_by_key[key]

        def percentile(fraction: float) -> float:
            position = (count - 1) * fraction
            lower_index = math.floor(position)
            upper_index = math.ceil(position)
            lower_value = values[lower_index]
            upper_value = values[upper_index]
            return lower_value + (upper_value - lower_value) * (position - lower_index)

        bin_values[key] = {
            0: percentile(0.25),
            1: percentile(0.5),
            2: percentile(0.75),
        }

    for rail, engine, temperature_bin, residual in database.execute(
        """SELECT rail, engine, temperature_bin, residual_dex
             FROM collection_unique_rows
            ORDER BY rail, engine, temperature_bin, residual_dex"""
    ):
        key = (str(rail), str(engine), int(temperature_bin))
        if key != current_key:
            if current_key is not None:
                save_quantiles(current_key, current_values)
            current_key = key
            current_index = 0
            count = bins_by_key[key]
            current_targets = {
                math.floor((count - 1) * fraction)
                for fraction in (0.25, 0.5, 0.75)
            } | {
                math.ceil((count - 1) * fraction)
                for fraction in (0.25, 0.5, 0.75)
            }
            current_values = {}
        if current_index in current_targets:
            current_values[current_index] = float(residual)
        current_index += 1
    if current_key is not None:
        save_quantiles(current_key, current_values)

    temperature_bins: list[dict[str, object]] = []
    for key, quantiles in sorted(bin_values.items()):
        rail, engine, temperature_bin = key
        count = bins_by_key[key]
        temperature_bins.append(
            {
                "rail": rail,
                "engine": engine,
                "temperature_bin": temperature_bin,
                "temperature_center_K": (temperature_bin + 0.5) * bin_width_K,
                "n": count,
                "q1_dex": quantiles[0],
                "median_dex": quantiles[1],
                "q3_dex": quantiles[2],
            }
        )

    density_counts: Counter[tuple[str, str, int, int, int, int]] = Counter()
    hex_radius = 1 / 48
    sqrt_three = math.sqrt(3)
    for rail, engine, temperature, residual, flagged in database.execute(
        """SELECT rail, engine, temperature_K, residual_dex, flagged
             FROM collection_unique_rows"""
    ):
        x_low, x_high = rail_limits[str(rail)]
        value = float(residual)
        clipped = int(value < -3 or value > 3)
        if value < -3:
            q, r = 0, -1
        elif value > 3:
            q, r = 0, 1
        else:
            x = (float(temperature) - x_low) / (x_high - x_low)
            y = (value + 3) / 6
            fractional_q = (sqrt_three / 3 * x - y / 3) / hex_radius
            fractional_r = (2 / 3 * y) / hex_radius
            cube_x = fractional_q
            cube_z = fractional_r
            cube_y = -cube_x - cube_z
            rounded_x, rounded_y, rounded_z = (
                round(cube_x), round(cube_y), round(cube_z)
            )
            x_error = abs(rounded_x - cube_x)
            y_error = abs(rounded_y - cube_y)
            z_error = abs(rounded_z - cube_z)
            if x_error > y_error and x_error > z_error:
                rounded_x = -rounded_y - rounded_z
            elif y_error > z_error:
                rounded_y = -rounded_x - rounded_z
            else:
                rounded_z = -rounded_x - rounded_y
            q, r = int(rounded_x), int(rounded_z)
        density_counts[
            (str(rail), str(engine), q, r, int(flagged), clipped)
        ] += 1
    density_rows: list[dict[str, object]] = []
    for (rail, engine, q, r, flagged, clipped), count in sorted(density_counts.items()):
        x_low, x_high = rail_limits[rail]
        x_center = sqrt_three * hex_radius * (q + r / 2)
        y_center = 1.5 * hex_radius * r
        temperature_center = x_low + x_center * (x_high - x_low)
        residual_center = -3 + y_center * 6
        density_rows.append(
            {
                "rail": rail,
                "engine": engine,
                "x_bin": q,
                "y_bin": -1 if clipped and r < 0 else 36 if clipped else r,
                "temperature_center_K": temperature_center,
                "residual_center_dex": (
                    -3.0 if clipped and r < 0 else 3.0 if clipped else residual_center
                ),
                "flagged": flagged,
                "clipped": clipped,
                "row_count": count,
                "hex_x_radius_K": hex_radius * (x_high - x_low),
                "hex_y_radius_dex": hex_radius * 6,
            }
        )

    points: list[dict[str, object]] = []
    samples = _collection_stratified_sample(
        database,
        limit=COLLECTION_HTML_SAMPLE_LIMIT,
    )
    selected_rails = {rail for rail, _label in observables}
    small_panels = {
        (str(row["rail"]), str(row["engine"]))
        for row in panel_rows
        if int(row["plotted_n"]) <= 300
    }
    for rail, engine in sorted(small_panels):
        for row in database.execute(
            """SELECT source, reference, temperature_K, sio2, composition, species,
                      residual_dex, flagged
                 FROM collection_unique_rows WHERE rail = ? AND engine = ?
                ORDER BY temperature_K, reference""",
            (rail, engine),
        ):
            source, reference, temperature, sio2, composition, species, residual, flagged = row
            points.append(
                {
                    "rail": rail,
                    "engine": engine,
                    "source": source,
                    "reference": reference,
                    "temperature_K": temperature,
                    "sio2": sio2,
                    "composition": composition,
                    "species": species,
                    "residual_dex": residual,
                    "flagged": int(flagged),
                    "clipped": float(residual) < -3 or float(residual) > 3,
                }
            )
    return {
        "panels": [row for row in panel_rows if row["rail"] in selected_rails],
        "temperature_bins": [
            row for row in temperature_bins if row["rail"] in selected_rails
        ],
        "density": [row for row in density_rows if row["rail"] in selected_rails],
        "points": [row for row in points if row["rail"] in selected_rails],
        "sample": [row for row in samples if row["rail"] in selected_rails],
    }


_COLLECTION_TABLE_FIELDS = {
    "scorecard": (
        "rail", "observable", "primary_engine", "comparison_engine", "n_shared",
        "primary_rms_dex", "comparison_rms_dex", "high_n_shared",
        "high_primary_rms_dex", "high_comparison_rms_dex", "low_n_shared",
        "low_primary_rms_dex", "low_comparison_rms_dex",
    ),
    "panels": (
        "rail", "observable", "engine", "n", "plotted_n", "rms_dex",
        "flagged_n", "clipped_low_n", "clipped_high_n", "temperature_min_K",
        "temperature_max_K",
    ),
    "temperature_bins": (
        "rail", "engine", "temperature_bin", "temperature_center_K", "n",
        "q1_dex", "median_dex", "q3_dex",
    ),
    "temperature_density": (
        "rail", "engine", "x_bin", "y_bin", "temperature_center_K",
        "residual_center_dex", "flagged", "clipped", "row_count",
        "hex_x_radius_K", "hex_y_radius_dex",
    ),
    "temperature_points": (
        "rail", "engine", "source", "reference", "temperature_K", "sio2",
        "composition", "species", "residual_dex", "flagged", "clipped",
    ),
    "temperature_sample": (
        "rail", "engine", "source", "reference", "temperature_K", "sio2",
        "composition", "species", "residual_dex", "flagged", "clipped",
        "temperature_bin", "sio2_bin",
    ),
    "components": (
        "rail", "component", "engine", "shared_with", "n_shared", "rms_dex"
    ),
    "fingerprints": (
        "rail", "engine", "temperature_lower_K", "temperature_upper_K",
        "sio2_lower", "sio2_upper", "n", "sum_residual_dex",
        "sum_squared_residual_dex", "median_abs_dex", "band", "colour",
    ),
    "window_unknown": (
        "rail", "engine", "temperature_lower_K", "temperature_upper_K",
        "n", "sum_residual_dex", "sum_squared_residual_dex",
    ),
    "coverage": (
        "rail", "observable", "engine", "scored_n", "input_missing_n",
        "outside_domain_n", "not_output_n", "other_refused_n", "refused_n",
        "scored_percent",
    ),
    "progress": (
        "landing", "date", "rail", "engine", "rms_dex", "n", "exposed_n",
        "held_out_n", "held_out_rms_dex", "shared_with", "basis", "annotation",
    ),
    "exposure": (
        "rail", "engine", "exposed_n", "held_out_n", "held_out_rms_dex",
    ),
    "all_rows": (
        "rail", "engine", "view", "n", "rms_dex", "sd_dex",
        "median_abs_dex", "exposed_n", "held_out_n", "held_out_rms_dex",
        "source_disagreement_n", "source_disagreement_rms_dex", "unflagged_n",
        "unflagged_rms_dex",
    ),
}


def _write_collection_csvs(
    output_dir: Path,
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, Path]:
    table_dir = output_dir / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for name, fields in _COLLECTION_TABLE_FIELDS.items():
        path = table_dir / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tables.get(name, ()))
        files[f"table:{name}"] = path
    return files


def _read_collection_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _read_annotations_file(path: Path | None) -> tuple[dict[str, str], tuple[str, ...]]:
    if path is None:
        return {}, ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: annotations must be a JSON object")
    raw_annotations = payload.get("annotations", {})
    if not isinstance(raw_annotations, Mapping):
        raise ValueError(f"{path}: annotations must map landing sha/date to label")
    annotations = {
        str(key): str(value)
        for key, value in raw_annotations.items()
        if str(key).strip() and str(value).strip()
    }
    raw_fixes = payload.get("fix_status", ())
    if not isinstance(raw_fixes, Sequence) or isinstance(raw_fixes, (str, bytes)):
        raise ValueError(f"{path}: fix_status must be a list of caller-supplied lines")
    return annotations, tuple(str(line) for line in raw_fixes if str(line).strip())


def _write_collection_methods(
    data: ScorePlotData,
    output_dir: Path,
    *,
    openimcc_commit: str,
    fix_status_lines: Sequence[str],
) -> Path:
    path = output_dir / "methods.md"
    revision = str(data.stamp.get("revision") or "not recorded")
    default_pack = _openimcc_default_pack(data) or "not recorded in score metadata"
    fix_lines = "\n".join(f"- {line}" for line in fix_status_lines)
    if not fix_lines:
        fix_lines = "- No fix-status lines were supplied by the caller."
    path.write_text(
        "\n".join(
            (
                "# Hero chart methods",
                "",
                f"- Scorer revision: `{revision}`",
                f"- OpenIMCC score pin: `{openimcc_commit}`",
                f"- OpenIMCC default pack at the scored pin: `{default_pack}`",
                "- Error: `log10(predicted / measured)`, in dex; closer to zero is better.",
                "- Same-row comparisons include only observations scored once by both engines.",
                "- Progress uses stored shared-row RMS comparisons when history contains them; older history rows without comparisons use their stored all-row RMS.",
                f"- Temperature views split at {HERO_TEMPERATURE_FLOOR_K:g} K; "
                f"{HERO_TEMPERATURE_FLOOR_K:g} K belongs to the upper view.",
                f"- The interactive temperature and SiO₂ windows snap to {BATTERY_HERO_FINGERPRINT_TEMPERATURE_BIN_K:g} K and {BATTERY_HERO_FINGERPRINT_SIO2_BIN_WIDTH:g} mole-fraction fingerprint bin edges. RMS is exact for those selected bins: sqrt(sum(e²) / n); the page embeds n, sum(e), and sum(e²) for each occupied fingerprint cell.",
                "- Rows without a resolvable SiO₂ fraction contribute to exact RMS only when the full 0–1 composition range is selected.",
                f"- Interactive plotted points and tooltips use a stratified sample capped at {COLLECTION_HTML_SAMPLE_LIMIT} rows per rail and engine; static density plots use all scored rows.",
                "- Exposed rows match the exposure census or exposed-source list. Held-out rows are absent from both and from the pre-cutoff observation IDs; held-out RMS is displayed only when n ≥ 5.",
                f"- Source disagreement is identified by notice kind `{NoticeKind.SOURCE_DISAGREEMENT.value}` and shown separately from unflagged rows.",
                "",
                "## Caller-supplied fix status",
                "",
                fix_lines,
                "",
                "## Known caveats",
                "",
                "- Melt activity includes echo rows whose input is the analysed melt.",
                "- The scorer's all-numeric RMS includes uncalibrated KEMS p_partial rows excluded from plotted points under d-139.",
                "- Source-disagreement-flagged vapour rows are separated from unflagged rows in the all-rows table.",
                "- Fingerprint cells with fewer than three scored rows are blank.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def _save_collection_png(figure: Any, path: Path) -> None:
    figure.savefig(
        path,
        dpi=COLLECTION_EXPORT_DPI,
        bbox_inches="tight",
        metadata={"Software": "battery_hero_plot"},
    )
    plt.close(figure)


def _draw_scorecard_png(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> None:
    colors = _hero_engine_colors(
        tuple({str(row["primary_engine"]) for row in rows} | {str(row["comparison_engine"]) for row in rows})
    ) if rows else {}
    max_rows = max(
        (
            sum(row["rail"] == rail for row in rows)
            for rail, _label in observables
        ),
        default=1,
    )
    figure, axes = plt.subplots(
        1,
        len(observables),
        figsize=(max(4.2, 3.6 * len(observables)), max(3.8, 0.48 * max_rows + 1.5)),
        squeeze=False,
        sharex=True,
    )
    plot_values = _scorecard_plot_values(rows)
    all_values = [
        float(value)
        for values in plot_values.values()
        for value in values
        if value is not None
    ]
    right = max(0.6, max(all_values, default=0.5) * 1.18)
    for axis, (rail, label) in zip(axes.flat, observables, strict=True):
        selected = [row for row in rows if row["rail"] == rail]
        partners = list(dict.fromkeys(str(row["comparison_engine"]) for row in selected))
        primaries = list(dict.fromkeys(str(row["primary_engine"]) for row in selected))
        comparisons = [(primary, partner) for primary in primaries for partner in partners]
        comparisons = [
            pair for pair in comparisons
            if any(row["primary_engine"] == pair[0] and row["comparison_engine"] == pair[1] for row in selected)
        ]
        axis.axvspan(0, 0.5, color=GOAL_COLOUR, alpha=0.12, zorder=0)
        axis.axvline(0, color="#263238", linewidth=1.1, zorder=1)
        for index, (primary, partner) in enumerate(comparisons):
            row = next(
                item for item in selected
                if item["primary_engine"] == primary and item["comparison_engine"] == partner
            )
            values = plot_values[(rail, primary, partner)]
            for primary_value, partner_value, offset, alpha in (
                (values[0], values[1], 0.13, 1.0),
                (values[2], values[3], -0.13, 0.38),
            ):
                if primary_value is None or partner_value is None:
                    continue
                y = index + offset
                axis.plot(
                    [float(primary_value), float(partner_value)], [y, y],
                    color="#64748b", linewidth=0.65, alpha=alpha, zorder=2,
                )
                axis.scatter(
                    [float(primary_value)], [y], marker="o", s=21,
                    color=colors[primary], alpha=alpha, zorder=3,
                )
                axis.scatter(
                    [float(partner_value)], [y], marker="s", s=21,
                    color=colors[partner], alpha=alpha, zorder=3,
                )
            counts = f"n={row['n_shared']}"
            high_n = row.get("high_n_shared")
            low_n = row.get("low_n_shared")
            if high_n:
                counts += f" · ≥{HERO_TEMPERATURE_FLOOR_K:g} {high_n}"
            if low_n:
                counts += f" · <{HERO_TEMPERATURE_FLOOR_K:g} {low_n}"
            axis.text(right * 0.995, index, counts, ha="right", va="center", color="#64748b", fontsize=5.3)
        axis.set_title(label, loc="left", fontsize=8)
        axis.set_yticks(range(len(comparisons)))
        axis.set_yticklabels(
            [f"{primary} vs {partner}" for primary, partner in comparisons],
            fontsize=5.5,
        )
        axis.invert_yaxis()
        axis.set_xlim(0, right)
        axis.grid(axis="x", color="#94a3b8", alpha=0.2, linewidth=0.5)
        axis.tick_params(axis="x", labelsize=6)
        axis.set_xlabel("Shared-row RMS error (dex)", fontsize=6.5)
    engine_handles = [
        Line2D([0], [0], marker="o", linestyle="", color=color, label=engine, markersize=4)
        for engine, color in colors.items()
    ]
    temp_handles = [
        Line2D(
            [0], [0], marker="o", linestyle="", color="#334155", alpha=1,
            label=f"≥{HERO_TEMPERATURE_FLOOR_K:g} K", markersize=4
        ),
        Line2D(
            [0], [0], marker="o", linestyle="", color="#334155", alpha=0.38,
            label=f"<{HERO_TEMPERATURE_FLOOR_K:g} K", markersize=4
        ),
    ]
    figure.suptitle("Which engine is closest to measurement for each observable?", fontsize=11, y=0.99)
    figure.text(0.5, 0.955, "Error = log10(predicted / measured), dex · closer to 0 is better · green band = within ±0.5 dex", ha="center", fontsize=7)
    if engine_handles:
        figure.legend(handles=engine_handles + temp_handles, loc="upper center", bbox_to_anchor=(0.5, 0.925), ncol=min(8, len(engine_handles) + 2), frameon=False, fontsize=6)
    figure.subplots_adjust(left=0.08, right=0.99, top=0.82, bottom=0.14, wspace=0.32)
    _save_collection_png(figure, path)


def _draw_temperature_png(
    path: Path,
    rail: str,
    panel_rows: Sequence[Mapping[str, str]],
    bin_rows: Sequence[Mapping[str, str]],
    density_rows: Sequence[Mapping[str, str]],
    point_rows: Sequence[Mapping[str, str]],
) -> None:
    colors = _hero_engine_colors(tuple(row["engine"] for row in panel_rows))
    engines = tuple(engine for engine in colors if any(row["engine"] == engine for row in panel_rows))
    panel_by_engine = {row["engine"]: row for row in panel_rows}
    limits = [
        _finite_float(panel_by_engine[engine].get(key))
        for engine in engines
        for key in ("temperature_min_K", "temperature_max_K")
    ]
    limits = [value for value in limits if value is not None]
    low, high = (min(limits), max(limits)) if limits else (0.0, 1.0)
    if low == high:
        low -= 50
        high += 50
    figure, axes = plt.subplots(
        max(1, len(engines)),
        1,
        figsize=(12.2, max(3.0, 2.0 * len(engines))),
        squeeze=False,
        sharex=True,
        sharey=True,
    )
    axes_flat = list(axes.flat)
    for axis, engine in zip(axes_flat, engines, strict=False):
        panel = panel_by_engine[engine]
        color = colors[engine]
        n = int(panel.get("n") or 0)
        plotted_n = int(panel.get("plotted_n") or 0)
        rms = panel.get("rms_dex") or "unavailable"
        flagged = int(panel.get("flagged_n") or 0)
        clipped_low = int(panel.get("clipped_low_n") or 0)
        clipped_high = int(panel.get("clipped_high_n") or 0)
        count_label = f"{plotted_n} plotted of {n} scored"
        axis.axhspan(-0.5, 0.5, color=GOAL_COLOUR, alpha=0.12, zorder=0)
        axis.axhline(0, color="#263238", linewidth=1.15, zorder=1)
        axis.axvline(
            float(HERO_TEMPERATURE_FLOOR_K),
            color="#64748b",
            linestyle="--",
            linewidth=0.8,
            zorder=1,
        )
        selected_bins = [row for row in bin_rows if row["rail"] == rail and row["engine"] == engine]
        if selected_bins:
            x_values = [float(row["temperature_center_K"]) for row in selected_bins]
            medians = [float(row["median_dex"]) for row in selected_bins]
            q1 = [float(row["q1_dex"]) for row in selected_bins]
            q3 = [float(row["q3_dex"]) for row in selected_bins]
            axis.fill_between(x_values, q1, q3, color=color, alpha=0.2, linewidth=0)
            axis.plot(x_values, medians, color=color, linewidth=1.2, zorder=3)
        if plotted_n <= 300:
            selected_points = [
                row for row in point_rows
                if row["rail"] == rail and row["engine"] == engine and row["clipped"].casefold() != "true"
            ]
            for is_flagged, alpha in ((False, 0.35), (True, 0.75)):
                points = [row for row in selected_points if row["flagged"] == str(int(is_flagged))]
                if points:
                    axis.scatter(
                        [float(row["temperature_K"]) for row in points],
                        [max(-3, min(3, float(row["residual_dex"]))) for row in points],
                        s=4,
                        marker="o",
                        color=_lighten_colour(color, 0.35) if is_flagged else color,
                        alpha=alpha,
                        linewidths=0,
                        zorder=2,
                    )
        else:
            selected_density = [row for row in density_rows if row["rail"] == rail and row["engine"] == engine]
            for is_flagged in (False, True):
                bins = [
                    row for row in selected_density
                    if row["flagged"] == str(int(is_flagged))
                    and row["clipped"] != "1"
                ]
                if not bins:
                    continue
                base_colour = _lighten_colour(color, 0.35) if is_flagged else color
                ramp = LinearSegmentedColormap.from_list(
                    f"hero-{rail}-{engine}-{is_flagged}",
                    [_lighten_colour(base_colour, 0.55), base_colour],
                )
                counts = [int(row["row_count"]) for row in bins]
                polygons = []
                for row in bins:
                    center_x = float(row["temperature_center_K"])
                    center_y = float(row["residual_center_dex"])
                    radius_x = float(row["hex_x_radius_K"])
                    radius_y = float(row["hex_y_radius_dex"])
                    polygons.append(
                        [
                            (
                                center_x + radius_x * math.cos(math.radians(30 + 60 * index)),
                                center_y + radius_y * math.sin(math.radians(30 + 60 * index)),
                            )
                            for index in range(6)
                        ]
                    )
                collection = PolyCollection(
                    polygons,
                    cmap=ramp,
                    alpha=0.82 if is_flagged else 0.92,
                    linewidths=0,
                    edgecolors="none",
                    zorder=2 if not is_flagged else 3,
                )
                collection.set_array(counts)
                axis.add_collection(collection)
            for clipped_side, marker, y_bin in (("low", "v", "-1"), ("high", "^", "36")):
                for is_flagged, alpha in ((False, 0.9), (True, 0.75)):
                    bins = [
                        row for row in selected_density
                        if row["clipped"] == "1"
                        and row["y_bin"] == y_bin
                        and row["flagged"] == str(int(is_flagged))
                    ]
                    if bins:
                        marker_colour = _lighten_colour(color, 0.35) if is_flagged else color
                        axis.scatter(
                            [float(row["temperature_center_K"]) for row in bins],
                            [-3.0 if clipped_side == "low" else 3.0] * len(bins),
                            s=6,
                            marker=marker,
                            color=marker_colour,
                            alpha=alpha,
                            linewidths=0,
                            zorder=4,
                        )
        outliers = [
            row for row in point_rows
            if row["rail"] == rail and row["engine"] == engine and row["clipped"].casefold() == "true"
        ]
        for clipped_side, marker in (("low", "v"), ("high", "^")):
            selected_outliers = [
                row for row in outliers
                if (float(row["residual_dex"]) < -3) == (clipped_side == "low")
            ]
            if selected_outliers:
                axis.scatter(
                    [float(row["temperature_K"]) for row in selected_outliers],
                    [-3.0 if clipped_side == "low" else 3.0] * len(selected_outliers),
                    s=16,
                    marker=marker,
                    color=color,
                    linewidths=0,
                    zorder=4,
                )
        axis.set_xlim(low, high)
        axis.set_ylim(*Y_LIMITS)
        axis.set_ylabel("Signed error (dex)", fontsize=7)
        axis.grid(color="#94a3b8", alpha=0.17, linewidth=0.45)
        axis.tick_params(labelsize=6)
        axis.set_title(
            f"{engine} · {count_label} · all-scored RMS {rms} dex · {flagged} extrapolated · {clipped_low + clipped_high} beyond ±3 dex",
            loc="left",
            fontsize=7.5,
        )
    axes_flat[-1].set_xlabel("Temperature (K)", fontsize=7)
    if axes_flat:
        axes_flat[0].text(0.995, 0.94, "association data floor", transform=axes_flat[0].transAxes, ha="right", va="top", fontsize=5.5, color="#64748b")
    observable = dict(COLLECTION_OBSERVABLES)[rail]
    figure.suptitle(f"How does {observable.lower()} error change with temperature?", fontsize=10, y=0.995)
    figure.text(0.5, 0.978, "Error = log10(predicted / measured), dex · closer to 0 is better · green band = within ±0.5 dex", ha="center", fontsize=6.5)
    figure.subplots_adjust(left=0.09, right=0.985, top=0.94, bottom=0.08, hspace=0.55)
    _save_collection_png(figure, path)


def _lighten_colour(colour: str, fraction: float) -> tuple[float, float, float]:
    red, green, blue = matplotlib.colors.to_rgb(colour)
    return tuple(channel + (1 - channel) * fraction for channel in (red, green, blue))


def _draw_component_png(
    path: Path,
    rail: str,
    rows: Sequence[Mapping[str, str]],
) -> None:
    selected = [row for row in rows if row["rail"] == rail]
    engines = tuple(
        engine for engine in _HERO_ENGINE_ORDER
        if any(row["engine"] == engine for row in selected)
    )
    colors = _hero_engine_colors(engines)
    by_pair: dict[tuple[str, str], dict[str, Mapping[str, str]]] = defaultdict(dict)
    for row in selected:
        by_pair[(row["component"], row["shared_with"])][row["engine"]] = row
    pairs = sorted(
        by_pair,
        key=lambda pair: (
            -int(by_pair[pair].get("openimcc", {}).get("n_shared") or 0),
            pair[0],
            pair[1],
        ),
    )
    fig_height = max(3.2, 0.30 * len(pairs) + 1.4)
    figure, axis = plt.subplots(figsize=(11.5, fig_height))
    all_values = [
        float(row["rms_dex"])
        for row in selected
        if row.get("rms_dex") not in (None, "")
    ]
    right = max(0.65, max(all_values, default=0.5) * 1.15)
    axis.axvspan(0, 0.5, color=GOAL_COLOUR, alpha=0.12, zorder=0)
    axis.axvline(0, color="#263238", linewidth=1.0, zorder=1)
    for pair_index, pair in enumerate(pairs):
        pair_rows = by_pair[pair]
        primary = _finite_float(pair_rows.get("openimcc", {}).get("rms_dex"))
        partner = _finite_float(pair_rows.get(pair[1], {}).get("rms_dex"))
        if primary is None or partner is None:
            continue
        shared_n = pair_rows["openimcc"]["n_shared"]
        axis.plot(
            [primary, partner],
            [pair_index, pair_index],
            color="#64748b",
            linewidth=0.75,
            zorder=2,
        )
        primary_artist = axis.scatter(
            [primary], [pair_index], marker="o", s=24,
            color=colors["openimcc"], alpha=0.95, linewidths=0, zorder=3,
        )
        primary_artist.set_gid(f"component-pair:{pair[0]}:{pair[1]}:openimcc")
        partner_artist = axis.scatter(
            [partner], [pair_index], marker="s", s=22,
            color=colors[pair[1]], alpha=0.95, linewidths=0, zorder=3,
        )
        partner_artist.set_gid(f"component-pair:{pair[0]}:{pair[1]}:{pair[1]}")
        axis.text(
            right * 0.995, pair_index, f"n={shared_n}",
            ha="right", va="center", color="#64748b", fontsize=5.5,
        )
    axis.set_yticks(range(len(pairs)))
    axis.set_yticklabels([f"{component} · {partner}" for component, partner in pairs], fontsize=5.8)
    axis.invert_yaxis()
    axis.set_xlim(0, right)
    axis.set_xlabel("RMS error on rows shared with OpenIMCC (dex)", fontsize=7)
    axis.grid(axis="x", color="#94a3b8", alpha=0.2, linewidth=0.5)
    axis.tick_params(axis="x", labelsize=6)
    title = "species" if rail == Rail.VAPOUR.value else "oxide"
    observable = "vapour pressure" if rail == Rail.VAPOUR.value else "melt activity"
    figure.suptitle(f"Which engine is closest by {title} for {observable}?", fontsize=10, y=0.99)
    figure.text(0.5, 0.96, "Shared-row RMS error = log10(predicted / measured), dex · closer to 0 is better · green band = within ±0.5 dex", ha="center", fontsize=6.5)
    handles = [
        Line2D([0], [0], marker="o", linestyle="", color=color, label=engine, markersize=4)
        for engine, color in colors.items()
    ]
    if handles:
        figure.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.935), ncol=min(6, len(handles)), frameon=False, fontsize=6)
    figure.subplots_adjust(left=0.11, right=0.99, top=0.88, bottom=0.12)
    _save_collection_png(figure, path)


def _draw_fingerprint_png(
    path: Path,
    rail: str,
    rows: Sequence[Mapping[str, str]],
    engines: Sequence[str],
) -> None:
    colors = _hero_engine_colors(engines)
    figure, axes = plt.subplots(
        1,
        max(1, len(engines)),
        figsize=(max(6.0, 3.2 * len(engines)), 4.2),
        squeeze=False,
        sharex=True,
        sharey=True,
    )
    figure.subplots_adjust(left=0.07, right=0.99, top=0.87, bottom=0.14, wspace=0.18)
    rail_rows = [row for row in rows if row["rail"] == rail]
    temperatures = [
        _finite_float(row.get(key))
        for row in rail_rows
        for key in ("temperature_lower_K", "temperature_upper_K")
    ]
    temperatures = [value for value in temperatures if value is not None]
    limits = (min(temperatures), max(temperatures)) if temperatures else (0.0, 1.0)
    x_limits = (0.0, 1.0)
    handles = _fingerprint_legend_handles()
    for axis, engine in zip(axes.flat, engines, strict=False):
        cells = [
            {
                **row,
                "temperature_lower_K": _finite_float(row.get("temperature_lower_K")),
                "temperature_upper_K": _finite_float(row.get("temperature_upper_K")),
                "sio2_lower": _finite_float(row.get("sio2_lower")),
                "sio2_upper": _finite_float(row.get("sio2_upper")),
                "n": int(row.get("n") or 0),
            }
            for row in rail_rows
            if row["engine"] == engine
        ]
        _draw_hero_sweet_spot(
            axis,
            engine,
            cells,
            x_limits=x_limits,
            temperature_limits=limits,
            export_dpi=COLLECTION_EXPORT_DPI,
        )
        axis.set_title(engine, loc="left", fontsize=6.5)
    observable = dict(COLLECTION_OBSERVABLES)[rail]
    figure.suptitle(f"Where does {observable.lower()} meet the target?", fontsize=10, y=0.99)
    figure.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.94), ncol=3, frameon=False, fontsize=6)
    _save_collection_png(figure, path)


def _fingerprint_legend_handles() -> list[matplotlib.patches.Patch]:
    good_limit, okay_limit = BATTERY_HERO_FINGERPRINT_BAND_BOUNDARIES
    good_label = format(good_limit.normalize(), "f")
    okay_label = format(okay_limit.normalize(), "f")
    labels = {
        "good": f"within {good_label} dex — good",
        "okay": f"{good_label}–{okay_label} dex — okay",
        "needs work": f"over {okay_label} dex — needs work",
    }
    return [
        matplotlib.patches.Patch(
            facecolor=colour,
            edgecolor="none",
            label=labels[name],
        )
        for name, colour in FINGERPRINT_BANDS
    ]


def _draw_coverage_png(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> None:
    colors = {
        "scored_n": "#405b74",
        "input_missing_n": "#b9c2cc",
        "outside_domain_n": "#8796a5",
        "not_output_n": "#68798b",
        "other_refused_n": "#d89b79",
    }
    labels = {
        "scored_n": "scored",
        "input_missing_n": "input missing",
        "outside_domain_n": "outside domain",
        "not_output_n": "not an output of this engine",
        "other_refused_n": "other refused",
    }
    fig, axes = plt.subplots(
        len(observables), 1,
        figsize=(11.5, 2.1 * len(observables)),
        squeeze=False,
    )
    for axis, (rail, observable) in zip(axes.flat, observables, strict=True):
        selected = [row for row in rows if row["rail"] == rail]
        y_positions = list(range(len(selected)))
        left = [0] * len(selected)
        for field_name in colors:
            values = [int(row.get(field_name) or 0) for row in selected]
            axis.barh(y_positions, values, left=left, color=colors[field_name], height=0.68, label=labels[field_name])
            left = [offset + value for offset, value in zip(left, values, strict=True)]
        for index, row in enumerate(selected):
            percent = row.get("scored_percent")
            if percent:
                axis.text(left[index] + max(left, default=0) * 0.01, index, f"{percent}% scored", va="center", fontsize=5.5, color="#334155")
        axis.set_yticks(y_positions)
        axis.set_yticklabels([row["engine"] for row in selected], fontsize=6)
        axis.invert_yaxis()
        axis.set_title(observable, loc="left", fontsize=7.5)
        axis.set_xlabel("Rows", fontsize=6)
        axis.grid(axis="x", color="#94a3b8", alpha=0.2, linewidth=0.45)
        axis.tick_params(axis="x", labelsize=5.5)
    handles, legend_labels = axes.flat[0].get_legend_handles_labels()
    fig.suptitle("Can each engine predict the rows in this benchmark?", fontsize=10, y=0.995)
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.5, 0.978), ncol=5, frameon=False, fontsize=5.5)
    fig.subplots_adjust(left=0.14, right=0.99, top=0.95, bottom=0.06, hspace=0.58)
    _save_collection_png(fig, path)


def _draw_progress_png(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> None:
    engine_names = tuple(
        engine for engine in _HERO_ENGINE_ORDER
        if any(row["engine"] == engine for row in rows)
    )
    colors = _hero_engine_colors(engine_names)
    figure, axes = plt.subplots(
        len(observables), 1,
        figsize=(12, 2.25 * len(observables)),
        squeeze=False,
        sharey=True,
    )
    max_rms = max(
        [
            _finite_float(row.get("rms_dex")) or 0
            for row in rows
        ]
        + [
            _finite_float(row.get("held_out_rms_dex")) or 0
            for row in rows
            if int(row.get("held_out_n") or 0) >= HERO_MINIMUM_REPORT_N
        ]
        + [1.0]
    )
    for axis, (rail, observable) in zip(axes.flat, observables, strict=True):
        selected = [row for row in rows if row["rail"] == rail]
        axis.axhspan(0, 0.5, color=GOAL_COLOUR, alpha=0.12, zorder=0)
        axis.axhline(0, color="#263238", linewidth=0.8)
        for engine in engine_names:
            engine_rows = [row for row in selected if row["engine"] == engine and row.get("rms_dex")]
            engine_rows.sort(key=lambda row: row.get("date") or "")
            dates: list[float] = []
            values: list[float] = []
            held_dates: list[float] = []
            held_values: list[float] = []
            for row in engine_rows:
                date = _history_date({"commit_date": row.get("date")})
                rms = _finite_float(row.get("rms_dex"))
                if date is None or rms is None:
                    continue
                dates.append(date)
                values.append(rms)
                held_n = int(row.get("held_out_n") or 0)
                held_rms = _finite_float(row.get("held_out_rms_dex"))
                if held_n >= HERO_MINIMUM_REPORT_N and held_rms is not None:
                    held_dates.append(date)
                    held_values.append(held_rms)
                if row.get("annotation"):
                    axis.annotate(
                        row["annotation"],
                        (date, rms),
                        textcoords="offset points",
                        xytext=(3, 5),
                        fontsize=5,
                        color="#334155",
                    )
            if dates:
                axis.plot(dates, values, color=colors[engine], marker="o", markersize=3, linewidth=1.0, label=engine)
            if held_dates:
                axis.scatter(held_dates, held_values, color=colors[engine], marker="D", s=20, zorder=4)
        axis.set_title(observable, loc="left", fontsize=7.5)
        axis.set_ylabel("Shared-row RMS (dex)", fontsize=6)
        axis.set_ylim(0, max(0.6, max_rms * 1.08))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        axis.tick_params(axis="x", rotation=22, labelsize=5.5)
        axis.tick_params(axis="y", labelsize=5.5)
        axis.grid(color="#94a3b8", alpha=0.2, linewidth=0.45)
    axes.flat[-1].set_xlabel("Green landing date", fontsize=6)
    handles = [
        Line2D([0], [0], marker="o", linestyle="-", color=colors[engine], label=engine, markersize=3)
        for engine in engine_names
    ]
    if handles:
        handles.append(Line2D([0], [0], marker="D", linestyle="", color="#334155", label="held-out RMS (n ≥ 5)", markersize=4))
        figure.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=min(7, len(handles)), frameon=False, fontsize=6)
    figure.suptitle("Is RMS error improving across green landings?", fontsize=10, y=0.999)
    figure.text(0.5, 0.987, "Lower shared-row RMS is better · green band = within 0.5 dex · diamonds show held-out RMS when n ≥ 5", ha="center", fontsize=6)
    figure.subplots_adjust(left=0.11, right=0.99, top=0.95, bottom=0.06, hspace=0.62)
    _save_collection_png(figure, path)


def _draw_exposure_png(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> None:
    exposure_rows = [row for row in rows if row.get("exposed_n") or row.get("held_out_n")]
    figure, axes = plt.subplots(
        len(observables), 1,
        figsize=(5.0, max(1.8, 1.4 * len(observables))),
        squeeze=False,
        sharex=True,
    )
    for axis, (rail, observable) in zip(axes.flat, observables, strict=True):
        selected = [row for row in exposure_rows if row["rail"] == rail]
        y = list(range(len(selected)))
        exposed = [int(row.get("exposed_n") or 0) for row in selected]
        held = [int(row.get("held_out_n") or 0) for row in selected]
        axis.barh(y, exposed, color="#607d8b", height=0.35, label="exposed · in-sample")
        axis.barh([value + 0.36 for value in y], held, color="#334155", height=0.35, label="held-out · out-of-sample")
        for index, (exposed_n, held_n) in enumerate(zip(exposed, held, strict=True)):
            axis.text(max(exposed_n, held_n) + 0.3, index + 0.18, f"{exposed_n} / {held_n}", va="center", fontsize=5.5)
        axis.set_yticks([value + 0.18 for value in y])
        axis.set_yticklabels([row["engine"] for row in selected], fontsize=7)
        axis.invert_yaxis()
        axis.set_title(observable, loc="left", fontsize=7.5)
        axis.grid(axis="x", color="#94a3b8", alpha=0.2, linewidth=0.45)
        axis.tick_params(axis="x", labelsize=6.5)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    if not exposure_rows:
        axes.flat[0].text(0.5, 0.5, "Exposure inputs were not supplied", transform=axes.flat[0].transAxes, ha="center", va="center", fontsize=8)
    figure.suptitle("Are the scored rows exposed or held out?", fontsize=10, y=0.995)
    if handles:
        figure.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.976), ncol=2, frameon=False, fontsize=6.5)
    figure.text(0.98, 0.015, "Exposed is in-sample · held-out is the out-of-sample check", ha="right", fontsize=6, color="#475569")
    figure.subplots_adjust(left=0.25, right=0.98, top=0.93, bottom=0.07, hspace=0.62)
    _save_collection_png(figure, path)


def _html_table(rows: Sequence[Mapping[str, str]]) -> str:
    if not rows:
        return "<p>No rows.</p>"
    columns = tuple(rows[0])
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<div class=table-wrap><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>"


def _write_collection_html(
    path: Path,
    *,
    chart_files: Mapping[str, Path],
    table_rows: Mapping[str, Sequence[Mapping[str, str]]],
    engine_names: Sequence[str],
    observables: Sequence[tuple[str, str]] = COLLECTION_OBSERVABLES,
) -> None:
    def section(
        chart_id: str,
        title: str,
        image_key: str,
        tables: Sequence[tuple[str, Sequence[Mapping[str, str]]]],
        deferred_tables: Sequence[tuple[str, str, str]] = (),
    ) -> str:
        image_path = chart_files[image_key]
        image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        table_markup = "".join(
            f"<h4>{html.escape(label)}</h4>{_html_table(rows)}"
            for label, rows in tables
        )
        table_markup += "".join(
            f'<h4>{html.escape(label)}</h4><div class="table-wrap" '
            f'data-deferred-table="{html.escape(table_kind)}" '
            f'data-rail="{html.escape(rail)}"></div>'
            for label, table_kind, rail in deferred_tables
        )
        return (
            f'<section class="chart" id="{html.escape(chart_id)}">'
            f"<h2>{html.escape(title)}</h2>"
            f'<img src="data:image/png;base64,{image}" alt="{html.escape(title)}">'
            f"<details><summary>Show chart data</summary>{table_markup}</details></section>"
        )

    with path.open("w", encoding="utf-8") as output:
        output.write(
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Battery hero charts</title><style>"
            ":root{--page:#fbfcfe;--surface:#fff;--ink:#18212b;--muted:#526171;--grid:#d8e0e8;--link:#1b5f9e}"
            ":root[data-theme=dark]{--page:#111820;--surface:#19232d;--ink:#edf3f8;--muted:#aebbc8;--grid:#344351;--link:#8fc6ff}"
            "*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif}"
            "main{max-width:1440px;margin:auto;padding:20px}h1{font-size:clamp(1.5rem,4vw,2.25rem);margin:.2rem 0}h2{font-size:1.2rem;margin:.3rem 0 .8rem}h3{font-size:1rem}"
            ".chart,.interactive{background:var(--surface);border:1px solid var(--grid);border-radius:12px;padding:16px;margin:16px 0;overflow:hidden}.collection-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:0 14px}.collection-grid>.chart{grid-column:1/-1}#chart-a{grid-column:1}#chart-g{grid-column:2;grid-row:1}"
            ".chart img{display:block;width:100%;height:auto}.table-wrap{overflow:auto;max-height:55vh}table{border-collapse:collapse;width:100%;font-size:.78rem;white-space:nowrap}th,td{border-bottom:1px solid var(--grid);padding:5px 8px;text-align:left}th{position:sticky;top:0;background:var(--surface)}details{margin-top:12px}summary{cursor:pointer;color:var(--link);font-weight:600}"
            ".controls{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:end}.controls label{display:grid;gap:3px;color:var(--muted);font-size:.82rem}.controls input,.controls select,.controls button{min-height:38px;padding:6px 8px;border:1px solid var(--grid);border-radius:6px;background:var(--surface);color:var(--ink)}"
            ".range{display:flex;gap:5px}.toggles{display:flex;flex-wrap:wrap;gap:8px 14px;margin:12px 0}.toggles label{display:flex;align-items:center;gap:5px;font-size:.84rem}.toggles input{min-height:0}canvas{display:block;width:100%;height:390px;border:1px solid var(--grid);border-radius:6px;margin:10px 0 20px;touch-action:pan-y}.tooltip{position:fixed;z-index:5;display:none;max-width:min(340px,90vw);padding:9px 11px;background:var(--ink);color:var(--page);border-radius:6px;font-size:.78rem;pointer-events:none;white-space:pre-wrap}"
            "@media(max-width:700px){main{padding:10px}.chart,.interactive{padding:10px;border-radius:8px}.collection-grid{grid-template-columns:minmax(0,1fr)}#chart-a,#chart-g{grid-column:1;grid-row:auto}.chart{overflow-x:auto}#chart-a img,[id^=chart-d-] img{width:760px;max-width:none}.chart:not(#chart-a):not([id^=chart-d-]) img{width:100%;max-width:100%}.controls{align-items:stretch}.controls label{flex:1 1 130px}.controls input,.controls select{width:100%}canvas{height:320px}table{font-size:.7rem}}"
            "</style></head><body><main><header><button id=\"theme-toggle\" type=\"button\">Toggle light/dark</button>"
            "<h1>Battery benchmark: which engine fits your application?</h1>"
            "<p>Error = log10(predicted / measured), dex · closer to 0 is better · green band = within ±0.5 dex</p></header>"
        )
        output.write('<div class="collection-grid">')
        output.write(section("chart-a", "A · Which engine is closest to measurement?", "chart:A", [("Scorecard", table_rows["scorecard"])]))
        for rail, label in observables:
            output.write(
                section(
                    f"chart-b-{rail}",
                    f"B · How does {label.lower()} error change with temperature?",
                    f"chart:B:{rail}",
                    (
                        ("Panel counts and clipping", [row for row in table_rows["panels"] if row["rail"] == rail]),
                        ("Binned median and interquartile range", [row for row in table_rows["temperature_bins"] if row["rail"] == rail]),
                    ),
                    (("Stratified sample for the point layer and tooltips", "samples", rail),),
                )
            )
        for rail, title, label in (
            (rail, "Species", "vapour pressure")
            if rail == Rail.VAPOUR.value
            else (rail, "Oxide", "melt activity")
            for rail in (Rail.VAPOUR.value, Rail.MELT_ACTIVITY.value)
            if rail in {selected for selected, _label in observables}
        ):
            output.write(
                section(
                    f"chart-c-{rail}",
                    f"C · Which engine is closest by {title.lower()} for {label}?",
                    f"chart:C:{rail}",
                    (("Shared-row RMS", [row for row in table_rows["components"] if row["rail"] == rail]),),
                )
            )
        for rail, label in observables:
            output.write(
                section(
                    f"chart-d-{rail}",
                    f"D · Where does {label.lower()} meet the target?",
                    f"chart:D:{rail}",
                    (),
                    (("Fingerprint cells", "fingerprints", rail),),
                )
            )
        output.write(section("chart-e", "E · Can each engine predict the benchmark rows?", "chart:E", [("Coverage", table_rows["coverage"])]))
        output.write(section("chart-f", "F · Is shared-row RMS improving across green landings?", "chart:F", [("Progress", table_rows["progress"])]))
        output.write(section("chart-g", "G · Are scored rows exposed or held out?", "chart:G", [("Exposure", table_rows["exposure"])]))
        output.write("</div>")
        output.write(
            "<section class=\"interactive\" id=\"application-window\"><h2>Explore your application window</h2>"
            f"<p>Points and row tooltips use a stratified sample, capped at {COLLECTION_HTML_SAMPLE_LIMIT} rows per rail and engine. Hover a sampled point or fingerprint cell for row source, T, composition, and residual.</p><p id=\"window-rms\" aria-live=\"polite\">Window RMS uses exact binned sums.</p><div class=\"controls\">"
            "<label>Observable<select id=\"observable-select\"></select></label>"
            "<label>Temperature range (K)<span class=\"range\"><input id=\"temperature-min\" type=\"number\" aria-label=\"Minimum temperature\"><input id=\"temperature-max\" type=\"number\" aria-label=\"Maximum temperature\"></span></label>"
            "<label>SiO₂ range<span class=\"range\"><input id=\"sio2-min\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" aria-label=\"Minimum SiO2\"><input id=\"sio2-max\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" aria-label=\"Maximum SiO2\"></span></label>"
            "<label>Fingerprint engine<select id=\"fingerprint-engine\"></select></label></div>"
            "<div class=\"toggles\" id=\"engine-toggles\"></div><h3>Signed residual versus temperature</h3><canvas id=\"error-canvas\"></canvas>"
            "<h3>Temperature × SiO₂ fingerprint</h3><canvas id=\"fingerprint-canvas\"></canvas></section>"
        )
        output.write(
            "<details><summary>Rows with unavailable SiO₂ (included only for the full 0–1 range)</summary>"
            + _html_table(table_rows["window_unknown"])
            + "</details>"
        )
        output.write(
            "<section class=\"chart\"><h2>All rows by rail, engine, and temperature view</h2>"
            "<details><summary>Show all-rows table</summary>"
            + _html_table(table_rows["all_rows"])
            + "</details></section><div id=\"tooltip\" class=\"tooltip\" role=\"tooltip\"></div>"
        )
        output.write('<script type="application/json" id="row-data">')
        output.write(
            json.dumps(
                table_rows["temperature_sample"],
                separators=(",", ":"),
            ).replace("<", "\\u003c")
        )
        output.write("</script><script type=\"application/json\" id=\"cell-data\">")
        rail_names = [rail for rail, _label in observables]
        rail_codes = {rail: index for index, rail in enumerate(rail_names)}
        engine_codes = {
            engine: index for index, engine in enumerate(engine_names)
        }

        def encode_cell(
            row: Mapping[str, str], *, sio2_unknown: bool = False
        ) -> list[object]:
            temperature_bin = int(
                Decimal(row["temperature_lower_K"])
                / BATTERY_HERO_FINGERPRINT_TEMPERATURE_BIN_K
            )
            sio2_bin = (
                -1
                if sio2_unknown
                else int(
                    Decimal(row["sio2_lower"])
                    / BATTERY_HERO_FINGERPRINT_SIO2_BIN_WIDTH
                )
            )
            band_code = {"good": 1, "okay": 2, "needs work": 3}.get(
                row.get("band", ""), 0
            )
            return [
                rail_codes[row["rail"]],
                engine_codes[row["engine"]],
                temperature_bin,
                sio2_bin,
                int(row["n"]),
                row["sum_residual_dex"],
                row["sum_squared_residual_dex"],
                row.get("median_abs_dex", ""),
                band_code,
                sio2_unknown,
            ]

        cell_payload = {
            "rails": rail_names,
            "engines": list(engine_names),
            "cells": [
                *(encode_cell(row) for row in table_rows["fingerprints"]),
                *(
                    encode_cell(row, sio2_unknown=True)
                    for row in table_rows["window_unknown"]
                ),
            ],
        }
        output.write(
            json.dumps(cell_payload, separators=(",", ":")).replace(
                "<", "\\u003c"
            )
        )
        output.write("</script><script>\n")
        output.write("const rows=JSON.parse(document.getElementById('row-data').textContent);\n")
        output.write("const cellData=JSON.parse(document.getElementById('cell-data').textContent);\n")
        output.write("const cells=cellData.cells;\n")
        output.write("const PALETTE=")
        output.write(json.dumps(_hero_engine_colors(engine_names)))
        output.write(";const BANDS={good:'#1a9641',okay:'#f6c90e','needs work':'#d7191c'};\n")
        output.write(
            _COLLECTION_INTERACTIVE_SCRIPT
            .replace("__RAILS__", json.dumps([rail for rail, _label in observables]))
            .replace("__LABELS__", json.dumps(dict(observables)))
            .replace(
                "__TEMPERATURE_BIN_K__",
                str(BATTERY_HERO_FINGERPRINT_TEMPERATURE_BIN_K),
            )
            .replace(
                "__SIO2_BIN_WIDTH__",
                str(BATTERY_HERO_FINGERPRINT_SIO2_BIN_WIDTH),
            )
            .replace(
                "__TEMPERATURE_FLOOR_K__",
                str(HERO_TEMPERATURE_FLOOR_K),
            )
        )
        output.write("</script></main></body></html>")


_COLLECTION_INTERACTIVE_SCRIPT = r"""
const rails=__RAILS__,labels=__LABELS__;
const TEMPERATURE_BIN_K=__TEMPERATURE_BIN_K__,SIO2_BIN_WIDTH=__SIO2_BIN_WIDTH__,TEMPERATURE_FLOOR_K=__TEMPERATURE_FLOOR_K__;
const railSelect=document.getElementById('observable-select'), engineSelect=document.getElementById('fingerprint-engine');
const toggles=document.getElementById('engine-toggles'), tMin=document.getElementById('temperature-min'),tMax=document.getElementById('temperature-max');
const sMin=document.getElementById('sio2-min'),sMax=document.getElementById('sio2-max');
const errorCanvas=document.getElementById('error-canvas'),mapCanvas=document.getElementById('fingerprint-canvas'),tip=document.getElementById('tooltip');
const engines=Object.keys(PALETTE);
for(const rail of rails)railSelect.add(new Option(labels[rail],rail));
for(const engine of engines){
  engineSelect.add(new Option(engine,engine));
  const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=true;check.value=engine;check.dataset.engineToggle=engine;
  const swatch=document.createElement('span');swatch.style.cssText='display:inline-block;width:12px;height:12px;background:'+PALETTE[engine]+';border-radius:50%';
  label.append(check,swatch,document.createTextNode(engine));toggles.append(label);
}
let minTemp=Infinity,maxTemp=-Infinity;
for(const cell of cells){minTemp=Math.min(minTemp,cell[2]*TEMPERATURE_BIN_K);maxTemp=Math.max(maxTemp,(cell[2]+1)*TEMPERATURE_BIN_K);}
tMin.value=Number.isFinite(minTemp)?Math.floor(minTemp):0;tMax.value=Number.isFinite(maxTemp)?Math.ceil(maxTemp):3000;sMin.value=0;sMax.value=1;
function enabled(engine){return !!toggles.querySelector('[data-engine-toggle=\"'+CSS.escape(engine)+'\"]')?.checked;}
function snap(value,step,round){return round(value/step)*step;}
function windowValues(){return [snap(+tMin.value,TEMPERATURE_BIN_K,Math.floor),snap(+tMax.value,TEMPERATURE_BIN_K,Math.ceil),snap(+sMin.value,SIO2_BIN_WIDTH,Math.floor),snap(+sMax.value,SIO2_BIN_WIDTH,Math.ceil)];}
function snapControls(){const [lo,hi,slo,shi]=windowValues();tMin.value=lo;tMax.value=hi;sMin.value=slo.toFixed(2);sMax.value=shi.toFixed(2);}
function inWindow(row){const [lo,hi,slo,shi]=windowValues();if(+row.temperature_K<lo||+row.temperature_K>hi)return false;return row.sio2==null?(slo<=0&&shi>=1):(+row.sio2>=slo&&+row.sio2<=shi);}
function updateWindowRms(){
  const [lo,hi,slo,shi]=windowValues(),rail=railSelect.value,parts=[];
  for(const engine of engines){if(!enabled(engine))continue;let n=0,squares=0;
    for(const cell of cells){if(cellData.rails[cell[0]]!==rail||cellData.engines[cell[1]]!==engine)continue;
      const temperatureLower=cell[2]*TEMPERATURE_BIN_K,temperatureUpper=(cell[2]+1)*TEMPERATURE_BIN_K;
      if(temperatureLower<lo||temperatureUpper>hi)continue;
      if(cell[9]){if(slo<=0&&shi>=1){n+=cell[4];squares+=+cell[6];}continue;}
      const sio2Lower=cell[3]*SIO2_BIN_WIDTH,sio2Upper=(cell[3]+1)*SIO2_BIN_WIDTH;
      if(sio2Lower<slo||sio2Upper>shi)continue;
      n+=cell[4];squares+=+cell[6];
    }
    parts.push(engine+': '+(n?Math.sqrt(squares/n).toFixed(3)+' dex (n='+n+')':'no scored bins'));
  }
  document.getElementById('window-rms').textContent='Exact binned RMS for '+lo+'–'+hi+' K and SiO₂ '+slo.toFixed(2)+'–'+shi.toFixed(2)+': '+(parts.join(' · ')||'no engines selected');
}
function size(canvas){const rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;canvas.width=Math.max(1,Math.round(rect.width*dpr));canvas.height=Math.max(1,Math.round(rect.height*dpr));const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w:rect.width,h:rect.height};}
function lighten(hex,amount){const n=parseInt(hex.slice(1),16),r=n>>16,g=(n>>8)&255,b=n&255;return 'rgb('+Math.round(r+(255-r)*amount)+','+Math.round(g+(255-g)*amount)+','+Math.round(b+(255-b)*amount)+')';}
function plotError(){
  const {ctx,w,h}=size(errorCanvas),rail=railSelect.value,m={l:54,r:14,t:18,b:38},pw=w-m.l-m.r,ph=h-m.t-m.b,[lo,hi]=windowValues();
  ctx.clearRect(0,0,w,h);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--page');ctx.fillRect(0,0,w,h);
  const x=v=>m.l+(v-lo)/(hi-lo||1)*pw,y=v=>m.t+(3-Math.max(-3,Math.min(3,v)))/6*ph;
  ctx.fillStyle='rgba(12,163,12,.12)';ctx.fillRect(m.l,y(.5),pw,y(-.5)-y(.5));ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--grid');
  for(let i=0;i<=6;i++){const yy=m.t+i*ph/6;ctx.beginPath();ctx.moveTo(m.l,yy);ctx.lineTo(w-m.r,yy);ctx.stroke();}
  ctx.strokeStyle='#263238';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(m.l,y(0));ctx.lineTo(w-m.r,y(0));ctx.stroke();
  ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(x(TEMPERATURE_FLOOR_K),m.t);ctx.lineTo(x(TEMPERATURE_FLOOR_K),h-m.b);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--ink');ctx.font='12px system-ui';ctx.fillText('Temperature (K)',w/2-42,h-8);ctx.save();ctx.translate(14,h/2+38);ctx.rotate(-Math.PI/2);ctx.fillText('Signed error (dex)',0,0);ctx.restore();
  for(const row of rows){if(row.rail!==rail||!enabled(row.engine)||!inWindow(row))continue;const px=x(+row.temperature_K),py=y(+row.residual_dex);ctx.globalAlpha=row.flagged?.72:.35;ctx.fillStyle=row.flagged?lighten(PALETTE[row.engine],.38):PALETTE[row.engine];ctx.beginPath();if(+row.residual_dex>3){ctx.moveTo(px,py-4);ctx.lineTo(px-4,py+4);ctx.lineTo(px+4,py+4);ctx.closePath();}else if(+row.residual_dex < -3){ctx.moveTo(px,py+4);ctx.lineTo(px-4,py-4);ctx.lineTo(px+4,py-4);ctx.closePath();}else ctx.arc(px,py,2.1,0,Math.PI*2);ctx.fill();}
  ctx.globalAlpha=1;errorCanvas._plot={x,y,m,pw,ph,lo,hi,rail};
}
function plotMap(){
  const {ctx,w,h}=size(mapCanvas),rail=railSelect.value,engine=engineSelect.value,m={l:54,r:14,t:18,b:36},pw=w-m.l-m.r,ph=h-m.t-m.b;
  const railCells=cells.filter(c=>!c[9]&&cellData.rails[c[0]]===rail&&cellData.engines[c[1]]===engine),[tlo,thi,slo,shi]=windowValues();let lo=Infinity,hi=-Infinity;
  for(const cell of railCells){lo=Math.min(lo,cell[2]*TEMPERATURE_BIN_K);hi=Math.max(hi,(cell[2]+1)*TEMPERATURE_BIN_K);}if(!Number.isFinite(lo)||!Number.isFinite(hi)){lo=0;hi=1;}
  ctx.clearRect(0,0,w,h);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--page');ctx.fillRect(0,0,w,h);const x=v=>m.l+(v-slo)/(shi-slo||1)*pw,y=v=>m.t+(hi-v)/(hi-lo||1)*ph;
  for(const cell of railCells){if(!enabled(engine)||cell[4]<3||!cell[8])continue;const xl=Math.max(slo,cell[3]*SIO2_BIN_WIDTH),xh=Math.min(shi,(cell[3]+1)*SIO2_BIN_WIDTH),yl=Math.max(tlo,cell[2]*TEMPERATURE_BIN_K),yh=Math.min(thi,(cell[2]+1)*TEMPERATURE_BIN_K);if(xh<=xl||yh<=yl)continue;ctx.fillStyle=BANDS[['','good','okay','needs work'][cell[8]]];ctx.fillRect(x(xl)+1,y(yh)+1,Math.max(0,x(xh)-x(xl)-2),Math.max(0,y(yl)-y(yh)-2));}
  ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');ctx.strokeRect(m.l,m.t,pw,ph);ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--ink');ctx.font='12px system-ui';ctx.fillText('SiO₂ mole fraction',w/2-58,h-8);ctx.save();ctx.translate(14,h/2+30);ctx.rotate(-Math.PI/2);ctx.fillText('Temperature (K)',0,0);ctx.restore();mapCanvas._plot={x,y,m,pw,ph,lo,hi,rail,engine};
}
function showTip(event,kind){
  const canvas=kind==='map'?mapCanvas:errorCanvas,p=canvas._plot;if(!p)return;const rect=canvas.getBoundingClientRect(),mx=event.clientX-rect.left,my=event.clientY-rect.top;let row=null,cell=null,best=Infinity;
  if(kind==='map'){
    const [tlo,thi,slo,shi]=windowValues(),sx=slo+(mx-p.m.l)/p.pw*(shi-slo),temp=p.hi-(my-p.m.t)/p.ph*(p.hi-p.lo);
    if(!enabled(p.engine)){tip.style.display='none';return;}
    cell=cells.find(c=>!c[9]&&cellData.rails[c[0]]===p.rail&&cellData.engines[c[1]]===p.engine&&c[4]>=3&&sx>=c[3]*SIO2_BIN_WIDTH&&sx<(c[3]+1)*SIO2_BIN_WIDTH&&temp>=c[2]*TEMPERATURE_BIN_K&&temp<(c[2]+1)*TEMPERATURE_BIN_K&&(c[2]*TEMPERATURE_BIN_K)<thi&&((c[2]+1)*TEMPERATURE_BIN_K)>tlo);
    if(cell)row=rows.find(r=>r.rail===p.rail&&r.engine===p.engine&&r.sio2!=null&&+r.sio2>=cell[3]*SIO2_BIN_WIDTH&&+r.sio2<(cell[3]+1)*SIO2_BIN_WIDTH&&+r.temperature_K>=cell[2]*TEMPERATURE_BIN_K&&+r.temperature_K<(cell[2]+1)*TEMPERATURE_BIN_K&&inWindow(r));
  }else{
    for(const r of rows){if(r.rail!==p.rail||!enabled(r.engine)||!inWindow(r))continue;const dx=p.x(+r.temperature_K)-mx,dy=p.y(+r.residual_dex)-my,d=dx*dx+dy*dy;if(d<best){best=d;row=r;}}
    if(best>110)row=null;
  }
  if(!row&&!cell){tip.style.display='none';return;}
  const detail=row?'Row source: '+row.source+' ('+row.reference+')\nT: '+row.temperature_K+' K\nComposition: '+row.composition+'\nResidual: '+row.residual_dex+' dex':'No row in this cell';
  tip.textContent=cell?'Median |error|: '+cell[7]+' dex · n='+cell[4]+'\n'+detail:detail;tip.style.display='block';tip.style.left=Math.min(event.clientX+12,innerWidth-350)+'px';tip.style.top=Math.min(event.clientY+12,innerHeight-120)+'px';
}
function deferredRows(kind,rail){
  if(kind==='samples')return rows.filter(row=>row.rail===rail);
  const bandNames=['','good','okay','needs work'];
  return cells.filter(cell=>!cell[9]&&cellData.rails[cell[0]]===rail).map(cell=>({
    rail:cellData.rails[cell[0]],engine:cellData.engines[cell[1]],
    temperature_lower_K:String(cell[2]*TEMPERATURE_BIN_K),temperature_upper_K:String((cell[2]+1)*TEMPERATURE_BIN_K),
    sio2_lower:String(cell[3]*SIO2_BIN_WIDTH),sio2_upper:String((cell[3]+1)*SIO2_BIN_WIDTH),n:cell[4],
    sum_residual_dex:cell[5],sum_squared_residual_dex:cell[6],median_abs_dex:cell[7],
    band:bandNames[cell[8]],colour:BANDS[bandNames[cell[8]]]||''
  }));
}
function renderDeferredTable(holder,tableRows){
  if(!tableRows.length){holder.textContent='No rows.';return;}
  const columns=Object.keys(tableRows[0]),table=document.createElement('table'),head=document.createElement('thead'),headRow=document.createElement('tr'),body=document.createElement('tbody');
  for(const column of columns){const cell=document.createElement('th');cell.textContent=column;headRow.append(cell);}head.append(headRow);
  for(const record of tableRows){const row=document.createElement('tr');for(const column of columns){const cell=document.createElement('td');cell.textContent=record[column]??'';row.append(cell);}body.append(row);}
  table.append(head,body);holder.replaceChildren(table);
}
for(const holder of document.querySelectorAll('[data-deferred-table]')){
  const details=holder.closest('details');details.addEventListener('toggle',()=>{
    if(!details.open||holder.dataset.loaded)return;
    renderDeferredTable(holder,deferredRows(holder.dataset.deferredTable,holder.dataset.rail));holder.dataset.loaded='true';
  });
}
function redraw(){plotError();plotMap();updateWindowRms();}
for(const control of [railSelect,engineSelect,tMin,tMax,sMin,sMax])control.addEventListener('input',redraw);
for(const control of [tMin,tMax,sMin,sMax])control.addEventListener('change',()=>{snapControls();redraw();});
toggles.addEventListener('change',redraw);errorCanvas.addEventListener('pointermove',e=>showTip(e,'error'));errorCanvas.addEventListener('pointerleave',()=>tip.style.display='none');mapCanvas.addEventListener('pointermove',e=>showTip(e,'map'));mapCanvas.addEventListener('pointerleave',()=>tip.style.display='none');
document.getElementById('theme-toggle').addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';redraw();});
window.addEventListener('resize',redraw);redraw();
""";


def render_hero_collection(
    data: ScorePlotData,
    source_path: Path,
    output_dir: Path,
    *,
    simulator_commit: str,
    openimcc_commit: str,
    render_date: str | None = None,
    history_path: Path | None = None,
    annotations: Mapping[str, str] | None = None,
    fix_status_lines: Sequence[str] = (),
) -> dict[str, Path]:
    """Render the static A–G collection, sidecar tables, and embedded HTML."""

    if not data.engines:
        raise ValueError("no engines found in score JSONL")
    if data.rails_explicit:
        unsupported = sorted(set(data.selected_rails) - set(COLLECTION_SCORE_RAILS))
        if unsupported:
            raise ValueError(
                "collection renderer supports --rail only for: "
                + ", ".join(COLLECTION_SCORE_RAILS)
                + f"; unsupported: {', '.join(unsupported)}"
            )
        selected_rails = set(data.selected_rails)
        observables = tuple(
            item for item in COLLECTION_OBSERVABLES if item[0] in selected_rails
        )
    else:
        observables = COLLECTION_OBSERVABLES
    if not observables:
        raise ValueError("collection renderer needs at least one supported rail")
    observable_rails = {rail for rail, _label in observables}
    render_date = render_date or datetime.now(timezone.utc).date().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    scratch = os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(
        prefix="battery-hero-collection-",
        dir=scratch if scratch else None,
    ) as scratch_path:
        database = sqlite3.connect(Path(scratch_path) / "collection.sqlite3")
        database.execute("PRAGMA journal_mode = OFF")
        database.execute("PRAGMA synchronous = OFF")
        database.execute("PRAGMA temp_store = FILE")
        database.execute("PRAGMA cache_size = -32768")
        _collection_row_database(
            source_path,
            database,
            bin_width_K=data.bin_width_K,
        )
        raw_tables = _collection_raw_tables(
            database,
            data,
            bin_width_K=data.bin_width_K,
            observables=observables,
        )
        history = read_history(history_path)
        annotation_map = dict(annotations or {})
        tables: dict[str, list[dict[str, object]]] = {
            "scorecard": [
                row
                for row in _scorecard_table_rows(data)
                if row["rail"] in observable_rails
            ],
            "panels": raw_tables["panels"],
            "temperature_bins": raw_tables["temperature_bins"],
            "temperature_density": raw_tables["density"],
            "temperature_points": raw_tables["points"],
            "temperature_sample": raw_tables["sample"],
            "components": [
                row
                for row in _component_table_rows(data)
                if row["rail"] in observable_rails
            ],
            "fingerprints": [
                row
                for row in _fingerprint_table_rows(data)
                if row["rail"] in observable_rails
            ],
            "window_unknown": [
                row
                for row in _window_unknown_table_rows(data)
                if row["rail"] in observable_rails
            ],
            "coverage": [
                row
                for row in _coverage_table_rows(data)
                if row["rail"] in observable_rails
            ],
            "progress": [
                row
                for row in _history_collection_rows(
                    data,
                    history,
                    simulator_commit=simulator_commit,
                    render_date=render_date,
                    annotations=annotation_map,
                )
                if row["rail"] in observable_rails
            ],
            "exposure": [],
            "all_rows": [
                row
                for row in _all_rows_table_rows(data)
                if row["rail"] in observable_rails
            ],
        }
        for rail, _label in observables:
            for engine in _ordered_hero_engines(data.engines):
                view = _view_metric(_collection_metric(data, rail, engine), "all")
                held = view.get("held_out", {})
                held = held if isinstance(held, Mapping) else {}
                tables["exposure"].append(
                    {
                        "rail": rail,
                        "engine": engine,
                        "exposed_n": view.get("exposed_n", ""),
                        "held_out_n": held.get("n", ""),
                        "held_out_rms_dex": held.get("rms_dex", ""),
                    }
                )
        files = _write_collection_csvs(output_dir, tables)
        csv_rows = {
            name: _read_collection_csv(files[f"table:{name}"])
            for name in _COLLECTION_TABLE_FIELDS
        }
        chart_files: dict[str, Path] = {}
        path = output_dir / "battery-hero-A-scorecard.png"
        _draw_scorecard_png(path, csv_rows["scorecard"], observables)
        chart_files["chart:A"] = path
        files["chart:A"] = path
        for rail, _label in observables:
            path = output_dir / f"battery-hero-B-{_revision_slug(rail)}.png"
            _draw_temperature_png(
                path,
                rail,
                [row for row in csv_rows["panels"] if row["rail"] == rail],
                csv_rows["temperature_bins"],
                csv_rows["temperature_density"],
                csv_rows["temperature_points"],
            )
            key = f"chart:B:{rail}"
            chart_files[key] = path
            files[key] = path
        for rail in (Rail.VAPOUR.value, Rail.MELT_ACTIVITY.value):
            if rail not in observable_rails:
                continue
            path = output_dir / f"battery-hero-C-{_revision_slug(rail)}.png"
            _draw_component_png(path, rail, csv_rows["components"])
            key = f"chart:C:{rail}"
            chart_files[key] = path
            files[key] = path
        for rail, _label in observables:
            path = output_dir / f"battery-hero-D-{_revision_slug(rail)}.png"
            _draw_fingerprint_png(path, rail, csv_rows["fingerprints"], data.engines)
            key = f"chart:D:{rail}"
            chart_files[key] = path
            files[key] = path
        for chart_key, filename, draw, rows in (
            ("chart:E", "battery-hero-E-coverage.png", _draw_coverage_png, csv_rows["coverage"]),
            ("chart:F", "battery-hero-F-progress.png", _draw_progress_png, csv_rows["progress"]),
            ("chart:G", "battery-hero-G-exposure.png", _draw_exposure_png, csv_rows["exposure"]),
        ):
            path = output_dir / filename
            draw(path, rows, observables)
            chart_files[chart_key] = path
            files[chart_key] = path
        methods_path = _write_collection_methods(
            data,
            output_dir,
            openimcc_commit=openimcc_commit,
            fix_status_lines=fix_status_lines,
        )
        files["methods"] = methods_path
        html_path = output_dir / "battery-hero.html"
        _write_collection_html(
            html_path,
            chart_files=chart_files,
            table_rows=csv_rows,
            engine_names=data.engines,
            observables=observables,
        )
        files["html"] = html_path
        database.close()
    return files


def _collection_metric(
    data: ScorePlotData, rail: str, engine: str
) -> Mapping[str, object]:
    return data.hero_all_numeric_metrics.get((rail, engine), {})


def _collection_comparison(
    data: ScorePlotData, rail: str, primary_engine: str
) -> Mapping[str, object]:
    comparison = data.hero_comparisons.get((rail, primary_engine))
    if comparison is not None:
        return comparison
    chart = "sio_evolution" if rail == Rail.SIO_EVOLUTION.value else rail
    return data.hero_deck_comparisons.get((chart, primary_engine), {})


def _collection_history_comparisons(
    data: ScorePlotData,
) -> dict[tuple[str, str], Mapping[str, object]]:
    comparisons = dict(data.hero_comparisons)
    for chart, rail in (
        ("sio_evolution", Rail.SIO_EVOLUTION.value),
        ("residue_composition", Rail.RESIDUE_COMPOSITION.value),
    ):
        for primary in HERO_PRIMARY_ENGINES:
            record = data.hero_deck_comparisons.get((chart, primary))
            if record is not None:
                comparisons[(rail, primary)] = {**record, "rail": rail}
    return comparisons


def _view_metric(
    metric: Mapping[str, object], view_name: str
) -> Mapping[str, object]:
    views = metric.get("views")
    views = views if isinstance(views, Mapping) else {}
    view = views.get(view_name)
    return view if isinstance(view, Mapping) else {}


def _scorecard_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rail, label in COLLECTION_OBSERVABLES:
        for primary in ("openimcc",):
            comparison_record = _collection_comparison(data, rail, primary)
            comparisons = comparison_record.get("comparisons")
            if not isinstance(comparisons, Sequence) or isinstance(comparisons, (str, bytes)):
                continue
            for comparison in comparisons:
                if not isinstance(comparison, Mapping):
                    continue
                views = comparison.get("views")
                views = views if isinstance(views, Mapping) else {}
                all_view = views.get("all", {})
                all_view = all_view if isinstance(all_view, Mapping) else {}
                high = views.get("temperature_ge_1700", {})
                high = high if isinstance(high, Mapping) else {}
                low = views.get("temperature_lt_1700", {})
                low = low if isinstance(low, Mapping) else {}
                rows.append(
                    {
                        "rail": rail,
                        "observable": label,
                        "primary_engine": primary,
                        "comparison_engine": str(comparison.get("engine") or "unknown"),
                        "n_shared": all_view.get("n_shared", comparison.get("n_shared", 0)),
                        "primary_rms_dex": all_view.get("primary_rms_dex_on_shared", comparison.get("primary_rms_dex_on_shared")),
                        "comparison_rms_dex": all_view.get("outside_rms_dex", comparison.get("outside_rms_dex")),
                        "high_n_shared": high.get("n_shared", ""),
                        "high_primary_rms_dex": high.get("primary_rms_dex_on_shared", ""),
                        "high_comparison_rms_dex": high.get("outside_rms_dex", ""),
                        "low_n_shared": low.get("n_shared", ""),
                        "low_primary_rms_dex": low.get("primary_rms_dex_on_shared", ""),
                        "low_comparison_rms_dex": low.get("outside_rms_dex", ""),
                    }
                )
    return rows


def _scorecard_plot_values(
    rows: Sequence[Mapping[str, object]],
) -> dict[
    tuple[str, str, str],
    tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None],
]:
    values: dict[
        tuple[str, str, str],
        tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None],
    ] = {}
    for row in rows:
        key = (
            str(row["rail"]),
            str(row["primary_engine"]),
            str(row["comparison_engine"]),
        )
        def decimal(field: str) -> Decimal | None:
            value = row.get(field)
            return None if value in (None, "") else Decimal(str(value))

        values[key] = (
            decimal("high_primary_rms_dex"),
            decimal("high_comparison_rms_dex"),
            decimal("low_primary_rms_dex"),
            decimal("low_comparison_rms_dex"),
        )
    return values


def _component_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in data.hero_component_comparisons:
        rail = str(record.get("rail") or "")
        component = str(record.get("species") or "unknown")
        if record.get("primary_engine") != "openimcc" or rail not in {
            Rail.VAPOUR.value,
            Rail.MELT_ACTIVITY.value,
        }:
            continue
        comparisons = record.get("comparisons")
        if not isinstance(comparisons, Sequence) or isinstance(comparisons, (str, bytes)):
            continue
        for comparison in comparisons:
            if not isinstance(comparison, Mapping):
                continue
            partner = str(comparison.get("engine") or "unknown")
            views = comparison.get("views")
            views = views if isinstance(views, Mapping) else {}
            all_view = views.get("all", {})
            all_view = all_view if isinstance(all_view, Mapping) else {}
            n_shared = all_view.get("n_shared", comparison.get("n_shared", 0))
            if int(n_shared or 0) < HERO_MINIMUM_REPORT_N:
                continue
            rows.extend(
                (
                    {
                        "rail": rail,
                        "component": component,
                        "engine": "openimcc",
                        "shared_with": partner,
                        "n_shared": n_shared,
                        "rms_dex": all_view.get(
                            "primary_rms_dex_on_shared",
                            comparison.get("primary_rms_dex_on_shared"),
                        ),
                    },
                    {
                        "rail": rail,
                        "component": component,
                        "engine": partner,
                        "shared_with": partner,
                        "n_shared": n_shared,
                        "rms_dex": all_view.get(
                            "outside_rms_dex", comparison.get("outside_rms_dex")
                        ),
                    },
                )
            )
    rows.sort(
        key=lambda row: (
            str(row["rail"]),
            -int(row["n_shared"] or 0),
            str(row["component"]),
            str(row["shared_with"]),
            str(row["engine"]),
        )
    )
    return rows


def _fingerprint_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (rail, engine), cells in sorted(data.hero_sweet_spot_cells.items()):
        if rail not in COLLECTION_SCORE_RAILS:
            continue
        for cell in cells:
            count = int(cell.get("n") or 0)
            if count == 0:
                continue
            value = cell.get("median_abs_dex")
            band = (
                _battery_hero_fingerprint_band(value)
                if count >= 3 and value is not None
                else ""
            )
            colour = FINGERPRINT_COLOURS.get(band, "")
            rows.append(
                {
                    "rail": rail,
                    "engine": engine,
                    "temperature_lower_K": cell.get("temperature_lower_K"),
                    "temperature_upper_K": cell.get("temperature_upper_K"),
                    "sio2_lower": cell.get("sio2_lower"),
                    "sio2_upper": cell.get("sio2_upper"),
                    "n": count,
                    "sum_residual_dex": cell.get("sum_residual_dex", ""),
                    "sum_squared_residual_dex": cell.get(
                        "sum_squared_residual_dex", ""
                    ),
                    "median_abs_dex": value,
                    "band": band,
                    "colour": colour,
                }
            )
    return rows


def _window_unknown_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    return [
        dict(cell)
        for key in sorted(data.hero_sio2_unknown_cells)
        for cell in data.hero_sio2_unknown_cells[key]
        if str(cell["rail"]) in COLLECTION_SCORE_RAILS
    ]


def _coverage_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    reasons_by_key: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for (rail, engine, reason), count in data.hero_refusal_totals.items():
        if count <= 0:
            continue
        lowered = reason.casefold()
        if "missing" in lowered or "input" in lowered or "identity" in lowered:
            group = "input missing"
        elif "not_observable" in lowered:
            group = "not an output of this engine"
        elif any(token in lowered for token in ("outside", "domain", "unsupported")):
            group = "outside the engine's domain"
        else:
            group = "other"
        reasons_by_key[(rail, engine)][group] += count
    rows: list[dict[str, object]] = []
    for rail, label in COLLECTION_OBSERVABLES:
        for engine in _ordered_hero_engines(data.engines):
            metric = _collection_metric(data, rail, engine)
            scored = int(metric.get("n") or 0)
            summary = data.summaries.get(engine, EngineSummary())
            observable_absence = int(summary.not_observable_by_rail.get(rail, 0))
            refused = int(summary.refused_by_rail.get(rail, 0))
            groups = reasons_by_key[(rail, engine)]
            missing = groups["input missing"]
            outside = groups["outside the engine's domain"]
            not_output = max(
                groups["not an output of this engine"], observable_absence
            )
            other = max(0, refused - missing - outside - groups["not an output of this engine"])
            total = scored + missing + outside + not_output + other
            rows.append(
                {
                    "rail": rail,
                    "observable": label,
                    "engine": engine,
                    "scored_n": scored,
                    "input_missing_n": missing,
                    "outside_domain_n": outside,
                    "not_output_n": not_output,
                    "other_refused_n": other,
                    "refused_n": missing + outside + other,
                    "scored_percent": "" if not total else f"{100 * scored / total:.17g}",
                }
            )
    return rows


def _history_collection_rows(
    data: ScorePlotData,
    history: Sequence[Mapping[str, Any]],
    *,
    simulator_commit: str,
    render_date: str,
    annotations: Mapping[str, str],
) -> list[dict[str, object]]:
    entries = list(history)
    if not any(
        str(entry.get("store_sha") or "") == simulator_commit
        or str(entry.get("store_revision") or "") == str(data.stamp.get("revision") or "")
        for entry in entries
    ):
        entries.append(
            {
                "store_sha": simulator_commit,
                "store_revision": data.stamp.get("revision"),
                "commit_date": render_date,
                "metrics": list(
                    {
                        **data.hero_all_numeric_metrics,
                        **data.hero_deck_metrics,
                    }.values()
                ),
                "comparisons": list(_collection_history_comparisons(data).values()),
            }
        )
    rows: list[dict[str, object]] = []
    for entry in entries:
        landing = str(entry.get("store_sha") or entry.get("store_revision") or "unknown")
        date = str(entry.get("commit_date") or "")
        annotation = annotations.get(landing) or annotations.get(date[:10]) or ""
        comparisons = entry.get("comparisons")
        shared_values: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        rails_with_comparisons: set[str] = set()
        if isinstance(comparisons, Sequence) and not isinstance(
            comparisons, (str, bytes)
        ):
            for record in comparisons:
                if not isinstance(record, Mapping):
                    continue
                rail = str(record.get("rail") or "")
                primary = str(record.get("primary_engine") or "")
                if rail not in COLLECTION_SCORE_RAILS or not primary:
                    continue
                rails_with_comparisons.add(rail)
                comparison_rows = record.get("comparisons")
                if not isinstance(comparison_rows, Sequence) or isinstance(
                    comparison_rows, (str, bytes)
                ):
                    continue
                for comparison in comparison_rows:
                    if not isinstance(comparison, Mapping):
                        continue
                    outside = str(comparison.get("engine") or "")
                    if not outside:
                        continue
                    views = comparison.get("views")
                    views = views if isinstance(views, Mapping) else {}
                    all_view = views.get("all", {})
                    all_view = all_view if isinstance(all_view, Mapping) else comparison
                    n_shared = int(
                        all_view.get("n_shared", comparison.get("n_shared")) or 0
                    )
                    if n_shared < 1:
                        continue
                    held = all_view.get("held_out", {})
                    held = held if isinstance(held, Mapping) else {}
                    exposed_n = all_view.get(
                        "exposed_n", comparison.get("exposed_n", "")
                    )
                    held_out_n = held.get("n", "")
                    for engine, rms_field, held_rms_field, shared_with in (
                        (
                            primary,
                            "primary_rms_dex_on_shared",
                            "primary_rms_dex_on_shared",
                            outside,
                        ),
                        (
                            outside,
                            "outside_rms_dex",
                            "outside_rms_dex",
                            primary,
                        ),
                    ):
                        rms = all_view.get(rms_field, comparison.get(rms_field))
                        if rms in (None, ""):
                            continue
                        shared_values[(rail, engine)].append(
                            {
                                "rms_dex": rms,
                                "n": n_shared,
                                "exposed_n": exposed_n,
                                "held_out_n": held_out_n,
                                "held_out_rms_dex": held.get(held_rms_field, ""),
                                "shared_with": shared_with,
                                "basis": "shared_rows",
                                "primary_engine": primary,
                                "comparison_engine": outside,
                            }
                        )
        selected_shared = {
            key: min(
                values,
                key=lambda value: (
                    str(value["primary_engine"]) != "openimcc",
                    -int(value["n"]),
                    str(value["primary_engine"]),
                    str(value["comparison_engine"]),
                ),
            )
            for key, values in shared_values.items()
        }
        metrics = entry.get("metrics")
        if not isinstance(metrics, Sequence) or isinstance(metrics, (str, bytes)):
            continue
        for metric in metrics:
            if not isinstance(metric, Mapping):
                continue
            rail = str(metric.get("rail") or "")
            engine = str(metric.get("engine") or "")
            if rail not in COLLECTION_SCORE_RAILS:
                continue
            views = metric.get("views")
            views = views if isinstance(views, Mapping) else {}
            all_view = views.get("all", {})
            all_view = all_view if isinstance(all_view, Mapping) else {}
            held = all_view.get("held_out", {})
            held = held if isinstance(held, Mapping) else {}
            shared = selected_shared.get((rail, engine))
            if rail in rails_with_comparisons and shared is None:
                continue
            rows.append(
                {
                    "landing": landing,
                    "date": date,
                    "rail": rail,
                    "engine": engine,
                    "rms_dex": (
                        shared["rms_dex"] if shared is not None else metric.get("rms_dex")
                    ),
                    "n": (
                        shared["n"]
                        if shared is not None
                        else metric.get("n", metric.get("numeric_count", ""))
                    ),
                    "exposed_n": (
                        shared["exposed_n"]
                        if shared is not None
                        else all_view.get("exposed_n", "")
                    ),
                    "held_out_n": (
                        shared["held_out_n"] if shared is not None else held.get("n", "")
                    ),
                    "held_out_rms_dex": (
                        shared["held_out_rms_dex"]
                        if shared is not None
                        else held.get("rms_dex", "")
                    ),
                    "shared_with": shared["shared_with"] if shared is not None else "",
                    "basis": shared["basis"] if shared is not None else "legacy_all_rows",
                    "annotation": annotation,
                }
            )
    return rows


def _all_rows_table_rows(data: ScorePlotData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rail, _label in COLLECTION_OBSERVABLES:
        for engine in _ordered_hero_engines(data.engines):
            metric = _collection_metric(data, rail, engine)
            if not metric:
                continue
            for view_name, _view_label in HERO_TEMPERATURE_VIEWS:
                view = _view_metric(metric, view_name)
                disagreement = view.get("source_disagreement_flagged", {})
                disagreement = disagreement if isinstance(disagreement, Mapping) else {}
                unflagged = view.get("unflagged", {})
                unflagged = unflagged if isinstance(unflagged, Mapping) else {}
                held = view.get("held_out", {})
                held = held if isinstance(held, Mapping) else {}
                rows.append(
                    {
                        "rail": rail,
                        "engine": engine,
                        "view": view_name,
                        "n": view.get("n", ""),
                        "rms_dex": view.get("rms_dex", ""),
                        "sd_dex": view.get("sd_dex", ""),
                        "median_abs_dex": view.get("median_abs_dex", ""),
                        "exposed_n": view.get("exposed_n", ""),
                        "held_out_n": held.get("n", ""),
                        "held_out_rms_dex": held.get("rms_dex", ""),
                        "source_disagreement_n": disagreement.get("n", ""),
                        "source_disagreement_rms_dex": disagreement.get("rms_dex", ""),
                        "unflagged_n": unflagged.get("n", ""),
                        "unflagged_rms_dex": unflagged.get("rms_dex", ""),
                    }
                )
    return rows


def render_hero_charts(
    data: ScorePlotData,
    output_dir: Path,
    *,
    history_path: Path | None = None,
    openimcc_score_commit: str | None = None,
) -> dict[str, Path]:
    """Render the vaporisation, redox, and melt activity current/history charts."""

    history = read_history(history_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    engine_colors = _hero_engine_colors(data.engines)
    for rail in HERO_RAILS:
        facets = _hero_facets(data, rail)
        facet_counts = _hero_facet_counts(data, rail)
        numeric_facets = sorted(
            (facet for facet in facets if facet_counts[facet]),
            key=lambda facet: (-facet_counts[facet], facet),
        )
        empty_facets = [facet for facet in facets if not facet_counts[facet]]
        minor_empty_facets = (
            [facet for facet in empty_facets if facet.startswith("minor:")]
            if rail == Rail.VAPOUR.value
            else []
        )
        other_empty_facets = [
            facet for facet in empty_facets if facet not in minor_empty_facets
        ]
        facet_refusals = {
            facet: _hero_refusal_entries(data, rail, facet)
            for facet in numeric_facets
        }
        sweet_spot_engines = tuple(
            engine
            for (sweet_spot_rail, engine) in sorted(data.hero_sweet_spot_cells)
            if sweet_spot_rail == rail
            and any(
                int(cell.get("n") or 0)
                for cell in data.hero_sweet_spot_cells[(sweet_spot_rail, engine)]
            )
        )
        rail_sweet_spot_cells = [
            cell
            for (sweet_spot_rail, _engine), cells in data.hero_sweet_spot_cells.items()
            if sweet_spot_rail == rail
            for cell in cells
        ]
        sio2_edges = [
            edge
            for cell in rail_sweet_spot_cells
            for edge in (
                _finite_float(cell.get("sio2_lower")),
                _finite_float(cell.get("sio2_upper")),
            )
            if edge is not None
        ]
        temperature_edges = [
            edge
            for cell in rail_sweet_spot_cells
            for edge in (
                _finite_float(cell.get("temperature_lower_K")),
                _finite_float(cell.get("temperature_upper_K")),
            )
            if edge is not None
        ]
        sweet_spot_x_limits = (
            (min(sio2_edges), max(sio2_edges)) if sio2_edges else (0.0, 1.0)
        )
        sweet_spot_temperature_limits = (
            (min(temperature_edges), max(temperature_edges))
            if temperature_edges
            else (0.0, 1.0)
        )
        sweet_spot_cells = {
            engine: data.hero_sweet_spot_cells[(rail, engine)]
            for engine in sweet_spot_engines
        }
        scorecard_rows = _hero_scorecard_rows(data, rail)
        row_specs: list[tuple[str, object, float]] = []
        facet_pairs = [
            tuple(numeric_facets[index : index + 2])
            for index in range(0, len(numeric_facets), 2)
        ]
        for pair_index, pair in enumerate(facet_pairs):
            row_specs.append(("current", pair, 1.08))
            refusal_rows = max(
                (
                    len(
                        {
                            reason
                            for _engine, reason, _count in facet_refusals[facet]
                        }
                    )
                    for facet in pair
                ),
                default=0,
            )
            row_specs.append(("refusals", pair, max(0.24, min(0.52, 0.12 * refusal_rows))))
            if pair_index + 1 < len(facet_pairs):
                row_specs.append(("spacer", (), 0.45))
        if facet_pairs:
            row_specs.append(("spacer", (), 0.45))
        if other_empty_facets:
            row_specs.append(
                (
                    "empty",
                    other_empty_facets,
                    0.25 + 0.11 * math.ceil(len(other_empty_facets) / 3),
                )
            )
        if minor_empty_facets:
            row_specs.append(
                (
                    "minor_empty",
                    minor_empty_facets,
                    0.25 + 0.11 * math.ceil(len(minor_empty_facets) / 3),
                )
            )
        row_specs.append(
            (
                "scorecard",
                (),
                _hero_scorecard_section_height(scorecard_rows),
            )
        )
        row_specs.append(("spacer", (), 0.28))
        row_specs.append(("history", (), 1.65))
        sweet_spot_pairs = [
            tuple(sweet_spot_engines[index : index + 2])
            for index in range(0, len(sweet_spot_engines), 2)
        ]
        if sweet_spot_pairs:
            row_specs.append(("spacer", (), 0.28))
        for index, pair in enumerate(sweet_spot_pairs):
            if index:
                row_specs.append(("spacer", (), 0.2))
            row_specs.append(("sweet_spot", pair, 2.35))
        figure_height = max(5.5, sum(spec[2] for spec in row_specs) / 0.78)
        figure = plt.figure(figsize=(14.0, figure_height))
        grid = figure.add_gridspec(
            nrows=len(row_specs),
            ncols=2,
            height_ratios=[spec[2] for spec in row_specs],
            left=0.055,
            right=0.91,
            top=0.85,
            bottom=0.08,
            hspace=0.3,
            wspace=0.2,
        )
        scatter_axes: list[Axes] = []
        history_engine_names: set[str] = set()
        row_index = 0
        for kind, payload, _height in row_specs:
            if kind == "current":
                pair = payload
                assert isinstance(pair, tuple)
                for column, facet in enumerate(pair):
                    location = (
                        grid[row_index, :]
                        if len(pair) == 1
                        else grid[row_index, column]
                    )
                    axis = figure.add_subplot(location)
                    _draw_hero_current(
                        axis,
                        data,
                        rail,
                        facet,
                        engine_colors=engine_colors,
                    )
                    axis.set_title(facet, loc="left", fontsize=7.5)
                    scatter_axes.append(axis)
            elif kind == "refusals":
                pair = payload
                assert isinstance(pair, tuple)
                for column, facet in enumerate(pair):
                    location = (
                        grid[row_index, :]
                        if len(pair) == 1
                        else grid[row_index, column]
                    )
                    axis = figure.add_subplot(location)
                    _draw_hero_refusals(
                        axis,
                        facet_refusals[facet],
                        engine_colors=engine_colors,
                    )
            elif kind in {"empty", "minor_empty"}:
                axis = figure.add_subplot(grid[row_index, :])
                axis.set_axis_off()
                block_lines = _hero_empty_facet_text(
                    data,
                    rail,
                    payload,
                    minor_group=kind == "minor_empty",
                ).splitlines()
                axis.text(
                    0.005,
                    0.97,
                    block_lines[0],
                    transform=axis.transAxes,
                    ha="left",
                    va="top",
                    fontsize=5.5,
                    fontweight="bold",
                )
                lines_per_column = math.ceil((len(block_lines) - 1) / 3)
                for column in range(3):
                    start = 1 + column * lines_per_column
                    column_lines = block_lines[start : start + lines_per_column]
                    if column_lines:
                        axis.text(
                            0.005 + column / 3,
                            0.79,
                            "\n".join(column_lines),
                            transform=axis.transAxes,
                            ha="left",
                            va="top",
                            fontsize=5.1,
                            linespacing=1.05,
                        )
            elif kind == "scorecard":
                scorecard_axis = figure.add_subplot(grid[row_index, :])
                scorecard_axis.set_axis_off()
                scorecard_axis.text(
                    0.005,
                    0.98,
                    "Cross-engine signed dex scorecard · population SD (ddof=0); "
                    f"held-out metrics require n≥{HERO_MINIMUM_REPORT_N}",
                    transform=scorecard_axis.transAxes,
                    ha="left",
                    va="top",
                    fontsize=5.5,
                    fontweight="bold",
                )
                _draw_hero_scorecard_table(scorecard_axis, scorecard_rows)
            elif kind == "history":
                history_axis = figure.add_subplot(grid[row_index, :])
                history_engine_names = _draw_hero_history(
                    history_axis,
                    rail,
                    history,
                    engine_colors=engine_colors,
                )
            elif kind == "spacer":
                pass
            else:
                pair = payload
                assert isinstance(pair, tuple)
                for column, engine in enumerate(pair):
                    location = (
                        grid[row_index, :]
                        if len(pair) == 1
                        else grid[row_index, column]
                    )
                    axis = figure.add_subplot(location)
                    _draw_hero_sweet_spot(
                        axis,
                        engine,
                        sweet_spot_cells[engine],
                        x_limits=sweet_spot_x_limits,
                        temperature_limits=sweet_spot_temperature_limits,
                    )
            row_index += 1

        status_lines: list[str] = []
        for engine in HERO_PRIMARY_ENGINES:
            comparisons = data.hero_comparisons[(rail, engine)].get(
                "comparisons", ()
            )
            metric = data.hero_all_numeric_metrics.get((rail, engine))
            views = {} if metric is None else metric.get("views", {})
            views = views if isinstance(views, Mapping) else {}
            all_view = views.get("all", {})
            all_view = all_view if isinstance(all_view, Mapping) else {}
            all_numeric_count = _compact_count(all_view.get("n"))
            all_rms = _format_dex(all_view.get("rms_dex"))
            all_rms_text = (
                f"all-numeric RMS unavailable (n={all_numeric_count})"
                if all_rms == "unavailable"
                else f"all-numeric RMS {all_rms} dex (n={all_numeric_count})"
            )
            all_numeric_label = (
                f"{all_rms_text}, SD {_format_dex(all_view.get('sd_dex'))} dex "
                f"(n={all_numeric_count}), median |dex| "
                f"{_format_dex(all_view.get('median_abs_dex'))} dex (n={all_numeric_count})"
            )
            if all_view:
                all_numeric_label += (
                    f"; no T n={_compact_count(all_view.get('no_temperature_n'))}"
                )
            high_view = views.get("temperature_ge_1700", {})
            low_view = views.get("temperature_lt_1700", {})
            if isinstance(high_view, Mapping):
                all_numeric_label += "; " + _hero_metric_view_text(
                    HERO_TEMPERATURE_VIEWS[1][1], high_view
                )
            if isinstance(low_view, Mapping):
                all_numeric_label += "; " + _hero_metric_view_text(
                    HERO_TEMPERATURE_VIEWS[2][1], low_view
                )
            comparison_labels: list[str] = []
            for comparison in comparisons:
                if not isinstance(comparison, Mapping):
                    continue
                comparison_views = comparison.get("views", {})
                if not isinstance(comparison_views, Mapping):
                    comparison_views = {}
                for view_name, view_label in HERO_TEMPERATURE_VIEWS:
                    view = comparison_views.get(view_name)
                    if isinstance(view, Mapping):
                        comparison_labels.append(
                            _hero_comparison_view_text(
                                str(comparison.get("engine") or "unknown"),
                                engine,
                                view_label,
                                view,
                            )
                        )
            same_row_label = (
                "; ".join(comparison_labels)
                if comparison_labels
                else "no same-row comparisons (n_shared=0)"
            )
            status_lines.append(f"{engine}: {same_row_label} · {all_numeric_label}")
        if rail == Rail.VAPOUR.value:
            openimcc_record = data.hero_comparisons[(rail, "openimcc")]
            pair = next(
                (
                    comparison
                    for comparison in openimcc_record.get("comparisons", ())
                    if isinstance(comparison, Mapping)
                    and comparison.get("engine") == "vaporock"
                ),
                None,
            )
            if pair is not None:
                pair_views = pair.get("views", {})
                pair_views = pair_views if isinstance(pair_views, Mapping) else {}
                pair_text = "; ".join(
                    _hero_comparison_view_text(
                        "VapoRock",
                        "OpenIMCC",
                        label,
                        view,
                    )
                    for key, label in HERO_TEMPERATURE_VIEWS
                    if isinstance((view := pair_views.get(key)), Mapping)
                )
                status_lines.append(
                    "OpenIMCC vs VapoRock on shared vapour rows: " + pair_text
                )
            for record in data.hero_openimcc_vaporock_species:
                if record.get("rail") != rail:
                    continue
                comparisons = record.get("comparisons", ())
                comparison = next(
                    (
                        row
                        for row in comparisons
                        if isinstance(row, Mapping)
                        and row.get("engine") == "vaporock"
                    ),
                    None,
                ) if isinstance(comparisons, Sequence) else None
                if comparison is None:
                    continue
                views = comparison.get("views", {})
                views = views if isinstance(views, Mapping) else {}
                species_parts = []
                for key, label in HERO_TEMPERATURE_VIEWS:
                    view = views.get(key)
                    if not isinstance(view, Mapping):
                        continue
                    n_shared = int(view.get("n_shared") or 0)
                    if n_shared < HERO_MINIMUM_REPORT_N:
                        no_temperature_note = (
                            f"; no T n={_compact_count(view.get('no_temperature_n'))}"
                            if key == "all" and "no_temperature_n" in view
                            else ""
                        )
                        species_parts.append(
                            f"{label} n_shared={_compact_count(n_shared)} "
                            f"(<{HERO_MINIMUM_REPORT_N})"
                            f"{no_temperature_note}"
                        )
                    else:
                        species_parts.append(
                            _hero_comparison_view_text(
                                "VapoRock",
                                "OpenIMCC",
                                label,
                                view,
                            )
                        )
                status_lines.append(
                    f"OpenIMCC vs VapoRock shared vapour rows · {record.get('species')}: "
                    + "; ".join(species_parts)
                    + f" (per-species metrics require n≥{HERO_MINIMUM_REPORT_N})"
                )
        not_observable = [
            f"{engine} {_compact_count(diagnostic.get('not_observable_count'))}"
            for (diagnostic_rail, engine), diagnostic in sorted(
                data.hero_diagnostics.items()
            )
            if diagnostic_rail == rail
            and int(diagnostic.get("not_observable_count") or 0) > 0
        ]
        if not_observable:
            status_lines.append(
                "not an observable of this engine: " + ", ".join(not_observable)
            )
        title_artist = figure.suptitle(
            _HERO_TITLES[rail], fontsize=10, y=0.994
        )
        status_artist = figure.text(
            0.5,
            0.966,
            "\n".join(status_lines),
            ha="center",
            va="top",
            fontsize=5.4,
            linespacing=1.15,
            wrap=True,
        )
        legend_engine_names = {
            engine
            for series_rail, _facet, engine, _composition in data.hero_series
            if series_rail == rail
        } | {
            engine
            for refusal_rail, _facet, _composition, engine, _reason in data.hero_refusals
            if refusal_rail == rail
        }
        legend_engine_names.update(history_engine_names)
        legend_engine_names.update(
            engine
            for (sweet_spot_rail, engine), cells in data.hero_sweet_spot_cells.items()
            if sweet_spot_rail == rail
            and any(int(cell.get("n") or 0) for cell in cells)
        )
        flagged_points = any(
            point.flagged
            for (series_rail, _facet, _engine, _composition), points
            in data.hero_series.items()
            if series_rail == rail
            for point in points
        )
        legend_handles = _hero_legend_handles(
            legend_engine_names,
            engine_colors,
            flagged=flagged_points,
        )
        if sweet_spot_pairs:
            legend_handles.extend(_fingerprint_legend_handles())
        legend = None
        if legend_handles:
            legend = figure.legend(
                handles=legend_handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 0.916),
                ncol=min(6, len(legend_handles)),
                frameon=False,
                fontsize=6.0,
                handlelength=1.4,
                columnspacing=1.0,
            )
        _reserve_hero_status_space(
            figure,
            grid,
            title_artist,
            status_artist,
            legend,
            scatter_axes,
        )
        caption = (
            "Scatter marker face=engine colour; flagged/extrapolated=white face with engine-coloured edge. "
            "History: solid=RMS, dashed=median |dex|, dash-dot=SD/paired primary RMS, dotted=outside-engine RMS; n_shared is shown at each point. "
            "Fingerprint fill uses median |dex| traffic-light bands; n<3 cells are blank. "
            f"Temperature split uses measured residual field {HERO_TEMPERATURE_FIELD}; "
            f"{HERO_TEMPERATURE_FLOOR_K:g} K is in the ≥ view and rows without T "
            "are all-only with a no T count."
        )
        if openimcc_score_commit:
            caption += f" OpenIMCC SCORE commit: {openimcc_score_commit}."
        if rail == Rail.MELT_ACTIVITY.value:
            caption += (
                " ThermoEngine comparison caveat: 140/147 rows pair pure-solid "
                "data with its pure-liquid model reference; main's fix is pending."
            )
        if rail == Rail.VAPOUR.value:
            d139_counts = _d139_exclusion_counts(data, rail=rail)
            if d139_counts:
                counts = ", ".join(
                    f"{engine} {_compact_count(count)}"
                    for engine, count in d139_counts
                )
                caption += (
                    "\nVapour scatter excludes uncalibrated KEMS p_partial rows "
                    f"under d-139: {counts}; scorer all-numeric and same-row RMS include them."
                )
            openimcc_pack = _openimcc_default_pack(data) or "not recorded in score metadata"
            caption += (
                f"\nDefault pack at scored pin: {openimcc_pack}; a later OpenIMCC "
                "change will make v4 the default."
            )
        figure.text(
            0.5,
            0.018,
            caption,
            ha="center",
            va="bottom",
            fontsize=5.4,
        )
        path = output_dir / f"battery-hero-{rail}.svg"
        png_path = output_dir / f"battery-hero-{rail}.png"
        with matplotlib.rc_context({"svg.hashsalt": "battery-hero"}):
            figure.savefig(
                path,
                format="svg",
                bbox_inches="tight",
                metadata={"Date": None, "Creator": "battery_hero_plot"},
            )
        figure.savefig(
            png_path,
            format="png",
            dpi=150,
            bbox_inches="tight",
            metadata={"Software": "battery_hero_plot"},
        )
        plt.close(figure)
        files[f"hero:{rail}:svg"] = path
        files[f"hero:{rail}:png"] = png_path
    return files


def _hero_deck_comparison_caption(
    data: ScorePlotData, chart: str
) -> str:
    metric_entries: list[str] = []
    for metric_chart, engine in sorted(data.hero_deck_metrics):
        if metric_chart != chart:
            continue
        metric = data.hero_deck_metrics[(chart, engine)]
        views = metric.get("views", {})
        views = views if isinstance(views, Mapping) else {}
        for key, label in HERO_TEMPERATURE_VIEWS:
            view = views.get(key)
            if isinstance(view, Mapping):
                metric_entries.append(
                    f"{engine} {_hero_metric_view_text(label, view)}"
                )
    entries: list[str] = []
    for primary in HERO_PRIMARY_ENGINES:
        metric = data.hero_deck_metrics.get((chart, primary), {})
        record = data.hero_deck_comparisons.get((chart, primary), {})
        count = int(metric.get("n") or 0)
        comparisons = record.get("comparisons", ())
        if not isinstance(comparisons, Sequence) or isinstance(
            comparisons, (str, bytes)
        ):
            comparisons = ()
        if not comparisons:
            entries.append(
                f"{primary}: no shared chart rows (n_shared=0; scored n={_compact_count(count)})"
            )
            continue
        for comparison in comparisons:
            if not isinstance(comparison, Mapping):
                continue
            comparison_views = comparison.get("views", {})
            comparison_views = (
                comparison_views if isinstance(comparison_views, Mapping) else {}
            )
            if not comparison_views:
                shared_count = _compact_count(comparison.get("n_shared"))
                entries.append(
                    f"{primary} vs {comparison.get('engine')} RMS "
                    f"{_format_dex(comparison.get('outside_rms_dex'))} dex "
                    f"(n_shared={shared_count}) · {primary} RMS "
                    f"{_format_dex(comparison.get('primary_rms_dex_on_shared'))} dex "
                    f"(n_shared={shared_count})"
                )
                continue
            entries.extend(
                f"{primary} vs {comparison.get('engine')} "
                + _hero_comparison_view_text(
                    str(comparison.get("engine") or "unknown"),
                    primary,
                    label,
                    view,
                )
                + f" (scored n={_compact_count(count)})"
                for key, label in HERO_TEMPERATURE_VIEWS
                if isinstance((view := comparison_views.get(key)), Mapping)
            )
    metrics_label = (
        "Chart metrics (each number includes its n): " + "; ".join(metric_entries)
        if metric_entries
        else "No scored chart metrics."
    )
    return (
        "Same-row comparisons on chart rows: "
        + "; ".join(entries)
        + " · "
        + f"Temperature split uses measured residual field {HERO_TEMPERATURE_FIELD}; "
        f"{HERO_TEMPERATURE_FLOOR_K:g} K is included in the ≥ view, and rows "
        "without T are all-only. "
        + metrics_label
    )


def _hero_deck_refusal_entries(
    data: ScorePlotData, rail: str
) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (engine, reason, count)
        for (source_rail, engine, reason), count in sorted(
            data.hero_refusal_totals.items()
        )
        if source_rail == rail and count
    )


def render_hero_deck_charts(
    data: ScorePlotData,
    output_dir: Path,
    *,
    history_path: Path | None = None,
    openimcc_score_commit: str | None = None,
) -> dict[str, Path]:
    """Render the scorer-owned hero deck axes and explicit missing-data panels."""

    output_dir.mkdir(parents=True, exist_ok=True)
    history = read_history(history_path)
    engine_colors = _hero_engine_colors(data.engines)
    chart_configs = (
        (
            "polymerisation",
            "Polymerisation test",
            Rail.VAPOUR.value,
            "NBO/T",
            "signed dex",
        ),
        (
            "redox_sensitivity",
            "Redox sensitivity",
            Rail.VAPOUR.value,
            "ΔIW = log₁₀(fO₂) − IW",
            "signed dex",
        ),
        (
            "sio_evolution",
            "SiO evolution by pO₂ bin",
            Rail.SIO_EVOLUTION.value,
            "Temperature (K)",
            "signed dex",
        ),
        (
            "residue_composition",
            "Residue composition",
            Rail.RESIDUE_COMPOSITION.value,
            "Fraction evaporated",
            "signed dex",
        ),
        (
            "fo2_cross_comparison",
            "fO₂ cross-comparison",
            Rail.REDOX.value,
            "Measured Fe³⁺/Fe²⁺ ratio",
            "Predicted Fe³⁺/Fe²⁺ ratio",
        ),
    )
    file_slugs = {
        "polymerisation": "polymerisation",
        "redox_sensitivity": "redox-sensitivity",
        "sio_evolution": "sio-evolution",
        "residue_composition": "residue-composition",
        "fo2_cross_comparison": "fo2-cross-comparison",
    }
    files: dict[str, Path] = {}

    for chart, title, source_rail, x_label, y_label in chart_configs:
        points = [
            point for point in data.hero_deck_points if point.get("chart") == chart
        ]
        oxygen_modes = tuple(
            sorted({str(point.get("oxygen_mode") or "unknown") for point in points})
        )
        source_labels = tuple(
            (
                _HERO_OXYGEN_MODE_MARKERS[mode],
                _HERO_OXYGEN_MODE_LABELS[mode],
            )
            for mode in oxygen_modes
            if mode in _HERO_OXYGEN_MODE_MARKERS
        )
        mixed_sio_sources = chart == "sio_evolution" and len(oxygen_modes) > 1
        sio_bin_marker_sizes: dict[int, float] = {}
        if chart == "redox_sensitivity":
            points = [
                {
                    **point,
                    "marker": _HERO_OXYGEN_MODE_MARKERS.get(
                        str(point.get("oxygen_mode") or ""), "o"
                    ),
                }
                for point in points
            ]
        elif mixed_sio_sources:
            group_indices = sorted(
                {
                    int(point["group_index"])
                    for point in points
                    if point.get("group_index") is not None
                }
            )
            sio_bin_marker_sizes = {
                group: (
                    36.0
                    if len(group_indices) == 1
                    else 16.0 + 40.0 * index / (len(group_indices) - 1)
                )
                for index, group in enumerate(group_indices)
            }
            points = [
                {
                    **point,
                    "marker": _HERO_OXYGEN_MODE_MARKERS.get(
                        str(point.get("oxygen_mode") or ""), "o"
                    ),
                    "marker_size": sio_bin_marker_sizes[int(point["group_index"])],
                }
                for point in points
            ]
        current_groups: list[tuple[str, tuple[Mapping[str, object], ...]]] = []
        if chart == "redox_sensitivity":
            for species in ("Fe", "SiO", "Na"):
                current_groups.append(
                    (
                        species,
                        tuple(point for point in points if point.get("facet") == species),
                    )
                )
        elif chart == "polymerisation" and points:
            point_facets = {
                str(point.get("facet") or "unknown species") for point in points
            }
            hero_order = _hero_facets(data, source_rail)
            ordered_facets = tuple(
                facet for facet in hero_order if facet in point_facets
            ) + tuple(sorted(point_facets - set(hero_order)))
            current_groups.extend(
                (
                    facet,
                    tuple(
                        point
                        for point in points
                        if str(point.get("facet") or "unknown species") == facet
                    ),
                )
                for facet in ordered_facets
            )
        else:
            current_groups.append(("", tuple(points)))

        current_groups_per_row = (
            3 if chart in {"polymerisation", "redox_sensitivity"} else 1
        )
        current_row_count = max(
            1, math.ceil(len(current_groups) / current_groups_per_row)
        )

        sweet_spot_engines = tuple(
            engine
            for (rail, engine), cells in sorted(data.hero_sweet_spot_cells.items())
            if rail == source_rail and any(int(cell.get("n") or 0) for cell in cells)
        )
        sweet_spot_pairs = [
            tuple(sweet_spot_engines[index : index + 2])
            for index in range(0, len(sweet_spot_engines), 2)
        ]
        refusal_rails = (
            (source_rail, Rail.SIO_EVOLUTION.value)
            if chart == "redox_sensitivity"
            else (source_rail,)
        )
        axis_refusal_entries = tuple(
            (engine, reason, count)
            for (axis_chart, engine, reason), count
            in sorted(data.hero_deck_axis_refusals.items())
            if axis_chart == chart and count
        )
        refusal_entries = tuple(
            entry
            for rail in refusal_rails
            for entry in _hero_deck_refusal_entries(data, rail)
        ) + axis_refusal_entries
        refusal_reason_count = len(
            {reason for _engine, reason, _count in refusal_entries}
        )
        refusal_strip_height = max(0.55, refusal_reason_count * 0.15)
        row_heights = [1.65] * current_row_count + [
            refusal_strip_height,
            1.55,
        ]
        row_heights.extend(2.4 for _ in sweet_spot_pairs)
        figure = plt.figure(figsize=(14.0, max(7.0, sum(row_heights) / 0.78)))
        grid = figure.add_gridspec(
            nrows=len(row_heights),
            ncols=6,
            height_ratios=row_heights,
            left=0.055,
            right=0.92,
            top=0.86,
            bottom=0.075,
            hspace=0.38,
            wspace=0.34,
        )
        figure.suptitle(title, fontsize=10, y=0.985)
        caption = _hero_deck_comparison_caption(data, chart)
        openimcc_pack = _openimcc_default_pack(data) or "not recorded in score metadata"
        caption += (
            f" · Default pack at scored pin: {openimcc_pack}; a later "
            "OpenIMCC change will make v4 the default."
        )
        if openimcc_score_commit:
            caption += f" · OpenIMCC SCORE commit: {openimcc_score_commit}."
        if chart == "residue_composition":
            caption += " · fraction evaporated: no typed mass-loss value yet (t-1312)"
        elif chart in {"redox_sensitivity", "sio_evolution"}:
            modes = set(oxygen_modes)
            if modes == {"solved"}:
                caption += " · fO₂: solved cell oxygen balance (model), not measured"
            elif "solved" in modes:
                caption += (
                    " · fO₂ sources include solved cell oxygen balance (model), "
                    "not measured, and printed/commanded scorer conditions; "
                    "marker shapes distinguish source"
                )
            elif modes and modes <= {"printed", "commanded"}:
                caption += " · fO₂: printed/commanded scorer input from observations-v2"
        figure.text(
            0.5,
            0.935,
            caption,
            ha="center",
            va="center",
            fontsize=4.8,
            wrap=True,
        )

        if chart in {"polymerisation", "redox_sensitivity"}:
            for group_index, (species, species_points) in enumerate(current_groups):
                row = group_index // current_groups_per_row
                column = group_index % current_groups_per_row
                location = (
                    grid[row, :]
                    if chart == "polymerisation" and not species
                    else grid[row, column * 2 : (column + 1) * 2]
                )
                axis = figure.add_subplot(location)
                if species_points:
                    _draw_hero_deck_scatter(
                        axis, species_points, engine_colors=engine_colors
                    )
                elif chart == "polymerisation":
                    missing = sum(
                        count
                        for (missing_chart, _engine), count
                        in data.hero_deck_unavailable_axis_counts.items()
                        if missing_chart == chart
                    )
                    message = "No scorer-computed NBO/T rows have signed dex values."
                    if missing:
                        message += (
                            f"  Rows without the required axis: "
                            f"{_compact_count(missing)}."
                        )
                    _draw_hero_deck_unavailable(axis, message)
                else:
                    axis.set_axis_off()
                    axis.text(
                        0.5,
                        0.5,
                        f"No scored {species} row with a scorer-recorded oxygen condition",
                        transform=axis.transAxes,
                        ha="center",
                        va="center",
                        fontsize=7,
                    )
                if species:
                    axis.set_title(species, loc="left", fontsize=7)
                axis.set_xlabel(x_label, fontsize=6)
                axis.set_ylabel(y_label, fontsize=6)
        elif chart == "fo2_cross_comparison":
            unavailable_axis = figure.add_subplot(grid[0, 0:2])
            unavailable_axis.set_axis_off()
            unavailable_axis.text(
                0.5,
                0.5,
                "engine-computed fO2 not yet available",
                transform=unavailable_axis.transAxes,
                ha="center",
                va="center",
                fontsize=8,
                wrap=True,
            )
            unavailable_axis.set_title("Panel 1 · engine fO₂", loc="left", fontsize=7)
            axis = figure.add_subplot(grid[0, 2:6])
            if points:
                _draw_hero_deck_scatter(axis, points, engine_colors=engine_colors)
                values = [
                    value
                    for point in points
                    for value in (
                        _finite_float(point.get("x_value")),
                        _finite_float(point.get("y_value")),
                    )
                    if value is not None
                ]
                if values:
                    low, high = min(values), max(values)
                    padding = max((high - low) * 0.04, abs(high) * 0.02, 1e-6)
                    axis.plot(
                        [low - padding, high + padding],
                        [low - padding, high + padding],
                        color="#555555",
                        linestyle="--",
                        linewidth=0.8,
                        zorder=1,
                    )
            else:
                _draw_hero_deck_unavailable(
                    axis,
                    "No Fe³⁺/Fe²⁺ redox scores have both a measured composition and measured fO₂.",
                )
            axis.set_title("Panel 2 · Fe³⁺/Fe²⁺", loc="left", fontsize=7)
            axis.set_xlabel(x_label, fontsize=6)
            axis.set_ylabel(y_label, fontsize=6)
        else:
            axis = figure.add_subplot(grid[0, :])
            if chart == "residue_composition" and not points:
                missing = sum(
                    count
                    for (missing_chart, _engine), count
                    in data.hero_deck_unavailable_axis_counts.items()
                    if missing_chart == chart
                )
                message = "fraction evaporated: no typed mass-loss value yet (t-1312)."
                if missing:
                    message += (
                        f"  Scored residue rows without this axis: "
                        f"{_compact_count(missing)}."
                    )
                _draw_hero_deck_unavailable(
                    axis,
                    message,
                )
            elif points:
                if chart == "sio_evolution" and not mixed_sio_sources:
                    group_indices = sorted(
                        {
                            int(point["group_index"])
                            for point in points
                            if point.get("group_index") is not None
                        }
                    )
                    markers = {
                        group: _HERO_MARKERS[index % len(_HERO_MARKERS)]
                        for index, group in enumerate(group_indices)
                    }
                    points = [
                        {**point, "marker": markers[int(point["group_index"])]}
                        for point in points
                    ]
                _draw_hero_deck_scatter(axis, points, engine_colors=engine_colors)
            else:
                missing = sum(
                    count
                    for (missing_chart, _engine), count
                    in data.hero_deck_unavailable_axis_counts.items()
                    if missing_chart == chart
                )
                if chart == "redox_sensitivity":
                    message = (
                        "No scored Fe, SiO, or Na row has a scorer-recorded oxygen "
                        "condition; ΔIW cannot be computed."
                    )
                elif chart == "sio_evolution":
                    message = (
                        "No scored SiO row has a scorer-recorded oxygen condition "
                        "and a finite temperature; "
                        "pO₂ binning cannot be computed."
                    )
                else:
                    message = "No scorer-computed NBO/T rows have signed dex values."
                if missing:
                    message += f"  Rows without the required axis: {_compact_count(missing)}."
                _draw_hero_deck_unavailable(axis, message)
            axis.set_xlabel(x_label, fontsize=6)
            axis.set_ylabel(y_label, fontsize=6)

        refusal_axis = figure.add_subplot(grid[current_row_count, :])
        _draw_hero_refusals(
            refusal_axis,
            refusal_entries,
            engine_colors=engine_colors,
        )
        if refusal_axis.axison:
            refusal_axis.set_title("Refused scorer rows (not-observable rows separate)", loc="left", fontsize=6)

        history_axis = figure.add_subplot(grid[current_row_count + 1, :])
        history_engine_names = _draw_hero_history(
            history_axis,
            source_rail,
            history,
            engine_colors=engine_colors,
        )
        history_axis.set_xlabel("")

        for pair_index, pair in enumerate(sweet_spot_pairs):
            row_index = current_row_count + 2 + pair_index
            for column, engine in enumerate(pair):
                axis = figure.add_subplot(
                    grid[row_index, column * 3 : (column + 1) * 3]
                )
                cells = data.hero_sweet_spot_cells[(source_rail, engine)]
                _draw_hero_sweet_spot(
                    axis,
                    engine,
                    cells,
                    x_limits=(0.0, 1.0),
                    temperature_limits=data.x_limits,
                )
            if len(pair) == 1:
                blank = figure.add_subplot(grid[row_index, 3:6])
                blank.set_axis_off()

        group_labels: list[tuple[str, str]] = []
        size_labels: list[tuple[float, str]] = []
        if chart == "sio_evolution" and points:
            seen_groups: dict[int, str] = {}
            for point in points:
                if point.get("group_index") is not None:
                    seen_groups[int(point["group_index"])] = str(point["group_label"])
            groups = sorted(seen_groups.items())
            if mixed_sio_sources:
                size_labels = [
                    (sio_bin_marker_sizes[group], label)
                    for group, label in groups
                ]
            else:
                group_labels = [
                    (_HERO_MARKERS[index % len(_HERO_MARKERS)], label)
                    for index, (_group, label) in enumerate(groups)
                ]
        legend_engine_names = {
            str(point.get("engine") or "unknown") for point in points
        }
        legend_engine_names.update(
            engine for engine, _reason, _count in refusal_entries
        )
        legend_engine_names.update(history_engine_names)
        legend_engine_names.update(sweet_spot_engines)
        handles = _hero_legend_handles(
            legend_engine_names,
            engine_colors,
            flagged=any(bool(point.get("flagged")) for point in points),
            group_labels=group_labels,
            source_labels=(
                source_labels
                if chart == "redox_sensitivity" or mixed_sio_sources
                else ()
            ),
            size_labels=size_labels,
        )
        if sweet_spot_pairs:
            handles.extend(_fingerprint_legend_handles())
        if handles:
            figure.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 0.902),
                ncol=min(max(1, len(handles)), 8),
                frameon=False,
                fontsize=5.5,
            )

        figure.text(
            0.5,
            0.025,
            "Scored rows only; fingerprint colours show median |dex| bands and cells with n<3 are blank.",
            ha="center",
            va="bottom",
            fontsize=5.4,
        )
        path = output_dir / f"battery-hero-{file_slugs[chart]}.svg"
        png_path = output_dir / f"battery-hero-{file_slugs[chart]}.png"
        with matplotlib.rc_context({"svg.hashsalt": "battery-hero-deck"}):
            figure.savefig(
                path,
                format="svg",
                bbox_inches="tight",
                metadata={"Date": None, "Creator": "battery_hero_plot"},
            )
        figure.savefig(
            png_path,
            format="png",
            dpi=150,
            bbox_inches="tight",
            metadata={"Software": "battery_hero_plot"},
        )
        plt.close(figure)
        files[f"deck:{file_slugs[chart]}:svg"] = path
        files[f"deck:{file_slugs[chart]}:png"] = png_path
    return files


def _draw_hero_deck_unavailable(axis: Axes, message: str) -> None:
    axis.set_axis_off()
    axis.text(
        0.5,
        0.55,
        "axis unavailable",
        transform=axis.transAxes,
        ha="center",
        va="center",
        fontsize=8,
        fontweight="bold",
    )
    axis.text(
        0.5,
        0.38,
        message,
        transform=axis.transAxes,
        ha="center",
        va="center",
        fontsize=6,
        wrap=True,
    )


def render_plots(
    data: ScorePlotData,
    output_dir: Path,
    *,
    simulator_commit: str,
    openimcc_commit: str,
    render_date: str | None = None,
    group_by: Sequence[str] = DEFAULT_GROUP_BY,
    abundance_feedstock: str = DEFAULT_ABUNDANCE_FEEDSTOCK,
    omit_empty_engines: bool | None = None,
    history_path: Path | None = None,
    openimcc_score_commit: str | None = None,
    source_path: Path | None = None,
    annotations: Mapping[str, str] | None = None,
    fix_status_lines: Sequence[str] = (),
) -> dict[str, Path]:
    """Write shared-scale species/element tiles and one PNG per engine/view."""

    if source_path is not None:
        if tuple(group_by) != DEFAULT_GROUP_BY:
            raise ValueError(
                "group_by/--group-by is unsupported by the collection renderer; "
                "use its fixed A–G observable views"
            )
        if omit_empty_engines is not None:
            raise ValueError(
                    "omit_empty_engines/--omit-empty-engines is unsupported by the collection renderer"
            )
        return render_hero_collection(
            data,
            source_path,
            output_dir,
            simulator_commit=simulator_commit,
            openimcc_commit=openimcc_commit,
            render_date=render_date,
            history_path=history_path,
            annotations=annotations,
            fix_status_lines=fix_status_lines,
        )
    if not data.engines:
        raise ValueError("no engines found in score JSONL")
    views = tuple(dict.fromkeys(group_by))
    if not views or any(view not in {"species", "element"} for view in views):
        raise ValueError("group_by must contain species, element, or both")
    render_date = render_date or datetime.now(timezone.utc).date().isoformat()
    abundance = load_element_abundance(abundance_feedstock)
    output_dir.mkdir(parents=True, exist_ok=True)
    revision = _revision_slug(data.stamp.get("revision"))
    stem = f"battery-hero-{revision}-{render_date}"
    footer = stamp_text(
        data,
        simulator_commit=simulator_commit,
        openimcc_commit=openimcc_commit,
        render_date=render_date,
    )
    d139_exclusions = [
        f"{engine} {count:,} uncalibrated KEMS p_partial"
        for engine, count in _d139_exclusion_counts(data)
    ]
    if d139_exclusions:
        footer += "  |  excluded under d-139: " + ", ".join(d139_exclusions)
    files: dict[str, Path] = {}
    empty_engines = _empty_engine_names(data)
    tile_engines = (
        data.engines
        if omit_empty_engines is False
        else tuple(engine for engine in data.engines if engine not in empty_engines)
    )
    engine_image_engines = (
        tuple(engine for engine in data.engines if engine not in empty_engines)
        if omit_empty_engines is True
        else data.engines
    )
    if not tile_engines:
        raise ValueError("no engines with scored dex rows for tile")
    omitted_note = _no_scored_rows_note(
        data,
        empty_engines if omit_empty_engines is not False else (),
    )
    for view in views:
        columns = min(2, len(tile_engines))
        rows = math.ceil(len(tile_engines) / columns)
        figure, axes_grid = plt.subplots(
            rows,
            columns,
            figsize=(16.0, 3.2 * rows + 1.0),
            squeeze=False,
            sharex=True,
            sharey=True,
        )
        axes = list(axes_grid.flat)
        for axis, engine in zip(axes, tile_engines):
            draw_panel(axis, data, engine, abundance, view=view)
        for axis in axes[len(tile_engines) :]:
            axis.set_visible(False)
        figure.suptitle(
            f"Battery score · {view} view · binned signed residuals",
            fontsize=11,
            y=0.995,
        )
        view_note = (
            "Element lines pool pressure and activity residuals; both are log10(predicted / measured), in dex."
            if view == "element"
            else f"Rails: {', '.join(data.selected_rails)} · pressure is solid, melt activity is dashed."
        )
        figure.text(0.5, 0.962, view_note, ha="center", va="center", fontsize=7)
        figure.legend(
            handles=_legend_handles(),
            loc="upper center",
            bbox_to_anchor=(0.5, 0.935),
            ncol=6,
            frameon=False,
            fontsize=7,
        )
        if omitted_note:
            figure.text(0.5, 0.026, omitted_note, ha="center", va="bottom", fontsize=5.5)
        figure.text(0.5, 0.008, footer, ha="center", va="bottom", fontsize=6)
        figure.subplots_adjust(
            left=0.06,
            right=0.96,
            top=0.80,
            bottom=0.10,
            hspace=0.58,
            wspace=0.32,
        )

        tile_png = output_dir / f"{stem}-{view}-tile.png"
        tile_svg = output_dir / f"{stem}-{view}-tile.svg"
        figure.savefig(tile_png, dpi=100, bbox_inches="tight")
        figure.savefig(tile_svg, format="svg", bbox_inches="tight")
        plt.close(figure)
        files[f"{view}:tile_png"] = tile_png
        files[f"{view}:tile_svg"] = tile_svg

        for engine in engine_image_engines:
            figure, axis = plt.subplots(figsize=(12.0, 4.8))
            draw_panel(axis, data, engine, abundance, view=view)
            figure.suptitle(f"Battery score · {view} view", fontsize=11, y=0.995)
            view_note = (
                "Element lines pool pressure and activity residuals; both are log10(predicted / measured), in dex."
                if view == "element"
                else f"Rails: {', '.join(data.selected_rails)} · pressure is solid, melt activity is dashed."
            )
            figure.text(0.5, 0.95, view_note, ha="center", va="center", fontsize=8)
            figure.legend(
                handles=_legend_handles(),
                loc="upper center",
                bbox_to_anchor=(0.5, 0.915),
                ncol=6,
                frameon=False,
                fontsize=7,
            )
            figure.text(0.5, 0.015, footer, ha="center", va="bottom", fontsize=6)
            figure.subplots_adjust(left=0.09, right=0.93, top=0.79, bottom=0.20)
            engine_png = output_dir / f"{stem}-{view}-{_revision_slug(engine)}.png"
            figure.savefig(engine_png, dpi=120, bbox_inches="tight")
            plt.close(figure)
            files[f"{view}:engine:{engine}"] = engine_png
    if history_path is not None:
        files.update(
            render_hero_charts(
                data,
                output_dir,
                history_path=history_path,
                openimcc_score_commit=openimcc_score_commit,
            )
        )
        files.update(
            render_hero_deck_charts(
                data,
                output_dir,
                history_path=history_path,
                openimcc_score_commit=openimcc_score_commit,
            )
        )
    return files


def _summary_payload(
    data: ScorePlotData,
    files: Mapping[str, Path],
    abundance: ElementAbundance,
    *,
    omitted_engines: Sequence[str] = (),
    history: Sequence[Mapping[str, Any]] = (),
    openimcc_score_commit: str | None = None,
) -> dict[str, Any]:
    engines: dict[str, Any] = {}
    for engine in data.engines:
        summary = data.summaries[engine]
        engines[engine] = {
            "scored_rows": summary.scored_rows,
            "deduplicated_observations": summary.unique_points,
            "species_count": len(summary.species),
            "temperature_span_K": [summary.min_temperature_K, summary.max_temperature_K],
            "rms_dex": summary.rms_dex,
            "refused_by_reason": dict(sorted(summary.refusals.items())),
            "refused_by_rail": dict(sorted(summary.refused_by_rail.items())),
            "not_observable_by_rail": dict(
                sorted(summary.not_observable_by_rail.items())
            ),
            "non_dex_rows_skipped": summary.non_dex_rows,
            "missing_identity_rows_skipped": summary.missing_identity_rows,
            "source_relation_counts": dict(sorted(summary.source_relations.items())),
            "versions": sorted(summary.versions),
            "default_packs": sorted(summary.default_packs),
        }
    return {
        "store_revision": data.stamp.get("revision"),
        "openimcc_score_commit": openimcc_score_commit,
        "openimcc_default_pack_at_scored_pin": _openimcc_default_pack(data),
        "openimcc_default_pack_note": (
            "A later OpenIMCC change will make v4 the default."
        ),
        "engines": engines,
        "rails_seen": list(data.rails_seen),
        "selected_rails": list(data.selected_rails),
        "bin_width_K": data.bin_width_K,
        "x_limits_K": list(data.x_limits),
        "abundance_feedstock": abundance.feedstock_id,
        "element_abundance_classes": abundance_class_table(data, abundance),
        "omitted_empty_engines": list(omitted_engines),
        "no_scored_rows_footer": _no_scored_rows_note(data, omitted_engines),
        "hero_charts": _hero_summary_payload(data),
        "hero_deck_charts": _hero_deck_summary_payload(data),
        "files": {name: str(path) for name, path in files.items()},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="scorer residuals.jsonl, compact .jsonl.gz, or battery .tgz archive",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs-private/research/2026-10-16-battery-hero-plot"),
        help="directory for generated images (default: private research directory)",
    )
    parser.add_argument("--bin-width-k", type=float, default=DEFAULT_BIN_WIDTH_K)
    parser.add_argument(
        "--abundance-feedstock",
        default=DEFAULT_ABUNDANCE_FEEDSTOCK,
        help=(
            "feedstock whose oxide abundance sets line classes "
            f"(default: {DEFAULT_ABUNDANCE_FEEDSTOCK})"
        ),
    )
    parser.add_argument(
        "--omit-empty-engines",
        action="store_true",
        default=None,
        help=(
            "omit engines without scored rows from the tile (default); "
            "per-engine images include them unless enabled"
        ),
    )
    parser.add_argument(
        "--rail",
        action="append",
        help="include a rail; repeat or comma-separate (default: vapour, melt_activity, SiO_evolution)",
    )
    parser.add_argument(
        "--group-by",
        nargs="+",
        choices=("species", "element"),
        default=DEFAULT_GROUP_BY,
        metavar="VIEW",
        help="render one or both views (default: species element)",
    )
    parser.add_argument("--simulator-commit", required=True)
    parser.add_argument("--openimcc-commit", required=True)
    parser.add_argument(
        "--exposure-census",
        type=Path,
        help="CSV of diagnostic-exposed rows by observation_id",
    )
    parser.add_argument(
        "--exposed-sources",
        type=Path,
        help="CSV of diagnostic-exposed source IDs",
    )
    parser.add_argument(
        "--pre-cutoff-observation-ids",
        type=Path,
        help="newline-separated observation IDs present at the exposure cutoff",
    )
    parser.add_argument("--render-date", help="UTC date for stamps and filenames (default: today)")
    parser.add_argument(
        "--history-path",
        type=Path,
        required=True,
        help="append-only hero chart history JSONL (for example docs-private/.../hero-history.jsonl)",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        help="JSON caller notes with annotations (landing sha/date to label) and fix_status lines",
    )
    parser.add_argument(
        "--append-history",
        action="store_true",
        help="append this archive's scorer summary as one history row",
    )
    parser.add_argument(
        "--landing-sha",
        help="green landing commit for --append-history (date is read from git)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    exposure_policy = _exposure_policy_from_files(
        census_path=args.exposure_census,
        exposed_sources_path=args.exposed_sources,
        baseline_observation_ids_path=args.pre_cutoff_observation_ids,
    )
    data = read_scores(
        args.input,
        bin_width_K=args.bin_width_k,
        rails=args.rail,
        exposure_policy=exposure_policy,
        _collection_mode=True,
    )
    if args.append_history:
        if not args.landing_sha:
            raise SystemExit("--append-history requires --landing-sha")
        summary = _archive_json_member(args.input, "data/battery/score-summary.json")
        record = _history_record(
            summary,
            landing_sha=args.landing_sha,
            comparisons=_collection_history_comparisons(data),
            diagnostics=data.hero_diagnostics,
            refusal_coverage=_refusal_coverage_from_summaries(data.summaries),
            hero_metrics={
                **data.hero_all_numeric_metrics,
                **data.hero_deck_metrics,
            },
            openimcc_score_commit=args.openimcc_commit,
            openimcc_vaporock_species=data.hero_openimcc_vaporock_species,
        )
        append_history(args.history_path, record)
    abundance = load_element_abundance(args.abundance_feedstock)
    annotations, fix_status_lines = _read_annotations_file(args.annotations)
    files = render_plots(
        data,
        args.output_dir,
        simulator_commit=args.simulator_commit,
        openimcc_commit=args.openimcc_commit,
        render_date=args.render_date,
        group_by=args.group_by,
        abundance_feedstock=args.abundance_feedstock,
        omit_empty_engines=args.omit_empty_engines,
        history_path=args.history_path,
        openimcc_score_commit=args.openimcc_commit,
        source_path=args.input,
        annotations=annotations,
        fix_status_lines=fix_status_lines,
    )
    omitted_engines = (
        _empty_engine_names(data)
        if args.omit_empty_engines is not False
        else ()
    )
    history = read_history(args.history_path)
    print(
        json.dumps(
            _summary_payload(
                data,
                files,
                abundance,
                omitted_engines=omitted_engines,
                history=history,
                openimcc_score_commit=args.openimcc_commit,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
