"""Dependency-free O'Neill QFM evaluator shared by redox callers."""


def oneill_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return the O'Neill QFM buffer log10(fO2/bar) at ``temperature_K``.

    Source: PySulfSat's linearisation (s6_corrections.py:640, labelled
    "Oneill"). This line is NOT printed in O'Neill, H.St.C. (1987), Am. Mineral.
    72, 67-75 (REF-035): that paper's p. 73 gives QFM (reaction 2) as an
    unnumbered least-squares fit valid over 900-1420 K, which this line tracks
    to about 0.03 log10 between 1000 and 1420 K. Above 1420 K both forms are
    extrapolated. The MAGEMin buffer translation that uses this line is
    tracked as b-913.
    T_K <= 0 is deliberately unguarded here; callers own their domain checks.
    """
    return 8.58 - 25050 / temperature_K
