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

# Experimental Rein–Chipman centres. Read 1 is the stored disk peak on the
# embedded JPEG. Read 2 is a visual centre of the filled disk on a fresh
# 600 dpi page crop. T, activity, both pixel reads, and the half-difference
# uncertainty are literals, not a comparison with an audit file.
FIGURE_READS = (
    (
        "CaO",
        "shornikov_2016_fig1a_CaO_rein1965_activity",
        "1873",
        "0.0038",
        [283.0, 300.0],
        [1440.3, 5484.2],
        [1441.0, 5485.0],
        [283.23, 300.28],
        "0.001",
    ),
    (
        "MgO",
        "shornikov_2016_fig1a_MgO_rein1965_activity",
        "1873",
        "0.096",
        [294.0, 134.0],
        [1473.3, 4986.2],
        [1476.0, 4988.0],
        [294.89, 134.61],
        "0.003",
    ),
    (
        "SiO2",
        "shornikov_2016_fig1b_SiO2_rein1965_activity",
        "1873",
        "0.1",
        [930.0, 132.0],
        [3382.4, 4980.2],
        [3381.5, 4980.0],
        [929.71, 131.94],
        None,
    ),
)

# The five experimental markers the first pass missed. Coordinates are the
# measured centres, not the reviewer's approximate starting points.
ADDED_ACTIVITY = (
    ("SiO2", "shornikov_2016_fig1a_SiO2_rein1965_activity", "1", [126.0, 14.0]),
    ("SiO2", "shornikov_2016_fig1b_SiO2_rein1965_activity", "1", [1288.0, 13.5]),
    ("CaO", "shornikov_2016_fig1a_CaO_rein1965_activity", "0.042", [518.0, 176.5]),
    ("CaO", "shornikov_2016_fig1b_CaO_rein1965_activity", "0.021", [928.0, 213.0]),
)

EXPERIMENTAL_COUNTS = {
    "shornikov_2016_fig1a_CaO_rein1965_activity": 5,
    "shornikov_2016_fig1b_CaO_rein1965_activity": 5,
    "shornikov_2016_fig1a_MgO_rein1965_activity": 3,
    "shornikov_2016_fig1b_MgO_rein1965_activity": 3,
    "shornikov_2016_fig1a_SiO2_rein1965_activity": 13,
    "shornikov_2016_fig1b_SiO2_rein1965_activity": 12,
}


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
    assert len(activity_points) == 41
    counts = {
        row["observation_id"]: len(row["values"].get("points") or [])
        for _formula, row in _observations(document)
        if "rein1965" in row["observation_id"]
    }
    assert counts == EXPERIMENTAL_COUNTS
    model_rows = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "activity_coefficient" and "rein1965" not in row["observation_id"]
    ]
    assert len(model_rows) == 6
    for row in model_rows:
        assert not row["values"].get("points")
        assert row["values"]["unavailable_reason"] == "model_curve_not_sampled"
        assert row["values"]["quantity"] == "activity"
        assert row["phase"] == "l"
        assert row["T_K"] == "1873"
    for formula, observation_id, activity, embedded in ADDED_ACTIVITY:
        match = next(
            item
            for item in document["species"][formula]["observations"]
            if item["observation_id"] == observation_id
        )
        point = next(
            item for item in match["values"]["points"] if item["embedded_px_read_1"] == embedded
        )
        assert point["activity"] == activity
        assert point["T_K"] == "1873"
        assert point.get("read_uncertainty_dex") not in {"0", "0.0", "0.000"}
    for (
        formula,
        observation_id,
        temperature,
        activity,
        embedded,
        page,
        page_2,
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
        assert point["page_600dpi_px_read_2"] == page_2
        assert point["embedded_px_read_2"] == embedded_2
        if uncertainty is None:
            assert "read_uncertainty_dex" not in point
        else:
            assert point["read_uncertainty_dex"] == uncertainty
        assert point["log10_activity_ordinate"].startswith("-")

    context = document["species"]["CaO-MgO-SiO2"]["context"]
    mixing = next(
        row
        for row in context
        if row["observation_id"] == "shornikov_2016_fig2a_series4_mixing_energy"
    )
    assert len(mixing["values"]["points"]) == 9
    mixing_b = next(
        row
        for row in context
        if row["observation_id"] == "shornikov_2016_fig2b_series4_mixing_energy"
    )
    assert len(mixing_b["values"]["points"]) == 8
    for row in context:
        if row["type"] == "figure_mixing_energy" and not row["observation_id"].endswith(
            "series4_mixing_energy"
        ):
            assert row["values"]["points"] == []
    mixing_point = next(
        item
        for item in mixing["values"]["points"]
        if item["embedded_px_read_1"] == [299.0, 264.0]
    )
    assert mixing_point["T_K"] == "1873"
    assert mixing_point["delta_Gm_kJ_per_mole_read"] == "-31"
    assert mixing_point["page_600dpi_px_read_1"] == [1204.7, 1260.1]
    assert mixing_point["page_600dpi_px_read_2"] == [1203.5, 1260.5]
    assert mixing_point["embedded_px_read_2"] == [298.60, 264.15]
    assert mixing_point["read_uncertainty_kJ_per_mole"] == "0.007"
    added_mixing = next(
        item
        for item in mixing["values"]["points"]
        if item["embedded_px_read_1"] == [502.0, 358.0]
    )
    assert added_mixing["delta_Gm_kJ_per_mole_read"] == "-40"
    assert added_mixing["T_K"] == "1873"
    for row in context:
        for point in row["values"].get("points") or []:
            assert point.get("read_uncertainty_kJ_per_mole") not in {"0", "0.0", "0.000"}
    for _formula, row in _observations(document):
        for point in row["values"].get("points") or []:
            assert point.get("read_uncertainty_dex") not in {"0", "0.0", "0.000"}
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
    assert len(migrated) == 85
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
    assert len(activities) == 41
    unavailable = [row for row in migrated if row.value.kind is ValueKind.UNAVAILABLE]
    assert len(unavailable) == 6
    assert all(row.identity.temperature_K.value == Decimal("1873") for row in unavailable)

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

    sample = next(row for row in activities if "fig1a_CaO_rein1965_activity" in row.observation_id)
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
