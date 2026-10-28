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

    migrated_rows = list(migrator.result.observations.values())
    rows = [
        row for row in migrated_rows
        if row.identity.quantity.value is Quantity.VALENCE_RATIO
    ]
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
    table2_vectors = {}
    table2_observations = {}
    for formula, species_data in document["species"].items():
        for observation in species_data["observations"]:
            if observation["type"] != "equilibrium_phase_composition":
                continue
            table2_observations[formula] = observation
            for point in observation["values"]["points"]:
                table2_vectors.setdefault(point["sample"], {})[formula] = point["value_as_published"]

    assert len(table2_vectors) == 18
    assert set(table2_vectors["S75-Cr25 (R5)"]) == set(printed_composition)
    assert {key: Decimal(value) for key, value in table2_vectors["S75-Cr25 (R5)"].items()} == printed_composition
    assert len(table2_observations) == 12
    assert all(
        point["locator"]["page"] == 15
        and point["locator"]["published_page"] == "not_printed"
        and point["point_conditions"]["T_C"]["state"]["value"] == "1150"
        for observation in table2_observations.values()
        for point in observation["values"]["points"]
    )

    # Table 4 retains these cells as an identity condition; Table 2 owns their typed observations.
    for ratio_point in source_observation["values"]["points"]:
        source_value = ratio_point["point_conditions"]["composition"]["state"]["value"]
        sample = "".join(ratio_point["sample"].split())
        table2_sample = next(label for label in table2_vectors if "".join(label.split()) == sample)
        assert source_value["amount_basis"] == "mass_percent"
        assert dict(source_value["components_as_printed"]) == table2_vectors[table2_sample]
        assert ratio_point["point_conditions"]["composition"]["locator"]["note"].startswith("Shared identity condition")

    table2_context = next(
        row for row in document["species"]["S"]["context"]
        if row["observation_id"] == "jin_2022_table2_glass_compositions"
    )
    table_lines = table2_context["values"]["table_transcription"].splitlines()
    table_headers = [cell.strip() for cell in table_lines[0].split("|")]
    for line in table_lines[1:]:
        if not line.strip() or line.startswith("Dash is"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        assert all(value in {"", "-"} for value in cells[1:13])
        assert len(cells) == len(table_headers)

    expected_identity_composition = wt_pct_to_mole_fraction(printed_composition)
    assert s75_point.identity.composition.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert s75_point.identity.composition.value.as_map() == expected_identity_composition.as_map()
    assert s75_point.point_conditions["composition"].locator.page == 15
    assert s75_point.point_conditions["composition"].locator.table == "2"
    typed_rows = [
        row for row in migrated_rows
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(typed_rows) == 194
    assert all(row.identity.temperature_K.value == Decimal("1423.15") for row in typed_rows)
    assert all(row.point_conditions["T_C"].state.value == Decimal("1150") for row in typed_rows)
    assert all(row.point_conditions["atmosphere"].state.value == "air" for row in typed_rows)
    assert all(row.point_conditions["quench"].state.value == "water quench" for row in typed_rows)
    assert all(row.locator.page == 15 and row.locator.published_page == "not_printed" for row in typed_rows)
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
    assert chromate_estimate["locator"]["page"] == 9
    assert chromate_estimate["locator"]["pdf_page_index"] == 8
    assert chromate_estimate["values"]["value_as_published"] == 2.09
    assert "77.6%" in chromate_estimate["values"]["relation_as_printed"]
