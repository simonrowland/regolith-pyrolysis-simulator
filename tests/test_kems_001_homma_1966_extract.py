"""Evidence-class pins for Homma et al. (1966) theoretical alpha values."""

from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-001-homma-1966.yaml"
SOURCE_ID = "kems-001-homma-1966"
EXPECTED = {
    "homma_1966_mn_olette_theoretical_quoted_deep": 1095,
    "homma_1966_cu_olette_theoretical_quoted_deep": 143,
    "homma_1966_sn_olette_theoretical_quoted_deep": 20,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def test_olette_theoretical_coefficients_are_model_derived() -> None:
    rows = _rows(_document())
    assert set(EXPECTED) <= rows.keys()

    for observation_id, value in EXPECTED.items():
        row = rows[observation_id]
        values = row["values"]
        assert values["method_class"] == "model_derived"
        assert values["olette_alpha_theoretical"] == value
        assert values["derivation"]["raw_inputs"]["state"] == {
            "tag": "value",
            "value": "not_printed",
        }
        assert values["derivation"]["method"]["locator"]["equation"] == "8"
        references = values["derivation"]["references"]
        assert {"[1]", "[6]", "[7]", "[8]"} <= {
            reference[:3] for reference in references
        }


def test_isolated_migration_has_no_hard_issues(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        extract_doc=_document(),
    )

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    migrated_ids = {
        observation.observation_id.rsplit("::", 1)[-1]
        for observation in result.observations.values()
    }
    assert set(EXPECTED) <= migrated_ids
