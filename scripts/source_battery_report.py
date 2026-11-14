#!/usr/bin/env python3
"""Show how one literature extract moves through migration and battery scoring."""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.enums import AdmissionStatus, Engine, ResidualStatus  # noqa: E402
from simulator.battery.migrate import (  # noqa: E402
    iter_observation_store_paths,
    migrate,
    write_outputs,
)
from simulator.battery.score import (  # noqa: E402
    SCORE_ENGINE_SET,
    _observation_matches_run_filters,
    _short_refusal_token,
    comparison_candidates,
    load_score_context,
    refusal_census,
    score_store,
)
from simulator.battery.validate import observation_issue_location  # noqa: E402


def _isolated_source_root(source_id: str, repo_root: Path, out_root: Path) -> Path:
    extract_path = repo_root / "data" / "literature" / "extracts" / f"{source_id}.yaml"
    if not extract_path.is_file():
        raise FileNotFoundError(f"no extract for source_id {source_id!r}: {extract_path}")
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    if not isinstance(extract, dict) or extract.get("source_id") != source_id:
        raise ValueError(f"extract source_id does not match {source_id!r}: {extract_path}")

    index_path = repo_root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, dict) or not isinstance(index.get("sources"), list):
        raise ValueError(f"invalid literature index: {index_path}")
    source_rows = [
        row
        for row in index["sources"]
        if isinstance(row, dict) and row.get("source_id") == source_id
    ]
    if len(source_rows) != 1:
        raise ValueError(
            f"expected one literature index row for {source_id!r}, found {len(source_rows)}"
        )

    literature = out_root / "data" / "literature"
    extracts = literature / "extracts"
    compilations = literature / "compilations"
    extracts.mkdir(parents=True)
    compilations.mkdir()
    shutil.copyfile(extract_path, extracts / extract_path.name)
    (literature / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": index.get("schema_version"), "sources": source_rows},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    ratchet = repo_root / "data" / "battery" / "decision-band-ratchet.yaml"
    if ratchet.is_file():
        battery = out_root / "data" / "battery"
        battery.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ratchet, battery / ratchet.name)
    return out_root


def _overlay_observation_store(
    committed_dir: Path,
    migrated_dir: Path,
    overlay_dir: Path,
    *,
    source_id: str,
    replace_source_extract: bool = False,
) -> None:
    overlay_dir.mkdir(parents=True)
    migrated_paths = iter_observation_store_paths(migrated_dir)
    migrated_by_relative = {
        path.relative_to(migrated_dir): path for path in migrated_paths
    }
    for path in iter_observation_store_paths(committed_dir):
        relative = path.relative_to(committed_dir)
        if replace_source_extract and relative == Path(f"{source_id}.yaml"):
            continue
        if relative in migrated_by_relative:
            continue
        target = overlay_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(path)
    for relative, path in migrated_by_relative.items():
        target = overlay_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def _score_overlay_root(
    source_id: str,
    repo_root: Path,
    migrated_root: Path,
    scratch_root: Path,
) -> Path:
    """Build a read-only corpus view with this source's migrated store overlaid."""

    literature = repo_root / "data" / "literature"
    migrated_literature = migrated_root / "data" / "literature"
    overlay_root = scratch_root / "score-context"
    overlay_literature = overlay_root / "data" / "literature"
    overlay_literature.mkdir(parents=True)
    for path in literature.iterdir():
        target = overlay_literature / path.name
        if path.name in {"extracts-v2", "observations-v2"}:
            _overlay_observation_store(
                path,
                migrated_literature / path.name,
                target,
                source_id=source_id,
                replace_source_extract=path.name == "extracts-v2",
            )
        else:
            target.symlink_to(path, target_is_directory=path.is_dir())
    (overlay_root / "data" / "battery").symlink_to(
        repo_root / "data" / "battery", target_is_directory=True
    )
    return overlay_root


def _score_context_for_source(
    source_id: str,
    *,
    repo_root: Path,
    migrated_root: Path,
    scratch_root: Path,
    context_sources: Sequence[str] | None,
) -> Any:
    overlay_root = _score_overlay_root(
        source_id, repo_root, migrated_root, scratch_root
    )
    corpus = load_score_context(overlay_root, sources=context_sources)
    migrated = load_score_context(migrated_root, sources=(source_id,))
    return replace(
        corpus,
        works={**corpus.works, **migrated.works},
        experiments={**corpus.experiments, **migrated.experiments},
        observations={**corpus.observations, **migrated.observations},
        origins={**corpus.origins, **migrated.origins},
        extract_review={**corpus.extract_review, **migrated.extract_review},
        benches={**corpus.benches, **migrated.benches},
        hard_issues_by_observation={
            **corpus.hard_issues_by_observation,
            **migrated.hard_issues_by_observation,
        },
        root=overlay_root,
    )


def _score_source(
    context: Any,
    source_id: str,
    *,
    engines: Sequence[Engine] | None,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    source_observation_ids = {
        observation_id
        for observation_id, observation in context.observations.items()
        if observation.source_id == source_id
    }
    candidates = tuple(
        row
        for row in comparison_candidates(context)
        if row.observation_id in source_observation_ids
        and _observation_matches_run_filters(context, row, work_id=source_id)
    )
    residuals, _scored_candidates = score_store(
        context, engines=engines, work_id=source_id
    )
    return candidates, tuple(
        row for row in residuals if row.reference in source_observation_ids
    )


def _hard_issue_rows(validation: Any) -> tuple[int, list[dict[str, object]]]:
    issues = validation.hard_issues
    counts: Counter[str] = Counter()
    observation_ids: dict[str, set[str]] = defaultdict(set)
    for issue in issues:
        reason = issue.reason.value
        counts[reason] += 1
        location = observation_issue_location(issue.path)
        if location is not None:
            observation_ids[reason].add(location[0])
    rows = [
        {
            "reason": reason,
            "count": counts[reason],
            "distinct_observations": len(observation_ids[reason]),
            "example_observation_ids": sorted(observation_ids[reason])[:3],
        }
        for reason in sorted(counts)
    ]
    return len(issues), rows


def _score_rows(residuals: Sequence[Any]) -> dict[str, object]:
    by_rail: dict[str, list[Any]] = defaultdict(list)
    refusal_examples: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    for residual in residuals:
        rail = "unassigned" if residual.rail is None else residual.rail.value
        by_rail[rail].append(residual)
        if residual.status is ResidualStatus.REFUSED and residual.refusal is not None:
            detail_reason = _short_refusal_token(
                residual.refusal.detail.get("reason")
            )
            refusal_examples[(residual.refusal.reason.value, detail_reason)].add(
                residual.reference
            )

    def stage_rows(rows: Sequence[Any]) -> dict[str, object]:
        return {
            "distinct_observations": len({row.reference for row in rows}),
            "engine_observation_events": len(rows),
        }

    rails: list[dict[str, object]] = []
    for rail, rows in sorted(by_rail.items()):
        numeric = [row for row in rows if row.numeric is not None]
        eligible = [row for row in rows if row.score_eligible]
        inside_band = [
            row
            for row in numeric
            if row.status is ResidualStatus.MATCH
        ]
        rails.append(
            {
                "rail": rail,
                "numeric_residuals": stage_rows(numeric),
                "score_eligible": stage_rows(eligible),
                "inside_band": stage_rows(inside_band),
            }
        )

    refusal_counts: Counter[str] = Counter()
    detail_counts: dict[str, Counter[str | None]] = defaultdict(Counter)
    for key, count in refusal_census(residuals).items():
        reason, separator, detail_reason = key.partition(":")
        refusal_counts[reason] += count
        detail_counts[reason][detail_reason if separator else None] += count
    refusal_rows = [
        {
            "reason": reason,
            "distinct_observations": len(
                {
                    observation_id
                    for (refusal_reason, _detail_reason), observation_ids
                    in refusal_examples.items()
                    if refusal_reason == reason
                    for observation_id in observation_ids
                }
            ),
            "engine_observation_events": refusal_counts[reason],
            "example_observation_ids": sorted(
                {
                    observation_id
                    for (refusal_reason, _detail_reason), observation_ids
                    in refusal_examples.items()
                    if refusal_reason == reason
                    for observation_id in observation_ids
                }
            )[:3],
            "detail_reasons": [
                {
                    "reason": detail_reason,
                    "engine_observation_events": count,
                    "example_observation_ids": sorted(
                        refusal_examples[(reason, detail_reason)]
                    )[:3],
                }
                for detail_reason, count in sorted(
                    detail_counts[reason].items(),
                    key=lambda row: "" if row[0] is None else row[0],
                )
            ],
        }
        for reason in sorted(refusal_counts)
    ]
    return {
        "engine_observation_events": len(residuals),
        "numeric_residuals": stage_rows(
            [row for row in residuals if row.numeric is not None]
        ),
        "score_eligible": stage_rows(
            [row for row in residuals if row.score_eligible]
        ),
        "inside_band": stage_rows(
            [
                row
                for row in residuals
                if row.numeric is not None and row.status is ResidualStatus.MATCH
            ]
        ),
        "rails": rails,
        "refusals_by_reason": refusal_rows,
    }


def build_source_report(
    source_id: str,
    *,
    engines: Sequence[Engine] | None = None,
    context_sources: Sequence[str] | None = None,
    repo_root: Path = ROOT,
    scratch_out_dir: Path | None = None,
) -> dict[str, object]:
    """Migrate one extract, then score it against its selected corpus context.

    ``context_sources=None`` loads the full corpus, as used by the CLI.
    """

    if scratch_out_dir is None:
        out_root = Path(tempfile.mkdtemp(prefix=f"source-battery-report-{source_id}-"))
    else:
        out_root = scratch_out_dir
        out_root.mkdir(parents=True, exist_ok=False)
    _isolated_source_root(source_id, repo_root, out_root)

    result = migrate(out_root, write=False)
    if result.validation is None:
        raise RuntimeError("migration did not return its validator report")
    write_outputs(result, out_root)

    context = _score_context_for_source(
        source_id,
        repo_root=repo_root,
        migrated_root=out_root,
        scratch_root=out_root,
        context_sources=context_sources,
    )
    candidates, residuals = _score_source(context, source_id, engines=engines)
    admitted = sorted(
        row.observation_id
        for row in candidates
        if row.admission.status is AdmissionStatus.ADMITTED
    )
    pending = sorted(
        row.observation_id
        for row in candidates
        if row.admission.status is AdmissionStatus.PENDING
    )

    hard_issue_count, hard_issues_by_reason = _hard_issue_rows(result.validation)
    extract_key = f"data/literature/extracts/{source_id}.yaml"
    extract_rows = result.source_counts.get(extract_key)
    extract_row_count = 0 if extract_rows is None else extract_rows.rows_in
    return {
        "source_id": source_id,
        "scratch_out_dir": str(out_root),
        "engines": [
            engine.value for engine in (SCORE_ENGINE_SET if engines is None else engines)
        ],
        "extract_rows": extract_row_count,
        "extract_has_rows": extract_row_count > 0,
        "migrated_observations": len(result.observations),
        "hard_issue_count": hard_issue_count,
        "hard_issues_by_reason": hard_issues_by_reason,
        "candidates": {
            "admitted": {"count": len(admitted), "observation_ids": admitted},
            "pending": {"count": len(pending), "observation_ids": pending},
        },
        "scoring": _score_rows(residuals),
    }


def _markdown(report: dict[str, object]) -> str:
    candidates = report["candidates"]
    assert isinstance(candidates, dict)
    scoring = report["scoring"]
    assert isinstance(scoring, dict)
    lines = [
        f"Source battery report: `{report['source_id']}`",
        "",
        f"Scratch output: `{report['scratch_out_dir']}`",
        "",
        "| Stage | Rail / reason | Distinct observations | Count | Details |",
        "|---|---|---:|---:|---|",
        f"| Extract rows | — | — | {report['extract_rows']} | "
        f"{'source extract rows' if report['extract_has_rows'] else 'extract has no rows'} |",
        f"| Migrated observations | — | {report['migrated_observations']} | — | distinct observations |",
    ]
    hard_issues = report["hard_issues_by_reason"]
    assert isinstance(hard_issues, list)
    if hard_issues:
        for row in hard_issues:
            examples = ", ".join(row["example_observation_ids"]) or "—"
            lines.append(
                f"| Hard issues | {row['reason']} | {row['distinct_observations']} | "
                f"{row['count']} | {examples} |"
            )
        lines.append(
            f"| Hard issues | total | — | {report['hard_issue_count']} | all reasons |"
        )
    else:
        lines.append("| Hard issues | total | 0 | 0 | no hard issues |")

    for status in ("admitted", "pending"):
        row = candidates[status]
        lines.append(
            f"| Candidates | {status} | {row['count']} | — | distinct observations |"
        )

    metric_rows = (
        ("Numeric residuals", "numeric_residuals"),
        ("Score-eligible", "score_eligible"),
        ("Inside band", "inside_band"),
    )
    for label, key in metric_rows:
        row = scoring[key]
        lines.append(
            f"| {label} | all rails | {row['distinct_observations']} | "
            f"{row['engine_observation_events']} | engine × observation events |"
        )
        for rail_row in scoring["rails"]:
            rail_counts = rail_row[key]
            lines.append(
                f"| {label} | {rail_row['rail']} | "
                f"{rail_counts['distinct_observations']} | "
                f"{rail_counts['engine_observation_events']} | engine × observation events |"
            )

    for row in scoring["refusals_by_reason"]:
        examples = ", ".join(row["example_observation_ids"])
        details = "; ".join(
            f"{detail['reason'] or 'detail.reason not reported'}: "
            f"{detail['engine_observation_events']}"
            for detail in row["detail_reasons"]
        )
        lines.append(
            f"| Typed refusals | {row['reason']} | "
            f"{row['distinct_observations']} | {row['engine_observation_events']} | "
            f"{details}; examples: {examples} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id")
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=[engine.value for engine in SCORE_ENGINE_SET],
        help="score with these engines (default: score.py's full engine set)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    engines = None if args.engines is None else tuple(Engine(item) for item in args.engines)
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            report = build_source_report(
                args.source_id, engines=engines, context_sources=None
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        report = build_source_report(
            args.source_id, engines=engines, context_sources=None
        )
        print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
