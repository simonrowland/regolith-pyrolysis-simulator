"""Solid-buffer oxygen fugacity owner for printed buffer offsets.

IW, QFM, and NNO are the d-324 curves. Mo-MoO2, MH, and MW are the
d-324 extension (t-1624): an EMF or assessed-table central inside its
window, a JANAF route above that window, and an unsized band once every
table and fit has ended.

One module owns the calibrations migration may apply. Runtime redox still
uses its own lines and must not import this module:

* ``simulator/chemistry/qfm_oneill.py`` is the linearisation ``8.58-25050/T``.
  It is not O'Neill 1987. ``engines/builtin/cco_redox_buffer.py`` wraps it.
* ``simulator/fe_redox.py`` keeps ``legacy_iw`` and Holzheid liquid FeO.
  Liquid FeO is not the solid IW buffer and is not a calibration here.
* ``simulator/core.py`` and ``simulator/offgas_fo2.py`` are runtime callers.
* ``simulator/battery/waypoints.py`` still looks up Frost
  ``PUBLISHED_BUFFERS``. Repointing it would change experiment-buffer numbers.

R for a μ fit is O'Neill 1987's 8.31441 J K^-1 mol^-1. An atmosphere equation
becomes log10(fO2/bar) by adding log10(1.01325). The brief's +0.00570 is that
shift rounded; the value used is the exact logarithm. Each function below
records premise, algebra, the unit check, and one survey-table value.

Band rule (d-324). For QFM and NNO a calibration is valid at T only inside
the authors' stated window. An unstated range does not certify, so it is not
in the valid set. The band is the max-min of log10(fO2/bar) among valid
calibrations, each evaluated at the requested pressure with its own published
pressure coefficient (zero when none was published). One valid calibration
has band 0. If none are valid, the band is the full distinct-source spread
and the authority of a calibration used there is extrapolated.

IW is the band d-324 names as ~0.01 dex. It is |Wolf minus O'Neill 1988
equation (11)| after each log is rounded to the survey's printed 0.0001.
That printed residual is 0.0067, 0.0077, and 0.0085 at 1473, 1673, and
1873 K. Myers & Gunter is not in the pair: inside 1073.15-1573.15 K it must
not collapse the adopted curve's band to 0, and outside that window it must
not re-enter through the empty-valid fallback.

QFM pressure. O'Neill 1987 publishes no volume term. At P other than 1 bar
the value uses Chou's published C = 0.092 (bar), and the band widens by
|0.110-0.092|·|P-1|/T. Hewitt 1978 already prints C = 0.110; a row that
names Hewitt uses that equation and does not add a second volume term.
Frost's coefficient is not used.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping

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
class EdgeOffset:
    """Signed log10(fO2/bar) offset between the region-2 central and one route.

    ``signed_dex`` is central minus the named route, both already rounded
    to the survey table's 0.0001. The sign does not depend on which region
    the requested temperature falls in.
    """

    route_id: str
    edge_temperature_K: Decimal
    signed_dex: Decimal


@dataclass(frozen=True)
class BufferEvaluation:
    """log10(fO2/bar) of one calibration, plus the band at that T and P.

    ``band_dex`` is None only for the extension's region 3, where no
    calibration sizes the band. ``region`` is None for IW, QFM, and NNO.
    """

    buffer: str
    calibration_id: str
    log10_fO2_bar: Decimal
    temperature_K: Decimal
    pressure_bar: Decimal | None
    authority: str
    band_dex: Decimal | None
    provenance: str
    pressure_coefficient_spread: bool
    valid_calibration_ids: tuple[str, ...]
    region: int | None = None
    edge_offsets: tuple[EdgeOffset, ...] = ()
    unsized_notice: str | None = None
    divergence_note: str | None = None


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


# d-324 extension. Reactions are written per mole of O2, so
# log10(fO2/bar) = ΔrG / (R T ln 10). The ruling's factor 2 is that
# stoichiometric normalization (a half-O2 Ellingham reaction doubled onto
# one mole of O2). It is not applied a second time.
#
# Mo-MoO2: Mo(cr) + O2 = MoO2(cr).
#   ΔrG = ΔfG(MoO2) − ΔfG(Mo). JANAF Mo-009 and Mo-002.
#   This is the formation reaction, so log10(fO2/bar) = −log10(Kf) when
#   ΔfG(Mo) is 0. Unit check against the MoO2 log-Kf column (3 d.p., the
#   table's precision), CODATA R = 8.31446261815324 J K^-1 mol^-1
#   (simulator.physical_constants.GAS_CONSTANT, those digits stored here
#   as Decimal so the float's binary expansion is not re-parsed):
#   1000 K −21.1754
#   rounds to the printed −21.176; 1400 K −12.5887 rounds to −12.589.
#   A second factor of 2 would land near −25 at 1400 K. O'Neill's fit
#   there is about −12.6, so the second factor is rejected.
# MH: 4 Fe3O4(cr) + O2 = 6 Fe2O3(cr).
#   ΔrG = 6 ΔfG(Fe2O3) − 4 ΔfG(Fe3O4). JANAF Fe-030 and Fe-032.
#   Haas & Hemingway print the decomposition; the sign above is the
#   oxidation, which is the form whose ΔrG / (R T ln 10) is log fO2.
# MW: νw Fe0.947O(cr) + O2 = νm Fe3O4(cr), y = 0.053 so Fe_(1−y)O is the
#   JANAF solid. νw = 6/(1−4y), νm = (2−2y)/(1−4y), the printed
#   magnetite–wüstite coefficients. ΔrG = νm ΔfG(Fe3O4) − νw ΔfG(Fe0.947O).
#   JANAF Fe-001 and Fe-032.
# Haas equation (7.57) misses Table 10.7 by +166 to +244 in log10. At
# 1450 K, Table 10.7 is −9.535 and the polynomial is +175.7, a +185.2
# difference. It is not a calibration.
# Sanity at 1473 K, 1 bar, survey-rounded: JANAF Mo −11.5363 against
# O'Neill 1986 −11.6492; JANAF MH −2.5237 against Haas Table 10.9
# −2.7641; JANAF MW −9.8320 against Haas Table 10.7 −9.1649.
# O'Neill μ fits keep R = 8.31441. The JANAF route uses the CODATA R.
_R_CODATA = Decimal("8.31446261815324")
# Region 3. The token is NoticeKind.BUFFER_BAND_UNSIZED_BEYOND_ALL_CALIBRATIONS.
UNSIZED_BAND_NOTICE = "buffer_band_unsized_beyond_all_calibrations"
_MW_DIVERGENCE_NOTE = (
    "O'Neill 1988 warns against extrapolating equation (16): wustite in "
    "equilibrium with magnetite changes from Fe0.945O at 833 K to Fe0.833O "
    "at 1697 K; the JANAF route holds Fe0.947O, and that non-stoichiometry "
    "is the known divergence."
)
_WUSTITE_Y = Decimal("0.053")
_WUSTITE_DENOM = 1 - 4 * _WUSTITE_Y
_NU_WUSTITE = Decimal(6) / _WUSTITE_DENOM
_NU_MAGNETITE = (Decimal(2) - 2 * _WUSTITE_Y) / _WUSTITE_DENOM
_JANAF_TABLES = Path(__file__).resolve().parents[2] / (
    "data/literature/compilations/janaf/tables"
)
_JANAF_ROWS: dict[str, tuple[tuple[Decimal, Decimal], ...]] = {}
_JANAF_TABLES_BY_CALIBRATION = {
    "janaf_mo_moo2": ("Mo-002", "Mo-009"),
    "janaf_mh_fe3o4_fe2o3": ("Fe-030", "Fe-032"),
    "janaf_mw_fe0_947o_fe3o4": ("Fe-001", "Fe-032"),
}

# Haas & Hemingway 1992, one bar. log f(O2) is linear in 1/T between
# printed nodes and exact on a node. Duplicate transition nodes in
# Table 10.9 (849.10 K, 955.53 K) are one value and are stored once.
_HAAS_10_7 = """
839.15 -26.045
850 -25.543
900 -23.404
950 -21.499
1000 -19.786
1050 -18.235
1100 -16.822
1150 -15.529
1200 -14.339
1250 -13.239
1300 -12.217
1350 -11.264
1400 -10.373
1450 -9.535
1500 -8.745
1550 -7.997
1600 -7.285
1650 -6.606
1700 -5.955
1750 -5.329
1800 -4.724
"""
_HAAS_10_9 = """
200 -112.392
250 -87.224
273.15 -78.678
298.15 -70.935
300 -70.413
350 -58.396
400 -49.382
450 -42.370
500 -36.758
550 -32.163
600 -28.327
650 -25.072
700 -22.271
750 -19.827
800 -17.667
849.10 -15.760
850 -15.727
900 -13.975
950 -12.423
955.53 -12.263
1000 -11.049
1050 -9.812
1100 -8.692
1150 -7.670
1200 -6.735
1250 -5.876
1300 -5.083
1350 -4.351
1400 -3.672
1450 -3.040
1500 -2.451
1550 -1.901
1600 -1.387
1650 -0.904
1700 -0.450
1750 -0.023
1800 0.380
"""


def _parse_log_table(text: str) -> tuple[tuple[Decimal, Decimal], ...]:
    rows: list[tuple[Decimal, Decimal]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        temperature, log_f = line.split()
        rows.append((Decimal(temperature), Decimal(log_f)))
    if len(rows) < 2:
        raise ValueError("a buffer table needs two rows")
    return tuple(rows)


_HAAS_TABLE_10_7 = _parse_log_table(_HAAS_10_7)
_HAAS_TABLE_10_9 = _parse_log_table(_HAAS_10_9)


def _dg_joule_to_log10(dg_j_mol: Decimal, temperature_K: Decimal) -> Decimal:
    """ΔrG(J mol^-1) / (R T ln 10) = log10(fO2/bar) per mole of O2.

    R is the CODATA value. R·T is J mol^-1 and ln(10) is dimensionless,
    so the quotient is the log10 of a fugacity referred to 1 bar, which
    is JANAF's p°.
    """

    return dg_j_mol / (_R_CODATA * temperature_K * _LN10)


def _liquid_onset_K(table: Mapping[str, object]) -> Decimal | None:
    """Temperature of a crystal–liquid marker that the YAML could not store.

    Rows at and above that temperature are the liquid and are not the
    pure solid the buffer reaction names. A solid–solid transition or a
    Cp lambda stays in the table.
    """

    ambiguities = table.get("parse_ambiguities")
    if not isinstance(ambiguities, list):
        return None
    for item in ambiguities:
        if not isinstance(item, Mapping):
            continue
        raw = str(item.get("raw_line") or "")
        if "LIQUID" not in raw.upper():
            continue
        token = raw.split(None, 1)[0]
        return Decimal(token)
    return None


def _janaf_dg_rows(table_id: str) -> tuple[tuple[Decimal, Decimal], ...]:
    """Solid ΔfG rows, kJ mol^-1, from the as-published JANAF cell.

    Loaded on the first extended-buffer evaluation. A missing table is
    an error: the route is not invented.
    """

    cached = _JANAF_ROWS.get(table_id)
    if cached is not None:
        return cached
    from simulator.reference_data.janaf import load_table_document

    path = _JANAF_TABLES / f"{table_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"JANAF table {table_id} is not at {path}")
    document = load_table_document(path)
    table = document.get("table")
    if not isinstance(table, Mapping):
        raise ValueError(f"{path} has no table mapping")
    onset = _liquid_onset_K(table)
    values = table.get("values")
    if not isinstance(values, list):
        raise ValueError(f"{path} has no values")
    rows: list[tuple[Decimal, Decimal]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        temperature = item.get("temperature")
        gibbs = item.get("formation_gibbs_energy")
        if not isinstance(temperature, Mapping) or not isinstance(gibbs, Mapping):
            continue
        try:
            t_value = Decimal(str(temperature.get("as_published")))
            dg_value = Decimal(str(gibbs.get("as_published")))
        except (InvalidOperation, ValueError):
            continue
        if onset is not None and t_value >= onset:
            continue
        if rows and rows[-1][0] == t_value:
            if rows[-1][1] != dg_value:
                raise ValueError(f"{table_id} repeats {t_value} K with two ΔfG values")
            continue
        rows.append((t_value, dg_value))
    if len(rows) < 2:
        raise ValueError(f"{table_id} has fewer than two solid ΔfG rows")
    rows.sort(key=lambda item: item[0])
    stored = tuple(rows)
    _JANAF_ROWS[table_id] = stored
    return stored


def _interp_segment(
    rows: tuple[tuple[Decimal, Decimal], ...],
    temperature_K: Decimal,
    *,
    extrapolate: bool,
    inv_t: bool,
) -> Decimal:
    """Linear between the bracketing rows. Exact on a printed node.

    ``inv_t`` interpolates in 1/T (Haas log f). Otherwise the abscissa is
    T (JANAF ΔfG). Outside the table the same end segment continues when
    ``extrapolate`` is set; otherwise this raises.
    """

    if temperature_K == rows[0][0]:
        return rows[0][1]
    if temperature_K == rows[-1][0]:
        return rows[-1][1]
    if rows[0][0] < temperature_K < rows[-1][0]:
        for (left_t, left_y), (right_t, right_y) in zip(rows, rows[1:], strict=True):
            if left_t < temperature_K <= right_t:
                return _segment_value(
                    left_t, left_y, right_t, right_y, temperature_K, inv_t=inv_t
                )
        raise RuntimeError(f"no bracket for {temperature_K}")
    if not extrapolate:
        raise ValueError(f"{temperature_K} is outside the printed table")
    if temperature_K > rows[-1][0]:
        left, right = rows[-2], rows[-1]
    else:
        left, right = rows[0], rows[1]
    return _segment_value(left[0], left[1], right[0], right[1], temperature_K, inv_t=inv_t)


def _segment_value(
    left_t: Decimal,
    left_y: Decimal,
    right_t: Decimal,
    right_y: Decimal,
    temperature_K: Decimal,
    *,
    inv_t: bool,
) -> Decimal:
    if inv_t:
        left_x = Decimal(1) / left_t
        right_x = Decimal(1) / right_t
        x_value = Decimal(1) / temperature_K
    else:
        left_x, right_x, x_value = left_t, right_t, temperature_K
    return left_y + (right_y - left_y) * (x_value - left_x) / (right_x - left_x)


def _table_covers(
    rows: tuple[tuple[Decimal, Decimal], ...], temperature_K: Decimal
) -> bool:
    return rows[0][0] <= temperature_K <= rows[-1][0]


def _janaf_log(
    terms: tuple[tuple[Decimal, str], ...],
    temperature_K: Decimal,
    *,
    extrapolate: bool,
) -> Decimal:
    """Σ ν_i ΔfG_i(T), kJ mol^-1, then the per-mole-O2 log10."""

    dg_kJ = Decimal(0)
    for coefficient, table_id in terms:
        rows = _janaf_dg_rows(table_id)
        dg_kJ += coefficient * _interp_segment(
            rows, temperature_K, extrapolate=extrapolate, inv_t=False
        )
    return _dg_joule_to_log10(dg_kJ * Decimal(1000), temperature_K)


def _janaf_covers(table_ids: tuple[str, ...], temperature_K: Decimal) -> bool:
    return all(_table_covers(_janaf_dg_rows(table_id), temperature_K) for table_id in table_ids)


def _calibration_covers_own_window(
    item: Calibration, temperature_K: Decimal
) -> bool:
    table_ids = _JANAF_TABLES_BY_CALIBRATION.get(item.calibration_id)
    if table_ids is not None:
        return _janaf_covers(table_ids, temperature_K)
    return _in_window(item, temperature_K)


def _log_mo_oneill1986(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill 1986 as O'Neill 1987 prints it. 1000 < T < 1450, 1 bar.

    μ = −603268 + 337.460 T − 20.6892 T ln T, J mol^-1. No volume term
    was printed, so a requested pressure does not move the curve. The
    ±140 J fit precision is about 0.005 dex at 1473 K and is not the band.
    O'Neill 2002 Table 6 quotes this equation; it is not a second calibration.
    """

    del pressure_bar
    mu = (
        Decimal("-603268")
        + Decimal("337.460") * temperature_K
        - Decimal("20.6892") * temperature_K * temperature_K.ln()
    )
    return _mu_to_log10(mu, temperature_K)


def _log_mo_janaf(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """JANAF Mo(cr) + O2 = MoO2(cr). Extrapolates past the last solid row."""

    del pressure_bar
    return _janaf_log(
        ((Decimal(1), "Mo-009"), (Decimal(-1), "Mo-002")),
        temperature_K,
        extrapolate=True,
    )


def _log_mh_haas(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Haas & Hemingway 1992 Table 10.9, 200–1800 K, one bar.

    No volume coefficient is printed for this table. Chou's C = 0.019 is
    not glued on. Past the table the last (or first) 1/T segment continues.
    """

    del pressure_bar
    return _interp_segment(
        _HAAS_TABLE_10_9,
        temperature_K,
        extrapolate=True,
        inv_t=True,
    )


def _log_mh_chou1978(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Chou 1978 equation (12), atm, then +log10(1.01325).

    He measured 873–1073 K and said equation (12) may be used from 950 K
    to extrapolate. The closed window is not stated as a certification
    bound, so selecting this curve is not a certified calibration. His
    volume coefficient is not applied.
    """

    del pressure_bar
    return _ab_atm("-26629", "15.288", temperature_K)


def _log_mh_janaf(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """JANAF 4 Fe3O4 + O2 = 6 Fe2O3."""

    del pressure_bar
    return _janaf_log(
        ((Decimal(6), "Fe-030"), (Decimal(-4), "Fe-032")),
        temperature_K,
        extrapolate=True,
    )


def _log_mw_oneill1988(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """O'Neill 1988 equation (16). 833 < T < 1270, 1 bar. No volume term.

    μ = −581927 − 65.618 T + 38.7410 T ln T. He warns that extrapolating
    is uncertain because wüstite changes from Fe0.945O at 833 K to
    Fe0.833O at 1697 K. The polynomial itself has no data above 1270 K.
    """

    del pressure_bar
    mu = (
        Decimal("-581927")
        - Decimal("65.618") * temperature_K
        + Decimal("38.7410") * temperature_K * temperature_K.ln()
    )
    return _mu_to_log10(mu, temperature_K)


def _log_mw_haas(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """Haas & Hemingway 1992 Table 10.7, 839.15–1800 K, one bar.

    The printed reaction is (2−2y)/(1−4y) Fe3O4 = 6/(1−4y) Fe_(1−y)O + O2.
    Equation (7.57) is not used. No volume coefficient. Past the table the
    end 1/T segment continues.
    """

    del pressure_bar
    return _interp_segment(
        _HAAS_TABLE_10_7,
        temperature_K,
        extrapolate=True,
        inv_t=True,
    )


def _log_mw_janaf(temperature_K: Decimal, pressure_bar: Decimal | None) -> Decimal:
    """JANAF νw Fe0.947O + O2 = νm Fe3O4, y = 0.053."""

    del pressure_bar
    return _janaf_log(
        ((_NU_MAGNETITE, "Fe-032"), (-_NU_WUSTITE, "Fe-001")),
        temperature_K,
        extrapolate=True,
    )


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
    _cal(
        "oneill1986_via_oneill1987",
        "Mo-MoO2",
        "O'Neill 1986 as printed by O'Neill 1987",
        "primary_emf",
        ("oneill 1986", "oneill 1987"),
        specific=True,
        spread=True,
        window=_window("1000", "1450", low_exclusive=True, high_exclusive=True),
    ),
    _cal(
        "janaf_mo_moo2",
        "Mo-MoO2",
        "JANAF Mo-002 Mo(cr) and Mo-009 MoO2(cr)",
        "tabulated_thermochemistry",
        ("janaf", "nist janaf"),
        specific=True,
        spread=False,
        window=_NONE,
    ),
    _cal(
        "haas1992_table_10_9",
        "MH",
        "Haas & Hemingway 1992 Table 10.9",
        "assessed_table",
        ("haas and hemingway 1992", "haas hemingway 1992", "table 10.9"),
        specific=True,
        spread=True,
        window=_window(
            str(_HAAS_TABLE_10_9[0][0]),
            str(_HAAS_TABLE_10_9[-1][0]),
        ),
    ),
    _cal(
        "chou1978_eq12",
        "MH",
        "Chou 1978 equation (12)",
        "primary_h2_sensor",
        ("chou 1978",),
        specific=True,
        spread=False,
        window=_window("950", None),
    ),
    _cal(
        "janaf_mh_fe3o4_fe2o3",
        "MH",
        "JANAF Fe-032 Fe3O4(cr) and Fe-030 Fe2O3(cr)",
        "tabulated_thermochemistry",
        ("janaf", "nist janaf"),
        specific=True,
        spread=False,
        window=_NONE,
    ),
    _cal(
        "oneill1988_eq16",
        "MW",
        "O'Neill 1988 equation (16)",
        "primary_emf",
        ("oneill 1988",),
        specific=True,
        spread=True,
        window=_window("833", "1270", low_exclusive=True, high_exclusive=True),
    ),
    _cal(
        "haas1992_table_10_7",
        "MW",
        "Haas & Hemingway 1992 Table 10.7",
        "assessed_table",
        ("haas and hemingway 1992", "haas hemingway 1992", "table 10.7"),
        specific=True,
        spread=True,
        window=_window(
            str(_HAAS_TABLE_10_7[0][0]),
            str(_HAAS_TABLE_10_7[-1][0]),
        ),
    ),
    _cal(
        "janaf_mw_fe0_947o_fe3o4",
        "MW",
        "JANAF Fe-001 Fe0.947O(cr) and Fe-032 Fe3O4(cr)",
        "tabulated_thermochemistry",
        ("janaf", "nist janaf"),
        specific=True,
        spread=False,
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
    "oneill1986_via_oneill1987": _log_mo_oneill1986,
    "janaf_mo_moo2": _log_mo_janaf,
    "haas1992_table_10_9": _log_mh_haas,
    "chou1978_eq12": _log_mh_chou1978,
    "janaf_mh_fe3o4_fe2o3": _log_mh_janaf,
    "oneill1988_eq16": _log_mw_oneill1988,
    "haas1992_table_10_7": _log_mw_haas,
    "janaf_mw_fe0_947o_fe3o4": _log_mw_janaf,
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
    if token in {"mo-moo2", "mo-moo2."}:
        return "Mo-MoO2"
    if token in {"mh", "m-h"}:
        return "MH"
    if token in {"mw"}:
        return "MW"
    raise KeyError(f"unknown buffer {buffer}")


def _member_log(
    item: Calibration, temperature_K: Decimal, pressure_bar: Decimal | None
) -> Decimal:
    """The printed equation only. O'Neill's borrowed C=0.092 is not applied."""

    return _LOG[item.calibration_id](temperature_K, pressure_bar)


# d-324: the IW band is this pair, not the distinct-source spread.
_IW_BAND_PAIR = (
    "wolf2023_quote_oneill_pownceby1993",
    "oneill1988_eq11",
)
_SURVEY_LOG_QUANTUM = Decimal("0.0001")


def _survey_log(value: Decimal) -> Decimal:
    """The survey table prints log10(fO2/bar) to 0.0001."""

    return value.quantize(_SURVEY_LOG_QUANTUM, rounding=ROUND_HALF_UP)


def _band(
    buffer: str, temperature_K: Decimal, pressure_bar: Decimal | None
) -> tuple[Decimal, tuple[str, ...]]:
    members = tuple(
        item for item in calibrations_for(buffer) if item.in_distinct_spread
    )
    valid = tuple(item for item in members if _in_window(item, temperature_K))
    valid_ids = tuple(item.calibration_id for item in valid)
    if buffer == "IW":
        # Printed 4 d.p. residual. The unrounded subtraction at 1673 K is
        # 0.00775 and would display as 0.0078; the table difference is 0.0077.
        logs = tuple(
            _survey_log(_member_log(calibration(item), temperature_K, pressure_bar))
            for item in _IW_BAND_PAIR
        )
        return abs(logs[0] - logs[1]), valid_ids
    chosen = valid if valid else members
    logs = tuple(_member_log(item, temperature_K, pressure_bar) for item in chosen)
    width = max(logs) - min(logs) if logs else Decimal(0)
    return width, valid_ids


_EXTENDED_BUFFERS = frozenset({"Mo-MoO2", "MH", "MW"})
_MO_EDGES = (Decimal(1000), Decimal(1450))
_MH_EDGE = Decimal(1073)
_MW_EDGE = Decimal(1270)
_REGION2_ROUTES = {
    "Mo-MoO2": ("janaf_mo_moo2", "oneill1986_via_oneill1987"),
    "MH": (
        "haas1992_table_10_9",
        "chou1978_eq12",
        "janaf_mh_fe3o4_fe2o3",
    ),
    "MW": (
        "haas1992_table_10_7",
        "oneill1988_eq16",
        "janaf_mw_fe0_947o_fe3o4",
    ),
}


def _signed_edge(central: Decimal, route: Decimal) -> Decimal:
    return _survey_log(central) - _survey_log(route)


def _pair_width(
    central: Decimal,
    route: Decimal,
    edge_central: Decimal,
    edge_route: Decimal,
) -> Decimal:
    """Add the irreducible edge disagreement and drift beyond that baseline.

    Let d(T) = log10(fO2/bar)_central − log10(fO2/bar)_route, rounded to
    the survey precision of 0.0001 dex. The base |d(edge)| is the disagreement
    where both routes are valid; we do not know which is right. The additional
    |d(T) - d(edge)| is drift of the extrapolated route beyond that baseline,
    the extrapolation error proper. Thus B = |d(T) - d(edge)| + |d(edge)| dex.
    B is continuous at the edge and equals |d(edge)| there. By the triangle
    inequality B >= |d(T)|, so it never understates the raw disagreement.
    It exceeds that raw gap when drift reverses the baseline sign: for MH at
    1673 K the +0.3367 dex drift opposes the −0.4907 dex baseline, giving
    |−0.1540 − (−0.4907)| + 0.4907 = 0.8274 dex. The old sum
    |d(T)| + |d(edge)| double-counted the baseline and gave 0.6447 dex
    (0.645). For MW at 1274.15 K, d(T) = +0.5214 and d(edge) = +0.5191 dex,
    so the +0.0023 dex drift gives B = 0.5214 dex; the old sum was 1.0405.
    For Mo at 1473 K, |0.1129 − 0.1150| + 0.1150 = 0.1171 dex.
    The units are dex throughout because each d is a difference of log10
    fugacities relative to 1 bar.
    """

    at_temperature = _signed_edge(central, route)
    at_edge = _signed_edge(edge_central, edge_route)
    return abs(at_temperature - at_edge) + abs(at_edge)


def _edge(route_id: str, temperature_K: Decimal, central: Decimal, route: Decimal) -> EdgeOffset:
    return EdgeOffset(
        route_id=route_id,
        edge_temperature_K=temperature_K,
        signed_dex=_signed_edge(central, route),
    )


def _member(calibration_id: str, temperature_K: Decimal) -> Decimal:
    return _LOG[calibration_id](temperature_K, None)


def _spread_band(buffer: str, temperature_K: Decimal) -> Decimal:
    """Valid-at-T spread of the printed calibrations. One valid curve is 0."""

    logs = tuple(
        _survey_log(_member(item, temperature_K))
        for item in valid_calibration_ids(buffer, temperature_K)
    )
    if len(logs) <= 1:
        return Decimal(0)
    return max(logs) - min(logs)


def _region2_band(
    buffer: str, central_calibration_id: str, temperature_K: Decimal
) -> Decimal:
    """Band for a region-2 central route against the other named curves."""

    routes = _REGION2_ROUTES[buffer]
    edge_temperature = {
        "Mo-MoO2": (
            _MO_EDGES[1] if temperature_K >= _MO_EDGES[1] else _MO_EDGES[0]
        ),
        "MH": _MH_EDGE,
        "MW": _MW_EDGE,
    }[buffer]
    central = _member(central_calibration_id, temperature_K)
    central_edge = _member(central_calibration_id, edge_temperature)
    widths = tuple(
        _pair_width(
            central,
            _member(route_id, temperature_K),
            central_edge,
            _member(route_id, edge_temperature),
        )
        for route_id in routes
        if route_id != central_calibration_id
    )
    return max(widths, default=Decimal(0))


def _evaluation(
    *,
    buffer: str,
    calibration_id: str,
    log_value: Decimal,
    temperature_K: Decimal,
    pressure_bar: Decimal | None,
    authority: str,
    band: Decimal | None,
    provenance: str,
    region: int,
    edges: tuple[EdgeOffset, ...],
    unsized: bool,
    divergence: str | None,
) -> BufferEvaluation:
    return BufferEvaluation(
        buffer=buffer,
        calibration_id=calibration_id,
        log10_fO2_bar=log_value,
        temperature_K=temperature_K,
        pressure_bar=pressure_bar,
        authority=authority,
        band_dex=band,
        provenance=provenance,
        pressure_coefficient_spread=False,
        valid_calibration_ids=valid_calibration_ids(buffer, temperature_K),
        region=region,
        edge_offsets=edges,
        unsized_notice=UNSIZED_BAND_NOTICE if unsized else None,
        divergence_note=divergence,
    )


def _evaluate_extended(
    name: str,
    temperature_K: Decimal,
    pressure_bar: Decimal | None,
    calibration_id: str | None,
) -> BufferEvaluation:
    """Mo-MoO2, MH, and MW. IW, QFM, and NNO do not enter here.

    Region 1 is the primary EMF fit inside its certified window, band equal
    to the valid-at-T spread. Region 2 is the assessed table or the JANAF
    route where every solid phase is still inside its table, band equal to
    the comparison-route pair above. Region 3 continues that central curve
    and does not invent a band width. A requested pressure is recorded and
    does not move the log: none of these curves publishes a volume term. An
    explicit calibration id applies those region and band rules to its own
    table window.
    """

    canonical = _extended_canonical(name, temperature_K, pressure_bar)
    if calibration_id is None:
        return canonical
    item = calibration(calibration_id)
    if item.buffer != name:
        raise KeyError(f"{calibration_id} is not a {name} calibration")
    in_own_window = _calibration_covers_own_window(item, temperature_K)
    if not in_own_window:
        authority = "extrapolated"
        region = 3
        band = None
        unsized = True
    elif item.provenance in {"assessed_table", "tabulated_thermochemistry"}:
        authority = "tabulated_thermochemistry"
        region = 2
        band = _region2_band(name, item.calibration_id, temperature_K)
        unsized = False
    else:
        authority = "certified"
        region = 1
        band = _spread_band(name, temperature_K)
        unsized = False
    return replace(
        canonical,
        calibration_id=item.calibration_id,
        log10_fO2_bar=_LOG[item.calibration_id](temperature_K, pressure_bar),
        authority=authority,
        provenance=item.provenance,
        region=region,
        band_dex=band,
        unsized_notice=UNSIZED_BAND_NOTICE if unsized else None,
    )


def _extended_canonical(
    name: str,
    temperature_K: Decimal,
    pressure_bar: Decimal | None,
) -> BufferEvaluation:
    if name == "Mo-MoO2":
        return _canonical_mo(temperature_K, pressure_bar)
    if name == "MH":
        return _canonical_mh(temperature_K, pressure_bar)
    if name == "MW":
        return _canonical_mw(temperature_K, pressure_bar)
    raise KeyError(f"unknown extended buffer {name}")


def _mo_edges() -> tuple[EdgeOffset, ...]:
    return tuple(
        _edge(
            "oneill1986_via_oneill1987",
            edge,
            _member("janaf_mo_moo2", edge),
            _member("oneill1986_via_oneill1987", edge),
        )
        for edge in _MO_EDGES
    )


def _canonical_mo(
    temperature_K: Decimal, pressure_bar: Decimal | None
) -> BufferEvaluation:
    edges = _mo_edges()
    in_emf = _in_window(calibration("oneill1986_via_oneill1987"), temperature_K)
    if in_emf:
        return _evaluation(
            buffer="Mo-MoO2",
            calibration_id="oneill1986_via_oneill1987",
            log_value=_member("oneill1986_via_oneill1987", temperature_K),
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            authority="certified",
            band=Decimal(0),
            provenance="primary_emf",
            region=1,
            edges=edges,
            unsized=False,
            divergence=None,
        )
    covered = _calibration_covers_own_window(
        calibration("janaf_mo_moo2"), temperature_K
    )
    janaf_log = _member("janaf_mo_moo2", temperature_K)
    if covered:
        band = _region2_band("Mo-MoO2", "janaf_mo_moo2", temperature_K)
        return _evaluation(
            buffer="Mo-MoO2",
            calibration_id="janaf_mo_moo2",
            log_value=janaf_log,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            authority="tabulated_thermochemistry",
            band=band,
            provenance="tabulated_thermochemistry",
            region=2,
            edges=edges,
            unsized=False,
            divergence=None,
        )
    return _evaluation(
        buffer="Mo-MoO2",
        calibration_id="janaf_mo_moo2",
        log_value=janaf_log,
        temperature_K=temperature_K,
        pressure_bar=pressure_bar,
        authority="extrapolated",
        band=None,
        provenance="tabulated_thermochemistry",
        region=3,
        edges=edges,
        unsized=True,
        divergence=None,
    )


def _mh_edges() -> tuple[EdgeOffset, ...]:
    haas = _member("haas1992_table_10_9", _MH_EDGE)
    return (
        _edge("chou1978_eq12", _MH_EDGE, haas, _member("chou1978_eq12", _MH_EDGE)),
        _edge(
            "janaf_mh_fe3o4_fe2o3",
            _MH_EDGE,
            haas,
            _member("janaf_mh_fe3o4_fe2o3", _MH_EDGE),
        ),
    )


def _canonical_mh(
    temperature_K: Decimal, pressure_bar: Decimal | None
) -> BufferEvaluation:
    """Haas Table 10.9 is the central through the whole printed table.

    There is no region 1. Chou's measured window does not take the centre.
    Above 1800 K the Haas 1/T segment continues and the band is unsized,
    even though Chou's equation and the JANAF route still have numbers.
    """

    edges = _mh_edges()
    haas_rows = _HAAS_TABLE_10_9
    haas_log = _member("haas1992_table_10_9", temperature_K)
    if _table_covers(haas_rows, temperature_K):
        band = _region2_band(
            "MH", "haas1992_table_10_9", temperature_K
        )
        return _evaluation(
            buffer="MH",
            calibration_id="haas1992_table_10_9",
            log_value=haas_log,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            authority="tabulated_thermochemistry",
            band=band,
            provenance="assessed_table",
            region=2,
            edges=edges,
            unsized=False,
            divergence=None,
        )
    return _evaluation(
        buffer="MH",
        calibration_id="haas1992_table_10_9",
        log_value=haas_log,
        temperature_K=temperature_K,
        pressure_bar=pressure_bar,
        authority="extrapolated",
        band=None,
        provenance="assessed_table",
        region=3,
        edges=edges,
        unsized=True,
        divergence=None,
    )


def _mw_edges() -> tuple[EdgeOffset, ...]:
    haas = _member("haas1992_table_10_7", _MW_EDGE)
    return (
        _edge("oneill1988_eq16", _MW_EDGE, haas, _member("oneill1988_eq16", _MW_EDGE)),
        _edge(
            "janaf_mw_fe0_947o_fe3o4",
            _MW_EDGE,
            haas,
            _member("janaf_mw_fe0_947o_fe3o4", _MW_EDGE),
        ),
    )


def _canonical_mw(
    temperature_K: Decimal, pressure_bar: Decimal | None
) -> BufferEvaluation:
    """O'Neill equation (16) inside his window, Haas Table 10.7 above it.

    Region 1 uses only the valid printed curves. JANAF enters in region 2,
    where the fixed Fe0.947O composition is the known divergence from
    O'Neill's variable wüstite. Above 1800 K, and below the Haas table
    when O'Neill's window has also ended, the Haas segment continues and
    the band is unsized.
    """

    edges = _mw_edges()
    oneill = calibration("oneill1988_eq16")
    haas_rows = _HAAS_TABLE_10_7
    in_oneill = _in_window(oneill, temperature_K)
    in_haas = _table_covers(haas_rows, temperature_K)
    divergence = _MW_DIVERGENCE_NOTE
    if in_oneill:
        return _evaluation(
            buffer="MW",
            calibration_id="oneill1988_eq16",
            log_value=_member("oneill1988_eq16", temperature_K),
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            authority="certified",
            band=_spread_band("MW", temperature_K),
            provenance="primary_emf",
            region=1,
            edges=edges,
            unsized=False,
            divergence=None,
        )
    haas_log = _member("haas1992_table_10_7", temperature_K)
    if in_haas and temperature_K >= _MW_EDGE:
        band = _region2_band(
            "MW", "haas1992_table_10_7", temperature_K
        )
        return _evaluation(
            buffer="MW",
            calibration_id="haas1992_table_10_7",
            log_value=haas_log,
            temperature_K=temperature_K,
            pressure_bar=pressure_bar,
            authority="tabulated_thermochemistry",
            band=band,
            provenance="assessed_table",
            region=2,
            edges=edges,
            unsized=False,
            divergence=divergence,
        )
    return _evaluation(
        buffer="MW",
        calibration_id="haas1992_table_10_7",
        log_value=haas_log,
        temperature_K=temperature_K,
        pressure_bar=pressure_bar,
        authority="extrapolated",
        band=None,
        provenance="assessed_table",
        region=3,
        edges=edges,
        unsized=True,
        divergence=divergence,
    )


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
    used sits inside its stated window. Mo-MoO2, MH, and MW are resolved
    before that rule: their region-2 authority is tabulated_thermochemistry.
    """

    name = _buffer_name(buffer)
    temperature = Decimal(temperature_K)
    if temperature <= 0:
        raise ValueError("buffer temperature must be positive")
    if name in _EXTENDED_BUFFERS:
        pressure = None if pressure_bar is None else Decimal(pressure_bar)
        return _evaluate_extended(name, temperature, pressure, calibration_id)
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
