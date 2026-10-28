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
EXPECTED_PREVIOUS_TABLE3_RATIOS = {
    "shornikov_2000_table3_prev_r01_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[3]", 1463.0, "Al2O3", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r01_si2o2_sio": (
        "Si2O2", "I((Si2O2)+)/I((SiO)+)", 7.83e-4, "[3]", 1463.0, "Al2O3", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r02_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[4]", 1467.0, "ZrO2", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r02_si2o2_sio": (
        "Si2O2", "I((Si2O2)+)/I((SiO)+)", 1.11e-4, "[4]", 1467.0, "ZrO2", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r03_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[27]", 1538.0, "Mo", "Si–Li2O"
    ),
    "shornikov_2000_table3_prev_r03_sio2_sio": (
        "SiO2", "I((SiO2)+)/I((SiO)+)", 1.88e-5, "[27]", 1538.0, "Mo", "Si–Li2O"
    ),
    "shornikov_2000_table3_prev_r03_si2o2_sio": (
        "Si2O2", "I((Si2O2)+)/I((SiO)+)", 3.44e-4, "[27]", 1538.0, "Mo", "Si–Li2O"
    ),
    "shornikov_2000_table3_prev_r04_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[41]", 1548.0, "Mo", "Si–Ge–SiO2"
    ),
    "shornikov_2000_table3_prev_r04_si_sio": (
        "Si", "I((Si)+)/I((SiO)+)", 2.29e-4, "[41]", 1548.0, "Mo", "Si–Ge–SiO2"
    ),
    "shornikov_2000_table3_prev_r05_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[17,18]", 1550.0, "C", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r05_si_sio": (
        "Si", "I((Si)+)/I((SiO)+)", 1.50e-1, "[17,18]", 1550.0, "C", "Si–SiO2"
    ),
    "shornikov_2000_table3_prev_r06_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[19]", 1550.0, "Si3N4", "Si–Si2N2O–Si3N4"
    ),
    "shornikov_2000_table3_prev_r06_si_sio": (
        "Si", "I((Si)+)/I((SiO)+)", 1.50e-1, "[19]", 1550.0, "Si3N4", "Si–Si2N2O–Si3N4"
    ),
    "shornikov_2000_table3_prev_r07_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[39]", 1560.0, "Mo", "Si–Ge–SiO2"
    ),
    "shornikov_2000_table3_prev_r07_si_sio": (
        "Si", "I((Si)+)/I((SiO)+)", 3.19e-2, "[39]", 1560.0, "Mo", "Si–Ge–SiO2"
    ),
    "shornikov_2000_table3_prev_r08_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[3]", 1900.0, "Al2O3", "SiO2"
    ),
    "shornikov_2000_table3_prev_r08_sio2_sio": (
        "SiO2", "I((SiO2)+)/I((SiO)+)", 4.44e-3, "[3]", 1900.0, "Al2O3", "SiO2"
    ),
    "shornikov_2000_table3_prev_r09_sio_sio": (
        "SiO", "I((SiO)+)/I((SiO)+)", 1.0, "[5]", 1950.0, "Pt", "SiO2"
    ),
    "shornikov_2000_table3_prev_r09_sio2_sio": (
        "SiO2", "I((SiO2)+)/I((SiO)+)", 6.32e-2, "[5]", 1950.0, "Pt", "SiO2"
    ),
}


def test_all_six_tables_and_deferred_figure_are_retained_from_rendered_pages():
    extract = yaml.safe_load(EXTRACT.read_text())
    assert "70(3), 360-370" in extract["source"]["citation"]
    assert "original pages 386-398" in extract["source"]["citation"]
    fidelity = extract["fidelity_samples"]
    assert [sample["path"] for sample in fidelity] == [
        "species.SiO2.observations[shornikov_2000_table3_ratio_sio2_sio].values.ion_intensity_ratio",
        "species.Si2O2.observations[shornikov_2000_table3_prev_r01_si2o2_sio].values.ion_intensity_ratio",
        "species.MoO.observations[shornikov_2000_text_ratio_moo_moo2].values.ion_intensity_ratio",
    ]
    assert [sample["value"] for sample in fidelity] == [1.13e-3, 7.83e-4, 0.04]
    assert fidelity[0]["locator"] == {
        "table": "3",
        "row": "Mo cell, 1950 K, this work",
        "page": 4,
        "published_page": 363,
        "pdf_page_index": 3,
    }
    assert fidelity[1]["path"].endswith(
        "shornikov_2000_table3_prev_r01_si2o2_sio].values.ion_intensity_ratio"
    )
    assert fidelity[1]["value"] == 7.83e-4
    assert fidelity[1]["locator"]["page"] == 4
    assert fidelity[1]["locator"]["pdf_page_index"] == 3
    assert fidelity[1]["locator"] == {
        "table": "3",
        "row": "Al2O3 cell, Si–SiO2 system, 1463 K",
        "page": 4,
        "published_page": 363,
        "pdf_page_index": 3,
    }
    assert fidelity[2]["locator"] == {
        "published_page": 363,
        "pdf_page_index": 3,
        "section": "mass spectra discussion",
        "paragraph": "ion-current ratios for molybdenum oxide species",
    }
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
    assert all(
        set(row["qualitative_cells_as_printed"].values()) <= {"—", "+"}
        for row in table3["values"]["rows"]
    )
    table3_ids = [
        "shornikov_2000_table3_ratio_si_sio",
        "shornikov_2000_table3_ratio_sio_sio",
        "shornikov_2000_table3_ratio_sio2_sio",
        "shornikov_2000_table3_ratio_si2o2_sio",
    ]
    assert table3["values"]["this_work_observation_ids"] == table3_ids
    assert set(observations) == set(EXPECTED_RATIOS) | set(EXPECTED_PREVIOUS_TABLE3_RATIOS)
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
    for observation_id, expected in EXPECTED_PREVIOUS_TABLE3_RATIOS.items():
        (
            species,
            ion_pair,
            ratio,
            attribution,
            temperature,
            cell_material,
            system,
        ) = expected
        item = observations[observation_id]
        values = item["values"]
        assert item["type"] == "rate_series"
        assert item["locator"]["page"] == 4
        assert item["locator"]["pdf_page_index"] == 3
        assert item["locator"]["table"] == "3"
        assert item["point_conditions"]["temperature_K"]["state"] == {
            "tag": "value",
            "value": str(int(temperature)),
        }
        assert item["point_conditions"]["temperature_K"]["locator"]["page"] == 4
        assert values["quantity"] == "ion_intensity_ratio"
        assert values["method_class"] == "quoted_attributed"
        assert values["evidence_class"] == "quoted_attributed"
        assert values["attribution"] == attribution
        assert values["ion_pair"] == ion_pair
        assert values["ion_intensity_ratio"] == ratio
        assert values["T_K"] == temperature
        assert values["cell_material_as_printed"] == cell_material
        assert values["system_as_printed"] == system
        assert any(
            candidate["observation_id"] == observation_id
            for candidate in extract["species"][species]["observations"]
        )

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
    assert set(migrated) == set(EXPECTED_RATIOS) | set(EXPECTED_PREVIOUS_TABLE3_RATIOS)
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
    for observation_id, (
        _species,
        _ion_pair,
        ratio,
        _attribution,
        temperature,
        _cell,
        _system,
    ) in EXPECTED_PREVIOUS_TABLE3_RATIOS.items():
        observation = migrated[observation_id]
        assert float(observation.value.point) == ratio
        assert observation.identity.temperature_K.value == temperature
        assert observation.identity.subtype.tag.value == "unknown"
        assert observation.identity.subtype.reason == "no subtype mapped from source"
        located_temperature = observation.point_conditions["temperature_K"]
        assert located_temperature.state.value == temperature
        assert located_temperature.locator is not None
        assert located_temperature.locator.page == 4
        assert located_temperature.locator.pdf_page_index == 3
    assert len(result.works) == 1
