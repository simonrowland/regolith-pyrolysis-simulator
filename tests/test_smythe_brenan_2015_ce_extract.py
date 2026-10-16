from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Phase,
    Quantity,
    UncertaintyKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/smythe-brenan-2015-cerium-redox.yaml"
RENDER_PINS = {
    # Table 3, PDF page 39, render row -> log(Ce4+/Ce3+).
    "DS11-C38": (Decimal("-0.15"), 38),
    "DS09-C2-13": (Decimal("-0.41"), 38),
    # Table 3, PDF page 40.
    "DS11-C66": (Decimal("-2.57"), 39),
    "DS09-C3-15": (Decimal("-1.93"), 39),
    # Table 3, PDF page 41.
    "DS11-C22": (Decimal("-1.68"), 40),
    "DS09-C3-14": (Decimal("-2.98"), 40),
    "DS09-C3-04": (Decimal("-1.12"), 40),
}
RENDER_WIDTH_PINS = {
    "DS11-C38": Decimal("0.13"),
    "DS09-C3-15": Decimal("0.11"),
    "DS09-C3-04": Decimal("0.10"),
}


def test_table3_ce_log_ratio_rows_match_render_and_migrate_with_conditions():
    extract = yaml.safe_load(EXTRACT.read_text())
    observation = extract["species"]["Ce"]["observations"][0]
    rows = observation["values"]["series"]
    assert len(rows) == 45

    hydrous_rows = [item for item in rows if "H2O_wt_pct" in item]
    assert len(hydrous_rows) == 8
    assert all("XANES" in item["instrument"] for item in hydrous_rows)
    assert all(
        "potentiometric" in item["instrument"]
        for item in rows
        if "H2O_wt_pct" not in item
    )
    assert observation["values"]["method_class"] == "measured_reduced"
    hydrous = next(item for item in rows if item["sample"] == "DS08-C1-02")
    assert Decimal(str(hydrous["H2O_wt_pct"])) == Decimal("9.1")
    assert Decimal(str(hydrous["composition_wt_pct"]["SiO2"])) == Decimal("65.44")
    assert Decimal(str(hydrous["composition_wt_pct"]["total"])) == Decimal("90.89")
    assert "XANES" in hydrous["instrument"]
    assert hydrous["noble_metal_oxide_buffer_as_published"] == "Ru"
    anhydrous = next(item for item in rows if item["sample"] == "DS11-C38")
    assert "potentiometric" in anhydrous["instrument"]
    assert Decimal(str(anhydrous["composition_wt_pct"]["SiO2"])) == Decimal("71.05")
    assert "H2O_wt_pct" not in anhydrous
    assert all("oxygen_buffer_as_published" not in item for item in rows)
    assert all("fO2_control" not in item["point_conditions"] for item in rows)

    for sample, (value, pdf_page_index) in RENDER_PINS.items():
        row = next(item for item in rows if item["sample"] == sample)
        assert Decimal(str(row["value"])) == value
        assert row["locator"]["pdf_page_index"] == pdf_page_index
        assert row["locator"]["table"] == "3"

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert len(migrated) == 45
    assert all(row.value.kind is ValueKind.POINT for row in migrated)
    assert all(row.identity.species.formula == "Ce" for row in migrated)
    assert all(row.identity.species.phase.value is Phase.GLASS for row in migrated)
    assert all(row.identity.quantity.value is Quantity.VALENCE_RATIO for row in migrated)
    assert all(row.identity.fO2_Pa.is_value for row in migrated)
    for source_row in rows:
        migrated_row = next(
            row
            for row in migrated
            if f"row={source_row['sample'].casefold()}" in row.observation_id
        )
        printed = {
            oxide: Decimal(str(value))
            for oxide, value in source_row["composition_wt_pct"].items()
            if oxide != "total"
        }
        expected = wt_pct_to_mole_fraction(printed)
        assert migrated_row.identity.composition.is_value
        assert migrated_row.identity.composition.value.components == expected.components
        point_composition = migrated_row.point_conditions["composition"].state.value
        assert point_composition.components == expected.components
        assert "Ce2O3" in migrated_row.identity.composition.value.as_map()
        if "H2O_wt_pct" in source_row:
            point_h2o = migrated_row.point_conditions["H2O_wt_pct"]
            assert point_h2o.state.is_value
            assert Decimal(str(point_h2o.state.value)) == Decimal(
                str(source_row["H2O_wt_pct"])
            )
            assert point_h2o.locator is not None
            assert point_h2o.locator.table == "2"
    assert all(
        row.identity.valence_basis.value.value_form is ValenceValueForm.LOG10_RATIO
        for row in migrated
    )
    assert all(row.uncertainty.kind is UncertaintyKind.PRINTED for row in migrated)

    # The paper's printed calibration turns XANES into the reported ratio.
    # Its calibration inputs are unprinted and typed as such on each row.
    hard = migrator.result.validation.hard_issues
    assert not hard
    assert all(row.derivation is not None for row in migrated)
    assert all(row.derivation.inputs == () for row in migrated)
    assert all(
        row.derivation.declares_unprinted_raw_inputs
        and row.derivation.has_located_unprinted_raw_inputs
        for row in migrated
    )

    for row in migrated:
        conditions = row.point_conditions or {}
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == conditions["temperature_K"].state.value
        for condition in (
            "temperature_K",
            "composition",
            "fO2_log",
            "total_pressure_Pa",
        ):
            assert conditions[condition].state.is_value
            assert conditions[condition].locator is not None

    for sample, (value, _page) in RENDER_PINS.items():
        migrated_row = next(
            row for row in migrated if f"row={sample.casefold()}" in row.observation_id
        )
        assert migrated_row.value.point == value

    for sample, width in RENDER_WIDTH_PINS.items():
        migrated_row = next(
            row for row in migrated if f"row={sample.casefold()}" in row.observation_id
        )
        assert Decimal(str(migrated_row.uncertainty.verbatim["sigma"])) == width

    absolute_fO2_row = next(
        row for row in migrated if "row=ds09-c2-13" in row.observation_id
    )
    assert absolute_fO2_row.identity.fO2_Pa.is_value
    assert Decimal(str(absolute_fO2_row.identity.fO2_Pa.value)) == Decimal("100000")
    for sample, pressure in {
        "DS09-C2-13": Decimal("1000000"),
        "DS08-C1-02": Decimal("1000000000"),
    }.items():
        migrated_row = next(
            row for row in migrated if f"row={sample.casefold()}" in row.observation_id
        )
        point_pressure = migrated_row.point_conditions["total_pressure_Pa"]
        assert Decimal(str(point_pressure.state.value)) == pressure
