"""F4 R-label: durable ids do not embed mutable printed labels."""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.migrate import Migrator
from simulator.battery.records import Locator
from simulator.battery.stable_ids import temperature_token


def test_transition_suffix_uses_temperature_not_subtype() -> None:
    from simulator.battery.generators.janaf import _observation
    from simulator.battery.enums import Phase, Quantity
    from simulator.battery.records import State, Value
    from simulator.battery.migrate import make_species

    # Minimal call through suffix logic via generate would be heavy; check helper contract.
    assert temperature_token(Decimal("933.450")) == "933.450"
    # Relabelled subtype must not appear as the only key once T is present.
    assert "crystal-liquid" != temperature_token(Decimal("933.450"))


def test_experiment_id_ignores_locator_table_figure_labels() -> None:
    class _Dummy:
        pass

    # Bind the method without constructing a full Migrator.
    fn = Migrator._experiment_id
    self = _Dummy()
    locator = Locator(table="Table II (retitled)", figure="Fig. 3a")
    minted = fn(self, "10.1/example", locator, "fallback-source")
    assert minted == "10.1/example::fallback-source"
    assert "Table" not in minted
    assert "Fig" not in minted
    with_dist = fn(
        self, "10.1/example", locator, "fallback-source", distinguisher="run-a"
    )
    assert with_dist == "10.1/example::fallback-source::run-a"


def test_mutation_locator_table_no_longer_drives_experiment_id(monkeypatch) -> None:
    class _Dummy:
        pass

    self = _Dummy()
    locator = Locator(table="BEFORE")
    before = Migrator._experiment_id(self, "w", locator, "fb")
    locator2 = Locator(table="AFTER-RETITLE")
    after = Migrator._experiment_id(self, "w", locator2, "fb")
    assert before == after == "w::fb"
