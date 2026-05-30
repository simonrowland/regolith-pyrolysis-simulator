from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.runner import (
    PyrolysisRun,
    RunnerError,
    _canonical_runtime_campaign_overrides,
    _parse_runtime_campaign_overrides_json,
)
from simulator.session import SimSession, SimSessionConfig
from simulator.session_cli import _parse_setpoint_overrides


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDSTOCK = "lunar_mare_low_ti"


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def _session_config(**overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": FEEDSTOCK,
        "feedstocks": _load_yaml("feedstocks.yaml"),
        "setpoints": _load_yaml("setpoints.yaml"),
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C0",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


def test_setpoints_patch_deep_merges_without_mutating_base() -> None:
    base = PyrolysisRun(feedstock_id=FEEDSTOCK)._session_config()
    base_c2a = base.setpoints["campaigns"]["C2A_continuous"]
    base_c2b = base.setpoints["campaigns"]["C2B"]

    patched = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch={
            "campaigns": {
                "C2A_continuous": {
                    "dT_dt_C_per_hr": {
                        "peak_SiO_window_1400_1600C": [1.0, 2.0],
                    },
                    "phase_r_added_key": 7.0,
                }
            }
        },
    )._session_config()

    patched_c2a = patched.setpoints["campaigns"]["C2A_continuous"]
    assert patched_c2a["dT_dt_C_per_hr"]["peak_SiO_window_1400_1600C"] == [
        1.0,
        2.0,
    ]
    assert patched_c2a["dT_dt_C_per_hr"]["early_ramp_1050_1320C"] == (
        base_c2a["dT_dt_C_per_hr"]["early_ramp_1050_1320C"]
    )
    assert patched_c2a["phase_r_added_key"] == 7.0
    assert patched.setpoints["campaigns"]["C2B"] == base_c2b
    assert "phase_r_added_key" not in base_c2a


def test_setpoints_patch_fallback_and_runtime_override_precedence() -> None:
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A",
        setpoints_patch={
            "campaigns": {
                "C2A_continuous": {
                    "p_total_mbar_default": 123.0,
                }
            }
        },
        allow_fallback_vapor=True,
        runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
    )

    config = run._session_config()

    assert (
        config.setpoints["campaigns"]["C2A_continuous"][
            "p_total_mbar_default"
        ]
        == 123.0
    )
    assert config.setpoints["chemistry_kernel"]["allow_fallback_vapor"] is True
    assert config.runtime_campaign_overrides == {"C2A": {"p_total_mbar": 9.0}}

    session = SimSession().start(config)

    assert session.simulator.campaign_mgr.overrides["C2A"] == {
        "p_total_mbar": 9.0,
    }


def test_setpoints_patch_rejects_chemistry_kernel() -> None:
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch={"chemistry_kernel": {"allow_fallback_vapor": True}},
    )

    with pytest.raises(RunnerError, match="setpoints_patch.*chemistry_kernel"):
        run._session_config()


def test_pyrolysis_run_override_alias_conflict_and_compat() -> None:
    with pytest.raises(ValueError, match="runtime_campaign_overrides conflicts"):
        PyrolysisRun(
            feedstock_id=FEEDSTOCK,
            runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
            setpoints_overrides={"C2A": {"p_total_mbar": 10.0}},
        )

    equal = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
        setpoints_overrides={"C2A": {"p_total_mbar": 9.0}},
    )
    legacy = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_overrides={"C2A": {"p_total_mbar": 9.0}},
    )

    assert equal.runtime_campaign_overrides == {"C2A": {"p_total_mbar": 9.0}}
    assert legacy._session_config().runtime_campaign_overrides == {
        "C2A": {"p_total_mbar": 9.0},
    }


def test_sim_session_config_override_alias_conflict_and_compat() -> None:
    with pytest.raises(ValueError, match="runtime_campaign_overrides conflicts"):
        _session_config(
            runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
            setpoints_overrides={"C2A": {"p_total_mbar": 10.0}},
        )

    equal = _session_config(
        runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
        setpoints_overrides={"C2A": {"p_total_mbar": 9.0}},
    )
    legacy = _session_config(
        setpoints_overrides={"C2A": {"p_total_mbar": 9.0}},
    )

    assert equal.runtime_campaign_overrides == {"C2A": {"p_total_mbar": 9.0}}
    assert legacy.runtime_campaign_overrides == {"C2A": {"p_total_mbar": 9.0}}


def test_runner_cli_override_alias_parse_and_conflict() -> None:
    legacy = _parse_runtime_campaign_overrides_json(
        '{"C2A": {"p_total_mbar": 9.0}}',
        flag_name="--setpoints-overrides",
    )
    runtime = _parse_runtime_campaign_overrides_json(
        '{"C2A": {"p_total_mbar": 9.0}}',
        flag_name="--runtime-campaign-overrides",
    )
    assert _canonical_runtime_campaign_overrides(
        runtime_campaign_overrides=runtime,
        setpoints_overrides=legacy,
    ) == {"C2A": {"p_total_mbar": 9.0}}

    with pytest.raises(ValueError, match="runtime_campaign_overrides conflicts"):
        _canonical_runtime_campaign_overrides(
            runtime_campaign_overrides={"C2A": {"p_total_mbar": 9.0}},
            setpoints_overrides={"C2A": {"p_total_mbar": 10.0}},
        )


def test_session_cli_override_alias_parse_and_conflict() -> None:
    legacy = '{"C2A": {"p_total_mbar": 9.0}}'
    runtime = '{"C2A": {"p_total_mbar": 9.0}}'

    assert _parse_setpoint_overrides([], legacy, runtime) == {
        "C2A": {"p_total_mbar": 9.0}
    }
    assert _parse_setpoint_overrides(["C2A.p_total_mbar=8.0"], None, runtime) == {
        "C2A": {"p_total_mbar": 8.0}
    }

    with pytest.raises(ValueError, match="runtime-campaign-overrides conflicts"):
        _parse_setpoint_overrides(
            [],
            '{"C2A": {"p_total_mbar": 9.0}}',
            '{"C2A": {"p_total_mbar": 10.0}}',
        )
