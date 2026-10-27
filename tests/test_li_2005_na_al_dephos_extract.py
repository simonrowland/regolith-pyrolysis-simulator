"""Render-pinned migration checks for Li et al. (2005) Tables 1 and 2."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/literature/extracts/li-2005-na-al-phosphate-fe-activities.yaml"
SOURCE_ID = "li-2005-na-al-phosphate-fe-activities"


def _migrate():
    extract = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    source = extract["source"]
    migrator = migrate_one_extract(
        ROOT,
        SOURCE,
        index={SOURCE_ID: {"source_id": SOURCE_ID, **source}},
        aliases={},
    )
    migrator.finalize()
    return extract, migrator.result


def _row(result, quantity, species, table, row):
    matches = [
        item
        for item in result.observations.values()
        if item.identity.quantity.is_value
        and item.identity.quantity.value is quantity
        and item.identity.species.formula == species
        and item.locator.table == table
        and (f":row={row}:" in item.observation_id or f"_row_{row}_" in item.observation_id)
    ]
    assert len(matches) == 1
    return matches[0]


def test_li_2005_tables_render_values_and_migrated_conditions():
    extract, result = _migrate()
    assert len(result.experiments) == 2
    assert len(result.observations) == 370
    assert len(result.registry_issues) == 0
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 0
    for experiment, ratio in zip(extract["experiments"], ("1/10", "1/18")):
        assert experiment["fO2_control"]["channel"]["value"] == "gas_mix"
        assert ratio in experiment["fO2_control"]["buffer"]["state"]["value"]
        assert experiment["fO2_control"]["buffer"]["locator"]["page"] == 2

    counts = Counter(
        item.identity.quantity.value
        for item in result.observations.values()
        if item.identity.quantity.is_value
    )
    assert counts == {
        Quantity.ACTIVITY_COEFFICIENT: 50,
        Quantity.DISTRIBUTION_RATIO: 25,
        Quantity.VALENCE_RATIO: 25,
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 245,
    }
    assert sum(item.value.kind.value == "unavailable" for item in result.observations.values()) == 25

    # Values checked against the 200 dpi render of PDF page 3 (published p. 14).
    pins = (
        (Quantity.ACTIVITY_COEFFICIENT, "FeO", "1", "1-1", "1.96"),
        (Quantity.ACTIVITY_COEFFICIENT, "FeO1.5", "2", "2-14", "0.066"),
        (Quantity.DISTRIBUTION_RATIO, "P", "1", "1-1", "247"),
        (Quantity.VALENCE_RATIO, "Fe", "2", "2-14", "0.289"),
    )
    for quantity, species, table, row, expected in pins:
        item = _row(result, quantity, species, table, row)
        assert item.value.point == Decimal(expected)
        assert item.locator.page == 3
        assert item.locator.table == table
        assert f":row={row}:" in item.observation_id or f"_row_{row}_" in item.observation_id

    typed = [
        item for item in result.observations.values()
        if item.identity.quantity.is_value
    ]
    for item in typed:
        identity = item.identity
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        assert identity.fO2_Pa.is_value
        # The paper gives no total pressure; it must stay unknown.
        assert identity.total_pressure_Pa.is_unknown

    metal_p = _row(result, Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "P", "1", "1-1")
    assert metal_p.value.point == Decimal("0.00245")
    assert metal_p.identity.subtype.value == "wt_percent"
    assert metal_p.identity.species.phase.is_unknown
    assert "post-quench phase" in metal_p.identity.species.phase.reason
    metal_o = _row(result, Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "O", "1", "1-1")
    assert metal_o.value.point == Decimal("0.0862")
    slag_na2o = _row(result, Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "Na2O", "1", "1-1")
    assert slag_na2o.value.point == Decimal("2.37")
    assert slag_na2o.identity.subtype.value == "wt_percent"

    # The source says the furnace sample was argon quenched, but does not say
    # the slag became glass or that its chemistry was preserved by quenching.
    slag_feo = next(
        item for item in extract["species"]["FeO"]["observations"]
        if item["observation_id"] == "li_2005_table_1_row_1-1_slag_FeO_composition"
    )
    assert slag_feo["units"] == "%"
    assert slag_feo["phase_as_measured"] == "quenched slag; whether it formed glass is not stated"
    assert slag_feo["quench_statement"]["preservation"] == "not_stated"
    assert slag_feo["quench_statement"]["locator"]["page"] == 2
    slag_fe2o3 = next(
        item for item in extract["species"]["Fe2O3"]["observations"]
        if item["observation_id"] == "li_2005_table_1_row_1-1_slag_Fe2O3_composition"
    )
    assert slag_fe2o3["values"]["evidence_class"] == "quoted_attributed"
    assert "numerical total-Fe assay is not tabulated" in slag_fe2o3["values"]["attribution"]

    # The capacity values remain in the source extract, refused only because the
    # closed quantity vocabulary has no phosphate-capacity token.
    capacity_rows = [
        point
        for obs in extract["species"]["P"]["observations"]
        if obs["type"] == "phosphate_capacity"
        for point in obs["values"]["points"]
    ]
    assert len(capacity_rows) == 25
    assert all("log10_capacity_as_printed" in point for point in capacity_rows)
