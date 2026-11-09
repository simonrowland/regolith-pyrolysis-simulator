"""Printed oxygen reaches identity.fO2_Pa for every accepting quantity.

Pretorius and Muan is not on this base. Holzheid 1997 Table 3a and
O'Neill and Eggins 2002 Table 7 are the rows that print a log fO2 on
activity-coefficient observations. The log stays on point conditions.
identity.fO2_Pa is the same conversion used for redox rows.

A named buffer offset uses its cited calibration when available. A row with
no printed oxygen or buffer offset stays unknown.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.identity import IdentityEqualKind, identity_equal
from simulator.battery.migrate import (
    Migrator,
    _BARE_LOG_BAR_NOTICE,
    _BARE_LOG_SCALE_UNKNOWN,
    _BOUNDED_FO2_REASON,
    _log_fO2_to_pa,
)
from simulator.battery.records import Located, Value
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
    from simulator.oxygen_buffers import feo_iw_log10_fO2_bar

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


def test_activity_buffer_offset_uses_canonical_qfm(tmp_path: Path) -> None:
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        point={
            "T_K": 1200,
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
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and isinstance((observation.point_conditions or {}).get("fO2_control"), Located)
    ]
    assert len(matches) == 1
    oxygen = matches[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    # O'Neill (1987), reaction (2): μ°O2 = -587474 + 1584.427T
    # - 203.3164T ln(T) + 0.092710T² J/mol. At 1200 K, division by
    # R T ln(10) gives -12.296398239 log10(fO2/bar); adding FMQ+2.7 gives
    # this independent literal. J/mol over R T is dimensionless.
    expected_log = Decimal("-9.596398239")
    assert abs(oxygen.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert matches[0].point_conditions["fO2_control"].state.value == "FMQ+2.7"


def test_child_buffer_beats_parent_log(tmp_path: Path) -> None:
    # The child's buffer-relative value takes precedence over parent fallback.
    result = _migrate_fixture(
        tmp_path,
        quantity="activity",
        parent={"log_fO2": {"value": "-9", "units": "log10(bar)"}},
        point={
            "T_K": 1000,
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
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and isinstance((observation.point_conditions or {}).get("fO2_control"), Located)
        and (observation.point_conditions or {})["fO2_control"].state.value == "IW+2"
    ]
    assert len(matches) == 1
    oxygen = matches[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_value
    # O'Neill (1988) eq. 11 first segment at 1000 K gives -20.799980169
    # log10(fO2/bar), then the printed IW+2 adds to -18.799980169. μ°/(R T)
    # is dimensionless and division by ln(10) gives the base-10 log.
    expected_log = Decimal("-18.799980169")
    assert abs(oxygen.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")


def test_jugo_sb5_uses_printed_frost_fmq_at_200_mpa() -> None:
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
    # Jugo et al. (2010), p. 5934, Eq. 6, defines ΔFMQ by subtracting the FMQ
    # log fugacity at the run P–T. With the source's Frost (1991) calibration,
    # the alpha-quartz branch is not used at 1323.15 K; beta-quartz gives
    # the 1-bar base is 8.735 - 25096.3/T. Add [Frost(T,2000 bar) -
    # Frost(T,1 bar)] = 0.110(2000-1)/T and the printed +2.7. Each pressure
    # term is (K/bar)*bar/K; evaluating the printed coefficients gives this
    # independent log literal, -7.365899369 log10(fO2/bar).
    expected_log = Decimal("-7.365899369")
    assert abs(oxygen.value.log10() - Decimal(5) - expected_log) < Decimal("5e-9")
    assert any(
        "calibration=frost_1991_table_1" in notice.reason
        and "pressure correction=Frost 1991 molar-volume ΔV term" in notice.reason
        for notice in observation.notices
    )


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
