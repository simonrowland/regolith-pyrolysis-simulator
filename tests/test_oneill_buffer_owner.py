"""Independent pins for O'Neill's printed QFM and IW μO2 fits."""

from __future__ import annotations

from decimal import Decimal

import pytest

from simulator.chemistry.fo2_buffers import evaluate_buffer
from simulator.oxygen_buffers import (
    oneill_1987_qfm_log10_fO2_bar,
    oneill_iw_log10_fO2_bar,
)


# The expected values below were evaluated independently from O'Neill's
# printed μO2 equations. O'Neill 1987 p. 73 gives R = 8.31441 J mol^-1 K^-1
# for converting μO2 to log fO2; O'Neill 1988 p. 477, equation (11), prints
# μO2 in J/mol and uses the same conversion. The runtime transcription still
# uses CODATA here, while the calibration catalogue uses the printed R.
_QFM = (
    (1000, -16.442983538992, "-16.443087599223"),
    (1100, -14.181288439296, "-14.181378186279"),
    (1300, -10.695145557892, "-10.695213242648"),
)
_IW = (
    (1000, -20.799980168890, "-20.800111802589"),
    (1100, -18.291960959829, "-18.292076721405"),
    (1300, -14.420998176713, "-14.421089440711"),
)


@pytest.mark.parametrize(("temperature_K", "expected_float", "expected_decimal"), _QFM)
def test_qfm_transcriptions_are_independently_pinned(
    temperature_K: int, expected_float: float, expected_decimal: str
) -> None:
    actual_float = oneill_1987_qfm_log10_fO2_bar(float(temperature_K))
    actual_decimal = evaluate_buffer(
        "QFM",
        Decimal(temperature_K),
        calibration_id="oneill1987_mu",
    ).log10_fO2_bar

    assert actual_float == pytest.approx(expected_float, abs=5e-12)
    assert actual_decimal.quantize(Decimal("0.000000000001")) == Decimal(
        expected_decimal
    )


@pytest.mark.parametrize(("temperature_K", "expected_float", "expected_decimal"), _IW)
def test_iw_transcriptions_are_independently_pinned(
    temperature_K: int, expected_float: float, expected_decimal: str
) -> None:
    actual_float = oneill_iw_log10_fO2_bar(float(temperature_K))
    actual_decimal = evaluate_buffer(
        "IW",
        Decimal(temperature_K),
        calibration_id="oneill1988_eq11",
    ).log10_fO2_bar

    assert actual_float == pytest.approx(expected_float, abs=5e-12)
    assert actual_decimal.quantize(Decimal("0.000000000001")) == Decimal(
        expected_decimal
    )
