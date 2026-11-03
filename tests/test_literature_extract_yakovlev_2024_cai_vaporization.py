"""Migration pins for Yakovlev and Shornikov (2024), CAI vaporization."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "yakovlev-2024-cai-vaporization"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def test_yakovlev_2024_authored_fig3a_activities_migrate_as_reductions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(document, path=EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    series_inputs = {
        "yakovlev_2024_fig3a_mgal2o4_figure_only": (
            "MgAl2O4",
            [
                "yakovlev_2024_fig2b_mgo_figure_only",
                "yakovlev_2024_fig2b_al2o3_figure_only",
            ],
        ),
        "yakovlev_2024_fig3a_ca2mgsi2o7_figure_only": (
            "Ca2MgSi2O7",
            [
                "yakovlev_2024_fig2b_cao_figure_only",
                "yakovlev_2024_fig2b_mgo_figure_only",
                "yakovlev_2024_fig2b_sio2_figure_only",
            ],
        ),
        "yakovlev_2024_fig3a_ca2al2sio7_figure_only": (
            "Ca2Al2SiO7",
            [
                "yakovlev_2024_fig2b_cao_figure_only",
                "yakovlev_2024_fig2b_al2o3_figure_only",
                "yakovlev_2024_fig2b_sio2_figure_only",
            ],
        ),
    }
    for observation_id, (formula, inputs) in series_inputs.items():
        source = next(
            observation
            for observation in document["species"][formula]["observations"]
            if observation["observation_id"] == observation_id
        )
        values = source["values"]
        assert values["method_class"] == "measured_reduced"
        assert values["derived_from"] == inputs
        assert values["derivation"]["inputs"] == inputs
        assert "not reproduced in this paper" in values["derivation"]["relation"]

    mg_point_id = (
        f"{SOURCE_ID}::yakovlev_2024_fig3a_mgal2o4_figure_only::"
        "points:h=e3d2be7417ab"
    )
    mg_point = result.observations[mg_point_id]
    assert mg_point.evidence.class_.value == "measured_reduced"
    assert mg_point.derivation is not None
    assert any("fig2b_mgo_figure_only" in item for item in mg_point.derived_from)
    assert any("fig2b_al2o3_figure_only" in item for item in mg_point.derived_from)
    assert any("fig2b_mgo_figure_only" in item for item in mg_point.derivation.inputs)
    assert any("fig2b_al2o3_figure_only" in item for item in mg_point.derivation.inputs)

    ca_point = result.observations[
        f"{SOURCE_ID}::yakovlev_2024_fig2a_ca_figure_only::"
        "T=2039.79:h=7b20b65172ae"
    ]
    assert ca_point.identity.temperature_K.is_value
    assert str(ca_point.identity.temperature_K.value) == "2039.79"
    composition = ca_point.point_conditions["composition"]
    assert composition.state.is_unknown
    assert composition.locator is not None
    assert composition.locator.published_page == 31

    for observation in result.observations.values():
        assert observation.point_conditions
        assert observation.identity.temperature_K is not None
        assert all(
            condition.locator is not None and condition.locator.has_location()
            for condition in observation.point_conditions.values()
        )
        if observation.identity.temperature_K.is_unknown:
            temperature = observation.point_conditions["temperature_K"]
            assert temperature.state.is_unknown
            assert temperature.locator is not None
            assert temperature.locator.has_location()
