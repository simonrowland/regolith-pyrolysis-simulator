from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken, Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/doyle-1988-feo-activity-multicomponent-magma.yaml"
EXPECTED_TABLE1A = tuple(tuple(line.split("|")) for line in '2A|1|0.339|46.0|8.6|45.0|2.8|---|Hi|\n2A|2|0.339|46.1|5.8|47.7|2.1|---|Hi|\n2A|3|0.339|42.2|15.0|36.4|7.5|---|Hi|\n2A|4|0.339|35.2|23.3|34.7|9.0|---|Hi|*\n2A|5|0.339|45.1|10.4|41.4|4.7|---|Hi|\n2A|6|0.398|36.1|21.7|31.0|11.9|---|Lo|$\n2A|7|0.398|41.2|9.4|45.9|5.5|---|Hi|\n2A|8|0.398|40.6|10.6|45.3|5.1|---|Hi|\n2A|9|0.398|42.3|5.0|51.0|3.7|---|Lo|\n2A|10|0.398|34.1|21.1|36.9|8.4|---|Hi|*\n2A|11|0.452|38.4|6.9|52.8|4.3|---|Hi|\n2A|12|0.452|37.3|9.4|50.0|4.5|---|Hi|\n2A|13|0.452|37.0|11.2|46.8|6.6|---|Hi|\n2A|14|0.452|32.1|20.3|41.6|7.3|---|Hi|*\n2A|15|0.507|36.4|4.4|59.8|2.6|---|Lo|\n2A|16|0.507|34.1|14.1|49.2|5.0|---|Hi|\n2A|17|0.507|32.8|21.7|33.0|14.2|---|Lo|*\n2A|18|0.507|36.7|5.1|59.1|2.7|---|Hi|\n2A|19|0.507|31.5|19.0|43.5|8.1|---|Hi|\n2A|20|0.507|35.4|10.3|53.2|4.6|---|Lo|\n2A|21|0.507|35.7|8.5|54.6|4.2|---|Hi|\n2A|22|0.507|32.1|19.7|43.2|6.8|---|Lo|*\n2A|23|0.569|28.4|16.8|45.9|8.6|---|Lo|*\n2A|24|0.569|30.3|14.1|50.5|6.2|---|Lo|\n2A|25|0.569|29.9|18.3|39.2|14.3|---|Lo|\n2A|26|0.569|31.0|9.1|57.6|4.1|---|Lo|\n2A|27|0.569|32.6|3.7|63.7|2.7|---|Hi|\n2A|28|0.569|31.3|10.3|55.3|5.1|---|Hi|\n2A|29|0.569|32.5|5.2|62.6|2.6|---|Hi|\n2A|30|0.569|31.8|7.3|59.3|3.8|---|Hi|\n2C|1|0.170|51.1|20.4|13.0|10.7|2.8|Hi|\n2C|2|0.170|49.6|20.6|13.0|13.8|2.2|Hi|$\n2C|3|0.170|56.5|19.9|10.9|7.1|4.7|Hi|\n2C|4|0.170|52.6|20.6|12.4|11.5|3.1|Lo|$@\n2C|5|0.170|62.1|17.8|7.5|2.7|9.0|Hi|\n2C|6|0.227|55.5|17.2|15.8|6.6|4.3|Hi|\n2C|7|0.227|58.3|17.5|11.5|2.9|7.7|Hi|\n2C|8|0.227|68.4|10.7|9.9|2.6|7.1|Lo|&\n2C|9|0.227|50.2|20.1|15.0|11.1|2.4|Lo|$@\n2C|10|0.227|56.9|15.7|14.8|6.8|4.8|Hi|\n2C|11|0.227|42.5|22.6|17.5|14.1|1.5|Hi|\n2C|12|0.227|64.4|14.2|9.1|3.4|8.0|Hi|\n2C|13|0.285|53.1|14.8|21.9|7.0|3.6|Hi|\n2C|14|0.285|42.8|21.5|21.9|10.4|2.6|Lo|\n2C|15|0.285|46.8|21.7|18.5|10.4|3.8|Lo|\n2C|16|0.339|65.0|10.6|16.8|1.6|7.8|Hi|\n2C|17|0.339|55.4|13.2|21.7|3.4|5.9|Hi|\n2C|18|0.339|49.2|15.3|26.2|4.9|4.1|Hi|\n2C|19|0.398|42.1|12.7|35.9|5.0|2.8|Hi|\n2C|20|0.398|47.5|12.3|31.8|2.3|4.9|Hi|\n2C|21|0.398|39.3|18.0|30.6|10.3|1.7|Lo|\n2C|22|0.452|44.3|9.4|41.0|2.3|3.7|Lo|\n2C|23|0.452|46.9|8.4|38.5|2.1|4.7|Hi|\n2C|24|0.452|33.0|20.7|34.6|11.1|1.2|Hi|*\n2C|25|0.452|38.8|13.5|38.6|3.5|3.1|Lo|\n2C|26|0.452|35.4|19.2|33.1|8.9|2.3|Lo|\n2C|27|0.507|40.7|9.6|44.6|1.4|4.3|Hi|\n2C|28|0.507|43.9|8.8|40.6|0.9|6.0|Lo|\n2C|29|0.507|31.6|19.4|37.0|9.9|1.4|Hi|\n2C|30|0.507|35.4|16.0|39.7|5.9|2.8|Hi|\n2C|31|0.507|32.8|18.7|36.4|9.5|1.9|Lo|\n2C|32|0.569|34.7|12.2|49.8|1.7|2.9|Lo|\n2C|33|0.569|32.4|14.6|47.1|4.2|2.5|Hi|\n2C|34|0.569|30.2|17.4|44.3|6.9|1.5|Hi|\n2C|35|0.569|29.8|19.4|40.1|10.2|1.3|Hi|*\n2C|36|0.569|29.9|18.9|41.1|9.4|1.3|Hi|*\n2C|37|0.569|34.4|14.0|47.6|2.9|3.4|Lo|\n2C|38|0.569|33.0|17.4|43.9|6.1|2.6|Lo|\n2B|1|0.227|55.7|16.9|13.7|9.5|2.8|Lo|\n2B|2|0.227|44.7|19.8|19.9|12.9|1.4|Hi|$\n2B|3|0.227|51.0|18.2|17.7|9.1|2.4|Hi|\n2B|4|0.227|50.5|16.6|18.0|11.2|2.0|Hi|\n2B|5|0.227|56.4|16.5|13.4|9.2|3.0|Lo|&\n2B|6|0.227|50.5|17.6|13.3|15.0|1.8|Lo|*\n2B|7|0.285|48.4|17.3|22.5|10.4|2.2|Hi|\n2B|8|0.285|53.7|16.8|17.6|9.0|3.0|Lo|\n2B|9|0.285|43.5|20.2|21.7|12.4|1.6|Hi|\n2B|10|0.339|44.0|14.7|29.0|10.8|1.4|Lo|\n2B|11|0.339|37.5|22.2|25.2|13.7|0.6|Hi|\n2B|12|0.398|41.8|13.9|36.0|6.6|1.6|Lo|\n2B|13|0.398|39.5|14.1|33.3|9.7|1.3|Hi|\n2B|14|0.398|49.5|9.9|32.9|4.5|3.2|Lo|\n2B|15|0.398|34.9|19.1|33.0|11.7|0.9|Hi|\n2B|16|0.452|34.7|17.4|34.8|10.6|0.8|Hi|\n2B|17|0.452|39.9|9.9|41.8|4.6|2.3|Hi|\n2B|18|0.452|39.5|12.8|40.8|6.6|1.8|Lo|\n2B|19|0.452|36.8|15.2|37.7|9.6|0.8|Lo|\n2B|20|0.452|42.5|8.5|43.1|4.1|2.6|Lo|\n2B|21|0.452|37.7|14.3|38.5|9.3|1.0|Hi|\n2B|22|0.507|33.2|15.2|41.2|9.4|0.7|Lo|\n2B|23|0.507|34.1|12.1|46.1|5.9|0.9|Hi|\n2B|24|0.507|34.8|10.2|48.3|4.7|1.2|Hi|\n2B|25|0.507|35.8|7.0|52.3|3.3|1.2|Lo|\n2B|26|0.507|32.0|16.4|38.9|10.4|0.4|Hi|\n2B|27|0.569|33.7|7.5|55.4|2.9|1.4|Hi|\n2B|28|0.569|34.1|7.7|55.2|4.4|1.1|Lo|\n2B|29|0.569|32.1|14.8|44.7|9.7|0.4|Lo|\n2B|30|0.569|34.4|6.4|56.0|3.4|1.4|Lo|\n2B|31|0.569|31.7|15.9|44.2|9.6|0.4|Hi|\n2B|32|0.569|32.8|11.5|48.5|7.2|0.8|Hi|\n2B|33|0.569|33.0|11.1|49.5|6.2|0.9|Hi|\n2D|1|0.285|60.9|15.6|12.7|2.6|8.7|Hi|\n2D|2|0.285|61.9|13.3|14.5|1.7|9.1|Lo|&\n2D|3|0.285|43.3|23.3|19.2|10.5|4.3|Hi|\n2D|4|0.285|52.6|19.7|13.1|4.3|10.2|Lo|\n2D|5|0.285|59.0|17.1|9.8|2.1|12.8|Lo|*\n2D|6|0.285|49.5|20.7|16.3|8.3|6.5|Lo|\n2D|7|0.285|46.8|22.2|15.9|7.6|7.4|Hi|\n2D|8|0.285|52.0|19.2|14.4|4.4|9.9|Hi|\n2D|9|0.285|40.3|24.2|20.6|12.1|2.7|Lo|\n2D|10|0.398|48.1|17.2|20.7|2.5|10.1|Lo|\n2D|11|0.398|32.8|21.0|32.2|11.3|1.7|Hi|\n2D|12|0.398|40.7|18.8|27.5|6.1|5.1|Hi|\n2D|13|0.398|47.7|18.0|22.5|3.3|8.3|Hi|\n2D|14|0.398|36.9|22.8|26.6|10.5|3.1|Lo|\n2D|15|0.398|42.7|18.9|24.5|8.1|5.9|Lo|\n2D|16|0.398|46.4|17.2|23.6|4.4|8.7|Lo|\n2D|17|0.398|37.3|21.2|27.5|9.5|3.3|Hi|\n2D|18|0.452|45.2|16.7|27.4|3.1|7.9|Hi|\n2D|19|0.452|37.8|18.1|31.7|7.4|4.0|Lo|\n2D|20|0.452|43.2|16.0|26.9|4.4|7.6|Lo|\n2D|21|0.452|46.0|14.5|28.9|2.9|7.3|Hi|\n2D|22|0.507|33.4|18.0|37.1|7.9|2.7|Hi|\n2D|23|0.507|48.5|14.1|26.6|2.2|9.5|Lo|\n2D|24|0.507|37.0|17.9|33.9|6.4|4.7|Lo|\n2D|25|0.507|41.8|16.8|31.4|3.7|7.3|Lo|\n2D|26|0.507|41.8|15.3|35.2|2.8|6.5|Hi|\n2D|27|0.569|37.0|15.6|38.9|3.5|6.1|Hi|\n2D|28|0.569|39.6|15.6|38.1|3.0|7.3|Hi|'.splitlines())
FO2_BY_ACTIVITY = {"0.170": "-12.06", "0.227": "-11.81", "0.285": "-11.61", "0.339": "-11.46", "0.398": "-11.32", "0.452": "-11.21", "0.507": "-11.11", "0.569": "-11.01"}
FO2_UNCERTAINTY = "0.02"
SIGMA_BY_ACTIVITY = {"0.170": "0.008", "0.227": "0.010", "0.285": "0.012", "0.339": "0.014", "0.398": "0.016", "0.452": "0.018", "0.507": "0.020", "0.569": "0.022"}
FLOWS_BY_ACTIVITY = {
    "0.170": {"75.6 CO": "---", "24.4 CO2": "15.6", "H2": "120.0", "N2": "135.6"},
    "0.227": {"75.6 CO": "---", "24.4 CO2": "20.2", "H2": "120.0", "N2": "140.2"},
    "0.285": {"75.6 CO": "---", "24.4 CO2": "24.7", "H2": "120.0", "N2": "144.7"},
    "0.339": {"75.6 CO": "---", "24.4 CO2": "29.0", "H2": "120.0", "N2": "149.0"},
    "0.398": {"75.6 CO": "50.0", "24.4 CO2": "---", "H2": "29.1", "N2": "130.0"},
    "0.452": {"75.6 CO": "50.0", "24.4 CO2": "---", "H2": "24.0", "N2": "130.0"},
    "0.507": {"75.6 CO": "50.0", "24.4 CO2": "---", "H2": "20.3", "N2": "130.0"},
    "0.569": {"75.6 CO": "50.0", "24.4 CO2": "---", "H2": "16.7", "N2": "130.0"},
}
OXIDES = ("SiO2", "Al2O3", "FeO", "CaO", "K2O")


def _table_rows(document):
    return [row for row in document["species"]["FeO"]["observations"] if row["observation_id"].startswith("doyle_1988_activity_p004_t01_")]


def _assert_every_table1a_cell(document):
    rows = _table_rows(document)
    assert len(rows) == 129 == len(EXPECTED_TABLE1A)
    assert [row["values"]["activity"] for row in rows] == [expected[2] for expected in EXPECTED_TABLE1A]
    assert [row["locator"]["panel"] for row in rows] == [f"Fig. {expected[0]}" for expected in EXPECTED_TABLE1A]
    experiment = document["experiments"][0]
    assert experiment["method"] == MethodToken.QUENCH_EQUILIBRATION.value
    assert experiment["locator"]["page"] == 2
    assert experiment["locator"]["published_page"] == 1828
    assert experiment["locator"]["pdf_page_index"] == 1
    assert experiment["locator"]["section"] == "Experimental and Analytical Methods"
    assert experiment["method_as_printed"] == (
        "each sample is suspended on a 99.94% Fe-wire loop and is equilibrated with a COHN gas "
        "at 1600 K in the hot zone of the Deltech MoSi₂ vertical quench furnace."
    )
    for row, expected in zip(rows, EXPECTED_TABLE1A):
        panel, printed_row, activity, sio2, al2o3, feo, cao, k2o, sm, marker = expected
        raw = row["values"]["oxide_wt_pct_as_printed"]
        method_details = row["derivation"]["method_detail_as_printed"]
        assert method_details == document["experiments"][0]["method_detail_as_printed"]
        control_details = method_details["fO2_control"]
        for detail_name, section in (
            ("control", "Control of the Activity of FeO"),
            ("gas_mixing", "Experimental and Analytical Methods"),
            ("fugacity_calculation", "Experimental and Analytical Methods"),
        ):
            locator = control_details[detail_name]["locator"]
            assert locator["page"] == 2 and locator["published_page"] == 1828
            assert locator["pdf_page_index"] == 1 and locator["section"] == section
        assert control_details["fugacity_calculation"]["as_printed"] == (
            "Fugacities of the gaseous species in the hot zone are calculated by BRINKLEY's "
            "(1946) method using the algorithm of VAN ZEGGEREN and STOREY (1970)."
        )
        assert control_details["control"]["as_printed"] == (
            "aluminosilicate liquids are equilibrated with reagent-grade (99.94%) metallic iron-wire "
            "under controlled oxygen fugacity at 1600 K."
        )
        assert control_details["gas_mixing"]["as_printed"] == experiment["method_as_printed"]
        assert control_details["gas_flow_locator"]["published_page"] == 1831
        assert control_details["gas_flow_locator"]["table"] == "1B"
        assert control_details["gas_flow_locator"]["note"] == (
            "Table 1B. Compositions of input gases (c.c.s/min) used in the experiments "
            "to determine aFeO at 1600±2K in [SiO₂-KAlO₂-CaAl₂Si₂O₈]-FeO liquids."
        )
        assert method_details["metal_phase"]["phase_as_printed"] == "reagent-grade (99.94%) metallic iron-wire"
        assert method_details["metal_phase"]["phase_locator"]["published_page"] == 1828
        assert "activity_as_printed" in method_details["metal_phase"]
        assert method_details["metal_phase"]["activity_as_printed"] == (
            r"Because the metallic iron is almost pure ( $a_{\mathrm{Fe}}^{\mathrm{metal}} = 1$ )"
        )
        assert method_details["metal_phase"]["activity_locator"]["published_page"] == 1828
        standard_state = method_details["FeO_standard_state"]
        assert standard_state["standard_state_as_printed"] == (
            "The standard state is molten Fe $_{0.947}$ O (COUGHLIN, 1954)."
        )
        assert standard_state["locator"]["published_page"] == 1828
        assert row["values"]["activity_as_printed"] == activity
        assert row["values"]["T_K"] == "1600"
        assert row["values"]["temperature_as_printed"] == "1600±2 K"
        assert row["values"]["temperature_uncertainty_K"] == "2"
        assert row["values"]["fO2_as_printed"] == f"-log10 fO2 = {FO2_BY_ACTIVITY[activity].removeprefix('-')}"
        assert row["values"]["fO2_log_uncertainty"] == FO2_UNCERTAINTY
        assert row["values"]["fO2_log_uncertainty_as_printed"] == "+0.02"
        assert row["values"]["gas_flows_cc_min_as_printed"] == FLOWS_BY_ACTIVITY[activity]
        assert row["values"]["quench_status"] == "not_stated"
        assert row["values"]["sigma"] == SIGMA_BY_ACTIVITY[activity]
        assert row["uncertainty"]["value"] == SIGMA_BY_ACTIVITY[activity]
        assert row["uncertainty"]["verbatim"] == f"± {SIGMA_BY_ACTIVITY[activity]}; confidence level not stated"
        assert row["values"]["starting_material_FeO_relation_as_printed"] == sm
        assert row["values"]["run_product_markers_as_printed"] == (marker or "none")
        assert row["locator"]["table"] == "1A" and row["locator"]["page"] == 4
        assert row["locator"]["row_label"] == f"Fig. {panel} row {printed_row}"
        assert [raw[name] for name in OXIDES] == [sio2, al2o3, feo, cao, k2o]
        if k2o == "---":
            assert row["values"]["oxide_absences_as_printed"]["K2O"]["state"]["tag"] == "not_applicable"
        conditions = row["point_conditions"]
        assert conditions["temperature_K"]["state"] == {"tag": "value", "value": "1600"}
        assert conditions["fO2_log"]["state"] == {"tag": "value", "value": FO2_BY_ACTIVITY[activity]}
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert "pressure" in conditions["total_pressure_Pa"]["state"]["reason"].lower()
        assert conditions["composition"]["locator"]["table"] == "1A"
        expected_components = {name: value for name, value in zip(OXIDES, (sio2, al2o3, feo, cao, k2o)) if value not in ("", "---")}
        assert conditions["composition"]["state"]["value"]["components"] == expected_components


def test_doyle_1988_table1a_every_cell_and_render_pins():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_every_table1a_cell(document)
    setpoints = document["species"]["FeO"]["context"]
    assert len(setpoints) == 8
    for context in setpoints:
        values = context["values"]
        activity = values["activity_as_printed"]
        assert values["uncertainty_as_printed"] == SIGMA_BY_ACTIVITY[activity]
        assert values["minus_log10_fO2_as_printed"] == FO2_BY_ACTIVITY[activity].removeprefix("-")
        assert values["minus_log10_fO2_uncertainty"] == FO2_UNCERTAINTY
        assert values["minus_log10_fO2_uncertainty_as_printed"] == "+0.02"
        flows = FLOWS_BY_ACTIVITY[activity]
        assert values["gas_flows_cc_min_as_printed"] == flows
        assert set(values["gas_flow_absences_as_printed"]) == {name for name, value in flows.items() if value == "---"}
        assert context["locator"]["page"] == 5 and context["locator"]["table"] == "1B"
    rows = _table_rows(document)
    assert len(rows) == sum((30, 33, 38, 28))
    for panel, value in (("2A", "0.339"), ("2C", "0.170"), ("2B", "0.227"), ("2D", "0.285")):
        row = next(r for r in rows if r["locator"]["panel"] == f"Fig. {panel}")
        assert row["values"]["activity"] == value
        assert row["locator"]["page"] == 4 and row["locator"]["table"] == "1A"
    marked_cells = {
        "doyle_1988_activity_p004_t01_r6_c8": ("0.170", "$@", "Fig. 2C"),
        "doyle_1988_activity_p004_t01_r10_c8": ("0.227", "&", "Fig. 2C"),
        "doyle_1988_activity_p004_t01_r11_c8": ("0.227", "$@", "Fig. 2C"),
        "doyle_1988_activity_p004_t01_r47_c1": ("0.227", "&", "Fig. 2B"),
        "doyle_1988_activity_p004_t01_r49_c8": ("0.285", "&", "Fig. 2D"),
    }
    by_id = {row["observation_id"]: row for row in rows}
    for observation_id, (value, marker, panel) in marked_cells.items():
        row = by_id[observation_id]
        assert row["values"]["activity"] == value
        assert row["values"]["run_product_markers_as_printed"] == marker
        assert row["locator"]["page"] == 4 and row["locator"]["table"] == "1A"
        assert row["locator"]["panel"] == panel
    quoted = next(row for row in document["species"]["FeO"]["observations"] if row["observation_id"] == "doyle_1988_quoted_activity_stoichiometric_liquid_feo_1_93")
    assert quoted["values"]["activity"] == "1.93" and quoted["locator"]["page"] == 2
    assert quoted["values"]["attribution_as_printed"].startswith("Stull and Prophet (1971)")


def test_doyle_1988_cell_pin_rejects_interior_mutation():
    mutated = deepcopy(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    _table_rows(mutated)[63]["values"]["oxide_wt_pct_as_printed"]["FeO"] = "0.001"
    with pytest.raises(AssertionError):
        _assert_every_table1a_cell(mutated)


def test_doyle_1988_marker_pin_rejects_removed_spinel_asterisk():
    mutated = deepcopy(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    row = next(r for r in _table_rows(mutated) if r["observation_id"] == "doyle_1988_activity_p004_t01_r6_c1")
    assert row["values"]["run_product_markers_as_printed"] == "*"
    row["values"]["run_product_markers_as_printed"] = "none"
    with pytest.raises(AssertionError):
        _assert_every_table1a_cell(mutated)


def test_doyle_1988_method_detail_pin_rejects_deleted_metal_activity():
    mutated = deepcopy(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    del mutated["experiments"][0]["method_detail_as_printed"]["metal_phase"]["activity_as_printed"]
    with pytest.raises(AssertionError):
        _assert_every_table1a_cell(mutated)


def test_doyle_1988_method_detail_pin_rejects_calculation_and_activity_mutations():
    original = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    measured = deepcopy(original)
    calculation = measured["experiments"][0]["method_detail_as_printed"]["fO2_control"]["fugacity_calculation"]
    calculation["as_printed"] = calculation["as_printed"].replace("calculated", "measured")
    with pytest.raises(AssertionError):
        _assert_every_table1a_cell(measured)

    collapsed_activity = deepcopy(original)
    metal_phase = collapsed_activity["experiments"][0]["method_detail_as_printed"]["metal_phase"]
    metal_phase["activity_as_printed"] = metal_phase["activity_as_printed"].replace(
        r"$a_{\mathrm{Fe}}^{\mathrm{metal}}", "a_Fe"
    )
    with pytest.raises(AssertionError):
        _assert_every_table1a_cell(collapsed_activity)


def test_doyle_1988_isolated_migration_pins_every_numeric_row_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_every_table1a_cell(document)
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    source_rows = document["species"]["FeO"]["observations"]
    target_ids = {row["observation_id"] for row in source_rows}
    migrated = {row.observation_id.rsplit("::", 1)[-1]: row for row in result.observations.values() if row.observation_id.rsplit("::", 1)[-1] in target_ids}
    assert set(migrated) == target_ids and len(migrated) == 130
    assert sum(row.value.kind.value == "point" for row in migrated.values()) == 130
    assert sum(row.value.kind.value == "unavailable" for row in migrated.values()) == 0
    for raw in source_rows:
        row = migrated[raw["observation_id"]]
        assert row.value.point == Decimal(raw["values"]["activity"])
        if raw["observation_id"].startswith("doyle_1988_activity_p004"):
            assert result.experiments[row.experiment_id].method.value is MethodToken.QUENCH_EQUILIBRATION
            assert row.uncertainty.kind.value == "printed"
            assert row.uncertainty.verbatim["value"] == SIGMA_BY_ACTIVITY[raw["values"]["activity"]]
            assert row.uncertainty.verbatim["verbatim"] == f"± {SIGMA_BY_ACTIVITY[raw['values']['activity']]}; confidence level not stated"
            assert row.uncertainty.verbatim["locator"]["page"] == 5
            assert row.uncertainty.verbatim["locator"]["table"] == "1B"
        assert row.identity.quantity.value is Quantity.ACTIVITY
        assert row.identity.species.phase.is_value and row.identity.species.phase.value.value == "l"
        assert row.identity.total_pressure_Pa.is_unknown
        if "total_pressure_Pa" in row.point_conditions:
            assert row.point_conditions["total_pressure_Pa"].state.is_unknown
        if raw["observation_id"].startswith("doyle_1988_activity_p004"):
            assert raw["values"]["standard_state_as_printed"] == "molten Fe0.947O (COUGHLIN, 1954)"
            assert raw["values"]["fO2_log_uncertainty"] == FO2_UNCERTAINTY
            assert raw["values"]["fO2_log_uncertainty_as_printed"] == "+0.02"
            assert row.identity.reference_state.is_unknown
            assert row.point_conditions["temperature_K"].state.value == Decimal("1600")
            assert row.point_conditions["fO2_log"].state.value == Decimal(FO2_BY_ACTIVITY[raw["values"]["activity"]])
            assert row.identity.temperature_K.value == Decimal("1600")
            assert row.identity.fO2_Pa.is_value
            components = raw["point_conditions"]["composition"]["state"]["value"]["components"]
            expected_composition = wt_pct_to_mole_fraction(
                {name: Decimal(value) for name, value in components.items()}
            ).components
            assert row.point_conditions["composition"].state.value.components == expected_composition
            assert row.identity.composition.is_value
            assert row.identity.composition.value.components == expected_composition
        else:
            assert raw["values"]["standard_state_as_printed"] == "stoichiometric liquid FeO (STULL and PROPHET, 1971)"
            assert row.point_conditions["T_C"].state.value == Decimal("1380")
            assert row.point_conditions["fO2_log"].state.is_unknown
            assert row.identity.fO2_Pa.is_unknown
            assert row.identity.composition.is_unknown
            assert row.identity.temperature_K.value == Decimal("1653.15")
        assert row.identity.total_pressure_Pa.is_unknown
    assert migrated["doyle_1988_quoted_activity_stoichiometric_liquid_feo_1_93"].value.point == Decimal("1.93")
    for row in [migrated[x["observation_id"]] for x in source_rows[:3]]:
        print("identity axes", row.observation_id, {axis: getattr(row.identity, axis) for axis in ("composition", "total_pressure_Pa", "fO2_Pa", "temperature_K")})
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa.is_value
