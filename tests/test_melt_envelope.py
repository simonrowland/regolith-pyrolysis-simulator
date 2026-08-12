"""Tests for HT-C3 / HT-PLAN §H2 melt-leg envelope instrument."""

from __future__ import annotations

import math

import pytest

from simulator.melt_backend.melt_envelope import (
    MELT_ENVELOPE_CONSTANTS,
    R_J_MOL_K,
    MeltEnvelopeValidationError,
    MeltExtrapolationEnvelope,
    UnknownMeltModelIdError,
    consume_melt_extrapolation_envelope,
    melt_extrapolation_diagnostic,
    melt_extrapolation_envelope,
)

# Initial registered model (HT1-audit §2.2 MELTS liquid calib top = 1700 K).
_MODEL = "MELTS-v1.0"
_T_CALIB = MELT_ENVELOPE_CONSTANTS[_MODEL]["T_calib_max_K"]
_S_EX = MELT_ENVELOPE_CONSTANTS[_MODEL]["S_ex_bound_J_molK"]
_VERSION = MELT_ENVELOPE_CONSTANTS[_MODEL]["constants_version"]


def test_zero_inside_calibration():
    """σ and ΔT are exactly zero for every T <= T_calib_max."""
    for t in (_T_CALIB - 200.0, _T_CALIB - 1.0, _T_CALIB):
        env = melt_extrapolation_envelope(t, _MODEL)
        assert env.melt_model_extrapolation_K == 0.0
        assert env.melt_extrap_sigma_mu_J_mol == 0.0
        assert env.melt_extrap_sigma_log10_P == 0.0
        assert env.melt_extrap_status == "in_calibration"
        assert env.T_calib_max_K == _T_CALIB
        assert env.melt_model_id == _MODEL


def test_linear_growth_above_calibration():
    """σ_μ grows linearly with ΔT; S_ex_bound is the slope."""
    deltas = (1.0, 50.0, 250.0, 500.0, 800.0)
    for dT in deltas:
        t = _T_CALIB + dT
        env = melt_extrapolation_envelope(t, _MODEL)
        assert env.melt_model_extrapolation_K == pytest.approx(dT)
        assert env.melt_extrap_sigma_mu_J_mol == pytest.approx(_S_EX * dT)
        assert env.melt_extrap_status == "extrapolated"
        expected_log10 = (_S_EX * dT) / (math.log(10.0) * R_J_MOL_K * t)
        assert env.melt_extrap_sigma_log10_P == pytest.approx(expected_log10)


def test_worked_numeric_check_sigma_mu_at_plus_250K():
    """HT-PLAN sanity: at T_calib+250 K with S=5 → σ_μ = 1250 J/mol."""
    # Algebra of the first formula only — independent of which T_calib is used.
    assert _S_EX == 5.0
    t = _T_CALIB + 250.0
    env = melt_extrapolation_envelope(t, _MODEL)
    assert env.melt_extrap_sigma_mu_J_mol == pytest.approx(1250.0)
    assert env.melt_model_extrapolation_K == pytest.approx(250.0)


def test_worked_numeric_check_sigma_log10P_projection_at_2200K():
    """Projection algebra: 1250 J/mol at 2200 K → σ_log10P ≈ 0.0297 dex.

    Hand check from HT-C3 brief / module derivation comment::

        1250 / (2.302585 * 8.314462618 * 2200) ≈ 0.029678 ≈ 0.0297
    """
    sigma_mu = 1250.0
    t = 2200.0
    expected = sigma_mu / (math.log(10.0) * R_J_MOL_K * t)
    assert expected == pytest.approx(0.029678, abs=5e-7)
    assert expected == pytest.approx(0.0297, abs=5e-5)

    # Live instrument at the T that produces σ_μ = 1250 for the registered model
    # (T = T_calib + 250). Projection uses that T, not a fixed 2200 K — the
    # 2200 K figure above is the pure algebra check of the projection formula.
    env_at_plus_250 = melt_extrapolation_envelope(_T_CALIB + 250.0, _MODEL)
    assert env_at_plus_250.melt_extrap_sigma_mu_J_mol == pytest.approx(1250.0)

    # If T_calib is 1950, plus-250 lands on 2200 and matches the hand figure
    # exactly; with T_calib=1700 the live σ_log10P at 2200 uses σ_μ=2500.
    # Always assert the projection formula identity at T=2200 via direct call
    # when the registered model places 2200 above calibration (it does).
    env_2200 = melt_extrapolation_envelope(2200.0, _MODEL)
    assert env_2200.melt_extrap_status == "extrapolated"
    dT = 2200.0 - _T_CALIB
    assert env_2200.melt_extrap_sigma_mu_J_mol == pytest.approx(_S_EX * dT)
    assert env_2200.melt_extrap_sigma_log10_P == pytest.approx(
        (_S_EX * dT) / (math.log(10.0) * R_J_MOL_K * 2200.0)
    )


def test_status_transitions_at_calib_boundary():
    just_below = melt_extrapolation_envelope(_T_CALIB - 1e-9, _MODEL)
    at = melt_extrapolation_envelope(_T_CALIB, _MODEL)
    just_above = melt_extrapolation_envelope(_T_CALIB + 1e-9, _MODEL)

    assert just_below.melt_extrap_status == "in_calibration"
    assert at.melt_extrap_status == "in_calibration"
    assert just_above.melt_extrap_status == "extrapolated"
    assert just_above.melt_model_extrapolation_K == pytest.approx(1e-9)
    assert just_above.melt_extrap_sigma_mu_J_mol == pytest.approx(_S_EX * 1e-9)


def test_unknown_melt_model_id_raises_typed_error():
    with pytest.raises(UnknownMeltModelIdError) as ei:
        melt_extrapolation_envelope(1800.0, "not-a-registered-melt-model")
    assert ei.value.melt_model_id == "not-a-registered-melt-model"
    assert "no silent default" in str(ei.value).lower() or "No silent default" in str(
        ei.value
    )


def test_constants_version_present_in_output():
    env = melt_extrapolation_envelope(_T_CALIB, _MODEL)
    assert isinstance(env, MeltExtrapolationEnvelope)
    assert env.constants_version == _VERSION
    assert env.constants_version  # non-empty
    # Also present when extrapolated
    env2 = melt_extrapolation_envelope(_T_CALIB + 100.0, _MODEL)
    assert env2.constants_version == _VERSION


def test_envelope_is_frozen():
    env = melt_extrapolation_envelope(_T_CALIB, _MODEL)
    with pytest.raises(Exception):
        env.melt_extrap_status = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize("temperature_K", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_temperature_is_marked_out_of_domain(temperature_K: float):
    envelope = melt_extrapolation_envelope(temperature_K, _MODEL)

    assert envelope.melt_extrap_status == "out_of_domain"
    assert envelope.melt_model_extrapolation_K == 0.0
    assert envelope.melt_extrap_sigma_mu_J_mol == 0.0
    assert envelope.melt_extrap_sigma_log10_P == 0.0
    assert all(
        math.isfinite(value)
        for value in (
            envelope.T_calib_max_K,
            envelope.melt_model_extrapolation_K,
            envelope.melt_extrap_sigma_mu_J_mol,
            envelope.melt_extrap_sigma_log10_P,
        )
    )


def test_typed_consumer_semantically_validates_every_envelope_field():
    temperature_K = _T_CALIB + 100.0
    diagnostic = melt_extrapolation_diagnostic(temperature_K, _MODEL)

    consumed = consume_melt_extrapolation_envelope(
        diagnostic,
        temperature_K=temperature_K,
    )

    assert consumed == melt_extrapolation_envelope(temperature_K, _MODEL)

    corrupt_values = {
        "melt_model_id": "not-a-registered-model",
        "T_calib_max_K": _T_CALIB + 1.0,
        "melt_model_extrapolation_K": 99.0,
        "melt_extrap_sigma_mu_J_mol": 99.0,
        "melt_extrap_sigma_log10_P": 99.0,
        "melt_extrap_status": "in_calibration",
        "constants_version": "stale-constants",
    }
    for field, corrupt_value in corrupt_values.items():
        corrupt = dict(diagnostic)
        corrupt[field] = corrupt_value
        with pytest.raises(MeltEnvelopeValidationError, match=field):
            consume_melt_extrapolation_envelope(
                corrupt,
                temperature_K=temperature_K,
            )


def test_typed_consumer_rejects_partial_envelope_instead_of_dropping_it():
    diagnostic = melt_extrapolation_diagnostic(_T_CALIB, _MODEL)
    diagnostic.pop("constants_version")

    with pytest.raises(MeltEnvelopeValidationError, match="partial"):
        consume_melt_extrapolation_envelope(
            diagnostic,
            temperature_K=_T_CALIB,
        )


def test_typed_consumer_classifies_marker_only_envelope_as_partial():
    with pytest.raises(MeltEnvelopeValidationError, match="partial"):
        consume_melt_extrapolation_envelope(
            {"instrument_status": "status_bearing_non_authoritative"},
            temperature_K=_T_CALIB,
        )


def test_r_constant_matches_ht_plan():
    assert R_J_MOL_K == 8.314462618
