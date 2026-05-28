"""E6c (north-star three-product runner CLI, 0.5.4.1, 2026-05-28).

Drives a SimSession through a campaign sweep and outputs the
four-product-class report from the E6a classifier
(``simulator.three_product_report.classify_products``) in either
markdown (E6b formatter) or JSON form.

CLI shape mirrors ``simulator/runner.py``'s SiO yield runner:

    python -m simulator.three_product_runner \\
        --feedstock lunar_mare_low_ti \\
        --campaign C2A \\
        --hours 24 \\
        --output report.md \\
        --format markdown

The runner is DIAGNOSTIC ONLY — it doesn't enforce yield
thresholds (that's E1b territory, deferred to post-Phase-D). It
surfaces what the simulator actually produced, mapped onto the
four north-star product classes per CLAUDE.md § 5.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.three_product_report import classify_products
from simulator.three_product_report_markdown import (
    format_three_product_markdown,
)


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


SUPPORTED_FORMATS = ('markdown', 'json')


def _load_yaml(data_dir: Path, name: str) -> dict:
    with (data_dir / name).open() as f:
        return yaml.safe_load(f) or {}


def _build_session(
    *,
    feedstock_id: str,
    campaign: str,
    data_dir: Path,
    backend_name: str = "stub",
) -> SimSession:
    """Build a SimSession with the canonical project setpoints +
    vapor pressures + feedstock catalog."""
    config = SimSessionConfig(
        feedstock_id=feedstock_id,
        feedstocks=_load_yaml(data_dir, "feedstocks.yaml"),
        setpoints=_load_yaml(data_dir, "setpoints.yaml"),
        vapor_pressures=_load_yaml(data_dir, "vapor_pressures.yaml"),
        campaign=campaign,
        backend_name=backend_name,
        backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
    )
    return SimSession().start(config)


def run(
    *,
    feedstock_id: str,
    campaign: str,
    hours: int,
    data_dir: Path | None = None,
    backend_name: str = "stub",
    early_tap_mode: bool = False,
) -> dict[str, Any]:
    """Programmatic entry point: build the session, run for
    ``hours`` ticks (or until completion), classify the resulting
    products. Returns the E6a classification dict directly.

    Args:
        feedstock_id: Feedstock catalog key (e.g.
            ``"lunar_mare_low_ti"``).
        campaign: Campaign label (e.g. ``"C2A"`` or
            ``"C2A_continuous"``).
        hours: Max simulated hours to advance.
        data_dir: Optional override for the data directory; defaults
            to the project's ``data/`` next to ``simulator/``.
        backend_name: Backend to select; ``"stub"`` is the default
            for runs without AlphaMELTS/MAGEMin installed.
        early_tap_mode: Pass-through to ``classify_products``; when
            True the residual ``cleaned_melt`` mass surfaces as the
            ``industrial_mixed_glass`` product class. Default False
            zeros out the bucket (mid-run melt is NOT a product).

    Returns:
        The 5-bucket classification dict from ``classify_products``.
    """
    session = _build_session(
        feedstock_id=feedstock_id,
        campaign=campaign,
        data_dir=data_dir or DEFAULT_DATA_DIR,
        backend_name=backend_name,
    )
    sim = session.simulator
    ticks = 0
    while ticks < hours and not sim.is_complete():
        sim.step()
        ticks += 1
    return classify_products(sim, early_tap_mode=early_tap_mode)


def _classification_to_json(
    classification: Mapping[str, Any],
    *,
    feedstock_id: str | None = None,
    campaign: str | None = None,
) -> str:
    """Serialize the classification dict as pretty-printed JSON
    with optional metadata header."""
    payload = {
        "feedstock_id": feedstock_id,
        "campaign": campaign,
        "classification": dict(classification),
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _emit_report(
    classification: Mapping[str, Any],
    *,
    feedstock_id: str,
    campaign: str,
    output_format: str,
    output_path: Path | None,
) -> str:
    """Format + write the report. Returns the formatted string so
    the caller can print or test it."""
    if output_format == 'markdown':
        body = format_three_product_markdown(
            classification,
            feedstock_id=feedstock_id,
            campaign=campaign,
            title=(
                f"Three-Product-Class Report — "
                f"{feedstock_id} / {campaign}"
            ),
        )
    elif output_format == 'json':
        body = _classification_to_json(
            classification,
            feedstock_id=feedstock_id,
            campaign=campaign,
        )
    else:
        raise ValueError(
            f"unsupported --format {output_format!r}; choose from "
            f"{SUPPORTED_FORMATS}"
        )
    if output_path is not None:
        output_path.write_text(body)
    return body


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulator.three_product_runner",
        description=(
            "Generate a four-product-class campaign report per "
            "CLAUDE.md § 5: metals + O2, pure silica glass, "
            "industrial mixed glass, refractory ceramic rump."
        ),
    )
    parser.add_argument("--feedstock", required=True,
                        help="feedstock catalog key (e.g. lunar_mare_low_ti)")
    parser.add_argument("--campaign", default="C2A",
                        help="campaign label (default: C2A)")
    parser.add_argument("--hours", type=int, default=24,
                        help="max simulated hours to advance (default: 24)")
    parser.add_argument("--output", type=Path, default=None,
                        help="output file path; stdout if omitted")
    parser.add_argument("--format", choices=SUPPORTED_FORMATS,
                        default="markdown",
                        help="output format (default: markdown)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help=(
                            "override the data/ directory "
                            "(default: project root data/)"
                        ))
    parser.add_argument("--backend", default="stub",
                        help="melt backend name (default: stub)")
    parser.add_argument("--early-tap", action="store_true",
                        help=(
                            "operator declares early-tap intent: "
                            "the residual cleaned_melt mass at end "
                            "of run surfaces as the industrial "
                            "mixed-glass product class. Default "
                            "OFF — mid-run melt is NOT a product."
                        ))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    classification = run(
        feedstock_id=args.feedstock,
        campaign=args.campaign,
        hours=args.hours,
        data_dir=args.data_dir,
        backend_name=args.backend,
        early_tap_mode=args.early_tap,
    )
    body = _emit_report(
        classification,
        feedstock_id=args.feedstock,
        campaign=args.campaign,
        output_format=args.format,
        output_path=args.output,
    )
    if args.output is None:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
