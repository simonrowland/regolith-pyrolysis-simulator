from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT
from simulator.battery.validate import validate_corpus


EXTRACT = REPO_ROOT / "data/literature/extracts/shornikov-2000-silica-gas-equilibria.yaml"


def _context_by_id(extract):
    return {
        item["observation_id"]: item
        for item in extract["species"]["SiO2"]["context"]
    }


def _observations_by_id(extract):
    return {
        item["observation_id"]: item
        for species in extract["species"].values()
        for item in species.get("observations", [])
    }


EXPECTED_RATIOS = {
    "shornikov_2000_table3_ratio_si_sio": ("I((Si)+)/I((SiO)+)", 3.03e-2),
    "shornikov_2000_table3_ratio_sio_sio": ("I((SiO)+)/I((SiO)+)", 1.0),
    "shornikov_2000_table3_ratio_sio2_sio": ("I((SiO2)+)/I((SiO)+)", 1.13e-3),
    "shornikov_2000_table3_ratio_si2o2_sio": ("I((Si2O2)+)/I((SiO)+)", 1.39e-2),
    "shornikov_2000_text_ratio_moo_moo2": ("I(MoO+)/I(MoO2+)", 0.04),
    "shornikov_2000_text_ratio_moo2_moo2": ("I(MoO2+)/I(MoO2+)", 1.0),
    "shornikov_2000_text_ratio_moo3_moo2": ("I(MoO3+)/I(MoO2+)", 0.58),
}


def test_all_six_tables_and_deferred_figure_are_retained_from_rendered_pages():
    extract = yaml.safe_load(EXTRACT.read_text())
    context = _context_by_id(extract)
    observations = _observations_by_id(extract)

    expected_table_rows = {
        "shornikov_2000_table1_reaction_1": 24,
        "shornikov_2000_table2_reaction_4": 23,
        "shornikov_2000_table4_appearance_potentials": 9,
        "shornikov_2000_table5_reaction_5": 5,
        "shornikov_2000_table6_reaction_17": 19,
    }
    for observation_id, expected_count in expected_table_rows.items():
        item = context[observation_id]
        assert len(item["values"]["rows"]) == expected_count
        assert item["locator"]["pdf_page_index"] >= 0

    table3 = context["shornikov_2000_table3_previous_mass_spectra"]
    assert table3["values"]["source_row_count"] == 10
    assert len(table3["values"]["rows"]) == 9
    table3_ids = [
        "shornikov_2000_table3_ratio_si_sio",
        "shornikov_2000_table3_ratio_sio_sio",
        "shornikov_2000_table3_ratio_sio2_sio",
        "shornikov_2000_table3_ratio_si2o2_sio",
    ]
    assert table3["values"]["this_work_observation_ids"] == table3_ids
    assert set(observations) == set(EXPECTED_RATIOS)
    for observation_id, (ion_pair, ratio) in EXPECTED_RATIOS.items():
        item = observations[observation_id]
        values = item["values"]
        assert item["type"] == "rate_series"
        assert item["phase"] == "gas"
        assert item["T_range_K"] == [1950.0, 1950.0]
        assert item["point_conditions"]["temperature_K"]["state"] == {
            "tag": "value",
            "value": "1950",
        }
        assert item["point_conditions"]["temperature_K"]["locator"]
        assert values["quantity"] == "ion_intensity_ratio"
        assert values["method_class"] == "measured_direct"
        assert values["ion_pair"] == ion_pair
        assert values["ion_intensity_ratio"] == ratio
        assert values["T_K"] == 1950.0
        assert "t-1329" in values["note"]
    assert observations[table3_ids[0]]["locator"]["published_page"] == 363
    assert observations[table3_ids[0]]["locator"]["pdf_page_index"] == 3

    figure_record = context["shornikov_2000_figure1_reaction5"]
    figure = figure_record["values"]
    assert figure_record["locator"]["published_page"] == 368
    assert figure_record["locator"]["pdf_page_index"] == 8
    assert figure["digitisation"] == "deferred to a figure pass"
    assert "points" not in figure
    assert [series["marker_count"] for series in figure["series"]] == [0, 1, 0, 0, 0, 0]


def test_extract_migrates_all_ratios_with_temperature_and_pending_subtype():
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
    migrated = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(migrated) == set(EXPECTED_RATIOS)
    for observation_id, (_ion_pair, ratio) in EXPECTED_RATIOS.items():
        observation = migrated[observation_id]
        assert float(observation.value.point) == ratio
        assert observation.identity.temperature_K.value == 1950.0
        assert observation.identity.subtype.tag.value == "unknown"
        assert observation.identity.subtype.reason == "no subtype mapped from source"
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == 1950.0
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index in (3,)
    assert len(result.works) == 1
