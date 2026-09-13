"""Species-rail differential harness (internal-consistency instrument).

The same per-species quantity from each laptop-runnable engine channel and
each keyed compilation table, reported as a residual matrix. The residual IS
the result. No pass/fail gate, no coefficient retune, no ``scoring_eligible``
field.

Pilot sources: JANAF-4th (J-era, kJ/mol, p° = 0.1 MPa) and Pankratz 1987
USBM B689 (cal-era, kcal/mol, p° = 1 atm). Laptop channels: NASA-CEA
polynomials, Ellingham dG per mol O2, and vapour-rail pure-component P_sat.
VapoRock / thermoengine / MELTS are typed-refusal holes on the laptop.
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
    ELLINGHAM_METAL_PHASE_GAS,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_range_K,
    ellingham_metal_phase_kind,
    ellingham_segment_for_temperature,
    ellingham_stoichiometry,
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
from simulator.vapour_rail.catalog import (
    CatalogCompileError,
    compile_vapour_rail_catalog,
)
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
CHANNEL_VAPOUR_RAIL_PSAT = "vapour_rail_psat"
QUANTITY_LOG10_PSAT = "log10_Psat_over_P0"
UNAVAILABLE_CHANNELS = ("vaporock", "thermoengine", "melts")
VAPOR_PRESSURES_PATH = REPO_ROOT / "data" / "vapor_pressures.yaml"

# JANAF printed p° = 0.1 MPa = 1 bar = 1e5 Pa. B689 p° = 1 atm.
# Na NBP 1156 K and Mg NBP 1363 K are the brief's 1-bar sanity rows
# (NIST Mg T_b = 1363.15 K). P_sat(T_b) = 1 bar by definition.
JANAF_STANDARD_PRESSURE_PA = 1.0e5
B689_STANDARD_PRESSURE_PA = 101325.0
NA_NBP_K = 1156.0
MG_NBP_K = 1363.0

# G12: only the alias the gap analysis named (O2 vs the rail id).
# The catalog species_id is already "O2"; this is identity, not invention.
# A missing row is rail_row_absent, not a guessed synonym.
RAIL_SPECIES_ALIASES = {
    "O2": "O2",
}

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

    G9 (2026-09-12): the CEA extract has no condensed MnO or CoO record
    under any key spelling (MnO, CoO, MnO(a), CoO(cr), MNO, COO, Mn1O1,
    Co1O1). Elemental Mn_* / Co_* exist; CO is carbon monoxide and must
    never case-fold onto Co. Those oxides stay ``cea_formula_unmapped``;
    no polynomial is invented from another source.
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


def resolve_ellingham_oxide(formula: str) -> tuple[str, float, float] | None:
    """Map an oxide formula onto the Ellingham metal key.

    Premise: Ellingham segments are keyed by metal (n_M M + O2 → n_ox oxide).
    Compilation rows are keyed by oxide formula (MgO, Na2O, …). Passing the
    oxide string to ``ellingham_fit_range_K`` KeyErrors (G10). The project's
    own dissociation map ``OXIDE_TO_METAL[oxide] = (metal, n_metal, n_O)`` is
    the adapter: n_O oxygen atoms per formula unit, so moles O2 per formula
    = n_O / 2 and dG per mol O2 = 2 ΔfG / n_O (see oxide_dG_per_mol_O2_kJ).
    Algebra: exact-key lookup, never case-folded (CoO is cobalt oxide; CO is
    carbon monoxide).
    Unit check: n_O is oxygen atoms / formula; 2/n_O has units
    mol-oxide / mol-O2.
    Sanity: MgO → (Mg, 1, 1) and the Mg line matches JANAF MgO(cr) inside
    the fit window; CoO → (Co, 1, 1) but Co is absent from
    ELLINGHAM_FIT_SEGMENTS (typed refusal ellingham_species_unsupported);
    an oxide with no map entry has no metal key.
    """

    stoich = OXIDE_TO_METAL.get(formula)
    if stoich is None:
        return None
    metal, n_metal, n_oxygen = stoich
    return str(metal), float(n_metal), float(n_oxygen)


def _binary_oxide_without_metal_key(formula: str) -> bool:
    """True for a two-element oxide (M + O) that OXIDE_TO_METAL does not map."""

    if formula in OXIDE_TO_METAL:
        return False
    composition = parse_formula_composition(formula)
    if not composition:
        return False
    elements = {str(el): float(n) for el, n in composition}
    oxygen = elements.get("O", 0.0)
    if oxygen <= 0.0:
        return False
    others = [el for el, n in elements.items() if el != "O" and n > 0.0]
    return len(others) == 1


def oxide_dG_per_mol_O2_kJ(delta_fG_kJ_mol: float, oxide: str) -> float | None:
    """Rescale oxide ΔfG onto the Ellingham per-mol-O2 basis.

    Premise: OXIDE_TO_METAL[oxide] = (metal, n_metal, n_O) with n_O the oxygen
    atoms per formula unit, so moles O2 per formula = n_O / 2.
    Algebra: dG per mol O2 = ΔfG(oxide) / (n_O / 2) = 2 ΔfG / n_O.
    Unit check: kJ/mol-oxide × (mol-oxide / mol-O2) = kJ/mol-O2.
    Sanity: Na2O has n_O = 1 → 2 ΔfG(Na2O), matching 4 Na + O2 → 2 Na2O.
    """

    stoich = resolve_ellingham_oxide(oxide)
    if stoich is None:
        return None
    _metal, _n_metal, n_oxygen = stoich
    if n_oxygen <= 0:
        return None
    return 2.0 * float(delta_fG_kJ_mol) / float(n_oxygen)


def cea_elemental_vaporization_dG_kJ_mol(element: str, T_K: float) -> float | None:
    """G_CEA(M(g)) − G_CEA(M(condensed)) in kJ/mol at T.

    Returns None when either phase is missing or off its CEA T cover.
    """

    T = float(T_K)
    hits = list(cea_by_formula().get(element, ()))
    gas = [e for e in hits if e.phase == "gas" and e.T_min_K <= T <= e.T_max_K]
    condensed = [
        e for e in hits if e.phase != "gas" and e.T_min_K <= T <= e.T_max_K
    ]
    if len(gas) != 1 or len(condensed) != 1:
        return None
    try:
        g_gas = cea_polynomial(gas[0].cea_key).evaluate(T).g_J_per_mol
        g_cond = cea_polynomial(condensed[0].cea_key).evaluate(T).g_J_per_mol
    except NasaCeaDomainError:
        return None
    return (g_gas - g_cond) / 1000.0


def elemental_reference_shift_kJ_per_mol_O2(
    oxide: str, T_K: float
) -> float | None:
    """CEA condensed-metal vs gas-metal, scaled onto the Ellingham O2 basis.

    Premise: JANAF ΔfG of M_xO_y above the metal boiling point is vs M(g).
    Ellingham segments above T_b are n_M M(g) + O2 → n_ox oxide. CEA
    formation via ``_elemental_cea_entry`` selects the non-gas (condensed)
    record while it still covers T (Na_L to 2300 K, K_L to 2200 K, Mg_L
    to 6000 K, Ca_L to 6000 K), so CEA ΔfG is vs M(l/s).

    Algebra, Na2O (OXIDE_TO_METAL['Na2O'] = (Na, 2, 1)):
      per mol Na2O:  2 Na + 1/2 O2 → Na2O
      per mol O2:    4 Na + O2 → 2 Na2O
      ΔG_vap(Na) = G(Na(g)) − G(Na(l))          [kJ/mol Na]
      n_M per mol O2 = 2 n_metal / n_O = 4
      shift per mol O2 = 4 ΔG_vap(Na)

    Unit check: (mol metal / mol O2) × (kJ / mol metal) = kJ / mol O2.

    Sanity at 1600 K: CEA ΔG_vap(Na) = −35.025 kJ/mol;
    4 ΔG_vap = −140.10 kJ/mol O2. The CEA-vs-Ellingham residual at
    1600 K is −120.7 kJ/mol O2, so the reference-state shift accounts
    for the bulk of the gap (leftover residual − shift = +19.4 kJ/mol O2).
    At T_b, ΔG_vap ≈ 0 and the shift vanishes. Same construction for
    K2O (4 K per mol O2; T_b ≈ 1032 K, all envelope points are gas-basis
    on Ellingham) and MgO / CaO (2 M per mol O2; Mg T_b = 1363.15 K,
    Ca T_b = 1757 K).
    """

    stoich = OXIDE_TO_METAL.get(oxide)
    if stoich is None:
        return None
    metal, n_metal, n_oxygen = stoich
    if n_oxygen <= 0:
        return None
    dG_vap = cea_elemental_vaporization_dG_kJ_mol(metal, T_K)
    if dG_vap is None:
        return None
    n_metal_per_mol_O2 = 2.0 * float(n_metal) / float(n_oxygen)
    return n_metal_per_mol_O2 * dG_vap


def _cea_elemental_is_condensed(element: str, T_K: float) -> bool:
    resolved = _elemental_cea_entry(element, T_K)
    if resolved.cea_key is None:
        return False
    for entry in cea_by_formula().get(element, ()):
        if entry.cea_key == resolved.cea_key:
            return entry.phase != "gas"
    return False


def elemental_reference_mismatch_applies(oxide: str, T_K: float) -> bool:
    """True when Ellingham is M(g)-basis and CEA formation is M(condensed)."""

    stoich = OXIDE_TO_METAL.get(oxide)
    if stoich is None:
        return False
    metal = stoich[0]
    if metal not in ELLINGHAM_FIT_SEGMENTS:
        return False
    try:
        phase = ellingham_metal_phase_kind(metal, T_K)
    except (KeyError, ValueError):
        return False
    if phase != ELLINGHAM_METAL_PHASE_GAS:
        return False
    return _cea_elemental_is_condensed(metal, T_K)


def ellingham_oxide_stoichiometry_for_formula(
    oxide: str,
) -> tuple[float, float] | None:
    """n_M, n_ox for n_M M + O2 → n_ox oxide from the table formula.

    Premise: OXIDE_TO_METAL[oxide] = (metal, n_metal, n_O) with n_O the
    oxygen atoms per formula unit, so one formula is
    n_metal M + (n_O/2) O2 → 1 oxide.
    Algebra: per mol O2, n_M = 2 n_metal / n_O and n_ox = 2 / n_O.
    Unit check: n_M and n_ox are mole ratios per mol O2 (dimensionless).
    Sanity: Fe2O3 → (4/3, 2/3); FeO → (2, 2); Na2O → (4, 2); Al2O3 → (4/3, 2/3).
    """

    stoich = OXIDE_TO_METAL.get(oxide)
    if stoich is None:
        return None
    _metal, n_metal, n_oxygen = stoich
    if n_oxygen <= 0:
        return None
    return (
        2.0 * float(n_metal) / float(n_oxygen),
        2.0 / float(n_oxygen),
    )


def oxide_identity_mismatch_applies(oxide: str) -> bool:
    """True when the Ellingham metal key is fitted to a different oxide.

    Premise: the harness maps OXIDE_TO_METAL[oxide][0] onto
    ELLINGHAM_FIT_SEGMENTS[metal]. That metal key is one oxide's line
    (Fe is 2 Fe + O2 → 2 FeO, n_M=2, n_ox=2), not every oxide of that
    metal (Fe2O3 would be 4/3 Fe + O2 → 2/3 Fe2O3). Mapping Fe2O3 onto
    the Fe line is the wrong reaction (b-489); the adapter refuses it.
    Algebra: mismatch iff (n_M, n_ox)_Ellingham ≠ (2 n_metal/n_O, 2/n_O).
    Unit check: both pairs are mole ratios per mol O2.
    Sanity: Fe2O3 vs Fe is a mismatch; FeO, Al2O3, Cr2O3, Na2O match.
    """

    stoich = OXIDE_TO_METAL.get(oxide)
    if stoich is None:
        return False
    metal = stoich[0]
    if metal not in ELLINGHAM_FIT_SEGMENTS:
        return False
    expected = ellingham_oxide_stoichiometry_for_formula(oxide)
    if expected is None:
        return False
    actual = ellingham_stoichiometry(metal)
    return not (
        math.isclose(expected[0], actual[0], rel_tol=0.0, abs_tol=1e-9)
        and math.isclose(expected[1], actual[1], rel_tol=0.0, abs_tol=1e-9)
    )


def ellingham_line_product_oxide(metal: str, T_K: float | None = None) -> str:
    """Oxide product the Ellingham metal line is fitted to.

    Premise: each metal key is one reaction n_M M + O2 → n_ox oxide,
    written in EllinghamFitSegment.phase_basis (Fe is
    ``2 Fe(alpha) + O2 -> 2 FeO(s)``, not hematite).
    Algebra: take the token after ``->``, drop a leading stoichiometric
    coefficient (``2``, ``2/3``, ``4/3``), then drop a parenthetical
    phase suffix.
    Unit check: the remaining token is a formula (FeO, MgO, Al2O3, …).
    Sanity: Fe → FeO; Al → Al2O3; Fe2O3 is a different oxide than the Fe line.
    """

    if metal not in ELLINGHAM_FIT_SEGMENTS:
        return ""
    if T_K is None:
        phase_basis = ELLINGHAM_FIT_SEGMENTS[metal][0].phase_basis
    else:
        phase_basis = ellingham_segment_for_temperature(metal, T_K).phase_basis
    _lhs, sep, rhs = str(phase_basis).partition("->")
    if not sep:
        return ""
    tokens = rhs.strip().split()
    if tokens and all(ch.isdigit() or ch in "./" for ch in tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return ""
    formula = tokens[0]
    if "(" in formula:
        formula = formula.split("(", 1)[0]
    return formula


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
# Vapour-rail P_sat channel
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def vapour_rail_catalog():
    """Compiled schema-v2 vapour rail (evaluators only; no U0 request rules)."""

    payload = yaml.safe_load(VAPOR_PRESSURES_PATH.read_text(encoding="utf-8")) or {}
    return compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def standard_pressure_Pa(compilation_id: str) -> float:
    if compilation_id == COMPILATION_B689:
        return B689_STANDARD_PRESSURE_PA
    return JANAF_STANDARD_PRESSURE_PA


def log10_psat_over_P0_from_dfg(
    delta_fG_gas_kJ_mol: float,
    delta_fG_condensed_kJ_mol: float,
    T_K: float,
) -> float:
    """Compilation-derived log10(P_sat/P0) from gas and condensed ΔfG at T.

    Premise: the vaporisation equilibrium is M(cr or l) ⇌ M(g) with
    a_condensed = 1. Standard-state pressure P0 is 0.1 MPa for JANAF
    (1 bar) and 1 atm for B689.
    Algebra: ΔvapG° = dfG(g) − dfG(cr or l);
    K = P_sat/P0; ln K = −ΔvapG°/(R T);
    ln(P_sat/P0) = −[dfG(g) − dfG(cr or l)] / (R T);
    log10(P_sat/P0) = −ΔvapG° / (R T ln 10)
    = log10K_from_delta_fG_kJ_mol(ΔvapG°, T).
    Unit check: kJ/mol / (kJ/(mol·K) · K) is dimensionless; P_sat and P0
    are both pressures so P_sat/P0 is dimensionless.
    Sanity: Na at 1156 K, P_sat = 1 bar = JANAF P0 → log10(P_sat/P0) = 0
    when ΔvapG° = 0 (the definition of the normal boiling point). Same
    construction for Mg at 1363 K (NIST T_b = 1363.15 K).
    """

    dvap = float(delta_fG_gas_kJ_mol) - float(delta_fG_condensed_kJ_mol)
    return log10K_from_delta_fG_kJ_mol(dvap, T_K)


def resolve_rail_species_id(formula: str, catalog=None) -> str | None:
    """Catalog species_id for a compilation formula. Exact id, then unique formula.

    The only naming alias is O2 (G12). A missing row is not an alias.
    """

    catalog = catalog or vapour_rail_catalog()
    alias = RAIL_SPECIES_ALIASES.get(formula, formula)
    if alias in catalog.species:
        return alias
    if formula in catalog.species:
        return formula
    hits = [
        sid
        for sid, spec in catalog.species.items()
        if str(spec.formula) == formula
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def _evaluate_rail_pressure_Pa(species_id: str, T_K: float, catalog=None) -> float | str:
    """Pure-component limit: a_melt = 1. Refusal reason string on failure."""

    catalog = catalog or vapour_rail_catalog()
    compiled = catalog.species.get(species_id)
    if compiled is None or compiled.evaluator is None:
        return "rail_row_absent"
    ev = compiled.evaluator
    kwargs: dict[str, Any] = {}
    if abs(float(ev.activity_exponent or 0.0)) > 0.0:
        kwargs["source_activity"] = 1.0
    needs_o2 = (
        abs(float(ev.pO2_exponent or 0.0)) > 0.0
        or ev.o2_channel_term is not None
    )
    if needs_o2:
        kwargs["pO2_bar"] = float(ev.pO2_reference_bar or 1.0)
    try:
        result = ev.evaluate(float(T_K), **kwargs)
    except CatalogCompileError:
        return "rail_row_absent"
    P = float(result.pressure_pa)
    if not math.isfinite(P) or P <= 0.0:
        return "rail_row_absent"
    return P


def _pick_condensed_psat(
    points: Sequence[KeyedTablePoint],
) -> KeyedTablePoint | None:
    """Prefer liquid (the boiling reference); else the lowest-ΔfG solid."""

    liquids = [p for p in points if p.phase_kind == PHASE_LIQUID]
    if liquids:
        return min(liquids, key=lambda p: p.delta_fG_kJ_mol)
    solids = [p for p in points if p.phase_kind == PHASE_SOLID]
    if not solids:
        return None
    return min(solids, key=lambda p: p.delta_fG_kJ_mol)


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
    comparison_quantity: str = "delta_fG_kJ_mol",
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
        key=_point_key(
            compilation_id, record_id, t_for_key, channel, comparison_quantity
        ),
        source_id=compilation_id,
        observation_id=record_id,
        species=str(formula or ""),
        provenance_class=provenance_class,
        comparison_quantity=comparison_quantity,
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
    finding = _finding_class(provenance, status)
    note = point.note
    if status == "mismatch" and elemental_reference_mismatch_applies(
        point.formula, point.T_K
    ):
        finding = "elemental_reference_state_mismatch"
        shift = elemental_reference_shift_kJ_per_mol_O2(point.formula, point.T_K)
        n_oxygen = OXIDE_TO_METAL[point.formula][2]
        shift_per_formula = (
            None if shift is None else shift * float(n_oxygen) / 2.0
        )
        shift_s = (
            "None"
            if shift_per_formula is None
            else f"{shift_per_formula:.3f}"
        )
        note = (
            f"{note}; CEA condensed-metal vs table/Ellingham gas-metal; "
            f"ΔG_vap scaled to formula = {shift_s} kJ/mol"
        ).strip("; ")
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
        finding_class=finding,
        engine_channel=CHANNEL_NASA_CEA,
        cea_key=resolved.cea_key,
        skip_reason=None,
        note=note,
    )


def score_ellingham_point(point: KeyedTablePoint) -> GibbsPointScore | None:
    # Ellingham is n_M M + O2 → n_ox oxide(condensed). Gas-phase oxide tables
    # (MgO(g), FeO(g), …) are a different reaction and must not be rescaled
    # onto the condensed line (that comparison is ~900 kJ of phase mismatch,
    # not a formation residual).
    if point.phase_kind not in {PHASE_SOLID, PHASE_LIQUID}:
        return None
    resolved = resolve_ellingham_oxide(point.formula)
    if resolved is None:
        if _binary_oxide_without_metal_key(point.formula):
            return _refusal_score(
                compilation_id=point.compilation_id,
                record_id=point.record_id,
                formula=point.formula,
                T_K=point.T_K,
                channel=CHANNEL_ELLINGHAM,
                reason="ellingham_species_unsupported",
                provenance_class=PROVENANCE_INDEPENDENT,
                table_kJ_mol=point.delta_fG_kJ_mol,
                note=(
                    f"{point.note}; oxide has no OXIDE_TO_METAL metal key"
                ).strip("; "),
            )
        return None
    metal, _n_metal, n_oxygen = resolved
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
            note=(
                f"{point.note}; via OXIDE_TO_METAL[{point.formula!r}] → {metal} "
                "(no Ellingham segment)"
            ).strip("; "),
        )
    # Each metal key is one oxide reaction. Fe is 2 Fe + O2 → 2 FeO, not
    # hematite (b-489). Map only when the line's own oxide IS this formula.
    if oxide_identity_mismatch_applies(point.formula):
        line_oxide = ellingham_line_product_oxide(metal, point.T_K)
        n_M, n_ox = ellingham_stoichiometry(metal)
        phase_basis = ellingham_segment_for_temperature(
            metal, point.T_K
        ).phase_basis
        return _refusal_score(
            compilation_id=point.compilation_id,
            record_id=point.record_id,
            formula=point.formula,
            T_K=point.T_K,
            channel=CHANNEL_ELLINGHAM,
            reason="ellingham_line_is_different_oxide",
            provenance_class=PROVENANCE_INDEPENDENT,
            table_kJ_mol=point.delta_fG_kJ_mol,
            note=(
                f"{point.note}; Ellingham {metal} line is {line_oxide} "
                f"(n_M={n_M:g} n_ox={n_ox:g}: {phase_basis}); "
                f"not {point.formula}"
            ).strip("; "),
        )
    # Certified band is per-species ellingham_fit_range_K, not the legacy
    # ELLINGHAM_FIT_RANGE_K = (1100, 1700) constant. Na/Mg/Fe/Ca primary-refit
    # segments run to 2600 K; a 2000-2600 K point is in-range for those metals
    # and stays a residual, not a refusal. Same skip token as the CEA channel:
    # engine_channel_out_of_range:<low>-<high>K. K and Ni keep their
    # certified-band flags (no invented slope beyond the segment).
    low, high = ellingham_fit_range_K(metal)
    if not (low <= point.T_K <= high):
        band_note = (
            f"certified_band_flag fit_range_K=({low:g},{high:g})"
        )
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
            note=f"{point.note}; {band_note}".strip("; "),
        )
    table_per_o2 = oxide_dG_per_mol_O2_kJ(point.delta_fG_kJ_mol, point.formula)
    if table_per_o2 is None:
        return None
    engine = ellingham_delta_g_kj_per_mol_o2(metal, point.T_K)
    residual = engine - table_per_o2
    provenance = ellingham_provenance(metal, point.compilation_id)
    status = _status_for_residual(residual, provenance)
    finding = _finding_class(provenance, status)
    note = (
        f"{point.note}; rescaled 2*ΔfG/n_O with n_O={n_oxygen} "
        f"via OXIDE_TO_METAL[{point.formula!r}] → {metal}"
    )
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
        finding_class=finding,
        engine_channel=CHANNEL_ELLINGHAM,
        cea_key=None,
        skip_reason=None,
        note=note,
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
    note = (
        "channel-vs-channel (CEA ΔfG rescaled per mol O2 minus Ellingham). "
        "Descriptive magnitude band only; never an acceptance verdict."
    )
    if status == "mismatch" and oxide_identity_mismatch_applies(point.formula):
        finding = "oxide_identity_mismatch"
        metal, _n_metal, _n_oxygen = OXIDE_TO_METAL[point.formula]
        n_M, n_ox = ellingham_stoichiometry(metal)
        expected = ellingham_oxide_stoichiometry_for_formula(point.formula)
        expected_s = (
            "None"
            if expected is None
            else f"n_M={expected[0]:g} n_ox={expected[1]:g}"
        )
        phase_basis = ellingham_segment_for_temperature(
            metal, point.T_K
        ).phase_basis
        note = (
            f"{note} Ellingham {metal} is n_M={n_M:g} n_ox={n_ox:g} "
            f"({phase_basis}); {point.formula} expects {expected_s}."
        )
    elif status == "mismatch" and elemental_reference_mismatch_applies(
        point.formula, point.T_K
    ):
        finding = "elemental_reference_state_mismatch"
        shift = elemental_reference_shift_kJ_per_mol_O2(point.formula, point.T_K)
        leftover = None if shift is None else float(residual) - shift
        shift_s = "None" if shift is None else f"{shift:.3f}"
        leftover_s = "None" if leftover is None else f"{leftover:.3f}"
        note = (
            f"{note} CEA condensed-metal vs Ellingham gas-metal; "
            f"n_M ΔG_vap = {shift_s} kJ/mol O2; leftover residual−shift = "
            f"{leftover_s} kJ/mol O2."
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
        note=note,
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


def score_psat_pair(
    gas: KeyedTablePoint,
    condensed: KeyedTablePoint,
    pressure_Pa: float,
    species_id: str,
) -> GibbsPointScore:
    """Score rail P_sat against compilation-derived log10(P_sat/P0)."""

    T = float(gas.T_K)
    P0 = standard_pressure_Pa(gas.compilation_id)
    table_log10 = log10_psat_over_P0_from_dfg(
        gas.delta_fG_kJ_mol, condensed.delta_fG_kJ_mol, T
    )
    engine_log10 = math.log10(float(pressure_Pa) / P0)
    residual_log10 = engine_log10 - table_log10
    table_dvap = float(gas.delta_fG_kJ_mol) - float(condensed.delta_fG_kJ_mol)
    engine_dvap = -R_KJ_PER_MOL_K * T * LN10 * engine_log10
    residual_kJ = engine_dvap - table_dvap
    provenance = PROVENANCE_INDEPENDENT
    status = _status_for_residual(residual_kJ, provenance)
    return GibbsPointScore(
        key=_point_key(
            gas.compilation_id,
            gas.record_id,
            T,
            CHANNEL_VAPOUR_RAIL_PSAT,
            QUANTITY_LOG10_PSAT,
        ),
        source_id=gas.compilation_id,
        observation_id=gas.record_id,
        species=gas.formula,
        provenance_class=provenance,
        comparison_quantity=QUANTITY_LOG10_PSAT,
        temperature_K=T,
        table_kJ_mol=table_dvap,
        engine_kJ_mol=engine_dvap,
        residual_kJ_mol=residual_kJ,
        residual_log10K=residual_log10,
        band_kJ_mol=PIN_BAND_KJ_MOL,
        status=status,
        finding_class=_finding_class(provenance, status),
        engine_channel=CHANNEL_VAPOUR_RAIL_PSAT,
        cea_key=None,
        skip_reason=None,
        note=(
            f"{gas.note}; ln(P_sat/P0)=-[dfG(g)-dfG({condensed.phase})]/(R T); "
            f"P0={P0:g} Pa; rail_id={species_id}; "
            f"condensed_record={condensed.record_id}; a_melt=1"
        ),
    )


def _psat_refusal(
    point: KeyedTablePoint,
    reason: str,
    *,
    note: str = "",
) -> GibbsPointScore:
    return _refusal_score(
        compilation_id=point.compilation_id,
        record_id=point.record_id,
        formula=point.formula,
        T_K=point.T_K,
        channel=CHANNEL_VAPOUR_RAIL_PSAT,
        reason=reason,
        provenance_class=PROVENANCE_INDEPENDENT,
        table_kJ_mol=None,
        note=note or point.note,
        comparison_quantity=QUANTITY_LOG10_PSAT,
    )


def score_psat_nbp_sanity(
    rail: SpeciesRail,
    catalog=None,
) -> list[ScoredRailPoint]:
    """Na 1156 K and Mg 1363 K vs P_sat = 1 bar (JANAF P0)."""

    catalog = catalog or vapour_rail_catalog()
    out: list[ScoredRailPoint] = []
    for formula, T_K in (("Na", NA_NBP_K), ("Mg", MG_NBP_K)):
        if not rail.in_rail(formula):
            continue
        point = KeyedTablePoint(
            compilation_id=COMPILATION_JANAF,
            record_id=f"{formula}-nbp-sanity",
            formula=formula,
            phase="l",
            phase_kind=PHASE_LIQUID,
            T_K=T_K,
            delta_fG_kJ_mol=0.0,
            log10_Kf=0.0,
            log10_Kf_as_published="0",
            printed_page=None,
            note="NBP sanity: P_sat(T_b)=1 bar = JANAF P0",
        )
        sid = resolve_rail_species_id(formula, catalog)
        if sid is None:
            out.append(_wrap(_psat_refusal(point, "rail_row_absent"), point, rail))
            continue
        pressure = _evaluate_rail_pressure_Pa(sid, T_K, catalog)
        if isinstance(pressure, str):
            out.append(_wrap(_psat_refusal(point, pressure), point, rail))
            continue
        P0 = JANAF_STANDARD_PRESSURE_PA
        engine_log10 = math.log10(float(pressure) / P0)
        residual_log10 = engine_log10  # table log10(1 bar / P0) = 0
        engine_dvap = -R_KJ_PER_MOL_K * T_K * LN10 * engine_log10
        provenance = PROVENANCE_INDEPENDENT
        status = _status_for_residual(engine_dvap, provenance)
        score = GibbsPointScore(
            key=_point_key(
                COMPILATION_JANAF,
                point.record_id,
                T_K,
                CHANNEL_VAPOUR_RAIL_PSAT,
                QUANTITY_LOG10_PSAT,
            ),
            source_id=COMPILATION_JANAF,
            observation_id=point.record_id,
            species=formula,
            provenance_class=provenance,
            comparison_quantity=QUANTITY_LOG10_PSAT,
            temperature_K=T_K,
            table_kJ_mol=0.0,
            engine_kJ_mol=engine_dvap,
            residual_kJ_mol=engine_dvap,
            residual_log10K=residual_log10,
            band_kJ_mol=PIN_BAND_KJ_MOL,
            status=status,
            finding_class=_finding_class(provenance, status),
            engine_channel=CHANNEL_VAPOUR_RAIL_PSAT,
            cea_key=None,
            skip_reason=None,
            note=(
                f"{point.note}; rail_id={sid}; P_engine={float(pressure):.6g} Pa; "
                f"P0={P0:g} Pa; a_melt=1"
            ),
        )
        out.append(_wrap(score, point, rail))
    return out


def score_psat_channel(
    table_points: Sequence[KeyedTablePoint],
    rail: SpeciesRail,
    catalog=None,
) -> list[ScoredRailPoint]:
    """Score vapour-rail P_sat at compilation T with both gas and condensed rows."""

    catalog = catalog or vapour_rail_catalog()
    grouped: dict[
        tuple[str, str, float], dict[str, list[KeyedTablePoint]]
    ] = defaultdict(lambda: {"gas": [], "condensed": []})
    for point in table_points:
        key = (point.compilation_id, point.formula, round(float(point.T_K), 4))
        if point.phase_kind == PHASE_GAS:
            grouped[key]["gas"].append(point)
        elif point.phase_kind in {PHASE_SOLID, PHASE_LIQUID}:
            grouped[key]["condensed"].append(point)
    out: list[ScoredRailPoint] = []
    for (_comp, _formula, _T), buckets in grouped.items():
        gas_rows = buckets["gas"]
        cond_rows = buckets["condensed"]
        wrap_point = gas_rows[0] if gas_rows else cond_rows[0]
        gas = gas_rows[0] if len(gas_rows) == 1 else None
        condensed = _pick_condensed_psat(cond_rows)
        if gas is None:
            out.append(
                _wrap(_psat_refusal(wrap_point, "gas_row_absent"), wrap_point, rail)
            )
            continue
        if condensed is None:
            out.append(
                _wrap(
                    _psat_refusal(wrap_point, "condensed_row_absent"),
                    wrap_point,
                    rail,
                )
            )
            continue
        sid = resolve_rail_species_id(gas.formula, catalog)
        if sid is None:
            out.append(
                _wrap(_psat_refusal(gas, "rail_row_absent"), gas, rail)
            )
            continue
        pressure = _evaluate_rail_pressure_Pa(sid, gas.T_K, catalog)
        if isinstance(pressure, str):
            out.append(_wrap(_psat_refusal(gas, pressure), gas, rail))
            continue
        out.append(_wrap(score_psat_pair(gas, condensed, pressure, sid), gas, rail))
    out.extend(score_psat_nbp_sanity(rail, catalog))
    return out


@dataclass
class ScoredRailPoint:
    score: GibbsPointScore
    tier: str
    compilation_id: str
    printed_page: int | None = None
    table_log10_Kf: float | None = None
    n_points: int | None = None
    T_min_K: float | None = None
    T_max_K: float | None = None
    first_observation_id: str | None = None

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
        if self.n_points is not None:
            row["n_points"] = self.n_points
            row["T_min_K"] = self.T_min_K
            row["T_max_K"] = self.T_max_K
            row["first_observation_id"] = self.first_observation_id
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
    table_points: list[KeyedTablePoint] = []
    unavailable_emitted: set[tuple[str, str]] = set()
    for item in iter_source_items(rail):
        if isinstance(item, KeyRefusal):
            out.append(_wrap_refusal(item, rail, CHANNEL_NASA_CEA))
            continue
        table_points.append(item)
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
    out.extend(score_psat_channel(table_points, rail))
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
        "vapour_rail_psat_janaf": "100000 Pa (0.1 MPa)",
        "vapour_rail_psat_b689": "101325 Pa (1 atm)",
    },
}


def _refusal_reason_key(skip_reason: str | None) -> str:
    reason = skip_reason or "typed-refusal"
    if reason.startswith(TYPED_REFUSAL_PREFIX):
        reason = reason[len(TYPED_REFUSAL_PREFIX):]
    return reason.split(":", 1)[0]


def thin_points_for_ledger(
    points: Sequence[ScoredRailPoint],
) -> list[ScoredRailPoint]:
    """Keep every scored residual; one aggregate refusal per group.

    Group key is (compilation, record_id, engine_channel, reason). The
    aggregate carries n_points, T_min_K, T_max_K, and the first
    observation_id so a reader can still find the record. Scored points
    stay one row each.
    """

    kept: list[ScoredRailPoint] = []
    groups: dict[tuple[str, str, str, str], int] = {}
    for point in points:
        if point.score.status != "typed-refusal":
            kept.append(point)
            continue
        reason = _refusal_reason_key(point.score.skip_reason)
        key = (
            point.compilation_id,
            str(point.score.observation_id or ""),
            str(point.score.engine_channel or ""),
            reason,
        )
        T = point.score.temperature_K
        if key not in groups:
            groups[key] = len(kept)
            kept.append(
                ScoredRailPoint(
                    score=point.score,
                    tier=point.tier,
                    compilation_id=point.compilation_id,
                    printed_page=point.printed_page,
                    table_log10_Kf=point.table_log10_Kf,
                    n_points=1,
                    T_min_K=T,
                    T_max_K=T,
                    first_observation_id=point.score.observation_id,
                )
            )
            continue
        existing = kept[groups[key]]
        existing.n_points = int(existing.n_points or 1) + 1
        if T is not None:
            existing.T_min_K = (
                T if existing.T_min_K is None else min(existing.T_min_K, T)
            )
            existing.T_max_K = (
                T if existing.T_max_K is None else max(existing.T_max_K, T)
            )
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


def _top_psat_residuals(
    points: Sequence[ScoredRailPoint],
    *,
    n: int = 20,
    envelope_only: bool = True,
) -> list[dict[str, Any]]:
    candidates = [
        p
        for p in points
        if p.score.engine_channel == CHANNEL_VAPOUR_RAIL_PSAT
        and p.score.status in {"match", "mismatch"}
        and p.score.residual_log10K is not None
        and not str(p.score.observation_id or "").endswith("-nbp-sanity")
    ]
    if envelope_only:
        candidates = [
            p
            for p in candidates
            if (temperature_band_for(p.score.temperature_K) or TemperatureBand(
                "", None, None, False
            )).envelope
        ]
    candidates.sort(
        key=lambda p: abs(float(p.score.residual_log10K or 0.0)),
        reverse=True,
    )
    rows = []
    for point in candidates[:n]:
        residual = float(point.score.residual_log10K or 0.0)
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
                "residual_log10": residual,
                "residual_kJ_mol": point.score.residual_kJ_mol,
                "finding_class": point.score.finding_class,
                "provenance_class": point.score.provenance_class,
                "band": None if assigned is None else assigned.label,
                "divergence_label": divergence_label(abs(residual)),
                "note": point.score.note,
            }
        )
    return rows


def _psat_sanity_rows(
    points: Sequence[ScoredRailPoint],
) -> list[dict[str, Any]]:
    rows = []
    for point in points:
        if point.score.engine_channel != CHANNEL_VAPOUR_RAIL_PSAT:
            continue
        if not str(point.score.observation_id or "").endswith("-nbp-sanity"):
            continue
        rows.append(
            {
                "species": point.score.species,
                "temperature_K": point.score.temperature_K,
                "status": point.score.status,
                "residual_log10": point.score.residual_log10K,
                "residual_kJ_mol": point.score.residual_kJ_mol,
                "skip_reason": point.score.skip_reason,
                "note": point.score.note,
                "finding_class": point.score.finding_class,
            }
        )
    rows.sort(key=lambda row: str(row["species"]))
    return rows


def _elemental_reference_accounting() -> dict[str, Any]:
    T = 1600.0
    shift = elemental_reference_shift_kJ_per_mol_O2("Na2O", T)
    dG_vap = cea_elemental_vaporization_dG_kJ_mol("Na", T)
    return {
        "oxide": "Na2O",
        "T_K": T,
        "stoichiometry": "4 Na + O2 -> 2 Na2O; shift = 4 ΔG_vap(Na)",
        "dG_vap_Na_kJ_mol": dG_vap,
        "shift_kJ_per_mol_O2": shift,
        "also_checked": ("K2O", "MgO", "CaO"),
        "note": (
            "CEA elemental Na is Na_L (condensed) at 1600 K; Ellingham is "
            "4 Na(g)+O2. The −140.10 kJ/mol O2 shift is the cheap hypothesis "
            "for the −120.7 kJ/mol O2 CEA-vs-Ellingham residual; leftover "
            "+19.4 kJ/mol O2 is not a 1 kJ agreement."
        ),
    }


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
        "elemental_reference_accounting": _elemental_reference_accounting(),
        "psat_top20_in_envelope": _top_psat_residuals(
            points, n=20, envelope_only=True
        ),
        "psat_sanity": _psat_sanity_rows(points),
        "g9_cea_mno_coo": (
            "no condensed MnO or CoO record in nasa-cea-thermo.yaml "
            "under any key spelling; typed refusal cea_formula_unmapped; "
            "CO is carbon monoxide and is not case-folded onto Co"
        ),
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
            "| species | T_K | CEA kJ/mol O2 | Ellingham kJ/mol O2 | residual | finding_class | label |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    vs = report["channel_vs_channel"]
    if not vs:
        lines.append("| — | — | — | — | — | no_matched_points | — |")
    for row in vs[:20]:
        lines.append(
            "| {species} | {T} | {cea:.4g} | {ell:.4g} | {res:.4g} | `{finding}` | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                cea=float(row["cea_kJ_per_mol_O2"]),
                ell=float(row["ellingham_kJ_per_mol_O2"]),
                res=float(row["residual_kJ_per_mol_O2"]),
                finding=row["finding_class"],
                label=row["divergence_label"],
            )
        )
    lines.extend(
        [
            "",
            "### Inside per-species `ellingham_fit_range_K` (includes 2000-2600 K primary-refit)",
            "",
            "| species | T_K | CEA kJ/mol O2 | Ellingham kJ/mol O2 | residual | finding_class | label |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    vs_all = report["channel_vs_channel_species_fit"]
    if not vs_all:
        lines.append("| — | — | — | — | — | no_matched_points | — |")
    for row in vs_all[:20]:
        lines.append(
            "| {species} | {T} | {cea:.4g} | {ell:.4g} | {res:.4g} | `{finding}` | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                cea=float(row["cea_kJ_per_mol_O2"]),
                ell=float(row["ellingham_kJ_per_mol_O2"]),
                res=float(row["residual_kJ_per_mol_O2"]),
                finding=row["finding_class"],
                label=row["divergence_label"],
            )
        )
    accounting = report.get("elemental_reference_accounting") or {}
    lines.extend(
        [
            "",
            "## Na2O elemental-reference accounting (1600 K)",
            "",
            (
                f"- stoichiometry: `{accounting.get('stoichiometry', '')}`"
            ),
            (
                "- ΔG_vap(Na) = {vap:.3f} kJ/mol; shift = {shift:.2f} kJ/mol O2".format(
                    vap=float(accounting.get("dG_vap_Na_kJ_mol") or 0.0),
                    shift=float(accounting.get("shift_kJ_per_mol_O2") or 0.0),
                )
            ),
            f"- {accounting.get('note', '')}",
            "",
        ]
    )
    lines.extend(
        [
            "",
            "## Vapour-rail P_sat (compilation-derived comparator)",
            "",
            (
                "Comparator: ln(P_sat/P0) = −[dfG(g) − dfG(cr or l)] / (R T) "
                "at the same printed T. JANAF P0 = 0.1 MPa. Residual in log10. "
                "Na 1156 K and Mg 1363 K are 1-bar boiling-point sanity rows."
            ),
            "",
            f"- G9 NASA-CEA MnO/CoO: {report.get('g9_cea_mno_coo', '')}",
            "",
            "### Twenty largest in-envelope |residual| (log10)",
            "",
            "| species | T_K | residual log10 | finding_class | label |",
            "|---|---:|---:|---|---|",
        ]
    )
    psat_rows = report.get("psat_top20_in_envelope") or []
    if not psat_rows:
        lines.append("| — | — | — | no_scored_points | — |")
    for row in psat_rows:
        lines.append(
            "| {species} | {T} | {res:.4g} | `{finding}` | `{label}` |".format(
                species=row["species"],
                T=row["temperature_K"],
                res=float(row["residual_log10"]),
                finding=row["finding_class"],
                label=row["divergence_label"],
            )
        )
    lines.extend(
        [
            "",
            "### Na/Mg boiling-point sanity",
            "",
            "| species | T_K | status | residual log10 | note |",
            "|---|---:|---|---:|---|",
        ]
    )
    sanity = report.get("psat_sanity") or []
    if not sanity:
        lines.append("| — | — | missing | — | no sanity rows |")
    for row in sanity:
        res = row.get("residual_log10")
        res_s = "—" if res is None else f"{float(res):.4g}"
        lines.append(
            "| {species} | {T} | {status} | {res} | {note} |".format(
                species=row["species"],
                T=row["temperature_K"],
                status=row["status"],
                res=res_s,
                note=str(row.get("note") or "").replace("|", "/"),
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
