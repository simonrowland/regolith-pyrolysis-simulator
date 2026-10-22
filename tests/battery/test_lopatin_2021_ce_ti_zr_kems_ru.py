"""Render-grounded extraction checks for Lopatin et al. 2021 Table 1."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "lopatin-2021-ce-ti-zr-kems-ru"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.pdf"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"

# Each printed cell was transcribed from the 600 dpi render of physical PDF page 3.
TABLE1_RENDER_GRID = (
    ("0.10", "0.18", "0.72", "0.12", "0.053", "1.2", "0.29"),
    ("–", "0.18", "0.82", "–", "0.020а", "–", "0.11а"),
    ("0.05", "0.09", "0.86", "0.16", "0.024", "3.2", "0.27"),
    ("–", "0.09", "0.91", "–", "0.010а", "–", "0.11а"),
)

FORMULAS = ("TiO2", "CeO2", "ZrO2")
QUOTED_ATTRIBUTION = (
    "Table 1 footnote a: data from [6], Meng Z., Ren X., Wei P. (2014), "
    "J. Am. Ceram. Soc. 97, 1566, doi: 10.1111/jace.12886."
)


def test_table_cells_bind_to_sample_and_migrate_with_printed_values() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"source PDF is absent: {SOURCE}")

    page3 = subprocess.run(
        ["pdftotext", "-f", "3", "-l", "3", "-layout", str(SOURCE), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    page3_flat = re.sub(r"\s+", " ", page3)
    page2 = subprocess.run(
        ["pdftotext", "-f", "2", "-l", "2", "-layout", str(SOURCE), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    page2_flat = re.sub(r"-\s+", "", page2)
    page2_flat = re.sub(r"\s+", " ", page2_flat)
    page4 = subprocess.run(
        ["pdftotext", "-f", "4", "-l", "4", "-layout", str(SOURCE), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Таблица 1." in page3_flat
    assert "при 2250 K" in page3_flat
    assert "Данные из работы [6]" in page3_flat
    for row in TABLE1_RENDER_GRID:
        assert " ".join(row) in page3_flat
    assert "2.0±0.1" in page3_flat
    assert "2060" in page3_flat and "2160 K" in page3_flat
    assert "7.0, 10.2, 5.5 и 9.7 эВ (±0.3)" in page2_flat
    assert "2100–2150 K состоит" in page2_flat
    assert "только после повышения температуры" in page2_flat
    assert "30 В" in page4
    assert "точностью ±10°С" in page4
    assert "1900‒2200 K" in page4
    assert "Meng Z., Ren X., Wei P." in page4
    assert "10.1111/jace.12886" in page4

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected: dict[tuple[int, str, str], str] = {}
    for row_number, row in enumerate(TABLE1_RENDER_GRID, start=1):
        activities = {"TiO2": row[3], "CeO2": row[4]}
        gammas = {"TiO2": row[5], "CeO2": row[6]}
        for formula, printed in activities.items():
            if printed != "–":
                expected[(row_number, formula, "activity")] = printed.rstrip("а")
        for formula, printed in gammas.items():
            if printed != "–":
                expected[(row_number, formula, "activity_coefficient")] = printed.rstrip("а")

    raw_table: dict[tuple[int, str, str], dict] = {}
    for formula, container in extract["species"].items():
        for record in container.get("observations", []):
            if record.get("locator", {}).get("table") != "1":
                continue
            row_number = int(record["locator"]["row"])
            quantity = record["values"]["quantity"]
            raw_table[(row_number, formula, quantity)] = record
    assert set(raw_table) == set(expected)
    assert len(raw_table) == 12
    assert all(
        "Table 1 specifies 2250 K" in record["values"]["row_note"]
        and "1900–2200 K" in record["values"]["row_note"]
        for record in raw_table.values()
    )

    for key, printed in expected.items():
        row_number, formula, quantity = key
        record = raw_table[key]
        field = "activity" if quantity == "activity" else "activity_coefficient"
        assert record["values"][field] == printed, key
        assert record["locator"]["page"] == 3
        assert record["locator"]["published_page"] == 1570
        assert record["locator"]["pdf_page_index"] == 2
        if row_number in {2, 4}:
            assert record["values"]["method_class"] == "quoted_attributed"
            assert record["values"]["evidence_class"] == "quoted_attributed"
            assert record["values"]["attribution"] == QUOTED_ATTRIBUTION
        elif quantity == "activity_coefficient":
            assert record["values"]["method_class"] == "measured_reduced"
            assert record["derived_from"] == [
                f"lopatin_2021_table1_{formula.lower()}_activity_row_{row_number:02}"
            ]

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SID
    ]
    assert len(migrated) == 13
    assert all(observation.value.kind is ValueKind.POINT for observation in migrated)

    table1 = [
        observation
        for observation in migrated
        if observation.locator.table == "1"
    ]
    assert len(table1) == 12
    points: Counter[tuple[int, str, str, Decimal]] = Counter()
    for observation in table1:
        identity = observation.identity
        formula = identity.species.formula
        quantity = identity.quantity.value.value
        row_number = int(observation.observation_id.split("row_", 1)[1])
        expected_value = Decimal(expected[(row_number, formula, quantity)])
        assert observation.value.point == expected_value
        assert identity.species.formula == formula
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == Decimal("2250")
        assert identity.composition.is_value
        expected_composition = {
            component: Decimal(value)
            for component, value in zip(FORMULAS, TABLE1_RENDER_GRID[row_number - 1][:3])
            if value != "–"
        }
        assert dict(identity.composition.value.components) == expected_composition
        assert observation.point_conditions is not None
        assert "temperature_K" in observation.point_conditions
        assert observation.point_conditions["temperature_K"].locator.page == 3
        points[(row_number, formula, quantity, expected_value)] += 1
    assert len(points) == 12
    ratio = next(
        observation
        for observation in migrated
        if observation.identity.quantity.value is Quantity.ION_INTENSITY_RATIO
    )
    assert "lopatin_2021_tio2_ti3o5_reference_ion_ratio" in ratio.observation_id
    assert ratio.value.point == Decimal("2.0")
    assert ratio.uncertainty.kind.value == "printed"
    assert ratio.uncertainty.verbatim["sigma"] == "0.1"

    contexts = extract["species"]["TiO2"]["context"]
    vapor_results = next(
        context for context in contexts if context["type"] == "measured_text_data_context"
    )
    assert vapor_results["locator"]["page"] == 2
    assert vapor_results["locator"]["published_page"] == 1569
    assert vapor_results["values"]["candidate_quantity_tokens"] == [
        "ion_intensity",
        "ion_intensity_ratio",
        "p_partial",
    ]
    assert vapor_results["values"]["vapor_temperature_range_K_as_printed"] == ["2100", "2150"]
    assert vapor_results["values"]["ion_appearance_energies_eV_as_printed"] == {
        "TiO+": "7.0 ± 0.3",
        "TiO2+": "10.2 ± 0.3",
        "CeO+": "5.5 ± 0.3",
        "CeO2+": "9.7 ± 0.3",
    }
    assert "2700 K" in vapor_results["values"]["zirconium_oxide_spectral_observation_as_printed"]
    method = next(context for context in contexts if context["type"] == "method_context")
    assert method["locator"]["page"] == 4
    assert method["locator"]["published_page"] == 1571
    assert method["values"]["ionizing_voltage_V_as_printed"] == "30"
    assert method["values"]["optical_pyrometer_range_K_as_printed"] == ["1900", "2200"]
