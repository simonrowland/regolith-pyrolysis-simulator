"""Unprinted total pressure stays off point conditions; a printed one stays.

Ruling (offers channel 2026-10-22, option b): a Knudsen cell total is the
sum of in-cell partials, not a set point. An unknown total_pressure_Pa in
point_conditions stops same-point matching. A printed imposed total stays.
The identity axis keeps the unknown the migrator already recorded.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.records import Value
from tests.battery.test_kems_background_not_identity import _migrate_fixture

_UNPRINTED_REASON = "Knudsen cell; no numeric in-cell total printed"
_PRINTED_PA = Decimal("101325")
_CHAMBER_PA = Decimal("0.0001")
_IDENTITY_REASON = "in_cell_total_pressure_not_derivable"


def _located_unknown(reason: str) -> dict:
    return {
        "state": {"tag": "unknown", "reason": reason},
        "locator": {"page": 1, "note": reason},
    }


def _located_point(amount: Decimal) -> dict:
    return {
        "state": {
            "tag": "value",
            "value": {"kind": "point", "point": str(amount)},
        },
        "locator": {"page": 2, "note": "printed carrier-gas total"},
    }


def _partial_row(observation_id: str, total_pressure: dict) -> dict:
    return {
        "observation_id": observation_id,
        "experiment": "effusion-run",
        "type": "rate_series",
        "locator": {"page": 1},
        "phase": "gas",
        "regime": "knudsen_effusion",
        "units": "Pa",
        "point_conditions": {"total_pressure_Pa": total_pressure},
        "values": {
            "quantity": "p_partial",
            "method_class": "measured_direct",
            "admission_status": "admitted",
            "series": [{"T_K": 1933.0, "pressure_atm": "1.0e-8"}],
        },
    }


def _extract() -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-unprinted-total",
        "source": {
            "citation": "Fixture, A. (2026), Unprinted total pin, DOI 10.1234/UNPRINTED-TOTAL",
            "doi": "10.1234/UNPRINTED-TOTAL",
        },
        "extraction": {
            "method": "unit_test",
            "date": "2026-10-22",
            "worker": "pytest",
        },
        "review_status": "draft",
        "benches": [
            {"id": "cell-1", "identity": {"basis": "described_in_this_work"}},
        ],
        "experiments": [
            {
                "experiment_id": "effusion-run",
                "bench_id": "cell-1",
                "method": "knudsen_effusion",
                "locator": {"page": 1},
                "sample": {
                    "printed_composition": {
                        "state": {
                            "tag": "value",
                            "value": {"MgO": 50, "SiO2": 50},
                        },
                        "locator": {"page": 1},
                    },
                    "mass_kg": {
                        "state": {
                            "tag": "value",
                            "value": {"kind": "point", "point": "0.0001"},
                        },
                        "locator": {"page": 1},
                    },
                },
                "pressure_environment": {
                    "total_pressure_Pa": _located_point(_CHAMBER_PA),
                    "sweep_gas": {
                        "state": {"tag": "not_applicable", "reason": "not_applicable"},
                        "locator": {"page": 1},
                    },
                    "regime": {
                        "regime_class": {"tag": "unknown", "reason": "not_published"}
                    },
                },
            }
        ],
        "species": {
            "SiO": {
                "observations": [
                    _partial_row(
                        "unprinted_row", _located_unknown(_UNPRINTED_REASON)
                    ),
                    _partial_row("printed_row", _located_point(_PRINTED_PA)),
                ]
            }
        },
    }


def _rows(result, local_id: str):
    rows = [
        observation
        for observation in result.observations.values()
        if f"::{local_id}" in observation.observation_id
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert rows, local_id
    return rows


def _point_amount(located) -> Decimal:
    raw = located.state.value
    if isinstance(raw, Value):
        assert raw.point is not None
        return raw.point
    return Decimal(str(raw))


def test_unprinted_total_stays_off_point_conditions_and_printed_total_stays(
    tmp_path: Path,
) -> None:
    result = _migrate_fixture(tmp_path, _extract())
    for observation in _rows(result, "unprinted_row"):
        assert "total_pressure_Pa" not in (observation.point_conditions or {})
        assert "temperature_K" in (observation.point_conditions or {})
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is not None and identity_pressure.is_unknown
        assert identity_pressure.reason == _IDENTITY_REASON
        assert identity_pressure.reason != _UNPRINTED_REASON
    for observation in _rows(result, "printed_row"):
        located = (observation.point_conditions or {})["total_pressure_Pa"]
        assert located.state.is_value
        assert _point_amount(located) == _PRINTED_PA
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is not None and identity_pressure.is_unknown
        assert identity_pressure.reason == _IDENTITY_REASON
