from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import Quantity, ValueKind


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGET_SIDS = (
    "kems-015-hashimoto-1983",
    "kems-022-demaria-1971",
    "wimpenny-2019-zn-isotope-evaporation-extreme-t",
    "nakano-hashimoto-2020-bubbles-to-chondrites-i",
    "kems-031-halwax-2024",
    "kems-133-costa-2017",
    "kems-025-markova-1983",
    "kems-007-costa-2015",
)


@pytest.fixture(scope="module")
def migrated_targets():
    extract_dir = _REPO_ROOT / "data" / "literature" / "extracts"
    extracts = [extract_dir / f"{sid}.yaml" for sid in _TARGET_SIDS]
    with patch.object(migrate_module, "discover_extracts", return_value=extracts):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


def _source_observations(result, source_id: str):
    return [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
    ]


def _assert_printed_point(
    result,
    *,
    source_id: str,
    id_fragment: str,
    temperature_K: str,
    value: str,
) -> None:
    observations = [
        observation
        for observation in _source_observations(result, source_id)
        if id_fragment in observation.observation_id
        and observation.value.kind is ValueKind.POINT
        and observation.value.point == Decimal(value)
    ]
    assert observations, (source_id, id_fragment, temperature_K, value)
    point = observations[0]
    temperature = point.point_conditions["temperature_K"]
    assert temperature.state.is_value
    assert temperature.state.value == Decimal(temperature_K)
    assert temperature.locator is not None


def test_yield_table_items_recognizes_numeric_mass_loss_runs() -> None:
    runs = [
        {"Tmax_C": 2183, "mass_loss_wt_pct": 97.4},
        {"Tmax_C": 2928, "mass_loss_wt_pct": "?"},
    ]
    assert migrate_module._yield_table_items({"runs": runs}) == ("runs", runs)
    assert migrate_module._yield_table_items(
        {"runs": [{"Tmax_C": 2183, "mass_loss_wt_pct": "?"}]}
    ) == (None, None)
    assert migrate_module._yield_table_items(
        {"runs": [{"T_C": 1700, "FeO_wt_pct": 27.92}]}
    ) == (None, None)


@pytest.mark.parametrize(
    ("source_id", "id_fragment", "temperature_K", "value"),
    [
        (
            "kems-015-hashimoto-1983",
            "hashimoto_1983_table3_residue_composition_series::T=1973.15:row=c7",
            "1973.15",
            "15.53",
        ),
        (
            "kems-022-demaria-1971",
            "demaria_1971_o_atomic_psat_table5::T=2015.0:row=12022",
            "2015.0",
            "0.0093219",
        ),
        (
            "wimpenny-2019-zn-isotope-evaporation-extreme-t",
            "wimpenny_2019_table1_experimental_zn_rows::rhyolite-soil-1::T=2073.15",
            "2073.15",
            "0.3",
        ),
        (
            "kems-133-costa-2017",
            "costa_2017_solidus",
            "2050.0",
            "2050.0",
        ),
        (
            "kems-025-markova-1983",
            "markova_1983_fe_psat_figs_figure_only::T=1984.47",
            "1984.47",
            "0.9110692037171052631578947367",
        ),
        (
            "kems-007-costa-2015",
            "costa_2015_fe_partial_pressure_ir_digitized::T=2014.83",
            "2014.83",
            "1.214395462383",
        ),
    ],
)
def test_existing_high_temperature_points_keep_printed_value_and_temperature(
    migrated_targets, source_id, id_fragment, temperature_K, value
) -> None:
    _assert_printed_point(
        migrated_targets,
        source_id=source_id,
        id_fragment=id_fragment,
        temperature_K=temperature_K,
        value=value,
    )


def test_demaria_table4_points_keep_printed_values_and_temperatures(migrated_targets) -> None:
    observations = _source_observations(migrated_targets, "kems-022-demaria-1971")
    table4 = [
        observation
        for observation in observations
        if (
            "demaria_1971_al2o_psat_table4" in observation.observation_id
            or "demaria_1971_alo_psat_table4" in observation.observation_id
        )
        and observation.value.kind is ValueKind.POINT
    ]
    assert len(table4) == 20
    for observation in table4:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
    alo_points = [
        observation
        for observation in table4
        if "demaria_1971_alo_psat_table4" in observation.observation_id
    ]
    assert len(alo_points) == 6
    assert {point.identity.species.formula for point in alo_points} == {"AlO"}
    _assert_printed_point(
        migrated_targets,
        source_id="kems-022-demaria-1971",
        id_fragment="demaria_1971_al2o_psat_table4::T=2060.0",
        temperature_K="2060.0",
        value="0.00051371775",
    )


def test_nakano_numeric_runs_emit_stable_mass_loss_points(migrated_targets) -> None:
    observations = _source_observations(
        migrated_targets, "nakano-hashimoto-2020-bubbles-to-chondrites-i"
    )
    points = [
        observation
        for observation in observations
        if "nakano_2020_mass_loss_selected_runs::T=" in observation.observation_id
        and observation.value.kind is ValueKind.POINT
    ]
    assert len(points) == 8
    assert len({observation.observation_id for observation in points}) == 8
    for observation in points:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == observation.identity.temperature_K.value
        assert temperature.locator is not None
    _assert_printed_point(
        migrated_targets,
        source_id="nakano-hashimoto-2020-bubbles-to-chondrites-i",
        id_fragment="nakano_2020_mass_loss_selected_runs::T=2456.15",
        temperature_K="2456.15",
        value="0.974",
    )


def test_halwax_figure_only_pressures_have_no_numeric_points(migrated_targets) -> None:
    observations = _source_observations(migrated_targets, "kems-031-halwax-2024")
    assert not any(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
