from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/dikov-2009-luna16-condensate-iron.yaml"


def _migrate():
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_render_pinned_rows_and_migrated_fe_valence_values():
    """Independent page-render pins guard the printed row counts and values."""
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sample_table = next(
        row
        for row in document["species"]["L1639"]["observations"]
        if row["observation_id"] == "dikov_2009_table1_l1639_elemental_at_percent"
    )
    valence_table = next(
        row
        for row in document["species"]["L1639"]["observations"]
        if row["observation_id"] == "dikov_2009_table2_l1639_valence_composition_at_percent"
    )
    augite_table = next(
        row
        for row in document["species"]["augite_condensate"]["observations"]
        if row["observation_id"] == "dikov_2009_table3_augite_condensate_composition"
    )
    comparisons = next(
        row
        for row in document["species"]["L1639"]["context"]
        if row["observation_id"] == "dikov_2009_prior_vaporization_comparisons"
    )

    # Rendered PDF page 2, Table 1: 9 rows; 200 Å row has Bi=0.01 and Total=100.01.
    assert len(sample_table["values"]["printed_rows"]) == 9
    assert sample_table["values"]["printed_rows"][3][12:14] == ["0.01", "100.01"]
    # Rendered PDF page 3, Table 2: 9 rows; the 400 Å Fe0/SumFe cell is 0.43.
    assert len(valence_table["values"]["printed_rows"]) == 9
    assert valence_table["values"]["printed_rows"][4][15] == "0.43"
    # Rendered PDF page 6, Table 3: 12 rows; 4100 Å has Si0=3.5 and Fe3+=1.7.
    assert len(augite_table["values"]["printed_rows"]) == 12
    assert augite_table["values"]["printed_rows"][11][1:4] == ["3.5", None, "17.2"]
    assert augite_table["values"]["printed_rows"][11][10] == "1.7"
    # Rendered PDF page 7, discussion: retain the printed ± glyph and attribution.
    assert comparisons["values"]["method_class"] == "quoted_attributed"
    assert comparisons["values"]["comparisons"][0]["attribution"] == "Brewer and Rosenblatt (1969)"
    assert "P(FeO)/P(Fe) ± 0.2" in comparisons["values"]["comparisons"][0]["statement_as_printed"]
    assert "approximately two orders of magnitude" in comparisons["values"]["comparisons"][1]["statement_as_printed"]

    result = _migrate()
    rows = tuple(result.observations.values())
    ratio_rows = tuple(
        row for row in rows if "dikov_2009_table2_fe0_fraction" in row.observation_id
    )
    assert len(ratio_rows) == 9
    ratio = next(row for row in ratio_rows if row.value.point == Decimal("0.21"))
    assert ratio.identity.quantity.value is Quantity.VALENCE_RATIO
    assert ratio.identity.valence_basis.value.numerator_oxidation_states == (0,)
    assert ratio.identity.species.phase.value is Phase.CR
    assert ratio.value.point == Decimal("0.21")
    depth_rows = {
        str(row.point_conditions["depth_A"].state.value): row for row in ratio_rows
    }
    assert depth_rows["Surface"].value.point == Decimal("0.21")
    assert depth_rows["700"].value.point == Decimal("0.21")
    assert depth_rows["100"].value.point == Decimal("0.46")
    assert depth_rows["Surface"].point_conditions["depth_A"].locator.table == "2"
    assert depth_rows["700"].point_conditions["depth_A"].locator.table == "2"
    assert depth_rows["100"].point_conditions["depth_A"].locator.table == "2"
    assert depth_rows["100"].point_conditions["depth_A"].locator.page == 3
    assert depth_rows["100"].identity.composition.is_value
    assert depth_rows["100"].identity.composition.value.as_map()["Fe"] == Decimal("6.7")
    assert all(row.identity.composition.is_value for row in ratio_rows)
    assert all(row.point_conditions["temperature_K"].state.is_unknown for row in ratio_rows)
    assert all(row.point_conditions["fO2_Pa"].state.is_unknown for row in ratio_rows)
    assert all("total_pressure_Pa" not in row.point_conditions for row in ratio_rows)
    assert depth_rows["100"].identity.temperature_K.is_unknown
    assert depth_rows["100"].identity.fO2_Pa.is_unknown
    assert depth_rows["100"].identity.total_pressure_Pa.is_unknown

    # The printed upper-zone percentages are canonical percent units; migration converts to fractions.
    upper_layer_rows = {
        token: next(
            row
            for row in rows
            if f"dikov_2009_upper_layer_{token}_fraction" in row.observation_id
        )
        for token in ("fe0", "fe2", "fe3")
    }
    assert {token: row.value.point for token, row in upper_layer_rows.items()} == {
        "fe0": Decimal("0.33"), "fe2": Decimal("0.45"), "fe3": Decimal("0.22")
    }
    assert all(
        upper_layer_rows[token].identity.valence_basis.value
        .numerator_oxidation_states
        == oxidation_state
        for token, oxidation_state in (("fe0", (0,)), ("fe2", (2,)), ("fe3", (3,)))
    )

    augite_fraction_rows = {
        observation_id: next(
            row for row in rows if observation_id in row.observation_id
        )
        for observation_id in (
            "dikov_2009_augite_condensate_fe0_fraction",
            "dikov_2009_augite_condensate_fe2_fraction",
            "dikov_2009_augite_condensate_fe3_fraction",
            "dikov_2009_initial_augite_fe0_fraction",
            "dikov_2009_initial_augite_fe2_fraction",
            "dikov_2009_initial_augite_fe3_fraction",
        )
    }
    # Rendered PDF page 5, Experimental prose: percent of total iron content.
    printed_augite_fractions = {
        "dikov_2009_augite_condensate_fe0_fraction": "7.8",
        "dikov_2009_augite_condensate_fe2_fraction": "46.6",
        "dikov_2009_augite_condensate_fe3_fraction": "45.6",
        "dikov_2009_initial_augite_fe0_fraction": "0.0",
        "dikov_2009_initial_augite_fe2_fraction": "84.0",
        "dikov_2009_initial_augite_fe3_fraction": "16.0",
    }
    raw_augite_fractions = {
        row["observation_id"]: row["values"]["points"][0]["value"]
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] in printed_augite_fractions
    }
    assert raw_augite_fractions == printed_augite_fractions
    migrated_augite_fractions = {
        observation_id: row.value.point
        for observation_id, row in augite_fraction_rows.items()
    }
    assert migrated_augite_fractions == {
        "dikov_2009_augite_condensate_fe0_fraction": Decimal("0.078"),
        "dikov_2009_augite_condensate_fe2_fraction": Decimal("0.466"),
        "dikov_2009_augite_condensate_fe3_fraction": Decimal("0.456"),
        "dikov_2009_initial_augite_fe0_fraction": Decimal("0"),
        "dikov_2009_initial_augite_fe2_fraction": Decimal("0.840"),
        "dikov_2009_initial_augite_fe3_fraction": Decimal("0.160"),
    }
    assert all(
        row.identity.quantity.value is Quantity.VALENCE_RATIO
        and row.identity.species.phase.value is Phase.CR
        for row in augite_fraction_rows.values()
    )
    condensate_fe0 = augite_fraction_rows["dikov_2009_augite_condensate_fe0_fraction"]
    assert condensate_fe0.identity.total_pressure_Pa.is_value
    assert condensate_fe0.identity.total_pressure_Pa.value == Decimal("101325")
    assert condensate_fe0.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
    assert condensate_fe0.identity.composition.is_unknown
    assert condensate_fe0.identity.temperature_K.is_unknown
    assert condensate_fe0.identity.fO2_Pa.is_unknown
    initial_fe0 = augite_fraction_rows["dikov_2009_initial_augite_fe0_fraction"]
    assert initial_fe0.identity.composition.is_value
    assert initial_fe0.identity.composition.value.as_map()["Fe2+"] == Decimal("2.1")
    assert initial_fe0.identity.composition.value.as_map()["Fe3+"] == Decimal("0.4")
    assert initial_fe0.identity.temperature_K.is_unknown
    assert initial_fe0.identity.fO2_Pa.is_unknown
    assert initial_fe0.identity.total_pressure_Pa.is_unknown
    assert len(rows) == 21
    assert sum(row.value.kind.value == "unavailable" for row in rows) == 3

    # The only quantity-unknown observations are the three documented exact schema gaps.
    unknown_quantities = {
        row.observation_id.rsplit("::", 1)[-1]
        for row in result.observations.values()
        if row.identity.quantity.is_unknown
    }
    assert unknown_quantities == {
        "dikov_2009_table1_l1639_elemental_at_percent",
        "dikov_2009_table2_l1639_valence_composition_at_percent",
        "dikov_2009_table3_augite_condensate_composition",
    }
    assert all(row.identity.species.phase.is_value for row in result.observations.values())
    augite_refusal = next(
        row
        for row in rows
        if "dikov_2009_table3_augite_condensate_composition" in row.observation_id
    )
    assert augite_refusal.point_conditions["temperature_range_as_published"].state.is_value
    assert augite_refusal.point_conditions["atmosphere"].state.value == "He at 1 atm"
    assert augite_refusal.point_conditions["duration_s"].state.is_value

    # The paper gives no lunar sample T, fO2, or sample total pressure. Do not infer them from context.
    assert ratio.identity.temperature_K.is_unknown
    assert ratio.identity.fO2_Pa.is_unknown
    assert ratio.identity.total_pressure_Pa.is_unknown
    assert ratio.point_conditions["depth_A"].state.is_value
    assert result.validation.hard_issues == ()
