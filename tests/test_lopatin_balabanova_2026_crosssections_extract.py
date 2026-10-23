from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lopatin-balabanova-2026-ionization-crosssections.yaml"


def test_lopatin_balabanova_2026_cross_sections_remain_context_only() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(
        root=ROOT,
        index={EXTRACT.stem: dict(extract["source"], source_id=EXTRACT.stem)},
        aliases={},
    )
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.observations == {}
    assert set(extract["species"]) == {"B2O3", "NaBO2", "NaPO3", "KPO3"}
    expected = {
        "B2O3": ("1.72 ± 0.22", "6.31", "— (not reported)"),
        "NaBO2": ("2.73 ± 0.25", "6.41", "3.01"),
        "NaPO3": ("3.51 ± 0.18", "7.60", "2.83"),
        "KPO3": ("4.43 ± 0.31", "8.23", "4.29"),
    }
    for formula, values in expected.items():
        species = extract["species"][formula]
        assert species["observations"] == []
        record = next(
            record
            for record in species["context"]
            if record["observation_id"].endswith("_cross_section_method_data")
        )
        assert record["locator"]["page"] == 3
        assert record["locator"]["pdf_page_index"] == 2
        assert tuple(record["values"]["table_1"].values()) == values
        assert "no quantity token" in record["values"]["schema_gap"]
        assert record["type"] == "method_data_context"

    [method_record] = [
        record
        for record in extract["species"]["B2O3"]["context"]
        if record["observation_id"].endswith("_methods_and_calibration_measurements")
    ]
    method_values = method_record["values"]
    assert method_record["locator"]["page"] == 2
    assert "30 eV" in method_values["apparatus_and_method"]
    assert "5.05 × 10−16 cm2" in method_values["apparatus_and_method"]
    assert "430 arb. units" in method_values["B2O3"]
    assert "1.72 × 10−16 cm2" in method_values["B2O3"]
    assert "72, 72, 45, and 9 arb. units" in method_values["NaBO2"]
    assert "dimerization" in method_values["NaBO2"]
    assert "not measured because of low ion-current intensity" in method_values["NaPO3"]
    assert "66 and 3.0 arb. units" in method_values["KPO3"]
