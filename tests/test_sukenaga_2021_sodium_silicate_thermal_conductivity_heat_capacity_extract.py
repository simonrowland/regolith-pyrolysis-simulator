"""Table 1 values and deferred figures for Sukenaga et al. (2021)."""

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sukenaga-2021-sodium-silicate-thermal-conductivity-heat-capacity.yaml"


def test_table1_render_transcription_and_deferred_figure_records() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["SiO2"]["context"]
    table = next(row for row in context if row["observation_id"].endswith("table1_room_temperature_glass_properties"))
    rows = table["values"]["rows"]
    assert rows == [
        {
            "sample": "SiO2 glass",
            "composition_mol_pct": {"SiO2": 100, "Na2O": "not_reported"},
            "thermal_diffusivity_10^-7_m2_s": 8.67,
            "thermal_diffusivity_sigma_10^-7_m2_s": 0.17,
            "cp_J_kg_K": 736,
            "cp_attribution": "Richet et al. (1982), footnote 1",
            "density_kg_m3": 2200,
            "density_sigma_kg_m3": 2,
            "thermal_conductivity_W_m_K": 1.40,
        },
        {
            "sample": "NS2 glass (Na2O·2SiO2)",
            "nominal_composition_mol_pct": {"SiO2": 67.0, "Na2O": 33.0},
            "analyzed_composition_mol_pct": {"SiO2": 66.1, "Na2O": 33.9},
            "thermal_diffusivity_10^-7_m2_s": 4.40,
            "thermal_diffusivity_sigma_10^-7_m2_s": 0.11,
            "cp_J_kg_K": 886,
            "density_kg_m3": 2490,
            "density_sigma_kg_m3": 10,
            "thermal_conductivity_W_m_K": 0.971,
        },
    ]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert [row["locator"]["figure"] for row in figures] == ["1", "2", "3"]
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["series"] for row in figures)


def test_context_only_extract_migrates_without_hard_issues() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
