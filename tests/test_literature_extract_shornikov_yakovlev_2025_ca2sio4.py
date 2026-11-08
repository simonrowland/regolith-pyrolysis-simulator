from copy import deepcopy
from decimal import Decimal
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
RESULT_CONTEXT_IDS = {
    "shornikov_yakovlev_2025_mixing_enthalpy",
    "shornikov_yakovlev_2025_mixing_entropy",
}
RENDERED_PINS = (
    "−34.90 ± 0.20 kJ/mol",
    "6.60 ± 0.10 J/(mol K)",
    ["−54", "−34"],
    ["−2.4", "3.3"],
)


def _rows_by_id(document: dict) -> dict[str, dict]:
    species = document["species"]["Ca2SiO4"]
    return {
        row["observation_id"]: row
        for row in (*species["observations"], *species["context"])
    }


def _render_pins(document: dict) -> tuple[object, ...]:
    rows = _rows_by_id(document)
    return (
        rows["shornikov_yakovlev_2025_mixing_enthalpy"]["values"]["value_as_printed"],
        rows["shornikov_yakovlev_2025_mixing_entropy"]["values"]["value_as_printed"],
        rows["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"]["values"]["range_kJ_mol_as_published"],
        rows["shornikov_yakovlev_2025_prior_mixing_entropy_range"]["values"]["range_J_mol_K_as_published"],
    )


def test_render_pins_row_count_conditions_and_isolated_migration():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _rows_by_id(document)
    species = document["species"]["Ca2SiO4"]
    scored_ids = {row["observation_id"] for row in species["observations"]}
    context_ids_in_source = {row["observation_id"] for row in species["context"]}

    assert validate_extract_document(
        document, expected_source_id=document["source_id"]
    ) == []
    assert scored_ids == OBSERVATION_IDS - RESULT_CONTEXT_IDS
    assert context_ids_in_source == RESULT_CONTEXT_IDS | {
        "shornikov_yakovlev_2025_sample_preparation",
        "shornikov_yakovlev_2025_prior_precision_context",
        "shornikov_yakovlev_2025_barin_comparison_context",
    }
    assert set(rows) == OBSERVATION_IDS | context_ids_in_source
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
    _rows_by_id(mutated)["shornikov_yakovlev_2025_mixing_enthalpy"]["values"][
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
    assert result.validation is not None
    assert result.validation.hard_issues == (), result.validation.hard_issues
    assert result.registry_issues == [], result.registry_issues
    assert set(migrated) == OBSERVATION_IDS - RESULT_CONTEXT_IDS
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 2
    context_ids = {
        row["context_id"].rsplit("::", 1)[-1]
        for context_rows in result.context_by_work.values()
        for row in context_rows
        if row["source_id"] == document["source_id"]
    }
    assert context_ids == {
        *RESULT_CONTEXT_IDS,
        "shornikov_yakovlev_2025_sample_preparation",
        "shornikov_yakovlev_2025_prior_precision_context",
        "shornikov_yakovlev_2025_barin_comparison_context",
    }
    assert migrated["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"].locator.page == 1
    assert migrated["shornikov_yakovlev_2025_prior_mixing_enthalpy_range"].locator.pdf_page_index == 0
    unknown_quantity_ids = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.value is None
    }
    assert unknown_quantity_ids == OBSERVATION_IDS - RESULT_CONTEXT_IDS
    assert all(rows[observation_id]["phase"] == "unknown" for observation_id in OBSERVATION_IDS)
    assert all(
        migrated[observation_id].identity.species.phase.is_unknown
        for observation_id in OBSERVATION_IDS - RESULT_CONTEXT_IDS
    )
    context_by_id = {
        row["context_id"].rsplit("::", 1)[-1]: row
        for context_rows in result.context_by_work.values()
        for row in context_rows
        if row["source_id"] == document["source_id"]
    }
    uncertainty_pins = {
        "shornikov_yakovlev_2025_mixing_enthalpy": (Decimal("0.20"), "kJ/mol"),
        "shornikov_yakovlev_2025_mixing_entropy": (Decimal("0.10"), "J/(mol K)"),
    }
    for observation_id in RESULT_CONTEXT_IDS:
        row = context_by_id[observation_id]
        assert row["type"] == "thermodynamic_example"
        assert row["values"]["method_class"] == EvidenceClass.MODEL_DERIVED.value
        assert row["values"]["admission_status"] == "typed_refusal"
        assert "calculation equation" in row["values"]["reason"]
        assert "numerical pressure inputs" in row["values"]["reason"]
        assert row["locator"]["published_page"] == 266
        assert row["derivation"]["method"]["locator"]["published_page"] == 267
        source_row = rows[observation_id]
        assert row["uncertainty"] == source_row["uncertainty"]
        expected_width, expected_unit = uncertainty_pins[observation_id]
        assert Decimal(row["uncertainty"]["value"]) == expected_width
        assert row["uncertainty"]["unit"] == expected_unit

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
        f"obs={len(migrated)} hard={len(result.validation.hard_issues)} registry={len(result.registry_issues)}",
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
