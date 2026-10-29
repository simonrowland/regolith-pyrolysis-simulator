from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1996-fe-mn-cr-redox-interactions.yaml"

# Independent row transcription from the 600 dpi PDF render of Table 4 (PDF p. 6).
TABLE4_GRID = [
    (s, fe, mn, t, sf, sm, pf, pm, mf, mm)
    for s, fe, mn, rows in [
        ("FeMn1", "0.98", "0.98", [(1400, "0.13", "0.04", "0.09", "0", "present", "0"), (1300, "0.06", "0.05", "0.01", "0", "present", "0"), (1200, "0.04", "0.06", "0", "0.03", "absent", "0.03"), (1100, "0.025", "0.07", "0", "0.045", "absent", "0.04")]),
        ("FeMn2", "0.49", "0.98", [(1400, "0.065", "0.04", "0.02", "0", "present", "0"), (1300, "0.03", "0.05", "0", "0.025", "absent", "0.03"), (1200, "0.015", "0.06", "0", "0.05", "absent", "0.05"), (1100, "0.01", "0.07", "0", "0.06", "absent", "0.05")]),
        ("FeMn3", "0.20", "0.98", [(1400, "0.025", "0.04", "0", "0.015", "absent", "0.02"), (1300, "0.01", "0.05", "0", "0.045", "absent", "0.04"), (1200, "0.008", "0.06", "0", "0.05", "absent", "0.04"), (1100, "0.005", "0.07", "0", "0.065", "absent", "0.06")]),
    ]
    for t, sf, sm, pf, pm, mf, mm in rows
]

# Independent row transcription from the 600 dpi PDF render of Table 5 (PDF p. 7).
TABLE5_GRID = [
    (s, fe, cr, t, sf, sc, pf, pc, mf, mc)
    for s, fe, cr, rows in [
        ("FeCr1", "0.98", "0.055", [(1400, "0.13", "0.006", "0.04", "0", "present", "0"), (1300, "0.06", "0.011", "0.01", "0", "present", "0"), (1200, "0.04", "0.016", "0", "0.003", "absent", "0.004"), (1100, "0.025", "0.016", "0", "0.008", "absent", "0.009")]),
        ("FeCr2", "0.48", "0.054", [(1400, "0.06", "0.006", "0.015", "0", "present", "0"), (1300, "0.03", "0.011", "0", "0", "present", "0"), (1200, "0.02", "0.016", "0", "0.009", "absent", "0.009"), (1100, "0.01", "0.016", "0", "0.012", "absent", "0.017")]),
        ("FeCr3", "0.195", "0.053", [(1400, "0.025", "0.006", "0.002", "0", "present", "0"), (1300, "0.01", "0.011", "0", "0.007", "absent", "0.007"), (1200, "0.008", "0.016", "0", "0.019", "absent", "0.015"), (1100, "0.005", "0.016", "0", "0.014", "absent", "0.02")]),
        ("FeCr4", "0.98", "0.17", [(1400, "0.13", "0.017", "0.03", "0", "present", "0"), (1300, "0.06", "0.035", "0", "0.015", "absent", "0.013"), (1200, "0.04", "0.05", "0", "0.037", "absent", "0.04"), (1100, "0.025", "0.05", "0", "0.04", "absent", ">0.03")]),
        ("FeCr5", "0.98", "0.31", [(1400, "0.13", "0.031", "0.01", "0", "present", "0"), (1300, "0.06", "0.061", "0", "0.04", "absent", ">0.03"), (1200, "0.04", "0.09", "0", "0.08", "absent", ">0.03"), (1100, "0.025", "0.09", "0", "0.08", "absent", ">0.03")]),
    ]
    for t, sf, sc, pf, pc, mf, mc in rows
]


def _grid_rows(context_record, second_element):
    return [
        (
            row["sample"], row["Fe_wt%"], row[f"{second_element}_wt%"], row["T_C"],
            *row["separate"], *row["predicted"], *row["measured"],
        )
        for row in context_record["values"]["rows"]
    ]


def _assert_grid(actual, expected):
    assert len(actual) == len(expected)
    assert actual == expected


def test_schreiber_grid_is_render_pinned_and_context_only_values_do_not_migrate_as_observations():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(item.value.kind is not ValueKind.UNAVAILABLE for item in migrator.result.observations.values())

    mn_table = next(item for item in source["species"]["Mn"]["context"] if item["observation_id"].endswith("table4_manganese_ion_concentrations"))
    cr_table = next(item for item in source["species"]["Cr"]["context"] if item["observation_id"].endswith("table5_chromium_ion_concentrations"))
    actual4 = _grid_rows(mn_table, "Mn")
    actual5 = _grid_rows(cr_table, "Cr")
    _assert_grid(actual4, TABLE4_GRID)
    _assert_grid(actual5, TABLE5_GRID)
    assert len(actual4) == 12
    assert len(actual5) == 20
    assert actual4[0][0:4] == ("FeMn1", "0.98", "0.98", 1400)
    assert actual5[19][-1] == ">0.03"

    # Mutation proof: independently altering either side of a pinned cell is caught.
    changed_grid = deepcopy(TABLE4_GRID)
    changed_grid[0] = (*changed_grid[0][:-1], "0.1")
    with pytest.raises(AssertionError):
        _assert_grid(actual4, changed_grid)
    changed_extract = deepcopy(actual5)
    changed_extract[0] = (*changed_extract[0][:-1], "0.1")
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract, TABLE5_GRID)

    for species, entries in source["species"].items():
        assert entries["observations"] == []
        for figure in (entry for entry in entries["context"] if entry.get("type") == "figure_only"):
            assert figure["values"]["digitisation"] == "deferred to a figure pass"
            assert "points" not in figure["values"]
            assert all(isinstance(series["marker_count"], int) for series in figure["values"]["series"])

    assert Decimal("0.004") == Decimal(cr_table["values"]["rows"][2]["measured"][1])
