"""Remote-gate probe for actual pressure and oxygen engine requests."""

from __future__ import annotations

import json
import warnings
from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import AmountBasis, FO2Channel, Quantity
from simulator.battery.records import (
    Composition,
    FO2Control,
    Located,
    Locator,
    State,
    Value,
)
from tests.battery import factories as F
from tests.battery.census_cvpress_round18_round20 import (
    actual_engine_request,
    census_table,
)


def test_actual_engine_request_census():
    """Compare old and current assembled requests at the backend boundary."""

    table = census_table()
    assert table
    for row in table:
        warnings.warn(json.dumps(row, sort_keys=True), stacklevel=1)
        assert row["rows"] > 0, row
        assert row["changed_requests"] == 0, row
        assert row["changed_pressure_inputs"] == 0, row
        assert row["changed_oxygen_inputs"] == 0, row
        assert row["changed_requests"] >= row["changed_pressure_inputs"], row
        assert row["changed_requests"] >= row["changed_oxygen_inputs"], row
        assert row["round18_backend_calls"] + row["round18_omitted"] == row["rows"]
        assert row["current_backend_calls"] + row["current_omitted"] == row["rows"]


def test_backend_capture_stops_before_native_engine():
    """The captured tuple comes from the actual backend adapter boundary."""

    import simulator.battery.score as current_score

    location = Locator(page=3, table="2", record="run 1", note="column=pO2")
    experiment = replace(
        F.tabulation_experiment(),
        fO2_control=FO2Control(
            State.of(FO2Channel.COMMANDED),
            oxygen_partial_pressure_Pa=Located(
                State.of(Value.point_of(Decimal("10"))), location,
            ),
        ),
    )
    identity = replace(
        F.psat_identity("O2"),
        quantity=State.of(Quantity.P_PARTIAL),
        reservoir=None,
        composition=State.of(Composition(
            "oxides",
            (("FeO", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
            AmountBasis.MOLE_FRACTION,
        )),
        total_pressure_Pa=State.of(Decimal("101325")),
        fO2_Pa=State.unknown("oxygen not printed on this partial-pressure row"),
    )
    row = replace(
        F.observation("synthetic-o2", experiment.experiment_id, identity, Decimal("10")),
        locator=location,
    )

    request = actual_engine_request(current_score, row, experiment, None)
    assert request[0] == "backend_call", request
    assert request[1] is not None
    assert request[2] is not None
