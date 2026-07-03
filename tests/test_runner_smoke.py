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

from collections import defaultdict
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.run_executor import RunExecutor
from simulator.runner import (
    NOT_APPLICABLE_UNTIL_P0,
    PyrolysisRun,
    RUNNER_SCHEMA_VERSION,
    RunnerError,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "runner"


# Mass-balance tolerance for every snapshot in the golden fixtures.
# Mirrors the existing simulator-wide tolerance enforced by
# ``tests/test_mass_balance.py``; surfacing the same number here means a
# runner change that opens a balance gap fails fast against the goldens.
MASS_BALANCE_MAX_PCT = 5e-12

VPR_P6A_TRACE_CONTROLS = {
    "sio_start_temperature_c": 1050.0,
    "sio_hold_temperature_c": 1600.0,
    "sio_ramp_c_per_hr": 15.0,
    "sio_liner_temperature_c": 1100.0,
}

# Schema-shape: the top-level keys every runner output must expose.
TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "run_metadata",
    "final_state",
    "final",
    "stage_purity_report",
    "vapor_pressure_source_report",
    "shuttle_refusal_history",
    "pO2_enforcement_by_hour",
    "per_hour_summary",
    "shadow_trace",
    "status",
    "reason",
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
    "backend_status",
    "backend_authoritative",
    "backend_real_active",
    "evidence_class",
    "runtime_status",
    "label_source",
    "certification_allowed",
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
    "O2_source_side_potential_kg_cumulative",
    "O2_metric_label",
    "metal_yields_kg",
    "condensation_train_kg",
    "vapor_species_kg_hr",
    "wall_deposit_delta_kg",
    "wall_deposit_cumulative_kg",
    "Kn",
    "regime",
    "transport_formula_id",
})
PER_HOUR_OPTIONAL_KEYS = frozenset({
    "pO2_enforcement",
    # Conditionally-emitted per-hour keys: present on a row only when the
    # backing source is populated (staged / diagnostic / real-backend runs),
    # so they are absent on the plain stub-backend smoke SCENARIOS. Whitelisted
    # so the per_hour_summary issubset gate accepts a legitimate row instead of
    # flagging key drift (BUG-032 + same-class sweep; see
    # docs/runner-output-schema.md "Per-hour summary"). Most come from
    # HourSnapshot fields via build_per_hour_summary; reduced_real_cache_state is
    # added downstream by simulator/run_executor.py from the simulator's
    # _last_reduced_real_cache_state when it is not None.
    "evap_plane_selectivity",
    "mre_uncertified_yield",
    "fe_redox_split",
    "stage_3_capture",
    "redox_source_breakdown",
    "mass_balance_error_category",
    "reduced_real_cache_state",
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
        allow_fallback_vapor=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )
    return run.run()


@pytest.mark.parametrize("alias", ["internal-analytical", "internal_analytical"])
def test_pyrolysis_run_folds_internal_analytical_alias_to_stable_stub_token(alias):
    """A `--backend internal-analytical` run serializes the stable `stub` token.

    The display alias folds onto `stub` in PyrolysisRun.__post_init__, so the
    serialized run metadata (`"backend"`) stays the byte-stable legacy token and
    the fidelity-vocabulary backend-token translator never sees an unknown
    `internal-analytical` token. The runner CLI `--backend` choices accept the
    alias.
    """
    run = PyrolysisRun(feedstock_id="lunar_mare_low_ti", backend_name=alias)
    assert run.backend_name == "stub"


def test_c3_alkali_recipe_dose_routes_to_credit_line_not_additives():
    # S2b: the C3 alkali dose is a recycled credit-line draw request, NOT a
    # physical additives_kg seed. additives_kg stays empty; the dose surfaces
    # as c3_alkali_credit_* metadata and shuttle inventory.
    schema = RecipeSchema()
    na_dose = ("campaigns", "C3", "alkali_dosing", "Na_kg")
    k_dose = ("campaigns", "C3", "alkali_dosing", "K_kg")
    patch = RecipePatch({na_dose: 12.0, k_dose: 4.0}).validated(schema)
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
        setpoints_patch=schema.to_setpoints_patch(patch),
    )

    config = run._session_config()
    session = run._start_session()
    sim = session.simulator
    payload = run.run()

    assert dict(config.additives_kg) == {}
    assert sim.record.additives_kg == {}
    assert sim.shuttle_Na_inventory_kg >= 12.0
    assert sim._c3_alkali_credit_drawn_kg_by_species["Na"] == pytest.approx(12.0)
    assert sim.atom_ledger.kg_by_account("reservoir.reagent.Na").get("Na", 0.0) == (
        pytest.approx(-12.0)
    )
    assert payload["run_metadata"]["additives_kg"] == {}
    assert payload["run_metadata"]["c3_alkali_credit_dose_kg_by_species"] == {
        "K": 4.0,
        "Na": 12.0,
    }
    assert payload["run_metadata"]["c3_alkali_credit_drawn_kg_by_species"][
        "Na"
    ] >= 12.0
    assert payload["run_metadata"]["c3_alkali_credit_drawn_kg_by_species"][
        "K"
    ] == pytest.approx(0.0)
    assert payload["run_metadata"]["c3_alkali_credit_outstanding_kg_by_species"][
        "Na"
    ] >= 12.0
    _assert_mass_balance_bound(payload)

    undosed = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
    ).run()
    assert undosed["run_metadata"]["additives_kg"] == {}


def test_c3_alkali_dosing_conflict_fail_loud():
    """CONFLICT: explicit additives_kg and C3 alkali_dosing must not disagree."""
    schema = RecipeSchema()
    na_dose = ("campaigns", "C3", "alkali_dosing", "Na_kg")
    k_dose = ("campaigns", "C3", "alkali_dosing", "K_kg")
    patch = RecipePatch({na_dose: 12.0, k_dose: 4.0}).validated(schema)
    setpoints_patch = schema.to_setpoints_patch(patch)

    conflict = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
        additives_kg={"Na": 5.0},
        setpoints_patch=setpoints_patch,
        allow_fallback_vapor=True,
    )
    with pytest.raises(
        RunnerError,
        match=(
            r"campaigns\.C3\.alkali_dosing\.Na_kg conflicts with "
            r"additives_kg\['Na'\]"
        ),
    ):
        conflict._session_config()

    k_conflict = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
        additives_kg={"K": 1.0},
        setpoints_patch=setpoints_patch,
        allow_fallback_vapor=True,
    )
    with pytest.raises(
        RunnerError,
        match=(
            r"campaigns\.C3\.alkali_dosing\.K_kg conflicts with "
            r"additives_kg\['K'\]"
        ),
    ):
        k_conflict._session_config()

    dosing_only = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
        setpoints_patch=setpoints_patch,
        allow_fallback_vapor=True,
    )
    dosing_config = dosing_only._session_config()
    assert dict(dosing_config.additives_kg) == {}
    assert dosing_config.setpoints["campaigns"]["C3"]["alkali_dosing"] == {
        "K_kg": pytest.approx(4.0),
        "Na_kg": pytest.approx(12.0),
    }

    additive_only = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C3_NA",
        hours=1,
        additives_kg={"Na": 5.0, "K": 2.0},
        allow_fallback_vapor=True,
    )
    additive_config = additive_only._session_config()
    assert additive_config.additives_kg["Na"] == pytest.approx(5.0)
    assert additive_config.additives_kg["K"] == pytest.approx(2.0)


def test_runtime_campaign_overrides_refuse_unknown_field_names() -> None:
    valid = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        runtime_campaign_overrides={
            "C2A": {
                "pO2_mbar": 1.0,
                "p_total_mbar": 9.0,
                "ramp_rate": 12.0,
            }
        },
        allow_fallback_vapor=True,
    )
    assert valid._session_config().runtime_campaign_overrides["C2A"] == {
        "pO2_mbar": 1.0,
        "p_total_mbar": 9.0,
        "ramp_rate": 12.0,
    }

    poisoned = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        runtime_campaign_overrides={"C2A": {"unused_limit": 1.0}},
        allow_fallback_vapor=True,
    )
    with pytest.raises(
        RunnerError,
        match=(
            r"runtime_campaign_overrides\['C2A'\]\.unused_limit.*"
            r"known overridable fields.*pO2_mbar"
        ),
    ):
        poisoned._session_config()


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
    metadata = payload["run_metadata"]
    assert metadata["evidence_class"] == "internal-analytical"
    assert metadata["runtime_status"] in {"ok", "unavailable", "out_of_domain"}
    assert metadata["backend_real_active"] is metadata["backend_authoritative"]
    assert metadata["certification_allowed"] is False
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

    assert set(payload["final"]) == {
        "wall_deposit_by_species_kg",
        "deposit_by_surface_species_kg",
        "pump_outlet_by_species_kg",
    }
    assert isinstance(payload["final"]["wall_deposit_by_species_kg"], dict)
    assert isinstance(payload["final"]["deposit_by_surface_species_kg"], dict)
    assert (
        payload["final"]["pump_outlet_by_species_kg"]
        == NOT_APPLICABLE_UNTIL_P0
    )

    assert isinstance(payload["stage_purity_report"], dict)
    for stage_key, stage in payload["stage_purity_report"].items():
        assert isinstance(stage_key, str)
        assert set(stage).issuperset({
            "stage_number",
            "label",
            "accepted_species",
            "designated_species_kg",
            "impurity_species_kg",
            "purity_fraction",
            "verdict",
        })
        assert stage["verdict"] in {"PURE", "MIXED", "CONTAMINATED"}

    source_report = payload["vapor_pressure_source_report"]
    assert isinstance(source_report, dict)
    assert set(source_report) == {"species", "summary", "total_species"}
    assert isinstance(source_report["species"], dict)
    assert isinstance(source_report["summary"], dict)
    assert source_report["total_species"] == len(source_report["species"])
    for species, source in source_report["species"].items():
        assert isinstance(species, str)
        assert source in {
            "thermoengine",
            "alphamelts_python_api",
            "alphamelts_text",
            "vaporock",
            "builtin_fallback",
            "builtin_authoritative",
            "kernel_diagnostic",
        }
    for source, item in source_report["summary"].items():
        assert isinstance(source, str)
        assert set(item) == {"count", "percentage"}
        assert isinstance(item["count"], int)
        assert isinstance(item["percentage"], (int, float))

    assert isinstance(payload["per_hour_summary"], list)
    assert isinstance(payload["pO2_enforcement_by_hour"], list)
    for row in payload["pO2_enforcement_by_hour"]:
        assert set(row).issuperset({
            "hour",
            "setpoint_mbar",
            "achieved_mbar",
            "limited_by_total_pressure",
            "status",
        })
    for entry in payload["per_hour_summary"]:
        assert PER_HOUR_KEYS.issubset(entry), (
            f"per_hour_summary key drift: extras "
            f"{set(entry) - PER_HOUR_KEYS}, missing "
            f"{PER_HOUR_KEYS - set(entry)}"
        )
        assert set(entry).issubset(PER_HOUR_KEYS | PER_HOUR_OPTIONAL_KEYS)
        assert isinstance(entry["metal_yields_kg"], dict)
        assert isinstance(entry["condensation_train_kg"], dict)
        assert isinstance(entry["vapor_species_kg_hr"], dict)
        assert isinstance(entry["wall_deposit_delta_kg"], dict)
        assert isinstance(entry["wall_deposit_cumulative_kg"], dict)
        assert entry["Kn"] is None or isinstance(entry["Kn"], (int, float))
        assert isinstance(entry["regime"], str)
        assert entry["transport_formula_id"] == NOT_APPLICABLE_UNTIL_P0

    assert isinstance(payload["shadow_trace"], list)
    for event in payload["shadow_trace"]:
        assert isinstance(event, dict)
        # operator_decision + parity_warning + parity_error are the only
        # event types the runner surfaces today.
        assert "event" in event

    assert payload["status"] in ("ok", "partial", "failed", "refused")
    assert isinstance(payload["reason"], str)
    assert isinstance(payload["error_message"], str)


def _assert_mass_balance_bound(payload: dict) -> None:
    """Every per-hour entry must keep mass_balance_pct under the tolerance."""

    for entry in payload["per_hour_summary"]:
        assert abs(entry["mass_balance_pct"]) < MASS_BALANCE_MAX_PCT, (
            f"hour {entry['hour']} mass_balance_pct={entry['mass_balance_pct']}"
            f" exceeded {MASS_BALANCE_MAX_PCT}%"
        )


def _nested_wall_deposit_kg(values) -> dict[str, dict[str, float]]:
    nested = defaultdict(dict)
    for (segment, species), kg in sorted(values.items()):
        amount = float(kg)
        if abs(amount) > 1.0e-12:
            nested[str(segment)][str(species)] = amount
    return {
        segment: dict(sorted(species_kg.items()))
        for segment, species_kg in sorted(nested.items())
    }


def _assert_nested_kg_close(actual: dict, expected: dict) -> None:
    assert actual.keys() == expected.keys()
    for segment, expected_species in expected.items():
        actual_species = actual[segment]
        assert actual_species.keys() == expected_species.keys()
        for species, expected_kg in expected_species.items():
            assert actual_species[species] == pytest.approx(expected_kg, abs=1e-12)


def _assert_flat_kg_close(actual: dict, expected: dict) -> None:
    assert actual.keys() == expected.keys()
    for species, expected_kg in expected.items():
        assert actual[species] == pytest.approx(expected_kg, abs=1e-12)


def _assert_species_totals_match_trace(actual: dict, trace_values: dict) -> None:
    expected = defaultdict(float)
    for (_segment, species), kg in trace_values.items():
        expected[str(species)] += float(kg)
    for species, actual_kg in actual.items():
        assert actual_kg == pytest.approx(expected.get(str(species), 0.0), abs=1e-12)
    nonzero_expected = {
        species
        for species, kg in expected.items()
        if abs(float(kg)) > 1.0e-12
    }
    assert nonzero_expected.issubset(actual.keys())


def _nested_total_kg(values: dict[str, dict[str, float]]) -> float:
    return sum(
        float(kg)
        for species_kg in values.values()
        for kg in species_kg.values()
    )


def _assert_p6a_payload_matches_trace(payload: dict, trace) -> None:
    cumulative = defaultdict(float)
    saw_wall_delta = False
    saw_vapor = False
    saw_kn = False

    assert len(payload["per_hour_summary"]) == len(trace.snapshots)
    for entry, snapshot, wall_delta in zip(
        payload["per_hour_summary"],
        trace.snapshots,
        trace.wall_deposit_by_segment_species_delta,
    ):
        expected_vapor = {
            str(species): float(kg_hr)
            for species, kg_hr in sorted(snapshot.evap_flux.species_kg_hr.items())
            if abs(float(kg_hr)) > 1.0e-12
        }
        _assert_flat_kg_close(entry["vapor_species_kg_hr"], expected_vapor)
        saw_vapor = saw_vapor or bool(expected_vapor)

        expected_delta = _nested_wall_deposit_kg(wall_delta)
        _assert_nested_kg_close(entry["wall_deposit_delta_kg"], expected_delta)
        saw_wall_delta = saw_wall_delta or bool(expected_delta)

        for key, kg in wall_delta.items():
            cumulative[key] += float(kg)
        expected_cumulative = _nested_wall_deposit_kg(cumulative)
        _assert_nested_kg_close(
            entry["wall_deposit_cumulative_kg"],
            expected_cumulative,
        )
        assert _nested_total_kg(
            entry["wall_deposit_cumulative_kg"]
        ) == pytest.approx(sum(float(kg) for kg in cumulative.values()), abs=1e-12)

        kn_summary = dict(snapshot.knudsen_regime_summary or {})
        if kn_summary:
            assert entry["Kn"] == pytest.approx(
                float(kn_summary["knudsen_number"]),
            )
            assert entry["regime"] == kn_summary["knudsen_regime"]
            saw_kn = True
        else:
            assert entry["Kn"] is None
            assert entry["regime"] == ""
        assert entry["transport_formula_id"] == NOT_APPLICABLE_UNTIL_P0

    assert saw_wall_delta
    assert saw_vapor
    assert saw_kn
    _assert_nested_kg_close(
        payload["final"]["deposit_by_surface_species_kg"],
        _nested_wall_deposit_kg(trace.wall_deposit_by_segment_species_kg),
    )
    _assert_species_totals_match_trace(
        payload["final"]["wall_deposit_by_species_kg"],
        trace.wall_deposit_by_segment_species_kg,
    )
    assert payload["final"]["pump_outlet_by_species_kg"] == NOT_APPLICABLE_UNTIL_P0


def _run_c2a_trace_export():
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_continuous",
        hours=24,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
        **VPR_P6A_TRACE_CONTROLS,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "vpr-p6a-parity",
        },
    )
    session = run._start_session()
    run._apply_sio_pre_run_controls(session.simulator)
    execution = RunExecutor().execute_session(session, hours=int(run.hours))
    payload = run._build_output(execution)
    return payload, execution


@pytest.mark.serial  # spawns CLI subprocess; flakes under xdist co-scheduling
def test_vpr_p6a_cli_artifact_matches_in_process_trace(tmp_path):
    """Design Section 9 R9.2/R9.5: CLI P6a exports match PhysicsTrace data."""

    _payload, execution = _run_c2a_trace_export()
    output = tmp_path / "vpr-p6a-cli.json"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock=lunar_mare_low_ti",
            "--campaign=C2A_continuous",
            "--hours=24",
            "--allow-fallback-vapor",
            "--allow-unmeasured-alpha-fallback",
            "--force-builtin-vapor-pressure",
            f"--sio-start-temperature-c={VPR_P6A_TRACE_CONTROLS['sio_start_temperature_c']}",
            f"--sio-hold-temperature-c={VPR_P6A_TRACE_CONTROLS['sio_hold_temperature_c']}",
            f"--sio-ramp-c-per-hr={VPR_P6A_TRACE_CONTROLS['sio_ramp_c_per_hr']}",
            f"--sio-liner-temperature-c={VPR_P6A_TRACE_CONTROLS['sio_liner_temperature_c']}",
            f"--output={output}",
            "--started-at-utc=2026-05-15T00:00:00Z",
            "--kernel-commit-sha=vpr-p6a-parity",
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
    cli_payload = json.loads(output.read_text())
    assert cli_payload["status"] == "ok"
    _assert_p6a_payload_matches_trace(cli_payload, execution.trace)


def test_vpr_p6a_p0_gated_fields_are_explicit_sentinels():
    """Design Section 9 R9.2/R9.3/R9.5: P6b-only fields are explicit P0 sentinels."""

    payload, _execution = _run_c2a_trace_export()
    assert (
        payload["final"]["pump_outlet_by_species_kg"]
        == NOT_APPLICABLE_UNTIL_P0
    )
    for entry in payload["per_hour_summary"]:
        assert entry["transport_formula_id"] == NOT_APPLICABLE_UNTIL_P0
    assert {
        "vapor_species_kg_hr",
        "wall_deposit_delta_kg",
        "wall_deposit_cumulative_kg",
        "Kn",
        "regime",
        "transport_formula_id",
    }.issubset(payload["per_hour_summary"][0])
    assert "deposit_by_surface_species_kg" in payload["final"]
    assert "pump_outlet_by_species_kg" in payload["final"]


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


def test_runner_cli_rejects_zero_mass_with_named_failure(tmp_path):
    output = tmp_path / "zero-mass.json"
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock=lunar_mare_low_ti",
            "--campaign=C0",
            "--hours=2",
            "--mass-kg=0",
            f"--output={output}",
            "--started-at-utc=2026-05-15T00:00:00Z",
            "--kernel-commit-sha=cli-zero-mass",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert output.exists(), f"CLI did not write {output}"
    payload = json.loads(output.read_text())
    assert payload["status"] == "failed"
    assert payload["run_metadata"]["mass_kg"] == pytest.approx(0.0)
    assert "zero_input_basis_breach" in payload["error_message"]


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
    # Autoreview r5 P2 (2026-05-27): the failed envelope MUST advertise
    # the SAME top-level shape as a successful run; downstream
    # consumers diffing the schema shouldn't have to special-case
    # failures. Pin to the happy-path TOP_LEVEL_KEYS set.
    assert set(payload) == TOP_LEVEL_KEYS, (
        f"failure envelope shape drift: extra={set(payload) - TOP_LEVEL_KEYS} "
        f"missing={TOP_LEVEL_KEYS - set(payload)}"
    )
    assert payload["shuttle_refusal_history"] == []
    assert payload["pO2_enforcement_by_hour"] == []
    assert payload["per_hour_summary"] == []
    assert payload["shadow_trace"] == []


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
        assert isinstance(
            entry["O2_source_side_potential_kg_cumulative"], (int, float)
        )
        assert (
            entry["O2_source_side_potential_kg_cumulative"]
            == entry["O2_yield_kg_cumulative"]
        )
        assert entry["O2_metric_label"] == (
            "source-side O2 potential (emitted; not recovered)"
        )


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


def test_conditional_per_hour_observables_are_whitelisted() -> None:
    """Every conditionally-emitted per-hour observable key must live in
    PER_HOUR_OPTIONAL_KEYS, else the per_hour_summary issubset gate flags a
    legitimate staged/diagnostic row as key drift.

    Regression guard for BUG-032 (``evap_plane_selectivity``) and its
    same-class siblings ``mre_uncertified_yield`` / ``fe_redox_split``: the
    runner already emits these keys, but only on runs where the backing
    HourSnapshot field is populated -- a path the smoke SCENARIOS happen not
    to exercise -- so the missing whitelist entries were latent. This test
    drives the emit helpers directly with populated inputs so the contract is
    actively verified, not merely tolerated.
    """

    from types import SimpleNamespace

    from simulator.runner import (
        _evap_plane_selectivity_observables,
        _fe_redox_split_observables,
        _mre_uncertified_yield_observables,
    )

    snapshot = SimpleNamespace(
        evap_plane_selectivity={
            "target_species": ["Na", "K"],
            "per_species_fraction": {"Na": 0.6, "K": 0.3},
            "total_flux_kg_hr": 1.0,
            "target_flux_kg_hr": 0.9,
            "target_selectivity": 0.9,
        },
        mre_uncertified_yield={"Al": 1.23},
        fe_redox_split={
            "fO2_log": -8.0,
            "ferric_frac": 0.2,
            "valid": True,
            "native_fe_saturation_event": {
                "native_fe_event": "deferred_not_liquid_for_redox",
                "native_fe_event_status": "deferred",
            },
        },
    )

    # The native-Fe saturation event is a Mapping and must serialize as a
    # nested JSON object, not a Python repr string (codex M2-FOLD-CLOSE:
    # the observables helper stringified every non-partition key).
    fe_split_export = _fe_redox_split_observables(snapshot)["fe_redox_split"]
    assert fe_split_export["native_fe_saturation_event"] == {
        "native_fe_event": "deferred_not_liquid_for_redox",
        "native_fe_event_status": "deferred",
    }

    emitted: set[str] = set()
    emitted |= set(_evap_plane_selectivity_observables(snapshot))
    emitted |= set(_mre_uncertified_yield_observables(snapshot))
    emitted |= set(_fe_redox_split_observables(snapshot))

    # All three must actually emit given non-empty inputs (guards against the
    # helpers silently short-circuiting and making this test a no-op).
    assert emitted == {
        "evap_plane_selectivity",
        "mre_uncertified_yield",
        "fe_redox_split",
    }
    # The contract under test: every emitted conditional key is whitelisted as
    # optional, and none collides with a required key.
    assert emitted.issubset(PER_HOUR_OPTIONAL_KEYS)
    assert emitted.isdisjoint(PER_HOUR_KEYS)

    # mass_balance_error_category is the same conditional-key class but is
    # added inline by build_per_hour_summary (when the snapshot carries a
    # non-empty category string), not via a standalone emit helper, so it is
    # asserted as a whitelist member rather than driven through a helper.
    assert "mass_balance_error_category" in PER_HOUR_OPTIONAL_KEYS
    assert "mass_balance_error_category" not in PER_HOUR_KEYS

    # reduced_real_cache_state is added downstream of build_per_hour_summary by
    # simulator/run_executor.py (when sim._last_reduced_real_cache_state is not
    # None) and serialized into per_hour_summary via runner.py; same conditional
    # class, so it is asserted as a whitelist member rather than helper-driven.
    assert "reduced_real_cache_state" in PER_HOUR_OPTIONAL_KEYS
    assert "reduced_real_cache_state" not in PER_HOUR_KEYS
