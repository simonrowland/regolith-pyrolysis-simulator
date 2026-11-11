"""Render pins and isolated migration for Ban-ya et al. (1985)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/banya-hino-takezoe-1985-na-fe-silicate-redox.yaml"
)

# Independent transcription from the page 2 render. Tuples are the printed
# run number, three oxide wt% values, and Fe3+/Fe2+ ratio in Table 1.
TABLE_1 = (
    ("1", "11.31", "70.20", "17.20", "5.585"),
    ("2", "15.80", "67.86", "14.12", "3.865"),
    ("3", "23.81", "65.25", "7.74", "2.466"),
    ("4", "27.41", "64.27", "5.63", "2.110"),
    ("5", "31.07", "62.72", "3.48", "1.816"),
    ("6", "35.82", "60.91", "2.42", "1.530"),
    ("7", "41.21", "58.51", "0.20", "1.278"),
)

# Independent transcription from the page 2 render. Tuples are the printed
# run number, P_H2O/P_H2, FeO, Fe2O3, SiO2, Na2O (wt%), Fe3+/Fe2+, and a_FetO.
TABLE_2 = (
    ("1", "0.395", "40.25", "1.95", "44.95", "13.14", "0.044", "0.452"),
    ("2", "0.394", "48.44", "3.53", "40.78", "5.89", "0.066", "0.450"),
    ("3", "0.393", "28.14", "3.61", "42.58", "25.06", "0.115", "0.449"),
    ("24", "0.394", "29.10", "3.87", "41.93", "24.82", "0.120", "0.450"),
    ("25", "0.392", "29.79", "2.94", "42.84", "24.00", "0.089", "0.448"),
    ("5", "0.482", "52.04", "3.74", "37.20", "5.67", "0.065", "0.551"),
    ("6", "0.482", "29.67", "5.01", "39.93", "25.15", "0.152", "0.550"),
    ("7", "0.482", "43.44", "0.68", "41.71", "14.13", "0.014", "0.551"),
    ("8", "0.481", "28.02", "4.93", "41.62", "24.35", "0.158", "0.550"),
    ("18", "0.482", "55.72", "3.68", "36.38", "4.50", "0.059", "0.551"),
    ("9", "0.571", "37.55", "4.91", "37.17", "20.63", "0.118", "0.653"),
    ("10", "0.572", "57.51", "3.04", "32.82", "5.19", "0.048", "0.654"),
    ("11", "0.569", "30.42", "6.97", "36.19", "27.55", "0.206", "0.650"),
    ("12", "0.568", "49.94", "2.87", "35.68", "11.03", "0.052", "0.649"),
    ("17", "0.566", "61.75", "3.55", "30.59", "2.61", "0.052", "0.647"),
    ("20", "0.568", "39.91", "4.30", "36.65", "18.54", "0.097", "0.648"),
    ("13", "0.615", "52.97", "6.60", "31.74", "7.28", "0.112", "0.702"),
    ("15", "0.611", "62.85", "4.74", "28.70", "2.89", "0.068", "0.698"),
    ("16", "0.610", "46.72", "5.62", "33.98", "12.82", "0.108", "0.696"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows_for_table(document: dict, species: str, table: str) -> list[dict]:
    return [
        row
        for row in document["species"][species]["observations"]
        if row["locator"]["table"] == table
    ]


def _assert_table_render_pins(document: dict) -> None:
    table1 = _rows_for_table(document, "Fe", "1")
    table2_redox = _rows_for_table(document, "Fe", "2")
    table2_activity = _rows_for_table(document, "Fe_tO", "2")
    assert len(table1) == len(TABLE_1) == 7, "Table 1 render contains 7 runs"
    assert len(table2_redox) == len(TABLE_2) == 19, "Table 2 render contains 19 runs"
    assert len(table2_activity) == len(TABLE_2) == 19

    for row, expected in zip(table1, TABLE_1, strict=True):
        run, feo, fe2o3, na2o, ratio = expected
        values = row["values"]
        assert values["run_number_as_printed"] == run
        assert values["FeO_wt_percent_as_printed"] == feo
        assert values["Fe2O3_wt_percent_as_printed"] == fe2o3
        assert values["Na2O_wt_percent_as_printed"] == na2o
        assert values["Fe3_over_Fe2_as_printed"] == ratio
        assert values["value"] == ratio
        assert values["Fe3_over_Fe2_as_printed"] == values["value"]
        assert row["locator"]["page"] == 2
        assert row["locator"]["table"] == "1"

    for redox, activity, expected in zip(
        table2_redox, table2_activity, TABLE_2, strict=True
    ):
        run, gas_ratio, feo, fe2o3, sio2, na2o, ratio, a_feto = expected
        values = redox["values"]
        activity_values = activity["values"]
        assert values["run_number_as_printed"] == run
        assert values["P_H2O_over_PH2_as_printed"] == gas_ratio
        assert values["FeO_wt_percent_as_printed"] == feo
        assert values["Fe2O3_wt_percent_as_printed"] == fe2o3
        assert values["SiO2_wt_percent_as_printed"] == sio2
        assert values["Na2O_wt_percent_as_printed"] == na2o
        assert values["Fe3_over_Fe2_as_printed"] == ratio
        assert values["value"] == ratio
        assert values["Fe3_over_Fe2_as_printed"] == values["value"]
        assert activity_values["P_H2O_over_PH2_as_printed"] == gas_ratio
        assert activity_values["a_FetO_as_printed"] == a_feto
        assert activity_values["activity"] == a_feto
        assert activity_values["a_FetO_as_printed"] == activity_values["activity"]
        assert activity_values["method_class"] == "measured_reduced"
        assert activity["derived_from"] == [redox["observation_id"]]
        assert activity["derivation"]["inputs"] == [redox["observation_id"]]
        assert activity["derivation"]["method"]["locator"]["equation"] == "2"
        assert redox["locator"]["page"] == activity["locator"]["page"] == 2
        assert redox["locator"]["table"] == activity["locator"]["table"] == "2"


def _assert_na2o_bound(document: dict) -> None:
    rows = document["species"]["Na2O"]["observations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["observation_id"] == "banya_hino_takezoe_1985_fig11_na2o_activity_upper_bound"
    assert row["type"] == "activity_coefficient"
    assert row["locator"] == {
        "page": 7,
        "pdf_page_index": 6,
        "published_page": 1771,
        "section": "4.4.4",
        "figure": "11",
        "note": "The prose gives the bound for the displayed Figure 11 composition range; no point was read from a contour.",
    }
    assert row["phase"] == "l"
    assert row["standard_state"]["state"]["value"]["convention"] == "raoultian_pure_endmember"
    assert row["standard_state"]["state"]["value"]["endmember"]["formula"] == "Na2O"
    assert row["standard_state"]["state"]["value"]["endmember"]["phase"]["value"] == "l"
    assert row["standard_state"]["inferred"] is False
    assert row["point_conditions"]["T_C"]["state"]["value"]["point"] == "1400"
    assert row["point_conditions"]["composition"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }
    assert row["point_conditions"]["iron_saturation"]["state"]["value"] == (
        "slag in equilibrium with liquid iron"
    )

    values = row["values"]
    assert values["quantity"] == "activity"
    assert values["method_class"] == "model_derived"
    assert values["activity_upper_bound_as_printed"] == "10^-5.5"
    assert values["inequality_as_printed"] == "≤"
    assert values["activity"] == {
        "kind": "bound",
        "bound_operator": "<=",
        "bound_value": "3.162277660168379331998893544432719e-6",
        "approximate": True,
    }
    assert values["standard_state_as_printed"] == "Na2O(l)"
    assert values["melt_system_as_printed"] == (
        "FetO-SiO2-Na2O slags in equilibrium with liquid iron"
    )
    assert values["composition_domain_as_printed"] == (
        "Displayed composition range in Figure 11; dotted line marks where the regular-solution model applies."
    )
    assert values["T_C_as_printed"] == "1400"
    assert row["derivation"]["method"]["state"]["value"] == (
        "Regular-solution relations from Eqs. (24), (26), and (33)."
    )
    assert row["derivation"]["references"] == [
        "Reference [36]: D. N. Rego, G. K. Sigworth and W. O. Philbrook, Proc. of Japan-United States Seminer, Kyoto (1983), p. 87 [ISIJ]."
    ]
    assert row["derivation"]["raw_inputs"]["state"]["value"] == "not_printed"


def test_all_printed_rows_match_render_pins_and_fidelity_samples() -> None:
    document = _document()
    assert document["review_status"] == "draft"
    assert check_all_fidelity_samples_match(document) == []
    _assert_table_render_pins(document)
    _assert_na2o_bound(document)

    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][8]["values"][
        "Fe3_over_Fe2_as_printed"
    ] = "0.088"
    with pytest.raises(AssertionError):
        _assert_table_render_pins(mutated)

    mutated_ratio = deepcopy(document)
    mutated_ratio["species"]["Fe"]["observations"][0]["values"]["value"] = "9.999"
    with pytest.raises(AssertionError):
        _assert_table_render_pins(mutated_ratio)

    mutated_activity = deepcopy(document)
    mutated_activity["species"]["Fe_tO"]["observations"][0]["values"][
        "activity"
    ] = "0.999"
    with pytest.raises(AssertionError):
        _assert_table_render_pins(mutated_activity)

    mutated_class = deepcopy(document)
    mutated_class["species"]["Fe_tO"]["observations"][0]["values"][
        "method_class"
    ] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_table_render_pins(mutated_class)

    mutated_bound = deepcopy(document)
    mutated_bound["species"]["Na2O"]["observations"][0]["values"][
        "activity_upper_bound_as_printed"
    ] = "10^-5.4"
    with pytest.raises(AssertionError):
        _assert_na2o_bound(mutated_bound)

    _assert_table_render_pins(document)
    _assert_na2o_bound(document)

    table3 = next(
        row
        for row in document["species"]["Fe_tO"]["context"]
        if row["observation_id"]
        == "banya_hino_takezoe_1985_table3_standard_free_energy_expressions"
    )
    assert len(table3["values"]["rows"]) == 11
    assert len(
        document["species"]["Fe_tO"]["context"][1]["values"][
            "activity_coefficient_expressions_as_printed"
        ]
    ) == 4

def test_isolated_migration_preserves_phase_conditions_and_identity() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    observations = list(result.observations.values())
    unavailable = sum(o.value.kind is ValueKind.UNAVAILABLE for o in observations)
    print(
        "isolated migration:",
        f"obs={len(observations)}",
        f"hard={len(result.validation.hard_issues)}",
        f"unavailable={unavailable}",
    )
    assert len(observations) == 46
    assert unavailable == 0

    redox = [o for o in observations if o.identity.quantity.value is Quantity.VALENCE_RATIO]
    activity = [o for o in observations if o.identity.quantity.value is Quantity.ACTIVITY]
    assert len(redox) == 26
    assert len(activity) == 20
    assert not [
        o
        for o in observations
        if not o.identity.quantity.is_value or not o.identity.species.phase.is_value
    ]

    for observation in observations:
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.is_value
        assert observation.identity.composition is not None
        assert observation.point_conditions["T_C"].state.is_value
        assert "total_pressure_Pa" not in observation.point_conditions
        assert "fO2_Pa" not in observation.point_conditions
        assert observation.identity.total_pressure_Pa is None or not (
            observation.identity.total_pressure_Pa.is_value
        )
        assert observation.identity.fO2_Pa is None or not observation.identity.fO2_Pa.is_value
        if observation.value.kind is ValueKind.BOUND:
            assert not observation.identity.composition.is_value
            assert not observation.point_conditions["composition"].state.is_value
            assert observation.point_conditions["iron_saturation"].state.is_value
        else:
            assert observation.value.kind is ValueKind.POINT
            assert observation.identity.composition.is_value
            assert observation.point_conditions["composition"].state.is_value
            assert observation.point_conditions["gas_mix"].state.is_value

    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    migrated_by_id = {
        observation.observation_id.split("::", maxsplit=1)[-1]: observation
        for observation in observations
    }
    assert set(migrated_by_id) == set(source_rows)
    for observation_id, row in source_rows.items():
        observation = migrated_by_id[observation_id]
        expected = row["values"].get("value", row["values"].get("activity"))
        if observation.value.kind is ValueKind.BOUND:
            assert observation_id == "banya_hino_takezoe_1985_fig11_na2o_activity_upper_bound"
            assert observation.value.bound_operator == "<="
            assert str(observation.value.bound_value) == (
                "0.000003162277660168379331998893544432719"
            )
            assert observation.value.approximate
            assert row["values"]["activity_upper_bound_as_printed"] == "10^-5.5"
            assert observation.point_conditions["T_C"].locator.page == 7
            continue
        assert str(observation.value.point) == expected
        assert observation.point_conditions["T_C"].locator.page == 2
        assert observation.point_conditions["composition"].locator.page == 2
        assert observation.point_conditions["gas_mix"].locator.page in (1, 2)
        if row["locator"]["table"] == "2":
            gas_mix = str(observation.point_conditions["gas_mix"].state.value)
            assert row["values"]["P_H2O_over_PH2_as_printed"] in gas_mix

    for observation in redox:
        assert observation.identity.species.formula == "Fe"
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_TABULATED
        assert observation.point_conditions["quench_state"].state.value == "not_stated"
    activity_points = [o for o in activity if o.value.kind is ValueKind.POINT]
    assert len(activity_points) == 19
    for observation in activity_points:
        assert observation.identity.species.formula == "Fe_tO"
        assert observation.identity.species.phase.value is Phase.L
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED

    na2o_bound = migrated_by_id["banya_hino_takezoe_1985_fig11_na2o_activity_upper_bound"]
    assert na2o_bound.identity.species.formula == "Na2O"
    assert na2o_bound.identity.species.phase.value is Phase.L
    assert na2o_bound.evidence.class_.value is EvidenceClass.MODEL_DERIVED
    assert na2o_bound.value.kind is ValueKind.BOUND
    assert na2o_bound.value.bound_operator == "<="

    for observation in (
        redox[0],
        next(o for o in redox if "t2_run_1_" in o.observation_id),
        activity[0],
    ):
        required = profile_for(observation.identity).required
        print(
            observation.observation_id,
            "required=",
            sorted(required),
            "temperature_K=",
            observation.identity.temperature_K,
            "composition=",
            observation.identity.composition,
            "fO2_Pa=",
            observation.identity.fO2_Pa,
            "total_pressure_Pa=",
            observation.identity.total_pressure_Pa,
        )
        assert {"composition", "temperature_K"} <= required
