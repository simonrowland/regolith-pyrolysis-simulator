"""Pins Alexander (2001)'s printed diffusion context and deferred figures."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/alexander-2001-maps.yaml"


def test_table_3_render_values_and_row_count():
    extract = yaml.safe_load(EXTRACT.read_text())
    rows = extract["species"]["SiO2"]["context"][0]["values"]["points"]

    assert len(rows) == 4
    assert [row["D0_m2_s"] for row in rows] == [1.98e-5, 1.56e-5, 1.55e-4, 3.7e-6]
    assert [row["temperature_C"] for row in rows] == [
        "1350–1500",
        "1350–1500",
        "1294–1398",
        "1320–1600",
    ]
    assert rows[3]["E_uncertainty_kJ_mol"] == 2


def test_figures_are_queued_without_digitised_values():
    extract = yaml.safe_load(EXTRACT.read_text())
    queued = extract["species"]["SiO2"]["context"][1]["values"]["figures"]

    assert len(queued) == 18
    assert [row["figure"] for row in queued] == [str(n) for n in range(1, 19)]
    assert all("caption" in row and "page" in row for row in queued)
    assert extract["species"]["SiO2"]["context"][1]["values"]["digitisation"] == (
        "deferred to a figure pass"
    )
