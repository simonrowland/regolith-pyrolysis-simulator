"""Regression pin for the 7d42b4f Na/K wall dew-point claim.

Claim under test (commit 7d42b4f message): at a physically admissible partial
pressure with 10 mbar total, Na and K dew points sit below ~651 C and ~429 C,
so on 1500-1650 C walls they cannot condense at all. These tests defend the
MECHANISM (dew-point relation: p_i < P_sat at the WALL temperature), not any
specific flux number, and ground it on external physics per AGENTS.md
invariant 7 — not on the simulator's own output:

- External anchors: Na boils at 883 C, K at 759 C at 1 atm (NIST/CRC normal
  boiling points). Vapor pressure is monotonically increasing in T
  (Clausius-Clapeyron, dH_vap > 0), so any wall above the normal boiling
  point has P_sat > 1 atm >= p_i for ANY admissible p_i <= P_total. This
  argument alone settles the sign; the Antoine sidecar only has to be sane.
- The Antoine coefficients themselves come from the literature sidecar
  data/vapor_pressures.yaml::pure_component_antoine (NIST SRD 69 fits),
  evaluated here by hand, independent of the simulator's code path.

Dew-point derivation (premise -> Antoine inversion -> units -> sanity):

  Premise:   ideal-gas mixture at P_total = 10 mbar = 1000 Pa, so by Dalton
             p_i <= P_total = 1000 Pa for every species i. The dew point at
             the MAX admissible p_i = 1000 Pa is the highest possible dew
             point; if that is below the wall band, every admissible mixture
             is undersaturated on those walls.
  Relation:  the sidecar Antoine form is log10(P_sat / Pa) = A - B/(T_K + C)
             (simulator/condensation.py::_antoine_psat_pa returns
             10.0 ** (A - B / (T_K + C)), P in Pa, T in K).
  Inversion: T_dew_K(p) = B / (A - log10(p / Pa)) - C.
  Units:     A, B, C as tabulated are already in the Pa/K convention of the
             sidecar (the NIST bar fits were converted by A += 5, per the
             sidecar source strings), so p enters in Pa and T_dew comes out
             in K; C carries the Kelvin offset of the shifted Antoine form.
  Sanity:    inverting at p = 101325 Pa must reproduce the external normal
             boiling points (Na 883 C, K 759 C) — checked below. At
             p = 1000 Pa the inversion gives Na 836.4 K = 563.3 C and
             K 702.4 K = 429.2 C, both far below the 1500-1650 C wall band
             (and consistent with the commit's "~651 C / ~429 C" wording).

Mirrored pattern: tests/test_coating_rate.py:364-370 asserts mol_s == 0.0
AND p_i <= P_total AND supersaturated is False — a mechanism assertion, not
a bare zero-check. The integration test below keeps that shape and adds the
dew-point fields the shadow record already exposes
(wall_saturation_pressure_pa / _refused).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.condensation import (
    _sticking_reactivity_class,
    _try_antoine_psat_pa,
    _wall_deposition_driving_pressure_pa,
)
from simulator.runner import PyrolysisRun

PA_PER_ATM = 101_325.0
MBAR_TO_PA = 100.0
CELSIUS_TO_KELVIN = 273.15

# Premise of the 7d42b4f claim: 10 mbar total system pressure.
TOTAL_PRESSURE_PA = 10.0 * MBAR_TO_PA  # 1000 Pa

# The run's wall band from the claim: 1500-1650 C.
WALL_BAND_C = (1500.0, 1550.0, 1575.0, 1600.0, 1650.0)
WALL_BAND_MIN_K = min(WALL_BAND_C) + CELSIUS_TO_KELVIN

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# External physical reality (NIST/CRC normal boiling points at 1 atm).
ALKALI_EXTERNAL_ANCHORS = {
    "Na": {"normal_boiling_point_C": 883.0},
    "K": {"normal_boiling_point_C": 759.0},
}


def _vapor_pressure_data() -> dict:
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        return yaml.safe_load(handle)


def _antoine_coefficients(species: str) -> dict:
    return _vapor_pressure_data()["metals"][species]["pure_component_antoine"]


def _antoine_psat_pa(coeff: dict, temperature_K: float) -> float:
    # Hand evaluation of the sidecar fit — deliberately NOT the simulator's
    # code path (invariant 7: ground truth, not self-parity).
    return 10.0 ** (
        float(coeff["A"])
        - float(coeff["B"]) / (float(temperature_K) + float(coeff.get("C", 0.0)))
    )


def _antoine_dew_point_K(coeff: dict, pressure_pa: float) -> float:
    # Inversion of log10(P/Pa) = A - B/(T_K + C): T_K = B/(A - log10(P/Pa)) - C.
    return float(coeff["B"]) / (
        float(coeff["A"]) - math.log10(float(pressure_pa))
    ) - float(coeff.get("C", 0.0))


@pytest.mark.parametrize("species", ["Na", "K"])
def test_antoine_sidecar_recovers_external_normal_boiling_point(
    species: str,
) -> None:
    # Grounding for everything else in this file: the sidecar coefficients,
    # inverted at 1 atm, must land on the externally known normal boiling
    # point (Na 883 C, K 759 C). 5% in K covers the short extrapolation of
    # the Na NIST fit (certified 924-1118 K; its boiling point is 1156 K)
    # and the in-range K fit (certified 679.4-1033 K; boiling 1032 K).
    coeff = _antoine_coefficients(species)
    external_boiling_K = (
        ALKALI_EXTERNAL_ANCHORS[species]["normal_boiling_point_C"]
        + CELSIUS_TO_KELVIN
    )

    assert coeff["source"]
    assert _antoine_dew_point_K(coeff, PA_PER_ATM) == pytest.approx(
        external_boiling_K,
        rel=0.05,
    )


@pytest.mark.parametrize("species", ["Na", "K"])
def test_dew_point_at_max_admissible_partial_pressure_is_below_wall_band(
    species: str,
) -> None:
    coeff = _antoine_coefficients(species)

    # Highest admissible dew point: p_i = P_total = 10 mbar = 1000 Pa.
    # Inversion: T_dew = B/(A - log10(1000 Pa)) - C  (Pa in, K out).
    #   Na: 1873.728/(7.460770 - 3) + 416.372 = 836.4 K = 563.3 C
    #   K:  4691.58/(9.457180 - 3) - 24.195   = 702.4 K = 429.2 C
    t_dew_K = _antoine_dew_point_K(coeff, TOTAL_PRESSURE_PA)

    # Mechanism, not the number: the dew point sits hundreds of K below the
    # coolest wall in the band (actual margins: Na 937 K, K 1071 K).
    assert t_dew_K < WALL_BAND_MIN_K - 500.0
    # External cross-check needing no Antoine extrapolation at all: the wall
    # band lies above the normal boiling point, so P_sat(T_wall) > 1 atm
    # >= p_i by monotonicity of vapor pressure alone.
    external_boiling_K = (
        ALKALI_EXTERNAL_ANCHORS[species]["normal_boiling_point_C"]
        + CELSIUS_TO_KELVIN
    )
    assert WALL_BAND_MIN_K > external_boiling_K


@pytest.mark.parametrize("species", ["Na", "K"])
@pytest.mark.parametrize("wall_temperature_C", WALL_BAND_C)
def test_wall_driving_force_is_zero_via_dewpoint_relation(
    species: str,
    wall_temperature_C: float,
) -> None:
    vapor_data = _vapor_pressure_data()
    coeff = vapor_data["metals"][species]["pure_component_antoine"]
    wall_temperature_K = wall_temperature_C + CELSIUS_TO_KELVIN
    # Worst case over all admissible mixtures: the driving force
    # max(0, p_i - P_sat) is monotonically nondecreasing in p_i, so proving
    # zero at p_i = P_total proves it for every p_i <= P_total.
    p_i_pa = TOTAL_PRESSURE_PA

    driving_pa = _wall_deposition_driving_pressure_pa(
        species,
        p_i_pa,
        wall_temperature_K,
        vapor_pressure_data=vapor_data,
    )

    assert driving_pa == 0.0

    # The zero must come from the dew-point relation, not from any guard,
    # filter, or clamp. Eliminate each alternative zero source in
    # _wall_deposition_driving_pressure_pa (simulator/condensation.py:4729):
    # (a) not the non-positive-pressure early return — p_i is finite, > 0;
    assert math.isfinite(p_i_pa) and p_i_pa > 0.0
    # (b) not the fail-closed P_sat-is-None branch — the Antoine evaluation
    #     at the wall temperature succeeds and is finite;
    p_sat_pa, refused = _try_antoine_psat_pa(
        species,
        wall_temperature_K,
        vapor_pressure_data=vapor_data,
    )
    assert refused is False
    assert p_sat_pa is not None and math.isfinite(p_sat_pa)
    # (c) not a reactive-product backstop or a species-name branch — Na/K are
    #     physisorbing and take the generic path (the only name branches are
    #     the CrO2/SiO guards at condensation.py:4761-4775, and both raise
    #     rather than suppress);
    assert _sticking_reactivity_class(species) != "reactive"
    # (d) so the only remaining branch is max(0.0, p_i - P_sat_pa), and it is
    #     zero exactly because p_i < P_sat at the WALL temperature. The
    #     undersaturation is verified against the hand-evaluated sidecar fit,
    #     and the simulator's P_sat is that same published fit, not a
    #     substitute surface.
    assert _antoine_psat_pa(coeff, wall_temperature_K) > p_i_pa
    assert p_sat_pa == pytest.approx(
        _antoine_psat_pa(coeff, wall_temperature_K),
        rel=1e-9,
    )
    assert p_sat_pa > p_i_pa


@pytest.mark.parametrize("species", ["Na", "K"])
def test_wall_driving_force_turns_positive_below_the_dew_point(
    species: str,
) -> None:
    # Contrast probe: if the zero in the band came from an allowlist,
    # species filter, or clamp, the function would return zero here too.
    # Instead, 20 K below the 1000 Pa dew point the same generic path must
    # return the full undersaturation gap p_i - P_sat(T) > 0 — proving the
    # in-band zero is the dew-point relation evaluating live for Na/K.
    vapor_data = _vapor_pressure_data()
    coeff = vapor_data["metals"][species]["pure_component_antoine"]
    p_i_pa = TOTAL_PRESSURE_PA
    below_dew_K = _antoine_dew_point_K(coeff, p_i_pa) - 20.0

    driving_pa = _wall_deposition_driving_pressure_pa(
        species,
        p_i_pa,
        below_dew_K,
        vapor_pressure_data=vapor_data,
    )

    expected_pa = p_i_pa - _antoine_psat_pa(coeff, below_dew_K)
    assert expected_pa > 0.0
    assert driving_pa == pytest.approx(expected_pa, rel=1e-9)


@pytest.mark.parametrize("wall_temperature_C", [1500.0, 1575.0, 1650.0])
def test_shadow_records_show_zero_alkali_wall_flux_via_dewpoint(
    wall_temperature_C: float,
) -> None:
    # Integration mirror of tests/test_coating_rate.py:342-370 on the claim's
    # own wall band: the shadow rate model must report identically zero
    # alkali wall flux, with the record's own fields showing the reason is
    # the dew-point relation at the wall temperature.
    payload = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        sio_start_temperature_c=1600.0,
        sio_hold_temperature_c=1600.0,
        sio_liner_temperature_c=wall_temperature_C,
        sio_pO2_mbar=0.0,
        include_wall_deposit_rate_diagnostics=True,
    ).run()

    assert payload["status"] == "ok"
    shadow = payload["per_hour_summary"][0][
        "wall_deposition_rate_shadow_candidate"
    ]["by_segment_species"]
    records_by_species: dict[str, list[dict]] = {}
    for segment in shadow.values():
        for species, record in segment.items():
            records_by_species.setdefault(species, []).append(record)

    for species in ("Na", "K"):
        records = records_by_species.get(species)
        assert records, f"no shadow wall-deposition record for {species}"
        for record in records:
            p_i_pa = float(record["species_partial_pressure_pa"])
            p_total_pa = float(record["total_pressure_pa"])
            p_sat_pa = float(record["wall_saturation_pressure_pa"])
            wall_K = float(record["surface"]["wall_temperature_K"])

            # Mirrored mechanism assertion (test_coating_rate.py:364-370):
            # zero flux AND admissible nonzero partial pressure AND not
            # supersaturated at the wall.
            assert float(record["mol_s"]) == 0.0
            assert 0.0 < p_i_pa <= p_total_pa
            assert record["supersaturated"] is False

            # The run sits on the claim's premise: ~10 mbar total, wall in
            # the commanded band.
            assert p_total_pa == pytest.approx(TOTAL_PRESSURE_PA, rel=1e-6)
            assert wall_K == pytest.approx(
                wall_temperature_C + CELSIUS_TO_KELVIN,
                rel=1e-9,
            )

            # The zero is the dew-point relation, not a fail-closed refusal
            # or clamp: P_sat at the WALL temperature was really evaluated,
            # exceeds the partial pressure, and equals the hand-evaluated
            # NIST sidecar fit at that temperature.
            assert record["wall_saturation_pressure_refused"] is False
            assert p_sat_pa > p_i_pa
            assert p_sat_pa == pytest.approx(
                _antoine_psat_pa(_antoine_coefficients(species), wall_K),
                rel=1e-9,
            )
