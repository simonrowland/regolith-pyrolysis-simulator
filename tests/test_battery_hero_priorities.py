from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.battery.enums import NoticeKind
from simulator.config import load_config_bundle


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "battery_hero_priorities.py"


def _residual(
    *,
    observation_id: str,
    engine: str,
    species: str,
    temperature_K: float,
    fO2_Pa: float,
    residual: float | None,
    rail: str = "vapour",
    status: str = "match",
    flagged: bool = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_request": {"engine": engine},
        "candidate_observation": {
            "experiment_id": observation_id,
            "identity": {
                "species": {"formula": species},
                "temperature_K": {"tag": "value", "value": str(temperature_K)},
                "fO2_Pa": {"tag": "value", "value": str(fO2_Pa)},
            },
        },
        "rail": rail,
        "reference": observation_id,
        "status": status,
    }
    if residual is not None:
        row["numeric"] = {"operation": "dex", "unit": "dex", "value": residual}
    if flagged:
        row["notices"] = [{"kind": NoticeKind.OUT_OF_CERTIFIED_BAND.value}]
    if status == "refused":
        row["refusal"] = {"reason": "unsupported_species"}
    return row


def test_cli_filters_hero_envelope_and_excludes_flagged_and_refused_rows(
    tmp_path: Path,
) -> None:
    hero_report = {
        "run_metadata": {
            "feedstock_id": "lunar_mare_low_ti",
            "backend": "internal-analytical",
            "activity_sources": {
                "melt_activity": ["openimcc"],
                "vapour": ["openimcc"],
            },
            "engines_used": {
                "active": {
                    "fe_redox_respeciation": "redox-provider",
                    "vapor_pressure": "vapor-provider",
                }
            },
        },
        "per_hour_summary": [
            {
                "hour": 1,
                "T_C": 100.0,
                "pO2_bar": 1e-5,
                "vapor_species_kg_hr": {
                    "CaO": 0.056077,
                    "Fe": 0.055845,
                    "SiO": 0.0600843,
                },
                "condensation_train_kg_cumulative": {"Fe": 0.01},
                "high_t_melt_activity": {
                    "provider": "openimcc",
                    "selected_parent_activities": {"FeO": 0.5},
                },
            },
            {
                "hour": 2,
                "T_C": 200.0,
                "pO2_bar": 2e-5,
                "vapor_species_kg_hr": {
                    "CaO": 0.056077,
                    "Fe": 0.055845,
                    "SiO": 0.0600843,
                },
                "condensation_train_kg_cumulative": {"Fe": 0.02},
                "high_t_melt_activity": {
                    "provider": "openimcc",
                    "selected_parent_activities": {"FeO": 0.6},
                },
            },
        ],
    }
    calcium_enthalpy = _residual(
        observation_id="cao-enthalpy",
        engine="internal-analytical",
        species="CaO",
        temperature_K=430,
        fO2_Pa=1.5,
        residual=10.0,
        rail="thermochemistry",
    )
    calcium_enthalpy["numeric"] = {
        "operation": "absolute",
        "unit": "kJ_per_declared_mol_basis",
        "value": 10.0,
    }
    calcium_ratio = _residual(
        observation_id="cao-ratio",
        engine="internal-analytical",
        species="CaO",
        temperature_K=430,
        fO2_Pa=1.5,
        residual=0.5,
        rail="thermochemistry",
    )
    calcium_ratio["numeric"] = {
        "operation": "ratio",
        "unit": "dimensionless",
        "value": 0.5,
    }
    rows = [
        {"kind": "battery_store_stamp", "revision": "synthetic"},
        calcium_enthalpy,
        calcium_ratio,
        _residual(
            observation_id="fe-three",
            engine="internal-analytical",
            species="Fe",
            temperature_K=400,
            fO2_Pa=1.5,
            residual=3.0,
        ),
        _residual(
            observation_id="fe-four",
            engine="internal-analytical",
            species="Fe",
            temperature_K=410,
            fO2_Pa=1.5,
            residual=4.0,
        ),
        _residual(
            observation_id="fe-flagged",
            engine="internal-analytical",
            species="Fe",
            temperature_K=550,
            fO2_Pa=1.5,
            residual=100.0,
            flagged=True,
        ),
        _residual(
            observation_id="fe-refused",
            engine="internal-analytical",
            species="Fe",
            temperature_K=420,
            fO2_Pa=1.5,
            residual=None,
            status="refused",
        ),
        _residual(
            observation_id="sio-one-half",
            engine="openimcc",
            species="SiO",
            temperature_K=430,
            fO2_Pa=1.5,
            residual=0.5,
            rail="vapour",
        ),
        _residual(
            observation_id="sio-minus-one-half",
            engine="openimcc",
            species="SiO",
            temperature_K=430,
            fO2_Pa=1.5,
            residual=-1.5,
            rail="vapour",
        ),
        _residual(
            observation_id="feo-two",
            engine="openimcc",
            species="FeO",
            temperature_K=430,
            fO2_Pa=1.5,
            residual=2.0,
            rail="melt_activity",
        ),
        _residual(
            observation_id="feo-minus-three",
            engine="openimcc",
            species="FeO",
            temperature_K=430,
            fO2_Pa=1.5,
            residual=-3.0,
            rail="melt_activity",
        ),
        _residual(
            observation_id="wrong-engine",
            engine="other-engine",
            species="Fe",
            temperature_K=400,
            fO2_Pa=1.5,
            residual=500.0,
        ),
        _residual(
            observation_id="wrong-temperature",
            engine="internal-analytical",
            species="Fe",
            temperature_K=700,
            fO2_Pa=1.5,
            residual=500.0,
        ),
        _residual(
            observation_id="wrong-oxygen-pressure",
            engine="internal-analytical",
            species="Fe",
            temperature_K=400,
            fO2_Pa=100.0,
            residual=500.0,
        ),
        _residual(
            observation_id="not-evolved",
            engine="internal-analytical",
            species="Al",
            temperature_K=400,
            fO2_Pa=1.5,
            residual=500.0,
        ),
    ]
    residuals = tmp_path / "residuals.jsonl"
    residuals.write_text("".join(json.dumps(row) + "\n" for row in rows))
    archive = tmp_path / "scores.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(residuals, arcname="data/battery/residuals.jsonl")
    hero_path = tmp_path / "hero-run.json"
    hero_path.write_text(json.dumps(hero_report))
    out = tmp_path / "priorities.md"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--archive",
            str(archive),
            "--hero-run",
            str(hero_path),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(out.with_suffix(".json").read_text())
    envelope = report["hero_envelope"]
    assert envelope["feedstock_composition_class"] == load_config_bundle().feedstocks[
        "lunar_mare_low_ti"
    ]["label"]
    assert envelope["engines_by_role"]["phase_equilibrium"] == [
        "internal-analytical"
    ]
    assert "openimcc" in envelope["engines_by_role"]["melt_activity"]
    assert "openimcc" in envelope["engines_by_role"]["vapour"]
    assert "FeO" in envelope["species_by_rail"]["melt_activity"]
    assert "SiO" in envelope["species_by_rail"]["vapour"]
    groups = report["rows"]
    assert [
        (row["rail"], row["species"], row["engine"], row["unit"])
        for row in groups
    ] == [
        (
            "thermochemistry",
            "CaO",
            "internal-analytical",
            "kJ_per_declared_mol_basis (absolute)",
        ),
        ("vapour", "Fe", "internal-analytical", "dex"),
        ("melt_activity", "FeO", "openimcc", "dex"),
        ("vapour", "SiO", "openimcc", "dex"),
        (
            "thermochemistry",
            "CaO",
            "internal-analytical",
            "dimensionless (ratio)",
        ),
    ]
    calcium_enthalpy_group, iron, iron_oxide, silica, calcium_ratio_group = groups
    assert calcium_enthalpy_group["n"] == 1
    assert calcium_enthalpy_group["rms"] == pytest.approx(10.0)
    assert calcium_ratio_group["n"] == 1
    assert calcium_ratio_group["rms"] == pytest.approx(0.5)
    assert iron["n"] == 2
    assert iron["unit"] == "dex"
    assert iron["rms"] == pytest.approx(math.sqrt(12.5))
    assert iron["median"] == pytest.approx(3.5)
    assert iron["bias"] == pytest.approx(3.5)
    assert iron["flagged_count"] == 1
    assert iron["refused_count"] == 1
    assert iron["worst_observation_ids"] == ["fe-four", "fe-three"]
    iron_mol = 2 * 0.055845 / resolve_species_formula(
        "Fe"
    ).molar_mass_kg_per_mol()
    assert iron["hero_evaporated_mol"] == pytest.approx(iron_mol)
    assert iron_oxide["rms"] == pytest.approx(math.sqrt(6.5))
    assert iron_oxide["hero_evaporated_mol"] == pytest.approx(iron_mol)
    assert silica["rms"] == pytest.approx(math.sqrt(1.25))
    assert silica["median"] == pytest.approx(-0.5)
    assert silica["bias"] == pytest.approx(-0.5)
    markdown = out.read_text()
    assert "| 1 | thermochemistry | CaO | internal-analytical |" in markdown
    assert "| 2 | vapour | Fe | internal-analytical |" in markdown
    assert "| 3 | melt_activity | FeO | openimcc |" in markdown
    assert "| 4 | vapour | SiO | openimcc |" in markdown
