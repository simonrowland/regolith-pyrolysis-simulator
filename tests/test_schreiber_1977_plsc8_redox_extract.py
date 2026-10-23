from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import QUANTITY_UNITS, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1977-plsc8-redox-ti-zr-hf-cr-eu.yaml"
RENDER_PINS = {
    "schreiber_1977_t3_cr_schreiber_haskin_1976": ("interval", ("0.65", "1.0"), "Cr"),
    "schreiber_1977_t3_cr_muan_1976": ("interval", ("0.9", "0.95"), "Cr"),
    "schreiber_1977_t3_eu_philpotts_drake": ("point", "0.7", "Eu"),
    "schreiber_1977_t3_ti_wells_hapke_1977": ("interval", ("0.01", "0.10"), "Ti"),
    "schreiber_1977_t3_ti_weeks_1973": ("point", "0.03", "Ti"),
    "schreiber_1977_t3_ti_many_works_armalcolite": ("interval", ("0.04", "0.13"), "Ti"),
    "schreiber_1977_t3_ti_agrell_1970": ("interval", ("0.08", "0.25"), "Ti"),
    "schreiber_1977_t3_ti_sung_1974": ("interval", ("0.3", "0.4"), "Ti"),
}
TABLE1_RENDER_GRID = [
    ("SiO2", "53.9", "51.1", "44-53", "37-49"),
    ("Al2O3", "10.7", "2.9", "13-19", "7-14"),
    ("FeO", "—", "—", "7-14", "18-23"),
    ("MnO", "—", "—", "0.09-0.3", "0.21-0.29"),
    ("MgO", "29.3", "26.1", "4-10", "6-17"),
    ("CaO", "5.7", "19.1", "8-12", "8-12"),
    ("Na2O", "—", "—", "1.8-3.8", "0.1-0.5"),
    ("K2O", "—", "—", "0.3-1.9", "0.2-0.3"),
    ("TiO2", "(1.3)^4", "(1.3)^4", "0.9-3.3", "0.3-13"),
    ("Cr2O3", "(0.1-0.6)", "(0.1-0.8)", "0.005-0.04", "0.12-0.70"),
    ("Eu(ppm)", "(9300)", "(10600)", "1.9^6", "0.21-2.6"),
]
TABLE2_RENDER_GRID = [
    ("Ti(IV)->Ti(III)", "70 ± 28", "71 ± 19", "70 ± 23"),
    ("Eu(III)->Eu(II)", "44 ± 35", "36 ± 16", "40 ± 25"),
    ("Cr(III)->Cr(II)", "35 ± 12", "38 ± 3", "36 ± 8"),
]


def _assert_render_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for container in document["species"].values()
        for row in container.get("observations", [])
    }
    for observation_id, (kind, expected, formula) in RENDER_PINS.items():
        row = observations[observation_id]
        point = row["values"]["points"][0]
        assert row["values"]["quantity"] == "valence_ratio"
        assert row["values"]["formula"] == formula
        assert row["values"]["method_class"].startswith("quoted_")
        assert row["locator"] == {
            "page": 16,
            "pdf_page_index": 15,
            "published_page": 1800,
            "table": "3",
            "row": row["locator"]["row"],
        }
        if kind == "interval":
            assert tuple(point["range"]) == expected
        else:
            assert point["value"] == expected

    context = document["species"]["Cr"]["context"]
    table1 = next(row for row in context if row["observation_id"] == "schreiber_1977_table1_synthetic_and_basalt_compositions")
    assert table1["locator"] == {"page": 4, "pdf_page_index": 3, "published_page": 1788, "table": "1"}
    table1_grid = [
        (row["oxide"], row["FAS"], row["FAD"], row["Earth"], row["Moon"])
        for row in table1["values"]["table_rows"]
    ]
    assert table1["values"]["table_row_count"] == len(TABLE1_RENDER_GRID)
    assert table1_grid == TABLE1_RENDER_GRID
    assert table1["values"]["candidate_tokens"] == ["composition"]
    table2 = next(row for row in context if row["observation_id"] == "schreiber_1977_table2_reduction_enthalpies")
    assert table2["locator"] == {"page": 9, "pdf_page_index": 8, "published_page": 1793, "table": "2"}
    table2_grid = [
        (row["redox_couple"], row["FAS"], row["FAD"], row["average"])
        for row in table2["values"]["table_rows"]
    ]
    assert table2["values"]["table_row_count"] == len(TABLE2_RENDER_GRID)
    assert table2_grid == TABLE2_RENDER_GRID
    assert table2["values"]["candidate_tokens"] == ["delta_fH"]
    zr = next(row for row in document["species"]["Zr"]["context"] if row["observation_id"] == "schreiber_1977_zr_table3_postulated_prior_fraction")
    assert zr["values"]["value_as_published"] == "0.15-0.25"
    assert zr["values"]["attribution"] == "Postulated by Garg and Ehmann (1976)"
    assert zr["values"]["admission_status"] == "typed_unavailable"


def test_table3_render_pins_and_fidelity_samples() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []


def test_render_pin_fails_after_source_value_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = next(
        row
        for row in document["species"]["Cr"]["observations"]
        if row["observation_id"] == "schreiber_1977_t3_cr_schreiber_haskin_1976"
    )
    row["values"]["points"][0]["range"][0] = "0.66"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_independent_table_render_pin_fails_after_extract_cell_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = next(row for row in document["species"]["Cr"]["context"] if row["observation_id"] == "schreiber_1977_table1_synthetic_and_basalt_compositions")
    table1["values"]["table_rows"][0]["FAS"] = "53.8"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_isolated_migration_preserves_each_quoted_value_and_species() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    migrated = tuple(result.observations.values())
    assert len(migrated) == len(RENDER_PINS)
    by_id = {
        str(row.observation_id).split("::", 2)[1].split("::", 1)[0]: row
        for row in migrated
    }
    for observation_id, (kind, expected, formula) in RENDER_PINS.items():
        row = by_id[observation_id]
        assert row.identity.species.formula == formula
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert QUANTITY_UNITS[row.identity.quantity.value] == "dimensionless"
        assert row.point_conditions["temperature_K"].state.is_unknown
        assert row.point_conditions["fO2_log"].state.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.notices == ()
        if kind == "interval":
            assert str(row.value.interval_low) == expected[0]
            assert str(row.value.interval_high) == expected[1]
            assert row.value.kind.value == "interval"
        else:
            assert str(row.value.point) == expected
            assert row.value.kind.value == "point"
        assert row.value.kind in {ValueKind.POINT, ValueKind.INTERVAL}

