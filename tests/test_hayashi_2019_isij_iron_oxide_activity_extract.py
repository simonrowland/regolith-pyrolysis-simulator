"""Render-grid and isolated-migration checks for Hayashi et al. (2019)."""

from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, EvidenceClass
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
SID = "hayashi-2019-isij-iron-oxide-activity-na2o-sio2-feo-fe2o3"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
RENDER_GRID = ROOT / f"tests/fixtures/literature_extracts/{SID}-render-grid.csv"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _raw_id(observation) -> str:
    return observation.observation_id.split("::", 1)[1].split("::", 1)[0]


def _by_id(document: dict, observation_id: str) -> tuple[dict, dict]:
    for container in document["species"].values():
        for record in container.get("observations", ()):
            if record["observation_id"] == observation_id:
                return record, record["values"]
    raise AssertionError(f"missing observation {observation_id}")


def _assert_render_values(document: dict, grid: list[dict[str, str]]) -> None:
    assert len(grid) == 9, "Table 2 contains nine sample rows"
    assert [row["sample"] for row in grid] == ["A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2", "E1"]
    for row in grid:
        sample = row["sample"]
        _, fe2 = _by_id(document, f"hayashi_2019_t2_{sample}_fe2_fraction")
        _, fe3_oct = _by_id(document, f"hayashi_2019_t2_{sample}_fe3_oct_fraction")
        _, fe3_tetr = _by_id(document, f"hayashi_2019_t2_{sample}_fe3_tetr_fraction")
        _, cfe = _by_id(document, f"hayashi_2019_t2_{sample}_cfe_pt")
        assert fe2["points"][0]["value"] == row["Fe2+/Fetotal_pct"]
        assert fe3_oct["points"][0]["value"] == row["Fe3+(oct)/Fetotal_pct"]
        assert fe3_tetr["points"][0]["value"] == row["Fe3+(tetr)/Fetotal_pct"]
        assert cfe["points"][0]["value"] == row["CFe(Pt)_mass_pct"]
        for observation_suffix, field, grid_field in (
            ("activity_feo", "activity", "aFeO"),
            ("activity_feo15", "activity", "aFeO1.5"),
            ("gamma_feo", "gamma", "gammaFeO"),
            ("gamma_feo15", "gamma", "gammaFeO1.5"),
        ):
            _, values = _by_id(document, f"hayashi_2019_t2_{sample}_{observation_suffix}")
            point = values["points"][0]
            assert point[field] == row[grid_field]
            assert point["composition_as_published"] == {
                "basis": "mol pct",
                "components": {
                    "FeO": row["FeO_mol_pct"],
                    "FeO1.5": row["FeO1.5_mol_pct"],
                    "Na2O": row["Na2O_mol_pct"],
                    "SiO2": row["SiO2_mol_pct"],
                },
            }


def test_render_grid_pins_all_rows_and_mutated_activity_fails():
    document = _document()
    grid = _grid()
    assert validate_extract_document(document, path=EXTRACT, expected_source_id=SID) == []
    _assert_render_values(document, grid)

    assert document["species"]["FeO"]["observations"]
    assert document["species"]["FeO1.5"]["observations"]
    assert len(document["species"]["Fe"]["observations"]) == 36
    feo_activity, _ = _by_id(document, "hayashi_2019_t2_A1_activity_feo")
    feo15_activity, _ = _by_id(document, "hayashi_2019_t2_A1_activity_feo15")
    feo15_reference = feo15_activity["standard_state"]["state"]["value"]["endmember"]
    assert feo_activity["standard_state"]["state"]["value"]["endmember"]["phase"]["value"] == "l"
    assert feo15_reference["phase"]["value"] == "cr"
    assert feo15_reference["polymorph"]["tag"] == "unknown"

    changed = deepcopy(document)
    _, changed_values = _by_id(changed, "hayashi_2019_t2_A1_activity_feo")
    changed_values["points"][0]["activity"] = "0.999"
    with pytest.raises(AssertionError):
        _assert_render_values(changed, grid)


def test_isolated_migration_binds_conditions_and_keeps_unprinted_pressure_unknown():
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 72
    assert result.validation is not None
    assert result.validation.hard_issues == ()

    raw_records = {
        record["observation_id"]: record
        for container in document["species"].values()
        for record in container.get("observations", ())
    }
    migrated = {_raw_id(obs): obs for obs in result.observations.values()}
    assert set(migrated) == set(raw_records)

    for raw_id, obs in migrated.items():
        source = raw_records[raw_id]
        point = source["values"].get("points", [{}])[0]
        conditions = point["point_conditions"]
        assert {"temperature_K", "composition", "fO2_Pa", "fO2_control", "total_pressure_Pa"} <= set(conditions)
        assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
        assert obs.identity.total_pressure_Pa is not None and not obs.identity.total_pressure_Pa.is_value
        assert obs.identity.composition is not None
        if raw_id.endswith("_cfe_pt"):
            assert not obs.identity.composition.is_value
        else:
            assert obs.identity.composition.is_value
            assert obs.identity.composition.value.amount_basis is AmountBasis.MOL_INVENTORY

        if raw_id.endswith(("_activity_feo", "_activity_feo15", "_gamma_feo", "_gamma_feo15", "_cfe_pt")):
            assert obs.identity.temperature_K is not None and obs.identity.temperature_K.is_value
            assert obs.identity.temperature_K.value == Decimal("1573")
        else:
            assert obs.identity.temperature_K is not None and obs.identity.temperature_K.is_unknown
        if not raw_id.endswith("_cfe_pt"):
            assert obs.identity.fO2_Pa is not None and obs.identity.fO2_Pa.is_value

    unavailable = [obs for obs in result.observations.values() if obs.admission.status.value != "admitted"]
    assert len(unavailable) == 18
    assert all(obs.admission.status.value == "rejected" for obs in unavailable)
    assert all("coordination" in obs.admission.reason for obs in unavailable)

    for row in _grid():
        raw_id = f"hayashi_2019_t2_{row['sample']}_fe2_fraction"
        source, values = _by_id(document, raw_id)
        assert source["units"] == "%"
        assert source["units_note"] == "Source wording: % of total Fe as printed"
        assert migrated[raw_id].value.point == Decimal(row["Fe2+/Fetotal_pct"]) / 100

    for raw_id, obs in migrated.items():
        source = raw_records[raw_id]
        conditions = source["values"].get("points", [{}])[0]["point_conditions"]
        assert obs.point_conditions is not None
        for condition_name, condition in conditions.items():
            assert condition["state"]["tag"] in {"value", "unknown"}
            assert condition["locator"]
        for condition_name in ("temperature_K", "composition"):
            located = obs.point_conditions[condition_name]
            assert located.state.is_value or located.state.is_unknown
            assert located.locator is not None

    for raw_id in migrated:
        if raw_id.endswith(("_activity_feo", "_activity_feo15", "_gamma_feo", "_gamma_feo15")):
            assert migrated[raw_id].identity.temperature_K.is_value
            assert migrated[raw_id].identity.temperature_K.value == Decimal("1573")

    gamma_ids = [raw_id for raw_id in migrated if raw_id.endswith(("_gamma_feo", "_gamma_feo15"))]
    assert len(gamma_ids) == 18
    for raw_id in gamma_ids:
        oxide = "FeO" if raw_id.endswith("_gamma_feo") else "FeO1.5"
        activity_id = raw_id.removesuffix("gamma_feo" if oxide == "FeO" else "gamma_feo15") + (
            "activity_feo" if oxide == "FeO" else "activity_feo15"
        )
        source, values = _by_id(document, raw_id)
        point = values["points"][0]
        obs = migrated[raw_id]
        assert source["values"]["method_class"] == "model_derived"
        assert source["values"]["evidence_class"] == "model_derived"
        assert obs.evidence.class_.is_value
        assert obs.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        assert len(obs.derived_from) == 1
        assert obs.derived_from[0].startswith(f"{SID}::{activity_id}::")
        parameters = dict(obs.derivation.parameters)
        mole_fraction = parameters[f"x({oxide})"]
        assert mole_fraction.state.is_value
        assert mole_fraction.state.value == (
            Decimal(point["composition_as_published"]["components"][oxide]) / 100
        )
        assert mole_fraction.locator is not None
        assert mole_fraction.locator.record == point["row_as_published"]

    for raw_id in (
        "hayashi_2019_t2_A1_activity_feo",
        "hayashi_2019_t2_A1_fe2_fraction",
        "hayashi_2019_t2_E1_gamma_feo15",
    ):
        identity = migrated[raw_id].identity
        print(
            raw_id,
            "composition=", identity.composition,
            "total_pressure_Pa=", identity.total_pressure_Pa,
            "fO2_Pa=", identity.fO2_Pa,
            "temperature_K=", identity.temperature_K,
        )

    for row in _grid():
        raw_id = f"hayashi_2019_t2_{row['sample']}_fe2_fraction"
        _, values = _by_id(document, raw_id)
        point = values["points"][0]
        assert point["oxygen_partial_pressure"]["value"] == row["P_O2_atm"]
        expected_identity_pressure = Decimal(point["point_conditions"]["fO2_Pa"]["state"]["value"])
        assert migrated[raw_id].identity.fO2_Pa.value == expected_identity_pressure
        gas = point["point_conditions"]["fO2_control"]["state"]["value"]
        assert "gas ratio not stated" in gas
        if row["P_O2_atm"] == "1.3e-6":
            assert gas.startswith("Ar-O2")
            assert "gas_flow_rate_as_published" not in point
        else:
            assert gas.startswith("CO-CO2")
            assert point["gas_flow_rate_as_published"] == "100 ml/min"
