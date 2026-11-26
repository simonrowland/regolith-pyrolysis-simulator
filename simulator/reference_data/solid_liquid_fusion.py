"""Solid-to-liquid ΔG_fus when JANAF does not cover the phase or the temperature.

One owner. JANAF stays first: ``janaf_fusion_energy`` is called, and a
successful return means the caller keeps today's JANAF path. Only a phase or
temperature gap walks the located Gibbs functions, in order IVTAN, Barin,
Robie & Hemingway. The first function that covers the oxide and T wins.
There is no second activity formula here. The scorer still applies
a_l = a_s * exp(-ΔG_fus/RT) with ΔG_fus = G_liquid - G_solid.

Barin 1995 (Thermochemical Data of Pure Substances), printed page 1046,
PDF page 1162 of barin-1995.pdf, formula weight 70.937. The solid and liquid
rows at 2115 K print the same G (-629.423 kJ/mol) and the same ΔfG
(-221.063 kJ/mol), so the printed ΔG_fus at the fusion temperature is 0.
The fusion jump on those rows is ΔH = 43.932 kJ/mol
(-241.363 - -285.295) and ΔS = 20.772 J/(mol·K) (183.480 - 162.708).
2115 K * 20.772 J/(mol·K) is 43.933 kJ/mol, one print quantum above ΔH.

Solid Cp is the printed constant 59.120 J/(mol·K) from 1600 K through
2115 K. It is not constant from 298.15 K (printed Cp there is 44.102 and
still rising at 1500 K), so this function does not claim that lower band.
Liquid Cp is the printed estimate 60.668 J/(mol·K) (Barin's flag ``e``)
from the first liquid node 2115 K through 2500 K. Below 2115 K the liquid
leg holds that Cp. That continuation is extrapolated, not a certified
liquid table. Absolute G uses the printed H and S at 2115 K, the same
H298 reference for both phases:

    H(T) = H(2115) + Cp * (T - 2115) / 1000          kJ/mol
    S(T) = S(2115) + Cp * ln(T / 2115)               J/(mol·K)
    G(T) = H(T) - T * S(T) / 1000                    kJ/mol
    ΔG_fus(T) = G_liquid(T) - G_solid(T)             kJ/mol

The scorer multiplies by 1000 and uses joules. Equal chemical potential,
μ = G° + RT ln(a), gives a_l = a_s * exp(-ΔG_fus/RT), so a positive
ΔG_fus (solid more stable below the fusion temperature) lowers the
liquid-reference activity. log10(a_l/a_s) = -ΔG_fus / (RT ln 10).
At 1673.15 K this reconstruction gives ΔG_fus = 9.100 kJ/mol, a shift of
-0.284 dex. No printed Turkdogan MnO solid, liquid, or ΔG_fus at 1673 K
was located in the corpus or in the OpenIMCC 66ec4a3 provenance. OpenIMCC's
own Gurvich liquid construction quotes a 0.22 dex fusion term; that figure
is a comparison target and is not copied into these anchors.

IVTAN's extracted table is MnO(g) only (table 1436). Robie & Hemingway
1995 USGS B2131 page 199 is manganosite crystals, 298.15-1800 K, with no
liquid branch. Neither supplies a solid-plus-liquid Gibbs function.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from simulator.reference_data.janaf import janaf_fusion_energy
from simulator.yaml_cache import load_cached_safe_yaml

ROOT = Path(__file__).resolve().parents[2]
_IVTAN_EXTRACT = ROOT / "data" / "literature" / "extracts" / "ivtan-mno-coo-thermo.yaml"
_ROBIE_MNO_RECORD = (
    ROOT
    / "data"
    / "literature"
    / "compilations"
    / "robie-hemingway-1995-usgs-b2131"
    / "records"
    / "high-temperature-p199.json"
)

# Printed MnO anchors, Barin 1995 p. 1046. H in kJ/mol, S and Cp in J/(mol·K).
_BARIN_TM_K = Decimal("2115")
_BARIN_SOLID_CP = Decimal("59.120")
_BARIN_SOLID_H = Decimal("-285.295")
_BARIN_SOLID_S = Decimal("162.708")
_BARIN_LIQUID_CP = Decimal("60.668")
_BARIN_LIQUID_H = Decimal("-241.363")
_BARIN_LIQUID_S = Decimal("183.480")
_BARIN_LO_K = Decimal("1600")
_BARIN_HI_K = Decimal("2500")
_BARIN_CERTIFIED_BAND = (
    "solid constant-Cp 1600-2115 K; "
    "liquid tabulated 2115-2500 K; "
    "liquid below 2115 K continues the printed Cp"
)
_BARIN_ANCHOR_TEXT = (
    "Barin 1995 printed p.1046 MnO fw=70.937 "
    "Tm=2115 "
    "solid Cp=59.120 H=-285.295 S=162.708 "
    "liquid Cp=60.668 H=-241.363 S=183.480 "
    "band solid 1600-2115 liquid 2115-2500"
)
_BARIN_ANCHOR_SHA = hashlib.sha256(_BARIN_ANCHOR_TEXT.encode("utf-8")).hexdigest()

# Narrower than the scorer's missing-input list on purpose. A hash or cache
# mismatch is not a licence to substitute another compilation.
_JANAF_PHASE_OR_TEMPERATURE_GAPS = (
    "no JANAF fusion table pair",
    "outside the JANAF table range",
    "is not bracketed by JANAF table rows",
    "needed JANAF formation Gibbs row missing",
    "JANAF crystal/liquid tables do not overlap",
    "JANAF tables have fewer than two complete overlap nodes",
)

_SOLID_PHASES = frozenset({"solid", "crystal", "cr", "condensed"})
_LIQUID_PHASES = frozenset({"liquid", "l", "melt"})
_PHASE_ANCHORS = {
    "solid": (_BARIN_SOLID_CP, _BARIN_SOLID_H, _BARIN_SOLID_S),
    "liquid": (_BARIN_LIQUID_CP, _BARIN_LIQUID_H, _BARIN_LIQUID_S),
}


@dataclass(frozen=True)
class LocatedFusionEnergy:
    """ΔG_fus from a non-JANAF solid/liquid pair.

    The attribute names the scorer already reads off a JANAF fusion result
    are repeated so the activity arithmetic stays one formula. ``source_name``,
    ``authority`` and ``certified_band`` are the typed notice. ``authority``
    is the Authority token string; the scorer maps it onto the enum.
    """

    delta_g_fus_kJ_per_mol: Decimal
    melting_temperature_K: Decimal
    accepted_melting_temperature_K: Decimal
    crystal_table: str
    liquid_table: str
    source_sha256: tuple[str, str]
    source_name: str
    authority: str
    certified_band: str


def located_fusion_when_janaf_does_not_cover(
    oxide: str, temperature_K: Decimal
) -> LocatedFusionEnergy | None:
    """Located ΔG_fus, or None when JANAF covers the phase and T.

    None is also the answer when JANAF has a gap and no located solid and
    liquid function covers that oxide at T. A ValueError that is not a
    phase or temperature gap is re-raised: those are corrupt JANAF inputs,
    not a missing phase.
    """

    temperature_K = Decimal(str(temperature_K))
    try:
        janaf_fusion_energy(oxide, temperature_K)
    except ValueError as exc:
        if not any(marker in str(exc) for marker in _JANAF_PHASE_OR_TEMPERATURE_GAPS):
            raise
    else:
        return None
    return _first_located_solid_liquid(oxide, temperature_K)


def _first_located_solid_liquid(
    oxide: str, temperature_K: Decimal
) -> LocatedFusionEnergy | None:
    """IVTAN, then Barin, then Robie. Looked up at call time so a test can patch it."""

    for source in (
        _ivtan_solid_liquid,
        _barin_mno_solid_liquid,
        _robie_hemingway_solid_liquid,
    ):
        found = source(oxide, temperature_K)
        if found is not None:
            return found
    return None


def barin_mno_gibbs_kJ_per_mol(phase: str, temperature_K: Decimal) -> Decimal | None:
    """Absolute G (kJ/mol) from the printed 2115 K H and S anchors.

    ``phase`` is ``solid`` or ``liquid``. None outside 1600-2500 K, where
    holding the 2115 K Cp would not be the printed heat capacity.
    """

    anchor = _PHASE_ANCHORS.get(phase)
    if anchor is None:
        raise ValueError(f"Barin MnO phase must be solid or liquid, not {phase!r}")
    temperature_K = Decimal(str(temperature_K))
    if temperature_K < _BARIN_LO_K or temperature_K > _BARIN_HI_K:
        return None
    heat_capacity, enthalpy_tm, entropy_tm = anchor
    enthalpy = enthalpy_tm + heat_capacity * (temperature_K - _BARIN_TM_K) / Decimal(1000)
    entropy = entropy_tm + heat_capacity * (temperature_K / _BARIN_TM_K).ln()
    return enthalpy - temperature_K * entropy / Decimal(1000)


def _barin_mno_solid_liquid(
    oxide: str, temperature_K: Decimal
) -> LocatedFusionEnergy | None:
    if oxide != "MnO":
        return None
    solid = barin_mno_gibbs_kJ_per_mol("solid", temperature_K)
    liquid = barin_mno_gibbs_kJ_per_mol("liquid", temperature_K)
    if solid is None or liquid is None:
        return None
    return LocatedFusionEnergy(
        delta_g_fus_kJ_per_mol=liquid - solid,
        melting_temperature_K=_BARIN_TM_K,
        accepted_melting_temperature_K=_BARIN_TM_K,
        crystal_table="barin-1995 p.1046 MnO(s)",
        liquid_table="barin-1995 p.1046 MnO(l)",
        source_sha256=(_BARIN_ANCHOR_SHA, _BARIN_ANCHOR_SHA),
        source_name="Barin 1995",
        authority="extrapolated",
        certified_band=_BARIN_CERTIFIED_BAND,
    )


@lru_cache(maxsize=1)
def _ivtan_document() -> Mapping[str, object] | None:
    if not _IVTAN_EXTRACT.is_file():
        return None
    document = load_cached_safe_yaml(_IVTAN_EXTRACT.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        return None
    return document


def _ivtan_solid_liquid(
    oxide: str, temperature_K: Decimal
) -> LocatedFusionEnergy | None:
    """IVTAN solid+liquid Gibbs, or None when the extract has no such pair.

    The extracted MnO row is gas (table 1436). A printed solid and liquid
    pair with no evaluator in this module is a hard error: skipping it would
    silently prefer the next source.
    """

    document = _ivtan_document()
    if document is None:
        return None
    species_block = document.get("species")
    if not isinstance(species_block, Mapping):
        return None
    species = species_block.get(oxide)
    if not isinstance(species, Mapping):
        return None
    observations = species.get("observations")
    if not isinstance(observations, list):
        return None
    solid = False
    liquid = False
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        if str(observation.get("type") or "") != "gibbs_table":
            continue
        phase = str(observation.get("phase") or "").strip().lower()
        if phase in _SOLID_PHASES:
            solid = True
        elif phase in _LIQUID_PHASES:
            liquid = True
    if not (solid and liquid):
        return None
    raise ValueError(
        f"IVTAN extract prints solid and liquid Gibbs for {oxide} at "
        f"{temperature_K} K but this owner has no evaluator for that table"
    )


@lru_cache(maxsize=1)
def _robie_mno_phase_text() -> str | None:
    if not _ROBIE_MNO_RECORD.is_file():
        return None
    document = json.loads(_ROBIE_MNO_RECORD.read_text(encoding="utf-8"))
    if str(document.get("formula_as_published") or "") != "MnO":
        return None
    return str(document.get("phase_as_published") or "")


def _robie_hemingway_solid_liquid(
    oxide: str, temperature_K: Decimal
) -> LocatedFusionEnergy | None:
    """Robie & Hemingway manganosite. Crystals only: no liquid leg, so None.

    Other oxides are not scanned across the bulletin. This record is the
    one MnO high-temperature table.
    """

    if oxide != "MnO":
        return None
    phase = _robie_mno_phase_text()
    if phase is None:
        return None
    if "liquid" not in phase.lower():
        return None
    raise ValueError(
        "Robie & Hemingway 1995 prints a liquid MnO Gibbs function at "
        f"{temperature_K} K but this owner has no evaluator for that table"
    )
