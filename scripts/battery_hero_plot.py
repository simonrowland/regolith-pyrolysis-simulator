"""Render a streaming, per-engine battery residual-versus-temperature plot.

Example::

    python scripts/battery_hero_plot.py \
      --input data/battery/residuals.jsonl \
      --simulator-commit 79cbaadb7 \
      --openimcc-commit <score-run-openimcc-commit>

The JSONL is read one line at a time. A temporary SQLite file collapses
same-experiment duplicates without retaining the input rows in memory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.lines import Line2D

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


DEFAULT_BIN_WIDTH_K = 100.0
# SiO pressure comparisons have their own canonical rail in the scorer.
DEFAULT_RAILS = (
    Rail.VAPOUR.value,
    Rail.MELT_ACTIVITY.value,
    Rail.SIO_EVOLUTION.value,
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


@dataclass(frozen=True)
class PanelSeries:
    subject: str
    rail: str
    points: tuple[BinPoint, ...]
    tier: str
    gid: str
    major_elements: tuple[str, ...]


@dataclass(frozen=True)
class ScorePlotData:
    stamp: Mapping[str, Any]
    engines: tuple[str, ...]
    rails_seen: tuple[str, ...]
    selected_rails: tuple[str, ...]
    summaries: Mapping[str, EngineSummary]
    series: Mapping[tuple[str, str, str], tuple[BinPoint, ...]]
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
    summaries: dict[str, EngineSummary] = defaultdict(EngineSummary)
    rails_seen: set[str] = set()
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
                residual_dex REAL NOT NULL
            )"""
        )

        pending: list[tuple[str, str, str, str, str, float, str, int, float]] = []
        with path.open("r", encoding="utf-8") as source:
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
                if status is ResidualStatus.REFUSED:
                    refusal = row.get("refusal")
                    reason = (
                        str(refusal.get("reason") or "untyped_refusal")
                        if isinstance(refusal, Mapping)
                        else "untyped_refusal"
                    )
                    summary.refusals[reason] += 1
                    continue

                numeric = row.get("numeric")
                if not isinstance(numeric, Mapping) or numeric.get("value") is None:
                    continue
                if rail not in selected:
                    continue
                if numeric.get("operation") != "dex":
                    summary.non_dex_rows += 1
                    continue

                identity = candidate_observation.get("identity")
                if not isinstance(identity, Mapping):
                    summary.missing_identity_rows += 1
                    continue
                species_identity = identity.get("species")
                formula = (
                    str(species_identity.get("formula") or "")
                    if isinstance(species_identity, Mapping)
                    else ""
                )
                temperature_raw = _state_value(identity.get("temperature_K"))
                temperature = _finite_float(temperature_raw)
                temperature_key = _decimal_key(temperature_raw)
                residual = _finite_float(numeric.get("value"))
                if not formula or temperature is None or temperature_key is None or residual is None:
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
                    )
                )
                if len(pending) >= 5000:
                    database.executemany(
                        "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        pending,
                    )
                    pending.clear()

        if pending:
            database.executemany(
                "INSERT INTO scored VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                pending,
            )
        database.commit()
        if stamp is None:
            raise ValueError(f"{path}: empty score file")

        bins: dict[tuple[str, str, str, int], _BinAccumulator] = {}
        for row in database.execute(
            """SELECT engine, species, rail, MIN(temperature_K),
                      MAX(CASE WHEN authority != ? OR out_of_band = 1 THEN 1 ELSE 0 END),
                      AVG(residual_dex)
                 FROM scored
                GROUP BY engine, species, rail, experiment, temperature_key
                ORDER BY engine, species, rail, MIN(temperature_K)""",
            (Authority.CERTIFIED.value,),
        ):
            engine, formula, rail, temperature, flagged, residual = row
            summary = summaries[engine]
            summary.unique_points += 1
            summary.residual_square_sum += float(residual) ** 2
            summary.rms_count += 1
            bin_index = math.floor(float(temperature) / bin_width_K)
            key = (engine, formula, rail, bin_index)
            accumulator = bins.setdefault(key, _BinAccumulator())
            accumulator.residual_sum += float(residual)
            accumulator.temperature_sum += float(temperature)
            accumulator.n += 1
            accumulator.flagged = accumulator.flagged or bool(flagged)
        database.close()

    if stamp is None:
        raise ValueError(f"{path}: missing battery store stamp")

    grouped: dict[tuple[str, str, str], list[BinPoint]] = defaultdict(list)
    for key, accumulator in bins.items():
        engine, formula, rail, bin_index = key
        grouped[(engine, formula, rail)].append(
            BinPoint(
                bin_index=bin_index,
                temperature_K=accumulator.temperature_sum / accumulator.n,
                residual_dex=accumulator.residual_sum / accumulator.n,
                n=accumulator.n,
                flagged=accumulator.flagged,
            )
        )
    series = {
        key: tuple(sorted(points, key=lambda point: point.bin_index))
        for key, points in grouped.items()
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

    return ScorePlotData(
        stamp=stamp,
        engines=engines,
        rails_seen=tuple(sorted(rails_seen)),
        selected_rails=selected_rails,
        summaries=dict(summaries),
        series=series,
        x_limits=x_limits,
        bin_width_K=bin_width_K,
    )


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

    bins: dict[tuple[str, str, int], _ElementAccumulator] = {}
    for (engine, formula, _rail), points in data.series.items():
        elements = parse_formula_elements(formula) or ()
        for element in set(elements):
            for point in points:
                accumulator = bins.setdefault(
                    (engine, element, point.bin_index), _ElementAccumulator()
                )
                accumulator.residual_sum += point.residual_dex * point.n
                accumulator.temperature_sum += point.temperature_K * point.n
                accumulator.n += point.n
                accumulator.flagged = accumulator.flagged or point.flagged

    grouped: dict[tuple[str, str], list[BinPoint]] = defaultdict(list)
    for (engine, element, bin_index), accumulator in bins.items():
        grouped[(engine, element)].append(
            BinPoint(
                bin_index=bin_index,
                temperature_K=accumulator.temperature_sum / accumulator.n,
                residual_dex=accumulator.residual_sum / accumulator.n,
                n=accumulator.n,
                flagged=accumulator.flagged,
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
            items.append(
                PanelSeries(
                    subject=formula,
                    rail=rail,
                    points=points,
                    tier=species_tier(formula, abundance),
                    gid=f"series:{engine}:{formula}:{rail}",
                    major_elements=_major_elements(formula, abundance),
                )
            )
    elif view == "element":
        for (series_engine, element), points in element_view_series(data).items():
            if series_engine != engine:
                continue
            items.append(
                PanelSeries(
                    subject=element,
                    rail="pooled",
                    points=points,
                    tier=element_tier(element, abundance),
                    gid=f"series:{engine}:{element}:pooled:element",
                    major_elements=(
                        (element,)
                        if element_tier(element, abundance) == TIER_MAJOR
                        else ()
                    ),
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

    for item in items:
        style = style_for_tier(item.tier)
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
                        marker="o",
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
                marker="o",
                facecolors="none" if point.flagged else style["color"],
                edgecolors=style["color"],
                linewidths=max(0.45, style["linewidth"] * 0.55),
                zorder=5,
            )
            artist.set_gid(marker_gid)

        if item.major_elements and item.points:
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
    label_positions, label_names = _spaced_axis_labels(label_candidates)
    right_axis.set_yticks(label_positions)
    right_axis.set_yticklabels(label_names)
    right_axis.tick_params(axis="y", labelsize=7, length=0, pad=2)
    if label_names:
        right_axis.set_ylabel("Major element", fontsize=7, labelpad=5)

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
        if refusals:
            reason, count = sorted(
                refusals.items(), key=lambda item: (-item[1], item[0])
            )[0]
            detail = f"{reason} ×{count:,}"
        else:
            detail = "no refusal reason recorded"
        entries.append(f"{engine} ({detail})")
    return "no scored rows: " + ", ".join(entries)


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
    return files


def _summary_payload(
    data: ScorePlotData,
    files: Mapping[str, Path],
    abundance: ElementAbundance,
    *,
    omitted_engines: Sequence[str] = (),
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
        "files": {name: str(path) for name, path in files.items()},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="scorer residuals.jsonl")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data = read_scores(
        args.input,
        bin_width_K=args.bin_width_k,
        rails=args.rail,
    )
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
    )
    omitted_engines = (
        _empty_engine_names(data)
        if args.omit_empty_engines is not False
        else ()
    )
    print(
        json.dumps(
            _summary_payload(data, files, abundance, omitted_engines=omitted_engines),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
