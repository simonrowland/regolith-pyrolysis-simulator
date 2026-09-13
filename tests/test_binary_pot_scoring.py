"""Laptop-runnable tests for the binary-pot scoring arm. No melt engines."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.diagnostic_helpers.binary_pot_battery import load_binary_pots
from simulator.diagnostic_helpers.binary_pot_scoring import (
    DEFAULT_POTS_PATH,
    SCORING_EXTRACTS,
    build_scoring_pots_from_extracts,
    convert_printed_composition_to_wt_pct,
    feto_fe2_fe3_to_feo_fe2o3_moles,
    load_scoring_pots,
    oxide_molar_mass_g_mol,
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
