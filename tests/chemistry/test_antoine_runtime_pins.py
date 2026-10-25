from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from engines.builtin.foulant_disposition import _pure_component_antoine_pa
from engines.builtin.melt_effect_adjustment import (
    WARN_TIER_ANALYTICAL_MODELS,
    _evaluate_analytical_forms,
)
from engines.builtin.vapor_pressure import _reconstructed_anchor_pressure_Pa
from simulator import volatile_properties
from simulator.chemistry.ellingham_graph import _antoine_reference_pressure_Pa
from simulator.chemistry.langmuir_knudsen import pseudo_antoine_p_eq_pa
from simulator.condensation import _antoine_psat_pa
from simulator.extraction import ExtractionMixin
from simulator.diagnostic_helpers.alphamelts_volatility import (
    _analytical_vapor_pressures_from_activities,
)
from simulator.diagnostic_helpers.species_rail_differential import (
    _pure_component_antoine_pa as _rail_antoine_pa,
)
from simulator.vapour_rail.catalog import (
    DEFAULT_EXTRAPOLATION_POLICY,
    CompiledPressureEvaluator,
    PressureObservable,
    ValidationStatus,
    _ReferencePressureModel,
)


def test_survey_antoine_sample_is_pinned_as_float_hex():
    sample = {"A": 5.0, "B": 1000.0, "C": -200.0}

    pressure_pa = _antoine_reference_pressure_Pa(sample, 1000.0)
    volatile_pressure = volatile_properties._evaluate_correlation(
        SimpleNamespace(correlation_family="antoine", coefficients=sample),
        1000.0,
    )

    assert pressure_pa is not None
    assert pressure_pa.hex() == "0x1.5f769cae07281p+12"
    assert volatile_pressure.hex() == "0x1.5f769cae07281p+12"


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_hex"),
    (
        ("Na", 1000.0, "0x1.160a226c438ffp+14"),
        ("K", 900.0, "0x1.777ea4a15c5ebp+14"),
        ("Mg", 1200.0, "0x1.585d52b026f95p+14"),
        ("Na", 1200.0, "0x1.ca9a35618cac1p+16"),
    ),
)
def test_normalized_runtime_callers_keep_bit_identical_antoine_pins(
    vapor_pressure_data, species, temperature_K, expected_hex
):
    coefficients = vapor_pressure_data["metals"][species][
        "pure_component_antoine"
    ]

    ellingham_pressure = _antoine_reference_pressure_Pa(
        coefficients, temperature_K
    )
    volatile_pressure = volatile_properties._evaluate_correlation(
        SimpleNamespace(
            correlation_family="antoine",
            coefficients=coefficients,
        ),
        temperature_K,
    )
    condensation_pressure = _antoine_psat_pa(
        species,
        temperature_K,
        vapor_pressure_data=vapor_pressure_data,
    )

    assert ellingham_pressure is not None
    assert ellingham_pressure.hex() == expected_hex
    assert volatile_pressure.hex() == expected_hex
    assert condensation_pressure.hex() == expected_hex


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_hex"),
    (
        ("Ca", 1500.0, "0x1.53b09d7ffab5dp+14"),
        # NIST-JANAF Al-003/Al-005 refit; updated from the superseded Stull sidecar.
        ("Al", 1800.0, "0x1.40296898f0cb5p+6"),
        ("Ti", 1900.0, "0x1.fb90b6722b333p-3"),
    ),
)
def test_langmuir_knudsen_antoine_p_eq_is_pinned_as_float_hex(
    species, temperature_K, expected_hex
):
    assert pseudo_antoine_p_eq_pa(species, temperature_K).hex() == expected_hex


def test_foulant_antoine_p_sat_is_pinned_as_float_hex(vapor_pressure_data):
    rows = (
        ("NaCl", 1473.15, "0x1.55ff3657cd95ep+13"),
        ("KCl", 1387.0, "0x1.c46a0fb6d4d66p+12"),
        ("CaCl2", 1650.0, "0x1.054b6a8a3c325p+10"),
    )
    for species, temperature_K, expected_hex in rows:
        pressure_pa = _pure_component_antoine_pa(
            vapor_pressure_data["foulant_vapor"][species], temperature_K
        )
        assert pressure_pa is not None
        assert pressure_pa.hex() == expected_hex


def test_reconstructed_gas_rail_anchor_pressure_is_pinned(vapor_pressure_data):
    data = copy.deepcopy(vapor_pressure_data)
    row = data["metals"]["Mg"]
    gas_rail = row["gas_rail_standard_reaction"]
    gas_rail["status"] = ""
    gas_rail.pop("authoritative", None)

    pressure_pa = _reconstructed_anchor_pressure_Pa(
        "Mg",
        row,
        {
            "pressure_rail": "gas_rail_standard_reaction",
            "temperature_K": 1366.0,
        },
    )

    assert pressure_pa.hex() == "0x1.900715495c51fp-43"


def test_alphamelts_activity_diagnostic_antoine_reference_pins(
    vapor_pressure_data,
):
    result = _analytical_vapor_pressures_from_activities(
        vapor_pressure_data=vapor_pressure_data,
        temperature_C=1600.0,
        pO2_bar=1e-9,
        melt_oxide_activities={
            "Na2O": 0.2,
            "K2O": 0.1,
            "MgO": 0.4,
            "SiO2": 0.5,
            "Al2O3": 0.3,
        },
        composition_wt_pct={
            "Na2O": 2.0,
            "K2O": 1.0,
            "MgO": 4.0,
            "SiO2": 45.0,
            "Al2O3": 15.0,
        },
    )

    for species, expected_hex in (
        ("Na", "0x1.0fc0c6cbb52d4p+14"),
        ("K", "0x1.59f4771222726p+16"),
        ("Mg", "0x1.f42edd8a229dap+20"),
        ("SiO", "0x1.67e4168316334p+1"),
    ):
        assert result["species"][species]["P_reference_Antoine_Pa"].hex() == (
            expected_hex
        )


def test_species_rail_antoine_evaluator_pins_real_rows(vapor_pressure_data):
    rows = (
        ("Na", 1000.0, "0x1.160a226c438ffp+14"),
        ("K", 900.0, "0x1.777ea4a15c5ebp+14"),
        ("Mg", 1200.0, "0x1.585d52b026f95p+14"),
    )
    for species, temperature_K, expected_hex in rows:
        pressure_pa = _rail_antoine_pa(
            vapor_pressure_data["metals"][species], temperature_K
        )
        assert pressure_pa is not None
        assert pressure_pa.hex() == expected_hex


def test_foulant_consumer_refuses_missing_antoine_A_as_invalid_input():
    refusal = None
    try:
        _pure_component_antoine_pa(
            {"pure_component_antoine": {"B": 1000.0, "C": 0.0}},
            1000.0,
        )
    except Exception as exc:
        refusal = exc
    else:
        pytest.fail("missing Antoine A unexpectedly produced a pressure")

    assert refusal is not None
    assert type(refusal).__name__ == "_AntoineCoefficientRefusal"
    assert str(refusal) == "missing_antoine_coefficient: coefficient=A value=None"


def test_foulant_consumer_refuses_missing_C_for_three_parameter_form():
    coefficients = {
        "A": 5.0,
        "B": 1000.0,
        "runtime_form": "log10(P_Pa) = A - B / (T_K + C)",
    }

    with pytest.raises(
        ValueError,
        match="missing_antoine_coefficient: coefficient=C",
    ):
        _pure_component_antoine_pa(
            {"pure_component_antoine": coefficients},
            1000.0,
        )


def test_ellingham_consumer_computes_declared_two_parameter_source():
    from pathlib import Path

    import yaml

    source_path = Path(__file__).resolve().parents[2] / "data/vapor_pressures.yaml"
    payload = yaml.safe_load(source_path.read_text())
    record = payload["families"]["oxide_trace_In2O_family"][
        "physical_properties"
    ]["species"]["In2O"]["t431_correlations"][0]

    pressure_pa = _antoine_reference_pressure_Pa(record, 1000.0)

    # Valderrama and Jacob report log10(p/atm) = -13150/T_K + 7.58;
    # at 1000 K this is about 0.273 Pa after converting atm to Pa.
    assert pressure_pa == pytest.approx(0.273, rel=0.01)


def test_ellingham_negative_denominator_regression_guard():
    pressure_pa = _antoine_reference_pressure_Pa(
        {"A": 5.0, "B": 1000.0, "C": -200.0},
        100.0,
    )

    # Preserve existing algebra: log10(P/Pa) = 5 - 1000/(100 - 200).
    assert pressure_pa == pytest.approx(1e15)


def test_c7_ca_partial_antoine_block_is_omitted_with_notice(monkeypatch):
    from types import SimpleNamespace

    import engines.builtin.evaporation_flux as evaporation_flux
    import simulator.evaporation as evaporation
    import simulator.silent_zero as silent_zero
    import simulator.transport_regime as transport_regime

    class FakeExtraction(ExtractionMixin):
        def __init__(self):
            self.melt = SimpleNamespace(
                stir_state=SimpleNamespace(axial=1.0),
                stir_factor=1.0,
                p_total_mbar=0.05,
                melt_surface_area_m2=0.2,
            )
            self.vapor_pressures = {
                "metals": {
                    "Ca": {
                        "pure_component_antoine": {"A": 5.0, "B": 1000.0},
                        "evaporation_alpha": {"value": 1.0},
                    }
                }
            }
            self.setpoints = {}
            self.overhead = SimpleNamespace(headspace_temperature_K=0.0)
            self.overhead_model = SimpleNamespace(pipe_diameter_m=0.12)

        def _evaporation_bulk_partial_pressure_pa(self, species):
            return 0.0

    flux = SimpleNamespace(
        flux_kg_s_m2=0.0,
        knudsen_number=1.0,
        r_melt=0.0,
        r_interface=0.0,
        r_gas=0.0,
        alpha_effective=1.0,
        frozen_skull_stir_clamped=False,
    )
    monkeypatch.setattr(
        evaporation_flux,
        "_series_resistance_evaporation_flux_kg_m2_s",
        lambda **kwargs: flux,
    )
    monkeypatch.setattr(
        evaporation,
        "viscous_p_bulk_out_of_domain_diagnostic",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(transport_regime, "continuum_validity_refuses", lambda _: False)
    monkeypatch.setattr(silent_zero, "record_on_host", lambda *args, **kwargs: None)

    extent, diagnostic = FakeExtraction()._c7_transport_extent_mol(
        {"hold_temp_C": 1200.0, "p_total_mbar": 0.05},
        ca_per_extent=3.0,
    )

    assert extent == 0.0
    assert "c7_ca_p_sat_pa" not in diagnostic
    assert diagnostic["c7_transport_refusal"] == (
        "missing_input_antoine_coefficient_C"
    )
    assert diagnostic["notices"][0].startswith(
        "antoine_species_skipped: species=Ca"
    )


def test_melt_effect_antoine_forms_keep_stull_pressure_pins():
    forms = WARN_TIER_ANALYTICAL_MODELS["cl_halide"]["forms"]
    result = _evaluate_analytical_forms(forms, T_K=1473.15)["vapor_pressure"]

    assert result["NaCl"]["P_Pa"].hex() == "0x1.55ff3657cd95ep+13"
    assert result["KCl"]["P_Pa"].hex() == "0x1.091daf8d43829p+14"


def test_vapour_catalog_antoine_log_and_boundary_pins(vapor_pressure_data):
    rows = (
        ("Na", 1000.0, "0x1.1004b22108244p+2"),
        ("K", 900.0, "0x1.185ec2f3c3cafp+2"),
        ("Mg", 1200.0, "0x1.15f6f66eb80bbp+2"),
    )
    references = {}
    for species, temperature_K, expected_hex in rows:
        coefficients = vapor_pressure_data["metals"][species][
            "pure_component_antoine"
        ]
        reference = _ReferencePressureModel("antoine", coefficients, ())
        references[species] = reference
        assert reference.log10_pressure(temperature_K).hex() == expected_hex

    evaluator = CompiledPressureEvaluator(
        species_id="Na",
        evaluator_family="antoine",
        pressure_observable=PressureObservable.PURE_COMPONENT_SATURATION_PRESSURE,
        species_basis="pure_component",
        valid_temperature_K=(924.0, 1118.0),
        validation_status=ValidationStatus.VALIDATED,
        reference_model=references["Na"],
        extrapolation_policy=DEFAULT_EXTRAPOLATION_POLICY,
        out_of_range_status="extrapolated",
        acquisition_flag="",
    )
    assert evaluator._reciprocal_T_tangent_log10(1200.0).hex() == (
        "0x1.452f3dc163188p+2"
    )
    assert evaluator.evaluate(1200.0).pressure_pa.hex() == (
        "0x1.d6b9eb0b55a4cp+16"
    )
    pressure_rows = (
        ("K", 900.0, (679.4, 1033.0), "0x1.777ea4a15c5ebp+14"),
        ("Mg", 1200.0, (701.0, 1361.0), "0x1.585d52b026f95p+14"),
    )
    for species, temperature_K, valid_range_K, expected_hex in pressure_rows:
        pressure_evaluator = CompiledPressureEvaluator(
            species_id=species,
            evaluator_family="antoine",
            pressure_observable=(
                PressureObservable.PURE_COMPONENT_SATURATION_PRESSURE
            ),
            species_basis="pure_component",
            valid_temperature_K=valid_range_K,
            validation_status=ValidationStatus.VALIDATED,
            reference_model=references[species],
            extrapolation_policy=DEFAULT_EXTRAPOLATION_POLICY,
            out_of_range_status="extrapolated",
            acquisition_flag="",
        )
        assert pressure_evaluator.evaluate(temperature_K).pressure_pa.hex() == (
            expected_hex
        )
