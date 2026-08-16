"""b-189 hot-train applicability admission and refusal-ordering regressions."""

from __future__ import annotations

from copy import deepcopy
from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.foulant_disposition import chi_escape_salt
from simulator.condensation import (
    CondensationModel,
    WallSaturationPressureRefusal,
    _antoine_psat_pa,
    _condensation_admission_refusal,
    _species_has_antoine_data,
    _species_has_compiled_or_legacy_pressure,
    _species_vapor_data,
    _try_antoine_psat_pa,
    _wall_deposition_driving_pressure_pa,
)
from simulator.diagnostic_helpers.extract_reproduction import _engine_pure_psat_pa
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.vapour_rail.catalog import (
    HotTrainInapplicable,
    compiled_catalog_for,
    vapor_pressure_legacy_view,
)
from simulator.vapour_rail.request import (
    REFUSAL_INAPPLICABLE_PREDICATE,
    _predicate_active,
    applicability_verdict,
)


DATA = Path(__file__).resolve().parents[1] / "data"
DORMANT_CARRIERS = ("Pb", "SnO", "PbO", "WO3", "AlF3", "B2O3", "BaO", "AlF2Cl")
P_CARRIERS = ("P2", "P4", "P4O6", "P4O10", "PO", "PO2")
REORDERED_ROWS = {
    "Bi",
    "Bi2O3",
    "ClO4",
    "Mg2Cl4",
    "MoO3_cross_check",
    "NH4CN",
    "NO",
    "O",
    "O2",
    "P2O5_gas",
    "Re2O7",
    "S2",
    "S_total",
    "TeO2",
    *P_CARRIERS,
}


@pytest.fixture(scope="module")
def payload() -> dict:
    with (DATA / "vapor_pressures.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _set_applicability(payload: dict, species_id: str, token: str) -> None:
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    family_id = catalog.species[species_id].family_id
    payload["families"][family_id]["code_metadata"][
        "hot_train_applicability"
    ] = token


def _configured_model(payload: dict, species_id: str) -> CondensationModel:
    model = CondensationModel(
        CondensationTrain.create_default(),
        vapor_pressure_data=payload,
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
        carrier_gas="O2",
        species_partial_pressures_mbar={species_id: 1.0},
    )
    return model


@pytest.mark.parametrize("species_id", DORMANT_CARRIERS)
def test_dormant_carrier_refused_at_condensation_seam(payload, species_id) -> None:
    """Rows that previously yielded pressure (for example Pb) now decline."""

    assert not _species_has_antoine_data(species_id, vapor_pressure_data=payload)
    assert _condensation_admission_refusal(
        species_id, vapor_pressure_data=payload
    ) == REFUSAL_INAPPLICABLE_PREDICATE


def test_dormant_carrier_pressure_path_is_typed_refusal(payload) -> None:
    warnings: list[str] = []
    assert _try_antoine_psat_pa(
        "Pb",
        1500.0,
        vapor_pressure_data=payload,
        antoine_extrapolation_warnings=warnings,
    ) == (None, True)
    assert any(REFUSAL_INAPPLICABLE_PREDICATE in warning for warning in warnings)


def test_previously_possible_bypass_is_now_refused(payload) -> None:
    model = _configured_model(payload, "Pb")
    model.condensation_temperatures_C["Pb"] = 600.0
    flux = EvaporationFlux(species_kg_hr={"Pb": 1.0})
    flux.update_totals()

    result = model.route(flux, MeltState())

    assert "Pb" not in result.wall_deposit_by_species
    assert all(
        "Pb" not in species_mass
        for species_mass in result.condensed_by_stage_species.values()
    )
    assert result.condensation_refusals_by_species["Pb"] == {
        "status": "refused",
        "reason": REFUSAL_INAPPLICABLE_PREDICATE,
        "output_status": "status_bearing",
    }
    assert "Pb" not in result.wall_deposit_fraction_by_species
    assert result.remaining_by_species["Pb"] == pytest.approx(1.0)
    assert sum(result.remaining_by_species.values()) == pytest.approx(
        flux.total_kg_hr
    )


def test_unknown_applicability_predicate_fails_closed(payload) -> None:
    mutated = deepcopy(payload)
    _set_applicability(mutated, "Pb", "hot_train")

    assert _condensation_admission_refusal(
        "Pb", vapor_pressure_data=mutated
    ) == REFUSAL_INAPPLICABLE_PREDICATE


def test_applicable_rows_are_unchanged(payload) -> None:
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    active = {
        species_id
        for species_id, row in catalog.species.items()
        if row.code_metadata.hot_train_applicability in {"applicable", "always"}
    }
    assert len(active) == 35
    for species_id in active:
        assert catalog.evaluator_for_hot_train(species_id) is catalog.species[
            species_id
        ].evaluator
    for species_id in ("Na", "K", "Fe", "SiO", "Mg", "Ca", "Al"):
        assert _species_has_antoine_data(species_id, vapor_pressure_data=payload)


def test_stage0_foulant_still_reads_not_applicable_rows(payload) -> None:
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    foulants = [
        row
        for row in catalog.species.values()
        if row.code_metadata.compatibility_projection == "foulant_vapor"
        and row.code_metadata.hot_train_applicability == "not_applicable"
    ]
    assert len(foulants) == 22
    assert all(row.evaluator is not None for row in foulants)

    split = chi_escape_salt("NaCl", 900.0, 1.0e-3)
    assert split.escaped_frac == pytest.approx(0.7055826222037498)
    assert split.retained_frac == pytest.approx(0.2944173777962502)
    assert split.status == "ok"


def test_refusal_out_of_domain_and_proven_zero_stay_distinct(payload) -> None:
    extrapolations: dict[str, dict] = {}
    with pytest.raises(WallSaturationPressureRefusal) as exc_info:
        _wall_deposition_driving_pressure_pa(
            "Pb",
            100.0,
            1500.0,
            vapor_pressure_data=payload,
            reactive_product_backstop=False,
            antoine_extrapolations=extrapolations,
        )
    assert exc_info.value.reason == REFUSAL_INAPPLICABLE_PREDICATE
    assert extrapolations == {}

    admitted_extrapolations: dict[str, dict] = {}
    pressure = _antoine_psat_pa(
        "Ca",
        1900.0,
        vapor_pressure_data=payload,
        antoine_extrapolations=admitted_extrapolations,
    )
    assert pressure is not None and pressure > 0.0
    assert admitted_extrapolations["Ca"]["temperature_K"] == pytest.approx(1900.0)


def test_predicate_readers_cannot_diverge() -> None:
    tokens = (
        "applicable",
        "always",
        "not_applicable",
        "inapplicable",
        "stage0_only",
        "",
        "hot_train",
    )
    stages = (None, "stage0", "c0b_p_cleanup", "stage0_p_carriers")
    phases = (None, "stage0")
    for token, stage, phase in product(tokens, stages, phases):
        state = SimpleNamespace(stage=stage, process_phase=phase)
        ordinary = SimpleNamespace(
            applicability_predicate=token,
            parent_species_ids=("Na2O",),
        )
        shared = applicability_verdict(token, process_phase=phase, stage=stage)
        actual = _predicate_active(ordinary, state)
        if stage == "c0b_p_cleanup":
            assert actual == (
                False,
                "c0b_p_cleanup admits only P2O5-sourced carrier rules",
            )
        else:
            assert actual == shared

        p_rule = SimpleNamespace(
            applicability_predicate=token,
            parent_species_ids=("P2O5",),
        )
        p_actual = _predicate_active(p_rule, state)
        if (
            token == "stage0_only"
            and not shared[0]
            and stage in {"stage0_p_carriers", "c0b_p_cleanup"}
        ):
            assert p_actual == (True, "")
        else:
            assert p_actual == shared


def test_every_flux_bearing_species_is_hot_train_applicable_or_refused(payload) -> None:
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    legacy = vapor_pressure_legacy_view(payload)
    flux_species = set(legacy.get("metals", {})) | set(legacy.get("oxide_vapors", {}))
    assert len(flux_species) == 41
    for species_id in flux_species:
        token = catalog.species[species_id].code_metadata.hot_train_applicability
        assert token in {"applicable", "always"} or (
            _condensation_admission_refusal(
                species_id, vapor_pressure_data=payload
            )
            is not None
        )


def test_legacy_antoine_rows_still_consult_applicability(payload) -> None:
    flipped = deepcopy(payload)
    _set_applicability(flipped, "Na", "not_applicable")
    assert not _species_has_antoine_data("Na", vapor_pressure_data=flipped)
    with pytest.raises(HotTrainInapplicable):
        _antoine_psat_pa("Na", 1200.0, vapor_pressure_data=flipped)

    model = _configured_model(flipped, "Na")
    result = model.route(
        EvaporationFlux(species_kg_hr={"Na": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )
    assert "Na" not in result.wall_deposit_by_species

    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    measured = {
        species_id
        for species_id in catalog.species
        if _condensation_admission_refusal(
            species_id, vapor_pressure_data=payload
        )
        == REFUSAL_INAPPLICABLE_PREDICATE
        and not _species_has_compiled_or_legacy_pressure(
            species_id, vapor_pressure_data=payload
        )
    }
    assert measured == REORDERED_ROWS


def test_stage0_only_refusal_is_phase_dependent(payload) -> None:
    catalog = compiled_catalog_for(payload, emit_u0_request_rules=False)
    for species_id in P_CARRIERS:
        assert _condensation_admission_refusal(
            species_id, vapor_pressure_data=payload
        ) == REFUSAL_INAPPLICABLE_PREDICATE

        assert catalog.evaluator_for_hot_train(
            species_id,
            process_phase="stage0",
            stage="stage0",
        ) is catalog.species[species_id].evaluator
        assert _species_vapor_data(
            species_id, vapor_pressure_data=payload
        )["condensation_saturation_model"] == (
            "unavailable_source_reaction_not_psat"
        )
        assert not _species_has_compiled_or_legacy_pressure(
            species_id, vapor_pressure_data=payload
        )


@pytest.mark.parametrize("species_id", ("BaO", "Pb", "WO3", "NaF", "MoO3"))
def test_reproduction_battery_sees_unvalidated_rows(payload, species_id) -> None:
    pressure, refusal = _engine_pure_psat_pa(species_id, 1400.0, payload)
    assert isinstance(pressure, float)
    assert refusal is None
