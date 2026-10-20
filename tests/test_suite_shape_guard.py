"""Suite-shape guard: the CI-duration contract (owner directive 2026-07-22).

The full suite must never drift back to multi-hour wall-clock. Three
structural invariants enforce that:

1. Every test with a timeout ceiling above the 300 s global default MUST
   carry exactly one recognized serialized-chain ``xdist_group`` mark —
   ``magemin_fullrun_<x>`` for heavy native tests, or ``serial`` for the
   socket/pool-contended web pair — so heavy tests run inside a
   duration-hinted serialized chain while the rest of the suite
   parallelizes around them (``--dist loadgroup``).
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

import json
import os
import re
import subprocess
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest


GLOBAL_TIMEOUT_CEILING_S = 300.0

# "serial" is the web/socket serialized chain — duration-hinted and scheduled
# by --dist loadgroup exactly like the magemin chains, so it satisfies the
# invariant's purpose (heavy tests live inside a hinted serialized chain).
# The web pause/resume pair cannot join a magemin chain: they serialize on
# socket/pool contention, not on the native-engine binary.
HEAVY_GROUP_PATTERN = re.compile(r"^(magemin_fullrun_[a-z]|serial)$")
DURATION_HINTS_PATH = Path(__file__).with_name("xdist_loadgroup_durations.json")
FULL_COLLECTION_REPORT_ENV = "REGOLITH_SUITE_SHAPE_COLLECTION_REPORT"

# Shrink-only debt roster: (file, test-function name), parameters stripped.
HEAVY_ROSTER = frozenset({
    # Owner-ratified b-894 follow-up (2026-10-15): the full C0 -> C6
    # trajectory and 5e-12 % mass closure require the complete campaign.
    # Measured 1283 s; nightly-only, in the file's existing serial chain.
    ("tests/test_sso_r_r20_state.py", "test_default_lunar_redox_runaway_acceptance"),
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
     "test_cumulative_transition_mass_closure_bounded_at_transitional_refusal"),
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
    # 2026-08-28: the happy-path journey drives a REAL browser through a REAL
    # 35-hour run against the live app, so its cost is the product's, not the
    # test's -- it cannot be shrunk without stopping testing the thing.
    # MEASURED: 508 s and 553 s on two consecutive runs. Its cap is derived
    # from the declared step budgets (tests/e2e/journey_budget.py), so it moves
    # only when a step budget moves, and it must exceed them: at the old 300 s
    # the journey was killed before it could report, and steps 5-7 had never
    # once rendered a verdict.
    ("tests/e2e/test_happy_path_journey.py", "test_happy_path_journey"),
    # 2026-08-28: pause/resume/cancel/restart browser journey. MEASURED 159 s
    # then 194 s on two consecutive live runs (90 s of each is the pause-hold
    # window). Cap is derived from declared step budgets in
    # tests/e2e/journey_budget.py (665 s + 120 s margin = 785 s): two
    # start-ack windows plus three 90 s advance/hold windows exceed the
    # 300 s default whenever the start-wedge (~41 s yaml parse) hits twice,
    # and a 300 s cap would kill the test before step 8 can report.
    ("tests/e2e/test_run_control_journey.py",
     "test_pause_resume_cancel_restart_journey"),
    # 2026-08-29: alternate-branch browser journey (BRANCH_ONE_TWO = one).
    # MEASURED 190.49 s on the committed A_staged+one path (Complete at hour
    # 34). Cap is derived from declared step budgets in
    # tests/e2e/journey_budget.py (845 s + 120 s margin = 965 s) so a slower
    # host, or the longer Path B chain (scouted at 638 s, still inside C3_NA
    # at hour 81), can still report. A 300 s cap would kill those runs
    # before the ledger step can report — same class as the happy-path
    # journey.
    ("tests/e2e/test_alternate_branch_journey.py",
     "test_alternate_branch_journey"),
    # gate-2 amendments (2026-07-23): C6-continue lengthened these past the
    # default ceiling; each carries its measured justification at the mark.
    ("tests/test_make_recipe_db_profile.py",
     "test_target_menu_generated_profiles_internal_analytical_eval"
     "_no_campaign_vocabulary_abort"),
    ("tests/test_cross_surface_parity.py",
     "test_batch_cli_web_mol_ledger_parity"),
    ("tests/chemistry/test_builtin_evaporation_flux_provider.py",
     "test_evaporation_caller_wiring_matches_shared_helper_across_short_run"),
    ("tests/test_staged_bakeout.py",
     "test_c2a_staged_k_shuttle_and_conservation_remain_visible"),
    # gate-baseline-9ed8ceb amendments (2026-07-28): the contention-robust
    # ceiling recalibration (quiet-box-timeout class) raised these three past
    # the default ceiling; each carries its measured justification at the mark
    # (372 s / 268 s serial on Studio 1 for the web pair; ~340 s for the C5
    # FeO track). Long-term fate per t-414 remains demotion or a cost cut.
    ("tests/test_web_events_decision_pause.py",
     "test_pause_resume_around_every_gate_is_ledger_identical"),
    ("tests/test_web_functional_qa.py",
     "test_alternate_path_b_completes_with_gate_pause_resume"),
    ("tests/test_yield_root_cause.py",
     "test_c5_targeted_feo_full_track_reduces_target_after_low_temperature"
     "_hours"),
})

# b-586: these campaign-length yield nodes must stay out of the PR tier. They
# are selected from the heavy roster so the guard cannot drift from its
# existing node inventory.
NIGHTLY_YIELD_HEAVY_ROSTER = frozenset(
    entry for entry in HEAVY_ROSTER
    if entry[0] == "tests/test_yield_root_cause.py"
)


def _nightly_yield_violations() -> list[str]:
    yield_module = import_module("tests.test_yield_root_cause")
    violations = []
    for relpath, name in sorted(NIGHTLY_YIELD_HEAVY_ROSTER):
        test_function = getattr(yield_module, name, None)
        marks = getattr(test_function, "pytestmark", ())
        if not any(mark.name == "nightly" for mark in marks):
            violations.append(
                f"{relpath}::{name}: yield full-track node must be marked "
                "nightly to stay out of the PR tier"
            )
    return violations


def _item_key(item) -> tuple[str, str]:
    return _node_key(item.nodeid)


def _node_key(nodeid: str) -> tuple[str, str]:
    # The roster names test functions, so strip parameter IDs and xdist's
    # --dist loadgroup suffix from collected item IDs.
    rel = nodeid.split("::", 1)[0]
    name = nodeid.rsplit("::", 1)[-1].split("[", 1)[0].split("@", 1)[0]
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


def pytest_collection_finish(session) -> None:
    """Write full-collection metadata for the unfiltered guard subprocess."""
    report_path = os.environ.get(FULL_COLLECTION_REPORT_ENV)
    if report_path is None:
        return
    collected = [
        {"nodeid": item.nodeid, "timeout_s": _timeout_s(item)}
        for item in session.items
    ]
    Path(report_path).write_text(
        json.dumps(collected), encoding="utf-8"
    )


def _collect_full_suite_items() -> list[dict[str, object]]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    # CI may pass a marker expression through PYTEST_ADDOPTS or pyproject.
    env.pop("PYTEST_ADDOPTS", None)
    with tempfile.TemporaryDirectory(prefix="suite-shape-collection-") as temp_dir:
        report_path = Path(temp_dir) / "collected-items.json"
        env[FULL_COLLECTION_REPORT_ENV] = str(report_path)
        pythonpath = [str(Path(__file__).resolve().parent), str(repo_root)]
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-o",
                "addopts=",
                "-p",
                "test_suite_shape_guard",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not report_path.is_file():
            detail = (result.stdout + result.stderr).strip()[-4000:]
            raise AssertionError(
                "full pytest collect-only listing failed without mark filters "
                f"(exit {result.returncode})\n{detail}"
            )
        return json.loads(report_path.read_text(encoding="utf-8"))


def _full_collection_roster_violations(
    collected_items: list[dict[str, object]],
    roster: frozenset[tuple[str, str]],
) -> list[str]:
    collected_keys = {
        _node_key(str(item["nodeid"])) for item in collected_items
    }
    violations = [
        f"roster entry {entry} matches no collected test — "
        "phantom row (typo or removed test); fix the name or delete the entry"
        for entry in sorted(roster - collected_keys)
    ]
    for item in collected_items:
        timeout = item["timeout_s"]
        if timeout is None or float(timeout) <= GLOBAL_TIMEOUT_CEILING_S:
            continue
        nodeid = str(item["nodeid"])
        if _node_key(nodeid) not in roster:
            violations.append(
                f"{nodeid}: timeout {float(timeout):g}s but not in the "
                "shrink-only heavy roster — reduce its cost or demote a "
                "roster entry (see module docstring)"
            )
    return violations


def test_suite_shape_roster_drift_is_checked_under_mark_expression(
    monkeypatch,
) -> None:
    fake_roster = frozenset({("tests/test_fake.py", "test_phantom")})
    full_collection = [
        {
            "nodeid": "tests/test_fake.py::test_unrostered_heavy",
            "timeout_s": GLOBAL_TIMEOUT_CEILING_S + 1,
        }
    ]
    monkeypatch.setitem(globals(), "HEAVY_ROSTER", fake_roster)
    monkeypatch.setitem(
        globals(), "_collect_full_suite_items", lambda: full_collection
    )
    monkeypatch.setitem(globals(), "_nightly_yield_violations", lambda: [])
    request = SimpleNamespace(
        session=SimpleNamespace(items=[]),
        config=SimpleNamespace(
            option=SimpleNamespace(markexpr="not nightly")
        ),
    )

    with pytest.raises(AssertionError) as exc_info:
        test_suite_shape_heavy_tests_are_grouped_and_rostered(request)

    message = str(exc_info.value)
    assert "test_phantom" in message
    assert "test_unrostered_heavy" in message


def _run_duration_hint_guard(
    monkeypatch,
    tmp_path: Path,
    full_collection: list[dict[str, object]],
    duration_hints: dict[str, float],
) -> None:
    monkeypatch.setitem(globals(), "HEAVY_ROSTER", frozenset())
    monkeypatch.setitem(
        globals(), "_collect_full_suite_items", lambda: full_collection
    )
    monkeypatch.setitem(globals(), "_nightly_yield_violations", lambda: [])
    hint_path = tmp_path / "duration-hints.json"
    hint_path.write_text(
        json.dumps({"durations_seconds": duration_hints}), encoding="utf-8"
    )
    monkeypatch.setitem(globals(), "DURATION_HINTS_PATH", hint_path)
    selected_items = [
        SimpleNamespace(
            nodeid=f"tests/test_selected.py::test_{index}",
            get_closest_marker=lambda _name: None,
            iter_markers=lambda _name: (),
        )
        for index in range(1001)
    ]
    request = SimpleNamespace(
        session=SimpleNamespace(items=selected_items),
        config=SimpleNamespace(
            option=SimpleNamespace(markexpr="not nightly")
        ),
    )
    test_suite_shape_heavy_tests_are_grouped_and_rostered(request)


def test_duration_hint_group_deselected_by_not_nightly_is_not_stale(
    monkeypatch, tmp_path: Path
) -> None:
    _run_duration_hint_guard(
        monkeypatch,
        tmp_path,
        [
            {
                "nodeid": "tests/test_nightly.py::test_heavy",
                "marks": ["nightly"],
                "timeout_s": None,
                "xdist_groups": ["magemin_fullrun_a"],
            }
        ],
        {"magemin_fullrun_a": 12.0},
    )


def test_duration_hint_with_no_collected_group_is_still_stale(
    monkeypatch, tmp_path: Path
) -> None:
    with pytest.raises(AssertionError, match="stale=\\['magemin_fullrun_z'\\]"):
        _run_duration_hint_guard(
            monkeypatch,
            tmp_path,
            [
                {
                    "nodeid": "tests/test_selected.py::test_fast",
                    "timeout_s": None,
                    "xdist_groups": [],
                }
            ],
            {"magemin_fullrun_z": 12.0},
        )


# Keep this guard unmarked so the PR tier's ``-m "not nightly"`` runs it.
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
                f"recognized serialized-chain group "
                f"(magemin_fullrun_<x> or serial; got {groups})"
            )
        if _item_key(item) not in HEAVY_ROSTER:
            violations.append(
                f"{item.nodeid}: timeout {ceiling:g}s but not in the "
                "shrink-only heavy roster — reduce its cost or demote a "
                "roster entry (see module docstring)"
            )
    # ``request.session.items`` contains only items surviving pytest's
    # selection filters. Inspect the yield functions directly so this tiering
    # invariant still sees nodes deselected by ``-m`` or ``-k``.
    violations.extend(_nightly_yield_violations())
    # Roster membership must see tests excluded by ``-m``, ``-k`` or a
    # targeted invocation, so compare against a fresh full collection.
    violations.extend(
        _full_collection_roster_violations(
            _collect_full_suite_items(), HEAVY_ROSTER
        )
    )
    if len(request.session.items) > 1000:
        collected_groups = {
            group
            for item in request.session.items
            for group in _group_names(item)
        }
        hint_groups = set(
            json.loads(DURATION_HINTS_PATH.read_text(encoding="utf-8"))[
                "durations_seconds"
            ]
        )
        missing_hints = sorted(collected_groups - hint_groups)
        stale_hints = sorted(hint_groups - collected_groups)
        if missing_hints or stale_hints:
            violations.append(
                "xdist duration-hint keys must match collected groups: "
                f"missing={missing_hints}, stale={stale_hints}"
            )
    assert not violations, "\n".join(violations)
