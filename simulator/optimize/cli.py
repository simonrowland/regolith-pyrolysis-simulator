"""Command-line entrypoint for Phase-O optimizer studies."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from simulator.optimize.study import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_PROFILES,
    STRATEGY_CLASS_NAMES,
    VALID_FIDELITIES,
    StudyError,
    run,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = run(
            profile=args.profile,
            feedstock=args.feedstock,
            strategy=args.strategy,
            fidelity=args.fidelity,
            parallel=args.parallel,
            budget=args.budget,
            out_dir=args.out,
            seed=args.seed,
        )
    except (OSError, StudyError, TypeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"out_dir: {result.out_dir}")
    print(f"winner: {result.winner.candidate_id}")
    print(f"strategy: {args.strategy}->{STRATEGY_CLASS_NAMES[args.strategy]}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.optimize",
        description="Run a Phase-O recipe optimizer study.",
    )
    parser.add_argument("--feedstock", required=True, help="feedstock id from data/feedstocks.yaml")
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_NAME,
        choices=tuple(sorted(DEFAULT_PROFILES)),
        help="objective profile name",
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=tuple(sorted(STRATEGY_CLASS_NAMES)),
        help="optimizer strategy",
    )
    parser.add_argument(
        "--fidelity",
        required=True,
        choices=VALID_FIDELITIES,
        help="evaluation fidelity",
    )
    parser.add_argument("--parallel", type=_positive_int, default=1, help="parallel workers")
    parser.add_argument("--budget", type=_positive_int, required=True, help="evaluation budget")
    parser.add_argument("--out", type=Path, default=None, help="artifact output directory")
    parser.add_argument("--seed", type=_non_negative_int, default=0, help="strategy seed")
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
