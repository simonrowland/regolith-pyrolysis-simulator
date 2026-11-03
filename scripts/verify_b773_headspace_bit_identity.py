#!/usr/bin/env python3
"""Pin the original C2A ledger and headspace-partial outputs for parity."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.runner import PyrolysisRun


EXPECTED_PRODUCT_LEDGER_SHA256 = (
    "00d08f19ac23e64096b12546d7fa4aacc00ce11cf3c337aebf432bf8ef55848a"
)
EXPECTED_HEADSPACE_PARTIALS_SHA256 = (
    "db243325831384731bb4de5324c66d8484f895c36609e1c61178244a63a11350"
)
EXPECTED_HEADSPACE_PARTIAL_HOURS = 21


def _load_optimizer_test_module():
    path = ROOT / "tests" / "test_optimizer_evalspec.py"
    spec = importlib.util.spec_from_file_location("_b773_optimizer_evalspec", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scenario helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _run_scenario() -> tuple[dict[str, str], list[dict[str, str]]]:
    scenario = _load_optimizer_test_module()
    _, run_config = scenario._build_eval_inputs(
        scenario.RecipePatch({}),
        "lunar_mare_low_ti",
        "internal-analytical",
        scenario._c2a_window_profile(1400.0, 1450.0, 18),
        scenario.RecipeSchema(),
    )
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign=run_config.campaign,
        hours=run_config.hours,
        mass_kg=run_config.mass_kg,
        backend_name=run_config.backend_name,
        runtime_campaign_overrides=run_config.runtime_campaign_overrides,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    sim = session.simulator
    captured_partials: list[dict[str, str]] = []
    original_solve = sim._calculate_evaporation_with_headspace_feedback

    def capture_solve(*args, **kwargs):
        result = original_solve(*args, **kwargs)
        partials = getattr(sim, "_solved_headspace_partials_Pa_this_tick", None)
        if isinstance(partials, Mapping):
            captured_partials.append({
                str(species): float(value).hex()
                for species, value in sorted(partials.items())
            })
        return result

    sim._calculate_evaporation_with_headspace_feedback = capture_solve
    run._run_session(session)
    products = {
        str(species): float(value).hex()
        for species, value in sorted(sim.product_ledger().items())
    }
    return products, captured_partials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--record",
        action="store_true",
        help="print the pre-refactor digests instead of checking the pins",
    )
    args = parser.parse_args()
    products, partials = _run_scenario()
    products_digest = _sha256(products)
    partials_digest = _sha256(partials)
    if args.record:
        print(f"product_ledger_sha256={products_digest}")
        print(f"headspace_partials_sha256={partials_digest}")
        print(f"headspace_partial_hours={len(partials)}")
        return 0
    if products_digest != EXPECTED_PRODUCT_LEDGER_SHA256:
        print(
            "product ledger digest mismatch: "
            f"expected {EXPECTED_PRODUCT_LEDGER_SHA256}, got {products_digest}",
            file=sys.stderr,
        )
        return 1
    if partials_digest != EXPECTED_HEADSPACE_PARTIALS_SHA256:
        print(
            "headspace partials digest mismatch: "
            f"expected {EXPECTED_HEADSPACE_PARTIALS_SHA256}, got {partials_digest}",
            file=sys.stderr,
        )
        return 1
    if len(partials) != EXPECTED_HEADSPACE_PARTIAL_HOURS:
        print(
            "headspace partial hour count mismatch: "
            f"expected {EXPECTED_HEADSPACE_PARTIAL_HOURS}, got {len(partials)}",
            file=sys.stderr,
        )
        return 1
    print(f"BIT_IDENTICAL product_ledger_sha256={products_digest}")
    print(f"BIT_IDENTICAL headspace_partials_sha256={partials_digest}")
    print(f"headspace_partial_hours={len(partials)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
