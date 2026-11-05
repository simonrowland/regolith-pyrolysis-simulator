from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import FO2Channel, Phase, ValueKind
from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
SID = "dohmen-1998-olivine-fe-gas"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"

# Independent transcription from the 600-dpi render of PDF page 4, Table 1.
TABLE1_RENDER_TRANSCRIPTION = (
    ("V892", "Fe + Fo100 (synthetic, polycrystalline)", "—", "Fa6Fo94; homogeneous grains"),
    ("V893", "Fo100 (synthetic, polycrystalline)", "—", "Fo100; single phase"),
    ("V914", "Fe (synthetic, polycrystalline)", "Fo100 (synthetic, polycrystalline)", "Fa2Fo98-Fa4Fo96; individual grains unzoned"),
    ("V929", "Fe + wustite (synthetic, polycrystalline)", "Fo100 (synthetic, polycrystalline)", "Fa26Fo74; individual grains unzoned†"),
    ("V932", "Fe + wustite (synthetic, polycrystalline)", "San Carlos olivine (single crystal)", "Fayalitic films†"),
    ("V933", "Fe (synthetic, polycrystalline)", "Fo90 + Fo100 (synthetic single crystals)", "Fayalitic films"),
    ("V937", "Fe + magnetite (synthetic, polycrystalline)", "Fo100 (synthetic, polycrystalline)", "Molten sample: Fa74Fo26†"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1_grid(document: dict) -> tuple[tuple[str, ...], ...]:
    record = next(
        item
        for item in document["species"]["Fe"]["context"]
        if item.get("type") == "table_record" and item["locator"].get("table") == "1"
    )
    fields = ("run", "position_A", "position_B", "product")
    return tuple(
        tuple(str(row.get(field, "")) for field in fields)
        for row in record["values"]["rows"]
    )


def _assert_table_matches_render(document: dict) -> None:
    assert _table1_grid(document) == TABLE1_RENDER_TRANSCRIPTION


def _migrate(document: dict):
    migrator = Migrator(root=ROOT, index={SID: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_table1_rows_and_products_match_rendered_page_four() -> None:
    document = _document()
    _assert_table_matches_render(document)
    assert len(_table1_grid(document)) == 7
    assert tuple(row[0] for row in _table1_grid(document) if "†" in row[3]) == (
        "V929",
        "V932",
        "V937",
    )

    changed_extract = deepcopy(document)
    record = next(
        item
        for item in changed_extract["species"]["Fe"]["context"]
        if item.get("type") == "table_record" and item["locator"].get("table") == "1"
    )
    record["values"]["rows"][2]["product"] = "Fa3Fo97"
    with pytest.raises(AssertionError):
        _assert_table_matches_render(changed_extract)


def test_isolated_migration_preserves_known_phases_temperatures_and_run_conditions() -> None:
    result = _migrate(_document())
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.works) == 1
    assert len(result.observations) == 5
    assert len(result.experiments) == 10
    assert sum(len(rows) for rows in result.context_by_work.values()) == 5
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()) == 5

    unknown_quantity_ids = {
        local_id
        for full_id, row in result.observations.items()
        if row.identity.quantity.is_unknown
        for local_id in (full_id.split("::", 1)[1],)
    }
    assert unknown_quantity_ids == {
        "dohmen_1998_table1_v892_product",
        "dohmen_1998_table1_v893_product",
        "dohmen_1998_table1_v914_product",
        "dohmen_1998_table1_v929_product",
        "dohmen_1998_table1_v937_product",
    }
    unknown_phase_ids = {
        local_id
        for full_id, row in result.observations.items()
        if row.identity.species.phase.is_unknown
        for local_id in (full_id.split("::", 1)[1],)
    }
    assert unknown_phase_ids == {"dohmen_1998_table1_v937_product"}
    source_rows = {
        row["observation_id"]: row
        for row in _document()["species"]["Fe"]["observations"]
    }

    expected_temperature = {
        "dohmen_1998_table1_v892_product": Decimal("1673.15"),
        "dohmen_1998_table1_v893_product": Decimal("1773.15"),
        "dohmen_1998_table1_v914_product": Decimal("1673.15"),
        "dohmen_1998_table1_v929_product": Decimal("1673.15"),
        "dohmen_1998_table1_v937_product": Decimal("1673.15"),
    }
    for full_id, row in result.observations.items():
        local_id = full_id.split("::", 1)[1]
        if local_id == "dohmen_1998_table1_v937_product":
            assert row.identity.species.phase.is_unknown
        else:
            assert row.identity.species.phase.is_value
            assert row.identity.species.phase.value is Phase.CR
        assert row.identity.composition is None or row.identity.composition.is_unknown
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        if local_id == "dohmen_1998_table1_v929_product":
            assert row.identity.fO2_Pa.is_value
            assert row.identity.fO2_Pa.value == Decimal("0.00002")
        else:
            assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == expected_temperature[local_id]
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value.point == expected_temperature[local_id]
        assert temperature.locator is not None
        assert temperature.locator.page == 4
        assert temperature.locator.table == "1"
        assert "total_pressure_Pa" not in row.point_conditions
        source_conditions = source_rows[local_id]["values"]["point_conditions"]
        sample_pressure = source_conditions["total_pressure_Pa"]
        assert sample_pressure["state"]["tag"] == "unknown"
        assert "locator" in sample_pressure
        reported_oxygen = source_conditions["fO2_Pa"]
        assert "locator" in reported_oxygen
        point_oxygen = row.point_conditions["fO2_Pa"]
        assert point_oxygen.locator is not None
        if local_id == "dohmen_1998_table1_v929_product":
            assert reported_oxygen["state"]["tag"] == "value"
            assert reported_oxygen["state"]["value"]["point"] == "0.00002"
            assert reported_oxygen["state"]["value"]["approximate"] is True
            assert point_oxygen.state.value.point == Decimal("0.00002")
        else:
            assert reported_oxygen["state"]["tag"] == "unknown"
            assert point_oxygen.state.is_unknown
        experiment = result.experiments[row.experiment_id]
        if local_id == "dohmen_1998_table1_v937_product":
            assert experiment.sample.form is not None
            assert experiment.sample.form.state.is_value
            assert experiment.sample.form.state.value == "Molten sample"
            assert experiment.sample.form.locator is not None
            assert experiment.sample.form.locator.page == 4
            assert experiment.sample.form.locator.table == "1"
        else:
            assert experiment.sample.form is None
        pressure = experiment.pressure_environment.total_pressure_Pa
        assert pressure.state.is_value
        assert pressure.state.value.kind is ValueKind.INTERVAL
        assert pressure.state.value.interval_low == Decimal("0.00001")
        assert pressure.state.value.interval_high == Decimal("0.001")

        oxygen = experiment.conditions["fO2_Pa"]
        if local_id == "dohmen_1998_table1_v929_product":
            assert oxygen.state.is_value
            assert oxygen.state.value.point == Decimal("0.00002")
            assert oxygen.state.value.approximate
        else:
            assert oxygen.state.is_unknown

    ramp_counts = {
        "V892": 2,
        "V893": 1,
        "V914": 2,
        "V929": 2,
        "V932": 1,
        "V933": 1,
        "V937": 1,
    }
    for run, expected_count in ramp_counts.items():
        experiment = next(
            experiment
            for experiment in result.experiments.values()
            if experiment.experiment_id.endswith(f"kems-{run.lower()}")
        )
        assert experiment.thermal_schedule is not None
        ramps = experiment.thermal_schedule.ramps
        assert ramps is not None
        assert len(ramps) == expected_count
        for ramp in ramps:
            assert ramp.rate_K_s.state.is_value
            assert ramp.rate_K_s.state.value.point == Decimal("0.083333333333333333")
            assert ramp.rate_K_s.locator is not None
            assert ramp.rate_K_s.locator.page == 4
            assert ramp.rate_K_s.locator.table == "1"
            assert ramp.end_temperature_K is not None
            assert ramp.end_temperature_K.state.is_value
            expected_ramp_temperature = (
                Decimal("1773.15") if run == "V893" else Decimal("1673.15")
            )
            assert ramp.end_temperature_K.state.value.point == expected_ramp_temperature

    v932 = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("kems-v932")
    )
    assert v932.conditions["fO2_Pa"].state.is_unknown
    assert v932.conditions["fO2_Pa"].locator is not None
    assert v932.conditions["fO2_Pa"].locator.page == 4
    assert v932.fO2_control is not None
    assert v932.fO2_control.channel.value is FO2Channel.BUFFER
    assert v932.fO2_control.buffer is not None
    assert v932.fO2_control.buffer.state.value == "iron-wustite buffer (Fe + wustite)"
    powder = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("1atm-1600c-metal-olivine-test")
    )
    assert powder.pressure_environment.total_pressure_Pa.state.is_unknown
    assert powder.pressure_environment.total_pressure_Pa.locator is not None
    assert powder.pressure_environment.total_pressure_Pa.locator.page == 3

    # The 1400 C iron-wustite oxygen pressure is a printed buffer condition,
    # not an O2 KMS result. The per-run experiment carries it with its locator.
    v929 = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("kems-v929")
    )
    oxygen = v929.conditions["fO2_Pa"]
    assert oxygen.locator is not None
    assert oxygen.locator.page == 6

    # The 1 atm gas-mixing run states its gas species, but not their ratio.
    diffusion = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("1atm-diffusion-couple")
    )
    assert diffusion.pressure_environment.total_pressure_Pa.state.value.point == Decimal("101325")
    assert diffusion.fO2_control.gas_ratio.value.state.is_unknown

    migrated_ni = next(
        record
        for rows in result.context_by_work.values()
        for record in rows
        if str(record.get("context_id", "")).endswith(
            "::context::dohmen_1998_text_v932_nickel_content"
        )
    )
    assert migrated_ni["species"] == "Ni"
    assert migrated_ni["locator"]["page"] == 8
    assert migrated_ni["units"] == "mass ppm"
    assert migrated_ni["values"]["basis"] == "ppm_mass"
    assert migrated_ni["values"]["original_crystal_as_published"] == "approximately 3000 ppm"
    assert migrated_ni["values"]["film_as_published"] == "<100 ppm"


def test_figure_values_are_deferred_and_table_v937_is_preserved_verbatim() -> None:
    document = _document()
    grid = _table1_grid(document)
    assert grid[-1] == TABLE1_RENDER_TRANSCRIPTION[-1]
    observation = next(
        item
        for item in document["species"]["Fe"]["observations"]
        if item["observation_id"] == "dohmen_1998_table1_v937_product"
    )
    assert observation["phase"] == "unknown"
    assert observation["values"]["composition_as_published"] == "Fa74Fo26"
    experiment = next(
        item for item in document["experiments"]
        if item["experiment_id"] == "dohmen-1998-kems-v937"
    )
    assert experiment["sample"]["form"]["state"]["value"] == "Molten sample"
    prose = next(
        item
        for item in document["source_caveats"]
        if item.get("quote") == "V937 (Fa = 70 mol%)"
    )
    assert prose["locator"]["page"] == 6
    assert "round" not in prose["note"].casefold()
    caveats_by_quote = {
        item.get("quote"): item for item in document["source_caveats"]
    }
    expected_quotes = {
        "Oriented single crystals of olivines (forsterite, Fo100, or San Carlos olivine, Fo92), polished on one side, were used to study the exact mechanism of the reaction.": 4,
        "The crystals used were either pieces of synthetic forsterite (white in color, Fo100) or San Carlos olivine (pale green, Fo90-92).": 7,
        "In one case (V932), two single crystals of both compositions were placed next to, but not touching, each other.": 7,
        "BSE of forsterite (a) and San Carlos olivine (both experiment V933) (c) with thick film of overgrown Fe-rich olivine; (b) and (d): enlargement of fayalite-rich regions in (a) and (c), respectively.": 7,
        "This is seen clearly in experiment V932 where two single crystals of olivine (Fo100 and Fo92) were used simultaneously.": 10,
    }
    for quote, page in expected_quotes.items():
        assert caveats_by_quote[quote]["locator"]["page"] == page
    dark_glass = next(
        item
        for item in document["source_caveats"]
        if "dark glass" in item.get("quote", "")
    )
    assert dark_glass["locator"]["page"] == 7
    assert "experiment V933" in dark_glass["note"]
    v933_glass = next(
        item
        for item in document["source_caveats"]
        if "sections from experiment V933" in item.get("quote", "")
    )
    assert v933_glass["locator"]["page"] == 8

    nickel = next(
        item
        for item in document["species"]["Ni"]["context"]
        if item["observation_id"] == "dohmen_1998_text_v932_nickel_content"
    )
    assert nickel["units"] == "mass ppm"
    assert nickel["values"]["basis"] == "ppm_mass"
    assert nickel["values"]["original_crystal_as_published"] == "approximately 3000 ppm"
    assert nickel["values"]["film_as_published"] == "<100 ppm"
    assert nickel["locator"]["page"] == 8
    film = next(
        item
        for item in document["species"]["Fe"]["context"]
        if item.get("observation_id")
        == "dohmen_1998_text_fe_rich_olivine_film_thickness"
    )
    assert film["values"]["maximum_thickness_as_printed"] == "up to 300 μm"
    assert film["locator"]["page"] == 7

    figure_index = next(
        item
        for item in document["species"]["Fe"]["context"]
        if item.get("type") == "figure_index"
    )
    assert {str(row["figure"]) for row in figure_index["values"]["figures"]} == {
        str(number) for number in range(1, 13)
    }
    assert "points" not in figure_index["values"]
    assert figure_index["values"]["digitisation"] == "deferred per extraction instructions"
