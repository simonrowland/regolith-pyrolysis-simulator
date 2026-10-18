"""Source pins and isolated migration for Shornikov (2020)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AdmissionStatus, Phase, UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2020-cao-mgo-thermodynamics.yaml"

# Table cells checked against pdftotext. MinerU digits match. The page glyph
# for a minus is an en-dash in the reference years only; numeric cells are ASCII.
RENDERED_CELLS = (
    ("CaO", "shornikov_2020_cao_mgo_thermodynamics_p003_t02_r03_c02", "-284.532", Phase.CR, "2973"),
    ("CaO", "shornikov_2020_cao_mgo_thermodynamics_p003_t02_r03_c04", "-279.824", Phase.L, "2973"),
    ("O2", "shornikov_2020_cao_mgo_thermodynamics_p003_t02_r10_c06", "0.000", Phase.G, "2973"),
    ("CaO", "shornikov_2020_cao_mgo_thermodynamics_p002_t01_r03_c02", "52.000", Phase.CR, "2900"),
    ("CaO", "shornikov_2020_cao_mgo_thermodynamics_p002_t01_r04_c02", "68.000", Phase.CR, None),
    ("MgO", "shornikov_2020_cao_mgo_thermodynamics_p002_t01_r10_c05", "63.718", Phase.CR, "3246"),
)

# Nominal T and mol% are two significant figures. Read 1 (0.1 K / 0.1 mol%)
# and the visual page centre stay beside them. s4_04 is the maroon disk.
# s3_07 is the horizontal solidus. s3_04 is not an observation.
FIGURE_READS = (
    (
        "shornikov_2020_fig1_s1_01",
        "3.0e3",
        "2960.7",
        "2.0e1",
        [206.0, 136.5],
        [928.0, 2536.5],
        "liquidus",
        "symbol 1 left-half teal",
    ),
    (
        "shornikov_2020_fig1_s4_04",
        "2.7e3",
        "2697.2",
        "5.0e1",
        [372.5, 256.0],
        [1426.5, 2896.5],
        "liquidus",
        "symbol 4 half-red disk, maroon on the upper half",
    ),
    (
        "shornikov_2020_fig1_s3_07",
        "2.6e3",
        "2642.9",
        "5.9e1",
        [424.38, 280.65],
        [1582.5, 2969.5],
        "solidus",
        "symbol 3 blue filled circle",
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


def _figure_markers(document):
    return [
        row
        for row in document["species"]["CaO-MgO"]["observations"]
        if row["observation_id"].startswith("shornikov_2020_fig1_")
    ]


def _assert_figure_reads(document) -> None:
    markers = _figure_markers(document)
    assert len(markers) == 19
    assert sum(row["values"]["property_kind"] == "liquidus" for row in markers) == 15
    assert sum(row["values"]["property_kind"] == "solidus" for row in markers) == 4
    gaps = [
        row
        for row in document["species"]["CaO-MgO"]["context"]
        if row["type"] == "figure_boundary_schema_gap"
    ]
    assert len(gaps) == 21
    assert len(markers) + len(gaps) == 40
    for row in markers:
        values = row["values"]
        assert values["property_kind"] in {"liquidus", "solidus"}
        assert values["status"] == "typed_refusal"
        assert "DERIVED" in values["reason"]
        assert values["read_2_method"] == "visual_page_grid"
        assert Decimal(values["read_uncertainty_K"]) > 0
        assert Decimal(values["read_uncertainty_mol_pct"]) > 0
    for row in gaps:
        assert row["values"]["property_kind"] == "not_a_liquidus_or_solidus"
        assert "liquidus" in row["values"]["schema_gap"]
        assert "solidus" in row["values"]["schema_gap"]
        assert Decimal(row["values"]["read_uncertainty_K"]) > 0
    for (
        observation_id,
        temperature,
        temperature_read,
        mole_pct,
        embedded,
        page,
        kind,
        marker,
    ) in FIGURE_READS:
        match = next(item for item in markers if item["observation_id"] == observation_id)
        assert match["values"]["T_K"] == temperature
        assert match["values"]["T_K_read_1"] == temperature_read
        assert match["values"]["cao_mol_pct_read"] == mole_pct
        assert match["values"]["embedded_px_read_1"] == embedded
        assert match["values"]["page_600dpi_px_read_2"] == page
        assert match["values"]["property_kind"] == kind
        assert match["locator"]["marker"] == marker
    solvus = next(
        row
        for row in gaps
        if row["observation_id"] == "shornikov_2020_fig1_s3_04"
    )
    assert solvus["values"]["T_K_read_1"] == "2603.9"
    assert solvus["values"]["page_600dpi_px_read_2"] == [699.5, 3022.0]
    assert "regions I and II contain no liquid" in solvus["values"]["schema_gap"]
    crescent = next(
        row
        for row in gaps
        if row["observation_id"] == "shornikov_2020_fig1_s2_09"
    )
    assert crescent["values"]["T_K_read_1"] == "2681.8"
    assert crescent["values"]["page_600dpi_px_read_2"] == [1426.0, 2914.0]


def _assert_rendered_cells(document) -> None:
    observations = _observations(document)
    for formula, observation_id, value, phase, temperature in RENDERED_CELLS:
        row = next(
            row
            for candidate, row in observations
            if candidate == formula and row["observation_id"] == observation_id
        )
        assert row["values"]["value"] == value
        assert row["phase"] == phase.value
        if temperature is None:
            assert "T_K" not in row["values"]
            assert row["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
        else:
            assert row["values"]["T_K"] == temperature


def test_table_and_figure_reads_are_pinned():
    document = _load()
    gibbs = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    fusion = [
        row
        for _formula, row in _observations(document)
        if row["values"].get("quantity") == "heat_of_fusion"
    ]
    assert len(gibbs) == 15
    assert {row["phase"] for row in gibbs} == {"cr", "l", "g"}
    assert sum(row["phase"] == "cr" for row in gibbs) == 3
    assert sum(row["phase"] == "l" for row in gibbs) == 2
    assert sum(row["phase"] == "g" for row in gibbs) == 10
    assert len(fusion) == 14
    _assert_rendered_cells(document)
    _assert_figure_reads(document)

    manara = next(
        row
        for row in document["species"]["CaO"]["observations"]
        if row["observation_id"] == "shornikov_2020_tm_cao_r08"
    )
    assert manara["values"]["T_K"] == "3222"
    assert manara["values"]["T_m_uncertainty_K"] == "25"
    assert "value" not in manara["values"]

    prose = next(
        row
        for row in document["species"]["CaO-MgO"]["context"]
        if row["observation_id"] == "shornikov_2020_prose_mixing_minimum"
    )
    assert "–10 kJ/mol" in prose["values"]["value_as_printed"]
    assert "50–55 mol. % CaO" in prose["values"]["value_as_printed"]

    prose_rows = {
        row["observation_id"]: row
        for row in document["species"]["CaO-MgO"]["context"]
    }
    rankin = prose_rows["shornikov_2020_prose_rankin_merwin_eutectic"]
    assert rankin["type"] == "quoted_prose_schema_gap"
    assert "59 mol. % CaO at 2573±50 K" in rankin["values"]["value_as_printed"]
    eutectic = prose_rows["shornikov_2020_prose_eutectic_range"]
    assert "45 (Afanasjev & Saldau, 1950)" in eutectic["values"]["value_as_printed"]
    assert "2643 K" in eutectic["values"]["value_as_printed"]
    liquidus = prose_rows["shornikov_2020_prose_liquidus_sources"]
    assert "Manara et al., 2014" in liquidus["values"]["value_as_printed"]
    kubaschewski = prose_rows["shornikov_2020_prose_kubaschewski_camgo2"]
    assert "3.77±0.62 kJ/mol" in kubaschewski["values"]["value_as_printed"]
    assert "2573 K" in kubaschewski["values"]["value_as_printed"]
    assert "Fletcher et al., 1970" in kubaschewski["values"]["source_criticism"]
    powell = prose_rows["shornikov_2020_prose_powell_searcy"]
    assert "78.65 kJ/mol" in powell["values"]["value_as_printed"]
    assert "36.82 J/mol/K" in powell["values"]["value_as_printed"]
    assert "Lvov & Ugolkov, 2003" in powell["values"]["source_criticism"]


def test_mutating_the_consumed_gibbs_value_or_deleting_a_row_fails_the_pin():
    document = _load()
    _assert_rendered_cells(document)
    solid = next(
        row
        for row in document["species"]["CaO"]["observations"]
        if row["observation_id"].endswith("p003_t02_r03_c02")
    )
    solid["values"]["value"] = "-999"
    with pytest.raises(AssertionError):
        _assert_rendered_cells(document)

    document = _load()
    _assert_figure_reads(document)
    overlap = next(
        row
        for row in document["species"]["CaO-MgO"]["observations"]
        if row["observation_id"] == "shornikov_2020_fig1_s4_04"
    )
    overlap["values"]["T_K"] = "-999"
    with pytest.raises(AssertionError):
        _assert_figure_reads(document)
    document = _load()
    overlap = next(
        row
        for row in document["species"]["CaO-MgO"]["observations"]
        if row["observation_id"] == "shornikov_2020_fig1_s4_04"
    )
    overlap["values"]["embedded_px_read_1"] = [-999, -999]
    with pytest.raises(AssertionError):
        _assert_figure_reads(document)

    document = _load()
    document["species"]["CaO"]["observations"] = [
        row
        for row in document["species"]["CaO"]["observations"]
        if row["values"].get("value") != "-284.532"
    ]
    remaining = [
        row
        for _formula, row in _observations(document)
        if row["type"] == "gibbs_table"
    ]
    assert len(remaining) == 14
    assert len(remaining) != 15


def test_isolated_migration_reaches_temperature_on_every_printed_row():
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
    assert len(migrated) == 69
    assert not result.registry_issues
    assert not result.validation.hard_issues

    def quantity(row):
        return row.identity.quantity.value.value

    gibbs = [row for row in migrated if quantity(row) == "delta_fG"]
    fusion = [row for row in migrated if quantity(row) == "heat_of_fusion"]
    transitions = [row for row in migrated if quantity(row) == "transition_temperature"]
    curves = [
        row
        for row in migrated
        if quantity(row) == "activity" and row.value.kind is ValueKind.UNAVAILABLE
    ]
    assert len(gibbs) == 15
    assert len(fusion) == 14
    assert len(transitions) == 34
    assert len(curves) == 6

    for formula, observation_id, value, _phase, temperature in RENDERED_CELLS:
        pool = gibbs if observation_id.find("_t02_") >= 0 else fusion
        row = next(item for item in pool if item.observation_id.endswith(observation_id))
        assert row.value.point == Decimal(value)
        if temperature is None:
            assert row.identity.temperature_K.is_unknown
            assert "dash" in row.point_conditions["temperature_K"].state.reason
        else:
            assert row.identity.temperature_K.is_value
            assert row.identity.temperature_K.value == Decimal(temperature)

    for row in gibbs:
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("2973")
        assert row.identity.standard_pressure_Pa.is_unknown
        assert row.identity.reaction.is_unknown
        assert row.identity.formation_elements.is_unknown

    eliezer = next(row for row in fusion if row.observation_id.endswith("r04_c02"))
    assert eliezer.identity.temperature_K.is_unknown
    for row in fusion:
        if row is eliezer:
            continue
        assert row.identity.temperature_K.is_value

    for row in transitions:
        assert row.identity.temperature_K.is_not_applicable
        assert row.value.kind is ValueKind.POINT
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.identity.subtype.is_value

    figure_rows = [row for row in transitions if "fig1_" in row.observation_id]
    assert len(figure_rows) == 19
    assert sum(row.identity.subtype.value == "liquidus" for row in figure_rows) == 15
    assert sum(row.identity.subtype.value == "solidus" for row in figure_rows) == 4
    assert not any(row.observation_id.endswith("fig1_s3_04") for row in figure_rows)
    for row in figure_rows:
        assert row.identity.subtype.value in {"liquidus", "solidus"}
        assert row.admission.status is AdmissionStatus.REJECTED
        assert "DERIVED" in row.admission.reason
        assert row.uncertainty.kind is UncertaintyKind.NONE
        assert row.uncertainty.kind is not UncertaintyKind.PRINTED
        assert row.value.kind is ValueKind.POINT
        assert row.identity.composition.is_unknown
        assert row.identity.composition.reason == "no composition mapped from source"
        assert row.point_conditions["composition"].state.is_unknown
        assert "mole percent" in row.point_conditions["composition"].state.reason

    from simulator.battery.score import _printed_uncertainty_band

    overlap = next(row for row in figure_rows if row.observation_id.endswith("fig1_s4_04"))
    assert overlap.value.point == Decimal("2.7e3")
    assert overlap.identity.subtype.value == "liquidus"
    band = _printed_uncertainty_band(
        overlap.identity.quantity.value,
        overlap.uncertainty,
        overlap.value.point,
    )
    assert band.band is None
    solidus = next(row for row in figure_rows if row.observation_id.endswith("fig1_s3_07"))
    assert solidus.identity.subtype.value == "solidus"
    assert solidus.value.point == Decimal("2.6e3")

    table_tm = next(row for row in transitions if row.observation_id.endswith("tm_cao_r08"))
    assert table_tm.value.point == Decimal("3222")
    assert table_tm.identity.subtype.value == "melting_point"

    for row in curves:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value

    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row.get("source_id") == document["source_id"]
    ]
    assert len(contexts) == 31
    assert not any(quantity(row) == "delta_fG" and "fig2" in row.observation_id for row in migrated)
