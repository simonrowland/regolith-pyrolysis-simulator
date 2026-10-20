"""Pin Ban-ya et al. prose composition summaries and deferred figure records."""
from __future__ import annotations

import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/banya-2004-sulphide-capacity-cao-al2o3.yaml"
CORPUS_ROOT = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
PDF = CORPUS_ROOT / "raw/banya-2004-sulphide-capacity-cao-al2o3/banya-2004-sulphide-capacity-cao-al2o3.pdf"


def _load_extract():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_printed_solubility_values_and_reference_table_are_preserved():
    doc = _load_extract()
    rows = {row["observation_id"]: row for row in doc["species"]["CaS"]["observations"]}
    binary = rows["banya_2004_cao_al2o3_cas_solubility_summary"]
    assert "value" not in binary["values"]
    assert binary["values"]["admission_status"] == "typed_unavailable"
    assert binary["values"]["value_as_published"] == "about 1.5 to 4 mol% CaS"
    assert "No point was synthesized" in binary["values"]["reason"]
    quoted = rows["banya_2004_cao_caf2_binary_cas_solubility_hawkins"]
    assert quoted["values"]["value"] == "27"
    assert quoted["values"]["approximate"] is True
    assert quoted["values"]["method_class"] == "quoted_attributed"
    assert quoted["values"]["attribution"] == "Hawkins et al. [14]"

    context = doc["species"]["CaS"]["context"]
    table1 = next(row for row in context if row["observation_id"] == "banya_2004_table1_reference_reaction_gibbs_data")
    printed_rows = table1["values"]["rows_as_printed"]
    assert len(printed_rows) == 20
    by_substance = {row["substance"]: row for row in printed_rows}
    assert by_substance["C(g)"]["delta_G0_expression_as_printed"] == "555800 + 11.26 T"
    assert by_substance["COS(g)"]["reaction_as_printed"] == "CO(g) + 1/2S2(g) = COS(g)"
    assert by_substance["SO2(g)"]["reaction_as_printed"] == "2CO2(g) + 1/2S2(g) = 2CO(g) + SO2(g)"
    assert by_substance["SO2(g)"]["delta_G0_expression_as_printed"] == "196550 - 94.98 T"
    assert by_substance["CO(g)"]["reaction_as_printed"] == "not printed"
    assert by_substance["CO2(g)"]["delta_G0_expression_as_printed"] == "not printed"
    assert "not formation Gibbs energies" in table1["values"]["schema_gap"]

    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 13
    for figure in figures:
        assert figure["values"]["points"] == []
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
    fig9 = next(row for row in figures if row["locator"]["figure"] == "9")
    assert "1923 K" in fig9["values"]["temperature_discrepancy"]
    assert "1973 K" in fig9["values"]["temperature_discrepancy"]
    assert any("sulphide_capacity" in row["values"].get("schema_gap", "") or row["values"].get("quantity") == "sulphide_capacity" for row in figures)


def test_isolated_migration_preserves_species_temperature_and_conditions():
    doc = _load_extract()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    assert set(migrated) == {
        "banya_2004_cao_al2o3_cas_solubility_summary",
        "banya_2004_cao_caf2_binary_cas_solubility_hawkins",
    }
    for raw in doc["species"]["CaS"]["observations"]:
        row = migrated[raw["observation_id"]]
        assert row.identity.quantity.is_value
        assert row.identity.species.formula == "CaS"
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.L
        assert row.point_conditions is not None
        assert "temperature_K" in row.point_conditions
        assert row.point_conditions["temperature_K"].state.is_value
        assert "composition" in row.point_conditions
        assert "total_pressure_Pa" in row.point_conditions
        assert "fO2_control" in row.point_conditions
        assert row.point_conditions["composition"].state.is_unknown

    interval = migrated["banya_2004_cao_al2o3_cas_solubility_summary"]
    assert interval.value.kind is ValueKind.UNAVAILABLE
    assert interval.identity.temperature_K.is_unknown
    assert interval.point_conditions["temperature_K"].state.value.kind is ValueKind.INTERVAL
    assert interval.point_conditions["temperature_K"].state.value.interval_low == Decimal("1823")
    assert interval.point_conditions["temperature_K"].state.value.interval_high == Decimal("1923")
    assert interval.point_conditions["fO2_control"].state.is_unknown
    assert str(interval.point_conditions["quench_state"].state.value) == "not_stated"

    point = migrated["banya_2004_cao_caf2_binary_cas_solubility_hawkins"]
    assert point.identity.temperature_K.is_value
    assert point.value.kind is ValueKind.POINT
    assert point.value.point == Decimal("27")
    assert point.value.approximate is True
    assert point.identity.temperature_K.value == Decimal("1873")
    assert point.point_conditions["fO2_control"].state.is_unknown


def _pdf_page_text(page: int) -> str:
    if not PDF.is_file():
        pytest.skip(f"source PDF not available: {PDF}")
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_physical_pdf_pages_support_table_figure_and_summary_locators():
    p2 = _pdf_page_text(2)
    assert "Table 1" in p2 and "Fig. 1." in p2
    p3 = _pdf_page_text(3)
    for figure in range(2, 6):
        assert f"Fig. {figure}." in p3
    p4 = _pdf_page_text(4)
    for figure in range(6, 10):
        assert f"Fig. {figure}." in p4
    assert "about 1.5" in p4 and "27 mol% CaS" in p4
    p5 = _pdf_page_text(5)
    for figure in range(10, 14):
        assert f"Fig. {figure}." in p5
    p7 = _pdf_page_text(7)
    assert "Table A1" in p7
