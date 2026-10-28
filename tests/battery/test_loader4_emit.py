from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import iter_extract_observations
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


def _source_observations(result, filename: str):
    key = f"data/literature/extracts/{filename}"
    return [result.observations[oid] for oid in result.observations_by_source[key]]


def test_real_nonyield_points_emit_unavailable_rows_with_conditions(
    tmp_path: Path,
) -> None:
    filename = "hanson-jones-1998-am-cr-partitioning-olivine-liquid.yaml"
    result = _migrate_real_extract(tmp_path, filename, write=False)
    observations = _source_observations(result, filename)
    parents = (
        "hj98_t3_olivine_tio2",
        "hj98_t3_glass_mno",
        "hj98_t3_olivine_mno",
        "hj98_t3_glass_na2o",
    )
    unavailable = [
        observation
        for observation in observations
        if any(
            observation.observation_id.startswith(
                f"hanson-jones-1998-am-cr-partitioning-olivine-liquid::{parent}::"
            )
            for parent in parents
        )
        and observation.value.kind is ValueKind.UNAVAILABLE
    ]

    assert len(observations) == 418
    assert len(unavailable) == 18
    assert all(observation.value.point is None for observation in unavailable)


def test_real_point_conditions_override_inferred_conditions(tmp_path: Path) -> None:
    filename = "hanson-jones-1998-am-cr-partitioning-olivine-liquid.yaml"
    result = _migrate_real_extract(tmp_path, filename, write=False)
    prefix = (
        "hanson-jones-1998-am-cr-partitioning-olivine-liquid::"
        "hj98_t3_olivine_tio2::T=1498.15:row=ppg-3:"
    )
    observation = next(
        item
        for item in _source_observations(result, filename)
        if item.observation_id.startswith(prefix)
    )

    assert observation.value.kind is ValueKind.UNAVAILABLE
    assert observation.value.point is None
    assert observation.point_conditions["temperature_K"].state.value == Decimal(
        "1498.15"
    )
    oxygen = observation.point_conditions["fO2_control"]
    assert oxygen.state.value == "IW-1"
    assert oxygen.locator is not None
    assert oxygen.locator.table == "3"
    temperature = observation.point_conditions["temperature_K"]
    assert temperature.locator is not None
    assert oxygen.locator != temperature.locator


def test_real_janaf_tabulated_delta_fg_points_use_the_exploded_buffer(
    tmp_path: Path,
) -> None:
    filename = "janaf-4th.yaml"
    source_id = "janaf-4th"
    source_path = REPO_ROOT / "data" / "literature" / "extracts" / filename
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    parents = []
    source_items = 0
    for _formula, observation in iter_extract_observations(document):
        values = observation.get("values")
        items = values.get("tabulated_delta_fG_kJ_mol") if isinstance(values, dict) else None
        if isinstance(items, list):
            parents.append(str(observation["observation_id"]))
            source_items += len(items)

    result = _migrate_real_extract(tmp_path, filename, write=False)
    observations = _source_observations(result, filename)
    children = [
        observation
        for observation in observations
        if any(
            observation.observation_id.startswith(f"{source_id}::{parent}::T=")
            for parent in parents
        )
    ]
    first = next(
        observation
        for observation in children
        if observation.observation_id.startswith(
            f"{source_id}::JANAF1998_PO_formation_tabulation::T=298.15:"
        )
    )

    assert source_items == 53
    assert len(children) == source_items
    assert len(observations) == 98
    assert first.value.kind is ValueKind.UNAVAILABLE
    assert first.value.unavailable_reason == "unsupported printed unit for delta_fG"
    assert first.point_conditions["temperature_K"].state.value == Decimal("298.15")
