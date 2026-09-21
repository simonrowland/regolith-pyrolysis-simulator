"""Run one finding's defect restorations; restore bytes even when pytest fails.

Run sequentially: these proofs intentionally mutate the current checkout.
Exit zero requires a green control and assertion failures under every mutation.
"""
from __future__ import annotations

import ast
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
W = "simulator/battery/waypoints.py"
R = "scripts/bench_readiness.py"
TW = "tests/battery/test_waypoints.py::"
TR = "tests/battery/test_migrate_benches.py::"


def mutation(path, old, new, *tests):
    return path, old, new, tests


CASES = {
    "implicit": [mutation(R, "        if implicit:\n", "        if False and implicit:\n",
        TR + "test_legacy_embedded_bench_reaches_waypoints_without_explicit_link")],
    "identity": [mutation(R, "        identity=identity,", "        identity=BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),",
        TR + "test_implicit_bench_external_apparatus_locator_never_claims_own_work",
        TR + "test_legacy_embedded_bench_reaches_waypoints_without_explicit_link"),
        mutation("simulator/battery/migrate.py",
            '            if (raw.get("identity") or {}).get("basis") == BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE.value:',
            '            if False and (raw.get("identity") or {}).get("basis") == BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE.value:',
            TR + "test_extract_cannot_declare_inferred_identity")],
    "aggregation": [mutation(R, "    return ReadinessStatus.PARTIAL", "    return ReadinessStatus.GAP",
        TR + "test_mixed_source_retains_ready_experiment_and_ranks_unique_blockers"),
        mutation(R, '"count": len(experiment_ids)', '"count": 1',
            TR + "test_readiness_counts_unique_sources_not_experiments",
            TR + "test_duplicate_gap_is_counted_once_per_experiment")],
    "summary": [mutation(R, '"top_blocking_waypoints": top_blockers', '"top_blocking_waypoints": []',
        TR + "test_mixed_source_retains_ready_experiment_and_ranks_unique_blockers"),
        mutation(R, '"by_engine": by_engine', '"by_engine": {}',
            TR + "test_mixed_source_retains_ready_experiment_and_ranks_unique_blockers"),
        mutation(R, '-len(item[1]["sources"])', 'len(item[1]["sources"])',
            TR + "test_blockers_rank_source_count_before_experiment_count")],
    "clausing": [mutation(W, "    if effective:\n", "    if False and effective:\n",
        TW + "test_corrected_printed_escape_area_wins_while_raw_route_remains_visible",
        TW + "test_derived_groups_compute_and_preserve_nonpoint_inputs"),
        mutation(W, '("bench.geometry.orifice_area_m2",), (WaypointFlag.GEOMETRIC_ONLY,)',
            '("bench.geometry.orifice_area_m2",), ()',
            TW + "test_raw_printed_area_cannot_satisfy_effective_requirement")],
    "alpha": [mutation(W, "denominator = _multiply(surface, alpha)", "denominator = surface",
        TW + "test_g2_uses_explicit_evaporation_alpha"),
        mutation(W, "alpha_flags = (WaypointFlag.ASSUMPTION,)", "alpha_flags = ()",
            TW + "test_derived_groups_compute_and_preserve_nonpoint_inputs")],
    "wt": [mutation(W, "routes.setdefault(species, []).insert(\n                        0,",
        "routes.setdefault(species, []).append(",
        TW + "test_partial_printed_oxide_inventory_preserves_printed_mass_basis")],
    "method": [mutation(W, ') or bench_method == MethodToken.KNUDSEN_EFFUSION.value', ')',
        TW + "test_bench_knudsen_method_selects_cell_when_experiment_method_unknown")],
    "mass": [mutation(W,
        'None\n                    if species_mass is None\n                    else _divide(species_mass, Value.point_of(molar_mass))',
        '_divide(species_mass or Value.point_of(0), Value.point_of(molar_mass))',
        TW + "test_non_numeric_mass_does_not_fabricate_zero_charge")],
    "ramp": [mutation(W, "    if ramp_series is not None and hold_series is not None:\n",
        "    if False and ramp_series is not None and hold_series is not None:\n",
        TW + "test_thermal_ramp_and_hold_are_composed")],
    "interval_hold": [mutation(W, "    if schedule is not None and schedule.ramps and schedule.setpoints_and_holds and not any(route.route == \"printed_time_temperature_points\" for route in routes):\n",
        "    if False and schedule is not None and schedule.ramps and schedule.setpoints_and_holds and not any(route.route == \"printed_time_temperature_points\" for route in routes):\n",
        TW + "test_incomplete_or_unordered_ramp_hold_schedule_cannot_earn_readiness[False-False]")],
    "order": [mutation(W,
        "        if len(schedule.ramps) != 1 or len(schedule.setpoints_and_holds) != 1 or ramp_series[-1][1] != hold_series[0][1]:",
        "        if False and (len(schedule.ramps) != 1 or len(schedule.setpoints_and_holds) != 1 or ramp_series[-1][1] != hold_series[0][1]):",
        TW + "test_incomplete_or_unordered_ramp_hold_schedule_cannot_earn_readiness[True-False]")],
    "engines": [mutation(W, "        for engine in ENGINE_POINT_CONSUMERS\n", "        for engine in ENGINE_POINT_CONSUMERS[:-1]\n",
        TW + "test_multicomponent_engine_charge_is_not_structural_failure")],
    "geometric": [mutation(W, "    if WaypointFlag.GEOMETRIC_ONLY in result.selected.flags:",
        "    if False and WaypointFlag.GEOMETRIC_ONLY in result.selected.flags:",
        TW + "test_geometric_only_escape_does_not_satisfy_kems_readiness")],
    "floor": [mutation(W, "        if below:\n", "        if True:\n",
        TW + "test_rps_pressure_floor_rejects_only_wholly_excluded_values")],
    "knudsen": [mutation(W, '    if method == MethodToken.KNUDSEN_EFFUSION.value:\n',
        '    if method == MethodToken.KNUDSEN_EFFUSION.value:\n        kems_status = ReadinessStatus.NOT_APPLICABLE\n',
        TW + "test_atmospheric_knudsen_method_gets_notice_without_refusal"),
        mutation(W, '    threshold = as_decimal(FREE_MOLECULAR_KNUDSEN_MIN)', '    threshold = Decimal("0.01")',
            TW + "test_unknown_method_knudsen_predicate_and_directional_pressure_bounds"),
        mutation(W, '    low, high = _positive_range(kn) if kn is not None else (None, None)',
            '    high, low = _positive_range(kn) if kn is not None else (None, None)',
            TW + "test_unknown_method_knudsen_predicate_and_directional_pressure_bounds")],
    "notice": [mutation(W,
        "kems_notices = (KnudsenConsistencyNotice(kn, threshold, kn_inputs, species),)",
        "kems_notices = ()",
        TW + "test_atmospheric_knudsen_method_gets_notice_without_refusal",
        TW + "test_knudsen_notice_detects_failing_actual_ramp_segment",
        TR + "test_readiness_json_carries_knudsen_notice_and_inputs")],
    "golden": [mutation("tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json",
        '"mass_kg": 1000.0', '"mass_kg": 1001.0',
        "tests/test_runner_smoke.py::test_runner_golden_fixture_matches[ci_carbonaceous_chondrite_C2B_12h]")],
}
GROUPS = {
    "finding-1": ("implicit", "identity"), "finding-2": ("aggregation",),
    "finding-3": ("summary",), "finding-4": ("clausing",), "finding-5": ("alpha",),
    "finding-6": ("wt",), "finding-7": ("method",), "finding-8": ("mass",),
    "finding-9": ("ramp", "interval_hold", "order"), "finding-10": ("engines",),
    "finding-11": ("geometric",), "finding-12": ("floor", "knudsen", "notice"),
    "N1": ("identity",), "N2": ("clausing",), "N3": ("interval_hold",),
    "N4": ("order",), "N5": ("floor",), "N6": ("wt",), "N7": ("mass",),
    "N8": ("aggregation", "summary"), "knudsen": ("knudsen", "notice"),
    "notice": ("notice",),
    "finding-13": ("golden",),
}


def main():
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    finding = sys.argv[1]
    common = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip())
    common = (ROOT / common).resolve()
    python = os.environ.get("PYTHON") or next((str(path) for path in (
        common.parent / ".venv/bin/python", ROOT / ".venv/bin/python",
    ) if path.exists()), sys.executable)
    logs = Path(tempfile.mkdtemp(prefix=f"bench-c2-{finding}-"))
    for group in GROUPS[finding]:
        for index, (relative, old, new, tests) in enumerate(CASES[group]):
            path = ROOT / relative
            original = path.read_bytes()
            text = original.decode()
            if old not in text:
                raise RuntimeError(f"mutation target absent: {group}: {old!r}")
            mutated = text.replace(old, new, 1)
            json.loads(mutated) if path.suffix == ".json" else ast.parse(mutated)
            junit = logs / f"{group}-{index}.xml"
            command = [python, "-m", "pytest", *tests, "-q", f"--junitxml={junit}"]
            environment = {**os.environ, "PYTHONPYCACHEPREFIX": str(logs / "pycache")}
            control = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True)
            (logs / f"{group}-{index}-control.log").write_bytes(control.stdout + control.stderr)
            if control.returncode:
                raise RuntimeError(f"control is not green: {group}; {logs}")
            try:
                path.write_text(mutated)
                result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True)
                (logs / f"{group}-{index}-mutant.log").write_bytes(result.stdout + result.stderr)
                report = ET.parse(junit).getroot()
                failures = report.findall(".//failure")
                errors = report.findall(".//error")
                assertion_failures = all(
                    (failure.get("message") or "").startswith(("assert ", "AssertionError", "DID NOT RAISE", "Failed: DID NOT RAISE"))
                    or (failure.text or "").rstrip().endswith("AssertionError")
                    for failure in failures
                )
                if result.returncode != 1 or not failures or errors or not assertion_failures:
                    raise RuntimeError(f"mutation did not yield assertion failures: {group}; {logs}")
                print(f"!RESULT: bench-c2-fix2c — {finding}/{group}-{index}: {len(failures)} failures; {logs}", flush=True)
            finally:
                path.write_bytes(original)
            assert path.read_bytes() == original


if __name__ == "__main__":
    main()
