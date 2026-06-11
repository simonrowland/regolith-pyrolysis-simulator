from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import scripts.make_recipe_db_profile as generator
from simulator.optimize.evaluate import EngineBugAbort, evaluate
from simulator.optimize.profiles import validate_profile
from simulator.optimize.recipe import RecipePatch
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


@pytest.mark.parametrize("target_id", sorted(generator.TARGET_MENU))
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
    objective = validated["objectives"][0]
    assert objective["type"] == "composition_target"
    assert objective["id"] == target_id
    assert objective["metric"] == f"composition_target:{target_id}"
    assert objective["target"]["require_coating_gate"] is True
    assert {row["metric"] for row in validated["objectives"][1:]} == {
        "energy_kWh",
        "duration_h",
    }


@pytest.mark.parametrize("target_id", sorted(generator.TARGET_MENU))
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
