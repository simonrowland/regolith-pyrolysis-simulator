from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import IdentityEqualKind, Phase, Quantity, UncertaintyKind
from simulator.battery.identity import identity_equal, profile_for, quantity_token
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = "costa-2025-ree-cmas-glass-melt-thermochemistry.yaml"


def _table3_rows(result):
    return [
        row
        for row in result.observations.values()
        if "costa_2025_" in row.observation_id and "_table3" in row.observation_id
    ]


def _one(rows, observation_id: str):
    matches = [row for row in rows if observation_id in row.observation_id]
    assert len(matches) == 1, [row.observation_id for row in matches]
    return matches[0]


def test_costa_table3_migrates_composition_temperature_and_source_conditions(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, EXTRACT)
    rows = _table3_rows(result)

    assert len(rows) == 28
    assert sum(quantity_token(row.identity) is Quantity.TRANSITION_TEMPERATURE for row in rows) == 21
    assert sum(quantity_token(row.identity) is Quantity.ENTHALPY_OF_FUSION for row in rows) == 7
    assert result.validation is not None
    assert result.validation.hard_issues == ()

    for row in rows:
        assert row.identity.species.formula == "CaO"
        assert row.identity.composition.is_value
        assert "composition" in profile_for(row.identity).required
        assert row.locator.page == 11
        assert row.locator.pdf_page_index == 10
        assert row.value.point is not None

        conditions = row.point_conditions
        assert conditions is not None
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator.page == 11
        composition = conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.page == 11
        assert composition.locator.table == "1"
        assert composition.locator.note is not None
        assert composition.locator.note.startswith("row=")
        gas = conditions["gas_environment"]
        assert gas.state.is_value
        assert gas.locator.page == 3
        assert conditions["fO2_Pa"].state.is_unknown
        assert conditions["total_pressure_Pa"].state.is_unknown

        if "_tg_table3" in row.observation_id or "_tc_table3" in row.observation_id:
            assert row.identity.species.phase.value is Phase.GLASS
            quench = conditions["quench_state"]
            assert quench.state.value == "asserted_preserved"
            assert quench.locator.page == 4
        elif "_tm_table3" in row.observation_id or "_heat_of_fusion_table3" in row.observation_id:
            assert row.identity.species.phase.value is Phase.CR

    y_l = _one(rows, "costa_2025_y_l_heat_of_fusion_table3")
    y_h = _one(rows, "costa_2025_y_h_heat_of_fusion_table3")
    assert y_l.point_conditions["temperature_K"].state.value == Decimal("1462")
    assert y_h.point_conditions["temperature_K"].state.value == Decimal("1462")
    assert y_l.identity.composition != y_h.identity.composition
    # The paper leaves the crystalline polymorph unidentified, so this pair is
    # indeterminate on that axis; distinct printed compositions still cannot
    # produce an equal identity.
    assert identity_equal(y_l.identity, y_h.identity).kind is not IdentityEqualKind.EQUAL


def test_costa_table3_render_pins_use_physical_pdf_page_and_printed_values(
    tmp_path: Path,
) -> None:
    rows = _table3_rows(_migrate_real_extract(tmp_path, EXTRACT))
    render_pins = (
        (
            "costa_2025_cmas_tg_table3",
            Decimal("1021"),
            Decimal("5"),
            "row=CMAS",
            "column=Tg (K)",
        ),
        (
            "costa_2025_y_h_tc_table3",
            Decimal("1246"),
            Decimal("6"),
            "row=Y-H",
            "column=Tc (K)",
        ),
        (
            "costa_2025_gd_l_tm_table3",
            Decimal("1457"),
            Decimal("7"),
            "row=Gd-L",
            "column=Tm (K)",
        ),
        (
            "costa_2025_gd_h_heat_of_fusion_table3",
            Decimal("16.36"),
            Decimal("0.82"),
            "row=Gd-H",
            "column=ΔHfusion",
        ),
    )
    for observation_id, value, uncertainty, row_label, column_label in render_pins:
        row = _one(rows, observation_id)
        assert row.value.point == value
        assert row.uncertainty.kind is UncertaintyKind.PRINTED
        assert isinstance(row.uncertainty.verbatim, dict)
        assert Decimal(str(row.uncertainty.verbatim["value"])) == uncertainty
        assert row.locator.page == 11
        assert row.locator.pdf_page_index == 10
        assert row_label in row.locator.note
        assert column_label in row.locator.note
