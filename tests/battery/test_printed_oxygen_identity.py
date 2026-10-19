"""Pin migrated identity.fO2_Pa for printed oxygen on activity coefficients.

Pretorius and Muan is not on this base. Holzheid 1997 Table 3a and
O'Neill and Eggins 2002 Table 7 are the rows that print a log fO2 on
activity-coefficient observations. The current lift keeps the printed log
on point conditions and leaves identity.fO2_Pa unknown unless a pascal
pressure was already copied.

t-1360: a later commit teaches every accepting quantity to use the same
log10(bar) -> Pa conversion. The Holzheid and NiO assertions below record
the pre-change identities and are updated in that commit. The FeO pin and
the -9.56 sanity value must not move.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, _log_fO2_to_pa
from simulator.battery.records import Value

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


def test_holzheid_v69_printed_log_does_not_reach_gamma_identity() -> None:
    # Current behaviour. t-1360 fills identity.fO2_Pa from this printed log.
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
    assert observation.identity.fO2_Pa.is_unknown
    assert observation.identity.fO2_Pa.reason == _UNMAPPED


def test_oneill_nio_parent_log_does_not_reach_gamma_identity() -> None:
    # The log is on the parent values, not the series row. Current behaviour
    # copies it onto point conditions and still leaves identity unknown.
    result = _migrated("oneill-2002-feo-activity-coefficients-cmas.yaml")
    observation = _one(result, "oneill_2002_table7_nio", "row=ad-eutectic")
    assert observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
    log = observation.point_conditions["fO2_log"]
    assert _decimal_point(log.state) == Decimal("-12.61")
    assert "fO2_Pa" not in (observation.point_conditions or {})
    assert observation.identity.fO2_Pa is not None
    assert observation.identity.fO2_Pa.is_unknown
    assert observation.identity.fO2_Pa.reason == _UNMAPPED


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
