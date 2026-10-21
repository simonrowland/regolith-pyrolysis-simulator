from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ariyo-holappa-2000-titanium-activity.yaml"
SID = "ariyo-holappa-2000-titanium-activity"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))

# Independently transcribed from the 600 dpi PDF renders of Table 1, pages 12–13.
RENDER_GRID = [
    ("KCST1", "38.4", "42.3", "19.3", "0.416", "0.427", "0.157", "0.53"),
    ("KCST2", "33.6", "36.2", "30.2", "0.374", "0.375", "0.252", "0.63"),
    ("KCST3", "34.0", "40.9", "25.1", "0.373", "0.420", "0.207", "0.55"),
    ("KCST4", "28.5", "39.8", "31.7", "0.319", "0.415", "0.267", "0.81"),
    ("KCST5", "27.4", "34.0", "38.6", "0.311", "0.360", "0.329", "0.42"),
    ("KCST6", "25.8", "35.3", "38.9", "0.293", "0.375", "0.333", "0.45"),
    ("KCST7", "37.7", "39.0", "23.3", "0.411", "0.398", "0.191", "0.39"),
    ("KCST8", "33.0", "30.0", "37.0", "0.372", "0.315", "0.314", "0.81"),
    ("SCST9", "34.3", "42.2", "23.5", "0.376", "0.431", "0.193", "0.68"),
    ("SCST10", "42.4", "47.0", "10.6", "0.450", "0.465", "0.084", "2.28"),
    ("SCST11", "43.7", "45.8", "10.5", "0.463", "0.453", "0.084", "1.28"),
    ("SCST12", "42.5", "34.5", "23.0", "0.462", "0.350", "0.188", "1.12"),
    ("SCST13", "40.7", "31.4", "27.89", "0.447", "0.323", "0.230", "1.68"),
]


def _assert_context_matches_grid(document: dict, grid: list[tuple[str, ...]]) -> None:
    rows = document["species"]["TiO1.67"]["context"][0]["values"]["activity_coefficient_rows"]
    assert len(rows) == 13
    actual = [
        (r["sample_id"], r["CaO_wt_pct"], r["SiO2_wt_pct"], r["TiO1.67_wt_pct"],
         r["X_CaO"], r["X_SiO2"], r["X_TiO1.67"], r["gamma_TiO1.67"])
        for r in rows
    ]
    assert actual == grid


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_ariyo_table1_render_grid_and_deferred_lineage(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert document["species"]["TiO1.67"]["observations"] == []
    _assert_context_matches_grid(document, RENDER_GRID)
    context = document["species"]["TiO1.67"]["context"]
    assert context[0]["values"]["schema_gap"].startswith("Derived activity coefficients")
    assert [c["values"]["quantity_as_printed"] for c in context[3:]] == [
        "log activity of TiO1.67 and Ti/O deoxidation relation",
        "activity coefficient of titanium oxide",
    ]
    assert context[3]["locator"]["page"] == context[4]["locator"]["page"] == 11
    assert all(c["values"]["digitisation"] == "deferred to a figure pass" for c in context[3:])

    # Each mutation must be caught: one independently transcribed render cell,
    # and one extract cell.
    bad_grid = deepcopy(RENDER_GRID)
    bad_grid[0] = ("KCST1", "38.5", *bad_grid[0][2:])
    with pytest.raises(AssertionError):
        _assert_context_matches_grid(document, bad_grid)
    bad_document = deepcopy(document)
    bad_document["species"]["TiO1.67"]["context"][0]["values"]["activity_coefficient_rows"][0]["gamma_TiO1.67"] = "0.54"
    with pytest.raises(AssertionError):
        _assert_context_matches_grid(bad_document, RENDER_GRID)

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert not [o for o in migrated.observations.values() if o.source_id == SID]
