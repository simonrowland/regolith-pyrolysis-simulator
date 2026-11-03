"""Survey-table pins for the solid-buffer owner.

Expected logs are the printed survey figures at 1473, 1673, and 1873 K,
not a second copy of the evaluator. Band membership follows the stated
window rule: an unstated range does not certify.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from simulator.chemistry.fo2_buffers import (
    evaluate_buffer,
    match_printed_calibration,
    valid_calibration_ids,
)

_Q = Decimal("0.0001")
_TEMPS = (Decimal(1473), Decimal(1673), Decimal(1873))


def _q4(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


def _q3(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


# Printed log10(fO2/bar) from the 2026-11-03 survey table, 1 bar.
_TABLE = {
    "wolf2023_quote_oneill_pownceby1993": ("-11.9375", "-9.7147", "-7.9780"),
    "oneill1988_eq11": ("-11.9308", "-9.7070", "-7.9695"),
    "myers_gunter1979_iw": ("-11.8463", "-9.5706", "-7.7809"),
    "berry2024_eval_oneill_pownceby1993": ("-11.9380", "-9.7153", "-7.9786"),
    "oneill1987_mu": ("-8.4120", "-6.3051", "-4.5773"),
    "huebner_sato1970_eq4p": ("-7.5589", "-5.5357", "-3.9445"),
}
_BUFFER_OF = {
    "wolf2023_quote_oneill_pownceby1993": "IW",
    "oneill1988_eq11": "IW",
    "myers_gunter1979_iw": "IW",
    "berry2024_eval_oneill_pownceby1993": "IW",
    "oneill1987_mu": "QFM",
    "huebner_sato1970_eq4p": "NNO",
}


def test_survey_table_logs_match_to_printed_precision() -> None:
    for calibration_id, printed in _TABLE.items():
        for temperature, expected in zip(_TEMPS, printed, strict=True):
            evaluation = evaluate_buffer(
                _BUFFER_OF[calibration_id],
                temperature,
                calibration_id=calibration_id,
            )
            assert _q4(evaluation.log10_fO2_bar) == Decimal(expected)


def test_nno_bar_shift_is_log10_of_1_01325() -> None:
    # Huebner & Sato equation (4'), atm, as printed: 9.36 - 24930/T.
    atm = Decimal("9.36") - Decimal("24930") / Decimal(1473)
    evaluation = evaluate_buffer("NNO", Decimal(1473))
    assert abs(
        (evaluation.log10_fO2_bar - atm) - Decimal("1.01325").log10()
    ) < Decimal("1e-20")
    assert _q4(evaluation.log10_fO2_bar) == Decimal("-7.5589")
    # The brief's +0.00570 is that shift rounded to five digits.
    assert _q4(Decimal("1.01325").log10()) == Decimal("0.0057") or abs(
        Decimal("1.01325").log10() - Decimal("0.00570")
    ) < Decimal("0.00001")


def test_iw_pressure_term_is_the_printed_coefficient() -> None:
    base = evaluate_buffer("IW", Decimal(1473))
    pressed = evaluate_buffer("IW", Decimal(1473), pressure_bar=Decimal(100))
    # The two logs are rounded at context precision, so their difference
    # matches the printed 0.055(P-1)/T term to well under a dex place.
    assert abs(
        (pressed.log10_fO2_bar - base.log10_fO2_bar)
        - Decimal("0.055") * Decimal(99) / Decimal(1473)
    ) < Decimal("1e-20")
    assert not pressed.pressure_coefficient_spread


def test_qfm_oneill_borrows_chou_volume_and_flags_the_coefficient_spread() -> None:
    base = evaluate_buffer("QFM", Decimal(1200))
    pressed = evaluate_buffer("QFM", Decimal(1200), pressure_bar=Decimal(100))
    assert pressed.pressure_coefficient_spread
    assert pressed.log10_fO2_bar - base.log10_fO2_bar == (
        Decimal("0.092") * Decimal(99) / Decimal(1200)
    )
    assert pressed.band_dex - base.band_dex == (
        abs(Decimal("0.110") - Decimal("0.092")) * Decimal(99) / Decimal(1200)
    )


def test_hewitt_uses_its_own_volume_term() -> None:
    evaluation = evaluate_buffer(
        "QFM",
        Decimal(1000),
        pressure_bar=Decimal(100),
        calibration_id="hewitt1978",
    )
    assert evaluation.authority == "certified"
    assert not evaluation.pressure_coefficient_spread
    reference = evaluate_buffer(
        "QFM",
        Decimal(1000),
        calibration_id="hewitt1978",
    )
    assert evaluation.log10_fO2_bar - reference.log10_fO2_bar == (
        Decimal("0.110") * Decimal(99) / Decimal(1000)
    )


def test_nno_volume_term_is_in_atmospheres() -> None:
    # P = 2 atm, expressed in bar. The 1 in (P-1) is 1 atm.
    base = evaluate_buffer("NNO", Decimal(1473))
    two_atm = Decimal(2) * Decimal("1.01325")
    evaluation = evaluate_buffer("NNO", Decimal(1473), pressure_bar=two_atm)
    # P = 2 atm is 2*1.01325 bar. The 1 in (P-1) is 1 atm, so the shift
    # from the omitted-pressure curve is 0.046/T. Context rounding of the
    # two logs leaves an ulp, not a second coefficient.
    assert abs(
        (evaluation.log10_fO2_bar - base.log10_fO2_bar)
        - Decimal("0.046") / Decimal(1473)
    ) < Decimal("1e-20")


def test_band_rule_in_range_versus_extrapolated() -> None:
    # 1473 K: only Myers & Gunter IW is inside a stated window, so the
    # valid-set spread is 0. Wolf has no printed window, so using it is
    # extrapolated. 1673 and 1873 K: nobody is in range, so the band is the
    # full solid spread, which the survey prints as 0.145 and 0.198.
    at_1473 = evaluate_buffer("IW", Decimal(1473))
    assert at_1473.valid_calibration_ids == ("myers_gunter1979_iw",)
    assert at_1473.band_dex == 0
    assert at_1473.authority == "extrapolated"
    assert valid_calibration_ids("IW", Decimal(1673)) == ()
    assert valid_calibration_ids("IW", Decimal(1873)) == ()
    assert _q3(evaluate_buffer("IW", Decimal(1673)).band_dex) == Decimal("0.145")
    assert _q3(evaluate_buffer("IW", Decimal(1873)).band_dex) == Decimal("0.198")
    # The recorded 0.007-0.009 dex is the rounded Wolf minus O'Neill 1988
    # table residual, not the T-dependent band.
    wolf = [Decimal(item) for item in _TABLE["wolf2023_quote_oneill_pownceby1993"]]
    oneill = [Decimal(item) for item in _TABLE["oneill1988_eq11"]]
    residuals = [left - right for left, right in zip(wolf, oneill, strict=True)]
    assert residuals == [
        Decimal("-0.0067"),
        Decimal("-0.0077"),
        Decimal("-0.0085"),
    ]
    assert all(Decimal("0.006") <= abs(item) <= Decimal("0.009") for item in residuals)

    # 1200 K QFM: O'Neill and Wones are in range. Chou eq. 24 and Hewitt are not.
    qfm = evaluate_buffer("QFM", Decimal(1200))
    assert qfm.authority == "certified"
    assert qfm.valid_calibration_ids == ("oneill1987_mu", "wones_gilbert1969")
    oneill_log = evaluate_buffer(
        "QFM", Decimal(1200), calibration_id="oneill1987_mu"
    ).log10_fO2_bar
    wones_log = evaluate_buffer(
        "QFM", Decimal(1200), calibration_id="wones_gilbert1969"
    ).log10_fO2_bar
    assert qfm.band_dex == abs(oneill_log - wones_log)
    assert _q4(qfm.band_dex) == Decimal("0.1519")

    # Survey temperatures are above 1420 K: full printed spread 0.256, 0.244, 0.359.
    qfm_spreads = [
        _q3(evaluate_buffer("QFM", temperature).band_dex) for temperature in _TEMPS
    ]
    assert qfm_spreads == [Decimal("0.256"), Decimal("0.244"), Decimal("0.359")]
    assert all(
        evaluate_buffer("QFM", temperature).authority == "extrapolated"
        for temperature in _TEMPS
    )

    # NNO at 1473 K is inside Huebner's window. The in-range pair differs by 0.007.
    nno = evaluate_buffer("NNO", Decimal(1473))
    assert nno.authority == "certified"
    assert nno.valid_calibration_ids == (
        "huebner_sato1970_eq4p",
        "myers_gunter1979_nno",
    )
    assert _q3(nno.band_dex) == Decimal("0.007")
    assert _q3(evaluate_buffer("NNO", Decimal(1673)).band_dex) == Decimal("0.288")
    assert _q3(evaluate_buffer("NNO", Decimal(1873)).band_dex) == Decimal("0.302")
    assert evaluate_buffer("NNO", Decimal(1673)).authority == "extrapolated"


def test_printed_calibration_match_is_exact() -> None:
    assert match_printed_calibration("IW", "O'Neill 1988 equation (11)") == (
        "oneill1988_eq11"
    )
    assert match_printed_calibration("IW", "O'Neill and Pownceby 1993") == (
        "wolf2023_quote_oneill_pownceby1993"
    )
    assert match_printed_calibration("QFM", "O'Neill 1987") == "oneill1987_mu"
    assert match_printed_calibration("QFM", "O'Neill 1987b") is None
    assert match_printed_calibration("NNO", "O'Neill 1987b") == (
        "oneill1987b_via_oneill1988_eq15"
    )
    assert match_printed_calibration("NNO", "Huebner & Sato (1970)") == (
        "huebner_sato1970_eq4p"
    )
    assert match_printed_calibration("NNO", "Huebner (1971)") is None
    assert match_printed_calibration("NNO", "Huebner & Sato 1970 equation (2')") == (
        "huebner_sato1970_eq2p"
    )
    assert match_printed_calibration("QFM", "Hewitt 1978 and O'Neill 1987") is None
    assert match_printed_calibration("IW", "") is None
