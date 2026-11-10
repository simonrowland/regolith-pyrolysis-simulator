from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.condensation import CondensationModel
from simulator.core import CampaignPhase
from simulator.overhead import OverheadGasModel
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.runner import (
    PyrolysisRun,
    RunnerError,
    ShadowedWallTemperatureDefaultWarning,
    _canonical_runtime_campaign_overrides,
    _deep_merge_setpoints,
    _parse_runtime_campaign_overrides_json,
)
from simulator.session import SimSession, SimSessionConfig
from simulator.session_cli import _parse_setpoint_overrides
from simulator.state import CondensationTrain, MeltState


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
        "backend_name": "internal-analytical",
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


def test_pipe_segment_default_patch_preserves_wall_temperatures_for_all_campaigns(
) -> None:
    base = _load_yaml("setpoints.yaml")
    path = (
        "overhead_headspace",
        "pipe_segment_temperatures_C",
        "default_C",
    )
    segment_names = tuple(
        segment.name
        for segment in CondensationModel(
            CondensationTrain.create_default(base)
        ).pipe_segments
    )
    baseline = OverheadGasModel(base["overhead_headspace"])
    patched_models = []
    schema = RecipeSchema(setpoints=base)

    for wall_temperature_C in (1400.0, 1750.0):
        recipe_patch = schema.to_setpoints_patch(
            RecipePatch({path: wall_temperature_C})
        )
        patched_setpoints = _deep_merge_setpoints(base, recipe_patch)
        patched_models.append(
            OverheadGasModel(patched_setpoints["overhead_headspace"])
        )

    campaigns = tuple(
        campaign
        for campaign in CampaignPhase
        if campaign not in {CampaignPhase.IDLE, CampaignPhase.COMPLETE}
    )
    for campaign in campaigns:
        for hour in range(25):
            melt = MeltState(
                campaign=campaign,
                campaign_hour=float(hour),
                hour=float(hour),
            )
            expected_liner = baseline.resolve_pipe_temperature_C(melt)
            expected = baseline.resolve_pipe_segment_temperatures_C(
                segment_names,
                melt,
            )
            for model in patched_models:
                assert model.resolve_pipe_temperature_C(melt) == expected_liner
                assert model.resolve_pipe_segment_temperatures_C(
                    segment_names,
                    melt,
                ) == expected


def test_shadowed_pipe_segment_default_warns_with_schedule_details() -> None:
    base = _load_yaml("setpoints.yaml")
    path = (
        "overhead_headspace",
        "pipe_segment_temperatures_C",
        "default_C",
    )
    patch = RecipeSchema(setpoints=base).to_setpoints_patch(
        RecipePatch({path: 1400.0})
    )
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A_staged",
        setpoints_patch=patch,
    )

    with pytest.warns(ShadowedWallTemperatureDefaultWarning) as captured:
        run._session_config()

    notice = captured[0].message
    assert notice.code == "overhead_wall_temperature_default_shadowed"
    assert "overhead_headspace.pipe_segment_temperatures_C.default_C" in str(
        notice
    )
    assert "C2A_STAGED" in str(notice)
    assert all(
        f"stage_{stage}_to_stage_{stage + 1}" in str(notice)
        for stage in range(7)
    )


def test_shadowed_pipe_segment_default_does_not_warn_without_campaign_schedule(
) -> None:
    base = _load_yaml("setpoints.yaml")
    path = (
        "overhead_headspace",
        "pipe_segment_temperatures_C",
        "default_C",
    )
    patch = RecipeSchema(setpoints=base).to_setpoints_patch(
        RecipePatch({path: 1400.0})
    )
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C0",
        setpoints_patch=patch,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", ShadowedWallTemperatureDefaultWarning)
        run._session_config()


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


def test_loaded_recipe_patch_without_explicit_override_uses_recipe_config() -> None:
    setpoints_patch = {
        "campaigns": {
            "C4": {
                "pO2_mbar": 0.1,
                "p_total_mbar_default": 0.1,
                "temp_range_C": [1500.0, 1550.0],
            }
        }
    }
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C4",
        setpoints_patch=setpoints_patch,
    )

    config = run._session_config()
    session = SimSession().start(config)
    c4_config = session.simulator.campaign_mgr._campaign_config(CampaignPhase.C4)

    assert config.runtime_campaign_overrides == {}
    assert session.simulator.campaign_mgr.overrides.get("C4", {}) == {}
    assert c4_config["pO2_mbar"] == pytest.approx(0.1)
    assert session.simulator.melt.pO2_mbar == pytest.approx(0.1)
    assert c4_config["p_total_mbar_default"] == pytest.approx(0.1)
    assert c4_config["temp_range_C"] == pytest.approx([1500.0, 1550.0])


def test_session_merged_recipe_patch_has_no_implicit_override_layer() -> None:
    setpoints_patch = {
        "campaigns": {
            "C4": {
                "pO2_mbar_default": 0.12,
                "p_total_mbar_default": 0.12,
            }
        }
    }
    config = _session_config(
        campaign="C4",
        setpoints=_deep_merge_setpoints(_load_yaml("setpoints.yaml"), setpoints_patch),
    )

    session = SimSession().start(config)
    c4_config = session.simulator.campaign_mgr._campaign_config(CampaignPhase.C4)

    assert config.runtime_campaign_overrides == {}
    assert session.simulator.campaign_mgr.overrides.get("C4", {}) == {}
    assert c4_config["pO2_mbar_default"] == pytest.approx(0.12)


def test_loaded_recipe_patch_runtime_po2_above_total_refuses() -> None:
    run = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C4",
        setpoints_patch={
            "campaigns": {
                "C4": {
                    "pO2_mbar_default": 0.1,
                    "p_total_mbar_default": 0.1,
                }
            }
        },
        runtime_campaign_overrides={"C4": {"pO2_mbar": 0.3}},
    )

    config = run._session_config()
    assert config.setpoints["campaigns"]["C4"]["p_total_mbar_default"] == pytest.approx(
        0.1
    )

    with pytest.raises(ValueError, match="melt_pressure_partial_exceeds_total"):
        SimSession().start(config)


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
