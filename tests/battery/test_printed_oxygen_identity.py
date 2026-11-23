"""Printed oxygen reaches identity.fO2_Pa for every accepting quantity.

Pretorius and Muan is not on this base. Holzheid 1997 Table 3a and
O'Neill and Eggins 2002 Table 7 are the rows that print a log fO2 on
activity-coefficient observations. The log stays on point conditions.
identity.fO2_Pa is the same conversion used for redox rows.

A named IW, QFM, or NNO offset converts through fo2_buffers. A printed
absolute log still wins. A row with no printed oxygen stays unknown.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
import re

import pytest

from simulator.battery.enums import (
    Authority,
    Engine,
    NoticeKind,
    Quantity,
    ResidualStatus,
    ValueKind,
)
from simulator.battery.identity import IdentityEqualKind, identity_equal
from simulator.battery.migrate import (
    Migrator,
    _BARE_LOG_BAR_NOTICE,
    _BARE_LOG_SCALE_UNKNOWN,
    _BOUNDED_FO2_REASON,
    _band_text,
    _log_fO2_to_pa,
    load_migrated_store,
)
from simulator.battery.records import Located, Value, nominal_pressure_pa
from simulator.battery.oxygen_identity import (
    _printed_buffer_offset,
    _resolve_score_time_buffer_identity,
)
from simulator.battery.score import (
    _headline_payload_admits,
    compile_residual,
    flagged_strata,
    load_score_context,
    rail_for_quantity,
)
from simulator.chemistry.fo2_buffers import evaluate_buffer
from simulator.state_types import State
from tests.battery.test_migrate import _scalar_extract, _write_min_tree

ROOT = Path(__file__).resolve().parents[2]
_UNMAPPED = "no fO2_Pa mapped from source"
_BUFFER_IDENTITY_PIN_COUNTS = {
    "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas": 28,
    "jugo-2010-sulfur-xanes": 7,
    "gaillard-2003-hydrous-silicic-melt-activity": 96,
    "borisov-veksler-2021-k-fe-supplement": 6672,
    "zagrtdenov-2020-chromite-magnesiochromite-solubility": 132,
    "neuman-2026-levitation-k-isotopes": 6,
    "lu-2026-mercury-sulfur-liquidus": 61,
    "jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation": 117,
}
_BUFFER_MARKER_ONLY_COUNTS = {
    "hanson-jones-1998-am-cr-partitioning-olivine-liquid": 276,
    "neuman-2026-levitation-k-isotopes": 1457,
    "zhang-2024-alkali-evaporation-ti-thesis": 111,
    "leuthold-2023-basalt-partition-fo2": 2,
}

# 10^-9.56 bar × 1e5 Pa/bar, computed as Decimal arithmetic.
# The brief rounds this to 2.754e-5 Pa.
_LOG_MINUS_9_56_PA = Decimal("0.00002754228703338166448631210659")


def _migrated(name: str):
    path = ROOT / "data" / "literature" / "extracts" / name
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _assert_oxygen_conditions_have_locators(result) -> None:
    issues = [
        issue for issue in result.validation.hard_issues
        if issue.path.endswith(".fO2_log")
    ]
    assert not issues, issues


def test_real_deffrennes_air_intervals_keep_observation_locators(monkeypatch) -> None:
    from simulator.battery import migrate as migration

    source_id = "deffrennes-2020-ca-o-calphad"
    result = _migrated(f"{source_id}.yaml")
    _assert_oxygen_conditions_have_locators(result)
    rows = (
        ("deffrennes_2020_manara_air_solidification", "3222", 6),
        ("deffrennes_2020_yamada_solidification", "3178", 23),
    )
    for local_id, temperature, page in rows:
        observation = result.observations[f"{source_id}::{local_id}"]
        condition = observation.point_conditions["fO2_log"]
        assert observation.value.point == Decimal(temperature)
        assert condition.locator == observation.locator
        assert condition.locator.page == page
        interval = condition.state.value
        assert interval.kind is ValueKind.INTERVAL
        assert interval.interval_low == interval.interval_high

    retain = migration._retain_printed_fo2_interval

    def without_observation_locator(conditions, decision, *, locator):
        return retain(conditions, decision, locator=None)

    monkeypatch.setattr(
        migration, "_retain_printed_fo2_interval", without_observation_locator
    )
    reverted = _migrated(f"{source_id}.yaml")
    with pytest.raises(AssertionError, match="empirical Located value requires a locator"):
        _assert_oxygen_conditions_have_locators(reverted)
    assert result.observations.keys() == reverted.observations.keys()
    for observation_id, observation in result.observations.items():
        old = reverted.observations[observation_id]
        assert observation.value == old.value
        assert observation.identity == old.identity
    issues = reverted.validation.hard_issues
    assert {
        issue.path for issue in issues if issue.path.endswith(".fO2_log")
    } == {
        f"observation[{source_id}::{local_id}].point_conditions.fO2_log"
        for local_id, _, _ in rows
    }


def test_real_unknown_air_condition_keeps_its_existing_absence() -> None:
    result = _migrated("schreiber-1977-tetravalent-cr-diopside.yaml")
    observation = _one(
        result, "schreiber_1977_crvi_fraction_blue_diopside_melt"
    )
    condition = observation.point_conditions["fO2_log"]
    assert condition.state.is_unknown
    assert condition.locator is None


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
        calibration_id="oneill_1987_ammin_72_reaction2_emf",
        pressure_bar=_used_pressure_bar(observation),
    )
    assert "offset=+2.7" in observation.notices[0].reason or any(
        "offset=+2.7" in notice.reason for notice in observation.notices
    )
    # Unprinted FMQ uses the gate-8 O'Neill 1987 curve (CODATA R), not the
    # author-R survey row. _assert_buffer_conversion checks that value.
    assert _used_pressure_bar(observation) in (None, Decimal(1))
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
    # Unprinted IW is the gate-8 O'Neill 1988 curve at the row temperature.
    # Offset -1 is the printed sign, including U+2212, U+2013, and U+2014.
    # U+002D is the ASCII control.
    base = evaluate_buffer("IW", Decimal(1473)).log10_fO2_bar
    expected = base + Decimal("-1")
    controls = ("IW-1", "IW − 1", "IW – 1", "IW — 1", "IW = -2")
    expected_by_control = {
        "IW-1": expected,
        "IW − 1": expected,
        "IW – 1": expected,
        "IW — 1": expected,
        "IW = -2": base + Decimal("-2"),
    }
    logs = {}
    for index, control in enumerate(controls):
        observation = _activity_iw(tmp_path / str(index), control)
        recovered = _bar_log(observation)
        assert recovered.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) == (
            expected_by_control[control].quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
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
        calibration_id="oneill_1988_ammin_73_eq11",
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
        calibration_id="oneill_1987_ammin_72_reaction2_emf",
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
        calibration_id="oneill_1988_ammin_73_eq11",
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
        calibration_id="oneill_1988_ammin_73_eq11",
        pressure_bar=_used_pressure_bar(observation),
    )
    ideal = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.DELTA_IW_FROM_IDEAL_FEO_FE_RATIO
    ]
    assert len(ideal) == 1
    assert ideal[0].authority is Authority.EXTRAPOLATED


def _activity_buffer(tmp_path: Path, label: str, temperature_K: int):
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": temperature_K,
            "activity": "0.2",
            "oxygen_buffer_as_published": label,
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
    return matches[0]


def test_unknown_buffer_stays_a_typed_refusal(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "CCO", 1673)
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason.startswith("buffer-relative fO2 (")
    assert "CCO" in oxygen.reason
    assert "no calibration in the buffer owner" in oxygen.reason
    assert observation.notices == ()


def test_co_co2_label_without_a_ratio_stays_a_typed_refusal(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "CO-CO2.", 1673)
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason == (
        "CO-CO2. buffer-relative fO2 requires the printed CO/CO2 ratio; "
        "no fO2_Pa assigned"
    )
    assert not any(
        notice.kind
        in {
            NoticeKind.BUFFER_RELATIVE_FO2_CONVERTED,
            NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED,
            NoticeKind.BUFFER_BAND_UNSIZED_BEYOND_ALL_CALIBRATIONS,
        }
        for notice in observation.notices
    )


def _assert_extended_conversion(
    observation,
    *,
    buffer: str,
    temperature_K: Decimal,
    region: int,
) -> None:
    evaluation = evaluate_buffer(buffer, temperature_K)
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    assert oxygen.value == _log_fO2_to_pa(evaluation.log10_fO2_bar)
    notices = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    ]
    assert len(notices) == 1
    reason = notices[0].reason
    assert f"buffer={buffer}" in reason
    assert f"calibration={evaluation.calibration_id}" in reason
    assert f"T_K={temperature_K}" in reason
    assert f"authority={evaluation.authority}" in reason
    assert f"region={region}" in reason
    assert f"band_dex={_band_text(evaluation.band_dex)}" in reason
    for offset in evaluation.edge_offsets:
        signed = format(offset.signed_dex, "f")
        if not signed.startswith("-"):
            signed = "+" + signed
        assert f"edge_offset={offset.route_id}@" in reason
        assert signed in reason
    store_authority = (
        Authority.CERTIFIED
        if evaluation.authority in {"certified", "tabulated_thermochemistry"}
        else Authority.EXTRAPOLATED
    )
    assert notices[0].authority is store_authority
    extrapolated = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
    ]
    unsized = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_BAND_UNSIZED_BEYOND_ALL_CALIBRATIONS
    ]
    if evaluation.authority == "extrapolated":
        assert len(extrapolated) == 1
    else:
        assert extrapolated == []
    if evaluation.unsized_notice:
        assert len(unsized) == 1
        assert notices[0].band == "unsized"
    else:
        assert unsized == []
    if evaluation.divergence_note:
        assert evaluation.divergence_note in reason


def test_mo_moo2_label_converts(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "Mo-MoO2.", 1473)
    _assert_extended_conversion(
        observation, buffer="Mo-MoO2", temperature_K=Decimal(1473), region=2
    )


def test_mh_and_m_h_labels_convert(tmp_path: Path) -> None:
    mh = _activity_buffer(tmp_path, "MH", 1473)
    _assert_extended_conversion(
        mh, buffer="MH", temperature_K=Decimal(1473), region=2
    )


def test_m_h_hyphen_label_converts(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "M-H", 1423)
    _assert_extended_conversion(
        observation, buffer="MH", temperature_K=Decimal(1423), region=2
    )


def test_mw_label_converts_and_carries_the_wustite_warning(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "MW", 1281)
    _assert_extended_conversion(
        observation, buffer="MW", temperature_K=Decimal(1281), region=2
    )
    assert any(
        "Fe0.947O" in notice.reason
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    )


def test_mh_above_1800_k_is_unsized(tmp_path: Path) -> None:
    observation = _activity_buffer(tmp_path, "MH", 1873)
    _assert_extended_conversion(
        observation, buffer="MH", temperature_K=Decimal(1873), region=3
    )


def test_borisov_veksler_2021_rows_convert_per_buffer_family() -> None:
    """Real supplement rows. Counts are component observations, not source rows."""

    result = _migrated("borisov-veksler-2021-k-fe-supplement.yaml")
    families = {
        "mo-moo2": "Mo-MoO2",
        "mh": "MH",
        "m-h": "M-H",
        "mw": "MW",
        "co-co2": "CO-CO2",
    }
    converted: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    seen_temperature = set()
    for observation in result.observations.values():
        control = (observation.point_conditions or {}).get("fO2_control")
        if not isinstance(control, Located) or not control.state.is_value:
            continue
        label = str(control.state.value).strip()
        family = families.get(label.casefold().rstrip("."))
        if family is None:
            continue
        oxygen = observation.identity.fO2_Pa
        conversion = [
            notice
            for notice in observation.notices
            if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        ]
        if family == "CO-CO2":
            assert oxygen is not None and oxygen.is_unknown
            assert oxygen.reason == (
                "CO-CO2. buffer-relative fO2 requires the printed CO/CO2 ratio; "
                "no fO2_Pa assigned"
            )
            assert conversion == []
            refused[family] += 1
            continue
        assert oxygen is not None and oxygen.is_value
        assert len(conversion) == 1
        reason = conversion[0].reason
        temperature = observation.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert f"T_K={temperature.value}" in reason
        buffer = "MH" if family == "M-H" else family
        assert f"buffer={buffer}" in reason
        pressure = observation.identity.total_pressure_Pa
        if pressure is not None and pressure.is_value:
            assert "P_bar=" in reason
        seen_temperature.add(family)
        converted[family] += 1
    assert seen_temperature == {"Mo-MoO2", "MH", "M-H", "MW"}
    assert dict(converted) == {
        "Mo-MoO2": 2904,
        "MH": 728,
        "M-H": 78,
        "MW": 52,
    }
    assert refused["CO-CO2"] == 72


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
    assert "calibration=oneill_1988_ammin_73_eq11" in reason
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


def test_jugo_sb5_uses_canonical_fmq_with_frost_pressure_correction() -> None:
    result = _migrated("jugo-2010-sulfur-xanes.yaml")
    matches = [
        observation
        for observation in result.observations.values()
        if isinstance((observation.point_conditions or {}).get("fO2_control"), Located)
        and observation.point_conditions["fO2_control"].state.is_value
        and observation.point_conditions["fO2_control"].state.value == "FMQ+2.7"
    ]
    assert len(matches) == 1
    observation = matches[0]

    assert _decimal_point(observation.identity.temperature_K) == Decimal("1323.15")
    assert _decimal_point(observation.identity.total_pressure_Pa) == Decimal("200000000")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    # O'Neill (1987) reaction (2) supplies the canonical 1-bar FMQ base at
    # 1323.15 K. Frost's beta-quartz molar-volume term adds
    # 0.110 K/bar * (2000 - 1) bar / 1323.15 K = 0.166186751 dex; the printed
    # FMQ+2.7 then gives this independent nine-decimal log literal.
    expected_log = Decimal("-7.491317884")
    assert abs(oxygen.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    notices = [
        notice.reason
        for notice in observation.notices
        if notice.reason.startswith("buffer_relative_fO2_converted:")
    ]
    assert len(notices) == 1
    reason = notices[0]
    assert "offset_as_printed=FMQ+2.7" in reason
    assert "calibration=oneill_1987_ammin_72_reaction2_emf" in reason
    assert "band=±0.125419 dex" in reason
    assert "band_basis=absolute canonical-to-Frost 1991 spread" in reason
    assert "pressure=2000 bar" in reason
    assert "pressure correction=Frost 1991 molar-volume ΔV term" in reason


def test_mavrogenes_mav1_uses_pressure_delta_v_after_inheritance() -> None:
    result = _migrated("mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas.yaml")
    observation = _one(result, "mavrogenes_oneill_1999_mav1_s_in_quenched_glass")

    assert _decimal_point(observation.identity.temperature_K) == Decimal("1673.15")
    assert _decimal_point(observation.identity.total_pressure_Pa) == Decimal("500000000")
    oxygen = observation.identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    # O'Neill (1988) eq. 11 third segment is the 1-bar IW base at 1673.15 K:
    # μ° = -551159 + 269.404T - 16.9484T ln(T) J/mol, then μ°/(R T ln 10).
    # Frost's published IW pressure term adds 0.055(5000-1)/T dex, followed
    # by the printed -1.56. All terms are dimensionless; independent evaluation
    # gives -11.101141516 log10(fO2/bar) (predict-and-flag above IW's range).
    expected_log = Decimal("-11.101141516")
    assert abs(oxygen.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert any(
        "delta_iw_activity_corrected_gamma_model=Holzheid1997_FeO_gamma_1.7"
        in notice.reason
        and "pressure correction=Frost 1991 molar-volume ΔV term" in notice.reason
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
    assert oxygen.reason == "bounded or qualitative fO2 retained as a typed condition"
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


def test_every_numeric_canonical_buffer_identity_matches_the_owner() -> None:
    _, _, observations = load_migrated_store(
        ROOT, sources=tuple(_BUFFER_IDENTITY_PIN_COUNTS)
    )
    counts: Counter[str] = Counter()
    mismatches = []
    for observation in observations.values():
        marker = next(
            (
                notice
                for notice in observation.notices
                if notice.kind
                is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
            ),
            None,
        )
        identity = observation.identity
        oxygen = getattr(identity, "fO2_Pa", None)
        if (
            observation.source_id not in _BUFFER_IDENTITY_PIN_COUNTS
            or marker is None
            or oxygen is None
            or not oxygen.is_value
            or not isinstance(oxygen.value, Decimal)
        ):
            continue
        counts[observation.source_id] += 1
        match = re.search(
            r"(?:^|\s)buffer=([^\s]+).*?(?:^|\s)offset=([^\s]+)",
            marker.reason,
        )
        if match is None:
            mismatches.append(
                (observation.source_id, observation.observation_id, "missing buffer/offset")
            )
            continue
        buffer, raw_offset = match.groups()
        temperature_state = identity.temperature_K
        pressure_state = identity.total_pressure_Pa
        pressure_pa = (
            nominal_pressure_pa(pressure_state)
            if pressure_state is not None and pressure_state.is_value
            else None
        )
        temperature = (
            temperature_state.value
            if temperature_state is not None and temperature_state.is_value
            else None
        )
        if temperature is None or (
            pressure_pa is None
            and pressure_state is not None
            and pressure_state.is_value
        ):
            mismatches.append(
                (observation.source_id, observation.observation_id, "invalid T/P")
            )
            continue
        pressure_bar = (
            None if pressure_pa is None else pressure_pa / Decimal("100000")
        )
        evaluation = evaluate_buffer(
            buffer,
            temperature,
            pressure_bar=pressure_bar,
        )
        output = _log_fO2_to_pa(
            evaluation.log10_fO2_bar + Decimal(raw_offset.replace("−", "-"))
        )
        if output != oxygen.value:
            mismatches.append(
                (
                    observation.source_id,
                    observation.observation_id,
                    str(temperature),
                    buffer,
                    raw_offset,
                    str(output),
                    str(oxygen.value),
                )
            )

    assert dict(counts) == _BUFFER_IDENTITY_PIN_COUNTS
    assert not mismatches, mismatches


def test_every_marked_numeric_identity_resolves_with_dp112() -> None:
    _, _, observations = load_migrated_store(
        ROOT, sources=tuple(_BUFFER_IDENTITY_PIN_COUNTS)
    )
    counts: Counter[str] = Counter()
    samples = {}
    for observation in observations.values():
        resolved = _resolve_score_time_buffer_identity(observation)
        if resolved is observation:
            continue
        source_id = observation.source_id
        assert source_id in _BUFFER_IDENTITY_PIN_COUNTS
        counts[source_id] += 1
        samples.setdefault(source_id, (observation, resolved))

        stored = observation.identity.fO2_Pa
        actual = resolved.identity.fO2_Pa
        assert stored is not None and stored.is_value
        assert actual is not None and actual.is_value
        assert actual.value == stored.value
        notice = next(
            notice
            for notice in resolved.notices
            if notice.origin == "score:DP-112"
        )
        assert notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        assert notice.policy is not None
        assert notice.policy.id == "DP-112"
        assert notice.policy.version == 1
        assert notice.policy.status == "proposed"
        assert notice.policy.value == format(actual.value, "f")
        assert notice.policy.units == (
            "printed offset and log10(fO2/bar) in dex; P in bar; output in Pa"
        )
        strata = flagged_strata(resolved.notices)
        assert NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED.value in strata
        assert "proposed-policy" in strata
        assert notice.band is not None

        quantity = observation.identity.quantity.value
        species_formula = (
            observation.identity.species.formula
            if observation.identity.species is not None
            else ""
        )
        rail = rail_for_quantity(quantity, species_formula=species_formula)
        assert rail is not None
        payload = {
            "rail": rail.value,
            "notices": [
                {
                    "kind": notice.kind.value,
                    "reason": notice.reason,
                    "policy": {
                        "id": notice.policy.id,
                        "version": notice.policy.version,
                        "status": notice.policy.status,
                        "value": notice.policy.value,
                        "units": notice.policy.units,
                    },
                }
            ],
        }
        assert not _headline_payload_admits(payload, tier="measured")

    assert dict(counts) == _BUFFER_IDENTITY_PIN_COUNTS
    assert set(samples) == set(_BUFFER_IDENTITY_PIN_COUNTS)
    for observation, resolved in samples.values():
        marker = next(
            notice
            for notice in observation.notices
            if notice.kind
            is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
        )
        printed_buffer = _printed_buffer_offset(marker.reason)
        assert printed_buffer is not None
        buffer, _offset = printed_buffer
        identity = observation.identity
        temperature = identity.temperature_K.value
        pressure_pa = (
            nominal_pressure_pa(identity.total_pressure_Pa)
            if identity.total_pressure_Pa is not None
            and identity.total_pressure_Pa.is_value
            else None
        )
        pressure_bar = (
            None if pressure_pa is None else pressure_pa / Decimal("100000")
        )
        evaluation = evaluate_buffer(buffer, temperature, pressure_bar=pressure_bar)
        expected_band = (
            "unsized"
            if evaluation.band_dex is None
            else format(evaluation.band_dex, "f")
        )
        resolved_notice = next(
            notice for notice in resolved.notices if notice.origin == "score:DP-112"
        )
        assert resolved_notice.band == expected_band


def test_dp112_mutation_ignores_stored_numeric_identity() -> None:
    source_id = "mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas"
    _, _, observations = load_migrated_store(ROOT, sources=(source_id,))
    observation = next(
        item
        for item in observations.values()
        if item.source_id == source_id
        and any(
            notice.kind
            is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
            for notice in item.notices
        )
    )
    expected = _resolve_score_time_buffer_identity(observation).identity.fO2_Pa
    assert expected is not None and expected.is_value

    mutated_identity = replace(
        observation.identity,
        fO2_Pa=State.of(Decimal("123.456")),
    )
    mutated_observation = replace(observation, identity=mutated_identity)
    mutated = _resolve_score_time_buffer_identity(mutated_observation)
    assert mutated.identity.fO2_Pa == expected
    assert mutated.identity.fO2_Pa.value != Decimal("123.456")


def test_marker_rows_keep_buffer_labels_without_numeric_identity_fo2() -> None:
    _, _, observations = load_migrated_store(
        ROOT, sources=tuple(_BUFFER_MARKER_ONLY_COUNTS)
    )
    unchanged_counts: Counter[str] = Counter()
    neuman_targets = 0
    for observation in observations.values():
        marker = next(
            (
                notice
                for notice in observation.notices
                if notice.kind
                is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
            ),
            None,
        )
        if marker is None:
            continue
        resolved = _resolve_score_time_buffer_identity(observation)
        if resolved is observation:
            unchanged_counts[observation.source_id] += 1
            assert (
                NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED.value
                not in flagged_strata(observation.notices)
            )
            continue
        assert observation.source_id == "neuman-2026-levitation-k-isotopes"
        assert observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert any(
            notice.policy is not None and notice.policy.id == "DP-112"
            for notice in resolved.notices
        )
        neuman_targets += 1

    assert dict(unchanged_counts) == _BUFFER_MARKER_ONLY_COUNTS
    assert neuman_targets == _BUFFER_IDENTITY_PIN_COUNTS[
        "neuman-2026-levitation-k-isotopes"
    ]


def test_load_score_context_applies_dp112_before_scoring() -> None:
    context = load_score_context(
        ROOT,
        sources=("mavrogenes-oneill-1999-sulfide-solubility-mafic-magmas",),
    )
    assert any(
        notice.policy is not None and notice.policy.id == "DP-112"
        for observation in context.observations.values()
        for notice in observation.notices
    )


def test_neuman_levitation_uses_the_method_pressure_policy() -> None:
    context = load_score_context(
        ROOT,
        sources=("neuman-2026-levitation-k-isotopes",),
    )
    targets = [
        observation
        for observation in context.observations.values()
        if observation.source_id == "neuman-2026-levitation-k-isotopes"
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and any(
            notice.origin == "score:DP-112"
            for notice in observation.notices
        )
    ]
    assert len(targets) == 6
    for observation in targets:
        pressure_notice = next(
            notice
            for notice in observation.notices
            if notice.origin == "score:DP-002"
        )
        assert pressure_notice.kind is NoticeKind.METHOD_IMPLIED
        assert pressure_notice.policy is not None
        assert pressure_notice.policy.id == "DP-002"
        assert pressure_notice.policy.status == "canonical"
        assert pressure_notice.policy.value == "101325"
        dp112 = next(
            notice for notice in observation.notices if notice.origin == "score:DP-112"
        )
        assert "P_bar=1.01325" in dp112.reason
        assert "pressure_route=method_policy:DP-002" in dp112.reason


def test_nonqualifying_dp112_markers_keep_the_row_level_path() -> None:
    source_id = "gaillard-2003-hydrous-silicic-melt-activity"
    _, _, observations = load_migrated_store(ROOT, sources=(source_id,))
    observation = next(
        row
        for row in observations.values()
        if row.source_id == source_id
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and any(
            notice.kind
            is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
            for notice in row.notices
        )
    )
    marker = next(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    )
    no_offset = replace(
        observation,
        notices=tuple(
            replace(notice, reason="DP-112 buffer=IW") if notice is marker else notice
            for notice in observation.notices
        ),
    )
    unusable_temperature = replace(
        observation,
        identity=replace(
            observation.identity,
            temperature_K=State.unknown("temperature is not a numeric point"),
        ),
    )
    assert _resolve_score_time_buffer_identity(no_offset) is no_offset
    assert _resolve_score_time_buffer_identity(unusable_temperature) is unusable_temperature

    pressure_unknown = replace(
        observation,
        identity=replace(
            observation.identity,
            total_pressure_Pa=State.unknown("pressure was not reported"),
        ),
    )
    omitted_pressure = _resolve_score_time_buffer_identity(pressure_unknown)
    pressure_notice = next(
        notice
        for notice in omitted_pressure.notices
        if notice.kind is NoticeKind.PRESSURE_PROVENANCE_UNKNOWN
        and notice.origin == "score:DP-112"
    )
    dp112_notice = next(
        notice
        for notice in omitted_pressure.notices
        if notice.origin == "score:DP-112"
        and notice.kind
        is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    )
    assert "evaluate_buffer pressure term omitted at its one-bar reference" in (
        pressure_notice.reason
    )
    assert "not supplied (term omitted at one-bar reference)" in dp112_notice.reason
    printed_buffer = _printed_buffer_offset(marker.reason)
    assert printed_buffer is not None
    buffer, offset = printed_buffer
    evaluation = evaluate_buffer(
        buffer,
        pressure_unknown.identity.temperature_K.value,
        pressure_bar=None,
    )
    assert omitted_pressure.identity.fO2_Pa == State.of(
        _log_fO2_to_pa(evaluation.log10_fO2_bar + offset)
    )


def test_rejected_dp112_policy_raises_for_a_real_gaillard_row(monkeypatch) -> None:
    from simulator.battery.default_policy import load_policy_table

    source_id = "gaillard-2003-hydrous-silicic-melt-activity"
    _, _, observations = load_migrated_store(ROOT, sources=(source_id,))
    observation = next(
        row
        for row in observations.values()
        if row.source_id == source_id
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and any(
            notice.kind
            is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
            for notice in row.notices
        )
    )
    monkeypatch.setitem(load_policy_table()["DP-112"], "status", "rejected")
    with pytest.raises(ValueError, match="default policy DP-112 is rejected"):
        _resolve_score_time_buffer_identity(observation)


def test_dp112_stored_mutation_flows_through_activity_request_and_residual() -> None:
    from simulator.battery.generators.bench import activity_request_for_engine

    source_id = "gaillard-2003-hydrous-silicic-melt-activity"
    context = load_score_context(ROOT, sources=(source_id,))
    observation = next(
        row
        for row in context.observations.values()
        if row.source_id == source_id
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.ACTIVITY
        and any(notice.origin == "score:DP-112" for notice in row.notices)
    )
    experiment = context.experiments[observation.experiment_id]
    baseline_request = activity_request_for_engine(
        experiment, observation, Engine.INTERNAL_ANALYTICAL.value
    )
    assert baseline_request is not None and baseline_request.payload is not None
    assert baseline_request.readiness.status.value == "ready"

    unresolved = replace(
        observation,
        identity=replace(observation.identity, fO2_Pa=State.of(Decimal("123.456"))),
        notices=tuple(
            notice for notice in observation.notices if notice.origin != "score:DP-112"
        ),
    )
    resolved = _resolve_score_time_buffer_identity(
        unresolved,
        method=experiment.method.value if experiment.method.is_value else None,
    )
    mutated_request = activity_request_for_engine(
        experiment, resolved, Engine.INTERNAL_ANALYTICAL.value
    )
    assert mutated_request is not None and mutated_request.payload is not None
    assert mutated_request.payload == baseline_request.payload
    assert resolved.identity.fO2_Pa != State.of(Decimal("123.456"))

    mutated_observations = dict(context.observations)
    mutated_observations[resolved.observation_id] = resolved
    mutated_context = replace(context, observations=mutated_observations)
    residual, _candidate = compile_residual(
        resolved,
        Engine.INTERNAL_ANALYTICAL,
        context=mutated_context,
    )
    assert residual.status is not ResidualStatus.REFUSED
    assert residual.numeric is not None
    assert "proposed-policy" in flagged_strata(residual.notices)


def test_borisov_veksler_two_liquid_target_gets_the_real_capability_refusal() -> None:
    from simulator.battery.score import _is_bulk_not_liquid_composition

    source_id = "borisov-veksler-2021-k-fe-supplement"
    context = load_score_context(ROOT, sources=(source_id,))
    observation = next(
        row
        for row in context.observations.values()
        if row.source_id == source_id and _is_bulk_not_liquid_composition(row)
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.ALPHAMELTS,
        context=context,
    )
    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason.value == "bulk_not_liquid_composition"
    assert residual.numeric is None
