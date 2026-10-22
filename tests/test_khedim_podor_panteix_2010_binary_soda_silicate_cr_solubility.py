from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, PhaseRole, Quantity
from simulator.battery.identity import celsius_to_kelvin
from simulator.battery.migrate import REPO_ROOT, migrate
from tests.battery.test_migrate import _write_min_tree
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.printed_decimal_pair import printed_decimal_pair


SOURCE_ID = "khedim-podor-panteix-2010-binary-soda-silicate-cr-solubility"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
SOURCE_PDF = Path("raw") / SOURCE_ID / f"{SOURCE_ID}.pdf"

# Independent transcription of Table 2, confirmed against the 600 dpi render
# of physical PDF page 21. Cells retain the printed value and parenthetical SD.
RENDER_GRID = [
    {
        "T_C": 1250,
        "compositions": [
            ("Na2O-1.5SiO2", "1.5"),
            ("Na2O-2SiO2", "2"),
            ("Na2O-2.5SiO2", "2.5"),
            ("Na2O-3SiO2", "3"),
        ],
        "rows": [
            ("Fe/FeO", "-11.28", ["0.437 (0.042)", "0.571 (0.055)", "0.769 (0.070)", "1.035 (0.042)"]),
            ("Co/CoO", "-8.70", ["0.308 (0.037)", "0.395 (0.021)", "0.500 (0.024)", "0.619 (0.024)"]),
            ("Ni/NiO", "-6.51", ["0.275 (0.038)", "0.359 (0.022)", "0.442 (0.005)", "0.532 (0.013)"]),
            ("Fe3O4/Fe2O3", "-1.80", ["0.685 (0.012)", "0.685 (0.04)", "0.605 (0.031)", "0.628 (0.047)"]),
            ("Air", "-0.67", ["3.187 (0.206)", "2.755 (0.15)", "1.727 (0.218)", "1.424 (0.082)"]),
        ],
    },
    {
        "T_C": 1200,
        "compositions": [
            ("Na2O-2SiO2", "2"),
            ("Na2O-2.5SiO2", "2.5"),
            ("Na2O-3SiO2", "3"),
            ("Na2O-3.5SiO2", "3.5"),
        ],
        "rows": [
            ("Fe/FeO", "-11.89", ["0.581 (0.012)", "0.633 (0.067)", "0.873 (0.047)", "1.05 (0.029)"]),
            ("Co/CoO", "-9.25", ["0.349 (0.08)", "0.431 (0.017)", "0.536 (0.021)", "0.510 (0.014)"]),
            ("Ni/NiO", "-7.05", ["0.320 (0.015)", "0.389 (0.010)", "0.469 (0.010)", "0.474 (0.015)"]),
            ("Fe3O4/Fe2O3", "-1.84", ["0.547 (0.003)", "0.518 (0.031)", "0.552 (0.024)", "0.532 (0.008)"]),
            ("Air", "-0.67", ["2.128 (0.113)", "1.492 (0.097)", "1.259 (0.058)", "0.942 (0.033)"]),
        ],
    },
    {
        "T_C": 1175,
        "compositions": [
            ("Na2O-1.5SiO2", "1.5"),
            ("Na2O-2SiO2", "2"),
            ("Na2O-2.5SiO2", "2.5"),
            ("Na2O-3SiO2", "3"),
        ],
        "rows": [
            ("Fe/FeO", "-12.21", ["0.295 (0.027)", "0.494 (0.033)", "0.612 (0.027)", "0.822 (0.029)"]),
            ("Co/CoO", "-9.54", ["0.225 (0.011)", "0.328 (0.009)", "0.413 (0.019)", "0.498 (0.020)"]),
            ("Ni/NiO", "-7.34", ["0.214 (0.009)", "0.290 (0.009)", "0.371 (0.028)", "0.435 (0.021)"]),
            ("Fe3O4/Fe2O3", "-1.87", ["0.485 (0.015)", "0.481 (0.025)", "0.482 (0.024)", "0.507 (0.026)"]),
            ("Air", "-0.67", ["2.436 (0.206)", "1.834 (0.049)", "1.415 (0.034)", "1.172 (0.040)"]),
        ],
    },
    {
        "T_C": 1125,
        "compositions": [
            ("Na2O-1.5SiO2", "1.5"),
            ("Na2O-2SiO2", "2"),
            ("Na2O-2.5SiO2", "2.5"),
            ("Na2O-3SiO2", "3"),
        ],
        "rows": [
            ("Fe/FeO", "-12.90", ["0.223 (0.015)", "0.438 (0.011)", "0.562 (0.025)", "0.768 (0.026)"]),
            ("Co/CoO", "-10.14", ["0.196 (0.029)", "0.284 (0.011)", "0.388 (0.020)", "0.466 (0.026)"]),
            ("Ni/NiO", "-7.94", ["0.191 (0.004)", "0.254 (0.004)", "0.326 (0.011)", "0.379 (0.012)"]),
            ("Fe3O4/Fe2O3", "-1.93", ["0.395 (0.028)", "0.368 (0.007)", "0.418 (0.001)", "0.436 (0.018)"]),
            ("Air", "-0.67", ["1.980 (0.221)", "1.297 (0.084)", "1.215 (0.058)", "0.990 (0.063)"]),
        ],
    },
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _expected_rows(grid: list[dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    for block in RENDER_GRID if grid is None else grid:
        for row_number, (buffer, log_fO2, cells) in enumerate(block["rows"], start=1):
            for column_number, ((composition, silica_ratio), cell) in enumerate(
                zip(block["compositions"], cells), start=1
            ):
                printed_value, printed_sigma = printed_decimal_pair(cell)
                rows.append({
                    "observation_id": f"khedim_2010_binary_t{block['T_C']}_r{row_number:02d}_c{column_number:02d}",
                    "T_C": block["T_C"],
                    "buffer": buffer,
                    "fO2_log": Decimal(log_fO2),
                    "composition_as_published": composition,
                    "composition": [("Na2O", "1"), ("SiO2", silica_ratio)],
                    "printed_value": printed_value,
                    "printed_sigma": printed_sigma,
                    "component_amount": Decimal(printed_value) / Decimal("100"),
                    "sigma": Decimal(printed_sigma) / Decimal("100"),
                })
    return rows


def _assert_render_grid_matches(document: dict, grid: list[dict] | None = None) -> None:
    expected = _expected_rows(grid)
    observations = {row["observation_id"]: row for row in document["species"]["Cr"]["observations"]}
    assert len(observations) == len(expected) == 80
    for row in expected:
        observation = observations[row["observation_id"]]
        values = observation["values"]
        locator = observation["locator"]
        assert locator["page"] == 21
        assert locator["pdf_page_index"] == 20
        assert locator["published_page"] == 20
        assert locator["table"] == "2"
        assert locator["row"] == row["buffer"]
        assert values["T_C"] == row["T_C"]
        assert values["composition_as_published"] == row["composition_as_published"]
        assert values["value_as_published"] == row["printed_value"]
        assert values["sigma_as_published"] == row["printed_sigma"]
        assert Decimal(str(values["component_amount"])) == row["component_amount"]
        assert Decimal(str(observation["uncertainty"]["sigma"])) == row["sigma"]
        conditions = values["point_conditions"]
        assert Decimal(str(conditions["fO2_log"]["state"]["value"])) == row["fO2_log"]
        assert conditions["composition"]["state"]["value"]["components"] == [
            list(component) for component in row["composition"]
        ]
        assert values["redox_buffer_as_published"] == row["buffer"]
        assert "At.% Cr" in locator["column"]
        if row["buffer"] == "Air":
            assert conditions["gas_mix"]["state"]["value"] == "Air"
        else:
            assert "gas_mix" not in conditions


def test_table2_render_grid_matches_extract_and_detects_mutations() -> None:
    document = _document()
    _assert_render_grid_matches(document)

    changed_grid = deepcopy(RENDER_GRID)
    changed_grid[0]["rows"][0][2][0] = "0.438 (0.042)"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(document, changed_grid)

    changed_extract = deepcopy(document)
    changed_extract["species"]["Cr"]["observations"][0]["values"]["value_as_published"] = "0.438"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(changed_extract)


def test_figure_records_defer_points_and_keep_panels_and_series() -> None:
    context = _document()["species"]["Cr"]["context"]
    figures = {
        row["observation_id"]: row
        for row in context
        if row.get("type") == "figure_record"
    }
    expected = {
        "khedim_2010_figure_2_deferred": ("2", 25, "a-d"),
        "khedim_2010_figure_3_deferred": ("3", 27, "not printed"),
        "khedim_2010_figure_4a_deferred": ("4", 28, "a"),
        "khedim_2010_figure_4b_deferred": ("4", 28, "b"),
        "khedim_2010_figure_5a_deferred": ("5", 29, "a"),
        "khedim_2010_figure_5b_deferred": ("5", 29, "b"),
        "khedim_2010_figure_5c_deferred": ("5", 29, "c"),
        "khedim_2010_figure_6_deferred": ("6", 30, "not printed"),
        "khedim_2010_figure_7_deferred": ("7", 31, "not printed"),
        "khedim_2010_figure_8_deferred": ("8", 32, "not printed"),
    }
    assert set(figures) == set(expected)
    for observation_id, (figure, page, panel) in expected.items():
        record = figures[observation_id]
        values = record["values"]
        assert record["locator"]["figure"] == figure
        assert record["locator"]["page"] == page
        assert record["locator"]["pdf_page_index"] == page - 1
        assert values["panel"] == panel
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["admission_status"] == "typed_refusal"
        assert values["series"]
        assert all(series["marker_count"] > 0 for series in values["series"])
        assert "points" not in values
        assert "data_points" not in values


def test_isolated_migration_keeps_all_table2_points_and_conditions(tmp_path: Path) -> None:
    document = _document()
    result = migrate(_write_min_tree(tmp_path / "tree", document), write=False)
    expected = _expected_rows()
    assert len(result.observations) == 80
    assert len({row.observation_id for row in result.observations.values()}) == 80

    for row in expected:
        matches = [
            observation
            for observation in result.observations.values()
            if observation.observation_id.endswith(f"::{row['observation_id']}")
        ]
        assert len(matches) == 1, row["observation_id"]
        observation = matches[0]
        assert observation.value.kind.value == "point"
        assert observation.value.point == row["component_amount"]
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == "Cr"
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole.MELT
        assert observation.identity.subtype.value == "mole_fraction"
        assert observation.identity.temperature_K.value == celsius_to_kelvin(Decimal(row["T_C"]))
        assert observation.identity.fO2_Pa.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert observation.notices == ()
        assert observation.uncertainty.kind.value == "printed"
        assert observation.uncertainty.verbatim["confidence_level"] == "one_sigma"
        assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == row["sigma"]
        assert observation.uncertainty.verbatim["units"] == "dimensionless"

        conditions = observation.point_conditions
        assert conditions["fO2_log"].state.value == row["fO2_log"]
        assert conditions["composition"].state.value.components == tuple(
            (formula, Decimal(amount)) for formula, amount in row["composition"]
        )
        if row["buffer"] == "Air":
            assert conditions["gas_mix"].state.value == "Air"
        else:
            assert "gas_mix" not in conditions


def test_source_table_and_figure_locators_skip_if_corpus_is_absent() -> None:
    pdf = regolith_corpus_root() / SOURCE_PDF
    if not pdf.is_file():
        pytest.skip("the configured regolith corpus does not include this source PDF")

    expected_pages = {
        7: ("50°C/s", "10-6 bar at room temperature"),
        8: ("standard deviation", "10 measurements"),
        20: ("Table 1: Composition of glasses used as starting materials",),
        21: ("Table 2: Total content of chromium dissolved", "T=1250°C", "0.437 (0.042)", "-12.90"),
        22: ("Table 3: x, y and z values", "Cr3O4+"),
        23: ("Table 4: Standard entropies and enthalpies", "-120 ± 6"),
        25: ("Figure 2: Total chromium contents", "T=1250°C"),
        27: ("Figure 3: Plots of total chromium contents",),
        28: ("Figure 4: Plots of log(Rred)",),
        29: ("Figure 5: Logarithmic plot of Cr(III)",),
        30: ("Figure 6: Stoichiometric coefficients",),
        31: ("Figure 7: log[Cr(III)]", "-0.252x+1.085"),
        32: ("Figure 8: Plot of the B and C parameters",),
    }
    for page, labels in expected_pages.items():
        page_text = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for label in labels:
            assert label in page_text, (page, label)
    table2_text = subprocess.run(
        ["pdftotext", "-f", "21", "-l", "21", "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert sum(table2_text.count(f"{buffer}") for buffer in ("Fe/FeO", "Co/CoO", "Ni/NiO", "Fe3O4/Fe2O3", "Air")) == 20
