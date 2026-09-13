"""Binary-pot scoring arm: measured KEMS activities vs each melt engine.

Pots are content-derived from the Kambayashi 1985 and Ohara 1987 extracts.
Residuals are the result. No gate, no retune. model_derived / quoted rows
are emitted but not scored.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from simulator.accounting.formulas import parse_formula
from simulator.diagnostic_helpers.binary_pot_battery import (
    BinaryPot,
    BinaryPotBatteryError,
    Po2Request,
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
