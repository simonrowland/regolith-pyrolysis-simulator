"""Render-pinned context and isolated migration for Witthohn (2000)."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/witthohn-2000-coal-slag-alkali-vaporisation.yaml"

# Table 1 transcribed independently from its 600 dpi physical-page-15 render.
TABLE1_RENDER = """
Sample | A | B | C | D | E | F | G
Temperature 1) [°C] | 450 | 450 | 450 | 550 | 575 | ≈1500 | ≈1400
Ash content 2) | ad 9.6 | ad 12.6 | ad 9.9 | ad 6.2 | ad 8.4 | – | –
SiO2 2) | 42.4 | 46.7 | 36.4 | 12.7 | 38.3 | 42.0 | 40.9
Al2O3 | 2.8 | 3.1 | 4.2 | 2.1 | 18.3 | 24.3 | 24.3
Fe2O3 | 8.9 | 7.0 | 10.2 | 11.1 | 11.4 | 14.9 | 11.2
CaO | 19.3 | 15.5 | 17.4 | 38.1 | 8.5 | 9.4 | 9.7
MgO | 3.0 | 4.3 | 7.6 | 3.5 | 2.8 | 2.6 | 3.0
Na2O | <0.1 | 0.5 | 1.5 | 0.3 | 1.1 | 1.2 | 1.5
K2O | 0.3 | 0.4 | 0.5 | 0.1 | 1.7 | 1.7 | 1.7
SO3 | 12.5 | 15.0 | 11.6 | 14.4 | 9.2 | <0.1 | <0.1
P2O5 | <0.1 | <0.1 | <0.1 | 0.1 | 0.2 | 0.3 | 0.3
CO2 | 5.9 | 3.0 | 5.1 | – (3; not analyzed) | 12.5 | <0.1 | <0.1
Cl | 0.3 | 0.1 | 0.3 | 0.5 | <0.1 | – | –
Rest 4) | 1.8 | 0.8 | 1.2 | 0.5 | 2.8 | 1.4 | 2.1
Sum | 97.2 | 96.4 | 96.0 | 83.4 | 106.8 | 97.8 | 94.7
""".strip()


def document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def table1_context(extract: dict) -> dict:
    return next(
        row
        for container in extract["species"].values()
        for row in container.get("context", [])
        if row["observation_id"] == "witthohn_2000_table1_bulk_coal_ash_and_slag_compositions"
    )


def assert_table1_matches(extract: dict, rendered: str) -> None:
    expected_rows = [line.split(" | ") for line in rendered.splitlines()]
    actual_rows = [line.split(" | ") for line in table1_context(extract)["values"]["rows_as_printed"].splitlines()]
    assert len(expected_rows) == 16  # header plus the 15 printed data rows
    assert all(len(row) == 8 for row in expected_rows)
    assert actual_rows == expected_rows


def test_table1_grid_is_bound_to_render_and_both_mutations_fail() -> None:
    extract = document()
    assert_table1_matches(extract, TABLE1_RENDER)

    changed_render = TABLE1_RENDER.replace("SiO2 2) | 42.4", "SiO2 2) | 99.4", 1)
    with pytest.raises(AssertionError):
        assert_table1_matches(extract, changed_render)

    changed_extract = deepcopy(extract)
    table1_context(changed_extract)["values"]["rows_as_printed"] = TABLE1_RENDER.replace(
        "SiO2 2) | 42.4", "SiO2 2) | 99.4", 1
    )
    with pytest.raises(AssertionError):
        assert_table1_matches(changed_extract, TABLE1_RENDER)


def test_deferred_figures_keep_identity_series_and_no_numeric_points() -> None:
    extract = document()
    records = [
        (formula, row)
        for formula, container in extract["species"].items()
        for row in container.get("context", [])
        if row.get("type") == "figure"
    ]
    assert len(records) == 10
    assert Counter(str(row["locator"]["figure"]) for _, row in records) == Counter(
        {"1": 1, "2": 4, "3": 2, "4": 1, "5": 1, "6": 1}
    )

    expected_formula = {"Na": "Na", "NaCl": "NaCl", "K": "K", "KCl": "KCl", "Na2O": "Na2O", "K2O": "K2O"}
    for formula, row in records:
        values = row["values"]
        assert values["quantity"] == "figure_record"
        assert values["identity"]["phase"]
        assert values["identity"]["temperature"]
        assert values["axes_as_printed"]
        assert values["caption"]
        assert values["series"]
        assert values["points"] == []
        assert values["digitisation"] == "deferred to a figure pass"
        assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1
        assert all("marker_count" in series for series in values["series"])
        if str(row["locator"]["figure"]) == "1":
            assert values["identity"]["species"].startswith("not applicable")
            assert values["identity"]["phase"].startswith("not applicable")
        else:
            identity_species = values["identity"]["species"]
            assert identity_species.removesuffix("(g)") == expected_formula[formula]
            assert values["identity"]["formula"] == expected_formula[formula]
        if values.get("typed_quantity_as_printed") == "activity":
            assert all(series.get("standard_state_as_printed") for series in values["series"])
            assert values["standard_state_locator"]["page"] == 9
            assert values["standard_state_locator"]["page"] == values["standard_state_locator"]["pdf_page_index"] + 1
            if formula == "Na2O":
                inference = values["standard_state_inference"]
                phase_by_temperature = inference["phase_by_series_temperature_K"]
                assert inference["inferred"] is True
                assert "NIST-JANAF Fourth Edition" in inference["basis"]
                if str(row["locator"]["figure"]) == "4":
                    assert phase_by_temperature == {
                        "1273": "Na2O alpha crystal",
                        "1282": "Na2O alpha crystal",
                        "1373": "Na2O alpha crystal",
                        "1473": "Na2O liquid",
                        "1573": "Na2O liquid",
                        "1673": "Na2O liquid",
                    }
                    assert set(inference["near_transition_series_temperature_K"]) == {
                        "1273", "1282", "1373", "1473"
                    }
                else:
                    assert phase_by_temperature == {"1343": "Na2O alpha crystal"}
                    assert inference["near_transition_series_temperature_K"] == ["1343"]
                for series in values["series"]:
                    match = re.search(r"(\d{4}) K", series["name"])
                    temperature = match.group(1) if match else values["identity"]["temperature"].split()[0]
                    assert temperature in phase_by_temperature
            elif formula == "K2O":
                inference = values["standard_state_inference"]
                assert inference["inferred"] is False
                assert inference["reference_phase"] == "unknown"
                assert "uncertain K2O fusion estimates" in inference["basis"]
            else:
                assert all(
                    "phase of the pure-oxide reference is not specified" in series["standard_state_as_printed"]
                    for series in values["series"]
                )


def test_isolated_migration_has_no_scored_observations_or_hard_issues() -> None:
    extract = document()
    assert sum(
        len(container.get("observations", [])) for container in extract["species"].values()
    ) == 7
    assert table1_context(extract)["values"]["method_class"] == "measured_tabulated"
    assert extract["species"]["Na2O"]["context"][1]["values"][
        "section_heading_locator"
    ] == {"page": 4, "section": "Experimental"}

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    rows = {
        row.observation_id.split("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    assert len(rows) == 7
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in rows.values())
    assert all(row.admission.status is AdmissionStatus.REJECTED for row in rows.values())

    pressure_ids = {
        "witthohn_2000_na_g_fig2_figure_only",
        "witthohn_2000_na_g_fig3_figure_only",
        "witthohn_2000_k_g_fig2_figure_only",
        "witthohn_2000_k_g_fig3_figure_only",
    }
    activity_ids = {
        "witthohn_2000_na2o_fig4_figure_only",
        "witthohn_2000_na2o_fig5_figure_only",
        "witthohn_2000_k2o_fig6_model_figure_only",
    }
    assert {key for key, row in rows.items() if row.identity.quantity.value.value == "p_partial"} == pressure_ids
    assert {key for key, row in rows.items() if row.identity.quantity.value.value == "activity"} == activity_ids
    assert all(
        rows[key].evidence.class_.value.value == "measured_reduced"
        for key in pressure_ids
    )
    assert all(
        rows[key].evidence.class_.value.value == "quoted_attributed"
        for key in activity_ids
    )
    assert all(rows[key].identity.species.phase.value.value == "l" for key in activity_ids)

    expected_locators = {
        "witthohn_2000_na_g_fig2_figure_only": (16, "2"),
        "witthohn_2000_k_g_fig2_figure_only": (16, "2"),
        "witthohn_2000_na_g_fig3_figure_only": (17, "3"),
        "witthohn_2000_k_g_fig3_figure_only": (17, "3"),
        "witthohn_2000_na2o_fig4_figure_only": (17, "4"),
        "witthohn_2000_na2o_fig5_figure_only": (18, "5"),
        "witthohn_2000_k2o_fig6_model_figure_only": (18, "6"),
    }
    for observation_id, (page, figure) in expected_locators.items():
        row = rows[observation_id]
        assert row.locator.page == page
        assert row.locator.figure == figure
        temperature = row.point_conditions["temperature_K"]
        composition = row.point_conditions["composition"]
        assert temperature.locator.page == page
        assert temperature.locator.figure == figure
        assert composition.state.is_unknown
        assert composition.locator.page == page
        assert composition.locator.figure == figure
        if observation_id == "witthohn_2000_na2o_fig5_figure_only":
            assert temperature.state.is_value
            assert str(temperature.state.value) == "1343"
        else:
            assert temperature.state.is_unknown

    contexts = {
        row["observation_id"]: row
        for context_rows in migrator.result.context_by_work.values()
        for row in context_rows
    }
    assert len(contexts) == 19  # 12 green context rows plus seven sample rows
    samples = {
        observation_id: row
        for observation_id, row in contexts.items()
        if observation_id.startswith("witthohn_2000_table1_sample_")
    }
    assert len(samples) == 7
    assert all(
        row["values"]["method_class"] == "measured_tabulated"
        for row in samples.values()
    )
    assert samples["witthohn_2000_table1_sample_A_composition"]["values"][
        "SiO2_mass_fraction_percent"
    ] == "42.4"
    assert samples["witthohn_2000_table1_sample_C_composition"]["values"][
        "K2O_mass_fraction_percent"
    ] == "0.5"
    assert samples["witthohn_2000_table1_sample_G_composition"]["values"][
        "CaO_mass_fraction_percent"
    ] == "9.7"
    assert samples["witthohn_2000_table1_sample_D_composition"]["values"][
        "CO2_mass_fraction_percent"
    ] == "not analyzed"
