from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from simulator.cost_energy import (
    UnavailableQuantity,
    is_unavailable_quantity,
    unavailable_reason_of,
)
from simulator.cost_ledger import run_pumping_input_cost
from simulator.cost_parameters import (
    PAYLOAD_ABSENT_COST_PROVENANCE,
    default_cost_parameters_block,
)
from simulator.accounting.run_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    EXECUTION_STATUSES,
    RunArtifactContractError,
    build_run_artifact,
)


REQUIRED_HEADER_KEYS = {
    "run_id",
    "name",
    "created_at",
    "feedstock_id",
    "charge_mass_kg",
    "campaign_chain",
    "engine_identity",
    "target_snapshot",
    "cost_block",
}
OPTIONAL_HEADER_KEYS = {
    "recipe_snapshot",
    "seed",
    "c3_dose",
    "additives_kg",
    "effective_config",
}
TERMINAL_KEYS = {
    "final_state",
    "final",
    "stage_purity",
    "vapor_pressure_source_report",
    "run_metadata",
    "mass_balance_closure",
}
# W-A8: confidence is OPTIONAL — present only when the artifact carries the
# evidence to grade honestly (finite mass-balance residual); never fabricated.
OPTIONAL_TERMINAL_KEYS = {
    "confidence",
    "cost_totals",
    "terminal_product_taxonomy",
}


def _runner_payload(
    status: str = "ok",
    *,
    per_hour_summary: list[dict] | None = None,
) -> dict:
    if per_hour_summary is None:
        per_hour_summary = [
            {
                "hour": 1,
                "campaign": "C0",
                "T_C": 900.0,
                "mass_balance_pct": 0.125,
                "energy_cumulative_breakdown_kWh": {"furnace": 2.5},
                "wall_deposit_delta_kg": {"hot_zone": {"Fe": 0.01}},
            }
        ]
    return {
        "status": status,
        "reason": "synthetic_reason",
        "error_message": "synthetic error",
        "run_metadata": {
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "backend": "internal-analytical",
            "kernel_commit_sha": "kernel-sha",
        },
        "per_hour_summary": per_hour_summary,
        "final_state": {"process.cleaned_melt": {"SiO2": 2.0}},
        "final": {"wall_deposit_by_species_kg": {"Fe": 0.01}},
        "stage_purity_report": {"stage_1": {"verdict": "PURE"}},
        "vapor_pressure_source_report": {
            "vapor_pressure_backend_status": "ok",
            "authoritative_for_requested_vapor_pressure": True,
        },
    }


@pytest.mark.parametrize("status", sorted(EXECUTION_STATUSES))
def test_all_execution_statuses_construct_contract_envelope(status: str) -> None:
    artifact = build_run_artifact(
        _runner_payload(status), run_id=f"run-{status}", name="Synthetic run"
    )

    expected_keys = {
        "artifact_schema_version",
        "execution_status",
        "lifecycle",
        "header",
        "timesteps",
        "terminal",
    }
    if status != "ok":
        expected_keys.add("failure")
    assert set(artifact) == expected_keys
    # 0.2.0: W-A3 added optional timesteps[].ledger (additive → minor bump).
    # Bumping this pin is a deliberate controller decision, never a worker edit.
    assert artifact["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION == "0.2.0"
    assert artifact["execution_status"] == status
    assert artifact["lifecycle"] == "complete"
    assert ("failure" in artifact) is (status != "ok")


@pytest.mark.parametrize("status", [None, "", "complete", "OK", 1])
def test_invalid_execution_status_is_rejected(status) -> None:
    payload = _runner_payload()
    payload["status"] = status

    with pytest.raises(RunArtifactContractError, match="unknown execution status"):
        build_run_artifact(payload, run_id="run-invalid")


def test_missing_execution_status_is_rejected() -> None:
    payload = _runner_payload()
    del payload["status"]

    with pytest.raises(RunArtifactContractError, match="missing execution status"):
        build_run_artifact(payload, run_id="run-missing")


def test_zero_timestep_failure_artifact_is_valid() -> None:
    artifact = build_run_artifact(
        _runner_payload("failed", per_hour_summary=[]), run_id="run-zero-hour"
    )

    assert artifact["timesteps"] == []
    assert set(artifact["header"]) == REQUIRED_HEADER_KEYS
    assert artifact["failure"] == {
        "reason": "synthetic_reason",
        "error_message": "synthetic error",
    }
    assert TERMINAL_KEYS <= set(artifact["terminal"]) <= TERMINAL_KEYS | OPTIONAL_TERMINAL_KEYS
    assert artifact["terminal"]["mass_balance_closure"] == {
        "residual_pct": None,
        "basis": "final-hour percent",
    }


def test_timestep_summary_is_the_verbatim_input_mapping() -> None:
    summary = {
        "hour": 7,
        "campaign": "C3",
        "opaque_future_diagnostic": {"token": [1, 2, 3]},
    }
    artifact = build_run_artifact(
        _runner_payload(per_hour_summary=[summary]), run_id="run-verbatim"
    )

    assert artifact["timesteps"] == [{"hour": 7, "summary": summary}]
    assert artifact["timesteps"][0]["summary"] is summary


def test_terminal_preserves_precomputed_producer_blocks_without_reprojection(
) -> None:
    payload = _runner_payload()
    thermal_train_report = {"schema_version": "thermal-train-report-v2"}
    product_classification = {
        "classification": {"metals_and_oxygen": {"Fe": 1.0}},
        "markdown": "producer markdown",
    }
    terminal_product_taxonomy = {
        "match_status": "no_match",
        "physical_composition": {"mass_kg": 1.0},
    }
    payload["thermal_train_report"] = thermal_train_report
    payload["product_classification"] = product_classification
    payload["terminal_product_taxonomy"] = terminal_product_taxonomy

    artifact = build_run_artifact(payload, run_id="run-producer-blocks")

    assert artifact["terminal"]["thermal_train_report"] is thermal_train_report
    assert artifact["terminal"]["product_classification"] is product_classification
    assert (
        artifact["terminal"]["terminal_product_taxonomy"]
        is terminal_product_taxonomy
    )


def test_legacy_payload_omits_new_terminal_blocks() -> None:
    artifact = build_run_artifact(_runner_payload(), run_id="run-legacy")

    assert "thermal_train_report" not in artifact["terminal"]
    assert "product_classification" not in artifact["terminal"]
    assert "terminal_product_taxonomy" not in artifact["terminal"]


def test_terminal_product_taxonomy_preserves_explicit_nullability() -> None:
    payload = _runner_payload()
    payload["terminal_product_taxonomy"] = None

    artifact = build_run_artifact(payload, run_id="run-taxonomy-null")

    assert "terminal_product_taxonomy" in artifact["terminal"]
    assert artifact["terminal"]["terminal_product_taxonomy"] is None


def test_header_and_terminal_key_contract_omits_unavailable_optional_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "simulator.accounting.run_artifact.cache_version_for",
        lambda backend: None,
    )
    payload = _runner_payload()
    artifact = build_run_artifact(payload, run_id="run-minimum", name="Minimum")

    assert set(artifact["header"]) == REQUIRED_HEADER_KEYS
    assert OPTIONAL_HEADER_KEYS.isdisjoint(artifact["header"])
    assert artifact["header"]["engine_identity"] == {
        "name": "internal-analytical",
        "cache_version": None,
        "backend_wire_token": "internal-analytical",
        "kernel_commit_sha": "kernel-sha",
    }
    assert (
        artifact["header"]["engine_identity"]["cache_version"]
        != payload["run_metadata"]["kernel_commit_sha"]
    )
    assert TERMINAL_KEYS <= set(artifact["terminal"]) <= TERMINAL_KEYS | OPTIONAL_TERMINAL_KEYS
    assert artifact["terminal"]["mass_balance_closure"] == {
        "residual_pct": 0.125,
        "basis": "final-hour percent",
    }


def test_absent_terminal_payload_sections_are_omitted_but_empty_sections_are_kept(
) -> None:
    payload = _runner_payload()
    section_keys = {
        "final_state": "final_state",
        "final": "final",
        "stage_purity_report": "stage_purity",
        "vapor_pressure_source_report": "vapor_pressure_source_report",
    }
    for payload_key in section_keys:
        del payload[payload_key]

    absent = build_run_artifact(payload, run_id="run-absent-terminal-sections")

    assert set(section_keys.values()).isdisjoint(absent["terminal"])
    assert isinstance(absent["terminal"], dict)
    assert absent["terminal"]["mass_balance_closure"] == {
        "residual_pct": 0.125,
        "basis": "final-hour percent",
    }

    for payload_key in section_keys:
        payload[payload_key] = {}
    present_empty = build_run_artifact(payload, run_id="run-empty-terminal-sections")

    for artifact_key in section_keys.values():
        assert present_empty["terminal"][artifact_key] == {}


def test_available_optional_header_fields_keep_verified_shapes(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.accounting.run_artifact.cache_version_for",
        lambda backend: f"{backend}-cache-v1",
    )
    payload = _runner_payload()
    payload["run_metadata"].update(
        {
            "seed": 7,
            "c3_alkali_credit_dose_kg_by_species": {"Na": 1.25, "K": 0.5},
        }
    )
    payload["recipe_snapshot"] = {
        "setpoints_patch": {"campaigns": {"C1": {"target_C": 1400.0}}},
        "pins": ["campaigns.C1.target_C"],
        "recipe_schema_version": "recipe-schema-v1",
    }
    payload["effective_config"] = {
        "mass_kg": {"value": 1000.0, "source": "override"},
        "backend": {"value": "internal-analytical", "source": "default"},
    }

    artifact = build_run_artifact(payload, run_id="run-optional")

    assert artifact["header"]["seed"] == 7
    assert artifact["header"]["c3_dose"] == {"Na_kg": 1.25, "K_kg": 0.5}
    assert artifact["header"]["recipe_snapshot"] == payload["recipe_snapshot"]
    assert artifact["header"]["effective_config"] == payload["effective_config"]
    assert artifact["header"]["effective_config"] is not payload["effective_config"]
    # Deep copy, not shallow: mutating a nested source entry must not reach
    # the already-built header (stored bytes are authoritative/immutable).
    payload["effective_config"]["mass_kg"]["value"] = -1.0
    assert artifact["header"]["effective_config"]["mass_kg"]["value"] == 1000.0
    assert artifact["header"]["cost_block"]["electrical_cost_per_kWh"] == 0.15
    assert artifact["header"]["engine_identity"]["cache_version"] == "internal-analytical-cache-v1"


def test_lifecycle_argument_is_validated_and_emitted() -> None:
    payload = _runner_payload()

    cancelled = build_run_artifact(payload, run_id="run-cx", lifecycle="cancelled")
    assert cancelled["lifecycle"] == "cancelled"

    default = build_run_artifact(payload, run_id="run-def")
    assert default["lifecycle"] == "complete"

    with pytest.raises(RunArtifactContractError, match="unknown lifecycle"):
        build_run_artifact(payload, run_id="run-bad", lifecycle="aborted")


@pytest.mark.parametrize(
    ("c3_dose", "expected"),
    [
        # A single-species dose is real data — emit exactly that species,
        # without fabricating a 0.0 for the other one.
        ({"Na": 1.25}, {"Na_kg": 1.25}),
        ({"K": 0.5}, {"K_kg": 0.5}),
    ],
)
def test_partial_c3_dose_emits_present_species_only(c3_dose, expected) -> None:
    payload = _runner_payload()
    payload["run_metadata"]["c3_alkali_credit_dose_kg_by_species"] = c3_dose

    artifact = build_run_artifact(payload, run_id="run-partial-c3")

    assert artifact["header"]["c3_dose"] == expected


@pytest.mark.parametrize("c3_dose", [None, {}, {"Na": None, "K": None}])
def test_empty_c3_dose_is_omitted_without_fabricated_zero(c3_dose) -> None:
    payload = _runner_payload()
    payload["run_metadata"]["c3_alkali_credit_dose_kg_by_species"] = c3_dose

    artifact = build_run_artifact(payload, run_id="run-incomplete-c3")

    assert "c3_dose" not in artifact["header"]


@pytest.mark.parametrize("bad_patch", [None, "not-a-mapping", 7])
def test_missing_or_mistyped_setpoints_patch_omits_snapshot(bad_patch) -> None:
    # Empty {} is a truthful default-run snapshot (test below); a MISSING or
    # mistyped patch is not reconstructible and the snapshot must be omitted,
    # never fabricated.
    payload = _runner_payload()
    payload["recipe_snapshot"] = {
        "setpoints_patch": bad_patch,
        "pins": [],
        "recipe_schema_version": "recipe-schema-v1",
    }

    artifact = build_run_artifact(payload, run_id="run-bad-patch")

    assert "recipe_snapshot" not in artifact["header"]


def test_empty_setpoints_patch_yields_truthful_default_run_snapshot() -> None:
    # A default run overrides nothing: an EMPTY patch recorded as empty IS the
    # honest snapshot (omitting it would strip reproducibility from every
    # default run). Only a missing/mistyped patch disqualifies the snapshot.
    payload = _runner_payload()
    payload["recipe_snapshot"] = {
        "setpoints_patch": {},
        "pins": [],
        "recipe_schema_version": "recipe-schema-v1",
    }

    artifact = build_run_artifact(payload, run_id="run-default-snapshot")

    assert artifact["header"]["recipe_snapshot"]["setpoints_patch"] == {}
    assert (
        artifact["header"]["recipe_snapshot"]["recipe_schema_version"]
        == "recipe-schema-v1"
    )


def test_none_effective_config_is_omitted() -> None:
    payload = _runner_payload()
    payload["effective_config"] = None

    artifact = build_run_artifact(payload, run_id="run-no-effective-config")

    assert "effective_config" not in artifact["header"]


def test_cost_block_times_canonical_usage_equals_terminal_cost_totals() -> None:
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
                "energy_evaporation_thermal_kWh": 3.0,
                "energy_latent_kWh": 1.0,
                "energy_dissociation_kWh": 2.0,
            },
            {
                "hour": 2,
                "campaign": "C1",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 5.0,
                "energy_evaporation_thermal_kWh": 7.0,
                "energy_latent_kWh": 4.0,
                "energy_dissociation_kWh": 3.0,
            },
        ]
    )
    payload["run_metadata"]["cost_rollup_diagnostic"] = {
        "pumping_diagnostic": {
            "status": "ok",
            "pumping_electrical_kWh": 4.0,
        }
    }

    artifact = build_run_artifact(payload, run_id="run-cost-golden")

    cost_block = artifact["header"]["cost_block"]
    totals = artifact["terminal"]["cost_totals"]
    assert cost_block == {
        "electrical_cost_per_kWh": 0.15,
        "solar_heat_cost_per_kWh": 0.05,
        "provenance": PAYLOAD_ABSENT_COST_PROVENANCE,
    }
    assert totals["process_electrical_energy_kWh"] == pytest.approx(7.0)
    assert totals["pumping_electrical_energy_kWh"] == pytest.approx(4.0)
    assert totals["electrical_energy_kWh"] == pytest.approx(11.0)
    assert totals["process_electrical_cost_usd"] == pytest.approx(7.0 * 0.15)
    assert totals["pumping_electrical_cost_usd"] == pytest.approx(4.0 * 0.15)
    assert totals["electrical_cost_usd"] == pytest.approx(11.0 * 0.15)
    assert totals["solar_heat_cost_usd"] == pytest.approx(10.0 * 0.05)
    assert totals["total_cost_usd"] == pytest.approx(11.0 * 0.15 + 10.0 * 0.05)


def test_payload_cost_parameters_override_artifact_cost_identity() -> None:
    payload = _runner_payload(per_hour_summary=[])
    cost_parameters = default_cost_parameters_block()
    cost_parameters["parameters"]["electricity_cost_per_kWh"]["value"] = 12.0
    cost_parameters["parameters"]["solar_heat_cost_per_kWh"]["value"] = 0.07
    payload["cost_parameters"] = cost_parameters

    artifact = build_run_artifact(payload, run_id="run-cost-override")

    assert artifact["header"]["cost_block"]["electrical_cost_per_kWh"] == 12.0
    assert artifact["header"]["cost_block"]["solar_heat_cost_per_kWh"] == 0.07


def test_cost_totals_disclose_absent_pumping_basis() -> None:
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
                "energy_evaporation_thermal_kWh": 3.0,
            }
        ]
    )

    artifact = build_run_artifact(payload, run_id="run-cost-no-pumping")

    totals = artifact["terminal"]["cost_totals"]
    assert "pumping_electrical_energy_kWh" not in totals
    assert totals["basis_note"] == (
        "pumping electrical energy not emitted; electrical totals exclude pumping"
    )


@pytest.mark.parametrize(
    "status,pumping_kWh",
    [
        ("partial", 2.0),
        ("pumping_feasibility_unresolved", 4.0),
    ],
)
def test_cost_totals_exclude_nonresolved_pumping_and_name_status(
    status,
    pumping_kWh,
) -> None:
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
                "energy_evaporation_thermal_kWh": 3.0,
            }
        ]
    )
    payload["run_metadata"]["cost_rollup_diagnostic"] = {
        "pumping_diagnostic": {
            "status": status,
            "pumping_electrical_kWh": pumping_kWh,
        }
    }

    artifact = build_run_artifact(payload, run_id=f"run-cost-{status}")

    totals = artifact["terminal"]["cost_totals"]
    assert "pumping_electrical_energy_kWh" not in totals
    assert "pumping_electrical_cost_usd" not in totals
    assert totals["electrical_energy_kWh"] == pytest.approx(2.0)
    assert totals["electrical_cost_usd"] == pytest.approx(2.0 * 0.15)
    assert totals["basis_note"] == (
        f"pumping electrical energy excluded; diagnostic status={status}"
    )


def test_cost_totals_mark_refused_pumping_unavailable_instead_of_omitting() -> None:
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
                "energy_evaporation_thermal_kWh": 3.0,
            }
        ]
    )
    payload["run_metadata"]["cost_rollup_diagnostic"] = {
        "pumping_diagnostic": {
            "status": "refused",
            "reason": "missing-o2-vented-flow",
            "pumping_electrical_kWh": 0.0,
        }
    }

    artifact = build_run_artifact(payload, run_id="run-cost-refused-pumping")
    totals = artifact["terminal"]["cost_totals"]
    assert is_unavailable_quantity(totals["pumping_electrical_energy_kWh"])
    assert unavailable_reason_of(totals["pumping_electrical_energy_kWh"]) == (
        "missing-o2-vented-flow"
    )
    assert is_unavailable_quantity(totals["electrical_energy_kWh"])
    assert is_unavailable_quantity(totals["total_cost_usd"])
    assert totals["completeness"] == "incomplete"
    assert totals["process_electrical_energy_kWh"] == pytest.approx(2.0)


_REFUSED_PUMPING_HOUR_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/cost/refused_pumping_hour_canonical_totals.json"
)
_CANONICAL_UNAVAILABLE_LEAVES = (
    "pumping_electrical_energy_kWh",
    "pumping_electrical_cost_usd",
    "electrical_energy_kWh",
    "electrical_cost_usd",
    "total_cost_usd",
)


def _refused_offgas_pumping_context() -> dict:
    return {
        "status": "ok",
        "feedstock_id": "mars_basalt",
        "body": "mars",
        "ambient_pressure_pa": 610.0,
        "rows": [
            {
                "hour": 1,
                "target_pressure_pa": 500.0,
                "offgas_mol_per_s": math.nan,
                "duration_s": 3600.0,
                "gas_temperature_K": 300.0,
                "validated_line_conductance_m3_s": 1.0,
            }
        ],
    }


def _artifact_from_pumping_context(context: dict, run_id: str) -> dict:
    _cost, diagnostic = run_pumping_input_cost(context)
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
                "energy_evaporation_thermal_kWh": 3.0,
            }
        ]
    )
    payload["run_metadata"]["cost_rollup_diagnostic"] = {
        "pumping_diagnostic": diagnostic,
    }
    return build_run_artifact(payload, run_id=run_id)


def _jsonable(value):
    return json.loads(json.dumps(value))


def _json_content(value, *, indent=None):
    return json.loads(
        json.dumps(value, indent=indent, sort_keys=True, allow_nan=False)
    )


def _assert_unavailable_leaf_intact(value, *, reason: str, units: str) -> None:
    assert is_unavailable_quantity(value)
    assert value["status"] == "unavailable"
    assert value["reason"] == reason
    assert value["value"] is None
    assert value["units"] == units
    assert _json_content(value, indent=2) == _json_content(value)
    assert _json_content(value, indent=2) == {
        "reason": reason,
        "status": "unavailable",
        "units": units,
        "value": None,
    }


def _missing_o2_pumping_context() -> dict:
    return {
        "status": "refused",
        "reason": "missing-o2-vented-flow",
        "feedstock_id": "mars_basalt",
        "body": "mars",
        "ambient_pressure_pa": 610.0,
        "rows": [],
    }


_LEAF_UNITS = {
    "pumping_electrical_energy_kWh": "kWh",
    "pumping_electrical_cost_usd": "USD",
    "electrical_energy_kWh": "kWh",
    "electrical_cost_usd": "USD",
    "total_cost_usd": "USD",
}


def test_refused_pumping_hour_pins_canonical_unavailable_object() -> None:
    artifact = _artifact_from_pumping_context(
        _refused_offgas_pumping_context(),
        "run-refused-pumping-hour-fixture",
    )
    totals = artifact["terminal"]["cost_totals"]
    expected = json.loads(_REFUSED_PUMPING_HOUR_FIXTURE.read_text(encoding="utf-8"))
    pinned = expected["terminal.cost_totals"]
    observed = {key: _jsonable(totals[key]) for key in pinned}
    assert observed == pinned
    assert totals["completeness"] == "incomplete"
    assert totals["process_electrical_energy_kWh"] == pytest.approx(2.0)
    for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
        assert is_unavailable_quantity(totals[leaf])
        assert totals[leaf]["value"] is None
        assert unavailable_reason_of(totals[leaf]) == "invalid-offgas-rate"


def test_refused_pumping_hour_fixture_mutation_fails_then_restores(monkeypatch) -> None:
    from simulator import pumping_cost as pumping_mod
    from simulator.pumping_cost import SubambientPumpCost

    original = pumping_mod._infeasible_degenerate
    expected = json.loads(_REFUSED_PUMPING_HOUR_FIXTURE.read_text(encoding="utf-8"))
    pinned = expected["terminal.cost_totals"]

    def mutated(status: str) -> SubambientPumpCost:
        return SubambientPumpCost(
            status, 0.0, 0.0, math.inf, math.inf, False, status=status
        )

    monkeypatch.setattr(pumping_mod, "_infeasible_degenerate", mutated)
    with pytest.raises(AssertionError):
        billed_zero = _artifact_from_pumping_context(
            _refused_offgas_pumping_context(),
            "run-refused-pumping-hour-mutated",
        )["terminal"]["cost_totals"]
        observed = {key: _jsonable(billed_zero.get(key)) for key in pinned}
        assert observed == pinned
        assert is_unavailable_quantity(billed_zero.get("total_cost_usd"))
    monkeypatch.setattr(pumping_mod, "_infeasible_degenerate", original)
    restored = _artifact_from_pumping_context(
        _refused_offgas_pumping_context(),
        "run-refused-pumping-hour-restored",
    )["terminal"]["cost_totals"]
    observed = {key: _jsonable(restored[key]) for key in pinned}
    assert observed == pinned
    assert restored["completeness"] == "incomplete"


def test_refused_pumping_hour_round_trips_through_run_artifact_store(tmp_path) -> None:
    from web.run_store import RunArtifactStore

    artifact = _artifact_from_pumping_context(
        _refused_offgas_pumping_context(),
        "run-refused-pumping-hour-store",
    )
    produced = artifact["terminal"]["cost_totals"]
    for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
        _assert_unavailable_leaf_intact(
            produced[leaf],
            reason="invalid-offgas-rate",
            units=_LEAF_UNITS[leaf],
        )
    json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False)
    store = RunArtifactStore(tmp_path / "runs")
    assert store.save("run-refused-pumping-hour-store", artifact) is True
    loaded = store.load("run-refused-pumping-hour-store")
    assert loaded is not None
    totals = loaded["terminal"]["cost_totals"]
    expected = json.loads(_REFUSED_PUMPING_HOUR_FIXTURE.read_text(encoding="utf-8"))
    pinned = expected["terminal.cost_totals"]
    observed = {key: totals[key] for key in pinned}
    assert observed == pinned
    for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
        _assert_unavailable_leaf_intact(
            totals[leaf],
            reason="invalid-offgas-rate",
            units=_LEAF_UNITS[leaf],
        )
        assert type(totals[leaf]) is dict
        assert bool(totals[leaf]) is True


def test_unavailable_leaves_survive_indented_run_store_writer(tmp_path) -> None:
    from web.run_store import RunArtifactStore

    artifact = _artifact_from_pumping_context(
        _missing_o2_pumping_context(),
        "run-missing-o2-store",
    )
    produced = artifact["terminal"]["cost_totals"]
    for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
        _assert_unavailable_leaf_intact(
            produced[leaf],
            reason="missing-o2-vented-flow",
            units=_LEAF_UNITS[leaf],
        )
    store = RunArtifactStore(tmp_path / "runs")
    assert store.save("run-missing-o2-store", artifact) is True
    loaded = store.load("run-missing-o2-store")
    assert loaded is not None
    totals = loaded["terminal"]["cost_totals"]
    for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
        _assert_unavailable_leaf_intact(
            totals[leaf],
            reason="missing-o2-vented-flow",
            units=_LEAF_UNITS[leaf],
        )
    assert totals["completeness"] == "incomplete"


def test_unavailable_store_round_trip_falsy_bool_mutation_fails_then_restores(
    tmp_path, monkeypatch
) -> None:
    from web.run_store import RunArtifactStore

    def _round_trip(run_id: str) -> dict:
        artifact = _artifact_from_pumping_context(
            _missing_o2_pumping_context(),
            run_id,
        )
        produced = artifact["terminal"]["cost_totals"]
        for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
            _assert_unavailable_leaf_intact(
                produced[leaf],
                reason="missing-o2-vented-flow",
                units=_LEAF_UNITS[leaf],
            )
        store = RunArtifactStore(tmp_path / run_id)
        assert store.save(run_id, artifact) is True
        loaded = store.load(run_id)
        assert loaded is not None
        totals = loaded["terminal"]["cost_totals"]
        for leaf in _CANONICAL_UNAVAILABLE_LEAVES:
            _assert_unavailable_leaf_intact(
                totals[leaf],
                reason="missing-o2-vented-flow",
                units=_LEAF_UNITS[leaf],
            )
        return totals

    _round_trip("run-missing-o2-store-live")

    def falsy_bool(self) -> bool:
        return False

    monkeypatch.setattr(
        UnavailableQuantity, "__bool__", falsy_bool, raising=False
    )
    with pytest.raises(AssertionError):
        _round_trip("run-missing-o2-store-mutated")
    monkeypatch.undo()
    restored = _round_trip("run-missing-o2-store-restored")
    assert is_unavailable_quantity(restored["total_cost_usd"])
    assert restored["total_cost_usd"]["reason"] == "missing-o2-vented-flow"


def test_refused_pumping_hour_store_round_trip_mutation_fails_then_restores(
    tmp_path, monkeypatch
) -> None:
    from simulator import pumping_cost as pumping_mod
    from web.run_store import RunArtifactStore

    original = pumping_mod.SubambientPumpCost.to_json

    def mutated(self):
        payload = original(self)
        payload["required_pump_speed_m3_s"] = math.nan
        payload["compression_ratio"] = math.inf
        return payload

    monkeypatch.setattr(pumping_mod.SubambientPumpCost, "to_json", mutated)
    store = RunArtifactStore(tmp_path / "runs-mutated")
    with pytest.raises((ValueError, TypeError, OSError)):
        artifact = _artifact_from_pumping_context(
            _refused_offgas_pumping_context(),
            "run-refused-pumping-hour-mutated-store",
        )
        json.dumps(artifact, allow_nan=False)
        store.save("run-refused-pumping-hour-mutated-store", artifact)
    monkeypatch.setattr(pumping_mod.SubambientPumpCost, "to_json", original)
    restored_store = RunArtifactStore(tmp_path / "runs-restored")
    restored = _artifact_from_pumping_context(
        _refused_offgas_pumping_context(),
        "run-refused-pumping-hour-restored-store",
    )
    assert restored_store.save(
        "run-refused-pumping-hour-restored-store", restored
    ) is True
    loaded = restored_store.load("run-refused-pumping-hour-restored-store")
    assert is_unavailable_quantity(
        loaded["terminal"]["cost_totals"]["total_cost_usd"]
    )


def _below_envelope_pumping_context() -> dict:
    return {
        "status": "ok",
        "feedstock_id": "mars_basalt",
        "body": "mars",
        "ambient_pressure_pa": 610.0,
        "rows": [
            {
                "hour": 1,
                "target_pressure_pa": 0.01,
                "offgas_mol_per_s": 0.01,
                "duration_s": 3600.0,
                "gas_temperature_K": 300.0,
                "validated_line_conductance_m3_s": 1.0,
            }
        ],
    }


def _inside_envelope_pumping_context() -> dict:
    return {
        "status": "ok",
        "feedstock_id": "mars_basalt",
        "body": "mars",
        "ambient_pressure_pa": 610.0,
        "rows": [
            {
                "hour": 1,
                "target_pressure_pa": 500.0,
                "offgas_mol_per_s": 0.01,
                "duration_s": 3600.0,
                "gas_temperature_K": 300.0,
                "validated_line_conductance_m3_s": 1.0,
            }
        ],
    }


def test_below_envelope_pumping_prices_flagged_number_on_canonical_totals() -> None:
    from simulator.pumping_cost import (
        EXTRAPOLATED_AUTHORITY,
        OUT_OF_DOMAIN_PHYSICS_KIND,
        TARGET_BELOW_SPEED_FLOOR_REASON,
    )

    artifact = _artifact_from_pumping_context(
        _below_envelope_pumping_context(),
        "run-below-envelope-pumping",
    )
    totals = artifact["terminal"]["cost_totals"]
    assert totals["pumping_electrical_energy_kWh"] == pytest.approx(0.5345562989417003)
    assert totals["notice"]["kind"] == OUT_OF_DOMAIN_PHYSICS_KIND
    assert totals["notice"]["authority"] == EXTRAPOLATED_AUTHORITY
    assert totals["notice"]["reason"] == TARGET_BELOW_SPEED_FLOOR_REASON
    assert totals["notice"]["envelope_floor_pa"] == pytest.approx(0.49886775708000003)
    assert totals["electrical_energy_kWh"] == pytest.approx(2.0 + 0.5345562989417003)
    assert "completeness" not in totals
    assert not is_unavailable_quantity(totals["total_cost_usd"])


def test_inside_envelope_canonical_totals_stay_unflagged() -> None:
    artifact = _artifact_from_pumping_context(
        _inside_envelope_pumping_context(),
        "run-inside-envelope-pumping",
    )
    totals = artifact["terminal"]["cost_totals"]
    assert totals["pumping_electrical_energy_kWh"] == pytest.approx(0.008100986135507747)
    assert "notice" not in totals
    assert totals["electrical_energy_kWh"] == pytest.approx(2.0 + 0.008100986135507747)


def test_below_envelope_canonical_notice_mutation_fails_then_restores(monkeypatch) -> None:
    from simulator import pumping_cost as pumping_mod
    from simulator.pumping_cost import EXTRAPOLATED_AUTHORITY

    original = pumping_mod._extrapolated_below_speed_floor_notice

    def mutated(**_kwargs):
        return None

    monkeypatch.setattr(pumping_mod, "_extrapolated_below_speed_floor_notice", mutated)
    with pytest.raises(AssertionError):
        billed = _artifact_from_pumping_context(
            _below_envelope_pumping_context(),
            "run-below-envelope-mutated",
        )["terminal"]["cost_totals"]
        assert billed.get("notice", {}).get("authority") == EXTRAPOLATED_AUTHORITY
    monkeypatch.setattr(pumping_mod, "_extrapolated_below_speed_floor_notice", original)
    restored = _artifact_from_pumping_context(
        _below_envelope_pumping_context(),
        "run-below-envelope-restored",
    )["terminal"]["cost_totals"]
    assert restored["notice"]["authority"] == EXTRAPOLATED_AUTHORITY
    assert restored["pumping_electrical_energy_kWh"] == pytest.approx(0.5345562989417003)


def test_cost_totals_omit_when_canonical_usage_is_incomplete() -> None:
    payload = _runner_payload(
        per_hour_summary=[
            {
                "hour": 1,
                "campaign": "C0",
                "mass_balance_pct": 0.0,
                "energy_electrical_kWh": 2.0,
            }
        ]
    )

    artifact = build_run_artifact(payload, run_id="run-cost-incomplete")

    assert "cost_block" in artifact["header"]
    assert "cost_totals" not in artifact["terminal"]


def test_header_carries_reagent_dosing_as_an_input(monkeypatch) -> None:
    """additives_kg must appear in the HEADER, not only in terminal.run_metadata.

    Regression, 2026-08-28: dosing was recorded only on the output side, so two
    runs differing by 119 kg Mg / 55 kg K compared byte-identical across every
    header input field (effective_config's 645 resolved keys, recipe_snapshot,
    target_snapshot, charge_mass_kg, campaign_chain). Their headline Fe differed
    14.6x because the metallothermic shuttle needs reductant. A header that
    cannot express that difference cannot establish input equality.
    """
    payload = _runner_payload()
    payload["run_metadata"]["additives_kg"] = {"K": 55.3, "Mg": 119.0}
    artifact = build_run_artifact(payload, run_id="run-dosed")
    assert artifact["header"]["additives_kg"] == {"K": 55.3, "Mg": 119.0}


def test_header_omits_dosing_when_nothing_was_dosed(monkeypatch) -> None:
    """Absence stays absence: an empty block would assert 'dosed nothing'.

    A run whose dosing was never recorded must not be made to claim it dosed
    zero of everything -- that is a fabricated input, and it would make an
    undosed run compare equal to a genuinely zero-dosed one.
    """
    payload = _runner_payload()
    payload["run_metadata"]["additives_kg"] = {}
    artifact = build_run_artifact(payload, run_id="run-empty-dose")
    assert "additives_kg" not in artifact["header"]

    payload2 = _runner_payload()
    payload2["run_metadata"].pop("additives_kg", None)
    assert "additives_kg" not in build_run_artifact(
        payload2, run_id="run-no-dose"
    )["header"]
