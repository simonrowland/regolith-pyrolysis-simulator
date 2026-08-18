"""Bulk-organic source term: primary pyrolysis, char fate, tar fate.

Pure functions. No ledger writes. Callers commit through ``commit_batch``.

Three stages:

1. PRIMARY PYROLYSIS (empirical, class-level): bulk OM -> gas + tar + char.
   CO is not a primary product (Voropaev 2023 / REF-059: CO is char
   gasification).
2. CHAR FATE (empirical class placement + Boudouard limiter): surviving
   char is the logistic/Boudouard remainder. Ellingham a_C and the CCO
   buffer are diagnostics; they do not enter surviving_char_mol.
3. TAR FATE (kinetic, least certain): crack / coke / coat on the existing
   wall-deposit path.

Mass closes on C, H, N, O, S. Unknown is never converted to zero:
missing S -> H2S/COS are proven-zero; missing coefficient -> refusal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engines.builtin.cco_redox_buffer import (
    CCO_SOURCE,
    cco_log10_fO2_bar,
)
from simulator.silent_zero import (
    CATEGORY_MARK,
    CATEGORY_PROVEN_ZERO,
    CATEGORY_REFUSE,
    ZeroBecause,
    make_note,
)

GAS_CONSTANT_J_PER_MOL_K = 8.31446
CELSIUS_TO_KELVIN = 273.15
_PARAMS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "organics_pyrolysis.yaml"
)

PRIMARY_GAS_SPECIES = ("H2", "CH4", "H2O", "CO2", "N2", "H2S", "COS")
REDUCING_SPECIES = ("H2", "CO", "CH4", "H2S")
CHAR_SPECIES = "C"
TAR_SPECIES = "organic_tar"

# JANAF-style linear Ellingham intercepts for the two carbon oxidation
# lines, per mol O2. Used only to query a_C(pO2, T); Boudouard Kp is
# the sourced Voropaev relation, not these intercepts.
#
# Premise: Chase 1998 NIST-JANAF formation values at 298 K,
#   ΔHf(CO) = -110.527 kJ/mol, ΔGf(CO) = -137.163 kJ/mol,
#   ΔHf(CO2) = -393.522 kJ/mol, ΔGf(CO2) = -394.389 kJ/mol.
# Algebra, 2 C + O2 -> 2 CO (per mol O2):
#   ΔH = 2*(-110.527) = -221.054 kJ
#   ΔG_298 = 2*(-137.163) = -274.326 kJ
#   ΔS = (ΔH - ΔG)/T = 178.77 J/K  =>  0.17877 kJ/K
# Algebra, C + O2 -> CO2 (per mol O2):
#   ΔH = -393.522 kJ
#   ΔG_298 = -394.389 kJ
#   ΔS = (ΔH - ΔG)/T = 2.91 J/K  =>  0.00291 kJ/K
# Unit check: dG = dH - T dS in kJ/mol O2; dS in kJ/K/mol O2.
# Sanity: 2C+O2=2CO dG falls with T (unique more-gas-moles line);
#   at 1000 K, dG_CO ≈ -221.054 - 1000*0.17877 = -399.8 kJ/mol O2
#   (Richardson–Jeffes ~ -399 kJ). C+O2=CO2 is nearly flat.
_C_TO_CO_DH_KJ_PER_MOL_O2 = -221.054
_C_TO_CO_DS_KJ_PER_K_PER_MOL_O2 = 0.17877
_C_TO_CO2_DH_KJ_PER_MOL_O2 = -393.522
_C_TO_CO2_DS_KJ_PER_K_PER_MOL_O2 = 0.00291


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def load_organics_pyrolysis_params(
    path: Path | None = None,
) -> dict[str, Any]:
    payload = yaml.safe_load((path or _PARAMS_PATH).read_text()) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("organics_pyrolysis.yaml must be a mapping")
    return dict(payload)


def _interp_cumulative(temperature_C: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation of a cumulative % curve.

    Below the first knot the value is the first knot (do not invent a
    sub-200 C release). Above the last knot the value holds at the last
    knot: the 800 C '100%' is a run-end normalization, not complete
    release, so we refuse to extrapolate past it.
    """
    if temperature_C <= xs[0]:
        return ys[0] / 100.0
    if temperature_C >= xs[-1]:
        return ys[-1] / 100.0
    for left, right in zip(range(len(xs) - 1), range(1, len(xs))):
        if xs[left] <= temperature_C <= xs[right]:
            span = xs[right] - xs[left]
            if span <= 0.0:
                return ys[right] / 100.0
            frac = (temperature_C - xs[left]) / span
            return (ys[left] + frac * (ys[right] - ys[left])) / 100.0
    return ys[-1] / 100.0


def voropaev_release_fraction(
    species: str,
    temperature_C: float,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Voropaev 2023 cumulative release fraction at T.

    Shape only. The 800 C column is marked as run-end, not complete.
    """
    cfg = (params or load_organics_pyrolysis_params()).get("voropaev_2023_release") or {}
    temps = [float(t) for t in cfg.get("temperature_C") or ()]
    curves = dict(cfg.get("cumulative_pct") or {})
    if species not in curves or not temps:
        return {
            "fraction": None,
            "status": "refusal",
            "zero_because": ZeroBecause.MISSING_COEFFICIENT.value,
            "doctrine_category": CATEGORY_REFUSE,
            "detail": f"no Voropaev curve for {species!r}",
        }
    ys = [float(v) for v in curves[species]]
    fraction = _interp_cumulative(float(temperature_C), temps, ys)
    run_end = float(cfg.get("run_end_C") or 800.0)
    return {
        "fraction": fraction,
        "status": "partly_grounded",
        "run_end_normalization": float(temperature_C) >= run_end - 1e-9,
        "run_end_is_complete_release": False,
        "source": cfg.get("source"),
        "use": cfg.get("use"),
    }


def organic_co2_release_fraction(
    temperature_C: float,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Organic-decarboxylation CO2 only (not carbonate, not adsorbed).

    Adsorbed CO2 is proven-zero (Voropaev: none at 50/110 C). Carbonate
    CO2 is not_speciated here — it belongs to the existing carbonate
    path. The organic population is the Voropaev CO2 curve renormalized
    to the 53.2% that is out by 500 C.
    """
    payload = params or load_organics_pyrolysis_params()
    populations = dict(payload.get("co2_populations") or {})
    organic = dict(populations.get("organic_decarboxylation") or {})
    denom = _finite(organic.get("fraction_of_voropaev_CO2_by_500C"), 0.532)
    raw = voropaev_release_fraction("CO2", temperature_C, payload)
    if raw.get("fraction") is None:
        return raw
    # Above 500 C the organic population is exhausted; further Voropaev
    # CO2 is the carbonate second maximum.
    if float(temperature_C) >= 500.0:
        fraction = 1.0
    else:
        fraction = min(1.0, float(raw["fraction"]) / max(denom, 1e-12))
    return {
        "fraction": fraction,
        "status": "partly_grounded",
        "population": "organic_decarboxylation",
        "adsorbed": "proven_zero",
        "carbonate": "not_speciated",
        "source": organic.get("source"),
    }


def boudouard_lg_kp(temperature_K: float, params: Mapping[str, Any] | None = None) -> float:
    """Voropaev lg Kp = -9001/T + 9.28 for C + CO2 <-> 2CO.

    Premise: Voropaev 2023 reports lg Kp = -9001/T + 9.28.
    Algebra: Kp = 1 when 9001/T = 9.28 -> T = 9001/9.28 = 969.94 K
    = 696.79 C (paper rounds to 697 C).
    Unit check: T in K; lg is log10; Kp = pCO^2 / pCO2 with p in bar.
    Sanity: Kp << 1 at 500 C (char stable vs CO2); Kp >> 1 at 1000 C
    (irreversible toward CO). Classic steelmaking Boudouard gate.
    """
    cfg = ((params or load_organics_pyrolysis_params()).get("char_fate") or {}).get(
        "boudouard"
    ) or {}
    a = _finite(cfg.get("lg_Kp_A"), -9001.0)
    b = _finite(cfg.get("lg_Kp_B"), 9.28)
    t_k = max(float(temperature_K), 1.0)
    return a / t_k + b


def boudouard_kp(temperature_K: float, params: Mapping[str, Any] | None = None) -> float:
    return 10.0 ** boudouard_lg_kp(temperature_K, params)


def boudouard_crossover_K(params: Mapping[str, Any] | None = None) -> float:
    cfg = ((params or load_organics_pyrolysis_params()).get("char_fate") or {}).get(
        "boudouard"
    ) or {}
    a = abs(_finite(cfg.get("lg_Kp_A"), -9001.0))
    b = _finite(cfg.get("lg_Kp_B"), 9.28)
    return a / b


def carbon_delta_g_co_kj_per_mol_o2(temperature_K: float) -> float:
    """ΔG(2 C + O2 -> 2 CO) per mol O2. Falls with T."""
    return (
        _C_TO_CO_DH_KJ_PER_MOL_O2
        - float(temperature_K) * _C_TO_CO_DS_KJ_PER_K_PER_MOL_O2
    )


def carbon_delta_g_co2_kj_per_mol_o2(temperature_K: float) -> float:
    """ΔG(C + O2 -> CO2) per mol O2. Nearly flat."""
    return (
        _C_TO_CO2_DH_KJ_PER_MOL_O2
        - float(temperature_K) * _C_TO_CO2_DS_KJ_PER_K_PER_MOL_O2
    )


def carbon_activity(
    temperature_K: float,
    pO2_bar: float,
    *,
    p_gas_bar: float = 1.0,
) -> dict[str, float]:
    """Unclamped carbon activity from the C/CO and C/CO2 Ellingham lines.

    Formation basis matches ``ellingham_graph.metal_activity_factor``:
    n_M M + O2 -> n_ox oxide, a_M = (K * a_ox^{n_ox} / pO2)^{1/n_M}
    with K = exp(ΔG / RT) and a_ox = p_gas (bar) for a gaseous oxide.

    2 C + O2 -> 2 CO: n_M=2, n_ox=2.
    C + O2 -> CO2: n_M=1, n_ox=1.

    Binding activity is the lower of the two (more oxidizing line).
    a_C >= 1 means graphite is stable; a_C << 1 means it wants to gasify.
    Extent is still oxidant-limited — see ``char_fate``.
    """
    t_k = max(float(temperature_K), 1.0)
    p_o2 = max(float(pO2_bar), 1e-30)
    p_gas = max(float(p_gas_bar), 1e-30)
    rt = GAS_CONSTANT_J_PER_MOL_K * t_k

    dG_co = carbon_delta_g_co_kj_per_mol_o2(t_k)
    k_co = math.exp(dG_co * 1000.0 / rt)
    a_c_co = (k_co * (p_gas ** 2) / p_o2) ** 0.5

    dG_co2 = carbon_delta_g_co2_kj_per_mol_o2(t_k)
    k_co2 = math.exp(dG_co2 * 1000.0 / rt)
    a_c_co2 = k_co2 * p_gas / p_o2

    binding = min(a_c_co, a_c_co2)
    return {
        "a_C_CO": a_c_co,
        "a_C_CO2": a_c_co2,
        "a_C_binding": binding,
        "a_C_clamped": min(binding, 1.0),
        "dG_CO_kJ_per_mol_O2": dG_co,
        "dG_CO2_kJ_per_mol_O2": dG_co2,
        "pO2_bar": p_o2,
        "temperature_K": t_k,
    }


def _atom_dict(atoms: Mapping[str, float]) -> dict[str, float]:
    out = {"C": 0.0, "H": 0.0, "O": 0.0, "N": 0.0, "S": 0.0}
    for key, value in dict(atoms).items():
        symbol = str(key).strip()
        if symbol in out:
            out[symbol] = max(0.0, _finite(value))
    return out


def _add_atoms(target: dict[str, float], source: Mapping[str, float], sign: float = 1.0) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0.0) + sign * float(value)


def _species_atoms(species: str, mol: float) -> dict[str, float]:
    stoich = {
        "H2": {"H": 2.0},
        "CH4": {"C": 1.0, "H": 4.0},
        "H2O": {"H": 2.0, "O": 1.0},
        "CO2": {"C": 1.0, "O": 2.0},
        "CO": {"C": 1.0, "O": 1.0},
        "N2": {"N": 2.0},
        "NH3": {"N": 1.0, "H": 3.0},
        "HCN": {"H": 1.0, "C": 1.0, "N": 1.0},
        "H2S": {"H": 2.0, "S": 1.0},
        "COS": {"C": 1.0, "O": 1.0, "S": 1.0},
        "C": {"C": 1.0},
    }
    scale = max(0.0, float(mol))
    return {el: count * scale for el, count in stoich.get(species, {}).items()}


@dataclass(frozen=True)
class EvolvedGasRecord:
    """Consumable per-species gas record for a later H2/H2O–CO/CO2 fO2 task.

    Not coupled to melt fO2 in this module.
    """

    schema: str = "organics_evolved_gas.v1"
    temperature_C: float = 0.0
    stage: str = "combined"
    species_mol: dict[str, float] = field(default_factory=dict)
    species_mol_frac: dict[str, float] = field(default_factory=dict)
    reducing_species_mol: dict[str, float] = field(default_factory=dict)
    status: str = "status_bearing_non_authoritative"
    notes: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "temperature_C": self.temperature_C,
            "stage": self.stage,
            "species_mol": dict(self.species_mol),
            "species_mol_frac": dict(self.species_mol_frac),
            "reducing_species_mol": dict(self.reducing_species_mol),
            "status": self.status,
            "notes": list(self.notes),
        }


def _gas_record(
    species_mol: Mapping[str, float],
    *,
    temperature_C: float,
    stage: str,
    notes: list[dict[str, Any]] | None = None,
) -> EvolvedGasRecord:
    cleaned = {
        key: float(value)
        for key, value in species_mol.items()
        if float(value) > 1e-18
    }
    total = sum(cleaned.values())
    frac = (
        {key: value / total for key, value in cleaned.items()}
        if total > 0.0
        else {}
    )
    reducing = {key: float(cleaned.get(key, 0.0) or 0.0) for key in REDUCING_SPECIES}
    return EvolvedGasRecord(
        temperature_C=float(temperature_C),
        stage=stage,
        species_mol=cleaned,
        species_mol_frac=frac,
        reducing_species_mol=reducing,
        notes=tuple(notes or ()),
    )


@dataclass(frozen=True)
class PrimaryPyrolysisResult:
    gas_mol: dict[str, float]
    tar_atoms: dict[str, float]
    char_c_mol: float
    leftover_atoms: dict[str, float]
    notes: tuple[dict[str, Any], ...]
    status: str
    f_char_C: float
    temperature_C: float
    evolved_gas: EvolvedGasRecord

    def as_dict(self) -> dict[str, Any]:
        return {
            "gas_mol": dict(self.gas_mol),
            "tar_atoms": dict(self.tar_atoms),
            "char_c_mol": self.char_c_mol,
            "leftover_atoms": dict(self.leftover_atoms),
            "notes": list(self.notes),
            "status": self.status,
            "f_char_C": self.f_char_C,
            "temperature_C": self.temperature_C,
            "evolved_gas": self.evolved_gas.as_dict(),
        }


def primary_pyrolysis(
    atoms: Mapping[str, float],
    temperature_C: float,
    params: Mapping[str, Any] | None = None,
) -> PrimaryPyrolysisResult:
    """Split bulk organic atoms into gas + tar + nascent char.

    CO is forced to zero (proven-zero as a primary product).
    """
    payload = params or load_organics_pyrolysis_params()
    available = _atom_dict(atoms)
    notes: list[dict[str, Any]] = []
    nascent = dict(payload.get("nascent_char") or {})
    default_basis = str(nascent.get("default_basis") or "f_char_of_bulk_organic_C")
    char_row = dict(nascent.get(default_basis) or {})
    f_char = min(1.0, max(0.0, _finite(char_row.get("value"), 0.5075)))

    char_c = available["C"] * f_char
    mob_c = max(0.0, available["C"] - char_c)

    yields = dict(payload.get("primary_gas_yields_of_mobilized_C") or {})

    def _yield(name: str, default: float) -> float:
        row = dict(yields.get(name) or {})
        if str(row.get("status") or "") == "proven_zero":
            return 0.0
        return min(1.0, max(0.0, _finite(row.get("value"), default)))

    y_ch4 = _yield("CH4", 0.02)
    y_co2 = _yield("CO2_organic", 0.20)
    y_cos = _yield("COS", 0.002)

    r_ch4 = float(voropaev_release_fraction("CH4", temperature_C, payload)["fraction"] or 0.0)
    r_co2 = float(organic_co2_release_fraction(temperature_C, payload)["fraction"] or 0.0)
    r_cos = float(voropaev_release_fraction("COS", temperature_C, payload)["fraction"] or 0.0)
    r_h2 = float(voropaev_release_fraction("H2", temperature_C, payload)["fraction"] or 0.0)
    r_h2o = float(voropaev_release_fraction("H2O", temperature_C, payload)["fraction"] or 0.0)
    r_n2 = float(voropaev_release_fraction("N2", temperature_C, payload)["fraction"] or 0.0)
    r_h2s = float(voropaev_release_fraction("H2S", temperature_C, payload)["fraction"] or 0.0)

    n_ch4 = min(y_ch4 * mob_c * r_ch4, available["H"] / 4.0)
    n_co2 = min(y_co2 * mob_c * r_co2, available["O"] / 2.0)
    if available["S"] <= 1e-18:
        n_cos = 0.0
        n_h2s = 0.0
        notes.append(
            make_note(
                ZeroBecause.PROVEN_EMPTY_INVENTORY,
                site="organics_pyrolysis.primary.S",
                species="H2S",
                detail="carrier has no S; H2S and COS are proven-zero",
                doctrine_category=CATEGORY_PROVEN_ZERO,
            ).as_dict()
        )
    else:
        n_cos = min(
            y_cos * mob_c * r_cos,
            available["S"],
            max(0.0, available["O"] - 2.0 * n_co2),
        )
        n_h2s = min(
            max(0.0, available["S"] - n_cos) * r_h2s,
            available["S"] - n_cos,
            max(0.0, available["H"] - 4.0 * n_ch4) / 2.0,
        )

    gas_c = n_ch4 + n_co2 + n_cos
    if gas_c > mob_c + 1e-15:
        scale = mob_c / gas_c
        n_ch4 *= scale
        n_co2 *= scale
        n_cos *= scale
        gas_c = mob_c
    tar_c = max(0.0, mob_c - gas_c)

    tar_cfg = dict(payload.get("tar") or {})
    h_per_c = _finite(dict(tar_cfg.get("H_per_C") or {}).get("value"), 0.80)
    tar_h_cap = tar_c * max(0.0, h_per_c)

    used = _atom_dict({})
    _add_atoms(used, _species_atoms("CH4", n_ch4))
    _add_atoms(used, _species_atoms("CO2", n_co2))
    _add_atoms(used, _species_atoms("COS", n_cos))
    _add_atoms(used, _species_atoms("H2S", n_h2s))
    used["C"] += char_c

    remain = _atom_dict(available)
    _add_atoms(remain, used, sign=-1.0)
    for key in remain:
        remain[key] = max(0.0, remain[key])

    n_n2 = min(remain["N"] / 2.0, (remain["N"] / 2.0) * r_n2)
    remain["N"] = max(0.0, remain["N"] - 2.0 * n_n2)

    # Organic H2O limited by remaining O and H, shaped by Voropaev H2O.
    max_h2o = min(remain["O"], remain["H"] / 2.0)
    n_h2o = max_h2o * r_h2o
    remain["O"] = max(0.0, remain["O"] - n_h2o)
    remain["H"] = max(0.0, remain["H"] - 2.0 * n_h2o)

    tar_h = min(remain["H"], tar_h_cap)
    remain["H"] = max(0.0, remain["H"] - tar_h)
    tar_o = remain["O"]
    remain["O"] = 0.0
    tar_n = remain["N"]
    remain["N"] = 0.0
    tar_s = remain["S"]
    remain["S"] = 0.0

    n_h2 = (remain["H"] / 2.0) * r_h2
    remain["H"] = max(0.0, remain["H"] - 2.0 * n_h2)
    # Any leftover H after the H2 shape still has to leave or stay in tar.
    if remain["H"] > 1e-18:
        extra_h2 = remain["H"] / 2.0
        n_h2 += extra_h2
        remain["H"] = 0.0

    tar_atoms = {
        "C": tar_c,
        "H": tar_h,
        "O": tar_o,
        "N": tar_n,
        "S": tar_s,
    }
    tar_atoms = {k: v for k, v in tar_atoms.items() if v > 1e-18}
    remain["C"] = max(0.0, remain.get("C", 0.0) - tar_c)

    gas_mol = {
        "H2": n_h2,
        "CH4": n_ch4,
        "H2O": n_h2o,
        "CO2": n_co2,
        "N2": n_n2,
        "H2S": n_h2s,
        "COS": n_cos,
        "CO": 0.0,
    }
    gas_mol = {k: v for k, v in gas_mol.items() if v > 1e-18 or k == "CO"}
    # Drop the explicit CO zero from the committed gas dict; keep the note.
    notes.append(
        make_note(
            ZeroBecause.PROVEN_BELOW_THRESHOLD,
            site="organics_pyrolysis.primary.CO",
            species="CO",
            detail="CO is char gasification (Voropaev 2023), not a primary product",
            doctrine_category=CATEGORY_PROVEN_ZERO,
        ).as_dict()
    )
    gas_mol.pop("CO", None)

    leftover = {k: v for k, v in remain.items() if v > 1e-15}
    if leftover:
        notes.append(
            make_note(
                ZeroBecause.OUT_OF_DOMAIN_MARKED,
                site="organics_pyrolysis.primary.leftover",
                detail=f"unallocated atoms after split: {leftover}",
                doctrine_category=CATEGORY_MARK,
            ).as_dict()
        )

    evolved = _gas_record(
        gas_mol, temperature_C=temperature_C, stage="primary", notes=notes
    )
    return PrimaryPyrolysisResult(
        gas_mol=gas_mol,
        tar_atoms=tar_atoms,
        char_c_mol=char_c,
        leftover_atoms=leftover,
        notes=tuple(notes),
        status="status_bearing_non_authoritative",
        f_char_C=f_char,
        temperature_C=float(temperature_C),
        evolved_gas=evolved,
    )


@dataclass(frozen=True)
class CharFateResult:
    surviving_char_mol: float
    gasified_char_mol: float
    boudouard_extent_mol: float
    pO2_extent_mol: float
    produced_co_mol: float
    produced_co2_mol: float
    consumed_co2_mol: float
    a_C: dict[str, float]
    pO2_cco_bar: float
    boudouard_Kp: float
    T_crossover_C: float
    survive_fraction: float
    status: str
    notes: tuple[dict[str, Any], ...]
    evolved_gas: EvolvedGasRecord

    def as_dict(self) -> dict[str, Any]:
        return {
            "surviving_char_mol": self.surviving_char_mol,
            "gasified_char_mol": self.gasified_char_mol,
            "boudouard_extent_mol": self.boudouard_extent_mol,
            "pO2_extent_mol": self.pO2_extent_mol,
            "produced_co_mol": self.produced_co_mol,
            "produced_co2_mol": self.produced_co2_mol,
            "consumed_co2_mol": self.consumed_co2_mol,
            "a_C": dict(self.a_C),
            "pO2_cco_bar": self.pO2_cco_bar,
            "boudouard_Kp": self.boudouard_Kp,
            "T_crossover_C": self.T_crossover_C,
            "survive_fraction": self.survive_fraction,
            "status": self.status,
            "notes": list(self.notes),
            "evolved_gas": self.evolved_gas.as_dict(),
        }


def char_fate(
    char_c_mol: float,
    temperature_C: float,
    pO2_bar: float,
    *,
    co2_mol: float = 0.0,
    o2_lance_mol: float = 0.0,
    invent_pO2_oxidant: bool = False,
    params: Mapping[str, Any] | None = None,
) -> CharFateResult:
    """Whether nascent char survives at (pO2, T).

    What actually sets ``surviving_char_mol`` is the oxidant-limited
    pO2/lance branch plus the Boudouard limiter
    ``ξ / min(n_C, n_CO2) = Kp / (1 + Kp)``. Ellingham ``a_C`` and the
    CCO ``pO2`` line are computed as diagnostics and are not inputs to
    the remainder.

    Boudouard Kp is Voropaev's 1-bar relation (REF-059). The ~697 C
    crossover is Kp=1 at 1 bar; the model has no total-pressure argument,
    so the vacuum-max-char sentence is the 1-bar statement. Low total P
    would drive Boudouard further (2 gas moles from 1) and is not
    modelled.

    1. pO2 / lance: C + O2 -> CO2 (configured complete-oxidation basis).
       High pO2 (air lance) consumes all char. Vacuum supplies essentially
       no O2, so this branch is idle on the ledger path.
    2. Boudouard: C + CO2 -> 2 CO with Voropaev Kp(T) only.
    """
    payload = params or load_organics_pyrolysis_params()
    fate_cfg = dict(payload.get("char_fate") or {})
    notes: list[dict[str, Any]] = []
    n_char = max(0.0, float(char_c_mol))
    n_co2 = max(0.0, float(co2_mol))
    n_o2 = max(0.0, float(o2_lance_mol))
    t_k = float(temperature_C) + CELSIUS_TO_KELVIN
    p_o2 = max(float(pO2_bar), 1e-30)

    a_c = carbon_activity(t_k, p_o2)
    try:
        log_cco = cco_log10_fO2_bar(t_k, 1.0)
        pO2_cco = 10.0 ** log_cco
        cco_status = "inside_or_marked"
    except (TypeError, ValueError):
        pO2_cco = float("nan")
        cco_status = "refusal"
        notes.append(
            make_note(
                ZeroBecause.MISSING_THERMO,
                site="organics_pyrolysis.char_fate.cco",
                detail="CCO buffer refused; pO2 branch still uses Ellingham a_C",
                doctrine_category=CATEGORY_REFUSE,
            ).as_dict()
        )

    kp = boudouard_kp(t_k, payload)
    t_cross = boudouard_crossover_K(payload) - CELSIUS_TO_KELVIN

    lance_row = fate_cfg.get("lancing_pO2_bar")
    if isinstance(lance_row, Mapping):
        lance_p = _finite(lance_row.get("value"), 0.21)
    else:
        lance_p = _finite(lance_row, 0.21)
    half_p = _finite(
        dict(fate_cfg.get("pO2_half_gasification_bar") or {}).get("value"),
        1.0e-4,
    )
    # Logistic in log-pO2 so the surface is two-variable, not a switch.
    # Premise: at pO2 -> 0 the pO2 branch supplies no oxidant; at pO2
    # >= air the branch is lance-complete. Algebra: s = 1/(1+(p_half/p)^1)
    # on a log10 axis: x = (log10 p - log10 p_vac) / (log10 p_lance - ...).
    # Implemented as s = 1 / (1 + (p_half / pO2)).
    # Unit check: p in bar, s dimensionless in [0, 1].
    # Sanity: pO2=1e-12 -> s~0; pO2=0.21 -> s~1.
    pO2_frac = 1.0 / (1.0 + (half_p / p_o2))
    if p_o2 >= lance_p:
        pO2_frac = 1.0
    # Ledger path is oxidant-limited except at the explicit lance
    # end-member (pO2 >= air), which means O2 is being supplied.
    # The probe may also invent intermediate-pO2 oxidant to draw
    # the two-variable surface.
    if p_o2 >= lance_p or invent_pO2_oxidant:
        invented_o2 = pO2_frac * n_char
    else:
        invented_o2 = 0.0
    pO2_extent = min(n_char, n_o2 + invented_o2)
    remaining = max(0.0, n_char - pO2_extent)
    # Lance / pO2 oxidation product is CO2 (existing t-325 default basis).
    produced_co2 = pO2_extent

    # Boudouard equilibrium limiter: ξ / min(C, CO2) = Kp / (1 + Kp).
    # Below crossover this is small; above it it approaches 1.
    boud_drive = kp / (1.0 + kp)
    boudouard_extent = min(remaining, n_co2) * boud_drive
    remaining = max(0.0, remaining - boudouard_extent)
    produced_co = 2.0 * boudouard_extent
    consumed_co2 = boudouard_extent

    survive = remaining / n_char if n_char > 1e-18 else 1.0
    gas_mol = {}
    if produced_co > 1e-18:
        gas_mol["CO"] = produced_co
    if produced_co2 > 1e-18:
        gas_mol["CO2"] = produced_co2
    if consumed_co2 > 1e-18:
        gas_mol["CO2"] = gas_mol.get("CO2", 0.0) - consumed_co2
        if gas_mol["CO2"] <= 1e-18:
            gas_mol.pop("CO2", None)

    notes.append(
        {
            "site": "organics_pyrolysis.char_fate",
            "cco_source": CCO_SOURCE,
            "cco_status": cco_status,
            "doctrine_category": CATEGORY_MARK,
            "detail": (
                "1-bar Boudouard crossover is ~697 C (REF-059); Kp has no "
                "total-P term so the vacuum-max-char rule is the 1-bar "
                "statement; high pO2 gasifies char at any T"
            ),
        }
    )
    evolved = _gas_record(
        gas_mol, temperature_C=temperature_C, stage="char_fate", notes=notes
    )
    return CharFateResult(
        surviving_char_mol=remaining,
        gasified_char_mol=pO2_extent + boudouard_extent,
        boudouard_extent_mol=boudouard_extent,
        pO2_extent_mol=pO2_extent,
        produced_co_mol=produced_co,
        produced_co2_mol=produced_co2,
        consumed_co2_mol=consumed_co2,
        a_C=a_c,
        pO2_cco_bar=pO2_cco,
        boudouard_Kp=kp,
        T_crossover_C=t_cross,
        survive_fraction=survive,
        status="status_bearing_non_authoritative",
        notes=tuple(notes),
        evolved_gas=evolved,
    )


@dataclass(frozen=True)
class TarFateResult:
    remaining_tar_atoms: dict[str, float]
    cracked_gas_mol: dict[str, float]
    coke_c_mol: float
    coat_atoms: dict[str, float]
    crack_frac: float
    coke_frac: float
    coat_frac: float
    status: str
    notes: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "remaining_tar_atoms": dict(self.remaining_tar_atoms),
            "cracked_gas_mol": dict(self.cracked_gas_mol),
            "coke_c_mol": self.coke_c_mol,
            "coat_atoms": dict(self.coat_atoms),
            "crack_frac": self.crack_frac,
            "coke_frac": self.coke_frac,
            "coat_frac": self.coat_frac,
            "status": self.status,
            "notes": list(self.notes),
        }


def tar_fate(
    tar_atoms: Mapping[str, float],
    temperature_C: float,
    wall_temperature_C: float,
    params: Mapping[str, Any] | None = None,
) -> TarFateResult:
    """Crack / coke / coat. Least-certain leg; class-level kinetics.

    Coat uses the existing ``process.wall_deposit`` account (no parallel
    fouling path). Hot walls (mandate default 1500 C) give coat_frac=0.
    """
    payload = params or load_organics_pyrolysis_params()
    cfg = dict(payload.get("tar") or {})
    notes: list[dict[str, Any]] = [
        {
            "site": "organics_pyrolysis.tar_fate",
            "status": "status_bearing_non_authoritative",
            "doctrine_category": CATEGORY_MARK,
            "detail": cfg.get("fate_honesty")
            or "tar fate is kinetic and the least certain leg",
        }
    ]
    atoms = {k: max(0.0, float(v)) for k, v in dict(tar_atoms).items() if float(v) > 1e-18}
    if not atoms:
        return TarFateResult(
            remaining_tar_atoms={},
            cracked_gas_mol={},
            coke_c_mol=0.0,
            coat_atoms={},
            crack_frac=0.0,
            coke_frac=0.0,
            coat_frac=0.0,
            status="proven_zero",
            notes=tuple(
                [
                    make_note(
                        ZeroBecause.PROVEN_EMPTY_INVENTORY,
                        site="organics_pyrolysis.tar_fate",
                        species=TAR_SPECIES,
                        detail="no tar atoms",
                        doctrine_category=CATEGORY_PROVEN_ZERO,
                    ).as_dict()
                ]
            ),
        )

    dew = _finite(dict(cfg.get("dewpoint_C") or {}).get("value"), 400.0)
    crack_t = _finite(dict(cfg.get("crack_above_C") or {}).get("value"), 700.0)
    coke_t = _finite(dict(cfg.get("coke_below_C") or {}).get("value"), 500.0)
    crack_hi = _finite(
        dict(cfg.get("crack_frac_above_crack_T") or {}).get("value"), 0.70
    )
    coke_hi = _finite(
        dict(cfg.get("coke_frac_above_crack_T") or {}).get("value"), 0.20
    )
    crack_lo = _finite(
        dict(cfg.get("crack_frac_below_coke_T") or {}).get("value"), 0.10
    )
    coke_lo = _finite(
        dict(cfg.get("coke_frac_below_coke_T") or {}).get("value"), 0.40
    )
    t = float(temperature_C)
    wall = float(wall_temperature_C)

    coat_frac = 1.0 if wall <= dew else 0.0
    if t >= crack_t:
        crack_frac = crack_hi * (1.0 - coat_frac)
        coke_frac = coke_hi * (1.0 - coat_frac)
    elif t <= coke_t:
        crack_frac = crack_lo * (1.0 - coat_frac)
        coke_frac = coke_lo * (1.0 - coat_frac)
    else:
        # Linear blend between coke-favoured and crack-favoured.
        span = max(crack_t - coke_t, 1.0)
        x = (t - coke_t) / span
        crack_frac = (crack_lo + (crack_hi - crack_lo) * x) * (1.0 - coat_frac)
        coke_frac = (coke_lo + (coke_hi - coke_lo) * x) * (1.0 - coat_frac)
    remain_frac = max(0.0, 1.0 - crack_frac - coke_frac - coat_frac)

    def _scale(frac: float) -> dict[str, float]:
        return {k: v * frac for k, v in atoms.items() if v * frac > 1e-18}

    coat_atoms = _scale(coat_frac)
    remain_atoms = _scale(remain_frac)
    cracked = _scale(crack_frac)
    coked = _scale(coke_frac)
    coke_c = float(coked.get("C", 0.0))
    # Coke keeps C as char. Heteroatoms from that slice cannot vanish.
    coke_h = float(coked.get("H", 0.0))
    coke_o = float(coked.get("O", 0.0))
    coke_n = float(coked.get("N", 0.0))
    coke_s = float(coked.get("S", 0.0))
    n_coke_h2o = min(coke_o, coke_h / 2.0)
    coke_o -= n_coke_h2o
    coke_h -= 2.0 * n_coke_h2o
    n_coke_h2s = min(coke_s, coke_h / 2.0)
    coke_s -= n_coke_h2s
    coke_h -= 2.0 * n_coke_h2s
    n_coke_h2 = coke_h / 2.0
    n_coke_n2 = coke_n / 2.0
    if coke_o > 1e-18 or coke_s > 1e-18:
        remain_atoms["O"] = remain_atoms.get("O", 0.0) + coke_o
        remain_atoms["S"] = remain_atoms.get("S", 0.0) + coke_s

    # Cracked tar: leftover C after coke already removed from this slice
    # becomes CH4 if H allows, else stays as residual C in remaining tar.
    cracked_c = float(cracked.get("C", 0.0))
    cracked_h = float(cracked.get("H", 0.0))
    cracked_o = float(cracked.get("O", 0.0))
    cracked_n = float(cracked.get("N", 0.0))
    cracked_s = float(cracked.get("S", 0.0))
    n_ch4 = min(cracked_c, cracked_h / 4.0)
    cracked_c -= n_ch4
    cracked_h -= 4.0 * n_ch4
    n_h2o = min(cracked_o, cracked_h / 2.0)
    cracked_o -= n_h2o
    cracked_h -= 2.0 * n_h2o
    n_h2s = min(cracked_s, cracked_h / 2.0)
    cracked_s -= n_h2s
    cracked_h -= 2.0 * n_h2s
    n_h2 = cracked_h / 2.0
    n_n2 = cracked_n / 2.0
    # Residual cracked C/O/S that could not speciate returns to remaining tar.
    if cracked_c > 1e-18 or cracked_o > 1e-18 or cracked_s > 1e-18:
        remain_atoms["C"] = remain_atoms.get("C", 0.0) + cracked_c
        remain_atoms["O"] = remain_atoms.get("O", 0.0) + cracked_o
        remain_atoms["S"] = remain_atoms.get("S", 0.0) + cracked_s

    cracked_gas = {
        key: value
        for key, value in {
            "CH4": n_ch4,
            "H2O": n_h2o + n_coke_h2o,
            "H2S": n_h2s + n_coke_h2s,
            "H2": n_h2 + n_coke_h2,
            "N2": n_n2 + n_coke_n2,
        }.items()
        if value > 1e-18
    }
    return TarFateResult(
        remaining_tar_atoms=remain_atoms,
        cracked_gas_mol=cracked_gas,
        coke_c_mol=coke_c,
        coat_atoms=coat_atoms,
        crack_frac=crack_frac,
        coke_frac=coke_frac,
        coat_frac=coat_frac,
        status="status_bearing_non_authoritative",
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class OrganicsSourceResult:
    primary: PrimaryPyrolysisResult
    char: CharFateResult
    tar: TarFateResult
    final_gas_mol: dict[str, float]
    final_char_c_mol: float
    final_tar_atoms: dict[str, float]
    wall_tar_atoms: dict[str, float]
    evolved_gas_primary: EvolvedGasRecord
    evolved_gas_combined: EvolvedGasRecord
    atom_closure: dict[str, float]
    closed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.as_dict(),
            "char": self.char.as_dict(),
            "tar": self.tar.as_dict(),
            "final_gas_mol": dict(self.final_gas_mol),
            "final_char_c_mol": self.final_char_c_mol,
            "final_tar_atoms": dict(self.final_tar_atoms),
            "wall_tar_atoms": dict(self.wall_tar_atoms),
            "evolved_gas_primary": self.evolved_gas_primary.as_dict(),
            "evolved_gas_combined": self.evolved_gas_combined.as_dict(),
            "atom_closure": dict(self.atom_closure),
            "closed": self.closed,
        }


def apply_organics_source(
    atoms: Mapping[str, float],
    temperature_C: float,
    pO2_bar: float,
    wall_temperature_C: float,
    *,
    o2_lance_mol: float = 0.0,
    params: Mapping[str, Any] | None = None,
) -> OrganicsSourceResult:
    """Run the three stages and prove atom closure on the result."""
    payload = params or load_organics_pyrolysis_params()
    available = _atom_dict(atoms)
    primary = primary_pyrolysis(available, temperature_C, payload)
    char = char_fate(
        primary.char_c_mol,
        temperature_C,
        pO2_bar,
        co2_mol=float(primary.gas_mol.get("CO2", 0.0) or 0.0),
        o2_lance_mol=o2_lance_mol,
        params=payload,
    )
    tar = tar_fate(
        primary.tar_atoms, temperature_C, wall_temperature_C, payload
    )

    gas = dict(primary.gas_mol)
    gas["CO2"] = max(
        0.0,
        float(gas.get("CO2", 0.0))
        - char.consumed_co2_mol
        + char.produced_co2_mol,
    )
    gas["CO"] = float(gas.get("CO", 0.0)) + char.produced_co_mol
    for species, mol in tar.cracked_gas_mol.items():
        gas[species] = float(gas.get(species, 0.0)) + mol
    gas = {k: v for k, v in gas.items() if v > 1e-18}

    final_char = char.surviving_char_mol + tar.coke_c_mol
    combined = _gas_record(
        gas,
        temperature_C=temperature_C,
        stage="combined",
        notes=list(primary.notes) + list(char.notes) + list(tar.notes),
    )

    out_atoms = _atom_dict({})
    for species, mol in gas.items():
        _add_atoms(out_atoms, _species_atoms(species, mol))
    out_atoms["C"] += final_char
    _add_atoms(out_atoms, tar.remaining_tar_atoms)
    _add_atoms(out_atoms, tar.coat_atoms)

    closure = {
        key: out_atoms.get(key, 0.0) - available.get(key, 0.0)
        for key in ("C", "H", "O", "N", "S")
    }
    closed = all(abs(delta) < 1e-9 for delta in closure.values())
    return OrganicsSourceResult(
        primary=primary,
        char=char,
        tar=tar,
        final_gas_mol=gas,
        final_char_c_mol=final_char,
        final_tar_atoms=dict(tar.remaining_tar_atoms),
        wall_tar_atoms=dict(tar.coat_atoms),
        evolved_gas_primary=primary.evolved_gas,
        evolved_gas_combined=combined,
        atom_closure=closure,
        closed=closed,
    )


def char_survival_surface(
    temperatures_C: Mapping[str, float] | list[float],
    pO2_grid: list[float],
    *,
    char_c_mol: float = 1.0,
    co2_mol: float = 0.25,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Probe helper: survive_fraction vs pO2 at several temperatures."""
    rows: list[dict[str, Any]] = []
    temps = (
        list(temperatures_C.values())
        if isinstance(temperatures_C, Mapping)
        else list(temperatures_C)
    )
    for t_c in temps:
        for p_o2 in pO2_grid:
            result = char_fate(
                char_c_mol,
                float(t_c),
                float(p_o2),
                co2_mol=co2_mol,
                invent_pO2_oxidant=True,
                params=params,
            )
            rows.append(
                {
                    "temperature_C": float(t_c),
                    "pO2_bar": float(p_o2),
                    "survive_fraction": result.survive_fraction,
                    "a_C_binding": result.a_C["a_C_binding"],
                    "boudouard_Kp": result.boudouard_Kp,
                    "boudouard_extent_mol": result.boudouard_extent_mol,
                    "pO2_extent_mol": result.pO2_extent_mol,
                    "produced_co_mol": result.produced_co_mol,
                }
            )
    return rows
