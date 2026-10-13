from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT, Migrator
from simulator.battery.validate import validate_corpus
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/kassis-frischat-1981-silicate-glass-vapour.yaml"
)
FIGURE_TOLERANCE = 0.20
SERIES = {
    ("Na", Quantity.P_PARTIAL): {1300: 0.52, 1150: 0.039},
    ("Rb", Quantity.P_PARTIAL): {1300: 2.5, 1150: 0.18},
}
SERIES_EXPERIMENT = {
    "Na": "sodium-silicate-melt",
    "Rb": "rubidium-silicate-melt",
}
TOTAL_PRESSURE_IDS = {
    "kassis_1981_fig2_p_total_na_melt",
    "kassis_1981_fig2_p_total_rb_melt",
}
TOTAL_PRESSURE_REASON = (
    "source states total vapour pressure over a multi-species vapour, "
    "not a single Sen monomer; v2.1 has no total-vapour-pressure identity"
)


def test_kassis_frischat_source_facts_survive_migration() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(EXTRACT)
    result = migrator.result
    queued_total_pressure = [
        entry for entry in result.queue if entry.why == TOTAL_PRESSURE_REASON
    ]
    assert len(queued_total_pressure) == 2
    assert {
        entry.observation_id.rsplit("::", 1)[-1]
        for entry in queued_total_pressure
    } == TOTAL_PRESSURE_IDS
    total_pressure_rows = [
        observation
        for observation in result.observations.values()
        if any(
            observation.observation_id.startswith(f"{EXTRACT.stem}::{row_id}::")
            for row_id in TOTAL_PRESSURE_IDS
        )
    ]
    assert len(total_pressure_rows) == 8
    assert all(
        observation.identity.quantity.is_unknown
        and observation.identity.quantity.reason == TOTAL_PRESSURE_REASON
        for observation in total_pressure_rows
    )
    assert all(
        observation.value.kind is ValueKind.UNAVAILABLE
        and observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        for observation in total_pressure_rows
    )
    validation = validate_corpus(
        result.works, result.experiments, result.observations, residuals=None
    )
    assert validation.hard_issues == ()

    observations_by_series: dict[tuple[str, Quantity], dict[int, object]] = {
        series: {} for series in SERIES
    }
    for observation in result.observations.values():
        if not observation.identity.quantity.is_value:
            continue
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        assert observation.identity.quantity.is_value
        key = (observation.identity.species.formula, observation.identity.quantity.value)
        assert key in observations_by_series
        assert observation.identity.temperature_K.is_value
        assert observation.value.point is not None
        assert observation.point_conditions is not None
        assert observation.point_conditions["temperature_C"].state.is_value
        temperature_c = observation.point_conditions["temperature_C"].state.value
        assert float(observation.identity.temperature_K.value) == pytest.approx(
            float(temperature_c) + 273.15
        )
        observations_by_series[key][round(float(temperature_c))] = observation

    assert {key: set(points) for key, points in observations_by_series.items()} == {
        key: {1300, 1250, 1200, 1150} for key in SERIES
    }
    for series, expected_readings in SERIES.items():
        for temperature_c, expected_pressure_pa in expected_readings.items():
            observation = observations_by_series[series][temperature_c]
            assert observation.experiment_id.endswith(
                f"::experiment::{SERIES_EXPERIMENT[series[0]]}"
            )
            assert float(observation.value.point) == pytest.approx(
                expected_pressure_pa, rel=FIGURE_TOLERANCE
            )

    experiments = {
        experiment.experiment_id.rsplit("::", 1)[-1]: experiment
        for experiment in result.experiments.values()
    }
    sodium = experiments["sodium-silicate-melt"]
    rubidium = experiments["rubidium-silicate-melt"]
    assert sodium.sample.printed_composition.state.is_value
    assert sodium.sample.printed_composition.state.value == "Na2O.3SiO2"
    assert rubidium.sample.printed_composition.state.is_value
    assert rubidium.sample.printed_composition.state.value == "Rb2O.3SiO2"
    assert sodium.sample.mass_kg.state.is_value
    assert rubidium.sample.mass_kg.state.is_value
    assert float(sodium.sample.mass_kg.state.value.point) == pytest.approx(0.002)
    assert float(rubidium.sample.mass_kg.state.value.point) == pytest.approx(0.002)
    assert sodium.sample.mass_kg.state.value.approximate
    assert rubidium.sample.mass_kg.state.value.approximate
    sodium_bench = result.benches[sodium.bench_id]
    rubidium_bench = result.benches[rubidium.bench_id]
    assert sodium_bench.cell_material_and_liner.state.value == "Pt"
    assert rubidium_bench.cell_material_and_liner.state.value == "Pt"
    assert sodium_bench.geometry.orifice_diameter_m.state.is_value
    assert rubidium_bench.geometry.orifice_diameter_m.state.is_value
    assert float(sodium_bench.geometry.orifice_diameter_m.state.value.point) == pytest.approx(
        0.0008
    )
    assert float(rubidium_bench.geometry.orifice_diameter_m.state.value.point) == pytest.approx(
        0.0008
    )
    assert float(
        sodium_bench.geometry.cell_internal_dimensions["diameter_m"].state.value.point
    ) == pytest.approx(0.015)
    assert float(
        sodium_bench.geometry.cell_internal_dimensions["height_m"].state.value.point
    ) == pytest.approx(0.020)
    facts = {fact.name: fact for fact in sodium_bench.other_facts}
    assert facts["calibration_reference"].value.state.value.categorical == "99.998% Cu"
    assert float(facts["cell_wall_thickness"].value.state.value.point) == pytest.approx(
        0.2
    )
    assert facts["cell_wall_thickness"].unit == "mm"
    assert facts["balance_model"].value.state.value.categorical == "Mettler Model TA-I"
    assert sodium.pressure_environment.total_pressure_Pa.state.is_value
    assert rubidium.pressure_environment.total_pressure_Pa.state.is_value
    assert float(sodium.pressure_environment.total_pressure_Pa.state.value.point) == pytest.approx(
        6.6e-4
    )
    assert float(rubidium.pressure_environment.total_pressure_Pa.state.value.point) == pytest.approx(
        6.6e-4
    )

    sodium_thermochemistry = document["species"]["Na2O"]["context"][0]["values"]
    rubidium_thermochemistry = document["species"]["Rb2O"]["context"][0]["values"]
    assert sodium_thermochemistry["delta_H_kJ_per_mol"] == 316
    assert sodium_thermochemistry["delta_S_J_per_K"] == 102
    assert rubidium_thermochemistry["delta_H_kJ_per_mol"] == 328
    assert rubidium_thermochemistry["delta_S_J_per_K"] == 122
    assert sodium_thermochemistry["reference_pressure_footnote_as_printed"] == "p = 1.013×10^5 Pa."
    assert rubidium_thermochemistry["reference_pressure_footnote_as_printed"] == "p = 1.013×10^5 Pa."
