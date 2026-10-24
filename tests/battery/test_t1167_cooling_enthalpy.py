"""The measured Zigo cooling column is H(T)-H(298 K), with source lineage."""

from decimal import Decimal

from simulator.battery.enums import EvidenceClass, Quantity, StateTag, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


def test_zigo_measured_cooling_column_maps_to_H_minus_H298_with_provenance() -> None:
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
    assert fit_point.locator.page == 174 and fit_point.locator.table == "1"
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
    assert all(point.identity.quantity.value is Quantity.H_MINUS_H298 for point in table4_cooling)
    assert all(point.evidence.class_.value is EvidenceClass.MEASURED_DIRECT for point in table4_cooling)
    assert all(point.locator.page == 177 and point.locator.table == "4" for point in table4_cooling)
    assert all(
        point.point_conditions["temperature_K"].state.tag is StateTag.VALUE
        and point.point_conditions["temperature_K"].locator == point.locator
        for point in table4_cooling
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
    assert fusion_temperature.locator.page == 172
