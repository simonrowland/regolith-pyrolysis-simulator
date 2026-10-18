from __future__ import annotations

import csv
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tools import mineru_table_draft as draft
from tools import validate_literature_extracts as validator


FIXTURES = Path(__file__).parent / "fixtures" / "mineru-table-emit"
# The corpus checkout to compare against. REGOLITH_CORPUS_ROOT lets a gate point at a
# specific clone (the controller's, or a CI-provisioned one) instead of the default path.
CORPUS_ROOT = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))
EXTRACTS = (
    Path(__file__).resolve().parents[2] / "data" / "literature" / "extracts"
)
_DASHES = {"-", "–", "—", "−"}


def _convert_html(tmp_path: Path, html: str, *, page_idx: int = 0) -> Path:
    sid = "example-source"
    source = (
        tmp_path
        / "text"
        / sid
        / "mineru"
        / "p001-p003"
        / "chunk_content_list.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "page_idx": page_idx,
                    "table_caption": "Table 1",
                    "table_body": html,
                }
            ]
        ),
        encoding="utf-8",
    )
    index = draft.convert_source(source.parent, tmp_path / "draft")
    return index.parent


def _base_mapping(table: dict) -> dict:
    return {
        "source_id": "example-source",
        "source": {"citation": "A. Author, Example measurements, 2020."},
        "extraction": {
            "method": "mineru_table_draft emit",
            "date": "2026-10-17",
            "worker": "test",
        },
        "review_status": "draft",
        "tables": [table],
    }


def _emit(tmp_path: Path, draft_dir: Path, mapping: dict, **kwargs) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "mapping.yaml"
    path.write_text(
        yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    out = tmp_path / "fragment.yaml"
    draft.emit_extract(draft_dir, path, out, **kwargs)
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [
        observation
        for block in document["species"].values()
        for observation in block.get("observations") or []
    ]


def _pressure_table(**overrides) -> dict:
    table = {
        "page": 1,
        "table_index": 1,
        "type": "partial_pressure",
        "quantity": "partial_pressure",
        "evidence_class": "measured_direct",
        "unit": "atm",
        "phase": "gas",
        "rows": "all",
        "temperature": {"column": 1, "unit": "K"},
        "values": [{"column": 2, "species": "Mg"}],
    }
    table.update(overrides)
    return table


def test_module_docstring_documents_the_mapping() -> None:
    text = draft.__doc__ or ""
    assert "table_index" in text
    assert "needs_render_check" in text
    assert "pdftotext-layout" in text
    assert "printed_formula" in text


def test_happy_path_keeps_verbatim_text_and_skips_dashes(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>p Mg</th></tr>"
        "<tr><td>1851</td><td>1.25</td></tr>"
        "<tr><td>1859</td><td>-</td></tr>"
        "<tr><td>1914</td><td>0</td></tr></table>",
    )
    document = _emit(tmp_path, draft_dir, _base_mapping(_pressure_table()))
    rows = _observations(document)
    assert [row["values"]["pressure_atm"] for row in rows] == ["1.25", "0"]
    assert [row["values"]["T_K"] for row in rows] == ["1851", "1914"]
    assert all(isinstance(row["values"]["T_K"], str) for row in rows)
    assert all(isinstance(row["values"]["pressure_atm"], str) for row in rows)
    assert document["skipped_empty_or_dash"]["total"] == 1
    for row in rows:
        assert set(row["values"]) == {"quantity", "method_class", "T_K", "pressure_atm"}
        assert "points" not in row["values"]
        assert row["mineru_flags"] == []
        assert "needs_render_check" not in row
        assert row["locator"]["page"] == 1
        assert row["locator"]["table"] == "1"
        assert row["locator"]["row_label"] == row["values"]["T_K"]
        assert row["locator"]["column_header"] == "p Mg"
        temperature = row["point_conditions"]["temperature_K"]
        assert temperature["state"]["value"]["point"] == row["values"]["T_K"]
        assert temperature["locator"]["table"] == "1"
        errors: list[str] = []
        validator._check_observation(row, "row", errors, set())
        assert errors == []


def test_latex_scientific_notation_is_not_a_fused_superscript(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>p</th></tr>"
        r"<tr><td>1851</td><td>$4.68 \times 10^{-7}$</td></tr></table>",
    )
    document = _emit(tmp_path, draft_dir, _base_mapping(_pressure_table()))
    row = _observations(document)[0]
    assert row["values"]["pressure_atm"] == r"$4.68 \times 10^{-7}$"
    assert "needs_render_check" not in row
    assert "value" not in row["values"]


def test_celsius_axis_uses_t_c_and_does_not_store_a_kelvin_twin(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, C</th><th>p</th></tr>"
        "<tr><td>25</td><td>1.5</td></tr></table>",
    )
    document = _emit(
        tmp_path,
        draft_dir,
        _base_mapping(_pressure_table(temperature={"column": 1, "unit": "C"})),
    )
    row = _observations(document)[0]
    assert row["values"]["T_C"] == "25"
    assert "T_K" not in row["values"]
    assert "temperature_K" not in row.get("point_conditions", {})


def test_row_selection_accepts_listed_rows_or_a_predicate(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>p</th></tr>"
        "<tr><td>1851</td><td>1</td></tr>"
        "<tr><td>1859</td><td>2</td></tr>"
        "<tr><td>1914</td><td>3</td></tr></table>",
    )
    listed = _emit(
        tmp_path / "listed",
        draft_dir,
        _base_mapping(_pressure_table(rows=[3])),
    )
    assert [row["values"]["pressure_atm"] for row in _observations(listed)] == ["2"]
    predicate = _pressure_table(row_predicate={"column": 1, "matches": "^191"})
    predicate.pop("rows")
    matched = _emit(
        tmp_path / "matched",
        draft_dir,
        _base_mapping(predicate),
    )
    assert [row["values"]["T_K"] for row in _observations(matched)] == ["1914"]


def test_per_column_evidence_and_uncertainty(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>(8)</th><th>(14)</th></tr>"
        "<tr><td>1851</td><td>1.1</td><td>2.2</td></tr></table>",
    )
    table = _pressure_table(
        evidence_class=None,
        uncertainty={
            "columns": {
                2: {"form": "relative_bound", "bound_value": "8%", "confidence_level": "not_stated"}
            }
        },
        values=[
            {"column": 2, "species": "O", "evidence_class": "measured_reduced"},
            {"column": 3, "species": "O", "evidence_class": "model_derived"},
        ],
    )
    del table["evidence_class"]
    document = _emit(tmp_path, draft_dir, _base_mapping(table))
    rows = {row["locator"]["column_header"]: row for row in _observations(document)}
    assert rows["(8)"]["values"]["method_class"] == "measured_reduced"
    assert rows["(14)"]["values"]["method_class"] == "model_derived"
    assert rows["(8)"]["uncertainty"]["bound_value"] == "8%"
    assert "uncertainty" not in rows["(14)"]


def test_value_pairs_unwrap_printed_formulas_and_ignore_species_glyphs(
    tmp_path: Path,
) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>Solid</th><th>G</th></tr>"
        r"<tr><td>${\mathrm{{Al}}}_{2}{\mathrm{O}}_{3}$</td><td>-1033.869</td></tr>"
        "<tr><td>I.2 ×</td><td>2.0</td></tr></table>",
    )
    document = _emit(
        tmp_path,
        draft_dir,
        _base_mapping(
            {
                "page": 1,
                "table_index": 1,
                "type": "gibbs_table",
                "quantity": "delta_fG",
                "evidence_class": "quoted_attributed",
                "unit": "kJ/mol",
                "attribution": "Table caption.",
                "species_form": "printed_formula",
                "temperature": {"as_printed": "2000", "unit": "K"},
                "value_pairs": [
                    {"species_column": 1, "value_column": 2, "phase": "cr"}
                ],
            }
        ),
    )
    assert set(document["species"]) == {"Al2O3", "I.2×"}
    al = document["species"]["Al2O3"]["observations"][0]
    assert al["values"]["value"] == "-1033.869"
    assert al["values"]["T_K"] == "2000"
    assert al["phase"] == "cr"
    assert "needs_render_check" not in al
    labelled = document["species"]["I.2×"]["observations"][0]
    assert "needs_render_check" not in labelled


@pytest.mark.parametrize(
    ("html", "flag"),
    [
        ("<tr><td>1851</td><td>1.23<sup>4</sup></td></tr>", "fused_superscript"),
        ("<tr><td>1851</td><td>1.23.45</td></tr>", "run_together"),
        ("<tr><td>1851</td><td>1,23</td></tr>", "comma_decimal_ambiguous"),
        ("<tr><td>1851</td><td>25,540</td></tr>", "comma_decimal_ambiguous"),
        ("<tr><td>1851</td><td>I.23 × 10</td></tr>", "letter_for_digit"),
        ("<tr><td>1851</td><td>1.2 3.4</td></tr>", "stacked_values"),
        ("<tr><td>1851</td><td>1.2<br>3.4</td></tr>", "stacked_values"),
    ],
)
def test_each_value_cell_trap_is_refused_without_a_verified_spelling(
    tmp_path: Path, html: str, flag: str
) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>p</th></tr>" + html + "</table>",
    )
    with pytest.raises(ValueError, match=rf"needs_render_check \['{flag}'\]"):
        _emit(tmp_path / "bare", draft_dir, _base_mapping(_pressure_table()))
    verified = _pressure_table(
        render_verified=[{"row": 2, "column": 2, "text": "verified-spelling"}]
    )
    document = _emit(tmp_path / "verified", draft_dir, _base_mapping(verified))
    row = _observations(document)[0]
    assert "needs_render_check" not in row
    assert row["values"]["pressure_atm"] == "verified-spelling"


def test_dropped_plusminus_needs_a_confirming_pdftotext_page(tmp_path: Path) -> None:
    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>p</th></tr>"
        "<tr><td>1800</td><td>1851.02</td></tr></table>",
    )
    plain = _emit(tmp_path / "plain", draft_dir, _base_mapping(_pressure_table()))
    assert "needs_render_check" not in _observations(plain)[0]
    assert _observations(plain)[0]["values"]["pressure_atm"] == "1851.02"
    pdftotext = tmp_path / "layout.txt"
    pdftotext.write_text("header\n1851 ± 02\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs_render_check \\['dropped_plusminus'\\]"):
        _emit(
            tmp_path / "bare",
            draft_dir,
            _base_mapping(_pressure_table()),
            pdftotext=pdftotext,
        )
    verified = _pressure_table(
        render_verified=[{"row": 2, "column": 2, "text": "1851±02"}]
    )
    marked = _emit(
        tmp_path / "marked",
        draft_dir,
        _base_mapping(verified),
        pdftotext=pdftotext,
    )
    row = _observations(marked)[0]
    assert "needs_render_check" not in row
    assert row["values"]["pressure_atm"] == "1851±02"


def test_emit_refuses_a_pdftotext_only_table_and_a_csv_without_flags(
    tmp_path: Path,
) -> None:
    draft_dir = tmp_path / "tables" / "example-source"
    draft_dir.mkdir(parents=True)
    (draft_dir / "p001-t01.csv").write_text(
        "row,column,text\n1,1,1851\n",
        encoding="utf-8",
    )
    (draft_dir / "index.yaml").write_text(
        yaml.safe_dump(
            {
                "source_id": "example-source",
                "tables": [
                    {
                        "table_key": "p001-t01",
                        "pdf_page": 1,
                        "source_kind": "pdftotext-layout",
                        "source_file": "pdftotext-layout.txt",
                        "csv": "p001-t01.csv",
                        "columns": [{"column": 1, "header": "T"}],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    mapping = _base_mapping(
        {
            "page": 1,
            "table_index": 1,
            "type": "partial_pressure",
            "quantity": "partial_pressure",
            "evidence_class": "measured_direct",
            "unit": "atm",
            "values": [{"column": 1, "species": "Mg"}],
        }
    )
    with pytest.raises(ValueError, match="emit refuses"):
        _emit(tmp_path / "kind", draft_dir, mapping)
    index = yaml.safe_load((draft_dir / "index.yaml").read_text(encoding="utf-8"))
    index["tables"][0].pop("source_kind")
    index["tables"][0]["source_file"] = "content_list.json"
    (draft_dir / "index.yaml").write_text(
        yaml.safe_dump(index, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no MinerU flags"):
        _emit(tmp_path / "flags", draft_dir, mapping)
    assert draft.main(
        ["emit", str(draft_dir), str(tmp_path / "flags" / "mapping.yaml"), "--out", str(tmp_path / "out.yaml")]
    ) == 2


def _mineru_dir(source_id: str) -> Path:
    if not CORPUS_ROOT.is_dir():
        pytest.skip(f"regolith-corpus checkout is absent: {CORPUS_ROOT}")
    root = CORPUS_ROOT / "text" / source_id / "mineru"
    if not root.is_dir():
        pytest.skip(f"MinerU draft source is not on disk: {root}")
    return root


def _reviewed(source_id: str) -> dict:
    return yaml.safe_load((EXTRACTS / f"{source_id}.yaml").read_text(encoding="utf-8"))


def _emit_reviewed(tmp_path: Path, source_id: str) -> tuple[dict, Path]:
    draft_root = tmp_path / "draft"
    index = draft.convert_source(_mineru_dir(source_id), draft_root)
    out = tmp_path / "fragment.yaml"
    draft.emit_extract(index.parent, FIXTURES / f"{source_id}.mapping.yaml", out)
    return yaml.safe_load(out.read_text(encoding="utf-8")), index


def _origin_cells(index_path: Path, table_key: str) -> list[dict[str, str]]:
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    table = next(item for item in index["tables"] if item["table_key"] == table_key)
    with (index_path.parent / table["csv"]).open(encoding="utf-8", newline="") as stream:
        return [
            cell
            for cell in csv.DictReader(stream)
            if cell["row_type"] == "body"
            and cell["row"] == cell["origin_row"]
            and cell["column"] == cell["origin_column"]
        ]


def _column_of(observation: dict) -> int:
    return int(observation["observation_id"].rsplit("_c", 1)[1])


def _csv_row_column(observation_id: str) -> tuple[str, str]:
    tail = observation_id.rsplit("_r", 1)[1]
    row, column = tail.split("_c", 1)
    return str(int(row)), str(int(column))


def test_activity_cell_is_a_point_and_other_pressure_units_are_refused(
    tmp_path: Path,
) -> None:
    from simulator.battery.enums import Quantity, ValueKind
    from simulator.battery.migrate import select_declared_source

    draft_dir = _convert_html(
        tmp_path,
        "<table><tr><th>T, K</th><th>a</th></tr>"
        "<tr><td>1566</td><td>0.21</td></tr></table>",
    )
    activity = _emit(
        tmp_path / "activity",
        draft_dir,
        _base_mapping(
            _pressure_table(
                type="activity",
                quantity="activity",
                unit="dimensionless",
            )
        ),
    )
    activity_row = _observations(activity)[0]
    assert activity_row["values"]["activity"] == "0.21"
    assert "value" not in activity_row["values"]
    selected = select_declared_source(
        Quantity.ACTIVITY,
        "dimensionless",
        activity_row["values"],
    )
    assert selected.value.kind is ValueKind.POINT
    assert selected.field_name == "activity"
    assert selected.value.point == Decimal("0.21")

    coefficient = _emit(
        tmp_path / "gamma",
        draft_dir,
        _base_mapping(
            _pressure_table(
                type="activity_coefficient",
                quantity="activity_coefficient",
                unit="dimensionless",
            )
        ),
    )
    coefficient_row = _observations(coefficient)[0]
    assert coefficient_row["values"]["gamma"] == "0.21"
    assert "value" not in coefficient_row["values"]
    selected_gamma = select_declared_source(
        Quantity.ACTIVITY_COEFFICIENT,
        "dimensionless",
        coefficient_row["values"],
    )
    assert selected_gamma.value.kind is ValueKind.POINT
    assert selected_gamma.field_name == "gamma"
    assert selected_gamma.value.point == Decimal("0.21")

    with pytest.raises(ValueError, match="partial_pressure unit 'Torr' is not atm, bar, or Pa"):
        _emit(
            tmp_path / "torr",
            draft_dir,
            _base_mapping(_pressure_table(unit="Torr")),
        )


def test_shornikov_2017_emit_matches_cells_and_explains_stored_twins(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2017-spinel-evaporation"
    fragment, index = _emit_reviewed(tmp_path, source_id)
    reviewed = _reviewed(source_id)
    mapping = yaml.safe_load(
        (FIXTURES / f"{source_id}.mapping.yaml").read_text(encoding="utf-8")
    )
    assert [(table["page"], table["table_index"]) for table in mapping["tables"]] == [
        (4, 2),
        (4, 3),
    ]
    assert len(reviewed["species"]["MgAl2O4"]["context"]) == 8

    emitted = _observations(fragment)
    assert all("needs_render_check" not in row for row in emitted)
    assert fragment["skipped_empty_or_dash"]["tables"] == [
        {"page": 4, "table_index": 2, "count": 11},
        {"page": 4, "table_index": 3, "count": 2},
    ]
    by_table: dict[str, list[dict]] = {}
    for row in emitted:
        by_table.setdefault(row["locator"]["table"], []).append(row)
    assert len(by_table["2"]) == 73
    assert len(by_table["3"]) == 58
    assert not any(
        str(row["values"]["pressure_atm"]).strip() in _DASHES | {""} for row in emitted
    )

    reviewed_rows = [
        observation
        for block in reviewed["species"].values()
        for observation in block.get("observations") or []
    ]
    for table, count in (("2", 73), ("3", 58)):
        point_rows = [
            row
            for row in reviewed_rows
            if str(row["locator"]["table"]) == table and row["values"].get("points")
        ]
        absent = [
            row
            for row in reviewed_rows
            if str(row["locator"]["table"]) == table and not row["values"].get("points")
        ]
        assert sum(len(row["values"]["points"]) for row in point_rows) == count
        skip = next(
            item
            for item in fragment["skipped_empty_or_dash"]["tables"]
            if item["table_index"] == int(table)
        )
        assert skip["count"] == len(absent)

    def fragment_species(document: dict, observation: dict) -> str:
        for formula, block in document["species"].items():
            if observation in block["observations"]:
                return formula
        raise AssertionError(observation["observation_id"])

    for formula, block in fragment["species"].items():
        for row in block["observations"]:
            if row["locator"]["table"] != "2":
                continue
            assert formula in {"Mg", "MgO", "Al", "AlO", "Al2O", "O2", "MgAlO"}
            assert row["values"]["method_class"] == "measured_direct"
            assert row["uncertainty"]["bound_value"] == "8%"
    oxygen = [
        row
        for row in fragment["species"]["O"]["observations"]
        if row["locator"]["table"] == "3"
    ]
    classes = {_column_of(row): row["values"]["method_class"] for row in oxygen}
    assert classes[2] == "measured_reduced"
    assert classes[3] == "model_derived"
    assert all("uncertainty" in row for row in oxygen if _column_of(row) == 2)
    assert all("uncertainty" not in row for row in oxygen if _column_of(row) != 2)

    cells = {
        "2": _origin_cells(index, "p004-t02"),
        "3": _origin_cells(index, "p004-t03"),
    }
    for row in emitted:
        csv_row, csv_column = _csv_row_column(row["observation_id"])
        match = next(
            cell
            for cell in cells[row["locator"]["table"]]
            if cell["row"] == csv_row and cell["column"] == csv_column
        )
        assert match["text"].strip() == row["values"]["pressure_atm"]
        assert json.loads(match["flags"]) == row["mineru_flags"]

    differing = []
    for row in reviewed_rows:
        table = str(row["locator"]["table"])
        if table not in {"2", "3"} or not row["values"].get("points"):
            continue
        species = next(
            formula
            for formula, block in reviewed["species"].items()
            if row in block.get("observations") or []
        )
        for point in row["values"]["points"]:
            emitted_row = next(
                candidate
                for candidate in by_table[table]
                if fragment_species(fragment, candidate) == species
                and candidate["values"]["T_K"] == str(point["T_K_as_printed"])
                and draft._number_decimal(candidate["values"]["pressure_atm"])
                == Decimal(point["pressure_atm"])
            )
            by_table[table].remove(emitted_row)
            assert candidate_locator_matches(emitted_row, row)
            assert emitted_row["values"]["pressure_atm"] != point["pressure_atm"]
            assert emitted_row["values"]["pressure_atm"] != point["pressure_as_printed"]
            differing.append(emitted_row["observation_id"])
    assert by_table["2"] == []
    assert by_table["3"] == []
    assert len(differing) == 131
    errors = validator.validate_extract_document(
        fragment,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert errors == []


def candidate_locator_matches(emitted_row: dict, reviewed_row: dict) -> bool:
    locator = emitted_row["locator"]
    reviewed = reviewed_row["locator"]
    return (
        locator["page"] == reviewed["page"]
        and locator["table"] == str(reviewed["table"])
        and locator["pdf_page_index"] == reviewed["pdf_page_index"]
        and locator["published_page"] == reviewed["published_page"]
        and locator["row_label"] == emitted_row["values"]["T_K"]
        and locator["column_header"]
    )


def test_shornikov_2019_emit_reproduces_gibbs_cells(tmp_path: Path) -> None:
    source_id = "shornikov-2019-lunar-meteorite-evaporation-model"
    fragment, index = _emit_reviewed(tmp_path, source_id)
    reviewed = _reviewed(source_id)
    mapping = yaml.safe_load(
        (FIXTURES / f"{source_id}.mapping.yaml").read_text(encoding="utf-8")
    )
    assert [(table["page"], table["table_index"]) for table in mapping["tables"]] == [
        (4, 1)
    ]
    context = [
        row
        for block in reviewed["species"].values()
        for row in block.get("context") or []
    ]
    assert len(context) == 5
    gibbs = [
        (formula, observation)
        for formula, block in reviewed["species"].items()
        for observation in block.get("observations") or []
        if observation["type"] == "gibbs_table"
    ]
    assert len(gibbs) == 113
    cells = _origin_cells(index, "p004-t01")
    value_cells = [cell for cell in cells if cell["column"] in {"2", "4", "6"}]
    dashes = [cell for cell in value_cells if cell["text"].strip() in _DASHES | {""}]
    assert fragment["skipped_empty_or_dash"]["total"] == len(dashes)
    emitted = _observations(fragment)
    assert len(emitted) == 113
    assert all("needs_render_check" not in row for row in emitted)
    assert all("points" not in row["values"] for row in emitted)
    got = Counter(
        (
            formula,
            row["phase"],
            row["values"]["value"],
            row["values"]["T_K"],
            row["locator"]["page"],
            row["locator"]["table"],
        )
        for formula, block in fragment["species"].items()
        for row in block["observations"]
    )
    expected = Counter(
        (
            formula,
            observation["phase"],
            observation["values"]["value"],
            "2000",
            observation["locator"]["page"],
            str(observation["locator"]["table"]),
        )
        for formula, observation in gibbs
    )
    assert got == expected
    for row in emitted:
        assert isinstance(row["values"]["value"], str)
        assert isinstance(row["values"]["T_K"], str)
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"].startswith("Table 1 caption:")
        assert row["point_conditions"]["temperature_K"]["state"]["value"]["point"] == "2000"
        assert row["per"] == "mol_species"
        csv_row, csv_column = _csv_row_column(row["observation_id"])
        match = next(
            cell
            for cell in value_cells
            if cell["row"] == csv_row and cell["column"] == csv_column
        )
        assert match["text"].strip() == row["values"]["value"]
        assert json.loads(match["flags"]) == row["mineru_flags"]
    errors = validator.validate_extract_document(
        fragment,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert errors == []


def _merge(
    reviewed: dict,
    fragment: dict,
    *,
    replace_table: str | None,
    drop_type: str | None,
    append_table: str | None,
) -> dict:
    merged = yaml.safe_load(yaml.safe_dump(reviewed, sort_keys=False, allow_unicode=True))
    for block in merged["species"].values():
        kept = []
        for observation in block.get("observations") or []:
            if (
                replace_table is not None
                and str(observation.get("locator", {}).get("table")) == replace_table
            ):
                continue
            if drop_type is not None and observation.get("type") == drop_type:
                continue
            kept.append(observation)
        block["observations"] = kept
    for formula, block in fragment["species"].items():
        rows = []
        for observation in block.get("observations") or []:
            table = str(observation["locator"]["table"])
            if append_table is not None and table != append_table:
                continue
            if replace_table is not None and table != replace_table:
                continue
            rows.append(observation)
        if not rows:
            continue
        merged["species"].setdefault(formula, {"observations": []})
        merged["species"][formula].setdefault("observations", [])
        merged["species"][formula]["observations"].extend(rows)
    # The copied pins address the reviewed series ids. The migration proof
    # does not consult them.
    merged.pop("fidelity_samples", None)
    return merged


def _migrate(tmp_path: Path, document: dict):
    from simulator.battery.migrate import Migrator, REPO_ROOT

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "merged.yaml"
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def test_merged_shornikov_fragments_migrate_with_temperature_and_conditions(
    tmp_path: Path,
) -> None:
    from simulator.battery.enums import ValueKind

    sid_2017 = "shornikov-2017-spinel-evaporation"
    fragment_2017, _ = _emit_reviewed(tmp_path / "y2017", sid_2017)
    # Table 3 derived_from names the reviewed Table 2 series ids. Replacing
    # those series leaves the lineage unresolved, so the emitted cells are
    # appended and the reviewed series stay.
    merged_2017 = _merge(
        _reviewed(sid_2017),
        fragment_2017,
        replace_table=None,
        drop_type=None,
        append_table="2",
    )
    result_2017 = _migrate(tmp_path / "mig2017", merged_2017)
    records_2017 = list(result_2017.observations.values())
    assert result_2017.validation is not None
    assert not result_2017.validation.hard_issues
    assert not result_2017.registry_issues
    assert len(records_2017) == 217
    for record in records_2017:
        assert record.identity.temperature_K.is_value
        condition = record.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None
    emitted_2017 = [
        record for record in records_2017 if "p004_t02_" in record.observation_id
    ]
    assert len(emitted_2017) == 73
    for record in emitted_2017:
        assert record.value.kind is ValueKind.UNAVAILABLE
        assert record.locator.table == "2"
        assert "row_label=" in (record.locator.note or "")
        composition = record.point_conditions["composition"]
        assert composition.state.is_value
        assert dict(composition.state.value.components) == {
            "MgO": Decimal("0.5"),
            "Al2O3": Decimal("0.5"),
        }
        assert composition.locator is not None
        assert record.point_conditions["fO2_Pa"].state.is_unknown
        assert record.point_conditions["fO2_Pa"].locator is not None
        assert record.point_conditions["total_pressure_Pa"].state.is_unknown
        assert record.point_conditions["total_pressure_Pa"].locator is not None
        assert (
            record.point_conditions["temperature_K"].state.value
            == record.identity.temperature_K.value
        )

    sid_2019 = "shornikov-2019-lunar-meteorite-evaporation-model"
    fragment_2019, _ = _emit_reviewed(tmp_path / "y2019", sid_2019)
    merged_2019 = _merge(
        _reviewed(sid_2019),
        fragment_2019,
        replace_table=None,
        drop_type="gibbs_table",
        append_table=None,
    )
    result_2019 = _migrate(tmp_path / "mig2019", merged_2019)
    records_2019 = [
        record
        for record in result_2019.observations.values()
        if record.source_id == sid_2019
    ]
    assert not result_2019.validation.hard_issues
    assert not result_2019.registry_issues
    assert len(records_2019) == 113
    assert not any(record.value.kind is ValueKind.UNAVAILABLE for record in records_2019)
    for record in records_2019:
        assert record.identity.temperature_K.is_value
        assert record.identity.temperature_K.value == Decimal("2000")
        temperature = record.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("2000")
        assert temperature.locator is not None
        assert record.evidence.attribution
        assert record.identity.per.is_value
