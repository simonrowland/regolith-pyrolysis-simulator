"""Smoke tests for the JSON runner harness (Goal #18).

These tests guard four contracts:

* **Schema shape.**  Every fixture must have the exact top-level keys
  and per-section sub-keys spec'd in ``docs/runner-output-schema.md``.
  This is asserted independent of the golden bytes -- a future shape
  drift is louder than a content drift.
* **Golden parity.**  Three representative scenarios produce JSON that
  matches the committed fixtures byte-for-byte (modulo wall-clock
  fields that are pinned via the metadata-override hooks).
* **Determinism.**  Running the same scenario twice in the same process
  yields identical JSON.
* **Mass-balance bound.**  The mass-balance error in every golden
  fixture stays under ``5e-12 %`` -- the existing simulator invariant.

The CLI scenarios are produced via :func:`simulator.runner.PyrolysisRun.run`
directly rather than ``subprocess`` so a failing test can drop into pdb
without spinning up a child process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator.runner import PyrolysisRun, RUNNER_SCHEMA_VERSION


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "runner"


# Mass-balance tolerance for every snapshot in the golden fixtures.
# Mirrors the existing simulator-wide tolerance enforced by
# ``tests/test_mass_balance.py``; surfacing the same number here means a
# runner change that opens a balance gap fails fast against the goldens.
MASS_BALANCE_MAX_PCT = 5e-12


# Schema-shape: the top-level keys every runner output must expose.
TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "run_metadata",
    "final_state",
    "per_hour_summary",
    "shadow_trace",
    "status",
    "error_message",
})

# Schema-shape: keys every ``run_metadata`` block must expose.
RUN_METADATA_KEYS = frozenset({
    "schema_version",
    "feedstock_id",
    "campaign",
    "hours_requested",
    "hours_completed",
    "mass_kg",
    "additives_kg",
    "track",
    "backend",
    "started_at_utc",
    "engines_used",
    "kernel_commit_sha",
})

# Schema-shape: keys every per_hour_summary entry must expose.
PER_HOUR_KEYS = frozenset({
    "hour",
    "campaign",
    "T_C",
    "P_total_bar",
    "pO2_bar",
    "mass_balance_pct",
    "O2_yield_kg_cumulative",
    "metal_yields_kg",
    "condensation_train_kg",
})


# Three representative scenarios.  Each one mirrors the Goal #18
# CHECKLIST exactly so changes to fixture filenames or run arguments
# stay traceable to that doc.
SCENARIOS = [
    {
        "name": "lunar_mare_low_ti_C0_24h",
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C0",
        "hours": 24,
        "additives_kg": {},
        "fixture": "lunar_mare_low_ti_C0_24h.json",
    },
    {
        "name": "mars_basalt_C2A_12h",
        "feedstock_id": "mars_basalt",
        "campaign": "C2A",
        "hours": 12,
        # mars_basalt requires Stage 0 carbon reductant; without it
        # load_batch raises an AccountingError.
        "additives_kg": {"C": 30.0},
        "fixture": "mars_basalt_C2A_12h.json",
    },
    {
        "name": "ci_carbonaceous_chondrite_C2B_12h",
        "feedstock_id": "ci_carbonaceous_chondrite",
        "campaign": "C2B",
        "hours": 12,
        "additives_kg": {},
        "fixture": "ci_carbonaceous_chondrite_C2B_12h.json",
    },
]


def _run_scenario(scenario: dict) -> dict:
    """Run a scenario and return the resulting JSON document.

    Run metadata overrides pin started_at_utc + kernel_commit_sha to
    fixture-stable values so a fresh machine reproduces the goldens
    even when the repo SHA changes.
    """

    run = PyrolysisRun(
        feedstock_id=scenario["feedstock_id"],
        campaign=scenario["campaign"],
        hours=scenario["hours"],
        additives_kg=dict(scenario["additives_kg"]),
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )
    return run.run()


def _assert_schema_shape(payload: dict) -> None:
    """Assert the runner-output schema contract.

    Tested as its own helper so:

    * each scenario's schema-shape assertion is identical;
    * a contract test (``test_runner_schema_shape_contract``) can call
      this without picking a specific scenario.
    """

    assert set(payload) == TOP_LEVEL_KEYS, (
        f"top-level keys drift: {set(payload) - TOP_LEVEL_KEYS} extra, "
        f"{TOP_LEVEL_KEYS - set(payload)} missing"
    )
    assert payload["schema_version"] == RUNNER_SCHEMA_VERSION

    assert set(payload["run_metadata"]).issuperset(RUN_METADATA_KEYS), (
        f"run_metadata missing keys: "
        f"{RUN_METADATA_KEYS - set(payload['run_metadata'])}"
    )
    engines_used = payload["run_metadata"]["engines_used"]
    assert isinstance(engines_used, dict)
    assert "active" in engines_used
    assert "requested" in engines_used
    assert "registry" in engines_used
    # engines_used.active is the flat {intent: provider_id} view spec'd
    # by Goal #18 CHECKLIST item 3.
    assert isinstance(engines_used["active"], dict)
    for intent, provider in engines_used["active"].items():
        assert isinstance(intent, str)
        assert isinstance(provider, str)

    assert isinstance(payload["final_state"], dict)
    for account, species_mol in payload["final_state"].items():
        assert isinstance(account, str)
        assert isinstance(species_mol, dict)
        for species, mol in species_mol.items():
            assert isinstance(species, str)
            assert isinstance(mol, (int, float))

    assert isinstance(payload["per_hour_summary"], list)
    for entry in payload["per_hour_summary"]:
        assert set(entry) == PER_HOUR_KEYS, (
            f"per_hour_summary key drift: extras "
            f"{set(entry) - PER_HOUR_KEYS}, missing "
            f"{PER_HOUR_KEYS - set(entry)}"
        )
        assert isinstance(entry["metal_yields_kg"], dict)
        assert isinstance(entry["condensation_train_kg"], dict)

    assert isinstance(payload["shadow_trace"], list)
    for event in payload["shadow_trace"]:
        assert isinstance(event, dict)
        # operator_decision + parity_warning + parity_error are the only
        # event types the runner surfaces today.
        assert "event" in event

    assert payload["status"] in ("ok", "partial", "failed")
    assert isinstance(payload["error_message"], str)


def _assert_mass_balance_bound(payload: dict) -> None:
    """Every per-hour entry must keep mass_balance_pct under the tolerance."""

    for entry in payload["per_hour_summary"]:
        assert abs(entry["mass_balance_pct"]) < MASS_BALANCE_MAX_PCT, (
            f"hour {entry['hour']} mass_balance_pct={entry['mass_balance_pct']}"
            f" exceeded {MASS_BALANCE_MAX_PCT}%"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_runner_golden_fixture_matches(scenario):
    """A live run must reproduce the committed golden fixture exactly."""

    fixture_path = FIXTURES_DIR / scenario["fixture"]
    expected = json.loads(fixture_path.read_text())
    actual = _run_scenario(scenario)

    _assert_schema_shape(actual)
    _assert_mass_balance_bound(actual)
    assert actual == expected, (
        f"runner output diverged from golden fixture {scenario['fixture']!s}; "
        "regenerate via `python -m simulator.runner --output=tests/fixtures/"
        f"runner/{scenario['fixture']}` if the change is intentional."
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_runner_is_deterministic(scenario):
    """Running the same scenario twice yields byte-identical JSON."""

    first = _run_scenario(scenario)
    second = _run_scenario(scenario)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_runner_schema_shape_contract():
    """The shape contract is pinned by the simplest passing scenario.

    Lives separately so a future scenario removal still keeps the
    shape-checker live.
    """

    payload = _run_scenario(SCENARIOS[0])
    _assert_schema_shape(payload)


def test_runner_cli_entry_point_writes_output_file(tmp_path):
    """``python -m simulator.runner`` must write the JSON document.

    Subprocess invocation guards the CLI surface that the goal text
    spec'd as the operator entry point.  Mirrors a real shell run and
    catches breakage in arg parsing / file writing that an in-process
    test would miss.
    """

    output = tmp_path / "smoke.json"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock=lunar_mare_low_ti",
            "--campaign=C0",
            "--hours=2",
            f"--output={output}",
            "--started-at-utc=2026-05-15T00:00:00Z",
            "--kernel-commit-sha=cli-smoke",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"CLI exited non-zero (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert output.exists(), f"CLI did not write {output}"
    payload = json.loads(output.read_text())
    assert payload["status"] == "ok"
    assert payload["run_metadata"]["hours_completed"] == 2


def test_runner_records_operator_decision_in_shadow_trace():
    """When the simulator pauses for a decision mid-run, the runner
    auto-applies the recommendation and records an ``operator_decision``
    event in shadow_trace.

    Today's three scenarios do not auto-pause within the run windows
    chosen (12-24h), so we drive the decision path explicitly via a
    scenario that crosses C0 -> C2A/C2B fork: lunar_mare for a long
    enough horizon to enter the PATH_AB pause.

    Regression: locks in mode that decision auto-apply runs through
    ``decision.recommendation`` rather than picking ``options[0]``
    blindly, since the simulator's recommendation field carries the
    feedstock-specific routing.
    """

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        # Long enough to traverse C0 -> C0B -> C2 fork.  500h is well
        # past the C0 endpoint (which fires around T~950C, ~18h on the
        # default ramp) so the decision pause is reached.
        hours=500,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "decision-fixture",
        },
    )
    payload = run.run()
    decisions = [
        event for event in payload["shadow_trace"]
        if event.get("event") == "operator_decision"
    ]
    assert decisions, (
        "long-horizon lunar_mare run did not pause for any operator decision; "
        "either campaign auto-transitions changed or pyrolysis routing was "
        "refactored without updating this regression test"
    )
    for record in decisions:
        # Auto-applied choice must equal recommendation when one is set.
        if record["recommendation"]:
            assert record["choice"] == record["recommendation"]


def test_runner_failure_envelope_for_unknown_feedstock(tmp_path):
    """A bogus feedstock returns a status=failed JSON document rather
    than crashing.

    Guards the CLI's promise of always emitting JSON: pipelines that
    diff status fields shouldn't need to special-case argparse errors.
    """

    output = tmp_path / "fail.json"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock=this_feedstock_does_not_exist",
            "--campaign=C0",
            "--hours=1",
            f"--output={output}",
            "--started-at-utc=2026-05-15T00:00:00Z",
            "--kernel-commit-sha=fail-smoke",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert output.exists()
    payload = json.loads(output.read_text())
    assert payload["status"] == "failed"
    assert "unknown feedstock" in payload["error_message"].lower()


def test_runner_engines_yaml_optional_load(tmp_path):
    """``--engines=path.yaml`` is optional forward-compat for Goal #19.

    The runner accepts the flag, propagates the requested mapping into
    ``run_metadata.engines_used.requested`` verbatim, and leaves the
    simulator's actual provider wiring untouched (Goal #19 owns the
    wiring change).
    """

    engines_yaml = tmp_path / "engines.yaml"
    engines_yaml.write_text(
        "engines:\n"
        "  vapor_pressure: vaporock_v1\n"
        "  silicate_liquidus: alphamelts_v1\n"
    )
    output = tmp_path / "engines.json"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock=lunar_mare_low_ti",
            "--campaign=C0",
            "--hours=1",
            f"--engines={engines_yaml}",
            f"--output={output}",
            "--started-at-utc=2026-05-15T00:00:00Z",
            "--kernel-commit-sha=engines-smoke",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(output.read_text())
    requested = payload["run_metadata"]["engines_used"]["requested"]
    assert requested == {
        "vapor_pressure": "vaporock_v1",
        "silicate_liquidus": "alphamelts_v1",
    }


def test_per_hour_summary_includes_pressure_and_mass_balance():
    """Regression: each per-hour entry must include the four numeric
    fields the goal text specified.

    Exists because reviewer flagged risk of dropping ``P_total_bar`` /
    ``pO2_bar`` once the metals dict became the primary readout.  The
    full PER_HOUR_KEYS check above already covers shape, but this one
    asserts the values are populated as floats so a "key present,
    value None" regression is caught.
    """

    payload = _run_scenario(SCENARIOS[0])
    for entry in payload["per_hour_summary"]:
        assert isinstance(entry["T_C"], (int, float))
        assert isinstance(entry["P_total_bar"], (int, float))
        assert isinstance(entry["pO2_bar"], (int, float))
        assert isinstance(entry["mass_balance_pct"], (int, float))
        assert isinstance(entry["O2_yield_kg_cumulative"], (int, float))


def test_session_per_hour_summary_event_uses_runner_builder():
    """Regression: ``SimSession`` emits the SocketIO
    ``per_hour_summary`` source value by calling
    :func:`simulator.runner.build_per_hour_summary`, NOT a parallel
    implementation.

    Goal #18 acceptance criterion #4: "The SocketIO stream emits
    per_hour_summary frames as the run progresses; final JSON matches
    the runner output exactly."  A future patch that adds a web-side
    per-hour builder would silently let the web shape drift from the
    CLI shape; this regression test locks the import in place.

    The test reads the source rather than instantiating the SocketIO
    transport so it stays runnable without a real socketio loop.
    """

    session_core = (
        Path(__file__).resolve().parent.parent
        / "simulator"
        / "session.py"
    )
    source = session_core.read_text()
    assert "def _build_per_hour_summary" in source, (
        "SimSession must own the per-hour summary handoff so web/events.py "
        "can stay a thin SocketIO adapter."
    )
    assert "from simulator.runner import build_per_hour_summary" in source, (
        "simulator/session.py must import build_per_hour_summary from the "
        "runner module so the SocketIO stream cannot drift from the CLI "
        "runner schema (goal #18)."
    )
    assert "return build_per_hour_summary(sim, snapshot)" in source, (
        "SimSession must call build_per_hour_summary inside its StepResult "
        "builder; bypassing it lets a refactor open a per-hour shape gap."
    )


def test_runner_final_state_is_mol_keyed_not_kg():
    """Regression: ``final_state`` reports moles, not kilograms.

    AGENTS.md invariant #1 names the AtomLedger as mol-native -- kg
    numbers are external projections only.  The runner deliberately
    emits the mol view so downstream consumers can convert via the
    species registry rather than depend on the runner's choice of
    mass units.

    Catches: a refactor that "helpfully" calls ``kg_by_account``
    instead of ``mol_by_account`` to make the JSON more
    human-readable.  Validates the numbers are mol-magnitude by
    spot-checking SiO2 in process.cleaned_melt: a 1000 kg
    lunar mare batch has ~445 kg SiO2 = ~7.4 kmol = 7400 mol, NOT
    7400000 (which would be grams) and NOT 445 (which would be kg).
    """

    payload = _run_scenario(SCENARIOS[0])
    cleaned_melt = payload["final_state"].get("process.cleaned_melt", {})
    sio2_mol = cleaned_melt.get("SiO2")
    assert sio2_mol is not None, (
        "process.cleaned_melt should contain SiO2 after a lunar_mare run"
    )
    # 445 kg SiO2 / (60 g/mol / 1000) = ~7400 mol; the C0 ramp evaporates
    # only a sliver, so the post-24h figure stays in the 7000-7500 mol
    # band.  A kg-coded value would be ~445; a grams-coded value would
    # be ~445000.
    assert 5000 < sio2_mol < 9000, (
        f"final_state SiO2 in process.cleaned_melt = {sio2_mol}; "
        "expected ~7400 mol.  If this number looks like ~445 the runner "
        "regressed to kg-keyed output; if ~445000 the unit is grams."
    )


def test_runner_does_not_apply_ledger_transitions_directly():
    """Mutation purity guard: the runner module is read-only against
    the ``AtomLedger``.

    AGENTS.md invariant #1 says only kernel / melt_backend /
    accounting code may apply ledger transitions.  The runner is a
    NEW module under simulator/ that orchestrates the simulator from
    above; it must NOT introduce a new write path.

    Regression: catches a refactor that "helpfully" calls
    ``atom_ledger.apply`` / ``debit`` / ``credit`` / ``load_external``
    directly to assemble the final_state document.
    """

    runner_py = Path(__file__).resolve().parent.parent / "simulator" / "runner.py"
    source = runner_py.read_text()
    forbidden_writes = (
        "atom_ledger.apply(",
        "atom_ledger.debit(",
        "atom_ledger.credit(",
        "atom_ledger.load_external(",
        "atom_ledger.move(",
        "atom_ledger.record(",
        "atom_ledger.transfer(",
        "commit_batch(",
    )
    for pattern in forbidden_writes:
        assert pattern not in source, (
            f"simulator/runner.py contains forbidden ledger-mutation "
            f"call {pattern!r}; only the kernel / melt_backend / "
            f"accounting code may write to the ledger."
        )
