"""Pin O'Neill (1988) equation 11 coefficients to page 477."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ammin-73-470-oneill-1988.yaml"

EQUATION_11_SEGMENTS = (
    (
        (833.0, 1042.0),
        "mu_O2 = -605812 + 1366.718 T - 182.7955 T ln(T) + 0.10359 T^2",
    ),
    (
        (1042.0, 1184.0),
        "mu_O2 = -519357 + 59.427 T + 8.9276 T ln(T)",
    ),
    (
        (1184.0, 1450.0),
        "mu_O2 = -551159 + 269.404 T - 16.9484 T ln(T)",
    ),
)

ADDITIONAL_MEASURED_RELATIONS = {
    "oneill_1988_fe_fe3o4_measured_relation": (
        "mu_O2 = -607673 + 1060.994 T - 132.3909 T ln(T) + 0.066577 T^2",
        107.0,
    ),
    "oneill_1988_feo_fe3o4_measured_relation": (
        "mu_O2 = -581927 - 65.618 T + 38.7410 T ln(T)",
        306.0,
    ),
    "oneill_1988_cu_cu2o_measured_relation": (
        "mu_O2 = -347705 + 246.096 T - 12.9053 T ln(T)",
        62.0,
    ),
    "oneill_1988_cu2o_cuo_measured_relation": (
        "mu_O2 = -292245 + 377.012 T - 23.1976 T ln(T)",
        117.0,
    ),
}


def test_fe_feo_piecewise_relation_matches_printed_equation_11():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observation = next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == "oneill_1988_fe_feo_measured_relation"
    )
    actual = tuple(
        (tuple(segment["T_range_K"]), segment["relation"])
        for segment in observation["values"]["piecewise_relation"]
    )

    assert actual == EQUATION_11_SEGMENTS
    assert observation["values"]["uncertainty_J_per_mol"] == 100.0
    assert observation["uncertainty"]["mu_O2_J_per_mol"] == 100.0

    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    for observation_id, (relation, sigma) in ADDITIONAL_MEASURED_RELATIONS.items():
        measured = observations[observation_id]
        assert measured["values"]["relation"] == relation
        assert measured["values"]["uncertainty_J_per_mol"] == sigma
        assert measured["uncertainty"]["mu_O2_J_per_mol"] == sigma

    assert document["fidelity_samples"][0]["value"] == ADDITIONAL_MEASURED_RELATIONS[
        "oneill_1988_cu_cu2o_measured_relation"
    ][0]
