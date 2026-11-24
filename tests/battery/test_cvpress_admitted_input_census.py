"""Remote-gate probe for the fold-21 admitted-input census.

The table is a warning so ``pytest -q`` keeps it. The four iron-redox
sources have no O2 fugacity setpoint, so their lost-exemption count is
zero and is reported, not required. A synthetic same-cell O2 row is the
executable check that round 18's exemption is lost and the admitted
oxygen changes.
"""

from __future__ import annotations

import sys
import warnings
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import FO2Channel, Quantity
from simulator.battery.records import FO2Control, Located, Locator, State, Value
from tests.battery import factories as F
from tests.battery.census_cvpress_round18_round20 import (
    _load_round18,
    census_table,
    round18_admitted,
    round21_admitted,
)


def test_admitted_engine_input_census():
    table = census_table()
    assert "simulator.battery.fo2_ladder" not in sys.modules
    assert table
    for row in table:
        warnings.warn(
            f"{row['source']} rows={row['rows']} imposed={row['imposed']} "
            f"lost_exemptions={row['lost_exemptions']} "
            f"changed_engine_inputs={row['changed_engine_inputs']} "
            f"gained_exemptions={row['gained_exemptions']}",
            stacklevel=1,
        )
        assert row["rows"] > 0, row
        assert row["lost_exemptions"] == row["imposed"], row
        assert row["gained_exemptions"] == 0, row


def test_synthetic_o2_same_cell_loses_the_round18_exemption():
    root = Path(__file__).resolve().parents[2]
    old = _load_round18(root)
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
        quantity=State.of(Quantity.FUGACITY),
        reservoir=None,
        total_pressure_Pa=None,
    )
    row = replace(
        F.observation("synthetic-o2", experiment.experiment_id, identity, Decimal("10")),
        locator=location,
    )
    was_imposed, previous = round18_admitted(old, row, experiment, Quantity.FUGACITY)
    current = round21_admitted(row, experiment, Quantity.FUGACITY)
    assert was_imposed
    assert previous != current
    assert "simulator.battery.fo2_ladder" not in sys.modules
