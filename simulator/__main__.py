"""One-entry-point CLI for simulator surfaces."""

from __future__ import annotations

import sys
from typing import Sequence


def _print_help() -> None:
    print(
        "usage: python -m simulator {run,session} ...\n\n"
        "subcommands:\n"
        "  run      deterministic batch runner; flags match python -m simulator.runner\n"
        "  session  non-interactive SimSession script harness; emits NDJSON"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        _print_help()
        return 0

    command = args[0]
    rest = args[1:]
    if command == "run":
        from simulator.runner import main as runner_main

        return runner_main(rest)
    if command == "session":
        from simulator.session_cli import main as session_main

        return session_main(rest)

    print(f"unknown simulator subcommand: {command}", file=sys.stderr)
    _print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

