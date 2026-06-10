from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import scripts.make_recipe_db_profile as generator
from simulator.optimize.profiles import validate_profile


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
                "pc-extract-na",
                "--out",
                str(tmp_path / "profile.yaml"),
            ]
        )


@pytest.mark.parametrize(
    ("target_id", "expected_tier"),
    [
        ("pc-glass-clear", "clear_container"),
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
