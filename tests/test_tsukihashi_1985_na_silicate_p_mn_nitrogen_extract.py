from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import pytest
import yaml

from simulator.battery.enums import EvidenceClass, PhaseRole, Quantity
from simulator.battery.migrate import Migrator, REPO_ROOT


SOURCE_ID = "tsukihashi-1985-na-silicate-mn-p-nitrogen"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Independent transcription from the 250-dpi PDF renders: Table 1 on physical
# PDF page 2, and the appendix interaction table on physical PDF page 8.
TABLE1_RENDER_ROWS = [
    {
        "FeO_wt_pct": "0.64",
        "Fe2O3_wt_pct": "0.03",
        "Na2O_wt_pct": "62.41",
        "Na2CO3_wt_pct": "3.67",
        "V2O5_wt_pct": "3.80",
        "SiO2_wt_pct": "15.6",
        "P2O5_wt_pct": "12.2",
        "MnO_wt_pct": "1.65",
        "P_distribution_ratio": "not_printed",
        "Mn_distribution_ratio": "not_printed",
        "V_distribution_ratio": "not_printed",
    },
    {
        "FeO_wt_pct": "0.77",
        "Fe2O3_wt_pct": "0.02",
        "Na2O_wt_pct": "61.80",
        "Na2CO3_wt_pct": "not_printed",
        "V2O5_wt_pct": "3.70",
        "SiO2_wt_pct": "19.00",
        "P2O5_wt_pct": "13.05",
        "MnO_wt_pct": "1.66",
        "P_distribution_ratio": "105",
        "Mn_distribution_ratio": "7.62",
        "V_distribution_ratio": "32.6",
    },
]
APPENDIX_RENDER_ROWS = [
    {
        "gamma0_1600C_as_printed": "0.08 (ref. 3)",
        "epsilon_C_X_dilute_as_printed": "-8.0 (ref. 2)",
        "epsilon_C_X_at_aC_as_printed": "-6.1 (ref. 2)",
        "epsilon_X_C_at_Csatd_as_printed": "-24.3",
        "epsilon_X_C_average_as_printed": "-16.2",
        "gamma_X_1600C_as_printed": "2.66 x 10^-3",
        "gamma_X_1200C_as_printed": "5.33 x 10^-4",
    },
    {
        "gamma0_1600C_as_printed": "1.3 (ref. 3)",
        "epsilon_C_X_dilute_as_printed": "-3.45 (ref. 3)",
        "epsilon_C_X_at_aC_as_printed": "-0.47 (ref. 4)",
        "epsilon_X_C_at_Csatd_as_printed": "-4.03",
        "epsilon_X_C_average_as_printed": "-3.74",
        "gamma_X_1600C_as_printed": "0.592",
        "gamma_X_1200C_as_printed": "0.514",
    },
    {
        "gamma0_1600C_as_printed": "1.0 (ref. 3)",
        "epsilon_C_X_dilute_as_printed": "-23 (ref. 2)",
        "epsilon_C_X_at_aC_as_printed": "-11.5 (ref. 2)",
        "epsilon_X_C_at_Csatd_as_printed": "-43.8",
        "epsilon_X_C_average_as_printed": "-33.4",
        "gamma_X_1600C_as_printed": "8.99 x 10^-4",
        "gamma_X_1200C_as_printed": "1.34 x 10^-4",
    },
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_grid(document: dict) -> None:
    row = next(
        item
        for item in document["species"]["P"]["context"]
        if item["observation_id"] == "tsukihashi_1985_table1_complete"
    )
    rows = row["values"]["rows"]
    assert row["locator"]["page"] == 2
    assert row["locator"]["table"] == "1"
    assert row["values"]["row_count_as_printed"] == len(rows) == 2
    assert [{key: value for key, value in item.items() if key != "row"} for item in rows] == TABLE1_RENDER_ROWS

    appendix = next(
        item
        for item in document["species"]["Mn"]["context"]
        if item["observation_id"] == "tsukihashi_1985_appendix_interaction_parameters_table"
    )
    assert appendix["locator"]["page"] == 8
    assert appendix["values"]["row_count_as_printed"] == len(appendix["values"]["rows"]) == 3
    assert [
        {key: value for key, value in item.items() if key != "element"}
        for item in appendix["values"]["rows"]
    ] == APPENDIX_RENDER_ROWS


def test_render_pins_full_printed_tables_and_catches_mutations() -> None:
    document = _document()
    _assert_render_grid(document)

    changed_table = deepcopy(document)
    changed_table["species"]["P"]["context"][0]["values"]["rows"][1]["P_distribution_ratio"] = "106"
    with pytest.raises(AssertionError):
        _assert_render_grid(changed_table)

    changed_appendix = deepcopy(document)
    appendix = next(
        item
        for item in changed_appendix["species"]["Mn"]["context"]
        if item["observation_id"] == "tsukihashi_1985_appendix_interaction_parameters_table"
    )
    appendix["values"]["rows"][1]["gamma_X_1200C_as_printed"] = "0.515"
    with pytest.raises(AssertionError):
        _assert_render_grid(changed_appendix)


def test_isolated_migration_preserves_table1_identity_axes_and_values() -> None:
    document = _document()
    migrator = Migrator(
        root=REPO_ROOT,
        index={SOURCE_ID: document["source"]},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.validation.issues) == 2
    assert all("required axis fO2_Pa is unknown" in issue.detail for issue in result.validation.issues)
    assert len(result.observations) == 2
    expected = {
        "tsukihashi_1985_table1_p_distribution": ("P", Decimal("105")),
        "tsukihashi_1985_table1_mn_distribution": ("Mn", Decimal("7.62")),
    }
    for local_id, (formula, ratio) in expected.items():
        row = result.observations[f"{SOURCE_ID}::{local_id}"]
        assert row.identity.quantity.value is Quantity.DISTRIBUTION_RATIO
        assert row.identity.species.formula == formula
        assert row.value.point == ratio
        assert row.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert row.evidence.original_method_class == "measured_direct"
        assert row.identity.temperature_K.value == Decimal("1473.15")
        assert row.identity.phase_role.value is PhaseRole.SLAG
        assert row.identity.counter_phase_role.value is PhaseRole.METAL
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert not row.identity.fO2_Pa.is_value
        assert row.identity.composition.is_value
        assert row.locator.page == 2
        assert row.locator.table == "1"

    p_row = result.observations[f"{SOURCE_ID}::tsukihashi_1985_table1_p_distribution"]
    mn_row = result.observations[f"{SOURCE_ID}::tsukihashi_1985_table1_mn_distribution"]
    source_p = document["species"]["P"]["observations"][0]
    assert "temperature_K" not in source_p["values"]["point_conditions"]
    assert "total_pressure_Pa" not in source_p["values"]
    assert "oxygen_partial_pressure" not in source_p["values"]
    assert p_row.point_conditions["composition"].locator.page == 2
    assert p_row.point_conditions["composition"].locator.table == "1"
    assert p_row.point_conditions["temperature_C"].state.value == Decimal("1200")
    assert p_row.point_conditions["temperature_C"].locator.page == 2
    assert p_row.point_conditions["total_pressure_atm"].state.value == Decimal("1")
    assert p_row.point_conditions["total_pressure_atm"].locator.page == 3
    assert mn_row.point_conditions["temperature_C"].locator.page == 5
    assert mn_row.point_conditions["temperature_C"].locator.figure == "6"
    assert mn_row.point_conditions["total_pressure_atm"].locator.page == 3
    assert "pO2_Pa" not in p_row.point_conditions
    assert "pO2_Pa" not in mn_row.point_conditions

    # The independently printed nitrogen assay remains context because its
    # complete run composition is absent; Table 1 composition is not reused.
    cn_context = document["species"]["CN"]["context"][0]
    assert document["species"]["CN"]["observations"] == []
    assert cn_context["values"]["component_amount_as_published"] == "2.1"
    assert cn_context["values"]["admission_status"] == "typed_refusal"
    assert "complete slag composition" in cn_context["values"]["reason"]
    assert "does not state PCO" in cn_context["values"]["printed_conditions"]
