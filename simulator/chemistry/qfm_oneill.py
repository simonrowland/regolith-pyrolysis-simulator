"""Dependency-free O'Neill QFM evaluator shared by redox callers."""

_ONEILL_QFM_REFERENCE_BAND_K = (900.0, 1420.0)


def _oneill_qfm_outside_reference_band(temperature_K: float) -> bool:
    """O'Neill (1987), p.73 reaction-2 fit band, also for the initial guess."""
    lower, upper = _ONEILL_QFM_REFERENCE_BAND_K
    return not lower <= temperature_K <= upper


def _magemin_corrected_buffer_offset(
    offset: float, requested_log: float, native_log: float,
) -> float:
    """Invert MAGEMin's native buffer response once, with an active buffer.

    MAGEMin src/pp_min_function.c:685 adds T*0.019145*n kJ/mol O2;
    src/toolkit.c:1898-1916 divides chemical potential by R*T*ln(10),
    with R=0.0083144 kJ/(mol K) (src/initialize.c:227). Thus T cancels,
    s=0.019145/(R*ln(10))=1.000020188593014 dex per n, and
    n'=n+(requested-native)/s. Both numerator and s are dex, so n is
    dimensionless. A -9 request read as -9.052437 needs +0.05243594 in n;
    zero miss leaves n unchanged. The caller must verify the second solve:
    linearity is conditional on the buffer remaining active.
    """
    scale = 1.000020188593014  # 0.019145 / (0.0083144 * ln(10)), derived above.
    return offset + (requested_log - native_log) / scale


def oneill_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return the O'Neill QFM buffer log10(fO2/bar) at ``temperature_K``.

    Source: PySulfSat's linearisation (s6_corrections.py:640, labelled
    "Oneill"). This line is NOT printed in O'Neill, H.St.C. (1987), Am. Mineral.
    72, 67-75 (REF-035): that paper's p. 73 gives QFM (reaction 2) as an
    unnumbered least-squares fit valid over 900-1420 K, which this line tracks
    to about 0.03 log10 between 1000 and 1420 K. Above 1420 K both forms are
    extrapolated. MAGEMin uses this line only for an initial offset and
    corrects against its own native solved fO2 (b-913).
    T_K <= 0 is deliberately unguarded here; callers own their domain checks.
    """
    return 8.58 - 25050 / temperature_K
