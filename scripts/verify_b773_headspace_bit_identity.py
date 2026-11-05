#!/usr/bin/env python3
"""Pin headspace product-ledger and partial outputs for parity.

The C2A window is the original replay proof. The lunar C0 24 h case is
the runner golden scenario whose public payload and product ledger stayed
on the pre-replay bytes; this script pins that same float.hex preimage.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.runner import PyrolysisRun


# Re-recorded on review/b773merge (fd0e31a79). Landings after ffec48bd3
# moved the digests. The carrier-notice collapse does not: the same
# command on the parent production files prints these hashes. A later
# digest change means a product or a solved partial moved.
C2A_PRODUCT_LEDGER_SHA256 = (
    "cbe6602244716053985cb6d4b3b07ebc87b3cd316dabe83eb29f37d5c92a1351"
)
C2A_HEADSPACE_PARTIALS_SHA256 = (
    "fc734f75199a55e297df3669d1e2d70ac7b173938704c7cb8a3bfbf63d31be55"
)
C2A_HEADSPACE_PARTIAL_HOURS = 21

# fd0e31a79 lunar_mare_low_ti_C0_24h, same runner inputs as the golden
# scenario, float.hex of product_ledger() and of each solved partial map.
C0_PRODUCT_LEDGER_SHA256 = (
    "33cc5c8d02ef415c85523a2bc616f9d095bb1659cb251937e61b44cbb7e9ad08"
)
C0_HEADSPACE_PARTIALS_SHA256 = (
    "a18950d478e22c561e2d1107c8bea71a100471b1705b0595cd43d49b1d3d3968"
)
C0_HEADSPACE_PARTIAL_HOURS = 19


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


def _capture(run: PyrolysisRun) -> tuple[dict[str, str], list[dict[str, str]]]:
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


def _run_c2a() -> tuple[dict[str, str], list[dict[str, str]]]:
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
    return _capture(run)


def _run_c0() -> tuple[dict[str, str], list[dict[str, str]]]:
    """The lunar C0 24 h runner scenario, under the golden engine config."""

    from simulator import engine_local_config
    from tests.binding_admission_fixtures import isolate_engine_local_config
    from tests.test_runner_smoke import SCENARIOS

    scenario = next(
        item for item in SCENARIOS if item["name"] == "lunar_mare_low_ti_C0_24h"
    )
    run = PyrolysisRun(
        feedstock_id=scenario["feedstock_id"],
        campaign=scenario["campaign"],
        hours=scenario["hours"],
        additives_kg=dict(scenario["additives_kg"]),
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )
    with tempfile.TemporaryDirectory() as directory:
        with isolate_engine_local_config(
            Path(directory),
            source_path=engine_local_config.config_path(),
        ):
            return _capture(run)


def _check_case(
    name: str,
    products: dict[str, str],
    partials: list[dict[str, str]],
    *,
    record: bool,
    expected_products: str,
    expected_partials: str,
    expected_hours: int,
) -> int:
    products_digest = _sha256(products)
    partials_digest = _sha256(partials)
    print(f"case={name}")
    if record:
        print(f"product_ledger_sha256={products_digest}")
        print(f"headspace_partials_sha256={partials_digest}")
        print(f"headspace_partial_hours={len(partials)}")
        return 0
    failed = False
    if products_digest != expected_products:
        print(
            f"{name} product ledger digest mismatch: "
            f"expected {expected_products}, got {products_digest}",
            file=sys.stderr,
        )
        failed = True
    if partials_digest != expected_partials:
        print(
            f"{name} headspace partials digest mismatch: "
            f"expected {expected_partials}, got {partials_digest}",
            file=sys.stderr,
        )
        failed = True
    if len(partials) != expected_hours:
        print(
            f"{name} headspace partial hour count mismatch: "
            f"expected {expected_hours}, got {len(partials)}",
            file=sys.stderr,
        )
        failed = True
    if failed:
        return 1
    print(f"BIT_IDENTICAL case={name} product_ledger_sha256={products_digest}")
    print(f"BIT_IDENTICAL case={name} headspace_partials_sha256={partials_digest}")
    print(f"case={name} headspace_partial_hours={len(partials)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--record",
        action="store_true",
        help="print the pre-refactor digests instead of checking the pins",
    )
    parser.add_argument(
        "--case",
        choices=("c2a", "c0", "all"),
        default="all",
    )
    args = parser.parse_args()
    status = 0
    if args.case in ("c2a", "all"):
        products, partials = _run_c2a()
        status |= _check_case(
            "c2a",
            products,
            partials,
            record=args.record,
            expected_products=C2A_PRODUCT_LEDGER_SHA256,
            expected_partials=C2A_HEADSPACE_PARTIALS_SHA256,
            expected_hours=C2A_HEADSPACE_PARTIAL_HOURS,
        )
    if args.case in ("c0", "all"):
        products, partials = _run_c0()
        status |= _check_case(
            "c0",
            products,
            partials,
            record=args.record,
            expected_products=C0_PRODUCT_LEDGER_SHA256,
            expected_partials=C0_HEADSPACE_PARTIALS_SHA256,
            expected_hours=C0_HEADSPACE_PARTIAL_HOURS,
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
