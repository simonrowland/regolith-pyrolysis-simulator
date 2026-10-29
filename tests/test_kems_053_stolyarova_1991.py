from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity, UncertaintyKind
from simulator.battery.score import _printed_uncertainty_band
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "kems-053-stolyarova-1991.yaml"
EXTRACT_PATH = ROOT / "data" / "literature" / "extracts" / EXTRACT_NAME
SOURCE_ID = "kems-053-stolyarova-1991"
TABLE_IA_SERIES = {
    "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation": (
        "P_Ca_times_10^7_atm_as_printed"
    ),
    "stolyarova_1991_ca_partial_pressure_1993k_ion_comparison": (
        "P_Ca_times_10^7_atm_as_printed"
    ),
    "stolyarova_1991_cao_partial_pressure_1993k_complete_evaporation": (
        "P_CaO_times_10^8_atm_as_printed"
    ),
    "stolyarova_1991_cao_partial_pressure_1993k_ion_comparison": (
        "P_CaO_times_10^8_atm_as_printed"
    ),
}


def _series_by_id(document: dict, observation_id: str) -> dict:
    matches = [
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation["observation_id"] == observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def _point_at_fraction(series: dict, fraction: str) -> dict:
    matches = [
        point
        for point in series["values"]["points"]
        if point["SiO2_mole_fraction_as_printed"] == fraction
    ]
    assert len(matches) == 1
    return matches[0]


def _migrated_point(result, observation_id: str, fraction: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.startswith(f"{SOURCE_ID}::{observation_id}::")
        and dict(observation.identity.composition.value.components)["SiO2"]
        == Decimal(fraction)
    ]
    assert len(matches) == 1
    return matches[0]


def test_table_ia_per_cell_uncertainties_migrate_with_source_unit_and_locator(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    result = _migrate_real_extract(tmp_path / "current", EXTRACT_NAME)

    table_ia = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.partition("::")[2].split("::", 1)[0]
        in TABLE_IA_SERIES
    ]
    assert len(table_ia) == 44
    assert all(
        observation.uncertainty is not None
        and observation.uncertainty.kind is UncertaintyKind.PRINTED
        for observation in table_ia
    )
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in table_ia
    )
    assert all(
        observation.locator is not None
        and observation.locator.page == 2
        and observation.locator.table == "Ia"
        and "unit='atm'" in (observation.locator.note or "")
        for observation in table_ia
    )
    assert result.validation is not None
    assert not result.validation.hard_issues

    pins = (
        (
            "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation",
            "P_Ca_times_10^7_atm_as_printed",
            "2.2 ± 0.2",
            Decimal("0.0222915"),
        ),
        (
            "stolyarova_1991_ca_partial_pressure_1993k_ion_comparison",
            "P_Ca_times_10^7_atm_as_printed",
            "2.5 ± 0.3",
            Decimal("0.02533125"),
        ),
        (
            "stolyarova_1991_cao_partial_pressure_1993k_complete_evaporation",
            "P_CaO_times_10^8_atm_as_printed",
            "5.9 ± 0.4",
            Decimal("0.005978175"),
        ),
        (
            "stolyarova_1991_cao_partial_pressure_1993k_ion_comparison",
            "P_CaO_times_10^8_atm_as_printed",
            "6.0 ± 0.5",
            Decimal("0.0060795"),
        ),
    )
    for series_id, cell_field, printed_cell, expected_pa in pins:
        series = _series_by_id(document, series_id)
        point = _point_at_fraction(series, "0.50")
        assert point[cell_field] == printed_cell
        source_locator = point["point_conditions"]["composition"]["locator"]
        assert source_locator["page"] == 2
        assert source_locator["table"] == "Ia"

        observation = _migrated_point(result, series_id, "0.50")
        assert abs(observation.value.point - expected_pa) < Decimal("1e-15")
        band = _printed_uncertainty_band(
            Quantity.P_PARTIAL,
            observation.uncertainty,
            observation.value.point,
            source_observation=observation,
        )
        assert band.band is not None
        assert band.band.unit == "dimensionless"

    mutated = deepcopy(document)
    mutated_series = _series_by_id(
        mutated,
        "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation",
    )
    _point_at_fraction(mutated_series, "0.50").pop("sigma")
    mutated_result = _migrate_real_extract(
        tmp_path / "without-one-width", EXTRACT_NAME, extract_doc=mutated
    )
    mutated_observation = _migrated_point(
        mutated_result,
        "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation",
        "0.50",
    )
    mutated_band = _printed_uncertainty_band(
        Quantity.P_PARTIAL,
        mutated_observation.uncertainty,
        mutated_observation.value.point,
        source_observation=mutated_observation,
    )
    assert mutated_band.band is None


def test_printed_caosio2_melt_phase_maps_on_all_59_observations(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    printed_phrase = "CaO-SiO2 liquid oxide melt"
    phase_series = [
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation.get("phase_as_printed") == printed_phrase
    ]
    assert len(phase_series) == 7
    assert all(observation["phase"] == "l" for observation in phase_series)

    source_ids = {observation["observation_id"] for observation in phase_series}
    result = _migrate_real_extract(tmp_path / "phase", EXTRACT_NAME)
    phase_observations = [
        observation
        for observation in result.observations.values()
        if observation.observation_id.partition("::")[2].split("::", 1)[0]
        in source_ids
    ]
    assert len(phase_observations) == 59
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.L
        for observation in phase_observations
    )
    phase_locators = {
        (
            observation.locator.page,
            observation.locator.table,
            observation.locator.figure,
        )
        for observation in phase_observations
        if observation.locator is not None
    }
    assert {
        (2, "IIa", None),
        (2, "IIb", None),
        (4, None, "3"),
    } <= phase_locators

