"""Suite-shape guard: the CI-duration contract (owner directive 2026-07-22).

The full suite must never drift back to multi-hour wall-clock. Three
structural invariants enforce that:

1. Every test with a timeout ceiling above the 300 s global default MUST
   carry exactly one ``xdist_group("magemin_fullrun_<x>")`` mark — heavy
   native tests run inside one of the balanced serialized chains while the
   rest of the suite parallelizes around them (``--dist loadgroup``).
2. The heavy roster is SHRINK-ONLY. Membership below is a debt list, not a
   category: adding an entry requires demoting another or an owner-ratified
   justification comment, and every entry's long-term fate is demotion to
   the nightly/gate-window lane or a call-volume cut (t-414).
3. No test anywhere carries more than one distinct ``xdist_group`` mark:
   pytest-xdist 3.8 UNIONS group marks into a new fused scope, which
   silently detaches the test from its intended chain (the 2026-07-21
   review P1).

DO NOT WEAKEN OR DELETE THIS TEST to make a slow test pass — fix the test's
cost (warm pool engagement, call volume) or demote it explicitly. This
guard exists because the suite ran red-by-default for 12+ days behind a
wall of unattributed multi-hour timeouts (research/2026-07-21-ci-catch-mining).
"""

from __future__ import annotations

import re

import pytest


GLOBAL_TIMEOUT_CEILING_S = 300.0

HEAVY_GROUP_PATTERN = re.compile(r"^magemin_fullrun_[a-z]$")

# Shrink-only debt roster: (file, test-function name), parameters stripped.
HEAVY_ROSTER = frozenset({
    ("tests/chemistry/test_builtin_condensation_route_provider.py",
     "test_full_run_mass_balance_holds_with_kernel_committed_condensation"),
    ("tests/chemistry/test_builtin_condensation_route_provider.py",
     "test_split_path_end_state_matches_pre_flip_account_balances"),
    ("tests/chemistry/test_builtin_electrolysis_step_provider.py",
     "test_full_run_mass_balance_holds_with_kernel_committed_electrolysis"),
    ("tests/chemistry/test_builtin_electrolysis_step_provider.py",
     "test_full_run_o2_yields_split_across_distinct_bins"),
    ("tests/chemistry/test_builtin_evaporation_transition_provider.py",
     "test_full_run_mass_balance_holds_with_kernel_committed_transitions"),
    ("tests/chemistry/test_builtin_metallothermic_step_provider.py",
     "test_c6_static_hold_exercises_c6_proceed_decision_path"),
    ("tests/chemistry/test_builtin_metallothermic_step_provider.py",
     "test_c6_ci_empty_window_records_binding_refusal_without_transitions"),
    ("tests/chemistry/test_builtin_metallothermic_step_provider.py",
     "test_full_run_mass_balance_holds_with_kernel_committed_metallothermic"),
    ("tests/test_mass_balance.py",
     "test_cumulative_transition_mass_closure_bounded"),
    ("tests/test_run_executor.py",
     "test_run_executor_partial_path_sets_status_and_decisions"),
    ("tests/test_runner_smoke.py",
     "test_runner_records_operator_decision_in_shadow_trace"),
    ("tests/test_sio_tsweep_smoke.py", "test_sio_tsweep_cli_smoke_2x2x2_grid"),
    ("tests/test_sio_tsweep_smoke.py",
     "test_sio_tsweep_single_cell_deterministic"),
    ("tests/test_sio_tsweep_smoke.py", "test_sio_wall_sweep_cli_smoke"),
    ("tests/test_yield_root_cause.py",
     "test_pyrolysis_track_c5_reduces_feo_without_additives"),
    ("tests/test_yield_root_cause.py",
     "test_pc_extract_fe_target_has_fe_product_after_full_pyrolysis_track"),
    ("tests/test_yield_root_cause.py",
     "test_pc_extract_al_remains_infeasible_at_1p6v_c5_cap"),
})


def _item_key(item) -> tuple[str, str]:
    path = str(getattr(item, "fspath", "") or "")
    marker = path.find("tests/")
    rel = path[marker:] if marker >= 0 else path
    name = item.name.split("[", 1)[0]
    return (rel, name)


def _timeout_s(item) -> float | None:
    mark = item.get_closest_marker("timeout")
    if mark is None or not mark.args:
        return None
    try:
        return float(mark.args[0])
    except (TypeError, ValueError):
        return None


def _group_names(item) -> list[str]:
    names = []
    for mark in item.iter_markers("xdist_group"):
        if mark.args:
            names.append(str(mark.args[0]))
        elif "name" in mark.kwargs:
            names.append(str(mark.kwargs["name"]))
    return sorted(set(names))


def test_suite_shape_heavy_tests_are_grouped_and_rostered(request) -> None:
    violations: list[str] = []
    for item in request.session.items:
        groups = _group_names(item)
        if len(groups) > 1:
            violations.append(
                f"{item.nodeid}: multiple xdist_group marks {groups} — "
                "xdist UNIONS them into a fused scope (keep exactly one)"
            )
        ceiling = _timeout_s(item)
        if ceiling is None or ceiling <= GLOBAL_TIMEOUT_CEILING_S:
            continue
        heavy_groups = [g for g in groups if HEAVY_GROUP_PATTERN.match(g)]
        if len(heavy_groups) != 1:
            violations.append(
                f"{item.nodeid}: timeout {ceiling:g}s > "
                f"{GLOBAL_TIMEOUT_CEILING_S:g}s without exactly one "
                f"magemin_fullrun_<x> group (got {groups})"
            )
        if _item_key(item) not in HEAVY_ROSTER:
            violations.append(
                f"{item.nodeid}: timeout {ceiling:g}s but not in the "
                "shrink-only heavy roster — reduce its cost or demote a "
                "roster entry (see module docstring)"
            )
    assert not violations, "\n".join(violations)
