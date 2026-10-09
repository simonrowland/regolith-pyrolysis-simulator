"""Current output pins for repairs that already have a runtime notice path."""

from __future__ import annotations

from types import SimpleNamespace

from simulator.core import PyrolysisSimulator
from simulator.evaporation import _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
from simulator.fe_redox import KRESS91_LIQUID_CALIBRATION_MIN_T_C
from simulator.reduced_real_determinism import _curve_payload


def _projected_notice() -> dict[str, object]:
    return {
        "kind": "composition_projected",
        "reason": "composition_projected",
        "authority": "extrapolated",
        "certified_band": {"source": "test"},
        "dropped_components": [{"component": "FeS", "mass_fraction": 0.1}],
    }


def test_sr22_kress_floor_fallback_output_pin_has_no_repair_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=2,
        campaign_hour=3,
        campaign=SimpleNamespace(name="C2A"),
    )

    fallback = sim._melt_redox_liquidus_floor_fallback(
        source="none:liquidus_invalid",
        reason="invalid liquidus bounds",
        liquidus_status="invalid",
    )

    assert fallback.source == "none:liquidus_invalid"
    assert fallback.reason == "invalid liquidus bounds"
    assert fallback.liquidus_status == "invalid"
    record = sim._melt_redox_liquidus_gate_fallback_summary()["recent"][0]
    assert record == {
        "status": "liquidus_unavailable_floor_fallback",
        "source": "none:liquidus_invalid",
        "reason": "invalid liquidus bounds",
        "liquidus_status": "invalid",
        "floor_T_C": 1200.0,
        "hour": 2,
        "campaign_hour": 3,
        "campaign": "C2A",
    }
    assert "repair_notice" not in record


def test_sr23_fixed_bound_curve_pin_has_no_repair_notice_or_cache_change() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.atom_ledger = SimpleNamespace(
        mol_by_account=lambda account: {"SiO2": 1.0}
    )
    notice = _projected_notice()

    curve = sim._freeze_gate_kress_floor_curve(notice, [])

    assert curve is not None
    assert curve["source"] == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert curve["solidus_T_C"] == KRESS91_LIQUID_CALIBRATION_MIN_T_C
    assert curve["liquidus_T_C"] == KRESS91_LIQUID_CALIBRATION_MIN_T_C + 1.0e-6
    assert curve["path"] == (
        (KRESS91_LIQUID_CALIBRATION_MIN_T_C, 0.0),
        (KRESS91_LIQUID_CALIBRATION_MIN_T_C + 1.0e-6, 1.0),
    )
    assert set(curve) == {
        "source",
        "solidus_T_C",
        "liquidus_T_C",
        "path",
        "composition_projected_notice",
    }
    assert "repair_notice" not in curve
    assert _curve_payload(curve) == {
        "source": _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
        "solidus_T_C": KRESS91_LIQUID_CALIBRATION_MIN_T_C,
        "liquidus_T_C": KRESS91_LIQUID_CALIBRATION_MIN_T_C + 1.0e-6,
        "path": [
            {
                "temperature_C": KRESS91_LIQUID_CALIBRATION_MIN_T_C,
                "liquid_fraction": 0.0,
            },
            {
                "temperature_C": KRESS91_LIQUID_CALIBRATION_MIN_T_C + 1.0e-6,
                "liquid_fraction": 1.0,
            },
        ],
        "composition_projected_notice": curve["composition_projected_notice"],
    }
    assert sim.composition_projected_liquidus_run_notice() is None
