"""Graphite / C–CO log10(fO2) helper — token gate and published CCO sanity."""

from __future__ import annotations

import pytest

from simulator.chemistry.graphite_c_co import (
    is_c_co_buffer_token,
)
from simulator.oxygen_buffers import log10_fo2_c_co_bar


@pytest.mark.parametrize(
    "token",
    ["C-CO", "c-co", "CCO", "graphite-CO", "GRAPHITE/CO", "C-CO-CO2"],
)
def test_c_co_tokens_recognized(token: str) -> None:
    assert is_c_co_buffer_token(token)


@pytest.mark.parametrize(
    "token",
    [
        "CO partial pressure controlled by CO/Ar mixing",
        "IW",
        "NNO",
        "QFM",
        "",
    ],
)
def test_non_c_co_tokens_refused(token: str) -> None:
    assert not is_c_co_buffer_token(token)


@pytest.mark.parametrize(
    ("temperature_K", "expected_log10_fO2"),
    (
        (1473.15, -17.121048282265),
        (1673.15, -16.140193300064),
    ),
)
def test_log10_fo2_from_janaf_co_formation_gibbs(
    temperature_K: float, expected_log10_fO2: float
) -> None:
    # NIST-JANAF C-093, Carbon Monoxide, ΔfG° (kJ/mol): rows 1400/1500 K
    # are -235.149/-243.740 and rows 1600/1700 K are -252.284/-260.784.
    # Linear interpolation to these temperatures gives the independent
    # expected pins below for C(gr) + 1/2 O2 = CO, standard state p° = 1 bar.
    # The former -10.475256 pin used the high-pressure CCO fit at 1 atm and
    # encoded the defect; these values use the stated-CO reaction equilibrium.
    assert log10_fo2_c_co_bar(temperature_K, 1.0) == pytest.approx(
        expected_log10_fO2, abs=1e-12
    )


def test_log10_fo2_tracks_c_co_reaction_quotient() -> None:
    # At fixed T, a decade change in pCO changes log10(fO2) by exactly two.
    one_bar = log10_fo2_c_co_bar(1473.15, 1.0)
    tenth_bar = log10_fo2_c_co_bar(1473.15, 0.1)
    assert tenth_bar == pytest.approx(-19.121048282265, abs=1e-12)
    assert one_bar - tenth_bar == pytest.approx(2.0, abs=1e-12)
