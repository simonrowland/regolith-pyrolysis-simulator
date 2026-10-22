from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, PhaseRole, Quantity
from simulator.battery.identity import celsius_to_kelvin
from simulator.battery.migrate import REPO_ROOT, migrate
from tests.battery.test_migrate import _write_min_tree
from tests.helpers.corpus_paths import regolith_corpus_root


SOURCE_ID = "khedim-2008-chromia-solubility-thesis"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
SOURCE_PDF = Path("raw") / SOURCE_ID / f"{SOURCE_ID}.pdf"

# Independent transcription of the Appendix III grids from the 600 dpi renders.
# Each row is (redox buffer, printed log fO2, cells by the printed composition
# columns); each cell retains both the primary value and the unlabelled
# parenthetical value exactly as printed.
RENDER_GRID = [
    {
        "page": 169,
        "table": 20,
        "T_C": 1250,
        "row_start": 4,
        "columns": [3, 4, 5, 6],
        "compositions": [
            ("Na2O-1,5SiO2", [("Na2O", "1"), ("SiO2", "1.5")]),
            ("Na2O-2SiO2", [("Na2O", "1"), ("SiO2", "2")]),
            ("Na2O-2,5SiO2", [("Na2O", "1"), ("SiO2", "2.5")]),
            ("Na2O-3SiO2", [("Na2O", "1"), ("SiO2", "3")]),
        ],
        "rows": [
            ("Fe/FeO", "-11,28", ["0,437 (0,042)", "0,571(0,055)", "0,769 (0,070)", "1,035 (0,042)"]),
            ("Co/CoO", "-8,70", ["0,308 (0,037)", "0,395 (0,021)", "0,500 (0,024)", "0,619 (0,024)"]),
            ("Ni/NiO", "-6,51", ["0,275 (0,038)", "0,359 (0,022)", "0,442 (0,005)", "0,532 (0,013)"]),
            ("Fe3O4/Fe2O3", "-1,80", ["0,685 (0,012)", "0,685 (0,04)", "0,605 (0,031)", "0,628 (0,047)"]),
            ("Air", "-0,67", ["3,187 (0,206)", "2,755 (0,15)", "1,727 (0,218)", "1,424 (0,082)"]),
        ],
    },
    {
        "page": 169,
        "table": 20,
        "T_C": 1200,
        "row_start": 12,
        "columns": [3, 4, 5, 6],
        "compositions": [
            ("Na2O-2SiO2", [("Na2O", "1"), ("SiO2", "2")]),
            ("Na2O-2,5SiO2", [("Na2O", "1"), ("SiO2", "2.5")]),
            ("Na2O-3SiO2", [("Na2O", "1"), ("SiO2", "3")]),
            ("Na2O-3,5SiO2", [("Na2O", "1"), ("SiO2", "3.5")]),
        ],
        "rows": [
            ("Fe/FeO", "-11,89", ["0,581 (0,012)", "0,633(0,067)", "0,873 (0,047)", "1,05 (0,029)"]),
            ("Co/CoO", "-9,25", ["0,349 (0,08)", "0,431 (0,017)", "0,536 (0,021)", "0,510 (0,014)"]),
            ("Ni/NiO", "-7,05", ["0,320 (0,015)", "0,389 (0,010)", "0,469 (0,010)", "0,474 (0,015)"]),
            ("Fe3O4/Fe2O3", "-1,84", ["0,547 (0,003)", "0,518 (0,031)", "0,552 (0,024)", "0,532 (0,008)"]),
            ("Air", "-0,67", ["2,128 (0,113)", "1,492 (0,097)", "1,259 (0,058)", "0,942 (0,033)"]),
        ],
    },
    {
        "page": 169,
        "table": 20,
        "T_C": 1175,
        "row_start": 20,
        "columns": [3, 4, 5, 6],
        "compositions": [
            ("Na2O-1,5SiO2", [("Na2O", "1"), ("SiO2", "1.5")]),
            ("Na2O-2SiO2", [("Na2O", "1"), ("SiO2", "2")]),
            ("Na2O-2,5SiO2", [("Na2O", "1"), ("SiO2", "2.5")]),
            ("Na2O-3SiO2", [("Na2O", "1"), ("SiO2", "3")]),
        ],
        "rows": [
            ("Fe/FeO", "-12,21", ["0,295 (0,027)", "0,494(0,033)", "0,612 (0,027)", "0,822 (0,029)"]),
            ("Co/CoO", "-9,54", ["0,225 (0,011)", "0,328 (0,009)", "0,413 (0,019)", "0,498 (0,020)"]),
            ("Ni/NiO", "-7,34", ["0,214 (0,009)", "0,290 (0,009)", "0,371 (0,028)", "0,435 (0,021)"]),
            ("Fe3O4/Fe2O3", "-1,87", ["0,485 (0,015)", "0,481 (0,025)", "0,482 (0,024)", "0,507 (0,026)"]),
            ("Air", "-0,67", ["2,436 (0,206)", "1,834 (0,049)", "1,415 (0,034)", "1,172 (0,040)"]),
        ],
    },
    {
        "page": 170,
        "table": 21,
        "T_C": 1125,
        "row_start": 3,
        "columns": [3, 4, 5, 6],
        "compositions": [
            ("Na2O-1,5SiO2", [("Na2O", "1"), ("SiO2", "1.5")]),
            ("Na2O-2SiO2", [("Na2O", "1"), ("SiO2", "2")]),
            ("Na2O-2,5SiO2", [("Na2O", "1"), ("SiO2", "2.5")]),
            ("Na2O-3SiO2", [("Na2O", "1"), ("SiO2", "3")]),
        ],
        "rows": [
            ("Fe/FeO", "-12,90", ["0,223 (0,015)", "0,438 (0,011)", "0,562 (0,025)", "0,768 (0,026)"]),
            ("Co/CoO", "-10,14", ["0,196 (0,029)", "0,284 (0,011)", "0,388 (0,020)", "0,466 (0,026)"]),
            ("Ni/NiO", "-7,94", ["0,191 (0,004)", "0,254 (0,004)", "0,326 (0,011)", "0,379 (0,012)"]),
            ("Fe3O4/Fe2O3", "-1,93", ["0,395 (0,028)", "0,368 (0,007)", "0,418 (0,001)", "0,436 (0,018)"]),
            ("Air", "-0,67", ["1,980 (0,221)", "1,297 (0,084)", "1,215 (0,058)", "0,990 (0,063)"]),
        ],
    },
    {
        "page": 170,
        "table": 22,
        "T_C": 1250,
        "row_start": 3,
        "columns": [3, 4, 5],
        "compositions": [
            ("Na2O-CaO-3SiO2", [("Na2O", "1"), ("CaO", "1"), ("SiO2", "3")]),
            ("Na2O-CaO-4SiO2", [("Na2O", "1"), ("CaO", "1"), ("SiO2", "4")]),
            ("Na2O-CaO-7SiO2", [("Na2O", "1"), ("CaO", "1"), ("SiO2", "7")]),
        ],
        "rows": [
            ("Fe/FeO", "-11,28", ["0,476 (0,033)", "0,538 (0,021)", "0,599 (0,063)"]),
            ("Co/CoO", "-8,70", ["0,286 (0,016)", "0,331 (0,020)", "0,356 (0,012)"]),
            ("Ni/NiO", "-6,51", ["0,261 (0,018)", "0,283 (0,022)", "0,274 (0,010)"]),
            ("Fe3O4/Fe2O3", "-1,80", ["0,387 (0,022)", "0,318 (0,023)", "0,292 (0,003)"]),
            ("Air", "-0,67", ["1,248 (0,079)", "0,627 (0,033)", "0,467 (0,012)"]),
        ],
    },
    {
        "page": 170,
        "table": 23,
        "T_C": 1200,
        "row_start": 3,
        "columns": [3],
        "compositions": [("Na2O-CaO-3SiO2", [("Na2O", "1"), ("CaO", "1"), ("SiO2", "3")])],
        "rows": [
            ("Fe/FeO", "-11,89", ["0,376 (0.046)"]),
            ("Co/CoO", "-9,25", ["0,252 (0,020)"]),
            ("Ni/NiO", "-7,05", ["0,226 (0,006)"]),
            ("Fe3O4/Fe2O3", "-1,84", ["0,314 (0,020)"]),
            ("Air", "-0,67", ["0,958 (0,015)"]),
        ],
    },
    {
        "page": 170,
        "table": 23,
        "T_C": 1300,
        "row_start": 3,
        "columns": [5],
        "compositions": [("Na2O-CaO-3SiO2", [("Na2O", "1"), ("CaO", "1"), ("SiO2", "3")])],
        "rows": [
            ("Fe/FeO", "-10,70", ["0,688 (0,067)"]),
            ("Co/CoO", "-8,19", ["0,378 (0,022)"]),
            ("Ni/NiO", "-6,00", ["0,315 (0,034)"]),
            ("Fe3O4/Fe2O3", "-1,74", ["0,494 (0,016)"]),
            ("Air", "-0,67", ["1,586 (0,020)"]),
        ],
    },
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _printed_numbers(cell: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*([0-9]+,[0-9]+)\s*\(\s*([0-9]+[,.][0-9]+)\s*\)\s*", cell)
    assert match is not None, cell
    return match.group(1), match.group(2)


def _expected_rows(grid: list[dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    for panel in RENDER_GRID if grid is None else grid:
        for offset, (buffer, log_fO2, cells) in enumerate(panel["rows"]):
            for column, composition, cell in zip(panel["columns"], panel["compositions"], cells):
                printed_value, parenthetical = _printed_numbers(cell)
                rows.append(
                    {
                        "observation_id": (
                            f"khedim_2008_chromia_solubility_thesis_p{panel['page']}_"
                            f"t{panel['table']:02d}_r{panel['row_start'] + offset:02d}_c{column:02d}"
                        ),
                        "page": panel["page"],
                        "pdf_page_index": panel["page"] - 1,
                        "table": str(panel["table"]),
                        "row": panel["row_start"] + offset,
                        "column": column,
                        "T_C": panel["T_C"],
                        "buffer": buffer,
                        "fO2_log": Decimal(log_fO2.replace(",", ".")),
                        "composition_as_published": composition[0],
                        "composition": composition[1],
                        "printed_value": printed_value,
                        "parenthetical": parenthetical,
                        "component_amount": Decimal(printed_value.replace(",", ".")) / Decimal("100"),
                    }
                )
    return rows


def _assert_render_grid_matches(document: dict, grid: list[dict] | None = None) -> None:
    expected = _expected_rows(grid)
    observations = {
        row["observation_id"]: row
        for row in document["species"]["Cr"]["observations"]
    }
    assert len(observations) == len(expected) == 105
    for row in expected:
        observation = observations[row["observation_id"]]
        values = observation["values"]
        locator = observation["locator"]
        assert locator["page"] == row["page"]
        assert locator["pdf_page_index"] == row["pdf_page_index"]
        assert locator["table"] == row["table"]
        assert locator["row"] == row["buffer"]
        assert values["T_C"] == row["T_C"]
        assert values["composition_as_published"] == row["composition_as_published"]
        assert values["value_as_published"] == row["printed_value"]
        assert values["parenthetical_value_as_published"] == row["parenthetical"]
        assert Decimal(str(values["component_amount"])) == row["component_amount"]
        conditions = values["point_conditions"]
        assert Decimal(str(conditions["fO2_log"]["state"]["value"])) == row["fO2_log"]
        assert values["redox_buffer_as_published"] == row["buffer"]
        assert conditions["composition"]["state"]["value"]["components"] == [
            list(component) for component in row["composition"]
        ]
        assert "At.% Cr" in locator["column"]


def test_appendix_render_grid_matches_extract_and_detects_mutations() -> None:
    document = _document()
    _assert_render_grid_matches(document)

    changed_grid = deepcopy(RENDER_GRID)
    changed_grid[0]["rows"][0][2][0] = "0,438 (0,042)"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(document, changed_grid)

    changed_extract = deepcopy(document)
    changed_extract["species"]["Cr"]["observations"][0]["values"]["value_as_published"] = "0,438"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(changed_extract)


def test_isolated_migration_keeps_every_component_and_condition(tmp_path: Path) -> None:
    document = _document()
    result = migrate(_write_min_tree(tmp_path / "tree", document), write=False)
    expected = _expected_rows()
    assert len(result.observations) == 105
    assert len({row.observation_id for row in result.observations.values()}) == 105

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

        conditions = observation.point_conditions
        assert conditions["fO2_log"].state.value == row["fO2_log"]
        assert conditions["composition"].state.value.components == tuple(
            (formula, Decimal(amount)) for formula, amount in row["composition"]
        )


def test_source_page_text_and_scan_locators_skip_if_corpus_is_absent() -> None:
    pdf = regolith_corpus_root() / SOURCE_PDF
    if not pdf.is_file():
        pytest.skip("the configured regolith corpus does not include this source PDF")

    for page, caption in ((109, "Figure 1:"), (110, "Figure 2:"), (111, "Figure 3:")):
        rendered_text = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert caption in rendered_text

    for page, expected_text in ((169, "0,437 (0,042)"), (170, "1,586 (0,020)")):
        appendix_text = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if page == 169:
            assert "Annexe 3" in appendix_text
        else:
            assert "T=1125°C" in appendix_text
        assert expected_text in appendix_text
