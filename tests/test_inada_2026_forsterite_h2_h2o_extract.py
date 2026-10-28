"""Table 1 run rows are retained as context because its flux unit has no token."""

from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/inada-2026-forsterite-h2-h2o.yaml"


def test_table1_render_transcription_and_row_count() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    context = document["species"]["Mg2SiO4"]["context"]
    table = next(row for row in context if row["observation_id"] == "inada_2026_table1_run_level_evaporation_measurements")
    rows = table["values"]["rows"]
    assert len(rows) == 66
    assert table["values"]["measured_rate_row_count"] == 66
    assert Counter(row["temperature_K_as_printed"] for row in rows) == {
        "1400": 27,
        "1450": 4,
        "1500": 15,
        "1600": 20,
    }
    # These printed cells were independently checked on the 600 dpi render, PDF p. 6.
    by_run = {row["run_as_printed"]: row for row in rows}
    assert by_run["#025"]["QMS_signal_ratio_IH2O_over_IH2_as_printed"] == "(7.8 ± 9.7) × 10− 4"
    assert by_run["#025"]["evaporation_rate_particles_cm-2_s-1_as_printed"] == "(1.73 ± 0.02) × 1013"
    assert by_run["#035"]["setup_as_printed"] == "(ii)f)"
    assert by_run["#035"]["weight_loss_g_as_printed"] == "(2.61 ± 0.11) × 10− 5"
    assert by_run["#085"]["evaporation_rate_particles_cm-2_s-1_as_printed"] == "(9.72 ± 0.08) × 1013"
    assert all(row["temperature_K_as_printed"] in {"1400", "1450", "1500", "1600"} for row in rows)
    assert table["values"]["admission_status"] == "typed_refusal"
    assert "cm−2 s−1" in table["values"]["reason"]


def test_tables_and_deferred_figures_migrate_without_scored_rows() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    contexts = document["species"]["Mg2SiO4"]["context"]
    figures = [row for row in contexts if row["type"] == "figure_record"]
    assert len(figures) == 4
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4"}
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("species" in row["values"] and "phase" in row["values"] and "axes_as_printed" in row["values"] for row in figures)
    figure4 = next(row for row in figures if row["locator"]["figure"] == "4")
    assert figure4["values"]["axes_as_printed"] == {"x": "1/T", "y": "log(PH2O/PH2)"}

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
