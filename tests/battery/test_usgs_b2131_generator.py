"""Bulletin 2131 generator: raster formulas, printed phase windows, reference lines."""

import json
from decimal import Decimal

from simulator.battery.enums import PerBasis, Phase, Quantity, UncertaintyKind, ValueKind
from simulator.battery.generators.compilation_formula import aligned_raster_formula
from simulator.battery.generators.usgs_b2131 import (
    SOURCE_ID,
    formula_for_record,
    generate_record,
    owns_record,
    phase_windows,
)
from simulator.battery.generators.usgs_b2131_raster import FORMULA_RASTER, PHASE_SENTENCE
from simulator.battery.identity import bar_to_pa, quantity_token
from simulator.battery.migrate import REPO_ROOT

_ROOT = (
    REPO_ROOT
    / "data"
    / "literature"
    / "compilations"
    / "robie-hemingway-1995-usgs-b2131"
)


def _load(relative: str) -> dict:
    document = json.loads((_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _series_temperatures(observation) -> list[Decimal]:
    assert observation.value.kind is ValueKind.SERIES
    return [temperature for temperature, _value in observation.value.series]


def test_committed_raster_rows_are_allowed_edits() -> None:
    named = {
        "high-temperature-p078": ("ca", "Ca"),
        "high-temperature-p285": ("caso4", "CaSO4"),
        "high-temperature-p353": ("MgSio3", "MgSiO3"),
        "high-temperature-p320": ("CaMgSio4", "CaMgSiO4"),
    }
    for record_id, (published, corrected) in named.items():
        assert FORMULA_RASTER[record_id][:2] == (published, corrected)
    assert all(row[0] != "PeS" for row in FORMULA_RASTER.values())
    for record_id, (published, corrected, quote, _pdf, _printed) in FORMULA_RASTER.items():
        assert aligned_raster_formula(published, corrected) == corrected
        assert quote
        document = _load(f"records/{record_id}.json")
        formula, evidence = formula_for_record(document)
        assert formula == corrected
        assert evidence == quote
        assert document["formula_as_published"] == published


def test_phase_sentence_overrides_keep_the_mineru_bounds() -> None:
    k2o = phase_windows(PHASE_SENTENCE["high-temperature-p195"])
    assert [(window.lo, window.hi) for window in k2o] == [
        (Decimal("298.15"), Decimal("1200"))
    ]
    assert "high-temperature-p207" not in PHASE_SENTENCE
    geikielite = _load("records/high-temperature-p207.json")
    windows = phase_windows(geikielite["phase_as_published"])
    assert [(window.lo, window.hi) for window in windows] == [
        (Decimal("298.15"), Decimal("519")),
        (Decimal("519"), Decimal("1800")),
    ]


def test_calcium_windows_do_not_span_a_printed_bound() -> None:
    document = _load("records/high-temperature-p078.json")
    formula, quote = formula_for_record(document)
    assert formula == "Ca"
    assert quote is not None and "716" in quote and "1774" in quote
    generated = generate_record(document)
    observations = generated.observations
    assert observations
    quantities = {quantity_token(obs.identity) for obs in observations}
    assert quantities <= {
        Quantity.CP,
        Quantity.S,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
        Quantity.LOG10_KF,
    }
    bounds = {
        0: (Decimal("298.15"), Decimal("716"), Phase.CR),
        1: (Decimal("716"), Decimal("1115"), Phase.CR),
        2: (Decimal("1115"), Decimal("1755"), Phase.L),
        3: (Decimal("1774"), Decimal("1800"), Phase.G),
    }
    for observation in observations:
        suffix = observation.observation_id.rsplit(":w", 1)[1]
        index = int(suffix)
        low, high, phase = bounds[index]
        temperatures = _series_temperatures(observation)
        assert temperatures
        assert all(low <= temperature <= high for temperature in temperatures)
        assert observation.identity.species.phase.value is phase
        assert observation.identity.species.formula == "Ca"
    cp = [
        obs
        for obs in observations
        if quantity_token(obs.identity) is Quantity.CP
    ]
    at_716 = [obs for obs in cp if Decimal("716") in _series_temperatures(obs)]
    assert len(at_716) == 2
    for observation in at_716:
        temperatures = _series_temperatures(observation)
        below = any(temperature < Decimal("716") for temperature in temperatures)
        above = any(temperature > Decimal("716") for temperature in temperatures)
        assert below != above
    at_1115 = [obs for obs in cp if Decimal("1115") in _series_temperatures(obs)]
    assert {obs.identity.species.phase.value for obs in at_1115} == {Phase.CR, Phase.L}
    gas = [
        obs
        for obs in observations
        if Decimal("1774") in _series_temperatures(obs)
    ]
    assert gas
    assert {obs.identity.species.phase.value for obs in gas} == {Phase.G}
    liquid = [
        obs
        for obs in observations
        if obs.identity.species.phase.value is Phase.L
    ]
    assert all(
        temperature <= Decimal("1755")
        for obs in liquid
        for temperature in _series_temperatures(obs)
    )


def test_reference_state_second_line_is_the_printed_uncertainty() -> None:
    silver = generate_record(_load("summary/reference-p005-02.json"))
    gibbs = [
        obs
        for obs in silver.observations
        if quantity_token(obs.identity) is Quantity.DELTA_FG
    ]
    assert len(gibbs) == 1
    point = gibbs[0]
    assert point.value.kind is ValueKind.POINT
    assert point.value.point == Decimal("77.1")
    assert point.uncertainty.kind is UncertaintyKind.PRINTED
    assert point.uncertainty.value == Decimal("0.1")
    assert point.uncertainty.verbatim == "0.1"
    assert point.identity.temperature_K.value == Decimal("298.15")
    assert point.identity.species.phase.is_unknown
    # Species stores the printed trailing + on the charge axis.
    assert formula_for_record(_load("summary/reference-p005-02.json"))[0] == "Ag+"
    assert point.identity.species.formula == "Ag"
    assert point.identity.species.charge.value == 1
    thenardite = generate_record(_load("summary/reference-p029-04.json"))
    enthalpy = next(
        obs
        for obs in thenardite.observations
        if quantity_token(obs.identity) is Quantity.DELTA_FH
    )
    assert enthalpy.value.point == Decimal("-1387.8")
    assert enthalpy.uncertainty.value == Decimal("0.4")
    assert enthalpy.identity.species.formula == "Na2S04"


def test_lime_formation_basis_is_not_copied_onto_heat_capacity() -> None:
    generated = generate_record(_load("records/high-temperature-p182.json"))
    by_quantity = {quantity_token(obs.identity): obs for obs in generated.observations}
    assert set(by_quantity) == {
        Quantity.CP,
        Quantity.S,
        Quantity.DELTA_FH,
        Quantity.DELTA_FG,
        Quantity.LOG10_KF,
    }
    for quantity in (Quantity.DELTA_FG, Quantity.LOG10_KF):
        identity = by_quantity[quantity].identity
        assert identity.per.value is PerBasis.MOL_SPECIES
        assert ":313-315" in identity.per.reason
        assert identity.standard_pressure_Pa.value == bar_to_pa(1)
        assert identity.reaction.is_unknown
        assert identity.species.phase.value is Phase.CR
    heat = by_quantity[Quantity.CP].identity
    assert heat.per.value is PerBasis.MOL_SPECIES
    assert heat.per.reason is None
    assert heat.standard_pressure_Pa.is_unknown
    enthalpy = by_quantity[Quantity.DELTA_FH].identity
    assert enthalpy.per.reason is None
    assert enthalpy.standard_pressure_Pa.is_unknown


def test_records_without_a_parsed_thermochemical_cell_stay_generic() -> None:
    assert owns_record(_load("auxiliary/atomic-weight-001.json")) is False
    assert owns_record(_load("summary/cp-p041-01.json")) is False
    assert owns_record(
        {
            "source_id": SOURCE_ID,
            "record_id": "al-ht",
            "table_kind": "high_temperature",
            "formula": "Al",
            "rows": [
                {
                    "temperature": {"value": 298.15},
                    "delta_f_G": {"value": 0.0},
                    "delta_fG": 0.0,
                }
            ],
        }
    ) is False
    assert owns_record(
        {
            "source_id": SOURCE_ID,
            "record_id": "empty",
            "table_kind": "high_temperature",
            "rows": [
                {
                    "cells": {
                        "temperature": {"raw": "300", "value": 300},
                        "formation_gibbs": {"raw": "o.o", "value": None},
                    }
                }
            ],
        }
    ) is False
    assert owns_record(_load("records/high-temperature-p078.json")) is True
    assert owns_record(_load("summary/reference-p005-02.json")) is True


def test_paths_are_the_compilation_record() -> None:
    calcium = generate_record(_load("records/high-temperature-p078.json"))
    assert calcium.observations[0].locator.source_path == (
        "data/literature/compilations/"
        "robie-hemingway-1995-usgs-b2131/records/high-temperature-p078.json"
    )
    silver = generate_record(_load("summary/reference-p005-02.json"))
    assert silver.observations[0].locator.source_path == (
        "data/literature/compilations/"
        "robie-hemingway-1995-usgs-b2131/summary/reference-p005-02.json"
    )
