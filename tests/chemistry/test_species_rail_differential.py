"""Species-rail differential harness — fixture-fast, additive to the Gibbs pilot."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.gibbs_battery import (
    LEDGER_PATH as GIBBS_PILOT_LEDGER_PATH,
    R_KJ_PER_MOL_K,
    LN10,
    RT_LN10_298_15_KJ,
)
from simulator.diagnostic_helpers.species_rail import (
    MAJOR_MIN_FEEDSTOCKS,
    MINOR_MIN_FEEDSTOCKS,
    TIER_MAJOR,
    TIER_MINOR,
    TIER_TRACE,
    derive_species_rail,
)
from simulator.diagnostic_helpers.species_rail_differential import (
    CHANNEL_NASA_CEA,
    PHASE_GAS,
    PHASE_PROSE,
    PHASE_SOLID,
    THERMOCHEMICAL_CALORIE_J,
    KeyedTablePoint,
    classify_phase_token,
    engine_cea_delta_fG_kJ_mol,
    kcal_per_mol_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    resolve_cea_species,
    score_cea_point,
    score_ellingham_point,
    table_self_check_residual,
    write_ledger,
)
from simulator.reference_data.janaf import FEEDSTOCKS_PATH


def _pilot_ledger_digest() -> bytes:
    return hashlib.sha256(GIBBS_PILOT_LEDGER_PATH.read_bytes()).digest()


def test_rail_is_content_derived(tmp_path: Path) -> None:
    rail = derive_species_rail()
    assert "Xe" not in rail.elements
    assert "Re" not in rail.elements
    payload = yaml.safe_load(FEEDSTOCKS_PATH.read_text(encoding="utf-8"))
    first = next(name for name, row in payload.items() if isinstance(row, dict))
    composition = payload[first].setdefault("composition_wt_pct", {})
    composition["XeO2"] = 0.05
    mutated = tmp_path / "feedstocks.yaml"
    mutated.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    mutated_rail = derive_species_rail(mutated)
    assert "Xe" in mutated_rail.elements
    assert mutated_rail.in_rail("XeO2")
    assert not rail.in_rail("XeO2")


def test_out_of_rail_formula_is_not_scored() -> None:
    rail = derive_species_rail()
    assert not rail.in_rail("Re2O7")
    assert not rail.in_rail("Xe")
    assert rail.formula_tier("Re2O7") is None
    assert rail.formula_tier("Xe") is None
    # O2 is in-rail (O is carried by every oxide feedstock).
    assert rail.in_rail("O2")


def test_tier_follows_lowest_element() -> None:
    rail = derive_species_rail()
    assert rail.element_tier("Si") == TIER_MAJOR
    assert rail.element_tier("O") == TIER_MAJOR
    assert rail.formula_tier("SiO2") == TIER_MAJOR
    # Ag occupies the 6-feedstock cluster; Si is major. Lowest wins.
    assert rail.element_tier("Ag") == TIER_TRACE
    assert rail.formula_tier("Ag") == TIER_TRACE
    # A mixed major+trace formula is trace-tier.
    assert rail.formula_tier("Ag2SiO3") == TIER_TRACE
    # P occupancy sits in the minor band (gap 18 vs 15 / 7 vs 6).
    assert rail.element_tier("P") == TIER_MINOR
    assert rail.formula_tier("P2O5") == TIER_MINOR
    assert MAJOR_MIN_FEEDSTOCKS == 18
    assert MINOR_MIN_FEEDSTOCKS == 7


def test_unit_conversion_identities() -> None:
    """Thermochemical calorie, kcal→kJ, and log10 K = −dG/(R T ln 10)."""

    assert THERMOCHEMICAL_CALORIE_J == 4.184
    assert kcal_per_mol_to_kJ_per_mol(1.0) == pytest.approx(4.184)
    # Unit check: 80.700 kcal/mol × 4.184 kJ/kcal = 337.6488 kJ/mol.
    assert kcal_per_mol_to_kJ_per_mol(80.700) == pytest.approx(337.6488)
    # Sanity: ΔfG = 0 → log10 Kf = 0 at any T.
    assert log10K_from_delta_fG_kJ_mol(0.0, 298.15) == pytest.approx(0.0)
    # 1 dex at 298.15 K is R T ln 10 ≈ 5.708 kJ/mol.
    assert RT_LN10_298_15_KJ == pytest.approx(5.708, abs=0.002)
    assert log10K_from_delta_fG_kJ_mol(RT_LN10_298_15_KJ, 298.15) == pytest.approx(-1.0)
    # B689 AgS(g) 298.15 K self-check: printed log_k −59.154 vs recomputed
    # −337.6488 / (R T ln 10). Agreement to the 0.001 printed grain.
    recomputed = log10K_from_delta_fG_kJ_mol(kcal_per_mol_to_kJ_per_mol(80.700), 298.15)
    assert recomputed == pytest.approx(-59.154, abs=0.001)
    assert R_KJ_PER_MOL_K * 298.15 * LN10 == pytest.approx(RT_LN10_298_15_KJ)


def test_o2_identity_point_is_a_match() -> None:
    point = KeyedTablePoint(
        compilation_id="janaf",
        record_id="O-029",
        formula="O2",
        phase="ref",
        phase_kind="elemental_ref",
        T_K=298.15,
        delta_fG_kJ_mol=0.0,
        log10_Kf=0.0,
        log10_Kf_as_published="0.000",
        printed_page=None,
        note="JANAF O2(ref) identity",
    )
    resolved = resolve_cea_species("O2", "elemental_ref", 298.15)
    assert resolved.cea_key == "O2"
    engine = engine_cea_delta_fG_kJ_mol("O2", 298.15)
    assert engine == pytest.approx(0.0, abs=1e-9)
    score = score_cea_point(point)
    assert score.status == "match"
    assert score.engine_channel == CHANNEL_NASA_CEA
    assert score.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)
    assert score.provenance_class == "independent_tabulation"
    self_check = table_self_check_residual(point)
    assert self_check == pytest.approx(0.0, abs=1e-12)


def test_ellingham_matches_condensed_mgo_not_gas() -> None:
    """Ellingham is the condensed-oxide line; MgO(g) must not be rescaled onto it."""

    gas = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Mg-011",
        formula="MgO",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=1100.0,
        delta_fG_kJ_mol=-23.779,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    assert score_ellingham_point(gas) is None
    solid = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Mg-008",
        formula="MgO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1100.0,
        delta_fG_kJ_mol=-481.399,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    score = score_ellingham_point(solid)
    assert score is not None
    assert score.status == "match"
    assert score.residual_kJ_mol == pytest.approx(0.0, abs=0.05)


def test_prose_phase_is_typed_refusal_not_a_guess() -> None:
    assert classify_phase_token("g") == PHASE_GAS
    assert classify_phase_token("cr") == "solid"
    assert classify_phase_token("hexagonal") == PHASE_PROSE
    assert classify_phase_token("orpiment,l") == PHASE_PROSE
    assert classify_phase_token("red, black") == PHASE_PROSE
    assert classify_phase_token("cubic") == PHASE_PROSE
    assert classify_phase_token("vitreous,l") == PHASE_PROSE
    assert classify_phase_token("cr,l") == "mixed"
    # Never map a prose token onto gas/solid/liquid.
    assert classify_phase_token("solid, hexagonal") == PHASE_PROSE


def test_pilot_ledger_is_byte_identical_after_write(tmp_path: Path) -> None:
    before = _pilot_ledger_digest()
    before_bytes = GIBBS_PILOT_LEDGER_PATH.read_bytes()
    dest = tmp_path / "species_rail_differential_ledger.yaml"
    write_ledger(dest, points=[])
    after = _pilot_ledger_digest()
    assert after == before
    assert GIBBS_PILOT_LEDGER_PATH.read_bytes() == before_bytes
    written = dest.read_text(encoding="utf-8")
    assert "never_widen: true" in written
    payload = yaml.safe_load(written)
    assert "scoring_eligible" not in payload
    assert not any(
        "scoring_eligible" in (point or {})
        for point in payload.get("points") or []
    )
    assert payload["never_widen"] is True
    with pytest.raises(ValueError, match="gibbs-battery pilot ledger"):
        write_ledger(GIBBS_PILOT_LEDGER_PATH, points=[])
    assert GIBBS_PILOT_LEDGER_PATH.read_bytes() == before_bytes
