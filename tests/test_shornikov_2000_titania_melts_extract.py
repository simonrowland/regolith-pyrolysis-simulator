import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/shornikov-2000-titania-melts.yaml"


def test_figure_records_preserve_panels_standards_and_digitisation_status():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = {
        item["observation_id"]: item
        for item in extract["species"]["SiO2"]["context"]
    }
    expected = {
        "shornikov_2000_figure1_kems_vapor_pressures",
        "shornikov_2000_figure2a_casitio2_phase_diagram",
        "shornikov_2000_figure2b_casial2o4_phase_diagram",
        "shornikov_2000_figure2c_casitio2_oxide_activities",
        "shornikov_2000_figure2d_casial2o4_oxide_activities",
        "shornikov_2000_figure2e_casitio2_compound_activities",
        "shornikov_2000_figure2f_casial2o4_compound_activities",
        "shornikov_2000_figure3_normalized_formation_gibbs_energy",
    }
    figures = {key: value for key, value in context.items() if value["type"] == "figure_record"}
    assert set(figures) == expected

    digitisation = {
        "shornikov_2000_figure1_kems_vapor_pressures": "130 markers stored as figure_only partial_pressure observations (SiO 25, Ca 32, TiO 21, TiO2 30, O 22).",
        "shornikov_2000_figure2a_casitio2_phase_diagram": "continuous liquidus and solidus; no filled marker paths; not sampled.",
        "shornikov_2000_figure2b_casial2o4_phase_diagram": "continuous liquidus and solidus; no filled marker paths; not sampled.",
        "shornikov_2000_figure2c_casitio2_oxide_activities": "92 markers stored as figure_only activity observations (SiO2 25, CaO 27, TiO2 40).",
        "shornikov_2000_figure2d_casial2o4_oxide_activities": "23 markers stored as figure_only activity observations (SiO2 7, CaO 10, Al2O3 6).",
        "shornikov_2000_figure2e_casitio2_compound_activities": "42 markers stored as figure_only activity observations (CaSiO3 21, CaTiO3 21). Two legend glyphs beside numerals 1 and 2 are refused in the audit.",
        "shornikov_2000_figure2f_casial2o4_compound_activities": "14 markers stored as figure_only activity observations (CaSiO3 5, CaAl2O4 5, Ca2Al2SiO8 4).",
        "shornikov_2000_figure3_normalized_formation_gibbs_energy": "30 data markers read in the audit sidecar (series 1, 6 circles; series 2, 24 squares). Not scored: the ordinate is ΔfG/RT. Two legend glyphs refused.",
    }
    for key, item in figures.items():
        assert item["locator"]["page"] == item["locator"]["pdf_page_index"] + 1
        assert item["panel"]
        assert item["values"]["species"]
        assert item["values"]["phase"]
        assert item["values"]["temperature_as_printed"]
        assert item["values"]["axes_as_printed"]
        assert item["values"]["caption"]
        assert item["values"]["digitisation"] == digitisation[key]
        assert "points" not in item["values"]
        assert item["values"]["series"]
        assert all("marker_count" in series for series in item["values"]["series"])

    figure1 = figures["shornikov_2000_figure1_kems_vapor_pressures"]
    assert figure1["locator"] == {
        "page": 2,
        "published_page": 1470,
        "pdf_page_index": 1,
        "figure": "1",
    }
    assert figure1["values"]["KEMS_calibration_standards_as_printed"] == (
        "Partial pressures of silver [28] and (SiO) over silica [23, 24] were used as standards."
    )
    assert [series["marker"] for series in figure1["values"]["series"]] == [
        "filled circle",
        "filled square",
        "filled upward triangle",
        "filled downward triangle",
        "filled diamond",
    ]
    assert [series["evidence_class"] for series in figure1["values"]["series"]] == [
        "measured_reduced",
        "measured_reduced",
        "measured_reduced",
        "measured_reduced",
        "model_derived",
    ]

    oxide_activities = figures["shornikov_2000_figure2c_casitio2_oxide_activities"]
    assert [series["standard_state_as_printed"] for series in oxide_activities["values"]["series"]] == [
        "Partial vapor pressure of the same vapor species over individual SiO2; physical phase of the pure-oxide reference is not stated.",
        "Partial vapor pressure of the same vapor species over individual CaO; physical phase of the pure-oxide reference is not stated.",
        "Partial vapor pressure of the same vapor species over individual TiO2; physical phase of the pure-oxide reference is not stated.",
    ]
    assert all(
        series["standard_state_locator"]["page"]
        == series["standard_state_locator"]["pdf_page_index"] + 1
        == 2
        for series in oxide_activities["values"]["series"]
    )
    compound_activities = figures["shornikov_2000_figure2e_casitio2_compound_activities"]
    assert all(
        series["standard_state_locator"]["page"]
        == series["standard_state_locator"]["pdf_page_index"] + 1
        == 4
        for series in compound_activities["values"]["series"]
    )
    for panel in ("c", "d", "e", "f"):
        record = next(item for item in figures.values() if item["panel"] == panel)
        assert all(series["marker_count"] > 0 for series in record["values"]["series"])

    model_anchor = context["shornikov_2000_model_activity_integration_limit"]
    assert model_anchor["values"]["evidence_class"] == "model_derived"
    assert model_anchor["values"]["value_as_printed"] == "31.98 ± 0.15 mole % TiO2 at 2100 K"
    assert model_anchor["values"]["composition_as_printed"]["other_components"]["state"] == "unknown"
    quoted_anchor = context["shornikov_2000_quoted_perovskite_liquidus_coordinate"]
    assert quoted_anchor["values"]["evidence_class"] == "quoted_attributed"
    assert quoted_anchor["attribution"] == "[2]"
    assert quoted_anchor["values"]["value_as_printed"] == "31.92 mole % TiO2"


def test_figure_extract_migrates_figure_only_observations():
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    report = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert report.hard_issues == ()
    assert len(result.works) == 1
    assert len(result.observations) == 301
    for observation in result.observations.values():
        assert observation.identity.species.formula
