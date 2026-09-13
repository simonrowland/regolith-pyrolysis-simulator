"""Species-rail differential harness — fixture-fast, additive to the Gibbs pilot."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.gibbs_battery import (
    LEDGER_PATH as GIBBS_PILOT_LEDGER_PATH,
    GibbsPointScore,
    PIN_BAND_KJ_MOL,
    R_KJ_PER_MOL_K,
    LN10,
    RT_LN10_298_15_KJ,
    TYPED_REFUSAL_PREFIX,
)
from simulator.chemistry.ellingham_thermo import ellingham_fit_range_K
from simulator.diagnostic_helpers.species_rail import (
    MAJOR_MIN_FEEDSTOCKS,
    MINOR_MIN_FEEDSTOCKS,
    TIER_MAJOR,
    TIER_MINOR,
    TIER_TRACE,
    derive_species_rail,
)
from simulator.diagnostic_helpers.species_rail_differential import (
    CHANNEL_CEA_VS_ELLINGHAM,
    CHANNEL_NASA_CEA,
    ENVELOPE_BANDS,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PROSE,
    PHASE_SOLID,
    TEMPERATURE_BANDS,
    THERMOCHEMICAL_CALORIE_J,
    KeyedTablePoint,
    ScoredRailPoint,
    build_report,
    classify_phase_token,
    elemental_reference_mismatch_applies,
    elemental_reference_shift_kJ_per_mol_O2,
    engine_cea_delta_fG_kJ_mol,
    kcal_per_mol_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    render_report_markdown,
    resolve_cea_species,
    score_cea_point,
    score_channel_vs_channel,
    score_ellingham_point,
    table_self_check_residual,
    temperature_band_for,
    thin_points_for_ledger,
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


def _major_score(
    *,
    species: str,
    T_K: float,
    residual_kJ_mol: float,
    channel: str = CHANNEL_NASA_CEA,
) -> ScoredRailPoint:
    return ScoredRailPoint(
        score=GibbsPointScore(
            key=f"janaf::{species}:T={T_K}::{channel}",
            source_id="janaf",
            observation_id=species,
            species=species,
            provenance_class="independent_tabulation",
            comparison_quantity="delta_fG_kJ_mol",
            temperature_K=T_K,
            table_kJ_mol=0.0,
            engine_kJ_mol=residual_kJ_mol,
            residual_kJ_mol=residual_kJ_mol,
            residual_log10K=None,
            band_kJ_mol=PIN_BAND_KJ_MOL,
            status="mismatch",
            finding_class="compilation_disagreement",
            engine_channel=channel,
            cea_key=None,
            skip_reason=None,
        ),
        tier=TIER_MAJOR,
        compilation_id="janaf",
    )


def test_temperature_bands_split_envelope_from_plasma() -> None:
    assert temperature_band_for(1100.0).label == "<=1100"
    assert temperature_band_for(1100.01).label == "1100-1700"
    assert temperature_band_for(1700.0).label == "1100-1700"
    assert temperature_band_for(1700.01).label == "1700-2300"
    assert temperature_band_for(2300.0).label == "1700-2300"
    assert temperature_band_for(2300.01).label == "2300-2600"
    assert temperature_band_for(2600.0).label == "2300-2600"
    assert temperature_band_for(2600.01).label == ">2600"
    assert temperature_band_for(6000.0).label == ">2600"
    envelope_labels = [band.label for band in ENVELOPE_BANDS]
    assert envelope_labels == ["1100-1700", "1700-2300", "2300-2600"]
    assert [band.label for band in TEMPERATURE_BANDS[:1]] == ["<=1100"]
    assert TEMPERATURE_BANDS[-1].label == ">2600"


def test_headline_residual_tables_are_per_band_envelope_first() -> None:
    points = [
        _major_score(species="Si3", T_K=6000.0, residual_kJ_mol=-867.5),
        _major_score(species="Na2O", T_K=1600.0, residual_kJ_mol=-120.7),
        _major_score(species="MgO", T_K=2000.0, residual_kJ_mol=-116.6),
        _major_score(species="CaO", T_K=2500.0, residual_kJ_mol=-109.1),
        _major_score(species="FeO", T_K=900.0, residual_kJ_mol=-40.0),
    ]
    report = build_report(points)
    by_band = report["top20_major_residual_by_band"]
    assert [block["band"] for block in by_band] == [
        "1100-1700",
        "1700-2300",
        "2300-2600",
        "<=1100",
        ">2600",
    ]
    assert [block["envelope"] for block in by_band] == [
        True,
        True,
        True,
        False,
        False,
    ]
    species_by_band = {
        block["band"]: [row["species"] for row in block["rows"]]
        for block in by_band
    }
    assert species_by_band["1100-1700"] == ["Na2O"]
    assert species_by_band["1700-2300"] == ["MgO"]
    assert species_by_band["2300-2600"] == ["CaO"]
    assert species_by_band["<=1100"] == ["FeO"]
    assert species_by_band[">2600"] == ["Si3"]
    full_range = report["top20_major_residual"]
    assert [row["species"] for row in full_range] == [
        "Si3",
        "Na2O",
        "MgO",
        "CaO",
        "FeO",
    ]
    markdown = render_report_markdown(report)
    envelope_pos = markdown.index("### 1100-1700 K (envelope)")
    plasma_pos = markdown.index("### >2600 K (outside envelope)")
    full_pos = markdown.index(
        "## Twenty largest |residual| among MAJOR-tier points (full T range)"
    )
    assert envelope_pos < plasma_pos < full_pos
    assert "| Si3 | 6000.0 |" in markdown[full_pos:]


def test_ellingham_outside_species_fit_range_is_typed_refusal() -> None:
    low, high = ellingham_fit_range_K("Mg")
    assert (low, high) == (1100.0, 2600.0)
    outside = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Mg-008",
        formula="MgO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=2700.0,
        delta_fG_kJ_mol=-400.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    score = score_ellingham_point(outside)
    assert score is not None
    assert score.status == "typed-refusal"
    assert score.residual_kJ_mol is None
    assert score.skip_reason is not None
    assert score.skip_reason.startswith(
        f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:"
    )
    assert f"{low:g}-{high:g}K" in score.skip_reason
    cea = score_cea_point(outside)
    assert score_channel_vs_channel(outside, cea, score) is None

    inside = KeyedTablePoint(
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
    inside_score = score_ellingham_point(inside)
    assert inside_score is not None
    assert inside_score.status == "match"
    assert inside_score.residual_kJ_mol == pytest.approx(0.0, abs=0.05)


def test_ellingham_primary_refit_to_2600k_stays_scored() -> None:
    """Na ellingham_fit_range_K is (1100, 2600); 2500 K is in-range."""

    low, high = ellingham_fit_range_K("Na")
    assert (low, high) == (1100.0, 2600.0)
    hot = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Na-014",
        formula="Na2O",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=2500.0,
        delta_fG_kJ_mol=-50.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    score = score_ellingham_point(hot)
    assert score is not None
    assert score.status in {"match", "mismatch"}
    assert score.skip_reason is None


def test_channel_vs_channel_headline_is_legacy_fit_window() -> None:
    def _vs(species: str, T_K: float, residual: float) -> ScoredRailPoint:
        return ScoredRailPoint(
            score=GibbsPointScore(
                key=f"janaf::{species}:T={T_K}::{CHANNEL_CEA_VS_ELLINGHAM}",
                source_id="janaf",
                observation_id=species,
                species=species,
                provenance_class="independent_tabulation",
                comparison_quantity="delta_fG_kJ_per_mol_O2",
                temperature_K=T_K,
                table_kJ_mol=0.0,
                engine_kJ_mol=-residual,
                residual_kJ_mol=residual,
                residual_log10K=None,
                band_kJ_mol=PIN_BAND_KJ_MOL,
                status="mismatch",
                finding_class="channel_disagreement",
                engine_channel=CHANNEL_CEA_VS_ELLINGHAM,
                cea_key=None,
                skip_reason=None,
            ),
            tier=TIER_MAJOR,
            compilation_id="janaf",
        )

    report = build_report(
        [
            _vs("Na2O", 1600.0, -120.7),
            _vs("Na2O", 2300.0, -313.4),
            _vs("MgO", 1100.0, -5.0),
        ]
    )
    headline_T = [row["temperature_K"] for row in report["channel_vs_channel"]]
    assert headline_T == [1600.0, 1100.0]
    species_fit_T = [
        row["temperature_K"] for row in report["channel_vs_channel_species_fit"]
    ]
    assert species_fit_T == [2300.0, 1600.0, 1100.0]


def _refusal_point(
    *,
    record_id: str,
    T_K: float | None,
    channel: str,
    reason: str,
    formula: str = "XeO2",
) -> ScoredRailPoint:
    skip = reason if reason.startswith(TYPED_REFUSAL_PREFIX) else (
        f"{TYPED_REFUSAL_PREFIX}{reason}"
    )
    t_for_key = 0.0 if T_K is None else T_K
    return ScoredRailPoint(
        score=GibbsPointScore(
            key=f"janaf::{record_id}:T={t_for_key}::{channel}",
            source_id="janaf",
            observation_id=record_id,
            species=formula,
            provenance_class="independent_tabulation",
            comparison_quantity="delta_fG_kJ_mol",
            temperature_K=T_K,
            table_kJ_mol=None,
            engine_kJ_mol=None,
            residual_kJ_mol=None,
            residual_log10K=None,
            band_kJ_mol=PIN_BAND_KJ_MOL,
            status="typed-refusal",
            finding_class=None,
            engine_channel=channel,
            cea_key=None,
            skip_reason=skip,
        ),
        tier=TIER_TRACE,
        compilation_id="janaf",
    )


def test_ledger_aggregates_refusals_relationally(tmp_path: Path) -> None:
    scored_a = _major_score(species="SiO2", T_K=1500.0, residual_kJ_mol=2.0)
    scored_b = _major_score(species="SiO2", T_K=1600.0, residual_kJ_mol=3.0)
    refusals_same = [
        _refusal_point(
            record_id="X-001",
            T_K=T,
            channel=CHANNEL_NASA_CEA,
            reason="engine_channel_out_of_range:200-6000K",
        )
        for T in (200.0, 500.0, 800.0)
    ]
    refusal_other_reason = _refusal_point(
        record_id="X-001",
        T_K=900.0,
        channel=CHANNEL_NASA_CEA,
        reason="cea_formula_unmapped",
    )
    refusal_other_record = _refusal_point(
        record_id="X-002",
        T_K=200.0,
        channel=CHANNEL_NASA_CEA,
        reason="engine_channel_out_of_range:200-6000K",
    )
    raw = [
        scored_a,
        *refusals_same,
        scored_b,
        refusal_other_reason,
        refusal_other_record,
    ]
    thinned = thin_points_for_ledger(raw)
    scored_rows = [
        p for p in thinned if p.score.status in {"match", "mismatch"}
    ]
    refusal_rows = [p for p in thinned if p.score.status == "typed-refusal"]
    assert len(scored_rows) == 2
    assert len(refusal_rows) == 3
    assert sum(p.n_points or 0 for p in refusal_rows) == 5
    grouped = {
        (
            p.compilation_id,
            p.score.observation_id,
            p.score.engine_channel,
            p.score.skip_reason.split(":", 2)[1]
            if p.score.skip_reason
            else None,
        ): p
        for p in refusal_rows
    }
    same = grouped[("janaf", "X-001", CHANNEL_NASA_CEA, "engine_channel_out_of_range")]
    assert same.n_points == 3
    assert same.T_min_K == 200.0
    assert same.T_max_K == 800.0
    assert same.first_observation_id == "X-001"
    dest = tmp_path / "species_rail_differential_ledger.yaml"
    write_ledger(dest, points=raw)
    payload = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert payload["never_widen"] is True
    assert "The residual IS the result" in payload["doctrine"]
    assert "scoring_eligible" not in payload
    points = payload["points"]
    assert len(points) == 5
    refusal_payload = [row for row in points if row["status"] == "typed-refusal"]
    scored_payload = [row for row in points if row["status"] in {"match", "mismatch"}]
    assert len(scored_payload) == 2
    assert all("n_points" not in row for row in scored_payload)
    assert {row["n_points"] for row in refusal_payload} == {3, 1}
    assert sum(row["n_points"] for row in refusal_payload) == 5


def test_na2o_reference_shift_accounts_for_the_bulk_of_the_gap() -> None:
    """4 ΔG_vap(Na) at 1600 K is −140.10 kJ/mol O2; leftover is ~19 kJ."""

    T = 1600.0
    shift = elemental_reference_shift_kJ_per_mol_O2("Na2O", T)
    assert shift == pytest.approx(-140.10, abs=0.05)
    assert elemental_reference_mismatch_applies("Na2O", T) is True
    assert elemental_reference_mismatch_applies("MgO", 1100.0) is False
    assert elemental_reference_mismatch_applies("MgO", 1600.0) is True
    assert elemental_reference_mismatch_applies("K2O", 1600.0) is True

    na2o = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Na-014",
        formula="Na2O",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=T,
        delta_fG_kJ_mol=-200.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    cea = score_cea_point(na2o)
    ell = score_ellingham_point(na2o)
    assert cea.status in {"match", "mismatch"}
    assert ell is not None and ell.status in {"match", "mismatch"}
    vs = score_channel_vs_channel(na2o, cea, ell)
    assert vs is not None
    assert vs.finding_class == "elemental_reference_state_mismatch"
    leftover = float(vs.residual_kJ_mol) - float(shift)
    assert leftover == pytest.approx(19.4, abs=2.0)
    assert cea.finding_class == "elemental_reference_state_mismatch"

    mgo = KeyedTablePoint(
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
    mgo_ell = score_ellingham_point(mgo)
    mgo_cea = score_cea_point(mgo)
    assert mgo_ell is not None
    mgo_vs = score_channel_vs_channel(mgo, mgo_cea, mgo_ell)
    if mgo_vs is not None:
        assert mgo_vs.finding_class != "elemental_reference_state_mismatch"
