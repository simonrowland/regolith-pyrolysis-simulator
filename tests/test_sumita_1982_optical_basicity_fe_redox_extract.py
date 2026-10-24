from pathlib import Path

import yaml

from simulator.battery.enums import ValenceValueForm
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sumita-1982-optical-basicity-fe-redox.yaml"


def test_sumita_figures_are_deferred_context_and_no_numeric_points_are_admitted():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    iron_figures = source["species"]["Fe"]["context"]
    figure9 = next(item for item in iron_figures if item["observation_id"].endswith("figure9_fe_redox_deferred"))
    figure10 = next(item for item in iron_figures if item["observation_id"].endswith("figure10_fe_redox_deferred"))
    for figure, page in ((figure9, 5), (figure10, 6)):
        assert figure["locator"]["page"] == page
        assert figure["values"]["quantity"] == "valence_ratio"
        assert figure["values"]["valence_basis"]["value_form"] == ValenceValueForm.LOG10_RATIO.value
        assert "points" not in figure["values"]
        assert figure["values"]["digitisation"] == "deferred to a figure pass"
        assert all(isinstance(series["marker_count"], int) for series in figure["values"]["series"])

    optical = next(
        item for item in source["species"]["Pb"]["context"]
        if item["observation_id"] == "sumita_1982_optical_basicity_context"
    )
    assert "no optical-basicity quantity token" in optical["values"]["computation"]
