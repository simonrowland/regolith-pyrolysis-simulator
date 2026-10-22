from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.state import (
    EvaporationFlux,
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
)


MULTI_TICK_COUNT = 4
C0_ENDPOINT_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 200,
    "max_hold_hr": 25,
    "carrier_gas": "Ar",
    "p_total_mbar_default": 1.0,
    "pO2_mbar_default": 0.0,
    "alkali_retention_budget_fraction": 1.0e-3,
}
_ROOT = Path(__file__).resolve().parents[1]


def _silica_feedstocks() -> dict:
    return {
        "silica": {
            "label": "Silica",
            "composition_wt_pct": {"SiO2": 100.0},
        },
    }


def _silica_vapor_pressures() -> dict:
    # The forced SiO EvaporationFlux path under test is independent of melt
    # evaporation channels. Keep the production catalog because the direct
    # condensation route consumes its wall-pressure coverage; no warm-up
    # advance is needed to exercise wall attribution.
    return yaml.safe_load((_ROOT / "data" / "vapor_pressures.yaml").read_text())


def _setpoints() -> dict:
    return {
        "campaigns": {"C0": copy.deepcopy(C0_ENDPOINT_SETPOINTS)},
        "chemistry_kernel": {"allow_unmeasured_alpha_fallback": True},
    }


def _start_session(
    feedstocks: dict | None = None,
    setpoints: dict | None = None,
    vapor_pressures: dict | None = None,
) -> SimSession:
    return SimSession().start(
        SimSessionConfig(
            feedstock_id="silica",
            feedstocks=feedstocks or _silica_feedstocks(),
            setpoints=setpoints or _setpoints(),
            vapor_pressures=vapor_pressures or _silica_vapor_pressures(),
            campaign="C0",
            backend_name="internal-analytical",
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
        )
    )


def _wall_attribution(session: SimSession) -> dict[str, dict[str, float]]:
    sim = session.simulator
    kg_by_account = sim.atom_ledger.kg_by_account
    return {
        account: dict(kg_by_account(account))
        for account in PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS
        if kg_by_account(account)
    }


def _route_cold_wall_sio_tick(session: SimSession) -> dict[str, dict[str, float]]:
    sim = session.simulator
    model = sim.condensation_model
    model.configure_operating_conditions(
        wall_temperature_C=900.0,
        pipe_segment_temperatures_C={
            segment.name: 900.0 for segment in model.pipe_segments
        },
    )
    # Force a cold-wall operating point only for the routed tick. Restore the
    # pre-route melt temperature so a subsequent session.advance() does not
    # inherit 1700 C and evaporate pure-SiO2 under the schema-v2 catalogue
    # (that path trips cleaned_melt projection staleness on multi-tick runs).
    prior_melt_C = float(sim.melt.temperature_C)
    sim.melt.temperature_C = 1700.0
    try:
        sim._route_to_condensation(
            EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)
        )
        return _wall_attribution(session)
    finally:
        sim.melt.temperature_C = prior_melt_C


def _multi_tick_trace(
    feedstocks: dict,
    setpoints: dict,
    vapor_pressures: dict,
) -> list[dict]:
    session = _start_session(feedstocks, setpoints, vapor_pressures)
    trace = []
    for tick in range(MULTI_TICK_COUNT):
        _route_cold_wall_sio_tick(session)
        snapshot = session.snapshot()
        trace.append(
            {
                "tick": tick,
                "wall_accounts": _wall_attribution(session),
                "mass_balance_error_pct": snapshot.mass_balance_error_pct,
            }
        )
    return trace


def _has_runtime_wall_keys(mapping: dict) -> bool:
    if isinstance(mapping, dict):
        for key, value in mapping.items():
            if str(key).startswith("_wall_deposit"):
                return True
            if _has_runtime_wall_keys(value):
                return True
    return False


@pytest.mark.parametrize(
    ("p_total_mbar", "expect_vacuum_area_split"),
    [(1.0, False), (0.0, True)],
    ids=["owner-cover", "vacuum"],
)
def test_cold_wall_segment_attribution_matches_configured_geometry_values(
    p_total_mbar, expect_vacuum_area_split, monkeypatch
):
    setpoints = _setpoints()
    setpoints["campaigns"]["C0"]["p_total_mbar_default"] = p_total_mbar
    session = _start_session(setpoints=setpoints)
    model = session.simulator.condensation_model
    route_owner = model.route
    route_results = []

    def _record_route_owner(*args, **kwargs):
        result = route_owner(*args, **kwargs)
        route_results.append(result)
        return result

    monkeypatch.setattr(model, "route", _record_route_owner)
    attribution = _route_cold_wall_sio_tick(session)

    assert len(route_results) == 1
    route_result = route_results[0]
    sio_wall_kg = float(route_result.wall_deposit_by_species["SiO"])
    sio_fractions_by_account = (
        route_result.wall_deposit_account_fractions_by_species["SiO"]
    )
    expected_sio_kg = {
        account: sio_wall_kg * float(fraction)
        for account, fraction in sio_fractions_by_account.items()
    }
    expected_accounts = set(expected_sio_kg)

    assert set(attribution) == expected_accounts
    for account, sio_kg in expected_sio_kg.items():
        species_kg = attribution[account]
        assert species_kg == {
            "Si": pytest.approx(
                sio_kg * 0.5 * MOLAR_MASS["Si"] / MOLAR_MASS["SiO"]
            ),
            "SiO2": pytest.approx(
                sio_kg * 0.5 * MOLAR_MASS["SiO2"] / MOLAR_MASS["SiO"]
            ),
        }
    assert sum(
        sum(species_kg.values()) for species_kg in attribution.values()
    ) == pytest.approx(sio_wall_kg)
    assert sum(attribution[
        "process.wall_deposit_segment_stage_1_to_stage_2"
    ].values()) > sum(attribution[
        "process.wall_deposit_segment_stage_0_to_stage_1"
    ].values())
    if expect_vacuum_area_split:
        downstream = sum(attribution[
            "process.wall_deposit_segment_stage_1_to_stage_2"
        ].values())
        upstream = sum(attribution[
            "process.wall_deposit_segment_stage_0_to_stage_1"
        ].values())
        assert downstream / upstream == pytest.approx(4.5 / 4.0)

    stage_capture_kg = sum(session.simulator.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).values())
    assert stage_capture_kg > 0.0
    assert session.simulator.atom_ledger.kg_by_account(
        "process.wall_deposit"
    ) == {}
    overhead_kg = session.simulator.atom_ledger.kg_by_account("process.overhead_gas").get("SiO", 0.0)
    assert stage_capture_kg + sio_wall_kg + overhead_kg == pytest.approx(1.0)


def test_repeated_in_process_runs_keep_wall_attribution_deterministic():
    feedstocks = _silica_feedstocks()
    setpoints = _setpoints()
    vapor_pressures = _silica_vapor_pressures()

    first_run = _multi_tick_trace(feedstocks, setpoints, vapor_pressures)
    second_run = _multi_tick_trace(feedstocks, setpoints, vapor_pressures)

    assert first_run == second_run
    assert not _has_runtime_wall_keys(vapor_pressures)
    assert not _has_runtime_wall_keys(setpoints)
    assert not _has_runtime_wall_keys(feedstocks)


def test_parallel_simsessions_isolate_shared_wall_route_inputs():
    feedstocks = _silica_feedstocks()
    setpoints = _setpoints()
    vapor_pressures = _silica_vapor_pressures()
    source_snapshots = {
        "feedstocks": copy.deepcopy(feedstocks),
        "setpoints": copy.deepcopy(setpoints),
        "vapor_pressures": copy.deepcopy(vapor_pressures),
    }

    left = _start_session(feedstocks, setpoints, vapor_pressures)
    right = _start_session(feedstocks, setpoints, vapor_pressures)

    left_attribution = _route_cold_wall_sio_tick(left)
    assert _wall_attribution(right) == {}
    right_attribution = _route_cold_wall_sio_tick(right)

    assert left_attribution == right_attribution
    assert feedstocks == source_snapshots["feedstocks"]
    assert setpoints == source_snapshots["setpoints"]
    assert vapor_pressures == source_snapshots["vapor_pressures"]
