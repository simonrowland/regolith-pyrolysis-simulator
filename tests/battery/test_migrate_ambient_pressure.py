from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

import simulator.battery.migrate as migrate_module

from simulator.battery.enums import (
    AmbientPressureEvidenceClass,
    Authority,
    FO2Channel,
    IdentityEqualKind,
    NoticeKind,
    Quantity,
)
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import Migrator, _ambient_pressure_band_for
from simulator.battery.records import FO2Control, GasRatio, Value
from tests.battery import factories as F


def _ambient_pressure_result(
    tmp_path,
    *,
    quantity: Quantity = Quantity.TRANSITION_TEMPERATURE,
    include_evidence: bool = True,
    point_pressure_Pa: str | None = None,
    exploded: bool = False,
):
    source_id = "ambient-pressure-fixture"
    extract_dir = tmp_path / "data" / "literature" / "extracts"
    extract_dir.mkdir(parents=True)
    experiment = {"experiment_id": "open", "conditions": {"temperature_K": 1200}}
    if include_evidence:
        experiment["ambient_pressure_evidence"] = {
            "evidence_class": "gas_flow",
            "quote": "The sample was heated in a furnace with a continuous gas flow.",
            "locator": {"page": 3, "section": "Experimental method"},
        }
    if quantity is Quantity.P_PARTIAL:
        partial_values = {
            "quantity": "p_partial",
            "method_class": "measured_direct",
            "admission_status": "admitted",
        }
        partial_values.update(
            {"series": [{"T_K": 1200, "partial_pressure_pa": 25}]}
            if exploded
            else {"P": 25}
        )
        observation = {
            "observation_id": "partial-pressure",
            "experiment": "open",
            "type": "p_partial",
            "phase": "g",
            "units": "Pa",
            "locator": {"page": 4, "table": "1"},
            "values": partial_values,
        }
    elif quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION:
        observation = {
            "observation_id": "residue",
            "experiment": "open",
            "type": "residue_component_composition",
            "phase": "glass",
            "units": "wt%",
            "locator": {"page": 4, "table": "1"},
            "values": {
                "quantity": "residue_component_composition",
                "method_class": "measured_direct",
                "FeO_wt_pct": "12.59",
                "T_C": "1199",
            },
        }
    elif quantity is Quantity.CONDENSATE_COMPOSITION:
        observation = {
            "observation_id": "condensate",
            "experiment": "open",
            "type": "condensate_composition",
            "phase": "cr",
            "units": "mol%",
            "locator": {"page": 4, "table": "1"},
            "values": {
                "quantity": "condensate_composition",
                "method_class": "measured_direct",
                "component_mole_fractions": {"Na": "0.5", "K": "0.5"},
                "T_K": 1200,
            },
        }
    else:
        observation = {
            "observation_id": "transition",
            "experiment": "open",
            "type": "transition_temperature",
            "phase": "cr",
            "units": "K",
            "locator": {"page": 4, "table": "1"},
            "values": {
                "quantity": "transition_temperature",
                "property_kind": "melting",
                "method_class": "measured_direct",
                "admission_status": "admitted",
                "T_m_K": 1200,
            },
        }
    if point_pressure_Pa is not None:
        observation["point_conditions"] = {
            "total_pressure_Pa": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": point_pressure_Pa},
                },
                "locator": {"page": 4},
            }
        }
    payload = {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": {"citation": "Synthetic ambient-pressure fixture (2026)."},
        "extraction": {"method": "unit test", "date": "2026-11-03", "worker": "pytest"},
        "experiments": [experiment],
        "species": {"FeO": {"observations": [observation]}},
    }
    path = extract_dir / f"{source_id}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    migrator = Migrator(root=tmp_path, index={}, aliases={})
    migrator._migrate_extract(path)
    result = next(iter(migrator.result.observations.values()))
    return migrator, result


def _experiment_with_gas_ratio(numerator: str, denominator: str):
    experiment = F.kems_experiment()
    ratio = GasRatio(
        numerator_species=numerator,
        denominator_species=denominator,
        value=F.located(Value.point_of("1")),
        basis="mole",
    )
    return replace(
        experiment,
        fO2_control=FO2Control(
            channel=F.State.of(FO2Channel.GAS_MIX), gas_ratio=ratio
        ),
    )


def test_ambient_pressure_condensed_phase_has_no_band() -> None:
    experiment = F.kems_experiment()

    band, reason = _ambient_pressure_band_for(
        Quantity.ACTIVITY,
        species_formula="FeO",
        experiment=experiment,
    )

    assert band is None
    assert "no band assigned" in reason


@pytest.mark.parametrize(
    "quantity",
    [
        Quantity.PARTIAL_MOLAR_ENTHALPY,
        Quantity.TRANSITION_TEMPERATURE,
        Quantity.VISCOSITY,
        Quantity.DENSITY,
        Quantity.ELECTRICAL_CONDUCTIVITY,
        Quantity.FE3_FE2_RATIO,
        Quantity.VALENCE_RATIO,
    ],
)
def test_ambient_pressure_condensed_quantity_kinds_have_no_band(
    quantity: Quantity,
) -> None:
    band, reason = _ambient_pressure_band_for(
        quantity,
        species_formula="FeO",
        experiment=F.kems_experiment(),
    )

    assert band is None
    assert "no band assigned" in reason


@pytest.mark.parametrize(
    "quantity",
    [Quantity.CONDENSATE_COMPOSITION, Quantity.RESIDUE_COMPONENT_COMPOSITION],
)
def test_ambient_pressure_kinetic_composition_kinds_have_absolute_band(
    quantity: Quantity,
) -> None:
    band, _reason = _ambient_pressure_band_for(
        quantity,
        species_formula="Na",
        experiment=F.kems_experiment(),
    )

    assert band == "-0.10/+0.01 dex"


@pytest.mark.parametrize(
    "quantity",
    [Quantity.CONDENSATE_COMPOSITION, Quantity.RESIDUE_COMPONENT_COMPOSITION],
)
def test_ambient_filled_kinetic_composition_row_carries_absolute_band(
    tmp_path, quantity: Quantity
) -> None:
    _migrator, observation = _ambient_pressure_result(tmp_path, quantity=quantity)

    assert observation.identity.total_pressure_Pa.value == Decimal("101325")
    notice, = observation.notices
    assert notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
    assert notice.band == "-0.10/+0.01 dex"


def test_ambient_pressure_co2_co_p_o2_has_no_band() -> None:
    experiment = _experiment_with_gas_ratio("CO2", "CO")

    band, reason = _ambient_pressure_band_for(
        Quantity.P_PARTIAL,
        species_formula="O2",
        experiment=experiment,
    )

    assert band is None
    assert "CO2/CO ratio" in reason


def test_ambient_pressure_h2_co2_is_flag_only() -> None:
    experiment = _experiment_with_gas_ratio("H2", "CO2")

    band, reason = _ambient_pressure_band_for(
        Quantity.ACTIVITY,
        species_formula="FeO",
        experiment=experiment,
    )

    assert band is None
    assert "weakly pressure-dependent" in reason
    assert "flagged without a band" in reason


def test_ambient_pressure_absolute_pressure_band() -> None:
    experiment = F.kems_experiment()

    band, _reason = _ambient_pressure_band_for(
        Quantity.P_PARTIAL,
        species_formula="Na",
        experiment=experiment,
    )

    assert band == "-0.10/+0.01 dex"


def test_ambient_pressure_evaporation_alpha_uses_absolute_pressure_band() -> None:
    band, _reason = _ambient_pressure_band_for(
        Quantity.EVAPORATION_COEFFICIENT_ALPHA,
        species_formula="Na",
        experiment=F.kems_experiment(),
    )

    assert band == "-0.10/+0.01 dex"


def test_open_furnace_evidence_fills_required_pressure_with_authority_and_notice(
    tmp_path,
) -> None:
    migrator, observation = _ambient_pressure_result(tmp_path)
    experiment = migrator.result.experiments[observation.experiment_id]

    assert experiment.ambient_pressure_evidence is not None
    assert (
        experiment.ambient_pressure_evidence.evidence_class
        is AmbientPressureEvidenceClass.GAS_FLOW
    )
    assert observation.identity.total_pressure_Pa.value == Decimal("101325")
    notice, = observation.notices
    assert notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
    assert notice.authority is Authority.DERIVED_FROM_APPARATUS
    assert notice.band is None
    assert observation.derivation is None
    assert not any(
        entry.axes == ["total_pressure_Pa"]
        and entry.why == "source does not state a numeric total_pressure_Pa"
        for entry in migrator.result.queue
    )


def test_absent_open_furnace_evidence_keeps_typed_identity_refusal(tmp_path) -> None:
    _migrator, observation = _ambient_pressure_result(
        tmp_path, include_evidence=False
    )
    assert observation.identity.total_pressure_Pa.is_unknown
    assert observation.notices == ()

    identity = replace(
        observation.identity,
        species=replace(
            observation.identity.species,
            polymorph=F.State.of("wustite"),
        ),
    )
    candidate = replace(
        identity,
        total_pressure_Pa=F.State.of(Decimal("101325")),
    )
    refusal = identity_equal(identity, candidate)
    assert refusal.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert refusal.fields == ("total_pressure_Pa",)


def test_printed_point_pressure_wins_over_open_furnace_assumption(tmp_path) -> None:
    _migrator, observation = _ambient_pressure_result(
        tmp_path, point_pressure_Pa="250000"
    )

    assert observation.identity.total_pressure_Pa.value == Decimal("250000")
    assert not any(
        notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
        for notice in observation.notices
    )


def test_ambient_partial_pressure_is_not_copied_to_unmarked_point_condition(
    tmp_path,
) -> None:
    _migrator, observation = _ambient_pressure_result(
        tmp_path, quantity=Quantity.P_PARTIAL
    )

    assert observation.identity.total_pressure_Pa.value == Decimal("101325")
    assert "total_pressure_Pa" not in (observation.point_conditions or {})
    assert any(
        notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
        for notice in observation.notices
    )


def test_ambient_partial_pressure_series_keeps_notice_without_point_copy(
    tmp_path,
) -> None:
    migrator, _observation = _ambient_pressure_result(
        tmp_path, quantity=Quantity.P_PARTIAL, exploded=True
    )
    points = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.value is Quantity.P_PARTIAL
    ]

    assert points
    for observation in points:
        assert observation.identity.total_pressure_Pa.value == Decimal("101325")
        assert "total_pressure_Pa" not in (observation.point_conditions or {})
        assert any(
            notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
            for notice in observation.notices
        )
        assert (
            observation.derivation is None
            or "ambient_pressure_evidence" not in observation.derivation.relation
        )


def test_unmapped_ambient_pressure_category_keeps_typed_identity_refusal(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        migrate_module,
        "_CONDENSED_PHASE_PRESSURE_INSENSITIVE_QUANTITIES",
        migrate_module._CONDENSED_PHASE_PRESSURE_INSENSITIVE_QUANTITIES
        - {Quantity.TRANSITION_TEMPERATURE},
    )
    _migrator, observation = _ambient_pressure_result(tmp_path)

    assert observation.identity.total_pressure_Pa.is_unknown
    assert not any(
        notice.kind is NoticeKind.TOTAL_PRESSURE_AMBIENT_OPEN_FURNACE
        for notice in observation.notices
    )
    identity = replace(
        observation.identity,
        species=replace(
            observation.identity.species,
            polymorph=F.State.of("wustite"),
        ),
    )
    refusal = identity_equal(
        identity, replace(identity, total_pressure_Pa=F.State.of(Decimal("101325")))
    )
    assert refusal.kind is IdentityEqualKind.IDENTITY_UNKNOWN
    assert refusal.fields == ("total_pressure_Pa",)
