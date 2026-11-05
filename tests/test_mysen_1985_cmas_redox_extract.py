from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    Phase,
    Quantity,
    ValenceDenominatorKind,
    ValenceValueForm,
    ValueKind,
)
from simulator.battery.identity import ValenceBasis, profile_for
from simulator.battery.migrate import Migrator, _log_fO2_to_pa
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mysen-1985-cmas-redox.yaml"
SOURCE_ID = "mysen-1985-cmas-redox"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate_extract():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["Fe"]["observations"]
    }


def _render_pins(document: dict) -> None:
    observations = _source_observations(document)
    table2 = observations["mysen_1985_table2_mossbauer_fe3_sumfe"]["values"]["points"]
    wet = observations["mysen_1985_table1_wet_chemistry_fe3_sumfe"]["values"]["points"]
    moss = observations["mysen_1985_table1_mossbauer_fe3_sumfe"]["values"]["points"]

    assert len(table2) == 88
    assert len(wet) == 6
    assert len(moss) == 6
    assert table2[0]["sample_code_as_published"] == "CASIF5"
    assert table2[0]["value_as_published"] == "0.556"
    assert observations["mysen_1985_table2_mossbauer_fe3_sumfe"]["locator"]["page"] == 6
    assert observations["mysen_1985_table2_mossbauer_fe3_sumfe"]["locator"]["table"] == "2"
    assert table2[34]["value_as_published"] == "0.072"
    assert table2[75]["sample_code_as_published"] == "MASIXF5"
    assert table2[75]["value_as_published"] == "0.056"
    assert wet[0]["sample_code_as_published"] == "001*"
    assert wet[0]["value_as_published"] == "0.80"
    assert wet[0]["sigma_as_published"] == "0.05"
    assert observations["mysen_1985_table1_wet_chemistry_fe3_sumfe"]["locator"]["page"] == 4
    assert observations["mysen_1985_table1_wet_chemistry_fe3_sumfe"]["locator"]["table"] == "1"
    assert moss[5]["sample_code_as_published"] == "MV78†"
    assert moss[5]["value_as_published"] == "0.71"
    assert moss[5]["bulk_composition_as_published"] == "An46Fo16.2(SiO2)29.7(Fe2O3)8.1"
    assert moss[5]["point_conditions"]["composition"]["state"]["tag"] == "unknown"
    for observation in (observations["mysen_1985_table1_wet_chemistry_fe3_sumfe"], observations["mysen_1985_table1_mossbauer_fe3_sumfe"]):
        for point in observation["values"]["points"]:
            for condition in ("T_C", "fO2_log", "total_pressure_Pa", "composition"):
                assert point["point_conditions"][condition]["state"]["tag"] == "unknown"
                assert point["point_conditions"][condition]["locator"]["page"] == 4
    assert len(
        [point for point in table2 if point.get("spectrum_measurement_temperature_K") == 77]
    ) == 5


def test_mysen_render_pins_and_mutation_are_independent() -> None:
    document = _document()
    _render_pins(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][0]["values"]["points"][0]["value"] = 0.555
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_mysen_isolated_migration_preserves_rows_conditions_and_identity() -> None:
    source = _document()
    _render_pins(source)
    result = _migrate_extract()
    observations = [
        row
        for row in result.observations.values()
        if row.observation_id.startswith(f"{SOURCE_ID}::")
    ]

    assert len(observations) == 100
    assert result.registry_issues == []
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.POINT for row in observations) == 100
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 0
    assert all(row.admission.status is AdmissionStatus.ADMITTED for row in observations)
    assert all(
        row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.VALENCE_RATIO
        for row in observations
    )
    assert all(
        row.identity.species.phase.is_value
        and row.identity.species.phase.value is Phase.GLASS
        for row in observations
    )
    expected_basis = ValenceBasis(
        numerator_oxidation_states=(3,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    assert all(row.identity.valence_basis.value == expected_basis for row in observations)

    table2 = [row for row in observations if "::mysen_1985_table2_" in row.observation_id]
    table1 = [row for row in observations if "::mysen_1985_table1_" in row.observation_id]
    assert len(table2) == 88
    assert len(table1) == 12
    required_axes = frozenset(
        {"composition", "fO2_Pa", "temperature_K", "total_pressure_Pa", "valence_basis"}
    )

    for row in table2:
        assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert row.identity.temperature_K.is_value
        assert row.identity.fO2_Pa.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.composition is not None and row.identity.composition.is_unknown
        assert profile_for(row.identity).required == required_axes
        conditions = row.point_conditions
        assert conditions is not None
        for name in (
            "T_C",
            "fO2_log",
            "total_pressure_Pa",
            "composition",
            "quench_state",
        ):
            assert name in conditions
            assert conditions[name].locator is not None
        assert conditions["T_C"].state.is_value
        assert conditions["fO2_log"].state.is_value
        assert conditions["total_pressure_Pa"].state.is_value
        assert conditions["composition"].state.is_unknown
        assert conditions["composition"].locator.page == 3
        assert conditions["quench_state"].state.value == "not_stated"
        assert conditions["quench_state"].locator.page == 2

    assert table2[0].value.point == Decimal("0.556")
    assert table2[0].locator.page == 6 and table2[0].locator.table == "2"
    assert table2[0].point_conditions["T_C"].state.value == Decimal("1625")
    assert table2[0].identity.temperature_K.value == Decimal("1898.15")
    assert table2[0].point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert table2[0].identity.fO2_Pa.value == _log_fO2_to_pa(
        Decimal("-0.68"), Decimal("101325")
    )
    assert table2[0].identity.total_pressure_Pa.value == Decimal("101325")
    assert table2[34].value.point == Decimal("0.072")
    assert table2[34].locator.page == 6 and table2[34].locator.table == "2"
    assert table2[75].value.point == Decimal("0.056")
    assert table2[75].locator.page == 6 and table2[75].locator.table == "2"

    for row in table1:
        assert row.identity.temperature_K.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions is not None
        conditions = row.point_conditions
        for name in ("T_C", "fO2_log", "total_pressure_Pa"):
            if name == "total_pressure_Pa":
                assert name not in conditions
            else:
                assert conditions[name].state.is_unknown
                assert conditions[name].locator.page == 4
        assert conditions["quench_state"].state.value == "not_stated"
        assert row.locator.page == 4 and row.locator.table == "1"
        assert profile_for(row.identity).required == required_axes

    wet001 = next(row for row in table1 if "wet_chemistry" in row.observation_id and row.value.point == Decimal("0.8"))
    moss_mv78 = next(row for row in table1 if "mossbauer" in row.observation_id and row.value.point == Decimal("0.71"))
    assert wet001.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert wet001.point_conditions["composition"].state.is_unknown
    assert wet001.identity.composition.is_unknown
    assert wet001.value.point == Decimal("0.8")
    assert moss_mv78.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert moss_mv78.locator.page == 4 and moss_mv78.locator.table == "1"
    assert moss_mv78.point_conditions["composition"].state.is_unknown
    assert moss_mv78.point_conditions["composition"].locator.page == 4
    assert moss_mv78.identity.composition.is_unknown

