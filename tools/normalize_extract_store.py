#!/usr/bin/env python3
"""Idempotent literature-extract store normalizer (species ids + field idiom).

Does NOT bulk-rewrite the store. Default is ``--dry-run``. Apply is explicit
(``--apply``) and scoped with ``--only``. Ambiguous chemistry mappings are
refused, never guessed. Numeric values are untouched except printed unit
conversions.

Canonical species form (U0 / vapour-rail):
  * IUPAC element capitalization via the observation ``formula`` field
    (atom-gated). Naive case-fold is never the decision procedure.
  * Gas vs condensed stay distinct. Collision-gas formulas (SiO2, Al2O3, …)
    get a ``_gas`` suffix IFF the record is gas. Condensed allotropes keep a
    phase token (``_liquid``, ``_alpha``, ``_cr``, …) so they never collapse
    onto the melt-component id or onto each other.
  * Composite labels (``VO_VO2``, ``Yb_metal_and_YbO``, ``Se_n_ladder``) are
    kept. Splitting them is a harness concern, not a silent store rewrite.

Canonical fields:
  * temperature: ``T_K`` / ``value_K``
  * pressure: ``p_Pa``
  * oxygen fugacity: ``pO2_bar``
  * activity coefficient: scalar ``gamma`` OR structured ``gamma_range``
    (a published range string is never parsed into a single float)

Usage::

  PYTHONPATH=. python tools/normalize_extract_store.py
  PYTHONPATH=. python tools/normalize_extract_store.py --self-test
  PYTHONPATH=. python tools/normalize_extract_store.py --apply --only kems-001-homma-1966.yaml
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACTS_DIR = ROOT / "data" / "literature" / "extracts"
U0_PATH = ROOT / "data" / "vapour_rail_u0_manifest.yaml"
VP_PATH = ROOT / "data" / "vapor_pressures.yaml"
PINS_PATH = ROOT / "data" / "vapour_rail_validation_pins.yaml"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "literature" / "normalize_extract_store.yaml"
)

# U0 membership_sets.collision_gas bases (formula without _gas).
COLLISION_GAS_BASES = frozenset(
    {
        "Al2O3",
        "CaO",
        "CoO",
        "Cr2O3",
        "Fe2O3",
        "FeO",
        "K2O",
        "MgO",
        "MnO",
        "Na2O",
        "NiO",
        "P2O5",
        "SiO2",
        "TiO2",
    }
)

# IUPAC element symbols. Class A two-letter symbols are those whose
# one-letter split is impossible (CL: L is not an element). Class B are
# those where both letters are themselves elements (CO vs Co, SN vs Sn).
_ELEMENTS_IUPAC: tuple[str, ...] = (
    "Ac", "Ag", "Al", "Am", "Ar", "As", "At", "Au", "Ba", "Be", "Bh", "Bi",
    "Bk", "Br", "Ca", "Cd", "Ce", "Cf", "Cl", "Cm", "Cn", "Co", "Cr", "Cs",
    "Cu", "Db", "Ds", "Dy", "Er", "Es", "Eu", "Fe", "Fl", "Fm", "Fr", "Ga",
    "Gd", "Ge", "He", "Hf", "Hg", "Ho", "Hs", "In", "Ir", "Kr", "La", "Li",
    "Lr", "Lu", "Lv", "Mc", "Md", "Mg", "Mn", "Mo", "Mt", "Na", "Nb", "Nd",
    "Ne", "Nh", "Ni", "No", "Np", "Og", "Os", "Pa", "Pb", "Pd", "Pm", "Po",
    "Pr", "Pt", "Pu", "Ra", "Rb", "Re", "Rf", "Rg", "Rh", "Rn", "Ru", "Sb",
    "Sc", "Se", "Sg", "Si", "Sm", "Sn", "Sr", "Ta", "Tb", "Tc", "Te", "Th",
    "Ti", "Tl", "Tm", "Ts", "Xe", "Yb", "Zn", "Zr",
    "B", "C", "F", "H", "I", "K", "N", "O", "P", "S", "U", "V", "W", "Y",
)
_ELEMENTS_SET = frozenset(_ELEMENTS_IUPAC)
_ELEMENTS_UPPER = {e.upper(): e for e in _ELEMENTS_IUPAC}
_ONE_LETTER = frozenset(e for e in _ELEMENTS_IUPAC if len(e) == 1)
_ONE_UPPER = frozenset(e.upper() for e in _ONE_LETTER)
# Two-letter elements whose ALL-CAPS form is also a legal consecutive-element
# formula (CO = Co or C+O). Compare letters case-insensitively: Bi is B+I.
# Resolution requires the stored formula/MW.
CLASS_B_AMBIGUOUS = frozenset(
    e.upper()
    for e in _ELEMENTS_IUPAC
    if len(e) == 2
    and e[0].upper() in _ONE_UPPER
    and e[1].upper() in _ONE_UPPER
)
CASEFOLD_DANGEROUS = CLASS_B_AMBIGUOUS | {
    _ELEMENTS_UPPER[k] for k in CLASS_B_AMBIGUOUS if k in _ELEMENTS_UPPER
}

# CEA condensed-polymorph tokens that are NOT collision-gas / liquid flags.
_POLYMORPH_RE = re.compile(
    r"^(?P<body>.+)_(?P<poly>b-crt|b-qz|a-crt|a-qz|b-trid)$",
    re.I,
)

COMPOSITE_KEEP = re.compile(
    r"(_and_|_n_ladder|_metal_and_|_NH3_|_CH4_|_propellant|_salts|_alloy|"
    r"_oxides|_organic|_residual|_hydrocarbon)",
    re.I,
)

# CEA / extract phase tokens on the species key (not chemical).
PHASE_TOKEN_RE = re.compile(
    r"(?P<body>.*?)(?:_(?P<us>gas|liquid|solid|cr(?:I{1,3}|IV|V)?|liq|L|a'?|b|c|d|I{1,3}|IV|V|an)|"
    r"\((?P<paren>g|cr|l|L|s|a|b|c|d|gr|alpha|beta|I|II|III|IV|V|liq|liquid|solid)\))$"
)

PAREN_GAS_RE = re.compile(r"^(.+)\(g\)$")

SIMPLE_RANGE_RE = re.compile(
    r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"(?:–|—|-|to)\s*"
    r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)

ATM_PA = 101325.0
BAR_PA = 1.0e5
TORR_PA = 101325.0 / 760.0  # 133.32236842105263
C_TO_K = 273.15

CONVERSIONS = {
    "T_C_to_T_K": {
        "premise": "Celsius and Kelvin share the same interval; ice point is 273.15 K.",
        "algebra": "T_K = T_C + 273.15",
        "unit_check": "[°C] + 273.15 → [K]",
        "sanity": "0 °C = 273.15 K; 100 °C = 373.15 K; 1600 °C = 1873.15 K (Homma 1966).",
    },
    "p_atm_to_p_Pa": {
        "premise": "IUPAC standard atmosphere is exactly 101325 Pa.",
        "algebra": "p_Pa = p_atm × 101325",
        "unit_check": "[atm] × 101325 Pa/atm → [Pa]",
        "sanity": "1 atm = 101325 Pa; 5.54e-9 atm = 5.6123405e-4 Pa (DeMaria dual-write).",
    },
    "p_bar_to_p_Pa": {
        "premise": "1 bar = 10^5 Pa exactly.",
        "algebra": "p_Pa = p_bar × 1e5",
        "unit_check": "[bar] × 1e5 Pa/bar → [Pa]",
        "sanity": "1 bar = 100000 Pa; 1.3e-8 bar = 0.0013 Pa (Fedkin chamber).",
    },
    "p_torr_to_p_Pa": {
        "premise": "1 Torr = 1 atm / 760 = 101325/760 Pa.",
        "algebra": "p_Pa = p_torr × (101325/760)",
        "unit_check": "[Torr] × 133.32236842105263 Pa/Torr → [Pa]",
        "sanity": "760 Torr = 101325 Pa; 1e-6 Torr = 1.333223684e-4 Pa (Richter 2007).",
    },
    "log10_p_atm_to_p_Pa": {
        "premise": "Common-log partial pressure in atm, converted via the standard atmosphere.",
        "algebra": "p_Pa = 10**(log10_p_atm) × 101325",
        "unit_check": "10^[log10(atm)] × 101325 Pa/atm → [Pa]",
        "sanity": "log10_p_atm = -8 → 1e-8 atm = 1.01325e-3 Pa.",
    },
    "log10_pO2_bar_to_pO2_bar": {
        "premise": "Common-log oxygen fugacity in bar.",
        "algebra": "pO2_bar = 10**(log10_pO2_bar)",
        "unit_check": "10^[log10(bar)] → [bar]",
        "sanity": "log10_pO2_bar = -8 → 1e-8 bar.",
    },
}


@dataclass
class Change:
    kind: str
    path: str
    old: Any = None
    new: Any = None
    conversion: str | None = None
    note: str | None = None
    refused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileReport:
    path: str
    source_id: str
    changes: list[Change] = field(default_factory=list)
    refused: list[Change] = field(default_factory=list)

    @property
    def n_apply(self) -> int:
        return len(self.changes)

    @property
    def n_refuse(self) -> int:
        return len(self.refused)


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dump_yaml(doc: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        doc,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )


def load_rail_ids() -> set[str]:
    ids: set[str] = set()
    u0 = _load_yaml(U0_PATH) or {}
    for row in u0.get("species") or []:
        if isinstance(row, Mapping) and row.get("id"):
            ids.add(str(row["id"]))
    vp = _load_yaml(VP_PATH) or {}
    for fam in (vp.get("families") or {}).values():
        spp = ((fam or {}).get("physical_properties") or {}).get("species") or {}
        ids.update(str(k) for k in spp)
    pins = _load_yaml(PINS_PATH) or {}
    ids.update(str(k) for k in (pins.get("species") or {}))
    return ids


def discover_extracts(directory: Path | None = None) -> list[Path]:
    d = directory or EXTRACTS_DIR
    out = []
    for p in sorted(d.glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        if p.name.upper().startswith("SCHEMA"):
            continue
        out.append(p)
    return out


_COMPOSITE_PHASE_PARTS = frozenset(
    {
        "gas", "liquid", "solid", "cr", "L", "a", "b", "c", "d",
        "I", "II", "III", "IV", "V", "an", "liq",
        "I'", "a'", "b'", "c'",
        "b-crt", "b-qz", "a-crt", "a-qz", "b-trid",
    }
)


def is_composite_id(sid: str) -> bool:
    if COMPOSITE_KEEP.search(sid):
        return True
    if "_" in sid:
        parts = sid.split("_")
        if (
            len(parts) == 2
            and all(p and p[0].isalpha() for p in parts)
            and parts[1] not in _COMPOSITE_PHASE_PARTS
            and parts[1][0].isupper()
        ):
            # VO_VO2
            return True
    return False


def _class_b_token(two_up: str, formula: str | None) -> str:
    """Resolve a Class-B ALL-CAPS token using the stored formula, not case-fold.

    ``SN`` + formula ``SN`` (MW ~46) is S+N; ``Sn`` + formula ``Sn`` (MW ~119)
    is tin. ``HF`` (MW ~20) is hydrogen fluoride, not Hf. ``SI`` in ``SiO2``
    is silicon (formula starts with ``Si``), not sulphur+iodine.
    """
    iupac = _ELEMENTS_UPPER[two_up]
    if not formula:
        return two_up
    if formula == iupac:
        return iupac
    if formula.startswith(iupac):
        rest = formula[len(iupac) :]
        if not rest or rest[0].isupper() or rest[0].isdigit() or rest[0] in "()":
            return iupac
    if formula == two_up or formula.upper() == two_up:
        return two_up
    return two_up


def cea_orthography(sid: str, formula: str | None = None) -> str:
    """IUPAC-ize a CEA / extract species key without flattening isomers.

    Class A (``CL``→``Cl``, ``AL``→``Al``): the one-letter split is impossible.
    Class B (``CO``/``Co``, ``SN``/``Sn``, ``HF``/``Hf``, ``BI``/``Bi``): the
    stored ``formula`` field is the determination. Comma isomer tags, trailing
    primes, and CEA polymorph tokens (``_b-crt``) are preserved.
    """
    raw = str(sid).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]
    comma_tag = ""
    if "," in raw:
        raw, comma_tag = raw.split(",", 1)
        comma_tag = "," + comma_tag
    prime = ""
    if raw.endswith("'"):
        prime = "'"
        raw = raw[:-1]

    poly = ""
    pm = _POLYMORPH_RE.match(raw)
    if pm:
        raw = pm.group("body")
        poly = "_" + pm.group("poly")

    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch.isdigit() or ch in "()[]+-·.*":
            out.append(ch)
            i += 1
            continue
        if ch == "_":
            out.append(raw[i:])
            i = n
            break
        two = raw[i : i + 2]
        if len(two) == 2 and two.isalpha():
            two_up = two.upper()
            if two_up in _ELEMENTS_UPPER:
                if two_up in CLASS_B_AMBIGUOUS:
                    out.append(_class_b_token(two_up, formula))
                else:
                    out.append(_ELEMENTS_UPPER[two_up])
                i += 2
                continue
        if ch.isalpha():
            one_up = ch.upper()
            if one_up in _ELEMENTS_UPPER:
                out.append(_ELEMENTS_UPPER[one_up])
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out) + poly + prime + comma_tag


def classify_phase(
    sid: str,
    *,
    phase: str | None,
    standard_state: str | None,
) -> tuple[str, str]:
    """Return (phase_kind, leftover_suffix_token).

    phase_kind: gas | liquid | solid | unknown
    leftover_suffix_token: normalized token to keep on condensed ids, or ''.
    Trailing primes (``Cr2O3_I'``) stay on the token so allotropes do not merge.
    """
    raw = sid.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]
    prime = ""
    if raw.endswith("'"):
        prime = "'"
        raw = raw[:-1]

    us = ""
    paren = ""
    poly = ""
    pm = _POLYMORPH_RE.match(raw)
    if pm:
        poly = pm.group("poly")
    m = PHASE_TOKEN_RE.fullmatch(raw)
    if m:
        us = (m.group("us") or "").strip()
        paren = (m.group("paren") or "").strip()
    token = (us or paren or poly).lower()
    if prime and token:
        token = token + prime
    elif prime:
        token = prime

    phase_l = (phase or "").lower()
    ss = standard_state or ""

    if token in {"gas", "g"} or "phase_flag=0" in ss:
        return "gas", ""
    if "phase_flag=0" not in ss and (
        token in {"l", "liq", "liquid"} or "condensed_liquid" in phase_l
    ):
        return "liquid", "liquid"
    if token in {"a", "alpha"}:
        return "solid", "alpha"
    if token in {"a'", "alpha'"}:
        return "solid", "alpha'"
    if token in {"b", "beta"}:
        return "solid", "beta"
    if token in {"c"}:
        return "solid", "c"
    if poly:
        return "solid", poly
    if token.startswith("cr") or token in {"s", "solid", "gr"}:
        return "solid", token if token.startswith("cr") else "cr"
    if token.rstrip("'") in {"i", "ii", "iii", "iv", "v", "an"}:
        core = token.rstrip("'")
        marked = core.upper() if core != "an" else "an"
        return "solid", marked + ("'" if token.endswith("'") else "")
    if phase_l in {"condensed", "condensed_solid"} or "phase_flag=1" in ss:
        return "solid", token or "cr"
    if "condensed_solid" in phase_l:
        return "solid", token or "cr"
    if phase_l.startswith("gas") or phase_l == "ideal_gas":
        return "gas", ""
    if "phase_flag=0" in ss:
        return "gas", ""
    if "liquid" in phase_l:
        return "liquid", "liquid"
    if any(w in phase_l for w in ("solid", "crystal", "cryst", "condensed")):
        return "solid", token or "cr"
    if not phase_l and "phase_flag=" in ss:
        flag = re.search(r"phase_flag=(\d+)", ss)
        if flag and flag.group(1) == "0":
            return "gas", ""
        if flag:
            return "solid", token or "cr"
    return "unknown", token


def formula_from_observation(obs: Mapping[str, Any]) -> str | None:
    values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
    raw = values.get("formula")
    if raw is None:
        return None
    s = str(raw).strip().strip("'\"")
    return s or None


def _has_structural_discriminator(sid: str) -> bool:
    """CEA isomer / allotrope / polymorph tags that Hill-formula dests would erase."""
    s = str(sid)
    if "," in s:
        return True
    if s.endswith("'"):
        return True
    if _POLYMORPH_RE.match(s.strip().strip("'\"")):
        return True
    return False


def _formula_body(sid: str) -> str:
    """Strip comma tags, primes, and known phase/polymorph suffixes for collision-gas tests."""
    s = str(sid).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        s = s[1:-1]
    if "," in s:
        s = s.split(",", 1)[0]
    if s.endswith("'"):
        s = s[:-1]
    pm = _POLYMORPH_RE.match(s)
    if pm:
        return pm.group("body")
    m = PHASE_TOKEN_RE.fullmatch(s)
    if m:
        return m.group("body")
    return s


def canonical_species_id(
    sid: str,
    *,
    formula: str | None,
    phase: str | None,
    standard_state: str | None,
    rail_ids: set[str] | None = None,
) -> tuple[str, str, bool]:
    """Return (canonical_id, reason, refused).

    Destination is the orthographic form of the *source key* (Class A ``CL``→
    ``Cl``; Class B gated by stored ``formula``), not the Hill formula. That
    keeps isomers (``C3H7,i-propyl`` vs ``n-propyl``; ``CH2OH`` vs ``CH3O``;
    ``ALOH`` vs ``HALO``) distinct. ``refused`` is True only when a mapping
    would guess chemistry.
    """
    raw = str(sid).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]
    if is_composite_id(raw):
        return raw, "composite_kept", False

    phase_kind, token = classify_phase(
        raw, phase=phase, standard_state=standard_state
    )
    rail = rail_ids or set()
    if raw in rail:
        if phase_kind == "gas" and raw in COLLISION_GAS_BASES:
            return f"{raw}_gas", "already_rail_plus_collision_gas", False
        return raw, "already_on_rail", False
    formula_s = (formula or "").strip() or None
    ortho = cea_orthography(raw, formula_s)
    body = _formula_body(ortho)

    if formula_s is None:
        case_hits = [r for r in rail if r.lower() == raw.lower()]
        if raw in case_hits:
            return raw, "already_exact", False
        # Class A orthography (Li2CL2→Li2Cl2) does not need a formula.
        if ortho != raw and not any(
            raw[i : i + 2].upper() in CLASS_B_AMBIGUOUS
            for i in range(max(0, len(raw) - 1))
            if raw[i : i + 2].isalpha()
        ):
            return _apply_phase(ortho, body, phase_kind, token), "class_a_orthography", False
        if len(case_hits) >= 1:
            return raw, "refused_ambiguous_casefold_without_formula", True
        if ortho != raw:
            return _apply_phase(ortho, body, phase_kind, token), "class_a_orthography", False
        return raw, "kept_no_formula", False

    # Structural / isomer keys: never flatten onto Hill formula.
    if _has_structural_discriminator(raw):
        if phase_kind == "solid" and token:
            return f"{body}_{token}", "structural_discriminator", False
        return ortho, "structural_discriminator", False
    if ortho != formula_s and body != formula_s:
        # CH2OH (formula CH3O), CCN (formula C2N), HALO (formula AlOH), BrOO (BrO2)
        return ortho, "structural_connectivity", False

    return _apply_phase(ortho, body, phase_kind, token), "orthography_plus_phase", False


def _apply_phase(
    ortho: str,
    body: str,
    phase_kind: str,
    token: str,
    *,
    keep_ortho: bool = False,
) -> str:
    if keep_ortho:
        return ortho
    if phase_kind == "gas":
        if body in COLLISION_GAS_BASES:
            return f"{body}_gas"
        return ortho
    if phase_kind == "liquid":
        return f"{body}_liquid"
    if phase_kind == "solid":
        suffix = token or "cr"
        return f"{body}_{suffix}"
    return ortho


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def parse_simple_range(text: str) -> tuple[float, float] | None:
    m = SIMPLE_RANGE_RE.match(text.replace(" ", ""))
    if not m:
        m = SIMPLE_RANGE_RE.match(text)
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _set_if_absent(mapping: dict[str, Any], key: str, value: Any) -> bool:
    if key in mapping and mapping[key] is not None:
        return False
    mapping[key] = value
    return True


def normalize_gas_species_label(label: str) -> tuple[str, str | None]:
    """Strip a trailing (g) and return (canonical, as_published_or_None)."""
    s = str(label).strip()
    m = PAREN_GAS_RE.match(s)
    if m:
        return m.group(1), s
    return s, None


def normalize_values(
    values: dict[str, Any],
    *,
    path: str,
) -> list[Change]:
    changes: list[Change] = []

    # Temperature C → K (additive; never overwrite T_K / value_K).
    t_c = _finite_float(values.get("T_C"))
    if t_c is not None:
        t_k = t_c + C_TO_K
        if _set_if_absent(values, "T_K", t_k):
            changes.append(
                Change(
                    kind="unit_conversion",
                    path=f"{path}.T_K",
                    old=None,
                    new=t_k,
                    conversion="T_C_to_T_K",
                    note=f"T_K = {t_c:g} + 273.15",
                )
            )
    v_c = _finite_float(values.get("value_C"))
    if v_c is not None:
        v_k = v_c + C_TO_K
        if _set_if_absent(values, "value_K", v_k):
            changes.append(
                Change(
                    kind="unit_conversion",
                    path=f"{path}.value_K",
                    old=None,
                    new=v_k,
                    conversion="T_C_to_T_K",
                    note=f"value_K = {v_c:g} + 273.15",
                )
            )

    # pO2 synonyms / log form. Never invent a pO2 that is not in the record.
    for src, conv in (
        ("fO2_bar", None),
        ("oxygen_fugacity_bar", None),
    ):
        raw = _finite_float(values.get(src))
        if raw is not None and _set_if_absent(values, "pO2_bar", raw):
            changes.append(
                Change(
                    kind="field_alias",
                    path=f"{path}.pO2_bar",
                    old=None,
                    new=raw,
                    conversion=None,
                    note=f"copied from {src} (same unit, bar)",
                )
            )
    log_po2 = _finite_float(values.get("log10_pO2_bar") or values.get("fO2_log10_bar"))
    if log_po2 is not None:
        po2 = 10.0 ** log_po2
        if _set_if_absent(values, "pO2_bar", po2):
            changes.append(
                Change(
                    kind="unit_conversion",
                    path=f"{path}.pO2_bar",
                    old=None,
                    new=po2,
                    conversion="log10_pO2_bar_to_pO2_bar",
                    note=f"pO2_bar = 10**({log_po2:g})",
                )
            )

    # Activity range: structured, never a scalar.
    published = values.get("gamma_range_as_published")
    if isinstance(published, str) and published.strip():
        parsed = parse_simple_range(published)
        if parsed is None:
            structured = {
                "as_published": published,
                "parse_status": "refused_not_a_scalar",
            }
            note = "range string is not a single interval; kept as a range"
        else:
            structured = {
                "low": parsed[0],
                "high": parsed[1],
                "as_published": published,
                "parse_status": "interval",
            }
            note = f"interval [{parsed[0]:g}, {parsed[1]:g}]; not collapsed to a scalar"
        if "gamma_range" not in values:
            values["gamma_range"] = structured
            changes.append(
                Change(
                    kind="range_structure",
                    path=f"{path}.gamma_range",
                    old=None,
                    new=structured,
                    note=note,
                )
            )

    # gas_species SiO(g) → SiO, original preserved.
    gs = values.get("gas_species")
    if isinstance(gs, str) and gs.strip():
        canon, published_gs = normalize_gas_species_label(gs)
        if published_gs is not None and canon != gs:
            values["gas_species_as_published"] = published_gs
            values["gas_species"] = canon
            changes.append(
                Change(
                    kind="species_label",
                    path=f"{path}.gas_species",
                    old=gs,
                    new=canon,
                    note="stripped trailing (g); original in gas_species_as_published",
                )
            )

    # Pressure points.
    points = values.get("points")
    if isinstance(points, list):
        for i, pt in enumerate(points):
            if not isinstance(pt, dict):
                continue
            changes.extend(
                normalize_pressure_point(pt, path=f"{path}.points[{i}]")
            )
            t_c_pt = _finite_float(pt.get("T_C"))
            if t_c_pt is not None:
                t_k_pt = t_c_pt + C_TO_K
                if _set_if_absent(pt, "T_K", t_k_pt):
                    changes.append(
                        Change(
                            kind="unit_conversion",
                            path=f"{path}.points[{i}].T_K",
                            old=None,
                            new=t_k_pt,
                            conversion="T_C_to_T_K",
                            note=f"T_K = {t_c_pt:g} + 273.15",
                        )
                    )
    return changes


def normalize_pressure_point(pt: dict[str, Any], *, path: str) -> list[Change]:
    changes: list[Change] = []
    if _finite_float(pt.get("p_Pa") or pt.get("P_Pa") or pt.get("pressure_Pa")) is not None:
        return changes
    p_atm = _finite_float(pt.get("p_atm") or pt.get("P_atm"))
    if p_atm is not None:
        p_pa = p_atm * ATM_PA
        pt["p_Pa"] = p_pa
        changes.append(
            Change(
                kind="unit_conversion",
                path=f"{path}.p_Pa",
                old=None,
                new=p_pa,
                conversion="p_atm_to_p_Pa",
                note=f"p_Pa = {p_atm:g} × 101325",
            )
        )
        return changes
    p_bar = _finite_float(pt.get("p_bar") or pt.get("P_bar"))
    if p_bar is not None:
        p_pa = p_bar * BAR_PA
        pt["p_Pa"] = p_pa
        changes.append(
            Change(
                kind="unit_conversion",
                path=f"{path}.p_Pa",
                old=None,
                new=p_pa,
                conversion="p_bar_to_p_Pa",
                note=f"p_Pa = {p_bar:g} × 1e5",
            )
        )
        return changes
    p_torr = _finite_float(
        pt.get("p_torr") or pt.get("P_torr") or pt.get("p_mmHg") or pt.get("P_mmHg")
    )
    if p_torr is not None:
        p_pa = p_torr * TORR_PA
        pt["p_Pa"] = p_pa
        changes.append(
            Change(
                kind="unit_conversion",
                path=f"{path}.p_Pa",
                old=None,
                new=p_pa,
                conversion="p_torr_to_p_Pa",
                note=f"p_Pa = {p_torr:g} × (101325/760)",
            )
        )
        return changes
    log_atm = _finite_float(pt.get("log10_p_atm"))
    if log_atm is not None:
        p_pa = (10.0 ** log_atm) * ATM_PA
        pt["p_Pa"] = p_pa
        changes.append(
            Change(
                kind="unit_conversion",
                path=f"{path}.p_Pa",
                old=None,
                new=p_pa,
                conversion="log10_p_atm_to_p_Pa",
                note=f"p_Pa = 10**({log_atm:g}) × 101325",
            )
        )
        return changes
    log_pa = _finite_float(pt.get("log10_P_Pa") or pt.get("log10_p_Pa"))
    if log_pa is not None:
        p_pa = 10.0 ** log_pa
        pt["p_Pa"] = p_pa
        changes.append(
            Change(
                kind="unit_conversion",
                path=f"{path}.p_Pa",
                old=None,
                new=p_pa,
                conversion="log10_pO2_bar_to_pO2_bar",
                note=f"p_Pa = 10**({log_pa:g})",
            )
        )
    return changes


def normalize_equipment(equipment: dict[str, Any], *, path: str) -> list[Change]:
    changes: list[Change] = []
    block = equipment.get("chamber_pressure")
    if not isinstance(block, dict):
        return changes
    units = str(block.get("units") or "").strip().lower()
    raw = _finite_float(block.get("value"))
    if raw is None or units in {"pa", "pascal", ""}:
        return changes
    factor = None
    conv = None
    note = None
    if units in {"torr"}:
        factor, conv = TORR_PA, "p_torr_to_p_Pa"
        note = f"value_Pa = {raw:g} × (101325/760)"
    elif units in {"mmhg", "mm hg"}:
        factor, conv = TORR_PA, "p_torr_to_p_Pa"
        note = f"value_Pa = {raw:g} × (101325/760)  (mmHg = Torr)"
    elif units in {"bar"}:
        factor, conv = BAR_PA, "p_bar_to_p_Pa"
        note = f"value_Pa = {raw:g} × 1e5"
    elif units in {"atm"}:
        factor, conv = ATM_PA, "p_atm_to_p_Pa"
        note = f"value_Pa = {raw:g} × 101325"
    elif units in {"mbar", "millibar"}:
        factor, conv = 100.0, "p_bar_to_p_Pa"
        note = f"value_Pa = {raw:g} × 100  (mbar)"
    if factor is None:
        return changes
    new_val = raw * factor
    # Convert in place: this IS a numeric rewrite, allowed only as a unit
    # conversion we print. Record old value + old units.
    block["value_as_published"] = raw
    block["units_as_published"] = block.get("units")
    block["value"] = new_val
    block["units"] = "Pa"
    changes.append(
        Change(
            kind="unit_conversion",
            path=f"{path}.chamber_pressure.value",
            old=raw,
            new=new_val,
            conversion=conv,
            note=note,
        )
    )
    return changes


def normalize_document(
    doc: dict[str, Any],
    *,
    source_id: str,
    rail_ids: set[str] | None = None,
) -> tuple[dict[str, Any], FileReport]:
    out = deepcopy(doc)
    report = FileReport(path="", source_id=source_id)
    species = out.get("species")
    if not isinstance(species, dict):
        return out, report

    # First pass: compute canonical ids, detect in-file collisions.
    planned: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for sid, block in list(species.items()):
        obs_list = []
        if isinstance(block, Mapping):
            obs_list = block.get("observations") or []
        formula = None
        phase = None
        standard_state = None
        if isinstance(obs_list, list) and obs_list and isinstance(obs_list[0], Mapping):
            formula = formula_from_observation(obs_list[0])
            phase = obs_list[0].get("phase")
            standard_state = (
                None
                if obs_list[0].get("standard_state") is None
                else str(obs_list[0].get("standard_state"))
            )
            # If observations disagree on formula, refuse.
            formulas = {
                formula_from_observation(o)
                for o in obs_list
                if isinstance(o, Mapping)
            }
            formulas.discard(None)
            if len(formulas) > 1:
                report.refused.append(
                    Change(
                        kind="species_id",
                        path=f"species.{sid}",
                        old=sid,
                        new=None,
                        refused=True,
                        note=f"observations disagree on formula: {sorted(formulas)}",
                    )
                )
                planned[sid] = sid
                reasons[sid] = "refused_formula_disagreement"
                continue
        canon, reason, refused = canonical_species_id(
            sid,
            formula=formula,
            phase=None if phase is None else str(phase),
            standard_state=standard_state,
            rail_ids=rail_ids,
        )
        if refused:
            report.refused.append(
                Change(
                    kind="species_id",
                    path=f"species.{sid}",
                    old=sid,
                    new=canon,
                    refused=True,
                    note=reason,
                )
            )
            planned[sid] = sid
            reasons[sid] = reason
            continue
        planned[sid] = canon
        reasons[sid] = reason

    # In-file destination collisions: two keys mapping to the same canonical.
    dest_owners: dict[str, list[str]] = defaultdict(list)
    for src, dest in planned.items():
        dest_owners[dest].append(src)
    colliding_dests = {d for d, srcs in dest_owners.items() if len(srcs) > 1}
    for dest in colliding_dests:
        srcs = dest_owners[dest]
        # Hill flattening (or allotrope-token collapse) collided. Fall back to
        # the orthographic source key so isomers stay distinct. Refuse only if
        # that fallback still collides.
        fallback: dict[str, str] = {}
        for src in srcs:
            block = species.get(src) or {}
            obs0 = None
            if isinstance(block, Mapping):
                ol = block.get("observations") or []
                if isinstance(ol, list) and ol and isinstance(ol[0], Mapping):
                    obs0 = ol[0]
            formula = formula_from_observation(obs0) if obs0 else None
            fb = cea_orthography(src, formula)
            fallback[src] = fb
        fb_owners: dict[str, list[str]] = defaultdict(list)
        for src, fb in fallback.items():
            fb_owners[fb].append(src)
        if all(len(v) == 1 for v in fb_owners.values()) and len(fb_owners) == len(srcs):
            for src, fb in fallback.items():
                planned[src] = fb
                reasons[src] = "isomer_orthography_fallback"
            continue
        for src in srcs:
            report.refused.append(
                Change(
                    kind="species_id",
                    path=f"species.{src}",
                    old=src,
                    new=dest,
                    refused=True,
                    note=f"in-file key collision onto {dest!r} from {srcs}",
                )
            )
            planned[src] = src

    # Apply renames (skip those we reverted).
    new_species: dict[str, Any] = {}
    for sid, block in species.items():
        dest = planned.get(sid, sid)
        if dest != sid:
            report.changes.append(
                Change(
                    kind="species_id",
                    path=f"species.{sid}",
                    old=sid,
                    new=dest,
                    note=reasons.get(sid),
                )
            )
        if dest in new_species:
            # Should have been refused; keep original to stay lossless.
            new_species[sid] = block
            continue
        new_species[dest] = block
    out["species"] = new_species

    # Observation-level field idiom (walk possibly-renamed keys).
    for sid, block in new_species.items():
        if not isinstance(block, dict):
            continue
        obs_list = block.get("observations") or []
        if not isinstance(obs_list, list):
            continue
        for obs in obs_list:
            if not isinstance(obs, dict):
                continue
            oid = obs.get("observation_id") or "?"
            values = obs.get("values")
            if isinstance(values, dict):
                report.changes.extend(
                    normalize_values(
                        values, path=f"species.{sid}.observations[{oid}].values"
                    )
                )
            equipment = obs.get("equipment")
            if isinstance(equipment, dict):
                report.changes.extend(
                    normalize_equipment(
                        equipment,
                        path=f"species.{sid}.observations[{oid}].equipment",
                    )
                )

    # Fidelity sample species: / path prefixes, only when we actually renamed.
    rename = {c.old: c.new for c in report.changes if c.kind == "species_id"}
    samples = out.get("fidelity_samples")
    if isinstance(samples, list) and rename:
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            sp = sample.get("species")
            if isinstance(sp, str) and sp in rename:
                sample["species"] = rename[sp]
                report.changes.append(
                    Change(
                        kind="fidelity_sample",
                        path="fidelity_samples.species",
                        old=sp,
                        new=rename[sp],
                    )
                )
            for path_key in ("path", "field_path"):
                p = sample.get(path_key)
                if isinstance(p, str):
                    new_p = p
                    for old, new in rename.items():
                        new_p = new_p.replace(f"species.{old}.", f"species.{new}.")
                    if new_p != p:
                        sample[path_key] = new_p
                        report.changes.append(
                            Change(
                                kind="fidelity_sample",
                                path=f"fidelity_samples.{path_key}",
                                old=p,
                                new=new_p,
                            )
                        )
    return out, report


def reports_equal_after_second_pass(doc: dict[str, Any], source_id: str) -> bool:
    once, _ = normalize_document(doc, source_id=source_id)
    twice, report = normalize_document(once, source_id=source_id)
    return report.n_apply == 0 and twice == once


def run_self_test(fixture: Path = FIXTURE_PATH) -> None:
    doc = _load_yaml(fixture)
    assert isinstance(doc, dict)
    rail = load_rail_ids()
    out, report = normalize_document(doc, source_id="normalize_extract_store", rail_ids=rail)
    spp = out["species"]

    assert "AL" not in spp, "AL should canonicalise to Al"
    assert "Al" in spp
    assert "CO" in spp and "Co" in spp, "CO (carbon monoxide) and Co (cobalt) must stay distinct"
    assert spp["CO"]["observations"][0]["values"]["formula"] == "CO"
    assert spp["Co"]["observations"][0]["values"]["formula"] == "Co"
    assert "BI" in spp, "BI (boron iodide) must not case-fold onto Bi"
    assert "Bi" not in spp
    assert "AL2O3" not in spp
    assert "Al2O3_gas" in spp, "gaseous Al2O3 must not collapse onto the melt component"
    assert "AL2O3_L" not in spp
    assert "Al2O3_liquid" in spp, "condensed liquid must stay distinct from Al2O3_gas"
    assert "Li2CL2" not in spp
    assert "Li2Cl2" in spp, "Class A: CL is chlorine, not an element"
    assert "HF" in spp and "Hf" not in spp, "HF MW~20 is hydrogen fluoride"
    assert "SN" in spp and "Sn" in spp, "SN (S+N, MW~46) must stay distinct from Sn (tin, MW~119)"
    assert "ALOH" not in spp and "AlOH" in spp
    assert "HALO" not in spp and "HAlO" in spp, "HALO connectivity is H-Al-O, not AlOH"
    assert "C3H7,i-propyl" in spp and "C3H7,n-propyl" in spp
    assert "SiO2_b-crt" in spp and "SiO2_b-qz" in spp
    assert "CH2OH" in spp and "CH3O" in spp
    assert report.n_refuse == 0, [c.as_dict() for c in report.refused]
    assert "Fe" in spp

    fe_alpha = next(
        o for o in spp["Fe"]["observations"] if o["observation_id"] == "fe_alpha_pin"
    )
    assert fe_alpha["values"]["alpha"] == 0.24
    assert abs(fe_alpha["values"]["T_K"] - (1426.85 + 273.15)) < 1e-9
    assert fe_alpha["values"]["gas_species"] == "Fe"
    assert fe_alpha["values"]["gas_species_as_published"] == "Fe(g)"

    simple = next(
        o
        for o in spp["Fe"]["observations"]
        if o["observation_id"] == "fe_activity_simple_range"
    )
    gr = simple["values"]["gamma_range"]
    assert gr["low"] == 0.28 and gr["high"] == 0.37
    assert gr["as_published"] == "0.28–0.37"
    assert "gamma" not in simple["values"]

    complex_row = next(
        o
        for o in spp["Fe"]["observations"]
        if o["observation_id"] == "fe_activity_complex_range"
    )
    cgr = complex_row["values"]["gamma_range"]
    assert cgr["parse_status"] == "refused_not_a_scalar"
    assert "low" not in cgr
    assert "$10^{-3}$" in cgr["as_published"]

    psat = next(
        o
        for o in spp["Fe"]["observations"]
        if o["observation_id"] == "fe_psat_atm_only"
    )
    p_pa = psat["values"]["points"][0]["p_Pa"]
    assert abs(p_pa - 5.54e-09 * ATM_PA) < 1e-16
    assert psat["values"]["points"][0]["p_atm"] == 5.54e-09

    # Idempotent.
    out2, report2 = normalize_document(
        out, source_id="normalize_extract_store", rail_ids=rail
    )
    assert report2.n_apply == 0, [c.as_dict() for c in report2.changes]
    assert out2 == out

    # Dangerous case-fold was not applied.
    refused_notes = " ".join(c.note or "" for c in report.refused)
    assert "Al" in spp
    print("self-test OK")
    print(
        json.dumps(
            {
                "n_changes": report.n_apply,
                "n_refused": report.n_refuse,
                "species_after": sorted(spp),
                "change_kinds": dict(Counter(c.kind for c in report.changes)),
            },
            indent=2,
        )
    )


def format_file_report(report: FileReport) -> str:
    lines = [f"## {report.source_id}  ({report.path})"]
    lines.append(f"changes: {report.n_apply}  refused: {report.n_refuse}")
    for c in report.changes:
        extra = f"  [{c.conversion}]" if c.conversion else ""
        lines.append(
            f"  + {c.kind:18s} {c.path}: {c.old!r} → {c.new!r}{extra}"
            + (f"  # {c.note}" if c.note else "")
        )
    for c in report.refused:
        lines.append(
            f"  ! REFUSE {c.kind:12s} {c.path}: {c.old!r} → {c.new!r}"
            + (f"  # {c.note}" if c.note else "")
        )
    return "\n".join(lines)


def process_path(
    path: Path,
    *,
    apply: bool,
    rail_ids: set[str] | None,
) -> FileReport:
    doc = _load_yaml(path)
    if not isinstance(doc, dict):
        report = FileReport(path=str(path), source_id=path.stem)
        report.refused.append(
            Change(kind="file", path=str(path), refused=True, note="not a mapping")
        )
        return report
    source_id = str(doc.get("source_id") or path.stem)
    new_doc, report = normalize_document(
        doc, source_id=source_id, rail_ids=rail_ids
    )
    report.path = str(path)
    if apply and report.n_apply:
        path.write_text(_dump_yaml(new_doc), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="report changes, do not write (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write normalized YAML (overrides --dry-run)",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="restrict to these extract filenames or paths",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the fixture self-test and exit",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="write the machine-readable change report",
    )
    parser.add_argument(
        "--text-out",
        type=Path,
        default=None,
        help="write the per-file text report",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        run_self_test()
        return 0

    apply = bool(args.apply)
    rail_ids = load_rail_ids()
    if args.only:
        paths = []
        for item in args.only:
            p = Path(item)
            if not p.is_file():
                p = EXTRACTS_DIR / item
            if not p.is_file():
                raise SystemExit(f"extract not found: {item}")
            paths.append(p)
    else:
        paths = discover_extracts()

    reports: list[FileReport] = []
    for path in paths:
        reports.append(process_path(path, apply=apply, rail_ids=rail_ids))

    n_files_changed = sum(1 for r in reports if r.n_apply)
    n_changes = sum(r.n_apply for r in reports)
    n_refused = sum(r.n_refuse for r in reports)
    species_renames = [
        c
        for r in reports
        for c in r.changes
        if c.kind == "species_id"
    ]
    refused_species = [
        c
        for r in reports
        for c in r.refused
        if c.kind == "species_id"
    ]

    header = [
        f"mode: {'APPLY' if apply else 'DRY-RUN'}",
        f"files: {len(reports)}  files_with_changes: {n_files_changed}",
        f"changes: {n_changes}  refused: {n_refused}",
        f"species_ids_canonicalised: {len(species_renames)}",
        f"species_ids_ambiguous_refused: {len(refused_species)}",
    ]
    print("\n".join(header))
    print()
    for r in reports:
        if r.n_apply or r.n_refuse:
            print(format_file_report(r))
            print()

    payload = {
        "mode": "apply" if apply else "dry-run",
        "n_files": len(reports),
        "n_files_changed": n_files_changed,
        "n_changes": n_changes,
        "n_refused": n_refused,
        "species_ids_canonicalised": len(species_renames),
        "species_ids_ambiguous_refused": len(refused_species),
        "conversions": CONVERSIONS,
        "files": [
            {
                "path": r.path,
                "source_id": r.source_id,
                "changes": [c.as_dict() for c in r.changes],
                "refused": [c.as_dict() for c in r.refused],
            }
            for r in reports
            if r.n_apply or r.n_refuse
        ],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    if args.text_out:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        chunks = header + [""]
        for r in reports:
            if r.n_apply or r.n_refuse:
                chunks.append(format_file_report(r))
                chunks.append("")
        args.text_out.write_text("\n".join(chunks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
