"""Corpus-anchored test framework: fixture loader.

\\goal CHEMISTRY-E2E-TEST-REGIME (chunk 20/Phase-A)

This module walks the literature corpus at
``docs-private/deep-research/literature/<paper-id>/benchmark-fixture.yaml``
and emits :class:`CorpusAnchor` tuples. Each anchor binds a paper-derived
(temperature, melt composition, fO2, species, expected partial pressure,
tolerance-in-decades) tuple to a kernel ``VAPOR_PRESSURE`` dispatch so a
parametrized pytest can compare engine output to the literature value.

The loader is **paper-agnostic**: dropping a new ``benchmark-fixture.yaml``
with the documented schema extends the test surface without any code
change. Per §20 Phase A: drop a fixture, gain tests.

Schema contract (from `_shared/extraction-prompt-template.md` and the
6 §25 cohort-1 papers):

- ``feedstock.key``, ``feedstock.composition_wt_pct``: single-feedstock
  fixtures (SF2004, SF2018, CJ2015, etc.).
- ``expected.vapor_partial_pressures_Pa.<atom_species>``: list of
  ``{T_K, p_Pa, tolerance_decades, source}`` entries. Atom-only species
  (Na, K, Fe, Mg, ...) — keyed by the species_catalog walker.
- ``expected.vapor_partial_pressures_Pa_by_species.<compound>``: list of
  same entries for compound species (SiO, SiO2, O2, FeO, ...).
- ``expected.bulk_silicate_compositions.<body>``: per-body compositions
  for multi-melt fixtures (VF2013). Anchors carry a ``body`` key that
  selects the composition.
- ``expected.oxygen_fugacity_bar_by_body.<body>``: per-body, per-T fO2
  anchors (VF2013).
- ``expected.vapor_atomic_ratios_to_Na.<composition>.<element>``:
  atomic-ratio anchors for ``OVERHEAD_GAS_EQUILIBRIUM`` cohort tests.
- For single-feedstock fixtures, fO2 is sourced from a grid-level Kress91
  IW table (the same convention §25 v1 used). The loader carries this
  table inline so the corpus YAML stays canonical (no fO2 numbers
  duplicated into per-paper fixtures).

Convention note (resolved 2026-05-16):

- Simulator + VapoRock use **two-cation oxide** keys (Na2O, K2O, Al2O3,
  Cr2O3, ...) per ``simulator.state.OXIDE_SPECIES`` and
  ``simulator/melt_backend/vaporock.py`` lines 134-138 (verified against
  the installed VapoRock package's ``OXIDE_MOLWT`` table).
- Sossi & Fegley 2018 reports activity coefficients in **single-cation**
  form (NaO0.5, KO0.5, AlO1.5, ...). This convention difference matters
  ONLY for activity-coefficient checks (``activity_coefficient_envelopes``
  block in the SF2018 fixture, which is OUT OF SCOPE for this framework).
- All ``vapor_partial_pressures_Pa`` / ``vapor_partial_pressures_Pa_by_species``
  blocks in the §25 cohort-1 corpus store **absolute Pa**, so the loader
  passes the values through without conversion. The convention question
  documented in the dispatch prompt is therefore RESOLVED: no transform
  is needed for the partial-pressure framework.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


# ---------------------------------------------------------------------
# fO2 anchors
# ---------------------------------------------------------------------
#
# The §25 grid uses Kress & Carmichael 1991 basalt-family IW anchors
# (per ``docs-private/vapor-pressure-calibration-grid-2026-05-16.md``):
#
#   1700 K: log10(fO2/bar) = -7.46
#   1873.15 K: log10(fO2/bar) = -7.98
#   1900 K: log10(fO2/bar) = -8.061  (linear extrapolation from above)
#
# We extend the table to the temperature points the cohort-1 fixtures
# actually use (CJ2015 at 1700/1800/1900/2000 K; VF2013 at 2000-4000 K).
# For T outside [1700, 1900] we linearly extrapolate the same Kress91
# basalt line. Where a fixture provides its own per-body fO2 anchors
# (VF2013), those override.
#
# This table lives in the loader (tracked code), not in the corpus
# (gitignored docs-private), so the convention is reviewable.

_KRESS91_IW_ANCHORS: tuple[tuple[float, float], ...] = (
    (1700.0, -7.46),
    (1873.15, -7.98),
)


def _kress91_iw_log_fO2(T_K: float) -> float:
    """Linear (extrapolated) Kress91 basalt IW fO2 anchor.

    Returns ``log10(fO2/bar)`` at the requested temperature using the
    two §25 grid anchors. Linear extrapolation outside [1700, 1873.15]
    preserves the §25 grid-spec convention (where 1900 K = -8.061 by
    the same formula). Above ~2300 K the basalt-family line is no
    longer empirically supported, but the framework still emits a
    value rather than raising — anchors above 2300 K are flagged
    out-of-engine-range in the convergence narrative.
    """
    (T0, f0), (T1, f1) = _KRESS91_IW_ANCHORS
    slope = (f1 - f0) / (T1 - T0)
    return f0 + slope * (T_K - T0)


# ---------------------------------------------------------------------
# CorpusAnchor dataclass
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class CorpusAnchor:
    """A single (T, melt, fO2, species, expected_Pa, tolerance) tuple.

    Attributes:
        paper_id: Stable identifier from the fixture's top-level
            ``paper_id``. Matches the directory name.
        melt_id: ``<paper_id>:<body>`` (multi-melt fixtures) or
            ``<paper_id>:default`` (single-feedstock fixtures).
            Used as a parametrize id so failures cite the melt.
        T_K: Temperature in Kelvin.
        fO2_log: log10(fO2/bar). Either inline in the fixture (VF2013)
            or computed from the Kress91 IW table.
        species: Vapor species name (Na, SiO, O2, ...). Maps onto the
            kernel's diagnostic ``vapor_pressures_Pa`` key. Compound
            species like ``Na_plus`` are passed through; the test
            adapter decides how to project them onto the engine's
            output vocabulary.
        expected_Pa: Literature partial pressure in Pa.
        tolerance_decades: Per-entry tolerance from the fixture. Falls
            back to 1.0 (the §25 grid default) when missing.
        source: Citation string from the fixture entry.
        composition_wt_pct: Oxide composition the test must seed the
            simulator with. Two-cation convention (Na2O, K2O, Al2O3...).
    """

    paper_id: str
    melt_id: str
    T_K: float
    fO2_log: float
    species: str
    expected_Pa: float
    tolerance_decades: float
    source: str
    composition_wt_pct: Mapping[str, float] = field(
        default_factory=dict
    )

    @property
    def anchor_id(self) -> str:
        """Human-readable id for parametrize / pytest -v output."""
        return f"{self.melt_id}@{int(self.T_K)}K:{self.species}"


@dataclass(frozen=True)
class AtomicRatioAnchor:
    """A single gas-phase atomic ratio relative to another element.

    SF2004 Table 8 reports metal atom ratios in the vapor relative to
    sodium at 1900 K. The anchor carries the melt composition so the
    cohort can seed the simulator exactly like the partial-pressure
    anchors, even though the current overhead provider only consumes
    the already-populated ``process.overhead_gas`` ledger account.
    """

    paper_id: str
    melt_id: str
    composition_key: str
    T_K: float
    numerator_element: str
    denominator_element: str
    expected_ratio: float
    tolerance_decades: float
    source: str
    composition_wt_pct: Mapping[str, float] = field(
        default_factory=dict
    )

    @property
    def anchor_id(self) -> str:
        return (
            f"{self.melt_id}@{int(self.T_K)}K:"
            f"{self.numerator_element}/{self.denominator_element}"
        )


@dataclass(frozen=True)
class CJOlivineKEMSAnchor:
    """Costa-Jacobson 2015 olivine KEMS coupled pressure/flux anchor."""

    paper_id: str
    melt_id: str
    intent: str
    quantity: str
    cell_key: str
    cell_label: str
    T_K: float
    species: str
    measurement_species: str
    expected_Pa: float | None
    tolerance_decades: float
    expected_alpha: float | None
    alpha_absolute_uncertainty: float | None
    source: str
    coefficient_A: float | None
    coefficient_B_1e3K: float | None
    composition_wt_pct: Mapping[str, float] = field(
        default_factory=dict
    )

    @property
    def anchor_id(self) -> str:
        if self.quantity == "alpha":
            quantity = f"alpha({self.measurement_species})"
        else:
            quantity = f"p({self.species})"
        return (
            f"{self.melt_id}:{self.cell_label}@{int(self.T_K)}K:"
            f"{quantity}"
        )


# ---------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------

# Repo-root-relative path to the corpus. Gitignored — the loader handles
# a missing tree (no anchors yielded) without raising, so chemistry-suite
# tests that do not consume the framework still run on a fresh checkout.
_CORPUS_SUBPATH = "docs-private/deep-research/literature"


def _corpus_root(repo_root: Path | None = None) -> Path:
    """Resolve the absolute path to the corpus root.

    ``repo_root`` overrides the default (the simulator package root,
    three levels up from this file). The override exists so the
    paper-agnostic smoke test can point the loader at a synthetic
    fixture tree without copying real files.
    """
    if repo_root is not None:
        return Path(repo_root) / _CORPUS_SUBPATH
    here = Path(__file__).resolve()
    # tests/chemistry/corpus_fixtures.py → repo root is parents[2].
    return here.parents[2] / _CORPUS_SUBPATH


def _list_fixture_paths(repo_root: Path | None = None) -> list[Path]:
    """Return absolute paths of every ``benchmark-fixture.yaml`` under
    the corpus, sorted for deterministic parametrize ids."""
    root = _corpus_root(repo_root)
    if not root.exists():
        return []
    return sorted(root.glob("*/benchmark-fixture.yaml"))


# ---------------------------------------------------------------------
# Entry coercion
# ---------------------------------------------------------------------

def _coerce_float(value: Any) -> float | None:
    """Coerce a YAML scalar to float. Returns None if not coercible.

    The corpus fixtures sometimes carry float-shaped strings (``"1.78e0"``,
    ``"5.0e2"``) where pyyaml fails to autoload them as numbers because of
    the exponent format. The loader normalises them.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        try:
            v = float(value)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _entry_to_anchor_seed(
    entry: Mapping[str, Any],
) -> tuple[float, float, float] | None:
    """Project an entry into ``(T_K, p_Pa, tolerance_decades)``.

    Returns ``None`` when the entry is missing the required fields,
    so qualitative-only or partially-extracted fixtures contribute no
    anchors. Tolerance defaults to 1.0 decade (the §25 grid default)
    when the entry omits it.
    """
    T_K = _coerce_float(entry.get("T_K"))
    p_Pa = _coerce_float(entry.get("p_Pa"))
    if T_K is None or p_Pa is None or p_Pa <= 0.0:
        return None
    tol = _coerce_float(entry.get("tolerance_decades"))
    if tol is None or tol <= 0.0:
        tol = 1.0
    return (T_K, p_Pa, tol)


def _atomic_ratio_seed(
    element: str,
    payload: Any,
    default_tolerance_decades: float = 0.05,
) -> tuple[float, float, str] | None:
    """Project an atomic-ratio payload into ``(ratio, tolerance, source)``."""

    source = ""
    if isinstance(payload, Mapping):
        ratio = _coerce_float(payload.get("value"))
        tol = _coerce_float(payload.get("tolerance_decades"))
        source = str(payload.get("source") or "")
    else:
        ratio = _coerce_float(payload)
        tol = None
    if ratio is None or ratio <= 0.0:
        return None
    if tol is None or tol <= 0.0:
        tol = default_tolerance_decades
    if not source:
        source = f"atomic ratio row {element}"
    return (float(ratio), float(tol), source)


def _fixture_intents(expected: Mapping[str, Any]) -> set[str]:
    raw = expected.get("intents_exercised")
    values: list[Any]
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    intents = {str(value).upper() for value in values}

    # Backfill older fixtures that predate explicit intent routing. The
    # explicit field wins when present; this only preserves auto-extension
    # for the current corpus and synthetic fixtures.
    if not intents:
        if (
            expected.get("vapor_partial_pressures_Pa")
            or expected.get("vapor_partial_pressures_Pa_by_species")
            or expected.get("clausius_clapeyron_equations")
        ):
            intents.add("VAPOR_PRESSURE")
        if expected.get("vaporization_coefficients"):
            intents.add("EVAPORATION_FLUX")
        if expected.get("vapor_atomic_ratios_to_Na"):
            intents.add("OVERHEAD_GAS_EQUILIBRIUM")
    return intents


# ---------------------------------------------------------------------
# Single-feedstock vs multi-melt projection
# ---------------------------------------------------------------------

def _composition_wt_pct(
    feedstock: Mapping[str, Any],
) -> dict[str, float]:
    """Extract the oxide ``composition_wt_pct`` dict, dropping zero /
    missing entries. The simulator's ``load_batch`` rejects feedstocks
    with zero total composition, so the loader filters those out at
    source.
    """
    comp = dict(feedstock.get("composition_wt_pct") or {})
    out: dict[str, float] = {}
    for oxide, value in comp.items():
        v = _coerce_float(value)
        if v is not None and v > 0.0:
            out[str(oxide)] = v
    return out


def _multi_melt_compositions(
    expected: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    """Project ``bulk_silicate_compositions`` onto ``body → wt_pct`` dict.

    Drops ``notes`` / ``tolerance_decades`` and ``source`` annotations.
    Used by VF2013 (and any future multi-melt fixture). Returns an empty
    dict when the block is missing or has no usable composition rows.
    """
    bsc = expected.get("bulk_silicate_compositions") or {}
    out: dict[str, dict[str, float]] = {}
    for body, payload in bsc.items():
        if not isinstance(payload, Mapping):
            continue
        comp: dict[str, float] = {}
        for k, v in payload.items():
            if k in ("notes", "source", "total_wt_pct", "Mg_mol_pct",
                      "tolerance_decades", "Mg_number"):
                continue
            f = _coerce_float(v)
            if f is not None and f > 0.0:
                comp[str(k)] = f
        if comp:
            out[str(body)] = comp
    return out


# SF2004 Table 8 carries five composition columns, but the
# simulator-native fixture's top-level feedstock is the tholeiite column
# only. Keep the Table 5 rows here so the atomic-ratio cohort can
# construct every Table 8 melt while still reading the ratios themselves
# from the corpus fixture.
SF2004_TABLE_5_COMPOSITIONS: dict[str, dict[str, float]] = {
    "tholeiite": {
        "SiO2": 50.71,
        "MgO": 4.68,
        "Al2O3": 14.48,
        "TiO2": 1.70,
        "Fe2O3": 4.89,
        "FeO": 9.07,
        "CaO": 8.83,
        "Na2O": 3.16,
        "K2O": 0.77,
    },
    "alkali_basalt": {
        "SiO2": 44.80,
        "MgO": 11.07,
        "Al2O3": 13.86,
        "TiO2": 1.96,
        "Fe2O3": 2.91,
        "FeO": 9.63,
        "CaO": 10.16,
        "Na2O": 3.19,
        "K2O": 1.09,
    },
    "komatiite": {
        "SiO2": 47.10,
        "MgO": 29.60,
        "Al2O3": 4.04,
        "TiO2": 0.24,
        "Fe2O3": 12.80,
        "FeO": 0.0,
        "CaO": 5.44,
        "Na2O": 0.46,
        "K2O": 0.09,
    },
    "dunite": {
        "SiO2": 40.20,
        "MgO": 43.20,
        "Al2O3": 0.80,
        "TiO2": 0.20,
        "Fe2O3": 1.90,
        "FeO": 11.90,
        "CaO": 0.80,
        "Na2O": 0.30,
        "K2O": 0.10,
    },
    "type_B1_CAI": {
        "SiO2": 29.10,
        "MgO": 10.20,
        "Al2O3": 29.60,
        "TiO2": 1.30,
        "Fe2O3": 0.0,
        "FeO": 0.60,
        "CaO": 28.80,
        "Na2O": 0.18,
        "K2O": 0.10,
    },
}


def _multi_melt_fO2_log(
    expected: Mapping[str, Any], body: str, T_K: float,
) -> float | None:
    """Look up per-body fO2 from ``oxygen_fugacity_bar_by_body``.

    Returns ``None`` when the fixture has no entry for this body. The
    caller then falls back to the Kress91 IW table. The lookup matches
    on exact ``T_K`` first; if absent, it linearly interpolates between
    the two bracketing anchors. fO2 is converted from bar to
    log10(fO2/bar) — the simulator's canonical channel.
    """
    block = expected.get("oxygen_fugacity_bar_by_body") or {}
    rows = block.get(body)
    if not isinstance(rows, list) or not rows:
        return None
    points: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        T = _coerce_float(row.get("T_K"))
        fO2 = _coerce_float(row.get("fO2_bar"))
        if T is None or fO2 is None or fO2 <= 0.0:
            continue
        points.append((T, math.log10(fO2)))
    if not points:
        return None
    # Exact match first
    for T, log_f in points:
        if abs(T - T_K) < 0.5:
            return log_f
    # Bracket interpolation
    points.sort()
    if T_K <= points[0][0]:
        return points[0][1]
    if T_K >= points[-1][0]:
        return points[-1][1]
    for (T0, f0), (T1, f1) in zip(points, points[1:]):
        if T0 <= T_K <= T1:
            slope = (f1 - f0) / (T1 - T0)
            return f0 + slope * (T_K - T0)
    return None


# ---------------------------------------------------------------------
# Main entry: load all anchors
# ---------------------------------------------------------------------

def load_all_corpus_anchors(
    *,
    repo_root: Path | None = None,
) -> list[CorpusAnchor]:
    """Walk the corpus, return every (T, melt, fO2, species, p, tol) anchor.

    Paper-agnostic: every ``benchmark-fixture.yaml`` under the corpus
    is read. Fixtures whose ``expected.vapor_partial_pressures_Pa`` and
    ``expected.vapor_partial_pressures_Pa_by_species`` blocks are both
    empty contribute zero anchors (e.g. sossi-2018-moon-volatile-loss-cr
    is Cr-isotope only; sesko-2022 is qualitative only). The walker also
    skips entries whose ``p_Pa`` is missing or non-positive, so partially
    extracted fixtures contribute only the entries that carry numeric
    partial pressures.
    """
    anchors: list[CorpusAnchor] = []
    for path in _list_fixture_paths(repo_root):
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, Mapping):
            continue
        paper_id = str(data.get("paper_id") or path.parent.name)
        expected = data.get("expected") or {}
        if not isinstance(expected, Mapping):
            continue

        # Multi-melt projection (VF2013): each entry carries a ``body``
        # key that selects a composition from ``bulk_silicate_compositions``.
        body_compositions = _multi_melt_compositions(expected)

        # Single-feedstock projection: fall back to the top-level feedstock
        # composition when the fixture has no per-body block or when an
        # entry has no ``body`` key.
        feedstock = data.get("feedstock") or {}
        if not isinstance(feedstock, Mapping):
            feedstock = {}
        default_comp = _composition_wt_pct(feedstock)
        default_melt_label = str(feedstock.get("key") or paper_id)

        for block_name in (
            "vapor_partial_pressures_Pa",
            "vapor_partial_pressures_Pa_by_species",
        ):
            block = expected.get(block_name) or {}
            if not isinstance(block, Mapping):
                continue
            for species, entries in block.items():
                if not isinstance(entries, list):
                    # Skip ``note`` / ``notes`` and qualitative dict
                    # entries — they don't represent a numeric anchor.
                    continue
                if str(species).lower() in ("note", "notes"):
                    continue
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    seed = _entry_to_anchor_seed(entry)
                    if seed is None:
                        continue
                    T_K, p_Pa, tol = seed

                    body = entry.get("body")
                    if isinstance(body, str) and body in body_compositions:
                        composition = body_compositions[body]
                        melt_id = f"{paper_id}:{body}"
                        fO2_log = (
                            _multi_melt_fO2_log(expected, body, T_K)
                            if expected.get(
                                "oxygen_fugacity_bar_by_body")
                            else None
                        )
                        if fO2_log is None:
                            fO2_log = _kress91_iw_log_fO2(T_K)
                    else:
                        composition = default_comp
                        melt_id = f"{paper_id}:{default_melt_label}"
                        fO2_log = _kress91_iw_log_fO2(T_K)

                    if not composition:
                        # No usable melt composition for this anchor —
                        # the engine cannot be invoked without one.
                        continue

                    anchors.append(
                        CorpusAnchor(
                            paper_id=paper_id,
                            melt_id=melt_id,
                            T_K=T_K,
                            fO2_log=fO2_log,
                            species=str(species),
                            expected_Pa=p_Pa,
                            tolerance_decades=tol,
                            source=str(entry.get("source") or ""),
                            composition_wt_pct=composition,
                        )
                    )

    return anchors


def _atomic_ratio_compositions(
    paper_id: str,
    expected: Mapping[str, Any],
    default_label: str,
    default_comp: Mapping[str, float],
) -> dict[str, dict[str, float]]:
    compositions = _multi_melt_compositions(expected)
    if paper_id == "schaefer-fegley-2004-io-lava":
        compositions.update({
            key: dict(value)
            for key, value in SF2004_TABLE_5_COMPOSITIONS.items()
        })
    if default_comp:
        compositions.setdefault(default_label, dict(default_comp))
    return compositions


def load_all_atomic_ratio_anchors(
    *,
    repo_root: Path | None = None,
) -> list[AtomicRatioAnchor]:
    """Walk the corpus and return gas-phase atomic-ratio anchors."""

    anchors: list[AtomicRatioAnchor] = []
    for path in _list_fixture_paths(repo_root):
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, Mapping):
            continue
        paper_id = str(data.get("paper_id") or path.parent.name)
        expected = data.get("expected") or {}
        if not isinstance(expected, Mapping):
            continue
        ratio_block = expected.get("vapor_atomic_ratios_to_Na") or {}
        if not isinstance(ratio_block, Mapping):
            continue

        feedstock = data.get("feedstock") or {}
        if not isinstance(feedstock, Mapping):
            feedstock = {}
        default_comp = _composition_wt_pct(feedstock)
        default_label = str(feedstock.get("key") or paper_id)
        compositions = _atomic_ratio_compositions(
            paper_id,
            expected,
            default_label,
            default_comp,
        )

        T_K = _coerce_float(ratio_block.get("T_K"))
        if T_K is None:
            continue
        metadata_keys = {
            "T_K",
            "fractional_vaporization_pct",
            "notes",
            "note",
            "source",
            "tolerance_decades",
        }
        default_tol = _coerce_float(ratio_block.get("tolerance_decades"))
        if default_tol is None or default_tol <= 0.0:
            default_tol = 0.05

        for composition_key, ratios in ratio_block.items():
            if composition_key in metadata_keys:
                continue
            if not isinstance(ratios, Mapping):
                continue
            composition = compositions.get(str(composition_key))
            if not composition:
                continue
            for element, payload in ratios.items():
                if str(element).lower() in ("note", "notes", "source"):
                    continue
                seed = _atomic_ratio_seed(
                    str(element),
                    payload,
                    default_tolerance_decades=default_tol,
                )
                if seed is None:
                    continue
                ratio, tol, source = seed
                anchors.append(
                    AtomicRatioAnchor(
                        paper_id=paper_id,
                        melt_id=f"{paper_id}:{composition_key}",
                        composition_key=str(composition_key),
                        T_K=T_K,
                        numerator_element=str(element),
                        denominator_element="Na",
                        expected_ratio=ratio,
                        tolerance_decades=tol,
                        source=source,
                        composition_wt_pct=dict(composition),
                    )
                )
    return anchors


def sf2004_table8_atomic_ratio_anchors() -> list[AtomicRatioAnchor]:
    return [
        anchor for anchor in load_all_atomic_ratio_anchors()
        if anchor.paper_id == "schaefer-fegley-2004-io-lava"
    ]


# ---------------------------------------------------------------------
# Costa-Jacobson 2015 olivine KEMS coupled pressure/flux cohort
# ---------------------------------------------------------------------

_CJ_OLIVINE_CELL_LABELS = {
    "ir_cell": "Ir",
    "mo_cell": "Mo",
    "re_cell_piacente_1975": "Re",
}


def _cc_pressure_pa(coefficients: Mapping[str, Any], T_K: float) -> float | None:
    """Evaluate ``log10(P/kPa) = A - B*1000/T`` as Pa."""
    A = _coerce_float(coefficients.get("A"))
    B = _coerce_float(coefficients.get("B_1e3K"))
    if A is None or B is None or T_K <= 0.0:
        return None
    return (10.0 ** (A - (B * 1000.0 / T_K))) * 1000.0


def _cc_tolerance_decades(coefficients: Mapping[str, Any]) -> float:
    sigma = _coerce_float(coefficients.get("A_sigma"))
    if sigma is None or sigma <= 0.0:
        sigma = 0.15
    return float(sigma)


def _cj_pressure_temperatures(expected: Mapping[str, Any]) -> list[float]:
    temperatures: set[float] = set()
    for block_name in (
        "vapor_partial_pressures_Pa",
        "vapor_partial_pressures_Pa_by_species",
    ):
        block = expected.get(block_name) or {}
        if not isinstance(block, Mapping):
            continue
        for entries in block.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                T_K = _coerce_float(entry.get("T_K"))
                if T_K is not None:
                    temperatures.add(T_K)
    return sorted(temperatures)


def _emit_cj_olivine_pressure_grid(
    *,
    paper_id: str,
    melt_id: str,
    expected: Mapping[str, Any],
    composition: Mapping[str, float],
) -> list[CJOlivineKEMSAnchor]:
    cc = expected.get("clausius_clapeyron_equations") or {}
    if not isinstance(cc, Mapping):
        return []
    temperatures = _cj_pressure_temperatures(expected)
    if not temperatures:
        temperatures = [1700.0, 1800.0, 1900.0, 2000.0]

    anchors: list[CJOlivineKEMSAnchor] = []
    for cell_key, cell_label in _CJ_OLIVINE_CELL_LABELS.items():
        cell = cc.get(cell_key) or {}
        if not isinstance(cell, Mapping):
            continue
        for species in ("Mg", "SiO", "Fe"):
            coefficients = cell.get(species) or {}
            if not isinstance(coefficients, Mapping):
                continue
            A = _coerce_float(coefficients.get("A"))
            B = _coerce_float(coefficients.get("B_1e3K"))
            tolerance = _cc_tolerance_decades(coefficients)
            for T_K in temperatures:
                pressure = _cc_pressure_pa(coefficients, T_K)
                if pressure is None or pressure <= 0.0:
                    continue
                anchors.append(
                    CJOlivineKEMSAnchor(
                        paper_id=paper_id,
                        melt_id=melt_id,
                        intent="VAPOR_PRESSURE",
                        quantity="partial_pressure_Pa",
                        cell_key=cell_key,
                        cell_label=cell_label,
                        T_K=T_K,
                        species=species,
                        measurement_species=species,
                        expected_Pa=pressure,
                        tolerance_decades=tolerance,
                        expected_alpha=None,
                        alpha_absolute_uncertainty=None,
                        source=(
                            f"Costa-Jacobson 2015 {cell_label}-cell "
                            f"C-C equation for {species}"
                        ),
                        coefficient_A=A,
                        coefficient_B_1e3K=B,
                        composition_wt_pct=dict(composition),
                    )
                )
    return anchors


def _emit_cj_olivine_alpha_grid(
    *,
    paper_id: str,
    melt_id: str,
    expected: Mapping[str, Any],
    composition: Mapping[str, float],
) -> list[CJOlivineKEMSAnchor]:
    cc = expected.get("clausius_clapeyron_equations") or {}
    coeffs = expected.get("vaporization_coefficients") or {}
    if not isinstance(cc, Mapping) or not isinstance(coeffs, Mapping):
        return []

    anchors: list[CJOlivineKEMSAnchor] = []
    for species, entries in coeffs.items():
        if not isinstance(entries, list):
            continue
        measurement_species = f"{species}+"
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            T_K = _coerce_float(entry.get("T_K"))
            alpha = _coerce_float(entry.get("alpha"))
            uncertainty = _coerce_float(
                entry.get("alpha_absolute_uncertainty")
            )
            if T_K is None or alpha is None or alpha <= 0.0:
                continue
            if uncertainty is None or uncertainty <= 0.0:
                uncertainty = 0.002
            for cell_key, cell_label in _CJ_OLIVINE_CELL_LABELS.items():
                cell = cc.get(cell_key) or {}
                if not isinstance(cell, Mapping):
                    continue
                coefficients = cell.get(str(species)) or {}
                if not isinstance(coefficients, Mapping):
                    continue
                pressure = _cc_pressure_pa(coefficients, T_K)
                if pressure is None or pressure <= 0.0:
                    continue
                anchors.append(
                    CJOlivineKEMSAnchor(
                        paper_id=paper_id,
                        melt_id=melt_id,
                        intent="EVAPORATION_FLUX",
                        quantity="alpha",
                        cell_key=cell_key,
                        cell_label=cell_label,
                        T_K=T_K,
                        species=str(species),
                        measurement_species=measurement_species,
                        expected_Pa=pressure,
                        tolerance_decades=_cc_tolerance_decades(
                            coefficients
                        ),
                        expected_alpha=alpha,
                        alpha_absolute_uncertainty=uncertainty,
                        source=(
                            f"{entry.get('source') or 'alpha digitization'}; "
                            f"{cell_label}-cell C-C pressure basis"
                        ),
                        coefficient_A=_coerce_float(coefficients.get("A")),
                        coefficient_B_1e3K=_coerce_float(
                            coefficients.get("B_1e3K")
                        ),
                        composition_wt_pct=dict(composition),
                    )
                )
    return anchors


def _emit_cj_olivine_kems_grid(
    *,
    paper_id: str,
    melt_id: str,
    expected: Mapping[str, Any],
    composition: Mapping[str, float],
) -> list[CJOlivineKEMSAnchor]:
    intents = _fixture_intents(expected)
    anchors: list[CJOlivineKEMSAnchor] = []
    if "VAPOR_PRESSURE" in intents:
        anchors.extend(
            _emit_cj_olivine_pressure_grid(
                paper_id=paper_id,
                melt_id=melt_id,
                expected=expected,
                composition=composition,
            )
        )
    if "EVAPORATION_FLUX" in intents:
        anchors.extend(
            _emit_cj_olivine_alpha_grid(
                paper_id=paper_id,
                melt_id=melt_id,
                expected=expected,
                composition=composition,
            )
        )
    return anchors


def load_all_cj_olivine_kems_anchors(
    *,
    repo_root: Path | None = None,
) -> list[CJOlivineKEMSAnchor]:
    """Walk fixtures and emit olivine-coupled KEMS pressure/flux anchors."""

    anchors: list[CJOlivineKEMSAnchor] = []
    for path in _list_fixture_paths(repo_root):
        try:
            data = yaml.safe_load(path.read_text())
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, Mapping):
            continue
        expected = data.get("expected") or {}
        if not isinstance(expected, Mapping):
            continue
        if (
            not expected.get("clausius_clapeyron_equations")
            or not expected.get("vaporization_coefficients")
        ):
            continue

        feedstock = data.get("feedstock") or {}
        if not isinstance(feedstock, Mapping):
            feedstock = {}
        composition = _composition_wt_pct(feedstock)
        if not composition:
            continue
        paper_id = str(data.get("paper_id") or path.parent.name)
        melt_label = str(feedstock.get("key") or paper_id)
        anchors.extend(
            _emit_cj_olivine_kems_grid(
                paper_id=paper_id,
                melt_id=f"{paper_id}:{melt_label}",
                expected=expected,
                composition=composition,
            )
        )
    return anchors


# ---------------------------------------------------------------------
# §25 grid acceptance subset
# ---------------------------------------------------------------------
#
# The §25 cohort-1 grid (§ docs-private/vapor-pressure-calibration-grid-
# 2026-05-16.md) started as a 2 T × 3 melts × 5 species = 30 point surface:
#
#   T ∈ {1700, 1900} K
#   melt ∈ {tholeiite, lunar_mare_basalt_12022_proxy, EAC-1A}
#   species ∈ {SiO, Na, SiO2, O2, Mg}
#
# v2 proved 10 of those 30 cells are unreachable from the populated
# corpus. v3 keeps the 20 corpus-backed v2 cells and substitutes the
# 10 blocked cells with fixture-backed partial-pressure anchors from
# Costa-Jacobson 2015, Schaefer-Fegley 2004, Sossi-Fegley 2018, and
# Visscher-Fegley 2013. The grid remains exactly 30 anchors and keeps
# the fixed 1-decade pass tolerance.
#
# This subset selects anchors from the corpus that map onto the §25
# grid cells. The mapping uses the §25 v1 feedstock keys (tholeiite,
# lunar_mare_basalt_12022_proxy, eac1a) and the §25 v1 reference
# compositions, NOT the per-paper feedstock keys, because the §25 grid
# is a curated cross-paper comparison. The framework's own broader
# parametrization (every corpus anchor, not just §25's 30) lands as
# a separate test surface.

_GRID_25_TEMPERATURES_K = (1700.0, 1900.0)
_GRID_25_SPECIES = ("SiO", "Na", "SiO2", "O2", "Mg")

# Mapping: (T_K, species) → expected_Pa from the §25 v2 grid spec.
# Sourced from docs-private/vapor-pressure-calibration-grid-2026-05-16.md
# (committed corpus extraction). Three melts:
#   tholeiite → Schaefer & Fegley 2004 Table 9 HK back-solve
#   lunar_mare_basalt_12022_proxy → Sossi & Fegley 2018 Fig 3 digitization
#   EAC-1A → blocked except O2 (Kress91 IW anchor only)
# ``None`` entries are v2 blocked cells that v3 substitutes below.

_GRID_25: dict[str, dict[tuple[float, str], float | None]] = {
    "tholeiite": {
        (1700.0, "SiO"): 1.6624e-4,
        (1700.0, "Na"): 5.9576e-1,
        (1700.0, "SiO2"): 2.0015e-5,
        (1700.0, "O2"): 1.4695e-1,
        (1700.0, "Mg"): 5.1612e-6,
        (1900.0, "SiO"): 1.3071e-2,
        (1900.0, "Na"): 6.0841e+0,
        (1900.0, "SiO2"): 1.1875e-3,
        (1900.0, "O2"): 1.4786e+0,
        (1900.0, "Mg"): 2.8465e-4,
    },
    "lunar_mare_basalt_12022_proxy": {
        (1700.0, "SiO"): 3.8909e-2,
        (1700.0, "Na"): 1.2058e-2,
        (1700.0, "SiO2"): None,  # blocked — SF2018 has no SiO2
        # O2 reference is the Kress91 IW anchor itself: p(O2)_Pa =
        # 10**fO2_log_iw * 1e5 (bar → Pa). The §25 grid spec carries
        # this as a numeric anchor; the engine should reproduce the
        # requested fO2 directly. Useful as a trivial sanity check
        # that the fO2 channel is plumbed correctly through the
        # dispatch path.
        (1700.0, "O2"): 10.0 ** _kress91_iw_log_fO2(1700.0) * 1e5,
        (1700.0, "Mg"): 1.8593e-2,
        (1900.0, "SiO"): 1.5490e-1,
        (1900.0, "Na"): 1.7033e-2,
        (1900.0, "SiO2"): None,
        (1900.0, "O2"): 10.0 ** _kress91_iw_log_fO2(1900.0) * 1e5,
        (1900.0, "Mg"): 3.7097e-2,
    },
    "EAC-1A": {
        # Sesko 2022 publishes no numeric partial pressures (Fig 4.x
        # is mass-fraction qualitative only). The §25 grid spec keeps
        # 8 of 10 EAC-1A cells blocked. O2 uses the Kress91 IW anchor
        # (same as lunar) so the fO2-channel plumbing check still runs.
        (1700.0, "SiO"): None,
        (1700.0, "Na"): None,
        (1700.0, "SiO2"): None,
        (1700.0, "O2"): 10.0 ** _kress91_iw_log_fO2(1700.0) * 1e5,
        (1700.0, "Mg"): None,
        (1900.0, "SiO"): None,
        (1900.0, "Na"): None,
        (1900.0, "SiO2"): None,
        (1900.0, "O2"): 10.0 ** _kress91_iw_log_fO2(1900.0) * 1e5,
        (1900.0, "Mg"): None,
    },
}


_GRID_25_V3_SUBSTITUTIONS: dict[str, dict[str, Any]] = {
    "grid-25:EAC-1A@1700K:Mg": {
        "kind": "cj",
        "cell_id": "cj_fo93fa7_ir_cell",
        "cell_label": "Ir",
        "T_K": 1700.0,
        "species": "Mg",
    },
    "grid-25:EAC-1A@1900K:Mg": {
        "kind": "cj",
        "cell_id": "cj_fo93fa7_mo_cell",
        "cell_label": "Mo",
        "T_K": 1900.0,
        "species": "Mg",
    },
    "grid-25:EAC-1A@1700K:SiO": {
        "kind": "corpus",
        "cell_id": "sf2018_fig3_sio_1773",
        "paper_id": "sossi-fegley-2018-volatility",
        "T_K": 1773.0,
        "species": "SiO",
        "expected_Pa": 1.53e-2,
    },
    "grid-25:EAC-1A@1900K:SiO": {
        "kind": "corpus",
        "cell_id": "sf2018_fig3_sio_1873",
        "paper_id": "sossi-fegley-2018-volatility",
        "T_K": 1873.0,
        "species": "SiO",
        "expected_Pa": 2.82e-1,
    },
    "grid-25:EAC-1A@1700K:Na": {
        "kind": "corpus",
        "cell_id": "sf2004_tholeiite_1550_na",
        "paper_id": "schaefer-fegley-2004-io-lava",
        "T_K": 1550.0,
        "species": "Na",
        "expected_Pa": 7.17e-2,
    },
    "grid-25:EAC-1A@1900K:Na": {
        "kind": "corpus",
        "cell_id": "vf2013_mars_2000_na",
        "paper_id": "visscher-fegley-2013-debris-disks",
        "T_K": 2000.0,
        "species": "Na",
        "expected_Pa": 80.0,
    },
    "grid-25:EAC-1A@1700K:SiO2": {
        "kind": "corpus",
        "cell_id": "sf2004_tholeiite_1550_sio2_for_eac",
        "paper_id": "schaefer-fegley-2004-io-lava",
        "T_K": 1550.0,
        "species": "SiO2",
        "expected_Pa": 4.73e-7,
    },
    "grid-25:EAC-1A@1900K:SiO2": {
        "kind": "corpus",
        "cell_id": "sf2004_tholeiite_1700_sio2_for_eac",
        "paper_id": "schaefer-fegley-2004-io-lava",
        "T_K": 1700.0,
        "species": "SiO2",
        "expected_Pa": 2.0e-5,
    },
    "grid-25:lunar_mare_basalt_12022_proxy@1700K:SiO2": {
        "kind": "corpus",
        "cell_id": "sf2004_tholeiite_1900_sio2_for_lunar",
        "paper_id": "schaefer-fegley-2004-io-lava",
        "T_K": 1900.0,
        "species": "SiO2",
        "expected_Pa": 1.19e-3,
    },
    "grid-25:lunar_mare_basalt_12022_proxy@1900K:SiO2": {
        "kind": "corpus",
        "cell_id": "sf2004_tholeiite_1550_sio2_for_lunar",
        "paper_id": "schaefer-fegley-2004-io-lava",
        "T_K": 1550.0,
        "species": "SiO2",
        "expected_Pa": 4.73e-7,
    },
}


def _grid_25_cell_id(melt_key: str, T_K: float, species: str) -> str:
    return f"grid-25:{melt_key}@{int(T_K)}K:{species}"


def _grid_25_v3_source(old_cell_id: str, source: str) -> str:
    return f"§25 v3 substitute for {old_cell_id}: {source}"


def _grid_25_v3_matches_expected(
    anchor: CorpusAnchor,
    selector: Mapping[str, Any],
) -> bool:
    expected = selector.get("expected_Pa")
    if expected is None:
        return True
    return abs(anchor.expected_Pa - float(expected)) <= (
        max(abs(float(expected)), 1.0) * 1e-9
    )


def _grid_25_v3_cj_substitute(
    old_cell_id: str,
    selector: Mapping[str, Any],
    repo_root: Path | None,
) -> CorpusAnchor | None:
    for anchor in load_all_cj_olivine_kems_anchors(repo_root=repo_root):
        if anchor.intent != "VAPOR_PRESSURE":
            continue
        if anchor.quantity != "partial_pressure_Pa":
            continue
        if anchor.cell_label != selector["cell_label"]:
            continue
        if anchor.T_K != selector["T_K"]:
            continue
        if anchor.species != selector["species"]:
            continue
        if anchor.expected_Pa is None or anchor.expected_Pa <= 0.0:
            continue
        source = (
            f"{anchor.source}; KEMS Mg pressure, fO2 channel is a "
            "simulator dispatch convention only"
        )
        return CorpusAnchor(
            paper_id="grid-25",
            melt_id=f"grid-25:{selector['cell_id']}",
            T_K=anchor.T_K,
            fO2_log=_kress91_iw_log_fO2(anchor.T_K),
            species=anchor.species,
            expected_Pa=float(anchor.expected_Pa),
            tolerance_decades=1.0,
            source=_grid_25_v3_source(old_cell_id, source),
            composition_wt_pct=dict(anchor.composition_wt_pct),
        )
    return None


def _grid_25_v3_corpus_substitute(
    old_cell_id: str,
    selector: Mapping[str, Any],
    repo_root: Path | None,
) -> CorpusAnchor | None:
    source_contains = selector.get("source_contains")
    melt_id_suffix = selector.get("melt_id_suffix")
    for anchor in load_all_corpus_anchors(repo_root=repo_root):
        if anchor.paper_id != selector["paper_id"]:
            continue
        if anchor.T_K != selector["T_K"]:
            continue
        if anchor.species != selector["species"]:
            continue
        if melt_id_suffix and not anchor.melt_id.endswith(melt_id_suffix):
            continue
        if source_contains and source_contains not in anchor.source:
            continue
        if not _grid_25_v3_matches_expected(anchor, selector):
            continue
        return CorpusAnchor(
            paper_id="grid-25",
            melt_id=f"grid-25:{selector['cell_id']}",
            T_K=anchor.T_K,
            fO2_log=anchor.fO2_log,
            species=anchor.species,
            expected_Pa=anchor.expected_Pa,
            tolerance_decades=1.0,
            source=_grid_25_v3_source(old_cell_id, anchor.source),
            composition_wt_pct=dict(anchor.composition_wt_pct),
        )
    return None


def _grid_25_v3_substitute_anchor(
    old_cell_id: str,
    repo_root: Path | None,
) -> CorpusAnchor | None:
    selector = _GRID_25_V3_SUBSTITUTIONS.get(old_cell_id)
    if selector is None:
        return None
    if selector["kind"] == "cj":
        return _grid_25_v3_cj_substitute(old_cell_id, selector, repo_root)
    if selector["kind"] == "corpus":
        return _grid_25_v3_corpus_substitute(old_cell_id, selector, repo_root)
    return None


# §25 v1 calibration feedstocks (copied from tests/test_vaporock_backend.py
# lines 54-100). The framework re-uses these so the cohort-1 acceptance
# matches the §25 v1 grid exactly. New cohorts can add their own
# composition tables.
GRID_25_FEEDSTOCKS: dict[str, dict[str, Any]] = {
    "tholeiite": {
        "label": "SF2004 tholeiite",
        "composition_wt_pct": {
            "SiO2": 51.55,
            "TiO2": 1.73,
            "Al2O3": 14.72,
            "FeO": 13.69,
            "MgO": 4.76,
            "CaO": 8.97,
            "Na2O": 3.21,
            "K2O": 0.78,
        },
    },
    "lunar_mare_basalt_12022_proxy": {
        "label": "Sossi-Fegley 2018 lunar basalt 12022 proxy",
        "composition_wt_pct": {
            "SiO2": 44.5,
            "TiO2": 1.5,
            "Al2O3": 13.5,
            "FeO": 16.5,
            "MgO": 9.0,
            "CaO": 11.0,
            "Na2O": 0.4,
            "K2O": 0.10,
            "MnO": 0.20,
            "P2O5": 0.10,
            "Cr2O3": 0.35,
        },
    },
    "EAC-1A": {
        "label": "Sesko 2022 EAC-1A simulant",
        "composition_wt_pct": {
            "SiO2": 44.41,
            "Fe2O3": 12.20,
            "FeO": 0.0,
            "MgO": 12.09,
            "CaO": 10.98,
            "Al2O3": 12.80,
            "TiO2": 2.44,
            "MnO": 0.20,
            "Na2O": 2.95,
            "K2O": 1.32,
            "P2O5": 0.61,
        },
    },
}


def grid_25_anchors(
    *,
    repo_root: Path | None = None,
) -> list[CorpusAnchor]:
    """Return the §25 cohort-1 v3 30-anchor grid.

    v3 keeps v2's covered cells and replaces the 10 v2 blocked cells with
    fixture-backed substitutes. If a required private fixture is absent,
    the old cell remains blocked so the report stays honest.
    """
    anchors: list[CorpusAnchor] = []
    for melt_key, melt_data in GRID_25_FEEDSTOCKS.items():
        composition = dict(melt_data["composition_wt_pct"])
        for T_K in _GRID_25_TEMPERATURES_K:
            fO2_log = _kress91_iw_log_fO2(T_K)
            for species in _GRID_25_SPECIES:
                old_cell_id = _grid_25_cell_id(melt_key, T_K, species)
                expected = _GRID_25[melt_key].get((T_K, species))
                if expected is None:
                    substitute = _grid_25_v3_substitute_anchor(
                        old_cell_id,
                        repo_root,
                    )
                    if substitute is not None:
                        anchors.append(substitute)
                        continue
                    expected_Pa = float("nan")
                    source = (
                        "blocked: v3 substitute fixture missing from corpus"
                    )
                else:
                    expected_Pa = float(expected)
                    source = (
                        "§25 v3 retained v2 grid cell "
                        f"({melt_key} @ {int(T_K)} K, {species})"
                    )
                anchors.append(
                    CorpusAnchor(
                        paper_id="grid-25",
                        melt_id=f"grid-25:{melt_key}",
                        T_K=T_K,
                        fO2_log=fO2_log,
                        species=species,
                        expected_Pa=expected_Pa,
                        tolerance_decades=1.0,
                        source=source,
                        composition_wt_pct=composition,
                    )
                )
    return anchors


_GRID_25_SIO_T_MIN_K = 1673.0
_GRID_25_SIO_T_MAX_K = 4000.0
_GRID_25_SIO_PAPER_ORDER = {
    "cj2015": 0,
    "sof2018-mineru": 1,
    "sf2004": 2,
    "vf2013-moon": 3,
    "vf2013-mars": 4,
    "vf2013-bse": 5,
}

_GRID_25_SIO_VF_COMPOSITION_KEYS = {
    "vf2013-moon": "Moon_bulk_silicate_moon",
    "vf2013-mars": "Mars_bulk_silicate_mars",
    "vf2013-bse": "BSE_bulk_silicate_earth",
}


def _grid_25_sio_paper_tag(anchor: CorpusAnchor) -> str | None:
    if anchor.species != "SiO":
        return None
    if not (
        _GRID_25_SIO_T_MIN_K
        <= anchor.T_K
        <= _GRID_25_SIO_T_MAX_K
    ):
        return None
    if anchor.paper_id == "costa-jacobson-2015-olivine-kems":
        if "Ir-cell C-C eqn" in anchor.source:
            return "cj2015"
        return None
    if anchor.paper_id == "sossi-fegley-2018-volatility":
        if anchor.T_K > 1973.0:
            return None
        if "MinerU Fig 3" in anchor.source:
            return "sof2018-mineru"
        return None
    if anchor.paper_id == "schaefer-fegley-2004-io-lava":
        if int(anchor.T_K) in (1700, 1900):
            return "sf2004"
        return None
    if anchor.paper_id == "visscher-fegley-2013-debris-disks":
        if "Moon" in anchor.source:
            return "vf2013-moon"
        if "Mars" in anchor.source:
            return "vf2013-mars"
        if "BSE" in anchor.source:
            return "vf2013-bse"
    return None


def _grid_25_sio_vf_composition(
    paper_tag: str,
    repo_root: Path | None,
) -> dict[str, float]:
    composition_key = _GRID_25_SIO_VF_COMPOSITION_KEYS.get(paper_tag)
    if composition_key is None:
        return {}
    path = (
        _corpus_root(repo_root)
        / "visscher-fegley-2013-debris-disks"
        / "benchmark-fixture.yaml"
    )
    try:
        data = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError) as exc:
        raise RuntimeError(
            f"cannot load §25-bis-SiO VF2013 fixture: {path}"
        ) from exc
    if not isinstance(data, Mapping):
        raise RuntimeError(
            f"§25-bis-SiO VF2013 fixture is not a mapping: {path}"
        )
    expected = data.get("expected") or {}
    if not isinstance(expected, Mapping):
        raise RuntimeError(
            f"§25-bis-SiO VF2013 fixture lacks mapping expected block: {path}"
        )
    composition = dict(
        _multi_melt_compositions(expected).get(composition_key) or {}
    )
    composition.pop("ZnO", None)
    return composition


def grid_25_sio_anchors(
    *,
    repo_root: Path | None = None,
) -> list[CorpusAnchor]:
    """Return the §25-bis SiO(g) T-sweep benchmark cohort."""

    anchors: list[CorpusAnchor] = []
    for anchor in load_all_corpus_anchors(repo_root=repo_root):
        paper_tag = _grid_25_sio_paper_tag(anchor)
        if paper_tag is None:
            continue
        composition = dict(anchor.composition_wt_pct)
        if anchor.paper_id == "visscher-fegley-2013-debris-disks":
            composition = _grid_25_sio_vf_composition(paper_tag, repo_root)
            if not composition:
                continue
        anchors.append(
            CorpusAnchor(
                paper_id="grid-25-sio",
                melt_id=f"grid-25-sio:{paper_tag}",
                T_K=anchor.T_K,
                fO2_log=anchor.fO2_log,
                species="SiO",
                expected_Pa=anchor.expected_Pa,
                tolerance_decades=anchor.tolerance_decades,
                source=(
                    f"§25-bis-SiO from {anchor.paper_id} "
                    f"({anchor.melt_id}); {anchor.source}"
                ),
                composition_wt_pct=composition,
            )
        )
    return sorted(
        anchors,
        key=lambda anchor: (
            _GRID_25_SIO_PAPER_ORDER[
                anchor.melt_id.removeprefix("grid-25-sio:")
            ],
            anchor.T_K,
            anchor.expected_Pa,
        ),
    )


__all__ = (
    "AtomicRatioAnchor",
    "CJOlivineKEMSAnchor",
    "CorpusAnchor",
    "GRID_25_FEEDSTOCKS",
    "grid_25_anchors",
    "grid_25_sio_anchors",
    "load_all_cj_olivine_kems_anchors",
    "load_all_atomic_ratio_anchors",
    "load_all_corpus_anchors",
    "sf2004_table8_atomic_ratio_anchors",
)
