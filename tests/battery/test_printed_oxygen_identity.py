"""Printed oxygen reaches identity.fO2_Pa for every accepting quantity.

Pretorius and Muan is not on this base. Holzheid 1997 Table 3a and
O'Neill and Eggins 2002 Table 7 are the rows that print a log fO2 on
activity-coefficient observations. The log stays on point conditions.
identity.fO2_Pa is the same conversion used for redox rows.

A named buffer offset is not converted. There is no buffer curve in this
owner. A row with no printed oxygen stays unknown.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, _log_fO2_to_pa
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


def test_activity_buffer_offset_stays_unknown(tmp_path: Path) -> None:
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
    matches = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
        and isinstance((observation.point_conditions or {}).get("fO2_control"), Located)
    ]
    assert len(matches) == 1
    oxygen = matches[0].identity.fO2_Pa
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason.startswith("buffer-relative fO2 (")
    assert "FMQ+2.7" in oxygen.reason
    assert matches[0].point_conditions["fO2_control"].state.value == "FMQ+2.7"


def test_child_buffer_beats_parent_log(tmp_path: Path) -> None:
    # Parent log -9 bar is 1e-4 Pa. The child's named buffer is a refusal.
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
    assert oxygen is not None and oxygen.is_unknown
    assert oxygen.reason is not None
    assert oxygen.reason.startswith("buffer-relative fO2 (")
    assert "IW+2" in oxygen.reason


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
