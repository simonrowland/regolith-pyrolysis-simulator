from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.enums import AdmissionStatus, Quantity, RefusalReason
from simulator.battery import input_provenance
from simulator.battery.input_provenance import (
    COMPOSITION_FROM_SINGLE_LIQUID_GLASS,
    SINGLE_LIQUID_GLASS_REDOX_FEOT_ONLY,
    SingleLiquidGlassDecision,
    analysed_melt_weight_map,
    decide_single_liquid_glass_composition,
)
from simulator.battery.migrate import (
    Migrator,
    _single_liquid_glass_composition,
    _single_liquid_glass_locator,
    _single_liquid_glass_oxide_map,
)
from simulator.battery.score import ScoreContext, comparison_candidates


_OXIDES = {
    "SiO2": Decimal("60"),
    "Al2O3": Decimal("10"),
    "CaO": Decimal("30"),
}


def _glass_row(
    *,
    observation_id: str = "glass",
    experiment: str = "run-1",
    assemblage: str = "glass",
    phase: str = "glass",
    oxides: dict[str, Decimal] | None = None,
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "type": "composition",
        "experiment": experiment,
        "locator": {"page": 4, "table": "1", "row": "3"},
        "values": {
            "quantity": Quantity.EQUILIBRIUM_PHASE_COMPOSITION.value,
            "formula": "SiO2",
            "phase_label": phase,
            "phase_assemblage_as_printed": assemblage,
            "basis": "wt_percent",
            "composition_wt_pct": {
                name: str(amount) for name, amount in (oxides or _OXIDES).items()
            },
            "component_amount": {"value": "60"},
            "admission_status": "admitted",
            "sample_id_as_printed": "S-1",
            "sample_no_as_printed": "7",
        },
    }


def _target_row(
    *,
    observation_id: str = "activity",
    experiment: str = "run-1",
    quantity: Quantity = Quantity.ACTIVITY,
) -> dict[str, object]:
    values: dict[str, object] = {
        "quantity": quantity.value,
        "method_class": "measured_direct",
        "sample_id_as_printed": "S-1",
        "sample_no_as_printed": "7",
    }
    if quantity is Quantity.ACTIVITY:
        values["activity"] = "0.5"
    else:
        values["value"] = "0.25"
    return {
        "observation_id": observation_id,
        "type": "activity",
        "experiment": experiment,
        "phase": "liquid",
        "units": "dimensionless",
        "locator": {"page": 4, "table": "1", "row": "3"},
        "values": values,
    }


def _decide(
    observations: list[tuple[str, str, dict[str, object]]],
    *,
    experiments: list[dict[str, object]] | None = None,
) -> dict[str, SingleLiquidGlassDecision]:
    return decide_single_liquid_glass_composition(
        experiments=experiments or [],
        observations=observations,
        oxide_map_for_row=_single_liquid_glass_oxide_map,
        composition_for_oxides=_single_liquid_glass_composition,
        locator_for=_single_liquid_glass_locator,
    )


def test_migration_uses_single_liquid_glass_as_condition_and_consumes_its_record(
    tmp_path: Path,
) -> None:
    extract_dir = tmp_path / "data" / "literature" / "extracts"
    extract_dir.mkdir(parents=True)
    (tmp_path / "data" / "literature" / "compilations").mkdir(parents=True)
    path = extract_dir / "single-liquid-fixture.yaml"
    glass_oxides = {
        "SiO2": Decimal("58"),
        "Al2O3": Decimal("12"),
        "CaO": Decimal("10"),
        "FeO": Decimal("8"),
        "Fe2O3": Decimal("12"),
    }
    document = {
        "schema_version": "literature_extract.v1",
        "source_id": "single-liquid-fixture",
        "source": {"citation": "Single-liquid glass fixture"},
        "experiments": [
            {
                "experiment_id": "run-1",
                "sample": {
                    "sample_id_as_printed": "S-1",
                    "sample_no_as_printed": "7",
                    "form": {
                        "state": {"tag": "value", "value": "quenched to glass"},
                        "locator": {"page": 4, "table": "1", "row": "3"},
                    },
                    "volatile_loss_statement": {
                        "value": "Open system; alkali loss was reported.",
                        "locator": {"page": 4, "table": "1", "row": "3"},
                    },
                    "container_exchange_statement": {
                        "value": "Iron loss to Pt was reported.",
                        "locator": {"page": 4, "table": "1", "row": "3"},
                    },
                },
            }
        ],
        "species": {
            "SiO2": {
                "observations": [
                    _glass_row(oxides=glass_oxides),
                    {
                        **_target_row(),
                        "values": {
                            **_target_row()["values"],
                            "value": "0.5",
                        },
                    },
                ]
            },
            "Fe": {
                "observations": [
                    _target_row(
                        observation_id="redox",
                        quantity=Quantity.FE3_FE2_RATIO,
                    )
                ]
            },
        },
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    migrator = Migrator(root=tmp_path, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()

    activity = migrator.result.observations[
        "single-liquid-fixture::activity"
    ]
    composition_state = activity.identity.composition
    assert composition_state.is_value
    composition = composition_state.value
    assert composition.proxy_flag == COMPOSITION_FROM_SINGLE_LIQUID_GLASS
    assert composition.input_provenance == COMPOSITION_FROM_SINGLE_LIQUID_GLASS
    condition = activity.point_conditions["composition"]
    assert condition.state.value == composition
    caveats = activity.provenance[COMPOSITION_FROM_SINGLE_LIQUID_GLASS]
    assert caveats["volatile_loss"]["state"] == "stated"
    assert caveats["volatile_loss"]["locator"]["page"] == 4
    assert "alkali" in " ".join(caveats["volatile_loss"]["evidence"])
    assert caveats["container_exchange"]["state"] == "stated"
    assert caveats["container_exchange"]["locator"]["page"] == 4

    glass = migrator.result.observations["single-liquid-fixture::glass"]
    assert glass.admission.status is AdmissionStatus.REJECTED
    assert glass.admission.reason == (
        RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
    )
    glass_condition = glass.point_conditions["composition"]
    echo_identity = replace(glass.identity, composition=glass_condition.state)
    echo = input_provenance.input_composition_echo_detail(echo_identity)
    assert echo is not None
    assert echo["reason"] == RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
    assert (
        glass.provenance[COMPOSITION_FROM_SINGLE_LIQUID_GLASS]["status"]
        == "consumed_as_condition"
    )
    score_context = ScoreContext(
        works={},
        experiments={},
        observations={glass.observation_id: glass},
        root=tmp_path,
    )
    assert glass not in comparison_candidates(score_context)

    redox = migrator.result.observations["single-liquid-fixture::redox"]
    redox_composition = redox.identity.composition.value
    redox_names = {name for name, _amount in redox_composition.components}
    assert redox_composition.input_provenance == COMPOSITION_FROM_SINGLE_LIQUID_GLASS
    assert len(redox_names.intersection({"FeO", "FeOT"})) == 1
    assert "Fe2O3" not in redox_names
    assert input_provenance.input_composition_echo_detail(redox.identity) is None


def test_crystal_bearing_run_does_not_receive_glass_condition() -> None:
    rows = [
        _glass_row(assemblage="glass + olivine"),
        _target_row(),
    ]
    decisions = _decide(
        [(str(row["observation_id"]), "SiO2", row) for row in rows]
    )
    assert decisions["activity"].status != "applied"
    assert decisions["activity"].reason == "crystal_bearing_assemblage"


def test_explicit_run_composition_prevents_glass_fallback() -> None:
    rows = [_glass_row(), _target_row()]
    decisions = _decide(
        [(str(row["observation_id"]), "SiO2", row) for row in rows],
        experiments=[
            {
                "experiment_id": "run-1",
                "sample": {
                    "sample_id_as_printed": "S-1",
                    "printed_composition": {
                        "state": {
                            "tag": "value",
                            "value": {
                                "amount_basis": "mass_percent",
                                "components": [["SiO2", "100"]],
                            },
                        }
                    },
                },
            }
        ],
    )
    assert decisions["activity"].status == "not_target"
    assert decisions["activity"].reason == "run_composition_already_stated"
    assert not decisions["glass"].consumed_as_condition


def test_coexisting_spinel_in_run_notes_is_crystal_bearing() -> None:
    glass = _glass_row()
    glass["values"]["notes"] = "A spinel phase is present during the run."
    decisions = _decide(
        [("glass", "SiO2", glass), ("activity", "SiO2", _target_row())]
    )
    assert decisions["activity"].status != "applied"
    assert decisions["activity"].reason == "crystal_bearing_assemblage"


def test_non_oxide_glass_component_is_consumed_with_the_oxide_vector() -> None:
    glass = _glass_row()
    chloride = _glass_row(observation_id="glass-cl")
    chloride_values = chloride["values"]
    chloride_values.pop("composition_wt_pct")
    chloride_values["formula"] = "Cl"
    chloride_values["component_amount"] = {"value": "0.5"}
    rows = [glass, chloride, _target_row()]
    decisions = _decide(
        [(str(row["observation_id"]), "SiO2", row) for row in rows]
    )
    assert decisions["glass-cl"].consumed_as_condition
    assert decisions["glass-cl"].reason == (
        RefusalReason.INPUT_COMPOSITION_IS_SCORED_PHASE_ANALYSIS.value
    )


def test_crystal_guard_fails_if_shared_consumer_reverts_to_old_marker(
    monkeypatch,
) -> None:
    current_checker = input_provenance._is_crystal_bearing

    def old_marker_rule(
        text: str, *, include_single_liquid_phases: bool = False
    ) -> bool:
        return current_checker(
            text, include_single_liquid_phases=include_single_liquid_phases
        ) and "spinel" not in text.casefold()

    def assert_spinel_run_is_rejected() -> None:
        glass = _glass_row()
        glass["values"]["notes"] = "A spinel phase is present during the run."
        decisions = _decide(
            [("glass", "SiO2", glass), ("activity", "SiO2", _target_row())]
        )
        assert decisions["activity"].status != "applied"

    with monkeypatch.context() as mutation:
        mutation.setattr(input_provenance, "_is_crystal_bearing", old_marker_rule)
        with pytest.raises(AssertionError):
            assert_spinel_run_is_rejected()


def test_ambiguous_exact_run_id_does_not_receive_glass_condition() -> None:
    duplicate_run = {
        "experiment_id": "run-1",
        "sample": {"sample_id_as_printed": "S-1"},
    }
    rows = [_glass_row(), _target_row()]
    decisions = _decide(
        [(str(row["observation_id"]), "SiO2", row) for row in rows],
        experiments=[duplicate_run, duplicate_run],
    )
    assert decisions["activity"].status != "applied"
    assert decisions["activity"].reason == "ambiguous_run_id"


def test_redox_input_collapses_the_printed_iron_split_to_total_feot() -> None:
    oxides = {
        "SiO2": Decimal("58"),
        "Al2O3": Decimal("12"),
        "CaO": Decimal("10"),
        "FeO": Decimal("8"),
        "Fe2O3": Decimal("12"),
    }
    rows = [
        _glass_row(oxides=oxides),
        _target_row(quantity=Quantity.FE3_FE2_RATIO),
    ]
    decisions = _decide(
        [(str(row["observation_id"]), "Fe", row) for row in rows]
    )
    decision = decisions["activity"]
    assert decision.status == "applied"
    assert decision.composition is not None
    redox_weights = dict(decision.composition_wt_percent)
    production_weights, _excluded = analysed_melt_weight_map(
        oxides, formula="", redox=True
    )
    assert redox_weights["FeOT"] == Decimal(str(production_weights["FeO"]))
    assert "FeO" not in redox_weights
    assert "Fe2O3" not in redox_weights
    composition_names = {name for name, _amount in decision.composition.components}
    assert len(composition_names.intersection({"FeO", "FeOT"})) == 1
    assert "Fe2O3" not in composition_names
    assert SINGLE_LIQUID_GLASS_REDOX_FEOT_ONLY in (
        decision.composition.analysis_selection_rule or ""
    )
