from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os

import pytest
import yaml

from tools import mineru_table_draft as draft


FIXTURES = Path(__file__).parent / "fixtures" / "mineru-table-emit"
EXTRACTS = Path(__file__).resolve().parents[2] / "data" / "literature" / "extracts"
DEFAULT_CORPUS = Path.home() / "Repos" / "regolith-corpus"
MAPPINGS = sorted(FIXTURES.glob("*.mapping.yaml"))
HEADER_FIXTURES = Path(__file__).parent / "fixtures" / "mineru-table-header"


def _corpus_roots() -> tuple[Path, Path]:
    configured = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS))
    if configured.name == "raw":
        return configured.parent, configured
    if (configured / "raw").is_dir():
        return configured, configured / "raw"
    return configured, configured / "raw"


def _source_dir(corpus_root: Path, raw_root: Path, source_id: str) -> Path:
    candidates = (
        raw_root / source_id / "ocr-mineru",
        corpus_root / "text" / source_id / "mineru",
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


def _convert_source(
    source: Path, source_id: str, out_dir: Path, raw_root: Path
) -> Path:
    full_vlm = source / "full" / "vlm"
    if full_vlm.is_dir():
        alias = out_dir / "normalized" / source_id / "ocr-mineru" / "p001-p001" / "vlm"
        alias.mkdir(parents=True)
        for content_list in sorted(full_vlm.glob("*content_list.json")):
            (alias / content_list.name).symlink_to(content_list)
        if not any(alias.iterdir()):
            pytest.fail(f"no full-document MinerU content lists found under {full_vlm}")
        source = alias.parents[1]
    return draft.convert_source(source, out_dir / "converted", corpus_root=raw_root)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip().strip("$").strip()
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"[*†‡§¶|]+$", "", text).strip()
    scientific = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\\times\s*10\^\{?([+-]?\d+)\}?",
        text,
    )
    if scientific:
        return Decimal(scientific.group(1)) * (Decimal(10) ** int(scientific.group(2)))
    text = re.sub(r"\((?:\d+)\)$", "", text)
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _field_values(observation: dict, quantity: str) -> list[object]:
    values = observation.get("values") or {}
    if quantity == "Fe3plus_over_total_Fe":
        return [point.get("value") for point in values.get("points") or []]
    if quantity == "partial_pressure":
        fields = ("pressure_atm", "pressure_bar", "pressure_Pa")
        return [point.get(field) for point in values.get("points") or [] for field in fields if field in point]
    if quantity == "p_partial":
        return [point.get("pressure_bar") for point in values.get("series") or []]
    if quantity == "delta_fG":
        return [values.get("value")]
    if quantity == "elemental_abundance":
        return [values.get("abundance_ppm")]
    if quantity == "residue_composition_vs_time":
        return [point.get("residue_ppm") for point in values.get("series") or []]
    if quantity == "sodium_disilicate_total_weight_loss":
        fields = ("extractive_weight_loss_mg", "gravimetric_weight_loss_mg")
        return [row.get(field) for row in values.get("rows") or [] for field in fields]
    if quantity == "activity":
        if "activity" in values:
            return [values.get("activity")]
        return [point.get("activity") for point in values.get("points") or []]
    if quantity == "mass_loss_rate":
        return [values.get("mass_loss_rate")]
    if quantity == "mass_loss_areal_density":
        return [point.get("mass_loss_areal_density") for point in values.get("series") or []]
    if quantity == "transition_temperature":
        return [point.get("T_C") for point in values.get("series") or []]
    if quantity == "ion_intensity":
        return [values.get("ion_intensity")]
    return []


def _locator_page(locator: dict) -> int | None:
    page_index = locator.get("pdf_page_index")
    if page_index is not None:
        return int(page_index) + 1
    page = locator.get("page")
    return int(page) if page is not None else None


def _locator_key(locator: dict) -> tuple[str, str] | None:
    published_page = locator.get("published_page")
    if published_page is not None:
        return "published_page", str(published_page)
    page = _locator_page(locator)
    return ("pdf_page", str(page)) if page is not None else None


def _emitted_scalar(observation: dict) -> object:
    values = observation.get("values") or {}
    quantity = str(values.get("quantity") or "")
    field = {
        "activity": "activity",
        "activity_coefficient": "gamma",
        "ion_intensity": "value",
        "partial_pressure": f"pressure_{observation.get('units') or ''}",
    }.get(quantity, "value")
    return values.get(field)


def _extract_targets(
    mapping: dict, extract: dict
) -> dict[tuple[str, str, str, str], Counter[Decimal]]:
    targets: set[tuple[int, object, str]] = {
        (int(table["page"]), table.get("published_page"), str(table["quantity"]))
        for table in mapping.get("tables") or []
    }
    expected: dict[tuple[str, str, str, str], Counter[Decimal]] = defaultdict(Counter)
    for species, block in (extract.get("species") or {}).items():
        for observation in block.get("observations") or []:
            values = observation.get("values") or {}
            quantity = str(values.get("quantity") or "")
            if quantity not in {target[2] for target in targets}:
                continue
            locator = observation.get("locator") or {}
            for page, published_page, target_quantity in targets:
                if quantity != target_quantity:
                    continue
                if published_page is not None:
                    if str(locator.get("published_page")) != str(published_page):
                        continue
                    locator_kind, locator_value = "published_page", str(published_page)
                else:
                    if _locator_page(locator) != page:
                        continue
                    locator_kind, locator_value = "pdf_page", str(page)
                for raw in _field_values(observation, quantity):
                    number = _decimal(raw)
                    if number is not None:
                        expected[
                            (quantity, str(species), locator_kind, locator_value)
                        ][number] += 1
    return expected


def _emitted_cells(document: dict) -> dict[tuple[str, str, str, str], Counter[Decimal]]:
    emitted: dict[tuple[str, str, str, str], Counter[Decimal]] = defaultdict(Counter)
    for species, block in (document.get("species") or {}).items():
        for observation in block.get("observations") or []:
            values = observation.get("values") or {}
            quantity = str(values.get("quantity") or "")
            number = _decimal(_emitted_scalar(observation))
            if number is not None:
                locator_key = _locator_key(observation.get("locator") or {})
                if locator_key is not None:
                    emitted[(quantity, str(species), *locator_key)][number] += 1
    return emitted


def _assert_exact_landed_values(expected: dict, emitted: dict) -> None:
    missing = {
        key: counts - emitted.get(key, Counter())
        for key, counts in expected.items()
        if counts - emitted.get(key, Counter())
    }
    extra = {
        key: counts - expected.get(key, Counter())
        for key, counts in emitted.items()
        if counts - expected.get(key, Counter())
    }
    assert not missing and not extra, f"landed cell mismatch; missing={missing}; extra={extra}"


def _table_matches_observation(table: dict, observation: dict) -> bool:
    locator = observation.get("locator") or {}
    if str(locator.get("table")) != str(table["table_index"]):
        values = observation.get("values") or {}
        if not (
            # The source uses its printed Table 4.4 label; the mapping uses
            # MinerU's document-wide table index 11.
            table.get("quantity") == "sodium_disilicate_total_weight_loss"
            and values.get("quantity") == "sodium_disilicate_total_weight_loss"
            and _locator_page(locator) == int(table["page"])
        ):
            return False
    if table.get("published_page") is not None:
        return str(locator.get("published_page")) == str(table["published_page"])
    return _locator_page(locator) == int(table["page"])


def _assert_locator_values_match(
    *,
    document: dict,
    page: int,
    table_index: int,
    columns: list[int],
    quantity: str,
    species: str,
    expected: Counter[tuple[str, str, Decimal]],
    source: str,
) -> None:
    emitted: Counter[tuple[str, str, Decimal]] = Counter()
    for emitted_species, block in (document.get("species") or {}).items():
        if emitted_species != species:
            continue
        for observation in block.get("observations") or []:
            match = re.search(
                r"_p(?P<page>\d+)_t(?P<table>\d+)_r\d+_c(?P<column>\d+)$",
                str(observation.get("observation_id") or ""),
            )
            if (
                not match
                or int(match.group("page")) != page
                or int(match.group("table")) != table_index
                or int(match.group("column")) not in columns
                or (observation.get("values") or {}).get("quantity") != quantity
            ):
                continue
            locator = observation.get("locator") or {}
            value = _decimal(_emitted_scalar(observation))
            if value is not None:
                emitted[
                    (
                        str(locator.get("row_label") or ""),
                        str(locator.get("column_header") or ""),
                        value,
                    )
                ] += 1
    assert emitted == expected, (
        f"{source} cells differ by row_label and column_header: "
        f"expected {expected}, emitted {emitted}"
    )


def _assert_emitted_cells_bind_to_mapping(
    mapping: dict, draft_dir: Path, document: dict
) -> None:
    index_path = draft.resolve_draft_index(draft_dir, mapping["source_id"])
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    headers = {
        (int(table["pdf_page"]), int(table["source_table_number"])): {
            int(column["column"]): str(column["header"])
            for column in table["columns"]
        }
        for table in index["tables"]
    }
    specs: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for spec in mapping.get("tables") or []:
        specs[(int(spec["page"]), int(spec["table_index"]))].append(spec)
    for species, block in (document.get("species") or {}).items():
        for observation in block.get("observations") or []:
            match = re.search(
                r"_p(?P<page>\d+)_t(?P<table>\d+)_r(?P<row>\d+)_c(?P<column>\d+)$",
                str(observation.get("observation_id") or ""),
            )
            assert match, f"emitted cell has no mapped row/column identity: {observation}"
            page, table_index, row, column = (
                int(match.group(name)) for name in ("page", "table", "row", "column")
            )
            locator = observation.get("locator") or {}
            values = observation.get("values") or {}
            matching = [
                (spec, item)
                for spec in specs[(page, table_index)]
                if spec.get("rows", "all") == "all"
                or row in {int(selected) for selected in spec["rows"]}
                for item in draft._measurement_columns(spec)
                if int(
                    item["column"]
                    if item["kind"] == "fixed"
                    else item["value_column"]
                ) == column
                and (
                    (
                        item["kind"] == "fixed"
                        and str(item["species"]) == str(species)
                    )
                    or (
                        item["kind"] == "pair"
                        and draft.printed_formula(
                            str(locator.get("row_label") or "")
                        )
                        == str(species)
                    )
                )
            ]
            assert matching, f"emitted cell is outside its mapping: {observation['observation_id']}"
            assert locator.get("column_header", "") == headers[(page, table_index)].get(
                column, ""
            )
            assert "row_label" in locator
            assert any(
                (
                    item["kind"] == "pair"
                    and draft.printed_formula(str(locator["row_label"])) == str(species)
                )
                or (
                    item["kind"] == "fixed"
                    and spec.get("temperature")
                    and "column" in spec["temperature"]
                    and locator["row_label"]
                    == values.get(draft._TEMPERATURE_FIELDS[str(spec["temperature"]["unit"])])
                )
                or (
                    item["kind"] == "fixed"
                    and not (
                        spec.get("temperature")
                        and "column" in spec["temperature"]
                    )
                    and str(locator["row_label"]) == str(row)
                )
                for spec, item in matching
            ), f"emitted cell has an unmapped row label: {observation['observation_id']}"


def _assert_duplicate_columns_match_sources(
    mapping: dict, extract: dict, document: dict
) -> None:
    emitted_by_column: dict[tuple[str, str, int, int, int], list[Decimal]] = defaultdict(list)
    for species, block in (document.get("species") or {}).items():
        for observation in block.get("observations") or []:
            match = re.search(
                r"_p(?P<page>\d+)_t(?P<table>\d+)_r(?P<row>\d+)_c(?P<column>\d+)$",
                str(observation.get("observation_id") or ""),
            )
            if not match:
                continue
            quantity = str((observation.get("values") or {}).get("quantity") or "")
            number = _decimal(_emitted_scalar(observation))
            key = (
                quantity,
                str(species),
                int(match.group("page")),
                int(match.group("table")),
                int(match.group("column")),
            )
            if number is not None:
                emitted_by_column[key].append(number)

    for table in mapping.get("tables") or []:
        grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for measurement in table.get("values") or []:
            quantity = str(measurement.get("quantity") or table["quantity"])
            species = str(measurement["species"])
            evidence = str(
                measurement.get("evidence_class") or table.get("evidence_class") or ""
            )
            grouped[(quantity, species, evidence)].append(int(measurement["column"]))
        for (quantity, species, evidence), columns in grouped.items():
            if len(columns) < 2:
                continue
            source_observations = [
                observation
                for observation in (extract.get("species") or {}).get(species, {}).get(
                    "observations", []
                )
                if str((observation.get("values") or {}).get("quantity") or "") == quantity
                and _table_matches_observation(table, observation)
                and str(
                    (observation.get("values") or {}).get("evidence_class")
                    or (observation.get("values") or {}).get("method_class")
                    or (observation.get("values") or {}).get("measurement_status")
                    or ""
                ) == evidence
            ]
            if (
                quantity == "sodium_disilicate_total_weight_loss"
                and len(source_observations) == 1
                and (source_observations[0].get("values") or {}).get("rows")
            ):
                source_rows = source_observations[0]["values"]["rows"]
                expected_by_locator: Counter[tuple[str, str, Decimal]] = Counter()
                headers_by_column: dict[int, str] = {}
                for emitted_species, block in (document.get("species") or {}).items():
                    if emitted_species != species:
                        continue
                    for observation in block.get("observations") or []:
                        match = re.search(
                            r"_p(?P<page>\d+)_t(?P<table>\d+)_r\d+_c(?P<column>\d+)$",
                            str(observation.get("observation_id") or ""),
                        )
                        if (
                            not match
                            or int(match.group("page")) != int(table["page"])
                            or int(match.group("table")) != int(table["table_index"])
                            or int(match.group("column")) not in columns
                            or (observation.get("values") or {}).get("quantity")
                            != quantity
                        ):
                            continue
                        locator = observation.get("locator") or {}
                        column = int(match.group("column"))
                        header = str(locator.get("column_header") or "")
                        headers_by_column[column] = header
                for column in columns:
                    header = headers_by_column[column]
                    normalized_header = header.casefold()
                    if "extractive" in normalized_header:
                        field = "extractive_weight_loss_mg"
                    elif "gravimetric" in normalized_header:
                        field = "gravimetric_weight_loss_mg"
                    else:
                        raise AssertionError(
                            f"unrecognized Van Limpt column header: {header}"
                        )
                    for row in source_rows:
                        label = str(row["temperature_C"])
                        expected = _decimal(row[field])
                        assert expected is not None
                        expected_by_locator[(label, header, expected)] += 1
                _assert_locator_values_match(
                    document=document,
                    page=int(table["page"]),
                    table_index=int(table["table_index"]),
                    columns=columns,
                    quantity=quantity,
                    species=species,
                    expected=expected_by_locator,
                    source="Van Limpt",
                )
                continue

            if mapping.get("source_id") == "shornikov-2017-spinel-evaporation":
                expected_by_locator: Counter[tuple[str, str, Decimal]] = Counter()
                for observation in source_observations:
                    values = observation.get("values") or {}
                    header = str(
                        (observation.get("locator") or {}).get("equation") or ""
                    )
                    for point in values.get("points") or []:
                        row = point.get("T_K_as_printed", point.get("T_K"))
                        value = _decimal(point.get("pressure_atm"))
                        if row is not None and header and value is not None:
                            expected_by_locator[(str(row), header, value)] += 1
                if expected_by_locator:
                    _assert_locator_values_match(
                        document=document,
                        page=int(table["page"]),
                        table_index=int(table["table_index"]),
                        columns=columns,
                        quantity=quantity,
                        species=species,
                        expected=expected_by_locator,
                        source="Shornikov 2017",
                    )
                    continue

            if mapping.get("source_id") == "ueshima-1982-fe-mo-thermal":
                source_column_numbers = {
                    **{
                        f"Tc (°C), reading {reading}": 3 + reading
                        for reading in range(1, 4)
                    },
                    **{
                        f"Tr (°C), reading {reading}": 6 + reading
                        for reading in range(1, 4)
                    },
                }
                source_rows: dict[str, dict[int, Decimal]] = defaultdict(dict)
                for observation in source_observations:
                    values = observation.get("values") or {}
                    for point in values.get("series") or []:
                        locator = point.get("locator") or {}
                        row = locator.get("row")
                        column = source_column_numbers.get(
                            str(locator.get("column") or "")
                        )
                        value = _decimal(point.get("T_C"))
                        assert row and column in columns and value is not None
                        source_rows[str(row)][column] = value
                emitted_rows: dict[str, dict[int, Decimal]] = defaultdict(dict)
                emitted_headers: dict[int, str] = {}
                for emitted_species, block in (document.get("species") or {}).items():
                    if emitted_species != species:
                        continue
                    for observation in block.get("observations") or []:
                        match = re.search(
                            r"_p(?P<page>\d+)_t(?P<table>\d+)_r\d+_c(?P<column>\d+)$",
                            str(observation.get("observation_id") or ""),
                        )
                        if (
                            not match
                            or int(match.group("page")) != int(table["page"])
                            or int(match.group("table")) != int(table["table_index"])
                            or int(match.group("column")) not in columns
                            or (observation.get("values") or {}).get("quantity")
                            != quantity
                        ):
                            continue
                        locator = observation.get("locator") or {}
                        value = _decimal(_emitted_scalar(observation))
                        if value is not None:
                            column = int(match.group("column"))
                            emitted_rows[str(locator.get("row_label") or "")][
                                column
                            ] = value
                            emitted_headers[column] = str(
                                locator.get("column_header") or ""
                            )
                assert set(emitted_headers) == set(columns)
                expected_by_locator: Counter[
                    tuple[str, str, int, Decimal]
                ] = Counter()
                actual_by_locator: Counter[
                    tuple[str, str, int, Decimal]
                ] = Counter()
                for source_row, cells in source_rows.items():
                    matching_rows = [
                        row_label
                        for row_label, emitted_cells in emitted_rows.items()
                        if emitted_cells == cells
                    ]
                    assert len(matching_rows) == 1, (
                        "Ueshima source row has no unique emitted row by its "
                        f"column/value locator: {source_row} -> {matching_rows}"
                    )
                    row_label = matching_rows[0]
                    for column, value in cells.items():
                        expected_by_locator[
                            (row_label, emitted_headers[column], column, value)
                        ] += 1
                for row_label, cells in emitted_rows.items():
                    for column, value in cells.items():
                        actual_by_locator[
                            (row_label, emitted_headers[column], column, value)
                        ] += 1
                assert actual_by_locator == expected_by_locator, (
                    "Ueshima 1982 cells differ by row_label, column_header, "
                    f"and mapped column: expected={expected_by_locator}, "
                    f"emitted={actual_by_locator}"
                )
                continue

            if not source_observations:
                # This mapping group has no landed numeric observations to compare.
                continue
            assert len(source_observations) == len(columns), (
                f"duplicate-column group lacks locator-level values: {species} "
                f"{quantity} columns={columns}, observations={len(source_observations)}"
            )
            for column, source_observation in zip(columns, source_observations):
                expected_values = [
                    number
                    for value in _field_values(source_observation, quantity)
                    if (number := _decimal(value)) is not None
                ]
                output_values = emitted_by_column.get(
                    (quantity, species, int(table["page"]), int(table["table_index"]), column),
                    [],
                )
                assert output_values == expected_values, (
                    f"mapped column {column} row values differ for {species}: "
                    f"expected {expected_values}, emitted {output_values}"
                )


def _assert_per_column_method_classes(mapping: dict, extract: dict, document: dict) -> None:
    for table in mapping.get("tables") or []:
        for measurement in table.get("values") or []:
            if "evidence_class" not in measurement:
                continue
            quantity = str(measurement.get("quantity") or table["quantity"])
            species = str(measurement["species"])
            expected_class = str(measurement["evidence_class"])
            source_observations = [
                observation
                for observation in (extract.get("species") or {}).get(species, {}).get(
                    "observations", []
                )
                if str((observation.get("values") or {}).get("quantity") or "") == quantity
                and _table_matches_observation(table, observation)
            ]
            assert any(
                str((observation.get("values") or {}).get("method_class") or "")
                == expected_class
                for observation in source_observations
            ), (
                f"mapped method_class {expected_class} is absent from landed "
                f"{species} observations on page {table['page']}"
            )
            emitted_count = 0
            for emitted_species, block in (document.get("species") or {}).items():
                if str(emitted_species) != species:
                    continue
                for observation in block.get("observations") or []:
                    match = re.search(
                        r"_p(?P<page>\d+)_t(?P<table>\d+)_r\d+_c(?P<column>\d+)$",
                        str(observation.get("observation_id") or ""),
                    )
                    values = observation.get("values") or {}
                    if (
                        match
                        and int(match.group("page")) == int(table["page"])
                        and int(match.group("table")) == int(table["table_index"])
                        and int(match.group("column")) == int(measurement["column"])
                        and str(values.get("quantity") or "") == quantity
                    ):
                        emitted_count += 1
                        assert str(values.get("method_class") or "") == expected_class
            assert emitted_count, (
                f"no emitted {species} cells for mapped column {measurement['column']}"
            )


@pytest.mark.parametrize(
    "mapping_path",
    MAPPINGS,
    ids=lambda path: path.name.removesuffix(".mapping.yaml"),
)
def test_committed_emit_mappings_reproduce_landed_decimal_cells(
    mapping_path: Path, tmp_path: Path
) -> None:
    corpus_root, raw_root = _corpus_roots()
    if not corpus_root.is_dir() or not raw_root.is_dir():
        pytest.skip(f"regolith-corpus checkout is absent: {corpus_root}")

    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    if not source.is_dir():
        pytest.fail(f"MinerU source directory is absent for {source_id}: {source}")
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    output_path = tmp_path / f"{source_id}.yaml"
    if not mapping.get("tables"):
        with pytest.raises(ValueError, match="tables must be a non-empty list"):
            draft.emit_extract(draft_dir, mapping_path, output_path)
        return

    document = draft.emit_extract(draft_dir, mapping_path, output_path)
    extract_path = EXTRACTS / f"{source_id}.yaml"
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    expected = _extract_targets(mapping, extract)
    emitted = _emitted_cells(document)
    assert expected, f"no landed numeric cells found for mapped source {source_id}"
    _assert_exact_landed_values(expected, emitted)
    _assert_emitted_cells_bind_to_mapping(mapping, draft_dir, document)
    _assert_duplicate_columns_match_sources(mapping, extract, document)
    _assert_per_column_method_classes(mapping, extract, document)
    for observation in (
        item
        for block in (document.get("species") or {}).values()
        for item in block.get("observations") or []
    ):
        quantity = observation["values"]["quantity"]
        if quantity == "activity":
            assert "activity" in observation["values"]
            assert "value" not in observation["values"]


def test_wrong_printed_scale_direction_fails_landed_value_comparison(
    tmp_path: Path,
) -> None:
    mapping_path = FIXTURES / "stolyarova-1996-cao-alumina-silica-kems.mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    corpus_root, raw_root = _corpus_roots()
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)

    wrong_mapping = copy.deepcopy(mapping)
    scale_factor = next(
        value["scale_factor"]
        for table in wrong_mapping["tables"]
        for value in table.get("values", [])
        if "scale_factor" in value
    )
    scale_factor["direction"] = "cell_times_factor_equals_quantity"
    wrong_mapping_path = tmp_path / "wrong-direction.mapping.yaml"
    wrong_mapping_path.write_text(
        yaml.safe_dump(wrong_mapping, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    document = draft.emit_extract(
        draft_dir, wrong_mapping_path, tmp_path / "wrong-direction.yaml"
    )

    extract = yaml.safe_load(
        (EXTRACTS / f"{source_id}.yaml").read_text(encoding="utf-8")
    )
    expected = _extract_targets(mapping, extract)
    emitted = _emitted_cells(document)
    assert any(
        counts - emitted.get(key, Counter())
        for key, counts in expected.items()
    ), "the wrong direction unexpectedly reproduces landed values"


def test_column_headers_preserve_sample_and_mass_spectrum_groups() -> None:
    kems_rows = [
        [
            "Sample Element (ppm)",
            "10017 Basalt", "10017 Basalt",
            "10073 Breccia", "10073 Breccia",
            "12022 Basalt", "12022 Basalt",
        ],
        [
            "Sample Element (ppm)",
            "Initial", "1400°C 2 hr",
            "Initial", "1400°C 2 hr",
            "Initial", "950°C 2 hr",
        ],
    ]
    kems_headers = draft._column_headers(kems_rows, {1, 3, 5}, 7)
    landed_k = yaml.safe_load(
        (EXTRACTS / "kems-024-gibson-hubbard-1972.yaml").read_text(
            encoding="utf-8"
        )
    )
    k_observations = landed_k["species"]["K"]["observations"]
    expected_kems_headers: dict[int, str] = {}
    for column, sample, quote in (
        (2, "10017", "K | 10017 | Initial | 2610"),
        (4, "10073", "K | 10073 | Initial | 1200"),
        (6, "12022", "K | 12022 | Initial | 536"),
    ):
        observation = next(
            item
            for item in k_observations
            if (item.get("values") or {}).get("sample_number") == sample
            and (item.get("values") or {}).get("condition_as_printed") == "Initial"
        )
        values = observation["values"]
        assert observation["quote"] == quote
        expected_header = (
            f"{sample} {values['sample_form'].title()} "
            f"{values['condition_as_printed']}"
        )
        expected_kems_headers[column] = expected_header
    assert {
        column: kems_headers[column - 1] for column in expected_kems_headers
    } == expected_kems_headers

    vapor_group = "Mass spectra of vapor (relative units)"
    ternary = r"$CaO \cdot Al_{2}O_{3} \cdot SiO_{2}$"
    spectrum_rows = [
        ["Ions in the vapor mass spectra", *([vapor_group] * 6)],
        [
            "Ions in the vapor mass spectra",
            ternary, ternary, ternary,
            "$CaO$", "$Al_{2}O_{3}$", "$SiO_{2}$",
        ],
    ]
    spectrum_headers = draft._column_headers(spectrum_rows, {1, 3}, 7)
    landed_spectrum = yaml.safe_load(
        (EXTRACTS / "stolyarova-1996-cao-alumina-silica-kems.yaml").read_text(
            encoding="utf-8"
        )
    )
    spectrum_observations = landed_spectrum["species"]["MoO2"]["observations"]
    for output_column, printed_column in ((2, 1), (4, 3)):
        observation = next(
            item
            for item in spectrum_observations
            if (item.get("values") or {}).get("table_column_as_printed")
            == printed_column
        )
        assert f"this is column {printed_column}" in observation["locator"]["note"]
        assert "printed group header 'CaO · Al2O3 · SiO2'" in observation[
            "locator"
        ]["note"]
        header = spectrum_headers[output_column - 1]
        assert header == f"{vapor_group} {ternary}"


def test_shornikov_2016_spanning_headers_resolve_each_gibbs_column(
    tmp_path: Path,
) -> None:
    mapping_path = HEADER_FIXTURES / "shornikov-2016-cao-mgo-silicate.mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    corpus_root, raw_root = _corpus_roots()
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    if not source.is_dir():
        pytest.skip(f"MinerU source directory is absent for {source_id}: {source}")
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    document = draft.emit_extract(
        draft_dir, mapping_path, tmp_path / "shornikov-2016.yaml"
    )

    expected_headers = {
        2: "Solid phases $\\Delta_f G^{\\circ}_{2000},$  kJ/mole",
        4: "Liquid phases $\\Delta_f G^{\\circ}_{2000},$  kJ/mole",
        6: "Vapor species $\\Delta_f G^{\\circ}_{2000},$  kJ/mole",
    }
    seen: Counter[int] = Counter()
    for block in (document.get("species") or {}).values():
        for observation in block.get("observations") or []:
            match = re.search(
                r"_p002_t02_r\d+_c(?P<column>\d+)$",
                str(observation.get("observation_id") or ""),
            )
            if not match:
                continue
            column = int(match.group("column"))
            assert column in expected_headers
            assert observation["locator"]["column_header"] == expected_headers[column]
            seen[column] += 1

    assert seen == Counter({2: 13, 4: 8, 6: 17})


def test_van_limpt_extract_column_swap_fails_by_row_and_header(
    tmp_path: Path,
) -> None:
    mapping_path = FIXTURES / "kems-046-van-limpt-2007.mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    corpus_root, raw_root = _corpus_roots()
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    if not source.is_dir():
        pytest.skip(f"MinerU source directory is absent for {source_id}: {source}")
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    document = draft.emit_extract(draft_dir, mapping_path, tmp_path / "van-limpt.yaml")
    extract = yaml.safe_load(
        (EXTRACTS / f"{source_id}.yaml").read_text(encoding="utf-8")
    )
    swapped_extract = copy.deepcopy(extract)
    observation = next(
        item
        for item in swapped_extract["species"]["Na"]["observations"]
        if (item.get("values") or {}).get("quantity")
        == "sodium_disilicate_total_weight_loss"
        and _table_matches_observation(mapping["tables"][0], item)
    )
    first_row = next(
        row for row in observation["values"]["rows"] if row["temperature_C"] == 1416
    )
    assert (
        first_row["extractive_weight_loss_mg"],
        first_row["gravimetric_weight_loss_mg"],
    ) == (112, 125)
    first_row["extractive_weight_loss_mg"], first_row["gravimetric_weight_loss_mg"] = (
        first_row["gravimetric_weight_loss_mg"],
        first_row["extractive_weight_loss_mg"],
    )

    with pytest.raises(
        AssertionError, match="row_label and column_header"
    ):
        _assert_duplicate_columns_match_sources(mapping, swapped_extract, document)


def test_van_limpt_numeric_header_values_compare_to_their_cells(
    tmp_path: Path,
) -> None:
    mapping_path = FIXTURES / "kems-046-van-limpt-2007.mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    corpus_root, raw_root = _corpus_roots()
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    if not source.is_dir():
        pytest.skip(f"MinerU source directory is absent for {source_id}: {source}")
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    extract = {"species": {"Na": {"observations": [
        {
            "observation_id": f"van_limpt_{source_id}_p158_t11_r02_c{column:02d}",
            "locator": {"pdf_page_index": 157, "table": "11"},
            "values": {"value": value},
        }
        for column, value in ((2, 112), (3, 125))
    ]}}}
    compared = draft.compare_extract_to_draft(
        extract, draft.resolve_draft_index(draft_dir, source_id), mapping
    )
    first_row = {
        int(match.group("column")): row
        for row in compared
        if (
            match := re.search(
                r"_p158_t11_r02_c(?P<column>\d+)$",
                str(row["observation_id"]),
            )
        )
    }

    assert {column: row["value"] for column, row in first_row.items()} == {
        2: 112.0,
        3: 125.0,
    }
    assert all(row["status"] == "matched" for row in first_row.values())
    assert all(row["matched_cell"]["row"] == 2 for row in first_row.values())


def test_backtest_rejects_extra_cells_and_same_key_column_swaps(
    tmp_path: Path,
) -> None:
    mapping_path = FIXTURES / "stolyarova-1995-cao-alumina-kems.mapping.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    corpus_root, raw_root = _corpus_roots()
    source_id = mapping["source_id"]
    source = _source_dir(corpus_root, raw_root, source_id)
    draft_dir = _convert_source(source, source_id, tmp_path, raw_root)
    document = draft.emit_extract(
        draft_dir, mapping_path, tmp_path / "stolyarova-1995.yaml"
    )
    extract = yaml.safe_load(
        (EXTRACTS / f"{source_id}.yaml").read_text(encoding="utf-8")
    )
    expected = _extract_targets(mapping, extract)

    extra_document = copy.deepcopy(document)
    oxygen_observations = extra_document["species"]["O"]["observations"]
    extra_cell = copy.deepcopy(
        next(
            observation
            for observation in oxygen_observations
            if observation["values"]["quantity"] == "partial_pressure"
        )
    )
    extra_cell["values"]["pressure_atm"] = "0.123"
    oxygen_observations.append(extra_cell)
    with pytest.raises(AssertionError, match="extra="):
        _assert_exact_landed_values(expected, _emitted_cells(extra_document))

    swapped_document = copy.deepcopy(document)
    by_column_and_row: dict[tuple[int, int], dict] = {}
    for observation in swapped_document["species"]["O"]["observations"]:
        match = re.search(
            r"_p\d+_t\d+_r(?P<row>\d+)_c(?P<column>\d+)$",
            observation["observation_id"],
        )
        if not match or observation["values"]["quantity"] != "partial_pressure":
            continue
        key = (int(match.group("column")), int(match.group("row")))
        by_column_and_row[key] = observation
    row = next(
        row
        for row in sorted({row for _, row in by_column_and_row})
        if (5, row) in by_column_and_row and (6, row) in by_column_and_row
    )
    first = by_column_and_row[(5, row)]["values"]
    second = by_column_and_row[(6, row)]["values"]
    first["pressure_atm"], second["pressure_atm"] = (
        second["pressure_atm"],
        first["pressure_atm"],
    )
    assert _emitted_cells(swapped_document) == expected
    with pytest.raises(AssertionError, match="mapped column"):
        _assert_duplicate_columns_match_sources(mapping, extract, swapped_document)
