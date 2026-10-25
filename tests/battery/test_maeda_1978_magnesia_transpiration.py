from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract

ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/maeda-1978-magnesia-transpiration-o2-h2o.yaml"
SOURCE_ID = "maeda-1978-magnesia-transpiration-o2-h2o"


def _load_extract() -> dict[str, object]:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _run_row(document: dict[str, object], species: str, run: int) -> dict[str, object]:
    species_data = document["species"][species]
    return next(
        row
        for row in species_data["observations"]
        if row.get("run_number") == run
    )


def _assert_rendered_pins(document: dict[str, object]) -> None:
    pins = (
        ("Mg(OH)2", 1, "1948", "2.58e-6", "8.26×10-2 atm", "1.79×10-5", 6, "2", "1"),
        ("Mg(OH)2", 20, "1725", "4.81e-6", "1.85×10-1 atm", "2.29×10-5", 6, "2", "20"),
        ("MgO", 1, "1977", "3.33e-7", None, "5.61×10-6", 8, "4", "1"),
        ("MgO", 3, "1898", "4.62e-8", None, "3.37×10-6", 8, "4", "3"),
    )
    for species, run, temperature, pressure, water_pressure, collected_mg, page, table, row in pins:
        observation = _run_row(document, species, run)
        values = observation["values"]
        locator = observation["locator"]
        assert values["T_K"] == temperature
        assert Decimal(str(values["pressure_atm"])) == Decimal(pressure), (
            f"render pin failed for {species} run {run}, page {page}"
        )
        assert values["collected_Mg_g_as_printed"] == collected_mg
        if water_pressure is not None:
            assert values["water_vapor_pressure_atm_as_printed"] == water_pressure
        assert locator["page"] == page
        assert locator["table"] == table
        assert locator["row"] == row


def test_maeda_run_counts_and_rendered_values() -> None:
    document = _load_extract()
    assert len(document["species"]["Mg(OH)2"]["observations"]) == 24
    assert len(document["species"]["MgO"]["observations"]) == 10
    assert len(document["species"]["Mg"]["observations"]) == 34
    _assert_rendered_pins(document)


def test_maeda_render_pins_reject_a_mutated_pressure() -> None:
    document = deepcopy(_load_extract())
    row = _run_row(document, "Mg(OH)2", 20)
    row["values"]["pressure_atm"] = "4.82e-6"
    try:
        _assert_rendered_pins(document)
    except AssertionError as error:
        assert "render pin failed" in str(error)
    else:
        raise AssertionError("render pin accepted a mutated Table 2 pressure")


def test_maeda_conditions_and_lineage_reach_isolated_migration() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    prefix = f"{SOURCE_ID}::"
    observations = {
        observation_id: observation
        for observation_id, observation in migrator.result.observations.items()
        if observation_id.startswith(prefix)
    }
    assert len(observations) == 68
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    unavailable = [
        observation
        for observation in observations.values()
        if observation.value.kind is ValueKind.UNAVAILABLE
    ]
    assert len(unavailable) == 34

    pressure_rows = [
        observation
        for observation in observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert len(pressure_rows) == 34
    for observation in pressure_rows:
        identity = observation.identity
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        point = observation.point_conditions or {}
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        assert identity.total_pressure_Pa is not None and identity.total_pressure_Pa.is_value
        assert identity.composition is not None and identity.composition.is_unknown
        for key in ("temperature_K", "total_pressure_atm", "total_pressure_Pa", "composition", "gas_mix", "flow_rate_cm3_s"):
            assert key in point and point[key].locator is not None
        assert point["temperature_K"].state.is_value
        assert point["total_pressure_atm"].state.value in {Decimal("1"), Decimal("1.0")}

        source = observation.observation_id.split("::", 1)[1]
        assert observation.derivation is not None
        assert len(observation.derivation.inputs) == 1
        parent_id = observation.derivation.inputs[0]
        parent = observations[parent_id]
        assert parent.value.kind is ValueKind.UNAVAILABLE
        assert parent.observation_id in observation.derivation.inputs

        if "p006_t02" in source:
            assert identity.fO2_Pa is not None and identity.fO2_Pa.is_unknown
            assert point["fO2_Pa"].state.is_unknown
            assert point["water_vapor_pressure_atm"].state.is_value
            assert point["gas_mix"].state.value == "O2-H2O"
        else:
            assert point["gas_mix"].state.value in {"oxygen", "oxygen-carbon dioxide"}
            if "run03_" in parent.observation_id or "run04_" in parent.observation_id or "run06_" in parent.observation_id:
                assert identity.fO2_Pa is not None and identity.fO2_Pa.is_unknown
                assert point["fO2_Pa"].state.is_unknown
            else:
                assert identity.fO2_Pa is not None and identity.fO2_Pa.is_value
                assert identity.fO2_Pa.value == Decimal("101325.0")

    extract = _load_extract()
    for row in extract["species"]["Mg(OH)2"]["observations"]:
        assert "oxygen_partial_pressure" not in row["values"]
        assert row["point_conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"
    for run in (3, 4, 6):
        row = _run_row(extract, "MgO", run)
        assert row["values"]["gas_mixture_as_printed"] == "O2+CO2"
        assert "oxygen_partial_pressure" not in row["values"]
        assert row["point_conditions"]["fO2_Pa"]["state"]["tag"] == "unknown"
