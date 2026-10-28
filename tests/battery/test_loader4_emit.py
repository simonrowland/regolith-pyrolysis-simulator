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


def test_real_rows_that_fail_series_admission_stay_as_parent_context(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "kems-011-wetzel-gail-2013.yaml",
            (
                "wetzel_gail_2013_table5_quoted_model",
                "wetzel_gail_2013_table5_quoted_model_gibbs",
                "wetzel_gail_2013_table6_quoted_model",
                "wetzel_gail_2013_table6_quoted_model_gibbs",
            ),
            38,
            17,
            28,
        ),
        (
            "kems-035-sauerborn-2005.yaml",
            ("sauerborn_2005_table57_evidence_ranking_deep2",),
            4,
            74,
            2,
        ),
    )

    for (
        filename,
        parent_ids,
        expected_rows,
        expected_observations,
        expected_hard,
    ) in cases:
        source_id = Path(filename).stem
        document = yaml.safe_load(
            (REPO_ROOT / "data" / "literature" / "extracts" / filename).read_text(
                encoding="utf-8"
            )
        )
        raw_parents = {
            str(observation["observation_id"]): observation
            for _formula, observation in iter_extract_observations(document)
            if observation.get("observation_id") in parent_ids
        }
        assert set(raw_parents) == set(parent_ids)
        assert sum(
            len(raw_parents[parent_id]["values"]["rows"])
            for parent_id in parent_ids
        ) == expected_rows

        result = _migrate_real_extract(
            tmp_path / source_id,
            filename,
            write=False,
        )
        observations = _source_observations(result, filename)
        assert len(observations) == expected_observations
        assert result.validation is not None
        assert len(result.validation.hard_issues) == expected_hard

        for parent_id in parent_ids:
            observation_id = f"{source_id}::{parent_id}"
            parent = result.observations[observation_id]
            assert parent.value.kind is ValueKind.CATEGORICAL
            assert parent.value.categorical == "bound_not_point_ordering"
            assert all(
                "derived_from" not in row
                for row in raw_parents[parent_id]["values"]["rows"]
            )
            assert not any(
                observation.observation_id.startswith(f"{observation_id}::")
                for observation in observations
            )
