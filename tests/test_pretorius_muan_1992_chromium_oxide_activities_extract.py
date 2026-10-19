from copy import deepcopy
from pathlib import Path

import yaml

from literature_extract_assertions import assert_all_physical_locators
from simulator.battery.enums import Phase
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document, species, observation_id):
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def test_activity_tables_and_appendix_compositions_are_typed_and_located():
    document = _document()
    assert_all_physical_locators(document)

    context = document["species"]["CrO"]["context"]
    table_ii = next(row for row in context if row["observation_id"].endswith("table_2_context"))
    table_iii = next(row for row in context if row["observation_id"].endswith("table_3_context"))
    assert len(table_ii["values"]["printed_rows"]) == 32
    assert len(table_iii["values"]["printed_rows"]) == 21
    assert table_ii["locator"] == {
        "page": 3,
        "published_page": 1366,
        "pdf_page_index": 2,
        "table": "II",
    }
    assert table_iii["locator"]["page"] == 7
    assert table_iii["locator"]["published_page"] == 1370

    observations = document["species"]["CrO"]["observations"]
    assert len(observations) == 386
    activity = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_activity",
    )
    mole_fraction = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_mole_fraction",
    )
    gamma = _observation(
        document,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma",
    )
    assert activity["values"]["activity"] == "0.245"
    assert activity["T_C"] == "1500"
    assert activity["point_conditions"]["fO2_log"]["state"]["value"] == "-9.56"
    assert mole_fraction["values"]["value"] == "0.0126"
    assert mole_fraction["values"]["value_as_published"] == "1.26"
    assert gamma["values"]["gamma"] == "19.44"
    assert gamma["values"]["method_class"] == "measured_reduced"
    assert gamma["derived_from"] == [activity["observation_id"], mole_fraction["observation_id"]]
    assert gamma["locator"]["page"] == 3
    assert gamma["locator"]["published_page"] == 1366

    table_iv = next(
        row for row in observations if row["observation_id"].endswith("tIV_cacrs_4_cro_alloy")
    )
    assert table_iv["values"]["method_class"] == "model_derived"
    assert table_iv["locator"]["page"] == 8
    assert table_iv["locator"]["published_page"] == 1371

    appendix = [
        row
        for species in document["species"].values()
        for row in species["observations"]
        if row["locator"].get("appendix") in {"Appendix A", "Appendix C"}
    ]
    assert len(appendix) == 285
    appendix_table_by_page = {
        10: r"Appendix A EMPA of Equilibrium Phases in the System CaO-CrO $_{x}$ -SiO $_{2}$ \*",
        11: "Appendix A (continued)",
        12: r"Appendix C EMPA of Equilibrium Phases in the System $\mathrm{CaO - Al_2O_3 - CrO_x - SiO_2^*}$",
        13: "Appendix C (continued)",
    }
    assert all(
        row["locator"]["table"] == appendix_table_by_page[row["locator"]["page"]]
        for row in appendix
    )
    glass = next(
        row
        for row in appendix
        if row["locator"].get("appendix") == "Appendix C"
        and row["locator"].get("row", "").startswith("CACrS 4, Glass, SiO2")
    )
    assert glass["values"]["value_as_published"] == "54.24(0.18)"
    assert glass["uncertainty"]["type"] == "standard_deviation"


def test_isolated_migration_has_no_hard_issues_and_reaches_temperature_and_fugacity():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 704
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(
        observation.identity.temperature_K is not None
        and observation.identity.temperature_K.is_value
        for observation in result.observations.values()
    )
    assert all(
        "fO2_log" in (observation.point_conditions or {})
        for observation in result.observations.values()
    )
    metal = result.observations[
        "pretorius-muan-1992-chromium-oxide-activities::"
        "pretorius_muan_1992_chromium_oxide_activities_appendixa_ccrs_12_metal_cr"
    ]
    assert metal.identity.species.phase.is_unknown
    assert metal.identity.species.phase.reason == (
        "phase string 'Metal' is not in the closed automatic map"
    )
    prefix = "pretorius-muan-1992-chromium-oxide-activities::pretorius_muan_1992_chromium_oxide_activities_"
    formulas = {
        row.identity.species.formula for row in result.observations.values()
    }
    assert formulas >= {"CrO", "CrO1.5"}
    by_formula = {}
    for row in result.observations.values():
        by_formula.setdefault(row.identity.species.formula, 0)
        by_formula[row.identity.species.formula] += 1
    assert by_formula["CrO1.5"] == 193
    assert by_formula["CrO"] == 193
    expected_formula = {
        "tii_r01_cro1_5_activity": "CrO1.5",
        "tii_r01_cro1_5_mole_fraction": "CrO1.5",
        "tii_r01_cro1_5_gamma": "CrO1.5",
        "tii_r01_cro_activity": "CrO",
        "tii_r01_cro_mole_fraction": "CrO",
        "tii_r01_cro_gamma": "CrO",
        "tiii_r01_cro1_5_activity": "CrO1.5",
        "tiii_r01_cro_activity": "CrO",
        "tIV_cacrs_4_cro1_5_alloy": "CrO1.5",
        "tIV_cacrs_4_cro_alloy": "CrO",
    }
    for suffix, formula in expected_formula.items():
        assert result.observations[prefix + suffix].identity.species.formula == formula
    sesquioxide = result.observations[
        "pretorius-muan-1992-chromium-oxide-activities::"
        "pretorius_muan_1992_chromium_oxide_activities_appendixc_cacrs_4_ses_al2o3"
    ]
    assert sesquioxide.identity.species.phase.value is Phase.CR


def test_fidelity_pin_detects_a_mutated_gamma_cell():
    document = _document()
    assert check_all_fidelity_samples_match(document) == []

    mutated = deepcopy(document)
    gamma = _observation(
        mutated,
        "CrO",
        "pretorius_muan_1992_chromium_oxide_activities_tii_r01_cro1_5_gamma",
    )
    gamma["values"]["gamma"] = "19.45"
    assert check_all_fidelity_samples_match(mutated)


def test_all_fourteen_figures_have_deferred_inventory_and_physical_locators():
    document = _document()
    figures = [
        row
        for row in document["species"]["CrO"]["context"]
        if row.get("type") == "figure_only"
    ]

    assert len(figures) == 14
    assert {row["locator"]["figure"] for row in figures} == {
        str(number) for number in range(1, 15)
    }
    for figure in figures:
        values = figure["values"]
        assert values["typed_quantity"]
        assert values["species"]
        assert values["phase"]
        assert values["x_axis_as_printed"]
        assert values["y_axis_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["series"]
        assert all(isinstance(series["marker_count"], int) for series in values["series"])
