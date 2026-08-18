"""W0-SENS eligible-corpus reader — bench-set rows to sealed screen inputs.

Frozen by PREREGISTRATION-wave0.md steps 1 and 4 (lines 36, 39). This module
is the READER seam the aborted 2026-08-14 attempt found missing
(``sensitivity-screen/SCREEN-RESULTS.md:39-56``): it turns the tracked
``data/melt_activity/basalt-bench-set-v1.yaml`` rows into

- the sealed ``W0-SENS-ELIGIBLE-MANIFEST`` bytes (step 4) whose SHA-256 the
  driver recomputes over the exact sealed bytes,
- one :class:`~benchmarks.w0_sens.driver.ChannelResponse` per eligible row,
  each carrying a :class:`~benchmarks.w0_sens.driver.NaBearingProof` of
  sealed-manifest membership, and
- the three material-evidence disclosures, computed MECHANICALLY from the
  corpus fields the screen actually consumed rather than carried as prose a
  later bench-set edit could silently falsify.

Nothing here evaluates chemistry, reads a quarantined W value, or ranks.

ELIGIBILITY IS FAIL-CLOSED. Step 1 admits "only experimentally measured
reference anchors", and step 4 requires the custodian to write "the exact
eligible ``(source, composition, T, species, experimental/model type)`` rows
AND source hashes" into the sealed manifest. A row that does not carry the
metadata proving both is therefore not sealable and is EXCLUDED with a typed
reason (never silently, never by imputation): every excluded row appears in
:attr:`EligibleCorpus.exclusions` and in the screen's emitted audit table.
On the tracked bench set at the aborted run's SHA this admits the 40 scored
``a(SiO2)`` rows over 19 ``(source, composition)`` clusters and excludes the
Hastie-1981 and Richter Type-B rows, which carry no ``scoring_status``,
evidence class, or ``provenance.source_sha256`` — see AMBIGUITY A-2 in
``benchmarks/w0_sens/screen.py`` for the consequence and the controller
decision that would change it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# The benchmark harness is a step-1 SHA-pinned input (:36), so its own
# schema validation and composition normalization are reused verbatim
# rather than re-implemented: a private helper of a pinned file is a
# stabler contract here than a second copy of the same arithmetic.
from benchmarks.melt_activity_benchmark import (
    composition_wt_pct_for_point,
    load_bench_set,
)
from benchmarks.w0_sens import W0SensAbort
from benchmarks.w0_sens.driver import (
    ChannelResponse,
    NaBearingProof,
    SealedNaManifest,
)


ELIGIBLE_MANIFEST_ID = "W0-SENS-ELIGIBLE-MANIFEST"
ELIGIBLE_MANIFEST_VERSION = 1

# Step 1: "only experimentally measured reference anchors; model outputs,
# fitted sheets, and rows whose metadata reports zero literal empirical
# points are ineligible even when stored in the same benchmark file."
EXPERIMENTAL_CLASS_PREFIX = "experimental"
SCORED_ELIGIBLE_STATUS = "SCORED-ELIGIBLE"

# Step 4 seals "the exact eligible rows AND source hashes": a source hash
# must be a SHA-256 hex digest, not merely a nonempty string.
_SOURCE_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

# Point-level metadata keys that report the row's literal empirical point
# count (checked on the row and inside its ``provenance`` block). An
# explicit ZERO excludes the row; an absent key carries no opinion.
_EMPIRICAL_POINT_COUNT_KEYS = (
    "literal_empirical_point_count",
    "empirical_point_count",
)

# Channel-identity tokens. The driver keys sealed-manifest rows and
# Na-bearing proofs on ``(source, composition_id, channel)`` and REFUSES a
# duplicate identity (driver.py:152-157), while step 4 enumerates the
# eligible rows per ``(source, composition, T, species, ...)``. The channel
# string therefore has to carry T and the observed species/parent, or the
# four-temperature Yamaguchi compositions collide. See AMBIGUITY A-3.
CHANNEL_TOKENS = {
    "activity": "a",
    "activity_coefficient": "gamma",
    "partial_pressure": "p",
    "evaporation_flux": "J",
}
_PARENT_LABELLED = frozenset({"activity", "activity_coefficient"})

_NA_OXIDE = "Na2O"
_SI_OXIDE = "SiO2"


class CorpusRefusal(W0SensAbort):
    """A sealed-corpus precondition failed; the screen never starts.

    This is an instrument-precondition refusal raised BEFORE any frozen
    attempt opens a run record, not one of the frozen §9 outcome types.
    """

    abort_type = "ABORT-W0-SENS-CORPUS"


def channel_identity(
    observable: str, parent_oxide: str, species: str, temperature_K: float
) -> str:
    """Compound channel key: ``a(SiO2)@1473K`` / ``p(K)@1500K``.

    Carries T because the driver's row identity is
    ``(source, composition_id, channel)`` with no temperature field, while
    a sealed manifest row is per ``(source, composition, T, species, type)``.
    """
    token = CHANNEL_TOKENS.get(str(observable))
    if token is None:
        raise CorpusRefusal(f"unsupported benchmark observable {observable!r}")
    label = (
        str(parent_oxide) if str(observable) in _PARENT_LABELLED else str(species)
    )
    temperature = float(temperature_K)
    if not (math.isfinite(temperature) and temperature > 0.0):
        raise CorpusRefusal(f"row carries a non-physical temperature {temperature_K!r}")
    return f"{token}({label})@{temperature:.0f}K"


@dataclass(frozen=True)
class EligiblePoint:
    """One sealed eligible measured row."""

    point_id: str
    source: str  # bench-set ``population`` — the manifest ``(source, ...)`` half
    composition_id: str
    temperature_K: float
    channel: str
    observable: str
    parent_oxide: str
    species: str
    measured: float
    fO2_bar: float | None
    composition_wt_pct: Mapping[str, float]
    na2o_wt_pct: float
    sio2_wt_pct: float
    evidence_class: str
    source_evidence_class: str | None
    measurement_type: str
    source_sha256: str

    @property
    def cluster_id(self) -> str:
        return f"{self.source}::{self.composition_id}"


@dataclass(frozen=True)
class ExcludedPoint:
    """One bench-set row that could not be sealed, with its typed reason."""

    point_id: str
    source: str
    reason: str


@dataclass(frozen=True)
class Disclosure:
    """A material-evidence disclosure computed from the consumed corpus."""

    id: str
    title: str
    text: str
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class CellOutcome:
    """One evaluated ``(row, build)`` cell, or its typed missing reason."""

    point_id: str
    value: float | None
    status: str
    reason: str = ""

    @property
    def typed_reason(self) -> str:
        detail = " ".join(str(self.reason or "").split())
        status = str(self.status or "").strip() or "unknown"
        return f"{status}: {detail}" if detail else status


@dataclass(frozen=True)
class EligibleCorpus:
    """The sealed eligible corpus plus its audit trail."""

    bench_set_path: str
    bench_set_sha256: str
    points: tuple[EligiblePoint, ...]
    exclusions: tuple[ExcludedPoint, ...]
    manifest_bytes: bytes
    manifest: SealedNaManifest
    disclosures: tuple[Disclosure, ...]

    @property
    def manifest_sha256(self) -> str:
        return self.manifest.sha256

    @property
    def cluster_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for point in self.points:
            if point.cluster_id not in seen:
                seen.append(point.cluster_id)
        return tuple(seen)

    def na_bearing_cluster_ids(self) -> tuple[str, ...]:
        return tuple(
            cluster
            for cluster in self.cluster_ids
            if all(
                point.na2o_wt_pct > 0.0 and point.sio2_wt_pct > 0.0
                for point in self.points
                if point.cluster_id == cluster
            )
        )


def _evidence_class(point: Mapping[str, Any]) -> str:
    return str(point.get("evidence_class") or point.get("reduction_class") or "")


def _exclusion_reason(point: Mapping[str, Any]) -> str | None:
    """Typed reason this row cannot enter the sealed manifest, or ``None``."""
    if not bool(point.get("score", True)):
        status = point.get("scoring_status") or "score=false"
        dropped = point.get("dropped_reason")
        detail = f"; dropped_reason={dropped}" if dropped else ""
        return f"row is not scored ({status}){detail}"
    if str(point.get("scoring_status") or "") != SCORED_ELIGIBLE_STATUS:
        return (
            "row carries no SCORED-ELIGIBLE scoring_status; step 4 seals only "
            "rows whose eligibility metadata is recorded"
        )
    if bool(point.get("extrapolation_flag")):
        return (
            "row is extrapolation-flagged (evaluated outside its measurement "
            "window); step 1 admits measured anchors only"
        )
    evidence = _evidence_class(point)
    if not evidence.startswith(EXPERIMENTAL_CLASS_PREFIX):
        return (
            f"evidence class {evidence!r} is not an experimentally measured "
            "class; step 1 excludes model outputs and fitted sheets"
        )
    source_evidence = point.get("source_evidence_class")
    if source_evidence is not None and not str(source_evidence).startswith(
        EXPERIMENTAL_CLASS_PREFIX
    ):
        return (
            f"source evidence class {source_evidence!r} is not an "
            "experimentally measured class"
        )
    for key in _EMPIRICAL_POINT_COUNT_KEYS:
        for location, host in (("row", point), ("provenance", point.get("provenance"))):
            if not isinstance(host, Mapping) or key not in host:
                continue
            count = host[key]
            if isinstance(count, bool) or not isinstance(count, (int, float)):
                return (
                    f"{location} metadata {key}={count!r} is not a numeric "
                    "literal empirical point count"
                )
            if count < 0:
                return (
                    f"{location} metadata {key}={count!r} is negative; a "
                    "literal empirical point count cannot be"
                )
            if count == 0:
                return (
                    f"{location} metadata reports zero literal empirical "
                    f"points ({key}=0); step 1 admits experimentally "
                    "measured anchors only"
                )
    if str(point.get("observable")) not in CHANNEL_TOKENS:
        return f"unsupported observable {point.get('observable')!r}"
    sha = str((point.get("provenance") or {}).get("source_sha256") or "")
    if not sha:
        return (
            "row carries no provenance.source_sha256; step 4 seals the exact "
            "eligible rows AND their source hashes"
        )
    if _SOURCE_SHA256_RE.fullmatch(sha) is None:
        return (
            f"provenance.source_sha256 {sha!r} is not a 64-hex-digit SHA-256 "
            "digest; step 4 seals the exact eligible rows AND their source "
            "hashes"
        )
    try:
        measured = float(point["measured"])
    except (KeyError, TypeError, ValueError):
        return "row carries no numeric measured value"
    if not (math.isfinite(measured) and measured > 0.0):
        return f"measured value {measured!r} is not positive finite"
    return None


def build_manifest_bytes(
    points: Sequence[EligiblePoint],
    *,
    bench_set_path: str,
    bench_set_sha256: str,
) -> bytes:
    """Canonical sealed ``W0-SENS-ELIGIBLE-MANIFEST`` bytes (step 4).

    Rows carry the five identity/composition fields the driver validates
    plus the full step-4 tuple ``(source, composition, T, species,
    experimental/model type)`` and per-row source hashes. The driver ignores
    the extra keys, so the manifest can be both audit-complete and
    machine-checkable from the same bytes.
    """
    if not points:
        raise CorpusRefusal(
            "no eligible measured row survived step-1/step-4 eligibility; the "
            "manifest cannot be sealed"
        )
    identities = [(p.source, p.composition_id, p.channel) for p in points]
    duplicates = sorted(
        {identity for identity in identities if identities.count(identity) > 1}
    )
    if duplicates:
        raise CorpusRefusal(
            "eligible rows collapse onto a duplicate sealed identity "
            f"{duplicates}; the channel key must separate them"
        )
    document = {
        "manifest": ELIGIBLE_MANIFEST_ID,
        "version": ELIGIBLE_MANIFEST_VERSION,
        "bench_set": {"path": bench_set_path, "sha256": bench_set_sha256},
        "rows": [
            {
                "source": point.source,
                "composition_id": point.composition_id,
                "channel": point.channel,
                "na2o_wt_pct": point.na2o_wt_pct,
                "sio2_wt_pct": point.sio2_wt_pct,
                "temperature_K": point.temperature_K,
                "species": point.species,
                "parent_oxide": point.parent_oxide,
                "observable": point.observable,
                "measurement_type": point.measurement_type,
                "evidence_class": point.evidence_class,
                "source_evidence_class": point.source_evidence_class,
                "point_id": point.point_id,
                "source_sha256": point.source_sha256,
                "composition_wt_pct": dict(point.composition_wt_pct),
            }
            for point in points
        ],
    }
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _gibbs_duhem_disclosure(
    points: Sequence[EligiblePoint],
) -> Disclosure:
    by_source: dict[str, dict[str, int]] = {}
    for point in points:
        chain = " ".join(
            filter(None, (point.evidence_class, point.source_evidence_class))
        )
        bucket = by_source.setdefault(point.source, {"total": 0, "gibbs_duhem": 0})
        bucket["total"] += 1
        if "gibbs_duhem" in chain:
            bucket["gibbs_duhem"] += 1
    derived = [
        source
        for source, counts in by_source.items()
        if counts["gibbs_duhem"] == counts["total"] and counts["total"] > 0
    ]
    partial = {
        source: counts
        for source, counts in by_source.items()
        if 0 < counts["gibbs_duhem"] < counts["total"]
    }
    direct = [
        source for source, counts in by_source.items() if counts["gibbs_duhem"] == 0
    ]
    if len(by_source) == len(derived) and derived:
        text = (
            f"All {sum(c['total'] for c in by_source.values())} scored rows in "
            f"{len(derived)} source cluster population(s) "
            f"({', '.join(sorted(derived))}) carry a Gibbs-Duhem-derived "
            "evidence class: the scored channel is a transform of each "
            "laboratory's own measured alkali activities, not independent "
            "information. The populations are independent of each other by "
            "laboratory and method; each is internally derived."
        )
    else:
        text = (
            "Scored rows do NOT all carry a Gibbs-Duhem-derived evidence "
            f"class. Fully derived: {sorted(derived)}; partly derived: "
            f"{sorted(partial)}; not derived: {sorted(direct)}. The derived "
            "share is a transform of the measuring laboratory's own data and "
            "is not independent information."
        )
    return Disclosure(
        id="D1-gibbs-duhem-derived-channel",
        title="Scored channel derivation class",
        text=text,
        facts={"by_source": by_source},
    )


def _standard_state_disclosure(
    points: Sequence[EligiblePoint], raw_by_id: Mapping[str, Mapping[str, Any]]
) -> Disclosure:
    converted: list[dict[str, Any]] = []
    for point in points:
        block = dict(raw_by_id[point.point_id].get("standard_state_conversion") or {})
        if not block:
            continue
        raw = raw_by_id[point.point_id]
        uncertainty = dict(raw.get("uncertainty") or {})
        converted.append(
            {
                "point_id": point.point_id,
                "source": point.source,
                "directive": block.get("directive"),
                "from_standard_state": block.get("from_standard_state"),
                "to_standard_state": block.get("to_standard_state"),
                "activity_multiplier": block.get("activity_multiplier"),
                "as_published_preserved": "as_published_tridymite_activity" in raw,
                "combined_sigma_log10_dex": uncertainty.get(
                    "combined_sigma_log10_dex"
                ),
            }
        )
    if not converted:
        text = (
            "No scored row carries a standard-state conversion; every scored "
            "activity is entered on its as-published reference state."
        )
    else:
        sources = sorted({str(row["source"]) for row in converted})
        directives = sorted({str(row["directive"]) for row in converted})
        multipliers = sorted({float(row["activity_multiplier"]) for row in converted})
        preserved = all(bool(row["as_published_preserved"]) for row in converted)
        sigma = all(row["combined_sigma_log10_dex"] is not None for row in converted)
        text = (
            f"{len(converted)} scored row(s) in {', '.join(sources)} carry a "
            f"{'/'.join(directives)} standard-state conversion "
            f"({converted[0]['from_standard_state']} -> "
            f"{converted[0]['to_standard_state']}, activity multiplier "
            f"{', '.join(f'{m:.15g}' for m in multipliers)}). "
            f"The as-published value is {'preserved' if preserved else 'NOT preserved'} "
            f"alongside the converted value and a separate conversion "
            f"uncertainty is {'quadrature-combined' if sigma else 'NOT combined'} "
            "with the published coefficient sigma."
        )
    return Disclosure(
        id="D2-standard-state-conversion",
        title="Controller-directed standard-state conversion",
        text=text,
        facts={"converted_rows": converted, "converted_count": len(converted)},
    )


def _extrapolation_disclosure(
    all_points: Sequence[Mapping[str, Any]], eligible_sources: Sequence[str]
) -> Disclosure:
    flagged = [point for point in all_points if bool(point.get("extrapolation_flag"))]
    scored_flagged = [point for point in flagged if bool(point.get("score", True))]
    if scored_flagged:
        raise CorpusRefusal(
            "extrapolation-flagged rows are marked scored: "
            f"{sorted(str(p['id']) for p in scored_flagged)}; a fit evaluated "
            "outside its measurement window may not enter a statistic"
        )
    populations = set(eligible_sources)
    held = [
        point
        for point in all_points
        if str(point.get("population")) in populations
        and not bool(point.get("score", True))
    ]
    by_parent: dict[str, int] = {}
    for point in held:
        parent = str(point.get("parent_oxide"))
        by_parent[parent] = by_parent.get(parent, 0) + 1
    flagged_by_population: dict[str, int] = {}
    for point in flagged:
        population = str(point.get("population"))
        flagged_by_population[population] = flagged_by_population.get(population, 0) + 1
    text = (
        f"Exactly {len(flagged)} row(s) carry the extrapolation flag "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(flagged_by_population.items())) or 'none'}). "
        "They are fit evaluations outside their per-composition measurement "
        "windows, are all held, and entered no statistic. More broadly, "
        f"{len(held)} row(s) in the eligible source populations are held "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(by_parent.items())) or 'none'}); "
        "the extrapolation-flagged rows are the flagged subset. No scored row "
        "carries the flag."
    )
    return Disclosure(
        id="D3-extrapolation-flagged-held-rows",
        title="Extrapolation-flagged held rows",
        text=text,
        facts={
            "flagged_count": len(flagged),
            "flagged_by_population": flagged_by_population,
            "held_count": len(held),
            "held_by_parent_oxide": by_parent,
            "scored_flagged_count": 0,
        },
    )


def read_eligible_corpus(bench_set_path: Path | str) -> EligibleCorpus:
    """Read, seal, and audit the eligible measured corpus (steps 1 and 4)."""
    path = Path(bench_set_path)
    if not path.is_file():
        raise CorpusRefusal(f"bench set is not readable: {path}")
    raw_bytes = path.read_bytes()
    fixture = load_bench_set(path)
    compositions = dict(fixture["compositions"])

    points: list[EligiblePoint] = []
    exclusions: list[ExcludedPoint] = []
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for entry in fixture["points"]:
        point_id = str(entry["id"])
        source = str(entry["population"])
        reason = _exclusion_reason(entry)
        if reason is not None:
            exclusions.append(
                ExcludedPoint(point_id=point_id, source=source, reason=reason)
            )
            continue
        composition_id = str(entry["composition_id"])
        meta = compositions.get(composition_id)
        if meta is None:
            raise CorpusRefusal(
                f"row {point_id!r} names composition {composition_id!r} that the "
                "bench set does not define"
            )
        composition = composition_wt_pct_for_point(entry, compositions)
        if not composition:
            raise CorpusRefusal(
                f"composition {composition_id!r} normalizes to nothing"
            )
        evidence = _evidence_class(entry)
        raw_by_id[point_id] = entry
        points.append(
            EligiblePoint(
                point_id=point_id,
                source=source,
                composition_id=composition_id,
                temperature_K=float(entry["temperature_K"]),
                channel=channel_identity(
                    str(entry["observable"]),
                    str(entry["parent_oxide"]),
                    str(entry["species"]),
                    float(entry["temperature_K"]),
                ),
                observable=str(entry["observable"]),
                parent_oxide=str(entry["parent_oxide"]),
                species=str(entry["species"]),
                measured=float(entry["measured"]),
                fO2_bar=(
                    None if entry.get("fO2_bar") is None else float(entry["fO2_bar"])
                ),
                composition_wt_pct=dict(composition),
                na2o_wt_pct=float(composition.get(_NA_OXIDE, 0.0)),
                sio2_wt_pct=float(composition.get(_SI_OXIDE, 0.0)),
                evidence_class=evidence,
                source_evidence_class=(
                    None
                    if entry.get("source_evidence_class") is None
                    else str(entry["source_evidence_class"])
                ),
                measurement_type=(
                    "experimental"
                    if evidence.startswith(EXPERIMENTAL_CLASS_PREFIX)
                    else "model"
                ),
                source_sha256=str(entry["provenance"]["source_sha256"]),
            )
        )

    ordered = tuple(sorted(points, key=lambda p: (p.source, p.composition_id, p.channel)))
    bench_sha = hashlib.sha256(raw_bytes).hexdigest()
    manifest_bytes = build_manifest_bytes(
        ordered,
        bench_set_path=str(path),
        bench_set_sha256=bench_sha,
    )
    manifest = SealedNaManifest.from_bytes(manifest_bytes)
    eligible_sources = sorted({point.source for point in ordered})
    disclosures = (
        _gibbs_duhem_disclosure(ordered),
        _standard_state_disclosure(ordered, raw_by_id),
        _extrapolation_disclosure(list(fixture["points"]), eligible_sources),
    )
    return EligibleCorpus(
        bench_set_path=str(path),
        bench_set_sha256=bench_sha,
        points=ordered,
        exclusions=tuple(
            sorted(exclusions, key=lambda row: (row.source, row.point_id))
        ),
        manifest_bytes=manifest_bytes,
        manifest=manifest,
        disclosures=disclosures,
    )


def assemble_responses(
    corpus: EligibleCorpus,
    *,
    control: Mapping[str, CellOutcome],
    plus: Mapping[str, CellOutcome],
    minus: Mapping[str, CellOutcome],
) -> tuple[ChannelResponse, ...]:
    """One :class:`ChannelResponse` per eligible row, proofs attached.

    Every absent cell carries the evaluator's own typed status/reason; the
    channel-level ``missing`` shorthand is deliberately never used, so a
    wholly refused channel still reports THREE distinct typed reasons rather
    than one collapsed one. Missing cells are never imputed zero (step 4).
    """
    responses: list[ChannelResponse] = []
    for point in corpus.points:
        cells: dict[str, float] = {}
        reasons: dict[str, str] = {}
        for name, table in (
            ("y_control", control),
            ("y_plus", plus),
            ("y_minus", minus),
        ):
            outcome = table.get(point.point_id)
            if outcome is None:
                # Missing input refuses: an unevaluated cell is not a typed
                # missing cell, it is an incomplete screen.
                raise CorpusRefusal(
                    f"no evaluated {name} cell for eligible row "
                    f"{point.point_id!r}; the corpus was not run completely"
                )
            if outcome.value is None:
                reasons[name] = outcome.typed_reason
            else:
                cells[name] = float(outcome.value)
        responses.append(
            ChannelResponse(
                source=point.source,
                composition_id=point.composition_id,
                temperature_K=point.temperature_K,
                channel=point.channel,
                y_control=cells.get("y_control"),
                y_plus=cells.get("y_plus"),
                y_minus=cells.get("y_minus"),
                missing_cells=reasons or None,
                na_proof=NaBearingProof(
                    manifest=corpus.manifest,
                    source=point.source,
                    composition_id=point.composition_id,
                    channel=point.channel,
                ),
            )
        )
    return tuple(responses)
