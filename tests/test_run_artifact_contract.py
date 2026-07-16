from __future__ import annotations

import pytest

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
}
OPTIONAL_HEADER_KEYS = {
    "recipe_snapshot",
    "seed",
    "c3_dose",
    "cost_block",
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
OPTIONAL_TERMINAL_KEYS = {"confidence"}


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
            "backend": "stub",
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
        "name": "stub",
        "cache_version": None,
        "backend_wire_token": "stub",
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
        "backend": {"value": "stub", "source": "default"},
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
    assert "cost_block" not in artifact["header"]
    assert artifact["header"]["engine_identity"]["cache_version"] == "stub-cache-v1"


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
