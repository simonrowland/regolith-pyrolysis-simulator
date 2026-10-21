"""Independent low-T pure-solid pressure evidence, separate from melt release."""
import math
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    VaporPressureRangeError,
    require_antoine_source_certified_temperature,
    vapor_pressure_antoine_coefficients,
    vapor_pressure_valid_range_K,
    wall_condensation_antoine_coefficients,
)
from simulator.condensation import (
    WallSaturationPressureRefusal,
    _antoine_psat_pa,
    _species_vapor_data,
    _wall_deposition_driving_pressure_pa,
)
from simulator.battery.enums import Authority, Engine, MethodToken, NoticeKind, Phase
from simulator.battery.score import predict_with_engine
from tests.battery import factories as F

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("species", ["Mg", "Ca"])
@pytest.mark.parametrize("temperature", [500.0, 600.0, 650.0, 700.0, 1000.0])
def test_surface_evidence_preserves_source_fit_selection(species, temperature):
    data = _species_vapor_data(species)
    coefficients, block = vapor_pressure_antoine_coefficients(data, temperature)
    # Captured baseline selector: Ca below its pole used the legacy fallback;
    # every other case used the original flat pure fit. The solid-surface
    # bands must never change the source rail's domain/early-skip behavior.
    expected_block = (
        "antoine" if species == "Ca" and temperature == 500.0
        else "pure_component_antoine"
    )
    assert block == expected_block
    assert coefficients is data[expected_block]
    expected_band = (
        [701, 1361] if species == "Mg" else
        [1115, 1757] if temperature == 500.0 else [1254, 1712]
    )
    assert list(vapor_pressure_valid_range_K(data, block, temperature)) == expected_band


@pytest.mark.parametrize(("species", "temperature", "pressure"), [
    # Gilbreath's printed solid-Mg equation, independently converted torr -> Pa.
    ("Mg", 500.0, 4.06446e-5), ("Mg", 600.0, 0.0134618),
    # JANAF Ca-006 formation constants at p0=1 bar, stable alpha Ca(s).
    ("Ca", 500.0, 2.04174e-8), ("Ca", 600.0, 2.38232e-5),
])
def test_published_solid_surface_anchors(species, temperature, pressure):
    notices = {}
    actual = _antoine_psat_pa(
        species, temperature, enforce_hot_train_applicability=False,
        antoine_extrapolations=notices)
    assert actual == pytest.approx(pressure, rel=2e-6)
    assert notices == {}


@pytest.mark.parametrize(("species", "temperature", "expected"), [
    ("Mg", "500", 4.06446e-5), ("Ca", "600", 2.38232e-5),
])
@pytest.mark.parametrize("reservoir_phase", [Phase.CR, Phase.L, Phase.SUPERCOOLED_L])
def test_static_pressure_scoring_uses_solid_surface_band(
    species, temperature, expected, reservoir_phase,
):
    temperature = Decimal(temperature)
    experiment = F.tabulation_experiment(
        method=MethodToken.STATIC_EQUILIBRIUM, T_K=temperature)
    identity = F.psat_identity(species, T_K=temperature, reservoir_phase=reservoir_phase)
    reference = F.observation(
        "solid-surface", experiment.experiment_id, identity,
        Decimal(str(expected)), source_id="primary-solid-reference")
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, reference, experiment=experiment)
    assert float(prediction.value) == pytest.approx(expected, rel=2e-6)
    assert prediction.identity is identity
    if reservoir_phase is Phase.CR:
        assert prediction.authority is Authority.CERTIFIED
        assert prediction.notices == ()
    else:
        assert prediction.authority is Authority.EXTRAPOLATED
        notice, = prediction.notices
        assert notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        assert "phase_mismatch" in notice.reason
        assert notice.source == ("solid" if species == "Mg" else "alpha_solid")
        assert notice.destination == reservoir_phase.value
        assert notice.authority is Authority.EXTRAPOLATED
    low, high = prediction.certified_band["temperature_K"]
    assert low <= temperature <= high


@pytest.mark.parametrize("temperature", [496.0, 500.0, 600.0, 658.0])
def test_mg_certified_band_and_solid_reference(temperature):
    data = _species_vapor_data("Mg")
    coefficients, block = wall_condensation_antoine_coefficients(data, temperature)
    assert coefficients["phase"] == "solid"
    assert coefficients["source_certified_range_K"] == [496, 658]
    assert "REF-060" in coefficients["source"]
    # Source-release domain remains unchanged; only the pure-surface band admits it.
    with pytest.raises(VaporPressureRangeError):
        require_antoine_source_certified_temperature(
            "Mg", data, block, temperature, consumer="evaporation")
    notices = {}
    assert _antoine_psat_pa(
        "Mg", temperature, antoine_extrapolations=notices) > 0
    assert notices == {}


@pytest.mark.parametrize("temperature", [495.999, 658.001])
def test_mg_outside_solid_band_is_flagged(temperature):
    notices = {}
    assert _antoine_psat_pa(
        "Mg", temperature, antoine_extrapolations=notices) > 0.0
    assert notices
    assert all(n["authority_level"] == "extrapolated" for n in notices.values())


def test_mg_wall_consumer_receives_certified_solid_pressure():
    diagnostic = {}
    _wall_deposition_driving_pressure_pa("Mg", 1.0, 500.0, diagnostic_out=diagnostic)
    assert diagnostic["wall_saturation_pressure_pa"] == pytest.approx(4.06446e-5, rel=2e-6)
    assert diagnostic["wall_saturation_pressure_refused"] is False
    assert "wall_saturation_pressure_notice" not in diagnostic


def test_ca_table_matches_published_compilation_and_pressure_units():
    evidence = yaml.safe_load((ROOT / "validation-data/pin-evidence/crown_low_t_solid_psat.yaml").read_text())
    calcium = evidence["calcium"]
    table = yaml.safe_load((ROOT / calcium["compilation"]).read_text())
    rows = {r["temperature"]["value"]: r for r in table["table"]["values"]}
    assert calcium["valid_range_K"] == [400, 700]
    assert calcium["alpha_beta_transition_K"] == 716
    for temperature, log_kf in calcium["rows"]:
        assert rows[temperature]["log10_formation_equilibrium_constant"]["value"] == log_kf
        pressure = _antoine_psat_pa(
            "Ca", temperature, enforce_hot_train_applicability=False)
        # Independent source table in log units checks p0=1 bar, not 1 atm
        # or 1 Pa. The production evaluator only sees A/B/C segments.
        assert math.log10(pressure) == pytest.approx(5.0 + log_kf, abs=1e-12)


@pytest.mark.parametrize(("low", "high"), [(400., 450.), (450., 500.), (500., 600.), (600., 700.)])
def test_ca_log_pressure_is_linear_in_inverse_temperature(low, high):
    p1 = _antoine_psat_pa("Ca", low, enforce_hot_train_applicability=False)
    p2 = _antoine_psat_pa("Ca", high, enforce_hot_train_applicability=False)
    harmonic_midpoint = 2.0 * low * high / (low + high)
    mid = _antoine_psat_pa("Ca", harmonic_midpoint, enforce_hot_train_applicability=False)
    assert mid == pytest.approx(math.sqrt(p1 * p2), rel=1e-12)


@pytest.mark.parametrize("temperature", [594.590, 594.591, 594.592, 600.0])
def test_ca_solid_selection_bypasses_hartmann_schneider_pole(temperature):
    coefficients, _ = wall_condensation_antoine_coefficients(
        _species_vapor_data("Ca"), temperature)
    assert coefficients["phase"] == "alpha_solid"
    assert coefficients["C"] == 0
    assert _antoine_psat_pa("Ca", temperature, enforce_hot_train_applicability=False) > 0


@pytest.mark.parametrize("temperature", [700.001, 716., 773., 1000., 1253.999])
def test_ca_uncovered_lower_hartmann_branch_refuses(temperature):
    with pytest.raises(WallSaturationPressureRefusal):
        _antoine_psat_pa("Ca", temperature, enforce_hot_train_applicability=False)


def test_ca_below_table_is_predict_and_flag():
    notices = {}
    assert _antoine_psat_pa(
        "Ca", 399.9, enforce_hot_train_applicability=False,
        antoine_extrapolations=notices) > 0
    assert notices["Ca"]["authority_level"] == "extrapolated"
