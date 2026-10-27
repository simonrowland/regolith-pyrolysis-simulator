from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import StateTag, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/zhang-2011-boron-sodium-f-free-evaporation.yaml"
EXPECTED_FLUXES = {
    "zhang_2011_cs3_1300c_measured_flux": (1300, 5.46, "CS-3"),
    "zhang_2011_cs3_1350c_measured_flux": (1350, 14.5, "CS-3"),
    "zhang_2011_cs3_1400c_measured_flux": (1400, 20.4, "CS-3"),
    "zhang_2011_cs3zro2_1300c_measured_flux": (1300, 9.64, "CS-3 + 2 wt% ZrO2"),
    "zhang_2011_cs3zro2_1350c_measured_flux": (1350, 20.1, "CS-3 + 2 wt% ZrO2"),
    "zhang_2011_cs3zro2_1400c_measured_flux": (1400, 28.1, "CS-3 + 2 wt% ZrO2"),
    "zhang_2011_cs4_1300c_measured_flux": (1300, 8.78, "CS-4"),
    "zhang_2011_cs4_1350c_measured_flux": (1350, 15.3, "CS-4"),
    "zhang_2011_cs4_1400c_measured_flux": (1400, 24.4, "CS-4"),
}


def _assert_table6_render_pins(document: dict) -> None:
    rows = {
        item["observation_id"]: item
        for item in document["species"]["slag"]["observations"]
    }
    assert len(rows) == 9
    assert len({row["values"]["T_C"] for row in rows.values()}) == 3
    for observation_id, (temperature_c, flux, slag_id) in EXPECTED_FLUXES.items():
        row = rows[observation_id]
        assert row["locator"]["page"] == 8
        assert row["locator"]["table"] == "6"
        assert row["values"]["T_C"] == temperature_c
        assert row["values"]["mass_loss_rate"] == flux
        assert row["values"]["slag_id"] == slag_id
        assert row["values"]["admission_status"] == "typed_refusal"


def test_zhang_2011_table6_values_and_conditions_migrate() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    _assert_table6_render_pins(document)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 9
    assert not result.validation.hard_issues

    by_id = {key.split("::", 1)[1]: value for key, value in result.observations.items()}
    for observation_id, (temperature_c, _flux, _slag_id) in EXPECTED_FLUXES.items():
        observation = by_id[observation_id]
        assert observation.identity.temperature_K.tag is StateTag.VALUE
        assert observation.identity.temperature_K.value == Decimal(str(temperature_c + 273.15))
        # The source prints this composition (and the point condition below
        # carries it at Table 2). Current mass_loss_rate migration does not
        # bind it into identity; see the extract's recorded migrator gap.
        assert observation.identity.composition.tag is StateTag.UNKNOWN
        assert "no composition field" in observation.identity.composition.reason
        assert observation.identity.total_pressure_Pa.tag is StateTag.UNKNOWN
        assert observation.identity.fO2_Pa.tag is StateTag.UNKNOWN
        assert observation.point_conditions["T_C"].state.value == Decimal(temperature_c)
        assert observation.point_conditions["T_C"].locator.page == 8
        assert observation.point_conditions["T_C"].locator.table == "6"
        assert observation.point_conditions["atmosphere"].state.value == "argon"
        gas_flow = observation.point_conditions["gas_flow"]
        assert gas_flow.state.tag is StateTag.VALUE
        assert "five times" in gas_flow.state.value
        assert gas_flow.locator.page == 2
        assert gas_flow.locator.section == "2.2 Experimental Procedures"
        assert observation.point_conditions["composition"].state.tag is StateTag.VALUE
        assert observation.point_conditions["composition"].locator.page == 2
        assert observation.point_conditions["composition"].locator.table == "2"
        assert (
            observation.point_conditions["composition"].inference.relation
            == "wt_pct_to_mole_fraction"
        )
        assert observation.value.kind is ValueKind.UNAVAILABLE
        assert "unmapped mass loss rate unit" in observation.value.unavailable_reason


def test_zhang_2011_render_pin_detects_flux_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    _assert_table6_render_pins(document)
    document["species"]["slag"]["observations"][0]["values"]["mass_loss_rate"] = 5.47
    with pytest.raises(AssertionError):
        _assert_table6_render_pins(document)
