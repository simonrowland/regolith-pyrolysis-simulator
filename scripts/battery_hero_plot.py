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
has ``schema_version`` and, per rail and engine, the scorer's numeric count,
RMS and median |dex|, attempted refusal count and scorer-reason counts,
non-observable count, and open predict-and-flag notice counts by kind. Each row
also carries all-rail attempted-refusal and non-observable counts per engine.
Same-row benchmark RMS is stored separately for openimcc and internal-analytical
against the best comparison engine (vaporock, thermoengine, alphamelts, or
magemin); unavailable benchmarks have a null engine and RMS with ``n=0``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.axes import Axes
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, FormatStrFormatter

from simulator.battery.enums import (  # noqa: E402
    Authority,
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
    _flagged_payload_strata,
    _battery_hero_sio2_mole_fraction,
    _battery_hero_sweet_spot_records,
    _paired_engine_rail_benchmarks,
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
HERO_PRIMARY_ENGINES = ("openimcc", "internal-analytical")
HERO_COMPARISON_ENGINES = ("vaporock", "thermoengine", "alphamelts", "magemin")
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
TIER_STYLES: dict[str, dict[str, Any]] = {
    TIER_MAJOR: {"color": "#111111", "linewidth": 2.0, "markersize": 3.5},
    TIER_MINOR: {"color": "#777777", "linewidth": 1.2, "markersize": 3.0},
    TIER_TRACE: {"color": "#b9b9b9", "linewidth": 0.5, "markersize": 2.5},
}
_OUT_OF_BAND_NOTICE_KINDS = {
    NoticeKind.OUT_OF_CERTIFIED_BAND.value,
    NoticeKind.OUT_OF_GAMMA_DOMAIN.value,
}


@dataclass
class EngineSummary:
    scored_rows: int = 0
    unique_points: int = 0
    species: set[str] = field(default_factory=set)
    min_temperature_K: float | None = None
    max_temperature_K: float | None = None
    residual_square_sum: float = 0.0
    rms_count: int = 0
    refusals: Counter[str] = field(default_factory=Counter)
    refused_by_rail: Counter[str] = field(default_factory=Counter)
    not_observable_by_rail: Counter[str] = field(default_factory=Counter)
    excluded_d139_rows: int = 0
    non_dex_rows: int = 0
    missing_identity_rows: int = 0
    source_relations: Counter[str] = field(default_factory=Counter)
    versions: set[str] = field(default_factory=set)

    @property
    def rms_dex(self) -> float | None:
        if not self.rms_count:
            return None
        return math.sqrt(self.residual_square_sum / self.rms_count)


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
    hero_benchmarks: Mapping[tuple[str, str], Mapping[str, object]]
    hero_sweet_spot_cells: Mapping[
        tuple[str, str], tuple[Mapping[str, object], ...]
    ]
    x_limits: tuple[float, float]
    bin_width_K: float


@dataclass(frozen=True)
class ElementAbundance:
    feedstock_id: str
    oxide_wt_pct: Mapping[str, float]
    feedstock_elements: frozenset[str]


@dataclass
class _BinAccumulator:
    residual_sum: float = 0.0
    temperature_sum: float = 0.0
    n: int = 0
    flagged: bool = False


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


def _hero_facet(rail: str, formula: str, observable: str) -> str:
    if rail == Rail.REDOX.value:
        return observable if observable != "unknown" else "unknown observable"
    return formula if formula else "unknown species"


def _minor_foulant_facets(formula: str) -> tuple[str, ...]:
    elements = set(parse_formula_elements(formula) or ())
    facets = [f"minor:{element}" for element in ("P", "S", "Cl", "C") if element in elements]
    if elements.intersection({"Na", "K"}):
        facets.append("minor:alkali_salts")
    return tuple(facets)


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


def _has_out_of_band_flag(row: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    for source in (row.get("notices"), observation.get("notices")):
        if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
            continue
        for notice in source:
            if isinstance(notice, Mapping) and notice.get("kind") in _OUT_OF_BAND_NOTICE_KINDS:
                return True
    return False


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
) -> ScorePlotData:
    """Stream the score JSONL and return binned, duplicate-collapsed data."""

    if not math.isfinite(bin_width_K) or bin_width_K <= 0:
        raise ValueError("bin width must be a finite positive number of kelvin")
    selected_rails = _requested_rails(rails)
    selected = set(selected_rails)
    retained_rails = selected | set(HERO_RAILS)
    summaries: dict[str, EngineSummary] = defaultdict(EngineSummary)
    rails_seen: set[str] = set()
    benchmark_payloads: list[Mapping[str, object]] = []
    sweet_spot_samples: list[tuple[str, str, object, object, object]] = []
    hero_composition_sio2: dict[str, float] = {}
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
                authority TEXT NOT NULL,
                out_of_band INTEGER NOT NULL,
                residual_dex REAL NOT NULL,
                composition_key TEXT NOT NULL,
                composition_label TEXT NOT NULL,
                observable TEXT NOT NULL
            )"""
        )

        pending: list[
            tuple[str, str, str, str, str, float, str, int, float, str, str, str]
        ] = []
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

                rail_value = row.get("rail")
                rail = str(rail_value) if rail_value is not None else ""
                if rail:
                    rails_seen.add(rail)

                status = residual_status_token(row.get("status"))
                engine_observable = (
                    status is not ResidualStatus.REFUSED
                    or _residual_engine_observable(row)
                )
                if rail in HERO_RAILS:
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
                    if rail in HERO_RAILS and engine_observable:
                        formula = _row_formula(row)
                        identity = _row_identity(row)
                        composition_key, _composition_label = _composition_fields(identity)
                        observable = _row_observable(row)
                        facets = [_hero_facet(rail, formula, observable)]
                        if rail == Rail.VAPOUR.value:
                            facets.extend(_minor_foulant_facets(formula))
                        for facet in facets:
                            hero_refusals[
                                (rail, facet, composition_key, engine, reason)
                            ] += 1
                        hero_refusal_totals[(rail, engine, reason)] += 1
                    continue

                numeric = row.get("numeric")
                if not isinstance(numeric, Mapping) or numeric.get("value") is None:
                    continue
                if rail not in retained_rails:
                    continue
                if numeric.get("operation") != "dex":
                    if rail in selected:
                        summary.non_dex_rows += 1
                    continue
                if rail in HERO_RAILS:
                    benchmark_payloads.append(row)

                identity = _row_identity(row)
                if not identity:
                    if rail in selected:
                        summary.missing_identity_rows += 1
                    continue
                quantity = _state_value(identity.get("quantity"))
                if numeric.get("decision_band") is None and quantity == "p_partial":
                    notices = (
                        row.get("notices"),
                        candidate_observation.get("notices"),
                    )
                    has_calibration_notice = any(
                        isinstance(notice, Mapping)
                        and notice.get("kind") == NoticeKind.UNVERIFIED_APPARATUS.value
                        and isinstance(notice.get("reason"), str)
                        and notice["reason"].startswith("calibration_not_grounded:")
                        for source in notices
                        if isinstance(source, Sequence)
                        and not isinstance(source, (str, bytes))
                        for notice in source
                    )
                    if has_calibration_notice:
                        # Use the scorer's d-139 output marker; do not infer calibration here.
                        if rail in selected:
                            summary.excluded_d139_rows += 1
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

                authority = str(candidate_observation.get("authority") or "unknown")
                out_of_band = _has_out_of_band_flag(row, candidate_observation)
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
                if rail in HERO_RAILS:
                    composition_raw = _state_value(identity.get("composition"))
                    sio2_fraction = _battery_hero_sio2_mole_fraction(composition_raw)
                    if sio2_fraction is not None:
                        hero_composition_sio2[composition_key] = float(sio2_fraction)
                    sweet_spot_samples.append(
                        (
                            rail,
                            engine,
                            temperature_raw,
                            composition_raw,
                            numeric.get("value"),
                        )
                    )
                observable = _row_observable(row)
                pending.append(
                    (
                        engine,
                        formula,
                        rail,
                        str(experiment),
                        temperature_key,
                        temperature,
                        authority,
                        int(out_of_band),
                        residual,
                        composition_key,
                        composition_label,
                        observable,
                    )
                )
                if len(pending) >= 5000:
                    database.executemany(
                        "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        pending,
                    )
                    pending.clear()

        if pending:
            database.executemany(
                "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                pending,
            )
        database.commit()
        if stamp is None:
            raise ValueError(f"{path}: empty score file")

        bins: dict[
            tuple[str, str, str, str, str, str, int], _BinAccumulator
        ] = {}
        hero_bins: dict[
            tuple[str, str, str, str, str, str, int], _BinAccumulator
        ] = {}
        for row in database.execute(
            """SELECT engine, species, rail, composition_key, composition_label,
                      observable, MIN(temperature_K),
                      MAX(CASE WHEN authority != ? OR out_of_band = 1 THEN 1 ELSE 0 END),
                      AVG(residual_dex)
                 FROM scored
                GROUP BY engine, species, rail, composition_key, composition_label,
                         observable, experiment, temperature_key
                ORDER BY engine, species, rail, composition_key, observable,
                         MIN(temperature_K)""",
            (Authority.CERTIFIED.value,),
        ):
            (
                engine,
                formula,
                rail,
                composition_key,
                composition_label,
                observable,
                temperature,
                flagged,
                residual,
            ) = row
            if rail in selected:
                summary = summaries[engine]
                summary.unique_points += 1
                summary.residual_square_sum += float(residual) ** 2
                summary.rms_count += 1
            bin_index = math.floor(float(temperature) / bin_width_K)
            if rail in selected:
                key = (
                    engine,
                    formula,
                    rail,
                    composition_key,
                    composition_label,
                    observable,
                    bin_index,
                )
                accumulator = bins.setdefault(key, _BinAccumulator())
                accumulator.residual_sum += float(residual)
                accumulator.temperature_sum += float(temperature)
                accumulator.n += 1
                accumulator.flagged = accumulator.flagged or bool(flagged)
            if rail in HERO_RAILS:
                facets = [_hero_facet(rail, formula, observable)]
                if rail == Rail.VAPOUR.value:
                    facets.extend(_minor_foulant_facets(formula))
                for facet in facets:
                    key = (
                        rail,
                        facet,
                        engine,
                        composition_key,
                        composition_label,
                        observable,
                        bin_index,
                    )
                    accumulator = hero_bins.setdefault(key, _BinAccumulator())
                    accumulator.residual_sum += float(residual)
                    accumulator.temperature_sum += float(temperature)
                    accumulator.n += 1
                    accumulator.flagged = accumulator.flagged or bool(flagged)
        database.close()

    if stamp is None:
        raise ValueError(f"{path}: missing battery store stamp")

    grouped: dict[tuple[str, str, str], list[BinPoint]] = defaultdict(list)
    for key, accumulator in bins.items():
        engine, formula, rail, composition_key, composition_label, observable, bin_index = key
        grouped[(engine, formula, rail)].append(
            BinPoint(
                bin_index=bin_index,
                temperature_K=accumulator.temperature_sum / accumulator.n,
                residual_dex=accumulator.residual_sum / accumulator.n,
                n=accumulator.n,
                flagged=accumulator.flagged,
                composition_key=composition_key,
                composition_label=composition_label,
                observable=observable,
            )
        )
    series = {
        key: tuple(sorted(points, key=lambda point: point.bin_index))
        for key, points in grouped.items()
    }
    hero_grouped: dict[tuple[str, str, str, str], list[BinPoint]] = defaultdict(list)
    for key, accumulator in hero_bins.items():
        rail, facet, engine, composition_key, composition_label, observable, bin_index = key
        hero_grouped[(rail, facet, engine, composition_key)].append(
            BinPoint(
                bin_index=bin_index,
                temperature_K=accumulator.temperature_sum / accumulator.n,
                residual_dex=accumulator.residual_sum / accumulator.n,
                n=accumulator.n,
                flagged=accumulator.flagged,
                composition_key=composition_key,
                composition_label=composition_label,
                observable=observable,
                composition_sio2_mole_fraction=hero_composition_sio2.get(
                    composition_key
                ),
            )
        )
    hero_series = {
        key: tuple(sorted(points, key=lambda point: point.bin_index))
        for key, points in hero_grouped.items()
    }
    engines = tuple(sorted(summaries, key=lambda name: (name != "openimcc", name.casefold())))
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

    hero_benchmarks = {
        (str(record["rail"]), str(record["primary_engine"])): record
        for record in _paired_engine_rail_benchmarks(
            benchmark_payloads,
            primary_engines=HERO_PRIMARY_ENGINES,
            comparison_engines=HERO_COMPARISON_ENGINES,
            rails=HERO_RAILS,
        )
    }
    hero_sweet_spot_groups: dict[
        tuple[str, str], list[Mapping[str, object]]
    ] = defaultdict(list)
    for cell in _battery_hero_sweet_spot_records(sweet_spot_samples):
        hero_sweet_spot_groups[(str(cell["rail"]), str(cell["engine"]))].append(
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
        hero_benchmarks=hero_benchmarks,
        hero_sweet_spot_cells={
            key: tuple(cells)
            for key, cells in sorted(hero_sweet_spot_groups.items())
        },
        x_limits=x_limits,
        bin_width_K=bin_width_K,
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
) -> tuple[
    dict[tuple[str, str], Mapping[str, object]],
    dict[tuple[str, str], Mapping[str, object]],
    list[dict[str, object]],
]:
    rows: list[Mapping[str, object]] = []
    diagnostics: dict[tuple[str, str], dict[str, object]] = {}
    engines: set[str] = set()
    rails: set[str] = {rail.value for rail in Rail}
    refusal_coverage: Counter[tuple[str, str, str]] = Counter()
    with _open_residual_jsonl(path) as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                continue
            rail = str(row.get("rail") or "")
            engine = _engine_name(row)
            if not rail or not engine:
                continue
            engines.add(engine)
            rails.add(rail)
            status = residual_status_token(row.get("status"))
            engine_observable = (
                status is not ResidualStatus.REFUSED
                or _residual_engine_observable(row)
            )
            if status is ResidualStatus.REFUSED:
                bucket = "refused" if engine_observable else "not_observable"
                refusal_coverage[(rail, engine, bucket)] += 1
            if rail in HERO_RAILS:
                _record_hero_diagnostic(
                    diagnostics,
                    row,
                    rail=rail,
                    engine=engine,
                    status=status,
                    engine_observable=engine_observable,
                )
            numeric = row.get("numeric")
            if rail in HERO_RAILS and (
                isinstance(numeric, Mapping)
                and numeric.get("operation") == "dex"
                and numeric.get("value") is not None
            ):
                rows.append(row)
    benchmarks = {
        (str(record["rail"]), str(record["primary_engine"])): record
        for record in _paired_engine_rail_benchmarks(
            rows,
            primary_engines=HERO_PRIMARY_ENGINES,
            comparison_engines=HERO_COMPARISON_ENGINES,
            rails=HERO_RAILS,
        )
    }
    coverage = [
        {
            "rail": rail,
            "engine": engine,
            "refused_count": refusal_coverage[(rail, engine, "refused")],
            "not_observable_count": refusal_coverage[
                (rail, engine, "not_observable")
            ],
        }
        for rail in sorted(rails)
        for engine in sorted(engines)
    ]
    return benchmarks, _normalized_hero_diagnostics(diagnostics), coverage


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
    benchmarks: Mapping[tuple[str, str], Mapping[str, object]],
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]],
    refusal_coverage: Sequence[Mapping[str, object]] = (),
    commit_date: str | None = None,
) -> dict[str, Any]:
    records = summary.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("score-summary.json has no records list")
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
        and record.get("rail") in HERO_RAILS
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
        "schema_version": 1,
        "store_sha": landing_sha,
        "store_revision": store.get("revision"),
        "commit_date": commit_date or _git_commit_date(landing_sha),
        "metrics": metrics,
        "benchmarks": [benchmarks[key] for key in sorted(benchmarks)],
        "refusal_coverage": [dict(item) for item in refusal_coverage],
    }


def history_record_from_archive(
    archive_path: Path,
    *,
    landing_sha: str,
    benchmarks: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    refusal_coverage: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    summary = _archive_json_member(archive_path, "data/battery/score-summary.json")
    if benchmarks is None or diagnostics is None or refusal_coverage is None:
        streamed_benchmarks, streamed_diagnostics, streamed_coverage = (
            _history_fields_from_scores(archive_path)
        )
        if benchmarks is None:
            benchmarks = streamed_benchmarks
        if diagnostics is None:
            diagnostics = streamed_diagnostics
        if refusal_coverage is None:
            refusal_coverage = streamed_coverage
    return _history_record(
        summary,
        landing_sha=landing_sha,
        benchmarks=benchmarks,
        diagnostics=diagnostics,
        refusal_coverage=refusal_coverage,
    )


def append_history(path: Path, record: Mapping[str, Any]) -> bool:
    """Append a store row once, preserving every byte already in the JSONL."""

    store_sha = str(record.get("store_sha") or "")
    if not store_sha:
        raise ValueError("history row needs store_sha")
    if path.exists():
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if line.strip() and json.loads(line).get("store_sha") == store_sha:
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
) -> bool:
    return append_history(
        history_path,
        history_record_from_archive(archive_path, landing_sha=landing_sha),
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
            label="non-certified / out-of-band (hollow)",
        )
    )
    return handles


def _plot_y(value: float) -> float:
    return min(Y_LIMITS[1], max(Y_LIMITS[0], value))


@dataclass
class _ElementAccumulator:
    residual_sum: float = 0.0
    temperature_sum: float = 0.0
    n: int = 0
    flagged: bool = False


def element_view_series(
    data: ScorePlotData,
) -> dict[tuple[str, str], tuple[BinPoint, ...]]:
    """Pool deduplicated species observations by each element they carry."""

    bins: dict[tuple[str, str, str, str, int], _ElementAccumulator] = {}
    for (engine, formula, _rail), points in data.series.items():
        elements = parse_formula_elements(formula) or ()
        for element in set(elements):
            for point in points:
                accumulator = bins.setdefault(
                    (
                        engine,
                        element,
                        point.composition_key,
                        point.composition_label,
                        point.bin_index,
                    ),
                    _ElementAccumulator(),
                )
                accumulator.residual_sum += point.residual_dex * point.n
                accumulator.temperature_sum += point.temperature_K * point.n
                accumulator.n += point.n
                accumulator.flagged = accumulator.flagged or point.flagged

    grouped: dict[tuple[str, str], list[BinPoint]] = defaultdict(list)
    for (engine, element, composition_key, composition_label, bin_index), accumulator in bins.items():
        grouped[(engine, element)].append(
            BinPoint(
                bin_index=bin_index,
                temperature_K=accumulator.temperature_sum / accumulator.n,
                residual_dex=accumulator.residual_sum / accumulator.n,
                n=accumulator.n,
                flagged=accumulator.flagged,
                composition_key=composition_key,
                composition_label=composition_label,
                observable="pooled",
            )
        )
    return {
        key: tuple(sorted(points, key=lambda point: point.bin_index))
        for key, points in grouped.items()
    }


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
    actual_openimcc_commit = str(data.stamp.get("openimcc_commit") or openimcc_commit)
    parts = [
        f"OpenIMCC {openimcc_version} · {actual_openimcc_commit}",
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
_HERO_ENGINE_COLORS = {
    "openimcc": "#0072b2",
    "internal-analytical": "#d55e00",
    "vaporock": "#8f7868",
    "thermoengine": "#777777",
    "alphamelts": "#8d7898",
    "magemin": "#6f918d",
}
_HERO_EXTRA_COLORS = ("#75879a", "#9b836d", "#8a8d68", "#927c8d", "#708f87")


def _hero_engine_colors(engines: Sequence[str]) -> dict[str, str]:
    colors = dict(_HERO_ENGINE_COLORS)
    extras = sorted(set(engines) - colors.keys())
    colors.update(
        {
            engine: _HERO_EXTRA_COLORS[index % len(_HERO_EXTRA_COLORS)]
            for index, engine in enumerate(extras)
        }
    )
    return {engine: colors[engine] for engine in sorted(set(engines))}


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


def _temperature_tick(value: float, _position: int | None = None) -> str:
    return f"{value / 1_000:.2f}k" if abs(value) >= 1_000 else f"{value:.0f}"


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
    data: ScorePlotData,
    rail: str,
    engine_colors: Mapping[str, Any],
) -> list[Line2D]:
    engines = sorted(
        {
            engine
            for series_rail, _facet, engine, _composition in data.hero_series
            if series_rail == rail
        }
        | {
            engine
            for refusal_rail, _facet, _composition, engine, _reason in data.hero_refusals
            if refusal_rail == rail
        }
    )
    handles = [
        Line2D(
            [0],
            [0],
            color=engine_colors.get(engine, "#555555"),
            linewidth=1.3,
            marker="o",
            markersize=4,
            label=engine,
        )
        for engine in engines
    ]
    if any(
        point.flagged
        for (series_rail, _facet, _engine, _composition), points in data.hero_series.items()
        if series_rail == rail
        for point in points
    ):
        handles.append(
            Line2D(
                [0],
                [0],
                color="#444444",
                linewidth=0.0,
                marker="o",
                markerfacecolor="none",
                label="flagged / extrapolated",
            )
        )
    return handles


def _draw_hero_current(
    axis: Axes,
    data: ScorePlotData,
    rail: str,
    facet: str,
    *,
    engine_colors: Mapping[str, Any],
    composition_colormap: Any = None,
    composition_normalization: Normalize | None = None,
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
    composition_colormap = composition_colormap or plt.get_cmap("viridis")
    composition_normalization = composition_normalization or Normalize(vmin=0, vmax=1)
    for engine, composition, points in series:
        if not points:
            continue
        color = engine_colors.get(engine, "#555555")
        line, = axis.plot(
            [item.temperature_K for item in points],
            [item.residual_dex for item in points],
            color=color,
            linewidth=1.0,
            alpha=0.85,
            zorder=2,
        )
        line.set_gid(f"hero-series:{rail}:{facet}:{engine}:{_composition_slug(composition)}")
        for flagged in (False, True):
            flagged_points = [item for item in points if item.flagged is flagged]
            if not flagged_points:
                continue
            edge_colors = [
                color
                if item.composition_sio2_mole_fraction is None
                else composition_colormap(
                    composition_normalization(item.composition_sio2_mole_fraction)
                )
                for item in flagged_points
            ]
            artist = axis.scatter(
                [item.temperature_K for item in flagged_points],
                [item.residual_dex for item in flagged_points],
                marker="o",
                s=25,
                facecolors="none" if flagged else color,
                edgecolors=edge_colors,
                linewidths=1.2,
                zorder=3,
            )
            artist.set_gid(
                f"hero-marker:{rail}:{facet}:{engine}:{_composition_slug(composition)}:"
                f"{'flagged' if flagged else 'ordinary'}"
            )
    axis.axhline(0.0, color="#666666", linewidth=0.7, zorder=1)
    axis.set_ylabel("signed dex", fontsize=7)
    axis.set_xlabel("Temperature (K)", fontsize=7)
    axis.tick_params(labelsize=6)
    axis.grid(True, color="#999999", alpha=0.18, linewidth=0.5)
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
    axis.set_xlabel("refusal rows", fontsize=5.5)
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
    colormap: Any,
    normalization: Normalize,
) -> bool:
    """Draw scorer-owned cell medians and sample counts for one engine."""

    temperatures: list[float] = []
    for cell in cells:
        x_low = _finite_float(cell.get("sio2_lower"))
        x_high = _finite_float(cell.get("sio2_upper"))
        y_low = _finite_float(cell.get("temperature_lower_K"))
        y_high = _finite_float(cell.get("temperature_upper_K"))
        if None in (x_low, x_high, y_low, y_high):
            continue
        assert x_low is not None and x_high is not None
        assert y_low is not None and y_high is not None
        temperatures.extend((y_low, y_high))
        count = int(cell.get("n") or 0)
        value = _finite_float(cell.get("median_abs_dex"))
        color_eligible = bool(cell.get("color_eligible")) and value is not None
        fill = colormap(normalization(value)) if color_eligible else "#eeeeee"
        hatch = None if color_eligible else "///"
        axis.add_patch(
            Rectangle(
                (x_low, y_low),
                x_high - x_low,
                y_high - y_low,
                facecolor=fill,
                edgecolor="#aaaaaa",
                linewidth=0.25,
                hatch=hatch,
            )
        )
        label_color = (
            "#f7f7f7"
            if color_eligible and normalization(value) >= 0.55
            else "#222222"
        )
        axis.text(
            (x_low + x_high) / 2,
            (y_low + y_high) / 2,
            _compact_count(count),
            ha="center",
            va="center",
            color=label_color,
            fontsize=5.0,
        )

    if not temperatures or not any(int(cell.get("n") or 0) for cell in cells):
        axis.set_axis_off()
        return False
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(min(temperatures), max(temperatures))
    axis.set_xticks([index / 10 for index in range(11)])
    axis.set_xticklabels([f"{index / 10:.1f}" for index in range(11)], fontsize=5)
    cell_lowers = sorted(
        {
            _finite_float(cell.get("temperature_lower_K"))
            for cell in cells
            if _finite_float(cell.get("temperature_lower_K")) is not None
        }
    )
    tick_stride = max(1, math.ceil(len(cell_lowers) / 8))
    axis.set_yticks(cell_lowers[::tick_stride])
    axis.tick_params(axis="y", labelsize=5)
    axis.yaxis.set_major_formatter(FuncFormatter(_temperature_tick))
    axis.set_xlabel("SiO₂ mole fraction", fontsize=6)
    axis.set_ylabel("T (K)", fontsize=6)
    axis.set_title(
        f"{engine}: median |dex|; n<3 hatched",
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
) -> None:
    metrics_by_engine: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"rms_dex": [], "median_abs_dex": []}
    )
    paired_primary_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    benchmark_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
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
                for field_name in ("rms_dex", "median_abs_dex"):
                    value = _finite_float(metric.get(field_name))
                    if value is not None:
                        metrics_by_engine[engine][field_name].append((date, value))
        benchmarks = entry.get("benchmarks")
        if isinstance(benchmarks, Sequence) and not isinstance(benchmarks, (str, bytes)):
            for benchmark in benchmarks:
                if not isinstance(benchmark, Mapping) or benchmark.get("rail") != rail:
                    continue
                primary = str(benchmark.get("primary_engine") or "unknown")
                primary_value = _finite_float(benchmark.get("primary_rms_dex"))
                value = _finite_float(benchmark.get("benchmark_rms_dex"))
                if (
                    benchmark.get("benchmark_engine") is None
                    or primary_value is None
                    or value is None
                    or int(benchmark.get("n") or 0) == 0
                ):
                    continue
                paired_primary_points[primary].append((date, primary_value))
                benchmark_points[primary].append((date, value))

    engine_names = sorted(metrics_by_engine)
    for engine in engine_names:
        color = engine_colors.get(engine, "#555555")
        for field_name, linestyle, label in (
            ("rms_dex", "-", "RMS"),
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
        primary_points = sorted(paired_primary_points[primary])
        if primary_points:
            axis.plot(
                [date for date, _value in primary_points],
                [value for _date, value in primary_points],
                color=engine_colors.get(primary, "#333333"),
                linestyle="-.",
                marker="s",
                markersize=3,
                linewidth=1.0,
                label=f"{primary} same-row RMS",
            )
        comparison_points = sorted(benchmark_points[primary])
        if comparison_points:
            axis.plot(
                [date for date, _value in comparison_points],
                [value for _date, value in comparison_points],
                color=engine_colors.get(primary, "#555555"),
                linestyle=":",
                marker="D",
                markersize=3,
                linewidth=1.1,
                label=f"best comparison vs {primary}",
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
    axis.set_ylabel("RMS / median |dex|", fontsize=7)
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


def _current_history_entry(
    history: Sequence[Mapping[str, Any]], revision: object
) -> Mapping[str, Any]:
    for entry in reversed(history):
        if entry.get("store_revision") == revision:
            return entry
    return history[-1] if history else {}


def _current_engine_metric(
    history: Sequence[Mapping[str, Any]], rail: str, revision: object, engine: str
) -> Mapping[str, Any] | None:
    entry = _current_history_entry(history, revision)
    metrics = entry.get("metrics")
    if not isinstance(metrics, Sequence) or isinstance(metrics, (str, bytes)):
        return None
    return next(
        (
            metric
            for metric in metrics
            if isinstance(metric, Mapping)
            and metric.get("rail") == rail
            and metric.get("engine") == engine
        ),
        None,
    )


def _hero_summary_payload(
    data: ScorePlotData,
    history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    charts: dict[str, Any] = {}
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
            engine: _current_engine_metric(
                history, rail, data.stamp.get("revision"), engine
            )
            for engine in HERO_PRIMARY_ENGINES
        }
        charts[rail] = {
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
            },
            "same_row_benchmarks_by_primary_engine": {
                engine: data.hero_benchmarks[(rail, engine)]
                for engine in HERO_PRIMARY_ENGINES
            },
        }
    return charts


def render_hero_charts(
    data: ScorePlotData,
    output_dir: Path,
    *,
    history_path: Path | None = None,
) -> dict[str, Path]:
    """Render the vaporisation, redox, and melt activity current/history charts."""

    history = read_history(history_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    engine_colors = _hero_engine_colors(data.engines)
    composition_colormap = plt.get_cmap("viridis")
    composition_normalization = Normalize(vmin=0.0, vmax=1.0)
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
        sweet_spot_cells = {
            engine: data.hero_sweet_spot_cells[(rail, engine)]
            for engine in sweet_spot_engines
        }
        sweet_spot_values = [
            value
            for cells in sweet_spot_cells.values()
            for cell in cells
            if cell.get("color_eligible")
            if (value := _finite_float(cell.get("median_abs_dex"))) is not None
        ]
        color_max = max(sweet_spot_values, default=1.0) or 1.0
        normalization = Normalize(vmin=0.0, vmax=color_max)
        sweet_spot_colormap = plt.get_cmap("viridis")
        row_specs: list[tuple[str, object, float]] = []
        facet_pairs = [
            tuple(numeric_facets[index : index + 2])
            for index in range(0, len(numeric_facets), 2)
        ]
        for pair in facet_pairs:
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
        if facet_pairs:
            row_specs.append(("spacer", (), 0.16))
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
        current_axes: list[Axes] = []
        sweet_spot_axes: list[Axes] = []
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
                        composition_colormap=composition_colormap,
                        composition_normalization=composition_normalization,
                    )
                    axis.set_title(facet, loc="left", fontsize=7.5)
                    current_axes.append(axis)
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
            elif kind == "history":
                history_axis = figure.add_subplot(grid[row_index, :])
                _draw_hero_history(
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
                    if _draw_hero_sweet_spot(
                        axis,
                        engine,
                        sweet_spot_cells[engine],
                        colormap=sweet_spot_colormap,
                        normalization=normalization,
                    ):
                        sweet_spot_axes.append(axis)
            row_index += 1

        composition_values = [
            point.composition_sio2_mole_fraction
            for (series_rail, _facet, _engine, _composition), points in data.hero_series.items()
            if series_rail == rail
            for point in points
            if point.composition_sio2_mole_fraction is not None
        ]
        if current_axes and composition_values:
            composition_scalar = matplotlib.cm.ScalarMappable(
                norm=composition_normalization,
                cmap=composition_colormap,
            )
            composition_scalar.set_array([])
            colorbar = figure.colorbar(
                composition_scalar,
                ax=current_axes,
                fraction=0.018,
                pad=0.012,
            )
            colorbar.set_label("SiO₂ mole fraction", fontsize=5.5)
            colorbar.set_ticks([0.0, 0.5, 1.0])
            colorbar.ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))
            colorbar.ax.tick_params(labelsize=5)
        if sweet_spot_axes and sweet_spot_values:
            scalar = matplotlib.cm.ScalarMappable(
                norm=normalization,
                cmap=sweet_spot_colormap,
            )
            scalar.set_array([])
            colorbar = figure.colorbar(
                scalar,
                ax=sweet_spot_axes,
                fraction=0.018,
                pad=0.012,
            )
            colorbar.set_label("median |dex| (n≥3)", fontsize=5.5)
            colorbar.ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
            colorbar.ax.tick_params(labelsize=5)

        status_lines: list[str] = []
        for engine in HERO_PRIMARY_ENGINES:
            benchmark = data.hero_benchmarks[(rail, engine)]
            metric = _current_engine_metric(
                history, rail, data.stamp.get("revision"), engine
            )
            all_numeric_rms = _format_dex(None if metric is None else metric.get("rms_dex"))
            all_numeric_count = _compact_count(
                0 if metric is None else metric.get("numeric_count", metric.get("n", 0))
            )
            if benchmark.get("benchmark_engine") is None:
                status_lines.append(
                    f"{engine}: benchmark unavailable (same-row n=0) · "
                    f"all-numeric RMS {all_numeric_rms} dex (n={all_numeric_count})"
                )
            else:
                status_lines.append(
                    f"{engine}: same-row RMS {_format_dex(benchmark.get('primary_rms_dex'))} dex "
                    f"(n={_compact_count(benchmark.get('n'))}) vs "
                    f"{benchmark.get('benchmark_engine')} {_format_dex(benchmark.get('benchmark_rms_dex'))} dex · "
                    f"all-numeric RMS {all_numeric_rms} dex (n={all_numeric_count})"
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
        figure.suptitle(_HERO_TITLES[rail], fontsize=10, y=0.994)
        figure.text(
            0.5,
            0.966,
            "\n".join(status_lines),
            ha="center",
            va="top",
            fontsize=6.4,
            linespacing=1.15,
        )
        legend_handles = _hero_legend_handles(data, rail, engine_colors)
        if legend_handles:
            figure.legend(
                handles=legend_handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 0.916),
                ncol=min(6, len(legend_handles)),
                frameon=False,
                fontsize=6.0,
                handlelength=1.4,
                columnspacing=1.0,
            )
        caption = (
            "Line color=engine; marker edge=SiO₂ mole fraction (key at right); hollow=flagged/extrapolated. "
            "History: solid=RMS, dashed=median |dex|, dash-dot=paired RMS, dotted=best same-row benchmark. "
            "Sweet-spot fill=median |dex|; cell label=n; n<3 is grey and hatched."
        )
        if rail == Rail.MELT_ACTIVITY.value:
            caption += (
                " ThermoEngine comparison caveat: 140/147 rows pair pure-solid "
                "data with its pure-liquid model reference; main's fix is pending."
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
) -> dict[str, Path]:
    """Write shared-scale species/element tiles and one PNG per engine/view."""

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
        f"{engine} {data.summaries[engine].excluded_d139_rows:,} uncalibrated KEMS p_partial"
        for engine in data.engines
        if data.summaries[engine].excluded_d139_rows
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
        files.update(render_hero_charts(data, output_dir, history_path=history_path))
    return files


def _summary_payload(
    data: ScorePlotData,
    files: Mapping[str, Path],
    abundance: ElementAbundance,
    *,
    omitted_engines: Sequence[str] = (),
    history: Sequence[Mapping[str, Any]] = (),
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
        }
    return {
        "store_revision": data.stamp.get("revision"),
        "engines": engines,
        "rails_seen": list(data.rails_seen),
        "selected_rails": list(data.selected_rails),
        "bin_width_K": data.bin_width_K,
        "x_limits_K": list(data.x_limits),
        "abundance_feedstock": abundance.feedstock_id,
        "element_abundance_classes": abundance_class_table(data, abundance),
        "omitted_empty_engines": list(omitted_engines),
        "no_scored_rows_footer": _no_scored_rows_note(data, omitted_engines),
        "hero_charts": _hero_summary_payload(data, history),
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
    parser.add_argument("--render-date", help="UTC date for stamps and filenames (default: today)")
    parser.add_argument(
        "--history-path",
        type=Path,
        required=True,
        help="append-only hero chart history JSONL (for example docs-private/.../hero-history.jsonl)",
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
    data = read_scores(
        args.input,
        bin_width_K=args.bin_width_k,
        rails=args.rail,
    )
    if args.append_history:
        if not args.landing_sha:
            raise SystemExit("--append-history requires --landing-sha")
        summary = _archive_json_member(args.input, "data/battery/score-summary.json")
        record = _history_record(
            summary,
            landing_sha=args.landing_sha,
            benchmarks=data.hero_benchmarks,
            diagnostics=data.hero_diagnostics,
            refusal_coverage=_refusal_coverage_from_summaries(data.summaries),
        )
        append_history(args.history_path, record)
    abundance = load_element_abundance(args.abundance_feedstock)
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
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
