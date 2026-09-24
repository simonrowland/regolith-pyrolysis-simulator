#!/usr/bin/env python3
"""Print per-source Bench consumer readiness as deterministic JSON.

PARTIAL means heterogeneous per-experiment statuses, not partial completeness.
Per-experiment rows remain available so callers can select a usable experiment.
A work with no scoreable observations is still listed, as a gap.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import (  # noqa: E402
    load_migrated_benches,
    load_migrated_store,
    load_yaml,
    discover_extracts,
    locator_from_mapping,
    to_plain,
)
from simulator.battery.enums import BenchIdentityBasis  # noqa: E402
from simulator.battery.records import (  # noqa: E402
    ApparatusGeometry,
    Bench,
    BenchIdentity,
    BenchReference,
    Located,
    Locator,
    State,
)
from simulator.battery.waypoints import (  # noqa: E402
    ConsumerReadiness,
    ENGINE_POINT_CONSUMERS,
    GapReason,
    ReadinessGap,
    ReadinessStatus,
    consumer_readiness,
    is_pure_substance_engine_reference,
    pure_substance_engine_point_gap,
)


def _missing_bench_readiness(*, implicit=False) -> tuple[ConsumerReadiness, ...]:
    # Multiple cited apparatuses is a permanent typed absence: the no-select
    # ruling forbids choosing one, so no acquisition can ever close it. It is
    # not a fetch work item (that class is reference_not_yet_resolved below).
    reason = GapReason.UNATTRIBUTABLE_BY_CONSTRUCTION if implicit else GapReason.MISSING_EVIDENCE
    gap = ReadinessGap(
        "bench_identity" if implicit else "bench", reason,
        ("bench.identity.ref: multiple cited apparatuses",) if implicit else ("experiment.bench_id",),
    )
    return (
        ConsumerReadiness("kems", ReadinessStatus.GAP, (gap,)),
        ConsumerReadiness("rps", ReadinessStatus.GAP, (gap,)),
        *(
            ConsumerReadiness(
                "engine_point", ReadinessStatus.GAP, (gap,), engine
            )
            for engine in ENGINE_POINT_CONSUMERS
        ),
    )


def _located_evidence(value):
    if isinstance(value, Located):
        yield value
    elif is_dataclass(value):
        for field in fields(value):
            yield from _located_evidence(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _located_evidence(item)


def _corpus_apparatus_leads(works) -> dict[str, list[tuple[Mapping, str]]]:
    leads: dict[str, list[tuple[Mapping, str]]] = {}
    for corpus in {work.source_files.corpus_repo for work in works.values()}:
        corpus_path = Path(corpus).expanduser()
        if not corpus_path.is_absolute():
            corpus_path = Path.home() / "Repos" / corpus_path
        ledger = corpus_path / "ledger/apparatus-reference-leads.yaml"
        if not ledger.exists():
            continue
        for lead in (load_yaml(ledger).get("leads") or ()):
            leads.setdefault(str(lead.get("citing")), []).append((lead, str(ledger)))
    return leads


def _lead_reference_obtained(lead: Mapping) -> bool:
    return bool(
        lead.get("resolution") == "obtained"
        or lead.get("obtained_path")
        or lead.get("already_held")
    )


def _apparatus_references(root, works):
    references = {}
    for path in discover_extracts(root / "data/literature/extracts"):
        doc = load_yaml(path)
        source = str(doc.get("source_id") or path.stem)
        pending = [doc]
        while pending:
            value = pending.pop()
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if key in {"apparatus_reference", "cited_apparatus_reference", "apparatus_ref"} and item:
                        raw = item if isinstance(item, Mapping) else {"cited_as": str(item)}
                        cited = raw.get("cited_as") or raw.get("reference")
                        if cited:
                            ref = BenchReference(raw.get("work_id"), str(cited),
                                tuple(raw.get("for_parameters") or ()),
                                locator_from_mapping(raw.get("locator")) or Locator(source_path=str(path), record=str(key)))
                            references.setdefault(source, []).append(ref)
                    else:
                        pending.append(item)
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
    for source, leads in _corpus_apparatus_leads(works).items():
        for lead, ledger in leads:
            if str(lead.get("identity_verdict") or "").strip().lower() == "no":
                # Acquisition-verified non-apparatus citation (e.g. sample
                # provenance); it must not occupy an apparatus-reference slot.
                continue
            cited = lead.get("lead_as_given")
            if cited:
                ref = BenchReference(None, str(cited), tuple(lead.get("for_parameters") or ()),
                    Locator(source_path=str(ledger), record=str(lead.get("citing")),
                            note=str(lead.get("reference_list_locator") or "apparatus reference lead")))
                references.setdefault(source, []).append(ref)
    return references


def _implicit_bench(experiment, work=None, references=()) -> Bench | None:
    apparatus = experiment.apparatus
    pumping = experiment.pressure_environment.pumping or {}
    pumping_speed = pumping.get("pumping_speed_m3_s")
    if pumping_speed is not None and not pumping_speed.state.is_value:
        pumping_speed = None
    method = None
    if experiment.method.is_value:
        method = Located(State.of(experiment.method.value.value))
    geometry = None
    if apparatus is not None and apparatus.geometry is not None:
        raw_geometry = apparatus.geometry
        scalar_names = (
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
        kwargs = {
            name: value
            for name in scalar_names
            if (value := getattr(raw_geometry, name)) is not None
            and value.state.is_value
        }
        for name in ("cell_internal_dimensions", "chamber_dimensions"):
            values = getattr(raw_geometry, name) or {}
            kept = {
                key: value for key, value in values.items() if value.state.is_value
            }
            if kept:
                kwargs[name] = kept
        if kwargs:
            geometry = ApparatusGeometry(**kwargs)
    refs = list(references)
    own_paths = {experiment.locator.source_path} if experiment.locator else set()
    if work is not None:
        own_paths.update(file.path for file in work.source_files.files)
    for evidence in (*_located_evidence(apparatus), *_located_evidence(pumping)):
        locator = evidence.locator
        if locator and locator.source_path and locator.source_path not in own_paths:
            if not any(ref.locator == locator for ref in refs):
                refs.append(BenchReference(None, locator.source_path, ("apparatus",), locator))
    unique = {ref.cited_as: ref for ref in refs}
    if len(unique) > 1:
        return None  # One identity cannot select among multiple cited apparatuses.
    blocks = ["experiment.method"]
    if pumping_speed is not None:
        blocks.append("experiment.pressure_environment.pumping")
    if apparatus is not None:
        blocks.append("experiment.apparatus")
    identity = (
        BenchIdentity(BenchIdentityBasis.CITED_BY_AUTHOR, next(iter(unique.values())))
        if unique else BenchIdentity(BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
                                    reason="Provenance unverified; embedded blocks: " + ", ".join(blocks))
    )
    return Bench(
        id=f"{experiment.experiment_id}::bench::implicit",
        work_id=experiment.work_id or "unknown",
        identity=identity,
        method=method,
        geometry=geometry,
        pumping_speed_m3_s=pumping_speed,
    )


def _status(items: list[ConsumerReadiness]) -> ReadinessStatus:
    statuses = {item.status for item in items}
    if len(statuses) == 1:
        return next(iter(statuses))
    return ReadinessStatus.PARTIAL


def _deduplicated_gaps(
    records: list[tuple[str, ConsumerReadiness]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, tuple[str, ...]], set[str]] = {}
    for experiment_id, item in records:
        seen: set[tuple[str, str, tuple[str, ...]]] = set()
        for gap in item.gaps:
            key = (gap.waypoint, gap.reason.value, gap.missing)
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(key, set()).add(experiment_id)
    return [
        {
            "waypoint": waypoint,
            "reason": reason,
            "missing": list(missing),
            "count": len(experiment_ids),
            "experiment_ids": sorted(experiment_ids),
        }
        for (waypoint, reason, missing), experiment_ids in sorted(grouped.items())
    ]


def _aggregate(
    items: list[tuple[str, tuple[ConsumerReadiness, ...]]],
    *,
    engine: str | None = None,
) -> dict[str, object]:
    selected = [
        (experiment_id, item)
        for experiment_id, group in items
        for item in group
        if item.engine == engine
        and (engine is None or item.consumer == "engine_point")
    ]
    records = [item for _, item in selected]
    return {
        "consumer": "engine_point" if engine else records[0].consumer,
        **({"engine": engine} if engine else {}),
        "status": _status(records).value,
        "gaps": _deduplicated_gaps(selected),
    }


def _run_counts(
    items: list[tuple[str, tuple[ConsumerReadiness, ...]]],
    *,
    consumer: str,
    engine: str | None = None,
) -> dict[str, int]:
    counts = {status.value: 0 for status in ReadinessStatus}
    for _, group in items:
        selected = [
            item for item in group
            if item.consumer == consumer and item.engine == engine
        ]
        if consumer == "engine_point" and engine is None:
            status = _collapse_engines(group).status
        elif len(selected) == 1:
            status = selected[0].status
        else:
            continue
        counts[status.value] += 1
    return counts


def _sources_without_scoreable_observations(works, listed: set[str]) -> tuple[str, ...]:
    """Canonical source ids that the experiment walk never reaches.

    Readiness attributes each experiment to ``Work.source_ids[0]``. A work
    whose observations live only in ``context[]`` (or that has no experiments
    at all) has nothing to score and would otherwise disappear.
    """

    missing: list[str] = []
    seen = set(listed)
    for work in works.values():
        source_id = work.source_ids[0]
        if source_id in seen:
            continue
        seen.add(source_id)
        missing.append(source_id)
    return tuple(sorted(missing))


def _no_scoreable_observations_row(source_id: str) -> dict[str, object]:
    gap = {
        "waypoint": "observations",
        "reason": GapReason.NO_SCOREABLE_OBSERVATIONS.value,
        "missing": ["scoreable observations"],
        "count": 0,
        "experiment_ids": [],
    }
    consumers = [
        {"consumer": name, "status": ReadinessStatus.GAP.value, "gaps": [gap]}
        for name in ("kems", "rps", "engine_point")
    ]
    engines = [
        {
            "consumer": "engine_point",
            "engine": engine,
            "status": ReadinessStatus.GAP.value,
            "gaps": [gap],
        }
        for engine in ENGINE_POINT_CONSUMERS
    ]
    return {
        "source_id": source_id,
        "consumers": consumers,
        "engines": engines,
        "run_counts": {
            "by_consumer": {
                consumer: {status.value: 0 for status in ReadinessStatus}
                for consumer in ("kems", "rps", "engine_point")
            },
            "by_engine": {
                engine: {status.value: 0 for status in ReadinessStatus}
                for engine in ENGINE_POINT_CONSUMERS
            },
        },
        "informational_gaps": [dict(gap)],
        "experiments": [],
    }


def _collapse_engines(group: tuple[ConsumerReadiness, ...]) -> ConsumerReadiness:
    engines = [item for item in group if item.engine is not None]
    gaps = []
    seen = set()
    for item in engines:
        for gap in item.gaps:
            key = (gap.waypoint, gap.reason, gap.missing)
            if key not in seen:
                seen.add(key)
                gaps.append(gap)
    return ConsumerReadiness("engine_point", _status(engines), tuple(gaps))


def report(root: Path, *, modelling_inputs=None) -> dict[str, object]:
    works, experiments, observations = load_migrated_store(root)
    by_experiment = defaultdict(list)
    by_experiment_all: dict[str, list] = defaultdict(list)
    for observation in observations.values():
        by_experiment_all[observation.experiment_id].append(observation)
        if observation.point_conditions:
            by_experiment[observation.experiment_id].append(observation)
    benches = load_migrated_benches(root)
    references = _apparatus_references(root, works)
    lead_obtained = {
        (source, str(lead.get("lead_as_given"))): _lead_reference_obtained(lead)
        for source, leads in _corpus_apparatus_leads(works).items()
        for lead, _ in leads
    }
    by_source: dict[str, list[dict[str, object]]] = {}
    source_readiness: dict[
        str, list[tuple[str, tuple[ConsumerReadiness, ...]]]
    ] = {}
    source_information: dict[str, set[str]] = {}
    source_pending: dict[str, dict[str, set[str]]] = {}
    experiment_pending: dict[str, str] = {}
    for experiment in sorted(experiments.values(), key=lambda item: item.experiment_id):
        work = works.get(experiment.work_id or "")
        source_ids = work.source_ids if work is not None else (experiment.work_id or "unknown",)
        # Work.source_ids lists every citation alias of one work (e.g.
        # janaf-4th / nist-janaf-4th). Attribute the experiment to exactly one
        # readiness row — the first-registered (canonical) source id — so an
        # aliased work is not double-counted. All aliases stay valid lookup
        # keys for apparatus references and acquisition leads below.
        row_source_ids = source_ids[:1]
        bench = benches.get(experiment.bench_id or "")
        implicit = experiment.bench_id is None
        if implicit:
            refs = [ref for source_id in source_ids for ref in references.get(source_id, ())]
            bench = _implicit_bench(experiment, work, refs)
        identity = bench.identity if bench is not None else None
        if identity is not None and identity.basis is BenchIdentityBasis.CITED_BY_AUTHOR:
            ref = identity.ref
            ledger_backed = ref.locator is not None and str(
                ref.locator.source_path or ""
            ).endswith("apparatus-reference-leads.yaml")
            obtained = any(
                lead_obtained.get((source_id, ref.cited_as), False)
                for source_id in source_ids
            )
            if ledger_backed and not obtained:
                # Single cited apparatus whose reference the corpus acquisition
                # ledger has not obtained: actionable, an acquisition work item.
                experiment_pending[experiment.experiment_id] = ref.cited_as
                for source_id in row_source_ids:
                    source_pending.setdefault(source_id, {}).setdefault(
                        ref.cited_as, set()
                    ).add(experiment.experiment_id)
        readiness = (
            _missing_bench_readiness(implicit=implicit)
            if bench is None
            else consumer_readiness(experiment, bench, modelling_inputs=modelling_inputs)
        )
        if bench is not None and by_experiment[experiment.experiment_id]:
            contexts = {}
            for observation in by_experiment[experiment.experiment_id]:
                key = repr(observation.point_conditions)
                contexts.setdefault(key, observation)
            groups = [
                consumer_readiness(
                    experiment,
                    bench,
                    observation,
                    modelling_inputs=modelling_inputs,
                )
                for observation in contexts.values()
            ]
            aggregated = []
            for base in readiness:
                if base.consumer == "rps":
                    aggregated.append(base)
                    continue
                records = [item for group in groups for item in group
                           if item.consumer == base.consumer and item.engine == base.engine]
                aggregated.append(ConsumerReadiness(base.consumer, _status(records),
                    tuple(dict.fromkeys(gap for item in records for gap in item.gaps)), base.engine,
                    tuple({repr(notice): notice for item in records for notice in item.notices}.values())))
            readiness = tuple(aggregated)
        # Own observations only. An alias that shares locator.table does not
        # lend another source's pure_substance_reference declaration.
        linked_obs = by_experiment_all.get(experiment.experiment_id, ())
        if any(is_pure_substance_engine_reference(item) for item in linked_obs):
            gap = pure_substance_engine_point_gap()
            readiness = tuple(
                ConsumerReadiness(
                    item.consumer,
                    ReadinessStatus.NOT_APPLICABLE,
                    (gap,),
                    item.engine,
                    item.notices,
                )
                if item.consumer == "engine_point"
                else item
                for item in readiness
            )
        consumers = tuple(item for item in readiness if item.engine is None)
        consumers += (_collapse_engines(readiness),)
        engines = tuple(item for item in readiness if item.engine is not None)
        for source_id in row_source_ids:
            by_source.setdefault(source_id, []).append(
                {
                    "work_id": experiment.work_id,
                    "experiment_id": experiment.experiment_id,
                    "bench_id": experiment.bench_id,
                    "bench_identity": to_plain(bench.identity) if bench is not None else None,
                    "consumers": to_plain(consumers),
                    "engines": to_plain(engines),
                    "informational_gaps": (
                        [
                            {
                                "waypoint": "bench_link",
                                "reason": "implicit_legacy_bench",
                                "missing": ["experiment.bench_id"],
                            }
                        ]
                        if implicit
                        else []
                    )
                    + (
                        [
                            {
                                "waypoint": "bench_identity",
                                "reason": "reference_not_yet_resolved",
                                "missing": [
                                    f"bench.identity.ref: {experiment_pending[experiment.experiment_id]}"
                                ],
                            }
                        ]
                        if experiment.experiment_id in experiment_pending
                        else []
                    ),
                }
            )
            source_readiness.setdefault(source_id, []).append(
                (experiment.experiment_id, readiness)
            )
            if implicit:
                source_information.setdefault(source_id, set()).add(
                    experiment.experiment_id
                )
    for source_id in _sources_without_scoreable_observations(works, set(by_source)):
        by_source.setdefault(source_id, [])
        source_readiness.setdefault(source_id, [])
    rows: list[dict[str, object]] = []
    empty_counts = {status.value: 0 for status in ReadinessStatus}
    by_consumer: dict[str, dict[str, int]] = {
        consumer: dict(empty_counts) for consumer in ("kems", "rps", "engine_point")
    }
    by_engine: dict[str, dict[str, int]] = {
        engine: dict(empty_counts) for engine in ENGINE_POINT_CONSUMERS
    }
    by_consumer_runs: dict[str, dict[str, int]] = {
        consumer: dict(empty_counts) for consumer in ("kems", "rps", "engine_point")
    }
    by_engine_runs: dict[str, dict[str, int]] = {
        engine: dict(empty_counts) for engine in ENGINE_POINT_CONSUMERS
    }
    blockers: dict[tuple[str, str], dict[str, set[str]]] = {}
    for source_id in sorted(by_source):
        source_items = source_readiness[source_id]
        if not source_items:
            row = _no_scoreable_observations_row(source_id)
            rows.append(row)
            for item in row["consumers"]:
                counts = by_consumer[str(item["consumer"])]
                status = str(item["status"])
                counts[status] = counts.get(status, 0) + 1
            for item in row["engines"]:
                counts = by_engine[str(item["engine"])]
                status = str(item["status"])
                counts[status] = counts.get(status, 0) + 1
            blockers.setdefault(
                ("observations", GapReason.NO_SCOREABLE_OBSERVATIONS.value),
                {"sources": set(), "experiments": set()},
            )["sources"].add(source_id)
            continue
        consumer_rows = []
        for consumer in ("kems", "rps"):
            subset = [
                (experiment_id, tuple(item for item in group if item.consumer == consumer))
                for experiment_id, group in source_items
            ]
            consumer_rows.append(_aggregate(subset))
        collapsed = [
            (experiment_id, (_collapse_engines(group),))
            for experiment_id, group in source_items
        ]
        consumer_rows.append(_aggregate(collapsed))
        engine_rows = [
            _aggregate(source_items, engine=engine)
            for engine in ENGINE_POINT_CONSUMERS
        ]
        run_counts = {
            "by_consumer": {
                consumer: _run_counts(source_items, consumer=consumer)
                for consumer in ("kems", "rps", "engine_point")
            },
            "by_engine": {
                engine: _run_counts(source_items, consumer="engine_point", engine=engine)
                for engine in ENGINE_POINT_CONSUMERS
            },
        }
        info_ids = sorted(source_information.get(source_id, set()))
        informational_gaps: list[dict[str, object]] = []
        if info_ids:
            informational_gaps.append(
                {
                    "waypoint": "bench_link",
                    "reason": "implicit_legacy_bench",
                    "missing": ["experiment.bench_id"],
                    "count": len(info_ids),
                    "experiment_ids": info_ids,
                }
            )
        for cited_as in sorted(source_pending.get(source_id, {})):
            pending_ids = sorted(source_pending[source_id][cited_as])
            informational_gaps.append(
                {
                    "waypoint": "bench_identity",
                    "reason": "reference_not_yet_resolved",
                    "missing": [f"bench.identity.ref: {cited_as}"],
                    "count": len(pending_ids),
                    "experiment_ids": pending_ids,
                }
            )
        rows.append(
            {
                "source_id": source_id,
                "consumers": consumer_rows,
                "engines": engine_rows,
                "run_counts": run_counts,
                "informational_gaps": informational_gaps,
                "experiments": by_source[source_id],
            }
        )
        for item in consumer_rows:
            counts = by_consumer[str(item["consumer"])]
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        for item in engine_rows:
            counts = by_engine[str(item["engine"])]
            status = str(item["status"])
            counts[status] = counts.get(status, 0) + 1
        for consumer, counts in run_counts["by_consumer"].items():
            for status, count in counts.items():
                by_consumer_runs[consumer][status] += count
        for engine, counts in run_counts["by_engine"].items():
            for status, count in counts.items():
                by_engine_runs[engine][status] += count
        for experiment_id, group in source_items:
            seen = set()
            for item in group:
                if item.status is not ReadinessStatus.GAP:
                    continue
                for gap in item.gaps:
                    key = (gap.waypoint, gap.reason.value)
                    if key in seen:
                        continue
                    seen.add(key)
                    entry = blockers.setdefault(
                        key, {"sources": set(), "experiments": set()}
                    )
                    entry["sources"].add(source_id)
                    entry["experiments"].add(experiment_id)
    top_blockers = [
        {
            "waypoint": waypoint,
            "reason": reason,
            "source_count": len(ids["sources"]),
            "experiment_count": len(ids["experiments"]),
        }
        for (waypoint, reason), ids in sorted(
            blockers.items(),
            key=lambda item: (
                -len(item[1]["sources"]),
                -len(item[1]["experiments"]),
                item[0],
            ),
        )[:10]
    ]
    def _blocker_counts(key: tuple[str, str]) -> dict[str, int]:
        entry = blockers.get(key)
        if entry is None:
            return {"source_count": 0, "experiment_count": 0}
        return {
            "source_count": len(entry["sources"]),
            "experiment_count": len(entry["experiments"]),
        }

    return {
        "schema_version": "bench_readiness.v2",
        "sources": rows,
        "summary": {
            "by_consumer": by_consumer,
            "by_engine": by_engine,
            "by_consumer_runs": by_consumer_runs,
            "by_engine_runs": by_engine_runs,
            "top_blocking_waypoints": top_blockers,
            # The two bench_identity verdicts, counted separately so a caller can
            # tell how much of the gap acquisition could ever close: permanent
            # multi-apparatus absences vs single cited references not yet fetched.
            "bench_identity_verdicts": {
                "unattributable_by_construction": _blocker_counts(
                    ("bench_identity", GapReason.UNATTRIBUTABLE_BY_CONSTRUCTION.value)
                ),
                "reference_not_yet_resolved": {
                    "source_count": len(source_pending),
                    "experiment_count": len(
                        {
                            experiment_id
                            for per_source in source_pending.values()
                            for experiment_ids in per_source.values()
                            for experiment_id in experiment_ids
                        }
                    ),
                },
            },
        },
        "source_count": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--modelling-inputs", type=Path, help="Explicit operator inputs for RPS, JSON")
    args = parser.parse_args(argv)
    modelling_inputs = json.loads(args.modelling_inputs.read_text()) if args.modelling_inputs else None
    print(json.dumps(
        report(args.root, modelling_inputs=modelling_inputs),
        sort_keys=True,
        separators=(",", ":"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
