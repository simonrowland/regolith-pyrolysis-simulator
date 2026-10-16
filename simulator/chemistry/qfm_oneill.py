"""Dependency-free O'Neill QFM evaluator shared by redox callers."""


def oneill_qfm_log10_fO2_bar(temperature_K: float) -> float:
    """Return O'Neill's (1987; REF-035) QFM log10(fO2/bar) expression."""
    return 8.58 - 25050 / temperature_K
