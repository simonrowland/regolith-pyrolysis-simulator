from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator


EXTRACT = Path(
    "data/literature/extracts/schreiber-haskin-1976-plsc7-chromium-in-basalts.yaml"
)
# Independent literal transcription from the Figure 4 caption on rendered PDF p. 9.
FIG4 = {
    "schreiber_1976_fig4_YG_CrVI_fraction": ("10", "0"),
    "schreiber_1976_fig4_YG_CrIII_fraction": ("90", "0"),
    "schreiber_1976_fig4_G_CrIII_fraction": ("90", "2.6"),
    "schreiber_1976_fig4_G_CrII_fraction": ("10", "2.6"),
    "schreiber_1976_fig4_BG_CrIII_fraction": ("55", "5.2"),
    "schreiber_1976_fig4_BG_CrII_fraction": ("45", "5.2"),
    "schreiber_1976_fig4_B_CrIII_fraction": ("20", "7.9"),
    "schreiber_1976_fig4_B_CrII_fraction": ("80", "7.9"),
}


def test_caption_values_survive_migration_and_mutations_are_detected():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = document["species"]["Cr"]["observations"]
    from_extract = {
        row["observation_id"]: (
            row["values"]["points"][0]["value"],
            row["values"]["points"][0]["log_pO2_as_printed"],
        )
        for row in observations
    }
    assert from_extract == FIG4

    changed = deepcopy(from_extract)
    changed["schreiber_1976_fig4_BG_CrII_fraction"] = ("46", "5.2")
    assert changed != FIG4

    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == EXTRACT.stem
    ]
    assert not migrator.result.validation.hard_issues
    assert len(migrated) == len(FIG4)
    assert {row.identity.species.formula for row in migrated} == {"Cr"}
    assert {row.identity.quantity.value for row in migrated} == {Quantity.VALENCE_RATIO}
    assert {row.value.kind for row in migrated} == {ValueKind.POINT}
    actual = {
        row.observation_id.split("::", 1)[1].split("::", 1)[0]: str(row.value.point)
        for row in migrated
    }
    assert actual == {key: value for key, (value, _log_pO2) in FIG4.items()}
    assert all(row.identity.temperature_K.value == Decimal("1773.15") for row in migrated)
    assert all(row.point_conditions["fO2_log"].state.is_unknown for row in migrated)
