"""W0-SENS eligible-corpus reader tests — synthetic bench sets only.

Every bench set below is fabricated inside the test with invented sources,
compositions, and measured values. No test reads the tracked bench set, an
engine, a quarantined W value, or produces a ranking.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.w0_sens.corpus import (
    CellOutcome,
    CorpusRefusal,
    SealedNaManifest,
    _exclusion_reason,
    assemble_responses,
    build_manifest_bytes,
    channel_identity,
    read_eligible_corpus,
)
from benchmarks.w0_sens.driver import channel_shift, compute_join_metrics


SOURCE_A = "synthlab-a_kems_na2o_sio2"
SOURCE_B = "synthlab-b_emf_na2o_sio2"
COMPOSITIONS = {
    "synth_na2o_sio2_x080": {
        "material_class": "na_silicate_binary_melt",
        "composition_wt_pct": {"SiO2": 80.0, "Na2O": 20.0},
    },
    "synth_na2o_sio2_x060": {
        "material_class": "na_silicate_binary_melt",
        "composition_wt_pct": {"SiO2": 60.0, "Na2O": 40.0},
    },
    "synth_cmas": {
        "material_class": "synthetic_cmas",
        "composition_wt_pct": {"SiO2": 46.0, "CaO": 23.0, "MgO": 12.0, "Al2O3": 19.0},
    },
}


def _point(
    point_id: str,
    population: str,
    composition_id: str,
    temperature_K: float,
    **extra,
) -> dict:
    row = {
        "id": point_id,
        "population": population,
        "composition_id": composition_id,
        "material_class": "na_silicate_binary_melt",
        "temperature_K": temperature_K,
        "parent_oxide": "SiO2",
        "species": "SiO",
        "observable": "activity",
        "measured": 0.8,
        "units": "dimensionless",
        "score": True,
        "scoring_status": "SCORED-ELIGIBLE",
        "reduction_class": "experimental_gibbs_duhem_derived",
        "provenance": {"source_sha256": "a" * 64},
    }
    row.update(extra)
    return row


def _bench_set(tmp_path: Path, points: list[dict], name: str = "bench.yaml") -> Path:
    document = {
        "schema_version": "melt-activity-bench.v1",
        "title": "synthetic bench set",
        "compositions": COMPOSITIONS,
        "points": points,
    }
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _two_cluster_points() -> list[dict]:
    return [
        _point("a-1373", SOURCE_A, "synth_na2o_sio2_x080", 1373.0),
        _point("a-1473", SOURCE_A, "synth_na2o_sio2_x080", 1473.0),
        _point("b-1373", SOURCE_B, "synth_na2o_sio2_x060", 1373.0),
        _point("b-1473", SOURCE_B, "synth_na2o_sio2_x060", 1473.0),
    ]


def test_channel_identity_carries_temperature_and_label() -> None:
    assert channel_identity("activity", "SiO2", "SiO", 1473.0) == "a(SiO2)@1473K"
    assert (
        channel_identity("activity_coefficient", "MgO", "Mg", 1873.0)
        == "gamma(MgO)@1873K"
    )
    # Gas observables are labelled by the observed species, not the parent.
    assert channel_identity("partial_pressure", "K2O", "K", 1500.0) == "p(K)@1500K"
    with pytest.raises(CorpusRefusal):
        channel_identity("telepathy", "SiO2", "SiO", 1473.0)


def test_channel_identity_must_separate_temperatures(tmp_path, monkeypatch) -> None:
    """A T-free channel key collapses a multi-temperature composition.

    The driver rejects duplicate ``(source, composition_id, channel)``
    identities, so dropping T from the key makes the manifest unsealable —
    which is exactly why the key carries it (AMBIGUITY A-3).
    """
    path = _bench_set(tmp_path, _two_cluster_points())
    corpus = read_eligible_corpus(path)
    assert len(corpus.points) == 4
    assert len({point.channel for point in corpus.points}) == 2

    monkeypatch.setattr(
        "benchmarks.w0_sens.corpus.channel_identity",
        lambda observable, parent, species, temperature_K: "a(SiO2)",
    )
    with pytest.raises(CorpusRefusal) as excinfo:
        read_eligible_corpus(path)
    assert "duplicate sealed identity" in str(excinfo.value)


def test_eligibility_is_fail_closed_on_missing_metadata(tmp_path) -> None:
    points = _two_cluster_points()
    points += [
        _point(
            "held", SOURCE_A, "synth_na2o_sio2_x080", 1573.0,
            score=False, scoring_status="HELD-NOT-SCORED",
        ),
        # No scoring_status / evidence class / source hash: the shape of the
        # tracked Hastie-1981 and Richter Type-B rows.
        {
            "id": "bare",
            "population": "synthlab-c_kems",
            "composition_id": "synth_cmas",
            "material_class": "synthetic_cmas",
            "temperature_K": 1873.0,
            "parent_oxide": "MgO",
            "species": "Mg",
            "observable": "activity_coefficient",
            "measured": 0.5,
            "units": "dimensionless",
            "score": True,
        },
        _point(
            "no-hash", SOURCE_A, "synth_na2o_sio2_x080", 1673.0, provenance={}
        ),
        _point(
            "modelled", SOURCE_A, "synth_na2o_sio2_x080", 1773.0,
            reduction_class="model_curve_fit",
        ),
        _point(
            "extrapolated", SOURCE_B, "synth_na2o_sio2_x060", 1773.0,
            score=False,
            scoring_status="HELD-NOT-SCORED",
            extrapolation_flag=True,
        ),
        _point("nonpositive", SOURCE_A, "synth_na2o_sio2_x080", 1273.0, measured=0.0),
    ]
    corpus = read_eligible_corpus(_bench_set(tmp_path, points))
    assert {point.point_id for point in corpus.points} == {
        "a-1373",
        "a-1473",
        "b-1373",
        "b-1473",
    }
    reasons = {row.point_id: row.reason for row in corpus.exclusions}
    assert "not scored" in reasons["held"]
    assert "SCORED-ELIGIBLE" in reasons["bare"]
    assert "provenance.source_sha256" in reasons["no-hash"]
    assert "experimentally measured" in reasons["modelled"]
    assert "not scored" in reasons["extrapolated"]
    assert "positive finite" in reasons["nonpositive"]


def test_scored_extrapolation_flag_refuses(tmp_path) -> None:
    """A fit evaluated outside its window may not enter a statistic."""
    points = _two_cluster_points()
    points.append(
        _point(
            "scored-extrapolated", SOURCE_B, "synth_na2o_sio2_x060", 1773.0,
            extrapolation_flag=True,
        )
    )
    with pytest.raises(CorpusRefusal) as excinfo:
        read_eligible_corpus(_bench_set(tmp_path, points))
    assert "extrapolation-flagged" in str(excinfo.value)


def test_sealed_manifest_hash_is_over_the_emitted_bytes(tmp_path) -> None:
    corpus = read_eligible_corpus(_bench_set(tmp_path, _two_cluster_points()))
    assert (
        corpus.manifest_sha256
        == hashlib.sha256(corpus.manifest_bytes).hexdigest()
    )
    # Re-sealing the exact bytes reproduces the same manifest identity.
    assert SealedNaManifest.from_bytes(corpus.manifest_bytes).sha256 == (
        corpus.manifest_sha256
    )
    document = json.loads(corpus.manifest_bytes.decode("utf-8"))
    row = document["rows"][0]
    # Driver-validated identity fields plus the full step-4 tuple.
    for key in (
        "source",
        "composition_id",
        "channel",
        "na2o_wt_pct",
        "sio2_wt_pct",
        "temperature_K",
        "species",
        "measurement_type",
        "source_sha256",
    ):
        assert key in row, key
    assert row["measurement_type"] == "experimental"


def test_empty_eligible_corpus_refuses(tmp_path) -> None:
    points = [
        _point(
            "held", SOURCE_A, "synth_na2o_sio2_x080", 1573.0,
            score=False, scoring_status="HELD-NOT-SCORED",
        )
    ]
    with pytest.raises(CorpusRefusal):
        read_eligible_corpus(_bench_set(tmp_path, points))
    with pytest.raises(CorpusRefusal):
        build_manifest_bytes([], bench_set_path="x", bench_set_sha256="y")


def test_disclosures_are_computed_from_the_consumed_corpus(tmp_path) -> None:
    """Change the corpus, the disclosures change — they are not prose."""
    baseline = read_eligible_corpus(_bench_set(tmp_path, _two_cluster_points()))
    d1, d2, d3 = baseline.disclosures
    assert d1.id == "D1-gibbs-duhem-derived-channel"
    assert d1.facts["by_source"][SOURCE_A] == {"total": 2, "gibbs_duhem": 2}
    assert "transform" in d1.text
    assert d2.facts["converted_count"] == 0
    assert "No scored row carries a standard-state conversion" in d2.text
    assert d3.facts["flagged_count"] == 0

    points = _two_cluster_points()
    points[2] = _point(
        "b-1373", SOURCE_B, "synth_na2o_sio2_x060", 1373.0,
        evidence_class="experimental_emf_direct",
        source_evidence_class=None,
        reduction_class="authors_own_least_squares",
    )
    points[3] = _point(
        "b-1473", SOURCE_B, "synth_na2o_sio2_x060", 1473.0,
        evidence_class="experimental_gibbs_duhem_derived_controller_standard_state_converted",
        as_published_tridymite_activity=0.9,
        standard_state_conversion={
            "directive": "controller_directed",
            "from_standard_state": "pure_solid_SiO2_tridymite",
            "to_standard_state": "pure_liquid_SiO2",
            "activity_multiplier": 0.8327,
        },
        uncertainty={"combined_sigma_log10_dex": 0.0257},
    )
    points.append(
        _point(
            "b-held-extrapolated", SOURCE_B, "synth_na2o_sio2_x060", 1773.0,
            score=False,
            scoring_status="HELD-NOT-SCORED",
            extrapolation_flag=True,
        )
    )
    changed = read_eligible_corpus(_bench_set(tmp_path, points, name="bench2.yaml"))
    e1, e2, e3 = changed.disclosures
    # One source is now only partly Gibbs-Duhem derived; the text says so.
    assert e1.facts["by_source"][SOURCE_B] == {"total": 2, "gibbs_duhem": 1}
    assert "do NOT all carry" in e1.text
    assert e2.facts["converted_count"] == 1
    assert e2.facts["converted_rows"][0]["as_published_preserved"] is True
    assert e2.facts["converted_rows"][0]["combined_sigma_log10_dex"] == 0.0257
    assert "controller_directed" in e2.text
    assert e3.facts["flagged_count"] == 1
    assert e3.facts["held_count"] == 1
    assert e3.facts["scored_flagged_count"] == 0


def test_assemble_responses_types_every_absent_cell(tmp_path) -> None:
    corpus = read_eligible_corpus(_bench_set(tmp_path, _two_cluster_points()))
    ids = [point.point_id for point in corpus.points]
    control = {
        pid: CellOutcome(pid, 1.0, "ok")
        for pid in ids
    }
    plus = {pid: CellOutcome(pid, 10.0**0.2, "ok") for pid in ids}
    minus = {pid: CellOutcome(pid, 1.0, "ok") for pid in ids}
    # One wholly refused row and one half-refused row.
    refused = ids[0]
    half = ids[1]
    for table in (control, plus, minus):
        table[refused] = CellOutcome(
            refused, None, "refused", "ThermoEngine GibbsFreeEnergy is not finite"
        )
    plus[half] = CellOutcome(half, None, "observable_unavailable", "no positive activity")

    responses = assemble_responses(
        corpus, control=control, plus=plus, minus=minus
    )
    assert len(responses) == len(corpus.points)
    by_channel = {f"{r.cluster_id}|{r.channel}": r for r in responses}
    assert all(r.na_proof is not None for r in responses)
    assert all(
        r.na_proof.manifest_sha256 == corpus.manifest_sha256 for r in responses
    )
    # Every row keeps a distinct sealed identity.
    assert len(by_channel) == len(responses)
    wholly = next(r for r in responses if r.y_control is None)
    # Three distinct typed reasons, never one collapsed channel-level reason.
    assert len(wholly.typed_missing_cells()) == 3
    assert {reason for _, reason in wholly.typed_missing_cells()} == {
        "refused: ThermoEngine GibbsFreeEnergy is not finite"
    }
    assert channel_shift(wholly) is None
    metrics = compute_join_metrics(responses, evidence_grade=3)
    # 3 cells for the refused row + 1 for the half-refused sign.
    assert metrics.n_missing == 4
    assert metrics.C == 2


def test_assemble_responses_refuses_an_unevaluated_cell(tmp_path) -> None:
    corpus = read_eligible_corpus(_bench_set(tmp_path, _two_cluster_points()))
    ids = [point.point_id for point in corpus.points]
    full = {pid: CellOutcome(pid, 1.0, "ok") for pid in ids}
    partial = {pid: CellOutcome(pid, 1.0, "ok") for pid in ids[:-1]}
    with pytest.raises(CorpusRefusal) as excinfo:
        assemble_responses(corpus, control=full, plus=partial, minus=full)
    assert "not run completely" in str(excinfo.value)


def test_zero_literal_empirical_point_metadata_excludes(tmp_path) -> None:
    """MEDIUM-8: step 1 makes rows whose metadata reports zero literal
    empirical points ineligible, even stored in the same benchmark file."""
    points = _two_cluster_points()
    points.append(
        _point(
            "zero-count", SOURCE_A, "synth_na2o_sio2_x080", 1673.0,
            literal_empirical_point_count=0,
        )
    )
    points.append(
        _point(
            "zero-count-provenance", SOURCE_B, "synth_na2o_sio2_x060", 1673.0,
            provenance={
                "source_sha256": "b" * 64,
                "literal_empirical_point_count": 0,
            },
        )
    )
    corpus = read_eligible_corpus(_bench_set(tmp_path, points))
    reasons = {row.point_id: row.reason for row in corpus.exclusions}
    assert "zero literal empirical points" in reasons["zero-count"]
    assert "zero literal empirical points" in reasons["zero-count-provenance"]
    # An explicit POSITIVE count carries no exclusion opinion.
    points = _two_cluster_points()
    points.append(
        _point(
            "counted", SOURCE_A, "synth_na2o_sio2_x080", 1673.0,
            literal_empirical_point_count=3,
        )
    )
    corpus = read_eligible_corpus(_bench_set(tmp_path, points))
    assert "counted" in {point.point_id for point in corpus.points}


def test_source_sha256_must_be_a_real_hex_digest(tmp_path) -> None:
    """MEDIUM-8: any nonempty string used to pass as a source hash."""
    points = _two_cluster_points()
    points.append(
        _point(
            "bad-hash", SOURCE_A, "synth_na2o_sio2_x080", 1673.0,
            provenance={"source_sha256": "not-a-sha256"},
        )
    )
    points.append(
        _point(
            "short-hash", SOURCE_B, "synth_na2o_sio2_x060", 1673.0,
            provenance={"source_sha256": "ab12"},
        )
    )
    corpus = read_eligible_corpus(_bench_set(tmp_path, points))
    reasons = {row.point_id: row.reason for row in corpus.exclusions}
    assert "SHA-256" in reasons["bad-hash"]
    assert "SHA-256" in reasons["short-hash"]


def test_medium8_reviewer_probe_now_refuses() -> None:
    """The reviewer's executed probe, verbatim: both defects passed before
    the fix; both must now produce a typed exclusion."""
    probe = {
        "score": True,
        "scoring_status": "SCORED-ELIGIBLE",
        "evidence_class": "experimental_direct",
        "observable": "activity",
        "measured": 1.0,
        "literal_empirical_point_count": 0,
        "provenance": {"source_sha256": "not-a-sha256"},
    }
    assert _exclusion_reason(probe) is not None
    only_bad_hash = dict(probe)
    del only_bad_hash["literal_empirical_point_count"]
    reason = _exclusion_reason(only_bad_hash)
    assert reason is not None and "SHA-256" in reason
    only_zero_count = dict(probe)
    only_zero_count["provenance"] = {"source_sha256": "c" * 64}
    reason = _exclusion_reason(only_zero_count)
    assert reason is not None and "zero literal empirical points" in reason
