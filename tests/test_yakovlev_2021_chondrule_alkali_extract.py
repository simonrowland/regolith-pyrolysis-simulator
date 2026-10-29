from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yakovlev-2021-chondrule-alkali.yaml"


def test_yakovlev_2021_figure_points_migrate_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(document, path=EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    migrated = [
        row for rows in migrator.result.context_by_work.values() for row in rows
    ]
    assert len(migrated) == 17
    by_id = {row["observation_id"]: row for row in migrated}
    for point, temperature, ratio in (
        ("g1", 1026.84, 1.3731),
        ("b5", 1305.93, 0.6550),
        ("b13", 1599.94, 0.5609),
    ):
        row = by_id[f"yakovlev_2021_fig1_{point}_context"]
        assert row["type"] == "figure_read_point"
        assert row["values"]["method_class"] == "figure_only"
        assert row["values"]["evidence_class"] == "figure_only"
        assert row["values"]["series"] in {"granite", "basalt"}
        assert row["values"]["T_C"] == temperature
        assert row["values"]["K2O_Na2O_mass_ratio"] == ratio
        assert row["values"]["uncertainty"]["ratio"] > 0
        assert row["values"]["pixel_center"]["x"] > 0
        assert row["values"]["conditions"]["pressure"]["state"]["value"] == "10^-9 bar"
        assert row["values"]["conditions"]["duration"]["state"]["value"] == "30 min"
