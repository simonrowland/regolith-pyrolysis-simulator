from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import yaml
import pytest

from simulator.chemistry.kernel import (
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    OXYGEN_SINK_CHANNEL_MODE_VALUES,
)
from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C
from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR,
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH,
    C2A_STAGED_ORDER_PATH,
    C5_ALLOW_MRE_VOLTAGE_CAP_PATH,
    C4_HOLD_TEMP_C_PATH,
    FURNACE_MAX_T_C_PATH,
    KnobSpec,
    RecipePatch,
    RecipePinWarning,
    RecipeSchema,
    RecipeValidationError,
    STAGE0_CARBON_REDUCTANT_KG_PATH,
    STAGE0_REDOX_OXIDANT_KG_PATH,
)
import simulator.optimize.recipe as recipe_module
from simulator.optimize.canonical import canonical_json_dumps
from simulator.optimize.evaluate import _build_eval_inputs
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
from simulator.campaigns import CampaignManager
from simulator.core import CampaignPhase
from simulator.runner import PyrolysisRun
from simulator.session import SimSession
from simulator.state import BatchRecord, CondensationTrain, EvaporationFlux, MeltState


FEEDSTOCK = "lunar_mare_low_ti"
PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")
PTOTAL_DEFAULT = ("campaigns", "C0b_p_cleanup", "p_total_mbar_default")
C3_PO2_DEFAULT = ("campaigns", "C3", "pO2_mbar_default")
C3_PTOTAL_DEFAULT = ("campaigns", "C3", "p_total_mbar_default")
PRODUCT_TARGET = ("campaigns", "C0b_p_cleanup", "products", "oxygen_kg")
OXYGEN_SINK_CHANNEL_MODE = ("chemistry_kernel", OXYGEN_SINK_CHANNEL_MODE_KEY)
SETPOINTS_PATH = Path(__file__).resolve().parents[1] / "data" / "setpoints.yaml"
STAGE_SIO_TARGET = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "target_C",
)
STAGE_FE_DURATION = (
    "campaigns",
    "C2A_staged",
    "stages",
    "fe_hot_hold",
    "duration_h",
)
STAGE_COOL_RAMP = (
    "campaigns",
    "C2A_staged",
    "stages",
    "cool_for_na_shuttle",
    "ramp_rate_C_per_hr",
)
STAGE_SIO_PO2 = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "pO2_mbar",
)
STAGE_SIO_PTOTAL = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "p_total_mbar",
)
STAGE_SIO_GAS_MODE = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "gas_cover_mode",
)
C2A_ORDER = C2A_STAGED_ORDER_PATH
DATA_DIGESTS = {
    "feedstocks": "feedstocks-digest",
    "foulant_thermo": "foulant-thermo-digest",
    "materials": "materials-digest",
    "profile": "profile-digest",
    "setpoints": "setpoints-digest",
    "species_catalog": "species-catalog-digest",
    "vapor_pressures": "vapor-pressures-digest",
}


def _lookup_setpoint(root: dict, dotted_path: str):
    node = root
    for segment in dotted_path.split("."):
        node = node[segment]
    return node


def _stage_by_name(stages: list[dict], name: str) -> dict:
    return next(stage for stage in stages if stage["name"] == name)


def _c2a_staged_setpoints(fraction: float | None = None) -> dict:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    setpoints = copy.deepcopy(setpoints)
    c2a = setpoints["campaigns"]["C2A_staged"]
    if fraction is None:
        c2a.pop("depletion_flux_decay_fraction", None)
    else:
        c2a["depletion_flux_decay_fraction"] = fraction
    return setpoints


def _write_c5_mre_cap_bound_yaml(data_dir: Path, high: float, mtime_ns: int) -> None:
    path = data_dir / "setpoints.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "campaigns": {
                    "C5": {
                        "allow_mre_voltage_cap_upper_bound_V": high,
                    }
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _configured_c2a_staged_manager(fraction: float | None = None) -> CampaignManager:
    manager = CampaignManager(_c2a_staged_setpoints(fraction))
    manager.configure_campaign(
        MeltState(campaign=CampaignPhase.C2A_STAGED),
        CampaignPhase.C2A_STAGED,
    )
    return manager


def _flux(**species_kg_hr: float) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=dict(species_kg_hr))
    flux.update_totals()
    return flux


def _check_c2a_staged_endpoint(
    manager: CampaignManager,
    hour: int,
    flux: EvaporationFlux,
) -> bool:
    return manager.check_endpoint(
        MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=hour),
        flux,
        CondensationTrain.create_default(),
        BatchRecord(),
    )


def test_unknown_setpoint_path_is_denied_by_default() -> None:
    patch = RecipePatch({("campaigns", "C0", "label"): "retuned"})

    with pytest.raises(RecipeValidationError, match="unknown recipe path"):
        patch.validated()


def test_forbidden_prefixes_are_hard_errors() -> None:
    forbidden = [
        ("chemistry_kernel", "allow_fallback_vapor"),
        PRODUCT_TARGET,
        ("mass_balance", "gap_pct"),
    ]

    for path in forbidden:
        with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
            RecipePatch({path: 1.0}).validated()


def test_forbidden_prefix_wins_over_overlapping_allowlist() -> None:
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=PRODUCT_TARGET,
                kind="float",
                low=0.0,
                high=10.0,
                bounds_source="test",
            ),
        )
    )

    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipePatch({PRODUCT_TARGET: 1.0}).validated(schema)


def test_pinned_c2a_temperature_targets_leave_other_knobs_searchable() -> None:
    target_paths = (
        ("campaigns", "C2A_staged", "stages", "alkali_early_fe", "target_C"),
        ("campaigns", "C2A_staged", "stages", "sio_window", "target_C"),
        ("campaigns", "C2A_staged", "stages", "cool_for_na_shuttle", "target_C"),
    )
    schema = RecipeSchema(
        pinned_paths=[
            "C2A_staged.stages.alkali_early_fe.target_C",
            "C2A_staged.stages.sio_window.target_C",
            "C2A_staged.stages.cool_for_na_shuttle.target_C",
        ]
    )
    search_paths = {spec.path for spec in schema.search_allowlist}

    for path in target_paths:
        assert path not in search_paths
        spec = schema.spec_for(path)
        assert spec.search_enabled is False
        assert spec.runtime_enabled is True
    assert ("campaigns", "C2A_staged", "p_total_mbar") in search_paths
    assert (
        "campaigns",
        "C2A_staged",
        "stages",
        "alkali_early_fe",
        "ramp_rate_C_per_hr",
    ) in search_paths
    assert (
        "campaigns",
        "C2A_staged",
        "stages",
        "sio_window",
        "duration_h",
    ) in search_paths
    assert ("campaigns", "C2B", "pO2_mbar") in search_paths
    assert (
        "campaigns",
        "C2A_staged",
        "stages",
        "fe_hot_hold",
        "target_C",
    ) not in {spec.path for spec in schema.allowlist}


def test_pin_unknown_or_forbidden_path_fails_loudly() -> None:
    with pytest.raises(RecipeValidationError, match="pin path matches no optimizer knob"):
        RecipeSchema(pinned_paths=["C2A_staged.stages.sio_window.not_a_knob"])

    with pytest.raises(RecipeValidationError, match="forbidden recipe pin path"):
        RecipeSchema(pinned_paths=["mass_balance.gap_pct"])


def test_pin_already_fixed_fe_hot_hold_target_warns_without_changing_search() -> None:
    baseline = RecipeSchema()
    baseline_paths = tuple(spec.path for spec in baseline.search_allowlist)

    with pytest.warns(RecipePinWarning, match="already fixed.*fe_hot_hold.*target_C"):
        schema = RecipeSchema(pinned_paths=["C2A_staged.stages.fe_hot_hold.target_C"])

    assert tuple(spec.path for spec in schema.search_allowlist) == baseline_paths


def test_no_pin_schema_is_golden_neutral_for_search_and_evalspec_hash() -> None:
    profile = {
        "profile_id": "pin-golden-neutral-test",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": FEEDSTOCK,
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
                "rationale": "test oxygen objective evidence",
            },
        ],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "run": {
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
        },
        "fidelities": {"stub": {"backend_name": "stub", "hours": 1}},
        "seed_recipes": [
            {
                "id": "seed",
                "source_campaign": "C0",
                "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
            },
        ],
    }
    schema = RecipeSchema()
    unpinned = schema.with_pinned_paths(())
    paths = [".".join(spec.path) for spec in unpinned.search_allowlist]

    assert unpinned is schema
    assert len(paths) == 82
    assert (
        hashlib.sha256(canonical_json_dumps(paths).encode("utf-8")).hexdigest()
        == "7d86446f2c513af1bd79636abd4ec7842d98b9a977c27cc15b1d93f8e2baf173"
    )
    spec, _ = _build_eval_inputs(
        RecipePatch({}),
        FEEDSTOCK,
        "stub",
        profile,
        unpinned,
    )
    assert spec.recipe_id == "6b7dc801973b564bca8393aa91e3989bb58de245239f471bc4e7260dbfe5ddbf"
    # cache_key includes physics_constraints; recipe_id is allowlist-versioned and
    # moves when the live searchable allowlist identity changes.
    # 2026-06-29: moved when the Mg pseudo vapor-pressure row was removed,
    # AlphaMELTS activity-times-Antoine adopted the shared coefficient selector,
    # and SiO alpha_s became a cited temperature-dependent YAML spec.
    # Later 2026-06-29: moved again when redox v3 Step B added
    # diagnostic-only a_FeO_calphad metadata to the builtin vapor-pressure
    # provider. This is still source-fingerprint invalidation, not an
    # authoritative vapor/yield/ledger move.
    # 2026-06-30: moved when per-stage materials.yaml alpha_s overrides gained
    # explicit certification/status stamping; source-fingerprint only.
    # 2026-07-01: moved when C4b added FeSi to species_catalog and the
    # grounded wall_reactivity_matrix source surface.
    # 2026-07-03: moved when S2b routed the C3 Na/K dose through the credit
    # line (core/extraction/runner/state source edits). recipe_id + the 70-path
    # search allowlist above are UNCHANGED, so this is source-fingerprint
    # invalidation only, not a recipe/schema/allowlist or authoritative
    # vapor/yield/ledger move.
    # 2026-07-03 (SSO-2 c1): allowlist-v10 — 12 new C2A_staged per-stage gas
    # knobs (pO2/p_total/gas_cover_mode x 4 stages) enter the SEARCHABLE
    # allowlist, so recipe_id + allowlist hash + cache_key move BY DESIGN
    # (allowlist-identity move, not source-fingerprint).
    # 2026-07-03 (later): moved again for SIO-PATH0 (map/core diagnostics +
    # fO2 non-finite fail-loud guard, source-fingerprint) AND the
    # PHYSICS_GATE_VERSION v3 bump — the latter is the INTENDED semantic
    # invalidation: pre-S2c cached feasibility verdicts must not be served
    # under the new provenance-completeness gate (milestone-3 L2-P2).
    assert cache_key(spec) == "8daa588cfef249fc6c095b1fa761ca6b489fc626493812decb9bfd9539057950"


def test_bounds_and_type_checks_for_allowlisted_knob() -> None:
    RecipePatch({PO2_DEFAULT: 9.0}).validated()

    with pytest.raises(RecipeValidationError, match="above upper bound"):
        RecipePatch({PO2_DEFAULT: 30.0}).validated()

    with pytest.raises(RecipeValidationError, match="requires float value"):
        RecipePatch({PO2_DEFAULT: "9.0"}).validated()


def test_int_kind_rejects_float_and_bool() -> None:
    hold_time = ("campaigns", "C3", "endpoint", "hold_time_min")
    RecipePatch({hold_time: 30}).validated()

    with pytest.raises(RecipeValidationError, match="requires int value"):
        RecipePatch({hold_time: 30.5}).validated()

    with pytest.raises(RecipeValidationError, match="requires int value"):
        RecipePatch({hold_time: True}).validated()


def test_furnace_max_t_c_knob_bounds_and_top_level_patch() -> None:
    schema = RecipeSchema()
    spec = schema.spec_for(FURNACE_MAX_T_C_PATH)

    assert spec.low == pytest.approx(FURNACE_MAX_T_BOUNDS_C[0])
    assert spec.high == pytest.approx(FURNACE_MAX_T_BOUNDS_C[1])
    assert spec.units == "C"
    assert spec.runtime_enabled is True
    assert RecipePatch({FURNACE_MAX_T_C_PATH: FURNACE_MAX_T_BOUNDS_C[0]}).validated(schema)
    assert RecipePatch({FURNACE_MAX_T_C_PATH: FURNACE_MAX_T_BOUNDS_C[1]}).validated(schema)
    with pytest.raises(RecipeValidationError, match="below lower bound"):
        RecipePatch({FURNACE_MAX_T_C_PATH: 1299.0}).validated(schema)
    with pytest.raises(RecipeValidationError, match="above upper bound"):
        RecipePatch({FURNACE_MAX_T_C_PATH: 2001.0}).validated(schema)

    nested = schema.to_setpoints_patch(RecipePatch({FURNACE_MAX_T_C_PATH: 1450.0}))
    assert nested == {"furnace_max_T_C": 1450.0}
    config = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)._session_config()
    assert config.setpoints["furnace_max_T_C"] == pytest.approx(1450.0)


def test_nested_yaml_round_trip_and_setpoints_patch_smoke() -> None:
    patch = RecipePatch(
        {
            PO2_DEFAULT: 10.0,
            PTOTAL_DEFAULT: 10.0,
            ("campaigns", "C2A_continuous", "duration_h"): [20, 24],
        }
    )

    schema = RecipeSchema()
    nested = schema.to_setpoints_patch(patch)
    loaded = yaml.safe_load(yaml.safe_dump(nested, sort_keys=True))
    loaded_patch = RecipePatch.from_nested(loaded)
    assert loaded_patch.values[PO2_DEFAULT] == pytest.approx(10.0)
    assert loaded_patch.values[PTOTAL_DEFAULT] == pytest.approx(10.0)
    assert loaded_patch.values[
        ("campaigns", "C2A_continuous", "duration_h")
    ] == [20, 24]

    run = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)
    config = run._session_config()
    assert config.setpoints["campaigns"]["C0b_p_cleanup"]["pO2_mbar_default"] == 10.0
    assert config.setpoints["campaigns"]["C0b_p_cleanup"]["p_total_mbar_default"] == 10.0
    assert config.setpoints["campaigns"]["C2A_continuous"]["duration_h"] == [
        20,
        24,
    ]


def test_furnace_max_t_c_default_and_clamp_chokepoint() -> None:
    setpoints = copy.deepcopy(yaml.safe_load(SETPOINTS_PATH.read_text()))
    manager = CampaignManager(setpoints)

    target, ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
    )
    assert target == pytest.approx(1800.0)
    assert ramp == pytest.approx(15.0)

    setpoints["furnace_max_T_C"] = 1400.0
    manager = CampaignManager(setpoints)
    assert manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C2A_STAGED,
        7,
        MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=7),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C2B,
        0,
        MeltState(campaign=CampaignPhase.C2B),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C3_K,
        3,
        MeltState(campaign=CampaignPhase.C3_K),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C4,
        0,
        MeltState(campaign=CampaignPhase.C4),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C5,
        0,
        MeltState(campaign=CampaignPhase.C5),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.C6,
        0,
        MeltState(campaign=CampaignPhase.C6),
    )[0] == pytest.approx(1400.0)
    assert manager.get_temp_target(
        CampaignPhase.COMPLETE,
        0,
        MeltState(campaign=CampaignPhase.COMPLETE),
    )[0] is None


@pytest.mark.parametrize("value", [1299.0, 2000.1, float("inf"), "nan", "hot"])
def test_furnace_max_t_c_setpoints_validation_fails_loud(value) -> None:
    setpoints = copy.deepcopy(yaml.safe_load(SETPOINTS_PATH.read_text()))
    setpoints["furnace_max_T_C"] = value

    with pytest.raises(ValueError, match="furnace_max_T_C"):
        CampaignManager(setpoints)


@pytest.mark.parametrize("stages", [None, [], "bad", [None]])
def test_c2a_staged_empty_or_malformed_stages_fail_loud(stages) -> None:
    setpoints = _c2a_staged_setpoints()
    if stages is None:
        setpoints["campaigns"]["C2A_staged"].pop("stages", None)
    else:
        setpoints["campaigns"]["C2A_staged"]["stages"] = stages
    manager = CampaignManager(setpoints)

    with pytest.raises(ValueError, match="C2A_staged.stages"):
        manager.get_temp_target(
            CampaignPhase.C2A_STAGED,
            0,
            MeltState(campaign=CampaignPhase.C2A_STAGED),
        )


def test_c2a_staged_flux_decay_species_setpoints_are_explicit_ascii() -> None:
    # The flux_decay_species VALUES must be explicit ASCII species names (proven
    # by the per-stage checks below). The whole setpoints.yaml is NOT required to
    # be pure-ASCII — the project standard is latin1-safe (no C1 bytes
    # 0x80-0x9F), enforced by tests/test_artifact_guards.py; comments
    # legitimately carry latin1-printable glyphs (e.g. alpha, degree).
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    stages = setpoints["campaigns"]["C2A_staged"]["stages"]

    for stage_name in ("alkali_early_fe", "sio_window", "fe_hot_hold"):
        for species in _stage_by_name(stages, stage_name)["endpoint"][
            "flux_decay_species"
        ]:
            species.encode("ascii")  # value must be pure-ASCII

    assert _stage_by_name(stages, "alkali_early_fe")["endpoint"][
        "flux_decay_species"
    ] == ["Na", "K"]
    assert _stage_by_name(stages, "sio_window")["endpoint"][
        "flux_decay_species"
    ] == ["SiO"]
    assert _stage_by_name(stages, "fe_hot_hold")["endpoint"][
        "flux_decay_species"
    ] == ["Fe"]
    assert "flux_decay_species" not in _stage_by_name(
        stages,
        "cool_for_na_shuttle",
    )["endpoint"]


def test_c2a_staged_named_stage_knobs_render_to_real_stage_list() -> None:
    schema = RecipeSchema()
    stage_fields = {
        "alkali_early_fe": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "sio_window": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "fe_hot_hold": ("duration_h", "ramp_rate_C_per_hr"),
        "cool_for_na_shuttle": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
    }
    stage_paths = {
        (
            "campaigns",
            "C2A_staged",
            "stages",
            stage,
            field,
        )
        for stage, fields in stage_fields.items()
        for field in fields
    }
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert stage_paths <= search_paths
    assert (
        "campaigns",
        "C2A_staged",
        "stages",
        "fe_hot_hold",
        "target_C",
    ) not in search_paths
    patch = RecipePatch(
        {
            STAGE_SIO_TARGET: 1585.0,
            STAGE_FE_DURATION: 2,
            STAGE_COOL_RAMP: 500.0,
        }
    ).validated(schema)
    nested = schema.to_setpoints_patch(patch)
    loaded_patch = RecipePatch.from_nested(nested).validated(schema)
    stages = nested["campaigns"]["C2A_staged"]["stages"]

    assert loaded_patch.values[STAGE_SIO_TARGET] == pytest.approx(1585.0)
    assert loaded_patch.values[STAGE_FE_DURATION] == 2
    assert loaded_patch.values[STAGE_COOL_RAMP] == pytest.approx(500.0)
    assert _stage_by_name(stages, "sio_window")["target_C"] == pytest.approx(1585.0)
    assert _stage_by_name(stages, "fe_hot_hold")["duration_h"] == 2
    assert _stage_by_name(stages, "cool_for_na_shuttle")[
        "ramp_rate_C_per_hr"
    ] == pytest.approx(500.0)
    assert nested["campaigns"]["C2A_staged"]["max_hold_hr"] == 10
    assert all(path[-1] != "flux_decay_species" for path in loaded_patch.values)

    config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A_staged",
        hours=10,
        setpoints_patch=nested,
    )._session_config()
    cfg = config.setpoints["campaigns"]["C2A_staged"]
    assert cfg["max_hold_hr"] == 10
    target, ramp = CampaignManager(config.setpoints).get_temp_target(
        CampaignPhase.C2A_STAGED,
        4,
        MeltState(),
    )
    assert target == pytest.approx(1585.0)
    assert ramp == pytest.approx(175.0)


def test_c2a_staged_stage_gas_knobs_validate_and_render() -> None:
    schema = RecipeSchema()
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert {
        STAGE_SIO_PO2,
        STAGE_SIO_PTOTAL,
        STAGE_SIO_GAS_MODE,
    } <= search_paths
    po2_spec = schema.spec_for(STAGE_SIO_PO2)
    total_spec = schema.spec_for(STAGE_SIO_PTOTAL)
    mode_spec = schema.spec_for(STAGE_SIO_GAS_MODE)
    assert po2_spec.low == pytest.approx(0.0)
    assert po2_spec.high == pytest.approx(15.0)
    assert total_spec.low == pytest.approx(5.0)
    assert total_spec.high == pytest.approx(15.0)
    assert mode_spec.choices == ("pn2_sweep", "po2_hold")

    patch = RecipePatch(
        {
            STAGE_SIO_PO2: 2.5,
            STAGE_SIO_PTOTAL: 12.0,
            STAGE_SIO_GAS_MODE: "po2_hold",
        }
    ).validated(schema)
    nested = schema.to_setpoints_patch(patch)
    loaded_patch = RecipePatch.from_nested(nested).validated(schema)
    sio_stage = _stage_by_name(nested["campaigns"]["C2A_staged"]["stages"], "sio_window")

    assert loaded_patch.values[STAGE_SIO_PO2] == pytest.approx(2.5)
    assert loaded_patch.values[STAGE_SIO_PTOTAL] == pytest.approx(12.0)
    assert loaded_patch.values[STAGE_SIO_GAS_MODE] == "po2_hold"
    assert sio_stage["pO2_mbar"] == pytest.approx(2.5)
    assert sio_stage["p_total_mbar"] == pytest.approx(12.0)
    assert sio_stage["gas_cover_mode"] == "po2_hold"


def test_c2a_staged_stage_gas_knobs_fail_loudly() -> None:
    schema = RecipeSchema()

    with pytest.raises(
        RecipeValidationError,
        match=r"campaigns\.C2A_staged\.stages\.sio_window\.gas_cover_mode",
    ):
        RecipePatch({STAGE_SIO_GAS_MODE: "argon_blanket"}).validated(schema)
    with pytest.raises(
        RecipeValidationError,
        match=r"campaigns\.C2A_staged\.stages\.sio_window\.p_total_mbar",
    ):
        RecipePatch({STAGE_SIO_PTOTAL: 4.9}).validated(schema)
    with pytest.raises(
        RecipeValidationError,
        match=r"campaigns\.C2A_staged\.stages\.sio_window\.pN2_mbar",
    ):
        RecipePatch.from_nested(
            {
                "campaigns": {
                    "C2A_staged": {
                        "stages": [{"name": "sio_window", "pN2_mbar": 10.0}]
                    }
                }
            }
        ).validated(schema)
    with pytest.raises(
        RecipeValidationError,
        match="recipe_pressure_partial_exceeds_total",
    ):
        RecipePatch({STAGE_SIO_PO2: 12.0}).validated(schema)


def test_c2a_staged_pn2_sweep_requires_positive_carrier_floor() -> None:
    schema = RecipeSchema()

    with pytest.raises(
        RecipeValidationError,
        match="recipe_pressure_pn2_sweep_requires_positive_carrier",
    ):
        RecipePatch(
            {
                STAGE_SIO_PO2: 10.0,
                STAGE_SIO_PTOTAL: 10.0,
                STAGE_SIO_GAS_MODE: "pn2_sweep",
            }
        ).validated(schema)

    RecipePatch(
        {
            STAGE_SIO_PO2: 9.999,
            STAGE_SIO_PTOTAL: 10.0,
            STAGE_SIO_GAS_MODE: "pn2_sweep",
        }
    ).validated(schema)
    RecipePatch(
        {
            STAGE_SIO_PO2: 10.0,
            STAGE_SIO_PTOTAL: 10.0,
            STAGE_SIO_GAS_MODE: "po2_hold",
        }
    ).validated(schema)


def test_c2a_staged_stage_gas_defaults_are_empty_patch_neutral() -> None:
    schema = RecipeSchema()
    source_setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    source_c2a = source_setpoints["campaigns"]["C2A_staged"]

    assert schema.to_setpoints_patch(RecipePatch({})) == {}
    config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A_staged",
        hours=9,
        setpoints_patch=schema.to_setpoints_patch(RecipePatch({})),
    )._session_config()
    cfg = config.setpoints["campaigns"]["C2A_staged"]

    assert cfg["stages"] == source_c2a["stages"]
    assert "order" not in cfg
    assert cfg["pO2_mbar_default"] == pytest.approx(0.0)
    assert cfg["p_total_mbar_default"] == pytest.approx(10.0)
    assert all("gas_cover_mode" not in stage for stage in cfg["stages"])


def test_c2a_staged_order_choice_renders_and_executes_requested_order() -> None:
    schema = RecipeSchema()
    patch = RecipePatch({C2A_ORDER: "fe_then_sio"}).validated(schema)
    nested = schema.to_setpoints_patch(patch)
    stages = nested["campaigns"]["C2A_staged"]["stages"]

    assert nested["campaigns"]["C2A_staged"]["order"] == "fe_then_sio"
    assert [stage["name"] for stage in stages] == [
        "alkali_early_fe",
        "fe_hot_hold",
        "sio_window",
        "cool_for_na_shuttle",
    ]
    assert nested["campaigns"]["C2A_staged"]["max_hold_hr"] == 9

    loaded_patch = RecipePatch.from_nested(nested).validated(schema)
    assert loaded_patch.values[C2A_ORDER] == "fe_then_sio"

    config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A_staged",
        hours=9,
        setpoints_patch=nested,
    )._session_config()
    cfg = config.setpoints["campaigns"]["C2A_staged"]
    assert [stage["name"] for stage in cfg["stages"]] == [
        "alkali_early_fe",
        "fe_hot_hold",
        "sio_window",
        "cool_for_na_shuttle",
    ]
    manager = CampaignManager(config.setpoints)

    target, ramp = manager.get_temp_target(
        CampaignPhase.C2A_STAGED,
        4,
        MeltState(campaign=CampaignPhase.C2A_STAGED),
    )
    assert target == pytest.approx(1750.0)
    assert ramp == pytest.approx(150.0)

    target, ramp = manager.get_temp_target(
        CampaignPhase.C2A_STAGED,
        5,
        MeltState(campaign=CampaignPhase.C2A_STAGED),
    )
    assert target == pytest.approx(1600.0)
    assert ramp == pytest.approx(175.0)


@pytest.mark.parametrize(
    ("stages", "message"),
    (
        (
            [
                {"name": "alkali_early_fe"},
                {"name": "sio_window"},
                {"name": "cool_for_na_shuttle"},
            ],
            "missing C2A_staged.stages stage: fe_hot_hold",
        ),
        (
            [
                {"name": "alkali_early_fe"},
                {"name": "sio_window"},
                {"name": "mg_surprise"},
                {"name": "cool_for_na_shuttle"},
            ],
            "unknown C2A_staged stage: mg_surprise",
        ),
        (
            [
                {"name": "alkali_early_fe"},
                {"name": "sio_window"},
                {"name": "sio_window"},
                {"name": "cool_for_na_shuttle"},
            ],
            "duplicate C2A_staged stage: sio_window",
        ),
        (
            [
                {"name": "sio_window"},
                {"name": "alkali_early_fe"},
                {"name": "fe_hot_hold"},
                {"name": "cool_for_na_shuttle"},
            ],
            "C2A_staged.stages must keep alkali_early_fe first",
        ),
        (
            [
                {"name": "alkali_early_fe"},
                {"name": "sio_window"},
                {"name": "cool_for_na_shuttle"},
                {"name": "fe_hot_hold"},
            ],
            "C2A_staged.stages must keep cool_for_na_shuttle last",
        ),
    ),
)
def test_c2a_staged_stage_order_refuses_dropped_or_moved_required_stages(
    stages,
    message,
) -> None:
    with pytest.raises(RecipeValidationError, match=message):
        RecipePatch.from_nested(
            {"campaigns": {"C2A_staged": {"stages": stages}}}
        ).validated(RecipeSchema())


def test_c2a_staged_order_rejects_unknown_choice() -> None:
    with pytest.raises(
        RecipeValidationError,
        match=r"campaigns\.C2A_staged\.order",
    ):
        RecipePatch({C2A_ORDER: "iron_firstish"}).validated(RecipeSchema())


def test_c2a_staged_depletion_flux_decay_knob_bounds_and_neutral_validation() -> None:
    schema = RecipeSchema()
    spec = schema.spec_for(C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH)

    assert spec.low == pytest.approx(C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR)
    assert spec.high == pytest.approx(0.50)
    assert spec.path in {item.path for item in schema.search_allowlist}
    assert RecipePatch(
        {C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.0}
    ).validated(schema).values[
        C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH
    ] == pytest.approx(
        0.0
    )
    assert RecipePatch(
        {C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.005}
    ).validated(schema).values[
        C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH
    ] == pytest.approx(
        0.005
    )
    with pytest.raises(RecipeValidationError, match="below lower bound"):
        RecipePatch(
            {C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: -0.01}
        ).validated(schema)


def test_c2a_staged_depletion_flux_decay_golden_neutral_fixed_schedule() -> None:
    expected_targets = [
        (1250.0, 600.0),
        (1250.0, 600.0),
        (1250.0, 600.0),
        (1250.0, 600.0),
        (1600.0, 175.0),
        (1600.0, 175.0),
        (1600.0, 175.0),
        (1750.0, 150.0),
        (1150.0, 600.0),
    ]

    for fraction in (None, 0.0):
        manager = _configured_c2a_staged_manager(fraction)
        targets = [
            manager.get_temp_target(
                CampaignPhase.C2A_STAGED,
                hour,
                MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=hour),
            )
            for hour in range(9)
        ]
        end_hours = [
            hour
            for hour in range(9)
            if _check_c2a_staged_endpoint(
                manager,
                hour,
                _flux(Na=0.01, K=0.01, SiO=0.01, Fe=0.01),
            )
        ]

        assert targets == pytest.approx(expected_targets)
        assert end_hours[0] == 8
        assert manager._c2a_staged_stage_idx == 0


def test_c2a_staged_depletion_flux_decay_advances_stage_early() -> None:
    manager = _configured_c2a_staged_manager(0.25)

    assert not _check_c2a_staged_endpoint(manager, 0, _flux(Na=10.0, K=8.0))
    assert manager._c2a_staged_stage_idx == 0
    assert not _check_c2a_staged_endpoint(manager, 1, _flux(Na=5.0, K=4.0))
    assert manager._c2a_staged_stage_idx == 0
    assert not _check_c2a_staged_endpoint(manager, 2, _flux(Na=2.4, K=1.9))

    assert manager._c2a_staged_stage_idx == 1
    assert manager._c2a_staged_stage_start_hour == 3
    assert manager._c2a_staged_peak_flux_by_species == {}
    target, ramp = manager.get_temp_target(
        CampaignPhase.C2A_STAGED,
        3,
        MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=3),
    )
    assert target == pytest.approx(1600.0)
    assert ramp == pytest.approx(175.0)


def test_c2a_staged_depletion_flux_decay_times_out_when_flux_never_decays() -> None:
    manager = _configured_c2a_staged_manager(0.25)
    ended_hour = None
    stage_idx_by_hour: dict[int, int] = {}

    for hour in range(9):
        ended = _check_c2a_staged_endpoint(
            manager,
            hour,
            _flux(Na=100.0, K=100.0, SiO=100.0, Fe=100.0),
        )
        stage_idx_by_hour[hour] = manager._c2a_staged_stage_idx
        if ended:
            ended_hour = hour
            break

    assert stage_idx_by_hour[2] == 0
    assert stage_idx_by_hour[3] == 1
    assert stage_idx_by_hour[6] == 2
    assert stage_idx_by_hour[7] == 3
    assert ended_hour == 8


@pytest.mark.parametrize("mode", OXYGEN_SINK_CHANNEL_MODE_VALUES)
def test_oxygen_sink_channel_mode_round_trips_as_diagnostic_only(mode: str) -> None:
    patch = RecipePatch({OXYGEN_SINK_CHANNEL_MODE: mode})

    schema = RecipeSchema()
    nested = schema.to_setpoints_patch(patch)
    loaded = yaml.safe_load(yaml.safe_dump(nested, sort_keys=True))
    loaded_patch = RecipePatch.from_nested(loaded)
    assert loaded_patch.values[OXYGEN_SINK_CHANNEL_MODE] == mode

    run = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)
    config = run._session_config()
    assert config.setpoints["chemistry_kernel"][OXYGEN_SINK_CHANNEL_MODE_KEY] == mode

    session = SimSession().start(config)
    assert session.simulator.oxygen_sink_channel_mode.value == mode
    assert session.simulator._chem_kernel is not None
    assert session.simulator._chem_kernel.oxygen_sink_channel_mode.value == mode


def test_oxygen_sink_channel_mode_default_is_absent_from_setpoints_patch() -> None:
    config = PyrolysisRun(feedstock_id=FEEDSTOCK)._session_config()
    assert OXYGEN_SINK_CHANNEL_MODE_KEY not in config.setpoints.get(
        "chemistry_kernel", {}
    )

    session = SimSession().start(config)
    assert (
        session.simulator.oxygen_sink_channel_mode.value
        == "legacy_source_equilibrium"
    )


def test_oxygen_sink_channel_mode_rejects_unknown_value() -> None:
    with pytest.raises(RecipeValidationError, match="not in choices"):
        RecipePatch({OXYGEN_SINK_CHANNEL_MODE: "condensation_only_sink"}).validated()


def test_oxygen_sink_channel_mode_evalspec_round_trip_and_validation() -> None:
    mode = "deposit_gettering_diagnostic"
    spec = EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id=FEEDSTOCK,
        profile_id="profile-id",
        fidelity="fast",
        code_version="test-code-version",
        data_digests=DATA_DIGESTS,
        chemistry_kernel={OXYGEN_SINK_CHANNEL_MODE_KEY: mode},
    )
    payload = json.loads(canonical_evalspec_json(spec).decode("utf-8"))

    assert payload["chemistry_kernel"][OXYGEN_SINK_CHANNEL_MODE_KEY] == mode
    with pytest.raises(ValueError, match=OXYGEN_SINK_CHANNEL_MODE_KEY):
        EvalSpec(
            recipe_id="recipe-id",
            feedstock_recipe_digest="feedstock-recipe-digest",
            feedstock_id=FEEDSTOCK,
            profile_id="profile-id",
            fidelity="fast",
            code_version="test-code-version",
            data_digests=DATA_DIGESTS,
            chemistry_kernel={OXYGEN_SINK_CHANNEL_MODE_KEY: "behavior_mode"},
        )


def test_recipe_id_is_stable_and_schema_versioned() -> None:
    first = RecipePatch({PO2_DEFAULT: 9.0}).validated()
    second = RecipePatch.from_nested(
        {"campaigns": {"C0b_p_cleanup": {"pO2_mbar_default": 9.0}}}
    ).validated()

    assert first.recipe_id() == second.recipe_id()
    assert (
        first.recipe_id()
            == "40e6638db09ce4f9d85e944c26c6239783cd55baff69c8fcb65b96177140a2d9"
    )
    assert first.recipe_id(recipe_schema_version="recipe-schema-v2") != first.recipe_id()
    assert RecipePatch({PO2_DEFAULT: 8.0}).validated().recipe_id() != first.recipe_id()


def test_recipe_id_changes_when_allowlist_version_changes() -> None:
    patch = RecipePatch({PO2_DEFAULT: 9.0})
    old_schema = RecipeSchema(allowlist_version="allowlist-old")
    new_schema = RecipeSchema(allowlist_version="allowlist-new")

    assert patch.validated(old_schema).recipe_id(old_schema) != patch.validated(
        new_schema
    ).recipe_id(new_schema)


def test_redox_cleanup_dose_fields_validate_but_do_not_materialize() -> None:
    schema = RecipeSchema()
    oxidant_spec = schema.spec_for(STAGE0_REDOX_OXIDANT_KG_PATH)
    carbon_spec = schema.spec_for(STAGE0_CARBON_REDUCTANT_KG_PATH)
    patch = RecipePatch(
        {
            STAGE0_REDOX_OXIDANT_KG_PATH: 12.5,
            STAGE0_CARBON_REDUCTANT_KG_PATH: 7.25,
        }
    ).validated(schema)

    assert oxidant_spec.search_enabled is False
    assert carbon_spec.search_enabled is False
    assert oxidant_spec.runtime_enabled is False
    assert carbon_spec.runtime_enabled is False
    assert STAGE0_REDOX_OXIDANT_KG_PATH not in {
        spec.path for spec in schema.search_allowlist
    }
    assert STAGE0_CARBON_REDUCTANT_KG_PATH not in {
        spec.path for spec in schema.search_allowlist
    }
    assert schema.to_setpoints_patch(patch) == {}
    assert schema.redox_cleanup_doses_kg(patch) == pytest.approx((12.5, 7.25))


def test_c5_allow_mre_voltage_cap_is_primary_search_knob() -> None:
    schema = RecipeSchema()
    cap_spec = schema.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH)
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    owner_bound = _lookup_setpoint(
        setpoints,
        "campaigns.C5.allow_mre_voltage_cap_upper_bound_V",
    )
    branch_two = ("campaigns", "C5", "branch_two", "max_voltage_V")
    branch_one = ("campaigns", "C5", "branch_one", "max_voltage_V")
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert cap_spec.search_enabled is True
    assert cap_spec.runtime_enabled is False
    assert cap_spec.low == pytest.approx(0.0)
    assert cap_spec.high == pytest.approx(owner_bound)
    assert C5_ALLOW_MRE_VOLTAGE_CAP_PATH in search_paths
    assert branch_two not in search_paths
    assert branch_one not in search_paths
    assert schema.spec_for(branch_two).runtime_enabled is True
    assert schema.spec_for(branch_one).runtime_enabled is True


def test_c4_hold_temp_is_optimizer_search_knob_not_setpoints_patch() -> None:
    schema = RecipeSchema()
    hold_spec = schema.spec_for(C4_HOLD_TEMP_C_PATH)
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert C4_HOLD_TEMP_C_PATH in search_paths
    assert hold_spec.runtime_enabled is False
    assert hold_spec.low == pytest.approx(1580.0)
    assert hold_spec.high == pytest.approx(1670.0)
    assert schema.to_setpoints_patch(
        RecipePatch({C4_HOLD_TEMP_C_PATH: 1600.0})
    ) == {}


def test_c5_allow_mre_voltage_cap_rejects_above_owner_bound() -> None:
    schema = RecipeSchema()
    cap_spec = schema.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH)
    assert cap_spec.high is not None
    too_high = float(cap_spec.high) + 0.01

    with pytest.raises(RecipeValidationError, match="above upper bound"):
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: too_high}).validated(schema)


def test_default_allowlist_rebuilds_c5_mre_bound_when_setpoints_yaml_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_mtime_ns = 1_700_000_000_000_000_000
    monkeypatch.setattr(recipe_module, "DEFAULT_DATA_DIR", tmp_path)
    _write_c5_mre_cap_bound_yaml(tmp_path, high=1.25, mtime_ns=first_mtime_ns)
    initial_allowlist = tuple(
        replace(
            spec,
            high=recipe_module._c5_allow_mre_voltage_cap_upper_bound(),
        )
        if spec.path == C5_ALLOW_MRE_VOLTAGE_CAP_PATH
        else spec
        for spec in RecipeSchema.ALLOWLIST
    )
    monkeypatch.setattr(RecipeSchema, "ALLOWLIST", initial_allowlist)

    first = RecipeSchema()
    assert first.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH).high == 1.25

    _write_c5_mre_cap_bound_yaml(
        tmp_path, high=1.75, mtime_ns=first_mtime_ns + 1_000_000_000
    )
    warm = RecipeSchema()

    assert warm.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH).high == 1.75


def test_explicit_class_allowlist_rebuilds_c5_mre_bound_when_setpoints_yaml_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_mtime_ns = 1_700_000_000_000_000_000
    monkeypatch.setattr(recipe_module, "DEFAULT_DATA_DIR", tmp_path)
    _write_c5_mre_cap_bound_yaml(tmp_path, high=1.25, mtime_ns=first_mtime_ns)

    first = RecipeSchema(allowlist=RecipeSchema.ALLOWLIST)
    assert first.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH).high == 1.25

    _write_c5_mre_cap_bound_yaml(
        tmp_path, high=1.75, mtime_ns=first_mtime_ns + 1_000_000_000
    )
    warm = RecipeSchema(allowlist=tuple(RecipeSchema.ALLOWLIST))

    assert warm.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH).high == 1.75


def test_forbidden_floor_cannot_be_neutered_by_custom_schema() -> None:
    # Review P1: a caller-supplied forbidden_prefixes ADDS to the inviolable class
    # floor; it can never remove it. RecipeSchema(forbidden_prefixes=()) must STILL
    # deny a *.products path, else the safety boundary is bypassable via a custom
    # schema passed to RecipePatch.validated().
    neutered = RecipeSchema(forbidden_prefixes=())
    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipePatch({PRODUCT_TARGET: 1.0}).validated(neutered)

    # A caller addition is honored ON TOP OF the floor (extend, never replace).
    extended = RecipeSchema(forbidden_prefixes=("campaigns.C0",))
    assert extended.is_forbidden(("campaigns", "C0"))
    assert extended.is_forbidden(PRODUCT_TARGET)


def test_knob_bounds_source_provenance_is_honest() -> None:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    range_sourced = 0
    engineering_envelopes = 0

    for spec in RecipeSchema().allowlist:
        if spec.bounds_source.startswith("setpoints:"):
            yaml_path = spec.bounds_source.removeprefix("setpoints:")
            yaml_value = _lookup_setpoint(setpoints, yaml_path)
            assert isinstance(yaml_value, list), (
                f"{'.'.join(spec.path)} cites scalar YAML as bounds_source; "
                "scalar nominal knobs must use engineering_envelope"
            )
            assert len(yaml_value) == 2
            assert spec.low is not None
            assert spec.high is not None
            assert yaml_value[0] <= spec.low <= spec.high <= yaml_value[1]
            range_sourced += 1
        else:
            assert spec.bounds_source.startswith("engineering_envelope"), (
                f"{'.'.join(spec.path)} bounds_source must be setpoints: range "
                "or engineering_envelope"
            )
            engineering_envelopes += 1

    assert range_sourced + engineering_envelopes == len(RecipeSchema().allowlist)
    assert engineering_envelopes > 0


def test_pressure_default_pair_map_covers_allowlisted_siblings() -> None:
    schema = RecipeSchema()
    allowlisted = {spec.path for spec in schema.allowlist}
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    expected_pairs = {}

    for path in allowlisted:
        if len(path) != 3:
            continue
        if path[0] != "campaigns" or path[2] != "pO2_mbar_default":
            continue
        total_path = (path[0], path[1], "p_total_mbar_default")
        if total_path not in allowlisted:
            continue
        _lookup_setpoint(setpoints, ".".join(path))
        _lookup_setpoint(setpoints, ".".join(total_path))
        expected_pairs[path] = total_path

    assert dict(schema.PRESSURE_TOTAL_DEFAULT_BY_PO2_DEFAULT) == expected_pairs


def test_to_setpoints_patch_validates_before_rendering_forbidden_paths() -> None:
    patch = RecipePatch({("campaigns", "C2A", "products", "x"): 1.0})

    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipeSchema().to_setpoints_patch(patch)


def test_recipe_patch_refuses_explicit_partial_pressure_above_total() -> None:
    patch = RecipePatch({C3_PO2_DEFAULT: 1.2, C3_PTOTAL_DEFAULT: 0.8})

    with pytest.raises(RecipeValidationError, match="recipe_pressure_partial_exceeds_total"):
        patch.validated()


def test_to_setpoints_patch_keeps_po2_only_default_total_untouched() -> None:
    nested = RecipeSchema().to_setpoints_patch(RecipePatch({C3_PO2_DEFAULT: 0.8}))

    assert nested["campaigns"]["C3"]["pO2_mbar_default"] == pytest.approx(0.8)
    assert "p_total_mbar_default" not in nested["campaigns"]["C3"]
    config = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)._session_config()
    assert config.setpoints["campaigns"]["C3"]["p_total_mbar_default"] == pytest.approx(
        1.0
    )


def test_to_setpoints_patch_rejects_po2_only_above_default_total() -> None:
    with pytest.raises(RecipeValidationError, match="recipe_pressure_partial_exceeds_total"):
        RecipeSchema().to_setpoints_patch(RecipePatch({C3_PO2_DEFAULT: 1.2}))


def test_to_setpoints_patch_keeps_total_only_above_default_po2_untouched() -> None:
    nested = RecipeSchema().to_setpoints_patch(RecipePatch({C3_PTOTAL_DEFAULT: 1.2}))

    assert nested["campaigns"]["C3"]["p_total_mbar_default"] == pytest.approx(1.2)
    assert "pO2_mbar_default" not in nested["campaigns"]["C3"]
    config = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)._session_config()
    assert config.setpoints["campaigns"]["C3"]["pO2_mbar_default"] == pytest.approx(
        1.0
    )


def test_to_setpoints_patch_rejects_total_only_below_default_po2() -> None:
    with pytest.raises(RecipeValidationError) as exc_info:
        RecipeSchema().to_setpoints_patch(RecipePatch({C3_PTOTAL_DEFAULT: 0.6}))

    message = str(exc_info.value)
    assert "recipe_pressure_partial_exceeds_total" in message
    assert "campaigns.C3.pO2_mbar_default=1 (YAML default)" in message
    assert "campaigns.C3.p_total_mbar_default=0.6 (patched)" in message
    assert "set both pO2 and p_total knobs" in message


def test_po2_only_patch_recipe_id_differs_from_old_derived_total_effect() -> None:
    schema = RecipeSchema()
    po2_only = RecipePatch({C3_PO2_DEFAULT: 0.8}).validated(schema)
    explicit_old_derivation = RecipePatch(
        {C3_PO2_DEFAULT: 0.8, C3_PTOTAL_DEFAULT: 0.8}
    ).validated(schema)

    assert po2_only.recipe_id(schema) != explicit_old_derivation.recipe_id(schema)
    assert "p_total_mbar_default" not in po2_only.canonical_json()

    po2_only_config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch=schema.to_setpoints_patch(po2_only),
    )._session_config()
    explicit_config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch=schema.to_setpoints_patch(explicit_old_derivation),
    )._session_config()
    assert po2_only_config.setpoints["campaigns"]["C3"][
        "p_total_mbar_default"
    ] == pytest.approx(1.0)
    assert explicit_config.setpoints["campaigns"]["C3"][
        "p_total_mbar_default"
    ] == pytest.approx(0.8)


def test_dotted_path_segment_is_rejected() -> None:
    # Review P2: a segment embedding "." ("products.oxygen_kg" as ONE segment)
    # would slip past dotted-prefix "*.products" matching. Reject at normalization.
    with pytest.raises(RecipeValidationError, match="must not contain"):
        RecipePatch({("campaigns", "C0b_p_cleanup", "products.oxygen_kg"): 1.0})
