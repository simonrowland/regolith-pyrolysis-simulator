#!/usr/bin/env python3
"""Normalize an optimizer winner recipe into data/recipes."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.recipe_io import RecipeIOError, save_recipe_to_library


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Save winner.recipe.yaml into data/recipes/<name>.yaml",
    )
    parser.add_argument(
        "source",
        help="Optimizer output directory or path to winner.recipe.yaml",
    )
    parser.add_argument("name", help="Recipe library name or <name>.yaml")
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=None,
        help="Recipe library directory (default: data/recipes)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        kwargs = {}
        if args.library_dir is not None:
            kwargs["library_dir"] = args.library_dir
        destination = save_recipe_to_library(Path(args.source), args.name, **kwargs)
    except RecipeIOError as exc:
        parser.exit(1, f"save_recipe.py: error: {exc}\n")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
