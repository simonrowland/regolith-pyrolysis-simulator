import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Engine,
    NoticeKind,
    Phase,
    Quantity,
    ResidualStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.migrate import Migrator
from simulator.battery.records import Uncertainty
from simulator.battery.score import (
    ScoreContext,
    _printed_uncertainty_band,
    score_store,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zaitsev-1999-na2o-sio2-pccp.yaml"


def _migrate():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator


def test_zaitsev_1999_scored_rows_migrate_with_temperature_and_composition():
    _, migrator = _migrate()

    assert migrator.result.validation is not None
    assert migrator.result.validation.ok
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == 116
    assert all(
        all(
            key in (observation.point_conditions or {})
            and (observation.point_conditions or {})[key].state.is_value
            and (observation.point_conditions or {})[key].locator is not None
            for key in ("temperature_K", "composition")
        )
        for observation in migrator.result.observations.values()
    )
    table4 = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table4_" in observation.observation_id
    ]
    assert len(table4) == 44
    assert {observation.identity.species.formula for observation in table4} == {
        "Na2O"
    }
    assert all(
        observation.provenance["printed_form"] == "neg_log10"
        and observation.locator.page == 6
        for observation in table4
    )

    na2o = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table1_na2o_activity::" in observation.observation_id
    ]
    sio2 = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table1_sio2_activity::" in observation.observation_id
    ]
    assert len(na2o) == 29
    assert len(sio2) == 29
    assert all(len(observation.derived_from or ()) == 29 for observation in sio2)


def test_zaitsev_1999_render_pins_and_table1_row_count():
    document, _ = _migrate()
    species = document["species"]
    table4 = {
        observation["observation_id"]: observation["values"]["points"]
        for observation in species["Na2O"]["observations"]
        if observation["observation_id"].startswith("zaitsev_1999_table4_")
    }
    # These Table 4 cells were confirmed against the 600 dpi p. 1904 render.
    table4_pins = {
        ("zaitsev_1999_table4_present_na2o_activity", "0.103", "1430"): "9.394",
        ("zaitsev_1999_table4_ref16_na2o_activity", "0.103", "1430"): "9.823",
        ("zaitsev_1999_table4_present_na2o_activity", "0.254", "1557"): "8.346",
        ("zaitsev_1999_table4_ref16_na2o_activity", "0.254", "1557"): "8.79",
        ("zaitsev_1999_table4_present_na2o_activity", "0.303", "1344"): "9.261",
    }
    for (observation_id, x_na2o, temperature), expected in table4_pins.items():
        point = next(
            point
            for point in table4[observation_id]
            if point["x_Na2O_as_printed"] == x_na2o
            and point["T_K"] == temperature
        )
        assert point["activity"] == expected

    na2o = next(
        item["values"]["points"]
        for item in species["Na2O"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table1_na2o_activity"
    )
    sio2 = next(
        item["values"]["points"]
        for item in species["SiO2"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table1_sio2_activity"
    )

    assert len(na2o) == len(sio2) == 29
    assert (na2o[0]["activity"], na2o[0]["locator"]["row"]) == ("7.19e-10", "1")
    assert (na2o[-1]["activity"], na2o[-1]["locator"]["row"]) == ("8.62e-5", "29")
    assert (sio2[0]["activity"], sio2[0]["locator"]["row"]) == ("0.860", "1")
    assert (sio2[-1]["activity"], sio2[-1]["locator"]["row"]) == ("3.77e-6", "29")
    assert all(point["locator"]["published_page"] == 1901 for point in na2o + sio2)


def test_zaitsev_1999_partial_enthalpy_and_element_formation_signs():
    document, migrator = _migrate()

    present = next(
        item
        for item in document["species"]["Na2O"]["observations"]
        if item["observation_id"] == "zaitsev_1999_table5_present_partial_molar_enthalpy"
    )
    migrated_present = {
        observation.observation_id.split("row=", 1)[1].split(":", 1)[0]: observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table5_present_partial_molar_enthalpy::"
        in observation.observation_id
    }
    for point in present["values"]["points"]:
        observation = migrated_present[point["row_as_printed"]]
        assert observation.value.point == -Decimal(
            point["printed_minus_delta_H_kJ_per_mol"]
        )

    quoted = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table6_ref17_element_formation_enthalpy_"
        in observation.observation_id
    ]
    assert len(quoted) == 3
    assert {observation.value.point for observation in quoted} == {
        Decimal("-1556.03"),
        Decimal("-2100.8"),
        Decimal("-2467.3"),
    }

    standard_entropies = [
        observation
        for observation in migrator.result.observations.values()
        if "::zaitsev_1999_table6_ref17_standard_entropy_"
        in observation.observation_id
    ]
    assert len(standard_entropies) == 3
    assert {observation.value.point for observation in standard_entropies} == {
        Decimal("113.805"),
        Decimal("195.811"),
        Decimal("164.055"),
    }
    source_entropies = [
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation["observation_id"].startswith(
            "zaitsev_1999_table6_ref17_standard_entropy_"
        )
    ]
    assert len(source_entropies) == 2
    assert all(
        observation["units"] == "J/mol K"
        and "J/(mol K)" in observation["locator"]["note"]
        for observation in source_entropies
    )


def test_zaitsev_1999_table1_error_scope_and_scored_bands():
    document, migrator = _migrate()
    migrated = tuple(migrator.result.observations.values())
    activities = [
        observation
        for observation in migrated
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(activities) == 102
    assert all(observation.identity.quantity.is_value for observation in migrated)
    assert all(
        observation.identity.species is None
        or observation.identity.species.phase.is_value
        for observation in migrated
    )
    assert all(
        observation.identity.species is not None
        and observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.L
        for observation in activities
    )
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in migrated
    ) == 0

    table1_na = [
        observation
        for observation in activities
        if "::zaitsev_1999_table1_na2o_activity::" in observation.observation_id
    ]
    table1_sio2 = [
        observation
        for observation in activities
        if "::zaitsev_1999_table1_sio2_activity::" in observation.observation_id
    ]
    table4 = [
        observation
        for observation in activities
        if "::zaitsev_1999_table4_" in observation.observation_id
    ]
    assert len(table1_na) == len(table1_sio2) == 29
    assert len(table4) == 44
    table1 = table1_na + table1_sio2
    assert all(
        observation.uncertainty.kind is UncertaintyKind.PRINTED
        and observation.uncertainty.verbatim["value"] == 3
        and observation.uncertainty.verbatim["units"] == "%"
        and observation.uncertainty.verbatim["confidence_level"] == "not_stated"
        and observation.uncertainty.verbatim["locator"]["published_page"] == 1903
        for observation in table1
    )
    assert all(
        observation.uncertainty.kind is UncertaintyKind.NONE
        for observation in table4
    )

    table1_na_by_row = {}
    for observation in table1_na:
        match = re.search(r"row=(\d+):", observation.observation_id)
        assert match is not None
        table1_na_by_row[int(match.group(1))] = observation
    render_values = {1: "7.19e-10", 2: "2.61e-9", 3: "5.29e-11"}
    # Table 1 cells rendered on p. 1901, checked after isolated migration.
    for row, value in render_values.items():
        observation = table1_na_by_row[row]
        assert observation.value.point == Decimal(value)
        assert observation.locator.page == 3
        assert observation.locator.published_page == 1901
        assert observation.locator.pdf_page_index == 2
        assert observation.locator.table == "1"

    for row, expected_temperature, expected_x_na2o in (
        (1, "1473", "0.195"),
        (2, "1573", "0.195"),
        (3, "1273", "0.247"),
    ):
        identity = table1_na_by_row[row].identity
        assert identity.temperature_K.value == Decimal(expected_temperature)
        assert identity.composition.value.as_map() == {
            "Na2O": Decimal(expected_x_na2o),
            "SiO2": Decimal(1) - Decimal(expected_x_na2o),
        }
        assert identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa.is_unknown

    table4_present_render_pin = next(
        observation
        for observation in table4
        if observation.identity.temperature_K.value == Decimal("1430")
        and observation.identity.composition.value.as_map()["Na2O"]
        == Decimal("0.103")
        and "table4_present_na2o_activity" in observation.observation_id
    )
    assert table4_present_render_pin.value.point == Decimal(
        "4.036453929676049704380262802E-10"
    )
    assert table4_present_render_pin.locator.page == 6
    assert table4_present_render_pin.locator.published_page == 1904
    assert table4_present_render_pin.locator.pdf_page_index == 5
    assert table4_present_render_pin.locator.table == "4"

    parsed_table1_bands = [
        _printed_uncertainty_band(
            Quantity.ACTIVITY,
            observation.uncertainty,
            observation.value.point,
            source_observation=observation,
        ).band
        for observation in table1
    ]
    assert len(parsed_table1_bands) == 58
    assert all(band is not None for band in parsed_table1_bands)
    assert all(
        Decimal("0.013") < band.value < Decimal("0.014")
        for band in parsed_table1_bands
        if band is not None
    )

    scored = [table1_na_by_row[row] for row in range(1, 11)]
    context = ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations={
            observation.observation_id: observation for observation in scored
        },
        extract_review={document["source_id"]: document.get("review_status")},
        benches=migrator.result.benches,
        root=ROOT,
    )
    without_uncertainty = replace(
        context,
        observations={
            observation_id: replace(
                observation,
                uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
            )
            for observation_id, observation in context.observations.items()
        },
    )
    before, _ = score_store(
        without_uncertainty,
        engines=(Engine.OPENIMCC,),
        include_diagnostics=False,
    )
    after, _ = score_store(
        context,
        engines=(Engine.OPENIMCC,),
        include_diagnostics=False,
    )
    before_by_reference = {residual.reference: residual for residual in before}
    after_by_reference = {residual.reference: residual for residual in after}
    assert len(before_by_reference) == len(after_by_reference) == 10
    assert set(before_by_reference) == set(after_by_reference)
    for reference in before_by_reference:
        before_residual = before_by_reference[reference]
        after_residual = after_by_reference[reference]
        assert before_residual.numeric is not None
        assert after_residual.numeric is not None
        assert before_residual.status is ResidualStatus.NO_BAND
        assert before_residual.numeric.decision_band is None
        assert after_residual.status is ResidualStatus.NO_BAND
        assert after_residual.numeric.decision_band is None
        assert after_residual.numeric.metric_uncertainty is not None
        assert after_residual.numeric.metric_uncertainty.kind is UncertaintyKind.PRINTED
        assert before_residual.numeric.value == after_residual.numeric.value
        assert any(
            notice.kind is NoticeKind.UNVERIFIED_APPARATUS
            for notice in after_residual.notices
        )
        assert any(
            notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
            for notice in after_residual.notices
        )
