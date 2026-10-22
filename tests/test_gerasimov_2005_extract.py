from decimal import Decimal
from pathlib import Path

import yaml
from simulator.battery.migrate import Migrator


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/gerasimov-2005-laser-impact-melts.yaml"
)


def test_gerasimov_table_1_compositions_match_rendered_page_354():
    """Pin the Table 1 oxide rows to the page 4 render (printed p. 354)."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["silicate_melt"]["observations"]
    assert len(observations) == 2
    assert {row["values"]["table_row_count"] for row in observations} == {9}

    condensates = {
        row["values"]["sample_id"]: row["values"]["condensate_wt_percent"]
        for row in observations
    }
    assert condensates == {
        "augite": {
            "SiO2": 54.69,
            "TiO2": 0.74,
            "Al2O3": 7.57,
            "FeO": 8.11,
            "MgO": 12.06,
            "CaO": 12.33,
            "Na2O": 4.51,
        },
        "obsidian": {
            "SiO2": 62.16,
            "TiO2": 1.13,
            "Al2O3": 12.56,
            "FeO": 5.27,
            "MgO": 6.58,
            "CaO": 4.23,
            "Na2O": 7.25,
            "K2O": 1.08,
        },
    }
    starting = {row["values"]["sample_id"]: row["values"] for row in observations}
    assert starting["augite"]["composition_wt_pct"]["SiO2"] == 49.29
    assert starting["augite"]["starting_oxide_sigma_wt_percent"]["SiO2"] == 0.10
    assert starting["augite"]["starting_analyses_count"] == 18
    assert starting["obsidian"]["composition_wt_pct"]["K2O"] == 0.53
    assert starting["obsidian"]["starting_oxide_sigma_wt_percent"]["K2O"] == 0.04
    assert starting["obsidian"]["starting_analyses_count"] == 10
    assert observations[0]["values"]["censored_condensate_wt_percent"] == {"MnO": "<0.10"}
    assert "K2O" not in starting["augite"]["composition_wt_pct"]
    assert "MnO" not in starting["obsidian"]["composition_wt_pct"]


def test_gerasimov_printed_experiment_conditions_are_pinned():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["silicate_melt"]["observations"]
    assert [row["values"]["total_pressure_atm"] for row in observations] == [1, 1]
    for row in observations:
        pressure_locator = row["values"]["total_pressure_locator"]
        assert pressure_locator["page"] == 3
        assert pressure_locator["published_page"] == 353
        assert pressure_locator["note"] == "Atmosphere of helium kept at pressure of 1 atm."
        temperature = row["point_conditions"]["temperature_K"]
        assert "4000–5000 K" in temperature["state"]["reason"]
        assert temperature["locator"]["page"] == 3
        assert "total_pressure_Pa" not in row["point_conditions"]
        composition = row["point_conditions"]["composition"]["locator"]
        assert composition["column"] == f"{row['values']['sample_id'].title()} starting sample"

    contexts = document["species"]["silicate_melt"]["context"]
    ranges = {row["observation_id"]: row for row in contexts}
    assert ranges["gerasimov_2005_global_temperature_range"]["values"]["T_range_as_published"] == "3000–5000 K"
    estimate = ranges["gerasimov_2005_vaporization_temperature_estimate"]
    assert estimate["values"]["T_range_as_published"] == "4000–5000 K"
    assert estimate["values"]["note"] == "The temperature of vaporization was estimated as 4000–5000 K."
    assert estimate["locator"]["page"] == 3
    assert ranges["gerasimov_2005_helium_atmosphere_pressure"]["values"]["total_pressure_atm"] == 1

    serialized = EXTRACT.read_text(encoding="utf-8")
    assert "only a global 3000–5000 K range is stated" not in serialized
    assert "pressure is not stated for the Table 1 rows" not in serialized

    migrator = Migrator(root=EXTRACT.parents[3], index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {
        observation.observation_id.split("::")[-1]: observation
        for observation in migrator.result.observations.values()
    }
    for row in observations:
        observation = migrated[row["observation_id"]]
        condition_note = observation.point_conditions["composition"].locator.note
        assert f"{row['values']['sample_id'].title()} starting sample column" in condition_note
        assert f"column={row['values']['sample_id'].title()} condensate" in condition_note
        pressure = observation.identity.total_pressure_Pa
        assert pressure.is_value
        assert pressure.value == Decimal("101325")
        assert observation.identity.temperature_K.is_unknown
