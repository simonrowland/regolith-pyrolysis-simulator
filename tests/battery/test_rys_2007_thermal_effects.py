from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.identity import quantity_token
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = "rys-2007-alkali-oxide-thermodynamics.yaml"
TABLE7_PARENTS = (
    "rys_2007_table7_t1_structure_change",
    "rys_2007_table7_t2_unidentified_thermal_effect",
    "rys_2007_table7_t3_eutectic",
    "rys_2007_table7_t4_eutectic",
    "rys_2007_table7_t5_structure_change",
)
TABLE9_PARENTS = (
    "rys_2007_table9_eutectic",
    "rys_2007_table9_structure_change",
)


def _rows(result, parents: tuple[str, ...]):
    return [
        row
        for row in result.observations.values()
        if any(parent in row.observation_id for parent in parents)
    ]


def _one(rows, parent: str, sample: str, column: str):
    matches = [
        row
        for row in rows
        if parent in row.observation_id
        and row.locator.note is not None
        and f"row={sample}" in row.locator.note
        and f"column={column}" in row.locator.note
    ]
    assert len(matches) == 1, [row.observation_id for row in matches]
    return matches[0]


def test_rys_tables_7_and_9_thermal_cells_migrate_with_composition_and_locators(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, EXTRACT)
    table7 = _rows(result, TABLE7_PARENTS)
    table9 = _rows(result, TABLE9_PARENTS)

    assert len(table7) == 60
    assert len(table9) == 13
    assert len(table7) + len(table9) == 73
    assert all(quantity_token(row.identity) is Quantity.TRANSITION_TEMPERATURE for row in table7 + table9)
    assert all(row.identity.species.formula == "SiO2" for row in table7 + table9)
    assert all(row.identity.total_pressure_Pa.is_unknown for row in table7 + table9)
    assert all(
        row.point_conditions["total_pressure_Pa"].locator.page == row.locator.page
        for row in table7 + table9
    )
    assert all(row.value.point is not None for row in table7 + table9)

    for row in table7:
        assert row.identity.composition.is_value
    for row in table9:
        if row.locator.note and "row=56Na2O-44SiO2" in row.locator.note:
            assert row.identity.composition.is_unknown
            assert row.identity.composition.reason == (
                "printed_components_total_96_percent_no_reconstruction"
            )
            composition_condition = row.point_conditions["composition"]
            assert composition_condition.state.is_unknown
            assert composition_condition.locator.page == 97
        else:
            assert row.identity.composition.is_value


def test_rys_render_pins_use_physical_pdf_pages_and_table_cells(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, EXTRACT)
    rows = _rows(result, TABLE7_PARENTS + TABLE9_PARENTS)
    rendered_cells = (
        (
            "rys_2007_table7_t1_structure_change",
            "96Na2O-4SiO2",
            "T1 up",
            Decimal("1023.15"),
            82,
            81,
        ),
        (
            "rys_2007_table7_t3_eutectic",
            "92Na2O-8SiO2",
            "T3 up",
            Decimal("1115.15"),
            82,
            81,
        ),
        (
            "rys_2007_table9_eutectic",
            "63Na2O-37SiO2",
            "eutectic up",
            Decimal("1302.15"),
            97,
            96,
        ),
    )
    for parent, sample, column, value, page, pdf_page_index in rendered_cells:
        row = _one(rows, parent, sample, column)
        assert row.value.point == value
        assert row.locator.page == page
        assert row.locator.pdf_page_index == pdf_page_index
