"""Regression pins for the quoted transitions and contextual table rows."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2015-complex-oxide-vapor.yaml"


def test_shornikov_2015_extract_preserves_quoted_temperatures_and_context() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 3
    expected = {
        "shornikov_2015_mullite_melting_point_chase_1998": Decimal("2180"),
        "shornikov_2015_spinel_melting_point_chase_1998": Decimal("2388"),
        "shornikov_2015_perovskite_melting_point_chase_1998": Decimal("2243"),
    }
    for suffix, temperature in expected.items():
        observation = next(item for item in observations if item.observation_id.endswith(suffix))
        assert observation.value.point == temperature
        assert observation.locator.published_page == 694
        conditions = observation.point_conditions or {}
        located_temperature = conditions["temperature_K"]
        assert located_temperature.state.value == temperature
        assert located_temperature.locator.published_page == 694
        for key in ("total_pressure_Pa", "fO2_Pa"):
            condition = conditions[key]
            assert condition.state.tag.value == "unknown"
            assert condition.state.reason == "not_published"
            assert condition.locator.page == 5
            assert condition.locator.published_page == 694

    extract = migrator.result.context_by_work
    contexts = tuple(row for rows in extract.values() for row in rows)
    refusals = [
        row for row in contexts
        if (row.get("values") or {}).get("admission_status") == "typed_refusal"
    ]
    figures = [row for row in contexts if row.get("type") == "figure_only"]
    table1 = next(row for row in contexts if row["observation_id"] == "shornikov_2015_table1_cited_study_summary")
    assert len(refusals) == 20
    assert len(figures) == 3
    assert len(table1["values"]["rows"]) == 12
    assert all("digitisation" in row["values"] for row in figures)
