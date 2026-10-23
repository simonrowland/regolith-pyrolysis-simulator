from dataclasses import replace
from pathlib import Path

import yaml

from simulator.battery.enums import RefusalReason
from simulator.battery.validate import validate_corpus
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/green-1971-apollo12-basalt-petrology.yaml"


def test_green_1971_phase_temperatures_migrate_and_pin_printed_values():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(
        ROOT,
        EXTRACT,
        index={document["source_id"]: dict(document["source"])},
        aliases={},
    )
    migrator.finalize()

    assert migrator.result.validation is not None
    (collision,) = tuple(
        issue
        for issue in migrator.result.validation.hard_issues
        if "green1971_12022_pigeonite_in" in issue.detail
        and "green1971_12022_ilmenite_plagioclase_in" in issue.detail
    )
    assert collision.reason is RefusalReason.IDENTITY_MISMATCH
    assert "conflicting point values" in collision.detail
    assert migrator.result.validation.hard_issues == (collision,)
    assert len(migrator.result.observations) == 6

    observations = dict(migrator.result.observations)
    first = next(
        row
        for row in observations.values()
        if row.observation_id.endswith("::green1971_12022_pigeonite_in")
    )
    second = next(
        row
        for row in observations.values()
        if row.observation_id.endswith("::green1971_12022_ilmenite_plagioclase_in")
    )
    observations[second.observation_id] = replace(second, value=first.value)
    same_value_report = validate_corpus(
        migrator.result.works,
        migrator.result.experiments,
        observations,
        benches=migrator.result.benches,
        context_rows={
            str(row["context_id"]): row
            for rows in migrator.result.context_by_work.values()
            for row in rows
        },
    )
    assert not any(
        "green1971_12022_pigeonite_in" in issue.detail
        and "green1971_12022_ilmenite_plagioclase_in" in issue.detail
        for issue in same_value_report.hard_issues
    )
    observations = document["species"]["Apollo12_12065"]["observations"]
    liquidus = next(row for row in observations if row["observation_id"] == "green1971_12065_liquidus")
    olivine = next(row for row in observations if row["observation_id"] == "green1971_12065_olivine_reacts_out")
    assert liquidus["values"]["temperature_C_as_printed"] == 1180
    assert olivine["values"]["temperature_C_as_printed"] == 1150
