"""Graphite / C–CO log10(fO2) helper — token gate and published CCO sanity."""

from __future__ import annotations

import pytest

from simulator.chemistry.graphite_c_co import (
    is_c_co_buffer_token,
    log10_fo2_c_co_bar,
)


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


def test_log10_fo2_matches_published_cco_at_1_atm() -> None:
    # Jakobsson & Oskarsson 1994 CCO via LEPR at 1473.15 K, 1.01325 bar.
    assert log10_fo2_c_co_bar(1473.15, 1.01325) == pytest.approx(
        -10.475256412619215, abs=1e-12
    )
