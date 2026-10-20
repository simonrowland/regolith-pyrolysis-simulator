from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    AmountBasis,
    EvidenceClass,
    Quantity,
    ValueKind,
    ValenceValueForm,
)
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "jin-2022-cl-cr-sulfur-glass-solubility"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_table4_cr_vi_percentages_migrate_as_reduced_fraction_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    source_observation = document["species"]["Cr"]["observations"][0]
    assert source_observation["values"]["method_class"] == "measured_reduced"
    assert source_observation["values"]["value_form"] == "fraction"
    assert source_observation["values"]["value_as_published"] == "percent"
    assert source_observation["derivation"]["output_unit"] == "dimensionless"
    assert [point["value_as_published"] for point in source_observation["values"]["points"]] == [
        "57.7 ± 2.1",
        "73.1 ± 1.6",
        "83.3 ± 1.4",
        "77.6 ± 1.3",
    ]
    raw_inputs = source_observation["derivation"]["raw_inputs"]
    assert raw_inputs["state"]["value"] == "not_printed"
    assert raw_inputs["locator"]["page"] == 6

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    rows = list(migrator.result.observations.values())
    assert len(rows) == 4
    assert not migrator.result.validation.hard_issues
    assert {row.value.point for row in rows} == {
        Decimal("0.577"),
        Decimal("0.731"),
        Decimal("0.833"),
        Decimal("0.776"),
    }
    assert [Decimal(str(row.uncertainty.verbatim["sigma"])) for row in rows] == [
        Decimal("0.021"),
        Decimal("0.016"),
        Decimal("0.014"),
        Decimal("0.013"),
    ]
    assert all(row.derivation.output_unit == "dimensionless" for row in rows)
    assert all(row.quench_state.value == "not_stated" for row in rows)
    assert all(row.point_conditions["atmosphere"].locator.page == 11 for row in rows)
    for row in rows:
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert row.identity.species.formula == "Cr"
        assert row.identity.temperature_K.value == Decimal("1423.15")
        assert row.identity.composition.is_value
        assert "ZnO" in row.identity.composition.value.as_map()
        assert row.identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
        assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert row.derivation is not None
        assert row.derivation.output_unit == "dimensionless"
        assert row.derivation.raw_inputs is not None
        assert row.derivation.raw_inputs.state.value.value == "not_printed"
        assert row.point_conditions["atmosphere"].state.value == "air"
        assert row.point_conditions["quench"].state.value == "water quench"
        assert row.notices == ()

    s75_point = next(row for row in rows if row.value.point == Decimal("0.577"))
    printed_composition = {
        "Al2O3": Decimal("10.30"),
        "B2O3": Decimal("13.35"),
        "CaO": Decimal("8.06"),
        "Fe2O3": Decimal("1.07"),
        "MgO": Decimal("0.98"),
        "Na2O": Decimal("21.91"),
        "SiO2": Decimal("36.30"),
        "ZnO": Decimal("3.12"),
        "ZrO2": Decimal("2.89"),
        "SO3": Decimal("1.36"),
        "Cr2O3": Decimal("0.65"),
    }
    table_2 = next(
        row
        for row in document["species"]["S"]["context"]
        if row["observation_id"] == "jin_2022_table2_glass_compositions"
    )
    table_lines = table_2["values"]["table_transcription"].splitlines()
    table_headers = [cell.strip() for cell in table_lines[0].split("|")]
    table_rows = {}
    for line in table_lines[1:]:
        if not line.strip() or line.startswith("Dash is"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        table_rows["".join(cells[0].split())] = dict(zip(table_headers, cells))
    table_to_component = {
        "Al2O3": "Al2O3",
        "B2O3": "B2O3",
        "CaO": "CaO",
        "Fe2O3": "Fe2O3",
        "MgO": "MgO",
        "Na2O": "Na2O",
        "SiO2": "SiO2",
        "ZnO": "ZnO",
        "ZrO2": "ZrO2",
        "SO3 mass%": "SO3",
        "Cr2O3 mass%": "Cr2O3",
    }
    for point in source_observation["values"]["points"]:
        source_composition = point["point_conditions"]["composition"]
        source_value = source_composition["state"]["value"]
        sample_table_row = table_rows["".join(c for c in point["sample"] if not c.isspace())]
        assert source_composition["locator"]["row"] == sample_table_row["Sample"]
        expected_row = {
            component: sample_table_row[column]
            for column, component in table_to_component.items()
        }
        assert source_value["components_as_printed"] == expected_row
        assert dict(source_value["components"]) == expected_row

    source_composition = source_observation["values"]["points"][0]["point_conditions"]["composition"]
    source_value = source_composition["state"]["value"]
    assert source_value["amount_basis"] == "mass_percent"
    assert {
        key: Decimal(value)
        for key, value in source_value["components_as_printed"].items()
    } == printed_composition
    assert {
        key: Decimal(value) for key, value in source_value["components"]
    } == printed_composition
    expected_identity_composition = wt_pct_to_mole_fraction(printed_composition)
    assert s75_point.identity.composition.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert s75_point.identity.composition.value.as_map() == expected_identity_composition.as_map()
    assert s75_point.point_conditions["composition"].locator.page == 15
    assert s75_point.point_conditions["composition"].locator.table == "2"
    context = document["species"]["Cr"]["context"]
    mckeown = next(
        row for row in context
        if row["observation_id"] == "jin_2022_discussion_mckeown_cr_vi_context"
    )
    assert mckeown["values"]["value_as_published"] == "84%"
    assert mckeown["values"]["glass_composition_as_printed"] == "23.1 mass% Na2O in a similar simulated LAW glass"
    chromate_estimate = next(
        row for row in context
        if row["observation_id"] == "jin_2022_discussion_chromate_molar_solubility_context"
    )
    assert chromate_estimate["values"]["value_as_published"] == 2.09
    assert "77.6%" in chromate_estimate["values"]["relation_as_printed"]
