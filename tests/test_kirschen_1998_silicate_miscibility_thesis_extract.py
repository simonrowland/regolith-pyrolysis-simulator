"""Migration pins for Kirschen (1998), thesis tables II-1 and III-1."""

from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kirschen-1998-silicate-miscibility-thesis"
ARTICLE_ID = "kirschen-decapitani-1999-liquid-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
ARTICLE_EXTRACT = ROOT / f"data/literature/extracts/{ARTICLE_ID}.yaml"


def _migrated_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert not result.queue

    raw_rows = {
        f"{SOURCE_ID}::{row['observation_id']}": (formula, row)
        for formula, species in document["species"].items()
        for row in species.get("observations", [])
    }
    assert len(raw_rows) == len(result.observations) == 64
    for observation_id, observation in result.observations.items():
        formula, raw = raw_rows[observation_id]
        values = raw["values"]
        assert observation.value.kind.value == "point"
        amount = values["component_amount"]
        if isinstance(amount, dict):
            assert Decimal(str(observation.value.point)) == Decimal(str(amount["value"]))
            assert amount["sigma"] not in (None, "", "0", "0.0")
            assert Decimal(str(raw["uncertainty"]["sigma"])) == Decimal(
                str(amount["sigma"])
            )
        else:
            assert Decimal(str(observation.value.point)) == Decimal(str(amount))
        assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
        assert observation.identity.species.formula == values.get("formula", formula)
        assert observation.identity.species.phase.value.value == "glass"
        assert observation.identity.subtype.value == "wt_percent"
        assert not observation.notices

        temperature_key = "T_K" if "T_K" in values else "T_C"
        temperature = values[temperature_key]
        expected_temperature = Decimal(str(temperature["value"]))
        if temperature_key == "T_C":
            expected_temperature += Decimal("273.15")
        identity_temperature = observation.identity.temperature_K
        condition_temperature = observation.point_conditions["temperature_K"]
        assert identity_temperature.value == expected_temperature
        assert condition_temperature.state.value == expected_temperature
        assert condition_temperature.locator is not None
        assert condition_temperature.locator.page == raw["locator"]["page"]
        assert condition_temperature.locator.pdf_page_index == raw["locator"]["page"] - 1

        source_conditions = values.get("point_conditions", {})
        if "gas_mix" in source_conditions:
            assert observation.point_conditions["gas_mix"].state.value == source_conditions[
                "gas_mix"
            ]["state"]["value"]
        else:
            assert "gas_mix" not in observation.point_conditions

        assert observation.experiment_id.endswith(
            f"::experiment::{raw['experiment']}"
        )
        assert observation.locator.page == raw["locator"]["page"]
        assert observation.locator.pdf_page_index == raw["locator"]["page"] - 1
    assert len(result.experiments) == len(document["experiments"])
    return document, result


def test_remaining_unique_component_rows_migrate_with_identity_and_temperature() -> None:
    _migrated_rows()


def test_journal_repeats_are_linked_context_and_stmc_grid_matches_render() -> None:
    document, _ = _migrated_rows()
    linked = [
        context
        for context in document["species"]["SiO2"]["context"]
        if context.get("type") == "duplicate_measurement_context"
        and context.get("values", {}).get("published_source_id") == ARTICLE_ID
    ]
    linked_ids = {
        observation_id
        for context in linked
        for observation_id in context["values"]["published_observation_ids"]
    }
    article = yaml.safe_load(ARTICLE_EXTRACT.read_text(encoding="utf-8"))
    article_ids = {
        row["observation_id"]
        for row in article["species"]["SiO2"]["observations"]
        if row["values"].get("component_amount")
    }
    assert linked_ids == article_ids
    assert len(linked_ids) == 466

    contexts = {
        (row["values"]["sample_id_as_printed"], row["values"]["phase_order"]): row
        for row in linked
    }
    # Independently transcribed from the 600 dpi render of thesis PDF p. 47.
    grid = {
        ("STMC 4-1", 1): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("10.38", "0.86"),
            "TiO2": ("88.50", "1.33"),
        },
        ("STMC 4-1", 2): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("55.57", "1.13"),
            "TiO2": ("30.74", "0.53"),
        },
        ("STMC 4-2", 1): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("88.50", "1.33"),
            "TiO2": ("10.38", "0.86"),
        },
        ("STMC 4-2", 2): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("30.74", "0.53"),
            "TiO2": ("55.57", "1.13"),
        },
    }
    for key, components in grid.items():
        values = contexts[key]["values"]
        assert values["composition_wt_percent_as_printed"] == {
            formula: amount for formula, (amount, _sigma) in components.items()
        }
        assert values["one_sigma_wt_percent_as_printed"] == {
            formula: sigma for formula, (_amount, sigma) in components.items()
        }
        assert values["published_source_id"] == ARTICLE_ID
        assert len(values["published_observation_ids"]) == 4


def test_table_iii_footnote_sample_and_oxygen_condition_migrate() -> None:
    document, result = _migrated_rows()
    observation = result.observations[
        f"{SOURCE_ID}::kirschen_1998_iii_1_sta2_2h_phase1_sio2"
    ]
    source_row = next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == "kirschen_1998_iii_1_sta2_2h_phase1_sio2"
    )
    assert source_row["values"]["sample_id_as_printed"] == "STA2-2H1"
    assert source_row["values"]["run_note_as_printed"] == "1"
    assert source_row["values"]["system_as_printed"] == "SiO2-TiO2-Al2O3"
    assert observation.identity.temperature_K.value == Decimal("2050")
    assert observation.point_conditions["gas_mix"].state.value == "pure oxygen"
    assert observation.point_conditions["temperature_K"].locator.page == 89
