"""Hand-worked unit tests for tools/motzfeldt.py (t-511).

No landed extract currently carries a complete Motzfeldt geometry set
(orifice_area + clausing_factor + sample_surface_area + P_eq/P_meas). All
end-to-end store tests use the synthetic fixture at
``tests/fixtures/literature/motzfeldt_synthetic_extract.yaml``.

Hand-worked arithmetic (also documented in the fixture header)
--------------------------------------------------------------
Choose::

    a  = 1.0e-6 m²     (orifice area)
    f  = 1.0           (Clausing factor)
    A_s = 1.0e-4 m²    (sample free surface)
    α_true = 0.02

Forward Motzfeldt (eq. 2)::

    f * a / (α * A_s) = 1e-6 / (0.02 * 1e-4) = 1e-6 / 2e-6 = 0.5
    P_eq / P_meas = 1 + 0.5 = 1.5

With P_eq = 1.5 Pa → P_meas = 1.0 Pa.

Invert (eq. 3)::

    α = (f * a) / (A_s * (P_eq/P_meas − 1))
      = 1e-6 / (1e-4 * (1.5 − 1.0))
      = 1e-6 / (1e-4 * 0.5)
      = 1e-6 / 5e-5
      = 0.02

Multi-orifice (exact Motzfeldt points, same α, P_eq)::

    a=5e-7 → x=f a/A_s=0.005 → P_meas = 1.5 / (1 + 0.005/0.02) = 1.5/1.25 = 1.2
    a=1e-6 → x=0.01          → P_meas = 1.5 / 1.5              = 1.0
    a=2e-6 → x=0.02          → P_meas = 1.5 / 2.0              = 0.75

Linear fit of 1/P_meas vs x recovers intercept 1/P_eq = 2/3 and slope
1/(α P_eq) = 1/0.03, hence α = intercept/slope = 0.02.

Sanity vs published analysis: α = 0.02 is the high side of Costa & Jacobson
2015 Fe vaporization coefficient band (0.011–0.020) on Fo93Fa7 olivine via
multi-cell Whitman–Motzfeldt (NASA NTRS 20150002321).
"""

from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "literature"
    / "motzfeldt-synthetic.yaml"
)

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import extract_merge as em  # noqa: E402
import motzfeldt as mz  # noqa: E402
import validate_literature_extracts as vle  # noqa: E402


# ---------------------------------------------------------------------------
# Hand-worked single-orifice inversion
# ---------------------------------------------------------------------------


def test_hand_worked_forward_ratio():
    """Forward: f*a/(α*A_s) = 0.5 → P_eq/P_meas = 1.5."""
    # Arithmetic shown in module docstring / this file's header.
    a = 1.0e-6
    f = 1.0
    A_s = 1.0e-4
    alpha = 0.02
    load = (f * a) / (alpha * A_s)
    assert load == pytest.approx(0.5)
    R = mz.motzfeldt_ratio(
        orifice_area=a, clausing_factor=f, sample_surface_area=A_s, alpha=alpha
    )
    assert R == pytest.approx(1.5)


def test_hand_worked_invert_alpha():
    """Invert: α = (f*a)/(A_s*(R-1)) = 1e-6/(1e-4*0.5) = 0.02."""
    a = 1.0e-6
    f = 1.0
    A_s = 1.0e-4
    P_eq = 1.5
    P_meas = 1.0
    # Explicit hand arithmetic (do not call the library for the expected value):
    R = P_eq / P_meas  # 1.5
    expected = (f * a) / (A_s * (R - 1.0))  # 1e-6 / 5e-5 = 0.02
    assert expected == pytest.approx(0.02)

    res = mz.invert_alpha(
        mz.MotzfeldtInputs(
            P_eq=P_eq,
            P_meas=P_meas,
            orifice_area=a,
            clausing_factor=f,
            sample_surface_area=A_s,
        )
    )
    assert res.alpha == pytest.approx(expected)
    assert res.alpha == pytest.approx(0.02)
    assert res.P_eq_over_P_meas == pytest.approx(1.5)
    assert res.orifice_to_sample_ratio == pytest.approx(0.01)  # f*a/A_s


def test_hand_worked_uncertainty_propagation():
    """σ_α from σ_P_meas only: α ∝ 1/(R-1), R=P_eq/P_meas.

    With P_eq exact, P_meas = 1.0 ± 0.05 (5%):
      R = 1.5, σ_R = R * (σ_m/P_m) = 1.5 * 0.05 = 0.075
      σ_α/α = σ_R / (R − 1) = 0.075 / 0.5 = 0.15
      σ_α = 0.02 * 0.15 = 0.003
    """
    res = mz.invert_alpha(
        mz.MotzfeldtInputs(
            P_eq=1.5,
            P_meas=1.0,
            orifice_area=1.0e-6,
            clausing_factor=1.0,
            sample_surface_area=1.0e-4,
            sigma_P_meas=0.05,
        )
    )
    assert res.alpha == pytest.approx(0.02)
    assert res.sigma_alpha is not None
    assert res.sigma_alpha == pytest.approx(0.003, rel=1e-9)


def test_invert_refuses_P_eq_le_P_meas():
    with pytest.raises(ValueError, match="P_eq > P_meas"):
        mz.invert_alpha(
            mz.MotzfeldtInputs(
                P_eq=1.0,
                P_meas=1.0,
                orifice_area=1e-6,
                clausing_factor=1.0,
                sample_surface_area=1e-4,
            )
        )


def test_invert_refuses_clausing_gt_one():
    with pytest.raises(ValueError, match="clausing"):
        mz.invert_alpha(
            mz.MotzfeldtInputs(
                P_eq=1.5,
                P_meas=1.0,
                orifice_area=1e-6,
                clausing_factor=1.1,
                sample_surface_area=1e-4,
            )
        )


# ---------------------------------------------------------------------------
# Multi-orifice hand-worked series
# ---------------------------------------------------------------------------


def test_hand_worked_multi_orifice_extrapolation():
    """Three exact Motzfeldt points → P_eq=1.5, α=0.02 by linear fit.

    1/P vs x = f a / A_s:
      x=0.005, 1/P=1/1.2 ≈ 0.833333
      x=0.01,  1/P=1.0
      x=0.02,  1/P=1/0.75 ≈ 1.333333
    intercept b = 1/P_eq = 2/3 ≈ 0.666667
    slope m = 1/(α P_eq) = 1/0.03 ≈ 33.3333
    α = b/m = 0.02
    """
    A_s = 1.0e-4
    points = [
        mz.OrificePoint(P_meas=1.2, orifice_area=5.0e-7, clausing_factor=1.0),
        mz.OrificePoint(P_meas=1.0, orifice_area=1.0e-6, clausing_factor=1.0),
        mz.OrificePoint(P_meas=0.75, orifice_area=2.0e-6, clausing_factor=1.0),
    ]
    # Explicit hand check on one point before calling the library:
    x_mid = (1.0 * 1.0e-6) / A_s
    assert x_mid == pytest.approx(0.01)
    P_mid = 1.5 / (1.0 + x_mid / 0.02)
    assert P_mid == pytest.approx(1.0)

    res = mz.multi_orifice_alpha(points, sample_surface_area=A_s)
    assert res.P_eq == pytest.approx(1.5, rel=1e-12)
    assert res.alpha == pytest.approx(0.02, rel=1e-12)
    assert res.intercept == pytest.approx(1.0 / 1.5, rel=1e-12)
    assert res.slope == pytest.approx(1.0 / (0.02 * 1.5), rel=1e-12)
    assert res.n_points == 3
    assert res.r_squared == pytest.approx(1.0, abs=1e-12)


def test_multi_orifice_needs_two_points():
    with pytest.raises(ValueError, match="≥2"):
        mz.multi_orifice_alpha(
            [mz.OrificePoint(P_meas=1.0, orifice_area=1e-6)],
            sample_surface_area=1e-4,
        )


# ---------------------------------------------------------------------------
# Full Sossi–Fegley form (W_c = 1) vs simplified
# ---------------------------------------------------------------------------


def test_full_form_with_W_c_one_differs_from_simplified_at_finite_alpha():
    """At W_c=1 the full form is α = 1/(K+1), simplified is α = 1/K.

    For our hand-worked numbers: R=1.5, x=0.01 → K=(R-1)/x = 0.5/0.01 = 50.
    Simplified α = 1/K * x/(R-1) wait: simplified α = x/(R-1) = 0.01/0.5 = 0.02.
    Full with W_c=1: α = 1/(K − 1 + 2) = 1/(K+1) = 1/51 ≈ 0.019608.
    """
    full = mz.invert_alpha_full(
        P_eq=1.5,
        P_meas=1.0,
        orifice_area=1.0e-6,
        orifice_clausing=1.0,
        sample_surface_area=1.0e-4,
        cell_clausing=1.0,
    )
    assert full.alpha == pytest.approx(1.0 / 51.0)
    simple = mz.invert_alpha(
        mz.MotzfeldtInputs(
            P_eq=1.5,
            P_meas=1.0,
            orifice_area=1.0e-6,
            clausing_factor=1.0,
            sample_surface_area=1.0e-4,
        )
    )
    assert simple.alpha == pytest.approx(0.02)
    # Relative difference ~2% at α=0.02 (the 1/α − 1 ≈ 1/α approximation).
    assert abs(full.alpha - simple.alpha) / simple.alpha == pytest.approx(1.0 / 51.0)


# ---------------------------------------------------------------------------
# Langmuir optional path
# ---------------------------------------------------------------------------


def test_langmuir_alpha_from_rate_hand_worked():
    """J = α P / sqrt(2πMRT) → α = J * sqrt / P.

    Pick α=0.02, P=1.5 Pa, T=1750 K, M=0.055845 kg/mol (Fe):
      sqrt(2πMRT) = sqrt(2π * 0.055845 * 8.314462618 * 1750)
      J = 0.02 * 1.5 / sqrt(...)
    """
    alpha_true = 0.02
    P_eq = 1.5
    T_K = 1750.0
    M = 0.055845
    denom = math.sqrt(2.0 * math.pi * M * mz.R_GAS * T_K)
    J = alpha_true * P_eq / denom
    a_L, s_L = mz.alpha_from_langmuir_rate(
        flux_mol_m2_s=J,
        P_eq=P_eq,
        T_K=T_K,
        molar_mass_kg_mol=M,
    )
    assert a_L == pytest.approx(alpha_true, rel=1e-12)
    assert s_L is None  # no input sigmas


def test_combined_langmuir_kems_retains_both_not_average():
    denom = math.sqrt(2.0 * math.pi * 0.055845 * mz.R_GAS * 1750.0)
    J = 0.02 * 1.5 / denom
    out = mz.alpha_combined_langmuir_kems(
        P_meas=1.0,
        orifice_area=1e-6,
        clausing_factor=1.0,
        sample_surface_area=1e-4,
        flux_mol_m2_s=J,
        T_K=1750.0,
        molar_mass_kg_mol=0.055845,
        P_eq=1.5,
    )
    assert out["alpha_motzfeldt"]["alpha"] == pytest.approx(0.02)
    assert out["alpha_langmuir"]["alpha"] == pytest.approx(0.02)
    assert any("never averaged" in n for n in out["notes"])


# ---------------------------------------------------------------------------
# Cell-material → effective pO₂ boundary
# ---------------------------------------------------------------------------


def test_classify_mo_reducing():
    ann = mz.classify_cell_material("Mo")
    assert ann["boundary"] == "reducing"
    assert "getter" in ann["citation"].lower() or "Mo" in ann["citation"]


def test_classify_ir_liner_neutral():
    ann = mz.classify_cell_material("Ir liner in graphite cell")
    assert ann["boundary"] == "neutral"
    assert ann["material_normalized"] == "ir"


def test_classify_alumina_neutral():
    assert mz.classify_cell_material("alumina")["boundary"] == "neutral"


def test_classify_tungsten_reducing():
    assert mz.classify_cell_material("W")["boundary"] == "reducing"


def test_classify_unknown():
    ann = mz.classify_cell_material("unobtainium-42")
    assert ann["boundary"] == "unknown"


def test_effective_po2_from_equipment_mapping():
    ann = mz.effective_po2_boundary_from_cell_material(
        {"value": "Mo", "locator": {"page": 4}}
    )
    assert ann is not None
    assert ann["boundary"] == "reducing"
    assert ann["locator"] == {"page": 4}


def test_merge_attaches_effective_po2_boundary(tmp_path: Path):
    """extract_merge.build_by_species attaches the annotation at merge time."""
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    shutil.copy(FIXTURE, extracts / "motzfeldt-synthetic.yaml")
    # Minimal priority file so load_source_priority can run if needed.
    (extracts / "_source_priority.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_extract_source_priority.v1",
                "source_priority": {
                    "alpha": ["motzfeldt-synthetic"],
                    "psat_series": ["motzfeldt-synthetic"],
                    "gibbs_table": ["motzfeldt-synthetic"],
                    "activity_coefficient": ["motzfeldt-synthetic"],
                    "rate_series": ["motzfeldt-synthetic"],
                    "transition_point": ["motzfeldt-synthetic"],
                },
            }
        ),
        encoding="utf-8",
    )
    docs = em.load_extracts(extracts, require_valid=True)
    by_sp = em.build_by_species(
        docs,
        source_priority={
            "psat_series": ["motzfeldt-synthetic"],
            "alpha": ["motzfeldt-synthetic"],
        },
    )
    fe_rows = by_sp["species"]["Fe"]["observations"]
    mo_rows = [
        r
        for r in fe_rows
        if r.get("observation_id") == "synth_fe_kems_single_orifice"
    ]
    assert len(mo_rows) == 1
    assert "effective_po2_boundary" in mo_rows[0]
    assert mo_rows[0]["effective_po2_boundary"]["boundary"] == "reducing"

    ir_rows = [
        r for r in fe_rows if r.get("observation_id") == "synth_fe_oxide_ir_cell"
    ]
    assert len(ir_rows) == 1
    assert ir_rows[0]["effective_po2_boundary"]["boundary"] == "neutral"


# ---------------------------------------------------------------------------
# Synthetic fixture: scan + write-drafts through the validator
# ---------------------------------------------------------------------------


def test_landed_store_has_no_complete_geometry():
    """Document the gap: tool + tests use the synthetic fixture instead."""
    cands = mz.scan_geometry_candidates(REPO_ROOT / "data" / "literature" / "extracts")
    complete = [
        c
        for c in cands
        if c.complete_for_single_orifice or c.complete_for_multi_orifice
    ]
    assert complete == [], (
        "unexpected complete Motzfeldt geometry in landed extracts; "
        "update t-511 note if a real extract now carries orifice fields"
    )


def test_synthetic_fixture_validates():
    assert FIXTURE.is_file()
    errs = vle.validate_extract_file(FIXTURE)
    assert errs == [], errs


def test_scan_synthetic_finds_complete_geometry(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    shutil.copy(FIXTURE, d / "motzfeldt-synthetic.yaml")
    cands = mz.scan_geometry_candidates(d)
    assert any(c.complete_for_single_orifice for c in cands)
    assert any(c.complete_for_multi_orifice for c in cands)


def test_derive_single_orifice_from_synthetic(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    shutil.copy(FIXTURE, d / "motzfeldt-synthetic.yaml")
    cands = mz.scan_geometry_candidates(d)
    single = next(c for c in cands if c.observation_id == "synth_fe_kems_single_orifice")
    derived = mz.derive_alpha_for_candidate(single)
    assert derived is not None
    assert derived["mode"] == "single_orifice"
    assert derived["observation"]["values"]["alpha"] == pytest.approx(0.02)
    assert derived["observation"]["inferred"] is True
    parents = derived["observation"]["values"]["parents"]
    assert parents[0]["source_id"] == "motzfeldt-synthetic"
    assert parents[0]["observation_id"] == "synth_fe_kems_single_orifice"


def test_derive_multi_orifice_from_synthetic(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    shutil.copy(FIXTURE, d / "motzfeldt-synthetic.yaml")
    cands = mz.scan_geometry_candidates(d)
    multi = next(c for c in cands if c.observation_id == "synth_fe_kems_multi_orifice")
    derived = mz.derive_alpha_for_candidate(multi)
    assert derived is not None
    assert derived["mode"] == "multi_orifice"
    assert derived["observation"]["values"]["alpha"] == pytest.approx(0.02, rel=1e-12)
    assert derived["result"]["P_eq"] == pytest.approx(1.5, rel=1e-12)


def test_write_drafts_commits_validated_rows(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    dest = d / "motzfeldt-synthetic.yaml"
    shutil.copy(FIXTURE, dest)
    reports = mz.write_draft_alpha_observations(
        extracts_dir=d, dry_run=False
    )
    written = [r for r in reports if r.get("status") == "written"]
    assert len(written) >= 2  # single + multi
    assert all(r["alpha"] == pytest.approx(0.02, rel=1e-9) for r in written)

    # On-disk extract still validates.
    errs = vle.validate_extract_file(dest)
    assert errs == [], errs

    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    oids = {
        o["observation_id"]
        for o in doc["species"]["Fe"]["observations"]
    }
    assert "synth_fe_kems_single_orifice_motzfeldt_alpha" in oids
    assert "synth_fe_kems_multi_orifice_motzfeldt_multi_alpha" in oids

    # Inferred rows carry derivation + parent pointers.
    inferred = [
        o
        for o in doc["species"]["Fe"]["observations"]
        if o.get("inferred")
    ]
    assert inferred
    for o in inferred:
        assert o["type"] == "alpha"
        assert "derivation" in o["values"] or o.get("inference")
        assert o["values"]["parents"]
        assert o["values"]["semantics"] == "tool_derived_motzfeldt_inferred"


def test_write_drafts_dry_run_does_not_touch_disk(tmp_path: Path):
    d = tmp_path / "extracts"
    d.mkdir()
    dest = d / "motzfeldt-synthetic.yaml"
    shutil.copy(FIXTURE, dest)
    before = dest.read_text(encoding="utf-8")
    reports = mz.write_draft_alpha_observations(extracts_dir=d, dry_run=True)
    assert any(r.get("status") == "dry_run" for r in reports)
    assert dest.read_text(encoding="utf-8") == before


def test_build_inferred_observation_passes_validator():
    obs = mz.build_inferred_alpha_observation(
        observation_id="unit_test_alpha",
        alpha=0.02,
        sigma_alpha=0.003,
        parent_source_id="motzfeldt-synthetic",
        parent_observation_id="synth_fe_kems_single_orifice",
        derivation="hand-worked unit test derivation",
        T_range_K=[1750.0, 1750.0],
        phase="solid_solution_olivine",
    )
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "unit-test-motzfeldt",
        "source": {"citation": "unit test"},
        "extraction": {
            "method": "unit_test",
            "date": "2026-08-03",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[unit_test_alpha].values.alpha",
                "value": 0.02,
                "note": "unit test",
                "locator": {"note": "unit test"},
            }
        ],
        "species": {"Fe": {"observations": [obs]}},
    }
    errs = vle.validate_extract_document(doc, expected_source_id="unit-test-motzfeldt")
    assert errs == [], errs


def test_cli_invert_smoke(capsys):
    rc = mz.main(
        [
            "invert",
            "--P-eq",
            "1.5",
            "--P-meas",
            "1.0",
            "--orifice-area",
            "1e-6",
            "--clausing",
            "1.0",
            "--sample-area",
            "1e-4",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = yaml.safe_load(out)
    assert data["alpha"] == pytest.approx(0.02)


def test_cli_classify_cell_smoke(capsys):
    rc = mz.main(["classify-cell", "Mo"])
    assert rc == 0
    data = yaml.safe_load(capsys.readouterr().out)
    assert data["boundary"] == "reducing"


# ---------------------------------------------------------------------------
# t-511 FIX regressions (review-t511-cx.md P1 units + physical bounds)
# ---------------------------------------------------------------------------


def test_normalize_area_cm2_to_m2():
    """Null hypothesis: without conversion 0.01 cm2 would be treated as 0.01 m2."""
    assert mz.normalize_area_m2(0.01, "cm2", field="orifice_area") == pytest.approx(1e-6)
    assert mz.normalize_area_m2(1e-4, "m2", field="sample_surface_area") == pytest.approx(1e-4)


def test_normalize_area_missing_units_typed_refusal():
    with pytest.raises(mz.MotzfeldtUnitError, match="units missing"):
        mz.normalize_area_m2(1e-6, None, field="orifice_area")


def test_normalize_area_unknown_units_typed_refusal():
    with pytest.raises(mz.MotzfeldtUnitError, match="unrecognized"):
        mz.normalize_area_m2(1e-6, "furlongs2", field="orifice_area")


def test_reviewer_cm2_m2_mixed_units_write_through_alpha_0_02(tmp_path: Path):
    """Reviewer reproduction (review-t511-cx.md P1 conf 10/10).

    orifice_area={value: 0.01, units: cm2}  → 1e-6 m²
    sample_surface_area={value: 1e-4, units: m2}
    P_eq/P_meas = 1.5
    unit-aware α = 0.02; bare-numeric mix would yield α = 200.0 as schema-valid evidence.

    Null hypothesis: if scan/derive ignore units, written alpha ≈ 200 and this fails.
    """
    d = tmp_path / "extracts"
    d.mkdir()
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "mixed-units-cm2-m2",
        "source": {"citation": "unit-mix regression fixture"},
        "extraction": {
            "method": "regression",
            "date": "2026-08-03",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[mix_cm2].values.P_eq_Pa",
                "value": 1.5,
                "note": "pin",
                "locator": {"note": "pin"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "mix_cm2",
                        "type": "psat_series",
                        "locator": {"note": "cm2/m2 mix", "record": "mix_cm2"},
                        "T_range_K": [1750.0, 1750.0],
                        "phase": "solid_solution_olivine",
                        "regime": "kems_effusion",
                        "units": "Pa",
                        "values": {
                            "P_Pa": 1.0,
                            "P_meas_Pa": 1.0,
                            "P_eq_Pa": 1.5,
                            "T_K": 1750.0,
                            "gas_species": "Fe(g)",
                        },
                        "uncertainty": {"note": "synthetic"},
                        "equipment": {
                            "orifice_area": {
                                "value": 0.01,
                                "units": "cm2",
                                "locator": {"note": "0.01 cm2 = 1e-6 m2"},
                            },
                            "clausing_factor": {
                                "value": 1.0,
                                "units": "dimensionless",
                                "locator": {"note": "ideal"},
                            },
                            "sample_surface_area": {
                                "value": 1.0e-4,
                                "units": "m2",
                                "locator": {"note": "1e-4 m2"},
                            },
                            "cell_material": {
                                "value": "Mo",
                                "locator": {"note": "Mo"},
                            },
                        },
                    }
                ]
            }
        },
    }
    path = d / "mixed-units-cm2-m2.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert vle.validate_extract_file(path) == []

    cands = mz.scan_geometry_candidates(d)
    single = next(c for c in cands if c.observation_id == "mix_cm2")
    # SI normalization at scan time
    assert single.orifice_area == pytest.approx(1e-6)
    assert single.sample_surface_area == pytest.approx(1e-4)
    assert single.complete_for_single_orifice is True
    assert single.unit_refusals == ()

    derived = mz.derive_alpha_for_candidate(single)
    assert derived is not None and "observation" in derived
    alpha = derived["observation"]["values"]["alpha"]
    assert alpha == pytest.approx(0.02)
    # Explicit anti-regression: the bare-numeric bug product must not appear
    assert alpha != pytest.approx(200.0)
    assert alpha <= 1.0

    reports = mz.write_draft_alpha_observations(extracts_dir=d, dry_run=False)
    written = [r for r in reports if r.get("status") == "written"]
    assert len(written) == 1
    assert written[0]["alpha"] == pytest.approx(0.02)
    # On-disk extract still validates and does not carry α=200
    errs = vle.validate_extract_file(path)
    assert errs == [], errs
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    alphas = [
        o["values"]["alpha"]
        for o in on_disk["species"]["Fe"]["observations"]
        if o.get("inferred")
    ]
    assert len(alphas) == 1
    assert alphas[0] == pytest.approx(0.02)


def test_missing_area_units_refuses_scan_complete(tmp_path: Path):
    """Absent units → typed refusal; never assumed m².

    Null hypothesis: if missing units default to SI, complete_for_single becomes True.
    """
    d = tmp_path / "extracts"
    d.mkdir()
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "missing-area-units",
        "source": {"citation": "missing units regression"},
        "extraction": {
            "method": "regression",
            "date": "2026-08-03",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[no_units].values.P_eq_Pa",
                "value": 1.5,
                "note": "pin",
                "locator": {"note": "pin"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "no_units",
                        "type": "psat_series",
                        "locator": {"note": "no units", "record": "no_units"},
                        "T_range_K": [1750.0, 1750.0],
                        "regime": "kems_effusion",
                        "units": "Pa",
                        "values": {
                            "P_Pa": 1.0,
                            "P_meas_Pa": 1.0,
                            "P_eq_Pa": 1.5,
                        },
                        "uncertainty": {"note": "synthetic"},
                        "equipment": {
                            # value present, units ABSENT → refusal
                            "orifice_area": {
                                "value": 1.0e-6,
                                "locator": {"note": "no units"},
                            },
                            "clausing_factor": {
                                "value": 1.0,
                                "units": "dimensionless",
                                "locator": {"note": "ideal"},
                            },
                            "sample_surface_area": {
                                "value": 1.0e-4,
                                "units": "m2",
                                "locator": {"note": "ok"},
                            },
                        },
                    }
                ]
            }
        },
    }
    path = d / "missing-area-units.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    cands = mz.scan_geometry_candidates(d)
    cand = next(c for c in cands if c.observation_id == "no_units")
    assert cand.orifice_area is None
    assert cand.complete_for_single_orifice is False
    assert any("orifice_area" in r and "units missing" in r for r in cand.unit_refusals)
    derived = mz.derive_alpha_for_candidate(cand)
    assert derived is not None
    assert derived.get("refusal") == "MotzfeldtUnitError"
    assert "observation" not in derived


def test_alpha_out_of_bounds_refused_with_inputs():
    """Physical-bounds guard independent of units: α > 1 is MotzfeldtDomainError.

    Geometry that would invert to α=2 (a/A_s oversized relative to R−1).
    Null hypothesis: if bounds are only a soft note, invert_alpha returns alpha=2.
    """
    # x = f*a/A_s = 1e-4/1e-4 = 1.0; R-1 = 0.5 → α = 1/0.5 = 2.0
    with pytest.raises(mz.MotzfeldtDomainError, match="outside physical domain") as ei:
        mz.invert_alpha(
            mz.MotzfeldtInputs(
                P_eq=1.5,
                P_meas=1.0,
                orifice_area=1.0e-4,
                clausing_factor=1.0,
                sample_surface_area=1.0e-4,
            )
        )
    msg = str(ei.value)
    assert "alpha=2" in msg or "alpha=2.0" in msg
    assert "P_eq=1.5" in msg
    assert "A_s=" in msg


def test_alpha_out_of_bounds_refuses_write_through(tmp_path: Path):
    """Write-through path must not persist α > 1 as schema-valid evidence."""
    d = tmp_path / "extracts"
    d.mkdir()
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": "oob-alpha",
        "source": {"citation": "oob alpha regression"},
        "extraction": {
            "method": "regression",
            "date": "2026-08-03",
            "worker": "pytest",
        },
        "review_status": "draft",
        "fidelity_samples": [
            {
                "path": "species.Fe.observations[oob].values.P_eq_Pa",
                "value": 1.5,
                "note": "pin",
                "locator": {"note": "pin"},
            }
        ],
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "oob",
                        "type": "psat_series",
                        "locator": {"note": "oob", "record": "oob"},
                        "T_range_K": [1750.0, 1750.0],
                        "regime": "kems_effusion",
                        "units": "Pa",
                        "values": {
                            "P_Pa": 1.0,
                            "P_meas_Pa": 1.0,
                            "P_eq_Pa": 1.5,
                        },
                        "uncertainty": {"note": "synthetic"},
                        "equipment": {
                            "orifice_area": {
                                "value": 1.0e-4,
                                "units": "m2",
                                "locator": {"note": "oversized orifice"},
                            },
                            "clausing_factor": {
                                "value": 1.0,
                                "units": "dimensionless",
                                "locator": {"note": "ideal"},
                            },
                            "sample_surface_area": {
                                "value": 1.0e-4,
                                "units": "m2",
                                "locator": {"note": "A_s"},
                            },
                        },
                    }
                ]
            }
        },
    }
    path = d / "oob-alpha.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    reports = mz.write_draft_alpha_observations(extracts_dir=d, dry_run=False)
    written = [r for r in reports if r.get("status") == "written"]
    assert written == []
    # Candidate was complete geometrically but domain-refused
    skipped = [r for r in reports if r.get("status") == "skipped_incomplete_or_error"]
    assert skipped
    detail = skipped[0].get("detail") or {}
    assert detail.get("refusal") == "MotzfeldtDomainError"
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert not any(
        o.get("inferred") for o in on_disk["species"]["Fe"]["observations"]
    )


def test_multi_orifice_P_eq_Pa_is_pa_after_unit_normalization(tmp_path: Path):
    """Multi-orifice P_eq_Pa label is justified: inputs normalized to Pa first."""
    d = tmp_path / "extracts"
    d.mkdir()
    shutil.copy(FIXTURE, d / "motzfeldt-synthetic.yaml")
    cands = mz.scan_geometry_candidates(d)
    multi = next(c for c in cands if c.observation_id == "synth_fe_kems_multi_orifice")
    derived = mz.derive_alpha_for_candidate(multi)
    assert derived is not None
    assert derived["observation"]["values"]["P_eq_Pa"] == pytest.approx(1.5)
    assert "P_eq=1.5" in derived["observation"]["values"]["derivation"]
    assert "Pa" in derived["observation"]["values"]["derivation"]
    assert "identification" in derived["observation"]["values"]["derivation"]


def test_invert_refuses_nan_inputs():
    """P2 disposition→fix: public entry points reject non-finite inputs."""
    with pytest.raises(ValueError, match="finite"):
        mz.invert_alpha(
            mz.MotzfeldtInputs(
                P_eq=float("nan"),
                P_meas=1.0,
                orifice_area=1e-6,
                clausing_factor=1.0,
                sample_surface_area=1e-4,
            )
        )


def test_explicit_zero_clausing_not_defaulted_to_one():
    """P1: explicit 0.0 must not fail open via `raw.get(...) or default`."""
    points, refusal = mz._multi_points_from_equipment(
        {
            "orifice_area_units": "m2",
            "P_meas_units": "Pa",
            "points": [
                {"P_meas": 1.2, "orifice_area": 5e-7, "clausing_factor": 0.0},
                {"P_meas": 1.0, "orifice_area": 1e-6, "clausing_factor": 0.0},
            ],
        },
        default_clausing=1.0,
        default_A_s_m2=1e-4,
        default_P_units="Pa",
        default_area_units="m2",
    )
    # Points parse (0.0 is present), but multi_orifice_alpha must refuse them
    assert refusal is None
    assert points is not None
    assert all(p.clausing_factor == 0.0 for p in points)
    with pytest.raises(ValueError, match="non-physical"):
        mz.multi_orifice_alpha(points, sample_surface_area=1e-4)


def test_write_drafts_idempotent_fidelity_samples(tmp_path: Path):
    """P3: second write replaces fidelity samples rather than duplicating."""
    d = tmp_path / "extracts"
    d.mkdir()
    dest = d / "motzfeldt-synthetic.yaml"
    shutil.copy(FIXTURE, dest)
    before_n = len(yaml.safe_load(dest.read_text())["fidelity_samples"])
    mz.write_draft_alpha_observations(extracts_dir=d, dry_run=False)
    mid = yaml.safe_load(dest.read_text(encoding="utf-8"))
    mid_n = len(mid["fidelity_samples"])
    mz.write_draft_alpha_observations(extracts_dir=d, dry_run=False)
    after = yaml.safe_load(dest.read_text(encoding="utf-8"))
    after_n = len(after["fidelity_samples"])
    # First write adds derived samples; second write must not grow further
    assert mid_n > before_n
    assert after_n == mid_n
