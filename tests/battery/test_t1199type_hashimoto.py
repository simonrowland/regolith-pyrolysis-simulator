from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT = REPO_ROOT / "data/literature/extracts/kems-015-hashimoto-1983.yaml"
EXPECTED_RUN_FORM = "molten_spherule"
EXPECTED_LOADED_GEOMETRY = (
    "As loaded: tubular pellet pressed from powder; outer diameter 4 mm; length "
    "4 mm; straight hole 1.2 mm diameter."
)


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        self.flatten_mapping(node)
        return yaml.SafeLoader.construct_mapping(self, node, deep=deep)


def _assert_hashimoto_run_geometry(document: dict) -> None:
    runs = [
        experiment
        for experiment in document["experiments"]
        if experiment["experiment_id"] != "fcmas-free-evap-series"
    ]
    assert len(runs) == 31
    for experiment in runs:
        sample = experiment["sample"]
        assert sample["form"]["state"]["value"] == EXPECTED_RUN_FORM
        assert sample["form"]["locator"]["published_page"] == 113
        assert "single spherule after quenching" in sample["form"]["locator"]["note"]
        assert "point contact with the graphite crucible" in sample["form"]["locator"]["note"]
        assert sample["characterization"]["state"]["value"] == EXPECTED_LOADED_GEOMETRY
        assert sample["characterization"]["locator"]["published_page"] == 112
        area = sample["surface_area_m2"]
        assert area["state"] == {"tag": "unknown", "reason": "not_published"}
        assert area["locator"]["published_page"] == 113


def test_hashimoto_experiment_geometry_is_page_pinned_and_rejects_mutation() -> None:
    document = yaml.load(EXTRACT.read_text(), Loader=_UniqueKeyLoader)
    _assert_hashimoto_run_geometry(document)

    mutated = deepcopy(document)
    geometry = mutated["experiments"][1]["sample"]["characterization"]["state"]["value"]
    mutated["experiments"][1]["sample"]["characterization"]["state"]["value"] = geometry.replace(
        "outer diameter 4 mm", "outer diameter 5 mm"
    )
    with pytest.raises(AssertionError):
        _assert_hashimoto_run_geometry(mutated)


def test_hashimoto_typed_geometry_survives_isolated_migration(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-015-hashimoto-1983.yaml")

    assert len(result.observations) == 681
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 18
    assert len(result.experiments) == 33
    assert len(result.benches) == 1

    experiment = next(
        experiment
        for experiment in result.experiments.values()
        if experiment.experiment_id.endswith("run-17c1")
    )
    assert experiment.sample.form is not None
    assert experiment.sample.form.state.value == EXPECTED_RUN_FORM
    assert "single spherule after quenching" in experiment.sample.form.locator.note
    assert experiment.sample.characterization is not None
    assert experiment.sample.characterization.state.value == EXPECTED_LOADED_GEOMETRY
    assert experiment.sample.surface_area_m2 is not None
    assert experiment.sample.surface_area_m2.state.is_unknown
    assert experiment.sample.surface_area_m2.state.reason == "not_published"

    bench = next(
        bench
        for bench in result.benches.values()
        if bench.id.endswith("graphite-vacuum-furnace")
    )
    assert bench.cell_material_and_liner is not None
    assert bench.cell_material_and_liner.state.value == "high-purity graphite crucible"
