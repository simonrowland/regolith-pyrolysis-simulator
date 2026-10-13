from pathlib import Path

import pytest

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator


EXTRACT = Path("data/literature/extracts/wadhwa-2008-redox-small-bodies.yaml")


def test_wadhwa_redox_source_facts_survive_production_migration():
    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    contexts = {
        row["observation_id"]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    la_point = next(
        point
        for point in contexts["wadh08_fig3_shergottite_comparisons"]["values"][
            "plotted_points_read_to_about_0_1_log_unit"
        ]
        if point["sample"] == "Los Angeles"
    )
    assert la_point == {"sample": "Los Angeles", "x": "-1.4", "y": "-3.3"}
    assert contexts["wadh08_fig4_angrite_augite_comparison"]["values"]["axes"] == (
        "x = Eu/Sm pigeonite oxybarometer; y = angrite augite oxybarometer"
    )

    ureilite = sorted(
        (
            obs
            for obs in result.observations.values()
            if obs.source_id == EXTRACT.stem
        ),
        key=lambda obs: obs.identity.temperature_K.value,
    )
    assert [obs.identity.temperature_K.value for obs in ureilite] == [
        1200,
        1600,
    ]
    assert all(obs.identity.quantity.value is Quantity.FUGACITY for obs in ureilite)
    assert all(
        obs.evidence.class_.value is EvidenceClass.COMPILATION_ASSESSED
        for obs in ureilite
    )
    assert all(
        obs.value.unavailable_reason
        == "unmapped pressure unit 'fO2 as published; pressure unit not stated'"
        for obs in ureilite
    )
    assert [obs.point_conditions["temperature_K"].state.value for obs in ureilite] == [
        1200,
        1600,
    ]
    assert all(
        obs.point_conditions["temperature_K"].locator.published_page == 496
        for obs in ureilite
    )
    assert [obs.evidence.attribution for obs in ureilite] == [
        "Berkley and Jones (1982) report approximately ~10−18 at 1200 K and ~10−12 at 1600 K."
    ] * 2
    assert not result.validation.hard_issues


def test_wadhwa_ureilite_attribution_mutation_turns_migration_check_red(tmp_path):
    original = EXTRACT.read_text()
    mutated = original.replace(
        "          attribution: 'Berkley and Jones (1982) report approximately ~10−18 at 1200 K and ~10−12 at 1600 K.'\n",
        "",
    )
    assert mutated != original
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(mutated)

    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(mutated_extract)
    migrator.finalize()
    migrated = sorted(
        (
            obs
            for obs in migrator.result.observations.values()
            if obs.source_id == EXTRACT.stem
        ),
        key=lambda obs: obs.identity.temperature_K.value,
    )
    assert len(migrated) == 2
    with pytest.raises(AssertionError):
        assert [obs.evidence.attribution for obs in migrated] == [
            "Berkley and Jones (1982) report approximately ~10−18 at 1200 K and ~10−12 at 1600 K."
        ] * 2
