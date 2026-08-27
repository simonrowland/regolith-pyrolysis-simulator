from __future__ import annotations

import copy
import math
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
    "dT_dt_C_per_hr": 50,
    "max_hold_hr": 25,
    "soft_endpoint": {
        "min_hold_hr": 10,
        "temperature_min_C": 940,
    },
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


def test_cold_wall_segment_attribution_matches_configured_geometry_values():
    session = _start_session()

    attribution = _route_cold_wall_sio_tick(session)

    # Premise: the configured 0.06 m throat radius and downstream area ratios
    # give A1 = pi*(0.06 m)^2*4.0 and A2 = pi*(0.06 m)^2*4.5; the configured
    # operating point gives SiO wall flux J = 0.062461400479484286 mol/m^2/s.
    # 2026-07-23 re-pin (stale-pin correction): 07fa3fe gated the SiO
    # reactive-product backstop at the wall deposition site (T_surface <=
    # T_cond), moving J from the 37cf443-ratified 0.062743751473665094 to the
    # value below; re-derived from the executable cold-wall path on
    # compose-0.6.3 @ 32a3a5c and verified byte-identical across repeated
    # in-process runs and fresh processes. The area-proportional attribution
    # (A2/A1 = 4.5/4.0) and the disproportionation split are unchanged.
    # Algebra: m_i = J*A_i*M_SiO*3600, so A2/A1 = m2/m1 = 4.5/4.0.
    # Unit check: (mol/m^2/s)*(m^2)*(kg/mol)*(s/h) = kg/h.
    # Sanity: the attributed total is conserved and the larger area deposits
    # more mass.
    # 2026-07-30 re-pin (t-475): wall deposition now takes its driving force
    # from the species PARTIAL pressure in the flowing gas against saturation
    # at the wall temperature, replacing the pure-component P_sat(T_melt) that
    # was not an admissible partial pressure.
    # 2026-08-04 re-pin (b-118/b-127 coating-cluster): the continuation became
    # coating-conservative and uncovered Antoine segments refuse rather than
    # invent 100 Pa. Cold-wall SiO J moves
    # 0.062205888833975716 -> the value below (+0.865 %), i.e. MORE wall flux
    # — the physically correct direction for the coating failure mode (prior
    # under-reporting path removed). Re-derived from the executable cold-wall
    # path by inverting this test's own algebra,
    # J = Si_kg/(0.5*A1*M_Si/1000*3600), and cross-checked against the SiO2
    # leg — both legs agree to 1e-12 relative. Area ratio A2/A1 = 4.5/4.0
    # unchanged. The later t-523 correction restores extrapolated inventory
    # debit without changing this coating-conservative continuation or its pin.
    sio_flux_mol_m2_s = 0.06274375148239172
    segment_areas_m2 = {
        "process.wall_deposit_segment_stage_0_to_stage_1": (
            math.pi * 0.06**2 * 4.0
        ),
        "process.wall_deposit_segment_stage_1_to_stage_2": (
            math.pi * 0.06**2 * 4.5
        ),
    }
    expected_sio_kg = {
        account: (
            sio_flux_mol_m2_s
            * area_m2
            * (MOLAR_MASS["SiO"] / 1000.0)
            * 3600.0
        )
        for account, area_m2 in segment_areas_m2.items()
    }

    assert set(attribution) == set(expected_sio_kg)
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
    ) == pytest.approx(sum(expected_sio_kg.values()))
    assert sum(attribution[
        "process.wall_deposit_segment_stage_1_to_stage_2"
    ].values()) > sum(attribution[
        "process.wall_deposit_segment_stage_0_to_stage_1"
    ].values())
    assert session.simulator.atom_ledger.kg_by_account(
        "process.wall_deposit"
    ) == {}


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
