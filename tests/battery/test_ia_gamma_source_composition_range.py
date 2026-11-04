from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Engine, ExecutionState
from simulator.battery.generators.bench import activity_request_for_engine
from simulator.battery.score import predict_with_engine
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    P2O5_ACTIVITY_COEFFICIENT,
    melt_oxide_activity,
)
from tests.battery.test_melt_activity_requirements import _case, _composition


_NOTICE_ID = "ia_constant_gamma_outside_source_composition_range"


def _score_activity(
    oxide: str,
    composition: dict[str, str],
    temperature_K: str,
    *,
    baseline_value: str | None = None,
    source_id: str | None = None,
    observation_id: str | None = None,
):
    experiment, bench, observation = _case(
        composition=_composition(*composition.items()),
        formula=oxide,
        temperature=Decimal(temperature_K),
    )
    if source_id is not None or observation_id is not None:
        observation = replace(
            observation,
            source_id=source_id or observation.source_id,
            observation_id=observation_id or observation.observation_id,
        )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        observation,
        experiment=experiment,
        bench=bench,
        handles={},
        isolated=False,
    )
    assert prediction.execution.state is ExecutionState.PRODUCED
    if baseline_value is None:
        request = activity_request_for_engine(
            experiment, observation, "internal-analytical"
        )
        assert request is not None and request.payload is not None
        expected = melt_oxide_activity(
            oxide,
            request.payload["composition_mol"],
            temperature_K=float(temperature_K),
        )
        assert expected is not None
        assert prediction.value == Decimal(
            str(expected.thermodynamic_parent_activity())
        )
    else:
        assert prediction.value == Decimal(baseline_value)
    return prediction


def _source_range_notices(prediction):
    return tuple(
        notice for notice in prediction.notices if notice.reason.startswith(_NOTICE_ID)
    )


def test_kambayashi_kato_mgo_binary_row_is_flagged_without_changing_value() -> None:
    extract_path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/extracts/kambayashi-kato-1984-mgo-sio2-1973k.yaml"
    )
    extract = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    row = next(
        row
        for row in extract["species"]["MgO"]["observations"]
        if row["observation_id"]
        == "kambayashi_kato_1984_mgo_sio2_1973k_p006_t02_r02_c03"
    )
    # Kambayashi & Kato 1984 Table 2, p. 246. This binary composition is
    # outside the cited CMAS basicity band.
    prediction = _score_activity(
        "MgO",
        row["values"]["composition_mole_fraction"],
        row["values"]["T_K"],
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.009960556690592466",
        source_id=extract["source_id"],
        observation_id=row["observation_id"],
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=MgO" in reason
    assert "CMAS" in reason and "0.55" in reason and "0.65" in reason
    assert "Beckett 2002" in reason


def test_cmas_midrange_row_is_not_flagged() -> None:
    prediction = _score_activity(
        "MgO",
        {"CaO": "0.25", "MgO": "0.25", "Al2O3": "0.1", "SiO2": "0.4"},
        "1873",
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.011042745776710052",
    )

    assert _source_range_notices(prediction) == ()


def test_unrecorded_source_composition_range_is_still_flagged() -> None:
    prediction = _score_activity(
        "K2O", {"K2O": "0.2", "SiO2": "0.8"}, "1500"
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=K2O" in reason
    assert "unrecorded" in reason.lower()
    assert "De Maria et al. 1971" in reason


def test_mno_mass_percent_limit_is_enforced() -> None:
    prediction = _score_activity(
        "MnO",
        {"MnO": "0.8", "CaO": "0.1", "SiO2": "0.1"},
        "1873",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=MnO" in reason
    assert "MnO <9.6 mass %" in reason
    assert "Ohta & Suito 1995" in reason


def test_feto_mass_percent_limit_is_enforced() -> None:
    prediction = _score_activity(
        "MnO",
        {
            "CaO": "0.4",
            "SiO2": "0.33",
            "Al2O3": "0.2",
            "FeO": "0.05",
            "MnO": "0.02",
        },
        "1873",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=MnO" in reason
    assert "FetO <3 mass %" in reason
    assert "Ohta & Suito 1995" in reason


def test_single_isotherm_source_names_source_and_row_temperatures() -> None:
    prediction = _score_activity(
        "MnO",
        {
            "CaO": "0.4",
            "SiO2": "0.4",
            "Al2O3": "0.15",
            "FeO": "0.01",
            "MnO": "0.04",
        },
        "1600",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "source_isotherm=1873 K" in reason
    assert "row_temperature=1600 K" in reason
    assert "Ohta & Suito 1995" in reason


def test_anorthite_diopside_primary_bounds_are_unrecorded() -> None:
    prediction = _score_activity(
        "SiO2", {"CaO": "0.5", "SiO2": "0.5"}, "1600"
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=SiO2" in reason
    assert "range=unrecorded" in reason
    assert "Ghiorso & Sack 1995" in reason


def test_beckett_mgo_mole_fraction_limit_is_enforced_inside_basicity_window() -> None:
    prediction = _score_activity(
        "MgO",
        {"MgO": "0.6", "SiO2": "0.4"},
        "1873",
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.029152848850514536",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=MgO" in reason
    assert "X(MgO)" in reason and "0.55" in reason
    assert "Beckett 2002" in reason


def test_beckett_full_liquid_mole_fraction_set_is_declared_and_enforced() -> None:
    prediction = _score_activity(
        "MgO",
        {"MgO": "0.1", "Al2O3": "0.46", "SiO2": "0.44"},
        "1873",
        # Golden output from the unmodified 0ea42527e scorer.
        baseline_value="0.003327950782022208",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    assert "X(Al2O3)" in notices[0].reason
    assert "Beckett 2002" in notices[0].reason

    expected_ranges = (
        ("CaO", 0.0, 0.71),
        ("MgO", 0.0, 0.55),
        ("Al2O3", 0.0, 0.45),
        ("SiO2", 0.0, 0.95),
    )
    assert (
        MELT_OXIDE_ACTIVITY_COEFFICIENTS["CaO"]
        .source_composition_range.formula_mole_fraction_ranges
        == expected_ranges
    )
    assert (
        MELT_OXIDE_ACTIVITY_COEFFICIENTS["MgO"]
        .source_composition_range.formula_mole_fraction_ranges
        == expected_ranges
    )


def test_tio2_primary_composition_range_is_unrecorded() -> None:
    prediction = _score_activity(
        "TiO2",
        {
            "CaO": "0.25",
            "MgO": "0.25",
            "Al2O3": "0.1",
            "SiO2": "0.39",
            "TiO2": "0.01",
        },
        "1773",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=TiO2" in reason
    assert "range=unrecorded" in reason
    assert "Ghiorso & Sack 1995" in reason


def test_p2o5_primary_composition_range_is_unrecorded() -> None:
    prediction = _score_activity(
        "P2O5",
        {
            "CaO": "0.25",
            "MgO": "0.2",
            "FeO": "0.1",
            "SiO2": "0.44",
            "P2O5": "0.01",
        },
        "1873",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "oxide=P2O5" in reason
    assert "range=unrecorded" in reason
    assert "Turkdogan 2000" in reason


def test_chromium_source_isotherm_is_enforced_with_correct_table_page() -> None:
    prediction = _score_activity(
        "Cr2O3",
        {"CaO": "0.4", "SiO2": "0.5", "Cr2O3": "0.1"},
        "1200",
    )

    notices = _source_range_notices(prediction)
    assert len(notices) == 1
    reason = notices[0].reason
    assert "source_isotherm=1773.15 K" in reason
    assert "row_temperature=1200 K" in reason
    assert "Pretorius & Muan 1992 Table II p. 1366" in reason


def test_every_declared_gamma_has_source_composition_range_metadata() -> None:
    coefficients = (
        *MELT_OXIDE_ACTIVITY_COEFFICIENTS.values(),
        P2O5_ACTIVITY_COEFFICIENT,
    )
    assert all(coeff.source_composition_range for coeff in coefficients)
    assert all(coeff.source_composition_range.citation for coeff in coefficients)
    assert all(
        coeff.source_composition_range.description
        for coeff in coefficients
    )
