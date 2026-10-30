from __future__ import annotations

from pathlib import Path
from decimal import Decimal

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "evans-2009-sulfur-mn-ni-w-xanes"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_xanes_tables_and_deferred_figures_preserve_printed_evidence() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    sulfur = document["species"]["S"]
    context = sulfur["context"]

    def record(observation_id: str) -> dict:
        return next(row for row in context if row["observation_id"] == observation_id)

    table_2a = record("evans_2009_table_2a_mn_glass_compositions")["values"]["rows_as_printed"]
    assert "B310706p1a |" in table_2a
    assert "B020806p6b |" in table_2a
    assert "bdl" in table_2a
    assert "101.5(5)" in table_2a
    table_2b = record("evans_2009_table_2b_w_glass_compositions")["values"]["rows_as_printed"]
    assert "B301205p3b |" in table_2b
    assert "B141106p1a |" in table_2b
    table_2c = record("evans_2009_table_2c_ni_glass_compositions")["values"]["rows_as_printed"]
    assert "B120406p6b |" in table_2c
    assert "B010806p1a |" in table_2c

    typed_vectors = {}
    all_source_points = []
    for formula, species_data in document["species"].items():
        for observation in species_data["observations"]:
            if observation["type"] != "equilibrium_phase_composition":
                continue
            for point in observation["values"]["points"]:
                all_source_points.append(point)
                typed_vectors.setdefault(point["sample"], {})[formula] = point["value_as_published"]

    assert len(all_source_points) == 381
    assert len(typed_vectors) == 50
    assert typed_vectors["B030106p2b"]["Si"] == "19.93(8)"
    assert typed_vectors["B030106p2b"]["S"] == "0.224(1)"
    assert typed_vectors["B030106p2b"]["Mn"] == "0.381(3)"
    assert typed_vectors["B301205p3b"]["Si"] == "19.8(1)"
    assert typed_vectors["B111006p2"]["Ca"] == "24.(1)"
    assert typed_vectors["B120406p6b"]["O"] == "41.(1)"
    assert typed_vectors["B220806p4"]["W"] == "2.23(3)"
    assert typed_vectors["B010806p2"]["Ni"] == "0.132(8)"
    expected_sigma = {
        ("B030106p2b", "S"): "0.001",
        ("B301205p3b", "Si"): "0.1",
        ("B111006p2", "Ca"): "1",
        ("B120406p6b", "O"): "1",
    }
    assert all("sigma" in point for point in all_source_points)
    for point in all_source_points:
        key = (point["sample"], point["species_as_printed"])
        if key in expected_sigma:
            assert point["sigma"] == expected_sigma[key]
    assert all(point["run_as_printed"] == point["sample"][:7] for point in all_source_points)
    assert all(
        point["locator"]["page"] in {7, 8}
        and point["locator"]["published_page"] == (6853 if point["locator"]["page"] == 7 else 6854)
        and point["point_conditions"]["T_C"]["state"]["value"] == "1400"
        for point in all_source_points
    )

    # Independent transcription from the 600 dpi rendering of PDF page 10, Table 3.
    table_3 = record("evans_2009_table_3_s_xanes_peak_proportions")["values"]["rows_as_printed"]
    measured = [
        tuple(part.strip() for part in line.split("|"))
        for line in table_3.splitlines()
        if line.strip()
    ]
    assert [(row[0], *row[7:11]) for row in measured] == [
        ("B310706p1", "0.11(3)", "0.34(7)", "0.36(5)", "0.19(2)"),
        ("B310706p2", "0.33(3)", "0.21(5)", "0.44(4)", "0.020(6)"),
        ("B310706p3", "0.26(3)", "0.26(6)", "0.36(4)", "0.11(1)"),
        ("B310706p4", "0.31(3)", "0.19(4)", "0.44(4)", "0.06(1)"),
        ("B310706p6", "0.03(4)", "0.22(7)", "0.06(4)", "0.68(6)"),
        ("B151106p1", "0.10(3)", "0.22(6)", "0.27(5)", "0.41(5)"),
        ("B151106p1N", "0.13(4)", "0.45(10)", "0.40(7)", "0.02(1)"),
        ("B151106p4", "0.19(4)", "0.40(9)", "0.41(6)", "n.a."),
        ("B151106p5", "0.15(4)", "0.43(9)", "0.40(6)", "0.03(1)"),
        ("B151106p6", "0.16(4)", "0.44(10)", "0.40(7)", "n.a."),
        ("B150906p1", "0.14(4)", "0.36(9)", "0.45(7)", "0.05(2)"),
        ("B150906p2", "0.15(3)", "0.38(8)", "0.47(6)", "n.a."),
        ("B150906p3", "0.17(4)", "0.34(8)", "0.46(6)", "0.04(1)"),
        ("B150906p4", "0.22(3)", "0.25(6)", "0.52(6)", "0.01(2)"),
        ("B150906p5", "0.16(3)", "0.34(8)", "0.49(6)", "n.a."),
        ("B141106p1", "0.11(4)", "0.3(1)", "0.49(8)", "n.a."),
        ("B141106p2a", "0.13(3)", "0.24(7)", "0.46(6)", "0.09(2)"),
        ("B141106p2b", "0.07(3)", "0.32(9)", "0.43(8)", "0.10(2)"),
        ("B140906p4", "0.15(3)", "0.38(8)", "0.47(6)", "n.a."),
        ("B140906p4D", "0.29(3)", "0.18(5)", "0.53(5)", "n.a."),
        ("B140906p5", "0.21(3)", "0.33(7)", "0.46(5)", "0.001(4)"),
    ]

    table_1 = record("evans_2009_table_1_run_conditions")["values"]["rows_as_printed"]
    assert "B020806 | Mn | 1–10 | -7.18 | -0.84 | -0.61 | -0.7" in table_1
    assert "B190906 | Ni | 1–10 | -3.09 | -11.88 | -1.52 | 3.3" in table_1

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 11
    assert all(row["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all(row["values"]["points"] == [] for row in figures)
    assert all(row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 for row in context)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 381
    assert migrator.result.validation.hard_issues == ()
    migrated = [
        row for row in migrator.result.observations.values()
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(migrated) == 381
    assert all(row.identity.temperature_K.value == Decimal("1673.15") for row in migrated)
    assert all(row.point_conditions["T_C"].state.value == Decimal("1400") for row in migrated)
    assert all(row.locator.page in {7, 8} and row.locator.published_page in {6853, 6854} for row in migrated)
    assert all("fO2_log" not in row.point_conditions and "atmosphere" not in row.point_conditions for row in migrated)
    sulfur = next(
        row for row in migrated
        if row.identity.species.formula == "S" and row.value.point == Decimal("0.224")
    )
    assert sulfur.uncertainty.verbatim["sigma"] == "0.001"
