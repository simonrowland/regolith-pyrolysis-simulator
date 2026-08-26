"""Separate Gibbs / DfG(T) validation battery (additive; not the mass-spec ledger).

The extract-store reproduction harness adopts ``gibbs_table`` only for
``kems-*`` sources and then typed-skips them as not a runtime observable.
This module is the thermochemical battery that gap called for. It does not
change the kems- skip rule and never writes Hertz–Knudsen / Psat residuals.

Provenance (the b-134 trap)
---------------------------
A stored ``gibbs_table`` row is ``engine_own_input`` when it *is* the
coefficient source a live evaluator consumes (NASA CEA polynomials, Shomate
segments). Comparing that row to the same evaluator is a transcription
check. A row is ``independent_tabulation`` when it is a different
compilation (JANAF-4th, LH84, …) than the engine channel. That comparison
is physics/compilation validation. Every scored point carries exactly one
of those two classes.

Units (premise → algebra → unit check → sanity)
-----------------------------------------------
Premise: JANAF-style tables publish ΔfG°(T) in kJ/mol. The engine's NASA-9
polynomials yield G°(T) = H°(T) − T S°(T) in J/mol, with H including
ΔfH°298, so formation is the stoichiometric difference against elemental
records in their CEA standard states.

Algebra::

    ΔfG°_engine(T) [kJ/mol] = (G_sp − ν_P G_P(std) − ν_O2 G_O2) / 1000
    residual_kJ_mol         = ΔfG°_engine(T) − ΔfG°_table(T)
    residual_log10K         = −residual_kJ_mol / (R_kJ · T · ln 10)

R_kJ = 8.314462618e-3 kJ/(mol·K). Primary pin metric is kJ/mol (the table's
native unit). residual_log10K is a companion for process impact, not the
pin: a 5 kJ residual is 0.88 dex at 298.15 K and 0.17 dex at 1500 K, so
pinning only dex would hide T-dependence.

Unit check: residual_kJ_mol has dimension energy/amount. residual_log10K is
dimensionless. R T ln 10 at 298.15 K is 5.708 kJ/mol per dex.

Sanity: ΔfG°(O2, T) ≡ 0 (element). CEA O(g) at 298.15 K gives
ΔfG° ≈ 231.736 kJ/mol against the JANAF 231.731 kJ/mol identity for
½ O2 → O. 1 kJ/mol at 298.15 K = 0.1752 dex in log10 K.

ID map: pilot species only, exact keys, never case-folded (CO ≠ Co).
The full canonical namespace is the std1-standardize work; this module
must not grow a second canonicalizer.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    Nasa9Segment,
    NasaCeaDomainError,
    NasaCeaPolynomial,
)
from simulator.yaml_cache import load_cached_safe_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTS_DIR = REPO_ROOT / "data" / "literature" / "extracts"
CEA_EXTRACT_PATH = EXTRACTS_DIR / "nasa-cea-thermo.yaml"
LEDGER_PATH = REPO_ROOT / "data" / "literature" / "gibbs_battery_residual_ledger.yaml"
PARTITION_JSON_PATH = (
    REPO_ROOT
    / "docs-private"
    / "research"
    / "2026-08-26-gibbs-battery"
    / "partition.json"
)

LN10 = math.log(10.0)
R_KJ_PER_MOL_K = R_J_PER_MOL_K / 1000.0
RT_LN10_298_15_KJ = R_KJ_PER_MOL_K * 298.15 * LN10  # 5.708… kJ/mol per dex
KJ_PER_DEX_298_15 = RT_LN10_298_15_KJ
LOG10K_PER_KJ_298_15 = 1.0 / RT_LN10_298_15_KJ  # 0.1752… dex per kJ/mol

PROVENANCE_ENGINE_OWN_INPUT = "engine_own_input"
PROVENANCE_INDEPENDENT = "independent_tabulation"
PROVENANCE_CLASSES = frozenset(
    {PROVENANCE_ENGINE_OWN_INPUT, PROVENANCE_INDEPENDENT}
)

ENGINE_EVALUATOR_FAMILIES = frozenset({"nasa_cea_7", "nasa_cea_9", "shomate"})

# Label threshold for match vs mismatch (NOT a hide-disagreement gate).
# Transcription: coefficient roundtrip / elemental identity grain.
# Independent: 1 kJ/mol is the tabulated grain of these JANAF extracts.
TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL = 0.05
INDEPENDENT_AGREEMENT_BAND_KJ_MOL = 1.0
# Regression pin band around the recorded residual (mass-spec analogue of 0.01 dex).
PIN_BAND_KJ_MOL = 0.05

TYPED_REFUSAL_PREFIX = "typed-refusal:"

# JANAF P2 grid hits ΔfG=0 at 1200 K with note "elemental reference":
# above this T JANAF ΔfG of P-bearing species is no longer vs P(l).
JANAF_P2_ELEMENTAL_REFERENCE_T_K = 1200.0

_PHASE_TO_STANDARD_STATE = {
    "gas": "gas",
    "ideal_gas": "gas",
    "condensed_solid": "condensed_solid",
    "condensed_liquid": "condensed_liquid",
    "condensed": "condensed",
}


class GibbsBatteryError(ValueError):
    """Base error for the Gibbs battery."""


class UnmappedPilotSpeciesError(GibbsBatteryError):
    """Species is outside the explicit pilot hand-map (never case-fold)."""


class GibbsDomainRefusal(GibbsBatteryError):
    """Temperature outside the overlapping engine/table domain."""


@dataclass(frozen=True)
class PilotChannel:
    """Exact extract-id → CEA-key map for one pilot species.

    Matching is identity on ``extract_species_id``. Callers must not
    case-fold: CEA ``CO`` (carbon monoxide) and ``Co`` (cobalt) coexist.
    """

    extract_species_id: str
    cea_key: str
    nu_P: float
    nu_O2: float
    note: str


# Hand-map ONLY. Full namespace arrives from std1-standardize.
PILOT_CHANNELS: dict[str, PilotChannel] = {
    "PO": PilotChannel(
        "PO",
        "PO",
        1.0,
        0.5,
        "P(std) + 1/2 O2(g) → PO(g); CEA key PO (exact)",
    ),
    "PO2": PilotChannel(
        "PO2",
        "PO2",
        1.0,
        1.0,
        "P(std) + O2(g) → PO2(g); CEA key PO2 (exact)",
    ),
    "P2": PilotChannel(
        "P2",
        "P2",
        2.0,
        0.0,
        "2 P(std) → P2(g); CEA key P2 (exact)",
    ),
    "P4": PilotChannel(
        "P4",
        "P4",
        4.0,
        0.0,
        "4 P(std) → P4(g); CEA key P4 (exact)",
    ),
    "P4O6": PilotChannel(
        "P4O6",
        "P4O6",
        4.0,
        3.0,
        "4 P(std) + 3 O2(g) → P4O6(g); CEA key P4O6 (exact). "
        "JANAF table is flagged UNCERTAIN.",
    ),
    "O2": PilotChannel(
        "O2",
        "O2",
        0.0,
        1.0,
        "Elemental identity: ΔfG°(O2, T) ≡ 0 from O2 − O2. "
        "Transcription/convention check of the formation algebra.",
    ),
}

# Elemental CEA keys used by the formation difference. Exact; not derived
# by case-folding "P" or "O2".
CEA_ELEMENT_O2_KEY = "O2"
CEA_ELEMENT_P_CR_KEY = "P_cr"
CEA_ELEMENT_P_L_KEY = "P_L"

PILOT_INDEPENDENT_SOURCE_ID = "janaf-4th"
PILOT_OWN_INPUT_SOURCE_ID = "nasa-cea-thermo"
PILOT_INDEPENDENT_SPECIES = ("PO", "PO2", "P2", "P4", "P4O6")
PILOT_OWN_INPUT_SPECIES = ("O2",)


@dataclass(frozen=True)
class GibbsTableRow:
    source_id: str
    species_id: str
    observation_id: str
    phase: str | None
    units: str
    review_status: str | None
    evaluator_family: str | None
    payload_shape: tuple[str, ...]
    value_keys: tuple[str, ...]
    T_min_K: float | None
    T_max_K: float | None
    n_grid: int
    provenance_class: str
    is_kems: bool


@dataclass
class GibbsPointScore:
    key: str
    source_id: str
    observation_id: str
    species: str
    provenance_class: str
    comparison_quantity: str
    temperature_K: float | None
    table_kJ_mol: float | None
    engine_kJ_mol: float | None
    residual_kJ_mol: float | None
    residual_log10K: float | None
    band_kJ_mol: float
    status: str
    finding_class: str | None
    engine_channel: str | None
    cea_key: str | None
    skip_reason: str | None
    note: str = ""
    table_note: str | None = None

    def pin_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "source_id": self.source_id,
            "observation_id": self.observation_id,
            "species": self.species,
            "provenance_class": self.provenance_class,
            "comparison_quantity": self.comparison_quantity,
            "temperature_K": self.temperature_K,
            "table_kJ_mol": self.table_kJ_mol,
            "engine_kJ_mol": self.engine_kJ_mol,
            "residual_kJ_mol": self.residual_kJ_mol,
            "residual_log10K": self.residual_log10K,
            "band_kJ_mol": self.band_kJ_mol,
            "status": self.status,
            "finding_class": self.finding_class,
            "engine_channel": self.engine_channel,
            "cea_key": self.cea_key,
            "skip_reason": self.skip_reason,
            "note": self.note,
        }
        if self.table_note:
            out["table_note"] = self.table_note
        return out


def residual_log10K_from_kJ(residual_kJ_mol: float, T_K: float) -> float:
    """Companion dex residual: −Δ(ΔfG) / (R T ln 10)."""
    return -float(residual_kJ_mol) / (R_KJ_PER_MOL_K * float(T_K) * LN10)


def resolve_pilot_channel(species_id: str) -> PilotChannel:
    """Exact-key lookup. Never case-folds (CO carbon monoxide ≠ Co cobalt)."""
    try:
        return PILOT_CHANNELS[species_id]
    except KeyError as exc:
        raise UnmappedPilotSpeciesError(
            f"species_id {species_id!r} is not in the pilot hand-map; "
            "do not case-fold or guess a CEA key (CO ≠ Co). "
            "The full canonical namespace arrives from std1-standardize."
        ) from exc


def _evaluator_family(values: Mapping[str, Any] | None) -> str | None:
    if not isinstance(values, Mapping):
        return None
    fam = values.get("evaluator_family") or values.get("evaluator")
    return str(fam) if fam is not None else None


def _has_coefficient_segments(values: Mapping[str, Any] | None) -> bool:
    if not isinstance(values, Mapping):
        return False
    segs = values.get("segments")
    if not isinstance(segs, list) or not segs:
        return False
    return any(isinstance(s, Mapping) for s in segs)


def classify_provenance(
    *,
    evaluator_family: str | None,
    values: Mapping[str, Any] | None,
) -> str:
    """Structural split: live coefficient source vs independent tabulation."""
    fam = str(evaluator_family) if evaluator_family else ""
    if fam in ENGINE_EVALUATOR_FAMILIES and _has_coefficient_segments(values):
        return PROVENANCE_ENGINE_OWN_INPUT
    return PROVENANCE_INDEPENDENT


def classify_payload(values: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(values, Mapping):
        return ("empty",)
    keys = set(values)
    flags: list[str] = []
    fam = _evaluator_family(values)
    segs = values.get("segments")
    has_segs = isinstance(segs, list) and any(isinstance(s, Mapping) for s in segs)
    if fam in {"nasa_cea_7", "nasa_cea_9"} and has_segs:
        flags.append("cea_polynomial")
    if fam == "shomate" and has_segs:
        flags.append("shomate_polynomial")
    if "tabulated_delta_fG_kJ_mol" in keys:
        flags.append("DfG_T_grid")
    if "delta_fG_298_kJ_mol" in keys or "Delta_f_G_298_kJ_mol" in keys:
        flags.append("DfG_298")
    if any(
        k in keys
        for k in (
            "delta_fH_298_kJ_mol",
            "Delta_f_H_298_kJ_mol",
            "delta_f_H_298_15_J_per_mol",
            "Delta_f_H_298_kJ_mol",
        )
    ):
        flags.append("DfH_298")
    if any("S_298" in k or k == "S_298_over_R" for k in keys):
        flags.append("S_298")
    if any("logk" in k.lower() or k in {"Kd", "log_K", "Kp"} for k in keys):
        flags.append("logK")
    if any("over_R" in k for k in keys):
        flags.append("over_R")
    if "tabulated_points" in keys:
        flags.append("tabulated_points")
    if not flags:
        if "quantity" in keys:
            flags.append("stub_nonnumeric")
        else:
            flags.append("other")
    return tuple(sorted(set(flags)))


def _t_bounds(obs: Mapping[str, Any], values: Mapping[str, Any]) -> tuple[float | None, float | None, int]:
    grid_ts: list[float] = []
    n_grid = 0
    tab = values.get("tabulated_delta_fG_kJ_mol")
    if isinstance(tab, list) and tab:
        n_grid = len(tab)
        for pt in tab:
            if isinstance(pt, Mapping) and pt.get("T_K") is not None:
                try:
                    grid_ts.append(float(pt["T_K"]))
                except (TypeError, ValueError):
                    pass
    t_range = obs.get("T_range_K")
    tmin = tmax = None
    if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
        try:
            tmin, tmax = float(t_range[0]), float(t_range[1])
        except (TypeError, ValueError):
            tmin = tmax = None
    elif grid_ts:
        tmin, tmax = min(grid_ts), max(grid_ts)
    return tmin, tmax, n_grid


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    doc = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise GibbsBatteryError(f"YAML is not a mapping: {path}")
    return doc


def iter_extract_docs(directory: Path | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    extracts = directory or EXTRACTS_DIR
    for path in sorted(extracts.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            doc = _load_yaml_mapping(path)
        except GibbsBatteryError:
            continue
        source_id = str(doc.get("source_id") or path.stem)
        yield source_id, doc


def iter_gibbs_table_rows(
    directory: Path | None = None,
) -> Iterator[tuple[GibbsTableRow, Mapping[str, Any], Mapping[str, Any]]]:
    for source_id, doc in iter_extract_docs(directory):
        species_map = doc.get("species") or {}
        if not isinstance(species_map, Mapping):
            continue
        for species_id, block in species_map.items():
            if not isinstance(block, Mapping):
                continue
            observations = block.get("observations") or []
            if not isinstance(observations, list):
                continue
            for obs in observations:
                if not isinstance(obs, Mapping):
                    continue
                if str(obs.get("type") or "") != "gibbs_table":
                    continue
                values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
                fam = _evaluator_family(values)
                tmin, tmax, n_grid = _t_bounds(obs, values)
                row = GibbsTableRow(
                    source_id=source_id,
                    species_id=str(species_id),
                    observation_id=str(obs.get("observation_id") or ""),
                    phase=str(obs["phase"]) if obs.get("phase") is not None else None,
                    units=str(obs.get("units") or ""),
                    review_status=(
                        str(obs.get("review_status") or doc.get("review_status") or "")
                        or None
                    ),
                    evaluator_family=fam,
                    payload_shape=classify_payload(values),
                    value_keys=tuple(sorted(str(k) for k in values)),
                    T_min_K=tmin,
                    T_max_K=tmax,
                    n_grid=n_grid,
                    provenance_class=classify_provenance(
                        evaluator_family=fam, values=values
                    ),
                    is_kems=source_id.startswith("kems-"),
                )
                yield row, obs, values


def partition_gibbs_tables(
    directory: Path | None = None,
) -> dict[str, Any]:
    """Enumerate non-kems gibbs_table rows and split own-input vs independent."""
    rows: list[GibbsTableRow] = []
    total_obs = 0
    type_counts: Counter[str] = Counter()
    extracts = directory or EXTRACTS_DIR
    for path in sorted(extracts.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            doc = _load_yaml_mapping(path)
        except GibbsBatteryError:
            continue
        source_id = str(doc.get("source_id") or path.stem)
        species_map = doc.get("species") or {}
        if not isinstance(species_map, Mapping):
            continue
        for species_id, block in species_map.items():
            if not isinstance(block, Mapping):
                continue
            observations = block.get("observations") or []
            if not isinstance(observations, list):
                continue
            for obs in observations:
                if not isinstance(obs, Mapping):
                    continue
                total_obs += 1
                otype = str(obs.get("type") or "")
                type_counts[otype] += 1
                if otype != "gibbs_table":
                    continue
                values = (
                    obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
                )
                fam = _evaluator_family(values)
                tmin, tmax, n_grid = _t_bounds(obs, values)
                rows.append(
                    GibbsTableRow(
                        source_id=source_id,
                        species_id=str(species_id),
                        observation_id=str(obs.get("observation_id") or ""),
                        phase=(
                            str(obs["phase"]) if obs.get("phase") is not None else None
                        ),
                        units=str(obs.get("units") or ""),
                        review_status=(
                            str(obs.get("review_status") or doc.get("review_status") or "")
                            or None
                        ),
                        evaluator_family=fam,
                        payload_shape=classify_payload(values),
                        value_keys=tuple(sorted(str(k) for k in values)),
                        T_min_K=tmin,
                        T_max_K=tmax,
                        n_grid=n_grid,
                        provenance_class=classify_provenance(
                            evaluator_family=fam, values=values
                        ),
                        is_kems=source_id.startswith("kems-"),
                    )
                )
    non_kems = [r for r in rows if not r.is_kems]
    kems = [r for r in rows if r.is_kems]
    own = [r for r in non_kems if r.provenance_class == PROVENANCE_ENGINE_OWN_INPUT]
    indep = [r for r in non_kems if r.provenance_class == PROVENANCE_INDEPENDENT]
    by_source: Counter[str] = Counter(r.source_id for r in non_kems)
    by_shape: Counter[str] = Counter("+".join(r.payload_shape) for r in non_kems)
    by_source_class = {
        sid: {
            "n": n,
            "provenance_class": (
                PROVENANCE_ENGINE_OWN_INPUT
                if all(
                    r.provenance_class == PROVENANCE_ENGINE_OWN_INPUT
                    for r in non_kems
                    if r.source_id == sid
                )
                else (
                    PROVENANCE_INDEPENDENT
                    if all(
                        r.provenance_class == PROVENANCE_INDEPENDENT
                        for r in non_kems
                        if r.source_id == sid
                    )
                    else "mixed"
                )
            ),
        }
        for sid, n in by_source.most_common()
    }
    return {
        "schema_version": 1,
        "battery": "gibbs_thermochemistry",
        "observations_total": total_obs,
        "type_counts": dict(type_counts),
        "gibbs_table_total": len(rows),
        "gibbs_table_kems": len(kems),
        "gibbs_table_non_kems": len(non_kems),
        "engine_own_input": len(own),
        "independent_tabulation": len(indep),
        "by_source": by_source_class,
        "by_payload_shape": dict(by_shape.most_common()),
        "kems_note": (
            "The 12 kems-* gibbs_table rows stay in the mass-spec battery as "
            "coverage-only typed skips; they are not part of the 1690."
        ),
        "rows": [asdict(r) for r in non_kems],
    }


def write_partition_json(
    path: Path | None = None,
    directory: Path | None = None,
) -> Path:
    dest = path or PARTITION_JSON_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = partition_gibbs_tables(directory)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return dest


def _polynomial_from_cea_observation(
    *,
    cea_key: str,
    observation: Mapping[str, Any],
) -> NasaCeaPolynomial:
    values = observation["values"]
    if not isinstance(values, Mapping):
        raise GibbsBatteryError(f"{cea_key}: CEA observation has no values mapping")
    family = str(values.get("evaluator_family") or "nasa_cea_9")
    if family != "nasa_cea_9":
        raise GibbsBatteryError(
            f"{cea_key}: pilot evaluator only loads nasa_cea_9; got {family!r}"
        )
    phase = observation.get("phase")
    standard_state = _PHASE_TO_STANDARD_STATE.get(str(phase) if phase is not None else "")
    if standard_state is None:
        raise GibbsBatteryError(
            f"{cea_key}: CEA observation phase {phase!r} is not a NasaCeaPolynomial "
            "standard_state token (use observation['phase'], never the provenance string)"
        )
    segments_raw = values.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        raise GibbsBatteryError(f"{cea_key}: CEA observation has no segments")
    segments = tuple(
        Nasa9Segment(
            T_min_K=float(seg["T_min_K"]),
            T_max_K=float(seg["T_max_K"]),
            coefficients=tuple(float(x) for x in seg["a_coefficients"]),
            b1=float(seg["b1"]),
            b2=float(seg["b2"]),
            exponents=tuple(float(x) for x in seg["exponents"]),
        )
        for seg in segments_raw
    )
    return NasaCeaPolynomial(
        name=cea_key,
        family="nasa_cea_9",
        standard_state=standard_state,  # type: ignore[arg-type]
        segments=segments,
        formula=str(values["formula"]) if values.get("formula") is not None else None,
        delta_f_H_298_15_J_per_mol=values.get("delta_f_H_298_15_J_per_mol"),
        citation=str(values["citation"]) if values.get("citation") is not None else None,
        source_ref_code=(
            str(values["source_ref_code"])
            if values.get("source_ref_code") is not None
            else None
        ),
        reference_pressure_Pa=float(values.get("reference_pressure_Pa", 100_000.0)),
    )


@lru_cache(maxsize=1)
def load_cea_extract(path: str | None = None) -> dict[str, Any]:
    extract_path = Path(path) if path else CEA_EXTRACT_PATH
    return _load_yaml_mapping(extract_path)


def cea_polynomial(cea_key: str, *, extract_path: str | None = None) -> NasaCeaPolynomial:
    """Load one CEA extract record by exact species key. Never case-folds."""
    doc = load_cea_extract(extract_path)
    species_map = doc.get("species") or {}
    if cea_key not in species_map:
        raise UnmappedPilotSpeciesError(
            f"CEA extract has no exact key {cea_key!r}; "
            "refusing to case-fold (CO ≠ Co, AL ≠ Al)."
        )
    block = species_map[cea_key]
    observations = block.get("observations") or []
    match = next(
        (
            obs
            for obs in observations
            if isinstance(obs, Mapping) and obs.get("type") == "gibbs_table"
        ),
        None,
    )
    if match is None:
        raise GibbsBatteryError(f"CEA key {cea_key!r} has no gibbs_table observation")
    return _polynomial_from_cea_observation(cea_key=cea_key, observation=match)


def elemental_phosphorus_polynomial(
    T_K: float, *, extract_path: str | None = None
) -> NasaCeaPolynomial:
    """CEA elemental P standard state at T: P_cr below melt, P_L at/above.

    Melt breakpoint is the shared endpoint of the two CEA records
    (P_cr.T_max_K == P_L.T_min_K), not a magic number.
    """
    p_cr = cea_polynomial(CEA_ELEMENT_P_CR_KEY, extract_path=extract_path)
    p_l = cea_polynomial(CEA_ELEMENT_P_L_KEY, extract_path=extract_path)
    T = float(T_K)
    if T < p_cr.T_max_K:
        return p_cr
    return p_l


def engine_delta_fG_kJ_mol(
    channel: PilotChannel,
    T_K: float,
    *,
    extract_path: str | None = None,
) -> float:
    """ΔfG°(T) in kJ/mol from CEA polynomials on the overlapping domain only."""
    T = float(T_K)
    poly = cea_polynomial(channel.cea_key, extract_path=extract_path)
    try:
        g = poly.evaluate(T).g_J_per_mol
    except NasaCeaDomainError as exc:
        raise GibbsDomainRefusal(
            f"{TYPED_REFUSAL_PREFIX}T_outside_engine_domain:{exc}"
        ) from exc
    if channel.nu_P:
        p_el = elemental_phosphorus_polynomial(T, extract_path=extract_path)
        try:
            g -= channel.nu_P * p_el.evaluate(T).g_J_per_mol
        except NasaCeaDomainError as exc:
            raise GibbsDomainRefusal(
                f"{TYPED_REFUSAL_PREFIX}T_outside_engine_domain:{exc}"
            ) from exc
    if channel.nu_O2:
        o2 = cea_polynomial(CEA_ELEMENT_O2_KEY, extract_path=extract_path)
        try:
            g -= channel.nu_O2 * o2.evaluate(T).g_J_per_mol
        except NasaCeaDomainError as exc:
            raise GibbsDomainRefusal(
                f"{TYPED_REFUSAL_PREFIX}T_outside_engine_domain:{exc}"
            ) from exc
    return g / 1000.0


def _dfg_points_from_values(
    values: Mapping[str, Any],
) -> list[tuple[float, float, str | None]]:
    points: list[tuple[float, float, str | None]] = []
    seen: set[float] = set()
    tab = values.get("tabulated_delta_fG_kJ_mol")
    if isinstance(tab, list):
        for pt in tab:
            if not isinstance(pt, Mapping):
                continue
            if pt.get("T_K") is None or pt.get("delta_fG") is None:
                continue
            T = float(pt["T_K"])
            dfg = float(pt["delta_fG"])
            note = str(pt["note"]) if pt.get("note") is not None else None
            points.append((T, dfg, note))
            seen.add(T)
    if values.get("delta_fG_298_kJ_mol") is not None and 298.15 not in seen:
        points.append((298.15, float(values["delta_fG_298_kJ_mol"]), "298_anchor"))
    return points


def _point_key(source_id: str, observation_id: str, T_K: float) -> str:
    t_label = f"{T_K:.2f}".rstrip("0").rstrip(".")
    return f"{source_id}::{observation_id}:T={t_label}"


def _finding_class(
    *,
    provenance_class: str,
    status: str,
    species: str,
    T_K: float,
    table_note: str | None,
) -> str | None:
    if status not in {"match", "mismatch"}:
        return None
    if provenance_class == PROVENANCE_ENGINE_OWN_INPUT:
        return "data_integrity" if status == "mismatch" else "transcription_ok"
    note_l = (table_note or "").lower()
    # P2/P4 are elemental allotropes: JANAF switches the P standard state to
    # P2(g) at 1200 K (table note "elemental reference"). Compound oxides keep
    # compilation_disagreement as the primary class; the high-T P(l) vs P2(g)
    # fork is documented, not used to overwrite a 600 kJ JANAF-vs-Gurvich gap.
    elemental_allotrope = species in {"P2", "P4"}
    elemental = "elemental reference" in note_l or (
        elemental_allotrope and T_K >= JANAF_P2_ELEMENTAL_REFERENCE_T_K
    )
    if elemental:
        return "elemental_reference_state_mismatch"
    if status == "mismatch":
        return "compilation_disagreement"
    return "compilation_agreement"


def _status_for_residual(
    residual_kJ_mol: float, provenance_class: str
) -> str:
    band = (
        TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL
        if provenance_class == PROVENANCE_ENGINE_OWN_INPUT
        else INDEPENDENT_AGREEMENT_BAND_KJ_MOL
    )
    if abs(residual_kJ_mol) <= band:
        return "match"
    return "mismatch"


def score_point(
    *,
    source_id: str,
    observation_id: str,
    species: str,
    provenance_class: str,
    T_K: float,
    table_kJ_mol: float,
    table_note: str | None = None,
    extract_path: str | None = None,
    extra_note: str = "",
) -> GibbsPointScore:
    channel = resolve_pilot_channel(species)
    key = _point_key(source_id, observation_id, T_K)
    try:
        engine = engine_delta_fG_kJ_mol(channel, T_K, extract_path=extract_path)
    except GibbsDomainRefusal as exc:
        reason = str(exc)
        if not reason.startswith(TYPED_REFUSAL_PREFIX):
            reason = f"{TYPED_REFUSAL_PREFIX}T_outside_engine_domain"
        return GibbsPointScore(
            key=key,
            source_id=source_id,
            observation_id=observation_id,
            species=species,
            provenance_class=provenance_class,
            comparison_quantity="delta_fG_kJ_mol",
            temperature_K=T_K,
            table_kJ_mol=table_kJ_mol,
            engine_kJ_mol=None,
            residual_kJ_mol=None,
            residual_log10K=None,
            band_kJ_mol=PIN_BAND_KJ_MOL,
            status="typed-refusal",
            finding_class=None,
            engine_channel="nasa_cea_9",
            cea_key=channel.cea_key,
            skip_reason=reason,
            note=extra_note or "overlapping T-range only; no extrapolation",
            table_note=table_note,
        )
    residual = engine - float(table_kJ_mol)
    status = _status_for_residual(residual, provenance_class)
    return GibbsPointScore(
        key=key,
        source_id=source_id,
        observation_id=observation_id,
        species=species,
        provenance_class=provenance_class,
        comparison_quantity="delta_fG_kJ_mol",
        temperature_K=T_K,
        table_kJ_mol=float(table_kJ_mol),
        engine_kJ_mol=engine,
        residual_kJ_mol=residual,
        residual_log10K=residual_log10K_from_kJ(residual, T_K),
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=_finding_class(
            provenance_class=provenance_class,
            status=status,
            species=species,
            T_K=T_K,
            table_note=table_note,
        ),
        engine_channel="nasa_cea_9",
        cea_key=channel.cea_key,
        skip_reason=None,
        note=extra_note,
        table_note=table_note,
    )


def _janaf_doc(directory: Path | None = None) -> dict[str, Any]:
    path = (directory or EXTRACTS_DIR) / "janaf-4th.yaml"
    return _load_yaml_mapping(path)


def score_pilot(
    *,
    directory: Path | None = None,
    extract_path: str | None = None,
) -> list[GibbsPointScore]:
    """Score the pilot slice: five independent JANAF species + O2 identity."""
    scores: list[GibbsPointScore] = []
    janaf = _janaf_doc(directory)
    species_map = janaf.get("species") or {}
    for species_id in PILOT_INDEPENDENT_SPECIES:
        block = species_map.get(species_id) or {}
        observations = block.get("observations") or []
        for obs in observations:
            if not isinstance(obs, Mapping) or obs.get("type") != "gibbs_table":
                continue
            observation_id = str(obs.get("observation_id") or "")
            values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
            points = _dfg_points_from_values(values)
            if not points:
                scores.append(
                    GibbsPointScore(
                        key=f"{PILOT_INDEPENDENT_SOURCE_ID}::{observation_id}:payload",
                        source_id=PILOT_INDEPENDENT_SOURCE_ID,
                        observation_id=observation_id,
                        species=species_id,
                        provenance_class=PROVENANCE_INDEPENDENT,
                        comparison_quantity="delta_fG_kJ_mol",
                        temperature_K=None,
                        table_kJ_mol=None,
                        engine_kJ_mol=None,
                        residual_kJ_mol=None,
                        residual_log10K=None,
                        band_kJ_mol=PIN_BAND_KJ_MOL,
                        status="typed-refusal",
                        finding_class=None,
                        engine_channel="nasa_cea_9",
                        cea_key=PILOT_CHANNELS[species_id].cea_key,
                        skip_reason=f"{TYPED_REFUSAL_PREFIX}payload_not_comparable",
                        note="no tabulated ΔfG° points on this observation",
                    )
                )
                continue
            for T_K, table_kJ, table_note in points:
                scores.append(
                    score_point(
                        source_id=PILOT_INDEPENDENT_SOURCE_ID,
                        observation_id=observation_id,
                        species=species_id,
                        provenance_class=PROVENANCE_INDEPENDENT,
                        T_K=T_K,
                        table_kJ_mol=table_kJ,
                        table_note=table_note,
                        extract_path=extract_path,
                    )
                )
    for T_K in (298.15, 1000.0, 2000.0):
        scores.append(
            score_point(
                source_id=PILOT_OWN_INPUT_SOURCE_ID,
                observation_id="cea_O2_gibbs",
                species="O2",
                provenance_class=PROVENANCE_ENGINE_OWN_INPUT,
                T_K=T_K,
                table_kJ_mol=0.0,
                table_note="elemental identity ΔfG°(O2)=0",
                extract_path=extract_path,
                extra_note=(
                    "Transcription/convention check: CEA O2 polynomial against "
                    "the thermodynamic identity ΔfG°(element)=0. Not independent physics."
                ),
            )
        )
    return scores


def comparable_scores(scores: Sequence[GibbsPointScore]) -> list[GibbsPointScore]:
    return [s for s in scores if s.status in {"match", "mismatch"}]


def disagreement_scores(scores: Sequence[GibbsPointScore]) -> list[GibbsPointScore]:
    return [
        s
        for s in comparable_scores(scores)
        if s.status == "mismatch"
    ]


LEDGER_HEADER = {
    "schema_version": 1,
    "battery": "gibbs_thermochemistry",
    "metric": "residual_kJ_mol",
    "metric_units": "kJ/mol",
    "companion_metric": "residual_log10K",
    "companion_metric_units": "dimensionless",
    "band_kind": "absolute_kJ_mol",
    "comparison_quantity": "delta_fG_kJ_mol",
    "never_widen": True,
    "pin_edit_policy": "mechanism-comment-required",
    "doctrine": (
        "The residual IS the result. Never widen band_kJ_mol to hide a changed "
        "mechanism. Never edit a pin without a mechanism comment. "
        "provenance_class=engine_own_input is a transcription check "
        "(fail → data-integrity finding). "
        "provenance_class=independent_tabulation is a physics/compilation check "
        "(fail → physics finding). Do not blur the two. "
        "This file is not tests/chemistry/extract_store_reproduction_residual_baselines.yaml."
    ),
    "agreement_bands_kJ_mol": {
        "engine_own_input": TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL,
        "independent_tabulation": INDEPENDENT_AGREEMENT_BAND_KJ_MOL,
        "pin_regression": PIN_BAND_KJ_MOL,
        "note": (
            "Agreement bands label match vs mismatch against residual=0. "
            "The pin band is a regression envelope around the recorded residual, "
            "identical in role to the mass-spec ±0.01 dex pin. Neither band is "
            "an accuracy tolerance to be widened."
        ),
    },
    "pilot_species_id_map": {
        key: {
            "cea_key": ch.cea_key,
            "nu_P": ch.nu_P,
            "nu_O2": ch.nu_O2,
            "note": ch.note,
        }
        for key, ch in PILOT_CHANNELS.items()
    },
    "id_map_note": (
        "Hand-map of the pilot slice only. Matching is exact; never case-fold "
        "(CEA CO carbon monoxide ≠ Co cobalt; AL ≠ Al). The full canonical "
        "namespace arrives from the std1-standardize work."
    ),
    "elemental_cea_keys": {
        "O2": CEA_ELEMENT_O2_KEY,
        "P_cr": CEA_ELEMENT_P_CR_KEY,
        "P_L": CEA_ELEMENT_P_L_KEY,
        "P_std_rule": "P_cr for T < P_cr.T_max_K; P_L otherwise (CEA shared melt endpoint)",
    },
}


def ledger_document(scores: Sequence[GibbsPointScore]) -> dict[str, Any]:
    comparable = comparable_scores(scores)
    refused = [s for s in scores if s.status == "typed-refusal"]
    return {
        **LEDGER_HEADER,
        "pilot_summary": {
            "species": list(PILOT_INDEPENDENT_SPECIES) + list(PILOT_OWN_INPUT_SPECIES),
            "points_scored": len(comparable),
            "points_refused": len(refused),
            "matches": sum(1 for s in comparable if s.status == "match"),
            "mismatches": sum(1 for s in comparable if s.status == "mismatch"),
            "by_provenance": {
                PROVENANCE_ENGINE_OWN_INPUT: sum(
                    1
                    for s in comparable
                    if s.provenance_class == PROVENANCE_ENGINE_OWN_INPUT
                ),
                PROVENANCE_INDEPENDENT: sum(
                    1
                    for s in comparable
                    if s.provenance_class == PROVENANCE_INDEPENDENT
                ),
            },
            "by_finding_class": dict(
                Counter(s.finding_class for s in comparable if s.finding_class)
            ),
        },
        "points": [s.pin_dict() for s in scores],
    }


def write_ledger(
    path: Path | None = None,
    scores: Sequence[GibbsPointScore] | None = None,
) -> Path:
    dest = path or LEDGER_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = ledger_document(scores if scores is not None else score_pilot())
    comment = (
        "# Pinned residual ledger for the Gibbs / DfG thermochemical battery.\n"
        "# Separate from tests/chemistry/extract_store_reproduction_residual_baselines.yaml\n"
        "# (that file is the mass-spec ledger and stays one).\n"
        "# Doctrine: the residual IS the result. Never widen band_kJ_mol to hide a\n"
        "# changed mechanism. engine_own_input fail → data-integrity; independent\n"
        "# fail → physics/compilation finding. Do not blur the two.\n"
    )
    dest.write_text(
        comment
        + yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        ),
        encoding="utf-8",
    )
    return dest


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    dest = path or LEDGER_PATH
    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping):
        raise GibbsBatteryError(f"ledger is not a mapping: {dest}")
    return dict(doc)


def ledger_pins_by_key(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    points = doc.get("points") or []
    out: dict[str, Mapping[str, Any]] = {}
    for pt in points:
        if not isinstance(pt, Mapping):
            continue
        if pt.get("status") not in {"match", "mismatch"}:
            continue
        key = str(pt.get("key") or "")
        if not key:
            raise GibbsBatteryError("ledger comparable point missing key")
        if key in out:
            raise GibbsBatteryError(f"duplicate ledger key {key!r}")
        out[key] = pt
    return out
