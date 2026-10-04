"""b-718: sample printed_composition only from declaration or universal row maps."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import migrate
from simulator.battery.records import as_decimal
from simulator.battery.waypoints import normalized_composition
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _base() -> dict:
    return yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))


def _obs(template: dict, oid: str, *, composition: dict | None = None, experiment: str | None = None) -> dict:
    row = yaml.safe_load(yaml.safe_dump(template))
    row["observation_id"] = oid
    if experiment is not None:
        row["experiment"] = experiment
    if composition is not None:
        row.setdefault("values", {})
        row["values"]["composition_wt_pct"] = dict(composition)
        row["values"].pop("series", None)
        row["values"]["pressure_atm"] = 1.0
    return row


def test_subset_composition_stays_on_owner_row_not_experiment(tmp_path: Path) -> None:
    """A map on one row must not become experiment.sample for sibling rows."""

    extract = _base()
    template = extract["species"]["Na"]["observations"][0]
    owner = _obs(template, "owner_with_map", composition={"SiO2": 60, "MgO": 40})
    sibling = _obs(template, "sibling_without_map")
    sibling["values"].pop("series", None)
    sibling["values"]["pressure_atm"] = 1.0
    extract["species"]["Na"]["observations"] = [owner, sibling]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    owner_obs = result.observations["fixture-source::owner_with_map"]
    sibling_obs = result.observations["fixture-source::sibling_without_map"]
    assert owner_obs.experiment_id == sibling_obs.experiment_id
    experiment = result.experiments[owner_obs.experiment_id]

    assert experiment.sample.printed_composition is None or not (
        experiment.sample.printed_composition.state.is_value
    ), "subset map must not sit on experiment.sample"

    owner_pc = (owner_obs.point_conditions or {}).get("printed_composition")
    assert owner_pc is not None and owner_pc.state.is_value
    assert as_decimal(owner_pc.state.value["SiO2"]) == as_decimal("60")

    sibling_norm = normalized_composition(experiment, None, sibling_obs)
    assert sibling_norm.selected is None or sibling_norm.selected.route != (
        "normalized_printed_composition"
    )


def test_declared_sample_survives_unequal_observation_map(tmp_path: Path) -> None:
    """Registry sample.printed_composition must not be cleared by a foreign row map."""

    extract = _base()
    extract["experiments"] = [
        {
            "experiment_id": "declared-melt",
            "sample": {
                "printed_composition": {
                    "state": {"tag": "value", "value": {"SiO2": "50", "MgO": "50"}},
                    "locator": {"table": "1", "page": 1},
                },
            },
        }
    ]
    template = extract["species"]["Na"]["observations"][0]
    foreign = _obs(
        template,
        "foreign_row",
        composition={"SiO2": 90, "MgO": 10},
        experiment="declared-melt",
    )
    bare = _obs(template, "bare_row", experiment="declared-melt")
    bare["values"].pop("series", None)
    bare["values"]["pressure_atm"] = 1.0
    extract["species"]["Na"]["observations"] = [foreign, bare]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    experiment = next(
        item
        for item in result.experiments.values()
        if item.experiment_id.endswith("::experiment::declared-melt")
    )
    printed = experiment.sample.printed_composition
    assert printed is not None and printed.state.is_value
    assert as_decimal(printed.state.value["SiO2"]) == as_decimal("50")
    assert as_decimal(printed.state.value["MgO"]) == as_decimal("50")

    bare_obs = result.observations["fixture-source::bare_row"]
    norm = normalized_composition(experiment, None, bare_obs)
    assert norm.selected is not None
    assert norm.selected.route == "normalized_printed_composition"


def test_universal_row_maps_promote_to_experiment_sample(tmp_path: Path) -> None:
    """When every row carries the same map, experiment.sample may hold it."""

    extract = _base()
    template = extract["species"]["Na"]["observations"][0]
    recipe = {"SiO2": 55, "MgO": 45}
    extract["species"]["Na"]["observations"] = [
        _obs(template, "row_a", composition=recipe),
        _obs(template, "row_b", composition=recipe),
    ]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    obs_a = result.observations["fixture-source::row_a"]
    experiment = result.experiments[obs_a.experiment_id]
    printed = experiment.sample.printed_composition
    assert printed is not None and printed.state.is_value
    assert as_decimal(printed.state.value["SiO2"]) == as_decimal("55")
