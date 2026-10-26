"""The measured Zigo cooling column is H(T)-H(298 K), with source lineage."""

from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


def test_zigo_crystal_cooling_column_maps_to_H_minus_H298_with_provenance() -> None:
    migrator = migrate_one_extract(
        REPO_ROOT,
        REPO_ROOT / "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml"
    )

    point = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.H_MINUS_H298
        and observation.identity.temperature_K is not None
        and observation.identity.temperature_K.value == Decimal("1519")
    )

    assert point.value.kind is ValueKind.POINT
    assert point.value.point == Decimal("335.9")
    assert point.derivation is not None
    assert point.derivation.relation == "negative_cooling_enthalpy_to_H_minus_H298"
    assert dict(point.derivation.parameters)["original"].state.value == Decimal("335.9")


def test_zigo_table_fit_and_composite_rows_use_existing_quantities() -> None:
    migrator = migrate_one_extract(
        REPO_ROOT,
        REPO_ROOT / "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml",
    )
    migrator.finalize()
    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }

    fit_points = [
        observation
        for observation in observations.values()
        if observation.observation_id.startswith(
            "zigo-1987-gehlenite-fusion::zigo_1987_t1_crystal_cooling_fit::T="
        )
    ]
    assert len(fit_points) == 6
    fit_point = next(point for point in fit_points if point.value.point == Decimal("337.3"))
    assert fit_point.identity.quantity.value is Quantity.H_MINUS_H298
    assert fit_point.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert fit_point.derived_from
    assert all(
        source.startswith(
            "zigo-1987-gehlenite-fusion::zigo_1987_t1_crystal_cooling_measured::T="
        )
        for source in fit_point.derived_from
    )
    assert fit_point.locator.page == 4
    assert fit_point.locator.published_page == 174
    assert fit_point.locator.table == "1"
    assert fit_point.point_conditions["temperature_K"].state.value == Decimal("1519")
    assert fit_point.point_conditions["temperature_K"].locator == fit_point.locator

    table4_cooling = [
        observation
        for observation in observations.values()
        if observation.observation_id.startswith(
            "zigo-1987-gehlenite-fusion::zigo_1987_t4_melt_cooling_enthalpy_measured::T="
        )
    ]
    assert len(table4_cooling) == 5
    assert all(point.identity.quantity.tag is StateTag.UNKNOWN for point in table4_cooling)
    assert all(
        "sample_dependent_enthalpy_change_on_cooling" in point.identity.quantity.reason
        for point in table4_cooling
    )
    assert all(point.value.kind is ValueKind.UNAVAILABLE for point in table4_cooling)
    assert all(point.evidence.class_.value is EvidenceClass.MEASURED_DIRECT for point in table4_cooling)
    assert all(point.locator.page == 177 and point.locator.table == "4" for point in table4_cooling)
    expected_temperatures = {
        Decimal("1873"), Decimal("1888"), Decimal("1908"), Decimal("1932"), Decimal("1948")
    }
    assert {
        point.point_conditions["temperature_K"].state.value for point in table4_cooling
    } == expected_temperatures
    assert all(
        point.point_conditions["temperature_K"].state.tag is StateTag.VALUE
        and point.point_conditions["temperature_K"].locator == point.locator
        for point in table4_cooling
    )
    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/zigo-1987-gehlenite-fusion.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw_cooling = next(
        observation
        for observation in source["species"]["gehlenite"]["observations"]
        if observation["observation_id"] == "zigo_1987_t4_melt_cooling_enthalpy_measured"
    )["values"]
    assert raw_cooling["quantity"] == "sample_dependent_enthalpy_change_on_cooling"
    assert "pp. 172 and 178" in raw_cooling["note"]
    assert "proportions and structures" in raw_cooling["note"]
    assert [point["minus_delta_H_cool_kJ_mol-1"] for point in raw_cooling["points"]] == [
        545.1, 548.9, 561.5, 571.7, 584.3
    ]

    melt_relative_enthalpy = [
        observation
        for observation in observations.values()
        if observation.observation_id.split("::", 1)[1].startswith(
            "zigo_1987_t4_melt_relative_enthalpy_fit"
        )
    ]
    assert melt_relative_enthalpy
    assert all(
        observation.identity.quantity.tag is StateTag.UNKNOWN
        and observation.value.kind is ValueKind.UNAVAILABLE
        for observation in melt_relative_enthalpy
    )

    sensitivity = [
        observation
        for observation in observations.values()
        if observation.observation_id.startswith(
            "zigo-1987-gehlenite-fusion::zigo_1987_fusion_temperature_sensitivity::T="
        )
    ]
    assert len(sensitivity) == 2
    assert {point.value.point for point in sensitivity} == {
        Decimal("171.1"), Decimal("172.6")
    }
    assert all(point.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION for point in sensitivity)
    assert all(point.evidence.class_.value is EvidenceClass.MODEL_DERIVED for point in sensitivity)
    assert all(point.derived_from for point in sensitivity)
    assert all(point.derivation is not None for point in sensitivity)
    assert all(point.point_conditions["temperature_K"].locator == point.locator for point in sensitivity)

    fusion_temperature = observations["zigo_1987_fusion_temperature_selected"]
    assert fusion_temperature.identity.quantity.tag is StateTag.UNKNOWN
    assert fusion_temperature.value.kind is ValueKind.UNAVAILABLE
    assert fusion_temperature.point_conditions["temperature_K"].state.value == Decimal("1863")
    assert fusion_temperature.point_conditions["temperature_K"].locator == fusion_temperature.locator
    assert fusion_temperature.locator.page == 2
    assert fusion_temperature.locator.published_page == 172
