from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/berry-2003-in-situ-cr-xanes-furnace.yaml"
SID = "berry-2003-in-situ-cr-xanes-furnace"
CORPUS = Path(os.environ.get("REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"))


@pytest.mark.skipif(not (CORPUS / "raw" / SID / f"{SID}.pdf").is_file(), reason="corpus source is absent")
def test_quoted_chromium_fraction_and_deferred_spectra(tmp_path: Path) -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    row = source["species"]["Cr"]["observations"][0]
    assert row["values"]["method_class"] == "quoted_attributed"
    assert row["values"]["attribution"] == "Berry & O'Neill (2003), as cited in the source."
    point = row["values"]["points"][0]
    assert point["value"] == "0.5"
    assert point["approximate"] is True
    assert point["as_printed"] == "around half of total Cr is present as Cr2+"
    assert point["T_K"] == "1673"
    assert point["point_conditions"]["fO2_log"]["state"]["value"] == "-8"

    figures = [record for record in source["species"]["Cr"]["context"] if record.get("type") == "figure_record"]
    assert {record["locator"]["figure"] for record in figures} == {"1", "2", "3"}
    spectral = next(record for record in figures if record["locator"]["figure"] == "3")
    assert spectral["values"]["axes_as_printed"] == {"x": "Energy (eV)", "y": "Normalised intensity"}
    assert spectral["values"]["digitisation"] == "deferred to a figure pass"
    assert all(record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1 for record in figures)

    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    observations = [o for o in migrated.observations.values() if o.source_id == SID]
    assert len(observations) == 1
    observation = observations[0]
    assert observation.value.kind.value == "point"
    assert observation.value.point == Decimal("0.5")
    assert observation.value.approximate is True
    assert observation.identity.species.formula == "Cr"
    assert observation.identity.temperature_K.value == Decimal("1673")
    assert observation.identity.valence_basis.value.value_form.value == "fraction"
    assert observation.evidence.class_.value.value == "quoted_attributed"
    assert observation.point_conditions["fO2_log"].state.value == Decimal("-8")
