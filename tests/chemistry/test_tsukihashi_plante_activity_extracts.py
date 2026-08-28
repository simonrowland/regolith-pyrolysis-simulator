"""Transcription pins for Tsukihashi & Sano 1985 and Plante 1979 activity rows.

Quote the source table for spot-check; require the six-criterion payload
(numeric value, T, composition, pO2, silicate-melt class, liquid_melt form).
Do not tune residuals — a large residual is a battery result.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.extract_reproduction import (
    evaluate_observation,
    load_adopted_observations,
    load_vapor_pressure_data,
)

REPO = Path(__file__).resolve().parents[2]
EXTRACTS = REPO / "data" / "literature" / "extracts"
TSUKI = EXTRACTS / "ts1985.yaml"
PLANTE = EXTRACTS / "kems-042-plante-1979.yaml"


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _obs_by_id(doc: dict, observation_id: str) -> dict:
    for species_block in doc["species"].values():
        for obs in species_block.get("observations") or []:
            if obs.get("observation_id") == observation_id:
                return obs
    raise AssertionError(f"missing observation_id {observation_id}")


def _six_criteria(obs: dict) -> None:
    values = obs["values"]
    assert values.get("activity") is not None and float(values["activity"]) > 0.0
    assert obs.get("T_range_K") and len(obs["T_range_K"]) == 2
    assert values.get("composition_mol") or values.get("composition_wt_pct")
    assert float(values["pO2_bar"]) > 0.0
    assert values.get("system_class") == "silicate_melt"
    assert obs["condensed_form"]["state"] == "liquid_melt"
    assert obs.get("locator")


def test_tsukihashi_table2_quoted_coefficients_and_evaluated_activity() -> None:
    """Table 2 p.821: X_Na2O=0.40, A=(-1.46±0.02)×10^4, B=3.03±0.06."""

    doc = _load(TSUKI)
    obs = _obs_by_id(doc, "ts1985_na2o_table2_X0p40_T1200C")
    _six_criteria(obs)
    values = obs["values"]
    assert values["X_Na2O_as_published"] == pytest.approx(0.40)
    assert values["A"] == pytest.approx(-14600.0)
    assert values["B"] == pytest.approx(3.03)
    assert values["T_C"] == pytest.approx(1200.0)
    # log10(a) = A/T + B = -14600/1473.15 + 3.03
    t_k = 1473.15
    expected = 10.0 ** (-14600.0 / t_k + 3.03)
    assert values["activity"] == pytest.approx(expected, rel=1e-9)
    assert values["activity"] == pytest.approx(1.316026e-07, rel=1e-6)


def test_tsukihashi_sio2_gibbs_duhem_start_quoted() -> None:
    """p.821: a_SiO2 = 6.01×10^{-3} at 1200 °C, X_Na2O=0.500 (tridymite)."""

    doc = _load(TSUKI)
    obs = _obs_by_id(doc, "ts1985_sio2_gibbs_duhem_1200C_X0500")
    _six_criteria(obs)
    assert obs["values"]["activity"] == pytest.approx(0.00601)
    assert obs["values"]["activity_as_printed"] == "6.01 × 10^{-3}"
    assert obs["values"]["composition_mol"] == {"Na2O": 0.5, "SiO2": 0.5}


def test_plante_table2_series1104_first_row_quoted() -> None:
    """Table 2 p.276 Series 1104: T=1302 K, 43.94 wt% K2O, P_K=6.91E-7 atm."""

    doc = _load(PLANTE)
    obs = _obs_by_id(doc, "plante1979_table2_k2o_s1104_000_1302K")
    _six_criteria(obs)
    values = obs["values"]
    assert values["P_K_atm_as_published"] == pytest.approx(6.91e-7)
    assert values["composition_wt_pct"]["K2O"] == pytest.approx(43.94)
    assert values["po2_over_pK_as_published"] == pytest.approx(0.226)
    p_k = 6.91e-7
    activity = (p_k ** 2) * ((0.226 * p_k) ** 0.5)
    assert values["activity"] == pytest.approx(activity, rel=1e-9)


def test_plante_omits_two_phase_and_series_1214() -> None:
    doc = _load(PLANTE)
    ids = [
        obs["observation_id"]
        for obs in doc["species"]["K2O"]["observations"]
    ]
    assert not any("s1214" in i for i in ids)
    assert len(ids) == 162
    for obs in doc["species"]["K2O"]["observations"]:
        _six_criteria(obs)


def test_new_activity_rows_are_not_missing_numeric() -> None:
    """Numeric activity is present. T-domain skips are engine results, not missing data.

    Na2O/K2O table gammas are certified at a single T (1673 K / 1500 K).
    Tsukihashi and Plante points sit outside those certificates, so the
    battery records assumed-input rather than a residual pin. That is a
    real engine limitation, not a reason to drop the rows. SiO2 has no
    single-T certificate and must remain comparable.
    """

    wanted = {
        "ts1985_na2o_table2_X0p40_T1200C",
        "ts1985_sio2_gibbs_duhem_1200C_X0500",
        "plante1979_table2_k2o_s1104_000_1302K",
    }
    adopted = {
        obs.observation_id: obs
        for obs in load_adopted_observations()
        if obs.observation_id in wanted
    }
    assert wanted <= set(adopted), f"not adopted: {wanted - set(adopted)}"
    vp = load_vapor_pressure_data()
    for obs_id, obs in adopted.items():
        evaluation = evaluate_observation(obs, vapor_pressure_data=vp)
        reason = evaluation.skip_reason or ""
        assert "missing_numeric_activity" not in reason, (
            f"{obs_id} still refused as missing_numeric_activity: {reason}"
        )
        assert evaluation.records, f"{obs_id} silent"
        rec = evaluation.records[0]
        assert rec.expected_value is not None and rec.expected_value > 0.0
        if obs_id == "ts1985_sio2_gibbs_duhem_1200C_X0500":
            comparable = [r for r in evaluation.records if r.status in {"match", "mismatch"}]
            assert comparable, (
                f"{obs_id} must be comparable; skip={reason!r} "
                f"statuses={[r.status for r in evaluation.records]}"
            )
        else:
            # Out-of-domain constant-gamma certificate. Residual still computed.
            assert rec.actual_value is not None
            assert "documented_melt_activity_coefficient" in reason
