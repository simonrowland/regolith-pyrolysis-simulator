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


def _petthermotools_activity_result(activity_sources: dict[str, object]):
    backend = AlphaMELTSBackend()
    results = {
        "Conditions": {"mass": 100.0, "P_bar": 1.0},
        "Liquid": {"mass": 100.0, "SiO2": 50.0},
        "Liquid_prop": {"mass": 100.0, "SiO2_activity": 0.4},
        **activity_sources,
    }
    return backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={"SiO2": 50.0},
        total_input_kg=0.1,
    )


@pytest.mark.parametrize(
    ("gamma", "reason"),
    [
        (float("nan"), "nonfinite_gamma"),
        (0.0, "nonpositive_gamma"),
    ],
)
def test_sr01_invalid_gamma_falls_through_to_later_activity_source(
    gamma: float, reason: str
) -> None:
    result = _petthermotools_activity_result(
        {"activity_coefficients": {"SiO2": gamma}}
    )

    assert result.activity_coefficients == {"SiO2": 0.4}
    assert any(
        warning.startswith("SR01:")
        and "oxide='SiO2'" in warning
        and f"reason={reason}" in warning
        and "selected_source='phase_activity_fields'" in warning
        for warning in result.warnings
    )


@pytest.mark.parametrize(
    ("activity", "reason"),
    [
        (float("nan"), "nonfinite_direct_activity"),
        (0.0, "nonpositive_direct_activity"),
    ],
)
def test_sr02_invalid_direct_activity_uses_alternate_source(
    activity: float, reason: str
) -> None:
    result = _petthermotools_activity_result(
        {
            "melt_oxide_activities": {"SiO2": activity},
            "activities": {"Liquid": {"SiO2": 0.4}},
        }
    )

    assert result.activity_coefficients == {"SiO2": 0.4}
    assert any(
        warning.startswith("SR02:")
        and "oxide='SiO2'" in warning
        and f"reason={reason}" in warning
        and "alternate_source='activities'" in warning
        for warning in result.warnings
    )


def test_sr04_invalid_endmember_activity_names_parent_oxide() -> None:
    from engines.alphamelts.parser import project_equilibrium_to_diagnostics

    diagnostics = project_equilibrium_to_diagnostics(
        SimpleNamespace(
            status="ok",
            liquid_fraction=1.0,
            phase_masses_kg={"liquid": 1.0},
            activity_coefficients={"SiO2_Liq": 0.0},
        ),
        mode="test",
        engine_version="test",
    )

    assert diagnostics.activity_coefficients == {"SiO2_Liq": 0.0}
    assert any(
        notice.startswith("SR04:")
        and "endmember='SiO2_Liq'" in notice
        and "parent_oxide=SiO2" in notice
        and "reason=nonpositive" in notice
        for notice in diagnostics.backend_warnings
    )


def test_sr05_nonfinite_thermoengine_map_entries_are_dropped_with_notice() -> None:
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    notices: list[str] = []
    projected = transport._finite_mapping(
        {"SiO2": 50.0, "MgO": float("inf")},
        source="Liquid.oxide_wt",
        repair_notices=notices,
    )

    assert projected == {"SiO2": 50.0}
    assert len(notices) == 1
    assert "SR05:" in notices[0]
    assert "field=Liquid.oxide_wt" in notices[0]
    assert "item='MgO'" in notices[0]


def test_sr06_empty_thermoengine_liquid_composition_uses_bulk_with_notice() -> None:
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    class FakeMelts:
        def __init__(self):
            self.oxide_calls = 0

        def get_oxide_names(self):
            return ("SiO2", "MgO")

        def set_bulk_composition(self, _bulk):
            return None

        def equilibrate_tp(self, *_args, **_kwargs):
            return [("success", 1400.0, 0.1, object())]

        def get_list_of_phases_in_assemblage(self, _root):
            return ("Liquid",)

        def get_mass_of_phase(self, _root, _phase):
            return 1000.0

        def get_composition_of_phase(self, _root, _phase, basis):
            if basis == "component":
                return {"SiO2": 0.5, "MgO": float("inf")}
            self.oxide_calls += 1
            return {"SiO2": 60.0, "MgO": 40.0} if self.oxide_calls == 1 else {}

        def get_property_of_phase(self, _root, _phase, _name):
            return 1.0

        def get_dictionary_of_affinities(self, _root, **_kwargs):
            return {}

        def get_phase_names(self):
            return ("Liquid",)

    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    transport._equilibrate = object()
    transport._liq_phase = object()
    transport._operation_roots = []
    transport._new_melts_model = lambda: FakeMelts()
    transport._echo_log_fO2 = lambda *_args, **_kwargs: -10.0
    transport._chemical_potentials_for_phase = (
        lambda *_args: {"SiO2": 1.0, "MgO": 2.0}
    )
    component_moles: dict[str, float] = {}

    def capture_activities(**kwargs):
        component_moles.update(kwargs["component_mole_fraction"])
        return {}

    transport._activities_from_chemical_potentials = capture_activities
    transport._fe_redox_split = lambda _liquid: {}

    payload = transport._equilibrate_isolated(
        temperature_C=1400.0,
        pressure_bar=1.0,
        comp_wt={"SiO2": 60.0, "MgO": 40.0},
        warnings=(),
    )

    assert payload.liquid_composition_wt_pct == {"SiO2": 60.0, "MgO": 40.0}
    assert component_moles == {"SiO2": 0.5, "MgO": float("inf")}
    assert any(
        warning.startswith("SR06:")
        and "field=liquid_composition_wt_pct" in warning
        and "replacement=bulk_composition" in warning
        for warning in payload.warnings
    )
    assert any("composition_of_phase returned an empty payload" in warning for warning in payload.warnings)


def test_sr05_nonfinite_component_vector_keeps_typed_failure() -> None:
    from engines.alphamelts.thermoengine import (
        ThermoEngineNonFiniteField,
        ThermoEngineTransport,
    )

    class FakeLiquid:
        endmember_names = ("SiO2", "MgO")

    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    transport._liq_phase = FakeLiquid()

    with pytest.raises(ThermoEngineNonFiniteField):
        transport._activities_from_chemical_potentials(
            temperature_C=1400.0,
            pressure_bar=1.0,
            component_mole_fraction={"SiO2": 0.5, "MgO": float("inf")},
            comp_wt={"SiO2": 60.0, "MgO": 40.0},
        )


def test_sr07_empty_component_vector_uses_wt_moles_without_changing_activity() -> None:
    from engines.alphamelts.thermoengine import ThermoEngineTransport
    from simulator.accounting.formulas import resolve_species_formula

    class FakeLiquid:
        endmember_names = ("SiO2", "MgO")

        def chem_potential(self, _temperature, _pressure, *, mol):
            self.normalized_moles = mol[0]
            return (0.2, 0.3)

        def gibbs_energy(self, _temperature, _pressure, *, mol):
            return (0.0,)

    liquid = FakeLiquid()
    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    transport._liq_phase = liquid
    transport._activity_converter = lambda mu, _mu0, _temperature: mu
    notices: list[str] = []
    activities = transport._activities_from_chemical_potentials(
        temperature_C=1400.0,
        pressure_bar=1.0,
        component_mole_fraction={},
        comp_wt={"SiO2": 60.0, "MgO": 40.0},
        repair_notices=notices,
    )
    si_moles = 60.0 / (resolve_species_formula("SiO2").molar_mass_kg_per_mol() * 1000.0)
    mg_moles = 40.0 / (resolve_species_formula("MgO").molar_mass_kg_per_mol() * 1000.0)

    assert liquid.normalized_moles == pytest.approx(
        [si_moles / (si_moles + mg_moles), mg_moles / (si_moles + mg_moles)]
    )
    assert activities == {"SiO2": 0.2, "MgO": 0.3}
    assert len(notices) == 1 and notices[0].startswith("SR07:")
    assert "replacement=wt_derived_endmember_moles" in notices[0]


def test_sr08_negative_wt_component_clamps_to_zero_with_notice() -> None:
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    notices: list[str] = []
    moles = transport._endmember_moles_from_wt(
        {"SiO2": -1.0},
        ("SiO2",),
        repair_notices=notices,
    )

    assert moles == [0.0]
    assert len(notices) == 1 and notices[0].startswith("SR08:")
    assert "item='SiO2'" in notices[0]
    assert "replacement=0.0" in notices[0]


def test_sr09_missing_mu_empty_pure_state_and_nonpositive_activity_are_noticed() -> None:
    from engines.alphamelts.thermoengine import ThermoEngineTransport

    class FakeLiquid:
        endmember_names = ("SiO2", "MgO", "CaSiO3")

        def chem_potential(self, _temperature, _pressure, *, mol):
            return (1.0, 2.0)

        def gibbs_energy(self, _temperature, _pressure, *, mol):
            return () if mol[0][0] == 1.0 else (0.0,)

    transport = ThermoEngineTransport.__new__(ThermoEngineTransport)
    transport._liq_phase = FakeLiquid()
    transport._activity_converter = lambda *_args: 0.0
    notices: list[str] = []
    activities = transport._activities_from_chemical_potentials(
        temperature_C=1400.0,
        pressure_bar=1.0,
        component_mole_fraction={"SiO2": 0.5, "MgO": 0.5},
        comp_wt={},
        repair_notices=notices,
    )

    assert activities == {}
    assert any("item='SiO2'" in notice and "empty_pure_state_value" in notice for notice in notices)
    assert any("item='MgO'" in notice and "nonpositive_computed_activity" in notice for notice in notices)
    assert any("item='CaSiO3'" in notice and "missing_endmember_entry" in notice for notice in notices)


def test_sr10_diagnostics_projection_filters_nonfinite_fields_and_bad_masses() -> None:
    from engines.alphamelts.parser import project_equilibrium_to_diagnostics

    diagnostics = project_equilibrium_to_diagnostics(
        SimpleNamespace(
            status="ok",
            phases_present=("liquid", "olivine"),
            phase_masses_kg={"liquid": 1.0, "olivine": 0.0},
            liquid_fraction=1.0,
            liquid_composition_wt_pct={"SiO2": 60.0, "MgO": float("nan")},
            activity_coefficients={"SiO2": 0.4, "MgO": float("inf")},
        ),
        mode="test",
        engine_version="test",
    )

    assert diagnostics.phase_masses_kg == {"liquid": 1.0}
    assert diagnostics.liquid_composition_wt_pct == {"SiO2": 60.0}
    assert diagnostics.activity_coefficients == {"SiO2": 0.4}
    assert sum(warning.startswith("SR10:") for warning in diagnostics.backend_warnings) == 3


def test_sr11_path_point_omission_names_temperature_and_oxide() -> None:
    from engines.alphamelts.parser import project_equilibrium_to_diagnostics

    diagnostics = project_equilibrium_to_diagnostics(
        SimpleNamespace(
            status="ok",
            liquid_fraction=1.0,
            phase_masses_kg={"liquid": 1.0},
            liquid_fraction_path=({
                "temperature_C": 1200.0,
                "liquid_fraction": 0.5,
                "liquid_composition_wt_pct": {
                    "SiO2": 60.0,
                    "MgO": float("nan"),
                },
            },),
        ),
        mode="test",
        engine_version="test",
    )

    assert diagnostics.liquid_fraction_path[0]["liquid_composition_wt_pct"] == {
        "SiO2": 60.0,
    }
    assert any(
        warning.startswith("SR11:")
        and "T=1200.0C MgO" in warning
        for warning in diagnostics.backend_warnings
    )


def test_sr12_notices_cover_present_repairs_but_not_absent_controls() -> None:
    from engines.alphamelts.parser import _control_float

    warnings: list[str] = []
    assert _control_float({}, "pressure_bar", 1.0, repair_notices=warnings) == 1.0
    assert not any(warning.startswith("SR12:") for warning in warnings)
    assert _control_float({"temperature_C": None}, "temperature_C", 0.0, repair_notices=warnings) == 0.0
    assert _control_float({"fO2_log": float("nan")}, "fO2_log", -9.0, repair_notices=warnings) == -9.0
    assert _control_float({"temperature_C": True}, "temperature_C", 0.0, repair_notices=warnings) == 0.0
    assert _control_float({"temperature_C": b"1400"}, "temperature_C", 0.0, repair_notices=warnings) == 0.0

    assert len(warnings) == 4
    assert all(warning.startswith("SR12:") for warning in warnings)
    assert any("field=temperature_C" in warning and "non_declared_control_defaulted" in warning and "replacement=0.0" in warning for warning in warnings)
    assert any("field=fO2_log" in warning and "nonfinite_control_defaulted" in warning for warning in warnings)


def test_sr13_incomplete_first_activity_source_matches_green_selection() -> None:
    backend = AlphaMELTSBackend()
    repairs: list[dict[str, str]] = []
    values, diagnostics = backend._extract_activity_mapping(
        {
            "melt_oxide_activities": {"SiO2": 0.3},
            "activities": {"Liquid": {"SiO2": 0.4, "MgO": 0.5}},
        },
        liquid_composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
        repair_issues=repairs,
    )

    assert values == {"SiO2": 0.3}
    assert diagnostics["diagnostic_activity_source"] == "melt_oxide_activities"
    assert not any(item["site"] == "SR13" for item in repairs)


def test_sr13_empty_activity_source_notice_keeps_green_fallback_value() -> None:
    result = _petthermotools_activity_result(
        {
            "melt_oxide_activities": {},
            "activities": {"Liquid": {"SiO2": 0.4, "MgO": 0.5}},
        }
    )

    assert result.activity_coefficients == {"SiO2": 0.4, "MgO": 0.5}
    assert any(
        warning.startswith("SR13:")
        and "source='melt_oxide_activities'" in warning
        and "selected_source='activities'" in warning
        and "empty_source_skipped" in warning
        for warning in result.warnings
    )


def test_sr14_unparseable_phase_formula_uses_returned_oxide_composition() -> None:
    from simulator.accounting.formulas import resolve_species_formula

    backend = AlphaMELTSBackend()
    notices: list[str] = []
    species_mol, species_kg = backend._phase_species_from_instances(
        [{
            "instance_id": "olivine1",
            "phase": "olivine",
            "physical_mass_kg": 1.0,
            "formula_or_endmember_token": "solver-token",
            "composition_wt_pct": {"SiO2": 50.0},
        }],
        repair_notices=notices,
    )

    assert species_kg == {"olivine1": {"SiO2": 0.5}}
    assert species_mol == {
        "olivine1": {
            "SiO2": 0.5 / resolve_species_formula(
                "SiO2"
            ).molar_mass_kg_per_mol(),
        }
    }
    assert len(notices) == 1 and notices[0].startswith("SR14:")
    assert "token='solver-token'" in notices[0]
    assert "selected_source=returned_oxide_composition" in notices[0]


def test_sr16_magemin_phase_composition_filters_invalid_values_with_notices() -> None:
    backend = MAGEMinBackend()
    notices: list[str] = []
    payload = backend._phase_assemblage_payload(
        {
            "phases": {
                "liq": {
                    "mass_kg": 1.0,
                    "composition": {
                        "SiO2": 60.0,
                        "MgO": 0.0,
                        "FeO": -1.0,
                        "CaO": float("inf"),
                        "MnO": float("nan"),
                    },
                },
                "olivine": {"mass_kg": 1.0, "composition": {"MgO": 50.0}},
            },
        },
        repair_notices=notices,
    )

    assert payload[2] == {
        "liq": {"SiO2": 60.0, "CaO": float("inf")},
        "olivine": {"MgO": 50.0},
    }
    assert payload[3] == 0.5
    assert len(notices) == 3
    assert all(notice.startswith("SR16:") and "phase='liq'" in notice for notice in notices)


def test_sr15_composition_mapping_drops_invalid_values_with_notice() -> None:
    backend = AlphaMELTSBackend()
    notices: list[str] = []
    diagnostics = backend._out_of_domain_diagnostics(
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_wt_pct={
            "SiO2": 50.0,
            "MgO": 0.0,
            "CaO": float("inf"),
        },
        repair_notices=notices,
    )

    assert diagnostics["out_of_domain_crash_point"]["composition_wt_pct"] == {
        "SiO2": 50.0,
    }
    assert len(notices) == 2
    assert any(
        "field=composition_wt_pct" in notice
        and "species='MgO'" in notice
        and "reason=nonpositive" in notice
        for notice in notices
    )
    assert any(
        "field=composition_wt_pct" in notice
        and "species='CaO'" in notice
        and "reason=nonfinite" in notice
        for notice in notices
    )


def test_sr15_runtime_refusal_surfaces_nested_composition_repair_notice() -> None:
    backend = AlphaMELTSBackend()
    result = backend.equilibrate(
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'CaO': 0.0},
            'unsupported.account': {'MgO': 1.0},
        },
    )

    assert result.status == 'out_of_domain'
    assert any(
        'SR15:' in notice
        and 'field=composition_mol_by_account[process.cleaned_melt]' in notice
        and "species='CaO'" in notice
        and 'reason=nonpositive' in notice
        for notice in result.warnings
    )
