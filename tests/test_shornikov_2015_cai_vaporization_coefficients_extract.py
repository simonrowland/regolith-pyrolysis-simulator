"""Regression pins for Shornikov (2015) CAI vaporization coefficients."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2015-cai-vaporization-coefficients.yaml"
)


def test_shornikov_2015_cai_coefficients_migrate_with_located_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 53
    table_observations = tuple(
        row
        for row in observations
        if row.locator.table in {"1", "2", "3", "4", "5"}
    )
    assert len(table_observations) == 46

    for observation in observations:
        conditions = observation.point_conditions or {}
        assert {"temperature_K", "total_pressure_Pa", "fO2_Pa"} <= set(conditions)
        assert all(conditions[key].locator is not None for key in conditions)
        temperature = conditions["temperature_K"].state
        assert temperature.is_value or temperature.is_unknown
        for key in ("total_pressure_Pa", "fO2_Pa"):
            assert conditions[key].state.is_value or conditions[key].state.is_unknown

    expected_rendered_values = {
        "shornikov_2015_cai_vaporization_coefficients_p003_t01_r02_c04": (
            Decimal("0.79")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r02_c04": (
            Decimal("0.35")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p005_t04_r03_c04": (
            Decimal("0.0223")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p007_t05_r07_c08": (
            Decimal("0.66")
        ),
    }
    for observation_id, expected in expected_rendered_values.items():
        observation = next(
            row for row in observations if row.observation_id.endswith(observation_id)
        )
        assert observation.value.point == expected
        assert observation.locator.table is not None

    ranged = next(
        row
        for row in observations
        if row.observation_id.endswith("p003_t01_r02_c04")
    )
    assert ranged.identity.temperature_K.is_unknown
    temperature_interval = ranged.point_conditions["temperature_K"].state.value
    assert (temperature_interval.interval_low, temperature_interval.interval_high) == (
        Decimal("1283"), Decimal("1473")
    )
    scalar = next(
        row
        for row in observations
        if row.observation_id.endswith("p004_t03_r18_c04")
    )
    required_axes = profile_for(scalar.identity).required
    assert {"temperature_K", "composition", "total_pressure_Pa", "fO2_Pa"} <= (
        required_axes
    )
    assert scalar.identity.temperature_K.value == Decimal("2318.0")
    for key in ("composition", "total_pressure_Pa", "fO2_Pa"):
        assert getattr(scalar.identity, key).is_unknown
    assert scalar.point_conditions["temperature_K"].state.value == Decimal("2318.0")

    contexts = tuple(
        context
        for rows in migrator.result.context_by_work.values()
        for context in rows
    )
    figures = tuple(
        context for context in contexts if context.get("type") == "figure_only"
    )
    assert len(figures) == 2
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["quantity"]
        assert values["species_as_printed"]
        assert values["phase_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert "points" not in values
        if "panels" in values:
            assert {panel["panel"] for panel in values["panels"]} == {"a", "b"}
            series = [item for panel in values["panels"] for item in panel["series"]]
            assert len(series) == 12
        else:
            series = values["series"]
        assert all("marker_count" in item and "points" not in item for item in series)
