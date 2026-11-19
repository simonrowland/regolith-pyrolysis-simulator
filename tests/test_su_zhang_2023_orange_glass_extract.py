"""Source and migration pins for Su & Zhang (2023) supplementary profiles."""

from __future__ import annotations

import csv
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path
import re
import shutil
from time import perf_counter

from openpyxl import load_workbook
import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator, to_plain
from simulator.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    resolve_field_path,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SID = "su-zhang-2023-lunar-orange-glass-na-k-cu-degassing"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
AUDIT = ROOT / "data/literature/extracts/audits"
TABLE_ASSET = f"tables:{SID}"
THESIS_SID = "su-2024-lunar-volatiles-thesis"
THESIS_SHA = "471f14fd7cf2c51aacb54d7104b94b893512c9bda1936b519e6fb03675eaa401"
THESIS_ASSET = f"raw/{THESIS_SID}/{THESIS_SID}.pdf"
TABLE_3_1_ASSET = "tables/su-2024-lunar-volatiles-thesis/t3-1.csv"
WORKBOOK_ASSET = f"raw/{SID}/{SID}.xlsx"


def _corpus_file(relative: str) -> Path:
    return regolith_corpus_root() / relative


def _assert_thesis_chapter(document: dict) -> None:
    source = document["source"]
    article = source["article_text"]
    assert source["corpus_source_id"] == THESIS_SID
    assert article["corpus_asset"] == THESIS_ASSET
    assert article["sha256"] == THESIS_SHA
    assert article["printed_page_start"] == 44
    assert article["printed_page_end"] == 71
    assert article["physical_pdf_page_start"] == 59
    assert article["physical_pdf_page_end"] == 86
    assert article["pdf_page_index_start"] == 58
    assert article["pdf_page_index_end"] == 85
    assert article["printed_page_start"] == article["physical_pdf_page_start"] - 15
    assert article["printed_page_end"] == article["physical_pdf_page_end"] - 15
    assert article["pdf_page_index_start"] == article["physical_pdf_page_start"] - 1
    assert article["pdf_page_index_end"] == article["physical_pdf_page_end"] - 1
    assert "117924" in article["identification"]
    status = source["source_pdf_status"]
    assert "Article PDF not present" not in status
    assert "Chapter 3" in status
    assert "44-71" in status
    assert "59-86" in status


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _migrated_point(migrator: Migrator, parent_id: str, point_suffix: str):
    return next(
        row
        for row in migrator.result.observations.values()
        if f"::{parent_id}::{point_suffix}" in row.observation_id
    )


_MATRIX_TARGET = re.compile(r"^(.*\.matrix_as_printed)\[(\d+)\]\[(\d+)\]$")


def _resolve_audit_target(document: dict, path: str):
    matrix_match = _MATRIX_TARGET.fullmatch(path)
    if matrix_match:
        matrix = resolve_field_path(document, matrix_match.group(1))
        return matrix[int(matrix_match.group(2))][int(matrix_match.group(3))]
    return resolve_field_path(document, path)


def _assert_cell_audit_matches(document: dict, cell_audit: list[dict]) -> None:
    for row in cell_audit:
        assert float(row["xml_stored_value"]) == float(row["openpyxl_value"]), (
            row["sheet"], row["cell"], row["xml_stored_value"], row["openpyxl_value"]
        )
        for target in row["extract_target"].split(" | "):
            actual = str(_resolve_audit_target(document, target))
            assert actual == row["displayed_value"], (
                f"{row['sheet']}!{row['cell']} target {target}: "
                f"extract has {actual!r}, workbook displays {row['displayed_value']!r}"
            )


def _source_display_value(cell) -> str:
    value = cell.value
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    significant_digits = format(value, ".15g")
    number_format = cell.number_format or "General"
    if number_format == "General":
        return significant_digits
    match = re.fullmatch(r"0(?:\.(0+))?", number_format)
    assert match, (cell.parent.title, cell.coordinate, number_format)
    places = len(match.group(1) or "")
    rounded = Decimal(significant_digits).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )
    return f"{rounded:.{places}f}"


def _source_display_mismatches(document: dict, cell_audit: list[dict], workbook):
    mismatches = []
    target_checks = 0
    for row in cell_audit:
        source_value = _source_display_value(workbook[row["sheet"]][row["cell"]])
        if row["displayed_value"] != source_value:
            mismatches.append(
                f"{row['sheet']}!{row['cell']} audit: {row['displayed_value']!r} != {source_value!r}"
            )
        for target in row["extract_target"].split(" | "):
            target_checks += 1
            extract_value = str(_resolve_audit_target(document, target))
            if extract_value != source_value:
                mismatches.append(
                    f"{row['sheet']}!{row['cell']} target {target}: "
                    f"{extract_value!r} != {source_value!r}"
                )
    return mismatches, target_checks


def test_audit_target_resolver_supports_matrix_indices() -> None:
    document = {
        "species": {
            "unknown": {
                "context": [
                    {
                        "observation_id": "map",
                        "values": {"matrix_as_printed": [["a", "b"], ["c", "d"]]},
                    }
                ]
            }
        }
    }
    assert (
        _resolve_audit_target(
            document,
            "species.unknown.context[map].values.matrix_as_printed[1][0]",
        )
        == "c"
    )


def test_extract_validates_and_fidelity_mutation_is_detected() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SID) == []

    mutated = deepcopy(document)
    _observation(
        mutated, "SiO2", "su_zhang_2023_table_s2_empa_sio2_profile"
    )["values"]["series"][0]["SiO2_wt_pct"] = "38.7"
    assert check_all_fidelity_samples_match(mutated, label=f"mutated {SID}")


def test_every_cell_audit_value_matches_independent_workbook_display() -> None:
    workbook_path = _corpus_file(WORKBOOK_ASSET)
    if not workbook_path.is_file():
        pytest.skip(f"corpus workbook not available: {workbook_path}")

    started = perf_counter()
    document = _document()
    with (AUDIT / f"{SID}.cells.csv").open(encoding="utf-8", newline="") as stream:
        cell_audit = list(csv.DictReader(stream))
    workbook = load_workbook(workbook_path, data_only=True, read_only=False)
    assert len(cell_audit) == 7_777

    mismatches, target_checks = _source_display_mismatches(
        document, cell_audit, workbook
    )
    elapsed = perf_counter() - started
    print(
        f"FULL_CELL_SOURCE_GUARD cells={len(cell_audit)} target_checks={target_checks} "
        f"mismatches={len(mismatches)} runtime_seconds={elapsed:.3f}"
    )
    assert target_checks == 14_679
    assert not mismatches, "\n".join(mismatches[:20])

    expected_pins = {
        "D8": "5.72",
        "K13": "0.062",
        "C158": "8.81",
        "K91": "0.063",
        "G13": "0.267",
    }
    rows_by_cell = {
        row["cell"]: row
        for row in cell_audit
        if row["sheet"] == "Table S2" and row["cell"] in expected_pins
    }
    assert set(rows_by_cell) == set(expected_pins)
    for cell, expected in expected_pins.items():
        audit_row = rows_by_cell[cell]
        assert _source_display_value(workbook["Table S2"][cell]) == expected
        assert audit_row["displayed_value"] == expected
        assert all(
            str(_resolve_audit_target(document, target)) == expected
            for target in audit_row["extract_target"].split(" | ")
        )

    # Recreate the prior raw-XML rounding for D8 and keep YAML and CSV wrong together.
    d8_audit_row = rows_by_cell["D8"]
    legacy = Decimal(d8_audit_row["xml_stored_value"]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    legacy_display = f"{legacy:.2f}"
    assert legacy_display == "5.71"
    mutated_document = deepcopy(document)
    al2o3 = _observation(
        mutated_document, "Al2O3", "su_zhang_2023_table_s2_empa_al2o3_profile"
    )
    d8_point = next(
        point
        for point in al2o3["values"]["series"]
        if "D8 displayed" in point["locator"]["note"]
    )
    d8_point["Al2O3_wt_pct"] = legacy_display
    wrong_audit_row = dict(d8_audit_row, displayed_value=legacy_display)
    mutation_mismatches, _ = _source_display_mismatches(
        mutated_document, [wrong_audit_row], workbook
    )
    assert any("audit:" in mismatch for mismatch in mutation_mismatches)
    assert any("target " in mismatch for mismatch in mutation_mismatches)
    print(
        f"LEGACY_ROUNDING_MUTATION_RED Table S2!D8 old={legacy_display} "
        f"source={_source_display_value(workbook['Table S2']['D8'])}"
    )


def test_all_printed_rows_cells_and_maps_are_accounted_for() -> None:
    document = _document()

    expected_s2_counts = {
        "GB-G1": 36,
        "GB-G2": 33,
        "GB-G5": 36,
        "GB-G6": 41,
        "GB-G7": 17,
        "GB-S6": 10,
        "GB3": 20,
        "GB-A": 25,
        "GB-B": 20,
        "GB-C": 25,
    }
    s2_observations = [
        row
        for body in document["species"].values()
        for row in body.get("observations", [])
        if row["observation_id"].startswith("su_zhang_2023_table_s2_empa_")
    ]
    assert len(s2_observations) == 10
    for row in s2_observations:
        assert len(row["values"]["series"]) == 263
        assert Counter(point["sample"] for point in row["values"]["series"]) == expected_s2_counts
        assert row["phase"] == "glass"
        assert row["quench_statement"]["status"] == "not_stated"
        assert row["quench_statement"]["locator"] == {
            "section": "S2",
            "paragraph": "P38",
            "note": "Supplementary DOCX paragraph numbering from OOXML order.",
        }

    # Table S2 prints no Cr2O3/MnO analyses for GB-S6 and GB3; retain those cells as blanks.
    for species, field in (("Cr2O3", "Cr2O3_wt_pct"), ("MnO", "MnO_wt_pct")):
        row = _observation(
            document,
            species,
            f"su_zhang_2023_table_s2_empa_{species.lower()}_profile",
        )
        blanks = [point for point in row["values"]["series"] if point[field] is None]
        assert len(blanks) == 30
        assert Counter(point["sample"] for point in blanks) == {"GB-S6": 10, "GB3": 20}
        assert all(point["sigma"] is None for point in blanks)

    s3 = _observation(
        document, "Na2O", "su_zhang_2023_table_s3_sims_na2o_profile"
    )
    assert len(s3["values"]["series"]) == 61
    assert Counter(point["sample"] for point in s3["values"]["series"]) == {
        "GB-G1": 14,
        "GB-G2": 16,
        "GB-G5": 15,
        "GB-G6": 16,
    }

    s4_counts = {
        "GB-G1": 30,
        "GB-G2": 36,
        "GB-G5": 34,
        "GB-G6": 38,
        "GB-G7": 36,
        "GB-A": 34,
        "GB-B": 26,
        "GB-C": 34,
    }
    for species in ("Na2O", "K2O", "Cu"):
        row = _observation(
            document,
            species,
            f"su_zhang_2023_table_s4_la_icp_ms_{species.lower()}_profile",
        )
        assert len(row["values"]["series"]) == 268
        assert Counter(point["sample"] for point in row["values"]["series"]) == s4_counts
        assert row["values"]["method_class"] == "measured_reduced"

    # Independent workbook-cell pins; these values do not come from the audit CSV.
    s2_first = s2_observations[0]["values"]["series"][0]
    assert s2_first["SiO2_wt_pct"] == "38.8"
    assert "Table S2!A7, B7, N7, C4" in s2_first["locator"]["note"]
    assert "B7 displayed 38.8 (number format 0.0; stored value 38.824199999999998)" in s2_first["locator"]["note"]

    s3_first = s3["values"]["series"][0]
    assert s3_first["Na2O_wt_pct"] == "0.3469"
    assert s3_first["sigma"] == "0.0019"
    assert "Table S3!A6, B6, C6, C3" in s3_first["locator"]["note"]

    s4_cu = _observation(
        document, "Cu", "su_zhang_2023_table_s4_la_icp_ms_cu_profile"
    )["values"]["series"][0]
    assert s4_cu["residue_ppm"] == "5.56"
    assert s4_cu["sigma"] == "0.05"
    assert "Table S4!A11, D11, H11, C7" in s4_cu["locator"]["note"]

    s2_k2o = _observation(
        document, "K2O", "su_zhang_2023_table_s2_empa_k2o_profile"
    )
    k91 = next(
        point for point in s2_k2o["values"]["series"]
        if "K91 displayed" in point["locator"]["note"]
    )
    assert k91["K2O_wt_pct"] == "0.063"
    assert "K91 displayed 0.063 (number format 0.000; stored value 6.25E-2)" in k91["locator"]["note"]

    s2_mno = _observation(
        document, "MnO", "su_zhang_2023_table_s2_empa_mno_profile"
    )
    g13 = next(
        point for point in s2_mno["values"]["series"]
        if "G13 displayed" in point["locator"]["note"]
    )
    assert g13["MnO_wt_pct"] == "0.267"
    assert "G13 displayed 0.267 (number format 0.000; stored value 0.26650000000000001)" in g13["locator"]["note"]

    standards = next(
        row
        for row in document["species"]["unknown"]["context"]
        if row["observation_id"] == "su_zhang_2023_table_s1_empa_standards"
    )
    assert len(standards["values"]["rows"]) == 10
    assert standards["values"]["rows"][0]["values"]["standard_name"] == "ALBA"

    maps = [
        row
        for row in document["species"]["unknown"]["context"]
        if row["observation_id"].startswith("su_zhang_2023_table_s5_")
    ]
    assert len(maps) == 32
    assert {row["values"]["isotope_as_printed"] for row in maps} == {
        "Na23_cps",
        "K39_cps",
        "Fe57_cps",
        "Cu63_cps",
    }
    assert sum(
        row["values"]["shape"]["rows"] * row["values"]["shape"]["columns"]
        for row in maps
    ) == 63_996
    assert all(row["values"]["typed_refused_schema_gap"] for row in maps)
    assert all(
        row["values"]["source_sha256"]
        == "e257ffbf82975050e76320dfa0127b15699d712bce10095d2c6eedfcd3d0a2b3"
        for row in maps
    )
    assert all(row["values"]["sheet_name"] == "Table S5" for row in maps)
    assert all(row["values"]["units"] == "counts per second" for row in maps)
    assert all(
        re.fullmatch(r"A\d+:[A-Z]+\d+", row["values"]["cell_range"])
        and row["values"]["cell_range"] in row["locator"]["note"]
        for row in maps
    )
    assert all(row["values"]["caption_locator"] == "Table S5!A1" for row in maps)
    assert all(
        row["values"]["caption_as_printed"]
        == "Table S5. Raw LA-ICP-MS mapping data of 23Na, 39K, 57Fe and 63Cu of eight 74220 orange glass beads."
        for row in maps
    )
    assert all("Map2Profile.py" in row["values"]["method_as_printed"] for row in maps)
    assert all(
        row["values"]["method_locator"]["paragraphs"] == "P69-P88"
        and row["values"]["method_locator"]["section"] == "S3"
        for row in maps
    )
    assert all("matrix_as_printed" not in row["values"] for row in maps)
    assert all(
        "tools/validate_literature_extracts.py::OBSERVATION_TYPES" in row["values"]["schema_gap"]
        and "simulator/battery/migrate.py::QUANTITY_ALIASES" in row["values"]["schema_gap"]
        and "simulator/battery/enums.py::PHASE_BASIS_TOKENS" in row["values"]["schema_gap"]
        and "drowart-1960-al2o3-kems.yaml" in row["values"]["schema_gap"]
        for row in maps
    )
    _assert_unknown_context_census(document)

    with (AUDIT / f"{SID}.cells.csv").open(encoding="utf-8", newline="") as stream:
        cell_audit = list(csv.DictReader(stream))
    assert len(cell_audit) == 7_777
    assert Counter(row["sheet"] for row in cell_audit) == {
        "Table S1": 30,
        "Table S2": 5_676,
        "Table S3": 187,
        "Table S4": 1_884,
    }
    _assert_cell_audit_matches(document, cell_audit)
    with (AUDIT / f"{SID}.compare.csv").open(encoding="utf-8", newline="") as stream:
        compare_audit = list(csv.DictReader(stream))
    assert len(compare_audit) == 6
    assert {row["sheet"] for row in compare_audit} == {
        "Table S1", "Table S2", "Table S3", "Table S4", "Table S5", "TOTAL"
    }
    summary_by_sheet = {row["sheet"]: row for row in compare_audit}
    assert summary_by_sheet["Table S5"]["source_numeric_cells"] == "63996"
    assert summary_by_sheet["Table S5"]["per_cell_audit_records"] == "0"
    assert summary_by_sheet["Table S5"]["extract_target_checks"] == "0"
    assert summary_by_sheet["TOTAL"]["source_numeric_cells"] == "71773"
    assert summary_by_sheet["TOTAL"]["per_cell_audit_records"] == "7777"
    assert all(row["mismatches"] == "0" for row in compare_audit)
    assert all("result" not in row and "extract_target" not in row for row in compare_audit)


def test_cell_audit_detects_unpinned_profile_mutation() -> None:
    document = _document()
    with (AUDIT / f"{SID}.cells.csv").open(encoding="utf-8", newline="") as stream:
        cell_audit = list(csv.DictReader(stream))
    _assert_cell_audit_matches(document, cell_audit)

    mutated = deepcopy(document)
    manganese = _observation(
        mutated, "MnO", "su_zhang_2023_table_s2_empa_mno_profile"
    )
    g13 = next(
        point for point in manganese["values"]["series"]
        if "G13 displayed" in point["locator"]["note"]
    )
    g13["MnO_wt_pct"] = "9.999"
    with pytest.raises(AssertionError, match=r"Table S2!G13"):
        _assert_cell_audit_matches(mutated, cell_audit)


def test_isolated_migration_pins_values_conditions_and_schema_gaps() -> None:
    document = _document()
    profile_refusal = "natural glass radial profile; no evaporation experiment"
    source_record_count = 0
    source_profile_point_count = 0
    source_profile_points_by_table: Counter[str] = Counter()
    source_method_class_counts: Counter[str] = Counter()
    expected_refused_ids: set[str] = set()
    expected_profile_ids: set[str] = set()
    for body in document["species"].values():
        for source_row in body.get("observations", []):
            values = source_row.get("values") or {}
            series = values.get("series")
            point_count = len(series) if isinstance(series, list) else 1
            source_record_count += point_count
            raw_id = source_row["observation_id"]
            if values.get("typed_refused_schema_gap"):
                if isinstance(series, list):
                    expected_refused_ids.update(
                        f"{SID}::{raw_id}::point:{index}"
                        for index in range(len(series))
                    )
                else:
                    expected_refused_ids.add(f"{SID}::{raw_id}")
            if values.get("quantity") == profile_refusal:
                assert isinstance(series, list)
                source_profile_point_count += len(series)
                expected_profile_ids.update(
                    f"{SID}::{raw_id}::point:{index}"
                    for index in range(len(series))
                )
                source_profile_points_by_table[source_row["locator"]["table"]] += len(
                    series
                )
                source_method_class_counts[values["method_class"]] += len(series)

    assert source_record_count == 3_758
    assert source_profile_point_count == 3_495
    assert source_profile_points_by_table == {
        "Table S2": 2_630,
        "Table S3": 61,
        "Table S4": 804,
    }
    assert len(expected_refused_ids) == source_record_count

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == source_record_count
    assert not result.validation.hard_issues
    assert result.registry_issues == []
    assert sum(
        row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()
    ) == len(expected_refused_ids)

    unknown_quantity = {
        row.observation_id
        for row in result.observations.values()
        if not row.identity.quantity.is_value
    }
    assert unknown_quantity == expected_refused_ids
    assert not [
        row
        for row in result.observations.values()
        if not row.identity.species.phase.is_value
    ]

    profile_observations = [result.observations[oid] for oid in expected_profile_ids]
    assert len(profile_observations) == source_profile_point_count
    assert not any(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        for row in profile_observations
    )
    assert Counter(row.evidence.class_.value for row in profile_observations) == (
        source_method_class_counts
    )

    s2 = _migrated_point(
        migrator, "su_zhang_2023_table_s2_empa_sio2_profile", "point:0"
    )
    s3 = _migrated_point(
        migrator, "su_zhang_2023_table_s3_sims_na2o_profile", "point:0"
    )
    s4_na = _migrated_point(
        migrator, "su_zhang_2023_table_s4_la_icp_ms_na2o_profile", "point:0"
    )
    s4_cu = _migrated_point(
        migrator, "su_zhang_2023_table_s4_la_icp_ms_cu_profile", "point:0"
    )
    source_points = (
        (
            s2,
            _observation(
                document, "SiO2", "su_zhang_2023_table_s2_empa_sio2_profile"
            ),
            "SiO2_wt_pct",
            "38.8",
            "0.2",
        ),
        (
            s3,
            _observation(
                document, "Na2O", "su_zhang_2023_table_s3_sims_na2o_profile"
            ),
            "Na2O_wt_pct",
            "0.3469",
            "0.0019",
        ),
        (
            s4_na,
            _observation(
                document, "Na2O", "su_zhang_2023_table_s4_la_icp_ms_na2o_profile"
            ),
            "Na2O_wt_pct",
            "0.369",
            "0.005",
        ),
        (
            s4_cu,
            _observation(
                document, "Cu", "su_zhang_2023_table_s4_la_icp_ms_cu_profile"
            ),
            "residue_ppm",
            "5.56",
            "0.05",
        ),
    )
    for migrated, source_row, field, expected_value, expected_sigma in source_points:
        source_point = source_row["values"]["series"][0]
        assert Decimal(source_point[field]) == Decimal(expected_value)
        assert migrated.value.kind is ValueKind.UNAVAILABLE
        assert migrated.value.unavailable_reason == (
            "declared quantity is unknown; refusing to pick a number"
        )
        assert source_point["sigma"] == expected_sigma
        assert migrated.uncertainty.verbatim["sigma"] == expected_sigma
        assert migrated.uncertainty.verbatim["confidence_level"] == "one_sigma"
        assert migrated.locator is not None
        assert to_plain(migrated.locator) == source_point["locator"]

    for row in profile_observations:
        assert row.identity.quantity.is_unknown
        assert row.identity.quantity.reason == (
            f"unsupported quantity {profile_refusal!r}"
        )
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K is None
        assert row.identity.fO2_Pa is None
        assert row.identity.total_pressure_Pa is None
        assert row.identity.composition is None
        assert row.point_conditions is not None
        for axis in ("temperature_K", "fO2_Pa"):
            assert row.point_conditions[axis].state.is_unknown
            assert row.point_conditions[axis].locator is not None
        # The migrator keeps pressure unknown on Identity; it omits a separate
        # pressure point-condition entry when the source prints no pressure.
        assert "total_pressure_Pa" not in row.point_conditions

    assert s4_na.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert s4_na.derived_from == (TABLE_ASSET,)
    assert s4_na.derivation.inputs == (TABLE_ASSET,)
    assert len(s4_na.derivation.references) == 16
    assert any("gb_g1_na23_map" in ref for ref in s4_na.derivation.references)
    assert any("gb_g1_fe57_map" in ref for ref in s4_na.derivation.references)
    assert s4_na.derivation.method.locator.section == "S3"

    # Every source profile point has a typed refusal, so none contributes an
    # incomplete-identity finding for these source-absent axes.
    typed_profile_ids = expected_profile_ids - expected_refused_ids
    assert not typed_profile_ids
    validation_axes = (
        "composition",
        "fO2_Pa",
        "total_pressure_Pa",
        "sample_mass_kg",
        "temperature_K",
    )
    expected_validation_counts = Counter(
        {axis: len(typed_profile_ids) for axis in validation_axes}
    )
    assert len(result.validation.issues) == sum(expected_validation_counts.values())
    assert Counter(
        issue.path.rsplit(".", 1)[-1] for issue in result.validation.issues
    ) == expected_validation_counts
    _assert_model_context_is_unscored(document, result)


def _pdf_footer(page_text: str) -> str:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    assert lines
    return lines[-1]


def test_article_text_points_at_thesis_chapter_3() -> None:
    _assert_thesis_chapter(_document())


def test_article_text_page_mapping_mutation_is_detected() -> None:
    shifted = deepcopy(_document())
    shifted["source"]["article_text"]["printed_page_start"] = 43
    with pytest.raises(AssertionError):
        _assert_thesis_chapter(shifted)

    absent = deepcopy(_document())
    absent["source"]["source_pdf_status"] = (
        "Article PDF not present in the corpus; this extract uses the "
        "supplementary workbook and supplementary DOCX only."
    )
    with pytest.raises(AssertionError):
        _assert_thesis_chapter(absent)


def test_thesis_pdf_sha_and_chapter_page_map() -> None:
    thesis_pdf = _corpus_file(THESIS_ASSET)
    if not thesis_pdf.is_file():
        pytest.skip(f"thesis PDF not available: {thesis_pdf}")

    digest = hashlib.sha256(thesis_pdf.read_bytes()).hexdigest()
    assert digest == THESIS_SHA
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext is not available")

    chapter = pdftotext_page_text(thesis_pdf, 59)
    assert "Chapter 3" in chapter
    assert "117924" in chapter
    assert _pdf_footer(chapter) == "44"
    table = pdftotext_page_text(thesis_pdf, 78)
    assert "Table 3.1" in table
    assert "0.145(3)" in table
    assert _pdf_footer(table) == "63"
    assert _pdf_footer(pdftotext_page_text(thesis_pdf, 76)) == "61"
    assert _pdf_footer(pdftotext_page_text(thesis_pdf, 86)) == "71"


_MODEL_CONTEXT_IDS = (
    ("unknown", "su_zhang_2023_model_cooling_law", "literature_fixed_model_input"),
    ("unknown", "su_zhang_2023_model_bead_tau", "author_fitted_model_parameter"),
    ("Na2O", "su_zhang_2023_model_na_surface_boundary", "mixed_provenance_boundary"),
    ("Na2O", "su_zhang_2023_model_na_k_12065", "sample_specific_derived_parameter"),
    ("Na2O", "su_zhang_2023_model_na_diffusivity", "literature_fixed_model_input"),
    ("Na2O", "su_zhang_2023_model_na_surface_parameters", "author_fitted_model_parameter"),
    ("Cu", "su_zhang_2023_model_cu_diffusivity", "literature_fixed_model_input"),
    ("Cu", "su_zhang_2023_model_cu_surface_parameters", "author_fitted_model_parameter"),
    ("K2O", "su_zhang_2023_model_k_not_fitted", "model_parameter_absent"),
)
_UNKNOWN_MODEL_CONTEXT_IDS = frozenset(
    observation_id
    for species, observation_id, _context_type in _MODEL_CONTEXT_IDS
    if species == "unknown"
)
_UNKNOWN_SUPPLEMENTARY_CONTEXT_IDS = (
    "su_zhang_2023_table_s1_empa_standards",
    "su_zhang_2023_table_s5_gb_a_na23_map",
    "su_zhang_2023_table_s5_gb_a_k39_map",
    "su_zhang_2023_table_s5_gb_a_fe57_map",
    "su_zhang_2023_table_s5_gb_a_cu63_map",
    "su_zhang_2023_table_s5_gb_b_na23_map",
    "su_zhang_2023_table_s5_gb_b_k39_map",
    "su_zhang_2023_table_s5_gb_b_fe57_map",
    "su_zhang_2023_table_s5_gb_b_cu63_map",
    "su_zhang_2023_table_s5_gb_c_na23_map",
    "su_zhang_2023_table_s5_gb_c_k39_map",
    "su_zhang_2023_table_s5_gb_c_fe57_map",
    "su_zhang_2023_table_s5_gb_c_cu63_map",
    "su_zhang_2023_table_s5_gb_g1_na23_map",
    "su_zhang_2023_table_s5_gb_g1_k39_map",
    "su_zhang_2023_table_s5_gb_g1_fe57_map",
    "su_zhang_2023_table_s5_gb_g1_cu63_map",
    "su_zhang_2023_table_s5_gb_g2_na23_map",
    "su_zhang_2023_table_s5_gb_g2_k39_map",
    "su_zhang_2023_table_s5_gb_g2_fe57_map",
    "su_zhang_2023_table_s5_gb_g2_cu63_map",
    "su_zhang_2023_table_s5_gb_g5_na23_map",
    "su_zhang_2023_table_s5_gb_g5_k39_map",
    "su_zhang_2023_table_s5_gb_g5_fe57_map",
    "su_zhang_2023_table_s5_gb_g5_cu63_map",
    "su_zhang_2023_table_s5_gb_g6_na23_map",
    "su_zhang_2023_table_s5_gb_g6_k39_map",
    "su_zhang_2023_table_s5_gb_g6_fe57_map",
    "su_zhang_2023_table_s5_gb_g6_cu63_map",
    "su_zhang_2023_table_s5_gb_g7_na23_map",
    "su_zhang_2023_table_s5_gb_g7_k39_map",
    "su_zhang_2023_table_s5_gb_g7_fe57_map",
    "su_zhang_2023_table_s5_gb_g7_cu63_map",
)
_TABLE_3_1_PUBLIC_TOKENS = (
    "0.145(3)",
    "6001(236)",
    "62(3)",
    "179(7)",
    "diameter_um_as_printed",
    "data_as_printed",
    "total_mswd",
)


def _context_row(document: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][species]["context"]
        if row["observation_id"] == observation_id
    )


def _assert_page_map(locator: dict, *, page: int, published_page: int) -> None:
    assert locator["page"] == page
    assert locator["published_page"] == published_page
    assert locator["pdf_page_index"] == page - 1
    assert published_page == page - 15


def _assert_thesis_locator(locator: dict, *, page: int, published_page: int) -> None:
    assert locator["source_path"] == THESIS_ASSET
    assert locator["source_sha256"] == THESIS_SHA
    _assert_page_map(locator, page=page, published_page=published_page)


def _assert_unknown_context_census(document: dict) -> None:
    """The unknown context is exactly one S1 row, 32 S5 rows, and the model rows.

    A row outside that id set fails, including a row with no table locator.
    """
    rows = document["species"]["unknown"]["context"]
    ids = [row["observation_id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(_UNKNOWN_SUPPLEMENTARY_CONTEXT_IDS) | set(
        _UNKNOWN_MODEL_CONTEXT_IDS
    )
    supplementary = [
        row for row in rows if row["observation_id"] not in _UNKNOWN_MODEL_CONTEXT_IDS
    ]
    for row in supplementary:
        assert "table" in row["locator"], row["observation_id"]
    assert Counter(row["locator"]["table"] for row in supplementary) == {
        "Table S1": 1,
        "Table S5": 32,
    }


def _assert_model_context(document: dict) -> None:
    scope_note = document["extraction"]["scope_note"]
    assert "author_fitted_model_parameter" in scope_note
    assert "literature_fixed_model_input" in scope_note
    assert "sample_specific_derived_parameter" in scope_note
    assert "not a fixed 74220 input" in scope_note
    assert "mixed_provenance_boundary" in scope_note
    assert "authors_expectation" in scope_note
    assert "expected to also apply" in scope_note
    assert "authors_assumption" in scope_note
    assert "cannot be known a priori" in scope_note
    assert "fitted to these profiles" in scope_note
    assert (
        "Appendix B surface-boundary form are literature_fixed_model_input"
    ) not in scope_note
    assert (
        "Fit parameters, figure digitizations, diffusivities, and degassing fluxes "
        "are not extracted."
    ) not in scope_note
    assert "not a profile analysis condition" in scope_note
    _assert_unknown_context_census(document)

    found = [
        (species, row["observation_id"], row["type"])
        for species, body in document["species"].items()
        for row in body.get("context") or []
        if row["observation_id"].startswith("su_zhang_2023_model_")
    ]
    assert set(found) == set(_MODEL_CONTEXT_IDS)
    assert len(found) == len(_MODEL_CONTEXT_IDS)
    for species, observation_id, context_type in _MODEL_CONTEXT_IDS:
        row = _context_row(document, species, observation_id)
        assert row["type"] == context_type
        assert row["type"] not in {
            "composition_series",
            "partial_pressure",
            "volatility_series",
        }
    model_blob = yaml.safe_dump(
        [
            _context_row(document, species, observation_id)
            for species, observation_id, _context_type in _MODEL_CONTEXT_IDS
        ]
    )
    for token in _TABLE_3_1_PUBLIC_TOKENS:
        assert token not in model_blob

    cooling = _context_row(document, "unknown", "su_zhang_2023_model_cooling_law")
    _assert_thesis_locator(cooling["locator"], page=76, published_page=61)
    assert cooling["locator"]["equation"] == "3.4"
    assert cooling["values"]["provenance"] == "literature_fixed"
    assert cooling["values"]["law_as_printed"] == "T = T0 / (1 + t/tau)"
    assert cooling["values"]["T0_K"] == "1640"
    assert cooling["values"]["T0_source"] == "74220 liquidus, Delano 1990"
    assert cooling["values"]["Tend_K"] == "940"
    _assert_page_map(cooling["values"]["Tend_locator"], page=79, published_page=64)
    assert cooling["values"]["not_a_profile_analysis_condition"] is True

    tau = _context_row(document, "unknown", "su_zhang_2023_model_bead_tau")
    _assert_thesis_locator(tau["locator"], page=78, published_page=63)
    assert tau["locator"]["table"] == "Table 3.1"
    assert tau["values"]["role"] == "fitted_coefficients"
    assert tau["values"]["provenance"] == "fitted_to_these_profiles"
    assert tau["values"]["fit_scope"] == "in_sample"
    assert tau["values"]["parameter_names"] == [
        "tau",
        "a0_Na2O",
        "b0_Na2O",
        "a0_Cu",
        "b0_Cu",
    ]
    assert tau["values"]["parameter_count"] == 14
    assert tau["values"]["tau_range_s_as_printed"] == "48 to 179"
    assert tau["values"]["initial_cooling_rate_K_per_s_as_printed"] == "9-34"
    assert tau["values"]["cooling_rate_at_900_K_as_printed"] == "3-10"
    assert tau["values"]["cooling_rate_role"] == "derived_from_fitted_tau"
    assert tau["values"]["table_path"] == TABLE_3_1_ASSET
    assert "beads" not in tau["values"]
    assert "total_mswd" not in tau["values"]
    assert tau["values"]["figure_3_9"]["figure"] == "3.9"
    _assert_page_map(tau["values"]["figure_3_9"], page=80, published_page=65)

    boundary = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_boundary"
    )
    _assert_thesis_locator(boundary["locator"], page=143, published_page=128)
    assert boundary["locator"]["equation"] == "B.1"
    assert boundary["type"] != "literature_fixed_model_input"
    assert "role" not in boundary["values"]
    assert "provenance" not in boundary["values"]
    assert "general_form_applies_to" not in boundary["values"]
    assert "used_by_74220_fit" not in boundary["values"]
    assert boundary["values"]["reaction_role"] == "literature"
    assert boundary["values"]["reaction_source"] == "De Maria et al. 1971"
    assert boundary["values"]["reaction_equation"] == "B.1"
    assert boundary["values"]["general_form_role"] == "authors_expectation"
    assert boundary["values"]["general_form_equation"] == "B.7"
    applicability = boundary["values"]["general_form_applicability_as_printed"]
    assert "expected to also apply" in applicability
    assert "74220" in applicability
    assert "high-Ti basalt" in applicability
    assert boundary["values"]["surface_form_role"] == "authors_assumption"
    assert boundary["values"]["surface_form_as_printed"] == "Ce = a0 * exp(b0/T)"
    assert boundary["values"]["surface_form_equation"] == "B.10"
    condition = boundary["values"]["surface_form_condition_as_printed"]
    assert "exp(a-b/T)" in condition
    assert "Eqs. (B.3) and (B.4)" in condition
    assert boundary["values"]["surface_coefficients_role"] == "fitted_to_these_profiles"
    assert boundary["values"]["surface_coefficients_equation"] == "3.3"
    assert "two fitting parameters" in boundary["values"]["surface_coefficients_as_printed"]
    assert boundary["values"]["surface_coefficients_records"] == [
        "su_zhang_2023_model_na_surface_parameters",
        "su_zhang_2023_model_cu_surface_parameters",
    ]
    assert boundary["values"]["sample_12065_role"] == "sample_specific_derived_for_12065"
    assert boundary["values"]["sample_12065_record"] == "su_zhang_2023_model_na_k_12065"
    assert boundary["values"]["not_the_numerical_K_Na"] is True
    assert "K_Na" not in boundary["values"]
    assert "fNa_equil_atm" not in boundary["values"]
    assert "fO2_equil_atm" not in boundary["values"]
    assert boundary["values"]["ambient_fugacity"] == "cannot_be_known_a_priori"
    assert "cannot be known a priori" in boundary["values"]["ambient_as_printed"]
    _assert_page_map(
        boundary["values"]["surface_form_locator"], page=144, published_page=129
    )
    _assert_page_map(
        boundary["values"]["ambient_locator"], page=144, published_page=129
    )

    derived = _context_row(document, "Na2O", "su_zhang_2023_model_na_k_12065")
    _assert_thesis_locator(derived["locator"], page=144, published_page=129)
    assert derived["locator"]["equation"] == "B.6"
    assert derived["values"]["role"] == "sample_specific_derived_number"
    assert derived["values"]["provenance"] == "derived_for_sample_12065"
    assert derived["values"]["sample"] == "12065"
    assert derived["values"]["not_a_fixed_74220_input"] is True
    assert derived["values"]["K_Na"] == "exp(37.14 - 83853/T)"
    assert derived["values"]["K_Na_equation"] == "B.6"
    assert derived["values"]["C_NaO0_5_wt_pct"] == "0.29"
    assert derived["values"]["fNa_equil_atm"] == "exp(31.34 - 70400/T)"
    assert derived["values"]["fO2_equil_atm"] == "exp(18.24 - 53813/T)"
    _assert_page_map(derived["values"]["fNa_locator"], page=143, published_page=128)
    _assert_page_map(derived["values"]["fO2_locator"], page=143, published_page=128)

    na_d = _context_row(document, "Na2O", "su_zhang_2023_model_na_diffusivity")
    _assert_thesis_locator(na_d["locator"], page=76, published_page=61)
    assert na_d["values"]["source"] == "Lowry et al. 1982"
    assert na_d["values"]["expression_as_printed"] == "exp(-9.251 - 19628/T)"
    assert na_d["values"]["C0_wt_pct"] == "0.35"
    assert na_d["values"]["C0_uncertainty_wt_pct"] == "0.03"

    na_fit = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_parameters"
    )
    _assert_thesis_locator(na_fit["locator"], page=78, published_page=63)
    assert na_fit["locator"]["table"] == "Table 3.1"
    assert na_fit["values"]["role"] == "fitted_coefficients"
    assert na_fit["values"]["provenance"] == "fitted_to_these_profiles"
    assert na_fit["values"]["fit_scope"] == "in_sample"
    assert na_fit["values"]["shared_across_beads"] is True
    assert na_fit["values"]["parameter_names"] == ["a0", "b0"]
    assert na_fit["values"]["boundary_form"] == "Ce = a0 * exp(b0/T)"
    assert na_fit["values"]["not_the_12065_K_Na"] is True
    _assert_fitted_boundary_form(na_fit)
    assert na_fit["values"]["table_path"] == TABLE_3_1_ASSET
    assert "a0_as_printed" not in na_fit["values"]

    cu_d = _context_row(document, "Cu", "su_zhang_2023_model_cu_diffusivity")
    assert cu_d["values"]["source"] == "Ni and Zhang 2016"
    assert cu_d["values"]["expression_as_printed"] == "exp(-13.59 - 12153/T)"
    assert cu_d["values"]["C0_ppm"] == "8"
    assert cu_d["values"]["C0_uncertainty_ppm"] == "0.6"

    cu_fit = _context_row(document, "Cu", "su_zhang_2023_model_cu_surface_parameters")
    _assert_thesis_locator(cu_fit["locator"], page=78, published_page=63)
    assert cu_fit["locator"]["table"] == "Table 3.1"
    assert cu_fit["values"]["role"] == "fitted_coefficients"
    assert cu_fit["values"]["provenance"] == "fitted_to_these_profiles"
    assert cu_fit["values"]["parameter_names"] == ["a0", "b0"]
    assert cu_fit["values"]["boundary_form"] == "Ce = a0 * exp(b0/T)"
    _assert_fitted_boundary_form(cu_fit)
    assert cu_fit["values"]["table_path"] == TABLE_3_1_ASSET
    assert "a0_as_printed" not in cu_fit["values"]
    assert "b0_as_printed" not in cu_fit["values"]

    potassium = _context_row(document, "K2O", "su_zhang_2023_model_k_not_fitted")
    _assert_thesis_locator(potassium["locator"], page=75, published_page=60)
    assert potassium["values"]["provenance"] == "not_fitted"
    assert potassium["values"]["profiles_remain_measured"] is True
    assert "but not for K" in potassium["values"]["reason_as_printed"]


def _assert_model_context_is_unscored(document: dict, result) -> None:
    _assert_model_context(document)
    contexts = [row for rows in result.context_by_work.values() for row in rows]
    by_id = {row["context_id"]: row for row in contexts}
    assert len(by_id) == len(contexts)
    expected_model_ids = {
        f"{SID}::context::{observation_id}"
        for _species, observation_id, _context_type in _MODEL_CONTEXT_IDS
    }
    present_model_ids = {
        context_id
        for context_id in by_id
        if context_id.startswith(f"{SID}::context::su_zhang_2023_model_")
    }
    assert present_model_ids == expected_model_ids
    assert len(by_id) == len(_UNKNOWN_SUPPLEMENTARY_CONTEXT_IDS) + len(_MODEL_CONTEXT_IDS)
    for species, observation_id, context_type in _MODEL_CONTEXT_IDS:
        context_id = f"{SID}::context::{observation_id}"
        assert by_id[context_id]["species"] == species
        assert by_id[context_id]["type"] == context_type
        assert context_id not in result.observations
    assert len(result.observations) == 3_758
    sio2 = next(
        row
        for row in result.observations.values()
        if "::su_zhang_2023_table_s2_empa_sio2_profile::point:0" in row.observation_id
    )
    assert sio2.identity.temperature_K is None
    assert sio2.point_conditions["temperature_K"].state.is_unknown
    assert sio2.point_conditions["temperature_K"].state.reason == (
        "No analysis temperature is printed for this profile row; "
        "the model initial T0 is not an analysis condition."
    )


def test_model_context_pins_printed_inputs_and_table_3_1() -> None:
    _assert_model_context(_document())


def _assert_fitted_boundary_form(row: dict) -> None:
    role = row["values"]["boundary_form_role"]
    assert row["values"]["provenance"] == "fitted_to_these_profiles"
    assert "fitting parameters" in role
    assert "Eq. B.10" in role
    assert "literature-fixed input" in role
    assert "Appendix B boundary form" not in role


def test_model_context_tau_range_mutation_is_detected() -> None:
    document = deepcopy(_document())
    tau = _context_row(document, "unknown", "su_zhang_2023_model_bead_tau")
    tau["values"]["tau_range_s_as_printed"] = "49 to 179"
    with pytest.raises(AssertionError):
        _assert_model_context(document)


def test_boundary_single_literature_class_is_detected() -> None:
    document = deepcopy(_document())
    boundary = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_boundary"
    )
    boundary["values"]["general_form_role"] = "literature_fixed_form"
    with pytest.raises(AssertionError, match="authors_expectation"):
        _assert_model_context(document)

    document = deepcopy(_document())
    boundary = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_boundary"
    )
    boundary["values"]["surface_form_role"] = "literature_fixed_form"
    with pytest.raises(AssertionError, match="authors_assumption"):
        _assert_model_context(document)


def test_boundary_applicability_qualifier_mutation_is_detected() -> None:
    document = deepcopy(_document())
    boundary = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_boundary"
    )
    boundary["values"]["general_form_applicability_as_printed"] = (
        "Equation B.7 applies to 74220 orange glass."
    )
    with pytest.raises(AssertionError, match="expected to also apply"):
        _assert_model_context(document)


def test_boundary_surface_assumption_mutation_is_detected() -> None:
    document = deepcopy(_document())
    boundary = _context_row(
        document, "Na2O", "su_zhang_2023_model_na_surface_boundary"
    )
    boundary["values"]["surface_form_condition_as_printed"] = (
        "Ce = a0 * exp(b0/T) is the 74220 boundary."
    )
    with pytest.raises(AssertionError, match="exp\\(a-b/T\\)"):
        _assert_model_context(document)


def _assert_table_3_1_cells(table_path: Path) -> None:
    with table_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    beads = [row for row in rows if row["kind"] == "bead"]
    shared = [row for row in rows if row["kind"] == "shared_parameter"]
    footer = [row for row in rows if row["kind"] == "table_footer"]
    assert len(beads) == 10
    by_sample = {row["sample"]: row for row in beads}
    assert by_sample["GB-G1"]["tau_s_as_printed"] == "48(2)"
    assert by_sample["GB-G1"]["diameter_um_as_printed"] == "232"
    assert by_sample["GB-G6"]["tau_s_as_printed"] == "179(7)"
    sodium = next(row for row in shared if row["sample"] == "Na2O")
    copper = next(row for row in shared if row["sample"] == "Cu")
    assert sodium["a0_as_printed"] == "0.145(3)"
    assert sodium["b0_as_printed"] == "1183(33)"
    assert copper["a0_as_printed"] == "0.011(2)"
    assert copper["b0_as_printed"] == "6001(236)"
    assert len(footer) == 1
    assert footer[0]["mswd_as_printed"] == "2.91"


def test_table_3_1_corpus_pin_reads_cells_from_corpus() -> None:
    table_path = _corpus_file(TABLE_3_1_ASSET)
    if not table_path.is_file():
        pytest.skip(f"corpus table not available: {table_path}")
    document = _document()
    digest = hashlib.sha256(table_path.read_bytes()).hexdigest()
    for species, observation_id in (
        ("unknown", "su_zhang_2023_model_bead_tau"),
        ("Na2O", "su_zhang_2023_model_na_surface_parameters"),
        ("Cu", "su_zhang_2023_model_cu_surface_parameters"),
    ):
        row = _context_row(document, species, observation_id)
        assert row["values"]["table_path"] == TABLE_3_1_ASSET
        assert row["values"]["table_sha256"] == digest
    _assert_table_3_1_cells(table_path)


def test_table_3_1_corpus_cell_mutation_is_detected(tmp_path: Path) -> None:
    table_path = _corpus_file(TABLE_3_1_ASSET)
    if not table_path.is_file():
        pytest.skip(f"corpus table not available: {table_path}")
    mutated = tmp_path / "t3-1.csv"
    mutated.write_text(
        table_path.read_text(encoding="utf-8").replace("48(2)", "49(2)", 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _assert_table_3_1_cells(mutated)
