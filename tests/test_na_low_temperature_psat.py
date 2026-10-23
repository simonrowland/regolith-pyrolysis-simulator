"""t-1478 wall-pressure pins: unchanged green K and JANAF-grounded Na."""

import pytest

from simulator import condensation


@pytest.mark.parametrize("species, temperature, pressure_hex, refusal, authority", [
    ('Na', 250, '0x1.cdd11dfa3a1a7p-41', None, None),
    ('Na', 300, '0x1.373a95b88d836p-28', None, None),
    ('Na', 328, '0x1.7c0ae93616752p-23', None, None),
    ('Na', 371, '0x1.180f9e4616f84p-16', None, None),
    ('Na', 500, '0x1.872a3b07d0b4fp-4', None, None),
    ('Na', 700, '0x1.a3d1250aaa719p+6', None, None),
    ('Na', 900, '0x1.2c864bb666758p+12', None, None),
    ('K', 250, '0x1.7dc9b633cd045p-26', None, 'extrapolated'),
    ('K', 300, '0x1.44addc4e0b66ap-17', None, 'extrapolated'),
    ('K', 328, '0x1.1ef86e44e9eaap-13', None, 'extrapolated'),
    ('K', 371, '0x1.f8d0d13b98306p-9', None, 'extrapolated'),
    ('K', 500, '0x1.9b74cda9c4db1p+1', None, 'extrapolated'),
    ('K', 700, '0x1.dc398b061300dp+9', None, None),
    ('K', 900, '0x1.777ea4a15c5ebp+14', None, None),
])
def test_wall_pressure_pin(species, temperature, pressure_hex, refusal, authority):
    diagnostic = {}
    condensation._wall_deposition_driving_pressure_pa(
        species, 100.0, temperature, diagnostic_out=diagnostic,
    )
    pressure = diagnostic["wall_saturation_pressure_pa"]
    assert (pressure.hex() if pressure is not None else None) == pressure_hex
    assert diagnostic.get("wall_saturation_pressure_refusal_reason") == refusal
    assert diagnostic.get("wall_saturation_pressure_notice", {}).get("authority_level") == authority


def test_t1464_warm_bin_na_has_executable_certified_input():
    notices = {}
    assert condensation._antoine_psat_pa(
        "Na", 328.453, antoine_extrapolations=notices,
    ) > 0.0
    assert notices == {}


@pytest.mark.parametrize("temperature, log_pressure_pa", [
    # Printed Na-005 log Kf at p0=100000 Pa: -17.086, -13.460, -13.344,
    # -10.680, -8.717, -6.020, -4.239, -2.979, -1.318.
    (250, -12.086), (298.15, -8.460), (300, -8.344), (350, -5.680), (400, -3.717),
    (500, -1.020), (600, 0.761), (700, 2.021), (900, 3.682),
])
def test_printed_janaf_pressure_cells(temperature, log_pressure_pa):
    import math
    from simulator.reference_data.janaf import TABLES_DIR, load_table_document

    document = load_table_document(TABLES_DIR / "Na-005.yaml")
    row = next(r for r in document["table"]["values"]
               if r["temperature"]["value"] == temperature)
    assert row["log10_formation_equilibrium_constant"]["value"] == pytest.approx(
        log_pressure_pa - 5, abs=1e-12,
    )
    assert math.log10(condensation._antoine_psat_pa("Na", temperature)) == pytest.approx(
        log_pressure_pa, abs=1e-12,
    )


def _static_prediction(temperature, phase):
    from decimal import Decimal
    from simulator.battery.enums import Engine, MethodToken
    from simulator.battery.score import predict_with_engine
    from tests.battery import factories as F

    temperature = Decimal(str(temperature))
    experiment = F.tabulation_experiment(
        method=MethodToken.STATIC_EQUILIBRIUM, T_K=temperature,
    )
    identity = F.psat_identity("Na", T_K=temperature, reservoir_phase=phase)
    reference = F.observation(
        "na-solid", experiment.experiment_id, identity, Decimal("1"),
        source_id="printed-janaf",
    )
    return predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, reference, experiment=experiment,
    )


def test_crystalline_reference_is_certified_and_liquid_uses_fusion():
    from simulator.battery.enums import Authority, Phase

    solid = _static_prediction(300, Phase.CR)
    liquid = _static_prediction(300, Phase.SUPERCOOLED_L)
    assert solid.authority is Authority.CERTIFIED
    assert solid.notices == ()
    assert float(solid.value) == pytest.approx(4.528975799e-9, rel=1e-9)
    # Na-003 printed log Kf,l=-0.084 at300 K: liquid/solid ratio 1.213389.
    assert float(liquid.value) / float(solid.value) == pytest.approx(1.213389, rel=1e-6)
    assert liquid.authority is Authority.EXTRAPOLATED
    assert "phase_mismatch" in liquid.notices[0].reason


def test_liquid_fusion_interpolates_at_warm_bin_and_meets_at_melting():
    from engines.builtin.vapor_pressure import _liquid_reference_pressure_from_solid
    from simulator.battery.enums import Phase

    prediction = _static_prediction(328.453, Phase.SUPERCOOLED_L)
    assert prediction.value is not None
    # Bracketed by printed300/350 K liquid formation constants; no invented
    # zero fusion term between the rows.
    solid = condensation._antoine_psat_pa("Na", 328.453)
    assert 1.05 < float(prediction.value) / solid < 1.214
    converted, unavailable = _liquid_reference_pressure_from_solid("Na", 370.98, 1.25)
    assert unavailable is None
    assert converted == 1.25


def test_missing_liquid_fusion_input_is_typed_refusal():
    from simulator.battery.enums import Phase, RefusalReason

    prediction = _static_prediction(250, Phase.SUPERCOOLED_L)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.refusal_detail["reason"] == "solid_liquid_reference_conversion_missing_input"


def test_fusion_table_absent_refuses_instead_of_certifying_proxy(monkeypatch):
    from simulator.battery.enums import Phase, RefusalReason
    from simulator.reference_data import janaf

    def missing_table(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(janaf, "load_table_document", missing_table)
    prediction = _static_prediction(300, Phase.L)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE


def test_liquid_reference_does_not_certify_crystal_above_melting():
    from simulator.battery.enums import Authority, Phase

    assert _static_prediction(500, Phase.L).authority is Authority.CERTIFIED
    crystal = _static_prediction(500, Phase.CR)
    assert crystal.authority is Authority.EXTRAPOLATED
    assert "phase_mismatch" in crystal.notices[0].reason


def test_melting_handover_and_independent_literature_anchor():
    from pathlib import Path
    import yaml

    # Honig & Kramer (1969), RCA Rev30:285, reproduced in corpus extract.
    # Independent phase-boundary anchor, not a pressure measurement.
    document = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data/literature/extracts/janaf-4th.yaml").read_text()
    )
    observations = document["species"]["Na"]["observations"]
    anchor = next(r for r in observations if r["observation_id"] == "Na_triple_point_temperature")
    assert anchor["values"]["value_K"] == 370.98
    assert "Honig" in anchor["values"]["citation"]["primary"]
    left = condensation._antoine_psat_pa("Na", 370.98 - 1e-6)
    right = condensation._antoine_psat_pa("Na", 370.98 + 1e-6)
    assert right == pytest.approx(left, rel=1e-6)


def test_original_high_temperature_fit_and_handover_uncertainty():
    import math
    from engines.builtin.vapor_pressure import wall_condensation_antoine_coefficients

    # Independent output pins from REF-041 at924 and1118 K.
    for temperature in (924., 1000., 1118.):
        coefficients, _ = wall_condensation_antoine_coefficients(
            condensation._species_vapor_data("Na"), temperature,
        )
        assert (coefficients["A"], coefficients["B"], coefficients["C"]) == (
            7.46077, 1873.728, -416.372,
        )
    low = condensation._antoine_psat_pa("Na", 924 - 1e-6)
    high = condensation._antoine_psat_pa("Na", 924)
    assert abs(math.log10(low / high)) < 0.10


def test_original_lower_refusal_and_source_fit_are_preserved():
    from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients

    with pytest.raises(condensation.WallSaturationPressureRefusal):
        condensation._antoine_psat_pa("Na", 249.999)
    row = condensation._species_vapor_data("Na")
    for temperature in (250, 328, 500, 900, 1000, 1700):
        coefficients, block = vapor_pressure_antoine_coefficients(row, temperature)
        assert block == "antoine"
        assert coefficients is row["antoine"]


def test_hot_liquid_extrapolation_does_not_claim_phase_mismatch():
    from simulator.battery.enums import Authority, Phase

    prediction = _static_prediction(1200, Phase.L)
    assert prediction.authority is Authority.EXTRAPOLATED
    assert any("extrapolated" in n.reason for n in prediction.notices)
    assert not any("phase_mismatch" in n.reason for n in prediction.notices)


@pytest.mark.parametrize("species, temperature", [("Si", 1000), ("Mn", 1300)])
def test_other_elements_static_pressure_behavior_is_preserved(species, temperature):
    from decimal import Decimal
    from simulator.battery.enums import Engine, MethodToken, Phase
    from simulator.battery.score import predict_with_engine
    from tests.battery import factories as F

    temperature = Decimal(temperature)
    experiment = F.tabulation_experiment(
        method=MethodToken.STATIC_EQUILIBRIUM, T_K=temperature,
    )
    identity = F.psat_identity(species, T_K=temperature, reservoir_phase=Phase.L)
    reference = F.observation(
        "unchanged-solid", experiment.experiment_id, identity, Decimal("1"),
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, reference, experiment=experiment)
    assert prediction.value is not None
    assert prediction.refusal_reason is None
