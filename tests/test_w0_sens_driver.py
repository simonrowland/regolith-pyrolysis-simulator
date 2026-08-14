"""W0-SENS driver focused tests — synthetic channel responses only.

Every channel value below is invented, and every sealed manifest is
synthetic bytes built inside the test. No test touches a quarantined W
value, a real eligible manifest, or a real engine output, and none
produces a ranking.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math

import numpy as np
import pytest

from benchmarks.w0_sens.driver import (
    AFFECTED_THRESHOLD_DEX,
    _DEX_BOUNDARY_NOISE_FLOOR_DEX,
    BOOTSTRAP_PERCENTILES,
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    S_SHIFT_UNIT_DEX,
    AbortRankingInstrumentNull,
    BootstrapInterval,
    ChannelResponse,
    NaBearingProof,
    NonProductionBootstrapInterval,
    SealedNaManifest,
    bootstrap_R_interval,
    bootstrap_R_interval_NONPRODUCTION,
    channel_shift,
    compute_join_metrics,
    delta_c,
)


def _manifest_bytes(*clusters) -> bytes:
    """Synthetic sealed-manifest bytes.

    Each cluster is ``(source, composition_id, na2o_wt_pct, sio2_wt_pct,
    channels)`` and expands to one manifest row per channel.
    """
    rows = []
    for source, composition_id, na2o, sio2, channels in clusters:
        for channel in channels:
            rows.append(
                {
                    "source": source,
                    "composition_id": composition_id,
                    "channel": channel,
                    "na2o_wt_pct": na2o,
                    "sio2_wt_pct": sio2,
                }
            )
    document = {
        "manifest": "W0-SENS-ELIGIBLE-MANIFEST",
        "version": 1,
        "rows": rows,
    }
    return json.dumps(document, sort_keys=True).encode("utf-8")


MANIFEST_A = SealedNaManifest.from_bytes(
    _manifest_bytes(
        ("src-a", "comp-1", 40.0, 60.0, ("activity:X", "activity:Y")),
        ("src-b", "comp-2", 30.0, 55.0, ("activity:X", "activity:Y")),
    )
)
MANIFEST_B = SealedNaManifest.from_bytes(
    _manifest_bytes(
        ("src-b", "comp-2", 30.0, 55.0, ("activity:X", "activity:Y")),
    )
)


def _response(
    source: str,
    composition_id: str,
    channel: str,
    y_control: float | None,
    y_plus: float | None,
    y_minus: float | None,
    *,
    missing: str | None = None,
    missing_cells: dict | None = None,
    na_proof: NaBearingProof | None = None,
    manifest: SealedNaManifest | None = None,
) -> ChannelResponse:
    if manifest is not None:
        assert na_proof is None
        na_proof = NaBearingProof(
            manifest=manifest,
            source=source,
            composition_id=composition_id,
            channel=channel,
        )
    return ChannelResponse(
        source=source,
        composition_id=composition_id,
        temperature_K=1473.0,
        channel=channel,
        y_control=y_control,
        y_plus=y_plus,
        y_minus=y_minus,
        missing=missing,
        missing_cells=missing_cells,
        na_proof=na_proof,
    )


def _shifted(
    source: str,
    composition_id: str,
    channel: str,
    shift_dex: float,
    *,
    na_proof: NaBearingProof | None = None,
    manifest: SealedNaManifest | None = None,
) -> ChannelResponse:
    """Synthetic channel whose +10 kJ response shifts by ``shift_dex``."""
    return _response(
        source,
        composition_id,
        channel,
        y_control=1.0,
        y_plus=10.0**shift_dex,
        y_minus=1.0,
        na_proof=na_proof,
        manifest=manifest,
    )


def test_frozen_constants() -> None:
    assert AFFECTED_THRESHOLD_DEX == 0.05
    assert S_SHIFT_UNIT_DEX == 0.1
    assert BOOTSTRAP_SEED == 649013
    assert BOOTSTRAP_REPLICATES == 10_000
    assert BOOTSTRAP_PERCENTILES == (2.5, 97.5)


def test_delta_c_exact() -> None:
    assert delta_c(10.0**1.3, 10.0**0.2) == pytest.approx(1.1)
    assert delta_c(0.5, 2.0) == pytest.approx(-math.log10(4.0))


def test_affected_threshold_is_inclusive_at_0_05_dex() -> None:
    over = channel_shift(_shifted("src-a", "comp-1", "activity:X", 0.0500001))
    under = channel_shift(_shifted("src-a", "comp-1", "activity:X", 0.0499999))
    assert over is not None and over.affected is True
    assert under is not None and under.affected is False
    # The boundary is INCLUSIVE at the REAL frozen threshold: the log10
    # round trip lands a nominal 0.05 dex shift a few ulps below the float
    # 0.05 (0.04999999999999996), and step 5's "at least 0.05 dex" still
    # counts it. No threshold is moved to make this pass.
    assert AFFECTED_THRESHOLD_DEX == 0.05
    on = channel_shift(_shifted("src-a", "comp-1", "activity:X", 0.05))
    assert on is not None
    assert on.shift_dex == pytest.approx(0.05)
    assert on.affected is True
    # The noise floor is ulp-scale only: a shift a distinguishable margin
    # below the bar is NOT affected.
    below = channel_shift(_shifted("src-a", "comp-1", "activity:X", 0.05 - 1.0e-9))
    assert below is not None and below.affected is False


def test_near_below_boundary_is_not_affected() -> None:
    """Regression: the tolerance must not admit sub-threshold shifts.

    An earlier fix of the exclusive-boundary defect used an absolute 1e-12 dex
    floor, which is ~144,000 ulps of 0.05 and classified both of the margins
    below as affected. These are the two counterexamples that found it. They
    are genuinely below the bar -- 10**(0.05-5e-13) and 10**0.05 differ by
    thousands of float ulps, so no round trip can confuse them -- and the
    threshold stays at the frozen 0.05 rather than being moved to pass.
    """
    assert AFFECTED_THRESHOLD_DEX == 0.05
    for margin in (5.0e-13, 9.0e-13, 1.0e-12, 1.0e-11):
        shift = channel_shift(_shifted("src-a", "comp-1", "activity:X", 0.05 - margin))
        assert shift is not None, margin
        assert shift.affected is False, f"0.05 - {margin} must be below the bar"
    # ...while the exact boundary and its own representation error still pass.
    for value in (0.05, math.nextafter(0.05, 0.0), math.nextafter(0.05, 1.0)):
        shift = channel_shift(_shifted("src-a", "comp-1", "activity:X", value))
        assert shift is not None and shift.affected is True, value
    # The tolerance is derived from the threshold, not a hand-picked epsilon,
    # and stays inside the gap between the measured 6-ulp round-trip error and
    # the 7.2e4-ulp nearest margin that must be rejected.
    assert _DEX_BOUNDARY_NOISE_FLOOR_DEX == 16 * math.ulp(AFFECTED_THRESHOLD_DEX)
    assert 8 * math.ulp(0.05) <= _DEX_BOUNDARY_NOISE_FLOOR_DEX <= 1.0e4 * math.ulp(0.05)


def test_larger_signed_magnitude_across_both_signs_governs() -> None:
    response = _response(
        "src-a", "comp-1", "activity:X",
        y_control=1.0, y_plus=10.0**0.01, y_minus=10.0**0.07,
    )
    shift = channel_shift(response)
    assert shift is not None
    assert shift.shift_dex == pytest.approx(0.07)
    assert shift.affected is True
    assert shift.delta_plus == pytest.approx(0.01)
    assert shift.delta_minus == pytest.approx(0.07)
    assert shift.missing_signs == ()


def test_metrics_arithmetic_c_s_i_r() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.10),
        _shifted("src-a", "comp-1", "activity:Y", 0.20),
        _shifted("src-a", "comp-2", "activity:X", 0.30),
        _shifted("src-a", "comp-2", "activity:Y", 0.01),  # unaffected
    ]
    metrics = compute_join_metrics(responses, evidence_grade=3)
    assert metrics.C == 3
    assert metrics.S == pytest.approx(2.0)  # median 0.20 dex in 0.1-dex units
    assert metrics.I_measured == pytest.approx(6.0)
    assert metrics.R_measured == pytest.approx(18.0)
    assert metrics.n_channels == 4
    assert metrics.n_missing == 0
    assert metrics.n_clusters == 2
    assert len(metrics.affected_channels) == 3


def test_c_zero_non_anchor_yields_frozen_zeros_not_empty_median() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.01),
        _shifted("src-a", "comp-2", "activity:X", -0.02),
    ]
    metrics = compute_join_metrics(responses, evidence_grade=3)
    assert metrics.C == 0
    assert metrics.S == 0.0
    assert metrics.I_measured == 0.0
    assert metrics.R_measured == 0.0


def test_na_anchor_c_zero_raises_typed_abort_not_a_ranking() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.01, manifest=MANIFEST_A),
        _shifted("src-b", "comp-2", "activity:X", 0.02, manifest=MANIFEST_A),
    ]
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


def test_na_anchor_requires_two_na_bearing_clusters() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_A),
    ]
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


def test_na_anchor_missing_proof_refuses_even_with_positive_c() -> None:
    """Review finding: two proof-less clusters with C>0 must NOT pass."""
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20),
        _shifted("src-b", "comp-2", "activity:X", 0.10),
    ]
    # Without the anchor gate the same corpus is a healthy positive result.
    assert compute_join_metrics(responses, evidence_grade=3).R_measured > 0.0
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


def test_na_anchor_zero_na_proof_is_not_bearing() -> None:
    zero_na = SealedNaManifest.from_bytes(
        _manifest_bytes(
            ("src-a", "comp-1", 0.0, 60.0, ("activity:X",)),
            ("src-b", "comp-2", 30.0, 55.0, ("activity:X",)),
        )
    )
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=zero_na),
        _shifted("src-b", "comp-2", "activity:X", 0.10, manifest=zero_na),
    ]
    with pytest.raises(AbortRankingInstrumentNull):
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)


def test_na_anchor_mixed_manifests_refuse() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_A),
        _shifted("src-b", "comp-2", "activity:X", 0.10, manifest=MANIFEST_B),
    ]
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


def test_na_anchor_requires_a_nonmissing_channel_per_cluster() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_A),
        _response(
            "src-b", "comp-2", "activity:X",
            None, None, None, missing="engine_refused", manifest=MANIFEST_A,
        ),
    ]
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


MANIFEST_C = SealedNaManifest.from_bytes(
    _manifest_bytes(
        ("src-a", "comp-1", 40.0, 60.0, ("activity:X",)),
        ("src-b", "comp-2", 30.0, 55.0, ("activity:X",)),
        ("src-b", "comp-3", 35.0, 58.0, ("activity:X",)),
        ("src-c", "comp-4", 20.0, 50.0, ("activity:X",)),
    )
)


def test_a9_reading_a_dark_cluster_does_not_abort() -> None:
    """AMBIGUITY A-9 reading A: a dark cluster is typed missing, not an abort.

    This is the real 2026-08-14 run's shape: two independent Na-bearing
    sources carry live channels while a third cluster returns nothing. Under
    the superseded corpus-wide reading this raised
    ABORT-RANKING-INSTRUMENT-NULL; under the blind-adjudicated ruling in
    ADJUDICATION-A9.md it must produce metrics, with the dark cluster's cells
    still counted as missing so an auditor can see it. Red-by-revert against
    the previous dark-cluster abort.
    """
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_C),
        _shifted("src-b", "comp-2", "activity:X", 0.10, manifest=MANIFEST_C),
        _response(
            "src-c", "comp-4", "activity:X",
            None, None, None, missing="engine_refused", manifest=MANIFEST_C,
        ),
    ]
    metrics = compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert metrics.C == 2
    assert metrics.R_measured > 0.0
    # The dark cluster does not vanish: its absent cells stay counted.
    assert metrics.n_missing > 0


def test_a9_two_live_clusters_from_one_source_are_not_two_anchors() -> None:
    """Item 4's "independent" anchors means independent BY SOURCE.

    Two compositions from a single study are one measurement tradition. This
    direction is STRICTER than counting clusters, so it can only make the
    refusal more likely -- it exists because it is the faithful reading of
    ADJUDICATION-A9, not because of any outcome.
    """
    responses = [
        _shifted("src-b", "comp-2", "activity:X", 0.20, manifest=MANIFEST_C),
        _shifted("src-b", "comp-3", "activity:X", 0.30, manifest=MANIFEST_C),
        _response(
            "src-a", "comp-1", "activity:X",
            None, None, None, missing="engine_refused", manifest=MANIFEST_C,
        ),
    ]
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"
    assert "SOURCES" in str(excinfo.value)


def test_na_anchor_passes_when_gate_and_c_positive() -> None:
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_A),
        _shifted("src-b", "comp-2", "activity:X", 0.10, manifest=MANIFEST_A),
    ]
    metrics = compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert metrics.C == 2
    assert metrics.R_measured > 0.0


def test_missing_cells_are_never_imputed_zero() -> None:
    typed_missing = _response(
        "src-a", "comp-1", "activity:X", None, None, None,
        missing="engine_refused",
    )
    control_missing = _response(
        "src-a", "comp-2", "activity:X", None, 1.0e9, 1.0e9,
        missing_cells={"y_control": "no_positive_control"},
    )
    sign_missing = _response(
        "src-a", "comp-3", "activity:X", 1.0, None, 10.0**0.06,
        missing_cells={"y_plus": "engine_refused"},
    )
    assert channel_shift(typed_missing) is None
    assert channel_shift(control_missing) is None
    # A-16: one typed-missing sign makes the channel MISSING, not affected.
    assert channel_shift(sign_missing) is None

    metrics = compute_join_metrics(
        [typed_missing, control_missing, sign_missing], evidence_grade=2
    )
    assert metrics.C == 0
    assert metrics.R_measured == 0.0
    # Cell-level typed missing counts: 3 (whole channel) + 1 (control) + 1
    # (sign). Partial missingness is never dropped from the released counts.
    assert metrics.n_missing == 5


def test_one_signed_response_is_missing_not_affected() -> None:
    """Review MEDIUM-5 / AMBIGUITY A-16: "across the two perturbations"
    requires BOTH signed responses; a single sign clearing the bar — even
    by a wide margin — yields a missing channel, never an affected one.
    """
    response = _response(
        "src-a", "comp-1", "activity:X", 1.0, 10.0**0.06, None,
        missing_cells={"y_minus": "engine_refused"},
    )
    assert channel_shift(response) is None
    metrics = compute_join_metrics([response], evidence_grade=3)
    assert metrics.C == 0
    assert metrics.R_measured == 0.0
    assert metrics.n_missing == 1  # the typed missing sign still counts


def test_missing_sign_carries_a_typed_reason() -> None:
    response = _response(
        "src-a", "comp-1", "activity:X", 1.0, 10.0**0.07, None,
        missing_cells={"y_minus": "engine_refused"},
    )
    assert response.typed_missing_cells() == (("y_minus", "engine_refused"),)
    # A-16: the channel is missing (uncomputable), not affected — but its
    # typed missing sign is retained and counted, never imputed zero.
    assert channel_shift(response) is None
    metrics = compute_join_metrics([response], evidence_grade=3)
    assert metrics.C == 0
    assert metrics.n_missing == 1


def test_absent_cell_without_a_typed_reason_is_a_construction_error() -> None:
    with pytest.raises(ValueError):
        _response("src-a", "comp-1", "activity:X", 1.0, None, 1.0)
    with pytest.raises(ValueError):  # reason for a present cell
        _response(
            "src-a", "comp-1", "activity:X", 1.0, 1.0, 1.0,
            missing_cells={"y_plus": "engine_refused"},
        )
    with pytest.raises(ValueError):  # empty reason
        _response(
            "src-a", "comp-1", "activity:X", 1.0, None, 1.0,
            missing_cells={"y_plus": "  "},
        )
    with pytest.raises(ValueError):  # unknown cell name
        _response(
            "src-a", "comp-1", "activity:X", 1.0, None, 1.0,
            missing_cells={"y_plus": "engine_refused", "y_side": "??"},
        )
    with pytest.raises(ValueError):  # channel-level mixed with per-cell
        _response(
            "src-a", "comp-1", "activity:X", None, None, None,
            missing="engine_refused",
            missing_cells={"y_plus": "engine_refused"},
        )


def test_response_cells_must_be_positive_finite() -> None:
    with pytest.raises(ValueError):
        _response("src-a", "comp-1", "activity:X", 0.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        _response("src-a", "comp-1", "activity:X", -1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        _response(
            "src-a", "comp-1", "activity:X", 1.0, 1.0, 1.0,
            missing="engine_refused",
        )


def test_na_proof_requires_real_sealed_manifest_membership() -> None:
    """Re-review finding 3: a bare string is not a sealed manifest."""
    with pytest.raises(TypeError):
        NaBearingProof(
            manifest="forged-manifest-string",  # the review's forged exploit
            source="src-a",
            composition_id="comp-1",
            channel="activity:X",
        )
    # A row outside the sealed manifest cannot be proved at all.
    with pytest.raises(ValueError):
        NaBearingProof(
            manifest=MANIFEST_A,
            source="src-a",
            composition_id="comp-9",
            channel="activity:X",
        )
    with pytest.raises(ValueError):
        NaBearingProof(
            manifest=MANIFEST_A,
            source="src-a",
            composition_id="comp-1",
            channel="activity:ZZZ",
        )


def test_sealed_manifest_hash_is_over_the_exact_bytes() -> None:
    raw = _manifest_bytes(("src-a", "comp-1", 40.0, 60.0, ("activity:X",)))
    manifest = SealedNaManifest.from_bytes(raw)
    assert manifest.sha256 == hashlib.sha256(raw).hexdigest()
    tampered = SealedNaManifest.from_bytes(raw.replace(b"40.0", b"41.0"))
    assert tampered.sha256 != manifest.sha256
    # Composition is read FROM the sealed row; the caller cannot supply it.
    proof = NaBearingProof(
        manifest=manifest,
        source="src-a",
        composition_id="comp-1",
        channel="activity:X",
    )
    assert proof.is_na_bearing
    zero = SealedNaManifest.from_bytes(
        _manifest_bytes(("src-a", "comp-1", 0.0, 60.0, ("activity:X",)))
    )
    assert not NaBearingProof(
        manifest=zero,
        source="src-a",
        composition_id="comp-1",
        channel="activity:X",
    ).is_na_bearing


def test_sealed_manifest_schema_and_integrity_validation() -> None:
    with pytest.raises(ValueError):  # not JSON
        SealedNaManifest.from_bytes(b"not json")
    with pytest.raises(ValueError):  # no rows key
        SealedNaManifest.from_bytes(json.dumps({"nope": 1}).encode())
    with pytest.raises(ValueError):  # empty rows
        SealedNaManifest.from_bytes(json.dumps({"rows": []}).encode())
    with pytest.raises(ValueError):  # duplicate row identity
        SealedNaManifest.from_bytes(
            _manifest_bytes(("src-a", "comp-1", 40.0, 60.0, ("activity:X", "activity:X")))
        )
    with pytest.raises(ValueError):  # negative wt%
        SealedNaManifest.from_bytes(
            _manifest_bytes(("src-a", "comp-1", -1.0, 60.0, ("activity:X",)))
        )
    with pytest.raises(ValueError):  # non-finite wt%
        SealedNaManifest.from_bytes(
            _manifest_bytes(("src-a", "comp-1", float("nan"), 60.0, ("activity:X",)))
        )
    with pytest.raises(ValueError):  # contradictory composition per cluster
        SealedNaManifest.from_bytes(
            json.dumps(
                {
                    "rows": [
                        {"source": "src-a", "composition_id": "comp-1",
                         "channel": "activity:X", "na2o_wt_pct": 40.0,
                         "sio2_wt_pct": 60.0},
                        {"source": "src-a", "composition_id": "comp-1",
                         "channel": "activity:Y", "na2o_wt_pct": 41.0,
                         "sio2_wt_pct": 60.0},
                    ]
                }
            ).encode()
        )


def test_na_anchor_unproved_member_of_a_proved_cluster_refuses() -> None:
    """Re-review finding 3 exploit shape: the old first-proved-member check
    let an unproved later row of a cluster contribute to C (forged C=3).
    Now EVERY row must carry sealed-manifest proof.
    """
    responses = [
        _shifted("src-a", "comp-1", "activity:X", 0.20, manifest=MANIFEST_A),
        _shifted("src-b", "comp-2", "activity:X", 0.10, manifest=MANIFEST_A),
        _shifted("src-b", "comp-2", "activity:Y", 0.30),  # UNPROVED row
    ]
    # Without the anchor gate the same corpus is C=3 — the unproved row
    # genuinely contributes, so the refusal is not vacuous.
    assert compute_join_metrics(responses, evidence_grade=3).C == 3
    with pytest.raises(AbortRankingInstrumentNull) as excinfo:
        compute_join_metrics(responses, evidence_grade=3, na_anchor=True)
    assert excinfo.value.abort_type == "ABORT-RANKING-INSTRUMENT-NULL"


def test_proof_identity_must_match_the_response_row() -> None:
    """A proof minted for one row cannot be spliced onto another row."""
    proof = NaBearingProof(
        manifest=MANIFEST_A,
        source="src-a",
        composition_id="comp-1",
        channel="activity:X",
    )
    with pytest.raises(ValueError):  # wrong cluster
        _response("src-b", "comp-2", "activity:X", 1.0, 2.0, 1.0, na_proof=proof)
    with pytest.raises(ValueError):  # right cluster, wrong channel
        _response("src-a", "comp-1", "activity:Y", 1.0, 2.0, 1.0, na_proof=proof)
    # Exact identity passes.
    _response("src-a", "comp-1", "activity:X", 1.0, 2.0, 1.0, na_proof=proof)


def test_missing_cells_mapping_is_frozen_at_construction() -> None:
    """Re-review finding 5 exploit: the caller's mapping must not be
    retained — clearing it after construction turned a typed missing cell
    into released_n_missing=0.
    """
    cells = {"y_plus": "engine_refused"}
    response = _response(
        "src-a", "comp-1", "activity:X", 1.0, None, 10.0**0.06,
        missing_cells=cells,
    )
    assert compute_join_metrics([response], evidence_grade=3).n_missing == 1
    cells.clear()  # the old defect: released counts collapsed to 0 here
    assert response.typed_missing_cells() == (("y_plus", "engine_refused"),)
    assert compute_join_metrics([response], evidence_grade=3).n_missing == 1
    cells["y_minus"] = "spliced_later"  # later insertions cannot enter either
    assert response.typed_missing_cells() == (("y_plus", "engine_refused"),)
    with pytest.raises(TypeError):  # the stored mapping is immutable
        response.missing_cells["y_plus"] = "rewritten"


# Heterogeneous per-cluster shifts: every cluster's series differs, so
# whole-cluster resampling changes the R_measured distribution and the
# interval is non-degenerate. (A corpus whose clusters all carry identical
# shifts collapses every replicate to the same R, which lets an unseeded
# RNG survive the determinism test — review mutation hole, fixed here.)
_CLUSTER_SHIFTS = (
    (("activity:X", 0.08), ("activity:Y", 0.12)),
    (("activity:X", 0.21), ("activity:Y", 0.05)),
    (("activity:X", 0.03), ("activity:Y", 0.17)),
    (("activity:X", 0.14), ("activity:Y", 0.09)),
)


def _bootstrap_corpus() -> list[ChannelResponse]:
    rows: list[ChannelResponse] = []
    for cluster, channels in enumerate(_CLUSTER_SHIFTS):
        for channel, shift in channels:
            rows.append(
                _shifted(f"src-{cluster % 2}", f"comp-{cluster}", channel, shift)
            )
    return rows


def test_bootstrap_deterministic_under_frozen_seed(monkeypatch) -> None:
    corpus = _bootstrap_corpus()
    # Seed observability: the frozen path must construct PCG64 with the
    # frozen seed. Percentile VALUES of a discrete R distribution can
    # coincide across seeds even when the RNG is unseeded (observed: an
    # unseeded default_rng mutation survived a value-only comparison), so
    # determinism is asserted at the generator-construction boundary as
    # well as on the interval values.
    constructed: list[int] = []
    real_pcg64 = np.random.PCG64

    def recording_pcg64(seed=None, **kwargs):
        constructed.append(seed)
        return real_pcg64(seed, **kwargs)

    monkeypatch.setattr(np.random, "PCG64", recording_pcg64)
    first = bootstrap_R_interval(corpus, evidence_grade=3)
    monkeypatch.undo()
    second = bootstrap_R_interval(corpus, evidence_grade=3)
    assert constructed == [BOOTSTRAP_SEED]
    assert first.seed == BOOTSTRAP_SEED
    assert first.replicates == BOOTSTRAP_REPLICATES
    assert (first.lower, first.upper, first.half_width) == (
        second.lower,
        second.upper,
        second.half_width,
    )
    assert first.lower < first.upper  # heterogeneous corpus: non-degenerate
    assert first.half_width == pytest.approx((first.upper - first.lower) / 2.0)


def test_frozen_bootstrap_entry_point_admits_no_overrides() -> None:
    params = inspect.signature(bootstrap_R_interval).parameters
    assert "seed" not in params
    assert "replicates" not in params


def test_nonproduction_bootstrap_is_a_distinct_unreleasable_type() -> None:
    interval = bootstrap_R_interval_NONPRODUCTION(
        _bootstrap_corpus(), evidence_grade=3, seed=1, replicates=50
    )
    assert isinstance(interval, NonProductionBootstrapInterval)
    assert not isinstance(interval, BootstrapInterval)
    assert interval.replicates == 50


def test_bootstrap_resamples_whole_clusters() -> None:
    corpus = _bootstrap_corpus()
    # A degenerate one-cluster corpus collapses the interval to the point
    # value: resampling can only ever redraw that same whole series.
    one_cluster = [row for row in corpus if row.composition_id == "comp-0"]
    interval = bootstrap_R_interval_NONPRODUCTION(
        one_cluster, evidence_grade=3, seed=7, replicates=100
    )
    point = compute_join_metrics(one_cluster, evidence_grade=3)
    assert interval.lower == interval.upper == point.R_measured
    assert interval.half_width == 0.0


def test_bootstrap_rejects_empty_corpus() -> None:
    with pytest.raises(ValueError):
        bootstrap_R_interval([], evidence_grade=3)
