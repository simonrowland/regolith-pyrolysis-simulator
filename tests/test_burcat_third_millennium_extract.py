from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, StateTag, UncertaintyKind, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


def test_burcat_formation_enthalpies_migrate_without_choosing_competing_methods() -> None:
    migrator = migrate_one_extract(
        REPO_ROOT,
        REPO_ROOT / "data/literature/extracts/burcat-third-millennium.yaml",
    )
    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }

    assert len(observations) == 42
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.DELTA_FH
        for observation in observations.values()
    )

    numeric = [
        observation
        for observation in observations.values()
        if observation.value.kind is ValueKind.POINT
    ]
    assert len(numeric) == 42
    assert observations["burcat_2005_table1_C_gas"].value.point == Decimal("717.065")
    assert observations["burcat_2005_table1_O_gas"].value.point == Decimal("249.229")
    assert observations["burcat_2005_table3_OH_radical"].value.point == Decimal("37.34")

    table2_values = {
        "burcat_2005_table2_CH4_bomb_calorimeter": ("CH4", "Bomb Calorimeter", "-74.85", "0.29"),
        "burcat_2005_table2_CH4_flame_calorimeter": ("CH4", "Flame Calorimeter", "-74.48", "0.42"),
        "burcat_2005_table2_C2H6_bomb_calorimeter": ("C2H6", "Bomb Calorimeter", "-84.68", "0.50"),
        "burcat_2005_table2_C2H6_flame_calorimeter": ("C2H6", "Flame Calorimeter", "-83.85", "0.29"),
        "burcat_2005_table2_C3H8_bomb_calorimeter": ("C3H8", "Bomb Calorimeter", "-103.89", "0.59"),
        "burcat_2005_table2_C3H8_flame_calorimeter": ("C3H8", "Flame Calorimeter", "-104.68", "0.50"),
        "burcat_2005_table2_nC4H10_bomb_calorimeter": ("n-C4H10", "Bomb Calorimeter", "-127.03", "0.67"),
        "burcat_2005_table2_nC4H10_flame_calorimeter": ("n-C4H10", "Flame Calorimeter", "-125.65", "0.67"),
        "burcat_2005_table2_iC4H10_bomb_calorimeter": ("i-C4H10", "Bomb Calorimeter", "-135.60", "0.54"),
        "burcat_2005_table2_iC4H10_flame_calorimeter": ("i-C4H10", "Flame Calorimeter", "-134.18", "0.63"),
    }
    assert set(table2_values) <= set(observations)
    for observation_id, (species, calorimeter, value, uncertainty) in table2_values.items():
        observation = observations[observation_id]
        assert observation.identity.quantity.value is Quantity.DELTA_FH
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(value)
        assert observation.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        assert observation.evidence.attribution == "Cohen and Benson (1992)"
        assert observation.uncertainty.kind is UncertaintyKind.PRINTED
        assert isinstance(observation.uncertainty.verbatim, dict)
        assert observation.uncertainty.verbatim["value"] == float(uncertainty)
        assert observation.uncertainty.verbatim["units"] == "kJ/mol"
        assert observation.point_conditions["temperature_K"].state.tag is StateTag.VALUE
        assert observation.point_conditions["temperature_K"].state.value == Decimal("298.15")
        assert observation.point_conditions["temperature_K"].locator == observation.locator
        assert observation.locator.page == 14 and observation.locator.table == "2"

    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/burcat-third-millennium.yaml").read_text(
            encoding="utf-8"
        )
    )
    for observation_id, (species, calorimeter, value, uncertainty) in table2_values.items():
        raw = next(
            row
            for row in source["species"][species]["observations"]
            if row["observation_id"] == observation_id
        )
        assert raw["values"]["calorimeter_as_printed"] == calorimeter
        assert raw["values"]["delta_fH298_kJ_per_mol"] == float(value)
        assert raw["uncertainty"]["value"] == float(uncertainty)
        assert raw["values"]["attribution"] == "Cohen and Benson (1992)"
