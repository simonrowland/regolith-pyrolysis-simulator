from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    source_files_for,
    wt_pct_to_mole_fraction,
)
from tests.literature_extract_test_helpers import (
    assert_no_unavailable_observation_values,
    iter_locators,
)


SID = "neuman-2026-levitation-k-isotopes"
EXTRACT = REPO_ROOT / f"data/literature/extracts/{SID}.yaml"
CORPUS_ROOT = Path(
    os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus")
)
PDF = CORPUS_ROOT / "raw" / SID / f"{SID}.pdf"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate_literature_extracts as validator  # noqa: E402


# Independent transcription of the run rows from 600 dpi renders of physical
# PDF pages 3–4 and 6–7. Each tuple joins printed conditions and measurements
# to the same sample id across the main tables.
TABLE_RENDER = (
    {
        "table": "1",
        "sample": "3–5",
        "temperature_C": "2000",
        "duration_s": "100",
        "fO2_target": "IW − 1",
        "gas_mix_1_pct": "1.53",
        "gas_mix_2_pct": "98.47",
        "flow_rate_cm3_min": "374",
        "delta41K_permil": "6.41",
        "n": "8",
        "two_se_permil": "0.10",
        "Delta41K_permil": "6.89",
        "K2O_wt_pct": "0.484",
        "Cu_ppm": "0.105",
    },
    {
        "table": "2",
        "sample": "5–10",
        "temperature_C": "2046",
        "duration_s": "100",
        "fO2_target": "IW − 1",
        "gas_mix_1_pct": "1.53",
        "gas_mix_2_pct": "98.47",
        "flow_rate_cm3_min": "375",
        "delta41K_permil": "2.21",
        "n": "9",
        "two_se_permil": "0.05",
        "Delta41K_permil": "2.66",
        "K2O_wt_pct": "2.85",
        "Cu_ppm": "0.415",
    },
)

# Starting fused-bead averages transcribed from Tables 1 and 2 in the PDF.
STARTING_COMPOSITIONS = {
    "basalt": {
        "SiO2": "44.1",
        "TiO2": "1.92",
        "Cr2O3": "0.066",
        "Al2O3": "19.7",
        "FeO": "10.5",
        "MnO": "0.227",
        "MgO": "9.43",
        "CaO": "9.83",
        "Na2O": "2.47",
        "K2O": "1.72",
    },
    "loess": {
        "SiO2": "69.3",
        "TiO2": "0.906",
        "Cr2O3": "0.017",
        "Al2O3": "14.7",
        "FeO": "5.28",
        "MnO": "0.149",
        "MgO": "2.92",
        "CaO": "1.95",
        "Na2O": "1.24",
        "K2O": "3.62",
    },
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (formula, observation)
        for formula, block in document["species"].items()
        for observation in block.get("observations", [])
    }


def _series_row(document: dict, observation_id: str, sample: str) -> dict:
    observation = _source_observations(document)[observation_id][1]
    return next(
        point
        for point in observation["values"]["series"]
        if point.get("sample_id") == sample
    )


def _mass_loss_point(document: dict, material: str, sample: str) -> dict:
    observation = _source_observations(document)[
        f"neuman_2026_supplementary_table_s1_{material}_mass_loss_fraction"
    ][1]
    return next(
        point
        for point in observation["values"]["points"]
        if point["sample_id"] == sample
    )


def _assert_starting_composition_link(point: dict, material: str) -> None:
    composition = point["point_conditions"]["composition"]
    assert composition["state"]["tag"] == "value"
    assert composition["state"]["value"]["basis"] == "printed_oxides"
    assert composition["state"]["value"]["amount_basis"] == "mass_percent"
    assert {
        component: str(value)
        for component, value in composition["state"]["value"]["components"]
    } == STARTING_COMPOSITIONS[material]
    assert composition["locator"]["table"] == (
        "1" if material == "basalt" else "2"
    )


def _printed_condition(point: dict, key: str) -> str:
    return str(point["point_conditions"][key]["state"]["value"])


def _render_grid(document: dict) -> tuple[dict, ...]:
    grid = []
    for expected in TABLE_RENDER:
        table = expected["table"]
        sample = expected["sample"]
        material = "basalt" if table == "1" else "loess"
        delta = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_delta41k_nist_srm_3141a",
            sample,
        )
        reduced = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_delta41k_relative_to_starting",
            sample,
        )
        oxide = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_residue_oxide_composition",
            sample,
        )
        copper = _series_row(
            document,
            f"neuman_2026_table_{table}_{material}_cu_residue_ppm",
            sample,
        )
        grid.append(
            {
                "table": table,
                "sample": sample,
                "temperature_C": str(delta["T_C"]),
                "duration_s": str(delta["duration_s"]),
                "fO2_target": _printed_condition(delta, "fO2_control"),
                "gas_mix_1_pct": str(oxide["gas1"]),
                "gas_mix_2_pct": str(oxide["gas2"]),
                "flow_rate_cm3_min": _printed_condition(
                    delta, "gas_flow_rate_cm3_min"
                ),
                "delta41K_permil": str(
                    delta["isotope_delta_delta41K_NIST_SRM_3141a_permil"]
                ),
                "n": str(delta["n"]),
                "two_se_permil": str(delta["two_se_permil"]),
                "Delta41K_permil": str(
                    reduced["isotope_delta_Delta41K_sample_minus_starting_permil"]
                ),
                "K2O_wt_pct": str(oxide["K2O_wt_pct"]),
                "Cu_ppm": str(copper["residue_ppm"]),
            }
        )
    return tuple(grid)


def _expected_species_rows(document: dict) -> Counter:
    expected = Counter()
    for container, block in document["species"].items():
        for observation in block.get("observations", []):
            values = observation.get("values", {})
            if not isinstance(values, dict):
                continue
            for point in values.get("series", []):
                sample = point.get("sample_id")
                if sample is None:
                    continue
                if values.get("quantity") == Quantity.RESIDUE_COMPONENT_COMPOSITION.value:
                    components = [
                        field.removesuffix("_wt_pct")
                        for field in point
                        if field.endswith("_wt_pct")
                    ]
                    if components:
                        expected.update(
                            (observation["observation_id"], sample, formula)
                            for formula in components
                        )
                    elif "residue_ppm" in point:
                        expected[(
                            observation["observation_id"],
                            sample,
                            values.get("formula", container),
                        )] += 1
                else:
                    expected[(
                        observation["observation_id"],
                        sample,
                        values.get("formula", container),
                    )] += 1
    return expected


def _index_row() -> dict:
    return {
        "source_id": SID,
        "pdf_path": f"raw/{SID}/{SID}.pdf",
        "pdf_sha256": "0f745b41c8f76c6b5c77ba7c44275d59f5a910f4e1f236a93c10632c4358720e",
        "corpus": {
            "raw": {
                "path": f"raw/{SID}/{SID}.pdf",
                "present": True,
                "sha256": "0f745b41c8f76c6b5c77ba7c44275d59f5a910f4e1f236a93c10632c4358720e",
            },
            "tables": {
                "path": f"tables/{SID}",
                "exists": True,
                "file_count": 2,
            },
            "commit": "f63ace28d3a18987c0d5443c578a5e698655bfdc",
        },
    }


def _migrate(document: dict):
    migrator = Migrator(root=REPO_ROOT, index={SID: _index_row()}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    rows = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == SID
    ]
    return rows


def test_render_grid_and_both_cell_mutations_are_detected() -> None:
    document = _document()
    actual = _render_grid(document)
    assert actual == TABLE_RENDER

    changed_render = copy.deepcopy(TABLE_RENDER)
    changed_render[0]["K2O_wt_pct"] = "0.485"
    with pytest.raises(AssertionError):
        assert actual == changed_render

    changed_extract = copy.deepcopy(document)
    row = _series_row(
        changed_extract,
        "neuman_2026_table_1_basalt_residue_oxide_composition",
        "3–5",
    )
    row["K2O_wt_pct"] = "0.485"
    with pytest.raises(AssertionError):
        assert _render_grid(changed_extract) == TABLE_RENDER


def test_printed_loss_summaries_and_registered_supplement_context_are_preserved() -> None:
    document = _document()
    assert "supporting_material" not in document["source"]
    assert document["source"]["supporting_material_sha256"] == (
        "0f745b41c8f76c6b5c77ba7c44275d59f5a910f4e1f236a93c10632c4358720e"
    )
    contexts = {
        observation["observation_id"]: observation
        for block in document["species"].values()
        for observation in block.get("context", [])
    }

    expected_rows = {
        "neuman_2026_main_text_basalt_loss_summaries_p54": [
            ("3–5", "K", "~75% of the initial K"),
            ("3–5", "Cu", "99.8%"),
            ("3–5", "Zn", "94%"),
            ("1–5", "K", "45%"),
            ("2–5", "K", "59%"),
            (None, "Rb", "up to 88%"),
            (None, "Na", "up to 74%"),
            (None, "Ga", "up to 40%"),
            ("4–2", "K", "29%"),
            ("3–4", "K", "56%"),
        ],
        "neuman_2026_main_text_basalt_loss_summaries_p56": [
            ("4–2", "Cu", "97%"),
            ("4–2", "Zn", "94%"),
            ("4–2", "Rb", "42%"),
            ("4–2", "Na", "27%"),
            ("4–2", "Ga", "9.2%"),
        ],
        "neuman_2026_main_text_loess_loss_summaries_p58": [
            ("5–10", "K", "32%"),
            (None, "Zn", "up to 85%"),
            ("5–10", "Cu", "nearly all of the Cu was lost in the most extreme case"),
        ],
    }
    for observation_id, expected in expected_rows.items():
        observation = contexts[observation_id]
        assert observation["type"] == "printed_data_context"
        assert observation["values"]["candidate_tokens"] == [
            "mass_loss_fraction",
            "residue_component_composition",
        ]
        assert [
            (
                row.get("sample_id"),
                row["element"],
                row["loss_as_printed"],
            )
            for row in observation["values"]["rows"]
        ] == expected

    supplement = contexts["neuman_2026_supplementary_table_s1_mass_loss_inputs"]
    assert supplement["type"] == "printed_data_context"
    reason = supplement["values"]["reason"]
    assert "inputs to the measured_reduced mass-loss rows" in reason
    assert "K evaporation number, J and timescales are model-derived and remain context" in reason
    assert len(supplement["values"]["rows"]) == 49

    assert all(
        "starting_weight_g_as_printed" in row
        and "ending_weight_g_as_printed" in row
        for row in supplement["values"]["rows"]
    )


def test_mass_loss_points_link_pdf_starting_compositions_and_run_conditions() -> None:
    document = _document()
    expected_rows = {
        ("basalt", "1-1"): {
            "temperature_C": "2000",
            "temperature_K": "2273.15",
            "duration_s": "10",
            "fO2_control": "pure O2",
            "atmosphere": "pure O2",
            "flow": "375",
            "mass_loss_fraction": "-0.004310344828",
            "table": "1",
            "row": "1–1",
        },
        ("basalt", "3-5"): {
            "temperature_C": "2000",
            "temperature_K": "2273.15",
            "duration_s": "100",
            "fO2_control": "IW − 1",
            "atmosphere": (
                "1.53% 95 vol% Ar + 5 vol% CO2; "
                "98.47% 95 vol% Ar + 5 vol% CO"
            ),
            "flow": "374",
            "mass_loss_fraction": "0.053664166186",
            "table": "1",
            "row": "3–5",
        },
        ("loess", "5-1"): {
            "temperature_C": "2046",
            "temperature_K": "2319.15",
            "duration_s": "5",
            "fO2_control": "pure O2",
            "atmosphere": "pure O2",
            "flow": "375",
            "mass_loss_fraction": "0.003467062902",
            "table": "2",
            "row": "5–1",
        },
    }
    start_locators = {
        "basalt": next(
            experiment["sample"]["printed_composition"]["locator"]
            for experiment in document["experiments"]
            if experiment["experiment_id"]
            == "neuman-2026-basalt-levitation-evaporation"
        ),
        "loess": next(
            experiment["sample"]["printed_composition"]["locator"]
            for experiment in document["experiments"]
            if experiment["experiment_id"]
            == "neuman-2026-loess-levitation-evaporation"
        ),
    }

    for (material, sample), expected in expected_rows.items():
        point = _mass_loss_point(document, material, sample)
        conditions = point["point_conditions"]
        _assert_starting_composition_link(point, material)
        assert conditions["composition"]["locator"] == start_locators[material]
        assert str(point["T_C"]) == expected["temperature_C"]
        assert str(point["duration_s"]) == expected["duration_s"]
        assert str(point["mass_loss_fraction"]) == expected["mass_loss_fraction"]
        assert _printed_condition(point, "temperature_K") == expected[
            "temperature_K"
        ]
        assert _printed_condition(point, "duration_s") == expected["duration_s"]
        assert _printed_condition(point, "fO2_control") == expected["fO2_control"]
        assert _printed_condition(point, "atmosphere") == expected["atmosphere"]
        assert _printed_condition(point, "gas_flow_rate_cm3_min") == expected["flow"]
        assert conditions["temperature_K"]["locator"]["table"] == expected["table"]
        assert conditions["temperature_K"]["locator"]["row"] == expected["row"]
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert conditions["partial_pressure_Pa"]["state"]["tag"] == "unknown"

    all_mass_loss_points = [
        point
        for material in ("basalt", "loess")
        for point in _source_observations(document)[
            f"neuman_2026_supplementary_table_s1_{material}_mass_loss_fraction"
        ][1]["values"]["points"]
    ]
    assert len(all_mass_loss_points) == 49
    assert all(
        point["point_conditions"]["total_pressure_Pa"]["state"]["tag"]
        == "unknown"
        and point["point_conditions"]["partial_pressure_Pa"]["state"]["tag"]
        == "unknown"
        for point in all_mass_loss_points
    )
    unlinked_point = _mass_loss_point(document, "basalt", "2-6")
    unlinked_run = unlinked_point["point_conditions"]
    assert unlinked_point["material"] == "Basalt"
    assert "T_C" not in unlinked_point
    assert "duration_s" not in unlinked_point
    _assert_starting_composition_link(unlinked_point, "basalt")
    assert unlinked_run["temperature_K"]["state"]["tag"] == "unknown"
    assert unlinked_run["duration_s"]["state"]["tag"] == "unknown"
    assert unlinked_run["atmosphere"]["state"]["tag"] == "unknown"
    assert unlinked_run["gas_flow_rate_cm3_min"]["state"]["tag"] == "unknown"

    # This transcribed composition pin fails if a linked point is changed.
    mutated = copy.deepcopy(document)
    point = _mass_loss_point(mutated, "basalt", "1-1")
    point["point_conditions"]["composition"]["state"]["value"]["components"][
        0
    ][1] = "44.0"
    with pytest.raises(AssertionError):
        _assert_starting_composition_link(point, "basalt")


def test_derived_supplementary_mass_loss_rows_migrate_and_pin_values() -> None:
    # INDEX is assembled once per offer; inject this builder-shaped source row.
    index_row = _index_row()
    table_asset = next(
        asset
        for asset in source_files_for(SID, index_row).files
        if asset.asset_id == f"tables:{SID}"
    )
    pdf_asset = next(
        asset
        for asset in source_files_for(SID, index_row).files
        if asset.asset_id == f"pdf:{SID}"
    )
    assert pdf_asset.role.name == "PDF"
    assert table_asset.role.name == "TABLE_CSV"
    assert table_asset.path == f"tables/{SID}"

    document = _document()
    rows = _migrate(document)
    mass_loss = [
        row
        for row in rows
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and "neuman_2026_supplementary_table_s1_" in row.observation_id
    ]
    assert len(mass_loss) == 49
    assert all(row.value.kind.value == "point" for row in mass_loss)
    assert all(row.evidence.class_.value.value == "measured_reduced" for row in mass_loss)
    assert all(row.derived_from == (f"tables:{SID}",) for row in mass_loss)
    assert sum(row.identity.temperature_K.is_value for row in mass_loss) == 48
    assert sum(row.identity.temperature_K.is_unknown for row in mass_loss) == 1
    assert all(row.point_conditions for row in mass_loss)
    derived_points = [
        point
        for observation_id, (_, observation) in _source_observations(document).items()
        if observation_id.startswith("neuman_2026_supplementary_table_s1_")
        and observation_id.endswith("mass_loss_fraction")
        for point in observation["values"]["points"]
    ]
    assert len(derived_points) == 49
    assert all(
        {"starting_weight_g_as_printed", "ending_weight_g_as_printed"}
        <= point.get("derivation_parameters", {}).keys()
        for point in derived_points
    )
    assert sum(
        row.point_conditions["duration_s"].state.is_value for row in mass_loss
    ) == 48
    assert sum(
        row.point_conditions["duration_s"].state.is_unknown for row in mass_loss
    ) == 1
    assert all(row.point_conditions["duration_s"].locator is not None for row in mass_loss)

    source_points = {
        point["sample_id"]: point
        for observation_id, (_, observation) in _source_observations(document).items()
        if observation_id.startswith("neuman_2026_supplementary_table_s1_")
        and observation_id.endswith("mass_loss_fraction")
        for point in observation["values"]["points"]
    }
    migrated_by_sample = {
        row.locator.note.split("CSV row ", 1)[1].split(";", 1)[0]: row
        for row in mass_loss
    }
    assert set(migrated_by_sample) == set(source_points)
    for sample, row in migrated_by_sample.items():
        point = source_points[sample]
        material = point["material"].lower()
        conditions = row.point_conditions
        assert conditions is not None
        composition = conditions["composition"]
        assert composition.state.is_value and composition.locator is not None
        assert composition.state.value.as_map() == wt_pct_to_mole_fraction(
            STARTING_COMPOSITIONS[material]
        ).as_map()
        assert conditions["temperature_K"].locator is not None
        assert conditions["duration_s"].locator is not None
        assert conditions["atmosphere"].locator is not None
        assert conditions["gas_flow_rate_cm3_min"].locator is not None
        if sample == "2-6":
            assert row.identity.temperature_K.is_unknown
            assert conditions["temperature_K"].state.is_unknown
            assert conditions["duration_s"].state.is_unknown
        else:
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == conditions["temperature_K"].state.value
            assert conditions["duration_s"].state.is_value
            assert str(conditions["duration_s"].state.value) == str(point["duration_s"])
        assert (
            point["point_conditions"]["total_pressure_Pa"]["state"]["tag"]
            == "unknown"
        )
        assert (
            point["point_conditions"]["partial_pressure_Pa"]["state"]["tag"]
            == "unknown"
        )
        assert row.value.point == Decimal(point["mass_loss_fraction"])
    assert sum(
        row.value.point > 0 and row.identity.temperature_K.is_value
        for row in migrated_by_sample.values()
    ) == 43
    assert sum(row.value.point < 0 for row in migrated_by_sample.values()) == 5

    pinned = {
        "1-2": "0.023500309215",
        "1-4": "0.171612903226",
        "3-5": "0.053664166186",
    }
    sample_1_2 = next(
        point
        for point in _source_observations(document)[
            "neuman_2026_supplementary_table_s1_basalt_mass_loss_fraction"
        ][1]["values"]["points"]
        if point["sample_id"] == "1-2"
    )
    assert sample_1_2["derivation_parameters"] == {
        "starting_weight_g_as_printed": "0.01617",
        "ending_weight_g_as_printed": "0.01579",
    }

    def pinned_values(observations):
        return {
            row.locator.note.split("CSV row ", 1)[1].split(";", 1)[0]: str(
                row.value.point
            )
            for row in observations
            if row.locator.note
            and "CSV row " in row.locator.note
            and row.locator.note.split("CSV row ", 1)[1].split(";", 1)[0]
            in pinned
        }

    actual = pinned_values(mass_loss)
    assert actual == pinned

    target = next(
        row
        for row in mass_loss
        if row.locator.note and "CSV row 1-2;" in row.locator.note
    )
    mutated_value = replace(target.value, point=Decimal("0.023500309216"))
    mutated_row = replace(target, value=mutated_value)
    mutated_rows = [mutated_row if row is target else row for row in mass_loss]
    mutated = pinned_values(mutated_rows)
    with pytest.raises(AssertionError):
        assert mutated == pinned


def test_isolated_migration_preserves_species_and_run_conditions() -> None:
    document = _document()
    source = _source_observations(document)
    rows = _migrate(document)
    assert len(rows) == 2307
    assert_no_unavailable_observation_values(rows)
    assert sum(
        row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION for row in rows
    ) == 49

    actual_species_rows = Counter()
    without_sample = []
    for row in rows:
        if (
            row.identity.quantity.is_value
            and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
            and "neuman_2026_supplementary_table_s1_" in row.observation_id
        ):
            continue
        parts = row.observation_id.split("::", 2)
        source_id = parts[1]
        container, observation = source[source_id]
        sample_match = re.search(r":row=(.*?):h=", row.observation_id)
        if sample_match and "series" in observation.get("values", {}):
            actual_species_rows[(
                source_id,
                sample_match.group(1),
                row.identity.species.formula,
            )] += 1
            assert row.identity.temperature_K.is_value
            assert row.point_conditions is not None
            for key in (
                "temperature_K",
                "duration_s",
                "fO2_control",
                "atmosphere",
                "composition",
            ):
                condition = row.point_conditions[key]
                assert condition.state.is_value
                assert condition.locator is not None
            assert row.point_conditions["temperature_K"].locator.page > 0
            assert (
                row.point_conditions["temperature_K"].locator.page
                == row.point_conditions["temperature_K"].locator.pdf_page_index + 1
            )
        else:
            without_sample.append(row)
            assert row.identity.species.formula == observation.get("values", {}).get(
                "formula", container
            )

    assert actual_species_rows == _expected_species_rows(document)
    assert len(without_sample) == 2  # The two measured starting-material isotope baselines.
    assert all(row.identity.species.formula == "K" for row in without_sample)
    assert all(row.point_conditions is None for row in without_sample)


def test_per_file_validator_accepts_extract() -> None:
    errors = validator.validate_extract_document(
        _document(),
        path=EXTRACT,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert not errors


@pytest.mark.skipif(not PDF.is_file(), reason="regolith-corpus source PDF is absent")
def test_locator_pages_match_the_source_pdf() -> None:
    document = _document()
    page_text: dict[int, str] = {}
    for locator in iter_locators(document):
        if "page" not in locator:
            continue
        page = int(locator["page"])
        assert int(locator["pdf_page_index"]) == page - 1
        if page not in page_text:
            result = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            page_text[page] = result.stdout
        text = page_text[page]
        assert text.strip()
        table = locator.get("table")
        if table and not locator.get("source_path"):
            assert f"Table {table}" in text
        elif table and table in {"S1", "S2"}:
            assert f"Supplementary Table {table}" in text
        figure = locator.get("figure")
        if figure and str(figure).startswith("S"):
            cited_range = "Supplementary Figs. S1-S11" if page == 5 else "Supplementary Fig. S12"
            assert cited_range in text
        elif figure:
            assert f"Fig. {figure}" in text
