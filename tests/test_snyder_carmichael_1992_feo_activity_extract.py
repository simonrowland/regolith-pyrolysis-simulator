"""Source pins and migration checks for Snyder & Carmichael (1992) FeO data."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    wt_pct_to_mole_fraction,
)
from simulator.battery.oxygen_identity import log10_fo2_to_pa


SID = "snyder-carmichael-1992-olivine-liquid-feo-activity"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
FIT_ID = "snyder_1992_fe0_947o_ln_activity_multivariate_fit"
OXIDES = (
    "SiO2", "TiO2", "Al2O3", "Fe2O3", "FeO", "MnO", "NiO", "MgO",
    "CaO", "Na2O", "K2O",
)


# Run id, T(K), log10 fO2, duration (h), products, Ni(wt%), Fe(wt%).
# None marks a blank alloy cell in the printed table.
TABLE2 = (
    ("08-58", "1395", "-11.15", "332.3", "gl+lc+pl+ol", "89.5", "10.7"),
    ("44-51", "1562", "-7.40", "116.8", "gl+ol", None, None),
    ("44-57", "1461", "-10.29", "186.9", "gl+ol+pl", "93.7", "6.26"),
    ("44-60", "1468", "-9.83", "307.5", "gl+ol+pl+ox", "94.8", "4.61"),
    ("44-64", "1503", "-7.97", "265.7", "gl+ol", None, None),
    ("46-57", "1461", "-10.29", "186.9", "gl+ol", "94.2", "6.16"),
    ("46-60", "1468", "-9.83", "307.5", "gl+ol", "95.6", "5.06"),
    ("48-48", "1562", "-11.39", "96.7", "gl+ol", "73.0", "27.7"),
    ("48-56", "1559", "-10.36", "192.5", "gl+ol", "89.3", "10.7"),
    ("48-57", "1461", "-10.29", "186.9", "gl+ol+ox", "94.7", "5.71"),
    ("48-59", "1557", "-0.68", "235.8", "gl+ol¹", None, None),
    ("48-60", "1468", "-9.83", "307.5", "gl+ol+ox", "96.0", "4.30"),
    ("48-61A", "1559", "-9.11", "143.0", "gl+ol", "94.9", "4.61"),
    ("48-64", "1503", "-7.97", "265.7", "gl+ol", None, None),
    ("50-56", "1559", "-10.36", "192.5", "gl+ol", "85.1", "14.8"),
    ("50-59", "1557", "-0.68", "235.8", "gl+ol+ox", None, None),
    ("50-61A", "1559", "-9.11", "143.0", "gl+ol", "94.2", "5.61"),
    ("77-52", "1504", "-9.80", "91.2", "gl+ol", "93.5", "5.94"),
    ("77-64", "1503", "-7.97", "265.7", "gl+ol", None, None),
    ("83-58", "1395", "-11.15", "332.3", "gl+ol+pl+ilm", "87.8", "11.6"),
    ("83-66", "1395", "-9.28", "309.1", "gl+ol+pl+tmt", None, None),
)


# Run, a_Fe0.947O, gamma_Fe0.947O, Table 3 n, and its 11 exact printed glass
# values. Parentheses are the paper's printed standard deviations.
TABLE4 = (
    ("08-58", "0.53", "4.9", "10", ("48.1(4)", "1.58(5)", "15.5(2)", "0.83", "11.7(1)", "0.202(15)", "0.172(13)", "3.72(10)", "9.89(13)", "1.95(6)", "4.46(5)")),
    ("44-57", "0.27", "4.4", "10", ("54.6(7)", "1.01(4)", "16.7(1)", "0.43", "7.03(7)", "0.134(13)", "0.265(22)", "6.06(16)", "8.86(11)", "2.92(10)", "1.23(3)")),
    ("44-60", "0.30", "5.1", "10", ("54.2(5)", "0.97(4)", "16.4(2)", "0.50", "6.73(7)", "0.135(14)", "0.375(15)", "6.27(14)", "9.07(19)", "2.52(11)", "1.28(2)")),
    ("46-57", "0.26", "4.1", "10", ("52.9(5)", "1.50(5)", "16.0(1)", "0.47", "7.21(8)", "0.120(10)", "0.352(20)", "5.94(11)", "9.03(17)", "2.56(7)", "2.27(3)")),
    ("46-60", "0.33", "5.3", "10", ("53.7(4)", "1.47(3)", "15.5(1)", "0.57", "7.03(17)", "0.113(7)", "0.427(13)", "5.96(13)", "8.99(18)", "2.47(7)", "2.24(3)")),
    ("48-48", "0.13", "1.6", "10", ("52.1(5)", "1.86(3)", "13.3(1)", "0.16", "9.25(7)", "0.125(6)", "0.042(8)", "11.8(2)", "8.68(15)", "0.39(4)", "1.38(2)")),
    ("48-56", "0.13", "2.2", "15", ("54.3(6)", "1.93(5)", "13.3(1)", "0.21", "7.18(12)", "0.127(7)", "0.208(26)", "12.6(2)", "8.85(18)", "0.24(3)", "0.91(2)")),
    ("48-57", "0.24", "4.3", "10", ("52.6(5)", "2.17(6)", "15.2(2)", "0.44", "6.43(13)", "0.113(12)", "0.322(31)", "6.33(7)", "9.99(19)", "1.84(8)", "2.87(7)")),
    ("48-60", "0.27", "5.1", "10", ("53.5(6)", "2.20(9)", "14.6(2)", "0.50", "6.13(18)", "0.121(9)", "0.333(21)", "6.63(27)", "10.0(3)", "1.55(5)", "2.57(10)")),
    ("48-61A", "0.21", "3.4", "11", ("52.4(4)", "1.97(6)", "13.7(2)", "0.45", "7.04(6)", "0.126(16)", "0.637(16)", "10.2(2)", "9.02(12)", "0.83(7)", "1.99(4)")),
    ("50-56", "0.19", "2.0", "19", ("45.4(3)", "5.74(7)", "8.56(12)", "0.45", "11.4(1)", "0.210(12)", "0.204(10)", "12.7(2)", "14.4(2)", "0.09(3)", "0.11(1)")),
    ("50-61A", "0.25", "2.7", "12", ("44.1(4)", "5.73(9)", "8.58(11)", "0.85", "11.0(1)", "0.211(13)", "0.935(13)", "11.7(2)", "14.3(1)", "0.20(2)", "0.29(1)")),
    ("77-52", "0.25", "4.5", "10", ("54.0(8)", "1.85(8)", "13.9(2)", "0.50", "6.50(22)", "0.111(12)", "0.326(19)", "7.01(29)", "8.77(43)", "2.06(15)", "4.76(21)")),
    ("83-58", "0.59", "3.5", "10", ("45.7(6)", "3.81(9)", "13.8(2)", "1.05", "18.2(2)", "0.234(16)", "0.262(8)", "3.69(10)", "7.52(9)", "2.80(9)", "1.13(2)")),
)

# Additional Table 3 glass rows without a matching Table 4 FeO activity pair.
# These values are render-checked and retain the complete printed glass series.
OTHER_GLASS = (
    ("44-51", "11", ("51.4(6)", "0.78(4)", "16.4(1)", "1.19", "7.10(15)", "0.122(11)", "1.40(2)", "8.30(7)", "8.39(17)", "3.12(15)", "1.12(1)"), "99.3"),
    ("44-64", "10", ("53.4(4)", "0.85(3)", "17.5(3)", "0.99", "6.21(7)", "0.130(14)", "0.897(14)", "6.69(17)", "9.06(10)", "2.56(13)", "1.18(2)"), "99.5"),
    ("48-59", "10", ("51.3(5)", "1.86(7)", "12.7(1)", "6.91", "1.54(9)", "0.124(11)", "0.510(13)", "10.9(2)", "8.67(16)", "1.87(8)", "1.92(4)"), "98.3"),
    ("48-64", "10", ("52.3(6)", "2.12(7)", "14.6(1)", "1.08", "6.12(7)", "0.118(11)", "0.602(32)", "7.19(17)", "9.70(16)", "1.53(6)", "2.67(4)"), "98.1"),
    ("50-59", "10", ("42.9(3)", "5.42(12)", "7.88(9)", "11.3", "2.39(16)", "0.188(18)", "0.928(12)", "13.0(2)", "13.6(1)", "0.35(2)", "0.11(1)"), "98.1"),
    ("77-64", "10", ("53.8(6)", "1.88(5)", "14.2(1)", "1.07", "5.61(10)", "0.107(12)", "0.715(37)", "6.08(13)", "8.85(23)", "1.58(8)", "4.25(8)"), "98.1"),
    ("83-66", "10", ("48.9(4)", "3.46(7)", "13.1(2)", "2.47", "14.4(2)", "0.242(12)", "0.601(15)", "3.09(9)", "7.90(12)", "3.35(12)", "1.25(4)"), "98.8"),
)

STARTING_MATERIALS = (
    ("08¹", "47.84", "1.24", "17.90", "1.88", "6.21", "0.17", "3.87", "8.68", "2.78", "7.43", "0.92", "0.45", "99.37"),
    ("44²", "52.10", "0.81", "16.44", "1.56", "6.05", "0.14", "9.29", "8.46", "3.47", "0.74", "0.14", "0.34", "99.54"),
    ("46³", "49.21", "1.33", "14.19", "4.54", "3.75", "0.12", "8.32", "7.68", "4.59", "2.82", "1.37", "0.19", "99.11"),
    ("48", "49.23", "1.81", "12.26", "5.39", "2.72", "0.13", "11.85", "8.24", "2.78", "3.58", "1.02", "0.73", "99.74"),
    ("50", "40.52", "5.28", "8.17", "6.33", "6.29", "0.21", "11.57", "12.55", "2.50", "3.75", "0.44", "1.31", "98.92"),
    ("77", "50.76", "1.70", "12.38", "3.94", "3.02", "0.10", "8.89", "7.60", "2.82", "5.83", "1.28", "0.09", "98.41"),
    ("83⁴", "46.2", "3.83", "13.4", "1.59", "15.9", "0.22", "4.66", "7.58", "3.56", "1.03", "0.35", "1.16", "99.49"),
)
STARTING_OXIDES = (
    "SiO2", "TiO2", "Al2O3", "Fe2O3", "FeO", "MnO", "MgO", "CaO",
    "Na2O", "K2O", "P2O5", "H2O+", "Total",
)

STANDARD_STATE_STATEMENTS = (
    "For both reactions, we have used solid oxides as reference states.",
    "Fe0.947O was chosen as a standard state rather than estimated values for stoichiometric FeO because the latter yield ferrous iron activities in the melt which exceed unity (see e.g., DOYLE, 1988).",
    "All activities are relative to a solid standard state at temperature.",
    "Activity coefficients (γ’s) for NiO, FeO, MgO and Fe2O3 are for the liquid phase and that of Lb (= NiSi1/2O2) is for olivine.",
    "Relative to the uncertainty in ln a_Fe0.947O, ±20%, the difference between ln a_FeO and ln a_Fe0.947O is quite small. Consequently, we have taken ln a_FeO ≈ ln a_Fe0.947O in the subsequent calculations.",
)
TABLE3_SUMS = {
    "08-58": "98.1", "44-51": "99.3", "44-57": "99.2", "44-60": "98.5",
    "44-64": "99.5", "46-57": "98.3", "46-60": "98.4", "48-48": "99.0",
    "48-56": "99.9", "48-57": "98.3", "48-59": "98.3", "48-60": "98.1",
    "48-61A": "98.3", "48-64": "98.1", "50-56": "99.3", "50-59": "98.1",
    "50-61A": "98.0", "77-52": "99.8", "77-64": "98.1", "83-58": "98.2",
    "83-66": "98.8",
}


def _document(path: Path = EXTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _observation_map(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["FeO"]["observations"]
    }


def _central(raw: str) -> str:
    return re.sub(r"\([^)]*\)$", "", raw)


def _assert_printed_cells(document: dict) -> None:
    block = document["species"]["FeO"]
    context = block["context"][0]["values"]
    assert len(context["rows"]) == len(TABLE2) == 21
    for expected, actual in zip(TABLE2, context["rows"], strict=True):
        run, temp, fo2, hours, phases, ni, fe = expected
        assert actual["run_as_printed"] == run
        assert actual["temperature_K_as_printed"] == temp
        assert actual["log10_fO2_as_printed"] == fo2
        assert actual["time_hours_as_printed"] == hours
        assert actual["run_products_as_printed"] == phases
        for element, amount in (("Ni", ni), ("Fe", fe)):
            cell = actual["alloy_composition_wt_pct_as_printed"][element]
            if amount is None:
                assert cell == {"tag": "absent", "reason": "Table 2 cell is blank"}
            else:
                assert cell == amount
        assert actual["fO2_pressure_reference_as_printed"]["tag"] == "absent"
        assert actual["locator"]["page"] == 3
        assert actual["locator"]["table"] == "2"
    assert context["temperature_uncertainty_as_printed"] == (
        "Reported temperatures are believed to be accurate to within 3 K."
    )
    assert context["fO2_uncertainty_as_printed"] == (
        "Oxygen fugacity is believed to be accurate to ±0.05 log10 units."
    )
    assert context["run_product_footnotes_as_printed"] == [{
        "text_as_printed": "¹ The olivine in this sample contains inclusions of an Fe-Ti oxide.",
        "locator": {
            "page": 3,
            "published_page": 305,
            "pdf_page_index": 2,
            "table": "2",
            "record": "48-59",
            "note": "Footnote attached to the gl+ol¹ run-products cell",
        },
    }]
    assert context["electromotive_force_as_printed"] == {
        "tag": "absent", "reason": "The paper reports no EMF measurements."
    }

    contexts = {item["observation_id"]: item for item in block["context"]}
    starting = contexts["snyder_1992_table1_starting_material_compositions_context"]
    assert len(starting["values"]["rows"]) == 7
    assert starting["locator"]["page"] == 2 and starting["locator"]["table"] == "1"
    for expected, actual in zip(STARTING_MATERIALS, starting["values"]["rows"], strict=True):
        assert actual["specimen_as_printed"] == expected[0]
        assert tuple(actual["composition_wt_pct_as_printed"][oxide] for oxide in STARTING_OXIDES) == expected[1:]
        assert actual["locator"]["page"] == 2 and actual["locator"]["table"] == "1"
    assert starting["values"]["specimen_footnotes_as_printed"] == [
        "Specimen 94-8 from BALDRIDGE et al. (1981); see also SACK et al. (1987)",
        "Specimen Jor 44 from LUHR and CARMICHAEL (1985)",
        "Specimen Jor 46 from LUHR and CARMICHAEL (1985)",
        "Specimen 4-83C from WIEBE (1988)",
    ]

    glass_context = contexts["snyder_1992_table3_glass_compositions_context"]
    glass_rows = glass_context["values"]["rows"]
    assert glass_context["values"]["printed_analysis_rows"] == 44
    assert glass_context["values"]["glass_analysis_rows"] == len(glass_rows) == 21
    assert glass_context["locator"]["page"] == 4 and glass_context["locator"]["table"] == "3"
    glass_by_run = {row["run_as_printed"][:-3]: row for row in glass_rows}
    assert set(glass_by_run) == {row[0] for row in TABLE2}
    for run, n, composition, total in OTHER_GLASS:
        actual = glass_by_run[run]
        assert actual["point_count_as_printed"] == n
        assert tuple(actual["composition_wt_pct_as_printed"][oxide] for oxide in OXIDES) == composition
        assert actual["sum_as_printed"] == total == TABLE3_SUMS[run]
        assert actual["linked_table4_feo_activity"] is False
        assert actual["locator"]["table"] == "3"
    for run, _activity, _gamma, n, composition in TABLE4:
        actual = glass_by_run[run]
        assert actual["point_count_as_printed"] == n
        assert tuple(actual["composition_wt_pct_as_printed"][oxide] for oxide in OXIDES) == composition
        assert actual["sum_as_printed"] == TABLE3_SUMS[run]
        assert actual["linked_table4_feo_activity"] is True

    reference_context = contexts["snyder_1992_standard_state_statements_context"]["values"]
    assert tuple(row["statement_as_printed"] for row in reference_context["statements"]) == STANDARD_STATE_STATEMENTS
    assert reference_context["equation_2_as_printed"] == "0.947Fe_alloy + 1/2 O2 = Fe0.947O_solid"
    assert reference_context["equation_4_for_liquid_activity_as_printed"] == (
        "a_Fe0.947O^liq = (a_Fe^alloy)^0.947 f_O2^(1/2) exp(-ΔG°_rxn,2 / RT)"
    )
    assert reference_context["table4_activity_error_as_printed"] == (
        "Estimated errors for the values of ln a_NiO and ln a_Fe0.947O are believed to be 4% and 20%, respectively."
    )

    fit_raw = next(row for row in block["observations"] if row["observation_id"] == FIT_ID)["values"]
    assert fit_raw["equation_as_printed"] == "ln a_i^liq = a + b/T + c log10 fO2 + Σ_i d_i ln X_i"
    assert fit_raw["coefficients_as_printed"] == {
        "a": {"value_as_printed": "-1.367", "standard_error_as_printed": "0.592"},
        "b_K": {"value_as_printed": "6176.", "standard_error_as_printed": "742.1"},
        "c": {"value_as_printed": "0.2128", "standard_error_as_printed": "0.0086"},
        "d_TiO2": {"value_as_printed": "-0.05926", "standard_error_as_printed": "0.0212"},
        "d_FeO": {"value_as_printed": "0.7433", "standard_error_as_printed": "0.0299"},
        "d_Na2O+K2O": {"value_as_printed": "0.1337", "standard_error_as_printed": "0.0253"},
        "d_Fe2O3": {
            "value": {"tag": "absent", "reason": "Blank Table 5 coefficient cell"},
            "standard_error": {"tag": "absent", "reason": "Blank Table 5 standard-error cell"},
        },
        "d_MgO": {
            "value": {"tag": "absent", "reason": "Blank Table 5 coefficient cell"},
            "standard_error": {"tag": "absent", "reason": "Blank Table 5 standard-error cell"},
        },
    }
    assert fit_raw["domain_ranges_as_printed"] == {
        "T_K": {"minimum": "1395.", "maximum": "1579."},
        "log10_fO2": {"minimum": "-14.50", "maximum": "-8.18"},
        "XSiO2": {"minimum": "0.4212", "maximum": "0.8328"},
        "XTiO2": {"minimum": "0.0024", "maximum": "0.0784"},
        "XAl2O3": {"minimum": "0.0508", "maximum": "0.1238"},
        "XFe2O3": {"minimum": "0.0003", "maximum": "0.0055"},
        "XFeO": {"minimum": "0.0080", "maximum": "0.2471"},
        "XMgO": {"minimum": "0.0063", "maximum": "0.1910"},
        "XCaO": {"minimum": "0.0159", "maximum": "0.1581"},
        "XNa2O+K2O": {"minimum": "0.0016", "maximum": "0.0599"},
    }
    assert fit_raw["regression_statistics_as_printed"] == {
        "n": "74", "mean_deviation_percent": "4.34", "chi_squared": "0.70",
        "sum_squares_per_degree_of_freedom": "0.010",
    }

    rows = _observation_map(document)
    assert len(TABLE4) == 14
    assert sum(
        observation["observation_id"].startswith("snyder_1992_table4_fe0_947o_activity_")
        and "activity_coefficient" not in observation["observation_id"]
        for observation in rows.values()
    ) == 14
    assert sum(
        "activity_coefficient_" in observation["observation_id"]
        for observation in rows.values()
    ) == 14

    table2_by_run = {row[0]: row for row in TABLE2}
    for run, activity, gamma, n, raw_glass in TABLE4:
        run_row = table2_by_run[run]
        _, temp, fo2, _hours, phases, ni, fe = run_row
        raw_composition = dict(zip(OXIDES, raw_glass, strict=True))
        central_composition = {
            oxide: _central(value) for oxide, value in raw_composition.items()
        }
        for quantity, quantity_value, field in (
            ("activity", activity, "activity_as_printed"),
            ("activity_coefficient", gamma, "gamma_as_printed"),
        ):
            obs_id = (
                f"snyder_1992_table4_fe0_947o_{quantity}_{run.lower()}"
            )
            observation = rows[obs_id]
            values = observation["values"]
            assert values["quantity"] == quantity
            assert values[field] == quantity_value
            assert values["T_K"] == temp
            assert values["fO2_log_as_printed"] == fo2
            assert values["run_products_as_printed"] == phases
            assert values["alloy_composition_wt_pct_as_printed"] == {
                "Ni": ni, "Fe": fe
            }
            assert values["glass_analysis_point_count_as_printed"] == n
            assert values["glass_composition_sum_as_printed"] == TABLE3_SUMS[run]
            assert values["glass_composition_wt_pct_as_printed"] == raw_composition
            assert values["glass_composition_wt_pct"] == central_composition
            assert values["glass_analysis_phase_as_printed"] == "gl=glass"
            assert values["glass_composition_quench_state"] == "not_stated"
            assert values["glass_composition_quench_locator"]["page"] == 3
            assert values["composition_locator"]["table"] == "3"
            assert values["composition_locator"]["record"] == f"{run}-gl"
            assert observation["locator"]["table"] == "4"
            assert observation["locator"]["record"] == run
            assert observation["locator"]["table_row_number"] == TABLE4.index(
                next(row for row in TABLE4 if row[0] == run)
            ) + 2
            assert observation["locator"]["table_column_number"] == (5 if quantity == "activity" else 8)
            assert observation["locator"]["phase"] == "liquid"
            conditions = observation["point_conditions"]
            assert conditions["temperature_K"]["state"]["value"] == temp
            assert conditions["temperature_K"]["locator"]["table"] == "2"
            assert conditions["fO2_log"]["state"]["value"] == fo2
            assert conditions["fO2_log"]["locator"]["table"] == "2"
            assert conditions["total_pressure_Pa"]["state"]["value"] == {
                "kind": "point", "point": "101325"
            }
            assert conditions["total_pressure_Pa"]["locator"]["section"] == (
                "Experimental Procedures"
            )
            assert conditions["composition"]["state"]["value"]["components"] == [
                [oxide, amount] for oxide, amount in central_composition.items()
            ]
            assert conditions["composition"]["locator"]["table"] == "3"
            assert "fO2_reference_pressure" not in values
            assert values["fO2_reference_pressure_as_printed"]["tag"] == "absent"
            assert observation["phase"] == "l"
            assert observation["method_class"] == "author_derived"


def test_every_published_cell_is_pinned_and_an_interior_mutation_fails() -> None:
    document = _document()
    _assert_printed_cells(document)

    mutated = yaml.safe_load(yaml.safe_dump(document, allow_unicode=True))
    middle = _observation_map(mutated)[
        "snyder_1992_table4_fe0_947o_activity_48-60"
    ]
    middle["values"]["activity_as_printed"] = "0.28"
    middle["values"]["activity"] = "0.28"
    with pytest.raises(AssertionError):
        _assert_printed_cells(mutated)

    wrong_na_mutation = yaml.safe_load(yaml.safe_dump(document, allow_unicode=True))
    glass_context = next(
        item for item in wrong_na_mutation["species"]["FeO"]["context"]
        if item["observation_id"] == "snyder_1992_table3_glass_compositions_context"
    )
    glass_row = next(
        row for row in glass_context["values"]["rows"]
        if row["run_as_printed"] == "48-59-gl"
    )
    glass_row["composition_wt_pct_as_printed"]["Na2O"] = "1.87(5)"
    with pytest.raises(AssertionError):
        _assert_printed_cells(wrong_na_mutation)


def test_isolated_migration_lands_all_values_and_printed_identity_axes() -> None:
    document = _document()
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 29
    assert sum(obs.value.kind.value == "unavailable" for obs in result.observations.values()) == 1

    migrated = {
        oid.rsplit("::", 1)[-1]: obs
        for oid, obs in result.observations.items()
        if SID in oid
    }
    raw = _observation_map(document)
    assert len(migrated) == 29
    expected_quantity_unknown = {FIT_ID}
    actual_quantity_unknown = {
        key for key, observation in migrated.items()
        if observation.identity.quantity.is_unknown
    }
    assert actual_quantity_unknown == expected_quantity_unknown
    actual_phase_unknown = {
        key for key, observation in migrated.items()
        if observation.identity.species.phase.is_unknown
    }
    assert actual_phase_unknown == set()

    table2_by_run = {row[0]: row for row in TABLE2}
    expected_composition_rows = 0
    for run, activity, gamma, _n, raw_glass in TABLE4:
        _run, temp, fo2, _hours, _products, _ni, _fe = table2_by_run[run]
        central_composition = {
            oxide: _central(value)
            for oxide, value in zip(OXIDES, raw_glass, strict=True)
        }
        composition = wt_pct_to_mole_fraction(central_composition)
        for suffix, quantity, expected in (
            (
                f"snyder_1992_table4_fe0_947o_activity_{run.lower()}",
                Quantity.ACTIVITY,
                activity,
            ),
            (
                f"snyder_1992_table4_fe0_947o_activity_coefficient_{run.lower()}",
                Quantity.ACTIVITY_COEFFICIENT,
                gamma,
            ),
        ):
            observation = migrated[suffix]
            assert observation.value.point == Decimal(expected)
            assert observation.identity.quantity.is_value
            assert observation.identity.quantity.value is quantity
            assert observation.identity.species.phase.is_value
            assert observation.identity.species.phase.value is Phase.L
            assert observation.identity.temperature_K.value == Decimal(temp)
            assert observation.identity.fO2_Pa.value == log10_fo2_to_pa(
                Decimal(fo2), Decimal("100000")
            )
            assert observation.identity.total_pressure_Pa.value == Decimal("101325")
            assert observation.identity.composition.is_value
            assert observation.identity.composition.value == composition

            conditions = observation.point_conditions
            assert conditions is not None
            assert set(conditions) >= {
                "temperature_K", "fO2_log", "total_pressure_Pa", "composition"
            }
            assert conditions["temperature_K"].state.value == Decimal(temp)
            assert conditions["fO2_log"].state.value == Decimal(fo2)
            assert conditions["total_pressure_Pa"].state.value.point == Decimal("101325")
            assert conditions["composition"].state.value == composition
            assert observation.locator is not None
            assert observation.locator.page == 7 and observation.locator.table == "4"
            assert conditions["fO2_log"].locator.page == 3
            assert conditions["fO2_log"].locator.table == "2"
            assert conditions["composition"].locator.table == "3"
            expected_composition_rows += 1

        for quantity in ("activity", "activity_coefficient"):
            obs_id = f"snyder_1992_table4_fe0_947o_{quantity}_{run.lower()}"
            raw_values = raw[obs_id]["values"]
            assert raw_values["fO2_reference_pressure_as_printed"] == {
                "tag": "absent",
                "reason": "Table 2 does not state a pressure reference; the task permits the migrator bar default for these two-decimal values.",
            }

    assert expected_composition_rows == 28
    experiment = document["experiments"][0]
    assert experiment["pressure_environment"]["total_pressure_Pa"]["state"]["value"] == {
        "kind": "point", "point": "101325"
    }
    assert experiment["pressure_environment"]["sweep_gas"]["state"]["tag"] == "unknown"
    assert experiment["pressure_environment"]["sweep_gas"]["state"]["reason"] == (
        "The source says CO and CO2 were mixed but does not print their ratio."
    )
    assert all(
        "gas_mix" not in observation["point_conditions"]
        for observation in raw.values()
        if "point_conditions" in observation
    )
    fit = migrated[FIT_ID]
    assert fit.identity.quantity.is_unknown
    assert fit.value.kind.value == "unavailable"
    assert fit.identity.species.phase.value is Phase.L
    standard = migrated["snyder_1992_table4_fe0_947o_activity_08-58"].identity.reference_state
    assert standard.is_value
    assert standard.value.endmember.formula == "Fe0.947O"
    assert standard.value.endmember.phase.value is Phase.CR
    assert standard.value.endmember.polymorph.is_unknown

    # Numeric target totals: 14 activities + 14 activity coefficients; no EMF.
    assert sum(
        observation.value.kind.value == "point"
        for key, observation in migrated.items()
        if key != FIT_ID
    ) == 28
    assert document["species"]["FeO"]["context"][0]["values"][
        "electromotive_force_as_printed"
    ]["tag"] == "absent"
