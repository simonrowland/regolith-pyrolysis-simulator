from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import scripts.make_recipe_db_profile as generator
from simulator.optimize.evaluate import (
    EngineBugAbort,
    _build_eval_inputs,
    _composition_target_constraints,
    evaluate,
)
from simulator.optimize.product_pools import MELT_PRODUCT_POOLS, STREAM_PRODUCT_POOLS
from simulator.optimize.profiles import ProfileValidationError, validate_profile
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.state import CampaignPhase


SESSION_VALID_CAMPAIGNS = (
    "IDLE",
    "C0",
    "C0B",
    "C2A",
    "C2A_STAGED",
    "C2B",
    "C3_K",
    "C3_NA",
    "C4",
    "C5",
    "C6",
    "MRE_BASELINE",
    "COMPLETE",
)
SESSION_CAMPAIGN_ALIASES = {
    "C0b_p_cleanup": "C0B",
    "C2A_continuous": "C2A",
    "C2A_staged": "C2A_STAGED",
}


def _is_runnable_target(row: generator.TargetMenuRow) -> bool:
    return (
        generator._target_blocked_reason(
            row,
            campaign=row.maturity_campaign,
        )
        is None
    )


RUNNABLE_TARGET_IDS = tuple(
    sorted(
        target_id
        for target_id, row in generator.TARGET_MENU.items()
        if _is_runnable_target(row)
    )
)


def _setpoint_campaign_config(campaign: str) -> dict[str, object]:
    path = Path("data/setpoints.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["campaigns"][generator._setpoint_campaign_key(campaign)]

def test_pinned_session_campaign_vocabulary() -> None:
    assert tuple(member.name for member in CampaignPhase) == SESSION_VALID_CAMPAIGNS


@pytest.mark.parametrize("target_id", sorted(generator.TARGET_MENU))
def test_target_menu_campaigns_are_session_valid(target_id: str) -> None:
    campaign = generator.TARGET_MENU[target_id].maturity_campaign
    canonical = SESSION_CAMPAIGN_ALIASES.get(campaign, campaign)
    assert canonical in SESSION_VALID_CAMPAIGNS


def test_retain_alkali_legacy_c3_override_emits_session_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-glass-retain-na-k-c3.yaml"
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    assert (
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-glass-retain-na-k-c3",
                "--campaign",
                "C3",
                "--db",
                str(tmp_path / "cache.db"),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    profile = yaml.safe_load(out.read_text())
    assert profile["run"]["campaign"] == "C3_NA"
    assert "knudsen_viscous" not in profile["constraints"]["gates"]
    assert "extraction_completeness" not in profile["constraints"]["gates"]
    assert "target_species" not in profile["constraints"]


def test_pc_extract_al_generates_thermite_profile_when_c6_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-extract-al.yaml"
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    assert (
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-extract-al",
                "--db",
                str(tmp_path / "cache.db"),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    assert target["extraction"]["completeness_min"]["Al"] == pytest.approx(1.0)
    assert target["extraction"]["mechanisms"]["Al"] == "c6_mg_thermite"


def test_pc_extract_al_blocks_when_campaign_set_lacks_c6(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-extract-al-c2b.yaml"
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    assert (
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-extract-al",
                "--campaign",
                "C2B",
                "--db",
                str(tmp_path / "cache.db"),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    profile = yaml.safe_load(out.read_text())
    assert profile["status"] == "BLOCKED"
    assert profile["target_id"] == "pc-extract-al"
    assert profile["blocked_reason"] == "Al reachable via C6 thermite - row lacks C6"
    assert profile["disposition"]["kind"] == "missing_extraction_mechanism"


def test_pc_extract_fe_is_askable_via_c3_shuttle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-extract-fe-c3.yaml"
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    assert (
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-extract-fe",
                "--campaign",
                "C3",
                "--db",
                str(tmp_path / "cache.db"),
                "--out",
                str(out),
            ]
        )
        == 0
    )

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    assert target["maturity"]["campaign"] == "C3_NA"
    assert target["extraction"]["mechanisms"]["Fe"] == "c3_metallothermic_shuttle"


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
@pytest.mark.parametrize("feedstock", ["lunar_mare_low_ti", "ci_carbonaceous_chondrite"])
def test_target_menu_rows_emit_validating_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
    feedstock: str,
) -> None:
    out = tmp_path / f"{feedstock}__{target_id}.yaml"

    _run_generator(monkeypatch, tmp_path, feedstock, target_id, out)

    profile = yaml.safe_load(out.read_text())
    validated = validate_profile(profile, expected_feedstock=feedstock, source=out)
    _assert_pressure_default_boxes_are_jointly_feasible(validated)
    objective = validated["objectives"][0]
    assert objective["type"] == "composition_target"
    assert objective["id"] == target_id
    assert objective["metric"] == f"composition_target:{target_id}"
    assert objective["target"]["require_coating_gate"] is True
    assert {row["metric"] for row in validated["objectives"][1:]} == {
        "energy_kWh",
        "duration_h",
    }


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_extraction_gate_is_scoped_to_extracted_species(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = yaml.safe_load(out.read_text())
    validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    constraints = profile["constraints"]
    target = profile["objectives"][0]["target"]
    gates = tuple(constraints["gates"])
    completeness_min = dict(target["extraction"]["completeness_min"])
    target_species = tuple(constraints.get("target_species", ()))

    if completeness_min:
        assert "extraction_completeness" in gates
        assert target_species == tuple(completeness_min)
    else:
        assert "extraction_completeness" not in gates
        assert target_species == ()

    for species in target_species:
        assert target["species_vector"][species] == "extract"


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_stream_purity_gate_matches_product_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = yaml.safe_load(out.read_text())
    validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    gates = tuple(profile["constraints"]["gates"])

    if target["pool"] in STREAM_PRODUCT_POOLS:
        assert "delivered_stream_purity" in gates
    elif target["pool"] in MELT_PRODUCT_POOLS:
        assert "delivered_stream_purity" not in gates
    else:
        pytest.fail(f"unclassified target product pool: {target['pool']}")


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_knudsen_gate_tracks_vapor_removal_physics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    gates = tuple(profile["constraints"]["gates"])

    requires_vapor_removal = bool(target["extraction"]["completeness_min"])
    if requires_vapor_removal:
        assert "knudsen_viscous" in gates
    else:
        assert "knudsen_viscous" not in gates


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_extraction_minima_reach_physics_constraints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    completeness_min = dict(target["extraction"]["completeness_min"])
    constraints = _composition_target_constraints(profile, None)

    if completeness_min:
        assert constraints is not None
        assert set(constraints.extraction_min_fraction_by_species) == set(completeness_min)
        for species, value in completeness_min.items():
            threshold = constraints.extraction_min_fraction_by_species[species]
            assert threshold.value == pytest.approx(value)
            assert threshold.source == "profile"
            assert f"completeness_min.{species}" in threshold.source_ref
    else:
        assert constraints is not None
        assert constraints.extraction_min_fraction_by_species == {}


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_windowed_campaigns_emit_runtime_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "high",
        profile,
        RecipeSchema(),
        constraints=_composition_target_constraints(profile, None),
    )
    campaign = profile["run"]["campaign"]
    cfg = _setpoint_campaign_config(campaign)
    expected_temp_range = cfg.get("temp_range_C")

    if expected_temp_range is None:
        assert campaign not in spec.runtime_campaign_overrides
        return

    overrides = spec.runtime_campaign_overrides[campaign]
    assert run_config.runtime_campaign_overrides[campaign] == overrides
    assert overrides["thermal_window_low_C"] == pytest.approx(
        expected_temp_range[0]
    )
    assert overrides["thermal_window_high_C"] == pytest.approx(
        expected_temp_range[1]
    )
    assert overrides["thermal_window_preheat_hours"] >= 0.0
    max_hold_hr = cfg.get("max_hold_hr")
    if isinstance(max_hold_hr, (int, float)):
        assert overrides["max_hours"] <= float(max_hold_hr)
    else:
        assert run_config.hours >= int(profile["run"]["hours"])


def test_target_menu_generation_refuses_window_duration_over_campaign_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-extract-mg.yaml"
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    with pytest.raises(SystemExit, match=r"pc-extract-mg C4\.duration_h exceeds campaign max_hold_hr"):
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-extract-mg",
                "--hours",
                "24",
                "--db",
                str(tmp_path / "cache.db"),
                "--out",
                str(out),
            ]
        )


def test_runtime_loader_refuses_stale_window_profile_over_campaign_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-extract-mg.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", "pc-extract-mg", out)
    profile = yaml.safe_load(out.read_text())
    validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    profile["run"]["hours"] = 18

    with pytest.raises(
        ProfileValidationError,
        match=r"thermal_window_campaign_max_hold refusal.*FORCE_PROFILES=1",
    ):
        validate_profile(
            profile,
            expected_feedstock="lunar_mare_low_ti",
            source=out,
        )


def test_plural_only_seed_does_not_receive_unrelated_thermal_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "mars-phyllosilicate-mg.yaml"

    _run_generator(monkeypatch, tmp_path, "mars_phyllosilicate_clay", "pc-extract-mg", out)

    profile = yaml.safe_load(out.read_text())
    plural_seed = next(
        seed for seed in profile["seed_recipes"] if seed["id"] == "mars-clay-al-thermite-seed"
    )
    plural_campaigns = plural_seed.get("patch", {}).get("campaigns", {})
    assert "C4" not in plural_campaigns

    window_seed = next(
        seed for seed in profile["seed_recipes"] if seed["id"] == "pc-extract-mg-C4-thermal-window"
    )
    assert window_seed["source_campaign"] == "C4"
    assert window_seed["patch"]["campaigns"]["C4"]["temp_range_C"] == [1580.0, 1670.0]


def test_no_declared_campaign_window_is_target_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-glass-retain-na-k-c3.yaml"

    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", "pc-glass-retain-na-k-c3", out)

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    target = profile["objectives"][0]["target"]
    assert target["thermal_window"] == "not-declared-for-campaign:C3"


@pytest.mark.parametrize("target_id", RUNNABLE_TARGET_IDS)
def test_target_menu_generated_profiles_stub_eval_no_campaign_vocabulary_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"
    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = yaml.safe_load(out.read_text())
    try:
        evaluate(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "stub",
            profile=profile,
            candidate_id=f"smoke-{target_id}",
        )
    except EngineBugAbort as exc:
        message = str(exc)
        assert "unknown campaign" not in message
        assert "valid options:" not in message


def test_target_menu_unknown_id_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    with pytest.raises(SystemExit, match="unknown PC target"):
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-does-not-exist",
                "--out",
                str(tmp_path / "profile.yaml"),
            ]
        )


def test_target_menu_known_unseeded_id_refuses_to_invent_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))

    with pytest.raises(SystemExit, match="refusing to invent bounds"):
        generator.main(
            [
                "lunar_mare_low_ti",
                "--target",
                "pc-glass-green",
                "--out",
                str(tmp_path / "profile.yaml"),
            ]
        )


@pytest.mark.parametrize(
    ("target_id", "expected_tier"),
    [
        ("pc-glass-retain-na-k-c3", "workable_glass"),
    ],
)
def test_target_menu_tier_rows_resolve_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
    expected_tier: str,
) -> None:
    out = tmp_path / f"{target_id}.yaml"

    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = yaml.safe_load(out.read_text())
    objective = validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )["objectives"][0]
    fe_row = objective["target"]["composition_window"]["oxides"][
        "Fe_total_as_Fe2O3_wt_pct"
    ]
    assert fe_row["tier"] == expected_tier
    assert fe_row["needs_experiment"] is True
    assert fe_row["min"] >= 0.0
    assert fe_row["max"] > fe_row["min"]
    assert "provenance" in fe_row


@pytest.mark.parametrize(
    ("target_id", "species", "completeness_min"),
    [
        ("pc-extract-na", "Na", 0.95),
        ("pc-extract-k", "K", 0.90),
        ("pc-extract-fe", "Fe", 0.85),
        ("pc-extract-al", "Al", 1.0),
    ],
)
def test_target_menu_extract_rows_are_windowless(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_id: str,
    species: str,
    completeness_min: float,
) -> None:
    out = tmp_path / f"{target_id}.yaml"

    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", target_id, out)

    profile = yaml.safe_load(out.read_text())
    objective = validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )["objectives"][0]
    target = objective["target"]
    assert target["species_vector"][species] == "extract"
    assert "composition_window" not in target
    assert target["extraction"]["completeness_min"][species] == pytest.approx(
        completeness_min
    )
    assert target["score_weights"]["extraction"] == pytest.approx(1.0)
    assert target["score_weights"]["composition"] == pytest.approx(0.0)


def test_target_menu_retain_alkali_c3_uses_soft_alkali_window_without_fe_hard_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-glass-retain-na-k-c3.yaml"

    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", "pc-glass-retain-na-k-c3", out)

    profile = validate_profile(
        yaml.safe_load(out.read_text()),
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )
    constraints = profile["constraints"]
    target = profile["objectives"][0]["target"]
    oxides = target["composition_window"]["oxides"]

    assert "delivered_stream_purity" not in constraints["gates"]
    assert "extraction_completeness" not in constraints["gates"]
    assert "target_species" not in constraints
    assert target["species_vector"]["Fe"] == "retain"
    assert target["species_vector"]["Na"] == "retain"
    assert target["species_vector"]["K"] == "retain"
    assert target["species_vector"]["Si"] == "retain"
    assert target["extraction"]["completeness_min"] == {}
    assert oxides["Na2O_plus_K2O"]["min"] == pytest.approx(5.0)
    assert oxides["Na2O_plus_K2O"]["max"] == pytest.approx(18.0)
    assert oxides["Na2O_plus_K2O"]["strict"] is False
    assert "retained_alkali_ceiling_soft_rank" in oxides["Na2O_plus_K2O"]["provenance"]


def test_target_menu_glass_clear_uses_rev32_strict_soft_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-glass-clear.yaml"

    _run_generator(monkeypatch, tmp_path, "lunar_mare_low_ti", "pc-glass-clear", out)

    profile = yaml.safe_load(out.read_text())
    objective = validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )["objectives"][0]
    oxides = objective["target"]["composition_window"]["oxides"]
    assert oxides["FeO_total"]["min"] == pytest.approx(0.0)
    assert oxides["FeO_total"]["max"] == pytest.approx(0.5)
    assert oxides["FeO_total"]["strict"] is True
    assert oxides["Al2O3"]["min"] == pytest.approx(15.0)
    assert oxides["Al2O3"]["max"] == pytest.approx(20.0)
    assert oxides["Al2O3"]["strict"] is False
    assert oxides["Al2O3"]["weight"] == pytest.approx(2.0)


def test_target_menu_ratio_seed_has_strict_companions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out = tmp_path / "pc-ceramic-ca-al-ratio-seed.yaml"

    _run_generator(
        monkeypatch,
        tmp_path,
        "lunar_mare_low_ti",
        "pc-ceramic-ca-al-ratio-seed",
        out,
    )

    profile = yaml.safe_load(out.read_text())
    objective = validate_profile(
        profile,
        expected_feedstock="lunar_mare_low_ti",
        source=out,
    )["objectives"][0]
    window = objective["target"]["composition_window"]
    assert all(row["strict"] is True for row in window["oxides"].values())
    ratio = window["ratios"][0]
    assert ratio["numerator"] == ("CaO",)
    assert ratio["denominator"] == ("Al2O3",)
    assert ratio["min"] == pytest.approx(0.45)
    assert ratio["max"] == pytest.approx(0.75)


def test_target_menu_all_emits_materialized_seed_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "profiles"

    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))
    assert generator.main(
        [
            "lunar_mare_low_ti",
            "--target",
            "all",
            "--db",
            str(tmp_path / "cache.db"),
            "--out",
            str(out_dir),
        ]
    ) == 0

    emitted = sorted(path.name for path in out_dir.glob("*.yaml"))
    assert emitted == [
        f"lunar_mare_low_ti__{target_id}.real.yaml"
        for target_id in sorted(generator.TARGET_MENU)
    ]

    for path in out_dir.glob("*.yaml"):
        profile = validate_profile(
            yaml.safe_load(path.read_text()),
            expected_feedstock="lunar_mare_low_ti",
            source=path,
        )
        _assert_pressure_default_boxes_are_jointly_feasible(profile)


def _run_generator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    feedstock: str,
    target_id: str,
    out: Path,
) -> None:
    monkeypatch.setattr(generator, "_runtime_engine_identity", lambda: ("stub-engine", "test"))
    assert generator.main(
        [
            feedstock,
            "--target",
            target_id,
            "--db",
            str(tmp_path / "cache.db"),
            "--out",
            str(out),
        ]
    ) == 0


def _assert_pressure_default_boxes_are_jointly_feasible(profile) -> None:
    for seed in profile["seed_recipes"]:
        campaigns = (seed.get("patch") or {}).get("campaigns") or {}
        for campaign, config in campaigns.items():
            if not isinstance(config, dict):
                continue
            if (
                "pO2_mbar_default" not in config
                or "p_total_mbar_default" not in config
            ):
                continue
            _, po2_high = _numeric_interval(config["pO2_mbar_default"])
            total_low, _ = _numeric_interval(config["p_total_mbar_default"])
            assert po2_high <= total_low, (
                campaign,
                "pO2_mbar_default",
                config["pO2_mbar_default"],
                "p_total_mbar_default",
                config["p_total_mbar_default"],
            )


def _numeric_interval(value) -> tuple[float, float]:
    if isinstance(value, list):
        assert len(value) == 2
        return float(value[0]), float(value[1])
    return float(value), float(value)
