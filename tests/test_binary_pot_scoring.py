"""Laptop-runnable tests for the binary-pot scoring arm. No melt engines."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from scripts.calibration_battery import envelope, rail_for
from simulator.diagnostic_helpers.binary_pot_battery import (
    REFUSAL_COMPOSITION_PROJECTED,
    EquilibrateCell,
    Po2Request,
    composition_kg_and_mol,
    load_binary_pots,
)
from simulator.diagnostic_helpers.binary_pot_scoring import (
    DEFAULT_POTS_PATH,
    SCORING_EXTRACTS,
    build_scoring_pots_from_extracts,
    convert_printed_composition_to_wt_pct,
    feto_fe2_fe3_to_feo_fe2o3_moles,
    iter_activity_comparators,
    load_scoring_pots,
    method_class_is_scored,
    oxide_molar_mass_g_mol,
    recompute_scoring_from_report,
    render_scoring_report_markdown,
    score_scoring_arm,
    sync_scoring_pots_into_catalog,
)


ENGINE_ARM_POT_IDS = (
    "sio2_al2o3_50_50",
    "sio2_al2o3_80_20",
    "cao_sio2_40_60",
    "cao_al2o3_50_50",
    "mgo_sio2_40_60",
    "feo_sio2_40_60",
    "na2o_sio2_25_75",
    "feo_mgo_sio2_30_20_50",
)


def test_engine_arm_pots_unchanged() -> None:
    pots, _grid = load_binary_pots(DEFAULT_POTS_PATH)
    assert [pot.pot_id for pot in pots] == list(ENGINE_ARM_POT_IDS)


def test_scoring_pots_are_content_derived_from_extracts() -> None:
    derived = build_scoring_pots_from_extracts(SCORING_EXTRACTS)
    loaded = load_scoring_pots(DEFAULT_POTS_PATH)
    assert [p.pot_id for p in loaded] == [p.pot_id for p in derived]
    assert len(derived) == 30
    by_source = {p.source_id for p in derived}
    assert by_source == {
        "kems-057-kambayashi-1985",
        "kems-058-ohara-1987",
    }
    kambayashi = [p for p in derived if p.source_id.endswith("kambayashi-1985")]
    ohara = [p for p in derived if p.source_id.endswith("ohara-1987")]
    assert len(kambayashi) == 11  # 3 PbO-P2O5 + 8 FetO-P2O5
    assert len(ohara) == 19  # Table 2 samples 1-19; sample 20 has no composition
    for pot in derived:
        assert pot.source_id
        assert pot.observation_id
        assert pot.temperatures_K
        assert sum(pot.composition_wt_pct.values()) == pytest.approx(100.0)
        assert len(pot.composition_wt_pct) >= 2
        assert pot.pot_id not in ENGINE_ARM_POT_IDS

    # Table 3 is the only convertible PbO-P2O5 composition source. Emptying
    # it must drop those three pots; a hardcoded list would not shrink.
    extract_path = SCORING_EXTRACTS[0]
    payload = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(payload)
    found = False
    for block in mutated["species"].values():
        for obs in block["observations"]:
            if obs.get("observation_id") == (
                "kambayashi_1985_table3_pbo_p2o5_ion_current_ratios_1300c"
            ):
                obs["values"]["rows"] = []
                found = True
    assert found
    tmp = extract_path.with_name("kems-057-kambayashi-1985.mutated-for-test.yaml")
    try:
        tmp.write_text(yaml.safe_dump(mutated), encoding="utf-8")
        shrunk = build_scoring_pots_from_extracts((tmp, SCORING_EXTRACTS[1]))
        assert len(shrunk) == len(derived) - 3
        assert all("pbo_p2o5" not in p.pot_id for p in shrunk)
    finally:
        tmp.unlink(missing_ok=True)


def test_table3_elemental_wt_pct_reproduces_printed_mole_fraction() -> None:
    # Null hypothesis: a composition-basis slip. Table 3 row 1 prints
    # Pb=63.84 wt%, P=10.68 wt%, X_P2O5=0.359. Converted oxide moles
    # must recover that mole fraction.
    wt, as_printed, basis, conversion = convert_printed_composition_to_wt_pct(
        {"Pb_wt_percent": 63.84, "P_wt_percent": 10.68}
    )
    assert basis == "elemental_wt_percent"
    assert conversion == "elemental_Pb_P_wt_pct_to_PbO_P2O5_renormalized"
    assert as_printed == {"Pb_wt_percent": 63.84, "P_wt_percent": 10.68}
    n_pbo = wt["PbO"] / oxide_molar_mass_g_mol("PbO")
    n_p2o5 = wt["P2O5"] / oxide_molar_mass_g_mol("P2O5")
    assert n_p2o5 / (n_pbo + n_p2o5) == pytest.approx(0.359, abs=5e-4)
    assert sum(wt.values()) == pytest.approx(100.0)


def test_table4_printed_oxide_wt_pct_renormalizes() -> None:
    wt, _, basis, conversion = convert_printed_composition_to_wt_pct(
        {
            "P2O5_wt_percent": 1.52,
            "FeO_wt_percent": 86.55,
            "Fe2O3_wt_percent": 10.07,
        }
    )
    printed_sum = 1.52 + 86.55 + 10.07
    assert basis == "oxide_wt_percent"
    assert conversion == "printed_oxide_wt_pct_renormalized"
    assert wt["P2O5"] == pytest.approx(100.0 * 1.52 / printed_sum)
    assert wt["FeO"] == pytest.approx(100.0 * 86.55 / printed_sum)
    assert wt["Fe2O3"] == pytest.approx(100.0 * 10.07 / printed_sum)


def test_feto_split_mean_t_is_eight_point_five() -> None:
    n_feo, n_fe2o3 = feto_fe2_fe3_to_feo_fe2o3_moles(1.0, None)
    # t=0.95 → r=8.5; n_FeO=8.5/10; n_Fe2O3=1/20; n_Fe=0.95; n_O=1.
    assert n_feo == pytest.approx(0.85)
    assert n_fe2o3 == pytest.approx(0.05)
    assert n_feo + 2.0 * n_fe2o3 == pytest.approx(0.95)
    assert n_feo + 3.0 * n_fe2o3 == pytest.approx(1.0)


def test_sync_does_not_rewrite_engine_arm_pots(tmp_path: Path) -> None:
    original = DEFAULT_POTS_PATH.read_text(encoding="utf-8")
    clone = tmp_path / "binary_pots.yaml"
    clone.write_text(original, encoding="utf-8")
    pots = build_scoring_pots_from_extracts()
    sync_scoring_pots_into_catalog(pots, clone)
    engine, _ = load_binary_pots(clone)
    assert [p.pot_id for p in engine] == list(ENGINE_ARM_POT_IDS)
    scoring = load_scoring_pots(clone)
    assert len(scoring) == 30
    prefix = original.split("\nscoring_pots:")[0].split("\n# Scoring-arm pots")[0]
    written = clone.read_text(encoding="utf-8")
    assert written.startswith(prefix.rstrip())


def _refusal_cell(pot, *, engine: str = "alphamelts") -> EquilibrateCell:
    return EquilibrateCell(
        pot_id=pot.pot_id,
        engine=engine,
        temperature_K=float(pot.temperatures_K[0]),
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="refusal",
        refusal_reason="out_of_basis",
        engine_status="out_of_domain",
        engine_reason="SiO2 below MELTS band",
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )


def test_pbo_composition_has_formula_molar_mass() -> None:
    kg, mol = composition_kg_and_mol({"PbO": 73.77, "P2O5": 26.23})
    assert kg["PbO"] == pytest.approx(0.7377)
    assert mol["PbO"] > 0.0
    assert mol["P2O5"] > 0.0


def test_model_derived_observation_is_not_scored() -> None:
    pots = build_scoring_pots_from_extracts()
    pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
    comparators = iter_activity_comparators()
    assert any(method_class_is_scored(c.method_class) for c in comparators)
    assert any(c.method_class == "model_derived" for c in comparators)
    rows = score_scoring_arm(
        pots=(pot,),
        cells=(_refusal_cell(pot),),
        comparators=comparators,
        envelope=envelope,
        rail_for=rail_for,
    )
    derived = [r for r in rows if (r["conditions"] or {}).get("method_class") == "model_derived"]
    assert derived
    assert all(not r["score_eligible"] for r in derived)
    canon = envelope(
        dataset_id="x",
        observation_id="y",
        species="P2O5",
        observable="activity",
        units="1",
        measured=1.0,
        predicted=2.0,
        status="ok",
        conditions={},
        raw={"engine": "alphamelts"},
    )
    assert all(set(r) == set(canon) for r in rows)


def test_refusing_engine_yields_typed_refusal_envelope() -> None:
    pots = build_scoring_pots_from_extracts()
    pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
    rows = score_scoring_arm(
        pots=(pot,),
        cells=(_refusal_cell(pot, engine="magemin"),),
        comparators=iter_activity_comparators(),
        envelope=envelope,
        rail_for=rail_for,
    )
    assert rows
    assert all(r["authority"] == "refused" for r in rows)
    assert all(r["comparator_status"] == "out_of_basis" for r in rows)
    assert all(r["predicted"] is None for r in rows)
    assert all(not r["score_eligible"] for r in rows)
    assert all(r["engine"] == "magemin" for r in rows)


def test_composition_projected_is_typed_refusal_envelope() -> None:
    pots = build_scoring_pots_from_extracts()
    pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
    cell = EquilibrateCell(
        pot_id=pot.pot_id,
        engine="magemin",
        temperature_K=float(pot.temperatures_K[0]),
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="refusal",
        refusal_reason=REFUSAL_COMPOSITION_PROJECTED,
        engine_status="out_of_domain",
        engine_reason="dropped P2O5 (mass_fraction=0.0154881)",
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )
    rows = score_scoring_arm(
        pots=(pot,),
        cells=(cell,),
        comparators=iter_activity_comparators(),
        envelope=envelope,
        rail_for=rail_for,
    )
    assert rows
    assert all(r["authority"] == "refused" for r in rows)
    assert all(r["comparator_status"] == REFUSAL_COMPOSITION_PROJECTED for r in rows)
    assert all(r["predicted"] is None for r in rows)
    assert all(not r["score_eligible"] for r in rows)
    assert all("P2O5" in " ".join(r.get("notices") or []) for r in rows)


def test_recompute_scoring_reclassifies_magemin_projected_ok_cells() -> None:
    pots = build_scoring_pots_from_extracts()
    pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
    original = {
        "schema_version": 1,
        "kind": "binary_pot_scoring_arm",
        "generated_at": "2026-09-13T00:00:00Z",
        "authority": "diagnostic_only",
        "certifies": False,
        "calibrates": False,
        "hostname": "test",
        "receipt": {},
        "pots": [pot.as_payload() | {"pot_id": pot.pot_id}],
        "engines": {"magemin": {"available": True, "unavailable_reason": None}},
        "cells": [
            EquilibrateCell(
                pot_id=pot.pot_id,
                engine="magemin",
                temperature_K=float(pot.temperatures_K[0]),
                po2=Po2Request(mode="engine_default", po2_bar=None),
                status="ok",
                refusal_reason=None,
                engine_status="ok",
                engine_reason=None,
                melt_activities={},
                gas_partial_pressures_Pa={},
                liquid_fraction=0.001,
                wall_s=0.2,
                cpu_s=0.0,
                hostname="test",
            ).as_payload()
        ],
        "n_ok": 1,
        "n_refused": 0,
    }
    updated = recompute_scoring_from_report(original)
    rewritten = updated["cells"]
    assert rewritten
    assert all(cell["status"] == "refusal" for cell in rewritten)
    assert all(
        cell["refusal_reason"] == REFUSAL_COMPOSITION_PROJECTED for cell in rewritten
    )
    assert all("P2O5" in (cell["engine_reason"] or "") for cell in rewritten)
    matrix_cell = updated["refusal_matrix"][pot.pot_id]["magemin"]
    assert matrix_cell["dominant_reason"] == REFUSAL_COMPOSITION_PROJECTED
    assert matrix_cell["n_ok"] == 0
    assert "P2O5" in (matrix_cell.get("note") or "")
    markdown = render_scoring_report_markdown(updated)
    assert "composition_projected" in markdown
    assert "dropped P2O5" in markdown
    assert updated["n_ok"] == 0
    assert updated["n_refused"] == 1
    assert updated["n_score_eligible"] == 0


def test_measured_gamma_scores_when_engine_returns_activity() -> None:
    pots = build_scoring_pots_from_extracts()
    pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
    comparators = [
        c
        for c in iter_activity_comparators()
        if c.observation_id == "kambayashi_1985_gamma_p2o5_solid_std_henry"
        and c.temperature_K == pot.temperatures_K[0]
    ]
    assert comparators
    cell = EquilibrateCell(
        pot_id=pot.pot_id,
        engine="alphamelts",
        temperature_K=float(pot.temperatures_K[0]),
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={"P2O5": 1.0e-17},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )
    rows = score_scoring_arm(
        pots=(pot,),
        cells=(cell,),
        comparators=comparators,
        envelope=envelope,
        rail_for=rail_for,
    )
    assert len(rows) == 1
    assert rows[0]["score_eligible"] is True
    assert rows[0]["authority"] == "bridge"
    assert rows[0]["signed_residual"]["dex"] is not None
    assert rows[0]["rail"] == "melt activities"


def _successful_p2o5_cell(pot) -> EquilibrateCell:
    return EquilibrateCell(
        pot_id=pot.pot_id,
        engine="alphamelts",
        temperature_K=float(pot.temperatures_K[0]),
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={"P2O5": 1.0e-17},
        gas_partial_pressures_Pa={},
        liquid_fraction=1.0,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
    )


def _mutate_kambayashi_gamma(mutator) -> Path:
    extract_path = SCORING_EXTRACTS[0]
    payload = yaml.safe_load(extract_path.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(payload)
    found = False
    for block in mutated["species"].values():
        for obs in block["observations"]:
            if obs.get("observation_id") == "kambayashi_1985_gamma_p2o5_solid_std_henry":
                mutator(obs, block)
                found = True
    assert found
    tmp = extract_path.with_name("kems-057-kambayashi-1985.admission-mutated-for-test.yaml")
    tmp.write_text(yaml.safe_dump(mutated), encoding="utf-8")
    return tmp


def test_rejected_measured_observation_is_not_scored() -> None:
    """M12 confirm: canonical rejection is not bypassed by method_class=measured."""

    def mark_rejected(obs, _block) -> None:
        values = obs.setdefault("values", {})
        values["method_class"] = "measured"
        values["admission_status"] = "rejected_model_output_not_measurement"
        obs["admission_status"] = "rejected_model_output_not_measurement"

    tmp = _mutate_kambayashi_gamma(mark_rejected)
    try:
        pots = build_scoring_pots_from_extracts()
        pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
        comparators = [
            c
            for c in iter_activity_comparators(extract_paths=(tmp,))
            if c.observation_id == "kambayashi_1985_gamma_p2o5_solid_std_henry"
            and abs(c.temperature_K - 1643.0) < 1.0
        ]
        assert comparators
        assert all(c.method_class == "measured" for c in comparators)
        assert all(
            c.admission_reason == "model_output_not_measurement" for c in comparators
        )
        rows = score_scoring_arm(
            pots=(pot,),
            cells=(_successful_p2o5_cell(pot),),
            comparators=comparators,
            envelope=envelope,
            rail_for=rail_for,
        )
        assert rows
        assert all(not r["score_eligible"] for r in rows)
        assert any("model_output_not_measurement" in (r.get("notices") or []) for r in rows)
    finally:
        tmp.unlink(missing_ok=True)


def test_superseded_parent_observation_is_not_scored() -> None:
    """M12 confirm: a supersedes edge excludes the parent from scoring."""

    def add_replacement(obs, block) -> None:
        block["observations"].append(
            {
                "observation_id": "kambayashi_1985_gamma_p2o5_solid_std_henry_replacement",
                "type": "activity_coefficient",
                "supersedes": "kambayashi_1985_gamma_p2o5_solid_std_henry",
                "standard_state": obs.get("standard_state"),
                "values": {
                    "method_class": "measured",
                    "gamma_1370C": 2.2e-15,
                },
            }
        )

    tmp = _mutate_kambayashi_gamma(add_replacement)
    try:
        pots = build_scoring_pots_from_extracts()
        pot = next(p for p in pots if p.sample_no == 1 and "feto_p2o5_s" in p.pot_id)
        parent = [
            c
            for c in iter_activity_comparators(extract_paths=(tmp,))
            if c.observation_id == "kambayashi_1985_gamma_p2o5_solid_std_henry"
            and abs(c.temperature_K - 1643.0) < 1.0
        ]
        assert parent
        assert all(c.admission_reason == "superseded" for c in parent)
        rows = score_scoring_arm(
            pots=(pot,),
            cells=(_successful_p2o5_cell(pot),),
            comparators=parent,
            envelope=envelope,
            rail_for=rail_for,
        )
        assert rows
        assert all(not r["score_eligible"] for r in rows)
        assert any("superseded" in (r.get("notices") or []) for r in rows)
    finally:
        tmp.unlink(missing_ok=True)
