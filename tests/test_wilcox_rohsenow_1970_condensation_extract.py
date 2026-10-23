from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, StateTag
from simulator.battery.migrate import convert_temperature_to_k
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/wilcox-rohsenow-1970-k-film-condensation.yaml"


def test_wilcox_rohsenow_table_one_cells_and_fahrenheit_migration():
    """Pins Table 1's printed values (PDF p. 6) and all 22 row count."""
    document = yaml.safe_load(EXTRACT.read_text())
    rows = document["species"]["K"]["observations"]
    assert len(rows) == 22

    by_run = {row["values"]["run_no_as_printed"]: row for row in rows}
    # Table 1 PDF p. 6, rows 1, 3, 14, and 22. Values are pinned to the
    # rendered table after reconciling MinerU with the text layer.
    assert by_run["1"]["values"]["alpha"] == "0.873"
    assert by_run["1"]["T_F"] == "851.66"
    assert by_run["1"]["values"]["method_class"] == "calculated"
    assert "status" not in by_run["1"]["values"]
    assert "evidence_class" not in by_run["1"]["values"]
    assert (
        by_run["3"]["point_conditions"]["q_over_A_Btu_hr_ft2_as_printed"]
        ["state"]["value"]
        == "51687"
    )
    assert by_run["14"]["values"]["alpha"] == "0.975"
    assert (
        by_run["21"]["point_conditions"]["T_v_F_as_printed"]["state"]["value"]
        == "989.38"
    )
    assert (
        by_run["22"]["point_conditions"]["T_w_F_as_printed"]["state"]["value"]
        == "1096.87"
    )
    assert (
        by_run["22"]["point_conditions"]["P_v_atm_as_printed"]["state"]["value"]
        == "0.1516"
    )

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
        if observation.source_id == EXTRACT.stem
    }
    assert len(migrated) == 22
    assert not migrator.result.validation.hard_issues
    assert all(
        o.identity.quantity.value is Quantity.CONDENSATION_COEFFICIENT
        for o in migrated.values()
    )
    # The shared conditional-reduction gate currently requires stored parent
    # observations, which Table 1 does not provide as separate quantities.
    assert all(
        o.evidence.class_.reason
        == "author reduction requires an author derivation and admitted measured lineage"
        for o in migrated.values()
    )
    assert all(o.identity.temperature_K.tag is StateTag.VALUE for o in migrated.values())
    assert all(o.identity.total_pressure_Pa.tag is StateTag.UNKNOWN for o in migrated.values())
    assert all(
        o.identity.total_pressure_Pa.reason == "source does not state a numeric total pressure"
        for o in migrated.values()
    )
    expected_conditions = {
        "T_v_F_as_printed",
        "T_w_F_as_printed",
        "T_cu_ni_F_as_printed",
        "q_over_A_Btu_hr_ft2_as_printed",
        "P_v_atm_as_printed",
        "P_s_atm_as_printed",
    }
    for observation in migrated.values():
        assert expected_conditions <= observation.point_conditions.keys()
        assert all(
            condition.state.is_value and condition.locator.page == 6
            for condition in observation.point_conditions.values()
        )
    for run in ("1", "3", "14", "22"):
        observation = migrated[f"wilcox_rohsenow_1970_k_run_{run}"]
        temperature_k, trail = convert_temperature_to_k(by_run[run]["T_F"], "F")
        assert observation.identity.temperature_K.value == temperature_k
        assert trail == "fahrenheit_to_kelvin"
        assert observation.point_conditions["temperature_K"].inference.relation == trail
        assert observation.point_conditions["temperature_K"].locator.published_page == 364
        assert observation.point_conditions["P_v_atm_as_printed"].locator.pdf_page_index == 5
    assert migrated["wilcox_rohsenow_1970_k_run_1"].value.point == Decimal("0.873")
