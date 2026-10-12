"""The measured Zigo cooling column is H(T)-H(298 K), with source lineage."""

from decimal import Decimal

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator


def test_zigo_measured_cooling_column_maps_to_H_minus_H298_with_provenance() -> None:
    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(
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
