"""Migration pins for Kirschen and DeCapitani (1999), Tables 1 and 2."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import wt_pct_to_mole_fraction
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "kirschen-decapitani-1999-liquid-miscibility"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"
SINGLE_LIQUID_COMPOSITIONS = {
    "kirschen_decapitani_1999_t2_stc_16_1h": [
        ("CaO", "10.58"),
        ("SiO2", "35.94"),
        ("TiO2", "54.55"),
    ],
    "kirschen_decapitani_1999_t2_stm_7_2h": [
        ("MgO", "9.93"),
        ("SiO2", "39.76"),
        ("TiO2", "50.55"),
    ],
    "kirschen_decapitani_1999_t2_stmc_4_1h": [
        ("CaO", "5.91"),
        ("MgO", "3.86"),
        ("SiO2", "45.30"),
        ("TiO2", "45.56"),
    ],
    "kirschen_decapitani_1999_t2_stmc_5_2h": [
        ("CaO", "6.35"),
        ("MgO", "4.31"),
        ("SiO2", "33.52"),
        ("TiO2", "55.70"),
    ],
}
SINGLE_LIQUID_ROW_IDS = {
    f"{experiment_id}_phase1_{formula.lower()}"
    for experiment_id in SINGLE_LIQUID_COMPOSITIONS
    for formula in ("CaO", "MgO", "SiO2", "TiO2")
}


def _rows_by_id(document: dict, *, include_context: bool = False) -> dict[str, dict]:
    sections = ("observations", "context") if include_context else ("observations",)
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for section in sections
        for row in species.get(section, [])
        if "observation_id" in row
    }


def _assert_single_liquid_run_compositions(document: dict) -> None:
    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    observations = _rows_by_id(document)
    all_rows = _rows_by_id(document, include_context=True)
    moved = {row_id: all_rows[row_id] for row_id in SINGLE_LIQUID_ROW_IDS if row_id in all_rows}
    assert set(moved) == SINGLE_LIQUID_ROW_IDS
    assert not (SINGLE_LIQUID_ROW_IDS & set(observations))

    for experiment_id, expected in SINGLE_LIQUID_COMPOSITIONS.items():
        experiment = experiments[experiment_id]
        components = experiment["sample"]["initial_composition"]["state"]["value"]["components"]
        assert components == [list(component) for component in expected]
        run_rows = [row for row in moved.values() if row["experiment"] == experiment_id]
        assert len(run_rows) == 4
        assert all(row["type"] == "phase_composition_context" for row in run_rows)
        assert all(row["values"]["phase_assemblage_as_printed"] == "One liquid" for row in run_rows)
        numeric = {
            row["values"]["formula"]: Decimal(row["values"]["component_amount"]["value"])
            for row in run_rows
            if row["values"].get("status") != "typed_refusal"
        }
        assert numeric == {formula: Decimal(amount) for formula, amount in expected}
        refused = [row for row in run_rows if row["values"].get("status") == "typed_refusal"]
        assert len(refused) == (0 if len(expected) == 4 else 1)
        assert all(row["values"]["printed_cell_as_published"] == "..." for row in refused)


def _migrated_rows():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    raw_rows = {
        f"{SOURCE_ID}::{row['observation_id']}": (formula, row)
        for formula, species in document["species"].items()
        for row in species.get("observations", [])
    }
    assert len(raw_rows) == len(result.observations) == 560
    refused_ids = {
        observation_id
        for observation_id, (_formula, row) in raw_rows.items()
        if row["values"].get("status") == "typed_refusal"
    }
    queued_ids = {entry.observation_id for entry in result.queue}
    assert queued_ids == refused_ids
    assert all(entry.axes == ["value"] for entry in result.queue)
    assert all("no mapped equilibrium_phase_composition field selected" in entry.why for entry in result.queue)
    point_count = unavailable_count = 0
    for observation_id, observation in result.observations.items():
        formula, raw = raw_rows[observation_id]
        values = raw["values"]
        assert observation.identity.quantity.value.value == "equilibrium_phase_composition"
        assert observation.identity.species.formula == values["formula"]
        assert observation.identity.species.phase.value.value == "glass"
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value is not None
        assert observation.point_conditions["temperature_K"].state.value == observation.identity.temperature_K.value
        assert observation.point_conditions["temperature_K"].locator.page == raw["locator"]["page"]
        assert observation.point_conditions["temperature_K"].locator.pdf_page_index == raw["locator"]["page"] - 1
        assert observation.point_conditions["gas_mix"].state.value == "air"
        assert observation.point_conditions["gas_mix"].locator.page == 1
        assert values["pressure_as_printed"] == "ambient pressure"
        assert values["pressure_locator"]["page"] == 1
        assert "no numeric total pressure" in values["condition_note"]
        assert values["sample_id_as_printed"]
        assert values["system_as_printed"]
        assert values["assemblage_id"]
        assert values["phase_assemblage_as_printed"]
        assert values["basis"] == "wt_percent"

        if values.get("status") == "typed_refusal":
            unavailable_count += 1
            assert values["admission_status"] == "typed_refusal"
            assert "ellipsis" in values["reason"]
            assert values["printed_cell_as_published"] == "..."
            assert observation.value.kind.value == "unavailable"
        else:
            point_count += 1
            amount = values["component_amount"]
            assert observation.value.kind.value == "point"
            assert Decimal(str(observation.value.point)) == Decimal(str(amount["value"]))
            assert amount["sigma"] not in (None, "", "0", "0.0")
            assert Decimal(str(raw["uncertainty"]["sigma"])) == Decimal(str(amount["sigma"]))
            assert {notice.kind.value for notice in observation.notices} == {
                "phase_inferred"
            }
    assert point_count == 452
    assert unavailable_count == 108
    assert len(result.experiments) == len(document["experiments"]) == 74
    _assert_single_liquid_run_compositions(document)
    work_id = result.aliases[SOURCE_ID]
    for experiment_id, expected in SINGLE_LIQUID_COMPOSITIONS.items():
        migrated = result.experiments[f"{work_id}::experiment::{experiment_id}"]
        initial = migrated.sample.initial_composition
        assert initial is not None and initial.state.is_value
        assert initial.state.value.components == wt_pct_to_mole_fraction(
            {formula: Decimal(amount) for formula, amount in expected}
        ).components
        assert initial.locator is not None
        assert initial.locator.page == 15
        assert initial.locator.pdf_page_index == 14
        assert initial.locator.published_page == "598"
    return document, result


def test_single_liquid_compositions_move_to_run_conditions_and_reject_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_single_liquid_run_compositions(document)

    changed_run = deepcopy(document)
    sample = next(
        row["sample"]
        for row in changed_run["experiments"]
        if row["experiment_id"] == "kirschen_decapitani_1999_t2_stc_16_1h"
    )
    sample["initial_composition"]["state"]["value"]["components"][0][1] = "10.59"
    with pytest.raises(AssertionError):
        _assert_single_liquid_run_compositions(changed_run)

    changed_analysis = deepcopy(document)
    rows = _rows_by_id(changed_analysis, include_context=True)
    rows["kirschen_decapitani_1999_t2_stc_16_1h_phase1_cao"]["values"][
        "component_amount"
    ]["value"] = "10.59"
    with pytest.raises(AssertionError):
        _assert_single_liquid_run_compositions(changed_analysis)


def test_all_table_cells_migrate_with_species_temperature_and_atmosphere() -> None:
    _migrated_rows()


def test_measured_glass_rows_bind_their_printed_phase_compositions() -> None:
    document, result = _migrated_rows()
    source_rows = _rows_by_id(document)
    all_source_rows = _rows_by_id(document, include_context=True)
    measured_count = 0
    for observation_id, raw in source_rows.items():
        values = raw["values"]
        if values.get("status") == "typed_refusal":
            continue
        measured_count += 1
        printed = raw["point_conditions"]["composition"]
        assert printed["state"]["tag"] == "value", observation_id
        payload = printed["state"]["value"]
        assert payload["basis"] == "wt_percent", observation_id
        assert payload["amount_basis"] == "mass_percent", observation_id

        observation = result.observations[f"{SOURCE_ID}::{observation_id}"]
        assert observation.identity.species.formula == values["formula"]
        assert observation.identity.composition.is_value, observation_id
        condition = observation.point_conditions["composition"]
        assert condition.state.is_value, observation_id
        assert condition.locator.page == raw["locator"]["page"]
        assert condition.locator.table == raw["locator"]["table"]
    assert measured_count == 452

    # Independently transcribed from Table 1 (PDF p. 9) and Table 2 (PDF p. 15).
    expected = {
        "kirschen_decapitani_1999_t1_stc_10_1_phase1_cao": [
            ["CaO", "0.26"],
            ["SiO2", "95.91"],
            ["TiO2", "5.57"],
        ],
        "kirschen_decapitani_1999_t1_stc_10_1_phase2_cao": [
            ["CaO", "18.84"],
            ["SiO2", "32.44"],
            ["TiO2", "50.43"],
        ],
        "kirschen_decapitani_1999_t2_scm_3_1h_phase1_cao": [
            ["CaO", "2.96"],
            ["MgO", "1.07"],
            ["SiO2", "96.27"],
        ],
        "kirschen_decapitani_1999_t2_stmc_4_1h_phase1_cao": [
            ["CaO", "5.91"],
            ["MgO", "3.86"],
            ["SiO2", "45.30"],
            ["TiO2", "45.56"],
        ],
    }
    for observation_id, components in expected.items():
        raw = all_source_rows[observation_id]
        point = raw["point_conditions"]["composition"]
        assert point["state"]["value"]["components"] == components


def test_render_transcribed_stmc_grids_and_printed_precision() -> None:
    document, result = _migrated_rows()
    source_rows = _rows_by_id(document, include_context=True)
    # Independently transcribed from the 600 dpi article renders: Table 1, PDF p. 14;
    # Table 2, PDF p. 15. Values include printed one-sigma uncertainties.
    grid = {
        (1, "STMC 4-1", 1, 1610): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("10.38", "0.86"),
            "TiO2": ("88.50", "1.33"),
        },
        (1, "STMC 4-1", 2, 1610): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("55.57", "1.13"),
            "TiO2": ("30.74", "0.53"),
        },
        (1, "STMC 4-2", 1, 1551): {
            "CaO": ("0.18", "0.04"),
            "MgO": ("0.16", "0.03"),
            "SiO2": ("88.50", "1.33"),
            "TiO2": ("10.38", "0.86"),
        },
        (1, "STMC 4-2", 2, 1551): {
            "CaO": ("8.38", "0.34"),
            "MgO": ("5.74", "0.25"),
            "SiO2": ("30.74", "0.53"),
            "TiO2": ("55.57", "1.13"),
        },
        (2, "STMC 4-1H", 1, 1890): {
            "CaO": ("5.91", "0.12"),
            "MgO": ("3.86", "0.09"),
            "SiO2": ("45.30", "0.40"),
            "TiO2": ("45.56", "0.52"),
        },
    }
    for (table, sample, phase, temperature), components in grid.items():
        page = 14 if table == 1 else 15
        for formula, (amount, sigma) in components.items():
            row_id = f"kirschen_decapitani_1999_t{table}_{sample.lower().replace(' ', '_').replace('-', '_')}_phase{phase}_{formula.lower()}"
            row = source_rows[row_id]
            assert row["values"]["component_amount"] == {"value": amount, "sigma": sigma}
            assert row["values"]["formula"] == formula
            assert row["locator"]["page"] == page
            assert row["locator"]["pdf_page_index"] == page - 1
            if row_id in SINGLE_LIQUID_ROW_IDS:
                assert row["type"] == "phase_composition_context"
                assert row_id not in result.observations
            else:
                observation = result.observations[f"{SOURCE_ID}::{row_id}"]
                assert observation.value.point == Decimal(amount)
                assert Decimal(str(observation.identity.temperature_K.value)) == Decimal(temperature) + Decimal("273.15")
                assert observation.locator.page == page
                assert observation.locator.pdf_page_index == page - 1
                assert observation.identity.species.formula == formula
                assert observation.point_conditions["gas_mix"].state.value == "air"
    # Preserve the scan's two-decimal precision even though the value equals 0.4.
    laser_sigma = source_rows[
        "kirschen_decapitani_1999_t2_stmc_4_1h_phase1_sio2"
    ]["values"]["component_amount"]["sigma"]
    assert laser_sigma == "0.40"


def test_unavailable_cells_are_only_the_printed_ellipses() -> None:
    document, result = _migrated_rows()
    unavailable = [
        row
        for row in _rows_by_id(document, include_context=True).values()
        if row.get("values", {}).get("status") == "typed_refusal"
    ]
    assert len(unavailable) == 110
    assert all(row["values"]["printed_cell_as_published"] == "..." for row in unavailable)
    for row in unavailable:
        observation_id = row["observation_id"]
        if observation_id in SINGLE_LIQUID_ROW_IDS:
            assert row["type"] == "phase_composition_context"
            assert f"{SOURCE_ID}::{observation_id}" not in result.observations
        else:
            assert result.observations[f"{SOURCE_ID}::{observation_id}"].value.kind.value == "unavailable"
