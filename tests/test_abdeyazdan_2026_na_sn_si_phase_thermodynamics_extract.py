from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, QuenchState
from simulator.battery.identity import celsius_to_kelvin
from simulator.battery.migrate import migrate
from literature_extract_test_utils import isolated_root


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/abdeyazdan-2026-na-sn-si-phase-thermodynamics.yaml"

HTML_PINS = {
    "abdeyazdan_2026_t1_run01_NaSi9_r_glass_sio2": (
        "91.0 ± 0.17 mol.%",
        "1",
        4,
    ),
    "abdeyazdan_2026_t2_run01_NSnS2_glass_sio2": (
        "53.1 ± 0.60 mol.%",
        "2",
        4,
    ),
    "abdeyazdan_2026_t2_run01_NSnS2_glass_sno2": (
        "5.45 ± 0.07 mol.%",
        "2",
        4,
    ),
    "abdeyazdan_2026_t2_run01_NSnS2_glass_nao0p5": (
        "41.4 ± 0.70 mol.%",
        "2",
        4,
    ),
    "abdeyazdan_2026_t3_run01_NsnS35_glass_sio2": (
        "58.6 ± 0.46 mol.%",
        "3",
        4,
    ),
    "abdeyazdan_2026_t3_run01_NsnS35_glass_sno": (
        "12.1 ± 0.55 mol.%",
        "3",
        4,
    ),
    "abdeyazdan_2026_t3_run01_NsnS35_glass_nao0p5": (
        "29.3 ± 0.60 mol.%",
        "3",
        4,
    ),
}


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_html_pins(document: dict) -> None:
    observations = _observations(document)
    for observation_id, (value, table, html_row) in HTML_PINS.items():
        observation = observations[observation_id]
        assert observation["values"]["component_amount_as_published"] == value
        assert observation["locator"]["table"] == table
        assert observation["locator"]["html_row"] == html_row


def test_html_table_cells_and_row_counts_are_pinned_and_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert len(document["experiments"]) == 111
    observations = _observations(document)
    assert len(observations) == 452
    assert sum(row["locator"]["table"] == "1" for row in observations.values()) == 10
    assert sum(row["locator"]["table"] == "2" for row in observations.values()) == 188
    assert sum(row["locator"]["table"] == "3" for row in observations.values()) == 254
    _assert_html_pins(document)

    mutated = deepcopy(document)
    mutated_observation = _observations(mutated)[
        "abdeyazdan_2026_t2_run01_NSnS2_glass_sio2"
    ]
    mutated_observation["values"]["component_amount_as_published"] = "53.2 ± 0.60 mol.%"
    with pytest.raises(AssertionError):
        _assert_html_pins(mutated)


def test_isolated_migration_preserves_phase_conditions_and_printed_values(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert len(result.observations) == 452
    assert not result.validation.hard_issues

    by_id = {key.rsplit("::", 1)[1]: row for key, row in result.observations.items()}
    expected = {
        "abdeyazdan_2026_t1_run01_NaSi9_r_glass_sio2": (
            Decimal("0.91"), 1600, Phase.GLASS, PhaseRole.MELT
        ),
        "abdeyazdan_2026_t2_run01_NSnS2_glass_sio2": (
            Decimal("0.531"), 1200, Phase.GLASS, PhaseRole.MELT
        ),
        "abdeyazdan_2026_t3_run01_NsnS35_glass_sno": (
            Decimal("0.121"), 650, Phase.GLASS, PhaseRole.MELT
        ),
        "abdeyazdan_2026_t3_run01_NsnS35_metal_sn": (
            Decimal("1"), 650, Phase.CR, PhaseRole.METAL
        ),
    }
    for observation_id, (point, temperature, phase, role) in expected.items():
        observation = by_id[observation_id]
        identity = observation.identity
        assert identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.value.point == point
        assert identity.species.phase.value is phase
        assert identity.phase_role.value is role
        assert identity.temperature_K.value == celsius_to_kelvin(Decimal(temperature))
        assert identity.composition.value is None
        assert identity.total_pressure_Pa.value is None
        assert identity.fO2_Pa.value is None
        assert observation.point_conditions["T_C"].state.value == Decimal(temperature)
        assert observation.point_conditions["T_C"].locator.table == observation.locator.table

    first_glass = by_id["abdeyazdan_2026_t2_run01_NSnS2_glass_sio2"]
    assert first_glass.point_conditions["atmosphere"].state.value == "air"
    assert first_glass.quench_state is QuenchState.NOT_STATED
    metal = by_id["abdeyazdan_2026_t3_run01_NsnS35_metal_sn"]
    assert metal.point_conditions["atmosphere"].state.value == "equilibrium with Sn metal"

