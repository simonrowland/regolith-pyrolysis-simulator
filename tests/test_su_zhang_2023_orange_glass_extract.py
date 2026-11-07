"""Source and migration pins for Su & Zhang (2023) supplementary profiles."""

from __future__ import annotations

import csv
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator
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
    context_counts = Counter(
        row["locator"]["table"]
        for row in document["species"]["unknown"]["context"]
    )
    assert context_counts == {"Table S1": 1, "Table S5": 32}

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
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 3_698
    assert not result.validation.hard_issues
    assert result.registry_issues == []
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in result.observations.values()) == 263

    unknown_quantity = {
        row.observation_id
        for row in result.observations.values()
        if not row.identity.quantity.is_value
    }
    expected_total_ids = {
        f"{SID}::{row['observation_id']}"
        for row in document["species"]["unknown"]["observations"]
        if row["values"].get("typed_refused_schema_gap")
    }
    assert unknown_quantity == expected_total_ids
    assert not [row for row in result.observations.values() if not row.identity.species.phase.is_value]

    typed_profiles = [
        row
        for row in result.observations.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(typed_profiles) == 3_435
    assert sum(
        row.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        for row in typed_profiles
    ) == 2_631
    assert sum(
        row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for row in typed_profiles
    ) == 804

    s2 = _migrated_point(
        migrator, "su_zhang_2023_table_s2_empa_sio2_profile", "point:0"
    )
    s3 = _migrated_point(
        migrator, "su_zhang_2023_table_s3_sims_na2o_profile", "field:Na2O:row:0"
    )
    s4_na = _migrated_point(
        migrator, "su_zhang_2023_table_s4_la_icp_ms_na2o_profile", "field:Na2O:row:0"
    )
    s4_cu = _migrated_point(
        migrator, "su_zhang_2023_table_s4_la_icp_ms_cu_profile", "field:residue_ppm:row:0"
    )
    assert s2.value.point == Decimal("38.8")
    assert s3.value.point == Decimal("0.3469")
    assert s4_na.value.point == Decimal("0.369")
    assert s4_cu.value.point == Decimal("5.56")
    assert s2.uncertainty.verbatim["sigma"] == "0.2"
    assert s3.uncertainty.verbatim["sigma"] == "0.0019"
    assert s4_na.uncertainty.verbatim["sigma"] == "0.005"
    assert s4_cu.uncertainty.verbatim["sigma"] == "0.05"
    assert all(
        row.uncertainty.verbatim["confidence_level"] == "one_sigma"
        for row in (s2, s3, s4_na, s4_cu)
    )

    for row in typed_profiles:
        assert row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        assert row.identity.species.phase.value.value == "glass"
        assert row.identity.temperature_K.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.composition.is_unknown
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

    # The only untyped quantities are the 263 published analytical totals; all
    # 17,175 non-hard validation findings are the five source-absent axes on
    # each of the 3,435 measured profile observations.
    assert len(result.validation.issues) == 17_175
    assert all(issue.reason.value == "identity_incomplete" for issue in result.validation.issues)
    assert Counter(issue.path.rsplit(".", 1)[-1] for issue in result.validation.issues) == {
        "composition": 3_435,
        "fO2_Pa": 3_435,
        "total_pressure_Pa": 3_435,
        "sample_mass_kg": 3_435,
        "temperature_K": 3_435,
    }
