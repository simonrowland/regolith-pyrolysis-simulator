from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
        assert row.identity.quantity.value.value == "valence_ratio"
        assert row.point_conditions["temperature_K"].state.is_unknown
        assert row.point_conditions["fO2_log"].state.is_unknown
        if kind == "interval":
            assert str(row.value.interval_low) == expected[0]
            assert str(row.value.interval_high) == expected[1]
            assert row.value.kind.value == "interval"
        else:
            assert str(row.value.point) == expected
            assert row.value.kind.value == "point"

