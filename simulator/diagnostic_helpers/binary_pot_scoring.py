"""Binary-pot scoring arm: measured KEMS activities vs each melt engine.

Pots are content-derived from the Kambayashi 1985 and Ohara 1987 extracts.
Residuals are the result. No gate, no retune. model_derived / quoted rows
are emitted but not scored.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from simulator.accounting.formulas import parse_formula
from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    REFUSAL_COMPOSITION_PROJECTED,
    REPORT_DIR,
    BinaryPot,
    BinaryPotBatteryError,
    EquilibrateCell,
    Po2Request,
    _refusal_matrix,
    cell_score_authority,
    cell_score_notice_kinds,
    reclassify_projected_composition_cells,
)
from simulator.diagnostic_helpers.extract_reproduction import (
    AdoptedObservation,
    observation_admission_reason,
)
from simulator.state import MOLAR_MASS


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POTS_PATH = REPO_ROOT / "data" / "binary_pots.yaml"
SCORING_REPORT_STEM = "binary-pot-scoring-arm"
SCORING_EXTRACTS: tuple[Path, ...] = (
    REPO_ROOT / "data" / "literature" / "extracts" / "kems-057-kambayashi-1985.yaml",
    REPO_ROOT / "data" / "literature" / "extracts" / "kems-058-ohara-1987.yaml",
)

# Scoring arm does not reuse the engine-arm 1500–2300 K / commanded-pO2 grid.
# KEMS holds are Fe-saturated Knudsen cells; pO2 is the engine default.
SCORING_PO2 = Po2Request(mode="engine_default", po2_bar=None)

_SCORING_BLOCK_START = (
    "# Scoring-arm pots (measured KEMS compositions). Generated from the "
    "extracts;\n# do not hand-list. The eight engine-arm pots above are unchanged.\n"
)
_WT_PCT_SUM_TOLERANCE = 1.0e-9
_KAMBAYASHI_MEAN_T_IN_FETO = 0.95

_OXIDE_WT_KEYS: dict[str, str] = {
    "P2O5_wt_percent": "P2O5",
    "FeO_wt_percent": "FeO",
    "Fe2O3_wt_percent": "Fe2O3",
    "CaO_wt_percent": "CaO",
    "MgO_wt_percent": "MgO",
    "MnO_wt_percent": "MnO",
    "SiO2_wt_percent": "SiO2",
    "PbO_wt_percent": "PbO",
    "Na2O_wt_percent": "Na2O",
    "Al2O3_wt_percent": "Al2O3",
}
_OXIDE_MOL_KEYS: dict[str, str] = {
    "P2O5_mol_percent": "P2O5",
    "FetO_mol_percent": "FetO",
    "FeO_mol_percent": "FeO",
    "Fe2O3_mol_percent": "Fe2O3",
    "CaO_mol_percent": "CaO",
    "MgO_mol_percent": "MgO",
    "MnO_mol_percent": "MnO",
    "SiO2_mol_percent": "SiO2",
    "PbO_mol_percent": "PbO",
    "Na2O_mol_percent": "Na2O",
    "Al2O3_mol_percent": "Al2O3",
}

_UNSCORED_METHOD_CLASSES = frozenset(
    {"model_derived", "quoted_measurement", "quoted", "secondary_compilation"}
)


class BinaryPotScoringError(BinaryPotBatteryError):
    """Raised when a scoring-arm catalog or conversion contract is violated."""


@dataclass(frozen=True)
class ScoringPot:
    pot_id: str
    source_id: str
    observation_id: str
    sample_no: int | None
    temperatures_K: tuple[float, ...]
    composition_basis: str
    composition_conversion: str
    composition_as_printed: Mapping[str, float]
    composition_wt_pct: Mapping[str, float]
    why: str
    doi: str | None = None

    def as_binary_pot(self) -> BinaryPot:
        return BinaryPot(
            pot_id=self.pot_id,
            kato_1993_table4_system=None,
            why=self.why,
            composition_wt_pct=dict(self.composition_wt_pct),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "observation_id": self.observation_id,
            "sample_no": self.sample_no,
            "temperatures_K": list(self.temperatures_K),
            "composition_basis": self.composition_basis,
            "composition_conversion": self.composition_conversion,
            "composition_as_printed": dict(self.composition_as_printed),
            "composition_wt_pct": dict(self.composition_wt_pct),
            "why": self.why,
            "doi": self.doi,
        }


def oxide_molar_mass_g_mol(oxide: str) -> float:
    """Molar mass for an oxide formula. Prefer simulator MOLAR_MASS.

    PbO is not an OXIDE_SPECIES; CIAAW Pb + O via parse_formula is the
    fallback so PbO-P2O5 pots can be converted without adding Pb to the
    runtime melt basis. Domain gates still refuse PbO.
    """

    if oxide in MOLAR_MASS:
        mass = float(MOLAR_MASS[oxide])
        if math.isfinite(mass) and mass > 0.0:
            return mass
    return float(parse_formula(oxide, species=oxide).molar_mass_g_mol)


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0:
        return None
    return number


def _renormalize_wt_pct(masses: Mapping[str, float]) -> dict[str, float]:
    """Scale oxide masses to wt% on a 100 g basis.

    Premise: printed analyses may sum below 100 (unidentified remainder).
    Algebra: w_i = 100 * m_i / sum_j m_j.
    Unit check: g / g * 100 = wt%. Sanity: equal masses → 50/50.
    """

    total = sum(float(v) for v in masses.values())
    if total <= 0.0 or not math.isfinite(total):
        raise BinaryPotScoringError("composition masses must sum to a positive finite total")
    wt = {str(k): 100.0 * float(v) / total for k, v in masses.items() if float(v) > 0.0}
    if len(wt) < 2:
        raise BinaryPotScoringError("scoring pot must contain at least two oxides")
    return wt


def feto_fe2_fe3_to_feo_fe2o3_moles(
    n_feto: float, fe2_over_fe3: float | None
) -> tuple[float, float]:
    """Split n moles of Fe_t O into n_FeO and n_Fe2O3.

    Premise: printed FetO is Fe_t O (t Fe per O). Authors print Fe2+/Fe3+
    = r, or omit it. When r is omitted, use Kambayashi's mean t = 0.95.
    Fe2+/Fe3+ = r ⇒ n_Fe2+ = r n_Fe3+, n_Fe = n_Fe2+ + n_Fe3+,
    n_O = n_Fe2+ + 1.5 n_Fe3+ (FeO: 1 O/Fe; Fe2O3: 1.5 O/Fe).
    Then t = n_Fe/n_O = (r+1)/(r+1.5). FetO formula units count oxygen,
    so n_Fe = t n_FetO.
    Algebra: n_Fe2+ = n_FetO r/(r+1.5); n_Fe3+ = n_FetO/(r+1.5);
    n_FeO = n_Fe2+; n_Fe2O3 = n_Fe3+/2.
    When r is omitted: t = 0.95 = (r+1)/(r+1.5) ⇒ r = 8.5.
    Unit check: moles. Sanity: r→∞ → n_FeO = n_FetO, n_Fe2O3 = 0;
    r = 0 → n_FeO = 0, n_Fe2O3 = n_FetO/3 (one Fe2O3 = 3 Fe_{2/3}O).
    """

    n = float(n_feto)
    if not math.isfinite(n) or n < 0.0:
        raise BinaryPotScoringError("FetO moles must be finite and non-negative")
    if fe2_over_fe3 is None:
        # t(r + 1.5) = r + 1 → r(1 - t) = 1.5 t - 1 → r = (1.5 t - 1)/(1 - t)
        # t=0.95 → r = 0.425 / 0.05 = 8.5
        t = _KAMBAYASHI_MEAN_T_IN_FETO
        r = (1.5 * t - 1.0) / (1.0 - t)
    else:
        r = float(fe2_over_fe3)
    if not math.isfinite(r) or r < 0.0:
        raise BinaryPotScoringError("Fe2+/Fe3+ must be finite and non-negative")
    denom = r + 1.5
    n_feo = n * r / denom
    n_fe2o3 = n / (2.0 * denom)
    return n_feo, n_fe2o3


def convert_printed_composition_to_wt_pct(
    row: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float], str, str]:
    """Return (wt%, as_printed, basis, conversion_id)."""

    as_printed: dict[str, float] = {}
    pb = _finite_positive(row.get("Pb_wt_percent"))
    p_el = _finite_positive(row.get("P_wt_percent"))
    if pb is not None:
        as_printed["Pb_wt_percent"] = pb
    if p_el is not None:
        as_printed["P_wt_percent"] = p_el
    if pb is not None and p_el is not None:
        # Elemental wt% → oxide mass, then renormalize to 100 wt%.
        # Premise: authors print Pb and P (not PbO/P2O5) on Table 3.
        # Algebra: m_PbO = w_Pb * M_PbO / M_Pb;
        # m_P2O5 = w_P * M_P2O5 / (2 M_P).
        # Unit check: (g/100 g) * (g/mol) / (g/mol) = g oxide / 100 g sample.
        # Sanity: Table 3 row 1, X_P2O5 = n_P2O5 / (n_PbO + n_P2O5) = 0.359.
        m_pb = oxide_molar_mass_g_mol("Pb")
        m_pbo = oxide_molar_mass_g_mol("PbO")
        m_p = oxide_molar_mass_g_mol("P")
        m_p2o5 = oxide_molar_mass_g_mol("P2O5")
        masses = {
            "PbO": pb * m_pbo / m_pb,
            "P2O5": p_el * m_p2o5 / (2.0 * m_p),
        }
        return (
            _renormalize_wt_pct(masses),
            as_printed,
            "elemental_wt_percent",
            "elemental_Pb_P_wt_pct_to_PbO_P2O5_renormalized",
        )

    oxide_wt: dict[str, float] = {}
    for key, oxide in _OXIDE_WT_KEYS.items():
        value = _finite_positive(row.get(key))
        if value is None:
            continue
        as_printed[key] = value
        oxide_wt[oxide] = oxide_wt.get(oxide, 0.0) + value
    if len(oxide_wt) >= 2:
        return (
            _renormalize_wt_pct(oxide_wt),
            as_printed,
            "oxide_wt_percent",
            "printed_oxide_wt_pct_renormalized",
        )

    oxide_mol: dict[str, float] = {}
    for key, oxide in _OXIDE_MOL_KEYS.items():
        value = _finite_positive(row.get(key))
        if value is None:
            continue
        as_printed[key] = value
        oxide_mol[oxide] = oxide_mol.get(oxide, 0.0) + value
    r_fe = _finite_positive(row.get("Fe2_over_Fe3"))
    if r_fe is not None:
        as_printed["Fe2_over_Fe3"] = r_fe
    if "FetO" in oxide_mol:
        n_feo, n_fe2o3 = feto_fe2_fe3_to_feo_fe2o3_moles(
            oxide_mol.pop("FetO"), r_fe
        )
        oxide_mol["FeO"] = oxide_mol.get("FeO", 0.0) + n_feo
        oxide_mol["Fe2O3"] = oxide_mol.get("Fe2O3", 0.0) + n_fe2o3
    if len(oxide_mol) >= 2:
        # mol% → mass, then wt%. Premise: printed N_i are mole percent.
        # Algebra: m_i = N_i * M_i; w_i = 100 m_i / sum m.
        # Unit check: mol% * g/mol = relative grams; ratio is wt%.
        masses = {
            oxide: moles * oxide_molar_mass_g_mol(oxide)
            for oxide, moles in oxide_mol.items()
            if moles > 0.0
        }
        conversion = (
            "mol_pct_feto_split_to_oxide_wt_pct"
            if "Fe2_over_Fe3" in as_printed or "FetO_mol_percent" in as_printed
            else "mol_pct_to_oxide_wt_pct"
        )
        return (
            _renormalize_wt_pct(masses),
            as_printed,
            "oxide_mol_percent",
            conversion,
        )

    raise BinaryPotScoringError("row has no convertible melt composition")


def row_has_convertible_composition(row: Mapping[str, Any]) -> bool:
    try:
        convert_printed_composition_to_wt_pct(row)
    except BinaryPotScoringError:
        return False
    return True


def _row_temperature_K(
    row: Mapping[str, Any], obs: Mapping[str, Any]
) -> float | None:
    for candidate in (
        row.get("T_K"),
        (obs.get("values") or {}).get("T_K"),
        row.get("T_C") and (float(row["T_C"]) + 273.0),
    ):
        value = _finite_positive(candidate)
        if value is not None and value > 0.0:
            return value
    t_range = obs.get("T_range_K") or []
    if isinstance(t_range, Sequence) and t_range:
        lo = _finite_positive(t_range[0])
        hi = _finite_positive(t_range[-1])
        if lo is not None and hi is not None and math.isclose(lo, hi, abs_tol=1e-9):
            return lo
    return None


def _family_for(as_printed: Mapping[str, float], wt: Mapping[str, float]) -> str:
    if "Pb_wt_percent" in as_printed or "PbO" in wt:
        return "pbo_p2o5"
    additives = {"CaO", "MgO", "MnO", "SiO2"} & set(wt)
    if additives:
        return "feto_p2o5_i"
    return "feto_p2o5"


def _composition_preference(obs: Mapping[str, Any], row: Mapping[str, Any]) -> int:
    values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
    quantity = str(values.get("quantity") or "")
    score = 0
    if quantity == "melt_composition":
        score += 50
    if "composition" in quantity:
        score += 20
    if row.get("FeO_wt_percent") is not None and row.get("Fe2O3_wt_percent") is not None:
        score += 30
    if row.get("Fe2_over_Fe3") is not None:
        score += 20
    if row.get("Pb_wt_percent") is not None and row.get("P_wt_percent") is not None:
        score += 30
    if row.get("P2O5_mol_percent") is not None:
        score += 10
    return score


def _source_short(source_id: str) -> str:
    # kems-057-kambayashi-1985 → kambayashi_1985
    parts = str(source_id).split("-")
    if len(parts) >= 3:
        return f"{parts[2]}_{parts[-1]}"
    return re.sub(r"[^a-z0-9]+", "_", source_id.lower()).strip("_")


def _iter_extract_rows(extract: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any], int, Mapping[str, Any]]]:
    source_id = str(extract.get("source_id") or "")
    for species, block in (extract.get("species") or {}).items():
        for obs in block.get("observations") or []:
            if not isinstance(obs, Mapping):
                continue
            values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
            rows = values.get("rows")
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if isinstance(row, Mapping):
                    yield source_id, obs, index, row


def build_scoring_pots_from_extracts(
    extract_paths: Sequence[Path] | None = None,
) -> tuple[ScoringPot, ...]:
    """Every printed melt composition, temperatures from printed holds."""

    paths = tuple(extract_paths) if extract_paths is not None else SCORING_EXTRACTS
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in paths:
        extract = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(extract, Mapping):
            raise BinaryPotScoringError(f"{path} is not a mapping")
        source_id = str(extract.get("source_id") or "")
        doi = ((extract.get("source") or {}).get("doi") if isinstance(extract.get("source"), Mapping) else None)
        for _, obs, index, row in _iter_extract_rows(extract):
            if not row_has_convertible_composition(row):
                continue
            wt, as_printed, basis, conversion = convert_printed_composition_to_wt_pct(row)
            family = _family_for(as_printed, wt)
            sample = row.get("sample_no")
            if sample is None:
                group_key = (
                    source_id,
                    family,
                    json.dumps({k: round(v, 6) for k, v in as_printed.items()}, sort_keys=True),
                )
            else:
                group_key = (source_id, family, str(int(sample)))
            temperature = _row_temperature_K(row, obs)
            preference = _composition_preference(obs, row)
            current = grouped.get(group_key)
            if current is None:
                grouped[group_key] = {
                    "source_id": source_id,
                    "doi": doi,
                    "family": family,
                    "sample_no": None if sample is None else int(sample),
                    "row_index": index,
                    "obs": obs,
                    "row": row,
                    "wt": wt,
                    "as_printed": as_printed,
                    "basis": basis,
                    "conversion": conversion,
                    "preference": preference,
                    "temperatures": set(),
                }
                current = grouped[group_key]
            if temperature is not None:
                current["temperatures"].add(float(temperature))
            if preference > current["preference"]:
                current.update(
                    {
                        "obs": obs,
                        "row": row,
                        "row_index": index,
                        "wt": wt,
                        "as_printed": as_printed,
                        "basis": basis,
                        "conversion": conversion,
                        "preference": preference,
                    }
                )

    pots: list[ScoringPot] = []
    for group_key, payload in grouped.items():
        temperatures = tuple(sorted(payload["temperatures"]))
        if not temperatures:
            raise BinaryPotScoringError(
                f"no printed temperature for {payload['source_id']} {group_key}"
            )
        sample_no = payload["sample_no"]
        short = _source_short(payload["source_id"])
        if sample_no is not None:
            pot_id = f"{short}_{payload['family']}_s{sample_no:02d}"
        else:
            pot_id = f"{short}_{payload['family']}_r{payload['row_index'] + 1}"
        observation_id = str(payload["obs"].get("observation_id") or "")
        wt = dict(payload["wt"])
        total = sum(wt.values())
        if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=_WT_PCT_SUM_TOLERANCE):
            raise BinaryPotScoringError(f"{pot_id} wt% sums to {total:g}, not 100")
        why = (
            f"KEMS scoring pot from {payload['source_id']} "
            f"{observation_id}"
            + (f" sample {sample_no}" if sample_no is not None else "")
            + f"; printed {payload['basis']} converted by {payload['conversion']}."
        )
        pots.append(
            ScoringPot(
                pot_id=pot_id,
                source_id=payload["source_id"],
                observation_id=observation_id,
                sample_no=sample_no,
                temperatures_K=temperatures,
                composition_basis=payload["basis"],
                composition_conversion=payload["conversion"],
                composition_as_printed=dict(payload["as_printed"]),
                composition_wt_pct=wt,
                why=why,
                doi=str(payload["doi"]) if payload["doi"] else None,
            )
        )
    pots.sort(key=lambda pot: pot.pot_id)
    if not pots:
        raise BinaryPotScoringError("no scoring pots derived from extracts")
    return tuple(pots)


def load_scoring_pots(path: Path | None = None) -> tuple[ScoringPot, ...]:
    pots_path = Path(path) if path is not None else DEFAULT_POTS_PATH
    payload = yaml.safe_load(pots_path.read_text(encoding="utf-8")) or {}
    block = payload.get("scoring_pots")
    if not isinstance(block, Mapping) or not block:
        raise BinaryPotScoringError(f"{pots_path} has no scoring_pots mapping")
    pots: list[ScoringPot] = []
    for pot_id, row in block.items():
        if not isinstance(row, Mapping):
            raise BinaryPotScoringError(f"scoring pot {pot_id!r} must be a mapping")
        composition = row.get("composition_wt_pct")
        if not isinstance(composition, Mapping) or not composition:
            raise BinaryPotScoringError(f"scoring pot {pot_id!r} has no composition_wt_pct")
        wt = {str(k): float(v) for k, v in composition.items() if float(v) > 0.0}
        if len(wt) < 2:
            raise BinaryPotScoringError(f"scoring pot {pot_id!r} needs two oxides")
        total = sum(wt.values())
        # YAML dumps round to 10 significant figures; re-close onto 100 wt%.
        if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=1.0e-6):
            raise BinaryPotScoringError(f"{pot_id} wt% sums to {total:g}, not 100")
        wt = {k: 100.0 * v / total for k, v in wt.items()}
        temperatures = tuple(float(t) for t in (row.get("temperatures_K") or ()))
        if not temperatures:
            raise BinaryPotScoringError(f"{pot_id} has no temperatures_K")
        sample = row.get("sample_no")
        pots.append(
            ScoringPot(
                pot_id=str(pot_id),
                source_id=str(row.get("source_id") or ""),
                observation_id=str(row.get("observation_id") or ""),
                sample_no=None if sample is None else int(sample),
                temperatures_K=temperatures,
                composition_basis=str(row.get("composition_basis") or ""),
                composition_conversion=str(row.get("composition_conversion") or ""),
                composition_as_printed=dict(row.get("composition_as_printed") or {}),
                composition_wt_pct=wt,
                why=str(row.get("why") or "").strip(),
                doi=row.get("doi"),
            )
        )
    return tuple(pots)


def _yaml_float(value: float) -> float:
    return float(f"{float(value):.10g}")


def scoring_pots_to_mapping(pots: Sequence[ScoringPot]) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for pot in pots:
        row: dict[str, Any] = {
            "source_id": pot.source_id,
            "observation_id": pot.observation_id,
        }
        if pot.sample_no is not None:
            row["sample_no"] = pot.sample_no
        row["temperatures_K"] = [_yaml_float(t) for t in pot.temperatures_K]
        row["composition_basis"] = pot.composition_basis
        row["composition_conversion"] = pot.composition_conversion
        row["composition_as_printed"] = {
            k: _yaml_float(float(v)) for k, v in pot.composition_as_printed.items()
        }
        row["composition_wt_pct"] = {
            k: _yaml_float(float(v)) for k, v in pot.composition_wt_pct.items()
        }
        row["why"] = pot.why
        if pot.doi:
            row["doi"] = pot.doi
        block[pot.pot_id] = row
    return block


def render_scoring_pots_yaml_block(pots: Sequence[ScoringPot]) -> str:
    dumped = yaml.safe_dump(
        {"scoring_pots": scoring_pots_to_mapping(pots)},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=88,
    )
    return _SCORING_BLOCK_START + dumped


def sync_scoring_pots_into_catalog(
    pots: Sequence[ScoringPot],
    path: Path | None = None,
) -> Path:
    """Append or replace the scoring_pots block; leave engine-arm pots intact."""

    pots_path = Path(path) if path is not None else DEFAULT_POTS_PATH
    text = pots_path.read_text(encoding="utf-8")
    marker = "\nscoring_pots:"
    comment_marker = "\n# Scoring-arm pots"
    cut = len(text)
    for token in (comment_marker, marker):
        idx = text.find(token)
        if idx >= 0:
            cut = min(cut, idx)
    kept = text[:cut].rstrip() + "\n\n"
    pots_path.write_text(kept + render_scoring_pots_yaml_block(pots), encoding="utf-8")
    return pots_path


def engine_arm_pot_ids(path: Path | None = None) -> tuple[str, ...]:
    from simulator.diagnostic_helpers.binary_pot_battery import load_binary_pots

    pots, _grid = load_binary_pots(path)
    return tuple(pot.pot_id for pot in pots)


# ---------------------------------------------------------------------------
# Measured / model_derived activity comparators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityComparator:
    source_id: str
    observation_id: str
    species: str
    observable: str
    method_class: str
    sample_no: int | None
    temperature_K: float
    measured: float
    units: str
    standard_state: str | None
    uncertainty: Mapping[str, Any] | None
    doi: str | None
    row: Mapping[str, Any]
    admission_reason: str | None = None


def method_class_is_scored(method_class: str) -> bool:
    token = str(method_class or "").strip().lower()
    if not token:
        return False
    if token in _UNSCORED_METHOD_CLASSES or token.startswith("model_derived"):
        return False
    if token.startswith("quoted"):
        return False
    return token == "measured"


def _gamma_field_temperature_K(field: str, obs: Mapping[str, Any]) -> float | None:
    match = re.search(r"(\d{3,4})C$", field)
    if match:
        return float(match.group(1)) + 273.0
    return _row_temperature_K({}, obs)


def _superseded_observation_ids(extract: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for block in (extract.get("species") or {}).values():
        if not isinstance(block, Mapping):
            continue
        for obs in block.get("observations") or []:
            if isinstance(obs, Mapping) and obs.get("supersedes"):
                ids.add(str(obs["supersedes"]))
    return ids


def _canonical_admission_reason(
    *,
    extract: Mapping[str, Any],
    species: str,
    obs: Mapping[str, Any],
    values: Mapping[str, Any],
    superseded_ids: set[str],
) -> str | None:
    """Reuse extract_reproduction admission; do not reconstruct a second gate."""

    observation_id = str(obs.get("observation_id") or "")
    adopted = AdoptedObservation(
        species_id=str(species),
        source_id=str(extract.get("source_id") or ""),
        observation_id=observation_id,
        obs_type=str(obs.get("type") or ""),
        review_status=(
            str(obs["review_status"]) if obs.get("review_status") is not None else None
        ),
        phase=str(obs["phase"]) if obs.get("phase") is not None else None,
        regime=str(obs["regime"]) if obs.get("regime") is not None else None,
        standard_state=(
            str(obs["standard_state"]) if obs.get("standard_state") is not None else None
        ),
        T_range_K=None,
        units=str(obs["units"]) if obs.get("units") is not None else None,
        uncertainty=obs.get("uncertainty"),
        locator=obs.get("locator"),
        values=dict(values),
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption="",
        adoption_basis=(
            "superseded" if observation_id in superseded_ids else "priority_winner"
        ),
        admission_metadata=dict(obs),
    )
    return observation_admission_reason(adopted)


def iter_activity_comparators(
    extract_paths: Sequence[Path] | None = None,
) -> tuple[ActivityComparator, ...]:
    """Activity / activity-coefficient rows from the scoring extracts."""

    paths = tuple(extract_paths) if extract_paths is not None else SCORING_EXTRACTS
    found: list[ActivityComparator] = []
    for path in paths:
        extract = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(extract, Mapping):
            continue
        source_id = str(extract.get("source_id") or "")
        doi = None
        source_block = extract.get("source")
        if isinstance(source_block, Mapping):
            doi = source_block.get("doi")
        superseded_ids = _superseded_observation_ids(extract)
        for species, block in (extract.get("species") or {}).items():
            for obs in block.get("observations") or []:
                if not isinstance(obs, Mapping):
                    continue
                if str(obs.get("type") or "") != "activity_coefficient":
                    continue
                values = obs.get("values") if isinstance(obs.get("values"), Mapping) else {}
                method = str(values.get("method_class") or "")
                observation_id = str(obs.get("observation_id") or "")
                standard_state = obs.get("standard_state")
                uncertainty = obs.get("uncertainty") if isinstance(obs.get("uncertainty"), Mapping) else None
                admission_reason = _canonical_admission_reason(
                    extract=extract,
                    species=str(species),
                    obs=obs,
                    values=values,
                    superseded_ids=superseded_ids,
                )
                rows = values.get("rows")
                if isinstance(rows, list) and rows:
                    for row in rows:
                        if not isinstance(row, Mapping):
                            continue
                        measured = _finite_positive(row.get("a_P2O5"))
                        observable = "activity"
                        units = "1"
                        if measured is None:
                            ln_gamma = row.get("ln_gamma_P2O5")
                            if _finite_positive(ln_gamma) is not None or (
                                isinstance(ln_gamma, (int, float)) and math.isfinite(float(ln_gamma))
                            ):
                                measured = math.exp(float(ln_gamma))
                                observable = "activity_coefficient"
                        temperature = _row_temperature_K(row, obs)
                        if measured is None or temperature is None:
                            continue
                        sample = row.get("sample_no")
                        found.append(
                            ActivityComparator(
                                source_id=source_id,
                                observation_id=observation_id,
                                species=str(species),
                                observable=observable,
                                method_class=method,
                                sample_no=None if sample is None else int(sample),
                                temperature_K=float(temperature),
                                measured=float(measured),
                                units=units,
                                standard_state=None if standard_state is None else str(standard_state),
                                uncertainty=uncertainty,
                                doi=None if doi is None else str(doi),
                                row=dict(row),
                                admission_reason=admission_reason,
                            )
                        )
                    continue
                for field_name, value in values.items():
                    if not str(field_name).startswith("gamma_"):
                        continue
                    if str(field_name).endswith("_as_printed"):
                        continue
                    measured = _finite_positive(value)
                    temperature = _gamma_field_temperature_K(str(field_name), obs)
                    if measured is None or temperature is None:
                        continue
                    field_unc = None
                    if isinstance(uncertainty, Mapping) and field_name in uncertainty:
                        field_unc = {
                            "kind": "absolute",
                            "value": uncertainty[field_name],
                            "as_printed": uncertainty.get("as_printed"),
                        }
                    found.append(
                        ActivityComparator(
                            source_id=source_id,
                            observation_id=observation_id,
                            species=str(species),
                            observable="activity_coefficient",
                            method_class=method,
                            sample_no=None,
                            temperature_K=float(temperature),
                            measured=float(measured),
                            units="1",
                            standard_state=None if standard_state is None else str(standard_state),
                            uncertainty=field_unc or uncertainty,
                            doi=None if doi is None else str(doi),
                            row={"field": field_name, "value": measured},
                            admission_reason=admission_reason,
                        )
                    )
    return tuple(found)


def _engine_activity(cell: EquilibrateCell, species: str) -> float | None:
    activities = cell.melt_activities or {}
    for key in (species, f"{species}_Liq", species.replace("2O5", "2O5(l)")):
        value = activities.get(key)
        number = _finite_positive(value)
        if number is not None and number > 0.0:
            return number
    return None


def _activity_reference_phase(text: str | None) -> str | None:
    blob = str(text or "").lower()
    if not blob:
        return None
    if "(s)" in blob or "solid" in blob:
        return "solid"
    if "(l)" in blob or "liquid" in blob:
        return "liquid"
    return None


def _cell_activity_reference_phase(cell: EquilibrateCell, species: str) -> str | None:
    declared = _activity_reference_phase(cell.engine_reason)
    if declared is not None:
        return declared
    activities = cell.melt_activities or {}
    if f"{species}_Liq" in activities or f"{species}(l)" in activities:
        return "liquid"
    if species in activities:
        # Melt-engine activity is a liquid-reference quantity.
        return "liquid"
    return None


def _activity_reference_mismatch(
    cell: EquilibrateCell, comparator: ActivityComparator
) -> str | None:
    measured_phase = _activity_reference_phase(comparator.standard_state)
    predicted_phase = _cell_activity_reference_phase(cell, comparator.species)
    if (
        measured_phase
        and predicted_phase
        and measured_phase != predicted_phase
    ):
        return (
            f"KEMS standard state {comparator.standard_state}; "
            "engine melt_activity is liquid-reference; no solid/liquid conversion"
        )
    return None


def _predicted_for_comparator(
    cell: EquilibrateCell,
    comparator: ActivityComparator,
    pot: ScoringPot,
) -> tuple[float | None, str | None]:
    """Engine quantity matching the measured observable.

    KEMS Raoultian a_P2O5 is relative to P2O5(s). Engines report melt
    activity as a liquid-reference quantity. No solid/liquid conversion
    is applied; mismatched references are not scored. Henry gamma uses
    a = gamma * X, so gamma_pred = a_pred / X_P2O5 when the engine
    returns activity.
    Premise: X_P2O5 = n_P2O5 / sum n_oxide from the converted pot.
    Algebra: n_i = w_i / M_i; X = n_P2O5 / sum n. Sanity: equal moles → 0.5.
    """

    activity = _engine_activity(cell, comparator.species)
    if activity is None:
        return None, None
    if comparator.observable == "activity":
        return activity, (
            "engine melt_activity used as returned; KEMS standard state "
            f"{comparator.standard_state or 'unspecified'}; no solid/liquid conversion"
        )
    moles = {
        oxide: float(wt) / oxide_molar_mass_g_mol(oxide)
        for oxide, wt in pot.composition_wt_pct.items()
    }
    total = sum(moles.values())
    x = moles.get(comparator.species, 0.0) / total if total else 0.0
    if x <= 0.0:
        return None, "no mole fraction for Henry gamma conversion"
    # a = gamma * X  (Raoult/Henry definition) → gamma = a / X
    return activity / x, (
        f"gamma_pred = a_engine / X_{comparator.species} with X={x:.6g}; "
        "KEMS gamma is Henrian vs P2O5(s)"
    )


# ---------------------------------------------------------------------------
# Engine pass + envelope rows
# ---------------------------------------------------------------------------


def run_scoring_arm(
    *,
    pots_path: Path | None = None,
    extract_paths: Sequence[Path] | None = None,
    engine_names: Sequence[str] | None = None,
    handles: Mapping[str, Any] | None = None,
    pots: Sequence[ScoringPot] | None = None,
    progress_log: Path | None = None,
) -> dict[str, Any]:
    """Run printed-T scoring pots on every battery engine and score envelopes."""

    from scripts.calibration_battery import envelope, rail_for
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BATTERY_ENGINE_NAMES,
        _emit_progress,
        _engine_identities_from_toml,
        _hostname,
        _refusal_matrix,
        _utc_stamp,
        equilibrate_cell,
        probe_battery_engines,
    )

    names = tuple(engine_names) if engine_names is not None else BATTERY_ENGINE_NAMES
    scoring_pots = tuple(pots) if pots is not None else load_scoring_pots(pots_path)
    extracts = tuple(extract_paths) if extract_paths is not None else SCORING_EXTRACTS
    comparators = iter_activity_comparators(extracts)
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    hostname = _hostname()
    resolved = dict(handles) if handles is not None else probe_battery_engines(names)

    n_expected = 0
    for pot in scoring_pots:
        n_expected += len(pot.temperatures_K) * len(names)
    _emit_progress(
        progress_log,
        f"{_utc_stamp()} START scoring-arm hostname={hostname} n_expected={n_expected}",
    )

    cells: list[EquilibrateCell] = []
    for pot in scoring_pots:
        binary = pot.as_binary_pot()
        for name in names:
            handle = resolved.get(name) or probe_battery_engines((name,))[name]
            resolved[name] = handle
            for temperature_K in pot.temperatures_K:
                cell = equilibrate_cell(
                    handle,
                    binary,
                    temperature_K=float(temperature_K),
                    po2=SCORING_PO2,
                )
                cells.append(cell)
                _emit_progress(
                    progress_log,
                    (
                        f"{_utc_stamp()} {len(cells)}/{n_expected} "
                        f"pot={cell.pot_id} engine={cell.engine} "
                        f"T={cell.temperature_K:g} "
                        f"status={cell.status} "
                        f"reason={cell.refusal_reason or '-'} "
                        f"n_act={len(cell.melt_activities)} "
                        f"wall={cell.wall_s:.3f}"
                    ),
                )
                if not handle.available:
                    revived = probe_battery_engines((name,))[name]
                    resolved[name] = revived
                    handle = revived

    envelopes = score_scoring_arm(
        pots=scoring_pots,
        cells=cells,
        comparators=comparators,
        envelope=envelope,
        rail_for=rail_for,
    )
    binary_pots = [pot.as_binary_pot() for pot in scoring_pots]
    identities = _engine_identities_from_toml()
    engines_block: dict[str, Any] = {}
    for name in names:
        handle = resolved.get(name)
        engines_block[name] = {
            "available": bool(handle.available) if handle is not None else False,
            "unavailable_reason": (
                handle.unavailable_reason if handle is not None else "not_probed"
            ),
            "takes_fo2": bool(handle.takes_fo2) if handle is not None else False,
            "identity": (
                dict(handle.identity)
                if handle is not None and handle.identity
                else identities.get(name, {})
            ),
        }
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    scored = [row for row in envelopes if row.get("score_eligible")]
    residual_summary = _residual_summary(envelopes)
    report = {
        "schema_version": 1,
        "kind": "binary_pot_scoring_arm",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authority": "diagnostic_only",
        "certifies": False,
        "calibrates": False,
        "verdict": None,
        "posture": (
            "Measured KEMS activities vs each engine. Typed refusals are "
            "scored rows. model_derived / quoted extract rows are not scored. "
            "No coefficient is adjusted by this harness."
        ),
        "hostname": hostname,
        "receipt": {
            "hostname": hostname,
            "engine_identities": identities,
            "wall_s": wall,
            "cpu_s": cpu,
            "wall_cpu_ratio": (wall / cpu) if cpu > 0.0 else None,
        },
        "pots": [pot.as_payload() | {"pot_id": pot.pot_id} for pot in scoring_pots],
        "engines": engines_block,
        "refusal_matrix": _refusal_matrix(binary_pots, names, cells),
        "n_cells": len(cells),
        "n_ok": sum(1 for cell in cells if cell.status == "ok"),
        "n_refused": sum(1 for cell in cells if cell.status == "refusal"),
        "n_envelope_rows": len(envelopes),
        "n_score_eligible": len(scored),
        "scored_rows_per_engine": _count_scored_per_engine(envelopes),
        "residual_summary": residual_summary,
        "cells": [cell.as_payload() for cell in cells],
        "envelopes": envelopes,
    }
    _emit_progress(
        progress_log,
        (
            f"{_utc_stamp()} DONE scoring-arm hostname={hostname} "
            f"n_cells={report['n_cells']} n_ok={report['n_ok']} "
            f"n_refused={report['n_refused']} "
            f"n_envelope={report['n_envelope_rows']} "
            f"n_score_eligible={report['n_score_eligible']} "
            f"wall={wall:.3f} cpu={cpu:.3f}"
        ),
    )
    return report


def scoring_pot_from_payload(row: Mapping[str, Any]) -> ScoringPot:
    sample = row.get("sample_no")
    return ScoringPot(
        pot_id=str(row.get("pot_id") or ""),
        source_id=str(row.get("source_id") or ""),
        observation_id=str(row.get("observation_id") or ""),
        sample_no=None if sample is None else int(sample),
        temperatures_K=tuple(float(t) for t in (row.get("temperatures_K") or ())),
        composition_basis=str(row.get("composition_basis") or ""),
        composition_conversion=str(row.get("composition_conversion") or ""),
        composition_as_printed=dict(row.get("composition_as_printed") or {}),
        composition_wt_pct=dict(row.get("composition_wt_pct") or {}),
        why=str(row.get("why") or "").strip(),
        doi=row.get("doi"),
    )


def recompute_scoring_from_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Re-score an existing scoring-arm JSON (no engine re-run)."""

    from scripts.calibration_battery import envelope, rail_for

    pots = tuple(
        scoring_pot_from_payload(row)
        for row in report.get("pots") or []
        if isinstance(row, Mapping)
    )
    cells = reclassify_projected_composition_cells(
        [
            EquilibrateCell.from_payload(row)
            for row in report.get("cells") or []
            if isinstance(row, Mapping)
        ],
        pots,
    )
    extracts = SCORING_EXTRACTS
    comparators = iter_activity_comparators(extracts)
    envelopes = score_scoring_arm(
        pots=pots,
        cells=cells,
        comparators=comparators,
        envelope=envelope,
        rail_for=rail_for,
    )
    binary_pots = [pot.as_binary_pot() for pot in pots]
    engine_names = list((report.get("engines") or {}).keys()) or list(
        BATTERY_ENGINE_NAMES
    )
    scored = [row for row in envelopes if row.get("score_eligible")]
    updated = dict(report)
    updated["cells"] = [cell.as_payload() for cell in cells]
    updated["envelopes"] = envelopes
    updated["refusal_matrix"] = _refusal_matrix(binary_pots, engine_names, cells)
    updated["n_ok"] = sum(1 for cell in cells if cell.status == "ok")
    updated["n_refused"] = sum(1 for cell in cells if cell.status == "refusal")
    updated["n_envelope_rows"] = len(envelopes)
    updated["n_score_eligible"] = len(scored)
    updated["scored_rows_per_engine"] = _count_scored_per_engine(envelopes)
    updated["residual_summary"] = _residual_summary(envelopes)
    return updated


def score_scoring_arm(
    *,
    pots: Sequence[ScoringPot],
    cells: Sequence[EquilibrateCell],
    comparators: Sequence[ActivityComparator],
    envelope,
    rail_for,
) -> list[dict[str, Any]]:
    """Emit calibration_battery envelope rows for cells and comparators."""

    pots_by_id = {pot.pot_id: pot for pot in pots}

    rows: list[dict[str, Any]] = []
    for cell in cells:
        pot = pots_by_id[cell.pot_id]
        matched = [
            cmp for cmp in comparators
            if cmp.source_id == pot.source_id
            and (cmp.sample_no is None or cmp.sample_no == pot.sample_no)
            and math.isclose(cmp.temperature_K, cell.temperature_K, abs_tol=0.6)
        ]
        # Henry gamma (sample_no is None) applies to every pot of that source
        # at that T; still emit the cell refusal when nothing matches.
        if not matched:
            rows.append(
                _cell_envelope(
                    pot=pot,
                    cell=cell,
                    envelope=envelope,
                    rail_for=rail_for,
                    comparator=None,
                )
            )
            continue
        emitted_measured = False
        for comparator in matched:
            if comparator.sample_no is None and comparator.observable == "activity_coefficient":
                # One Henry-gamma row per engine×T, not per pot.
                key = (
                    comparator.observation_id,
                    cell.engine,
                    comparator.temperature_K,
                )
                if any(
                    row.get("raw", {}).get("henry_key") == list(key)
                    for row in rows
                ):
                    continue
            row = _comparator_envelope(
                pot=pot,
                cell=cell,
                comparator=comparator,
                envelope=envelope,
                rail_for=rail_for,
            )
            if comparator.sample_no is None:
                row.setdefault("raw", {})["henry_key"] = [
                    comparator.observation_id,
                    cell.engine,
                    comparator.temperature_K,
                ]
            rows.append(row)
            emitted_measured = True
        if not emitted_measured:
            rows.append(
                _cell_envelope(
                    pot=pot,
                    cell=cell,
                    envelope=envelope,
                    rail_for=rail_for,
                    comparator=None,
                )
            )
    return rows


def _envelope_notices(cell) -> tuple[str, ...]:
    notices: list[str] = []
    if cell.engine_reason:
        notices.append(cell.engine_reason)
    notices.extend(cell_score_notice_kinds(cell))
    return tuple(notices)


def _cell_envelope(*, pot, cell, envelope, rail_for, comparator) -> dict[str, Any]:
    refused = cell.status == "refusal"
    status = (cell.refusal_reason or "refused") if refused else "ok"
    species = "P2O5" if "P2O5" in pot.composition_wt_pct else next(iter(pot.composition_wt_pct))
    predicted = _engine_activity(cell, species)
    row = envelope(
        dataset_id=pot.source_id,
        observation_id=f"{pot.observation_id}:{pot.pot_id}@{cell.temperature_K:g}K:{cell.engine}",
        species=species,
        observable="activity",
        units="1",
        measured=None,
        predicted=predicted,
        status=status if refused or predicted is None else "ok",
        conditions={
            "temperature_K": cell.temperature_K,
            "pot_id": pot.pot_id,
            "composition_wt_pct": dict(pot.composition_wt_pct),
            "po2": cell.po2.as_payload(),
        },
        raw={
            "engine": cell.engine,
            "cell": cell.as_payload(),
            "pot": pot.as_payload(),
        },
        uncertainty=None,
        rail=rail_for(species, "activity_coefficient"),
        evidence="direct experiment",
        source_doi=pot.doi,
        notices=_envelope_notices(cell),
        authority=cell_score_authority(
            cell, refused=refused or predicted is None
        ),
        selected=False,
        score_allowed=False,
        run_id=f"{pot.pot_id}:{cell.engine}",
        execution={"hostname": cell.hostname, "wall_s": cell.wall_s, "cpu_s": cell.cpu_s},
    )
    return row


def _comparator_envelope(*, pot, cell, comparator, envelope, rail_for) -> dict[str, Any]:
    admission_reason = comparator.admission_reason
    reference_mismatch = _activity_reference_mismatch(cell, comparator)
    scored_method = (
        method_class_is_scored(comparator.method_class)
        and admission_reason is None
        and reference_mismatch is None
    )
    predicted, conversion_note = _predicted_for_comparator(cell, comparator, pot)
    refused = cell.status == "refusal" or predicted is None
    if cell.status == "refusal":
        status = cell.refusal_reason or "refused"
    elif predicted is None:
        status = "unsupported-observable"
    else:
        status = "ok"
    notices = list(_envelope_notices(cell))
    if conversion_note:
        notices.append(conversion_note)
    if admission_reason:
        notices.append(admission_reason)
    if reference_mismatch:
        notices.append(reference_mismatch)
    if not method_class_is_scored(comparator.method_class):
        notices.append(
            f"method_class={comparator.method_class}; model_derived/quoted rows are not scored"
        )
    row = envelope(
        dataset_id=comparator.source_id,
        observation_id=(
            f"{comparator.observation_id}:{pot.pot_id}@"
            f"{comparator.temperature_K:g}K:{cell.engine}"
        ),
        species=comparator.species,
        observable=comparator.observable,
        units=comparator.units,
        measured=comparator.measured,
        predicted=predicted,
        status=status,
        conditions={
            "temperature_K": comparator.temperature_K,
            "pot_id": pot.pot_id,
            "sample_no": comparator.sample_no,
            "composition_wt_pct": dict(pot.composition_wt_pct),
            "standard_state": comparator.standard_state,
            "method_class": comparator.method_class,
            "po2": cell.po2.as_payload(),
        },
        raw={
            "engine": cell.engine,
            "cell": cell.as_payload(),
            "comparator": {
                "observation_id": comparator.observation_id,
                "method_class": comparator.method_class,
                "row": dict(comparator.row),
            },
            "pot": pot.as_payload(),
        },
        uncertainty=comparator.uncertainty,
        rail=rail_for(comparator.species, "activity_coefficient"),
        evidence="direct experiment" if scored_method else "derived measurement",
        source_doi=comparator.doi,
        notices=tuple(notices),
        authority=cell_score_authority(cell, refused=refused),
        selected=scored_method,
        score_allowed=scored_method,
        run_id=f"{pot.pot_id}:{cell.engine}",
        execution={"hostname": cell.hostname, "wall_s": cell.wall_s, "cpu_s": cell.cpu_s},
    )
    return row


def _count_scored_per_engine(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if not row.get("score_eligible"):
            continue
        engine = row.get("engine") or (row.get("raw") or {}).get("engine")
        counts[str(engine or "unknown")] += 1
    return dict(sorted(counts.items()))


def _residual_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_engine: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.get("score_eligible"):
            continue
        dex = (row.get("signed_residual") or {}).get("dex")
        if isinstance(dex, (int, float)) and math.isfinite(float(dex)):
            engine = row.get("engine") or (row.get("raw") or {}).get("engine")
            by_engine[str(engine or "unknown")].append(float(dex))
    summary: dict[str, Any] = {}
    for engine, values in sorted(by_engine.items()):
        abs_vals = [abs(v) for v in values]
        summary[engine] = {
            "n": len(values),
            "median_signed_dex": sorted(values)[len(values) // 2],
            "median_abs_dex": sorted(abs_vals)[len(abs_vals) // 2],
            "max_abs_dex": max(abs_vals),
            "rmse_dex": math.sqrt(sum(v * v for v in values) / len(values)),
        }
    return summary


def render_scoring_report_markdown(report: Mapping[str, Any]) -> str:
    receipt = report.get("receipt") or {}
    lines = [
        "# Binary-pot battery — scoring arm",
        "",
        f"- generated: `{report.get('generated_at')}`",
        (
            f"- authority: `{report.get('authority')}`; "
            f"certifies: `{str(report.get('certifies')).lower()}`; "
            f"calibrates: `{str(report.get('calibrates')).lower()}`"
        ),
        "- verdict: none — measured KEMS activities vs each engine; refusals are the finding",
        "- model_derived / quoted extract rows are not scored",
        f"- hostname: `{report.get('hostname')}`",
        (
            f"- wall: `{receipt.get('wall_s'):.3f} s`; "
            f"cpu: `{receipt.get('cpu_s'):.3f} s`; "
            f"wall/cpu: `{receipt.get('wall_cpu_ratio')}`"
            if receipt.get("wall_s") is not None
            else "- wall/cpu: unavailable"
        ),
        f"- cells: `{report.get('n_cells')}` ok `{report.get('n_ok')}` refused `{report.get('n_refused')}`",
        (
            f"- envelope rows: `{report.get('n_envelope_rows')}`; "
            f"score_eligible: `{report.get('n_score_eligible')}`"
        ),
        "",
        "## Engine identity digests (`engines.local.toml`)",
        "",
    ]
    identities = receipt.get("engine_identities") or {}
    if identities:
        lines.extend(["| engine | version | digest |", "|---|---|---|"])
        for key, block in sorted(identities.items()):
            if not isinstance(block, Mapping):
                continue
            lines.append(
                f"| `{key}` | `{block.get('version', '')}` | `{block.get('digest', '')}` |"
            )
    else:
        lines.append("No `engines.local.toml` identities were readable on this host.")

    lines.extend(
        [
            "",
            "## Engine availability",
            "",
            "| engine | available | unavailable_reason |",
            "|---|---|---|",
        ]
    )
    for name, block in (report.get("engines") or {}).items():
        lines.append(
            f"| `{name}` | `{str(block.get('available')).lower()}` | "
            f"{block.get('unavailable_reason') or '—'} |"
        )

    engine_names = list((report.get("engines") or {}).keys())
    lines.extend(
        [
            "",
            "## Refusal matrix (pot × engine)",
            "",
            "Each cell is the dominant typed refusal (or `ok`). Counts are cells.",
            "",
        ]
    )
    header = "| pot | " + " | ".join(f"`{name}`" for name in engine_names) + " |"
    sep = "|---|" + "|".join("---" for _ in engine_names) + "|"
    lines.extend([header, sep])
    matrix = report.get("refusal_matrix") or {}
    for pot in report.get("pots") or []:
        pot_id = pot["pot_id"]
        cells = []
        for name in engine_names:
            cell = (matrix.get(pot_id) or {}).get(name) or {}
            reason = cell.get("dominant_reason")
            n_ok = cell.get("n_ok", 0)
            n_refused = cell.get("n_refused", 0)
            if reason is None and n_ok:
                label = f"ok ({n_ok})"
            elif reason is None:
                label = "—"
            else:
                label = f"{reason} ({n_refused}/{cell.get('n_cells', 0)})"
                note = cell.get("note")
                if reason == REFUSAL_COMPOSITION_PROJECTED and note:
                    label = (
                        f"{reason} ({n_refused}/{cell.get('n_cells', 0)}; {note})"
                    )
            cells.append(label)
        lines.append(f"| `{pot_id}` | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## Scored rows per engine",
            "",
            "| engine | score_eligible N |",
            "|---|---:|",
        ]
    )
    per_engine = dict(report.get("scored_rows_per_engine") or {})
    for name in engine_names:
        per_engine.setdefault(name, 0)
    for name in engine_names:
        lines.append(f"| `{name}` | {per_engine[name]} |")

    lines.extend(
        [
            "",
            "## Residual summary per engine",
            "",
            "| engine | n | median signed dex | median |Δ| dex | max |Δ| dex | RMSE dex |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    summary = report.get("residual_summary") or {}
    if not summary:
        lines.append("| — | 0 | — | — | — | — |")
    for name, block in summary.items():
        lines.append(
            f"| `{name}` | {block['n']} | {block['median_signed_dex']:+.3f} | "
            f"{block['median_abs_dex']:.3f} | {block['max_abs_dex']:.3f} | "
            f"{block['rmse_dex']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Scored rows",
            "",
            "| pot | engine | T K | species | observable | measured | predicted | dex | method | status | eligible |",
            "|---|---|---:|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    envelopes = [
        row for row in (report.get("envelopes") or [])
        if row.get("score_eligible") or row.get("comparator_status") not in {None}
    ]
    shown = [row for row in (report.get("envelopes") or []) if row.get("score_eligible")]
    if not shown:
        lines.append("| — | — | — | — | — | — | — | — | — | no score_eligible rows | `false` |")
    for row in shown[:80]:
        conditions = row.get("conditions") or {}
        predicted = row.get("predicted")
        measured = row.get("measured")
        dex = (row.get("signed_residual") or {}).get("dex")
        lines.append(
            "| `{pot}` | `{engine}` | {T:g} | {sp} | {obs} | {meas} | {pred} | {dex} | `{method}` | `{status}` | `{elig}` |".format(
                pot=conditions.get("pot_id"),
                engine=row.get("engine") or (row.get("raw") or {}).get("engine"),
                T=float(conditions.get("temperature_K") or 0.0),
                sp=row.get("species"),
                obs=row.get("observable"),
                meas="—" if measured is None else f"{measured:.4g}",
                pred="—" if predicted is None else f"{predicted:.4g}",
                dex="—" if dex is None else f"{float(dex):+.3f}",
                method=(conditions.get("method_class") or "—"),
                status=row.get("comparator_status"),
                elig=str(bool(row.get("score_eligible"))).lower(),
            )
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "The companion JSON contains every cell, typed refusal, and envelope row. "
            "No result is clipped or used to change a coefficient. "
            "MAGEMin ig drops P2O5 (no ig endmember) and now refuses those "
            "FetO-P2O5 pots as `composition_projected`; a projected bulk is "
            "not a result for the requested pot.",
            "",
        ]
    )
    return "\n".join(lines)


def write_scoring_reports(
    report: Mapping[str, Any],
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    dest = Path(output_dir) if output_dir is not None else REPORT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    json_path = dest / f"{SCORING_REPORT_STEM}.json"
    md_path = dest / f"{SCORING_REPORT_STEM}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    md_path.write_text(render_scoring_report_markdown(report))
    return json_path, md_path

