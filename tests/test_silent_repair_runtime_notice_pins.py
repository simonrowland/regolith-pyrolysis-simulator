"""Current output pins for repairs that already have a runtime notice path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.core import PyrolysisSimulator
from simulator.evaporation import _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
from simulator.fe_redox import KRESS91_LIQUID_CALIBRATION_MIN_T_C
from simulator.melt_backend.magemin import MAGEMinBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.sulfsat import SulfSatGate
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


def _sr24_simulator(monkeypatch, *, fe3_ratio: float, scas_ppm: float = 250.0):
    import pandas as pd
    import simulator.melt_backend.sulfsat as sulfsat_module

    monkeypatch.setattr(
        sulfsat_module,
        "kress91_split",
        lambda **_kwargs: {"fe3": fe3_ratio},
    )
    gate = SulfSatGate()
    gate._available = True
    gate._module = SimpleNamespace(
        calculate_S2017_SCSS=lambda **_kwargs: pd.DataFrame(
            {"SCSS2_ppm_ideal_Smythe2017": [-10.0]}
        ),
        calculate_CD2019_SCAS=lambda **_kwargs: pd.DataFrame(
            {"SCAS6_ppm": [scas_ppm]}
        ),
        calculate_S6St_Jugo2010_eq10=lambda **_kwargs: 1.25,
    )

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        hour=0,
        temperature_C=926.85,
        p_total_mbar=1000.0,
    )
    sim._stage0_sulfur_input_ppm = lambda: 500.0
    sim._melt_oxide_wt_pct = lambda: {
        "SiO2": 50.0,
        "Al2O3": 15.0,
        "FeO": 10.0,
        "CaO": 10.0,
        "MgO": 10.0,
        "TiO2": 1.0,
        "Na2O": 5.0,
        "K2O": 1.0,
        "MnO": 1.0,
    }
    sim._current_melt_redox_fO2_log = lambda: -10.0
    sim._sync_oxygen_reservoir_mirror = lambda: None
    sim._sulfsat_gate = gate
    sim._sulfur_saturation_steps = []
    result = SimpleNamespace(warnings=[])
    sim._attach_post_equilibrium_sulfsat(result)
    return sim, result


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


def test_sr21_rejected_backend_message_names_producer_without_claiming_fallback() -> None:
    result = SimpleNamespace(
        backend_name="unknown",
        ledger_transition=None,
        phase_species_mol={"melt": {"SiO2": 1.0}},
    )
    for backend, producer in (
        (MAGEMinBackend(), "magemin"),
        (AlphaMELTSBackend(), "alphamelts"),
    ):
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
        sim._disable_backend_after_failure = lambda: None

        try:
            sim._get_equilibrium()
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("real backend reject must raise RuntimeError")
        assert producer in message
        assert "using internal-analytical fallback" not in message


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
    notice = run_notice["notices"][0]
    assert notice["bounds_source"] == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert notice["projected_bounds"] == "invalid"
    assert notice["floor_T_C"] == KRESS91_LIQUID_CALIBRATION_MIN_T_C
    metadata: dict[str, object] = {}
    _attach_composition_projected_liquidus_notice(metadata, sim)
    assert metadata["composition_projected_liquidus_notice"] == run_notice
    assert _curve_payload(curve) == before


def test_sr23_missing_floor_value_keeps_projected_run_notice() -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._record_composition_projected_liquidus_notice(
        {
            **_projected_notice(),
            "bounds_source": _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
        }
    )

    run_notice = sim.composition_projected_liquidus_run_notice()
    assert run_notice is not None
    notice = run_notice["notices"][0]
    assert notice["bounds_source"] == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert "floor_T_C" not in notice
    assert "repair_notice" not in notice


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


def test_sr24_finite_fit_capacity_and_fraction_repairs_emit_notice(monkeypatch) -> None:
    sim, _result = _sr24_simulator(monkeypatch, fe3_ratio=1.25)

    notice = sim.sulfur_saturation_run_notice()

    assert notice is not None
    assert notice["notices"][0]["warnings"] == [
        "Kress-Carmichael 1991 Fe3+/SumFe fit produced non-finite or out-of-range ratio 1.25 (T_K=1200.0, fO2_log=-10.0); no operator Fe3Fet_Liq supplied -> calibration tagged out_of_range",
        "SR24:sulfsat_output_repaired field=SCSS_ppm raw=-10 reason=negative_capacity replacement=0.0 ppm",
        "SR24:sulfsat_output_repaired field=S6_fraction raw=1.25 reason=fraction_outside_[0,1] replacement=1.0",
    ]


def test_sr24_finite_capacity_and_fraction_repairs_preserve_numeric_outputs(
    monkeypatch,
) -> None:
    _sim, result = _sr24_simulator(monkeypatch, fe3_ratio=0.1)
    sulfur = result.sulfur_saturation

    assert (
        sulfur.SCSS_ppm,
        sulfur.SCAS_ppm,
        sulfur.S6_fraction,
        sulfur.S_in_sulfide_ppm,
        sulfur.S_in_sulfate_ppm,
    ) == (0.0, 250.0, 1.0, 0.0, 250.0)


def test_sr24_finite_capacity_and_fraction_repairs_preserve_calibration_status(
    monkeypatch,
) -> None:
    _sim, result = _sr24_simulator(monkeypatch, fe3_ratio=0.1)

    assert (
        result.sulfur_saturation.SCSS_ppm,
        result.sulfur_saturation.SCAS_ppm,
        result.sulfur_saturation.S6_fraction,
        result.sulfur_saturation.calibration_status,
    ) == (0.0, 250.0, 1.0, "in_range")


def test_sr24_repair_notice_text_does_not_flip_calibration_status(monkeypatch) -> None:
    monkeypatch.setattr(
        SulfSatGate,
        "_finite_output_repair_notice",
        staticmethod(lambda **_kwargs: "operator repair note"),
    )
    sim, result = _sr24_simulator(monkeypatch, fe3_ratio=0.1)

    assert result.sulfur_saturation.calibration_status == "in_range"
    assert result.sulfur_saturation.warnings == [
        "operator repair note",
        "operator repair note",
    ]
    _, _, _, _, redox_in_range = sim._sulfsat_gate._run_pysulfsat(
        liquid_comp_wt=sim._melt_oxide_wt_pct(),
        T_K=1200.0,
        P_bar=1.0,
        fO2_log=-10.0,
        operator_fe3fet=None,
    )
    assert redox_in_range is True


def test_sr24_repair_and_numerical_warning_keep_out_of_range_status(monkeypatch) -> None:
    monkeypatch.setattr(
        SulfSatGate,
        "_finite_output_repair_notice",
        staticmethod(lambda **_kwargs: "operator repair note"),
    )
    _sim, result = _sr24_simulator(
        monkeypatch, fe3_ratio=0.1, scas_ppm=float("nan")
    )
    sulfur = result.sulfur_saturation

    assert sulfur.calibration_status == "out_of_range"
    assert sulfur.warnings == [
        "Chowdhury-Dasgupta 2019 SCAS returned non-finite SCAS_ppm=nan; "
        "treating capacity as 0.0 ppm for degenerate no modeled sulfide/sulfate saturation",
        "operator repair note",
        "operator repair note",
    ]
