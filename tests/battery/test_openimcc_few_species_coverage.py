"""OpenIMCC few-species coverage: typed vapour outcomes and redox pressure.

The pressure cases load persisted observations through observation_from_plain
and the work experiments through experiment_from_plain. They call
``_fe_redox_pressure`` and do not equilibrate an engine. The one OpenIMCC
test is skipped when the package is not importable.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    Engine,
    FO2Channel,
    MethodToken,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.migrate import (
    experiment_from_plain,
    load_yaml,
    observation_from_plain,
)
from simulator.battery.records import (
    AmbientPressureEvidence,
    FO2Control,
    Notice,
    State,
)
from simulator.battery.score import (
    _FeRedoxRefusal,
    _fe_redox_pressure,
    _openimcc_unmodelled_vapour_reason,
    flagged_strata,
    predict_with_engine,
)
from simulator.physical_constants import ATM_PA_DECIMAL
from tests.battery.factories import (
    activity_identity,
    loc,
    observation,
    tabulation_experiment,
)

_ROOT = Path(__file__).resolve().parents[2]
_KRESS_AIR = (
    "kress-carmichael-1989-lime-iron-silicate-redox::"
    "kc89_table1_11A_Fe3plus_over_Fe2plus"
)
_KRESS_CONVENTION = (
    "kress-carmichael-1989-lime-iron-silicate-redox::"
    "kc89_table1_101A_Fe3plus_over_Fe2plus"
)
_MYSEN84 = "mysen-1984-alkaline-earth-redox::mysen_1984_table1_r01_cs2f23"
_MYSEN_VIRGO = "mysen-virgo-1989-nas-redox::mysen_virgo_1989_p003_t02_r003_c010"
_JAYA_GAS = (
    "jayasuriya-2004-iron-mossbauer::jayasuriya_2004_42k_1pct::"
    "T=1682.15:h=01c415b649f2"
)
_JAYA_NOTE_ONLY = (
    "jayasuriya-2004-iron-mossbauer::jayasuriya_2004_42k_1pct::"
    "T=1682.15:h=a13c2021cd3d"
)
_JAYA_HIGH_P = (
    "jayasuriya-2004-iron-mossbauer::jayasuriya_2004_42k_1pct::"
    "T=1673.15:h=6961c11eda4c"
)
_BORISOV_COMPILATION = "borisov-2018-compilation::borisov_2018_table2_row_10"
_BORISOV_TABLE1 = "borisov-2018-compilation::borisov_2018_table1_row_1"


def _load(extract_name: str, work_name: str, observation_ids: set[str]):
    document = load_yaml(_ROOT / "data/literature/extracts-v2" / extract_name)
    observations = {}
    for raw in document["observations"]:
        if raw.get("observation_id") in observation_ids:
            observations[raw["observation_id"]] = observation_from_plain(raw)
    missing = observation_ids.difference(observations)
    assert not missing, missing
    work = load_yaml(_ROOT / "data/literature/works" / work_name)
    experiments = {
        experiment.experiment_id: experiment
        for experiment in (
            experiment_from_plain(raw) for raw in work.get("experiments") or []
        )
    }
    return observations, experiments


def _pressure(observations, experiments, observation_id: str):
    row = observations[observation_id]
    experiment = experiments[row.experiment_id]
    return _fe_redox_pressure(row, row.identity, experiment)


def _assert_ambient(result, quote: str) -> None:
    assert isinstance(result, tuple), result
    bar, state, notice = result
    assert bar == pytest.approx(float(ATM_PA_DECIMAL) / 1e5)
    assert state.is_value
    assert state.value == ATM_PA_DECIMAL
    assert notice is not None
    assert notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
    assert notice.origin == "simulator.battery.score:_fe_redox_pressure"
    assert "assumed_ambient_pressure" in notice.reason
    assert "total_pressure_Pa=101325 Pa" in notice.reason
    assert quote in notice.reason
    assert "assumed_ambient_pressure" in flagged_strata((notice,))


def _assert_missing(result) -> None:
    assert isinstance(result, _FeRedoxRefusal)
    assert result.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert result.detail == {
        "reason": "missing_input",
        "input": "total_pressure_Pa",
    }


@pytest.mark.parametrize(
    ("formula", "reason"),
    (
        ("Mo", "cell_material_vapour"),
        ("MoO", "cell_material_vapour"),
        ("MoO2", "cell_material_vapour"),
        ("MoO3", "cell_material_vapour"),
        (" MoO3(g)", "cell_material_vapour"),
        ("Ga", "dopant_vapour_outside_model"),
        ("In", "dopant_vapour_outside_model"),
        ("GaO", "dopant_vapour_outside_model"),
        ("Ga2O", "dopant_vapour_outside_model"),
        ("InO", "dopant_vapour_outside_model"),
        ("In2O", "dopant_vapour_outside_model"),
        ("In2O(g)", "dopant_vapour_outside_model"),
    ),
)
def test_unmodelled_vapour_reason_is_typed(formula: str, reason: str) -> None:
    assert _openimcc_unmodelled_vapour_reason(Quantity.P_PARTIAL, formula) == reason
    assert _openimcc_unmodelled_vapour_reason(Quantity.P_SAT, formula) == reason
    assert _openimcc_unmodelled_vapour_reason(Quantity.P_REFERENCE, formula) == reason


@pytest.mark.parametrize(
    ("quantity", "formula"),
    (
        (Quantity.ACTIVITY, "Ga"),
        (Quantity.ACTIVITY_COEFFICIENT, "In"),
        (Quantity.ACTIVITY_COEFFICIENT, "Ga2O"),
        (Quantity.P_PARTIAL, "Cr"),
        (Quantity.P_PARTIAL, "SiO"),
        (Quantity.P_SAT, "Rb"),
    ),
)
def test_unmodelled_vapour_reason_stays_generic(quantity: Quantity, formula: str) -> None:
    assert _openimcc_unmodelled_vapour_reason(quantity, formula) is None


def test_kress_air_anchor_defaults_ambient_pressure() -> None:
    observations, experiments = _load(
        "kress-carmichael-1989-lime-iron-silicate-redox.yaml",
        "10.1016_0016-7037(89)90165-8.yaml",
        {_KRESS_AIR, _KRESS_CONVENTION},
    )
    _assert_ambient(_pressure(observations, experiments, _KRESS_AIR), "1 bar total-pressure")
    _assert_missing(_pressure(observations, experiments, _KRESS_CONVENTION))


def test_mysen_1984_method_sentence_defaults_and_virgo_does_not() -> None:
    mysen, mysen_experiments = _load(
        "mysen-1984-alkaline-earth-redox.yaml",
        "40dedafb27b503fd9faae3f02c089e9fcdc221c06390ce63ce0ba7e6e5a504d4.yaml",
        {_MYSEN84},
    )
    _assert_ambient(_pressure(mysen, mysen_experiments, _MYSEN84), "gas mixing")
    virgo, virgo_experiments = _load(
        "mysen-virgo-1989-nas-redox.yaml",
        "e7e10727e8e64c07d89367ee737b182fe0e3823791a16b3bb6db593d14826e05.yaml",
        {_MYSEN_VIRGO},
    )
    _assert_missing(_pressure(virgo, virgo_experiments, _MYSEN_VIRGO))


def test_jayasuriya_gas_mixing_note_defaults_and_keeps_printed_gigapascal() -> None:
    observations, experiments = _load(
        "jayasuriya-2004-iron-mossbauer.yaml",
        "969f2b22817a66ec32c1f2d4408de8c89201ec1e1ef5202c351f41543ae17721.yaml",
        {_JAYA_GAS, _JAYA_NOTE_ONLY, _JAYA_HIGH_P},
    )
    _assert_ambient(
        _pressure(observations, experiments, _JAYA_GAS),
        "gas-mixture",
    )
    _assert_ambient(
        _pressure(observations, experiments, _JAYA_NOTE_ONLY),
        "gas-mixing",
    )
    bar, state, notice = _pressure(observations, experiments, _JAYA_HIGH_P)
    assert notice is None
    assert state.value == Decimal("1100000000")
    assert bar == pytest.approx(11000.0)


def test_borisov_compilation_defaults_from_the_citation_and_table1_stays_printed() -> None:
    observations, experiments = _load(
        "borisov-2018-compilation.yaml",
        "10.1007_s00410-018-1524-8.yaml",
        {_BORISOV_COMPILATION, _BORISOV_TABLE1},
    )
    _assert_ambient(
        _pressure(observations, experiments, _BORISOV_COMPILATION),
        "a new model for 1 atm data",
    )
    bar, state, notice = _pressure(observations, experiments, _BORISOV_TABLE1)
    assert notice is None
    assert state.value == Decimal("101325")
    assert bar == pytest.approx(1.01325)


def _unknown_pressure_observation(**notice_kwargs):
    identity = replace(
        activity_identity(),
        quantity=Quantity.FE3_FE2_RATIO,
        total_pressure_Pa=State.unknown("total pressure was not printed"),
    )
    notices = ()
    if notice_kwargs:
        notices = (
            Notice(
                kind=NoticeKind.INPUT_OMITTED,
                affected_quantities=(Quantity.FE3_FE2_RATIO,),
                origin="test",
                **notice_kwargs,
            ),
        )
    return observation("row", "exp-1", identity, Decimal("1"), notices=notices)


def test_stated_pressure_is_not_replaced() -> None:
    identity = replace(
        activity_identity(total_P=Decimal("250000")),
        quantity=Quantity.FE3_FE2_RATIO,
    )
    row = observation(
        "row",
        "exp-1",
        identity,
        Decimal("1"),
        notices=(
            Notice(
                kind=NoticeKind.INPUT_OMITTED,
                affected_quantities=(Quantity.FE3_FE2_RATIO,),
                reason="gas-mixing furnace",
                origin="test",
            ),
        ),
    )
    bar, state, notice = _fe_redox_pressure(row, row.identity, tabulation_experiment())
    assert notice is None
    assert state.value == Decimal("250000")
    assert bar == pytest.approx(2.5)


def test_point_pressure_is_used_when_the_identity_omits_it() -> None:
    from tests.battery.factories import located

    row = _unknown_pressure_observation(
        reason="gas-mixing furnace",
    )
    row = replace(
        row,
        point_conditions={
            **(row.point_conditions or {}),
            "total_pressure_Pa": located(Decimal("300000")),
        },
    )
    bar, state, notice = _fe_redox_pressure(row, row.identity, tabulation_experiment())
    assert notice is None
    assert state.value == Decimal("300000")
    assert bar == pytest.approx(3.0)


def test_piston_cylinder_blocks_an_ambient_quote() -> None:
    row = _unknown_pressure_observation(reason="gas-mixing furnace")
    experiment = replace(
        tabulation_experiment(),
        method=State.of(MethodToken.PISTON_CYLINDER),
        ambient_pressure_evidence=AmbientPressureEvidence(
            evidence_class=AmbientPressureEvidenceClass.GAS_MIXING,
            quote="gas-mixing furnace at the stated load",
            locator=loc(),
        ),
    )
    _assert_missing(_fe_redox_pressure(row, row.identity, experiment))


def test_kbar_text_blocks_a_gas_mixing_phrase() -> None:
    row = _unknown_pressure_observation(reason="gas mixing at 11 kbar")
    _assert_missing(
        _fe_redox_pressure(row, row.identity, tabulation_experiment())
    )


def test_gas_mix_channel_and_ambient_evidence_are_read() -> None:
    row = _unknown_pressure_observation()
    channel = replace(
        tabulation_experiment(),
        fO2_control=FO2Control(channel=State.of(FO2Channel.GAS_MIX)),
    )
    _assert_ambient(
        _fe_redox_pressure(row, row.identity, channel),
        "fO2_control.channel=gas_mix",
    )
    evidenced = replace(
        tabulation_experiment(),
        ambient_pressure_evidence=AmbientPressureEvidence(
            evidence_class=AmbientPressureEvidenceClass.ATMOSPHERIC_PRESSURE_STATEMENT,
            quote="the runs were made at one atmosphere",
            locator=loc(),
        ),
    )
    _assert_ambient(
        _fe_redox_pressure(row, row.identity, evidenced),
        "class=atmospheric_pressure_statement: the runs were made at one atmosphere",
    )


def test_one_atm_alternative_is_not_method_evidence() -> None:
    row = _unknown_pressure_observation(
        reason=(
            "fO2 reference state not printed; 1 bar convention assumed "
            "(1 atm alternative: +0.0057 dex)"
        ),
    )
    _assert_missing(_fe_redox_pressure(row, row.identity, tabulation_experiment()))


def test_migrate_ambient_notice_is_not_the_assumed_stratum() -> None:
    notice = Notice(
        kind=NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE,
        affected_quantities=(Quantity.FE3_FE2_RATIO,),
        reason=(
            "total_pressure_Pa=101325 Pa derived from explicit "
            "ambient_pressure_evidence class=gas_mixing"
        ),
        origin="simulator.battery.migrate",
    )
    assert "assumed_ambient_pressure" not in flagged_strata((notice,))


def test_openimcc_kress_air_row_carries_ambient_pressure_and_virgo_does_not() -> None:
    pytest.importorskip("openimcc")
    kress, kress_experiments = _load(
        "kress-carmichael-1989-lime-iron-silicate-redox.yaml",
        "10.1016_0016-7037(89)90165-8.yaml",
        {_KRESS_AIR},
    )
    air = kress[_KRESS_AIR]
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        air,
        experiment=kress_experiments[air.experiment_id],
    )
    assert prediction.refusal_reason is None, prediction.refusal_detail
    assert prediction.value is not None
    assert prediction.execution.call_evidence == "openimcc.evaluate_redox"
    assert prediction.identity is not None
    assert prediction.identity.total_pressure_Pa.is_value
    assert prediction.identity.total_pressure_Pa.value == ATM_PA_DECIMAL
    assert any(
        "assumed_ambient_pressure" in notice.reason
        and "1 bar total-pressure" in notice.reason
        for notice in prediction.notices
    )

    virgo, virgo_experiments = _load(
        "mysen-virgo-1989-nas-redox.yaml",
        "e7e10727e8e64c07d89367ee737b182fe0e3823791a16b3bb6db593d14826e05.yaml",
        {_MYSEN_VIRGO},
    )
    row = virgo[_MYSEN_VIRGO]
    withheld = predict_with_engine(
        Engine.OPENIMCC,
        row,
        experiment=virgo_experiments[row.experiment_id],
    )
    assert not any(
        "assumed_ambient_pressure" in notice.reason for notice in withheld.notices
    )
    if withheld.execution.call_evidence == "openimcc.evaluate_redox":
        assert withheld.identity is not None
        pressure = withheld.identity.total_pressure_Pa
        assert pressure is None or not pressure.is_value
