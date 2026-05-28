"""0.5.4.1 B5 (CW1 historical-audit closure): YAML-driven MRE voltage
ladder via ``ExtractionMixin._build_mre_voltage_sequence``. Pre-B5
the ``setpoints['mre_voltage_sequence']['sequence']`` block was dead
config (operators saw no effect from edits); the Python builder
returned a hardcoded ladder. B5 wired the YAML through with a
graceful fallback so the YAML is now source-of-truth.

These tests pin:
1. ``_coerce_mre_decomposition_voltage`` numeric parsing (scalar,
   range mean, string with operator prefix, defensive None/bool/
   non-finite skip).
2. ``_parse_mre_voltage_sequence_yaml`` end-to-end YAML parse with
   the canonical published shape.
3. Fallback behaviour when YAML missing / empty / malformed.
4. Sorted-by-voltage invariant so the C5 prefix-filter
   (``voltage <= 1.6``) consumes a monotone list.
"""

from __future__ import annotations

import math

import pytest

from simulator.core import PyrolysisSimulator
from simulator.extraction import ExtractionMixin
from simulator.melt_backend.base import StubBackend


def _sim(setpoints=None) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints or {"campaigns": {}},
        {"x": {"label": "X", "composition_wt_pct": {"SiO2": 100}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    return sim


# ---------------------------------------------------------------------------
# 1. _coerce_mre_decomposition_voltage — primitive parsing
# ---------------------------------------------------------------------------

def test_coerce_voltage_scalar_float_passes_through():
    assert ExtractionMixin._coerce_mre_decomposition_voltage(1.4) == 1.4
    assert ExtractionMixin._coerce_mre_decomposition_voltage(2) == 2.0


def test_coerce_voltage_range_returns_midpoint():
    """[0.8, 1.0] range → midpoint 0.9. Per the documented parsing
    rule: operator who wants the lower bound pins both ends equal."""
    assert ExtractionMixin._coerce_mre_decomposition_voltage(
        [0.8, 1.0]
    ) == pytest.approx(0.9)


def test_coerce_voltage_string_with_lt_prefix_parses_numeric():
    """``"<0.5"`` → ``0.5`` (operator-warning that actual is below;
    the numeric is what we use)."""
    assert ExtractionMixin._coerce_mre_decomposition_voltage(
        "<0.5"
    ) == 0.5


def test_coerce_voltage_string_with_gt_or_tilde_prefix():
    """``">2.5"``, ``"~1.4"``, ``"±0.05"`` — all tolerated."""
    assert ExtractionMixin._coerce_mre_decomposition_voltage(">2.5") == 2.5
    assert ExtractionMixin._coerce_mre_decomposition_voltage("~1.4") == 1.4


def test_coerce_voltage_bare_string_numeric():
    assert ExtractionMixin._coerce_mre_decomposition_voltage("1.5") == 1.5


def test_coerce_voltage_unparseable_returns_none():
    """Defensive — caller filters None entries out."""
    assert ExtractionMixin._coerce_mre_decomposition_voltage(None) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage(True) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage(False) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage("bogus") is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage([1]) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage([1, 2, 3]) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage(
        float("nan")
    ) is None
    assert ExtractionMixin._coerce_mre_decomposition_voltage(
        float("inf")
    ) is None


# ---------------------------------------------------------------------------
# 2. _parse_mre_voltage_sequence_yaml — YAML round-trip
# ---------------------------------------------------------------------------

def _yaml_sequence(*rows) -> dict:
    return {
        "mre_voltage_sequence": {
            "sequence": list(rows),
        },
    }


def test_yaml_parse_canonical_published_shape():
    """Mirror the YAML shape from data/setpoints.yaml — species
    (string), decomposition_V (scalar OR [low,high] OR ``"<X"``),
    optional campaign + note + min_hold_hours."""
    setpoints = _yaml_sequence(
        {"species": "FeO", "decomposition_V": 0.6,
         "campaign": "C5", "note": "should be pre-depleted"},
        {"species": "Cr2O3", "decomposition_V": [0.8, 1.0],
         "campaign": "C5 trace"},
        {"species": "Na2O", "decomposition_V": "<0.5",
         "campaign": "C5 opening"},
        {"species": "SiO2", "decomposition_V": 1.4,
         "campaign": "C5 primary"},
    )
    sim = _sim(setpoints)
    seq = sim._parse_mre_voltage_sequence_yaml()
    # Sorted ascending by voltage (so C5 prefix-filter works).
    voltages = [entry["voltage"] for entry in seq]
    assert voltages == sorted(voltages)
    # Each entry has the expected shape.
    species_to_voltage = {
        entry["species"][0]: entry["voltage"] for entry in seq
    }
    assert species_to_voltage["FeO"] == 0.6
    assert species_to_voltage["Cr2O3"] == pytest.approx(0.9)
    assert species_to_voltage["Na2O"] == 0.5
    assert species_to_voltage["SiO2"] == 1.4
    # Default min_hold_hours when YAML omits the field.
    for entry in seq:
        assert entry["min_hold_hours"] == 3


def test_yaml_parse_min_hold_hours_passthrough():
    """When YAML carries an explicit ``min_hold_hours``, it overrides
    the default."""
    setpoints = _yaml_sequence(
        {"species": "Al2O3", "decomposition_V": 1.9,
         "min_hold_hours": 8},
        {"species": "MgO", "decomposition_V": 2.2,
         "min_hold_hours": 5},
    )
    sim = _sim(setpoints)
    seq = sim._parse_mre_voltage_sequence_yaml()
    hold_map = {
        entry["species"][0]: entry["min_hold_hours"] for entry in seq
    }
    assert hold_map["Al2O3"] == 8
    assert hold_map["MgO"] == 5


def test_yaml_parse_skips_malformed_entries():
    """An entry without ``species`` or with unparseable
    ``decomposition_V`` is silently dropped (caller may fall back
    on empty result)."""
    setpoints = _yaml_sequence(
        {"species": "FeO", "decomposition_V": 0.6},  # ok
        {"decomposition_V": 1.0},                    # missing species
        {"species": "X", "decomposition_V": "bogus"},  # unparseable V
        {"species": "Y"},                            # missing V
        {"species": "Z", "decomposition_V": float("nan")},  # non-finite
        "not a dict",                                # wrong type
    )
    sim = _sim(setpoints)
    seq = sim._parse_mre_voltage_sequence_yaml()
    species_list = [entry["species"][0] for entry in seq]
    assert species_list == ["FeO"]


# ---------------------------------------------------------------------------
# 3. _build_mre_voltage_sequence — fallback + integration
# ---------------------------------------------------------------------------

def test_build_falls_back_to_hardcoded_ladder_when_yaml_missing():
    """No setpoints at all → fallback ladder. Length matches the
    historic hardcoded set (8 entries)."""
    sim = _sim()
    seq = sim._build_mre_voltage_sequence()
    voltages = [entry["voltage"] for entry in seq]
    # Documented fallback ladder (sorted ascending in the source).
    assert voltages == [0.6, 0.9, 1.0, 1.4, 1.5, 1.9, 2.2, 2.5]
    # Hold-hour pattern matches the documented fallback values.
    hold_hours = [entry["min_hold_hours"] for entry in seq]
    assert hold_hours == [3, 2, 2, 5, 3, 8, 5, 10]


def test_build_falls_back_when_yaml_block_is_empty():
    """YAML present but ``sequence`` block empty/missing → fallback."""
    sim = _sim({"mre_voltage_sequence": {}, "campaigns": {}})
    seq = sim._build_mre_voltage_sequence()
    assert len(seq) == 8  # fallback ladder size


def test_build_falls_back_when_yaml_sequence_is_all_malformed():
    """All entries unparseable → empty parsed list → fallback."""
    sim = _sim(_yaml_sequence(
        {"species": None, "decomposition_V": "bogus"},
        {"species": "X", "decomposition_V": None},
    ))
    seq = sim._build_mre_voltage_sequence()
    assert len(seq) == 8  # fallback


def test_build_uses_yaml_when_present_and_valid():
    """One valid YAML entry → YAML-derived sequence (not fallback)."""
    sim = _sim(_yaml_sequence(
        {"species": "FeO", "decomposition_V": 0.6,
         "min_hold_hours": 3},
        {"species": "SiO2", "decomposition_V": 1.4,
         "min_hold_hours": 5},
    ))
    seq = sim._build_mre_voltage_sequence()
    assert len(seq) == 2
    assert seq[0]["species"] == ["FeO"]
    assert seq[1]["species"] == ["SiO2"]


def test_build_returns_fresh_lists_not_aliasing_fallback():
    """Defensive — the caller may mutate returned dicts (e.g., test
    monkey-patches a voltage). The fallback ladder is a tuple of dicts
    with tuple ``species``; the builder must return list-typed
    species + fresh dicts so mutation doesn't leak back."""
    sim = _sim()
    seq1 = sim._build_mre_voltage_sequence()
    seq1[0]["voltage"] = 99.9
    seq2 = sim._build_mre_voltage_sequence()
    assert seq2[0]["voltage"] == 0.6  # original, not 99.9


def test_build_yaml_path_sorted_by_voltage_ascending():
    """C5 limited MRE filters ``voltage <= 1.6``; the resulting list
    must stay monotone for the existing step-index logic to work
    without re-sort."""
    sim = _sim(_yaml_sequence(
        {"species": "CaO", "decomposition_V": 2.5},
        {"species": "FeO", "decomposition_V": 0.6},
        {"species": "TiO2", "decomposition_V": 1.5},
        {"species": "Al2O3", "decomposition_V": 1.9},
    ))
    seq = sim._build_mre_voltage_sequence()
    voltages = [entry["voltage"] for entry in seq]
    assert voltages == sorted(voltages)
    # C5 prefix-filter (mirroring extraction.py step 9).
    c5_seq = [s for s in seq if s["voltage"] <= 1.6]
    assert [s["species"][0] for s in c5_seq] == ["FeO", "TiO2"]


# ---------------------------------------------------------------------------
# 4. Integration: real setpoints.yaml from disk
# ---------------------------------------------------------------------------

def test_build_with_real_setpoints_yaml_returns_published_shape():
    """End-to-end: load the actual project setpoints.yaml and verify
    the resulting ladder makes physical sense. Voltage values cover
    the published Ellingham range (~0.5 → 2.5 V); species list
    includes the major Na2O/K2O alkalis the published YAML adds
    beyond the hardcoded fallback."""
    from pathlib import Path
    import yaml
    repo_root = Path(__file__).resolve().parent.parent
    setpoints = yaml.safe_load(
        (repo_root / "data" / "setpoints.yaml").read_text()
    )
    sim = _sim(setpoints)
    seq = sim._build_mre_voltage_sequence()
    species_set = {entry["species"][0] for entry in seq}
    # The published YAML carries entries the hardcoded fallback
    # didn't have (Na2O / K2O / V2O5); the wired YAML surfaces them.
    assert "Na2O" in species_set
    assert "K2O" in species_set
    voltages = [entry["voltage"] for entry in seq]
    # Sorted + within the published Ellingham band.
    assert voltages == sorted(voltages)
    assert min(voltages) >= 0.4 and max(voltages) <= 3.0
