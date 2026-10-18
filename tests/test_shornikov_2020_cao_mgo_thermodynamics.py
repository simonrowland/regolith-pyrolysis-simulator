"""Source pins and isolated migration for Shornikov (2020)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
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

# Figure 1 marker centres on the embedded JPEG. Read 1 and read 2 are
# independent centre estimates. T, mol% CaO, both pixel reads, and the
# half-difference uncertainty are literals. The axis is linear, so the
# uncertainty is in kelvin and mol%, not dex.
FIGURE_READS = (
    (
        "shornikov_2020_fig1_s1_01",
        "2960.8",
        "20.01",
        [206.08, 136.5],
        [205.58, 136.56],
        [925.9, 2536.8],
        "0.0673",
        "0.045",
    ),
    (
        "shornikov_2020_fig1_s2_02",
        "2998.8",
        "9.98",
        [150.37, 119.26],
        [150.38, 119.31],
        [758.6, 2485.1],
        "0.0489",
        "0.0013",
    ),
    (
        "shornikov_2020_fig1_s3_08",
        "2706.1",
        "84.32",
        [562.98, 251.99],
        [562.65, 251.85],
        [1997.6, 2883.5],
        "0.1567",
        "0.029",
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

    markers = [
        row
        for row in document["species"]["CaO-MgO"]["observations"]
        if row["values"].get("property_kind") == "phase_diagram_temperature"
    ]
    assert len(markers) == 32
    for (
        observation_id,
        temperature,
        mole_pct,
        embedded,
        embedded_2,
        page,
        uncertainty_k,
        uncertainty_mol,
    ) in FIGURE_READS:
        match = next(item for item in markers if item["observation_id"] == observation_id)
        assert match["values"]["embedded_px_read_1"] == embedded
        assert match["values"]["T_K"] == temperature
        assert match["values"]["cao_mol_pct_read"] == mole_pct
        assert match["values"]["embedded_px_read_2"] == embedded_2
        assert match["values"]["page_600dpi_px_read_1"] == page
        assert match["values"]["read_uncertainty_K"] == uncertainty_k
        assert match["values"]["read_uncertainty_mol_pct"] == uncertainty_mol
        assert match["values"]["read_uncertainty_K"] not in {"0", "0.0", "0.000"}
        assert match["values"]["read_uncertainty_mol_pct"] not in {"0", "0.0", "0.000"}

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
    assert len(migrated) == 82
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
    assert len(transitions) == 47
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

    figure = next(row for row in transitions if row.observation_id.endswith("fig1_s1_01"))
    assert figure.value.point == Decimal("2960.8")
    assert figure.identity.subtype.value == "phase_diagram_temperature"
    assert figure.identity.composition.is_not_applicable
    assert figure.point_conditions["composition"].state.is_unknown
    assert "mole percent" in figure.point_conditions["composition"].state.reason

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
    assert len(contexts) == 5
    assert not any(quantity(row) == "delta_fG" and "fig2" in row.observation_id for row in migrated)
