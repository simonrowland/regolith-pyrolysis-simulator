"""Attribution pins for Costa and Jacobson's Piacente comparator series."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-007-costa-2015.yaml"
SOURCE_ID = "kems-007-costa-2015"
ATTRIBUTION = (
    "V. Piacente, J. Matoušek, M. Spoliti, and V. Di Paolo (1975), "
    "Silikaty 19 [4], 289–299, reference 5 in Costa and Jacobson (2015)."
)
OBSERVATION_IDS = {
    "costa_2015_fe_partial_pressure_re_piacente_comparator",
    "costa_2015_mg_partial_pressure_re_piacente_comparator",
}


def test_piacente_comparator_series_name_the_cited_work() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }

    assert set(rows) >= OBSERVATION_IDS
    for observation_id in OBSERVATION_IDS:
        row = rows[observation_id]
        assert row["values"]["method_class"] == "quoted_attributed"
        assert row["values"]["attribution"] == ATTRIBUTION


def test_isolated_migration_has_no_hard_issues() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={SOURCE_ID: dict(document["source"], source_id=SOURCE_ID)},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
