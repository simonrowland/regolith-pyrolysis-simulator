from collections import Counter
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schumm-1981-alkali-aluminosilicate-thermochemical-tables.yaml"


def test_schumm_key_phase_tables_match_rendered_cells_and_row_counts() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species["observations"]
    }

    rendered_cells = (
        ("NaAlSiO4", "schumm81_nepheline_cp", 19, 298, "cp", 26.21),
        ("NaAlSiO4", "schumm81_nepheline_delta_fH", 19, 1000, "delta_fH", -502.75),
        ("NaAlSi3O8", "schumm81_albite_low_cp", 24, 300, "cp", 49.21),
        ("NaAlSi3O8", "schumm81_albite_high_delta_fG", 25, 1391, "delta_fG", -694.95),
        ("NaAlSi2O6", "schumm81_jadeite_delta_fG", 21, 800, "delta_fG", -609.47),
        ("KAlSiO4", "schumm81_kaliophilite_cp", 28, 810, "cp", 42.68),
        ("KAlSi2O6", "schumm81_leucite_delta_fH", 29, 955, "delta_fH", -720.07),
        ("KAlSi3O8", "schumm81_microcline_delta_fG", 30, 1400, "delta_fG", -693.91),
        ("KAlSi3O8", "schumm81_sanidine_delta_fH", 31, 1423, "delta_fH", -943.46),
    )
    for species, observation_id, page, temperature, field, value in rendered_cells:
        assert species in document["species"]
        row = observations[observation_id]
        assert row["locator"]["page"] == page
        assert any(
            point["T_K"] == temperature and point[field] == value
            for point in row["values"]["series"]
        )

    cp_counts = {
        observation_id: len(observations[observation_id]["values"]["series"])
        for observation_id in (
            "schumm81_nepheline_cp",
            "schumm81_albite_low_cp",
            "schumm81_albite_high_cp",
            "schumm81_jadeite_cp",
            "schumm81_kaliophilite_cp",
            "schumm81_leucite_cp",
            "schumm81_microcline_cp",
            "schumm81_sanidine_cp",
        )
    }
    assert cp_counts == {
        "schumm81_nepheline_cp": 18,
        "schumm81_albite_low_cp": 13,
        "schumm81_albite_high_cp": 14,
        "schumm81_jadeite_cp": 11,
        "schumm81_kaliophilite_cp": 19,
        "schumm81_leucite_cp": 21,
        "schumm81_microcline_cp": 13,
        "schumm81_sanidine_cp": 14,
    }

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    migrated = list(migrator.result.observations.values())
    assert not migrator.result.validation.hard_issues
    assert all(row.value.kind.value != "unavailable" for row in migrated)
    assert all(not row.identity.quantity.is_unknown for row in migrated)
    assert all(not row.identity.species.phase.is_unknown for row in migrated)
    assert all(row.identity.species.phase.value == "cr" for row in migrated)
    assert Counter(row.identity.quantity.value for row in migrated) == {
        "cp": 123,
        "H_minus_H298": 123,
        "delta_fH": 123,
        "delta_fG": 123,
        "S": 8,
    }
    assert len(migrated) == 500
