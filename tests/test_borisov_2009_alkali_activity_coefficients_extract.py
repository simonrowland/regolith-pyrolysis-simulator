"""Independent render pins and isolated migration for Borisov (2009)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/borisov-2009-alkali-activity-coefficients.yaml"

# Table 2 transcribed independently from the 600-dpi physical page 2 render.
# Each row is series, source sample, T, printed -log fO2, duration, SiO2, Na2O, K2O, Rb2O, total.
TABLE2_RENDER = [
    ["38", "RNK-38", "1470", "8.93", "15.0", "83.4", "3.3", "4.4", "7.1", "98.1"],
    ["36", "RNK-36", "1300", "10.74", "17.5", "76.7", "5.0", "6.0", "10.0", "97.7"],
    ["39", "RNK-39", "1400", "9.63", "16.0", "81.0", "4.0", "5.1", "8.5", "98.6"],
    ["37", "RNK-37", "1470", "8.93", "15.5", "85.8", "3.2", "4.2", "6.8", "100.1"],
]

# Table 3 transcribed independently from the 600-dpi physical page 3 render.
# Each row is sample, SiO2, Al2O3, MgO, CaO, Na2O, Na s.d., K2O, K s.d., Rb2O, Rb s.d., total.
TABLE3_RENDER = """
CSA6RNK-38|54.71|6.26|—|32.60|1.81|0.09|1.89|0.04|2.09|0.06|100.13
CSA12RNK-38|50.13|12.47|—|30.28|1.70|0.09|1.91|0.05|2.15|0.05|99.50
CSA18RNK-38|46.58|18.74|—|27.95|1.82|0.07|2.03|0.04|2.28|0.07|100.12
CSA25RNK-38|42.53|24.63|—|25.47|1.87|0.10|2.15|0.04|2.37|0.07|99.94
DARNK-36|47.09|14.98|10.03|22.16|2.20|0.10|1.78|0.04|2.40|0.07|100.66
DAS50RNK-36|58.71|9.37|6.22|13.67|3.21|0.11|3.28|0.04|5.96|0.10|100.45
DAS200RNK-36|72.57|4.87|2.91|6.05|2.64|0.11|3.62|0.08|7.26|0.10|99.95
DAS400RNK-36|77.37|2.25|1.98|5.85|2.27|0.14|3.36|0.11|6.43|0.16|99.54
DARNK-37|47.45|14.94|10.10|22.24|1.87|0.10|1.87|0.05|2.01|0.07|100.51
DAS50RNK-37|58.89|9.37|6.30|13.69|2.83|0.08|4.11|0.06|5.28|0.10|100.51
DAS200RNK-37|70.41|4.69|3.17|6.92|2.77|0.08|4.99|0.08|7.14|0.10|100.12
DAS400RNK-37|77.51|3.20|1.76|3.73|2.35|0.10|4.59|0.09|6.98|0.11|100.16
DARNK-39|46.96|14.91|10.08|22.11|2.10|0.10|2.01|0.06|2.37|0.06|100.57
DAS50RNK-39|57.95|9.26|6.18|13.49|3.05|0.08|4.21|0.07|5.87|0.09|100.03
DAS200RNK-39|69.54|4.50|3.17|7.01|2.99|0.08|4.98|0.07|7.89|0.10|100.11
DAS400RNK-39|77.35|3.31|1.77|3.22|2.37|0.07|4.42|0.05|7.63|0.09|100.10
""".strip()

# Table 4 transcribed independently from the 600-dpi physical page 6 render.
# Each row is sample, XSiO2, XNa2O, XK2O, XRb2O, gamma Na2O, gamma K2O, gamma Rb2O.
TABLE4_RENDER = """
DARNK-36|0.477|0.022|0.012|0.008|0.868|0.830|0.761
DAS50RNK-36|0.616|0.033|0.022|0.020|0.575|0.435|0.296
DAS200RNK-36|0.776|0.027|0.025|0.025|0.685|0.387|0.238
DAS400RNK-36|0.820|0.023|0.023|0.022|0.805|0.420|0.271
DARNK-39|0.477|0.021|0.013|0.008|0.875|0.811|0.785
DAS50RNK-39|0.613|0.031|0.028|0.020|0.579|0.372|0.304
DAS200RNK-39|0.747|0.031|0.034|0.027|0.580|0.309|0.223
DAS400RNK-39|0.832|0.025|0.030|0.026|0.732|0.348|0.230
DARNK-37|0.480|0.018|0.012|0.007|0.857|0.777|0.745
DAS50RNK-37|0.616|0.029|0.027|0.018|0.548|0.342|0.274
DAS200RNK-37|0.753|0.029|0.034|0.025|0.548|0.276|0.198
DAS400RNK-37|0.829|0.024|0.031|0.024|0.646|0.300|0.203
""".strip()

# Table 5 transcribed independently from the 600-dpi physical page 8 render.
# Each row is sample, XAl2O3, XNa2O, XK2O, XRb2O, three gammas, Al/Alk, ASI.
TABLE5_RENDER = """
CSA6|0.038|0.018|0.012|0.007|1.000|1.000|1.000|1.016|0.096
CSA12|0.079|0.018|0.013|0.007|1.026|0.954|0.937|2.066|0.204
CSA18|0.121|0.019|0.014|0.008|0.937|0.877|0.864|2.912|0.327
CSA25|0.164|0.021|0.016|0.009|0.881|0.800|0.803|3.678|0.465
""".strip()

OXIDES = ("SiO2", "Al2O3", "MgO", "CaO", "Na2O", "K2O", "Rb2O")
ALKALIS = ("Na2O", "K2O", "Rb2O")
SERIES = {
    "36": ("1300", "10.74", "17.5"),
    "37": ("1470", "8.93", "15.5"),
    "38": ("1470", "8.93", "15.0"),
    "39": ("1400", "9.63", "16.0"),
}
KELVIN = {"1300": Decimal("1573.15"), "1400": Decimal("1673.15"), "1470": Decimal("1743.15")}


def document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def observations_by_table(extract: dict, table: str) -> dict[tuple[str, str], dict]:
    actual = {}
    for formula, container in extract["species"].items():
        for row in container.get("observations", []):
            if str(row.get("locator", {}).get("table")) != table:
                continue
            values = row["values"]
            component = values.get("component_as_published", values.get("species"))
            key = (values["sample_as_printed"], component)
            assert key not in actual
            assert formula == component
            actual[key] = row
    return actual


def assert_table2_matches(extract: dict, rendered: list[list[str]]) -> None:
    actual = observations_by_table(extract, "2")
    assert len(actual) == 16
    expected_oxides = ("SiO2", "Na2O", "K2O", "Rb2O")
    for series_no, sample, temperature, minus_log, duration, *tail in rendered:
        components, total = tail[:4], tail[4]
        assert len(components) == len(expected_oxides)
        for oxide, expected in zip(expected_oxides, components, strict=True):
            row = actual[(sample, oxide)]
            values = row["values"]
            assert values["value_as_published"] == expected
            assert values["sample_total_wt_pct"] == total
            assert values["analysis_n"] == 20
            assert values["duration_h_as_printed"] == duration
            assert values["table2_series_no_as_printed"] == series_no
            assert row["T_C"] == temperature
            assert row["point_conditions"]["minus_log_fO2_as_printed"]["state"]["value"] == minus_log
            assert row["point_conditions"]["duration_h"]["state"]["value"] == duration
            assert row["point_conditions"]["temperature_K"]["state"]["value"] == str(KELVIN[temperature])
            assert row["locator"]["page"] == 2
            assert row["locator"]["pdf_page_index"] == 1


def assert_table3_matches(extract: dict, rendered: str) -> None:
    expected_rows = [line.split("|") for line in rendered.splitlines()]
    actual = observations_by_table(extract, "3")
    expected_count = sum(
        1 for row in expected_rows for index in (1, 2, 3, 4, 5, 7, 9)
        if row[index] != "—"
    )
    assert len(actual) == 108
    for row in expected_rows:
        sample, sio2, al2o3, mgo, cao, na2o, na_sd, k2o, k_sd, rb2o, rb_sd, total = row
        printed_components = {
            "SiO2": sio2, "Al2O3": al2o3, "MgO": mgo, "CaO": cao,
            "Na2O": na2o, "K2O": k2o, "Rb2O": rb2o,
        }
        errors = {"Na2O": na_sd, "K2O": k_sd, "Rb2O": rb_sd}
        actual_full_composition = None
        for oxide, printed in printed_components.items():
            if printed == "—":
                continue
            observation = actual[(sample, oxide)]
            values = observation["values"]
            assert values["value_as_published"] == printed, (sample, oxide)
            assert values["sample_total_wt_pct"] == total
            assert values["analysis_n"] == (30 if sample.startswith("CSA") else 20)
            assert values["sample_as_printed"] == sample
            if oxide in errors:
                assert values["standard_deviation_wt_pct"] == errors[oxide]
            actual_full_composition = values["sample_composition_as_published"]
        assert actual_full_composition is not None
        assert actual_full_composition["SiO2"] == sio2
        assert actual_full_composition["Al2O3"] == al2o3
        assert actual_full_composition["CaO"] == cao
        if mgo == "—":
            assert actual_full_composition["MgO"] == "— (not reported)"
            condition = actual[(sample, "SiO2")]["point_conditions"]["composition"]["state"]
            assert condition["tag"] == "unknown"
            assert "MgO" in condition["reason"]
        else:
            assert actual_full_composition["MgO"] == mgo
    assert expected_count == 108


def assert_table4_matches(extract: dict, rendered: str) -> None:
    rows = [line.split("|") for line in rendered.splitlines()]
    actual = observations_by_table(extract, "4")
    assert len(actual) == 36
    for sample, x_sio2, x_na, x_k, x_rb, g_na, g_k, g_rb in rows:
        for oxide, x_component, gamma in zip(ALKALIS, (x_na, x_k, x_rb), (g_na, g_k, g_rb), strict=True):
            observation = actual[(sample, oxide)]
            values = observation["values"]
            assert values["gamma"] == gamma
            assert values["X_SiO2_as_printed"] == x_sio2
            assert values["X_Na2O_as_printed"] == x_na
            assert values["X_K2O_as_printed"] == x_k
            assert values["X_Rb2O_as_printed"] == x_rb
            assert values["component_mole_fraction_as_printed"] == x_component
            assert values["sample_as_printed"] == sample


def assert_table5_matches(extract: dict, rendered: str) -> None:
    rows = [line.split("|") for line in rendered.splitlines()]
    actual = observations_by_table(extract, "5")
    assert len(actual) == 9
    reference = next(
        row for row in extract["species"]["Na2O"]["context"]
        if row["observation_id"] == "borisov_2009_table5_csa6_reference_melt"
    )
    assert [reference["values"][key] for key in ("X_Al2O3", "X_Na2O", "X_K2O", "X_Rb2O", "gamma_Na2O", "gamma_K2O", "gamma_Rb2O", "Al_over_alkalis", "ASI")] == rows[0][1:]
    for sample, x_al, x_na, x_k, x_rb, g_na, g_k, g_rb, al_alk, asi in rows[1:]:
        for oxide, x_component, gamma in zip(ALKALIS, (x_na, x_k, x_rb), (g_na, g_k, g_rb), strict=True):
            observation = actual[(sample, oxide)]
            values = observation["values"]
            assert values["gamma"] == gamma
            assert values["X_Al2O3_as_printed"] == x_al
            assert values["X_Na2O_as_printed"] == x_na
            assert values["X_K2O_as_printed"] == x_k
            assert values["X_Rb2O_as_printed"] == x_rb
            assert values["component_mole_fraction_as_printed"] == x_component
            assert values["Al_over_alkalis_as_printed"] == al_alk
            assert values["ASI_as_printed"] == asi


def test_table2_source_glass_cells_and_run_binding_mutations() -> None:
    extract = document()
    assert_table2_matches(extract, TABLE2_RENDER)

    changed_render = deepcopy(TABLE2_RENDER)
    changed_render[0][5] = "999"
    with pytest.raises(AssertionError):
        assert_table2_matches(extract, changed_render)

    changed_extract = deepcopy(extract)
    changed_extract["species"]["SiO2"]["observations"][0]["values"]["value_as_published"] = "999"
    with pytest.raises(AssertionError):
        assert_table2_matches(changed_extract, TABLE2_RENDER)


def test_table3_wide_grid_unknown_dash_and_cell_mutations() -> None:
    extract = document()
    assert_table3_matches(extract, TABLE3_RENDER)

    changed_render = TABLE3_RENDER.splitlines()
    first = changed_render[0].split("|")
    first[1] = "999"
    changed_render[0] = "|".join(first)
    with pytest.raises(AssertionError):
        assert_table3_matches(extract, "\n".join(changed_render))

    changed_extract = deepcopy(extract)
    first_cell = next(
        row for row in changed_extract["species"]["SiO2"]["observations"]
        if row["locator"].get("table") == "3" and row["values"]["sample_as_printed"] == "CSA6RNK-38"
    )
    first_cell["values"]["value_as_published"] = "999"
    with pytest.raises(AssertionError):
        assert_table3_matches(changed_extract, TABLE3_RENDER)


def test_table4_activity_grid_binds_composition_temperature_and_mutations() -> None:
    extract = document()
    assert_table4_matches(extract, TABLE4_RENDER)

    changed_render = TABLE4_RENDER.splitlines()
    first = changed_render[1].split("|")
    first[5] = "999"
    changed_render[1] = "|".join(first)
    with pytest.raises(AssertionError):
        assert_table4_matches(extract, "\n".join(changed_render))

    changed_extract = deepcopy(extract)
    point = next(
        row for row in changed_extract["species"]["Na2O"]["observations"]
        if row["locator"].get("table") == "4" and row["values"]["sample_as_printed"] == "DAS50RNK-36"
    )
    point["values"]["gamma"] = "999"
    with pytest.raises(AssertionError):
        assert_table4_matches(changed_extract, TABLE4_RENDER)


def test_table5_activity_grid_and_reference_definition_mutations() -> None:
    extract = document()
    assert_table5_matches(extract, TABLE5_RENDER)

    changed_render = TABLE5_RENDER.splitlines()
    row = changed_render[-1].split("|")
    row[7] = "999"
    changed_render[-1] = "|".join(row)
    with pytest.raises(AssertionError):
        assert_table5_matches(extract, "\n".join(changed_render))

    changed_extract = deepcopy(extract)
    point = next(
        row for row in changed_extract["species"]["Rb2O"]["observations"]
        if row["locator"].get("table") == "5" and row["values"].get("sample_as_printed") == "CSA25"
    )
    point["values"]["gamma"] = "999"
    with pytest.raises(AssertionError):
        assert_table5_matches(changed_extract, TABLE5_RENDER)


def test_isolated_migration_species_temperature_conditions_and_reference_state() -> None:
    extract = document()
    source_rows = [
        row
        for container in extract["species"].values()
        for row in container.get("observations", [])
    ]
    assert len(source_rows) == 169
    assert all(row["values"]["T_C"] == row["T_C"] for row in source_rows)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    rows = tuple(migrator.result.observations.values())
    assert len(rows) == 169
    assert Counter(row.identity.quantity.value for row in rows) == Counter({
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 124,
        Quantity.ACTIVITY_COEFFICIENT: 45,
    })

    formula_counts = Counter()
    for row in rows:
        quantity = row.identity.quantity.value
        formula = row.identity.species.formula
        formula_counts[(quantity, formula)] += 1
        assert row.locator.page == row.locator.pdf_page_index + 1
        assert row.identity.species.phase.value.value == "l"
        assert row.identity.temperature_K is not None and row.identity.temperature_K.is_value

        conditions = row.point_conditions or {}
        for name in ("temperature_K", "fO2_log", "minus_log_fO2_as_printed", "fO2_control", "total_pressure_Pa", "duration_h", "composition", "quench_state"):
            assert name in conditions, (row.observation_id, name)
            assert conditions[name].locator is not None, (row.observation_id, name)
        assert conditions["temperature_K"].state.value == row.identity.temperature_K.value
        assert conditions["fO2_log"].state.is_value
        assert conditions["minus_log_fO2_as_printed"].state.is_value
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["composition"].state.is_value or conditions["composition"].state.is_unknown

        if quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
            assert row.identity.species.formula in OXIDES
        else:
            assert formula in ALKALIS
            assert row.identity.reference_state is not None
            assert row.identity.reference_state.is_unknown
            assert "reference phase is unknown" in row.identity.reference_state.reason
        assert conditions["quench_state"].state.value == "not_stated"

    expected = {
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "SiO2"): 20,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "Al2O3"): 16,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "MgO"): 12,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "CaO"): 16,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "Na2O"): 20,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "K2O"): 20,
        (Quantity.EQUILIBRIUM_PHASE_COMPOSITION, "Rb2O"): 20,
        (Quantity.ACTIVITY_COEFFICIENT, "Na2O"): 15,
        (Quantity.ACTIVITY_COEFFICIENT, "K2O"): 15,
        (Quantity.ACTIVITY_COEFFICIENT, "Rb2O"): 15,
    }
    assert formula_counts == Counter(expected)


def test_all_eleven_figures_have_metadata_and_no_numeric_points() -> None:
    extract = document()
    figures = [
        context
        for container in extract["species"].values()
        for context in container.get("context", [])
        if context.get("type") == "deferred_figure"
    ]
    assert len(figures) == 11
    assert {row["locator"]["figure"] for row in figures} == {str(number) for number in range(1, 12)}
    for row in figures:
        values = row["values"]
        assert values["quantity"]
        assert values["formula"]
        assert values["species"]
        assert values["phase"]
        assert values["T_C"] is not None
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["panels"]
        assert values["series"]
        assert values["marker_counts_per_series"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
