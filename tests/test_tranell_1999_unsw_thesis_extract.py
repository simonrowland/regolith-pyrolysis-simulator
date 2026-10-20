"""Render-pinned Table 4.2 data and isolated migration for Tranell's thesis."""

from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tranell-1999-unsw-thesis.yaml"
TABLE42_RENDER = """
Sample | Mole fraction Si, XSi (ppm) | gamma Si
1 | 1051 | 0.0079
2 | 1602 | 0.0148
""".strip()


def document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def table42_rows(extract: dict) -> list[list[str]]:
    observations = [
        row
        for row in extract["species"]["Si"]["observations"]
        if row["type"] == "activity_coefficient"
    ]
    return [
        [
            row["values"]["sample_as_printed"],
            row["values"]["silicon_mole_fraction_ppm_as_printed"],
            row["values"]["gamma"],
        ]
        for row in observations
    ]


def assert_table42_matches_render(extract: dict, rendered: str) -> None:
    expected_rows = [line.split(" | ") for line in rendered.splitlines()][1:]
    assert table42_rows(extract) == expected_rows


def test_table42_grid_is_bound_to_render_and_both_mutations_fail() -> None:
    extract = document()
    assert_table42_matches_render(extract, TABLE42_RENDER)

    changed_render = TABLE42_RENDER.replace("1051", "9999", 1)
    with pytest.raises(AssertionError):
        assert_table42_matches_render(extract, changed_render)

    changed_extract = deepcopy(extract)
    changed_extract["species"]["Si"]["observations"][2]["values"]["gamma"] = "0.9999"
    with pytest.raises(AssertionError):
        assert_table42_matches_render(changed_extract, TABLE42_RENDER)


def test_table42_source_locator_is_present_on_physical_page() -> None:
    corpus_root = Path(
        os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
    )
    pdf_path = corpus_root / "raw/tranell-1999-unsw-thesis/tranell-1999-unsw-thesis.pdf"
    if not pdf_path.is_file():
        pytest.skip(f"Tranell thesis text is absent from {corpus_root}")

    page_text = subprocess.run(
        ["pdftotext", "-f", "118", "-l", "118", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Table 4.2" in page_text
    assert "1051" in page_text and "0.0079" in page_text
    assert "1602" in page_text and "0.0148" in page_text


def test_isolated_migration_preserves_species_temperature_and_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 4
    assert {row.identity.species.formula for row in observations} == {"Si"}
    for row in observations:
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.value == Decimal("1873")
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.value == Decimal("1873")
        oxygen = row.point_conditions["oxygen_partial_pressure"]
        assert oxygen.state.is_value
        assert str(oxygen.state.value) == "10^-12"
        assert oxygen.locator.page == 118
        assert row.locator.page == row.locator.pdf_page_index + 1
