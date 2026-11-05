from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    Phase,
    Quantity,
    QuenchState,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from simulator.battery.records import phase_token
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mysen-1984-alkaline-earth-redox.yaml"
MAPPING = ROOT / "data/literature/extracts/audits/mysen-1984-alkaline-earth-redox.mapping.yaml"
SOURCE_ID = "mysen-1984-alkaline-earth-redox"


def _migrated_rows():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
        if row.observation_id.startswith(f"{SOURCE_ID}::")
    }
    return migrator.result, rows


def test_table1_render_pins_and_migrated_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = document["species"]["Fe"]["observations"]
    table_mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))["tables"][0]
    result, rows = _migrated_rows()

    assert len(source_rows) == 34
    assert len(rows) == 34
    assert table_mapping["row_count_printed"] == 34
    source_by_id = {source["observation_id"]: source for source in source_rows}
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    assert len(result.registry_issues) == 0
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 0

    rendered_values = {
        "mysen_1984_table1_r01_cs2f23": ("0.249", "CS2F2.3"),
        "mysen_1984_table1_r09_cs14f5": ("0.000", "CS1.4F5"),
        "mysen_1984_table1_r32_bs12f5": ("0.771", "BS1.2F5"),
    }
    for observation_id, (value, sample) in rendered_values.items():
        row = rows[observation_id]
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
        assert row.locator is not None
        assert row.locator.page == 2
        assert row.locator.table == "1"
        source_locator = source_by_id[observation_id]["locator"]
        assert source_locator["row"] == sample
        assert source_locator["table_row_number"] >= 3
        assert source_locator["table_column_number"] == 10

    table3_pin = next(
        sample
        for sample in document["fidelity_samples"]
        if sample["locator"].get("page") == 9
    )
    assert table3_pin["path"].endswith(
        "mysen_1984_table1_r09_cs14f5].values.value"
    )
    assert table3_pin["value"] == "0.000"
    assert table3_pin["locator"]["table"] == "3"
    assert table3_pin["locator"]["row"] == "CS1.4F5"

    for source in source_rows:
        observation_id = source["observation_id"]
        row = rows[observation_id]
        identity = row.identity
        conditions = row.point_conditions or {}

        assert identity.quantity.is_value
        assert identity.quantity.value is Quantity.VALENCE_RATIO
        assert phase_token(identity.species) is Phase.GLASS
        assert identity.valence_basis.is_value
        assert identity.valence_basis.value.numerator_oxidation_states == (3,)
        assert (
            identity.valence_basis.value.denominator_kind
            is ValenceDenominatorKind.TOTAL_ELEMENT
        )
        assert identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
        assert row.value.kind is ValueKind.POINT

        for key in ("T_C", "fO2_log", "composition", "quench_state"):
            assert key in conditions
            assert conditions[key].locator is not None
        assert conditions["T_C"].state.value == Decimal(source["values"]["T_C"])
        assert conditions["fO2_log"].state.value == -Decimal(
            source["values"]["minus_log_fO2_as_published"]
        )
        assert conditions["quench_state"].state.value is QuenchState.NOT_STATED
        assert identity.temperature_K.is_value
        assert identity.fO2_Pa.is_value
        assert identity.composition.is_value
        assert conditions["composition"].state.value == identity.composition.value
        assert source["locator"]["table"] == "1"
        assert source["locator"]["page"] == 2
        assert source["locator"]["table_row_number"] >= 3
        assert source["locator"]["table_column_number"] == 10

        source_pressure = source["point_conditions"]["total_pressure_Pa"]
        assert source_pressure["state"]["tag"] == "unknown"
        assert source_pressure["locator"]["page"] == 2
        assert "total_pressure_Pa" not in conditions
        assert identity.total_pressure_Pa.is_unknown

    assert all(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
        and phase_token(row.identity.species) is Phase.GLASS
        for row in rows.values()
    )

    for observation_id in (
        "mysen_1984_table1_r01_cs2f23",
        "mysen_1984_table1_r09_cs14f5",
        "mysen_1984_table1_r32_bs12f5",
    ):
        required = profile_for(rows[observation_id].identity).required
        identity = rows[observation_id].identity
        assert required == {
            "temperature_K",
            "total_pressure_Pa",
            "composition",
            "fO2_Pa",
            "valence_basis",
        }
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        assert identity.fO2_Pa.is_value
        assert identity.total_pressure_Pa.is_unknown
