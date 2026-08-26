"""Gibbs / DfG thermochemical battery — fixture-fast, additive to mass-spec."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.gibbs_battery import (
    CEA_EXTRACT_PATH,
    INDEPENDENT_AGREEMENT_BAND_KJ_MOL,
    LEDGER_PATH,
    LN10,
    LOG10K_PER_KJ_298_15,
    PILOT_CHANNELS,
    PILOT_INDEPENDENT_SPECIES,
    PROVENANCE_ENGINE_OWN_INPUT,
    PROVENANCE_INDEPENDENT,
    R_KJ_PER_MOL_K,
    RT_LN10_298_15_KJ,
    TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL,
    TYPED_REFUSAL_PREFIX,
    UnmappedPilotSpeciesError,
    cea_polynomial,
    comparable_scores,
    engine_delta_fG_kJ_mol,
    ledger_pins_by_key,
    load_ledger,
    partition_gibbs_tables,
    residual_log10K_from_kJ,
    resolve_pilot_channel,
    score_pilot,
    score_point,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MASS_SPEC_LEDGER = (
    Path(__file__).resolve().parent / "extract_store_reproduction_residual_baselines.yaml"
)


def test_units_derivation_sanity_kj_and_logk() -> None:
    """1 kJ/mol at 298.15 K is 0.1752 dex; R T ln10 at 298.15 is 5.708 kJ/mol."""
    assert RT_LN10_298_15_KJ == pytest.approx(5.708, abs=0.002)
    assert LOG10K_PER_KJ_298_15 == pytest.approx(0.1752, abs=0.0002)
    # Algebra: residual_log10K = −Δ(ΔfG) / (R T ln 10).
    dex = residual_log10K_from_kJ(1.0, 298.15)
    assert dex == pytest.approx(-1.0 / RT_LN10_298_15_KJ, rel=1e-12)
    assert dex == pytest.approx(-0.1752, abs=0.0002)
    # Unit check: R * T * ln10 has units kJ/mol when R is kJ/(mol·K).
    assert R_KJ_PER_MOL_K * 298.15 * LN10 == pytest.approx(RT_LN10_298_15_KJ)
    # Sanity species: O2 formation identity is 0 kJ → 0 dex.
    channel = resolve_pilot_channel("O2")
    dfg = engine_delta_fG_kJ_mol(channel, 298.15)
    assert dfg == pytest.approx(0.0, abs=1e-9)
    assert residual_log10K_from_kJ(dfg, 298.15) == pytest.approx(0.0, abs=1e-12)


def test_pilot_id_map_is_exact_and_never_casefolds() -> None:
    for species_id, channel in PILOT_CHANNELS.items():
        assert resolve_pilot_channel(species_id) is channel
        assert channel.cea_key == species_id or channel.cea_key in {
            "PO",
            "PO2",
            "P2",
            "P4",
            "P4O6",
            "O2",
        }
    with pytest.raises(UnmappedPilotSpeciesError, match="not in the pilot hand-map"):
        resolve_pilot_channel("po")
    with pytest.raises(UnmappedPilotSpeciesError, match="CO ≠ Co"):
        resolve_pilot_channel("CO")
    with pytest.raises(UnmappedPilotSpeciesError, match="CO ≠ Co"):
        resolve_pilot_channel("Co")
    # CEA extract itself distinguishes CO (carbon monoxide) from Co (cobalt).
    carbon_monoxide = cea_polynomial("CO")
    cobalt = cea_polynomial("Co")
    assert carbon_monoxide.name == "CO"
    assert cobalt.name == "Co"
    assert carbon_monoxide.formula != cobalt.formula
    with pytest.raises(UnmappedPilotSpeciesError, match="refusing to case-fold"):
        cea_polynomial("co")
    with pytest.raises(UnmappedPilotSpeciesError, match="AL ≠ Al"):
        cea_polynomial("Al")


def test_o2_own_input_identity_is_transcription_match() -> None:
    score = score_point(
        source_id="nasa-cea-thermo",
        observation_id="cea_O2_gibbs",
        species="O2",
        provenance_class=PROVENANCE_ENGINE_OWN_INPUT,
        T_K=298.15,
        table_kJ_mol=0.0,
        extra_note="fixture identity",
    )
    assert score.status == "match"
    assert score.provenance_class == PROVENANCE_ENGINE_OWN_INPUT
    assert score.finding_class == "transcription_ok"
    assert score.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)
    assert abs(score.residual_kJ_mol) <= TRANSCRIPTION_AGREEMENT_BAND_KJ_MOL


def test_po_298_is_typed_refusal_not_extrapolation() -> None:
    score = score_point(
        source_id="janaf-4th",
        observation_id="janaf_PO_298_anchors",
        species="PO",
        provenance_class=PROVENANCE_INDEPENDENT,
        T_K=298.15,
        table_kJ_mol=-47.139,
    )
    assert score.status == "typed-refusal"
    assert score.skip_reason is not None
    assert score.skip_reason.startswith(f"{TYPED_REFUSAL_PREFIX}T_outside_engine_domain")
    assert score.residual_kJ_mol is None
    assert score.engine_kJ_mol is None
    assert score.provenance_class == PROVENANCE_INDEPENDENT


def test_po_500k_independent_compilation_disagreement() -> None:
    """JANAF PO vs CEA/Gurvich: ~4.3 kJ at 500 K is a physics finding."""
    score = score_point(
        source_id="janaf-4th",
        observation_id="JANAF1998_PO_formation_tabulation",
        species="PO",
        provenance_class=PROVENANCE_INDEPENDENT,
        T_K=500.0,
        table_kJ_mol=-62.24,
    )
    assert score.status == "mismatch"
    assert score.provenance_class == PROVENANCE_INDEPENDENT
    assert score.finding_class == "compilation_disagreement"
    assert score.residual_kJ_mol is not None
    assert abs(score.residual_kJ_mol) > INDEPENDENT_AGREEMENT_BAND_KJ_MOL
    # Gurvich CEA is more negative by ~4.3 kJ (ΔfH offset −27.86 vs −23.55).
    assert score.residual_kJ_mol == pytest.approx(-4.295, abs=0.02)


def test_p2_500k_independent_agreement() -> None:
    score = score_point(
        source_id="janaf-4th",
        observation_id="JANAF1998_P2_formation_tabulation",
        species="P2",
        provenance_class=PROVENANCE_INDEPENDENT,
        T_K=500.0,
        table_kJ_mol=77.480,
    )
    assert score.status == "match"
    assert score.finding_class == "compilation_agreement"
    assert score.residual_kJ_mol is not None
    assert abs(score.residual_kJ_mol) <= INDEPENDENT_AGREEMENT_BAND_KJ_MOL


def test_mass_spec_battery_still_skips_non_kems_gibbs() -> None:
    """The kems- skip rule is load-bearing and must not be dropped.

    Source-level pin: this battery is additive. Do not walk the adopted
    observation set here — that re-enters the mass-spec harness and
    contends with its 277 s module fixture under xdist.
    """
    src = (
        REPO_ROOT / "simulator" / "diagnostic_helpers" / "extract_reproduction.py"
    ).read_text(encoding="utf-8")
    assert 'KEMS_SOURCE_PREFIX = "kems-"' in src
    assert 'if otype == "gibbs_table" and not is_mass_spec:' in src
    assert "Pulling every priority-winner thermochemical table" in src


def test_partition_splits_own_input_from_independent() -> None:
    part = partition_gibbs_tables()
    # 2026-08-26 corpus integration: the store grew (Sossi remine added Table-5
    # thermochemical rows as kems gibbs coverage; metadata completion added
    # observations). The non-kems partition — the battery's subject — did NOT
    # move: 1690 = 1617 own-input + 73 independent, unchanged.
    assert part["observations_total"] == 2032
    assert part["gibbs_table_total"] == 1714
    assert part["gibbs_table_kems"] == 24
    assert part["gibbs_table_non_kems"] == 1690
    assert part["engine_own_input"] + part["independent_tabulation"] == 1690
    assert part["engine_own_input"] == 1617
    assert part["independent_tabulation"] == 73
    assert part["by_source"]["nasa-cea-thermo"]["n"] == 1615
    assert part["by_source"]["nasa-cea-thermo"]["provenance_class"] == (
        PROVENANCE_ENGINE_OWN_INPUT
    )
    assert part["by_source"]["janaf-4th"]["provenance_class"] == PROVENANCE_INDEPENDENT
    assert part["by_source"]["ivtan-mno-coo-thermo"]["provenance_class"] == (
        PROVENANCE_ENGINE_OWN_INPUT
    )
    for row in part["rows"]:
        assert row["provenance_class"] in {
            PROVENANCE_ENGINE_OWN_INPUT,
            PROVENANCE_INDEPENDENT,
        }
        assert not row["is_kems"]


def test_pilot_ledger_pins_every_comparable_point() -> None:
    scores = score_pilot()
    comparable = comparable_scores(scores)
    assert comparable, "pilot produced no comparable ΔfG points"
    assert {s.species for s in comparable} >= set(PILOT_INDEPENDENT_SPECIES) | {"O2"}
    assert any(s.provenance_class == PROVENANCE_ENGINE_OWN_INPUT for s in comparable)
    assert any(s.provenance_class == PROVENANCE_INDEPENDENT for s in comparable)
    # Both finding kinds exist: P2 agreement vs PO/P4O6 disagreement.
    findings = {s.finding_class for s in comparable}
    assert "compilation_disagreement" in findings
    assert "transcription_ok" in findings

    ledger = load_ledger()
    assert ledger["battery"] == "gibbs_thermochemistry"
    assert ledger["metric"] == "residual_kJ_mol"
    assert ledger["never_widen"] is True
    assert ledger["pin_edit_policy"] == "mechanism-comment-required"
    pins = ledger_pins_by_key(ledger)
    live_keys = {s.key for s in comparable}
    assert live_keys == set(pins), (
        f"ledger/live mismatch live_only={sorted(live_keys - set(pins))} "
        f"pin_only={sorted(set(pins) - live_keys)}"
    )
    for score in comparable:
        pin = pins[score.key]
        assert pin["provenance_class"] == score.provenance_class
        assert abs(float(pin["residual_kJ_mol"]) - float(score.residual_kJ_mol)) <= float(
            pin["band_kJ_mol"]
        )
        assert pin["status"] == score.status
        assert pin["finding_class"] == score.finding_class


def test_gibbs_ledger_is_not_the_mass_spec_ledger() -> None:
    assert LEDGER_PATH.resolve() != MASS_SPEC_LEDGER.resolve()
    mass = yaml.safe_load(MASS_SPEC_LEDGER.read_text(encoding="utf-8"))
    gibbs = load_ledger()
    assert mass.get("metric") == "residual_dex"
    assert gibbs.get("metric") == "residual_kJ_mol"
    assert CEA_EXTRACT_PATH.is_file()


def test_p4o6_expected_disagreement_is_a_physics_finding() -> None:
    score = score_point(
        source_id="janaf-4th",
        observation_id="JANAF1998_P4O6_formation_tabulation_UNCERTAIN",
        species="P4O6",
        provenance_class=PROVENANCE_INDEPENDENT,
        T_K=500.0,
        table_kJ_mol=-1993.955,
    )
    assert score.status == "mismatch"
    assert score.provenance_class == PROVENANCE_INDEPENDENT
    assert score.finding_class == "compilation_disagreement"
    assert score.residual_kJ_mol is not None
    # JANAF UNCERTAIN vs Gurvich/CEA: ~600 kJ, not a transcription error.
    assert score.residual_kJ_mol == pytest.approx(602.7, abs=1.0)
