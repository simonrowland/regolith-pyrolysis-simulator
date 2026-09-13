"""Species-rail differential harness (internal-consistency instrument).

The same per-species quantity from each laptop-runnable engine channel and
each keyed compilation table, reported as a residual matrix. The residual IS
the result. No pass/fail gate, no coefficient retune, no ``scoring_eligible``
field.

Pilot sources: JANAF-4th (J-era, kJ/mol, p° = 0.1 MPa) and Pankratz 1987
USBM B689 (cal-era, kcal/mol, p° = 1 atm). Pilot channels: NASA-CEA
polynomials and Ellingham dG per mol O2. VapoRock / thermoengine / MELTS are
typed-refusal holes on the laptop.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import yaml

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_FIT_RANGE_K,
    ELLINGHAM_FIT_SEGMENTS,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_range_K,
)
from simulator.diagnostic_helpers.gibbs_battery import (
    INDEPENDENT_AGREEMENT_BAND_KJ_MOL,
    LEDGER_PATH as GIBBS_PILOT_LEDGER_PATH,
    LN10,
    PIN_BAND_KJ_MOL,
    PROVENANCE_ENGINE_OWN_INPUT,
    PROVENANCE_INDEPENDENT,
    R_KJ_PER_MOL_K,
    TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL,
    TYPED_REFUSAL_PREFIX,
    GibbsDomainRefusal,
    GibbsPointScore,
    PilotChannel,
    UnmappedPilotSpeciesError,
    cea_polynomial,
    classify_provenance,
    engine_delta_fG_kJ_mol,
    load_cea_extract,
    residual_log10K_from_kJ,
    resolve_pilot_channel,
)
from simulator.diagnostic_helpers.species_rail import (
    TIER_MAJOR,
    SpeciesRail,
    derive_species_rail,
    parse_formula_composition,
)
from simulator.reference_data import pankratz_1987_usbm_b689_loader as b689_loader
from simulator.reference_data.janaf import (
    iter_table_paths,
    load_table_document,
)
from simulator.state import OXIDE_TO_METAL
from simulator.vapour_rail.engine_crosscheck import divergence_label
from simulator.vapour_rail.nasa_cea import NasaCeaDomainError

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "data" / "literature" / "species_rail_differential_ledger.yaml"
REPORT_DIR = (
    REPO_ROOT / "docs-private" / "research" / "2026-09-12-differential-harness"
)
COMPILATION_JANAF = "janaf"
COMPILATION_B689 = "pankratz-1987-usbm-b689"
CHANNEL_NASA_CEA = "nasa_cea_9"
CHANNEL_ELLINGHAM = "ellingham"
CHANNEL_CEA_VS_ELLINGHAM = "nasa_cea_vs_ellingham"
UNAVAILABLE_CHANNELS = ("vaporock", "thermoengine", "melts")

# Operating-envelope temperature bands for the headline residual tables.
# Boundary sources (each cut is a named project constant, not a round number):
#   1100 K — ELLINGHAM_FIT_RANGE_K[0], lower bound of the legacy linear
#            high-T Ellingham refit (ellingham_thermo.py).
#   1700 K — ELLINGHAM_FIT_RANGE_K[1], upper bound of that same refit.
#            The 1100-1700 K window is the first recipe-envelope band.
#   2300 K — vapour-rail operating-envelope high
#            (tools/compose_vapour_rail_carriers.py::OPERATING_HIGH_K = 2300 K
#            = 2027 °C). Sits above the CLAUDE.md millibar bake-off wall
#            temperatures: SiO 1745 °C = 2018 K, Fe 1775 °C = 2048 K
#            (docs-private/research/2026-08-26-wall-temperature-table).
#   2600 K — JANAF / Ellingham primary-refit grid ceiling for the mbar-regime
#            metals (ellingham_fit_range_K("Na"|"Mg"|"Fe"|"Ca")[1] = 2600 K).
#
# Membership is half-open on the left except the first band: T <= 1100,
# 1100 < T <= 1700, 1700 < T <= 2300, 2300 < T <= 2600, T > 2600.
# Envelope bands (recipe-relevant) are the middle three; headline tables
# list those first. The full-range top-20 stays below. Data are not deleted.
_T_BAND_1100_K = float(ELLINGHAM_FIT_RANGE_K[0])
_T_BAND_1700_K = float(ELLINGHAM_FIT_RANGE_K[1])
_T_BAND_2300_K = 2300.0
_T_BAND_2600_K = 2600.0


@dataclass(frozen=True)
class TemperatureBand:
    label: str
    t_min_exclusive: float | None
    t_max_inclusive: float | None
    envelope: bool


TEMPERATURE_BANDS: tuple[TemperatureBand, ...] = (
    TemperatureBand("<=1100", None, _T_BAND_1100_K, False),
    TemperatureBand("1100-1700", _T_BAND_1100_K, _T_BAND_1700_K, True),
    TemperatureBand("1700-2300", _T_BAND_1700_K, _T_BAND_2300_K, True),
    TemperatureBand("2300-2600", _T_BAND_2300_K, _T_BAND_2600_K, True),
    TemperatureBand(">2600", _T_BAND_2600_K, None, False),
)
ENVELOPE_BANDS: tuple[TemperatureBand, ...] = tuple(
    band for band in TEMPERATURE_BANDS if band.envelope
)


def temperature_band_for(T_K: float | None) -> TemperatureBand | None:
    if T_K is None:
        return None
    T = float(T_K)
    for band in TEMPERATURE_BANDS:
        lo_ok = band.t_min_exclusive is None or T > band.t_min_exclusive
        hi_ok = band.t_max_inclusive is None or T <= band.t_max_inclusive
        if lo_ok and hi_ok:
            return band
    return None

# JANAF coded states from PHASE_SUFFIX_RE. B689 uses c/g/l. Anything else is
# prose and is refused, never guessed.
_CODED_GAS = frozenset({"g", "gas"})
_CODED_SOLID = frozenset({"cr", "c", "s", "condensed_solid", "condensed"})
_CODED_LIQUID = frozenset({"l", "liquid", "condensed_liquid"})
_CODED_REF = frozenset({"ref"})
_CODED_MIXED = frozenset({"cr,l", "c,l", "c,1", "l,g"})

# Diatomic elemental standard states (JANAF/CEA convention, 298 K and above
# for H, N, O, F, Cl). Coefficient in ΔfG is n_element / 2.
_DIATOMIC_STANDARD_FORMULA = {
    "H": "H2",
    "N": "N2",
    "O": "O2",
    "F": "F2",
    "Cl": "Cl2",
}

PHASE_GAS = "gas"
PHASE_SOLID = "solid"
PHASE_LIQUID = "liquid"
PHASE_REF = "elemental_ref"
PHASE_MIXED = "mixed"
PHASE_PROSE = "prose"

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# Premise: the thermochemical calorie is defined as 4.184 J exactly
# (CIPM / ISO 31-4). USBM B689 prints ΔfG as kcal/mol using that calorie.
# Algebra: 1 kcal/mol = 4.184 kJ/mol.
# Unit check: kcal/mol × 4.184 kJ/kcal = kJ/mol.
# Sanity: B689 AgS(g) 298.15 K ΔfG = 80.700 kcal/mol → 337.6488 kJ/mol;
# recomputed log10 Kf = −337.6488 / (R T ln 10) = −59.153, matching the
# printed −59.154 to the 0.001 table grain.
THERMOCHEMICAL_CALORIE_J = 4.184
KCAL_PER_MOL_TO_KJ_PER_MOL = THERMOCHEMICAL_CALORIE_J

# Premise: log10 Kf = −ΔfG° / (R T ln 10) with ΔfG° the formation Gibbs
# at the table's standard pressure.
# Algebra: residual_log10K = −Δ(ΔfG)[kJ/mol] / (R[kJ/(mol·K)] T ln 10).
# R = 8.314462618 J/(mol·K) (CODATA 2018) = 8.314462618e-3 kJ/(mol·K).
# Unit check: kJ/mol / (kJ/(mol·K) · K) is dimensionless.
# Sanity: ΔfG = 0 → log10 Kf = 0; at 298.15 K, 1 kJ/mol = 0.1752 dex.
# Standard pressure (stated per source, never silently converted):
#   JANAF printed p° = 0.1 MPa = 1 bar.
#   NASA-CEA gas records: reference_pressure_Pa = 100000 (0.1 MPa), same as JANAF.
#   NASA-CEA condensed records: 101325 Pa = 1 atm (NasaCeaPolynomial docstring).
#   B689 (USBM Bulletin 689, 1987): 1 atm. A 1 atm vs 1 bar slip is
#   Δn_g RT ln(1.01325) ≈ 0.033 kJ/mol at 298 K per mol of gas, inside the
#   1 kJ/mol independent band; it is noted, not corrected.

def kcal_per_mol_to_kJ_per_mol(kcal_per_mol: float) -> float:
    return float(kcal_per_mol) * KCAL_PER_MOL_TO_KJ_PER_MOL


def log10K_from_delta_fG_kJ_mol(delta_fG_kJ_mol: float, T_K: float) -> float:
    return -float(delta_fG_kJ_mol) / (R_KJ_PER_MOL_K * float(T_K) * LN10)


def printed_logk_tolerance(as_published: str | None) -> float:
    """Half a unit in the last printed decimal place."""

    token = str(as_published or "").strip()
    if not token or token.upper() == "INFINITE":
        return 0.5
    cleaned = token.lstrip("+-")
    if "." in cleaned:
        decimals = len(cleaned.split(".", 1)[1])
        return 0.5 * (10.0 ** (-decimals))
    return 0.5


# ---------------------------------------------------------------------------
# Phase tokens
# ---------------------------------------------------------------------------


def classify_phase_token(token: str | None) -> str:
    """Map a compilation phase token. Prose and mixed phases are not guessed."""

    if token is None:
        return PHASE_PROSE
    raw = str(token).strip()
    if not raw:
        return PHASE_PROSE
    key = raw.lower()
    if key in _CODED_GAS:
        return PHASE_GAS
    if key in _CODED_SOLID:
        return PHASE_SOLID
    if key in _CODED_LIQUID:
        return PHASE_LIQUID
    if key in _CODED_REF:
        return PHASE_REF
    if key in _CODED_MIXED:
        return PHASE_MIXED
    return PHASE_PROSE


# ---------------------------------------------------------------------------
# Keyed table points
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyedTablePoint:
    compilation_id: str
    record_id: str
    formula: str
    phase: str
    phase_kind: str
    T_K: float
    delta_fG_kJ_mol: float
    log10_Kf: float | None
    log10_Kf_as_published: str | None
    printed_page: int | None
    note: str = ""


@dataclass(frozen=True)
class KeyRefusal:
    compilation_id: str
    record_id: str
    formula: str | None
    phase: str | None
    reason: str
    T_K: float | None = None
    printed_page: int | None = None
    note: str = ""


def _cell_value(cell: object) -> float | None:
    if isinstance(cell, Mapping):
        value = cell.get("value")
    else:
        value = cell
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _cell_as_published(cell: object) -> str | None:
    if isinstance(cell, Mapping) and cell.get("as_published") is not None:
        return str(cell["as_published"])
    if isinstance(cell, Mapping) and cell.get("raw") is not None:
        return str(cell["raw"])
    return None


def iter_janaf_keyed_points(
    rail: SpeciesRail,
) -> Iterator[KeyedTablePoint | KeyRefusal]:
    """Yield in-rail JANAF formation points. Mixed/prose states are refused."""

    for path in iter_table_paths():
        document = load_table_document(path)
        table = document.get("table") or {}
        index = table.get("index_entry") or {}
        formula = str(index.get("formula") or index.get("formula_as_published") or "")
        phase = str(index.get("state") or "")
        record_id = str(table.get("table_id") or path.stem)
        if not formula:
            yield KeyRefusal(
                COMPILATION_JANAF, record_id, None, phase or None, "missing_formula"
            )
            continue
        if not rail.in_rail(formula):
            continue
        phase_kind = classify_phase_token(phase)
        if phase_kind in {PHASE_PROSE, PHASE_MIXED}:
            yield KeyRefusal(
                COMPILATION_JANAF,
                record_id,
                formula,
                phase,
                "prose_phase" if phase_kind == PHASE_PROSE else "mixed_phase",
            )
            continue
        for row in table.get("values") or []:
            if not isinstance(row, Mapping):
                continue
            T_K = _cell_value(row.get("temperature"))
            if T_K is None:
                continue
            if T_K <= 0.0:
                yield KeyRefusal(
                    COMPILATION_JANAF,
                    record_id,
                    formula,
                    phase,
                    "nonpositive_temperature",
                    T_K=T_K,
                )
                continue
            dfg = _cell_value(row.get("formation_gibbs_energy"))
            if dfg is None:
                continue
            logk_cell = row.get("log10_formation_equilibrium_constant")
            yield KeyedTablePoint(
                compilation_id=COMPILATION_JANAF,
                record_id=record_id,
                formula=formula,
                phase=phase,
                phase_kind=phase_kind,
                T_K=T_K,
                delta_fG_kJ_mol=dfg,
                log10_Kf=_cell_value(logk_cell),
                log10_Kf_as_published=_cell_as_published(logk_cell),
                printed_page=None,
                note="janaf p°=0.1 MPa",
            )


def iter_b689_keyed_points(
    rail: SpeciesRail,
    *,
    include_ocr_suspect: bool = True,
) -> Iterator[KeyedTablePoint | KeyRefusal]:
    """Yield in-rail B689 formation points. Prose phases are refused.

    ``include_ocr_suspect=True`` is the loader's documented inspect path;
    ``load_records()`` otherwise raises on the first OCR-suspect record and
    cannot iterate the corpus. Suspect flags are copied into the point note;
    values are not repaired.
    """

    for record in b689_loader.load_records(include_ocr_suspect=include_ocr_suspect):
        record_id = str(record.get("record_id") or "")
        if str(record.get("table_kind") or "") != "formation":
            continue
        formula = record.get("formula") or record.get("formula_as_published")
        formula_s = str(formula) if formula else ""
        phase = record.get("phase")
        phase_s = str(phase) if phase is not None else ""
        printed_page = record.get("printed_page")
        page_i = int(printed_page) if printed_page is not None else None
        if not formula_s:
            yield KeyRefusal(
                COMPILATION_B689, record_id, None, phase_s or None, "missing_formula",
                printed_page=page_i,
            )
            continue
        if not rail.in_rail(formula_s):
            continue
        phase_kind = classify_phase_token(phase_s)
        if phase_kind in {PHASE_PROSE, PHASE_MIXED}:
            yield KeyRefusal(
                COMPILATION_B689,
                record_id,
                formula_s,
                phase_s,
                "prose_phase" if phase_kind == PHASE_PROSE else "mixed_phase",
                printed_page=page_i,
            )
            continue
        ocr_note = ""
        if record.get("metadata_ocr_suspect") or record.get("ocr_suspect"):
            ocr_note = "ocr_suspect=true; scored as printed, not repaired"
        for row in record.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            cells = row.get("cells") if isinstance(row.get("cells"), Mapping) else {}
            T_K = _cell_value(cells.get("temperature"))
            if T_K is None or T_K <= 0.0:
                continue
            delta_g_kcal = _cell_value(cells.get("delta_g"))
            if delta_g_kcal is None:
                continue
            logk_cell = cells.get("log_k")
            yield KeyedTablePoint(
                compilation_id=COMPILATION_B689,
                record_id=record_id,
                formula=formula_s,
                phase=phase_s,
                phase_kind=phase_kind,
                T_K=T_K,
                delta_fG_kJ_mol=kcal_per_mol_to_kJ_per_mol(delta_g_kcal),
                log10_Kf=_cell_value(logk_cell),
                log10_Kf_as_published=_cell_as_published(logk_cell),
                printed_page=page_i,
                note=(
                    "b689 p°=1 atm; kcal→kJ via 4.184 J/cal_th"
                    + (f"; {ocr_note}" if ocr_note else "")
                ),
            )


def table_self_check_residual(
    point: KeyedTablePoint,
) -> float | None:
    """Printed log10 Kf minus −ΔfG/(R T ln 10) from the same row.

    A disagreement at printed precision is a compilation finding, not an
    engine finding. Returns None when the table did not print log10 Kf.
    """

    if point.log10_Kf is None:
        return None
    recomputed = log10K_from_delta_fG_kJ_mol(point.delta_fG_kJ_mol, point.T_K)
    return float(point.log10_Kf) - recomputed


# Printed-precision self-check floor. JANAF low-T rows disagree with CODATA-R
# recomputation at ~0.02–0.08 dex (R-convention / rounding, not OCR). A
# finding is reserved for residuals well above that grain.
TABLE_SELF_CHECK_FINDING_DEX = 0.1


# ---------------------------------------------------------------------------
# NASA-CEA channel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CeaIndexEntry:
    cea_key: str
    formula: str
    phase: str
    T_min_K: float
    T_max_K: float


@dataclass(frozen=True)
class CeaResolution:
    cea_key: str | None
    reason: str | None
    band_K: tuple[float, float] | None = None


@lru_cache(maxsize=1)
def cea_index() -> tuple[CeaIndexEntry, ...]:
    doc = load_cea_extract()
    species_map = doc.get("species") or {}
    entries: list[CeaIndexEntry] = []
    for cea_key, block in species_map.items():
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
            continue
        values = match.get("values") if isinstance(match.get("values"), Mapping) else {}
        formula = values.get("formula")
        phase = match.get("phase")
        t_range = match.get("T_range_K") or []
        if formula is None or phase is None or len(t_range) != 2:
            continue
        entries.append(
            CeaIndexEntry(
                cea_key=str(cea_key),
                formula=str(formula),
                phase=str(phase),
                T_min_K=float(t_range[0]),
                T_max_K=float(t_range[1]),
            )
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def cea_by_formula() -> dict[str, tuple[CeaIndexEntry, ...]]:
    buckets: dict[str, list[CeaIndexEntry]] = defaultdict(list)
    for entry in cea_index():
        buckets[entry.formula].append(entry)
    return {formula: tuple(entries) for formula, entries in buckets.items()}


def _cea_phase_ok(entry_phase: str, phase_kind: str) -> bool:
    if phase_kind == PHASE_GAS:
        return entry_phase == "gas"
    if phase_kind == PHASE_LIQUID:
        return entry_phase == "condensed_liquid"
    if phase_kind == PHASE_SOLID:
        return entry_phase in {"condensed_solid", "condensed"}
    if phase_kind == PHASE_REF:
        return True
    return False


def resolve_cea_species(formula: str, phase_kind: str, T_K: float) -> CeaResolution:
    """Exact formula-string match, then unique phase+T cover. Never case-folds.

    Multiple condensed allotropes of one formula are an ambiguity (refuse),
    not a name pick. ``ref`` (JANAF elemental standard) accepts the unique
    formula match when exactly one CEA record exists.
    """

    T = float(T_K)
    formula_hits = list(cea_by_formula().get(formula, ()))
    if not formula_hits:
        return CeaResolution(None, "cea_formula_unmapped")
    phase_hits = [e for e in formula_hits if _cea_phase_ok(e.phase, phase_kind)]
    if phase_kind == PHASE_REF:
        if len(formula_hits) == 1:
            phase_hits = list(formula_hits)
        elif not phase_hits:
            return CeaResolution(None, "cea_key_ambiguous")
    if not phase_hits:
        return CeaResolution(None, "cea_formula_unmapped")
    covering = [e for e in phase_hits if e.T_min_K <= T <= e.T_max_K]
    if len(covering) == 1:
        return CeaResolution(covering[0].cea_key, None)
    if len(covering) > 1:
        return CeaResolution(None, "cea_key_ambiguous")
    band = (
        min(e.T_min_K for e in phase_hits),
        max(e.T_max_K for e in phase_hits),
    )
    return CeaResolution(None, "engine_channel_out_of_range", band)


def _elemental_cea_entry(element: str, T_K: float) -> CeaResolution:
    std_formula = _DIATOMIC_STANDARD_FORMULA.get(element, element)
    hits = list(cea_by_formula().get(std_formula, ()))
    if element in _DIATOMIC_STANDARD_FORMULA:
        hits = [e for e in hits if e.phase == "gas"]
    else:
        hits = [e for e in hits if e.phase != "gas"]
    if not hits:
        return CeaResolution(None, "cea_elemental_unmapped")
    covering = [e for e in hits if e.T_min_K <= T_K <= e.T_max_K]
    if len(covering) == 1:
        return CeaResolution(covering[0].cea_key, None)
    if len(covering) > 1:
        return CeaResolution(None, "cea_elemental_ambiguous")
    band = (
        min(e.T_min_K for e in hits),
        max(e.T_max_K for e in hits),
    )
    return CeaResolution(None, "engine_channel_out_of_range", band)


def cea_delta_fG_kJ_mol(cea_key: str, T_K: float) -> float:
    """ΔfG°(T) in kJ/mol from CEA polynomials on the overlapping domain only.

    Premise: NASA-9 H(T) includes ΔfH°(298), so G(T) = H(T) − T S(T) is the
    CEA assigned Gibbs. Formation is the stoichiometric difference against
    elemental standard-state records.
    Algebra: ΔfG(species) = G_sp − Σ (n_el / n_std) G_el_std, with n_std = 2
    for H,N,O,F,Cl (diatomic gas) and 1 otherwise.
    Unit check: G is J/mol; divide by 1000 for kJ/mol.
    Sanity: O2 is the O standard state, so ΔfG(O2) = G_O2 − G_O2 = 0 at every
    in-domain T (matches ``engine_delta_fG_kJ_mol`` on the O2 pilot channel).
    """

    T = float(T_K)
    poly = cea_polynomial(cea_key)
    try:
        g = poly.evaluate(T).g_J_per_mol
    except NasaCeaDomainError as exc:
        raise GibbsDomainRefusal(
            f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:{exc}"
        ) from exc
    formula = poly.formula
    if not formula:
        raise GibbsDomainRefusal(
            f"{TYPED_REFUSAL_PREFIX}cea_formula_unmapped:{cea_key}"
        )
    composition = parse_formula_composition(formula)
    if not composition:
        raise GibbsDomainRefusal(
            f"{TYPED_REFUSAL_PREFIX}cea_formula_unmapped:{formula}"
        )
    for element, count in composition:
        resolved = _elemental_cea_entry(element, T)
        if resolved.cea_key is None:
            reason = resolved.reason or "cea_elemental_unmapped"
            band = resolved.band_K
            extra = f":{band[0]:g}-{band[1]:g}K" if band else ""
            raise GibbsDomainRefusal(
                f"{TYPED_REFUSAL_PREFIX}{reason}:{element}{extra}"
            )
        n_std = 2.0 if element in _DIATOMIC_STANDARD_FORMULA else 1.0
        el_poly = cea_polynomial(resolved.cea_key)
        try:
            g -= (count / n_std) * el_poly.evaluate(T).g_J_per_mol
        except NasaCeaDomainError as exc:
            raise GibbsDomainRefusal(
                f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:{exc}"
            ) from exc
    return g / 1000.0


def _pilot_channel_for_cea_key(cea_key: str) -> PilotChannel | None:
    try:
        channel = resolve_pilot_channel(cea_key)
    except UnmappedPilotSpeciesError:
        return None
    if channel.cea_key != cea_key:
        return None
    return channel


def engine_cea_delta_fG_kJ_mol(cea_key: str, T_K: float) -> float:
    """Prefer ``engine_delta_fG_kJ_mol`` on the six-species pilot map."""

    channel = _pilot_channel_for_cea_key(cea_key)
    if channel is not None:
        return engine_delta_fG_kJ_mol(channel, T_K)
    return cea_delta_fG_kJ_mol(cea_key, T_K)


# ---------------------------------------------------------------------------
# Ellingham channel
# ---------------------------------------------------------------------------


def oxide_dG_per_mol_O2_kJ(delta_fG_kJ_mol: float, oxide: str) -> float | None:
    """Rescale oxide ΔfG onto the Ellingham per-mol-O2 basis.

    Premise: OXIDE_TO_METAL[oxide] = (metal, n_metal, n_O) with n_O the oxygen
    atoms per formula unit, so moles O2 per formula = n_O / 2.
    Algebra: dG per mol O2 = ΔfG(oxide) / (n_O / 2) = 2 ΔfG / n_O.
    Unit check: kJ/mol-oxide × (mol-oxide / mol-O2) = kJ/mol-O2.
    Sanity: Na2O has n_O = 1 → 2 ΔfG(Na2O), matching 4 Na + O2 → 2 Na2O.
    """

    stoich = OXIDE_TO_METAL.get(oxide)
    if stoich is None:
        return None
    _metal, _n_metal, n_oxygen = stoich
    if n_oxygen <= 0:
        return None
    return 2.0 * float(delta_fG_kJ_mol) / float(n_oxygen)


def ellingham_provenance(metal: str, compilation_id: str) -> str:
    segments = ELLINGHAM_FIT_SEGMENTS.get(metal)
    if not segments:
        return PROVENANCE_INDEPENDENT
    anchor = " ".join(segment.janaf_anchor for segment in segments).lower()
    if compilation_id == COMPILATION_JANAF and (
        "janaf" in anchor or "chase" in anchor
    ):
        return PROVENANCE_ENGINE_OWN_INPUT
    return PROVENANCE_INDEPENDENT


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _status_for_residual(residual_kJ_mol: float, provenance_class: str) -> str:
    band = (
        TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL
        if provenance_class == PROVENANCE_ENGINE_OWN_INPUT
        else INDEPENDENT_AGREEMENT_BAND_KJ_MOL
    )
    if abs(residual_kJ_mol) <= band:
        return "match"
    return "mismatch"


def _finding_class(provenance_class: str, status: str) -> str | None:
    if status not in {"match", "mismatch"}:
        return None
    if provenance_class == PROVENANCE_ENGINE_OWN_INPUT:
        return "data_integrity" if status == "mismatch" else "transcription_ok"
    if status == "mismatch":
        return "compilation_disagreement"
    return "compilation_agreement"


def _point_key(
    compilation_id: str,
    record_id: str,
    T_K: float,
    channel: str,
    quantity: str = "delta_fG_kJ_mol",
) -> str:
    t_label = f"{T_K:.2f}".rstrip("0").rstrip(".")
    return f"{compilation_id}::{record_id}:T={t_label}::{channel}::{quantity}"


def _refusal_score(
    *,
    compilation_id: str,
    record_id: str,
    formula: str | None,
    T_K: float | None,
    channel: str,
    reason: str,
    provenance_class: str,
    table_kJ_mol: float | None = None,
    cea_key: str | None = None,
    note: str = "",
    band: tuple[float, float] | None = None,
) -> GibbsPointScore:
    skip = reason if reason.startswith(TYPED_REFUSAL_PREFIX) else (
        f"{TYPED_REFUSAL_PREFIX}{reason}"
    )
    if band is not None and "engine_channel_out_of_range" in skip:
        skip = (
            f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:"
            f"{band[0]:g}-{band[1]:g}K"
        )
    t_for_key = float(T_K) if T_K is not None else 0.0
    return GibbsPointScore(
        key=_point_key(compilation_id, record_id, t_for_key, channel),
        source_id=compilation_id,
        observation_id=record_id,
        species=str(formula or ""),
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
        engine_channel=channel,
        cea_key=cea_key,
        skip_reason=skip,
        note=note,
    )


def score_cea_point(point: KeyedTablePoint) -> GibbsPointScore:
    # Compilation tables are not CEA coefficient segments, so this is always
    # independent_tabulation (a physics/compilation finding, not transcription).
    provenance = classify_provenance(evaluator_family=None, values=None)
    resolved = resolve_cea_species(point.formula, point.phase_kind, point.T_K)
    if resolved.cea_key is None:
        return _refusal_score(
            compilation_id=point.compilation_id,
            record_id=point.record_id,
            formula=point.formula,
            T_K=point.T_K,
            channel=CHANNEL_NASA_CEA,
            reason=resolved.reason or "cea_formula_unmapped",
            provenance_class=provenance,
            table_kJ_mol=point.delta_fG_kJ_mol,
            band=resolved.band_K,
            note=point.note,
        )
    try:
        engine = engine_cea_delta_fG_kJ_mol(resolved.cea_key, point.T_K)
    except GibbsDomainRefusal as exc:
        reason = str(exc)
        if not reason.startswith(TYPED_REFUSAL_PREFIX):
            reason = f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range"
        return _refusal_score(
            compilation_id=point.compilation_id,
            record_id=point.record_id,
            formula=point.formula,
            T_K=point.T_K,
            channel=CHANNEL_NASA_CEA,
            reason=reason,
            provenance_class=provenance,
            table_kJ_mol=point.delta_fG_kJ_mol,
            cea_key=resolved.cea_key,
            note=point.note,
        )
    residual = engine - float(point.delta_fG_kJ_mol)
    status = _status_for_residual(residual, provenance)
    return GibbsPointScore(
        key=_point_key(
            point.compilation_id, point.record_id, point.T_K, CHANNEL_NASA_CEA
        ),
        source_id=point.compilation_id,
        observation_id=point.record_id,
        species=point.formula,
        provenance_class=provenance,
        comparison_quantity="delta_fG_kJ_mol",
        temperature_K=point.T_K,
        table_kJ_mol=float(point.delta_fG_kJ_mol),
        engine_kJ_mol=engine,
        residual_kJ_mol=residual,
        residual_log10K=residual_log10K_from_kJ(residual, point.T_K),
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=_finding_class(provenance, status),
        engine_channel=CHANNEL_NASA_CEA,
        cea_key=resolved.cea_key,
        skip_reason=None,
        note=point.note,
    )


def score_ellingham_point(point: KeyedTablePoint) -> GibbsPointScore | None:
    # Ellingham is n_M M + O2 → n_ox oxide(condensed). Gas-phase oxide tables
    # (MgO(g), FeO(g), …) are a different reaction and must not be rescaled
    # onto the condensed line (that comparison is ~900 kJ of phase mismatch,
    # not a formation residual).
    if point.phase_kind not in {PHASE_SOLID, PHASE_LIQUID}:
        return None
    if point.formula not in OXIDE_TO_METAL:
        return None
    metal, _n_metal, n_oxygen = OXIDE_TO_METAL[point.formula]
    if metal not in ELLINGHAM_FIT_SEGMENTS:
        return _refusal_score(
            compilation_id=point.compilation_id,
            record_id=point.record_id,
            formula=point.formula,
            T_K=point.T_K,
            channel=CHANNEL_ELLINGHAM,
            reason="ellingham_species_unsupported",
            provenance_class=PROVENANCE_INDEPENDENT,
            table_kJ_mol=point.delta_fG_kJ_mol,
            note=point.note,
        )
    # Certified band is per-species ellingham_fit_range_K, not the legacy
    # ELLINGHAM_FIT_RANGE_K = (1100, 1700) constant. Na/Mg/Fe/Ca primary-refit
    # segments run to 2600 K; a 2000-2600 K point is in-range for those metals
    # and stays a residual, not a refusal. Same skip token as the CEA channel:
    # engine_channel_out_of_range:<low>-<high>K.
    low, high = ellingham_fit_range_K(metal)
    if not (low <= point.T_K <= high):
        return _refusal_score(
            compilation_id=point.compilation_id,
            record_id=point.record_id,
            formula=point.formula,
            T_K=point.T_K,
            channel=CHANNEL_ELLINGHAM,
            reason="engine_channel_out_of_range",
            provenance_class=ellingham_provenance(metal, point.compilation_id),
            table_kJ_mol=oxide_dG_per_mol_O2_kJ(point.delta_fG_kJ_mol, point.formula),
            band=(low, high),
            note=point.note,
        )
    table_per_o2 = oxide_dG_per_mol_O2_kJ(point.delta_fG_kJ_mol, point.formula)
    if table_per_o2 is None:
        return None
    engine = ellingham_delta_g_kj_per_mol_o2(metal, point.T_K)
    residual = engine - table_per_o2
    provenance = ellingham_provenance(metal, point.compilation_id)
    status = _status_for_residual(residual, provenance)
    return GibbsPointScore(
        key=_point_key(
            point.compilation_id, point.record_id, point.T_K, CHANNEL_ELLINGHAM,
            "delta_fG_kJ_per_mol_O2",
        ),
        source_id=point.compilation_id,
        observation_id=point.record_id,
        species=point.formula,
        provenance_class=provenance,
        comparison_quantity="delta_fG_kJ_per_mol_O2",
        temperature_K=point.T_K,
        table_kJ_mol=table_per_o2,
        engine_kJ_mol=engine,
        residual_kJ_mol=residual,
        residual_log10K=residual_log10K_from_kJ(residual, point.T_K),
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=_finding_class(provenance, status),
        engine_channel=CHANNEL_ELLINGHAM,
        cea_key=None,
        skip_reason=None,
        note=(
            f"{point.note}; rescaled 2*ΔfG/n_O with n_O={n_oxygen} "
            f"via OXIDE_TO_METAL[{point.formula!r}] → {metal}"
        ),
    )


def score_channel_vs_channel(
    point: KeyedTablePoint,
    cea_score: GibbsPointScore,
    ell_score: GibbsPointScore,
) -> GibbsPointScore | None:
    if cea_score.status not in {"match", "mismatch"}:
        return None
    if ell_score.status not in {"match", "mismatch"}:
        return None
    if cea_score.engine_kJ_mol is None or ell_score.engine_kJ_mol is None:
        return None
    cea_per_o2 = oxide_dG_per_mol_O2_kJ(cea_score.engine_kJ_mol, point.formula)
    if cea_per_o2 is None:
        return None
    residual = cea_per_o2 - ell_score.engine_kJ_mol
    provenance = PROVENANCE_INDEPENDENT
    status = _status_for_residual(residual, provenance)
    finding = (
        "channel_disagreement" if status == "mismatch" else "channel_agreement"
    )
    return GibbsPointScore(
        key=_point_key(
            point.compilation_id,
            point.record_id,
            point.T_K,
            CHANNEL_CEA_VS_ELLINGHAM,
            "delta_fG_kJ_per_mol_O2",
        ),
        source_id=point.compilation_id,
        observation_id=point.record_id,
        species=point.formula,
        provenance_class=provenance,
        comparison_quantity="delta_fG_kJ_per_mol_O2",
        temperature_K=point.T_K,
        table_kJ_mol=cea_per_o2,
        engine_kJ_mol=ell_score.engine_kJ_mol,
        residual_kJ_mol=residual,
        residual_log10K=residual_log10K_from_kJ(residual, point.T_K),
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=finding,
        engine_channel=CHANNEL_CEA_VS_ELLINGHAM,
        cea_key=cea_score.cea_key,
        skip_reason=None,
        note=(
            "channel-vs-channel (CEA ΔfG rescaled per mol O2 minus Ellingham). "
            "Descriptive magnitude band only; never an acceptance verdict."
        ),
    )


def score_table_self_check(point: KeyedTablePoint) -> GibbsPointScore | None:
    residual_log10 = table_self_check_residual(point)
    if residual_log10 is None:
        return None
    tol = max(
        printed_logk_tolerance(point.log10_Kf_as_published),
        TABLE_SELF_CHECK_FINDING_DEX,
    )
    status = "match" if abs(residual_log10) <= tol else "mismatch"
    return GibbsPointScore(
        key=_point_key(
            point.compilation_id,
            point.record_id,
            point.T_K,
            "table_self_check",
            "log10_Kf",
        ),
        source_id=point.compilation_id,
        observation_id=point.record_id,
        species=point.formula,
        provenance_class=PROVENANCE_ENGINE_OWN_INPUT,
        comparison_quantity="log10_Kf",
        temperature_K=point.T_K,
        table_kJ_mol=point.delta_fG_kJ_mol,
        engine_kJ_mol=None,
        residual_kJ_mol=None,
        residual_log10K=residual_log10,
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=(
            "compilation_table_self_check"
            if status == "mismatch"
            else "table_self_check_ok"
        ),
        engine_channel="table_self_check",
        cea_key=None,
        skip_reason=None,
        note=(
            f"printed log10 Kf minus −ΔfG/(R T ln 10); tolerance={tol:g} "
            f"(half last printed place)"
        ),
    )


@dataclass
class ScoredRailPoint:
    score: GibbsPointScore
    tier: str
    compilation_id: str
    printed_page: int | None = None
    table_log10_Kf: float | None = None

    def ledger_row(self) -> dict[str, Any]:
        row = self.score.pin_dict()
        row["tier"] = self.tier
        row["compilation_id"] = self.compilation_id
        if "engine_channel" not in row:
            row["engine_channel"] = self.score.engine_channel
        if self.printed_page is not None:
            row["printed_page"] = self.printed_page
        if self.table_log10_Kf is not None:
            row["table_log10_Kf"] = self.table_log10_Kf
        # Never emit scoring_eligible — absent, not false.
        row.pop("scoring_eligible", None)
        return row


def _wrap(
    score: GibbsPointScore,
    point: KeyedTablePoint,
    rail: SpeciesRail,
) -> ScoredRailPoint:
    tier = rail.formula_tier(point.formula) or TIER_MAJOR
    return ScoredRailPoint(
        score=score,
        tier=tier,
        compilation_id=point.compilation_id,
        printed_page=point.printed_page,
        table_log10_Kf=point.log10_Kf,
    )


def _wrap_refusal(
    refusal: KeyRefusal,
    rail: SpeciesRail,
    channel: str,
) -> ScoredRailPoint:
    formula = refusal.formula or ""
    tier = rail.formula_tier(formula) if formula else TIER_TRACE
    score = _refusal_score(
        compilation_id=refusal.compilation_id,
        record_id=refusal.record_id,
        formula=refusal.formula,
        T_K=refusal.T_K,
        channel=channel,
        reason=refusal.reason,
        provenance_class=PROVENANCE_INDEPENDENT,
        note=refusal.note,
    )
    return ScoredRailPoint(
        score=score,
        tier=tier or TIER_TRACE,
        compilation_id=refusal.compilation_id,
        printed_page=refusal.printed_page,
    )


def iter_source_items(
    rail: SpeciesRail,
) -> Iterator[KeyedTablePoint | KeyRefusal]:
    yield from iter_janaf_keyed_points(rail)
    yield from iter_b689_keyed_points(rail)


def score_rail(
    rail: SpeciesRail | None = None,
) -> list[ScoredRailPoint]:
    rail = rail or derive_species_rail()
    out: list[ScoredRailPoint] = []
    unavailable_emitted: set[tuple[str, str]] = set()
    for item in iter_source_items(rail):
        if isinstance(item, KeyRefusal):
            out.append(_wrap_refusal(item, rail, CHANNEL_NASA_CEA))
            continue
        cea = score_cea_point(item)
        out.append(_wrap(cea, item, rail))
        ell = score_ellingham_point(item)
        if ell is not None:
            wrapped_ell = _wrap(ell, item, rail)
            out.append(wrapped_ell)
            vs = score_channel_vs_channel(item, cea, ell)
            if vs is not None:
                out.append(_wrap(vs, item, rail))
        self_check = score_table_self_check(item)
        if self_check is not None and self_check.status == "mismatch":
            out.append(_wrap(self_check, item, rail))
        for channel in UNAVAILABLE_CHANNELS:
            marker = (item.compilation_id, channel)
            if marker in unavailable_emitted:
                continue
            unavailable_emitted.add(marker)
            out.append(
                ScoredRailPoint(
                    score=_refusal_score(
                        compilation_id=item.compilation_id,
                        record_id="_channel",
                        formula=None,
                        T_K=None,
                        channel=channel,
                        reason="engine_channel_unavailable",
                        provenance_class=PROVENANCE_INDEPENDENT,
                        note=(
                            "Laptop slice: channel is not importable. "
                            "The hole is the result; not a silent zero."
                        ),
                    ),
                    tier=TIER_MAJOR,
                    compilation_id=item.compilation_id,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Ledger + report
# ---------------------------------------------------------------------------

LEDGER_HEADER = {
    "schema_version": 1,
    "battery": "species_rail_differential",
    "metric": "residual_kJ_mol",
    "metric_units": "kJ/mol",
    "companion_metric": "residual_log10K",
    "companion_metric_units": "dimensionless",
    "band_kind": "absolute_kJ_mol",
    "comparison_quantity": "delta_fG_kJ_mol",
    "never_widen": True,
    "pin_edit_policy": "mechanism-comment-required",
    "doctrine": (
        "The residual IS the result. A table of Gibbs energies is not an "
        "experiment and cannot validate anything. This file is an "
        "internal-consistency instrument, not a battery scoring ledger. "
        "Never emit scoring_eligible. Never widen band_kJ_mol to hide a "
        "changed mechanism. provenance_class=engine_own_input is a "
        "transcription check; provenance_class=independent_tabulation is a "
        "physics/compilation finding. Do not blur the two. Do not retune a "
        "coefficient to shrink a residual."
    ),
    "agreement_bands_kJ_mol": {
        "engine_own_input": TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL,
        "independent_tabulation": INDEPENDENT_AGREEMENT_BAND_KJ_MOL,
        "pin_regression": PIN_BAND_KJ_MOL,
        "note": (
            "Agreement bands label match vs mismatch against residual=0. "
            "Descriptive magnitude band only; never an acceptance verdict."
        ),
    },
    "standard_pressure": {
        "janaf": "0.1 MPa (printed)",
        "nasa_cea_gas": "100000 Pa (0.1 MPa)",
        "nasa_cea_condensed": "101325 Pa (1 atm)",
        "pankratz-1987-usbm-b689": "1 atm",
    },
}


_LEDGER_COLLAPSE_REASONS = frozenset(
    {
        "cea_formula_unmapped",
        "mixed_phase",
        "prose_phase",
        "ellingham_species_unsupported",
        "engine_channel_unavailable",
        "missing_formula",
        "nonpositive_temperature",
    }
)


def _refusal_reason_key(skip_reason: str | None) -> str:
    reason = skip_reason or "typed-refusal"
    if reason.startswith(TYPED_REFUSAL_PREFIX):
        reason = reason[len(TYPED_REFUSAL_PREFIX):]
    return reason.split(":", 1)[0]


def thin_points_for_ledger(
    points: Sequence[ScoredRailPoint],
) -> list[ScoredRailPoint]:
    """Keep every comparable residual; collapse T-invariant refusals per record.

    ``cea_formula_unmapped`` does not depend on T. Writing it once per record
    still shows the hole; repeating it on every printed temperature inflates
    the ledger without new information. Out-of-range refusals stay per-T
    because the certified band is T-specific.
    """

    kept: list[ScoredRailPoint] = []
    seen: set[tuple[str, str, str, str]] = set()
    for point in points:
        if point.score.status != "typed-refusal":
            kept.append(point)
            continue
        reason = _refusal_reason_key(point.score.skip_reason)
        if reason in _LEDGER_COLLAPSE_REASONS:
            key = (
                point.compilation_id,
                point.score.observation_id,
                str(point.score.engine_channel or ""),
                reason,
            )
            if key in seen:
                continue
            seen.add(key)
        kept.append(point)
    return kept


def ledger_document(points: Sequence[ScoredRailPoint]) -> dict[str, Any]:
    comparable = [
        p for p in points if p.score.status in {"match", "mismatch"}
        and p.score.engine_channel in {CHANNEL_NASA_CEA, CHANNEL_ELLINGHAM}
    ]
    refused = [p for p in points if p.score.status == "typed-refusal"]
    return {
        **LEDGER_HEADER,
        "summary": {
            "points_total": len(points),
            "points_scored": len(comparable),
            "points_refused": len(refused),
            "matches": sum(1 for p in comparable if p.score.status == "match"),
            "mismatches": sum(1 for p in comparable if p.score.status == "mismatch"),
        },
        "points": [p.ledger_row() for p in points],
    }


def write_ledger(
    path: Path | None = None,
    points: Sequence[ScoredRailPoint] | None = None,
) -> Path:
    dest = path or LEDGER_PATH
    if dest.resolve() == GIBBS_PILOT_LEDGER_PATH.resolve():
        raise ValueError("refusing to overwrite the gibbs-battery pilot ledger")
    dest.parent.mkdir(parents=True, exist_ok=True)
    scored = points if points is not None else score_rail()
    payload = ledger_document(thin_points_for_ledger(scored))
    payload["summary"]["points_before_ledger_thinning"] = len(scored)
    comment = (
        "# Species-rail differential residual matrix.\n"
        "# Internal-consistency instrument, not a battery scoring ledger.\n"
        "# Doctrine: the residual IS the result. never_widen: true.\n"
        "# No scoring_eligible field (absent, not false).\n"
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


def _count_matrix(points: Sequence[ScoredRailPoint]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for point in points:
        channel = str(point.score.engine_channel or "")
        if channel == "table_self_check":
            continue
        counts[
            (
                point.compilation_id,
                point.tier,
                channel,
                point.score.status,
            )
        ] += 1
    rows = []
    for (compilation, tier, channel, status), n in sorted(counts.items()):
        rows.append(
            {
                "compilation": compilation,
                "tier": tier,
                "channel": channel,
                "status": status,
                "n": n,
            }
        )
    return rows


def _top_major_residuals(
    points: Sequence[ScoredRailPoint],
    *,
    n: int = 20,
    band: TemperatureBand | None = None,
) -> list[dict[str, Any]]:
    candidates = [
        p
        for p in points
        if p.tier == TIER_MAJOR
        and p.score.status in {"match", "mismatch"}
        and p.score.residual_kJ_mol is not None
        and p.score.engine_channel in {CHANNEL_NASA_CEA, CHANNEL_ELLINGHAM}
    ]
    if band is not None:
        candidates = [
            p
            for p in candidates
            if temperature_band_for(p.score.temperature_K) is band
        ]
    candidates.sort(key=lambda p: abs(float(p.score.residual_kJ_mol or 0.0)), reverse=True)
    rows = []
    for point in candidates[:n]:
        residual = float(point.score.residual_kJ_mol or 0.0)
        log10 = point.score.residual_log10K
        assigned = temperature_band_for(point.score.temperature_K)
        rows.append(
            {
                "key": point.score.key,
                "compilation_id": point.compilation_id,
                "record_id": point.score.observation_id,
                "species": point.score.species,
                "tier": point.tier,
                "engine_channel": point.score.engine_channel,
                "temperature_K": point.score.temperature_K,
                "residual_kJ_mol": residual,
                "residual_log10K": log10,
                "finding_class": point.score.finding_class,
                "provenance_class": point.score.provenance_class,
                "printed_page": point.printed_page,
                "band": None if assigned is None else assigned.label,
                "divergence_label": divergence_label(
                    abs(float(log10)) if log10 is not None else None
                ),
            }
        )
    return rows


def _top20_major(points: Sequence[ScoredRailPoint]) -> list[dict[str, Any]]:
    return _top_major_residuals(points, n=20)


def _top20_major_by_band(
    points: Sequence[ScoredRailPoint],
) -> list[dict[str, Any]]:
    """Envelope bands first, then the below/above-envelope bands."""

    ordered = list(ENVELOPE_BANDS) + [
        band for band in TEMPERATURE_BANDS if not band.envelope
    ]
    rows = []
    for band in ordered:
        rows.append(
            {
                "band": band.label,
                "envelope": band.envelope,
                "t_min_exclusive_K": band.t_min_exclusive,
                "t_max_inclusive_K": band.t_max_inclusive,
                "rows": _top_major_residuals(points, n=20, band=band),
            }
        )
    return rows


def _refusal_breakdown(points: Sequence[ScoredRailPoint]) -> dict[str, int]:
    reasons: Counter[str] = Counter()
    for point in points:
        if point.score.status != "typed-refusal":
            continue
        reason = point.score.skip_reason or "typed-refusal"
        if reason.startswith(TYPED_REFUSAL_PREFIX):
            reason = reason[len(TYPED_REFUSAL_PREFIX):]
        reason = reason.split(":", 1)[0]
        reasons[reason] += 1
    return dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])))


def _oxide_in_ellingham_species_range(formula: str, T_K: float | None) -> bool:
    if T_K is None:
        return False
    stoich = OXIDE_TO_METAL.get(formula)
    if stoich is None:
        return False
    metal = stoich[0]
    if metal not in ELLINGHAM_FIT_SEGMENTS:
        return False
    low, high = ellingham_fit_range_K(metal)
    return low <= float(T_K) <= high


def _in_legacy_ellingham_fit_window(T_K: float | None) -> bool:
    if T_K is None:
        return False
    low, high = ELLINGHAM_FIT_RANGE_K
    return low <= float(T_K) <= high


def _channel_vs_channel_table(
    points: Sequence[ScoredRailPoint],
    *,
    legacy_fit_window: bool = False,
) -> list[dict[str, Any]]:
    rows = []
    for point in points:
        if point.score.engine_channel != CHANNEL_CEA_VS_ELLINGHAM:
            continue
        if point.score.status not in {"match", "mismatch"}:
            continue
        T = point.score.temperature_K
        if not _oxide_in_ellingham_species_range(point.score.species, T):
            continue
        if legacy_fit_window and not _in_legacy_ellingham_fit_window(T):
            continue
        residual = point.score.residual_kJ_mol
        log10 = point.score.residual_log10K
        rows.append(
            {
                "key": point.score.key,
                "species": point.score.species,
                "compilation_id": point.compilation_id,
                "temperature_K": point.score.temperature_K,
                "cea_kJ_per_mol_O2": point.score.table_kJ_mol,
                "ellingham_kJ_per_mol_O2": point.score.engine_kJ_mol,
                "residual_kJ_per_mol_O2": residual,
                "residual_log10K": log10,
                "finding_class": point.score.finding_class,
                "divergence_label": divergence_label(
                    abs(float(log10)) if log10 is not None else None
                ),
            }
        )
    rows.sort(
        key=lambda row: abs(float(row["residual_kJ_per_mol_O2"] or 0.0)),
        reverse=True,
    )
    return rows


def _self_check_failures(points: Sequence[ScoredRailPoint]) -> list[dict[str, Any]]:
    rows = []
    for point in points:
        if point.score.engine_channel != "table_self_check":
            continue
        if point.score.status != "mismatch":
            continue
        rows.append(
            {
                "key": point.score.key,
                "compilation_id": point.compilation_id,
                "record_id": point.score.observation_id,
                "species": point.score.species,
                "temperature_K": point.score.temperature_K,
                "residual_log10K": point.score.residual_log10K,
                "printed_page": point.printed_page,
                "finding_class": point.score.finding_class,
            }
        )
    rows.sort(key=lambda row: abs(float(row["residual_log10K"] or 0.0)), reverse=True)
    return rows


def build_report(points: Sequence[ScoredRailPoint]) -> dict[str, Any]:
    vs = _channel_vs_channel_table(points)
    vs_legacy = _channel_vs_channel_table(points, legacy_fit_window=True)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authority": "diagnostic",
        "certifies": False,
        "calibrates": False,
        "verdict": None,
        "doctrine": LEDGER_HEADER["doctrine"],
        "never_widen": True,
        "counts": _count_matrix(points),
        "top20_major_residual_by_band": _top20_major_by_band(points),
        "top20_major_residual": _top20_major(points),
        "typed_refusal_breakdown": _refusal_breakdown(points),
        "channel_vs_channel": vs_legacy[:50],
        "channel_vs_channel_n": len(vs_legacy),
        "channel_vs_channel_species_fit": vs[:50],
        "channel_vs_channel_species_fit_n": len(vs),
        "table_self_check_failures": _self_check_failures(points),
        "note": (
            "divergence_label is a descriptive magnitude band only; "
            "never an acceptance verdict."
        ),
    }


def render_report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Species-rail differential harness (pilot slice)",
        "",
        f"- generated: `{report['generated_at']}`",
        (
            f"- authority: `{report['authority']}`; "
            f"certifies: `{str(report['certifies']).lower()}`; "
            f"calibrates: `{str(report['calibrates']).lower()}`"
        ),
        "- verdict: none — this report measures residuals and cannot pass or fail the model",
        "- `divergence_label` is a descriptive magnitude band only; never an acceptance verdict",
        "",
        "## Counts by compilation × tier × channel × status",
        "",
        "| compilation | tier | channel | status | n |",
        "|---|---|---|---|---:|",
    ]
    for row in report["counts"]:
        lines.append(
            f"| {row['compilation']} | {row['tier']} | {row['channel']} "
            f"| {row['status']} | {row['n']} |"
        )
    lines.extend(
        [
            "",
            "## Largest |residual| among MAJOR-tier points, by temperature band",
            "",
            (
                "Envelope bands (1100-1700, 1700-2300, 2300-2600 K) first. "
                "Cuts: 1100/1700 K = `ELLINGHAM_FIT_RANGE_K`; 2300 K = vapour-rail "
                "`OPERATING_HIGH_K` (above the CLAUDE.md SiO/Fe millibar bake-off "
                "wall table); 2600 K = JANAF/Ellingham primary-refit grid ceiling."
            ),
            "",
        ]
    )
    for block in report["top20_major_residual_by_band"]:
        kind = "envelope" if block["envelope"] else "outside envelope"
        lines.append(f"### {block['band']} K ({kind})")
        lines.append("")
        band_rows = block["rows"]
        if not band_rows:
            lines.append("None.")
            lines.append("")
            continue
        lines.extend(
            [
                (
                    "| species | T_K | channel | residual kJ/mol | finding_class "
                    "| provenance | label |"
                ),
                "|---|---:|---|---:|---|---|---|",
            ]
        )
        for row in band_rows:
            lines.append(
                "| {species} | {T} | {ch} | {res:.4g} | `{finding}` | `{prov}` | `{label}` |".format(
                    species=row["species"],
                    T=row["temperature_K"],
                    ch=row["engine_channel"],
                    res=float(row["residual_kJ_mol"]),
                    finding=row["finding_class"],
                    prov=row["provenance_class"],
                    label=row["divergence_label"],
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Twenty largest |residual| among MAJOR-tier points (full T range)",
            "",
            (
                "| species | T_K | channel | residual kJ/mol | finding_class "
                "| provenance | label |"
            ),
            "|---|---:|---|---:|---|---|---|",
        ]
    )
    for row in report["top20_major_residual"]:
        lines.append(
            "| {species} | {T} | {ch} | {res:.4g} | `{finding}` | `{prov}` | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                ch=row["engine_channel"],
                res=float(row["residual_kJ_mol"]),
                finding=row["finding_class"],
                prov=row["provenance_class"],
                label=row["divergence_label"],
            )
        )
    lines.extend(
        [
            "",
            "## Typed-refusal breakdown",
            "",
            "| reason | n |",
            "|---|---:|",
        ]
    )
    for reason, n in report["typed_refusal_breakdown"].items():
        lines.append(f"| `{reason}` | {n} |")
    lines.extend(
        [
            "",
            "## Channel-vs-channel (CEA vs Ellingham, oxides)",
            "",
            (
                "Headline table is inside `ELLINGHAM_FIT_RANGE_K` = (1100, 1700) K. "
                "Per-species `ellingham_fit_range_K` for Na/Mg/Fe/Ca extends to 2600 K; "
                "those in-range points stay scored (not refusals) and are listed below."
            ),
            "",
            "### Inside the (1100, 1700) K Ellingham fit window",
            "",
            "| species | T_K | CEA kJ/mol O2 | Ellingham kJ/mol O2 | residual | label |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    vs = report["channel_vs_channel"]
    if not vs:
        lines.append("| — | — | — | — | — | no_matched_points |")
    for row in vs[:20]:
        lines.append(
            "| {species} | {T} | {cea:.4g} | {ell:.4g} | {res:.4g} | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                cea=float(row["cea_kJ_per_mol_O2"]),
                ell=float(row["ellingham_kJ_per_mol_O2"]),
                res=float(row["residual_kJ_per_mol_O2"]),
                label=row["divergence_label"],
            )
        )
    lines.extend(
        [
            "",
            "### Inside per-species `ellingham_fit_range_K` (includes 2000-2600 K primary-refit)",
            "",
            "| species | T_K | CEA kJ/mol O2 | Ellingham kJ/mol O2 | residual | label |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    vs_all = report["channel_vs_channel_species_fit"]
    if not vs_all:
        lines.append("| — | — | — | — | — | no_matched_points |")
    for row in vs_all[:20]:
        lines.append(
            "| {species} | {T} | {cea:.4g} | {ell:.4g} | {res:.4g} | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                cea=float(row["cea_kJ_per_mol_O2"]),
                ell=float(row["ellingham_kJ_per_mol_O2"]),
                res=float(row["residual_kJ_per_mol_O2"]),
                label=row["divergence_label"],
            )
        )
    failures = report["table_self_check_failures"]
    lines.extend(["", "## Table self-check failures", ""])
    if not failures:
        lines.append("None. Printed log10 Kf agrees with −ΔfG/(R T ln 10) to printed precision.")
    else:
        lines.extend(
            [
                "| record | species | T_K | residual log10K | page |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in failures[:50]:
            lines.append(
                f"| {row['record_id']} | {row['species']} | {row['temperature_K']} "
                f"| {float(row['residual_log10K']):.4g} | {row['printed_page'] or '—'} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_reports(
    report: Mapping[str, Any],
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    dest = output_dir or REPORT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    json_path = dest / "report.json"
    md_path = dest / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(render_report_markdown(report))
    return json_path, md_path


def run_harness(
    *,
    ledger_path: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    points = score_rail()
    write_ledger(ledger_path, points)
    report = build_report(points)
    write_reports(report, report_dir)
    return report
