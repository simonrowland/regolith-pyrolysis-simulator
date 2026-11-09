"""Render an optimizer winner and its direct run as the README hero tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from simulator.accounting.yield_disposition import (
    YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION,
)


MELT_OFFGAS_O2_ACCOUNTS = frozenset(
    {
        "terminal.oxygen_melt_offgas_captured",
        "terminal.oxygen_melt_offgas_stored",
        "terminal.oxygen_melt_offgas_vented_to_vacuum",
    }
)


def render_hero_tables(
    summary: Mapping[str, Any],
    run_report: Mapping[str, Any],
    *,
    commit: str,
) -> str:
    winner = summary.get("winner")
    if not isinstance(winner, Mapping) or winner.get("feasible") is not True:
        raise ValueError("study summary has no feasible winner")

    metadata = run_report["run_metadata"]
    if summary.get("feedstock_id") != metadata.get("feedstock_id"):
        raise ValueError("study and winner run use different feedstocks")

    fraction_rows = run_report["yield_disposition"]["fraction_table"]["rows"]
    element_lines = [
        "| element | left the melt | not left the melt | cleanup sequestered |",
        "|---|---:|---:|---:|",
    ]
    for row in fraction_rows:
        fractions = row["destination_fractions"]
        left = sum(
            float(fractions.get(destination, 0.0))
            for destination in YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION.left_melt
        )
        stayed = sum(
            float(fractions.get(destination, 0.0))
            for destination in YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION.not_left_melt
        )
        cleanup = sum(
            float(fractions.get(destination, 0.0))
            for destination in YIELD_DISPOSITION_MELT_BOUNDARY_PARTITION.cleanup_sequestered
        )
        element_lines.append(
            f"| {row['element']} | {left:.3f} | {stayed:.3f} | {cleanup:.3f} |"
        )

    rump = run_report["product_classification"]["classification"][
        "refractory_ceramic_rump"
    ]
    rump_total = float(rump["rump_total_kg"])
    composition = "; ".join(
        f"{species} {float(mass) / rump_total * 100:.1f} wt%"
        for species, mass in sorted(
            rump["rump_kg_by_species"].items(),
            key=lambda item: (-float(item[1]), str(item[0])),
        )
    )
    oxygen_released_kg = sum(
        float(row["terminal_kg"])
        for row in run_report["yield_disposition"]["terminal_species_streams"]
        if row["species"] == "O2" and row["account"] in MELT_OFFGAS_O2_ACCOUNTS
    )
    result_lines = [
        "| result | value |",
        "|---|---|",
        f"| Rump mass | {rump_total:.3f} kg |",
        f"| Rump composition | {composition} |",
        f"| O₂ released from melt | {oxygen_released_kg:.3f} kg |",
        f"| Backend | {metadata['backend']} |",
        f"| Winner | {winner['candidate_id']} |",
        f"| Seed | {summary['seed']} |",
        f"| Budget | {summary['budget']} |",
        f"| Commit | {commit} |",
    ]
    return "\n".join((*element_lines, "", *result_lines))


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_summary", type=Path)
    parser.add_argument("winner_run_report", type=Path)
    parser.add_argument("--commit", required=True, help="commit SHA used for the study")
    args = parser.parse_args()
    try:
        output = render_hero_tables(
            _read_json(args.study_summary),
            _read_json(args.winner_run_report),
            commit=args.commit,
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
