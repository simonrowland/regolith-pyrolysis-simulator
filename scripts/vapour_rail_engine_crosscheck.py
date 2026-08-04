#!/usr/bin/env python3
"""Run the diagnostic vapour-rail / VapoRock measured-divergence campaign.

VapoRock calls are restricted to the VR-5 warm pool and the validated
1350--1950 K envelope.  The paired JSON/Markdown outputs report signed pressure
divergence, fO2 slopes, temperature dependence, and coverage asymmetries.  The
campaign never calibrates a coefficient and has no pass/fail exit threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.calibration import temperature_grid_K  # noqa: E402
from simulator.vapour_rail.engine_crosscheck import (  # noqa: E402
    DEFAULT_FEEDSTOCK_ID,
    DEFAULT_FO2_LOG10_BAR,
    DEFAULT_P_FLOOR_PA,
    render_crosscheck_markdown,
    run_engine_crosscheck,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory for engine_crosscheck_report.json and .md",
    )
    parser.add_argument(
        "--feedstock",
        default=DEFAULT_FEEDSTOCK_ID,
        help="feedstock ID from data/feedstocks.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--temperatures-K",
        nargs="+",
        type=float,
        default=None,
        help=(
            "explicit in-domain temperatures; default is the inclusive 50 K "
            "1350--1950 K grid plus 1573.15 K"
        ),
    )
    parser.add_argument(
        "--fo2-log10-bar",
        nargs="+",
        type=float,
        default=list(DEFAULT_FO2_LOG10_BAR),
        help="matched admitted fO2 grid (default: -9 -8 -7)",
    )
    parser.add_argument(
        "--warm-pool-size",
        type=int,
        default=1,
        help="VR-5 warm-pool worker count (default: %(default)s)",
    )
    parser.add_argument(
        "--p-floor-Pa",
        type=float,
        default=DEFAULT_P_FLOOR_PA,
        help="sub-floor pressures become censored intervals (default: %(default)g)",
    )
    return parser.parse_args(argv)


def write_reports(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "engine_crosscheck_report.json"
    markdown_path = output_dir / "engine_crosscheck_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_crosscheck_markdown(report))
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.warm_pool_size < 1:
        raise SystemExit("--warm-pool-size must be >= 1")
    temperatures = args.temperatures_K or temperature_grid_K()

    from simulator.vapour_rail.engine_crosscheck import load_crosscheck_composition

    report = run_engine_crosscheck(
        composition=load_crosscheck_composition(args.feedstock),
        temperatures_K=temperatures,
        fo2_log10_bar=args.fo2_log10_bar,
        p_floor_Pa=args.p_floor_Pa,
        warm_pool_size=args.warm_pool_size,
    )
    json_path, markdown_path = write_reports(report, args.output_dir)
    print(
        f"species_compared={','.join(report['coverage']['species_compared']) or 'none'}"
    )
    print(
        "wild_divergence_species="
        + (",".join(report["wild_divergence_species"]) or "none")
    )
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
