"""t-1484 Fe wall certification; historical outcome pins in9f5c00151."""

import math
from decimal import Decimal

import pytest

from simulator import condensation


def _static_prediction(temperature, phase, polymorph=None):
    from dataclasses import replace
    from simulator.battery.enums import Engine, MethodToken
    from simulator.battery.records import State
    from simulator.battery.score import predict_with_engine
    from tests.battery import factories as F

    temperature = Decimal(str(temperature))
    experiment = F.tabulation_experiment(method=MethodToken.STATIC_EQUILIBRIUM, T_K=temperature)
    identity = F.psat_identity("Fe", T_K=temperature, reservoir_phase=phase)
    if polymorph is not None:
        identity = replace(identity, reservoir=State.of(
            replace(identity.reservoir.value, polymorph=State.of(polymorph)),
        ))
    observation = F.observation("fe-wall", experiment.experiment_id, identity, Decimal("1"))
    return predict_with_engine(Engine.INTERNAL_ANALYTICAL, observation, experiment=experiment)


@pytest.mark.parametrize("temperature, gas_log_kf, liquid_log_kf", [
    # Printed JANAF Fe-008 gas and Fe-006 liquid cells, p0=100000 Pa.
    (100, -208.902, None), (1000, -13.824, None), (1400, -7.808, None),
    (1600, -5.952, None), (1800, -4.522, None), (2000, -3.429, 0.0),
    (2100, -2.966, 0.0), (2200, -2.548, 0.0), (2300, -2.168, 0.0),
    (3200, 0.0, -0.121), (4000, 0.0, -1.236),
])
def test_printed_janaf_cells(temperature, gas_log_kf, liquid_log_kf):
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    for table_id, expected in [("Fe-008", gas_log_kf), ("Fe-006", liquid_log_kf)]:
        if expected is None:
            continue
        row = next(r for r in load_table_document(TABLES_DIR / f"{table_id}.yaml")["table"]["values"]
                   if r["temperature"]["value"] == temperature)
        assert row["log10_formation_equilibrium_constant"]["value"] == expected
    pressure = condensation._antoine_psat_pa("Fe", temperature)
    assert math.log10(pressure) == pytest.approx(
        5 + gas_log_kf - (liquid_log_kf or 0), abs=1e-12,
    )


@pytest.mark.parametrize("temperature", [100, 1000, 1184, 1400, 1665, 1800, 1809, 1923, 2300, 3133.345, 4000])
def test_all_wall_readers_share_the_certified_surface(temperature):
    from simulator.battery.enums import Authority, Phase
    from simulator.diagnostic_helpers.species_rail_differential import _evaluate_rail_pressure_Pa

    notices = {}
    pressure = condensation._antoine_psat_pa("Fe", temperature, antoine_extrapolations=notices)
    assert pressure > 0.0
    assert notices == {}
    diagnostic = {}
    driving = condensation._wall_deposition_driving_pressure_pa(
        "Fe", 200.0, temperature, diagnostic_out=diagnostic,
    )
    assert diagnostic["wall_saturation_pressure_pa"] == pressure
    assert diagnostic["wall_saturation_pressure_refused"] is False
    assert driving == max(0.0, 200.0 - pressure)
    assert _evaluate_rail_pressure_Pa("Fe", temperature) == pressure
    inverse = condensation.antoine_dew_temperature_diagnostic("Fe", pressure)
    assert inverse["status"] == "diagnostic_only"
    assert inverse["temperature_K"] == pytest.approx(temperature, abs=1e-9)
    assert inverse["valid_range_K"] == [100.0, 4000.0]
    assert inverse["routing_authority"] is False
    prediction = _static_prediction(temperature, Phase.CR if temperature <= 1809 else Phase.L)
    assert float(prediction.value) == pressure
    assert prediction.authority is Authority.CERTIFIED
    assert prediction.notices == ()


def test_dew_at_c2a_incident_pressure_uses_printed_janaf_bracket():
    from simulator.battery.enums import Authority, Phase

    # Fe-008:2100 K logK=-2.966,2200 K=-2.548. Reciprocal-T
    # interpolation at log10(154.6/100000) gives2136.052149172936 K.
    # K minus273.15 gives1862.902149 C (the brief's~1820 C is the old fit).
    inverse = condensation.antoine_dew_temperature_diagnostic("Fe", 154.6)
    assert inverse["status"] == "diagnostic_only"
    assert inverse["temperature_K"] == pytest.approx(2136.052149172936, abs=1e-9)
    assert inverse["valid_range_K"] == [100.0, 4000.0]
    assert "reason" not in inverse
    assert condensation._antoine_psat_pa("Fe", inverse["temperature_K"]) == pytest.approx(154.6, rel=1e-12)
    prediction = _static_prediction(inverse["temperature_K"], Phase.L)
    assert prediction.authority is Authority.CERTIFIED
    assert prediction.notices == ()


def test_every_segment_and_phase_node_is_continuous():
    from engines.builtin.vapor_pressure import wall_condensation_antoine_coefficients

    row = condensation._species_vapor_data("Fe")
    segments = row["pure_component_antoine"]["segments"]
    assert segments[0]["valid_range_K"][0] == 100
    assert segments[-1]["valid_range_K"][1] == 4000
    phase_nodes = {1042, 1184, 1665, 1809, 3133.345}
    assert phase_nodes <= {s["valid_range_K"][1] for s in segments}
    for left, right in zip(segments, segments[1:]):
        boundary = left["valid_range_K"][1]
        assert boundary == right["valid_range_K"][0]
        lo, hi = math.nextafter(boundary, -math.inf), math.nextafter(boundary, math.inf)
        assert wall_condensation_antoine_coefficients(row, lo)[0] is left
        assert wall_condensation_antoine_coefficients(row, hi)[0] is right
        y1 = math.log10(condensation._antoine_psat_pa("Fe", lo))
        y2 = math.log10(condensation._antoine_psat_pa("Fe", hi))
        assert abs(y2-y1) <= 64 * math.ulp(max(1.0, abs(y1), abs(y2)))
    assert condensation._antoine_psat_pa("Fe", 3133.345) == 100000.0
    for temperature, phase in [(1100, "alpha_solid"), (1400, "gamma_solid"), (1800, "delta_solid"), (1900, "liquid")]:
        assert wall_condensation_antoine_coefficients(row, temperature)[0]["phase"] == phase


def test_lower_refusal_and_upper_flag_are_shared():
    from simulator.battery.enums import Authority, Phase
    from simulator.diagnostic_helpers.species_rail_differential import _evaluate_rail_pressure_Pa

    with pytest.raises(condensation.WallSaturationPressureRefusal):
        condensation._antoine_psat_pa("Fe", 99.999)
    assert _evaluate_rail_pressure_Pa("Fe", 99.999) == "rail_row_absent"
    assert _static_prediction(99.999, Phase.CR).value is None
    notices = {}
    pressure = condensation._antoine_psat_pa("Fe", 4100, antoine_extrapolations=notices)
    assert pressure > 0.0
    assert all(n["authority_level"] == "extrapolated" for n in notices.values())
    prediction = _static_prediction(4100, Phase.L)
    assert float(prediction.value) == pressure
    assert prediction.authority is Authority.EXTRAPOLATED
    assert condensation.antoine_dew_temperature_diagnostic("Fe", pressure)["status"] == "refused_pressure_outside_certified_range"


@pytest.mark.parametrize("temperature, ratio", [(1000, 10**0.264), (1400, 10**0.110), (1800, 10**0.002), (1809, 1.0)])
def test_liquid_reference_uses_shared_fusion_conversion(temperature, ratio):
    from simulator.battery.enums import Authority, Phase

    pressure = condensation._antoine_psat_pa("Fe", temperature)
    prediction = _static_prediction(temperature, Phase.SUPERCOOLED_L)
    assert prediction.value is not None
    assert float(prediction.value) / pressure == pytest.approx(ratio, rel=1e-12)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("phase_mismatch" in n.reason for n in prediction.notices)


def test_missing_liquid_cells_refuse_instead_of_certifying_proxy(monkeypatch):
    from simulator.battery.enums import Phase, RefusalReason
    from simulator.reference_data import janaf

    assert _static_prediction(100, Phase.SUPERCOOLED_L).value is None
    def missing(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(janaf, "load_table_document", missing)
    prediction = _static_prediction(1400, Phase.L)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "solid_liquid_reference_conversion_missing_input"


@pytest.mark.parametrize("temperature, polymorph, certified", [
    (1000, "alpha", True), (1400, "gamma", True), (1800, "delta", True),
    (1000, "bcc", True), (1800, "alpha_delta", True),
    (1400, "alpha", False), (1400, "bcc", False), (1800, "gamma", False),
])
def test_explicit_crystalline_phase_is_not_silently_certified(temperature, polymorph, certified):
    from simulator.battery.enums import Authority, Phase, Polymorph

    prediction = _static_prediction(temperature, Phase.CR, Polymorph(polymorph))
    assert prediction.authority is (Authority.CERTIFIED if certified else Authority.EXTRAPOLATED)
    assert any("phase_mismatch" in n.reason for n in prediction.notices) is not certified


def test_crystal_above_melting_is_flagged():
    from simulator.battery.enums import Authority, Phase

    prediction = _static_prediction(2000, Phase.CR)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("phase_mismatch" in n.reason for n in prediction.notices)


def test_glass_is_not_certified_as_liquid_iron():
    from simulator.battery.enums import Authority, Phase

    prediction = _static_prediction(2000, Phase.GLASS)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("phase_mismatch" in n.reason for n in prediction.notices)


@pytest.mark.parametrize("temperature, pressure_hex", [
    (1400, "0x1.3c937e7b26f48p-7"), (1600, "0x1.7d0efb6f2ca55p-2"),
    (1800, "0x1.97891a48f18eap+2"), (1923, "0x1.b3b1e8c849a46p+4"),
    (2000, "0x1.edfb3ac884c5ep+5"), (2300, "0x1.bc70f4034e423p+9"),
])
def test_non_wall_source_construction_remains_unchanged(temperature, pressure_hex):
    from engines.antoine import _antoine_log10_pressure
    from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients, vapor_pressure_valid_range_K

    row = condensation._species_vapor_data("Fe")
    coefficients, block = vapor_pressure_antoine_coefficients(row, temperature)
    assert coefficients is row["pure_component_antoine"]
    assert (10**_antoine_log10_pressure(coefficients["A"], coefficients["B"], coefficients["C"], temperature)).hex() == pressure_hex
    assert vapor_pressure_valid_range_K(row, block, temperature) is None


def test_independent_near_melting_pressure_anchor_from_corpus():
    from pathlib import Path
    import yaml

    # Ferguson,Nuth & Johnson2004,Table1,row39:1836 K,1.8 Pa.
    # JANAF is compared to this independent measurement, not tuned to it.
    document = yaml.safe_load((Path(__file__).resolve().parents[1] /
        "data/literature/extracts/ferguson-2004-iron-vapor-pressure.yaml").read_text())
    point = next(p for p in document["species"]["Fe"]["observations"][0]["values"]["points"] if p["row"] == 39)
    assert (point["T_K_as_printed"], point["P_Pa"]) == (1836, 1.8)
    # Report the discrepancy explicitly; this is not a measurement-fit gate.
    gap = math.log10(condensation._antoine_psat_pa("Fe", 1836) / point["P_Pa"])
    assert gap == pytest.approx(0.439, abs=0.001)


def test_alcock_independent_melting_pressure_sanity():
    # REF-032 Alcock-Itkin-Horrigan1984 liquid Fe, as reproduced by
    # https://mmrc.caltech.edu/PVD/manuals/Metals%20Vapor%20pressure.pdf:
    # log10(P/atm)=6.347-19574/T. atm*101325 Pa/atm gives Pa.
    # The source states its equations reproduce pressures to5% or better;
    # this independent check does not supply or alter JANAF coefficients.
    anchor_pa = 101325 * 10**(6.347 - 19574/1809)
    assert condensation._antoine_psat_pa("Fe", 1809) == pytest.approx(anchor_pa, rel=0.05)
