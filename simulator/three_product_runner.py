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

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.yaml_cache import load_cached_safe_yaml
from simulator.backends import BackendSelectionPolicy
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, _positive_mass_kg
from simulator.session import SimSession, SimSessionConfig
from simulator.three_product_report import classify_products
from simulator.three_product_report_markdown import (
    format_three_product_markdown,
)


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


SUPPORTED_FORMATS = ('markdown', 'json')


def _load_yaml(data_dir: Path, name: str) -> dict:
    with (data_dir / name).open() as f:
        return load_cached_safe_yaml(f.read()) or {}


def _build_session(
    *,
    feedstock_id: str,
    campaign: str,
    data_dir: Path,
    mass_kg: float,
    backend_name: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
) -> SimSession:
    """Build a SimSession with the canonical project setpoints +
    vapor pressures + feedstock catalog."""
    config = SimSessionConfig(
        feedstock_id=feedstock_id,
        feedstocks=_load_yaml(data_dir, "feedstocks.yaml"),
        setpoints=_load_yaml(data_dir, "setpoints.yaml"),
        vapor_pressures=_load_yaml(data_dir, "vapor_pressures.yaml"),
        materials=_load_yaml(data_dir, "materials.yaml"),
        campaign=campaign,
        backend_name=backend_name,
        backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
        mass_kg=mass_kg,
    )
    return SimSession().start(config)


def _run_with_provenance(
    *,
    feedstock_id: str,
    campaign: str,
    hours: int,
    mass_kg: float,
    data_dir: Path | None = None,
    backend_name: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    early_tap_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the classifier and project the canonical runner provenance."""
    mass_kg = _positive_mass_kg(mass_kg)
    session = _build_session(
        feedstock_id=feedstock_id,
        campaign=campaign,
        data_dir=data_dir or DEFAULT_DATA_DIR,
        mass_kg=mass_kg,
        backend_name=backend_name,
    )
    execution = RunExecutor().execute_session(session, hours=int(hours))

    # Keep the three-product runner's data-dir/session path while reusing the
    # canonical runner's output builder for all provenance and run notices.
    canonical_run = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=campaign,
        hours=hours,
        mass_kg=mass_kg,
        backend_name=backend_name,
    )
    canonical_document = canonical_run._build_output(execution)
    session._set_result_document(canonical_document)
    classification = classify_products(
        execution.simulator,
        early_tap_mode=early_tap_mode,
    )
    provenance = {
        "run_metadata": canonical_document["run_metadata"],
        "vapor_pressure_source_report": canonical_document[
            "vapor_pressure_source_report"
        ],
        "degraded_path_engagement": canonical_document[
            "degraded_path_engagement"
        ],
    }
    return classification, provenance


def run(
    *,
    feedstock_id: str,
    campaign: str,
    hours: int,
    mass_kg: float = 1000.0,
    data_dir: Path | None = None,
    backend_name: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
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
        mass_kg: Positive feedstock charge mass in kg; defaults to the
            canonical runner's 1000 kg batch.
        data_dir: Optional override for the data directory; defaults
            to the project's ``data/`` next to ``simulator/``.
        backend_name: Backend to select; ``"internal-analytical"`` is the
            canonical name for runs without AlphaMELTS/MAGEMin installed.
        early_tap_mode: Pass-through to ``classify_products``; when
            True the residual ``cleaned_melt`` mass surfaces as the
            ``industrial_mixed_glass`` product class. Default False
            zeros out the bucket (mid-run melt is NOT a product).

    Returns:
        The 5-bucket classification dict from ``classify_products``.
    """
    classification, _ = _run_with_provenance(
        feedstock_id=feedstock_id,
        campaign=campaign,
        hours=hours,
        mass_kg=mass_kg,
        data_dir=data_dir,
        backend_name=backend_name,
        early_tap_mode=early_tap_mode,
    )
    return classification


def _classification_to_json(
    classification: Mapping[str, Any],
    *,
    feedstock_id: str | None = None,
    campaign: str | None = None,
    mass_kg: float,
    provenance: Mapping[str, Any],
) -> str:
    """Serialize the classification dict as pretty-printed JSON
    with optional metadata header."""
    payload = {
        "feedstock_id": feedstock_id,
        "campaign": campaign,
        "mass_kg": mass_kg,
        "classification": dict(classification),
        **dict(provenance),
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _emit_report(
    classification: Mapping[str, Any],
    *,
    feedstock_id: str,
    campaign: str,
    mass_kg: float,
    provenance: Mapping[str, Any],
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
        metadata = provenance["run_metadata"]
        engines_used = metadata.get("engines_used", {})
        vapor_engine = engines_used.get("registry", {}).get(
            "vapor_pressure", {}
        )
        shadows = vapor_engine.get("shadows", []) or []
        extrapolation = provenance["degraded_path_engagement"].get(
            "vapour_pressure_extrapolation", {}
        )
        body += (
            "\n**Run provenance**: "
            f"mass_kg={mass_kg:g}; "
            f"backend={metadata.get('backend', 'unknown')}; "
            f"backend_status={metadata.get('backend_status', 'unknown')}; "
            f"backend_authoritative={str(metadata.get('backend_authoritative', False)).lower()}; "
            f"vapor_pressure_active={vapor_engine.get('authoritative', 'none')}; "
            f"vapor_pressure_shadows={','.join(map(str, shadows)) or 'none'}; "
            "vapor_pressure_extrapolated_summaries="
            f"{int(extrapolation.get('total_count', 0) or 0)}\n"
        )
    elif output_format == 'json':
        body = _classification_to_json(
            classification,
            feedstock_id=feedstock_id,
            campaign=campaign,
            mass_kg=mass_kg,
            provenance=provenance,
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
    parser.add_argument("--mass-kg", type=float, default=1000.0,
                        help="batch feedstock mass in kg (default: 1000)")
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
    parser.add_argument(
        "--backend",
        default=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        help="melt backend name (default: internal-analytical)",
    )
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
    classification, provenance = _run_with_provenance(
        feedstock_id=args.feedstock,
        campaign=args.campaign,
        hours=args.hours,
        mass_kg=args.mass_kg,
        data_dir=args.data_dir,
        backend_name=args.backend,
        early_tap_mode=args.early_tap,
    )
    body = _emit_report(
        classification,
        feedstock_id=args.feedstock,
        campaign=args.campaign,
        mass_kg=args.mass_kg,
        provenance=provenance,
        output_format=args.format,
        output_path=args.output,
    )
    if args.output is None:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
