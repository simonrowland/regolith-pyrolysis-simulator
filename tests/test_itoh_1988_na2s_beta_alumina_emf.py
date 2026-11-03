from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery import factories

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/itoh-1988-na2s-beta-alumina-emf.yaml"

# Independently pinned from the PDF page renders, not from the draft CSV.
RENDER_PINS = {
    "itoh_1988_t1_run1_no01_emf": ("1.4785", "I", "Run 1 No. 1", 3),
    "itoh_1988_t1_run1_no01_exchange_gibbs": ("-142650", "I", "Run 1 No. 1", 4),
    "itoh_1988_t1_run1_no01_na2s_delta_fg": ("-349570", "I", "Run 1 No. 1", 5),
    "itoh_1988_t1_run1_no29_emf": ("1.4299", "I", "Run 1 No. 29", 3),
    "itoh_1988_t1_run1_no29_na2s_delta_fg": ("-335920", "I", "Run 1 No. 29", 5),
    "itoh_1988_t1_run2_no19_emf": ("1.5299", "I", "Run 2 No. 19", 3),
    "itoh_1988_t1_run2_no19_exchange_gibbs": ("-147610", "I", "Run 2 No. 19", 4),
    "itoh_1988_t1_run2_no19_na2s_delta_fg": ("-364040", "I", "Run 2 No. 19", 5),
    "itoh_1988_t2_500_s2_g": ("12736", "II", "500 K", 2),
    "itoh_1988_t2_500_na2s_delta_fg": ("-369037", "II", "500 K", 7),
    "itoh_1988_t2_700_ag2s_delta_fg": ("-61429", "II", "700 K", 8),
}

EQ_PINS = {
    "itoh_1988_eq3_emf_fit": "E/V(±0.00057) = 1.75793(±0.00086) − 4.452(±0.014) × 10⁻⁴(T)",
    "itoh_1988_eq4_exchange_gibbs_fit": "ΔG (J ± 55) = −169613(±83) + 42.95(±0.13)(T)",
    "itoh_1988_eq7_na2s_delta_fg_fit": "ΔG°_Na2S (J mol−1 ± 130) = −427940(±200) + 124.92(±0.32)(T)",
    "itoh_1988_eq8_reference_na2s_delta_fg_fit": "ΔG°_Na2S (J mol−1) = −441729 + 145.44(T)",
}


def observations(doc: dict) -> dict[str, tuple[str, dict]]:
    return {
        row["observation_id"]: (species, row)
        for species, block in doc["species"].items()
        for row in block.get("observations", [])
    }


def assert_render_pins(doc: dict) -> None:
    obs = observations(doc)
    for observation_id, (expected, table, row, column) in RENDER_PINS.items():
        species, item = obs[observation_id]
        assert item["values"]["value"] == expected
        assert item["locator"]["page"] == 2
        assert item["locator"]["pdf_page_index"] == 1
        assert item["locator"]["published_page"] == 1396
        assert item["locator"]["table"] == table
        assert item["locator"]["row"] == row
        assert item["locator"]["column"] == column
        assert species in {"S2", "Na2S", "Ag2S"}
    for observation_id, expected in EQ_PINS.items():
        assert obs[observation_id][1]["values"]["fit_as_printed"] == expected


def test_itoh_1988_render_pins_rows_lineage_and_real_migration() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    obs = observations(doc)

    # Printed body counts: Table I has 55 rows (36 + 19), and Table II has 3.
    assert len(obs) == 169
    table_i = [item for item in obs.values() if item[1]["observation_id"].startswith("itoh_1988_t1_")]
    table_ii = [item for item in obs.values() if item[1]["observation_id"].startswith("itoh_1988_t2_")]
    fits = [item for item in obs.values() if "_eq" in item[1]["observation_id"]]
    assert len(table_i) == 144
    assert len(table_ii) == 21
    assert len(fits) == 4
    assert sum(item[1]["values"].get("quantity") == "electromotive_force_as_published" for item in table_i) == 55
    assert sum(item[1]["values"].get("quantity") == "exchange_reaction_gibbs_energy" for item in table_i) == 55
    assert sum(item[1]["observation_id"].endswith("_na2s_delta_fg") for item in table_i) == 34
    assert doc["scope_assessment"]["printed_table_counts"]["table_I_body_data_rows"] == 55
    assert doc["scope_assessment"]["printed_table_counts"]["table_II_body_rows"] == 3
    assert doc["scope_assessment"]["printed_table_counts"]["table_II_stored_gibbs_cells"] == 21

    assert_render_pins(doc)
    assert obs["itoh_1988_t1_run1_no01_emf"][1]["values"]["value_as_printed"] == "1.4785 V"
    assert obs["itoh_1988_t1_run1_no01_exchange_gibbs"][1]["derived_from"] == ["itoh_1988_t1_run1_no01_emf"]
    row29 = obs["itoh_1988_t1_run1_no29_na2s_delta_fg"][1]
    assert row29["values"]["T_K"] == "737.7"
    assert "itoh_1988_t1_run1_no29_na2s_delta_fg" not in obs["itoh_1988_eq7_na2s_delta_fg_fit"][1]["derived_from"]
    assert len(obs["itoh_1988_eq3_emf_fit"][1]["derived_from"]) == 33
    assert len(obs["itoh_1988_eq7_na2s_delta_fg_fit"][1]["derived_from"]) == 33
    assert len(obs["itoh_1988_eq8_reference_na2s_delta_fg_fit"][1]["derived_from"]) == 3
    eq8 = obs["itoh_1988_eq8_reference_na2s_delta_fg_fit"][1]
    assert eq8["values"]["method_class"] == "model_derived"
    assert eq8["values"]["evidence_class"] == "model_derived"
    assert eq8["derived_from"] == [
        "itoh_1988_t2_500_na2s_delta_fg",
        "itoh_1988_t2_600_na2s_delta_fg",
        "itoh_1988_t2_700_na2s_delta_fg",
    ]
    assert obs["itoh_1988_eq8_reference_na2s_delta_fg_fit"][1]["locator"]["page"] == 3
    assert obs["itoh_1988_eq8_reference_na2s_delta_fg_fit"][1]["locator"]["published_page"] == 1397

    # Mutation check: the render-backed pin must reject a changed stored cell.
    mutated = deepcopy(doc)
    mutated_obs = observations(mutated)
    mutated_obs["itoh_1988_t1_run1_no01_emf"][1]["values"]["value"] = "1.4786"
    with pytest.raises(AssertionError):
        assert_render_pins(mutated)

    migrator = factories.migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 169
    assert not result.validation.hard_issues
    assert not result.registry_issues
    assert sum(item.value.kind.value == "point" for item in result.observations.values()) == 40
    assert sum(item.value.kind.value == "unavailable" for item in result.observations.values()) == 129

    sid = "itoh-1988-na2s-beta-alumina-emf"
    expected_quantity_unknown = {
        f"{sid}::itoh_1988_t1_run{run}_no{number:02d}_{suffix}"
        for run, last in ((1, 36), (2, 19))
        for number in range(1, last + 1)
        for suffix in ("emf", "exchange_gibbs")
    }
    expected_quantity_unknown.update(
        f"{sid}::itoh_1988_t2_{temperature}_{species}_g"
        for temperature in ("500", "600", "700")
        for species in ("s2", "na", "ag", "na2s", "ag2s")
    )
    expected_quantity_unknown.update(
        {
            f"{sid}::itoh_1988_eq3_emf_fit",
            f"{sid}::itoh_1988_eq4_exchange_gibbs_fit",
        }
    )
    assert {
        item.observation_id
        for item in result.observations.values()
        if not item.identity.quantity.is_value
    } == expected_quantity_unknown
    assert not any(item.identity.species.phase.is_unknown for item in result.observations.values())

    # Run 2 and Run 1 Nos. 34–36 have changed, unquantified sulfide inventory.
    for suffix in (
        "itoh_1988_t1_run2_no01_emf",
        "itoh_1988_t1_run2_no19_emf",
        "itoh_1988_t1_run1_no34_emf",
        "itoh_1988_t1_run1_no35_emf",
        "itoh_1988_t1_run1_no36_emf",
    ):
        source_item = obs[suffix][1]
        assert source_item["point_conditions"]["composition"]["state"]["tag"] == "unknown"
        migrated_item = next(item for key, item in result.observations.items() if key.endswith(suffix))
        assert migrated_item.identity.composition is None or not migrated_item.identity.composition.is_value

    # Source conditions mark unreported pressure and fO2 unknown; the real
    # migrator drops the unprinted total-pressure condition rather than inventing it.
    for _, item in obs.values():
        source_conditions = item["point_conditions"]
        assert source_conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert source_conditions["fO2_log"]["state"]["tag"] == "unknown"
        assert "101325" not in str(source_conditions["total_pressure_Pa"])
    for item in result.observations.values():
        conditions = item.point_conditions
        assert conditions is not None
        assert {"temperature_K", "fO2_log"} <= set(conditions)
        assert "total_pressure_Pa" not in conditions
        assert conditions["temperature_K"].locator is not None
        assert conditions["fO2_log"].locator is not None
        assert conditions["fO2_log"].state.is_unknown
        assert item.identity.total_pressure_Pa is None or not item.identity.total_pressure_Pa.is_value
        assert item.identity.fO2_Pa is None or not item.identity.fO2_Pa.is_value

    # Print and pin all requested identity axes on three representative rows.
    for suffix, temperature, has_composition in (
        ("itoh_1988_t1_run1_no01_emf", "625.8", True),
        ("itoh_1988_t1_run1_no34_emf", "791.4", False),
        ("itoh_1988_t2_700_ag2s_delta_fg", "700", False),
    ):
        item = next(value for key, value in result.observations.items() if key.endswith(suffix))
        assert item.identity.temperature_K.is_value
        assert item.identity.temperature_K.value == Decimal(temperature)
        assert (item.identity.composition is not None and item.identity.composition.is_value) is has_composition
        assert item.identity.total_pressure_Pa is None or not item.identity.total_pressure_Pa.is_value
        assert item.identity.fO2_Pa is None or not item.identity.fO2_Pa.is_value

    # Identity and point conditions receive the temperature and run inventory.
    for suffix, temperature, inventory in (
        ("itoh_1988_t1_run1_no01_emf", "625.8", {"Ag": "1.00", "Ag2S": "1.00", "Na2S": "1.00"}),
        ("itoh_1988_t2_700_ag2s_delta_fg", "700", None),
    ):
        item = next(value for key, value in result.observations.items() if key.endswith(suffix))
        assert item.identity.temperature_K.is_value
        assert item.identity.temperature_K.value == Decimal(temperature)
        condition = item.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.state.value == Decimal(temperature)
        if inventory is not None:
            composition = item.identity.composition
            assert composition.is_value
            assert {name: str(amount) for name, amount in composition.value.components} == inventory

    mapped_na2s = next(value for key, value in result.observations.items() if key.endswith("itoh_1988_t1_run1_no01_na2s_delta_fg"))
    assert mapped_na2s.identity.quantity.value.value == "delta_fG"
    assert mapped_na2s.value.point == Decimal("-349.570")
    assert all(
        item.identity.species.phase.is_value
        for item in result.observations.values()
    )
