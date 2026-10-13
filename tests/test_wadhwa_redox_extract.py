from pathlib import Path

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator, load_yaml


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

    source = load_yaml(EXTRACT)
    printed_points = (
        source["species"]["FeO"]["observations"][0]["values"]["points"]
    )
    assert [point["fO2_as_published"] for point in printed_points] == [
        "~10−18",
        "~10−12",
    ]
    assert all("log10_fO2" not in point for point in printed_points)

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
    assert not result.validation.hard_issues
