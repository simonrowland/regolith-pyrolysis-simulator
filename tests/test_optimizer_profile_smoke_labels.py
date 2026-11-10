from pathlib import Path

import yaml


PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "optimize_profiles"
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
