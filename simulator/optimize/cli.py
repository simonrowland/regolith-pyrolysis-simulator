"""Command-line entrypoint for Phase-O optimizer studies."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Sequence

from simulator.optimize.profiles import ProfileValidationError, load_profile
from simulator.optimize.study import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_PROFILES,
    STRATEGY_CLASS_NAMES,
    VALID_FIDELITIES,
    StudyError,
    run,
)


JOB_STATUS_NAME = "job_status.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        profile = _resolve_profile_arg(args.profile, parser)
        result = run(
            profile=profile,
            feedstock=args.feedstock,
            strategy=args.strategy,
            fidelity=args.fidelity,
            parallel=args.parallel,
            budget=args.budget,
            out_dir=args.out,
            seed=args.seed,
        )
    except (OSError, ProfileValidationError, StudyError, TypeError, ValueError) as exc:
        _write_job_status(
            args.out,
            status="FAILED",
            reason=type(exc).__name__,
            message=str(exc),
        )
        parser.exit(2, f"error: {exc}\n")
    print(f"out_dir: {result.out_dir}")
    print(f"winner: {result.winner.candidate_id}")
    print(f"strategy: {args.strategy}->{STRATEGY_CLASS_NAMES[args.strategy]}")
    _write_job_status(
        result.out_dir,
        status="SUCCEEDED",
        reason="completed",
        winner_candidate_id=result.winner.candidate_id,
    )
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
        help="built-in profile name, feedstock profile id, or YAML profile path",
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


def _resolve_profile_arg(profile: str, parser: argparse.ArgumentParser):
    if profile in DEFAULT_PROFILES:
        return profile
    try:
        return load_profile(profile)
    except ProfileValidationError as exc:
        profile_path = Path(profile)
        if not profile_path.exists() and profile_path.suffix not in {".yaml", ".yml"}:
            parser.exit(
                2,
                f"error: argument --profile: invalid choice: {profile!r}\n",
            )
        raise exc


def _write_job_status(
    out_dir: Path | None,
    *,
    status: str,
    reason: str,
    message: str = "",
    winner_candidate_id: str | None = None,
) -> None:
    if out_dir is None:
        return
    payload = {
        "completed_at": datetime.now(UTC).isoformat(),
        "message": message,
        "reason": reason,
        "status": status,
        "success": status == "SUCCEEDED",
    }
    if winner_candidate_id is not None:
        payload["winner_candidate_id"] = winner_candidate_id
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f"{JOB_STATUS_NAME}.tmp"
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(out_dir / JOB_STATUS_NAME)
    except OSError:
        return


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
