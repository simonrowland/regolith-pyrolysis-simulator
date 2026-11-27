from __future__ import annotations

import pytest

from simulator.battery import migrate
from simulator.battery.enums import METHOD_TOKEN_ALIASES, MethodToken
from simulator.battery.migrate import map_method


NEW_METHOD_TOKENS = (
    MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
    MethodToken.ADIABATIC_CALORIMETRY,
    MethodToken.DROP_CALORIMETRY,
    MethodToken.SOLUTION_CALORIMETRY,
    MethodToken.PPMS_HEAT_CAPACITY,
    MethodToken.MOSSBAUER_SPECTROSCOPY,
    MethodToken.XANES_SPECTROSCOPY,
    MethodToken.WET_CHEMICAL_ANALYSIS,
    MethodToken.PISTON_CYLINDER,
)


@pytest.mark.parametrize("token", NEW_METHOD_TOKENS)
def test_new_method_tokens_round_trip(token: MethodToken) -> None:
    assert MethodToken(token.value) is token


@pytest.mark.parametrize(
    ("printed", "token"),
    (
        (
            "aerodynamic_levitation_laser_heating",
            MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
        ),
        (
            "aerodynamic_levitation_laser_heating_then_quench",
            MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
        ),
        (
            "adiabatic_calorimetry",
            MethodToken.ADIABATIC_CALORIMETRY,
        ),
        ("drop_calorimetry", MethodToken.DROP_CALORIMETRY),
        (
            "solution_calorimetry_at_298_K",
            MethodToken.SOLUTION_CALORIMETRY,
        ),
        (
            "Physical Properties Measurement System (PPMS) heat-capacity measurement",
            MethodToken.PPMS_HEAT_CAPACITY,
        ),
        (
            "mossbauer_room_temperature",
            MethodToken.MOSSBAUER_SPECTROSCOPY,
        ),
        (
            "Cr K-edge XANES silicate glass redox",
            MethodToken.XANES_SPECTROSCOPY,
        ),
        (
            "Cr K-edge XANES at 1400 °C; high-temperature redox ratio preserved on quench",
            MethodToken.XANES_SPECTROSCOPY,
        ),
        (
            "Fe K-edge XANES",
            MethodToken.XANES_SPECTROSCOPY,
        ),
        (
            "Modified indirect potentiometric redox titration; numerator is all reduced Ti, reported as Ti3+, and Ti4+ is total Ti minus reduced Ti",
            MethodToken.WET_CHEMICAL_ANALYSIS,
        ),
        (
            "high_pressure_anhydrous_piston_cylinder",
            MethodToken.PISTON_CYLINDER,
        ),
        (
            "piston_cylinder_equilibration_quench",
            MethodToken.PISTON_CYLINDER,
        ),
        (
            "end_loaded_piston_cylinder_CCO_buffered",
            MethodToken.PISTON_CYLINDER,
        ),
    ),
)
def test_reviewed_printed_method_aliases_map_to_closed_tokens(
    printed: str, token: MethodToken
) -> None:
    assert METHOD_TOKEN_ALIASES[printed] is token
    mapped = map_method(printed)
    assert mapped.is_value and mapped.value is token


@pytest.mark.parametrize(
    "printed",
    (
        "compiled_trace_element_concentrations",
        "aerodynamic_levitation",
        "aerodynamic_levitation_evaporation",
        "drop_calorimetry_and_compiled_calorimetry",
        "high_temperature_drop_solution_calorimetry",
        "electron_microprobe_and_wet_chemical_analysis",
        "quoted_wet_chemical_analysis_method",
        "containerless_laser_heating",
    ),
)
def test_ambiguous_method_labels_remain_typed_unknown(printed: str) -> None:
    assert printed not in METHOD_TOKEN_ALIASES
    assert map_method(printed).is_unknown


@pytest.mark.parametrize(
    "observation_id",
    (
        "pank68_naca1_table2_measured_outside_fit_range",
        "pank68_naca2_table2_measured_outside_fit_range",
        "pank68_naca3_table2_measured_outside_fit_range",
    ),
)
def test_wrapped_pankratz_method_alias_resolves_from_production_yaml(
    observation_id: str,
) -> None:
    path = (
        migrate.REPO_ROOT
        / "data/literature/extracts"
        / "pankratz-1968-ri7201-sodium-calcium-silicates.yaml"
    )
    document = migrate.load_yaml(path)
    observations = {
        observation["observation_id"]: observation
        for _, observation in migrate.iter_extract_observations(document)
    }
    observation = observations[observation_id]
    mapped = map_method(observation["values"]["method"])

    assert mapped.is_value
    assert mapped.value is MethodToken.DROP_CALORIMETRY


def test_vacuum_evaporation_migrates_yakovlev_without_creating_observations() -> None:
    path = (
        migrate.REPO_ROOT
        / "data/literature/extracts/yakovlev-2021-chondrule-alkali.yaml"
    )
    migrator = migrate.Migrator(root=migrate.REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()

    experiment = next(
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.experiment_id.endswith(
            "::experiment::yakovlev-shornikov-2021-vacuum-evaporation"
        )
    )
    assert experiment.method.is_unknown
    assert not any(
        observation.experiment_id == experiment.experiment_id
        for observation in migrator.result.observations.values()
    )


def test_generic_vacuum_evaporation_does_not_resolve_to_langmuir() -> None:
    assert "vacuum_evaporation" not in METHOD_TOKEN_ALIASES
    assert map_method("vacuum_evaporation").is_unknown


def test_gibson_hubbard_source_stays_on_vacuum_chamber_pyrolysis() -> None:
    path = (
        migrate.REPO_ROOT
        / "data/literature/extracts/kems-024-gibson-hubbard-1972.yaml"
    )
    document = migrate.load_yaml(path)
    experiment = next(
        experiment
        for experiment in document["experiments"]
        if experiment["experiment_id"] == "lunar-volatilization-series"
    )

    mapped = map_method(experiment["method"])

    assert METHOD_TOKEN_ALIASES["vacuum_chamber_pyrolysis"] is (
        MethodToken.VACUUM_CHAMBER_PYROLYSIS
    )
    assert mapped.is_value
    assert mapped.value is MethodToken.VACUUM_CHAMBER_PYROLYSIS


@pytest.mark.parametrize(
    ("label", "expected"),
    (
        ("knudsen_effusion", MethodToken.KNUDSEN_EFFUSION),
        ("static_equilibrium", MethodToken.STATIC_EQUILIBRIUM),
        ("langmuir_free_evaporation", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("transpiration", MethodToken.TRANSPIRATION),
        ("tga", MethodToken.TGA),
        ("dta_dsc", MethodToken.DTA_DSC),
        ("evolved_gas_ms", MethodToken.EVOLVED_GAS_MS),
        ("solar_furnace_pyrolysis", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        ("vacuum_chamber_pyrolysis", MethodToken.VACUUM_CHAMBER_PYROLYSIS),
        ("emf_cell", MethodToken.EMF_CELL),
        ("quench_equilibration", MethodToken.QUENCH_EQUILIBRATION),
        ("tabulation", MethodToken.TABULATION),
        ("engine_evaluation", MethodToken.ENGINE_EVALUATION),
        ("static_capsule_vapor_pressure", MethodToken.STATIC_EQUILIBRIUM),
        ("kems_effusion", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_compiled_experiment", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_method_context", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_review_compilation", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_ion_comparison", MethodToken.KNUDSEN_EFFUSION),
        ("knudsen_effusion_mass_spectrometry", MethodToken.KNUDSEN_EFFUSION),
        (
            "Knudsen-cell mass-spectrometric ion currents converted to partial pressures using the thesis calibration.",
            MethodToken.KNUDSEN_EFFUSION,
        ),
        (
            "KEMS ion-current intensities converted to partial pressures using the thesis calibration (Eq. 2).",
            MethodToken.KNUDSEN_EFFUSION,
        ),
        ("free_evaporation", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("open_furnace_apparent", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("solar_furnace", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        ("solar_vacuum_pyrolysis", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        (
            "solar_vacuum_pyrolysis_free_evaporation",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        (
            "solar_fresnel_vacuum_pyrolysis",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        (
            "solar_fresnel_continuously_pumped_vacuum_pyrolysis",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        ("melt_equilibration_quench", MethodToken.QUENCH_EQUILIBRATION),
        ("gas_standard_state_thermo", MethodToken.TABULATION),
    ),
)
def test_previously_accepted_method_labels_keep_their_targets(
    label: str, expected: MethodToken
) -> None:
    mapped = map_method(label)

    assert mapped.is_value
    assert mapped.value is expected


@pytest.mark.parametrize("label", (None, "unrecognized_method_label"))
def test_missing_or_unrecognized_method_labels_remain_typed_unknown(
    label: object,
) -> None:
    assert map_method(label).is_unknown
