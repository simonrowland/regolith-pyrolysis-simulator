from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, UncertaintyKind, ValueKind
from simulator.battery.identity import celsius_to_kelvin
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/abdullah-2013-redox-acid-base-soda-lime-silicate-thesis.yaml"
RENDER_GRID = ROOT / "tests/fixtures/abdullah-2013-cr-solubility-render-grid.tsv"
ACTIVATION_GRID = ROOT / "tests/fixtures/abdullah-2013-table-5_4-render-grid.tsv"
SAMPLES = ("N2S", "NC3S", "NC4S", "NC5S", "NC6S")
COMPOSITIONS = {
    "N2S": "Na2O-2SiO2 (N2S)",
    "NC3S": "Na2O-CaO-3SiO2 (NC3S)",
    "NC4S": "Na2O-CaO-4SiO2 (NC4S)",
    "NC5S": "Na2O-CaO-5SiO2 (NC5S)",
    "NC6S": "Na2O-CaO-6SiO2 (NC6S)",
}
CITATION_99 = (
    "Khedim, H. Etude de la limite de solubilite de la chromine (Cr2O3) dans les "
    "silicates fondus-determination de grandeurs thermodynamiques et "
    "physicochimiques. PhD Thesis Université Henri Poincaré (2008)."
)
GRID_FIELDS = (
    "observation_id",
    "source_table",
    "sample_id_as_published",
    "sample_composition_as_published",
    "species_formula",
    "experiment",
    "T_C",
    "buffer_as_published",
    "fO2_log",
    "phase_label",
    "phase_label_as_published",
    "basis",
    "component_amount",
    "component_amount_as_published",
    "sigma",
    "uncertainty_units",
    "method_class",
    "attribution_as_published",
    "also_reported_table",
    "page",
    "row",
)
CELL_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:\(\s*±\s*(\d+(?:\.\d+)?)\s*\)|±\s*\(\s*(\d+(?:\.\d+)?)\s*\))\s*$"
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _cell_values(raw: str) -> tuple[str, str]:
    match = CELL_RE.fullmatch(raw)
    assert match, raw
    return decimal_text(match.group(1)), decimal_text(match.group(2) or match.group(3))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _expected_grid() -> list[dict[str, str]]:
    source_rows = _read_rows(RENDER_GRID)
    rows53 = [row for row in source_rows if row["table"] == "5.3"]
    rows48 = [row for row in source_rows if row["table"] == "4.8"]
    table53_cells: dict[tuple[int, str, str], tuple[str, str]] = {}
    secondary_locators: dict[tuple[int, str, str], str] = {}
    for row in rows53:
        temperature = int(row["row_T_C"])
        for sample in ("NC3S", "NC4S", "NC5S", "NC6S"):
            table53_cells[(temperature, row["buffer"], sample)] = _cell_values(row[sample])

    for row in rows48:
        temperature = int(row["row_T_C"])
        for sample in ("NC3S", "NC6S"):
            key = (temperature, "Air", sample)
            assert _cell_values(row[sample]) == table53_cells[key]
            secondary_locators[key] = "4.8"

    expected = []
    for row in rows53:
        temperature = int(row["row_T_C"])
        buffer = row["buffer"]
        experiment = f"abdullah_2013_t5_3_t{temperature}_{_slug(buffer)}"
        for sample in ("NC3S", "NC4S", "NC5S", "NC6S"):
            amount, sigma = _cell_values(row[sample])
            key = (temperature, buffer, sample)
            expected.append(
                {
                    "observation_id": f"abdullah_2013_t5_3_t{temperature}_{_slug(buffer)}_{_slug(sample)}",
                    "source_table": "5.3",
                    "sample_id_as_published": sample,
                    "sample_composition_as_published": COMPOSITIONS[sample],
                    "species_formula": "Cr",
                    "experiment": experiment,
                    "T_C": str(temperature),
                    "buffer_as_published": buffer,
                    "fO2_log": decimal_text(row["log_fO2"]),
                    "phase_label": "melt",
                    "phase_label_as_published": "NCxS melt",
                    "basis": "mole_fraction",
                    "component_amount": amount,
                    "component_amount_as_published": row[sample],
                    "sigma": sigma,
                    "uncertainty_units": "at.%",
                    "method_class": "measured_direct",
                    "attribution_as_published": "",
                    "also_reported_table": secondary_locators.get(key, ""),
                    "page": "211",
                    "row": f"{temperature} °C; {buffer}; {sample}",
                }
            )

    for row in rows48:
        temperature = int(row["N2S_T_C"])
        amount, sigma = _cell_values(row["N2S"])
        expected.append(
            {
                "observation_id": f"abdullah_2013_t4_8_n2s_t{temperature}_air_khedim99",
                "source_table": "4.8",
                "sample_id_as_published": "N2S",
                "sample_composition_as_published": COMPOSITIONS["N2S"],
                "species_formula": "Cr",
                "experiment": "abdullah_2013_t4_8_n2s_quoted_khedim99_air",
                "T_C": str(temperature),
                "buffer_as_published": "Air",
                "fO2_log": "",
                "phase_label": "melt",
                "phase_label_as_published": "Glasses",
                "basis": "mole_fraction",
                "component_amount": amount,
                "component_amount_as_published": row["N2S"],
                "sigma": sigma,
                "uncertainty_units": "at.%",
                "method_class": "quoted_attributed",
                "attribution_as_published": CITATION_99,
                "also_reported_table": "",
                "page": "151",
                "row": f"N2S, {temperature} °C",
            }
        )
    return sorted(expected, key=lambda row: row["observation_id"])


def _load_extract() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _extract_grid(document: dict) -> list[dict[str, str]]:
    rows = []
    for species_formula, species in document["species"].items():
        for observation in species.get("observations", []):
            values = observation["values"]
            conditions = values["point_conditions"]
            fO2 = conditions.get("fO2_log", {}).get("state", {}).get("value", "")
            secondary = values.get("also_reported_at", {})
            rows.append(
                {
                    "observation_id": observation["observation_id"],
                    "source_table": str(observation["locator"]["table"]),
                    "sample_id_as_published": values["sample_id_as_published"],
                    "sample_composition_as_published": values["sample_composition_as_published"],
                    "species_formula": values.get("formula", species_formula),
                    "experiment": observation["experiment"],
                    "T_C": decimal_text(values["T_C"]),
                    "buffer_as_published": values["buffer_as_published"],
                    "fO2_log": decimal_text(fO2),
                    "phase_label": values["phase_label"],
                    "phase_label_as_published": values["phase_label_as_published"],
                    "basis": values["basis"],
                    "component_amount": decimal_text(values["component_amount"]),
                    "component_amount_as_published": values["component_amount_as_published"],
                    "sigma": decimal_text(observation["uncertainty"]["sigma"]),
                    "uncertainty_units": observation["uncertainty"]["units"],
                    "method_class": values["method_class"],
                    "attribution_as_published": values.get("attribution", ""),
                    "also_reported_table": str(secondary.get("table", "")),
                    "page": str(observation["locator"]["page"]),
                    "row": observation["locator"]["row"],
                }
            )
    return sorted(rows, key=lambda row: row["observation_id"])


def _assert_grid_matches(document: dict, expected: list[dict[str, str]]) -> None:
    assert_observation_grid_matches(_extract_grid(document), expected, GRID_FIELDS)


def test_cr_solubility_cells_match_render_grid_and_duplicate_rows_are_stored_once() -> None:
    document = _load_extract()
    expected = _expected_grid()

    assert len(expected) == 70
    assert sum(row["source_table"] == "5.3" for row in expected) == 68
    assert sum(row["source_table"] == "4.8" for row in expected) == 2
    assert sum(row["also_reported_table"] == "4.8" for row in expected) == 4
    _assert_grid_matches(document, expected)


def test_grid_and_extract_mutations_each_break_the_render_comparison() -> None:
    document = _load_extract()
    expected = _expected_grid()
    _assert_grid_matches(document, expected)

    mutated_grid = deepcopy(expected)
    mutated_grid[0]["component_amount"] = "999"
    with pytest.raises(AssertionError):
        _assert_grid_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    first_id = expected[0]["observation_id"]
    first = next(
        observation
        for species in mutated_extract["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == first_id
    )
    first["values"]["component_amount"] += 1
    with pytest.raises(AssertionError):
        _assert_grid_matches(mutated_extract, expected)


def test_every_cr_solubility_observation_migrates_as_a_point_with_conditions() -> None:
    expected = _expected_grid()
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }

    assert len(observations) == len(expected) == 70
    assert not migrator.result.validation.hard_issues
    for row in expected:
        observation = observations[row["observation_id"]]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(row["component_amount"])
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == "Cr"
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole.MELT
        assert observation.identity.subtype.value == "mole_fraction"
        assert observation.identity.temperature_K.value == celsius_to_kelvin(Decimal(row["T_C"]))
        temperature = observation.point_conditions["T_C"]
        assert temperature.state.value == Decimal(row["T_C"])
        assert temperature.locator.page == int(row["page"])
        assert temperature.locator.pdf_page_index == int(row["page"]) - 1
        if row["fO2_log"]:
            assert observation.point_conditions["fO2_log"].state.value == Decimal(row["fO2_log"])
            assert observation.point_conditions["fO2_log"].locator.page == 211
        else:
            assert "fO2_log" not in observation.point_conditions
        if row["buffer_as_published"] == "Air":
            assert observation.point_conditions["atmosphere"].state.value == "air"
        else:
            assert "atmosphere" not in observation.point_conditions
        assert observation.uncertainty.kind is UncertaintyKind.PRINTED
        assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(row["sigma"])
        assert observation.uncertainty.verbatim["units"] == "at.%"
        assert not observation.notices


def test_activation_fit_context_and_deferred_figure_records_preserve_printed_data() -> None:
    document = _load_extract()
    context = document["species"]["Cr"]["context"]
    activation = {
        item["values"]["buffer_as_published"]: item
        for item in context
        if item.get("locator", {}).get("table") == "5.4"
    }
    for row in _read_rows(ACTIVATION_GRID):
        item = activation[row["buffer"]]
        assert item["locator"]["page"] == 213
        assert item["locator"]["pdf_page_index"] == 212
        assert item["values"]["log_fO2_as_published"] == row["log_fO2"]
        assert Decimal(str(item["values"]["activation_energy_kJ_per_mol_as_published"])) == Decimal(
            row["activation_energy_kJ_mol"]
        )

    figures = {
        item["locator"].get("figure"): item
        for item in context
        if item.get("type") == "figure_only"
    }
    fig24 = figures["5.24"]
    fig25 = figures["5.25"]
    assert fig24["locator"]["page"] == 212
    assert fig25["locator"]["page"] == 214
    assert fig24["values"]["digitisation"] == "deferred to a figure pass"
    assert fig25["values"]["digitisation"] == "deferred to a figure pass"
    assert [series["marker_count"] for series in fig24["values"]["series"]] == [3, 3, 3, 3]
    assert [series["marker_count"] for series in fig25["values"]["series"]] == [7, 5, 7]
    assert fig24["values"]["axes"]["x"]["label"] == "10^4/T (K^-1)"
    assert fig25["values"]["axes"]["x"]["label"] == "log fO2"
    fit = next(item for item in context if item["observation_id"] == "abdullah_2013_figure_5_24_arrhenius_regression")
    assert [series["equation_as_published"] for series in fit["values"]["series"]] == [
        "y = -0,5046x + 3,1066",
        "y = -0,6259x + 2,8391",
        "y = -0,9478x + 5,0925",
        "y = -1,5801x + 9,6965",
    ]
