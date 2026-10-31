"""B-702 regressions for extract standard-state typing clusters."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"


def test_b702_standard_state_clusters_migrate(tmp_path: Path) -> None:
    sossi = _migrate_real_extract(
        tmp_path / "sossi-fegley", "kems-041-sossi-fegley-2018.yaml"
    )
    table_rows = [
        observation
        for observation in sossi.observations.values()
        if "::sossi_fegley_2018_table2_gamma_" in observation.observation_id
    ]
    assert len(table_rows) == 44
    unresolved = [
        item
        for item in table_rows
        if item.identity.reference_state is not None
        and item.identity.reference_state.is_unknown
    ]
    assert len(unresolved) == 42
    assert len(
        {
            item.observation_id.split("::", 1)[1].split("::", 1)[0]
            for item in unresolved
        }
    ) == 29
    assert all(
        item.identity.reference_state.reason
        == "compiled coefficients whose solid/liquid reference is set by each cited primary study"
        for item in unresolved
    )
    solid_rows = [
        item
        for item in table_rows
        if item.identity.reference_state is not None
        and item.identity.reference_state.is_value
    ]
    assert len(solid_rows) == 2
    assert {
        item.identity.reference_state.value.endmember.formula
        for item in solid_rows
    } == {"CaO", "MgO"}
    assert all(
        item.identity.reference_state.value.endmember.phase.is_value
        and item.identity.reference_state.value.endmember.phase.value.value == "cr"
        for item in solid_rows
    )

    ichise_doc = yaml.safe_load(
        (EXTRACTS / "kems-066-ichise-1977.yaml").read_text(encoding="utf-8")
    )
    ichise_rows = [
        item
        for item in ichise_doc["species"]["Al"]["observations"]
        if item["observation_id"].startswith(
            "ichise_1977_al_activity_table1_1400C_nal_"
        )
    ]
    assert len(ichise_rows) == 12
    assert all(item["values"]["point"]["T_C"] == 1400.0 for item in ichise_rows)
    al_states = [
        battery_migrate.reference_state_from_extract(
            item["standard_state"],
            species_formula="Al",
            values=item["values"],
        )
        for item in ichise_rows
    ]
    assert all(state is not None and state.is_unknown for state in al_states)
    assert all(
        state.reason
        == "Table 1 reports Al activity on both pure solid and pure liquid reference scales in one observation"
        for state in al_states
    )

    ichise = _migrate_real_extract(
        tmp_path / "ichise", "kems-066-ichise-1977.yaml"
    )
    migrated_al_rows = [
        item
        for item in ichise.observations.values()
        if "::ichise_1977_al_activity_table1_1400C_nal_" in item.observation_id
    ]
    assert len(migrated_al_rows) == 12
    assert all(item.point_conditions is not None for item in migrated_al_rows)
    assert all(
        item.point_conditions["temperature_K"].state.is_value
        and item.point_conditions["temperature_K"].locator is not None
        and item.point_conditions["temperature_K"].locator.published_page == 421
        for item in migrated_al_rows
    )

    furukawa_doc = yaml.safe_load(
        (EXTRACTS / "kems-069-furukawa-1976.yaml").read_text(encoding="utf-8")
    )
    furukawa_clusters = (
        ("Ti", "furukawa_1976_ti_activity_table4_1600C", "Ti"),
        ("Fe", "furukawa_1976_fe_activity_table4_1600C", "Fe"),
    )
    for species, observation_id, endmember in furukawa_clusters:
        row = next(
            item
            for item in furukawa_doc["species"][species]["observations"]
            if item["observation_id"] == observation_id
        )
        reference_state = battery_migrate.reference_state_from_extract(
            row["standard_state"],
            species_formula=species,
            values=row["values"],
        )
        assert reference_state is not None and reference_state.is_value
        assert reference_state.value.convention.value == "raoultian_pure_endmember"
        assert reference_state.value.endmember.formula == endmember
        assert reference_state.value.endmember.phase.is_value
        assert reference_state.value.endmember.phase.value.value == "l"

    furukawa = _migrate_real_extract(
        tmp_path / "furukawa", "kems-069-furukawa-1976.yaml"
    )
    for _species, observation_id, _endmember in furukawa_clusters:
        source_row = next(
            item
            for item in furukawa_doc["species"][_species]["observations"]
            if item["observation_id"] == observation_id
        )
        assert source_row["values"]["T_C"] == 1600.0
        assert all(
            point["T_C"] == 1600.0 for point in source_row["values"]["points"]
        )
        migrated_rows = [
            item
            for item in furukawa.observations.values()
            if item.observation_id.endswith(f"::{observation_id}")
        ]
        assert len(migrated_rows) == 1
        migrated_row = migrated_rows[0]
        assert migrated_row.value.kind.value == "unavailable"
        assert migrated_row.identity.temperature_K.is_value
        assert migrated_row.identity.temperature_K.value == Decimal("1873.15")
        assert migrated_row.point_conditions is not None
        temperature = migrated_row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1873.15")
        assert temperature.locator is not None
        assert temperature.locator.published_page == 385

    ueda = _migrate_real_extract(
        tmp_path / "ueda", "kems-095-ueda-1986.yaml"
    )
    ti_rows = [
        item
        for item in ueda.observations.values()
        if "::ueda_1986_gamma_ti_table2_nco_" in item.observation_id
    ]
    assert len(ti_rows) == 11
    assert all(item.identity.reference_state is not None for item in ti_rows)
    assert all(item.identity.reference_state.is_value for item in ti_rows)
    assert all(
        item.identity.reference_state.value.convention.value
        == "raoultian_pure_endmember"
        and item.identity.reference_state.value.endmember.formula == "Ti"
        and item.identity.reference_state.value.endmember.phase.is_value
        and item.identity.reference_state.value.endmember.phase.value.value == "l"
        for item in ti_rows
    )

    hastie_doc = yaml.safe_load(
        (EXTRACTS / "kems-020-hastie-1981-nbsir.yaml").read_text(
            encoding="utf-8"
        )
    )
    hastie_row = next(
        item
        for item in hastie_doc["species"]["NaCl"]["observations"]
        if item["observation_id"] == "hastie_1981_fig15_salt_slag_exchange"
    )
    reference_state = battery_migrate.reference_state_from_extract(
        hastie_row["standard_state"],
        species_formula="NaCl",
        values=hastie_row["values"],
    )
    assert reference_state is not None and reference_state.is_value
    assert reference_state.value.convention.value == "raoultian_pure_endmember"
    assert reference_state.value.endmember.formula == "NaCl"
    assert reference_state.value.endmember.phase.is_value
    assert reference_state.value.endmember.phase.value.value == "l"
