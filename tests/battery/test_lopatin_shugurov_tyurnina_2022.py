"""Source-to-migration pins for the SrO–Al2O3 KEMS supplementary tables."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "lopatin-shugurov-tyurnina-2022-sro-al2o3-kems"
REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = regolith_corpus_root()
DOCX = CORPUS_ROOT / "raw" / SID / f"{SID}.docx"
EXTRACT = REPO_ROOT / "data" / "literature" / "extracts" / f"{SID}.yaml"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SOURCE_COLUMNS = {
    2: (("Sr", 1, 0), ("SrO", 2, -2), ("O", 3, 0)),
    3: (("Sr", 1, 0), ("O", 2, 0)),
    4: (("Sr", 1, 0), ("O", 2, 0)),
    5: (("Sr", 1, 0), ("O", 2, 0)),
    6: (("Sr", 1, -2), ("O", 2, -2)),
    7: (("Sr", 1, -2), ("O", 2, -2)),
}


def test_all_printed_pressure_cells_migrate_from_the_docx_source() -> None:
    if not DOCX.is_file():
        pytest.skip(f"supporting-information DOCX is absent: {DOCX}")

    with zipfile.ZipFile(DOCX) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    tables = document.findall(".//w:tbl", NS)

    source_points: Counter[tuple[str, Decimal, Decimal]] = Counter()
    table_rows = {}
    for table_number, columns in SOURCE_COLUMNS.items():
        rows = tables[table_number - 1].findall("./w:tr", NS)
        table_rows[table_number] = len(rows)
        for row in rows[2:]:
            cells = [
                "".join((text.text or "") for text in cell.findall(".//w:t", NS))
                for cell in row.findall("./w:tc", NS)
            ]
            temperature = Decimal(cells[0].strip())
            for formula, column, scale_exponent in columns:
                raw = cells[column].strip()
                if not raw or raw == "-":
                    continue
                scientific = re.fullmatch(r"(\d+(?:\.\d+)?)10(-\d+)", raw)
                if scientific:
                    value = Decimal(scientific.group(1)) * (
                        Decimal(10) ** int(scientific.group(2))
                    )
                else:
                    value = Decimal(raw) * (Decimal(10) ** scale_exponent)
                source_points[(formula, temperature, value)] += 1

    assert table_rows == {2: 56, 3: 13, 4: 14, 5: 12, 6: 18, 7: 10}
    assert sum(source_points.values()) == 213
    assert source_points[("Sr", Decimal(1816), Decimal("0.19"))] == 1
    assert source_points[("SrO", Decimal(1951), Decimal("0.0026"))] == 1
    assert source_points[("O", Decimal(1766), Decimal("0.005"))] == 1
    assert source_points[("Sr", Decimal(1956), Decimal("0.0173"))] == 1

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    extract_points: Counter[tuple[str, Decimal, Decimal]] = Counter()
    for formula, container in extract["species"].items():
        for observation in container.get("observations", []):
            if observation["values"].get("quantity") != "partial_pressure":
                continue
            assert observation["values"]["gas_species"] == formula
            for point in observation["values"]["points"]:
                extract_points[
                    (
                        formula,
                        Decimal(str(point["T_K"])),
                        Decimal(str(point["P_Pa"])),
                    )
                ] += 1
    assert extract_points == source_points

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    migrated_points = Counter()
    for observation in result.observations.values():
        if observation.source_id != SID:
            continue
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.species.phase.value is Phase.G
        assert observation.identity.temperature_K.is_value
        migrated_points[
            (
                observation.identity.species.formula,
                observation.identity.temperature_K.value,
                observation.value.point,
            )
        ] += 1
    assert migrated_points == source_points
