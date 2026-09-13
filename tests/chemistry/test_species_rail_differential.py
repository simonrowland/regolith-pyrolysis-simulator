"""Species-rail differential harness — fixture-fast, additive to the Gibbs pilot."""

from __future__ import annotations

import hashlib
import math
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
    AL_MELTING_K,
    CHANNEL_CEA_VS_ELLINGHAM,
    CHANNEL_NASA_CEA,
    CHANNEL_VAPOUR_RAIL_PSAT,
    FINDING_ANTOINE_EXTRAPOLATED_BEYOND_FIT,
    FINDING_CONDENSED_ROW_PAST_TRANSITION,
    JANAF_STANDARD_PRESSURE_PA,
    MG_NBP_K,
    NA_NBP_K,
    QUANTITY_LOG10_PSAT,
    SI_MELTING_K,
    cea_by_formula,
    _evaluate_rail_pressure_Pa,
    _pick_condensed_psat,
    _sidecar_valid_range_K,
    psat_finding_class,
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
    ellingham_line_product_oxide,
    ellingham_line_product_phase_kind,
    ellingham_oxide_stoichiometry_for_formula,
    oxide_identity_mismatch_applies,
    engine_cea_delta_fG_kJ_mol,
    kcal_per_mol_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    log10_psat_over_P0_from_dfg,
    render_report_markdown,
    resolve_cea_species,
    resolve_ellingham_oxide,
    resolve_rail_species_id,
    score_cea_point,
    score_channel_vs_channel,
    score_ellingham_point,
    score_psat_channel,
    score_psat_nbp_sanity,
    score_psat_pair,
    UNAVAILABLE_CHANNELS,
    score_rail,
    score_table_self_check,
    table_self_check_residual,
    temperature_band_for,
    thin_points_for_ledger,
    write_ledger,
    _count_matrix,
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


def test_cea_mno_and_coo_remain_unmapped() -> None:
    """CEA extract has no MnO/CoO condensed records (G9). Never case-fold Co→CO."""

    index = cea_by_formula()
    assert "MnO" not in index
    assert "CoO" not in index
    for spelling in (
        "MnO(a)",
        "CoO(cr)",
        "MNO",
        "COO",
        "Mn1O1",
        "Co1O1",
    ):
        assert spelling not in index
    for formula in ("MnO", "CoO"):
        for T_K in (298.15, 1100.0, 1500.0):
            resolved = resolve_cea_species(formula, PHASE_SOLID, T_K)
            assert resolved.cea_key is None
            assert resolved.reason == "cea_formula_unmapped"
            point = KeyedTablePoint(
                compilation_id="janaf",
                record_id=f"{formula}-absent",
                formula=formula,
                phase="cr",
                phase_kind=PHASE_SOLID,
                T_K=T_K,
                delta_fG_kJ_mol=-200.0,
                log10_Kf=None,
                log10_Kf_as_published=None,
                printed_page=None,
            )
            score = score_cea_point(point)
            assert score.status == "typed-refusal"
            assert score.residual_kJ_mol is None
            assert score.skip_reason == (
                f"{TYPED_REFUSAL_PREFIX}cea_formula_unmapped"
            )
    # CO is carbon monoxide; Co is cobalt. Exact-key only.
    co_gas = resolve_cea_species("CO", PHASE_GAS, 298.15)
    assert co_gas.cea_key == "CO"
    co_metal = resolve_cea_species("Co", PHASE_SOLID, 1100.0)
    assert co_metal.cea_key == "Co_b"


def _o2_identity_point(*, log10_Kf: float, as_published: str) -> KeyedTablePoint:
    return KeyedTablePoint(
        compilation_id="janaf",
        record_id="O-029",
        formula="O2",
        phase="ref",
        phase_kind="elemental_ref",
        T_K=298.15,
        delta_fG_kJ_mol=0.0,
        log10_Kf=log10_Kf,
        log10_Kf_as_published=as_published,
        printed_page=None,
        note="JANAF O2(ref) identity",
    )


def test_o2_identity_point_is_a_match() -> None:
    point = _o2_identity_point(log10_Kf=0.0, as_published="0.000")
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


def test_inconsistent_table_logk_does_not_admit_cea_comparison(monkeypatch) -> None:
    """M01: ΔfG=0 with log10 Kf=−59.154 is a self-check mismatch, not a CEA match."""

    point = _o2_identity_point(log10_Kf=-59.154, as_published="-59.154")
    self_check = score_table_self_check(point)
    assert self_check is not None
    assert self_check.status == "mismatch"
    assert self_check.residual_log10K == pytest.approx(-59.154)
    assert self_check.finding_class == "compilation_table_self_check"
    cea_raw = score_cea_point(point)
    assert cea_raw.status == "match"
    assert cea_raw.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.species_rail_differential.iter_source_items",
        lambda rail: [point],
    )
    scored = score_rail()
    cea = [p for p in scored if p.score.engine_channel == CHANNEL_NASA_CEA]
    assert len(cea) == 1
    assert cea[0].score.status == "typed-refusal"
    assert cea[0].score.skip_reason == (
        f"{TYPED_REFUSAL_PREFIX}payload_not_comparable"
    )
    assert cea[0].score.finding_class == "compilation_table_self_check"
    assert cea[0].score.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)
    sc_rows = [
        p for p in scored if p.score.engine_channel == "table_self_check"
    ]
    assert len(sc_rows) == 1
    assert sc_rows[0].score.status == "mismatch"
    matrix = _count_matrix(scored)
    assert not any(
        row["channel"] == CHANNEL_NASA_CEA and row["status"] == "match"
        for row in matrix
    )
    assert any(
        row["channel"] == "table_self_check" and row["status"] == "mismatch"
        for row in matrix
    )


def test_consistent_o2_identity_still_cea_matches_through_score_rail(
    monkeypatch,
) -> None:
    """M01 control: consistent O2 (ΔfG=0, log10 Kf=0) remains a CEA match."""

    point = _o2_identity_point(log10_Kf=0.0, as_published="0.000")
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.species_rail_differential.iter_source_items",
        lambda rail: [point],
    )
    scored = score_rail()
    cea = [p for p in scored if p.score.engine_channel == CHANNEL_NASA_CEA]
    assert len(cea) == 1
    assert cea[0].score.status == "match"
    assert cea[0].score.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)
    assert cea[0].score.skip_reason is None
    assert not any(
        p.score.engine_channel == "table_self_check" for p in scored
    )


def test_unprobed_channels_do_not_claim_import_failure(monkeypatch) -> None:
    """M14: vaporock/thermoengine/melts are not-probed, not 'not importable'."""

    point = _o2_identity_point(log10_Kf=0.0, as_published="0.000")
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.species_rail_differential.iter_source_items",
        lambda rail: [point],
    )
    scored = score_rail()
    holes = [
        p for p in scored if p.score.engine_channel in UNAVAILABLE_CHANNELS
    ]
    assert {p.score.engine_channel for p in holes} == set(UNAVAILABLE_CHANNELS)
    assert len(holes) == 3
    for row in holes:
        assert row.score.status == "typed-refusal"
        assert row.score.skip_reason == f"{TYPED_REFUSAL_PREFIX}not_probed"
        note = (row.score.note or "").lower()
        assert "not importable" not in note
        assert "not probed" in note
        assert "unavailable" not in note or "attempted-unavailable" in note


def test_cea_o2_still_scores_alongside_unprobed_channels(monkeypatch) -> None:
    """M14 control: CEA O2 comparison still succeeds while holes are reported."""

    point = _o2_identity_point(log10_Kf=0.0, as_published="0.000")
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.species_rail_differential.iter_source_items",
        lambda rail: [point],
    )
    scored = score_rail()
    cea = [p for p in scored if p.score.engine_channel == CHANNEL_NASA_CEA]
    assert len(cea) == 1
    assert cea[0].score.status == "match"
    assert cea[0].score.residual_kJ_mol == pytest.approx(0.0, abs=1e-9)
    assert any(
        p.score.engine_channel in UNAVAILABLE_CHANNELS
        and p.score.status == "typed-refusal"
        for p in scored
    )


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
    assert resolve_ellingham_oxide("MgO") == ("Mg", 1.0, 1.0)
    assert "OXIDE_TO_METAL['MgO'] → Mg" in (score.note or "")


def test_ellingham_coo_is_unsupported() -> None:
    """CoO maps to Co; Co has no Ellingham segment (typed refusal, not a zero)."""

    assert resolve_ellingham_oxide("CoO") == ("Co", 1.0, 1.0)
    point = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Co-oxide",
        formula="CoO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1100.0,
        delta_fG_kJ_mol=-200.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    score = score_ellingham_point(point)
    assert score is not None
    assert score.status == "typed-refusal"
    assert score.residual_kJ_mol is None
    assert score.skip_reason == f"{TYPED_REFUSAL_PREFIX}ellingham_species_unsupported"


def test_ellingham_oxide_without_metal_key_is_typed_refusal() -> None:
    assert resolve_ellingham_oxide("WO3") is None
    point = KeyedTablePoint(
        compilation_id="janaf",
        record_id="W-oxide",
        formula="WO3",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1100.0,
        delta_fG_kJ_mol=-700.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    score = score_ellingham_point(point)
    assert score is not None
    assert score.status == "typed-refusal"
    assert score.residual_kJ_mol is None
    assert score.skip_reason == f"{TYPED_REFUSAL_PREFIX}ellingham_species_unsupported"
    assert "no OXIDE_TO_METAL metal key" in (score.note or "")


def test_ellingham_k_and_ni_keep_certified_band_flags() -> None:
    """K and Ni stay fail-closed at their certified ellingham_fit_range_K."""

    k_low, k_high = ellingham_fit_range_K("K")
    ni_low, ni_high = ellingham_fit_range_K("Ni")
    assert (k_low, k_high) == (1100.0, 2000.0)
    assert (ni_low, ni_high) == (1100.0, 2000.0)
    assert resolve_ellingham_oxide("K2O") == ("K", 2.0, 1.0)
    assert resolve_ellingham_oxide("NiO") == ("Ni", 1.0, 1.0)

    k_oor = KeyedTablePoint(
        compilation_id="janaf",
        record_id="K-012",
        formula="K2O",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=2100.0,
        delta_fG_kJ_mol=-50.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    k_score = score_ellingham_point(k_oor)
    assert k_score is not None
    assert k_score.status == "typed-refusal"
    assert k_score.skip_reason is not None
    assert k_score.skip_reason.startswith(
        f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:"
    )
    assert f"{k_low:g}-{k_high:g}K" in k_score.skip_reason
    assert "certified_band_flag" in (k_score.note or "")

    ni_oor = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Ni-oxide",
        formula="NiO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=2100.0,
        delta_fG_kJ_mol=-150.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    ni_score = score_ellingham_point(ni_oor)
    assert ni_score is not None
    assert ni_score.status == "typed-refusal"
    assert ni_score.skip_reason is not None
    assert ni_score.skip_reason.startswith(
        f"{TYPED_REFUSAL_PREFIX}engine_channel_out_of_range:"
    )
    assert f"{ni_low:g}-{ni_high:g}K" in ni_score.skip_reason
    assert "certified_band_flag" in (ni_score.note or "")

    k_inside = KeyedTablePoint(
        compilation_id="janaf",
        record_id="K-012",
        formula="K2O",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1600.0,
        delta_fG_kJ_mol=-50.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    k_in = score_ellingham_point(k_inside)
    assert k_in is not None
    assert k_in.status in {"match", "mismatch"}
    assert k_in.skip_reason is None


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


def test_fe2o3_ellingham_refuses_as_different_oxide_than_feo_line() -> None:
    """Fe Ellingham is 2 Fe + O2 → 2 FeO; Fe2O3 is not that line."""

    assert ellingham_oxide_stoichiometry_for_formula("Fe2O3") == (
        pytest.approx(4.0 / 3.0),
        pytest.approx(2.0 / 3.0),
    )
    assert ellingham_oxide_stoichiometry_for_formula("FeO") == (2.0, 2.0)
    assert ellingham_oxide_stoichiometry_for_formula("Al2O3") == (
        pytest.approx(4.0 / 3.0),
        pytest.approx(2.0 / 3.0),
    )
    assert oxide_identity_mismatch_applies("Fe2O3") is True
    assert oxide_identity_mismatch_applies("FeO") is False
    assert oxide_identity_mismatch_applies("Al2O3") is False
    assert oxide_identity_mismatch_applies("Cr2O3") is False
    assert oxide_identity_mismatch_applies("Na2O") is False
    assert ellingham_line_product_oxide("Fe", 1500.0) == "FeO"
    assert ellingham_line_product_oxide("Al", 1500.0) == "Al2O3"
    assert ellingham_line_product_oxide("Mg", 1100.0) == "MgO"

    T = 1500.0
    fe2o3 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Fe-030",
        formula="Fe2O3",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=T,
        delta_fG_kJ_mol=-438.347,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    cea = score_cea_point(fe2o3)
    ell = score_ellingham_point(fe2o3)
    assert ell is not None
    assert ell.status == "typed-refusal"
    assert ell.residual_kJ_mol is None
    assert ell.engine_kJ_mol is None
    assert ell.skip_reason == (
        f"{TYPED_REFUSAL_PREFIX}ellingham_line_is_different_oxide"
    )
    assert "FeO" in (ell.note or "")
    assert "not Fe2O3" in (ell.note or "")
    vs = score_channel_vs_channel(fe2o3, cea, ell)
    assert vs is None
    assert cea.finding_class != "oxide_identity_mismatch"

    feo = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Fe-020",
        formula="FeO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=T,
        delta_fG_kJ_mol=-175.415,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    feo_ell = score_ellingham_point(feo)
    assert feo_ell is not None
    assert feo_ell.status in {"match", "mismatch"}
    assert feo_ell.skip_reason is None
    assert feo_ell.finding_class != "oxide_identity_mismatch"


def test_cao_liquid_ellingham_refuses_solid_product_phase() -> None:
    """M03: CaO(l) is not the Ca line product CaO(s) at 1200 K."""

    assert ellingham_line_product_oxide("Ca", 1200.0) == "CaO"
    assert ellingham_line_product_phase_kind("Ca", 1200.0) == PHASE_SOLID
    liquid = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Ca-028",
        formula="CaO",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=1200.0,
        delta_fG_kJ_mol=-460.415,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    ell = score_ellingham_point(liquid)
    assert ell is not None
    assert ell.status == "typed-refusal"
    assert ell.residual_kJ_mol is None
    assert ell.engine_kJ_mol is None
    assert ell.skip_reason == (
        f"{TYPED_REFUSAL_PREFIX}ellingham_line_is_different_oxide"
    )
    assert "CaO(s)" in (ell.note or "") or "solid" in (ell.note or "")
    assert "not CaO(l)" in (ell.note or "")


def test_cao_crystal_ellingham_stays_comparable() -> None:
    """M03 control: CaO(cr) vs the solid CaO line remains a residual, not a refusal."""

    crystal = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Ca-027",
        formula="CaO",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1200.0,
        delta_fG_kJ_mol=-509.239,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    ell = score_ellingham_point(crystal)
    assert ell is not None
    assert ell.status in {"match", "mismatch"}
    assert ell.skip_reason is None
    assert ell.residual_kJ_mol == pytest.approx(0.064, abs=5e-4)
    assert ell.finding_class == "data_integrity"


def test_psat_comparator_zero_when_dvapG_is_zero() -> None:
    """ln(P_sat/P0)=0 when dfG(g)=dfG(condensed); Na NBP identity."""

    assert log10_psat_over_P0_from_dfg(0.0, 0.0, NA_NBP_K) == pytest.approx(0.0)
    assert log10_psat_over_P0_from_dfg(0.0, 0.0, MG_NBP_K) == pytest.approx(0.0)
    # Unit: 1 kJ/mol at 298.15 K is 0.1752 dex.
    dex = log10_psat_over_P0_from_dfg(RT_LN10_298_15_KJ, 0.0, 298.15)
    assert dex == pytest.approx(-1.0)


def test_psat_o2_alias_is_catalog_id_and_evaluator_absent() -> None:
    """G12: O2 maps to catalog id O2; missing evaluator is rail_row_absent."""

    assert resolve_rail_species_id("O2") == "O2"
    assert resolve_rail_species_id("Na") == "Na"
    pressure = _evaluate_rail_pressure_Pa("O2", 90.0)
    assert pressure == "rail_row_absent"


def test_psat_channel_refuses_missing_gas_or_condensed_or_rail() -> None:
    rail = derive_species_rail()
    gas = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Fe2O3-g",
        formula="Fe2O3",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=1500.0,
        delta_fG_kJ_mol=-100.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    scores = score_psat_channel([gas], rail)
    refusals = [
        p for p in scores
        if p.score.species == "Fe2O3" and p.score.status == "typed-refusal"
        and p.score.temperature_K == 1500.0
    ]
    assert refusals
    assert any(
        p.score.skip_reason == f"{TYPED_REFUSAL_PREFIX}condensed_row_absent"
        for p in refusals
    )

    condensed = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Fe2O3-cr",
        formula="Fe2O3",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1500.0,
        delta_fG_kJ_mol=-438.347,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
    )
    both = score_psat_channel([gas, condensed], rail)
    both_refusals = [
        p for p in both
        if p.score.species == "Fe2O3"
        and p.score.temperature_K == 1500.0
        and p.score.status == "typed-refusal"
    ]
    assert any(
        p.score.skip_reason == f"{TYPED_REFUSAL_PREFIX}rail_row_absent"
        for p in both_refusals
    )


def test_psat_pair_identity_and_nbp_sanity_rows() -> None:
    gas = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Na-005",
        formula="Na",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=NA_NBP_K,
        delta_fG_kJ_mol=0.0,
        log10_Kf=0.0,
        log10_Kf_as_published="0.000",
        printed_page=None,
        note="synthetic NBP pair",
    )
    liquid = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Na-003",
        formula="Na",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=NA_NBP_K,
        delta_fG_kJ_mol=0.0,
        log10_Kf=0.0,
        log10_Kf_as_published="0.000",
        printed_page=None,
    )
    score = score_psat_pair(gas, liquid, JANAF_STANDARD_PRESSURE_PA, "Na")
    assert score.comparison_quantity == QUANTITY_LOG10_PSAT
    denormal = score_psat_pair(gas, liquid, math.nextafter(0.0, 1.0), "Na")
    assert denormal.status in {"match", "mismatch"}
    assert denormal.residual_log10K is not None
    assert math.isfinite(float(denormal.residual_log10K))
    assert score.engine_channel == CHANNEL_VAPOUR_RAIL_PSAT
    assert score.residual_log10K == pytest.approx(0.0, abs=1e-12)
    assert score.status == "match"

    rail = derive_species_rail()
    na_P = _evaluate_rail_pressure_Pa("Na", NA_NBP_K)
    mg_P = _evaluate_rail_pressure_Pa("Mg", MG_NBP_K)
    assert isinstance(na_P, float)
    assert isinstance(mg_P, float)
    # Melt Pref is ~0.15 Pa (Na) / 5e-13 Pa (Mg); sidecar is ~1 bar.
    assert na_P == pytest.approx(84612.0, rel=0.02)
    assert mg_P == pytest.approx(103271.0, rel=0.02)
    assert abs(math.log10(na_P / JANAF_STANDARD_PRESSURE_PA)) < 0.1
    assert abs(math.log10(mg_P / JANAF_STANDARD_PRESSURE_PA)) < 0.1

    sanity = score_psat_nbp_sanity(rail)
    by_species = {p.score.species: p for p in sanity}
    assert "Na" in by_species
    assert "Mg" in by_species
    assert by_species["Na"].score.temperature_K == NA_NBP_K
    assert by_species["Mg"].score.temperature_K == MG_NBP_K
    for name in ("Na", "Mg"):
        score = by_species[name].score
        assert score.status in {"match", "mismatch"}
        assert score.residual_log10K is not None
        assert abs(float(score.residual_log10K)) < 0.1

    report = build_report(sanity)
    markdown = render_report_markdown(report)
    assert "## Vapour-rail P_sat" in markdown
    assert "Na/Mg boiling-point sanity" in markdown
    assert "pure_component_antoine" in markdown or "sidecar P_sat" in markdown


def test_psat_al_si_are_compilation_disagreement_not_cheap_hypotheses() -> None:
    """b-493: Al/Si rail P_sat is 1–2 dex above JANAF; cheap hypotheses die.

    H1: adapter uses Al-005/Si-005 gas and Al-003/Si-003 liquid, not cr
    past melting (Al 933.5 K, Si 1687 K) and not Al2/Si2/Si3.
    H2: Al 1700/2200 K and Si 2200 K sit inside the Stull sidecar fit
    (Al 1557–2329 K, Si 1997–2560 K), so the residual is not Antoine
    extrapolation. H3: Alcock 1984 liquid Al agrees with JANAF; Si is
    not in Alcock. Finding class is compilation_disagreement.
    """

    assert AL_MELTING_K == 933.5
    assert SI_MELTING_K == 1687.0
    al_fit = _sidecar_valid_range_K("Al")
    si_fit = _sidecar_valid_range_K("Si")
    assert al_fit == (1557.0, 2329.0)
    assert si_fit == (1997.0, 2560.0)
    assert al_fit[0] <= 1700.0 <= al_fit[1]
    assert al_fit[0] <= 2200.0 <= al_fit[1]
    assert si_fit[0] <= 2200.0 <= si_fit[1]
    assert not (al_fit[0] <= 1200.0 <= al_fit[1])
    assert not (si_fit[0] <= 1800.0 <= si_fit[1])
    assert not (si_fit[0] <= 2600.0 <= si_fit[1])

    al_cr_1200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Al-002",
        formula="Al",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=1200.0,
        delta_fG_kJ_mol=2.951,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    al_l_1200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Al-003",
        formula="Al",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=1200.0,
        delta_fG_kJ_mol=0.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    al_g_1200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Al-005",
        formula="Al",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=1200.0,
        delta_fG_kJ_mol=173.917,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    picked_1200 = _pick_condensed_psat([al_cr_1200, al_l_1200])
    assert picked_1200 is al_l_1200
    assert picked_1200.record_id == "Al-003"
    assert 1200.0 > AL_MELTING_K

    # Premise: Al(l) ⇌ Al(g), a=1, JANAF P0=0.1 MPa.
    # Algebra: log10(P_sat/P0)=−ΔvapG/(R T ln 10).
    # ΔvapG = dfG(Al-005 g)−dfG(Al-003 l)=117.631−0=117.631 kJ/mol at 1700 K.
    # R=8.314462618e-3 kJ/(mol·K); R T ln 10=0.008314462618×1700×2.302585
    # =32.546 kJ/mol. log10(P/P0)_JANAF=−117.631/32.546=−3.6143.
    # Sidecar log10(P/Pa)=10.73623−13204.109/(1700−24.306); P=718.53 Pa.
    # log10(P/P0)_rail=log10(718.53/1e5)=−2.1436.
    # residual=−2.1436−(−3.6143)=+1.4707 dex.
    # Unit: dimensionless dex. Sanity: Alcock 1984 liquid Al four-term at
    # 1700 K is 23.6 Pa, 0.01 dex from JANAF 24.3 Pa.
    al_l_1700 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Al-003",
        formula="Al",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=1700.0,
        delta_fG_kJ_mol=0.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    al_g_1700 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Al-005",
        formula="Al",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=1700.0,
        delta_fG_kJ_mol=117.631,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    al_P = _evaluate_rail_pressure_Pa("Al", 1700.0)
    assert isinstance(al_P, float)
    al_score = score_psat_pair(al_g_1700, al_l_1700, al_P, "Al")
    assert al_score.engine_channel == CHANNEL_VAPOUR_RAIL_PSAT
    assert al_score.status == "mismatch"
    assert al_score.finding_class == "compilation_disagreement"
    assert al_score.finding_class != FINDING_ANTOINE_EXTRAPOLATED_BEYOND_FIT
    assert al_score.finding_class != FINDING_CONDENSED_ROW_PAST_TRANSITION
    assert al_score.residual_log10K == pytest.approx(1.4707, abs=5e-4)
    assert "Al-003" in (al_score.note or "")
    assert "Stull 1947" in (al_score.note or "")
    assert psat_finding_class(
        status="mismatch",
        formula="Al",
        species_id="Al",
        T_K=1200.0,
        condensed=al_l_1200,
        provenance_class="independent_tabulation",
    ) == "compilation_disagreement"

    # Premise: Si(l) ⇌ Si(g), a=1, JANAF P0=0.1 MPa.
    # Algebra: same as Al. ΔvapG=dfG(Si-005 g)−dfG(Si-003 l)=144.382 kJ/mol
    # at 2200 K. R T ln 10=0.008314462618×2200×2.302585=42.118 kJ/mol.
    # log10(P/P0)_JANAF=−144.382/42.118=−3.4280.
    # Sidecar log10(P/Pa)=14.56436−23308.848/(2200−123.133); P=2194.21 Pa.
    # log10(P/P0)_rail=log10(2194.21/1e5)=−1.6587.
    # residual=−1.6587−(−3.4280)=+1.7693 dex.
    # Unit: dimensionless dex. Sanity: Si is absent from Alcock 1984
    # metallic-element tables; Clausius–Clapeyron from Tb≈3500 K and
    # ΔHvap≈383 kJ/mol gives ~42 Pa at 2200 K, with JANAF not Stull.
    si_cr_2200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Si-002",
        formula="Si",
        phase="cr",
        phase_kind=PHASE_SOLID,
        T_K=2200.0,
        delta_fG_kJ_mol=15.151,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    si_l_2200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Si-003",
        formula="Si",
        phase="l",
        phase_kind=PHASE_LIQUID,
        T_K=2200.0,
        delta_fG_kJ_mol=0.0,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    si_g_2200 = KeyedTablePoint(
        compilation_id="janaf",
        record_id="Si-005",
        formula="Si",
        phase="g",
        phase_kind=PHASE_GAS,
        T_K=2200.0,
        delta_fG_kJ_mol=144.382,
        log10_Kf=None,
        log10_Kf_as_published=None,
        printed_page=None,
        note="janaf p°=0.1 MPa",
    )
    picked_si = _pick_condensed_psat([si_cr_2200, si_l_2200])
    assert picked_si is si_l_2200
    assert 2200.0 > SI_MELTING_K
    si_P = _evaluate_rail_pressure_Pa("Si", 2200.0)
    assert isinstance(si_P, float)
    si_score = score_psat_pair(si_g_2200, si_l_2200, si_P, "Si")
    assert si_score.status == "mismatch"
    assert si_score.finding_class == "compilation_disagreement"
    assert si_score.finding_class != FINDING_ANTOINE_EXTRAPOLATED_BEYOND_FIT
    assert si_score.finding_class != FINDING_CONDENSED_ROW_PAST_TRANSITION
    assert si_score.residual_log10K == pytest.approx(1.7693, abs=5e-4)
    assert "Si-003" in (si_score.note or "")

    rail = derive_species_rail()
    channel = score_psat_channel(
        [al_g_1200, al_cr_1200, al_l_1200, si_g_2200, si_cr_2200, si_l_2200],
        rail,
    )
    by_key = {
        (p.score.species, p.score.temperature_K): p.score
        for p in channel
        if p.score.engine_channel == CHANNEL_VAPOUR_RAIL_PSAT
        and p.score.status != "typed-refusal"
        and p.score.species in {"Al", "Si"}
    }
    assert by_key[("Al", 1200.0)].finding_class == "compilation_disagreement"
    assert "condensed_record=Al-003" in (by_key[("Al", 1200.0)].note or "")
    assert by_key[("Si", 2200.0)].finding_class == "compilation_disagreement"
    assert "condensed_record=Si-003" in (by_key[("Si", 2200.0)].note or "")
