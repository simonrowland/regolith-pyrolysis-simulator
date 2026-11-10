import math
from pathlib import Path

import yaml

from simulator.optimize.objective import objective_definitions
from simulator.optimize.profiles import load_profile


PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "optimize_profiles"
GLASS_AND_METAL_OBJECTIVES = frozenset(
    {
        "pure_silica_glass_kg",
        "industrial_mixed_glass_kg",
        "refractory_ceramic_rump_kg",
        "metals_total_kg",
        "metals_plus_o2_kg",
    }
)
PRODUCT_OBJECTIVES = frozenset(
    {
        "pure_silica_glass_kg",
        "industrial_mixed_glass_kg",
        "refractory_ceramic_rump_kg",
        "metals_total_kg",
        "metals_plus_o2_kg",
        "oxygen_kg",
        "oxygen_stored_kg",
        "oxygen_vented_kg",
        "O2_kg",
        "o2_kg",
    }
)


def test_c0_profiles_with_product_objectives_are_labeled_smoke() -> None:
    unlabeled = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        profile = yaml.safe_load(path.read_text())
        run = profile.get("run", {})
        metrics = {objective.get("metric") for objective in profile.get("objectives", [])}
        if run.get("campaign") != "C0" or not metrics.intersection(PRODUCT_OBJECTIVES):
            continue
        labels = (
            profile.get("description", ""),
            profile.get("objective_emphasis", ""),
        )
        if any(
            not isinstance(label, str) or not label.startswith("C0 SMOKE ONLY:")
            for label in labels
        ):
            unlabeled.append(path.name)

    assert not unlabeled, (
        "C0 does not realize these final product objectives; label the profile "
        f"as a screening smoke run: {', '.join(unlabeled)}"
    )


def test_c0_smoke_labels_keep_glass_and_metal_off_the_scoring_path() -> None:
    """d-377: withhold unclaimed C0 products. No renormalization, no zero score."""

    still_scored = []
    smoke_profiles: list[tuple[str, dict]] = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        profile = load_profile(path.stem)
        declared = {
            objective.get("metric") for objective in profile.get("objectives", [])
        }
        if "C0" != (profile.get("run") or {}).get("campaign"):
            continue
        if not declared.intersection(GLASS_AND_METAL_OBJECTIVES):
            continue
        scored = {definition.metric for definition in objective_definitions(profile)}
        if scored & GLASS_AND_METAL_OBJECTIVES:
            still_scored.append(path.name)
        smoke_profiles.append((path.name, profile))

    assert not still_scored, (
        "C0 smoke profiles still score glass or metal objectives: "
        f"{', '.join(still_scored)}"
    )
    assert smoke_profiles, "expected C0 profiles that declare glass or metal objectives"

    from simulator.optimize.evaluate import _notes_with_c0_smoke_withhold
    from simulator.optimize.objective import (
        c0_smoke_withheld_objective_note,
        c0_smoke_withheld_weight_fraction,
        objective_importance_evidence,
        unrenormalized_study_scalar_weights,
    )

    for name, profile in smoke_profiles:
        evidence = objective_importance_evidence(profile)
        withheld_rows = [
            row for row in evidence if row.metric in GLASS_AND_METAL_OBJECTIVES
        ]
        retained_rows = [
            row for row in evidence if row.metric not in GLASS_AND_METAL_OBJECTIVES
        ]
        assert withheld_rows, name
        assert retained_rows, name
        total = math.fsum(row.weight for row in evidence)
        withheld_weight = math.fsum(row.weight for row in withheld_rows)
        fraction = c0_smoke_withheld_weight_fraction(profile)
        assert fraction is not None
        assert fraction == withheld_weight / total
        assert 0.0 < fraction < 1.0, name
        note = c0_smoke_withheld_objective_note(profile)
        assert note is not None
        assert note.startswith("campaign_does_not_claim_product: ")
        assert (
            note.count("reason=campaign_does_not_claim_product") == len(withheld_rows)
        )
        for row in withheld_rows:
            assert (
                f"{row.metric} weight={format(row.weight, '.12g')} "
                "reason=campaign_does_not_claim_product"
            ) in note, name
        assert f"withheld_weight_fraction={format(fraction, '.12g')}" in note, name
        assert _notes_with_c0_smoke_withhold((), profile) == (note,)
        weights = unrenormalized_study_scalar_weights(profile)
        # Declared retained weights. Renormalizing would divide each by
        # (1 - fraction) and make them sum to `total`.
        assert weights == tuple(row.weight for row in retained_rows)
        assert math.isclose(
            math.fsum(weights),
            total - withheld_weight,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        assert not math.isclose(math.fsum(weights), total)
        scored_metrics = {
            definition.metric for definition in objective_definitions(profile)
        }
        assert not scored_metrics & GLASS_AND_METAL_OBJECTIVES
        # Absent, not scored as 0. A unit score on each retained objective
        # is the retained weight sum, which stays positive.
        scalar = math.fsum(weights)
        assert scalar != 0.0
        assert scalar > 0.0
