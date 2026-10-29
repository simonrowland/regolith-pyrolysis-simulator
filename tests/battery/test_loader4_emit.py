from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate
from simulator.battery.enums import ValueKind
from simulator.battery.migrate import iter_extract_observations
from tests.battery.test_migrate import REPO_ROOT, _migrate_real_extract


def _source_observations(result, filename: str):
    key = f"data/literature/extracts/{filename}"
    return [result.observations[oid] for oid in result.observations_by_source[key]]


def test_real_nonyield_points_do_not_emit_generic_unavailable_rows(
    tmp_path: Path,
) -> None:
    filename = "hanson-jones-1998-am-cr-partitioning-olivine-liquid.yaml"
    result = _migrate_real_extract(tmp_path, filename, write=False)
    observations = _source_observations(result, filename)
    source = yaml.safe_load(
        (REPO_ROOT / "data" / "literature" / "extracts" / filename).read_text(
            encoding="utf-8"
        )
    )
    raw_unavailable = [
        point
        for body in source["species"].values()
        for row in body["observations"]
        for point in row.get("values", {}).get("points", [])
        if point.get("value") is None
    ]
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

    assert len(raw_unavailable) == 18
    assert all(
        point.get("value_as_published") and point.get("unavailable_reason")
        for point in raw_unavailable
    )
    assert len(observations) == 400
    assert unavailable == []


def test_real_point_conditions_override_inferred_conditions(tmp_path: Path) -> None:
    filename = "hanson-jones-1998-am-cr-partitioning-olivine-liquid.yaml"
    result = _migrate_real_extract(tmp_path, filename, write=False)
    prefix = (
        "hanson-jones-1998-am-cr-partitioning-olivine-liquid::"
        "hj98_t3_olivine_mno::T=1498.15:row=ppg-3:"
    )
    observation = next(
        item
        for item in _source_observations(result, filename)
        if item.observation_id.startswith(prefix)
    )

    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal("0.20")
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
    parents = {}
    expected_points = []
    source_items = 0
    for _formula, observation in iter_extract_observations(document):
        values = observation.get("values")
        items = (
            values.get("tabulated_delta_fG_kJ_mol")
            if isinstance(values, dict)
            else None
        )
        if isinstance(items, list):
            parent_id = str(observation["observation_id"])
            parents[parent_id] = []
            source_items += len(items)
            for item in items:
                point = (Decimal(str(item["T_K"])), Decimal(str(item["delta_fG"])))
                parents[parent_id].append(point)
                expected_points.append(point)

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
    assert len(parents) == 6
    assert len(children) == source_items
    assert len(observations) == 98
    assert all(observation.value.kind is ValueKind.POINT for observation in children)
    assert sorted(
        (
            observation.point_conditions["temperature_K"].state.value,
            observation.value.point,
        )
        for observation in children
    ) == sorted(expected_points)
    assert all(
        observation.derivation is not None
        and observation.derivation.output_unit == "kJ_per_mol"
        for observation in children
    )
    assert Decimal("-47.139") in {value for _temperature, value in expected_points}
    assert Decimal("-316.748") in {value for _temperature, value in expected_points}
    assert first.point_conditions["temperature_K"].state.value == Decimal("298.15")


def test_janaf_unit_refusal_keeps_the_printed_parent_series(
    tmp_path: Path,
    monkeypatch,
) -> None:
    filename = "janaf-4th.yaml"
    source_id = "janaf-4th"
    original_empty_value_from_payload = migrate.empty_value_from_payload

    def refuse_tabulated_point_units(values, obs_type, units, quantity=None):
        selected, exploded, source_selection = original_empty_value_from_payload(
            values, obs_type, units, quantity
        )
        if isinstance(values, dict) and isinstance(
            values.get("tabulated_delta_fG_kJ_mol"), list
        ):
            exploded = [
                {**item, "units": "unsupported printed units"} for item in exploded
            ]
        return selected, exploded, source_selection

    monkeypatch.setattr(
        migrate, "empty_value_from_payload", refuse_tabulated_point_units
    )
    result = _migrate_real_extract(
        tmp_path / "unit-refusal", filename, write=False
    )
    observations = _source_observations(result, filename)
    source_path = REPO_ROOT / "data" / "literature" / "extracts" / filename
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    expected_series = {}
    for _formula, observation in iter_extract_observations(document):
        values = observation.get("values")
        items = (
            values.get("tabulated_delta_fG_kJ_mol")
            if isinstance(values, dict)
            else None
        )
        if isinstance(items, list):
            expected_series[str(observation["observation_id"])] = tuple(
                (Decimal(str(item["T_K"])), Decimal(str(item["delta_fG"])))
                for item in items
            )

    by_id = {observation.observation_id: observation for observation in observations}
    assert len(expected_series) == 6
    for parent_id, points in expected_series.items():
        parent = by_id[f"{source_id}::{parent_id}"]
        assert parent.value.kind is ValueKind.SERIES
        assert parent.value.series == points
    refused_children = [
        observation
        for observation in observations
        if any(
            observation.observation_id.startswith(f"{source_id}::{parent_id}::T=")
            for parent_id in expected_series
        )
    ]
    assert len(refused_children) == 53
    assert all(
        observation.value.kind is ValueKind.UNAVAILABLE
        for observation in refused_children
    )


def test_real_leister_valence_limit_is_stored_as_a_bound(tmp_path: Path) -> None:
    filename = "leister-1999-fe-v-redox-2000c.yaml"
    result = _migrate_real_extract(tmp_path, filename, write=False)
    observations = _source_observations(result, filename)
    bound = next(
        observation
        for observation in observations
        if observation.observation_id.startswith(
            "leister-1999-fe-v-redox-2000c::leister_1999_table3_v5_valence_fraction::"
        )
        and ":row=6:" in observation.observation_id
    )

    assert len(observations) == 54
    assert bound.value.kind is ValueKind.BOUND
    assert bound.value.bound_operator == "<"
    assert bound.value.bound_value == Decimal("0.005")


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
