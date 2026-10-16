"""Dependency-free O'Neill QFM evaluator shared by redox callers."""


def oneill_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return the O'Neill QFM buffer log10(fO2/bar) at ``temperature_K``.

    Source: O'Neill, H.St.C. (1987), Am. Mineral. 72, 67-75 (REF-035). The
    equation and page locus inside the paper is not yet quoted: REF-035 is
    still marked ``needs_quote`` in docs/references/references.yaml.
    T_K <= 0 is deliberately unguarded here; callers own their domain checks.
    """
    return 8.58 - 25050 / temperature_K
