"""b-718: sample printed_composition only from declaration or universal row maps."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import migrate
from simulator.battery.records import as_decimal
from simulator.battery.waypoints import normalized_composition
from tests.battery.test_migrate import (
    FIXTURE_EXTRACT,
    _named_extract,
    _write_min_tree,
    _write_multi_extract_tree,
)


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


def test_composition_free_row_in_later_extract_blocks_sample_promotion(
    tmp_path: Path,
) -> None:
    """A later extract's bare row must be included in the promotion decision."""

    first = _named_extract("aaa-first", "10.1234/SHARED")
    first["experiments"] = [{"experiment_id": "shared"}]
    template = first["species"]["Na"]["observations"][0]
    first["species"]["Na"]["observations"] = [
        _obs(template, "owner", composition={"SiO2": 60, "MgO": 40}, experiment="shared")
    ]
    first_root = _write_multi_extract_tree(tmp_path / "first", [("aaa-first", first)])
    first_result = migrate(first_root, write=False)
    experiment_id = next(
        eid for eid in first_result.experiments if eid.endswith("::experiment::shared")
    )

    second = _named_extract("bbb-second", "10.1234/SHARED")
    second_template = second["species"]["Na"]["observations"][0]
    bare = _obs(second_template, "bare", experiment=experiment_id)
    bare["values"].pop("series", None)
    bare["values"]["pressure_atm"] = 1.0
    second["species"]["Na"]["observations"] = [bare]
    root = _write_multi_extract_tree(
        tmp_path / "complete", [("aaa-first", first), ("bbb-second", second)]
    )

    result = migrate(root, write=False)
    bare_obs = result.observations["bbb-second::bare"]
    experiment = result.experiments[bare_obs.experiment_id]
    assert experiment.sample.printed_composition is None
    assert normalized_composition(experiment, None, bare_obs).selected is None


# --- Pin (b718 ROR fix): printed-map selection before the fingerprint helper
# is factored out of _printed_composition_from_roots. These hold on the
# pre-move code and must keep holding after the move.


def _vocab():
    from simulator.battery.migrate import load_lab_parameter_vocabulary

    return load_lab_parameter_vocabulary()


def test_pin_printed_roots_single_map_normalises_decimals() -> None:
    from simulator.battery.migrate import _printed_composition_from_roots

    got = _printed_composition_from_roots(
        [("values", {"locator": {"table": "1"}, "composition_wt_pct": {"SiO2": 60.20, "Al2O3": "26.0"}})],
        _vocab(),
    )
    assert got is not None and got.state.is_value
    assert got.state.value == {"Al2O3": "26", "SiO2": "60.2"}


def test_pin_printed_roots_equal_maps_with_different_spelling_agree() -> None:
    from simulator.battery.migrate import _printed_composition_from_roots

    got = _printed_composition_from_roots(
        [
            ("values", {"locator": {"table": "1"}, "composition_wt_pct": {"SiO2": "60.0", "MgO": 40}}),
            ("equipment", {"locator": {"table": "2"}, "oxides_wt_pct": {"MgO": "40.00", "SiO2": 60}}),
        ],
        _vocab(),
    )
    assert got is not None and got.state.value == {"MgO": "40", "SiO2": "60"}


def test_pin_printed_roots_charge_key_beats_residual_map() -> None:
    from simulator.battery.migrate import _printed_composition_from_roots

    got = _printed_composition_from_roots(
        [
            (
                "values",
                {
                    "locator": {"table": "1"},
                    "starting_glass_wt_pct": {"SiO2": 50, "MgO": 50},
                    "composition_wt_pct": {"SiO2": 70, "MgO": 30},
                },
            )
        ],
        _vocab(),
    )
    assert got is not None and got.state.value == {"MgO": "50", "SiO2": "50"}


def test_pin_printed_roots_conflicting_non_charge_maps_select_nothing() -> None:
    from simulator.battery.migrate import _printed_composition_from_roots

    got = _printed_composition_from_roots(
        [
            ("values", {"locator": {"table": "1"}, "composition_wt_pct": {"SiO2": 70, "MgO": 30}}),
            ("equipment", {"locator": {"table": "2"}, "oxides_wt_pct": {"SiO2": 60, "MgO": 40}}),
        ],
        _vocab(),
    )
    assert got is None


def test_pin_printed_roots_map_without_locator_is_ignored() -> None:
    from simulator.battery.migrate import _printed_composition_from_roots

    assert (
        _printed_composition_from_roots(
            [("values", {"composition_wt_pct": {"SiO2": 70, "MgO": 30}})], _vocab()
        )
        is None
    )


# --- b718 ROR fix (regolith-main review of record, 2026-10-06) -------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _rows_observation(template: dict, oid: str, rows: list[dict]) -> dict:
    row = yaml.safe_load(yaml.safe_dump(template))
    row["observation_id"] = oid
    row["values"].pop("series", None)
    row["values"]["rows"] = rows
    return row


def test_nested_row_map_stays_on_its_row_not_siblings_or_sample(tmp_path: Path) -> None:
    """ROR P0, quoted Hastie 1981 Table 2 shape.

    One observation; its values.rows hold three printed rows and only the
    middle row prints composition_wt_pct. The parent must not carry that map,
    so the exploded siblings must not inherit it and the universal promotion
    must not see it on every child.
    """

    extract = _base()
    template = extract["species"]["Na"]["observations"][0]
    rows = [
        {"locator": {"table": "2", "note": "body row 1"}, "T_K": 1300.0, "pressure_atm": 1.0},
        {
            "locator": {"table": "2", "note": "body row 2"},
            "T_K": 1400.0,
            "pressure_atm": 2.0,
            "composition_wt_pct": {"K2O": 7.4, "Al2O3": 26.0, "SiO2": 66.6},
        },
        {"locator": {"table": "2", "note": "body row 3"}, "T_K": 1500.0, "pressure_atm": 3.0},
    ]
    extract["species"]["Na"]["observations"] = [
        _rows_observation(template, "quoted_fit_rows", rows)
    ]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    children = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::quoted_fit_rows::")
    ]
    assert len(children) == 3
    experiment = result.experiments[children[0].experiment_id]
    assert all(child.experiment_id == experiment.experiment_id for child in children)
    assert experiment.sample.printed_composition is None or not (
        experiment.sample.printed_composition.state.is_value
    ), "a map printed on one nested row must not become the sample"

    owners = []
    for child in children:
        printed = (child.point_conditions or {}).get("printed_composition")
        norm = normalized_composition(experiment, None, child)
        if printed is not None and printed.state.is_value:
            owners.append(child)
            assert as_decimal(printed.state.value["K2O"]) == as_decimal("7.4")
            assert norm.selected is not None
            assert norm.selected.route == "observation_normalized_printed_composition"
        else:
            assert norm.selected is None, (
                f"sibling {child.observation_id} selected {norm.selected.route}"
            )
            assert "composition" not in (child.point_conditions or {})
    assert len(owners) == 1


def test_observation_level_map_still_reaches_its_exploded_rows(tmp_path: Path) -> None:
    """A map the observation prints for itself (outside rows) may still attach."""

    extract = _base()
    template = extract["species"]["Na"]["observations"][0]
    observation = _rows_observation(
        template,
        "series_with_own_map",
        [
            {"locator": {"table": "2", "note": "r1"}, "T_K": 1300.0, "pressure_atm": 1.0},
            {"locator": {"table": "2", "note": "r2"}, "T_K": 1400.0, "pressure_atm": 2.0},
        ],
    )
    observation["values"]["composition_wt_pct"] = {"SiO2": 60, "MgO": 40}
    extract["species"]["Na"]["observations"] = [observation]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    children = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::series_with_own_map::")
    ]
    assert len(children) == 2
    for child in children:
        printed = (child.point_conditions or {}).get("printed_composition")
        assert printed is not None and printed.state.is_value
        assert as_decimal(printed.state.value["SiO2"]) == as_decimal("60")


def test_single_row_residue_map_does_not_promote_to_sample(tmp_path: Path) -> None:
    """A row-local map (one series row, e.g. a residue) is not the sample.

    With one exploded child the 'every observation shares one map' rule is
    trivially true; the row's own map must still not be promoted.
    """

    extract = _base()
    template = extract["species"]["Na"]["observations"][0]
    extract["species"]["Na"]["observations"] = [
        _rows_observation(
            template,
            "one_residue_row",
            [
                {
                    "locator": {"table": "1", "note": "residue after 69 % loss"},
                    "T_K": 2173.15,
                    "pressure_atm": 1.0,
                    "composition_wt_pct": {"MgO": 10.95, "Al2O3": 40.16, "SiO2": 22.16, "CaO": 26.75},
                }
            ],
        )
    ]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    child = next(
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::one_residue_row::")
    )
    printed = (child.point_conditions or {}).get("printed_composition")
    assert printed is not None and printed.state.is_value
    experiment = result.experiments[child.experiment_id]
    assert experiment.sample.printed_composition is None or not (
        experiment.sample.printed_composition.state.is_value
    )


def _declared(value: object) -> dict:
    return {
        "experiment_id": "declared-melt",
        "sample": {
            "printed_composition": {
                "state": {"tag": "value", "value": value},
                "locator": {"table": "1", "page": 1},
            },
        },
    }


def test_row_repeating_declared_map_with_total_does_not_shadow_it(tmp_path: Path) -> None:
    """Britt 2019 Cr XRF shape: the row restates the declaration plus Total."""

    extract = _base()
    extract["experiments"] = [_declared({"SiO2": "50", "MgO": "50"})]
    template = extract["species"]["Na"]["observations"][0]
    row = _obs(
        template,
        "xrf_row",
        composition={"SiO2": 50, "MgO": 50, "Total": 100},
        experiment="declared-melt",
    )
    extract["species"]["Na"]["observations"] = [row]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = result.observations["fixture-source::xrf_row"]
    experiment = result.experiments[observation.experiment_id]
    assert "printed_composition" not in (observation.point_conditions or {})
    norm = normalized_composition(experiment, None, observation)
    assert norm.selected is not None
    assert norm.selected.route == "normalized_printed_composition"


def test_prose_declared_printed_composition_does_not_own_the_sample(tmp_path: Path) -> None:
    """Badro 2021 vp-series shape: a prose string is not a usable declaration."""

    extract = _base()
    extract["experiments"] = [_declared("50 SiO2, 50 MgO wt%")]
    template = extract["species"]["Na"]["observations"][0]
    recipe = {"SiO2": 50, "MgO": 50}
    extract["species"]["Na"]["observations"] = [
        _obs(template, "row_a", composition=recipe, experiment="declared-melt"),
        _obs(template, "row_b", composition=recipe, experiment="declared-melt"),
    ]

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = result.observations["fixture-source::row_a"]
    printed = result.experiments[observation.experiment_id].sample.printed_composition
    assert printed is not None and printed.state.is_value
    assert isinstance(printed.state.value, dict)
    assert as_decimal(printed.state.value["SiO2"]) == as_decimal("50")


def _migrate_real(name: str):
    from simulator.battery.migrate import Migrator

    migrator = Migrator(root=_REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(_REPO_ROOT / "data/literature/extracts" / f"{name}.yaml")
    migrator.finalize()
    return migrator.result


def test_real_hastie_quoted_table2_illite_stays_on_body_row_16() -> None:
    result = _migrate_real("kems-020-hastie-1981-nbsir")
    children = [
        obs
        for oid, obs in result.observations.items()
        if "::hastie_1981_table2_k_logP_coefficients_quoted_20260906::" in oid
    ]
    assert len(children) == 17
    experiment = result.experiments[children[0].experiment_id]
    assert experiment.experiment_id.endswith("::experiment::hastie-1981-table2-quoted-fits")
    assert experiment.sample.printed_composition is None
    owners = []
    for child in children:
        printed = (child.point_conditions or {}).get("printed_composition")
        if printed is not None and printed.state.is_value:
            owners.append(child)
            assert dict(printed.state.value) == {
                "K2O": "7.4", "Al2O3": "26", "Fe2O3": "4.4", "MgO": "2.1", "SiO2": "60.2"
            }
        else:
            assert normalized_composition(experiment, None, child).selected is None
    assert [owner.locator.note for owner in owners] == ["body row 16"]


def test_real_hashimoto_depleted_rows_do_not_resolve_starting_charge() -> None:
    """ROR P1: residue-enrichment and stage-IV rows are not the FCMAS start."""

    result = _migrate_real("kems-015-hashimoto-1983")
    for local_id in (
        "hashimoto_1983_cao_al2o3_residue_enrichment",
        "hashimoto_1983_stage_iv_cao_relative_volatility",
    ):
        observation = result.observations[f"kems-015-hashimoto-1983::{local_id}"]
        assert not observation.experiment_id.endswith("::experiment::fcmas-free-evap-series")
        experiment = result.experiments[observation.experiment_id]
        norm = normalized_composition(experiment, None, observation)
        assert norm.selected is None, (local_id, norm.selected.route)
    start = result.observations["kems-015-hashimoto-1983::hashimoto_1983_table1_starting_composition"]
    assert start.experiment_id.endswith("::experiment::fcmas-free-evap-series")
    norm = normalized_composition(result.experiments[start.experiment_id], None, start)
    assert norm.selected is not None


def test_real_hashimoto_class_b1_geometry_rows_share_declared_start() -> None:
    """ROR note: the Fe class-B1 row sat alone on an auto experiment while the
    Mg and SiO class-B1 rows (same preform, same Table 1 map) were declared.
    All three now resolve the declared FCMAS start from one experiment."""

    result = _migrate_real("kems-015-hashimoto-1983")
    expected = {"SiO2": "35.43", "Al2O3": "3.16", "FeO": "35.04", "MgO": "23.84", "CaO": "2.53"}
    for local_id in (
        "hashimoto_1983_fe_geometry_class_b1",
        "hashimoto_1983_mg_geometry_class_b1",
        "hashimoto_1983_sio_geometry_class_b1",
    ):
        observation = result.observations[f"kems-015-hashimoto-1983::{local_id}"]
        assert observation.experiment_id.endswith(
            "::experiment::fcmas-free-evap-series"
        ), (local_id, observation.experiment_id)
        experiment = result.experiments[observation.experiment_id]
        printed = experiment.sample.printed_composition
        assert printed is not None and printed.state.is_value
        assert {k: str(as_decimal(v)) for k, v in printed.state.value.items()} == expected
        norm = normalized_composition(experiment, None, observation)
        assert norm.selected is not None
        assert norm.selected.route == "normalized_printed_composition", (
            local_id,
            norm.selected.route,
        )
