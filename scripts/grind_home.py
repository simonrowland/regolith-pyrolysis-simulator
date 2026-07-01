#!/usr/bin/env python3
"""Path helpers for the shared grind workspace.

This module is intentionally filesystem-light: it resolves path names but does
not create directories. Grind entrypoints can adopt these helpers later without
changing campaign behavior in this standalone tooling chunk.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Mapping, Sequence


GRIND_HOME_ENV = "GRIND_HOME"
DEFAULT_GRIND_HOME = Path("~/regolith-grind")
CAMPAIGNS_DIR = "campaigns"


def resolve_grind_home(
    env: Mapping[str, str] | None = None,
    *,
    default: Path | str = DEFAULT_GRIND_HOME,
) -> Path:
    """Return the configured grind home, expanding ``~`` only."""

    environ = os.environ if env is None else env
    configured = environ.get(GRIND_HOME_ENV, "").strip()
    root = Path(configured) if configured else Path(default)
    return _normalize_grind_home(root)


def _normalize_grind_home(root: Path | str) -> Path:
    expanded = Path(root).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"{GRIND_HOME_ENV} must resolve to an absolute path: {root!s}")
    return expanded.resolve(strict=False)


def _safe_component(value: str, *, label: str) -> str:
    component = value.strip()
    if not component:
        raise ValueError(f"{label} must not be empty")
    path = Path(component)
    if path.is_absolute() or path.name != component or component in {".", ".."}:
        raise ValueError(f"{label} must be a single relative path component: {value!r}")
    if "\x00" in component or "/" in component or "\\" in component:
        raise ValueError(f"{label} must be a single relative path component: {value!r}")
    return component


def campaign_dir(
    campaign: str,
    *,
    grind_home: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return ``GRIND_HOME/campaigns/<campaign>`` for a grind campaign."""

    root = _normalize_grind_home(grind_home) if grind_home is not None else resolve_grind_home(env)
    return root / CAMPAIGNS_DIR / _safe_component(campaign, label="campaign")


def campaign_path(
    campaign: str,
    *parts: str,
    grind_home: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return a validated path inside a campaign directory."""

    path = campaign_dir(campaign, grind_home=grind_home, env=env)
    for index, part in enumerate(parts):
        path = path / _safe_component(part, label=f"campaign path part {index + 1}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print GRIND_HOME-derived paths.")
    parser.add_argument(
        "--campaign",
        help="campaign name; prints the per-campaign directory when set",
    )
    parser.add_argument(
        "parts",
        nargs="*",
        help="optional path components below the campaign directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.parts and not args.campaign:
        raise SystemExit("path parts require --campaign")
    if args.campaign:
        print(campaign_path(args.campaign, *args.parts))
    else:
        print(resolve_grind_home())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
