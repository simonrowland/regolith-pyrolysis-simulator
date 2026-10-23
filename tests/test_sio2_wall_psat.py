"""t-1478 part 2: molecular silica wall pressure, distinct from SiO products.

Tests-only history d957e478f pins the former absent-fit/missing-reactivity
refusals at250,328,500,900,1400,1800 K and the328.453 K cold-bin input.
"""

import math
from decimal import Decimal

import pytest

from simulator import condensation


@pytest.mark.parametrize("temperature", [100, 250, 328, 328.453, 500, 900, 1400, 1800, 4500])
def test_all_wall_readers_use_the_selected_silica_surface(temperature):
    from simulator.diagnostic_helpers.species_rail_differential import _evaluate_rail_pressure_Pa
    from simulator.battery.enums import Authority, Phase

    notices = {}
    pressure = condensation._antoine_psat_pa(
        "SiO2_gas", temperature, antoine_extrapolations=notices,
    )
    assert math.isfinite(pressure) and pressure > 0.0
    assert notices == {}
    diagnostic = {}
    driving = condensation._wall_deposition_driving_pressure_pa(
        "SiO2_gas", 100.0, temperature, diagnostic_out=diagnostic,
    )
    assert diagnostic["wall_saturation_pressure_pa"] == pressure
    assert diagnostic["wall_saturation_pressure_refused"] is False
    assert driving == max(0.0, 100.0 - pressure)
    assert _evaluate_rail_pressure_Pa("SiO2_gas", temperature) == pressure
    inverse = condensation.antoine_dew_temperature_diagnostic("SiO2_gas", pressure)
    assert inverse["temperature_K"] == pytest.approx(temperature, abs=1e-9)
    assert inverse["valid_range_K"] == [100.0, 4500.0]
    assert inverse["routing_authority"] is False
    phase = Phase.CR if temperature <= 1994.4690265486726 else Phase.L
    prediction = _static_prediction(temperature, phase)
    assert float(prediction.value) == pressure
    assert prediction.authority is Authority.CERTIFIED
    assert prediction.notices == ()


def _static_prediction(temperature, phase, polymorph=None):
    from simulator.battery.enums import Engine, MethodToken
    from simulator.battery.score import predict_with_engine
    from tests.battery import factories as F

    temperature = Decimal(str(temperature))
    experiment = F.tabulation_experiment(method=MethodToken.STATIC_EQUILIBRIUM, T_K=temperature)
    identity = F.psat_identity("SiO2", T_K=temperature, reservoir_phase=phase)
    if polymorph is not None:
        from dataclasses import replace
        from simulator.battery.records import State
        identity = replace(identity, reservoir=State.of(
            replace(identity.reservoir.value, polymorph=State.of(polymorph)),
        ))
    observation = F.observation("silica-wall", experiment.experiment_id, identity, Decimal("1"))
    return predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)


@pytest.mark.parametrize("temperature, table, gas_kf, condensed_kf", [
    # Printed JANAF logKf cells, three decimal places. A difference of two
    # independently rounded cells permits at most0.001 dex rounding error.
    (900, "O-037", 17.899, 43.400),
    (1400, "O-035", 11.514, 24.680),
    (1800, "O-035", 8.845, 17.132),
    (2000, "O-038", 7.796, 14.388),
    (4500, "O-038", 1.222, -0.186),
])
def test_printed_janaf_cells(temperature, table, gas_kf, condensed_kf):
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    for table_id, expected in [("O-040", gas_kf), (table, condensed_kf)]:
        row = next(r for r in load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]["values"]
                   if r["temperature"]["value"] == temperature)
        assert row["log10_formation_equilibrium_constant"]["value"] == expected
    pressure = condensation._antoine_psat_pa("SiO2_gas", temperature)
    assert math.log10(pressure) == pytest.approx(5 + gas_kf - condensed_kf, abs=0.001)


def test_every_segment_boundary_is_continuous_and_phase_crossing_uses_fusion_owner():
    from engines.builtin.vapor_pressure import wall_condensation_antoine_coefficients
    from simulator.battery.generators.janaf import janaf_fusion_energy

    row = condensation._species_vapor_data("SiO2_gas")
    segments = row["pure_component_antoine"]["segments"]
    assert segments[0]["valid_range_K"][0] == 100
    assert segments[-1]["valid_range_K"][1] == 4500
    for left, right in zip(segments, segments[1:]):
        boundary = left["valid_range_K"][1]
        assert boundary == right["valid_range_K"][0]
        lo, hi = math.nextafter(boundary, -math.inf), math.nextafter(boundary, math.inf)
        assert wall_condensation_antoine_coefficients(row, lo)[0] is left
        assert wall_condensation_antoine_coefficients(row, hi)[0] is right
        y1 = math.log10(condensation._antoine_psat_pa("SiO2_gas", lo))
        y2 = math.log10(condensation._antoine_psat_pa("SiO2_gas", hi))
        assert abs(y2 - y1) <= 64 * math.ulp(max(1.0, abs(y1), abs(y2)))
    melting = float(janaf_fusion_energy("SiO2", Decimal(1900)).melting_temperature_K)
    assert next(s["valid_range_K"][1] for s in segments
                if s["phase"] == "high_cristobalite" and s["valid_range_K"][1] > 1900) == melting


def test_lower_refusal_and_upper_flag_are_shared():
    from simulator.battery.enums import Authority, Phase
    from simulator.diagnostic_helpers.species_rail_differential import _evaluate_rail_pressure_Pa

    with pytest.raises(condensation.WallSaturationPressureRefusal):
        condensation._antoine_psat_pa("SiO2_gas", 99.999)
    assert _evaluate_rail_pressure_Pa("SiO2_gas", 99.999) == "rail_row_absent"
    assert _static_prediction(99.999, Phase.CR).value is None
    notices = {}
    pressure = condensation._antoine_psat_pa("SiO2_gas", 4600, antoine_extrapolations=notices)
    assert pressure > 0.0
    assert all(n["authority_level"] == "extrapolated" for n in notices.values())
    prediction = _static_prediction(4600, Phase.L)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert float(prediction.value) == pressure
    inverse = condensation.antoine_dew_temperature_diagnostic("SiO2_gas", pressure)
    assert inverse["status"] == "refused_pressure_outside_certified_range"


@pytest.mark.parametrize("temperature", [100, 500, 1400, 1800])
def test_supercooled_liquid_reference_uses_existing_fusion_owner(temperature):
    from simulator.battery.enums import Authority, Phase
    from simulator.battery.generators.janaf import _janaf_fusion_energy_for_pair

    table = "O-037" if temperature < 1079.1208791208792 else "O-035"
    fusion = _janaf_fusion_energy_for_pair("SiO2", table, "O-038", Decimal(temperature))
    prediction = _static_prediction(temperature, Phase.SUPERCOOLED_L)
    solid = condensation._antoine_psat_pa("SiO2_gas", temperature)
    assert float(prediction.value) / solid == pytest.approx(
        math.exp(float(fusion.delta_g_fus_kJ_per_mol) * 1000 / (8.31441 * temperature)), rel=1e-12,
    )
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("phase_mismatch" in n.reason for n in prediction.notices)


def test_missing_fusion_input_refuses(monkeypatch):
    from simulator.battery.enums import Phase, RefusalReason
    from simulator.battery.generators import janaf

    def missing(*args):
        raise ValueError("missing printed fusion cell")

    monkeypatch.setattr(janaf, "_janaf_fusion_energy_for_pair", missing)
    prediction = _static_prediction(500, Phase.L)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "solid_liquid_reference_conversion_missing_input"


def test_sio_product_backstop_and_melt_source_remain_separate():
    from simulator.diagnostic_helpers.species_rail_differential import vapour_rail_catalog
    from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients

    row = condensation._species_vapor_data("SiO2_gas")
    assert row["fit_target"] == "standard_reaction_term"
    assert vapor_pressure_antoine_coefficients(row, 1800) == ({}, "antoine")
    evaluator = vapour_rail_catalog().species["SiO2_gas"].evaluator
    assert evaluator.activity_exponent == 1.0
    assert evaluator.pO2_exponent == 0.0
    assert evaluator.pressure_observable.value == "equilibrium_partial_pressure"
    diagnostic = {}
    assert condensation._wall_deposition_driving_pressure_pa("SiO", 100, 1800, diagnostic_out=diagnostic) == 100
    assert diagnostic["wall_saturation_pressure_status"] == "reactive_product_backstop"


@pytest.mark.parametrize("temperature, polymorph, certified", [
    (500, "cristobalite_high", False), (1400, "quartz", False),
    (1800, "quartz", False), (900, "alpha_quartz", False),
    (500, "quartz", True), (500, "alpha_quartz", True),
    (1400, "cristobalite_high", True),
])
def test_explicit_crystal_polymorph_is_not_silently_certified(temperature, polymorph, certified):
    from simulator.battery.enums import Authority, Phase, Polymorph

    prediction = _static_prediction(temperature, Phase.CR, Polymorph(polymorph))
    assert prediction.authority is (Authority.CERTIFIED if certified else Authority.EXTRAPOLATED)
    assert any("phase_mismatch" in n.reason for n in prediction.notices) is not certified


@pytest.mark.parametrize("temperature", [500, 2500])
def test_glass_is_not_certified_as_crystal_or_liquid(temperature):
    from simulator.battery.enums import Authority, Phase

    prediction = _static_prediction(temperature, Phase.GLASS)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("phase_mismatch" in n.reason for n in prediction.notices)
