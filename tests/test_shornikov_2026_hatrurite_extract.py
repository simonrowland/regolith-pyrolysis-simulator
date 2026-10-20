"""Source pins for Shornikov (2026) Table 2 partial pressures."""

from __future__ import annotations

import copy
import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT, convert_pressure_to_pa
from simulator.battery.enums import EvidenceClass, IdentityEqualKind, Quantity
from simulator.battery.identity import identity_equal
from tests.battery.factories import migrate_one_extract

TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as validator  # noqa: E402


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/shornikov-2026-hatrurite-evaporation.yaml"
)


def test_table_2_all_points_migrate_with_temperature_pressure_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())

    assert len(records) == 134
    assert len(result.experiments) == 1
    assert len(result.queue) == 2
    sample_composition = next(iter(result.experiments.values())).sample.initial_composition
    assert sample_composition is not None and sample_composition.state.is_value
    assert dict(sample_composition.state.value.components) == {
        "CaO": Decimal("0.75"),
        "SiO2": Decimal("0.25"),
    }
    assert sample_composition.locator.published_page == 298
    assert sorted((tuple(item.axes), item.why) for item in result.queue) == [
        (("phase",), "missing phase"),
        (("phase",), "missing phase"),
    ]
    assert not result.registry_issues

    pressure_records = [
        record
        for record in records
        if record.identity.quantity.value is Quantity.P_PARTIAL
    ]
    activity_records = [
        record for record in records if record.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(pressure_records) == 104
    assert len(activity_records) == 30

    expected_composition = {"CaO": Decimal("0.75"), "SiO2": Decimal("0.25")}

    def assert_stoichiometric_composition(record):
        composition_state = record.identity.composition
        assert composition_state.is_value
        composition = composition_state.value
        assert composition is not None
        assert composition.basis == "stoichiometric_Ca3SiO5_oxide_mole_fractions"
        assert composition.amount_basis.value == "mole_fraction"
        assert dict(composition.components) == expected_composition

    for record in records:
        assert_stoichiometric_composition(record)
        assert record.identity.temperature_K.is_value
        assert record.identity.species.formula in document["species"]

    for formula, block in document["species"].items():
        source_observation = next(
            observation
            for observation in block["observations"]
            if observation["observation_id"].startswith("shornikov_2026_table2_")
        )
        source_points = source_observation["values"]["points"]
        assert "molybdenum effusion cell (according to Knudsen)" in source_observation[
            "values"
        ]["note"]
        migrated = sorted(
            (
                record
                for record in pressure_records
                if record.identity.species.formula == formula
            ),
            key=lambda record: record.identity.temperature_K.value,
        )
        assert len(migrated) == len(source_points)
        for point, record in zip(source_points, migrated, strict=True):
            expected_pressure, relation = convert_pressure_to_pa(
                point["pressure_atm"], "atm"
            )
            assert relation == "atm_to_Pa"
            assert record.value.point == expected_pressure
            assert record.identity.temperature_K.value == Decimal(point["T_K"])
            condition = record.point_conditions["temperature_K"]
            assert condition.state.value == Decimal(point["T_K"])
            assert condition.locator.table == "2"
            assert condition.locator.pdf_page_index == 10
            assert record.locator.published_page == 306

    # A closed Knudsen cell does not state oxygen fugacity or in-cell total
    # pressure. Composition, temperature, and gas species are known.
    outcomes = [
        identity_equal(record.identity, record.identity) for record in pressure_records
    ]
    assert all(
        outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
        and outcome.fields == ("fO2_Pa", "total_pressure_Pa")
        for outcome in outcomes
    )
    for record in pressure_records:
        assert record.identity.fO2_Pa.is_unknown
        assert record.identity.fO2_Pa.reason
        assert record.identity.total_pressure_Pa.is_unknown
        assert record.identity.total_pressure_Pa.reason == "in_cell_total_pressure_not_derivable"

    for formula in ("CaO", "SiO2"):
        observation = next(
            row
            for row in document["species"][formula]["observations"]
            if row["observation_id"].startswith("shornikov_2026_figure4_")
        )
        source_points = observation["values"]["points"]
        migrated = sorted(
            (
                record
                for record in activity_records
                if record.identity.species.formula == formula
            ),
            key=lambda record: record.identity.temperature_K.value,
        )
        assert len(migrated) == len(source_points) == 15
        reference = migrated[0].identity.reference_state.value
        assert reference.convention.value == "raoultian_pure_endmember"
        assert reference.endmember.formula == formula
        assert reference.endmember.phase.tag.value == "unknown"
        assert reference.endmember.polymorph.tag.value == "unknown"
        assert all(
            record.identity.composition.is_value
            and record.identity.temperature_K.is_value
            and record.identity.species.phase.is_unknown
            and record.identity.species.phase.reason
            and record.identity.species.formula == formula
            and record.identity.reference_state.is_value
            and record.identity.reference_state.value.endmember.phase.is_unknown
            and record.identity.reference_state.value.endmember.polymorph.is_unknown
            for record in migrated
        )
        outcomes = [identity_equal(record.identity, record.identity) for record in migrated]
        assert all(
            outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN
            and outcome.fields == ("species.phase",)
            for outcome in outcomes
        )
        for point, record in zip(source_points, migrated, strict=True):
            assert record.value.point == Decimal(point["activity"])
            assert record.identity.temperature_K.value == Decimal(point["T_K"])
            assert record.evidence.class_.value is EvidenceClass.FIGURE_ONLY
            condition = record.point_conditions["temperature_K"]
            assert condition.state.value == Decimal(point["T_K"])
            assert condition.locator.table == "2"
            assert record.locator.figure == "4"


def test_table_2_fidelity_series_pins_fail_after_value_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert not validator.check_all_fidelity_samples_match(document, label=str(EXTRACT))

    for sample in document["fidelity_samples"]:
        changed = copy.deepcopy(document)
        block = changed["species"][sample["species"]]
        row = next(
            item
            for item in block["observations"]
            if item["observation_id"] == sample["observation_id"]
        )
        if sample["field"] == "points":
            point = row["values"]["points"][0]
            value_key = "pressure_atm" if "pressure_atm" in point else "activity"
            point[value_key] = "9.99e-99"
        else:
            row["values"][sample["field"]] += " mutation"
        assert validator.check_fidelity_sample_matches(changed, sample)
