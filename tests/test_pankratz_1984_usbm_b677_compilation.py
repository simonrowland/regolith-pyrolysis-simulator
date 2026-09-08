"""Completeness, source round-trip, OCR, and exact-grid contracts for B677."""

import json
import hashlib
import re
from html.parser import HTMLParser

import pytest
import yaml

from simulator.reference_data.pankratz_1984_usbm_b677_loader import (
    COMPILATION_ROOT,
    PrintedTemperatureUnavailable,
    load_manifest,
    load_records,
    lookup_temperature,
)


STANDARD_HEADINGS = ("T", "$Cp^o$", "$S^o$", "$H^o-H_{298}^o$", "$\\Delta Hf^o$", "$\\Delta Gf^o$")
IMAGE_VERIFIED_IDENTITIES = {
    "table-0007": ("Ag", "Silver", "(c,1)", STANDARD_HEADINGS[:4]),
    "table-0039": ("C4", "Carbon (ideal tetratomic gas)", "(g)", STANDARD_HEADINGS),
    "table-0097": ("Kr", "Krypton", "(g)", ("T", "Cp°", "S°", "H°-H°298")),
    "table-0224": ("Zr", "Zirconium (ideal monatomic gas)", "(g)", STANDARD_HEADINGS),
    "table-0333": ("(MgBr2)2", "Dimeric magnesium dibromide (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-0492": ("BrCl", "Bromine monochloride (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-0518": ("CuCl", "Copper monochloride", "(c,1)", STANDARD_HEADINGS),
    "table-0687": ("BeF2", "Beryllium difluoride", "(c,l)", STANDARD_HEADINGS),
    "table-0810": ("PF5", "Phosphorus pentafluoride (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-0984": ("FeI2", "Iron diiodide (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-1051": ("TiI4", "Titanium tetraiodide (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-1079": ("ErN", "Erbium nitride", "(c)", STANDARD_HEADINGS),
    "table-1185": ("H2O", "Dihydrogen monoxide, water", "(l,g)", STANDARD_HEADINGS),
    "table-1254": ("SO2", "Sulfur dioxide (ideal gas)", "(g)", STANDARD_HEADINGS),
    "table-1301": ("VO", "Vanadium monoxide", "(c)", STANDARD_HEADINGS),
    "table-1439": ("NiSO4", "Nickel sulfate", "(c)", STANDARD_HEADINGS),
    "table-1500": ("K2S", "Potassium monosulfide", "(c,1)", STANDARD_HEADINGS),
    "table-1513": ("Ni3S4", "Trinickel tetrasulfide", "(c)", STANDARD_HEADINGS),
    "table-1555": ("GeTe", "Germanium monotelluride", "(c,1)", STANDARD_HEADINGS),
    "table-1571": ("ZnTe", "Zinc monotelluride", "(c)", STANDARD_HEADINGS),
}

IMAGE_VERIFIED_NOTE_BLOCKS = {
    "table-0050": "\\*All data except fusion   \ntemperature estimated.   \n1550 K, transition point; $\\Delta H^{\\circ} = 0.775$   \n1618 K, melting point; $\\Delta H^{\\circ} = 3.500$",
    "table-0198": "\\*Data extrapolated 1100 - 1262 K.   \n722.65 K, melting point; $\\Delta H^{\\circ} = 4.180$   \n1262 K, boiling point; mixed polymeric gases.",
    "table-0352": "\\*Data estimated",
    "table-0500": "457.6 K, sublimation point; $\\Delta H^{\\circ} = 12.2$",
    "table-0652": "247 K, melting point; $\\Delta H^{\\circ} = 0.550$\n\n428 K, boiling point; $\\Delta H^{\\circ} = 9.5$",
    "table-0800": "1650 K, melting point; $\\Delta H^{\\circ} = 13.100$",
    "table-0951": "\\*Data estimated.",
    "table-1100": "\\*Data except enthalpy of formation at 298 K estimated.",
    "table-1251": "413 K, transition point; $\\Delta H^{\\circ} = 0.020$   \n600 K, melting point; $\\Delta H^{\\circ} = 15.699$",
    "table-1400": "1490 K, transition point; $\\Delta H^{\\circ} = 1.79$   \n1560 K, melting point: $\\Delta H^{\\circ} = 10.60$",
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def source_tables(path):
    source = path.read_text(encoding="utf-8")
    result = []
    for match in re.finditer(r"<table>[\s\S]*?</table>", source):
        parser = TableParser()
        parser.feed(match.group())
        result.append(parser.rows)
    return result


def source_table_matches(path):
    source = path.read_text(encoding="utf-8")
    return source, list(re.finditer(r"<table>[\s\S]*?</table>", source))


def independent_note_block(inter_table_text, record, next_record):
    if record["page"] < 47:
        return ""
    boundary = len(inter_table_text)
    caption = re.search(r"(?m)^\s*TABLE\s+\d+", inter_table_text)
    if caption:
        boundary = caption.start()
    if next_record is not None:
        name_raw = next_record.get("name_heading_raw") or ""
        name_position = inter_table_text.rfind(name_raw) if name_raw else -1
        formula_raw = next_record.get("formula_heading_raw") or ""
        formula_position = inter_table_text.rfind(formula_raw, 0, name_position + 1) if formula_raw else -1
        identity_position = formula_position if formula_position >= 0 else name_position
        if identity_position >= 0 and identity_position < boundary:
            boundary = inter_table_text.rfind("\n", 0, identity_position) + 1
    lines = inter_table_text[:boundary].splitlines()
    lines = [line for line in lines if not line.lstrip().startswith("#")]
    raw = "\n".join(lines).strip()
    lines = raw.splitlines()
    section_start = next((
        index for index, line in enumerate(lines)
        if re.match(r"\s*(?:Units:|Sources of data from general references|1\.\s+Barin,)", line)
    ), len(lines))
    return "\n".join(lines[:section_start]).strip()


def assert_image_verified_fixture(records):
    by_id = {record["record_id"]: record for record in records}
    for record_id, expected in IMAGE_VERIFIED_IDENTITIES.items():
        record = by_id[record_id]
        actual = (
            record["formula_as_published"],
            record["name_as_published"],
            record["phase_as_published"],
            tuple(column["heading_as_published"] for column in record["columns"]),
        )
        assert actual == expected
    for record_id, expected in IMAGE_VERIFIED_NOTE_BLOCKS.items():
        note = by_id[record_id]["post_table_note_block"]
        assert note["raw"] == expected
        assert note["ocr_suspect"] is False
        assert note["ocr_check"] == "image_verified_fixture_agreement"


def test_round_trip_reparses_mineru_source_and_compares_every_printed_token():
    cache = {}
    for record in load_records():
        locator = record["source_locator"]
        path = COMPILATION_ROOT / locator["file"]
        tables = cache.setdefault(path, source_tables(path))
        printed = tables[locator["table_index"] - 1]
        assert printed == record["source_table_rows_raw"]
        for row in record["rows"]:
            for cell in row["cells"]:
                assert isinstance(cell["raw"], str)
                source_row = cell["source_row_index"]
                source_column = cell["source_column_index"]
                if source_row is None:
                    assert source_column is None and cell["raw"] == ""
                else:
                    assert cell["raw"] == printed[source_row][source_column]


def test_image_verified_heading_identity_and_note_fixtures():
    records = list(load_records())
    assert len(IMAGE_VERIFIED_IDENTITIES) >= 20
    assert len(IMAGE_VERIFIED_NOTE_BLOCKS) >= 10
    assert_image_verified_fixture(records)


def test_every_record_maps_bijectively_to_its_complete_source_block():
    records = list(load_records())
    by_file = {}
    for record in records:
        by_file.setdefault(record["source_locator"]["file"], []).append(record)
    seen = set()
    for relative_path, file_records in by_file.items():
        source, matches = source_table_matches(COMPILATION_ROOT / relative_path)
        assert len(matches) == len(file_records)
        file_records.sort(key=lambda record: record["source_locator"]["table_index"])
        for index, (record, match) in enumerate(zip(file_records, matches, strict=True), 1):
            locator = record["source_locator"]
            assert locator["table_index"] == index
            assert record["record_id"] not in seen
            seen.add(record["record_id"])
            next_record = file_records[index] if index < len(file_records) else None
            next_start = matches[index].start() if index < len(matches) else len(source)
            note_raw = independent_note_block(source[match.end():next_start], record, next_record)
            assert note_raw == record["post_table_note_block"]["raw"]
            assert hashlib.sha256(note_raw.encode()).hexdigest() == locator["note_block_sha256"]
            complete = match.group() + "\n" + note_raw
            assert hashlib.sha256(complete.encode()).hexdigest() == locator["complete_source_block_sha256"]
    assert seen == {record["record_id"] for record in records}


def test_clean_heading_tokens_are_backed_by_page_image_comparison():
    for record in load_records():
        tokens = [record["formula_token"], record["name_token"], record["phase_token"]]
        tokens.extend(column["heading_token"] for column in record["columns"])
        for token in tokens:
            if not token["ocr_suspect"]:
                assert token["ocr_check"] == "raster_heading_region_token_agreement"


def test_manifest_record_and_toc_census_coverage_are_identical():
    manifest = load_manifest()
    records = list(load_records())
    census = json.loads((COMPILATION_ROOT / "census.json").read_text(encoding="utf-8"))
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids)) == 1571
    assert len(records) == manifest["summary"]["record_count"] == census["record_count"]
    assert ids == census["record_ids"]
    assert census["found_count"] == census["transcribed_count"] == 1571
    assert census["untranscribed_count"] == 0
    assert sum(section["record_count"] for section in census["sections"]) == 1571
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False


def test_feedstock_element_coverage_uses_existing_coverage_contract():
    from tools.harvest_atct_compilation import feedstock_coverage

    records = [{"formula": record.get("formula_as_published") or ""} for record in load_records()]
    assert feedstock_coverage(records) == load_manifest()["feedstock_element_coverage"]


def test_every_compilation_yaml_and_access_status_parse():
    yaml_paths = list(COMPILATION_ROOT.rglob("*.yaml"))
    yaml_paths.append(COMPILATION_ROOT.parent / "access-status.yaml")
    assert yaml_paths
    for path in yaml_paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path


def test_every_grid_token_is_a_printed_first_column_token():
    for record in load_records():
        assert record["temperature_grid"] == [row["cells"][0] for row in record["rows"]]
        for grid_cell in record["temperature_grid"]:
            assert grid_cell["value"] is not None
        assert lookup_temperature(record["record_id"], record["temperature_grid"][0]["value"])


@pytest.mark.parametrize("temperature", [0, 298.1, 299, 1234.567, float("nan"), float("inf")])
def test_refuses_non_grid_temperature_without_interpolation_or_default(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-0007", temperature)


def test_transition_duplicates_and_footnote_marker_are_preserved():
    transition = lookup_temperature("table-0007", 1235.0)
    assert len(transition) == 2
    extrapolated = lookup_temperature("table-0007", 1800.0)
    assert extrapolated[0]["cells"][0]["raw"] == "1800*"
    assert extrapolated[0]["cells"][0]["footnote_markers"] == ["*"]


def test_horizontal_chapter_one_grid_is_transcribed_without_guessing():
    record = list(load_records())[3]
    assert record["record_id"] == "table-0004"
    assert record["source_orientation"] == "transposed_for_temperature_lookup"
    assert [row["cells"][0]["raw"] for row in record["rows"]] == ["911", "990", "1121"]
    assert lookup_temperature("table-0004", 990.0)[0]["cells"][1]["raw"] == "2.663"


def test_ocr_t_header_is_not_promoted_to_a_one_kelvin_grid_row():
    record = list(load_records())[392]
    assert record["record_id"] == "table-0393"
    assert record["header_rows_raw"][0][0] == "1"
    assert record["temperature_grid"][0]["raw"] == "298*"
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("table-0393", 1.0)


def test_ocr_disagreements_remain_raw_and_corrections_are_ledgered():
    manifest = load_manifest()
    assert manifest["summary"]["ocr_suspect_count"] > 0
    assert manifest["summary"]["identity_check_disagreement_count"] > 0
    assert manifest["summary"]["correction_count"] == 7
    corrections = {(item["record_id"], item["field"], item.get("column_index")): item for item in manifest["corrections"]}
    assert corrections[("table-0393", "name_as_published", None)]["printed_token"] == "Tungsten hexabromide (ideal gas)"
    assert corrections[("table-0984", "column_heading", 0)]["printed_token"] == "T"
    assert corrections[("table-1051", "formula_as_published", None)]["printed_token"] == "TiI4"
    assert corrections[("table-1079", "column_heading", 0)]["printed_token"] == "T"
    assert corrections[("table-1167", "formula_as_published", None)]["printed_token"] == "Er2O3"
    assert corrections[("table-1301", "formula_as_published", None)]["printed_token"] == "VO"
    assert corrections[("table-1439", "formula_as_published", None)]["printed_token"] == "NiSO4"
    suspects = [cell for record in load_records() for row in record["rows"] for cell in row["cells"] if cell["ocr_suspect"]]
    assert suspects
    assert all(cell["ocr_check"] == "raster_table_bbox_token_disagreement" for cell in suspects)


def test_post_table_notes_preserve_raw_prose_and_structured_meaning():
    manifest = load_manifest()
    assert manifest["summary"]["post_table_note_record_count"] > 500
    assert manifest["summary"]["estimate_note_count"] > 250
    assert manifest["summary"]["uncertainty_count"] > 0
    assert manifest["summary"]["transition_count"] > 450
    records = {record["record_id"]: record for record in load_records()}
    assert "All data except enthalpy of formation at 298 K estimated" in records["table-0039"]["post_table_note_block"]["raw"]
    transitions = records["table-0518"]["post_table_note_block"]["transitions"]
    assert [(item["temperature_K"], item["kind"], item["enthalpy_as_published"]) for item in transitions] == [
        (685.0, "transition", 1.165),
        (696.0, "melting", 1.693),
    ]
    uncertain = records["table-0280"]["post_table_note_block"]["transitions"][-1]
    assert (uncertain["temperature_K"], uncertain["temperature_uncertainty_K"], uncertain["kind"]) == (781.0, 15.0, "melting")
    assert uncertain["enthalpy_uncertainty_as_published"] == 2.0
    form_change = records["table-1182"]["post_table_note_block"]["transitions"]
    assert [(item["temperature_K"], item["kind"]) for item in form_change] == [(1308.0, "transition")]
    assert records["table-0224"]["post_table_note_block"]["raw"] == ""
    assert all(not record["post_table_note_block"]["raw"].lstrip().startswith("Units:") for record in records.values())


def test_unknown_record_refuses_without_default():
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 298.0)
