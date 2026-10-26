"""Render pins and isolated migration checks for the Floss et al. extract."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from tests.battery import factories
from tools.validate_literature_extracts import validate_extract_document


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/floss-1998-corundum-hibonite-evaporation-residues.yaml"
)


def _source_observation(document: dict, observation_id: str) -> dict:
    return next(
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == observation_id
    )


def _source_context(document: dict, observation_id: str) -> dict:
    return next(
        context
        for species in document["species"].values()
        for context in species.get("context", [])
        if context["observation_id"] == observation_id
    )


def _migrated_observation(result, source_observation_id: str):
    return next(
        observation
        for observation in result.observations.values()
        if source_observation_id in observation.observation_id
    )


def test_floss_1998_render_pins_conditions_and_run_count() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert len(document["experiments"]) == 18  # Table 1: 13 MH, 3 CA6, 2 RTE runs.
    mass_loss_rows = [
        observation
        for observation in document["species"]["unknown"]["observations"]
        if observation["observation_id"].startswith("floss_1998_table1_")
    ]
    assert len(mass_loss_rows) == 18

    mh1_mass_loss = _source_observation(
        document, "floss_1998_table1_mh_1_mass_loss"
    )
    table4_alumina = _source_observation(
        document, "floss_1998_p005_t04_r02_c02_mh_1_al2o3"
    )
    table6_hafnia = _source_observation(
        document, "floss_1998_p010_t06_r28_c03_ca6_1_hf"
    )
    natural_gd = [
        observation
        for observation in document["species"]["Gd"]["observations"]
        if observation["locator"].get("row") == "Gd*"
    ]
    ca6_gd = [
        observation
        for observation in document["species"]["Gd"]["observations"]
        if observation["locator"].get("row") == "Gd"
        and observation["locator"].get("table") == "6"
    ]
    rte13_yb = _source_context(
        document, "floss_1998_context_0678"
    )

    assert len(natural_gd) == 13, [row["observation_id"] for row in natural_gd]
    assert len(ca6_gd) == 3, [row["observation_id"] for row in ca6_gd]
    for row in natural_gd:
        assert row["values"]["analyte"] == "Gd"
        assert row["values"]["method_class"] == "quoted_attributed"
        assert "interpolated" in row["values"]["attribution"]
        assert row["values"]["series"][0]["interpolated"] is True
        assert row["locator"]["row"] == "Gd*"
    assert natural_gd[0]["values"]["series"][0]["residue_ppm"] == "35"
    assert natural_gd[0]["locator"]["page"] == 9
    for row in ca6_gd:
        assert row["values"]["analyte"] == "Gd"
        assert row["values"]["method_class"] == "measured_direct"
        assert row["locator"]["row"] == "Gd"
        assert "interpolated" not in row["values"]["series"][0]
        assert "attribution" not in row["values"]
        assert row["attribution"] == "present work"
    ca6_gd_1 = _source_observation(
        document, "floss_1998_p010_t06_r44_c03_ca6_1_gd"
    )
    assert ca6_gd_1["values"]["series"][0]["residue_ppm"] == "126"
    assert ca6_gd_1["values"]["series"][0]["sigma"] == "13"
    assert ca6_gd_1["locator"]["page"] == 10

    assert rte13_yb["type"] == "typed_refusal"
    assert rte13_yb["values"]["status"] == "upper_limit"
    assert rte13_yb["values"]["sample_id"] == "RTE-13"
    assert rte13_yb["values"]["element"] == "Yb"
    assert rte13_yb["values"]["value_as_printed"] == "1598"
    assert rte13_yb["values"]["bound_operator"] == "<="
    assert "cannot represent a bound-valued residue_ppm cell" in rte13_yb["values"]["reason"]
    assert rte13_yb["locator"]["page"] == 13
    assert rte13_yb["locator"]["table"] == "8"

    mh8_table7_contexts = [
        row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row["type"] in {"censored_trace_value", "not_reported_cell"}
        and row["locator"].get("table") == "7"
        and row["values"].get("sample_id") == "MH-8"
    ]
    assert len(mh8_table7_contexts) == 9
    assert all(row["locator"]["column"] == "MH-8*" for row in mh8_table7_contexts)

    all_rows = [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
    ]
    lower_panel_headers = {
        "4": (17, {2: "MH-7", 3: "MH-12", 4: "MH-16", 5: "MH-15", 6: "MH-14", 7: "CA6-1", 8: "CA6-2", 9: "CA6-4"}, 41),
        "5": (15, {2: "MH-7", 3: "MH-12", 4: "MH-16", 5: "MH-15", 6: "MH-14", 7: "CA6-1", 8: "CA6-2", 9: "CA6-4"}, 31),
        "7": (28, {2: "MH-3", 3: "MH-10", 4: "MH-6", 5: "MH-8*", 6: "MH-7"}, 69),
    }
    for table, (first_row, headers, expected_count) in lower_panel_headers.items():
        lower_rows = [
            row
            for row in all_rows
            if row["locator"].get("table") == table
            and int(row["locator"].get("table_row_number", 0)) >= first_row
        ]
        assert len(lower_rows) == expected_count, [
            row["observation_id"] for row in lower_rows
        ]
        for row in lower_rows:
            column_number = row["locator"]["table_column_number"]
            assert row["locator"]["column"] == headers[column_number]
            for point in row["values"].get("series") or []:
                pressure_locator = point["point_conditions"]["total_pressure_Pa"]["locator"]
                assert pressure_locator["column"] == row["locator"]["column"]

    table5_alumina = _source_observation(
        document, "floss_1998_p006_t05_r15_c02_mh_7_al2o3"
    )
    table4_lower_alumina = _source_observation(
        document, "floss_1998_p005_t04_r17_c02_mh_7_al2o3"
    )
    table7_hafnia = _source_observation(
        document, "floss_1998_p011_t07_r34_c02_mh_3_hf"
    )
    assert table4_lower_alumina["values"]["series"][0]["Al2O3_wt_pct"] == "76.46"
    assert table4_lower_alumina["locator"]["column"] == "MH-7"
    assert table5_alumina["values"]["series"][0]["Al2O3_wt_pct"] == "82.88"
    assert table5_alumina["locator"]["column"] == "MH-7"
    assert table7_hafnia["values"]["series"][0]["residue_ppm"] == "110"
    assert table7_hafnia["locator"]["column"] == "MH-3"

    # Values and coordinates checked against the page renders: Table 1 p. 2,
    # Table 4 p. 5, and Table 6 p. 10 (printed page numbers 192, 195, 200).
    assert mh1_mass_loss["values"]["mass_loss_pct"] == "37.5"
    assert mh1_mass_loss["units"] == "%"
    assert mh1_mass_loss["locator"]["page"] == 2
    assert mh1_mass_loss["locator"]["table"] == "1"
    assert (mh1_mass_loss["locator"]["table_row_number"], mh1_mass_loss["locator"]["table_column_number"]) == (4, 3)

    assert table4_alumina["values"]["series"][0]["Al2O3_wt_pct"] == "86.09"
    assert table4_alumina["locator"]["page"] == 5
    assert table4_alumina["locator"]["table"] == "4"
    assert (table4_alumina["locator"]["table_row_number"], table4_alumina["locator"]["table_column_number"]) == (2, 2)

    assert table6_hafnia["values"]["series"][0]["residue_ppm"] == "32"
    assert table6_hafnia["values"]["series"][0]["sigma"] == "36"
    assert table6_hafnia["locator"]["page"] == 10
    assert table6_hafnia["locator"]["table"] == "6"
    assert table6_hafnia["locator"]["column"] == "CA6-1"
    assert (table6_hafnia["locator"]["table_row_number"], table6_hafnia["locator"]["table_column_number"]) == (28, 3)

    ca6_experiment = next(
        experiment
        for experiment in document["experiments"]
        if experiment["sample"]["sample_id"] == "CA6-1"
    )
    ca6_start = ca6_experiment["sample"]["initial_composition"]
    assert ca6_start["state"]["value"]["amount_basis"] == "mass_percent"
    assert ca6_start["state"]["value"]["components"] == [
        ["Al2O3", "89.26"],
        ["CaO", "8.35"],
        ["TiO2", "1.59"],
        ["MgO", "0.80"],
        ["ZrO2", "0.02"],
    ]
    omitted_note = ca6_start["locator"]["note"]
    assert "REE2O3 0.28 wt%, Y2O3 0.02 wt%, HfO2 0.02 wt%" in omitted_note
    assert "total 0.32 wt%" in omitted_note
    assert "without wt% renormalization" in omitted_note
    table2_ca6 = {
        row["values"]["oxide_as_printed"]: row["values"]["synthetic_CA6_as_printed"]
        for row in document["species"]["unknown"]["context"]
        if row["type"] == "starting_composition_analysis"
        and "oxide_as_printed" in row["values"]
    }
    assert {oxide: table2_ca6[oxide] for oxide in ("REE2O3", "Y2O3", "HfO2")} == {
        "REE2O3": "0.28",
        "Y2O3": "0.02",
        "HfO2": "0.02",
    }

    migrator = factories.migrate_one_extract(REPO_ROOT, EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.experiments) == 18
    assert len(result.observations) == 641
    assert sum(len(rows) for rows in result.context_by_work.values()) == 677
    assert not result.validation.hard_issues

    expected_migrated_values = (
        ("floss_1998_table1_mh_1_mass_loss", Decimal("0.375")),
        ("floss_1998_p005_t04_r02_c02_mh_1_al2o3", Decimal("86.09")),
        ("floss_1998_p010_t06_r28_c03_ca6_1_hf", Decimal("32")),
    )
    for source_id, expected in expected_migrated_values:
        assert _migrated_observation(result, source_id).value.point == expected
    for source_row in (mh1_mass_loss, table4_alumina, table6_hafnia):
        migrated = _migrated_observation(result, source_row["observation_id"])
        assert migrated.locator.page == source_row["locator"]["page"]
        assert migrated.locator.table == source_row["locator"]["table"]

    for row in natural_gd:
        migrated = _migrated_observation(result, row["observation_id"])
        assert migrated.identity.species.formula == "Gd"
        assert migrated.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    for row in ca6_gd:
        migrated = _migrated_observation(result, row["observation_id"])
        assert migrated.identity.species.formula == "Gd"
        assert migrated.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert migrated.evidence.attribution is None

    assert not any(
        "floss_1998_p013_t08_r23_c06_rte_13_yb" in observation.observation_id
        for observation in result.observations.values()
    )
    for source_row, expected_column in (
        (table4_lower_alumina, "MH-7"),
        (table5_alumina, "MH-7"),
        (table7_hafnia, "MH-3"),
    ):
        migrated = _migrated_observation(result, source_row["observation_id"])
        assert migrated.locator.table == source_row["locator"]["table"]
        assert expected_column in migrated.locator.note

    mass_loss = _migrated_observation(result, "floss_1998_table1_mh_1_mass_loss")
    assert mass_loss.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    assert mass_loss.identity.temperature_K.value == Decimal("2373.15")

    for observation in result.observations.values():
        conditions = observation.point_conditions or {}
        temperature = conditions["temperature_K"]
        assert temperature.state.tag.value == "value"
        assert temperature.locator is not None
        duration = conditions["duration_min"]
        assert duration.state.tag.value == "unknown"
        assert duration.state.value is None
        assert duration.locator is not None
        pressure = conditions.get("total_pressure_Pa")
        assert pressure is None or pressure.state.tag.value == "unknown"
        assert pressure is None or pressure.state.value is None
        identity = observation.identity
        if "_ca6_" in observation.observation_id:
            assert conditions["composition"].state.is_value
            if identity.quantity.value is Quantity.MASS_LOSS_FRACTION:
                assert identity.composition.is_unknown
            else:
                assert identity.composition.is_value
        else:
            assert identity.composition.is_unknown
        for axis in (identity.total_pressure_Pa, identity.fO2_Pa):
            assert axis.tag.value == "unknown"
            assert axis.value is None
        assert identity.temperature_K.value is not None

    for experiment in result.experiments.values():
        assert experiment.pressure_environment.total_pressure_Pa.state.tag.value == "unknown"
        assert experiment.pressure_environment.total_pressure_Pa.state.value is None

    ca6_identity = _migrated_observation(
        result, "floss_1998_p010_t06_r28_c03_ca6_1_hf"
    ).identity.composition
    assert ca6_identity.is_value
    assert ca6_identity.value.amount_basis.value == "mass_percent"
    assert dict(ca6_identity.value.components) == {
        "Al2O3": Decimal("89.26"),
        "CaO": Decimal("8.35"),
        "TiO2": Decimal("1.59"),
        "MgO": Decimal("0.80"),
        "ZrO2": Decimal("0.02"),
    }
