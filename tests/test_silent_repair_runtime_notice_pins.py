"""Current output pins for repairs that already have a runtime notice path."""

from __future__ import annotations

from types import SimpleNamespace

from simulator.core import PyrolysisSimulator
from simulator.evaporation import _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
from simulator.fe_redox import KRESS91_LIQUID_CALIBRATION_MIN_T_C
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.reduced_real_determinism import _curve_payload
from simulator.runner import _attach_composition_projected_liquidus_notice


def _projected_notice() -> dict[str, object]:
    return {
        "kind": "composition_projected",
        "reason": "composition_projected",
        "authority": "extrapolated",
        "certified_band": {"source": "test"},
        "dropped_components": [{"component": "FeS", "mass_fraction": 0.1}],
    }


def test_sr22_nonfinite_interpolation_fallback_emits_repair_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=2,
        campaign_hour=3,
        campaign=SimpleNamespace(name="C2A"),
    )

    fallback = sim._melt_redox_liquidus_floor_fallback(
        source="none:nonfinite_liquid_fraction",
        reason="non-finite liquid_fraction=nan",
        liquidus_status="invalid",
    )

    assert fallback.source == "none:nonfinite_liquid_fraction"
    assert fallback.reason == "non-finite liquid_fraction=nan"
    assert fallback.liquidus_status == "invalid"
    record = sim._melt_redox_liquidus_gate_fallback_summary()["recent"][0]
    assert record == {
        "status": "liquidus_unavailable_floor_fallback",
        "source": "none:nonfinite_liquid_fraction",
        "reason": "non-finite liquid_fraction=nan",
        "liquidus_status": "invalid",
        "floor_T_C": 1200.0,
        "hour": 2,
        "campaign_hour": 3,
        "campaign": "C2A",
        "repair_notice": {
            "kind": "kress_liquidus_floor_repair",
            "repaired": "unusable liquidus bounds or interpolation",
            "reason": "non-finite liquid_fraction=nan",
            "replacement": "deterministic Kress91 liquidus floor",
            "floor_T_C": 1200.0,
        },
    }


def test_sr22_invalid_bounds_fallback_emits_runtime_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=2,
        campaign_hour=3,
        campaign=SimpleNamespace(name="C2A"),
    )
    sim._melt_redox_liquidus_floor_fallback(
        source="none:invalid_liquidus_bounds",
        reason="invalid liquidus bounds",
        liquidus_status="invalid",
    )
    record = sim._melt_redox_liquidus_gate_fallback_summary()["recent"][0]
    assert record["repair_notice"] == {
        "kind": "kress_liquidus_floor_repair",
        "repaired": "unusable liquidus bounds or interpolation",
        "reason": "invalid liquidus bounds",
        "replacement": "deterministic Kress91 liquidus floor",
        "floor_T_C": 1200.0,
    }


def test_sr22_caught_interpolation_failure_has_no_repair_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=2,
        campaign_hour=3,
        campaign=SimpleNamespace(name="C2A"),
    )

    sim._melt_redox_liquidus_floor_fallback(
        source="none:invalid_liquid_fraction_curve",
        reason="cannot interpolate curve",
        liquidus_status="invalid",
    )

    record = sim._melt_redox_liquidus_gate_fallback_summary()["recent"][0]
    assert "repair_notice" not in record


def test_sr21_backend_fallback_message_names_both_producers() -> None:
    result = SimpleNamespace(
        ledger_transition=None,
        phase_species_mol={"melt": {"SiO2": 1.0}},
    )
    fallback = SimpleNamespace(status="ok")
    backend = MAGEMinBackend()
    backend.is_available = lambda: True
    backend.equilibrate = lambda **_kwargs: result
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.backend = backend
    sim._backend_failed = False
    sim.melt = SimpleNamespace(
        oxygen_reservoir=SimpleNamespace(melt_intrinsic_fO2_log=-10.0),
        temperature_C=1400.0,
        p_total_mbar=1.0,
    )
    sim._chem_registry = SimpleNamespace(authoritative_for=lambda _intent: None)
    sim.species_formula_registry = {}
    sim._backend_composition_mol_by_account = lambda: {}
    sim._backend_composition_mol = lambda: {}
    sim._validate_backend_account_scope_support = lambda _composition: None
    sim._sync_oxygen_reservoir_mirror = lambda: None
    sim._backend_accepts_kwarg = lambda _name: False
    sim._backend_allows_internal_analytical_fallback = lambda: True
    sim._disable_backend_after_failure = lambda: None
    sim._internal_analytical_equilibrium = lambda: fallback
    sim._record_equilibrium_status = lambda value: value

    assert sim._get_equilibrium() is fallback
    assert "magemin" in sim._last_backend_error
    assert "internal-analytical" in sim._last_backend_error


def test_sr22_missing_curve_has_no_floor_repair_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=2,
        campaign_hour=3,
        campaign=SimpleNamespace(name="C2A"),
    )

    sim._melt_redox_liquidus_floor_fallback(
        source="none:liquidus_unavailable",
        reason="no liquidus provider",
        liquidus_status="unavailable",
    )

    record = sim._melt_redox_liquidus_gate_fallback_summary()["recent"][0]
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


def test_sr23_runtime_notice_does_not_change_curve_record_bytes() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.atom_ledger = SimpleNamespace(
        mol_by_account=lambda account: {"SiO2": 1.0}
    )
    curve = sim._freeze_gate_kress_floor_curve(_projected_notice(), [])
    assert curve is not None
    before = _curve_payload(curve)

    sim._record_composition_projected_liquidus_notice_from_curve(curve)

    run_notice = sim.composition_projected_liquidus_run_notice()
    assert run_notice is not None
    assert run_notice["notices"][0]["repair_notice"] == {
        "kind": "kress_fixed_liquidus_bound_repair",
        "repaired": "missing or unusable projected liquidus bound",
        "reason": "no usable composition-projected liquidus bound",
        "replacement": "fixed Kress91 liquidus floor",
        "floor_T_C": 1200.0,
    }
    metadata: dict[str, object] = {}
    _attach_composition_projected_liquidus_notice(metadata, sim)
    assert metadata["composition_projected_liquidus_notice"] == run_notice
    assert _curve_payload(curve) == before


def test_sr23_usable_projected_bound_has_no_repair_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._record_composition_projected_liquidus_notice(
        {
            **_projected_notice(),
            "bounds_source": "liquidus_solidus:kernel:composition_projected",
        }
    )

    run_notice = sim.composition_projected_liquidus_run_notice()
    assert run_notice is not None
    assert "repair_notice" not in run_notice["notices"][0]
