from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yakovlev-2024-cai-vaporization.yaml"
EXPECTED = {
    "yakovlev_2024_fig3a_mgal2o4_figure_only": (
        "MgAl2O4",
        "Lewis relation (1)",
        [
            "yakovlev_2024_fig2b_mgo_figure_only",
            "yakovlev_2024_fig2b_al2o3_figure_only",
        ],
    ),
    "yakovlev_2024_fig3a_ca2mgsi2o7_figure_only": (
        "Ca2MgSi2O7",
        "Lewis relation (2)",
        [
            "yakovlev_2024_fig2b_cao_figure_only",
            "yakovlev_2024_fig2b_mgo_figure_only",
            "yakovlev_2024_fig2b_sio2_figure_only",
        ],
    ),
    "yakovlev_2024_fig3a_ca2al2sio7_figure_only": (
        "Ca2Al2SiO7",
        "Lewis relation (3)",
        [
            "yakovlev_2024_fig2b_cao_figure_only",
            "yakovlev_2024_fig2b_al2o3_figure_only",
            "yakovlev_2024_fig2b_sio2_figure_only",
        ],
    ),
}


def test_yakovlev_2024_fig3a_compound_activities_are_model_derived() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }

    assert set(EXPECTED) <= observations.keys()
    for observation_id, (formula, relation, inputs) in EXPECTED.items():
        values = observations[observation_id]["values"]
        assert values["species_formula"] == formula
        assert values["method_class"] == "model_derived"
        assert values["model"] == "Lewis relations (Lewis and Randall, 1923), equations (1)–(3)."
        assert values["derived_from"] == inputs
        assert values["derivation"]["inputs"] == inputs
        derivation = values["derivation"]["relation"]
        assert relation in derivation
        assert "not reproduced in this paper" in derivation
        assert "Shornikov et al. (1996)" in derivation
        assert "Bale et al. (2016) (FactSage)" in derivation
        assert "Shornikov (2017)" in derivation


def test_yakovlev_2024_isolated_migration_preserves_point_conditions() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "yakovlev-2024-cai-vaporization"
    ]
    assert len(observations) == 167
    has_printed_temperature = False
    for observation in observations:
        assert observation.point_conditions is not None
        for key in ("temperature_K", "composition"):
            condition = observation.point_conditions[key]
            assert condition.locator is not None
            has_printed_temperature |= key == "temperature_K" and condition.state.is_value
    assert has_printed_temperature
