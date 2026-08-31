"""Guard the domain report against reporting clean coverage that is not there.

This instrument exists to detect models being used outside their stated domain.
That makes its own silent failure modes especially bad: a resolver that stops
finding bands reports every species as "NO BAND", and one that mis-attributes
them reports confident numbers for the wrong species. Both look like a working
report. These tests pin the two distinctions the report is built on.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "domain_report", REPO / "scripts" / "domain_report.py"
)
domain_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(domain_report)


@pytest.fixture(scope="module")
def rows():
    return domain_report._species_rows(domain_report._load_rail())


# The eleven species the bake-off sequence actually evolves. If the rail is
# reshaped so one of these stops resolving, that is a real regression in the
# instrument, not a data detail -- the report would answer "NO BAND" for a
# species whose band exists.
BAKE_OFF = ("Na", "K", "Fe", "Mg", "SiO", "Ca", "Al", "Ti", "Cr", "Mn", "Si")


def test_every_bake_off_species_resolves_to_a_band(rows):
    missing = [s for s in BAKE_OFF if s not in rows or not rows[s]["bands"]]
    assert missing == [], (
        f"species resolved no pressure-model band: {missing}. Either the rail "
        "schema moved or the resolver stopped walking it -- both make the "
        "report answer NO BAND for species that have one."
    )


def test_resolution_is_by_schema_not_by_family_name(rows):
    """The band of a neighbour is not the band of the species.

    A hand screen that matched family names by substring attributed a Na2Cl2
    halide-dimer band (1277-1406 K) to potassium. K's real models are its own.
    Pinning the negative directly: no K band may be the dimer's span.
    """
    k_bands = {(lo, hi) for lo, hi, _ in rows["K"]["bands"]}
    assert (1277.0, 1406.0) not in k_bands
    # And K resolves from a family that is actually about potassium, not one
    # that merely contains the letter.
    assert "Na2Cl2" not in rows["K"]["family"]


def test_no_band_is_a_distinct_verdict_from_extrapolated():
    """Absent evidence and measured-and-outside must not collapse together.

    NO BAND is strictly worse than EXTRAPOLATED -- an extrapolation carries a
    distance, an absent domain carries nothing -- so the report must never
    report one as the other, and must never count either as in domain.
    """
    # Bands are (low, high, evaluator_family) as the resolver emits them.
    banded = {"bands": [(1400.0, 2273.0, "antoine")], "dormant": False}
    unbanded = {"bands": [], "dormant": False}

    assert domain_report._classify(banded, 1800.0) == ("in domain", 0.0)

    verdict, distance = domain_report._classify(banded, 2473.15)
    assert verdict == "EXTRAPOLATED"
    assert distance == pytest.approx(200.15)

    verdict, distance = domain_report._classify(unbanded, 1800.0)
    assert verdict == "NO BAND"
    assert distance is None, "an absent domain has no distance to report"


def test_any_covering_model_counts_as_in_domain():
    """The engine may legitimately select whichever model covers T.

    So one covering band is enough, and the nearest miss is only reported when
    none cover. Getting this backwards would report in-domain species as
    extrapolated and bury the real ones in noise.
    """
    two = {
        "bands": [(1400.0, 1600.0, "antoine"), (1900.0, 2600.0, "shomate")],
        "dormant": False,
    }
    assert domain_report._classify(two, 2000.0)[0] == "in domain"
    # Between the two bands: outside both, and the distance is to the NEAREST
    # edge, not the first band in the list.
    verdict, distance = domain_report._classify(two, 1700.0)
    assert verdict == "EXTRAPOLATED"
    assert distance == pytest.approx(100.0)


def test_coverage_never_recovers_above_the_band_tops(rows):
    """Relationship, not a pinned count.

    ~ COVERAGE IS A WINDOW, NOT A CEILING, and an earlier version of this test
    asserted otherwise. It required the in-domain count to fall monotonically
    with temperature and went red on real data: 7 species at 1400 C but 8 at
    1600 C. Bands have LOWER bounds too -- Ti opens at 1923 K and Ca at 1757 K,
    so at 1400 C they are below their bands rather than above them. Coverage
    rises as species enter, then collapses as they exit.

    The true invariant is only about the top end: once temperature is past the
    band tops, coverage is gone and cannot come back. A resolver bug that
    widened bands with temperature would break that while leaving any pinned
    count intact.
    """
    selected = {s: rows[s] for s in BAKE_OFF}

    def in_domain(T_C: float) -> int:
        return sum(
            1
            for row in selected.values()
            if domain_report._classify(row, T_C + 273.15)[0] == "in domain"
        )

    # From the plateau upward only -- below it, species are still entering.
    upper = [in_domain(T) for T in (2000, 2100, 2200, 2400, 2600)]
    assert upper == sorted(upper, reverse=True), (
        f"in-domain count recovered above the band tops: {upper}"
    )
    assert upper[-1] == 0, "no bake-off species should be in domain at 2600 C"
    # And the window really does open from below, which is why the invariant
    # above is one-sided rather than global.
    assert in_domain(1600) >= in_domain(1400)
