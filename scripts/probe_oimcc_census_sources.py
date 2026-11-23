#!/usr/bin/env python3
"""Per-source OpenIMCC produced/refused counts for the t-1936 census sources.

No arguments. The working directory is the import root. Prints comparable
text for a base run and a candidate run. The scorer decides every reason.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


_SOURCES = (
    "shornikov-2020-cao-tio2-melts",
    "shornikov-2025-calcium-oxide-evaporation",
    "shornikov-2026-wollastonite-kems",
    "kems-137-bischof-2023",
    "kems-138-bischof-2023",
    "kems-012-sossi-2019",
    "borisov-2018-compilation",
    "kress-carmichael-1989-lime-iron-silicate-redox",
    "mysen-1984-alkaline-earth-redox",
    "mysen-virgo-1989-nas-redox",
    "jayasuriya-2004-iron-mossbauer",
)
_RAILS = ("vapour", "redox")


def _reason_key(residual) -> str:
    refusal = residual.refusal
    reason = refusal.reason.value
    detail = refusal.detail or {}
    detail_reason = detail.get("reason")
    if isinstance(detail_reason, str) and detail_reason != reason:
        return f"{reason}:{detail_reason}"
    return reason


def main() -> int:
    if len(sys.argv) != 1:
        print("probe oimcc-census-sources takes no arguments", file=sys.stderr)
        return 2
    root = Path.cwd()
    sys.path.insert(0, str(root))
    from collections import Counter, defaultdict

    from simulator.battery.enums import Engine, ResidualStatus
    from simulator.battery.score import load_score_context, score_store

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    head = completed.stdout.strip() if completed.returncode == 0 else "unknown"
    print("probe oimcc-census-sources")
    print(f"head {head}")

    context = load_score_context(root, sources=_SOURCES)
    residuals, _candidates = score_store(context, engines=(Engine.OPENIMCC,))
    observations_by_source: dict[str, int] = {source: 0 for source in _SOURCES}
    other_sources: Counter[str] = Counter()
    for observation in context.observations.values():
        source_id = observation.source_id or ""
        if source_id in observations_by_source:
            observations_by_source[source_id] += 1
        else:
            other_sources[source_id] += 1

    produced: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    refused: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for residual in residuals:
        observation = context.observations.get(residual.reference)
        source_id = observation.source_id if observation is not None else "unmatched"
        rail = residual.rail.value if residual.rail is not None else "none"
        if residual.status is ResidualStatus.REFUSED:
            refused[(source_id, rail)][_reason_key(residual)] += 1
        else:
            produced[(source_id, rail)][residual.status.value] += 1

    total_produced = 0
    total_refused = 0
    extra_residual_sources = sorted(
        {source for source, _rail in (*produced, *refused)}.difference(_SOURCES)
    )
    for source in (*_SOURCES, *extra_residual_sources):
        print(f"source {source}")
        observation_n = observations_by_source.get(source)
        if observation_n is None:
            observation_n = other_sources.get(source, 0)
        print(f"  observations {observation_n}")
        rails = list(_RAILS)
        extra = sorted(
            {
                rail
                for (row_source, rail) in (*produced, *refused)
                if row_source == source and rail not in _RAILS
            }
        )
        for rail in rails + extra:
            produced_counts = produced.get((source, rail), Counter())
            refused_counts = refused.get((source, rail), Counter())
            produced_n = sum(produced_counts.values())
            refused_n = sum(refused_counts.values())
            total_produced += produced_n
            total_refused += refused_n
            print(f"  rail {rail} produced {produced_n} refused {refused_n}")
            for status in sorted(produced_counts):
                print(f"    produced {status} {produced_counts[status]}")
            for reason in sorted(refused_counts):
                print(f"    refused {reason} {refused_counts[reason]}")
    if other_sources:
        print("other_sources")
        for source, count in sorted(other_sources.items()):
            print(f"  {source or '(blank)'} {count}")
    print(
        "totals "
        f"observations {sum(observations_by_source.values())} "
        f"produced {total_produced} refused {total_refused}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
