from __future__ import annotations

from dataclasses import replace
import json
import sqlite3

from simulator.corpus_version import current_corpus_version
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
from simulator.optimize.result_scope import selector_where
from simulator.optimize.results_store import ResultStore


OLD_FINGERPRINT_KEY_SHAPE = (
    "c42cfb94e75f672bb0f86eea6dacc5e38365b7f0e773b498ad70d5aaaa018b62"
)


def _spec(**overrides: object) -> EvalSpec:
    values: dict[str, object] = {
        "recipe_id": "recipe-a",
        "feedstock_recipe_digest": "recipe-input-a",
        "feedstock_id": "feed-a",
        "profile_id": "profile-a",
        "fidelity": "reduced-real",
        "code_version": "code-a",
        "data_digests": {
            "corpus_version": current_corpus_version(),
            "profile": "profile-data-a",
            "vapor_pressures": "vapour-data-a",
        },
        "hours": 24,
        "mass_kg": 1000.0,
    }
    values.update(overrides)
    return EvalSpec(**values)


def test_non_corpus_fingerprints_are_optimizer_key_neutral() -> None:
    base = _spec()
    fingerprints_changed = replace(
        base,
        code_version="code-b",
        data_digests={
            "corpus_version": current_corpus_version(),
            "profile": "profile-data-b",
            "vapor_pressures": "vapour-data-b",
            "provider_fingerprint": "provider-data-b",
        },
        lab_alpha_digest="alpha-b",
        geometry_digest="geometry-b",
        oxide_vapor_ceiling_digest="ceiling-b",
        sink_channel_evidence_digests={"sink": "sink-b"},
        target_spec_digest="target-digest-b",
        target_provenance={"source_digest": "source-b"},
        vapor_pressure_provider_code_fingerprint="provider-code-b",
    )

    assert cache_key(fingerprints_changed) == cache_key(base)
    payload = json.loads(canonical_evalspec_json(fingerprints_changed))
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "code_version",
        "data_digests",
        "lab_alpha_digest",
        "geometry_digest",
        "oxide_vapor_ceiling_digest",
        "sink_channel_evidence_digests",
        "target_spec_digest",
        "target_provenance",
        "vapor_pressure_provider_code_fingerprint",
        "schema_version",
    ):
        assert forbidden not in serialized


def test_recipe_run_inputs_and_corpus_version_remain_identity() -> None:
    base = _spec(effective_exposed_area_m2=0.25, area_basis="measured")
    assert cache_key(replace(base, hours=48)) != cache_key(base)
    assert cache_key(replace(base, recipe_id="recipe-b")) != cache_key(base)
    assert cache_key(replace(base, effective_exposed_area_m2=0.5)) != cache_key(base)
    assert cache_key(replace(base, vapor_pressure_provider_id="provider-b")) != cache_key(
        base
    )

    prior_corpus = replace(
        base,
        data_digests={**base.data_digests, "corpus_version": "prior-corpus"},
    )
    assert cache_key(prior_corpus) != cache_key(base)
    assert cache_key(base) != OLD_FINGERPRINT_KEY_SHAPE
    assert current_corpus_version() == (
        "analytical-corpus-2026-07-31-vapour-rail-key-v2"
    )


def test_physics_constraints_are_first_class_identity_not_data_fingerprint() -> None:
    """Feasibility thresholds must miss cache; YAML-file fingerprints must not.

    physics_constraints rode under data_digests and was collaterally dropped when
    VR-3 made digests provenance-only. Restore it as a top-level identity field
    without resurrecting the nested data_digests blob or other fingerprints.
    """
    base = _spec(
        data_digests={
            "corpus_version": current_corpus_version(),
            "physics_constraints": "constraints-loose",
            "profile": "profile-a",
            "vapor_pressures": "vapour-a",
        }
    )
    threshold_changed = replace(
        base,
        data_digests={
            **base.data_digests,
            "physics_constraints": "constraints-tight",
        },
    )
    data_file_comment_changed = replace(
        base,
        data_digests={
            **base.data_digests,
            "profile": "profile-comment-only",
            "vapor_pressures": "vapour-comment-only",
        },
    )

    assert cache_key(threshold_changed) != cache_key(base)
    assert cache_key(data_file_comment_changed) == cache_key(base)

    payload = json.loads(canonical_evalspec_json(base))
    assert payload["physics_constraints"] == "constraints-loose"
    assert "data_digests" not in payload
    # Provenance fingerprints must not re-enter under any nested key.
    assert "profile" not in payload
    assert "vapor_pressures" not in payload
    assert payload.get("profile_id") == "profile-a"  # id remains; digest stays out


def test_sparse_digest_metadata_defaults_to_current_corpus_version() -> None:
    explicit = _spec(data_digests={"corpus_version": current_corpus_version()})
    provenance_only = _spec(data_digests={"profile": "metadata-only"})
    assert cache_key(provenance_only) == cache_key(explicit)


def test_result_selector_and_index_exclude_code_and_data_fingerprints(tmp_path) -> None:
    where, params = selector_where(
        "feed-a",
        profile_id="profile-a",
        fidelity="reduced-real",
        code_version="ignored-code",
        data_digests={"ignored": "digest"},
        result_scope={},
    )
    assert "code_version" not in where
    assert "data_digests" not in where
    assert params == ("feed-a", "profile-a", "reduced-real")

    path = tmp_path / "results.sqlite"
    ResultStore(path)
    with sqlite3.connect(path) as conn:
        columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info(idx_results_current_selector)")
        ]
    assert columns == [
        "feedstock_id",
        "profile_id",
        "fidelity",
        "result_scope",
        "corpus_version",
    ]
