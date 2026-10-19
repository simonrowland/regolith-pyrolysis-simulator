from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/lead-25-wetzel-2012a.yaml"
)


def _assert_table_and_figure_pins(document: dict) -> None:
    species = document["species"]["SiO"]
    contexts = {row["observation_id"]: row for row in species["context"]}

    table = contexts["wetzel_2012_table1_brendel_parameters"]
    rows = table["values"]["rows"]
    assert len(rows) == 5
    assert rows[0] == {
        "T_K": 40,
        "x0_AS1_cm-1": 957,
        "x0_AS2_cm-1": 1077,
        "x0_SS_cm-1": 693,
    }
    assert rows[-1]["x0_AS1_cm-1"] == 998
    assert table["values"]["oscillator_strength_rows"][2]["X_SS"] == 313
    assert table["values"]["gaussian_width_rows"][4]["sigma_AS1_cm-1"] == 52
    assert table["values"]["epsilon_infinity"] == 3.61
    assert table["values"]["cell_audit"]["numeric_cells_checked"] == 61

    figures = [row for row in species["context"] if row["type"] == "figure_only"]
    assert len(figures) == 8
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in figures
    )
    assert all("marker_count" in series for row in figures for series in row["values"]["series"])
    assert species["observations"] == []


def test_wetzel_table_one_and_deferred_figures_are_preserved_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table_and_figure_pins(document)

    mutated = deepcopy(document)
    mutated["species"]["SiO"]["context"][0]["values"]["rows"][0]["x0_AS1_cm-1"] = 958
    with pytest.raises(AssertionError):
        _assert_table_and_figure_pins(mutated)

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    assert len(migrator.result.queue) == 1
    assert migrator.result.queue[0].observation_id.endswith("::typed_absence")
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 10
