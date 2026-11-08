from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-yakovlev-2025-ca2sio4-conference.yaml"
OBSERVATION_IDS = {
    "shornikov_yakovlev_2025_mixing_enthalpy",
    "shornikov_yakovlev_2025_mixing_entropy",
    "shornikov_yakovlev_2025_prior_mixing_enthalpy_range",
    "shornikov_yakovlev_2025_prior_mixing_entropy_range",
}
RENDERED_PINS = (
    "−34.90 ± 0.20 kJ/mol",
    "6.60 ± 0.10 J/(mol K)",
    ["−54", "−34"],
    ["−2.4", "3.3"],
)


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["Ca2SiO4"]["observations"]
    }


def _render_pins(document: dict) -> tuple[object, ...]:
    rows = _observations(document)
    return (
        rows["shornikov_yakovlev_2025_mixing_enthalpy"]["values"]["value_as_printed"],
        rows["shornikov_yakovlev_2025_mixing_entropy"]["values"]["value_as_printed"],
        rows["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"]["values"]["range_kJ_mol_as_published"],
        rows["shornikov_yakovlev_2025_prior_mixing_entropy_range"]["values"]["range_J_mol_K_as_published"],
    )


def test_render_pins_row_count_conditions_and_isolated_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations(document)

    assert validate_extract_document(
        document, expected_source_id=document["source_id"]
    ) == []
    assert len(rows) == 4
    assert set(rows) == OBSERVATION_IDS
    assert document["extraction"]["table_inventory"]["printed_table_count"] == 0
    assert _render_pins(document) == RENDERED_PINS
    assert rows["shornikov_yakovlev_2025_mixing_enthalpy"]["values"]["uncertainty_kJ_mol_as_published"] == "0.20"
    assert rows["shornikov_yakovlev_2025_mixing_entropy"]["values"]["uncertainty_J_mol_K_as_published"] == "0.10"
    assert rows["shornikov_yakovlev_2025_mixing_enthalpy"]["values"][
        "mixing_enthalpy_kJ_mol_as_published"
    ] == "-34.90"
    assert rows["shornikov_yakovlev_2025_mixing_entropy"]["values"][
        "mixing_entropy_J_mol_K_as_published"
    ] == "6.60"
    assert rows["shornikov_yakovlev_2025_mixing_enthalpy"]["locator"]["published_page"] == 266
    assert rows["shornikov_yakovlev_2025_mixing_entropy"]["locator"]["published_page"] == 266
    assert rows["shornikov_yakovlev_2025_mixing_enthalpy"]["T_range_K"] == ["1762", "2269"]
    assert rows["shornikov_yakovlev_2025_mixing_entropy"]["T_range_K"] == ["1762", "2269"]

    context = document["species"]["Ca2SiO4"]["context"]
    preparation = next(row for row in context if row["observation_id"].endswith("sample_preparation"))
    assert preparation["values"]["firing_temperature_range_K_as_printed"] == ["1773", "1823"]
    assert next(row for row in context if row["observation_id"].endswith("barin_comparison_context"))["values"][
        "difference_from_Barin_1995_kJ_mol_as_printed"
    ] == "approximately 1.5"
    assert next(row for row in context if row["observation_id"].endswith("prior_precision_context"))["values"][
        "high_temperature_mixing_energy_measurement_error_up_to_kJ_mol_as_printed"
    ] == "4"

    mutated = deepcopy(document)
    _observations(mutated)["shornikov_yakovlev_2025_mixing_enthalpy"]["values"][
        "value_as_printed"
    ] = "−34.9 ± 0.20 kJ/mol"
    assert _render_pins(mutated) != RENDERED_PINS

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == document["source_id"]
    }
    kems_ids = {
        "shornikov_yakovlev_2025_mixing_enthalpy",
        "shornikov_yakovlev_2025_mixing_entropy",
    }

    assert result.validation is not None
    assert set(migrated) == OBSERVATION_IDS
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 4
    context_ids = {
        row["context_id"].rsplit("::", 1)[-1]
        for context_rows in result.context_by_work.values()
        for row in context_rows
        if row["source_id"] == document["source_id"]
    }
    assert context_ids == {
        "shornikov_yakovlev_2025_sample_preparation",
        "shornikov_yakovlev_2025_prior_precision_context",
        "shornikov_yakovlev_2025_barin_comparison_context",
    }
    assert migrated["shornikov_yakovlev_2025_mixing_enthalpy"].locator.page == 3
    assert migrated["shornikov_yakovlev_2025_mixing_enthalpy"].locator.pdf_page_index == 2
    assert migrated["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"].locator.page == 1
    assert migrated["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"].locator.pdf_page_index == 0
    unknown_quantity_ids = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.value is None
    }
    assert unknown_quantity_ids == OBSERVATION_IDS
    assert all(migrated[observation_id].identity.species.phase.is_unknown for observation_id in kems_ids)
    assert all(migrated[observation_id].identity.species.phase.is_value for observation_id in OBSERVATION_IDS - kems_ids)
    for observation_id in kems_ids:
        row = migrated[observation_id]
        assert row.evidence.class_.is_value
        assert row.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert row.value.kind is ValueKind.UNAVAILABLE
        assert "calculation equation" in row.admission.reason
        assert "numerical pressure inputs" in row.admission.reason
        assert row.derivation is not None
        assert row.derivation.method is not None
        assert row.derivation.method.locator is not None
        assert row.derivation.method.locator.published_page == 267

    expected_hard_issue_details = {
        "derivation.raw_inputs": "raw_inputs declaration is invalid for model_derived without a located not_printed declaration, method, and named references",
        "derivation.method": "model_derived raw_inputs method requires a nonempty value and an equation and physical page locator",
        "derivation.references": "model_derived raw_inputs method requires named compiled references",
        "derived_from": "derived observation requires derived_from",
    }
    for observation_id in kems_ids:
        row_issues = [
            issue
            for issue in result.validation.hard_issues
            if f"::{observation_id}]" in issue.path
        ]
        assert len(row_issues) == 4
        assert {
            (issue.path.rsplit("].", 1)[-1], issue.detail) for issue in row_issues
        } == set(expected_hard_issue_details.items())

    for observation_id, row in migrated.items():
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator is not None
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.is_unknown
        source_row = rows[observation_id]
        for condition in ("total_pressure_Pa", "fO2_Pa"):
            assert source_row["point_conditions"][condition]["state"] == {
                "tag": "unknown",
                "reason": "not_published",
            }

    print(
        "isolated migration:",
        f"obs={len(migrated)} hard={len(result.validation.hard_issues)} (missing printed derivation inputs/references)",
        f"unavailable={sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values())}",
        f"context={len(context_ids)}",
    )
    for observation_id in sorted(migrated)[:3]:
        row = migrated[observation_id]
        print(
            "identity axes",
            observation_id,
            f"composition={row.identity.composition.value}",
            f"total_pressure_Pa={row.identity.total_pressure_Pa or 'unknown'}",
            f"fO2_Pa={row.identity.fO2_Pa or 'unknown'}",
            f"temperature_K={row.identity.temperature_K}",
        )
