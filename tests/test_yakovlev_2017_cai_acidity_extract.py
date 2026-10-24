import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/yakovlev-2017-cai-acidity.yaml"


def test_all_figures_retain_printed_identity_attribution_and_deferred_status():
    extract = yaml.safe_load(EXTRACT.read_text())
    assert extract["source"]["doi"] == "10.1134/S0016702917020070"
    assert extract["species"]["SiO2"]["observations"] == []
    context = {
        item["observation_id"]: item
        for item in extract["species"]["SiO2"]["context"]
    }
    expected_figures = {
        "yakovlev_2017_figure1_major_oxide_vapor_pressures",
        "yakovlev_2017_figure2_krymka_partial_pressures",
        "yakovlev_2017_figure3_krymka_residual_composition",
        "yakovlev_2017_figure4_efremovka_partial_pressures",
        "yakovlev_2017_figure5_cao_mgo_sio2_activities",
        "yakovlev_2017_figure6_al2o3_activities",
        "yakovlev_2017_figure7_cai_bulk_composition_ratio",
    }
    figures = {
        key: value for key, value in context.items() if value["type"] == "figure_record"
    }
    assert set(figures) == expected_figures
    for figure in figures.values():
        assert figure["locator"]["page"] == figure["locator"]["pdf_page_index"] + 1
        assert figure["panel"]
        assert figure["attribution"]
        assert figure["values"]["species"]
        assert figure["values"]["phase"]
        assert figure["values"]["temperature_as_printed"]
        assert figure["values"]["axes_as_printed"]
        assert figure["values"]["caption"]
        assert figure["values"]["series"]
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert "points" not in figure["values"]
        assert all("marker" in series and "marker_count" in series for series in figure["values"]["series"])

    figure1 = figures["yakovlev_2017_figure1_major_oxide_vapor_pressures"]
    assert figure1["locator"] == {
        "page": 2,
        "published_page": 252,
        "pdf_page_index": 1,
        "figure": "1",
    }
    assert figure1["values"]["quantity"] == "p_sat"
    assert [series["name"] for series in figure1["values"]["series"]] == [
        "K2O",
        "Na2O",
        "FeO",
        "SiO2",
        "MgO",
        "CaO",
        "TiO2",
        "Al2O3",
    ]
    assert all(series["marker_count"] == 0 for series in figure1["values"]["series"])

    figure3 = figures["yakovlev_2017_figure3_krymka_residual_composition"]
    assert figure3["values"]["quantity"] == "residue_component_composition"
    assert figure3["attribution"] == "Yakovlev et al. (1987), as printed in the caption"
    assert [series["name"] for series in figure3["values"]["series"]] == [
        "MgO",
        "SiO2",
        "CaO",
        "Al2O3",
    ]

    figure5 = figures["yakovlev_2017_figure5_cao_mgo_sio2_activities"]
    assert [series["evidence_class"] for series in figure5["values"]["series"]] == [
        "quoted_attributed",
        "model_derived",
        "quoted_attributed",
        "model_derived",
    ]
    assert all(
        "p_i° as the partial pressure of pure component i" in series["standard_state_as_printed"]
        for series in figure5["values"]["series"]
    )

    figure6 = figures["yakovlev_2017_figure6_al2o3_activities"]
    assert figure6["values"]["system_as_captioned"] == "CaO–MgO–SiO2 ternary system, as printed in the Fig. 6 caption"
    assert figure6["values"]["system_as_discussed"] == "MgO–Al2O3–SiO2 ternary system, as printed in the discussion on page 5"
    assert figure6["values"]["discussion_locator"] == {
        "page": 5,
        "published_page": 255,
        "pdf_page_index": 4,
        "section": "Discussion",
    }

    figure7 = figures["yakovlev_2017_figure7_cai_bulk_composition_ratio"]
    assert figure7["values"]["quantity"] == "unavailable"
    assert "No existing quantity token" in figure7["values"]["schema_gap"]
    assert figure7["values"]["correlation_coefficient_as_printed"] == "r = 0.8"
    assert figure7["values"]["series"][0]["evidence_class"] == "quoted_attributed"
    assert figure7["values"]["series"][0]["attribution"].startswith("Grossman et al. (2000)")

    loss_limits = context["yakovlev_2017_quoted_cai_element_loss_limits"]["values"]
    assert loss_limits["evidence_class"] == "quoted_attributed"
    assert [item["value_as_printed"] for item in loss_limits["upper_limits_as_printed"]] == [
        "up to ~50%",
        "up to ~25%",
    ]
    assert all(item["approximate"] for item in loss_limits["upper_limits_as_printed"])


def test_context_only_extract_migrates_with_no_typed_observations():
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
    assert result.observations == {}
    # No rows are typed while the plotted values are deferred, so species-formula
    # identity checks are vacuous for this context-only extract.
    for observation in result.observations.values():
        assert observation.identity.species.formula
