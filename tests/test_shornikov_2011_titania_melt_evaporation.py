"""Source pins and isolated migration for Shornikov (2011)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2011-titania-melt-evaporation.yaml"
)

# Table cells checked against the PDF page 1 render and against pdftotext.
# MinerU stored an ASCII hyphen; the page glyph is an en-dash. The digits match.
RENDERED_CELLS = (
    ("CaO", "shornikov_2011_titania_melt_evaporation_p001_t01_r03_c02", "-416.099", Phase.CR),
    ("CaO", "shornikov_2011_titania_melt_evaporation_p001_t01_r03_c04", "-404.461", Phase.L),
    ("Ca", "shornikov_2011_titania_melt_evaporation_p001_t01_r03_c06", "-25.442", Phase.G),
)

# Figure 1 marker centres. Read 1 is the distance-transform peak on the
# embedded JPEG. Read 2 is a separate centre of that symbol's pixels.
# T, activity, both pixel reads, and the half-difference uncertainty are
# literals from the page, not from the audit table.
FIGURE_READS = (
    (
        "CaO",
        "shornikov_2011_fig1a_CaO_activity",
        "2050",
        "0.017",
        [148.0, 256.0],
        [1572.4, 2445.8],
        [148.17, 256.98],
        "0.007",
    ),
    (
        "TiO2",
        "shornikov_2011_fig1a_TiO2_activity",
        "2050",
        "0.021",
        [109.0, 262.0],
        [1416.4, 2469.8],
        [108.96, 261.96],
        None,
    ),
    (
        "SiO2",
        "shornikov_2011_fig1b_SiO2_activity",
        "2050",
        "0.0023",
        [421.0, 198.0],
        [2664.2, 2213.6],
        [421.24, 198.13],
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
        assert row["locator"]["page"] == 1


def test_table_and_figure_reads_are_pinned():
    document = _load()
    gibbs = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    assert len(gibbs) == 32
    assert {row["phase"] for row in gibbs} == {"cr", "l", "g"}
    _assert_rendered_cells(document)

    oxygen = next(
        row
        for _formula, row in _observations(document)
        if row["observation_id"] == "shornikov_2011_reducing_pO2_boundary"
    )
    assert oxygen["values"]["pressure_as_printed"] == "5.0 . 10\u20135"
    assert oxygen["values"]["pressure_atm"] == "5.0e-5"

    activity_points = []
    for _formula, row in _observations(document):
        if row["values"].get("quantity") != "activity":
            continue
        points = row["values"].get("points") or []
        activity_points.extend(points)
    assert len(activity_points) == 67
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
        row = document["species"][formula]["observations"]
        match = next(item for item in row if item["observation_id"] == observation_id)
        point = match["values"]["points"][0]
        assert point["T_K"] == temperature
        assert point["activity"] == activity
        assert point["embedded_px_read_1"] == embedded
        assert point["page_600dpi_px_read_1"] == page
        assert point["embedded_px_read_2"] == embedded_2
        if uncertainty is None:
            assert "read_uncertainty_dex" not in point
        else:
            assert point["read_uncertainty_dex"] == uncertainty


def test_mutating_the_consumed_gibbs_value_or_deleting_a_row_fails_the_pin():
    document = _load()
    _assert_rendered_cells(document)
    solid = document["species"]["CaO"]["observations"][0]
    assert solid["observation_id"].endswith("r03_c02")
    solid["values"]["value"] = "-999"
    with pytest.raises(AssertionError):
        _assert_rendered_cells(document)

    document = _load()
    gibbs = [
        row
        for row in document["species"]["CaO"]["observations"]
        if row["type"] == "gibbs_table"
    ]
    assert any(row["values"]["value"] == "-416.099" for row in gibbs)
    document["species"]["CaO"]["observations"] = [
        row
        for row in document["species"]["CaO"]["observations"]
        if row["values"].get("value") != "-416.099"
    ]
    remaining = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    assert len(remaining) == 31
    assert len(remaining) != 32


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
    assert len(migrated) == 112
    assert not result.registry_issues
    assert not result.validation.hard_issues

    gibbs = [row for row in migrated if row.identity.quantity.value.value == "delta_fG"]
    activities = [
        row
        for row in migrated
        if row.identity.quantity.value.value == "activity"
        and row.value.kind is ValueKind.POINT
    ]
    curves = [
        row
        for row in migrated
        if row.identity.quantity.value.value == "activity"
        and row.value.kind is ValueKind.UNAVAILABLE
    ]
    pressures = [row for row in migrated if row.identity.quantity.value.value == "p_partial"]
    assert len(gibbs) == 32
    assert len(activities) == 67
    assert len(curves) == 12
    assert len(pressures) == 1

    for _formula, observation_id, value, _phase in RENDERED_CELLS:
        row = next(item for item in gibbs if item.observation_id.endswith(observation_id))
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(value)
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("2050")
        temperature = row.point_conditions["temperature_K"].state.value
        point = temperature.point if hasattr(temperature, "point") else temperature
        assert point == Decimal("2050")

    for row in migrated:
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value

    sample = next(row for row in activities if "fig1a_CaO_activity" in row.observation_id)
    assert sample.identity.temperature_K.value == Decimal("2050")
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

    pressure = pressures[0]
    assert pressure.value.kind is ValueKind.POINT
    # 5.0e-5 atm = 5.06625 Pa. The store keeps pascals.
    assert pressure.value.point == Decimal("5.066250")
    assert pressure.identity.temperature_K.value == Decimal("2300")
