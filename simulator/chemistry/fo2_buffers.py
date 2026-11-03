"""Solid-buffer oxygen fugacity owner for printed IW, QFM, and NNO offsets.

One module owns the calibrations migration may apply. Runtime redox still
uses its own lines and must not import this module:

* ``simulator/chemistry/qfm_oneill.py`` is the linearisation ``8.58-25050/T``.
  It is not O'Neill 1987. ``engines/builtin/cco_redox_buffer.py`` wraps it.
* ``simulator/fe_redox.py`` keeps ``legacy_iw`` and Holzheid liquid FeO.
  Liquid FeO is not the solid IW buffer and is not a calibration here.
* ``simulator/core.py`` and ``simulator/offgas_fo2.py`` are runtime callers.
* ``simulator/battery/waypoints.py`` still looks up Frost
  ``PUBLISHED_BUFFERS``. Repointing it would change experiment-buffer numbers.

Equations are the ones transcribed in
``docs-private/research/2026-11-03-buffer-survey`` (fo2_table.py / REPORT.md).
R for a μ fit is O'Neill 1987's 8.31441 J K^-1 mol^-1. An atmosphere equation
becomes log10(fO2/bar) by adding log10(1.01325). The brief's +0.00570 is that
shift rounded; the value used is the exact logarithm.

Band rule (d-324). A calibration is valid at T only inside the authors'
stated window. An unstated range does not certify, so it is not in the valid
set. The band is the max-min of log10(fO2/bar) among valid calibrations, each
evaluated at the requested pressure with its own published pressure
coefficient (zero when none was published). One valid calibration has band 0.
If none are valid, the band is the full distinct-source spread and the
authority of a calibration used there is extrapolated. The recorded
0.007-0.009 dex is |Wolf minus O'Neill 1988| at the survey temperatures, not
this T-dependent band. Myers & Gunter stay in the spread only while
1073.15-1573.15 K.

QFM pressure. O'Neill 1987 publishes no volume term. At P other than 1 bar
the value uses Chou's published C = 0.092 (bar), and the band widens by
|0.110-0.092|·|P-1|/T. Hewitt 1978 already prints C = 0.110; a row that
names Hewitt uses that equation and does not add a second volume term.
Frost's coefficient is not used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

# 1 atm = 1.01325 bar. log10(fO2/bar) = log10(fO2/atm) + log10(1.01325).
_ATM_BAR = Decimal("1.01325")
_LOG10_ATM_TO_BAR = _ATM_BAR.log10()
# O'Neill 1987, p. 73 arithmetic. Not the CODATA R used by Holzheid.
_R_ONEILL = Decimal("8.31441")
_LN10 = Decimal(10).ln()

BufferLog = Callable[[Decimal, Decimal | None], Decimal]


@dataclass(frozen=True)
class Calibration:
    """One printed buffer curve.

    ``t_low`` / ``t_high`` are the authors' stated window in kelvin. Both
    ``None`` means the quoting paper printed no range. ``low_exclusive``
    follows the printed inequality: O'Neill writes ``900 < T < 1420``.
    """

    calibration_id: str
    buffer: str
    citation: str
    provenance: str
    phrases: tuple[str, ...]
    specific: bool
    in_distinct_spread: bool
    t_low: Decimal | None
    t_high: Decimal | None
    low_exclusive: bool
    high_exclusive: bool


@dataclass(frozen=True)
class BufferEvaluation:
    """log10(fO2/bar) of one calibration, plus the band at that T and P."""

    buffer: str
    calibration_id: str
    log10_fO2_bar: Decimal
    temperature_K: Decimal
    pressure_bar: Decimal | None
    authority: str
    band_dex: Decimal
    provenance: str
    pressure_coefficient_spread: bool
    valid_calibration_ids: tuple[str, ...]


def _in_window(cal: Calibration, temperature_K: Decimal) -> bool:
    if cal.t_low is None or cal.t_high is None:
        return False
    if cal.low_exclusive:
        if temperature_K <= cal.t_low:
            return False
    elif temperature_K < cal.t_low:
        return False
    if cal.high_exclusive:
        return temperature_K < cal.t_high
    return temperature_K <= cal.t_high


def _pressure_term(coefficient: Decimal, temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """C·(P-1)/T with P in bar. No pressure means the 1-bar reference (term 0)."""

    if pressure_bar is None:
        return Decimal(0)
    return coefficient * (pressure_bar - Decimal(1)) / temperature_K


def _mu_to_log10(mu_j_mol: Decimal, temperature_K: Decimal) -> Decimal:
    """μ(J mol^-1) / (R T ln 10) = log10(fO2/bar).

    Premise: O'Neill prints μ_O2 at the 1 bar standard state. Dividing by
    R T ln(10) with R = 8.31441 cancels J mol^-1 and leaves a pure number,
    which is log10 of a fugacity whose reference is that 1 bar. Unit check:
    R·T is J mol^-1, ln(10) is dimensionless, so the quotient is log10.
    """

    return mu_j_mol / (_R_ONEILL * temperature_K * _LN10)


def _ab_atm(a: str, b: str, temperature_K: Decimal) -> Decimal:
    """A/T + B printed in atm, shifted onto the bar table.

    Unit check: A/T and B are log10(fO2/atm). Adding log10(1.01325) is
    log10(fO2/bar). Sanity is the survey table, not a second fit.
    """

    return Decimal(a) / temperature_K + Decimal(b) + _LOG10_ATM_TO_BAR


def _ab_bar(
    a: str,
    b: str,
    temperature_K: Decimal,
    pressure_bar: Decimal | None,
    c: str = "0",
) -> Decimal:
    """A/T + B + C(P-1)/T already printed in bar."""

    return (
        Decimal(a) / temperature_K
        + Decimal(b)
        + _pressure_term(Decimal(c), temperature_K, pressure_bar)
    )


def _log_iw_wolf(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill & Pownceby 1993 as Wolf 2023 prints it. Canonical IW.

    Premise: Wolf 2023 footnote, quoted, P in bar, ln natural, no T range
    in the quoting paper. Provenance of the pick is quoted_from_secondary.
    Algebra: log10(fO2/bar) = -28776.8/T + 14.057 + 0.055(P-1)/T - 0.8853 ln T.
    The 0.055 term is 0 at 1 bar. Unit check: each term is a log10 number;
    (P-1)/T is bar K^-1 times a printed coefficient that already absorbs
    the volume and the log10 conversion. Sanity at 1473 K and 1 bar:
    -11.9375, the survey table.
    """

    return (
        Decimal("-28776.8") / temperature_K
        + Decimal("14.057")
        + _pressure_term(Decimal("0.055"), temperature_K, pressure_bar)
        - Decimal("0.8853") * temperature_K.ln()
    )


def _log_iw_oneill1988(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill 1988 equation (11), Fe + "FeO", μ_O2 in J mol^-1, 1 bar.

    Segments, contiguous, endpoints excluded from the lower piece:
    833 < T < 1042, 1042 ≤ T < 1184, 1184 ≤ T < 1450. Outside that overall
    window the top segment is the extrapolated evaluation. No pressure term
    was printed, so a requested P does not move this curve.
    """

    del pressure_bar
    if Decimal(833) < temperature_K < Decimal(1042):
        mu = (
            Decimal("-605812")
            + Decimal("1366.718") * temperature_K
            - Decimal("182.7955") * temperature_K * temperature_K.ln()
            + Decimal("0.10359") * temperature_K * temperature_K
        )
    elif Decimal(1042) <= temperature_K < Decimal(1184):
        mu = (
            Decimal("-519357")
            + Decimal("59.427") * temperature_K
            + Decimal("8.9276") * temperature_K * temperature_K.ln()
        )
    else:
        mu = (
            Decimal("-551159")
            + Decimal("269.404") * temperature_K
            - Decimal("16.9484") * temperature_K * temperature_K.ln()
        )
    return _mu_to_log10(mu, temperature_K)


def _log_iw_myers(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Myers & Gunter 1979, 7.184 - 28040/T, printed atm, 800-1300 °C.

    Authors say not to extrapolate. No pressure term.
    """

    del pressure_bar
    return _ab_atm("-28040", "7.184", temperature_K)


def _log_iw_berry(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Berry 2024 evaluation of O'Neill & Pownceby 1993. No pressure term.

    14.056 - 28776.5/T - 2.0384 log10(T). 0.8853·ln(10) is about 2.0385, so
    this is the Wolf temperature part in log10. No stated range.
    """

    del pressure_bar
    return (
        Decimal("14.056")
        - Decimal("28776.5") / temperature_K
        - Decimal("2.0384") * temperature_K.log10()
    )


def _log_qfm_oneill1987(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill 1987 μ_O2^QFM, reference pressure 1 bar. Canonical QFM.

    Premise: abstract and p. 73, 900 < T < 1420, no volume coefficient.
    Algebra: μ = -587474 + 1584.427 T - 203.3164 T ln T + 0.092710 T^2,
    then log10 = μ / (8.31441 T ln 10). A requested P is ignored here;
    ``evaluate_buffer`` adds Chou's C = 0.092 only when this calibration
    is the one used and P is not 1 bar. Sanity at 1473 K (extrapolated):
    -8.4120 on the survey table.
    """

    del pressure_bar
    mu = (
        Decimal("-587474")
        + Decimal("1584.427") * temperature_K
        - Decimal("203.3164") * temperature_K * temperature_K.ln()
        + Decimal("0.092710") * temperature_K * temperature_K
    )
    return _mu_to_log10(mu, temperature_K)


def _log_qfm_chou24(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 equation (24), atm, measured 873-1073 K. No P on A/T+B.

    The paper warns against extrapolation. Chou's separate C = 0.092 is the
    volume coefficient applied to the O'Neill value, not a second term here.
    """

    del pressure_bar
    return _ab_atm("-23973", "7.985", temperature_K)


def _log_qfm_hewitt1978(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Hewitt 1978, bars: -25287/T + 8.95 + 0.110(P_bar-1)/T, 650-850 °C."""

    return _ab_bar("-25287", "8.95", temperature_K, pressure_bar, "0.110")


def _log_qfm_wones(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Wones & Gilbert 1969 at 1 bar: -25738/T + 9.00, 600-1100 °C."""

    del pressure_bar
    return _ab_bar("-25738", "9.00", temperature_K, None)


def _log_qfm_hewitt1976(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 table 6 row for Hewitt 1976, atm. No range in that table."""

    del pressure_bar
    return _ab_atm("-25056", "8.74", temperature_K)


def _log_qfm_eugster(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 table 6 row for Eugster & Wones 1962 QFM, atm. No range."""

    del pressure_bar
    return _ab_atm("-26494", "9.69", temperature_K)


def _nno_volume(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Huebner & Sato 1970 Table 5 volume term.

    The table header is EQUATION FOR BUFFER CURVES (ATM), so P and the 1
    in (P-1) are atmospheres. A caller pressure in bar is divided by
    1.01325 before the term. At 1 atm the term is 0. At 1 bar it is about
    10^-6 dex. No separate coefficient spread: 0.046 is the one published
    volume coefficient used here.
    """

    if pressure_bar is None:
        return Decimal(0)
    pressure_atm = pressure_bar / _ATM_BAR
    return Decimal("0.046") * (pressure_atm - Decimal(1)) / temperature_K


def _log_nno_huebner(
    a: str, b: str
) -> BufferLog:
    """Huebner & Sato atm line, then the bar shift, then the volume term.

    Canonical (4') and the abstract: log10(fO2/atm) = 9.36 - 24930/T,
    window 519-1319 °C. Bar value = that + log10(1.01325). Sanity at
    1473 K with no volume term: -7.5589. Same-experiment body equations
    are selectable and are not in the distinct-source spread.
    """

    def evaluate(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
        return (
            _ab_atm(a, b, temperature_K)
            + _nno_volume(temperature_K, pressure_bar)
        )

    return evaluate


def _log_nno_myers(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Myers & Gunter 1979 NNO, 9.346 - 24920/T atm, 800-1300 °C. No P term."""

    del pressure_bar
    return _ab_atm("-24920", "9.346", temperature_K)


def _log_nno_oneill1987b(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill 1987b as O'Neill 1988 equation (15), μ, 800 < T < 1420. No P."""

    del pressure_bar
    mu = (
        Decimal("-480104")
        + Decimal("244.700") * temperature_K
        - Decimal("9.167") * temperature_K * temperature_K.ln()
    )
    return _mu_to_log10(mu, temperature_K)


def _log_nno_eugster(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 table 6, Eugster & Wones 1962 NNO, atm. No range."""

    del pressure_bar
    return _ab_atm("-24709", "8.94", temperature_K)


def _log_nno_charette(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 table 6, Charette & Flengas 1968, atm, calculated. No range."""

    del pressure_bar
    return _ab_atm("-24405", "8.867", temperature_K)


def _window(
    low: str | None,
    high: str | None,
    *,
    low_exclusive: bool = False,
    high_exclusive: bool = False,
) -> tuple[Decimal | None, Decimal | None, bool, bool]:
    return (
        None if low is None else Decimal(low),
        None if high is None else Decimal(high),
        low_exclusive,
        high_exclusive,
    )


def _cal(
    calibration_id: str,
    buffer: str,
    citation: str,
    provenance: str,
    phrases: tuple[str, ...],
    *,
    specific: bool,
    spread: bool,
    window: tuple[Decimal | None, Decimal | None, bool, bool],
) -> Calibration:
    low, high, low_exclusive, high_exclusive = window
    return Calibration(
        calibration_id=calibration_id,
        buffer=buffer,
        citation=citation,
        provenance=provenance,
        phrases=phrases,
        specific=specific,
        in_distinct_spread=spread,
        t_low=low,
        t_high=high,
        low_exclusive=low_exclusive,
        high_exclusive=high_exclusive,
    )


_NONE = _window(None, None)
_HUEBNER_WINDOW = _window("792.15", "1592.15")
_MYERS_WINDOW = _window("1073.15", "1573.15")

_CALIBRATIONS: tuple[Calibration, ...] = (
    _cal(
        "wolf2023_quote_oneill_pownceby1993",
        "IW",
        "O'Neill & Pownceby 1993 as quoted by Wolf 2023",
        "quoted_from_secondary",
        ("oneill and pownceby 1993", "wolf 2023"),
        specific=False,
        spread=True,
        window=_NONE,
    ),
    _cal(
        "oneill1988_eq11",
        "IW",
        "O'Neill 1988 equation (11)",
        "primary_emf",
        ("oneill 1988",),
        specific=True,
        spread=True,
        window=_window("833", "1450", low_exclusive=True, high_exclusive=True),
    ),
    _cal(
        "myers_gunter1979_iw",
        "IW",
        "Myers & Gunter 1979 IW",
        "primary_equilibrium",
        ("myers and gunter 1979", "myers gunter 1979"),
        specific=True,
        spread=True,
        window=_MYERS_WINDOW,
    ),
    _cal(
        "berry2024_eval_oneill_pownceby1993",
        "IW",
        "Berry 2024 evaluation of O'Neill & Pownceby 1993",
        "quoted_from_secondary",
        ("berry 2024",),
        specific=True,
        spread=True,
        window=_NONE,
    ),
    _cal(
        "oneill1987_mu",
        "QFM",
        "O'Neill 1987 μ_O2 QFM",
        "primary_emf",
        ("oneill 1987",),
        specific=True,
        spread=True,
        window=_window("900", "1420", low_exclusive=True, high_exclusive=True),
    ),
    _cal(
        "chou1978_eq24",
        "QFM",
        "Chou 1978 equation (24)",
        "primary_h2_sensor",
        ("chou 1978 equation 24", "chou 1978 eq 24"),
        specific=True,
        spread=True,
        window=_window("873", "1073"),
    ),
    _cal(
        "hewitt1978",
        "QFM",
        "Hewitt 1978",
        "primary_equilibrium",
        ("hewitt 1978",),
        specific=True,
        spread=True,
        window=_window("923.15", "1123.15"),
    ),
    _cal(
        "wones_gilbert1969",
        "QFM",
        "Wones & Gilbert 1969",
        "primary_equilibrium",
        ("wones and gilbert 1969", "wones gilbert 1969"),
        specific=True,
        spread=True,
        window=_window("873.15", "1373.15"),
    ),
    _cal(
        "chou1978_table6_hewitt1976",
        "QFM",
        "Hewitt 1976 as Chou 1978 table 6",
        "quoted_from_secondary",
        ("hewitt 1976",),
        specific=True,
        spread=True,
        window=_NONE,
    ),
    _cal(
        "chou1978_table6_eugster_wones1962_qfm",
        "QFM",
        "Eugster & Wones 1962 QFM as Chou 1978 table 6",
        "quoted_from_secondary",
        ("eugster and wones 1962", "eugster wones 1962"),
        specific=True,
        spread=True,
        window=_NONE,
    ),
    _cal(
        "huebner_sato1970_eq4p",
        "NNO",
        "Huebner & Sato 1970 equation (4') and abstract",
        "primary_emf",
        ("huebner and sato 1970", "huebner sato 1970", "equation 4'", "eq 4'"),
        specific=False,
        spread=True,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "huebner_sato1970_eq4",
        "NNO",
        "Huebner & Sato 1970 equation (4), IPTS-1948",
        "primary_emf_same_experiment",
        ("equation 4", "eq 4"),
        specific=True,
        spread=False,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "huebner_sato1970_eq2",
        "NNO",
        "Huebner & Sato 1970 equation (2)",
        "primary_emf_same_experiment",
        ("equation 2", "eq 2"),
        specific=True,
        spread=False,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "huebner_sato1970_eq3",
        "NNO",
        "Huebner & Sato 1970 equation (3)",
        "primary_emf_same_experiment",
        ("equation 3", "eq 3"),
        specific=True,
        spread=False,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "huebner_sato1970_eq2p",
        "NNO",
        "Huebner & Sato 1970 equation (2')",
        "primary_emf_same_experiment",
        ("equation 2'", "eq 2'"),
        specific=True,
        spread=False,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "huebner_sato1970_eq3p",
        "NNO",
        "Huebner & Sato 1970 equation (3')",
        "primary_emf_same_experiment",
        ("equation 3'", "eq 3'"),
        specific=True,
        spread=False,
        window=_HUEBNER_WINDOW,
    ),
    _cal(
        "myers_gunter1979_nno",
        "NNO",
        "Myers & Gunter 1979 NNO",
        "primary_equilibrium",
        ("myers and gunter 1979", "myers gunter 1979"),
        specific=True,
        spread=True,
        window=_MYERS_WINDOW,
    ),
    _cal(
        "oneill1987b_via_oneill1988_eq15",
        "NNO",
        "O'Neill 1987b as O'Neill 1988 equation (15)",
        "quoted_from_secondary",
        ("oneill 1987b", "oneill 1988 equation 15", "oneill 1988 eq 15"),
        specific=True,
        spread=True,
        window=_window("800", "1420", low_exclusive=True, high_exclusive=True),
    ),
    _cal(
        "chou1978_table6_eugster_wones1962_nno",
        "NNO",
        "Eugster & Wones 1962 NNO as Chou 1978 table 6",
        "quoted_from_secondary",
        ("eugster and wones 1962", "eugster wones 1962"),
        specific=True,
        spread=True,
        window=_NONE,
    ),
    _cal(
        "chou1978_table6_charette_flengas1968",
        "NNO",
        "Charette & Flengas 1968 as Chou 1978 table 6",
        "quoted_from_secondary",
        ("charette and flengas 1968", "charette flengas 1968"),
        specific=True,
        spread=True,
        window=_NONE,
    ),
)

_LOG: dict[str, BufferLog] = {
    "wolf2023_quote_oneill_pownceby1993": _log_iw_wolf,
    "oneill1988_eq11": _log_iw_oneill1988,
    "myers_gunter1979_iw": _log_iw_myers,
    "berry2024_eval_oneill_pownceby1993": _log_iw_berry,
    "oneill1987_mu": _log_qfm_oneill1987,
    "chou1978_eq24": _log_qfm_chou24,
    "hewitt1978": _log_qfm_hewitt1978,
    "wones_gilbert1969": _log_qfm_wones,
    "chou1978_table6_hewitt1976": _log_qfm_hewitt1976,
    "chou1978_table6_eugster_wones1962_qfm": _log_qfm_eugster,
    "huebner_sato1970_eq4p": _log_nno_huebner("-24930", "9.36"),
    "huebner_sato1970_eq4": _log_nno_huebner("-24810", "9.31"),
    "huebner_sato1970_eq2": _log_nno_huebner("-24930", "9.45"),
    "huebner_sato1970_eq3": _log_nno_huebner("-24760", "9.21"),
    "huebner_sato1970_eq2p": _log_nno_huebner("-24910", "9.36"),
    "huebner_sato1970_eq3p": _log_nno_huebner("-24900", "9.30"),
    "myers_gunter1979_nno": _log_nno_myers,
    "oneill1987b_via_oneill1988_eq15": _log_nno_oneill1987b,
    "chou1978_table6_eugster_wones1962_nno": _log_nno_eugster,
    "chou1978_table6_charette_flengas1968": _log_nno_charette,
}

CANONICAL_CALIBRATION_ID = {
    "IW": "wolf2023_quote_oneill_pownceby1993",
    "QFM": "oneill1987_mu",
    "NNO": "huebner_sato1970_eq4p",
}

# Chou 1978 table 6 C for FMQ, and Hewitt 1978's published C. Not a mean.
_QFM_ONEILL_VOLUME_C = Decimal("0.092")
_QFM_HEWITT_VOLUME_C = Decimal("0.110")

_BY_ID = {item.calibration_id: item for item in _CALIBRATIONS}


def calibration(calibration_id: str) -> Calibration:
    try:
        return _BY_ID[calibration_id]
    except KeyError as exc:
        raise KeyError(f"unknown buffer calibration {calibration_id}") from exc


def calibrations_for(buffer: str) -> tuple[Calibration, ...]:
    name = _buffer_name(buffer)
    return tuple(item for item in _CALIBRATIONS if item.buffer == name)


def valid_calibration_ids(buffer: str, temperature_K: Decimal) -> tuple[str, ...]:
    """Distinct-source calibrations inside their stated window at T."""

    temperature = Decimal(temperature_K)
    return tuple(
        item.calibration_id
        for item in calibrations_for(buffer)
        if item.in_distinct_spread and _in_window(item, temperature)
    )


def _buffer_name(buffer: str) -> str:
    token = buffer.strip().casefold()
    if token in {"iw"}:
        return "IW"
    if token in {"qfm", "fmq"}:
        return "QFM"
    if token in {"nno"}:
        return "NNO"
    raise KeyError(f"unknown buffer {buffer}")


def _member_log(
    item: Calibration, temperature_K: Decimal, pressure_bar: Decimal | None
) -> Decimal:
    """The printed equation only. O'Neill's borrowed C=0.092 is not applied."""

    return _LOG[item.calibration_id](temperature_K, pressure_bar)


def _band(
    buffer: str, temperature_K: Decimal, pressure_bar: Decimal | None
) -> tuple[Decimal, tuple[str, ...]]:
    members = tuple(
        item for item in calibrations_for(buffer) if item.in_distinct_spread
    )
    valid = tuple(item for item in members if _in_window(item, temperature_K))
    chosen = valid if valid else members
    logs = tuple(_member_log(item, temperature_K, pressure_bar) for item in chosen)
    width = max(logs) - min(logs) if logs else Decimal(0)
    return width, tuple(item.calibration_id for item in valid)


def evaluate_buffer(
    buffer: str,
    temperature_K: Decimal,
    *,
    pressure_bar: Decimal | None = None,
    calibration_id: str | None = None,
) -> BufferEvaluation:
    """log10(fO2/bar) = calibration(T[, P]) for one named buffer.

    ``pressure_bar is None`` omits every volume term and does not claim a
    pressure was used. Authority is certified only when the calibration
    used sits inside its stated window.
    """

    name = _buffer_name(buffer)
    temperature = Decimal(temperature_K)
    if temperature <= 0:
        raise ValueError("buffer temperature must be positive")
    chosen_id = calibration_id or CANONICAL_CALIBRATION_ID[name]
    item = calibration(chosen_id)
    if item.buffer != name:
        raise KeyError(f"{chosen_id} is not a {name} calibration")
    pressure = None if pressure_bar is None else Decimal(pressure_bar)
    log_value = _member_log(item, temperature, pressure)
    coefficient_spread = False
    if (
        item.calibration_id == "oneill1987_mu"
        and pressure is not None
        and pressure != Decimal(1)
    ):
        # Published volume stand-in for the missing O'Neill term, flagged
        # by widening the band with the two published coefficients.
        log_value += _pressure_term(_QFM_ONEILL_VOLUME_C, temperature, pressure)
        coefficient_spread = True
    width, valid_ids = _band(name, temperature, pressure)
    if coefficient_spread and pressure is not None:
        width += (
            abs(_QFM_HEWITT_VOLUME_C - _QFM_ONEILL_VOLUME_C)
            * abs(pressure - Decimal(1))
            / temperature
        )
    authority = "certified" if _in_window(item, temperature) else "extrapolated"
    return BufferEvaluation(
        buffer=name,
        calibration_id=item.calibration_id,
        log10_fO2_bar=log_value,
        temperature_K=temperature,
        pressure_bar=pressure,
        authority=authority,
        band_dex=width,
        provenance=item.provenance,
        pressure_coefficient_spread=coefficient_spread,
        valid_calibration_ids=valid_ids,
    )


def _normalize_citation(text: str) -> str:
    """Citation text to the phrase space the aliases were written in."""

    import re

    folded = (
        text.casefold()
        .replace("\u2019", "'")
        .replace("\u2032", "'")
        .replace("`", "'")
        .replace("&", " and ")
    )
    folded = re.sub(r"(?<=[a-z])'(?=[a-z])", "", folded)
    folded = re.sub(r"[^a-z0-9'+.\s]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _phrase_in(text: str, phrase: str) -> bool:
    import re

    return (
        re.search(
            r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])",
            text,
        )
        is not None
    )


def match_printed_calibration(buffer: str, text: str) -> str | None:
    """Calibration id when ``text`` names one this owner has, else None.

    Huebner (1971) does not match Huebner & Sato 1970. Two specific hits
    are ambiguous and return None so the caller uses the canonical curve.
    A longer phrase suppresses its own prefix (``equation 4'`` beats
    ``equation 4``).
    """

    try:
        pool = calibrations_for(buffer)
    except KeyError:
        return None
    normalized = _normalize_citation(text)
    if not normalized:
        return None
    hits: list[tuple[Calibration, str]] = []
    for item in pool:
        matched = [phrase for phrase in item.phrases if _phrase_in(normalized, phrase)]
        if matched:
            hits.append((item, max(matched, key=len)))
    if not hits:
        return None
    phrases = [phrase for _, phrase in hits]
    kept: list[Calibration] = []
    for item, phrase in hits:
        if any(
            other != phrase and other.startswith(phrase) and _phrase_in(normalized, other)
            for other in phrases
        ):
            continue
        kept.append(item)
    specific = [item for item in kept if item.specific]
    general = [item for item in kept if not item.specific]
    chosen = specific if specific else general
    ids = {item.calibration_id for item in chosen}
    if len(ids) != 1:
        return None
    return next(iter(ids))
