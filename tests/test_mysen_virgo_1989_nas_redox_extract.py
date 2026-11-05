"""Render-pinned extraction and isolated migration for Mysen and Virgo (1989)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    AmountBasis,
    Phase,
    Quantity,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "mysen-virgo-1989-nas-redox"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"

RENDER_PINS = {
    "mysen_virgo_1989_p003_t02_r003_c010": "0.797",
    "mysen_virgo_1989_p003_t02_r025_c010": "0.683",
    "mysen_virgo_1989_p003_t02_r055_c010": "0.200",
}
COMPOSITIONS = {
    "NASI": {"SiO2": "78.97", "Al2O3": "5.63", "Na2O": "10.39", "Fe2O3": "5.00"},
    "NASII": {"SiO2": "71.08", "Al2O3": "8.42", "Na2O": "15.50", "Fe2O3": "5.00"},
    "NASIII": {"SiO2": "55.54", "Al2O3": "13.89", "Na2O": "25.58", "Fe2O3": "5.00"},
    "NASIV": {"SiO2": "43.97", "Al2O3": "18.70", "Na2O": "32.33", "Fe2O3": "5.00"},
    "NASV": {"SiO2": "33.75", "Al2O3": "21.56", "Na2O": "39.69", "Fe2O3": "5.00"},
    "NASVI": {"SiO2": "35.06", "Al2O3": "14.89", "Na2O": "45.05", "Fe2O3": "5.00"},
    "NAS-VII": {"SiO2": "39.42", "Al2O3": "16.77", "Na2O": "38.82", "Fe2O3": "5.00"},
    "NAS-VIII": {"SiO2": "47.32", "Al2O3": "20.12", "Na2O": "27.56", "Fe2O3": "5.00"},
    "NASIX": {"SiO2": "52.43", "Al2O3": "22.30", "Na2O": "20.27", "Fe2O3": "5.00"},
    "NASX": {"SiO2": "37.61", "Al2O3": "21.89", "Na2O": "35.50", "Fe2O3": "5.00"},
    "NASXI": {"SiO2": "49.63", "Al2O3": "14.30", "Na2O": "31.34", "Fe2O3": "5.00"},
    "NAS-XII": {"SiO2": "53.63", "Al2O3": "11.37", "Na2O": "30.00", "Fe2O3": "5.00"},
    "NAS-XIII": {"SiO2": "58.69", "Al2O3": "7.97", "Na2O": "28.35", "Fe2O3": "5.00"},
}
TEMPERATURE_K = {
    "1550": Decimal("1823.15"),
    "1400": Decimal("1673.15"),
    "1250": Decimal("1523.15"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for observation in document["species"]["Fe"]["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 53, "Table 2 has 53 printed experimental rows"
    for observation_id, expected in RENDER_PINS.items():
        observation = observations[observation_id]
        assert observation["values"]["value"] == expected
        assert observation["locator"] == {
            "page": 3,
            "published_page": 60,
            "pdf_page_index": 2,
            "table": "2",
            "row": observation["values"]["sample_as_published"],
            "column": "Fe3+/ΣFe",
            "phase": None,
        }

    assert observations["mysen_virgo_1989_p003_t02_r003_c010"]["point_conditions"][
        "composition"
    ]["state"]["value"]["components"] == COMPOSITIONS["NASI"]
    assert check_all_fidelity_samples_match(document) == []

    for observation in observations.values():
        conditions = observation["point_conditions"]
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert conditions["fO2_log"]["units"] == "log10(bar)"
        assert observation["phase"] == "glass"
        assert observation["quench_statement"]["preservation"] == "asserted_preserved"
        assert observation["quench_statement"]["locator"]["page"] == 4
        assert observation["quench_statement"]["locator"]["published_page"] == 61


def test_render_transcription_pins_table_rows_and_rejects_interior_mutations() -> None:
    document = _document()
    _assert_render_pins(document)

    changed_ratio = deepcopy(document)
    changed_ratio["species"]["Fe"]["observations"][22]["values"]["value"] = "0.684"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_ratio)

    changed_pressure = deepcopy(document)
    changed_pressure["species"]["Fe"]["observations"][0]["point_conditions"][
        "total_pressure_Pa"
    ]["state"] = {"tag": "value", "value": "101325"}
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_pressure)


def test_isolated_migration_binds_all_conditions_and_keeps_printed_fraction() -> None:
    document = _document()
    _assert_render_pins(document)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 53
    source_by_id = _observations(document)
    migrated_by_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in result.observations.values()
    }
    assert set(migrated_by_id) == set(source_by_id)

    quantity_unknown = set()
    phase_unknown = set()
    unavailable = set()
    for observation_id, source in source_by_id.items():
        migrated = migrated_by_id[observation_id]
        if migrated.identity.quantity.is_unknown:
            quantity_unknown.add(observation_id)
        if migrated.identity.species.phase.is_unknown:
            phase_unknown.add(observation_id)
        if migrated.value.kind is ValueKind.UNAVAILABLE:
            unavailable.add(observation_id)

        assert migrated.identity.quantity.is_value
        assert migrated.identity.quantity.value is Quantity.VALENCE_RATIO
        assert migrated.identity.valence_basis is not None
        assert migrated.identity.valence_basis.is_value
        assert migrated.identity.valence_basis.value.numerator_oxidation_states == (3,)
        assert (
            migrated.identity.valence_basis.value.denominator_kind
            is ValenceDenominatorKind.TOTAL_ELEMENT
        )
        assert (
            migrated.identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
        )
        assert migrated.identity.species.formula == "Fe"
        assert migrated.identity.species.phase.is_value
        assert migrated.identity.species.phase.value is Phase.GLASS
        assert migrated.evidence.class_.value == "measured_reduced"
        assert migrated.value.kind is ValueKind.POINT
        assert migrated.value.point == Decimal(source["values"]["value"])
        assert migrated.identity.temperature_K.is_value
        assert migrated.identity.temperature_K.value == TEMPERATURE_K[source["T_C"]]
        assert migrated.identity.fO2_Pa.is_value
        assert migrated.identity.total_pressure_Pa.is_unknown

        conditions = migrated.point_conditions
        assert conditions is not None
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].state.value == TEMPERATURE_K[source["T_C"]]
        assert conditions["temperature_K"].locator.page == 3
        assert conditions["temperature_K"].locator.table == "2"
        assert conditions["sample_id"].state.is_value
        assert conditions["sample_id"].state.value == source["values"]["sample_as_published"]
        assert conditions["fO2_log"].state.is_value
        assert conditions["fO2_log"].state.value == Decimal(
            source["point_conditions"]["fO2_log"]["state"]["value"]
        )
        assert conditions["fO2_log"].locator.page == 3
        assert conditions["fO2_log"].locator.table == "2"
        assert "total_pressure_Pa" not in conditions
        assert conditions["composition"].state.is_value
        assert conditions["composition"].locator.page == 2
        assert conditions["composition"].locator.table == "1"
        assert conditions["composition"].state.value.amount_basis is AmountBasis.MOLE_FRACTION

        composition_label = source["values"]["composition_sample_as_published"]
        expected_composition = wt_pct_to_mole_fraction(
            {
                oxide: Decimal(amount)
                for oxide, amount in COMPOSITIONS[composition_label].items()
            }
        )
        assert conditions["composition"].state.value == expected_composition
        assert migrated.identity.composition.is_value
        assert migrated.identity.composition.value == expected_composition

    assert quantity_unknown == set()
    assert phase_unknown == set()
    assert unavailable == set()

    starred = [
        observation
        for observation in source_by_id.values()
        if observation["values"]["sample_as_published"].endswith("**")
    ]
    assert len(starred) == 7
    assert all(
        observation["values"]["spectrum_measurement_temperature_K"] == "77"
        for observation in starred
    )
