"""Printed oxygen reaches identity.fO2_Pa for every accepting quantity.

Pretorius and Muan is not on this base. Holzheid 1997 Table 3a and
O'Neill and Eggins 2002 Table 7 are the rows that print a log fO2 on
activity-coefficient observations. The log stays on point conditions.
identity.fO2_Pa is the same conversion used for redox rows.

A named IW, QFM, or NNO offset converts through fo2_buffers. A printed
absolute log still wins. A row with no printed oxygen stays unknown.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from simulator.battery.enums import Authority, NoticeKind, Quantity, ValueKind
from simulator.battery.identity import IdentityEqualKind, identity_equal
from simulator.battery.migrate import (
    Migrator,
    _BARE_LOG_BAR_NOTICE,
    _BARE_LOG_SCALE_UNKNOWN,
    _BOUNDED_FO2_REASON,
    _band_text,
    _log_fO2_to_pa,
)
from simulator.battery.records import Located, Value
from simulator.chemistry.fo2_buffers import evaluate_buffer
from tests.battery.test_migrate import _scalar_extract, _write_min_tree

ROOT = Path(__file__).resolve().parents[2]
_UNMAPPED = "no fO2_Pa mapped from source"

# 10^-9.56 bar × 1e5 Pa/bar, computed as Decimal arithmetic.
# The brief rounds this to 2.754e-5 Pa.
_LOG_MINUS_9_56_PA = Decimal("0.00002754228703338166448631210659")


def _migrated(name: str):
    path = ROOT / "data" / "literature" / "extracts" / name
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _one(result, *fragments: str):
    matches = [
        observation
        for observation_id, observation in result.observations.items()
        if all(fragment in observation_id for fragment in fragments)
    ]
    assert len(matches) == 1, [obs.observation_id for obs in matches]
    return matches[0]


def _decimal_point(state) -> Decimal | None:
    if state is None or not state.is_value or state.value is None:
        return None
    raw = state.value
    if isinstance(raw, Value):
        if raw.kind is not ValueKind.POINT or raw.point is None:
            return None
        return raw.point
    if isinstance(raw, Decimal):
        return raw
    return None


def test_log10_bar_to_pa_sanity_is_2_754e_5() -> None:
    # Unit check: dividing the result by the bar reference recovers the log,
    # so the product is a pressure in Pa and not a bare exponent.
    converted = _log_fO2_to_pa(Decimal("-9.56"))
    assert converted == _LOG_MINUS_9_56_PA
    assert (converted / Decimal("1e5")).log10() == Decimal("-9.56")
    assert abs(converted - Decimal("2.754e-5")) < Decimal("5e-9")


def test_holzheid_v69_printed_log_reaches_gamma_identity() -> None:
    result = _migrated("holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml")
    observation = _one(
        result,
        "holzheid_1997_table3a_ad_co_variable_mgo_1_1",
        "row=v-69",
    )
    assert observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
    log = observation.point_conditions["fO2_log"]
    assert _decimal_point(log.state) == Decimal("-9.63")
    assert log.inference is None
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-9.63"))


def test_oneill_nio_parent_log_reaches_gamma_identity() -> None:
    # The log is on the parent values, not the series row. Fallback facts
    # carry the bar frame, and the child identity uses that conversion.
    result = _migrated("oneill-2002-feo-activity-coefficients-cmas.yaml")
    observation = _one(result, "oneill_2002_table7_nio", "row=ad-eutectic")
    assert observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
    log = observation.point_conditions["fO2_log"]
    assert _decimal_point(log.state) == Decimal("-12.61")
    assert log.inference is None
    assert "fO2_Pa" not in (observation.point_conditions or {})
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-12.61"))


def test_oneill_feo_gamma_identity_keeps_printed_pressure() -> None:
    # Table 7 FeO already carries both the log and the converted pressure.
    # Identity uses that pressure. t-1360 must not change the number.
    result = _migrated("oneill-2002-feo-activity-coefficients-cmas.yaml")
    observation = _one(result, "oneill_2002_table7_feo", "row=ad-eutectic")
    assert observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
    assert _decimal_point(observation.point_conditions["fO2_log"].state) == Decimal(
        "-12.61"
    )
    printed = _decimal_point(observation.point_conditions["fO2_Pa"].state)
    assert printed == Decimal("2.454708915685030356082721741E-8")
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == printed
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-12.61"))


def _migrate_fixture(
    tmp_path: Path, *, quantity: str, point: dict, parent: dict | None = None
):
    values = {
        "quantity": quantity,
        "method_class": "measured_direct",
        "admission_status": "admitted",
        "series": [point],
        **(parent or {}),
    }
    extract = _scalar_extract(
        quantity=quantity,
        units="dimensionless",
        values=values,
        obs_type="activity_coefficient",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["standard_state"] = "pure liquid oxide"
    row["phase"] = "l"
    extract["species"] = {"FeO": {"observations": [row]}}
    migrator = Migrator(root=_write_min_tree(tmp_path, extract))
    migrator._migrate_extract(tmp_path / "data" / "literature" / "extracts" / "fixture-source.yaml")
    migrator.finalize()
    return migrator.result


def _logged(result, quantity: Quantity, log: str):
    expected = Decimal(log)
    matches = []
    for observation in result.observations.values():
        identity_quantity = observation.identity.quantity
        if not identity_quantity.is_value or identity_quantity.value is not quantity:
            continue
        located = (observation.point_conditions or {}).get("fO2_log")
        if not isinstance(located, Located):
            continue
        if _decimal_point(located.state) == expected:
            matches.append(observation)
    assert len(matches) == 1, [item.observation_id for item in matches]
    return matches[0]


def test_activity_row_fO2_log_reaches_identity(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "log_fO2": {"value": "-9.56", "units": "log10(bar)"},
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _logged(result, Quantity.ACTIVITY, "-9.56")
    log = observation.point_conditions["fO2_log"]
    assert log.inference is None
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-9.56"))
    assert observation.identity.fO2_Pa.value == _LOG_MINUS_9_56_PA
    assert all(
        notice.reason != _BARE_LOG_BAR_NOTICE for notice in observation.notices
    )


def test_gamma_row_fO2_log_reaches_identity(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity_coefficient",
        point={
            "T_K": 1673,
            "gamma": "1.72",
            "log_fO2": {"value": "-9.56", "units": "log10(bar)"},
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _logged(result, Quantity.ACTIVITY_COEFFICIENT, "-9.56")
    assert observation.point_conditions["fO2_log"].inference is None
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-9.56"))
    assert all(
        notice.reason != _BARE_LOG_BAR_NOTICE for notice in observation.notices
    )


def _series_logged(result, quantity: Quantity, log: str):
    expected = Decimal(log)
    matches = []
    for observation in result.observations.values():
        if (
            "::T=" not in observation.observation_id
            and "::point:" not in observation.observation_id
        ):
            continue
        identity_quantity = observation.identity.quantity
        if not identity_quantity.is_value or identity_quantity.value is not quantity:
            continue
        located = (observation.point_conditions or {}).get("fO2_log")
        if not isinstance(located, Located):
            continue
        if _decimal_point(located.state) == expected:
            matches.append(observation)
    assert len(matches) == 1, [item.observation_id for item in matches]
    return matches[0]


def _same_row_identity(observation):
    return identity_equal(observation.identity, observation.identity)


def test_unitless_holzheid_ad10_log_is_one_bar_with_notice(tmp_path: Path) -> None:
    # Holzheid Table 2 AD 10 prints log fO2 = -10.31 at 1677 K with no unit.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity_coefficient",
        point={
            "T_K": 1677,
            "gamma": "1.62",
            "log_fO2": "-10.31",
            "locator": {"published_page": 23, "table": "2", "row": "AD 10"},
        },
    )
    observation = _series_logged(result, Quantity.ACTIVITY_COEFFICIENT, "-10.31")
    assert observation.identity.temperature_K is not None
    assert observation.identity.temperature_K.value == Decimal("1677")
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == _log_fO2_to_pa(Decimal("-10.31"))
    assert any(notice.reason == _BARE_LOG_BAR_NOTICE for notice in observation.notices)
    assert "fO2_Pa" not in _same_row_identity(observation).fields


def test_unitless_log_iw_band_edges_follow_the_row_temperature(tmp_path: Path) -> None:
    """Closed window at this row's T: IW±6 is 1 bar; IW±6.0001 stays unknown.

    The centre is ``feo_iw_log10_fO2_bar`` at the row temperature, then
    ``Decimal(str(centre))``, the same conversion the migrator applies.
    """
    from simulator.fe_redox import feo_iw_log10_fO2_bar

    temperature = Decimal("1677")
    centre = Decimal(str(feo_iw_log10_fO2_bar(float(temperature), a_feo=1.0)))
    cases = (
        (centre - Decimal(6), True),
        (centre + Decimal(6), True),
        (centre - Decimal("6.0001"), False),
        (centre + Decimal("6.0001"), False),
    )
    for edge, accepted in cases:
        log = format(edge, "f")
        result = _migrate_fixture(
            tmp_path / log.replace("-", "m").replace(".", "p"),
            quantity="activity_coefficient",
            point={
                "T_K": int(temperature),
                "gamma": "1.62",
                "log_fO2": log,
                "locator": {"published_page": 23, "table": "2", "row": "IW edge"},
            },
        )
        observation = _series_logged(result, Quantity.ACTIVITY_COEFFICIENT, log)
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.value == temperature
        located_log = observation.point_conditions["fO2_log"]
        assert located_log.state.is_value
        assert _decimal_point(located_log.state) == Decimal(log)
        oxygen = observation.identity.fO2_Pa
        assert oxygen is not None
        if accepted:
            assert oxygen.is_value
            assert oxygen.value == _log_fO2_to_pa(Decimal(log))
            assert any(
                notice.reason == _BARE_LOG_BAR_NOTICE for notice in observation.notices
            )
        else:
            assert oxygen.is_unknown
            assert oxygen.reason == _BARE_LOG_SCALE_UNKNOWN
            outcome = _same_row_identity(observation)
            assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
            assert "fO2_Pa" in outcome.fields


def test_unitless_log_outside_iw_band_stays_unknown(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity_coefficient",
        point={
            "T_K": 1573,
            "gamma": "1.5",
            "log_fO2": "-1.0",
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _series_logged(result, Quantity.ACTIVITY_COEFFICIENT, "-1.0")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason == _BARE_LOG_SCALE_UNKNOWN
    outcome = _same_row_identity(observation)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "fO2_Pa" in outcome.fields


def test_unitless_log_without_temperature_stays_unknown(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity_coefficient",
        point={
            "gamma": "1.5",
            "log_fO2": "-10.31",
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _series_logged(result, Quantity.ACTIVITY_COEFFICIENT, "-10.31")
    assert (
        observation.identity.temperature_K is None
        or not observation.identity.temperature_K.is_value
    )
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason == _BARE_LOG_SCALE_UNKNOWN
    outcome = _same_row_identity(observation)
    assert outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert "fO2_Pa" in outcome.fields


def _activity_control(result, control: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and isinstance((observation.point_conditions or {}).get("fO2_control"), Located)
        and (observation.point_conditions or {})["fO2_control"].state.value == control
    ]
    assert len(matches) == 1
    return matches[0]


def _used_pressure_bar(observation) -> Decimal | None:
    pressure = observation.identity.total_pressure_Pa
    if pressure is None or not pressure.is_value or not pressure.value > 0:
        return None
    return pressure.value / Decimal("100000")


def _assert_buffer_conversion(
    observation,
    *,
    buffer: str,
    offset: Decimal,
    temperature_K: Decimal,
    kind: NoticeKind,
    calibration_id: str,
    pressure_bar: Decimal | None,
) -> None:
    oxygen = observation.identity.fO2_Pa
    evaluation = evaluate_buffer(
        buffer,
        temperature_K,
        pressure_bar=pressure_bar,
        calibration_id=calibration_id,
    )
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value == _log_fO2_to_pa(evaluation.log10_fO2_bar + offset)
    notices = [notice for notice in observation.notices if notice.kind is kind]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.band == _band_text(evaluation.band_dex)
    assert notice.authority is (
        Authority.CERTIFIED
        if evaluation.authority == "certified"
        else Authority.EXTRAPOLATED
    )
    assert f"buffer={buffer}" in notice.reason
    assert f"calibration={calibration_id}" in notice.reason
    assert f"T_K={temperature_K}" in notice.reason
    assert f"authority={evaluation.authority}" in notice.reason
    assert f"band_dex={notice.band}" in notice.reason
    if pressure_bar is None:
        assert "P_bar=" not in notice.reason
    else:
        assert "P_bar=" in notice.reason
    extrapolated = [
        item
        for item in observation.notices
        if item.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and item.reason == notice.reason
    ]
    if evaluation.authority == "extrapolated":
        assert len(extrapolated) == 1
    else:
        assert extrapolated == []


def test_activity_buffer_offset_uses_canonical_qfm(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "FMQ+2.7"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "FMQ+2.7")
    _assert_buffer_conversion(
        observation,
        buffer="QFM",
        offset=Decimal("2.7"),
        temperature_K=Decimal(1673),
        kind=NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        calibration_id="oneill1987_mu",
        pressure_bar=_used_pressure_bar(observation),
    )
    assert "offset=+2.7" in observation.notices[0].reason or any(
        "offset=+2.7" in notice.reason for notice in observation.notices
    )
    # Survey table, O'Neill 1987 QFM at 1673 K, 1 bar: -6.3051. The
    # printed control adds +2.7. This is the table, not evaluate_buffer.
    assert _used_pressure_bar(observation) in (None, Decimal(1))
    recovered = (observation.identity.fO2_Pa.value / Decimal("100000")).log10()
    assert recovered.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) == (
        Decimal("-6.3051") + Decimal("2.7")
    )
    assert observation.point_conditions["fO2_control"].state.value == "FMQ+2.7"


def _activity_iw(tmp_path: Path, control: str):
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1473,
            "activity": "0.2",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": control},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    return _activity_control(result, control)


def _bar_log(observation) -> Decimal:
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    return (oxygen.value / Decimal("100000")).log10()


def test_unicode_minus_offset_matches_ascii_and_the_survey_table(tmp_path: Path) -> None:
    # Survey Wolf IW at 1473 K is -11.9375. Offset -1 is the printed sign,
    # including U+2212, U+2013, and U+2014. U+002D is the ASCII control.
    expected = Decimal("-11.9375") + Decimal("-1")
    controls = ("IW-1", "IW − 1", "IW – 1", "IW — 1", "IW = -2")
    expected_by_control = {
        "IW-1": expected,
        "IW − 1": expected,
        "IW – 1": expected,
        "IW — 1": expected,
        "IW = -2": Decimal("-11.9375") + Decimal("-2"),
    }
    logs = {}
    for index, control in enumerate(controls):
        observation = _activity_iw(tmp_path / str(index), control)
        recovered = _bar_log(observation)
        assert recovered.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) == (
            expected_by_control[control]
        )
        token = "offset=-2" if control == "IW = -2" else "offset=-1"
        assert any(token in (notice.reason or "") for notice in observation.notices)
        logs[control] = observation.identity.fO2_Pa.value
    assert logs["IW − 1"] == logs["IW-1"]
    assert logs["IW – 1"] == logs["IW-1"]
    assert logs["IW — 1"] == logs["IW-1"]


def test_dangling_printed_sign_is_not_offset_zero(tmp_path: Path) -> None:
    for control in ("IW +", "IW =", "IW −"):
        observation = _activity_iw(tmp_path / str(ord(control[-1])), control)
        oxygen = observation.identity.fO2_Pa
        assert oxygen is not None and oxygen.is_unknown
        assert "not a point" in (oxygen.reason or "")
        assert not any(
            notice.kind
            in (
                NoticeKind.BUFFER_RELATIVE_FO2_CONVERTED,
                NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
            )
            for notice in observation.notices
        )


def test_child_buffer_beats_parent_log(tmp_path: Path) -> None:
    # Parent log -9 bar is 1e-4 Pa. The child's IW+2 is its own conversion.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1673,
            "activity": "0.2",
            "oxygen_buffer_as_published": "IW",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "IW+2"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "IW+2")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value != _log_fO2_to_pa(Decimal("-9"))
    _assert_buffer_conversion(
        observation,
        buffer="IW",
        offset=Decimal(2),
        temperature_K=Decimal(1673),
        kind=NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        calibration_id="wolf2023_quote_oneill_pownceby1993",
        pressure_bar=_used_pressure_bar(observation),
    )


def test_printed_calibration_is_used_when_the_owner_has_it(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1200,
            "activity": "0.2",
            "buffer_calibration": "O'Neill 1987",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "FMQ+1"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "FMQ+1")
    _assert_buffer_conversion(
        observation,
        buffer="QFM",
        offset=Decimal(1),
        temperature_K=Decimal(1200),
        kind=NoticeKind.BUFFER_RELATIVE_FO2_CONVERTED,
        calibration_id="oneill1987_mu",
        pressure_bar=_used_pressure_bar(observation),
    )
    assert not any(
        notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        for notice in observation.notices
    )


def test_printed_absolute_log_beats_buffer_offset(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "log_fO2": {"value": "-9.56", "units": "log10(bar)"},
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "FMQ+2.7"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "FMQ+2.7")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value == _log_fO2_to_pa(Decimal("-9.56"))
    assert not any(
        notice.kind
        in {
            NoticeKind.BUFFER_RELATIVE_FO2_CONVERTED,
            NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        }
        for notice in observation.notices
    )


def test_state_wrapped_log_beats_buffer_offset(tmp_path: Path) -> None:
    # Neuman stores the printed log as `{state: {value}}` with no top-level
    # value, beside IW+3. That number is the absolute log. Empty units are
    # the 1 bar entry, not a bare-log refusal and not a buffer conversion.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 2273,
            "activity": "0.2",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "IW+3"},
                    "locator": {"published_page": 1},
                },
                "log_fO2": {
                    "state": {"tag": "value", "value": "-2.33"},
                    "locator": {"published_page": 1},
                },
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "IW+3")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value == _log_fO2_to_pa(Decimal("-2.33"))
    assert not any(
        notice.kind
        in {
            NoticeKind.BUFFER_RELATIVE_FO2_CONVERTED,
            NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        }
        for notice in observation.notices
    )


def test_blank_log_does_not_veto_buffer_offset(tmp_path: Path) -> None:
    # A blank cell stored as state tag unknown is absence, not a bound.
    # IW-1 still converts. A real bound keeps the typed refusal.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 2073,
            "activity": "0.2",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "IW-1"},
                    "locator": {"published_page": 1},
                },
                "fO2_log": {
                    "state": {
                        "tag": "unknown",
                        "reason": "blank cell; no log fO2 value printed for this row",
                    },
                    "locator": {"published_page": 1},
                },
                "log_fO2": {
                    "state": {
                        "tag": "unknown",
                        "reason": "blank cell; no log fO2 value printed for this row",
                    },
                    "locator": {"published_page": 1},
                },
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "IW-1")
    _assert_buffer_conversion(
        observation,
        buffer="IW",
        offset=Decimal("-1"),
        temperature_K=Decimal(2073),
        kind=NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        calibration_id="wolf2023_quote_oneill_pownceby1993",
        pressure_bar=_used_pressure_bar(observation),
    )


def test_ideal_feo_fe_ratio_diw_carries_its_notice(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "buffer_calibration": "ideal xFeO and metal Fe",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "IW-1"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "IW-1")
    _assert_buffer_conversion(
        observation,
        buffer="IW",
        offset=Decimal("-1"),
        temperature_K=Decimal(1673),
        kind=NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
        calibration_id="wolf2023_quote_oneill_pownceby1993",
        pressure_bar=_used_pressure_bar(observation),
    )
    ideal = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.DELTA_IW_FROM_IDEAL_FEO_FE_RATIO
    ]
    assert len(ideal) == 1
    assert ideal[0].authority is Authority.EXTRAPOLATED


def test_unknown_buffer_stays_a_typed_refusal(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "oxygen_buffer_as_published": "Mo-MoO2",
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(matches) == 1
    oxygen = matches[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason.startswith("buffer-relative fO2 (")
    assert "Mo-MoO2" in oxygen.reason
    assert "no calibration in the buffer owner" in oxygen.reason


def test_approximate_buffer_phrase_is_not_a_point(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "point_conditions": {
                "fO2_control": {
                    "state": {"tag": "value", "value": "near FMQ"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    observation = _activity_control(result, "near FMQ")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason.startswith("buffer-relative fO2 (")
    assert "not a point offset" in oxygen.reason


def test_lu_2026_diw_converts_into_absolute_fo2_and_discloses_calibration() -> None:
    result = _migrated("lu-2026-mercury-sulfur-liquidus.yaml")
    observation = _one(result, "lu26_mer10_3gpa_a22_1675c_02_s_in_glass")
    oxygen = observation.identity.fO2_Pa
    # t-1619 / d-324: the printed ΔIW now populates the absolute identity.
    assert oxygen is not None and oxygen.is_value
    assert "fO2_Pa" not in (observation.point_conditions or {})
    evaluation = evaluate_buffer(
        "IW",
        Decimal("1948.15"),
        pressure_bar=Decimal(30000),
    )
    assert oxygen.value == _log_fO2_to_pa(
        evaluation.log10_fO2_bar + Decimal("-6.5")
    )
    notices = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    ]
    assert len(notices) == 1
    reason = notices[0].reason
    assert "buffer=IW" in reason
    assert "offset=−6.5" in reason or "offset=-6.5" in reason
    assert "calibration=wolf2023_quote_oneill_pownceby1993" in reason
    assert "T_K=1948.15" in reason
    assert "P_bar=30000" in reason
    assert "authority=extrapolated" in reason
    assert f"band_dex={_band_text(evaluation.band_dex)}" in reason
    assert "Cartier" not in reason
    assert notices[0].authority is Authority.EXTRAPOLATED
    assert notices[0].band == _band_text(evaluation.band_dex)
    logged = reason.split("log10_fO2_bar=", 1)[1].split(" ", 1)[0]
    assert Decimal(logged) == evaluation.log10_fO2_bar + Decimal("-6.5")
    assert not any(
        notice.kind is NoticeKind.DELTA_IW_FROM_IDEAL_FEO_FE_RATIO
        for notice in observation.notices
    )


def test_child_typed_pressure_beats_parent_log(tmp_path: Path) -> None:
    # Parent log -9 bar is 1e-4 Pa. The child's typed pressure stays 0.001 Pa.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1673,
            "activity": "0.2",
            "point_conditions": {
                "fO2_Pa": {
                    "state": {"tag": "value", "value": "0.001"},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and _decimal_point(
            (observation.point_conditions or {}).get("fO2_Pa").state
            if isinstance((observation.point_conditions or {}).get("fO2_Pa"), Located)
            else None
        )
        == Decimal("0.001")
    ]
    assert len(matches) == 1
    oxygen = matches[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value == Decimal("0.001")


def _series_activity_rows(result):
    return [
        observation
        for observation in result.observations.values()
        if ":row=" in observation.observation_id
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]


def test_child_bound_pressure_beats_parent_log(tmp_path: Path) -> None:
    # Parent log -9 bar is 1e-4 Pa. A typed bound on the child is present
    # oxygen, not an absent axis, so the parent point is not assigned.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1673,
            "activity": "0.2",
            "point_conditions": {
                "fO2_Pa": {
                    "state": {
                        "tag": "value",
                        "value": {
                            "kind": "bound",
                            "bound_operator": "<",
                            "bound_value": "0.001",
                        },
                    },
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    rows = _series_activity_rows(result)
    assert len(rows) == 1
    oxygen = rows[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason == _BOUNDED_FO2_REASON


def test_child_typed_unknown_pressure_beats_parent_log(tmp_path: Path) -> None:
    reason = "source prints only an upper bound"
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1673,
            "activity": "0.2",
            "point_conditions": {
                "fO2_Pa": {
                    "state": {"tag": "unknown", "reason": reason},
                    "locator": {"published_page": 1},
                }
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    rows = _series_activity_rows(result)
    assert len(rows) == 1
    located = rows[0].point_conditions["fO2_Pa"]
    assert located.state.is_unknown
    assert located.state.reason == reason
    oxygen = rows[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.value != _log_fO2_to_pa(Decimal("-9"))


def test_child_printed_pressure_bound_beats_parent_log(tmp_path: Path) -> None:
    # <10^-10 atm is a printed upper bound (1.01325e-5 Pa), not a point.
    # The parent log must not replace it with 1e-4 Pa.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1673,
            "activity": "0.2",
            "oxygen_partial_pressure": {
                "value": "<10^-10",
                "units": "atm",
                "semantics": "upper_bound_not_point",
                "locator": {"published_page": 1},
            },
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    rows = _series_activity_rows(result)
    assert len(rows) == 1
    oxygen = rows[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason == _BOUNDED_FO2_REASON
    assert oxygen.value != _log_fO2_to_pa(Decimal("-9"))


def test_plante_derived_ratio_reaches_p_partial_identity() -> None:
    # Table 2 series 1104 row 1 derives P_O2 = 0.226 P_K. The row-scope
    # strip must not drop that pressure: it is this row's oxygen, and
    # identity copies the point. The green pascal value is the pin.
    result = _migrated("kems-042-plante-1979.yaml")
    observation = _one(result, "plante1979_table2_s1104_r001_quoted")
    assert observation.identity.quantity.value is Quantity.P_PARTIAL
    located = observation.point_conditions["fO2_Pa"]
    assert located.inference is not None
    printed = _decimal_point(located.state)
    assert printed == Decimal("0.015823519950")
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_value
    assert observation.identity.fO2_Pa.value == printed


def test_activity_without_printed_oxygen_stays_unknown(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1673,
            "activity": "0.2",
            "locator": {"published_page": 1, "table": "1", "row": "1"},
        },
    )
    rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert rows
    assert all(
        observation.identity.fO2_Pa is not None
        and observation.identity.fO2_Pa.is_unknown
        and observation.identity.fO2_Pa.reason == _UNMAPPED
        for observation in rows
    )
    assert all("fO2_log" not in (observation.point_conditions or {}) for observation in rows)
    assert all("fO2_Pa" not in (observation.point_conditions or {}) for observation in rows)
