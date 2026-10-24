from __future__ import annotations

from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "shornikov-2018-cai-double-inversion.yaml"
)


def test_shornikov_2018_initial_compositions_and_deferred_figures() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    table = next(
        item
        for item in species["CaO"]["context"]
        if item["observation_id"] == "shornikov_2018_table1_initial_sample_compositions"
    )
    rows = table["values"]["rows"]

    assert len(rows) == 5
    assert table["values"]["numeric_cell_count"] == 20
    assert table["locator"]["page"] == 1
    assert [row["sample"] for row in rows] == [
        "5aN-L",
        "5aN-3",
        "5aN-H",
        "CAI4",
        "CAI B",
    ]
    # These literals were confirmed against the page 1 render at 600 dpi.
    assert rows[0]["composition_mol_pct"]["CaO"] == "11.17"
    assert rows[1]["composition_mol_pct"]["Al2O3"] == "23.83"
    assert rows[2]["composition_mol_pct"]["SiO2"] == "31.76"
    assert rows[3]["composition_mol_pct"]["MgO"] == "24.32"
    assert rows[4]["composition_mol_pct"]["CaO"] == "26.76"
    assert rows[1]["method_class"] == "quoted_attributed"
    assert rows[1]["attribution"] == "[5, 6]"
    assert all(row["locator"]["page"] == 1 for row in rows)

    figure_records = [
        context
        for record in species.values()
        for context in record["context"]
        if context["type"] == "figure_record"
    ]
    assert len(figure_records) == 5
    assert {item["locator"]["figure"] for item in figure_records} == {
        "1",
        "2",
        "3",
        "4",
        "5",
    }
    for figure in figure_records:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "quantity" in values
        assert "species_as_printed" in values
        assert values["temperature_as_printed"] == "2173 K"
        assert values["phase_as_printed"] == "residual melts"
        assert "axes_as_printed" in values
        assert "caption_as_printed" in values
        assert "panel" in values
        assert "series_list" in values
        series_keys = {
            next(iter(entry))
            if isinstance(entry, dict)
            else entry.split(":", maxsplit=1)[0]
            for entry in values["series_list"]
        }
        assert set(values["marker_count_per_series"]) == {
            str(key) for key in series_keys
        }
        assert all(
            isinstance(count, int) and count >= 0
            for count in values["marker_count_per_series"].values()
        )
        assert "points" not in values
