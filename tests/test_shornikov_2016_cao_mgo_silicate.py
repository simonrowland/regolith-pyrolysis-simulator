"""Source pins and isolated migration for Shornikov (2016)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2016-cao-mgo-silicate.yaml"

# Table cells checked against the PDF page 2 render and against pdftotext.
# MinerU stored an ASCII hyphen; the page glyph is an en-dash. The digits match.
RENDERED_CELLS = (
    ("CaO", "shornikov_2016_cao_mgo_silicate_p002_t02_r03_c02", "-421.511", Phase.CR),
    ("CaO", "shornikov_2016_cao_mgo_silicate_p002_t02_r03_c04", "-409.450", Phase.L),
    ("Ca", "shornikov_2016_cao_mgo_silicate_p002_t02_r03_c06", "-21.080", Phase.G),
    ("Ca2", "shornikov_2016_cao_mgo_silicate_p002_t02_r04_c06", "77.075", Phase.G),
    ("O2", "shornikov_2016_cao_mgo_silicate_p002_t02_r17_c06", "0.000", Phase.G),
)

# Figure 1 marker centres. Read 1 is the hole centroid (open squares) or the
# disk peak (filled circles) on the embedded JPEG. Read 2 is a separate centre
# of that symbol's own pixels. T, activity, both pixel reads, and the
# half-difference uncertainty are literals, not a comparison with an audit file.
FIGURE_READS = (
    (
        "CaO",
        "shornikov_2016_fig1a_CaO_activity",
        "1873",
        "0.089",
        [483.0, 138.0],
        [2040.6, 4998.2],
        [483.24, 137.78],
        "0.001",
    ),
    (
        "MgO",
        "shornikov_2016_fig1a_MgO_activity",
        "1873",
        "0.16",
        [258.95, 106.95],
        [1368.1, 4905.0],
        [258.88, 106.99],
        "0.000",
    ),
    (
        "SiO2",
        "shornikov_2016_fig1b_SiO2_rein1965_activity",
        "1873",
        "0.1",
        [930.0, 132.0],
        [3382.4, 4980.2],
        [929.45, 131.74],
        "0.001",
    ),
)


def _load():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document):
    return [
        (formula, row)
        for formula, block in document["species"].items()
        for row in block.get("observations") or []
    ]


def _assert_rendered_cells(document) -> None:
    observations = _observations(document)
    for formula, observation_id, value, phase in RENDERED_CELLS:
        row = next(
            row
            for candidate, row in observations
            if candidate == formula and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["phase"] == phase.value
        assert row["locator"]["page"] == 2


def test_table_and_figure_reads_are_pinned():
    document = _load()
    gibbs = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    assert len(gibbs) == 38
    assert {row["phase"] for row in gibbs} == {"cr", "l", "g"}
    assert sum(row["phase"] == "cr" for row in gibbs) == 13
    assert sum(row["phase"] == "l" for row in gibbs) == 8
    assert sum(row["phase"] == "g" for row in gibbs) == 17
    _assert_rendered_cells(document)

    activity_points = []
    for _formula, row in _observations(document):
        if row["values"].get("quantity") != "activity":
            continue
        activity_points.extend(row["values"].get("points") or [])
    assert len(activity_points) == 102
    for (
        formula,
        observation_id,
        temperature,
        activity,
        embedded,
        page,
        embedded_2,
        uncertainty,
    ) in FIGURE_READS:
        match = next(
            item
            for item in document["species"][formula]["observations"]
            if item["observation_id"] == observation_id
        )
        point = next(
            item
            for item in match["values"]["points"]
            if item["embedded_px_read_1"] == embedded
        )
        assert point["T_K"] == temperature
        assert point["activity"] == activity
        assert point["page_600dpi_px_read_1"] == page
        assert point["embedded_px_read_2"] == embedded_2
        assert point["read_uncertainty_dex"] == uncertainty
        assert point["log10_activity_ordinate"].startswith("-")

    mixing = next(
        row
        for row in document["species"]["CaO-MgO-SiO2"]["context"]
        if row["observation_id"] == "shornikov_2016_fig2a_series1_mixing_energy"
    )
    mixing_point = next(
        item
        for item in mixing["values"]["points"]
        if item["embedded_px_read_1"] == [106.44, 57.07]
    )
    assert mixing_point["T_K"] == "1873"
    assert mixing_point["delta_Gm_kJ_per_mole_read"] == "-12.65"
    assert mixing_point["page_600dpi_px_read_1"] == [626.6, 638.5]
    assert mixing_point["embedded_px_read_2"] == [106.69, 57.21]
    assert mixing_point["read_uncertainty_kJ_per_mole"] == "0.006"
    prose = next(
        row
        for row in document["species"]["CaO-MgO-SiO2"]["context"]
        if row["observation_id"] == "shornikov_2016_prose_4kJ_per_mole"
    )
    assert prose["values"]["value_as_printed"] == "4 kJ/mole approximately"


def test_mutating_the_consumed_gibbs_value_or_deleting_a_row_fails_the_pin():
    document = _load()
    _assert_rendered_cells(document)
    solid = next(
        row
        for row in document["species"]["CaO"]["observations"]
        if row["observation_id"].endswith("r03_c02")
    )
    solid["values"]["value"] = "-999"
    with pytest.raises(AssertionError):
        _assert_rendered_cells(document)

    document = _load()
    assert any(
        row["values"].get("value") == "-421.511"
        for row in document["species"]["CaO"]["observations"]
    )
    document["species"]["CaO"]["observations"] = [
        row
        for row in document["species"]["CaO"]["observations"]
        if row["values"].get("value") != "-421.511"
    ]
    remaining = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    assert len(remaining) == 37
    assert len(remaining) != 38


def test_isolated_migration_reaches_temperature_on_every_row():
    document = _load()
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = [
        row
        for row in result.observations.values()
        if row.source_id == document["source_id"]
    ]
    assert len(migrated) == 140
    assert not result.registry_issues
    assert not result.validation.hard_issues

    gibbs = [row for row in migrated if row.identity.quantity.value.value == "delta_fG"]
    activities = [
        row
        for row in migrated
        if row.identity.quantity.value.value == "activity"
        and row.value.kind is ValueKind.POINT
    ]
    assert len(gibbs) == 38
    assert len(activities) == 102

    for _formula, observation_id, value, _phase in RENDERED_CELLS:
        row = next(item for item in gibbs if item.observation_id.endswith(observation_id))
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("2000")
        temperature = row.point_conditions["temperature_K"].state.value
        point = temperature.point if hasattr(temperature, "point") else temperature
        assert point == Decimal("2000")

    for row in migrated:
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value

    sample = next(row for row in activities if "fig1a_CaO_activity" in row.observation_id)
    assert sample.identity.temperature_K.value == Decimal("1873")
    assert sample.identity.composition.is_unknown
    assert sample.point_conditions["composition"].state.is_unknown
    assert "mole percent" in sample.point_conditions["composition"].state.reason
    assert sample.identity.reference_state.is_unknown
    assert "standard state" in sample.identity.reference_state.reason
    assert "temperature_K" in profile_for(sample.identity).required
    assert sample.identity.per.is_value
    assert sample.identity.per.value.value == "dimensionless"

    gibbs_row = gibbs[0]
    assert gibbs_row.identity.standard_pressure_Pa.is_unknown
    assert gibbs_row.identity.reaction.is_unknown
    assert gibbs_row.identity.formation_elements.is_unknown
    assert gibbs_row.identity.composition.is_not_applicable

    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row.get("source_id") == document["source_id"]
    ]
    assert len(contexts) == 9
    assert not any(row.identity.quantity.value.value == "delta_fG" and "fig2" in row.observation_id for row in migrated)
