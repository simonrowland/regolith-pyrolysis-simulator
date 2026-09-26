"""C2 IMCC melt-activity shadow: opt-in, typed, and physics-neutral."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace

import pytest

import engines.builtin.vapor_pressure as builtin_vapor_pressure
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun
from simulator.state import HourSnapshot
from simulator.vapour_rail.melt_activity_resolver import (
    IMCC_ACTIVITY_SHADOW_FLUX_OXIDES,
    build_imcc_activity_shadow,
)


COMPOSITION_MOL = {
    "Na2O": 1.0,
    "K2O": 1.0,
    "SiO2": 45.0,
    "FeO": 10.0,
    "MgO": 20.0,
    "CaO": 10.0,
    "Al2O3": 10.0,
    "TiO2": 3.0,
}


def _lunar_run(*, imcc_activity_shadow: bool | None = None) -> dict:
    kwargs = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C2A",
        "hours": 1,
        "allow_fallback_vapor": True,
        "sio_start_temperature_c": 1800.0,
        "sio_hold_temperature_c": 1800.0,
        "sio_ramp_c_per_hr": 0.0,
        "run_metadata_overrides": {
            "started_at_utc": "2026-09-26T00:00:00Z",
            "kernel_commit_sha": "c2-shadow-test",
        },
    }
    if imcc_activity_shadow is not None:
        kwargs["imcc_activity_shadow"] = imcc_activity_shadow
    return PyrolysisRun(**kwargs).run()


@pytest.fixture(scope="module")
def lunar_runs() -> dict[str, dict]:
    return {
        "base": _lunar_run(),
        "off": _lunar_run(imcc_activity_shadow=False),
        "on": _lunar_run(imcc_activity_shadow=True),
    }


def _physics_projection(payload: dict) -> dict:
    rows = []
    for row in payload["per_hour_summary"]:
        projected = copy.deepcopy(row)
        projected.pop("imcc_activity_shadow", None)
        rows.append(projected)
    return {
        "final_state": copy.deepcopy(payload["final_state"]),
        "final": copy.deepcopy(payload["final"]),
        "yield_disposition": copy.deepcopy(payload["yield_disposition"]),
        "per_hour_summary": rows,
    }


def test_option_off_is_bit_identical_to_base(lunar_runs: dict[str, dict]) -> None:
    assert lunar_runs["off"] == lunar_runs["base"]
    assert all(
        "imcc_activity_shadow" not in row
        for row in lunar_runs["off"]["per_hour_summary"]
    )


def test_recipe_option_is_explicit_and_default_off() -> None:
    base = PyrolysisRun(feedstock_id="lunar_mare_low_ti")._session_config()
    enabled = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        setpoints_patch={"imcc_activity_shadow": True},
    )._session_config()

    assert base.setpoints.get("imcc_activity_shadow", False) is False
    assert enabled.setpoints["imcc_activity_shadow"] is True


def test_option_on_adds_schema_and_keeps_flux_and_ledgers_identical(
    lunar_runs: dict[str, dict],
) -> None:
    base = lunar_runs["base"]
    shadowed = lunar_runs["on"]
    row = shadowed["per_hour_summary"][0]
    shadow = row["imcc_activity_shadow"]

    assert shadow["schema"] == "imcc_activity_shadow.v1"
    assert shadow["behavior_authority"] is False
    assert shadow["temperature_K"] > 1950.0
    assert shadow["above_vaporock_cap"] is True
    assert set(shadow["activities_by_oxide"]) == set(IMCC_ACTIVITY_SHADOW_FLUX_OXIDES)
    assert set(shadow["flux_oxides"]) == set(IMCC_ACTIVITY_SHADOW_FLUX_OXIDES)
    assert "flags" in shadow
    assert "notices" in shadow
    assert "openimcc_version" in shadow
    assert "pack_digest" in shadow
    for oxide in IMCC_ACTIVITY_SHADOW_FLUX_OXIDES:
        oxide_row = shadow["activities_by_oxide"][oxide]
        assert {
            "imcc_activity",
            "constant_gamma_activity",
            "ratio",
        } <= set(oxide_row)

    assert _physics_projection(shadowed) == _physics_projection(base)


def test_out_of_domain_is_typed_and_does_not_extrapolate() -> None:
    shadow = build_imcc_activity_shadow(
        composition_mol=COMPOSITION_MOL,
        temperature_K=1600.0,
    )

    assert shadow["status"] == "out_of_imcc_domain"
    assert shadow["reason"] == "out of IMCC domain"
    assert shadow["reason_code"] == "imcc_shadow_out_of_domain"
    assert "no extrapolation" in shadow["refusal"]["detail"]
    assert all(
        row["imcc_activity"] is None
        for row in shadow["activities_by_oxide"].values()
    )


def test_in_domain_shadow_carries_imcc_provenance_and_activity_rows() -> None:
    shadow = build_imcc_activity_shadow(
        composition_mol=COMPOSITION_MOL,
        temperature_K=2200.0,
    )

    assert shadow["status"] == "ok"
    assert shadow["openimcc_version"]
    assert shadow["pack_digest"]
    assert isinstance(shadow["flags"], list)
    assert isinstance(shadow["notices"], list)
    assert all(
        row["imcc_activity"] is not None
        for row in shadow["activities_by_oxide"].values()
    )


def test_missing_openimcc_is_typed_refusal_and_not_a_run_failure(monkeypatch) -> None:
    import simulator.melt_backend.imcc_sf04.openimcc_bridge as bridge

    monkeypatch.setattr(bridge, "_openimcc", None)
    monkeypatch.setattr(
        bridge,
        "_OPENIMCC_IMPORT_ERROR",
        ImportError("test-only unavailable"),
    )
    shadow = build_imcc_activity_shadow(
        composition_mol=COMPOSITION_MOL,
        temperature_K=2200.0,
    )

    assert shadow["status"] == "refused"
    assert shadow["reason_code"] == "openimcc_not_importable"
    assert shadow["refusal"]["code"] == "openimcc_not_importable"
    assert shadow["above_vaporock_cap"] is True
    payload = _lunar_run(imcc_activity_shadow=True)
    assert payload["status"] == "ok"
    assert (
        payload["per_hour_summary"][0]["imcc_activity_shadow"]["reason_code"]
        == "openimcc_not_importable"
    )


def test_option_on_hour_snapshot_is_declared_schema_field() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        allow_fallback_vapor=True,
        sio_start_temperature_c=1800.0,
        sio_hold_temperature_c=1800.0,
        sio_ramp_c_per_hr=0.0,
        imcc_activity_shadow=True,
        run_metadata_overrides={
            "started_at_utc": "2026-09-26T00:00:00Z",
            "kernel_commit_sha": "c2-shadow-test",
        },
    )
    session = run._start_session()
    run._apply_sio_pre_run_controls(session.simulator)
    execution = RunExecutor().execute_session(session, hours=1)

    snapshot = execution.snapshots[0]
    snapshot_payload = asdict(snapshot)
    assert isinstance(snapshot, HourSnapshot)
    assert snapshot_payload["imcc_activity_shadow"]
    assert snapshot_payload["imcc_activity_shadow"]["schema"] == (
        "imcc_activity_shadow.v1"
    )


def test_strict_json_refuses_nan_shadow_temperature() -> None:
    payload = build_imcc_activity_shadow(
        composition_mol=COMPOSITION_MOL,
        temperature_K=float("nan"),
    )

    assert payload["status"] == "refused"
    assert payload["reason_code"] == "imcc_shadow_invalid_temperature"
    assert payload["temperature_K"] is None
    json.dumps(payload, allow_nan=False)


def test_flux_unchanged_invariant_rejects_shadow_feed_mutation(monkeypatch) -> None:
    shadow = build_imcc_activity_shadow(
        composition_mol=COMPOSITION_MOL,
        temperature_K=2073.15,
    )
    shadow_activity = shadow["activities_by_oxide"]["K2O"][
        "imcc_single_cation_activity"
    ]
    assert shadow_activity is not None

    baseline = _lunar_run(imcc_activity_shadow=False)
    original_melt_oxide_activity = builtin_vapor_pressure.melt_oxide_activity

    def feed_shadow_activity(parent_oxide, *args, **kwargs):
        result = original_melt_oxide_activity(parent_oxide, *args, **kwargs)
        if parent_oxide == "K2O" and result is not None:
            return replace(result, activity=shadow_activity)
        return result

    monkeypatch.setattr(
        builtin_vapor_pressure,
        "melt_oxide_activity",
        feed_shadow_activity,
    )
    mutated = _lunar_run(imcc_activity_shadow=False)

    with pytest.raises(AssertionError):
        assert mutated["per_hour_summary"][0]["vapor_species_kg_hr"] == (
            baseline["per_hour_summary"][0]["vapor_species_kg_hr"]
        )
