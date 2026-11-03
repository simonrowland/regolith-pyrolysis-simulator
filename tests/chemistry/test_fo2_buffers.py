"""Survey-table pins for the solid-buffer owner.

Expected logs are the printed survey figures at 1473, 1673, and 1873 K,
not a second copy of the evaluator. Band membership follows the stated
window rule: an unstated range does not certify.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pathlib import Path

import yaml

from simulator.battery.enums import NoticeKind
from simulator.chemistry.fo2_buffers import (
    UNSIZED_BAND_NOTICE,
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
    # d-324: the IW band is the printed Wolf minus O'Neill 1988 table
    # residual, about 0.01 dex. Myers inside 1073.15-1573.15 K must not
    # zero it. Myers outside that window must not re-enter.
    wolf = [Decimal(item) for item in _TABLE["wolf2023_quote_oneill_pownceby1993"]]
    oneill = [Decimal(item) for item in _TABLE["oneill1988_eq11"]]
    residuals = [left - right for left, right in zip(wolf, oneill, strict=True)]
    assert residuals == [
        Decimal("-0.0067"),
        Decimal("-0.0077"),
        Decimal("-0.0085"),
    ]
    at_1473 = evaluate_buffer("IW", Decimal(1473))
    assert at_1473.valid_calibration_ids == ("myers_gunter1979_iw",)
    assert at_1473.authority == "extrapolated"
    assert at_1473.band_dex == abs(residuals[0])
    assert at_1473.band_dex != 0
    at_1523 = evaluate_buffer("IW", Decimal("1523.15"))
    assert at_1523.valid_calibration_ids == ("myers_gunter1979_iw",)
    assert at_1523.band_dex != 0
    assert Decimal("0.006") <= at_1523.band_dex <= Decimal("0.009")
    assert valid_calibration_ids("IW", Decimal(1673)) == ()
    assert valid_calibration_ids("IW", Decimal(1873)) == ()
    assert evaluate_buffer("IW", Decimal(1673)).band_dex == abs(residuals[1])
    assert evaluate_buffer("IW", Decimal(1873)).band_dex == abs(residuals[2])
    myers_gap = abs(
        Decimal(_TABLE["wolf2023_quote_oneill_pownceby1993"][0])
        - Decimal(_TABLE["myers_gunter1979_iw"][0])
    )
    assert myers_gap == Decimal("0.0912")
    assert at_1473.band_dex < myers_gap

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


def _member(buffer: str, calibration_id: str, temperature: Decimal) -> Decimal:
    return evaluate_buffer(
        buffer, temperature, calibration_id=calibration_id
    ).log10_fO2_bar


def _pair(central: Decimal, route: Decimal, edge_central: Decimal, edge_route: Decimal) -> Decimal:
    """Pair width: round to survey precision, then count the edge once."""

    at_temperature = _q4(central) - _q4(route)
    at_edge = _q4(edge_central) - _q4(edge_route)
    return abs(at_temperature - at_edge) + abs(at_edge)


def test_iw_qfm_nno_stay_off_the_extended_region_path() -> None:
    for buffer, temperature in (("IW", 1473), ("QFM", 1200), ("NNO", 1473)):
        evaluation = evaluate_buffer(buffer, Decimal(temperature))
        assert evaluation.region is None
        assert evaluation.edge_offsets == ()
        assert evaluation.unsized_notice is None
        assert evaluation.divergence_note is None
        assert evaluation.band_dex is not None


# Printed log10(fO2/bar), survey table, 1 bar. Not a second copy of the fits.
_EXTENDED_TABLE = {
    ("Mo-MoO2", "oneill1986_via_oneill1987"): {
        1200: "-16.2945",
        1450: "-11.9715",
        1473: "-11.6492",
        1673: "-9.2294",
        1873: "-7.3402",
    },
    ("MW", "oneill1988_eq16"): {
        1000: "-19.8453",
        1473: "-9.3009",
        1673: "-6.5764",
        1873: "-4.4078",
    },
    ("MW", "haas1992_table_10_7"): {
        1000: "-19.786",
        1450: "-9.535",
        1500: "-8.745",
        1473: "-9.1649",
        1673: "-6.3017",
        1800: "-4.724",
    },
    ("MH", "haas1992_table_10_9"): {
        1450: "-3.040",
        1500: "-2.451",
        1473: "-2.7641",
        1673: "-0.6918",
        1750: "-0.023",
        1800: "0.380",
    },
    ("MH", "chou1978_eq12"): {
        1473: "-2.7844",
        1673: "-0.6232",
        1873: "1.0764",
    },
}


def test_extended_curves_match_the_survey_table() -> None:
    for (buffer, calibration_id), points in _EXTENDED_TABLE.items():
        for temperature, printed in points.items():
            evaluation = evaluate_buffer(
                buffer, Decimal(temperature), calibration_id=calibration_id
            )
            assert _q4(evaluation.log10_fO2_bar) == Decimal(printed)


def test_region2_bands_equal_the_edge_disagreement_at_each_edge() -> None:
    # At T = edge, d(T) = d(edge), so |d(T) - d(edge)| + |d(edge)|
    # reduces to the edge offset itself. The Mo endpoint literals are the
    # hand-rounded survey offsets at the open O'Neill endpoints.
    cases = (
        ("Mo-MoO2", Decimal(1000), Decimal("0.1739")),
        ("Mo-MoO2", Decimal(1450), Decimal("0.1150")),
        ("MH", Decimal(1073), Decimal("0.4907")),
        ("MW", Decimal(1270), Decimal("0.5191")),
    )
    for buffer, temperature, expected_band in cases:
        evaluation = evaluate_buffer(buffer, temperature)
        assert evaluation.band_dex is not None
        assert evaluation.band_dex == expected_band
        assert evaluation.region == 2
        assert evaluation.band_dex == max(
            abs(edge.signed_dex)
            for edge in evaluation.edge_offsets
            if edge.edge_temperature_K == temperature
        )


def test_region2_bands_are_continuous_just_above_each_edge() -> None:
    cases = (
        ("Mo-MoO2", Decimal("1450.1"), Decimal("0.1150")),
        ("MH", Decimal("1073.1"), Decimal("0.4907")),
        ("MW", Decimal("1270.1"), Decimal("0.5191")),
    )
    tolerance = Decimal("0.001")
    for buffer, temperature, edge_disagreement in cases:
        band = evaluate_buffer(buffer, temperature).band_dex
        assert band is not None
        assert abs(band - edge_disagreement) <= tolerance


def test_region2_interior_bands_match_pins_and_bound_raw_disagreement() -> None:
    cases = (
        (
            "Mo-MoO2",
            "janaf_mo_moo2",
            ("oneill1986_via_oneill1987",),
            1473,
            "0.1171",
        ),
        (
            "Mo-MoO2",
            "janaf_mo_moo2",
            ("oneill1986_via_oneill1987",),
            1673,
            "0.1378",
        ),
        (
            "Mo-MoO2",
            "janaf_mo_moo2",
            ("oneill1986_via_oneill1987",),
            1873,
            "0.1599",
        ),
        (
            "MH",
            "haas1992_table_10_9",
            ("chou1978_eq12", "janaf_mh_fe3o4_fe2o3"),
            1473,
            "0.7410",
        ),
        (
            "MH",
            "haas1992_table_10_9",
            ("chou1978_eq12", "janaf_mh_fe3o4_fe2o3"),
            1673,
            "0.8274",
        ),
        (
            "MW",
            "haas1992_table_10_7",
            ("oneill1988_eq16", "janaf_mw_fe0_947o_fe3o4"),
            "1274.15",
            "0.5214",
        ),
        (
            "MW",
            "haas1992_table_10_7",
            ("oneill1988_eq16", "janaf_mw_fe0_947o_fe3o4"),
            "1281.15",
            "0.5255",
        ),
        (
            "MW",
            "haas1992_table_10_7",
            ("oneill1988_eq16", "janaf_mw_fe0_947o_fe3o4"),
            1473,
            "0.6671",
        ),
        (
            "MW",
            "haas1992_table_10_7",
            ("oneill1988_eq16", "janaf_mw_fe0_947o_fe3o4"),
            1673,
            "0.8920",
        ),
    )
    for buffer, central_id, route_ids, temperature, expected_band in cases:
        temp = Decimal(temperature)
        band = evaluate_buffer(buffer, temp).band_dex
        assert band is not None
        assert band == Decimal(expected_band)
        for route_id in route_ids:
            raw_gap = abs(
                _q4(_member(buffer, central_id, temp))
                - _q4(_member(buffer, route_id, temp))
            )
            assert band >= raw_gap


def test_mo_regions_authority_and_edge_offsets() -> None:
    inside = evaluate_buffer("Mo-MoO2", Decimal(1200))
    assert inside.region == 1
    assert inside.authority == "certified"
    assert inside.calibration_id == "oneill1986_via_oneill1987"
    assert inside.band_dex == 0
    assert _q4(inside.log10_fO2_bar) == Decimal("-16.2945")
    # One EMF curve. The ±140 J fit precision is not the band.
    assert inside.valid_calibration_ids == ("oneill1986_via_oneill1987",)

    above = evaluate_buffer("Mo-MoO2", Decimal(1473))
    assert above.region == 2
    assert above.authority == "tabulated_thermochemistry"
    assert above.calibration_id == "janaf_mo_moo2"
    assert above.unsized_notice is None
    janaf = _member("Mo-MoO2", "janaf_mo_moo2", Decimal(1473))
    oneill = _member("Mo-MoO2", "oneill1986_via_oneill1987", Decimal(1473))
    janaf_edge = _member("Mo-MoO2", "janaf_mo_moo2", Decimal(1450))
    oneill_edge = _member("Mo-MoO2", "oneill1986_via_oneill1987", Decimal(1450))
    assert above.band_dex == _pair(janaf, oneill, janaf_edge, oneill_edge)
    offsets = {
        (item.route_id, item.edge_temperature_K): item.signed_dex
        for item in above.edge_offsets
    }
    assert offsets == {
        ("oneill1986_via_oneill1987", Decimal(1000)): Decimal("0.1739"),
        ("oneill1986_via_oneill1987", Decimal(1450)): Decimal("0.1150"),
    }
    # 1873 K is still inside the solid JANAF tables. The survey's O'Neill
    # column is the calibration, not the canonical central.
    at_1873 = evaluate_buffer("Mo-MoO2", Decimal(1873))
    assert at_1873.region == 2
    assert at_1873.authority == "tabulated_thermochemistry"
    assert _q4(
        evaluate_buffer(
            "Mo-MoO2", Decimal(1873), calibration_id="oneill1986_via_oneill1987"
        ).log10_fO2_bar
    ) == Decimal("-7.3402")
    beyond = evaluate_buffer("Mo-MoO2", Decimal(2900))
    assert beyond.region == 3
    assert beyond.authority == "extrapolated"
    assert beyond.band_dex is None
    assert beyond.unsized_notice == UNSIZED_BAND_NOTICE
    assert beyond.log10_fO2_bar == _member("Mo-MoO2", "janaf_mo_moo2", Decimal(2900))
    assert evaluate_buffer("Mo-MoO2", Decimal(2800)).region == 2


def test_mh_is_the_haas_table_until_1800_then_unsized() -> None:
    for temperature in (Decimal(1473), Decimal(1673), Decimal(1800)):
        evaluation = evaluate_buffer("MH", temperature)
        assert evaluation.region == 2
        assert evaluation.authority == "tabulated_thermochemistry"
        assert evaluation.calibration_id == "haas1992_table_10_9"
        assert evaluation.unsized_notice is None
        haas = _member("MH", "haas1992_table_10_9", temperature)
        chou = _member("MH", "chou1978_eq12", temperature)
        janaf = _member("MH", "janaf_mh_fe3o4_fe2o3", temperature)
        edge = Decimal(1073)
        haas_edge = _member("MH", "haas1992_table_10_9", edge)
        assert evaluation.band_dex == max(
            _pair(haas, chou, haas_edge, _member("MH", "chou1978_eq12", edge)),
            _pair(haas, janaf, haas_edge, _member("MH", "janaf_mh_fe3o4_fe2o3", edge)),
        )
    offsets = {
        (item.route_id, item.edge_temperature_K): item.signed_dex
        for item in evaluate_buffer("MH", Decimal(1473)).edge_offsets
    }
    assert offsets == {
        ("chou1978_eq12", Decimal(1073)): Decimal("0.2398"),
        ("janaf_mh_fe3o4_fe2o3", Decimal(1073)): Decimal("-0.4907"),
    }
    beyond = evaluate_buffer("MH", Decimal(1873))
    assert beyond.region == 3
    assert beyond.authority == "extrapolated"
    assert beyond.band_dex is None
    assert beyond.unsized_notice == UNSIZED_BAND_NOTICE
    assert _q4(beyond.log10_fO2_bar) == Decimal("0.9297")
    assert beyond.log10_fO2_bar == _member("MH", "haas1992_table_10_9", Decimal(1873))
    # Chou's closed window is unstated, so selecting the equation is not certified.
    selected = evaluate_buffer("MH", Decimal(1000), calibration_id="chou1978_eq12")
    assert _q4(selected.log10_fO2_bar) == _q4(
        _member("MH", "chou1978_eq12", Decimal(1000))
    )
    assert selected.authority == "extrapolated"
    assert selected.region == 3
    assert selected.band_dex is None
    assert selected.unsized_notice == UNSIZED_BAND_NOTICE


def test_mw_regions_carry_the_wustite_warning_above_the_emf_window() -> None:
    inside = evaluate_buffer("MW", Decimal(1000))
    assert inside.region == 1
    assert inside.authority == "certified"
    assert inside.calibration_id == "oneill1988_eq16"
    assert inside.divergence_note is None
    oneill = _member("MW", "oneill1988_eq16", Decimal(1000))
    haas = _member("MW", "haas1992_table_10_7", Decimal(1000))
    assert inside.band_dex == abs(_q4(oneill) - _q4(haas))
    assert inside.band_dex == Decimal("0.0593")
    # Just below the Haas nodes, only O'Neill is inside a stated window.
    gap = evaluate_buffer("MW", Decimal(836))
    assert gap.region == 1
    assert gap.band_dex == 0
    assert gap.valid_calibration_ids == ("oneill1988_eq16",)

    above = evaluate_buffer("MW", Decimal(1473))
    assert above.region == 2
    assert above.authority == "tabulated_thermochemistry"
    assert above.calibration_id == "haas1992_table_10_7"
    assert above.divergence_note is not None
    assert "Fe0.947O" in above.divergence_note
    assert "833" in above.divergence_note
    assert "1697" in above.divergence_note
    edge = Decimal(1270)
    haas_t = _member("MW", "haas1992_table_10_7", Decimal(1473))
    haas_edge = _member("MW", "haas1992_table_10_7", edge)
    assert above.band_dex == max(
        _pair(
            haas_t,
            _member("MW", "oneill1988_eq16", Decimal(1473)),
            haas_edge,
            _member("MW", "oneill1988_eq16", edge),
        ),
        _pair(
            haas_t,
            _member("MW", "janaf_mw_fe0_947o_fe3o4", Decimal(1473)),
            haas_edge,
            _member("MW", "janaf_mw_fe0_947o_fe3o4", edge),
        ),
    )
    offsets = {
        (item.route_id, item.edge_temperature_K): item.signed_dex
        for item in above.edge_offsets
    }
    assert offsets == {
        ("oneill1988_eq16", Decimal(1270)): Decimal("0.0789"),
        ("janaf_mw_fe0_947o_fe3o4", Decimal(1270)): Decimal("0.5191"),
    }
    # Borisov's MW rows sit just above 1270 K, on the Haas central.
    borisov = evaluate_buffer("MW", Decimal("1281.15"))
    assert borisov.region == 2
    assert borisov.calibration_id == "haas1992_table_10_7"

    beyond = evaluate_buffer("MW", Decimal(1873))
    assert beyond.region == 3
    assert beyond.authority == "extrapolated"
    assert beyond.band_dex is None
    assert beyond.unsized_notice == UNSIZED_BAND_NOTICE
    assert beyond.divergence_note is not None
    assert _q4(beyond.log10_fO2_bar) == Decimal("-3.8987")
    assert beyond.log10_fO2_bar == _member(
        "MW", "haas1992_table_10_7", Decimal(1873)
    )


def test_extended_buffers_record_pressure_and_do_not_move_the_log() -> None:
    for buffer in ("Mo-MoO2", "MH", "MW"):
        base = evaluate_buffer(buffer, Decimal(1473))
        pressed = evaluate_buffer(buffer, Decimal(1473), pressure_bar=Decimal(2000))
        assert pressed.log10_fO2_bar == base.log10_fO2_bar
        assert pressed.pressure_bar == Decimal(2000)
        assert not pressed.pressure_coefficient_spread
        assert base.pressure_bar is None


def test_explicit_janaf_authority_uses_its_table_window() -> None:
    janaf_inside = evaluate_buffer(
        "Mo-MoO2", Decimal(1673), calibration_id="janaf_mo_moo2"
    )
    assert janaf_inside.authority == "tabulated_thermochemistry"
    assert janaf_inside.region == 2
    assert janaf_inside.band_dex == Decimal("0.1378")
    assert janaf_inside.unsized_notice is None
    # JANAF's own table covers 1200 K even though the canonical centre is
    # the O'Neill EMF route there.
    janaf_inside_emf_region = evaluate_buffer(
        "Mo-MoO2", Decimal(1200), calibration_id="janaf_mo_moo2"
    )
    assert janaf_inside_emf_region.authority == "tabulated_thermochemistry"
    assert janaf_inside_emf_region.region == 2
    assert janaf_inside_emf_region.band_dex is not None
    assert janaf_inside_emf_region.band_dex > 0
    janaf_outside = evaluate_buffer(
        "Mo-MoO2", Decimal(2900), calibration_id="janaf_mo_moo2"
    )
    assert janaf_outside.authority == "extrapolated"
    assert janaf_outside.region == 3
    assert janaf_outside.band_dex is None
    assert janaf_outside.unsized_notice == UNSIZED_BAND_NOTICE

    for buffer, calibration_id in (
        ("MH", "janaf_mh_fe3o4_fe2o3"),
        ("MW", "janaf_mw_fe0_947o_fe3o4"),
    ):
        janaf_inside = evaluate_buffer(
            buffer, Decimal(1873), calibration_id=calibration_id
        )
        assert janaf_inside.authority == "tabulated_thermochemistry"
        assert janaf_inside.region == 2
        assert janaf_inside.band_dex is not None
        assert janaf_inside.unsized_notice is None


def test_explicit_haas_authority_uses_its_table_window() -> None:
    haas_inside = evaluate_buffer(
        "MH", Decimal(1473), calibration_id="haas1992_table_10_9"
    )
    assert haas_inside.authority == "tabulated_thermochemistry"
    assert haas_inside.region == 2
    assert haas_inside.band_dex is not None
    assert haas_inside.unsized_notice is None
    haas_outside = evaluate_buffer(
        "MH", Decimal(1873), calibration_id="haas1992_table_10_9"
    )
    assert haas_outside.authority == "extrapolated"
    assert haas_outside.region == 3
    assert haas_outside.band_dex is None
    assert haas_outside.unsized_notice == UNSIZED_BAND_NOTICE


def test_janaf_moo2_route_matches_the_printed_log_kf_column() -> None:
    """Mo + O2 = MoO2, so log10(fO2/bar) rounds to the printed −log Kf.

    The cell is the table's own 3-decimal log Kf, not this module's formula.
    """

    path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/compilations/janaf/tables/Mo-009.yaml"
    )
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    printed = None
    for row in document["table"]["values"]:
        if str(row["temperature"]["as_published"]) == "1400":
            printed = Decimal(
                str(row["log10_formation_equilibrium_constant"]["as_published"])
            )
            break
    assert printed == Decimal("12.589")
    evaluation = evaluate_buffer(
        "Mo-MoO2", Decimal(1400), calibration_id="janaf_mo_moo2"
    )
    assert _q3(evaluation.log10_fO2_bar) == -printed


def test_extended_names_match_the_printed_citation() -> None:
    assert match_printed_calibration("Mo-MoO2", "O'Neill 1986") == (
        "oneill1986_via_oneill1987"
    )
    assert match_printed_calibration("Mo-MoO2", "O'Neill 1987") == (
        "oneill1986_via_oneill1987"
    )
    assert match_printed_calibration("MH", "Haas & Hemingway 1992") == (
        "haas1992_table_10_9"
    )
    assert match_printed_calibration("MW", "Table 10.7") == "haas1992_table_10_7"
    assert match_printed_calibration("Mo-MoO2", "JANAF") == "janaf_mo_moo2"
    assert match_printed_calibration("MH", "Haas & Hemingway 1992 and JANAF") is None
    assert UNSIZED_BAND_NOTICE == (
        NoticeKind.BUFFER_BAND_UNSIZED_BEYOND_ALL_CALIBRATIONS.value
    )
