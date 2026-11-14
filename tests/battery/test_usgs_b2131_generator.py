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
from simulator.battery.generators.usgs_b2131_raster import (
    FORMULA_RASTER,
    PHASE_SENTENCE,
    PRINTED_FORMULA_TRANSCRIPTIONS,
)
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


def _compilation_path(record_id: str) -> str:
    folder = "summary" if record_id.startswith("reference-") else "records"
    return f"{folder}/{record_id}.json"


def _series_temperatures(observation) -> list[Decimal]:
    assert observation.value.kind is ValueKind.SERIES
    return [temperature for temperature, _value in observation.value.series]


def test_committed_raster_rows_are_allowed_edits() -> None:
    named = {
        "high-temperature-p078": ("ca", "Ca"),
        "high-temperature-p285": ("caso4", "CaSO4"),
        "high-temperature-p353": ("MgSio3", "MgSiO3"),
        "high-temperature-p320": ("CaMgSio4", "CaMgSiO4"),
        "high-temperature-p280": ("NH4N03", "NH4NO3"),
        "reference-p028-05": ("CuS04", "CuSO4"),
        "reference-p028-11": ("H2S04", "H2SO4"),
        "reference-p028-12": ("K2S04", "K2SO4"),
        "reference-p028-16": ("MgS04", "MgSO4"),
        "reference-p029-04": ("Na2S04", "Na2SO4"),
        "reference-p029-09": ("PbS04", "PbSO4"),
        "reference-p029-11": ("ZnS04", "ZnSO4"),
        "reference-p030-01": ("AlP04", "AlPO4"),
        "reference-p030-07": ("PbMo04", "PbMoO4"),
        "reference-p030-09": ("FeW04", "FeWO4"),
        "reference-p030-10": ("MnW04", "MnWO4"),
        "reference-p030-11": ("PbW04", "PbWO4"),
        "reference-p030-12": ("ZnW04", "ZnWO4"),
        "reference-p037-15": ("NaAlSi04", "NaAlSiO4"),
    }
    for record_id, (published, corrected) in named.items():
        assert FORMULA_RASTER[record_id][:2] == (published, corrected)
    assert all(row[0] != "PeS" for row in FORMULA_RASTER.values())
    for record_id, (published, corrected, quote, _pdf, _printed) in FORMULA_RASTER.items():
        assert aligned_raster_formula(published, corrected) == corrected
        assert quote
        document = _load(_compilation_path(record_id))
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


def test_high_temperature_rows_use_raster_corrected_temperature() -> None:
    printed_points = {
        "high-temperature-p189": (Decimal("1200"), Decimal("-81.2")),
        "high-temperature-p199": (Decimal("1700"), Decimal("-256.9")),
        "high-temperature-p205": (Decimal("1800"), Decimal("146.4")),
        "high-temperature-p217": (Decimal("1800"), Decimal("-512.4")),
        "high-temperature-p219": (Decimal("700"), Decimal("-430.8")),
        "high-temperature-p223": (Decimal("500"), Decimal("-846.0")),
    }
    for record_id, printed_point in printed_points.items():
        document = _load(f"records/{record_id}.json")
        gibbs = [
            observation
            for observation in generate_record(document).observations
            if quantity_token(observation.identity) is Quantity.DELTA_FG
        ]
        assert len(gibbs) == 1
        assert printed_point in gibbs[0].value.series


def test_unparsed_high_temperature_gibbs_is_unavailable_per_phase_window() -> None:
    printed_windows = {
        "high-temperature-p069": 2,
        "high-temperature-p075": 2,
        "high-temperature-p085": 2,
        "high-temperature-p089": 1,
        "high-temperature-p099": 2,
        "high-temperature-p102": 2,
        "high-temperature-p115": 1,
    }
    for record_id, window_count in printed_windows.items():
        document = _load(f"records/{record_id}.json")
        gibbs = [
            observation
            for observation in generate_record(document).observations
            if quantity_token(observation.identity) is Quantity.DELTA_FG
        ]
        assert len(gibbs) == window_count
        assert all(obs.value.kind is ValueKind.UNAVAILABLE for obs in gibbs)
        assert all(
            "unparsed OCR tokens" in (obs.value.unavailable_reason or "")
            for obs in gibbs
        )
        assert all(not obs.value.series and obs.value.point is None for obs in gibbs)


def test_unparsed_reference_gibbs_is_owned_and_unavailable() -> None:
    for record_id in (
        "reference-p005-05",
        "reference-p005-11",
        "reference-p006-05",
        "reference-p007-13",
    ):
        document = _load(f"summary/{record_id}.json")
        generated = generate_record(document)
        gibbs = [
            observation
            for observation in generated.observations
            if quantity_token(observation.identity) is Quantity.DELTA_FG
        ]
        assert owns_record(document)
        assert len(gibbs) == 1
        assert gibbs[0].value.kind is ValueKind.UNAVAILABLE
        assert "unparsed" in (gibbs[0].value.unavailable_reason or "")
        assert gibbs[0].identity.temperature_K.value == Decimal("298.15")


def test_printed_formula_transcriptions_restore_numeric_rows() -> None:
    expected = {
        "high-temperature-p274": (
            "Sr003",
            "SrCO3",
            (Decimal("298.15"), Decimal("-1137.6")),
            (Decimal("1400"), Decimal("-850.8")),
        ),
        "high-temperature-p367": (
            "KAlSi309",
            "KAlSi3O8",
            (Decimal("298.15"), Decimal("-3745.8")),
            (Decimal("1400"), Decimal("-2902.2")),
        ),
    }
    for record_id, (ocr_formula, printed_formula, first, last) in expected.items():
        document = _load(f"records/{record_id}.json")
        assert PRINTED_FORMULA_TRANSCRIPTIONS[record_id][:2] == (
            ocr_formula,
            printed_formula,
        )
        formula, evidence = formula_for_record(document)
        assert formula == printed_formula
        assert evidence and "printed page" in evidence
        gibbs = [
            observation
            for observation in generate_record(document).observations
            if quantity_token(observation.identity) is Quantity.DELTA_FG
        ]
        assert all(obs.identity.species.formula == printed_formula for obs in gibbs)
        points = [point for obs in gibbs for point in obs.value.series]
        assert first in points and last in points
        assert len(points) == 13


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
    assert enthalpy.identity.species.formula == "Na2SO4"


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
    # Magnetite and bromine have a blank published formula. The page formula
    # is not invented from the phase sentence, and the record id is not stored.
    assert owns_record(_load("records/high-temperature-p193.json")) is False
    assert generate_record(_load("records/high-temperature-p193.json")).observations == ()
    assert owns_record(_load("summary/reference-p005-13.json")) is False
    assert generate_record(_load("summary/reference-p005-13.json")).observations == ()
    ammonia = generate_record(_load("records/high-temperature-p280.json"))
    assert ammonia.observations
    assert {obs.identity.species.formula for obs in ammonia.observations} == {"NH4NO3"}


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
