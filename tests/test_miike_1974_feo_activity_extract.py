from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data"
    / "literature"
    / "extracts"
    / "miike-1974-feo-activity-silicate-melts.yaml"
)


def test_miike_1974_activities_match_page_413_render_and_printed_row_counts() -> None:
    """Pins rendered PDF page 3 (published p. 413), including each table panel."""
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = extract["species"]["FeO"]["observations"]
    by_sample = {row["values"]["composition_id"]: row for row in observations}

    assert len(observations) == 24
    assert Counter(row["locator"]["table"] for row in observations) == {
        "1": 6,
        "2": 10,
        "3": 8,
    }
    expected = {
        "1": ("0.93", "4.95e-11", "80.33"),
        "Ca-4": ("0.65", "8.04e-12", "55.13"),
        "Al-8": ("0.52", "5.15e-12", "56.78"),
    }
    for sample, (activity, p_o2, feo_wt_pct) in expected.items():
        row = by_sample[sample]
        point = row["values"]["points"][0]
        assert Decimal(point["activity"]) == Decimal(activity)
        assert Decimal(point["oxygen_partial_pressure"]["value"]) == Decimal(p_o2)
        composition = row["point_conditions"]["composition"]
        assert Decimal(composition["state"]["value"]["components"]["FeO"]) == Decimal(
            feo_wt_pct
        )
        assert row["locator"]["page"] == 3
        assert row["locator"]["published_page"] == 413


def test_miike_1974_equation_1_locators_match_rendered_page_411() -> None:
    """The rendered first PDF page prints Eq. (1) and the standard Gibbs energy on p. 411."""
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = extract["species"]["FeO"]["observations"]

    assert len(observations) == 24
    for row in observations:
        locator = row["values"]["reference_state_locator"]
        assert locator == {
            "page": 1,
            "pdf_page_index": 0,
            "published_page": 411,
            "equation": "1",
            "section": "2.1",
        }
        assert row["derivation"]["method"]["locator"] == locator


def test_miike_1974_isolated_migration_binds_printed_axes_and_no_total_pressure() -> None:
    """The isolated production migrator maps printed conditions without pressure assumptions."""
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == "miike-1974-feo-activity-silicate-melts"
    ]

    assert len(observations) == 24
    assert not result.validation.hard_issues
    assert all(observation.value.kind.value == "point" for observation in observations)
    assert sum(
        observation.value.kind.value == "unavailable"
        for observation in observations
    ) == 0
    assert len(result.queue) == 24
    assert all(entry.axes == ["reference_state"] for entry in result.queue)
    assert all("source reference state not printed" in entry.why for entry in result.queue)
    for observation in observations:
        identity = observation.identity
        assert identity.temperature_K.is_value
        assert identity.composition.is_value
        assert identity.fO2_Pa.is_value
        assert identity.total_pressure_Pa.is_unknown
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["composition"].locator is not None
        assert observation.point_conditions["gas_mix"].state.is_value
        assert identity.reference_state.is_unknown

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    for row in extract["species"]["FeO"]["observations"]:
        assert "total_pressure_Pa" not in row.get("values", {})
        assert "total_pressure_Pa" not in row.get("point_conditions", {})
