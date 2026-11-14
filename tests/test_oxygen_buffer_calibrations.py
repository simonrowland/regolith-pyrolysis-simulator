"""Primary calibration anchors used by battery migration."""

from __future__ import annotations

import pytest

from simulator.oxygen_buffers import (
    FROST_BUFFER_TEMPERATURE_RANGES_K,
    ONEILL_IW_TEMPERATURE_RANGES_K,
    ONEILL_QFM_TEMPERATURE_RANGE_K,
    frost_buffer_log10_fO2_bar,
    oneill_1987_qfm_log10_fO2_bar,
    oneill_iw_log10_fO2_bar,
)


def test_oneill_primary_emf_calibrations_match_source_sanity_values() -> None:
    # O'Neill 1988, eq. 11, Fe + "FeO", at 1000 K; O'Neill 1987, reaction
    # (2), QFM, at 1200 K. Values are the source equations converted from
    # μO2 at the printed 1-bar standard.
    assert oneill_iw_log10_fO2_bar(1000.0) == pytest.approx(
        -20.79998016889, abs=5e-12
    )
    assert oneill_1987_qfm_log10_fO2_bar(1200.0) == pytest.approx(
        -12.296398239396, abs=5e-12
    )
    assert ONEILL_IW_TEMPERATURE_RANGES_K == (
        (833.0, 1042.0),
        (1042.0, 1184.0),
        (1184.0, 1450.0),
    )
    assert ONEILL_QFM_TEMPERATURE_RANGE_K == (900.0, 1420.0)


@pytest.mark.parametrize(
    ("buffer", "expected"),
    (
        ("IW", -20.787),
        ("NNO", -15.57),
        ("QFM", -16.3613),
        ("WM", -19.795),
    ),
)
def test_frost_1991_table_one_coefficients_are_owned_by_oxygen_buffers(
    buffer: str, expected: float
) -> None:
    assert frost_buffer_log10_fO2_bar(buffer, 1000.0) == pytest.approx(expected)
    assert buffer in FROST_BUFFER_TEMPERATURE_RANGES_K
