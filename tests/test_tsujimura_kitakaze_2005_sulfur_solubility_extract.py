from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tsujimura-kitakaze-2005-sulfur-solubility-graphite-silicate-melts.yaml"
def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body.get("observations", [])
    }


def _migrate(document: dict, tmp_path: Path) -> Migrator:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "tsujimura-kitakaze-2005.yaml"
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator


def _assert_source_pins(document: dict, tmp_path: Path) -> None:
    rows = _source_observations(document)
    preparation_note = document["extraction"]["note"]
    assert "evacuated silica glass-tube method (A)" in preparation_note
    assert "sealed in a silica tube with a graphite rod" in preparation_note
    assert "final eight-hour runs instead used" in preparation_note
    silica = rows["tsujimura_kitakaze_2005_table2_sio2_glass_composition_a01_1"]
    assert silica["values"]["value"] == "70.9"
    assert silica["values"]["basis"] == "mole_percent"
    assert silica["locator"]["page"] == 3
    assert silica["locator"]["table"] == "2"
    assert silica["locator"]["row"] == "A01-1"
    assert silica["locator"]["column"] == "SiO2"

    sulfur = rows["tsujimura_kitakaze_2005_table2_s_sulfur_solubility_a01_2"]
    assert "Ar-mediated IHPV run" in sulfur["values"]["capsule_condition_as_published"]
    assert "silica tube" not in sulfur["values"]["capsule_condition_as_published"]
    assert sulfur["values"]["value"] == "910"
    assert sulfur["locator"]["page"] == 3
    assert sulfur["locator"]["table"] == "2"
    assert sulfur["locator"]["row"] == "A01-2"
    assert sulfur["locator"]["column"] == "S (ppm)"
    assert sulfur["uncertainty"]["value_uncertainty"] == "120"
    assert sulfur["uncertainty"]["units"] == "ppm"
    assert sulfur["uncertainty"]["confidence_level"] == "one_sigma"

    pressure = sulfur["values"]["total_pressure_Pa"]
    assert pressure == {"value": "1000", "units": "bar", "as_printed": "1000 bars"}
    printed_pressure = sulfur["point_conditions"]["total_pressure_bar"]
    assert printed_pressure["state"]["value"] == "1000"
    assert printed_pressure["units"] == "bar"
    assert printed_pressure["unit_as_published"] == "bars"
    assert printed_pressure["locator"]["page"] == 2
    composition = sulfur["point_conditions"]["composition"]["state"]["value"]
    assert composition["amount_basis"] == "mole_percent"
    assert composition["components"]["SiO2"] == "68.0"
    assert sulfur["point_conditions"]["composition"]["locator"]["row"] == "A01-2"

    c23 = rows["tsujimura_kitakaze_2005_table3_c23_s_k_alpha_shift"]
    assert c23["values"]["value"] == "-2.75"
    assert c23["locator"]["page"] == 4
    assert c23["locator"]["table"] == "3"
    assert c23["locator"]["row"] == "C23 (FeO = 0.1 mol%)"

    result = _migrate(document, tmp_path).result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    sulfur_910 = [
        observation
        for observation in result.observations.values()
        if observation.identity.species.formula == "S"
        and observation.value.kind is ValueKind.POINT
        and observation.value.point == Decimal("910")
    ]
    assert len(sulfur_910) == 1
    observation = sulfur_910[0]
    assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    assert observation.identity.subtype.value == "ppm_mass"
    assert observation.identity.phase_role.value is PhaseRole.GLASS
    assert observation.identity.species.phase.value is Phase.GLASS
    assert observation.identity.total_pressure_Pa.value == Decimal("100000000")
    assert observation.identity.temperature_K.value == Decimal("1523.15")
    assert observation.identity.fO2_Pa.is_unknown
    migrated_composition = observation.identity.composition.value
    assert migrated_composition.amount_basis is AmountBasis.MOLE_FRACTION
    assert dict(migrated_composition.components)["SiO2"] == Decimal("0.68")
    assert observation.point_conditions["total_pressure_bar"].state.value == Decimal("1000")
    assert observation.uncertainty.kind.value == "printed"
    assert observation.uncertainty.verbatim["value_uncertainty"] == "120"
    assert observation.uncertainty.verbatim["confidence_level"] == "one_sigma"


def test_tables_are_complete_and_migration_preserves_supported_rows_and_refusals(tmp_path):
    document = _document()
    rows = _source_observations(document)
    table2 = [row for row in rows.values() if row["locator"].get("table") == "2"]
    table3 = [row for row in rows.values() if row["locator"].get("table") == "3"]
    oxide_rows = [row for row in table2 if row["values"].get("formula") != "S"]
    sulfur_rows = [row for row in table2 if row["values"].get("formula") == "S"]
    assert len(document["species"]["S"]["context"][0]["values"]["rows"]) == 25
    assert len(table2) == 215
    assert len(oxide_rows) == 184
    assert len(sulfur_rows) == 31
    assert len(table3) == 7

    for row in table2:
        conditions = row["point_conditions"]
        assert conditions["T_C"]["state"]["value"] == "1250"
        assert conditions["total_pressure_bar"]["state"]["value"] == "1000"
        assert conditions["total_pressure_bar"]["units"] == "bar"
        assert conditions["fO2_log"]["state"]["tag"] == "unknown"
        composition = conditions["composition"]
        assert composition["state"]["value"]["amount_basis"] == "mole_percent"
        assert composition["locator"]["page"] == 3
        assert composition["locator"]["row"] == row["locator"]["row"]

    for row in oxide_rows:
        values = row["values"]
        assert values["basis"] == "mole_percent"
        assert values["admission_status"] == "typed_refusal"
        assert "PHASE_BASIS_TOKENS" in values["schema_gap"]
        assert row["uncertainty"]["confidence_level"] == "one_sigma"
        assert row["uncertainty"]["locator"]["page"] == 3

    migrator = _migrate(document, tmp_path)
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 38
    assert sum(o.value.kind is ValueKind.UNAVAILABLE for o in result.observations.values()) == 7

    oxide_ids = {row["observation_id"] for row in oxide_rows}
    basis_refusals = [
        item
        for item in result.queue
        if "basis is not" in item.why
        and item.observation_id
        and item.observation_id.rsplit("::", 1)[-1] in oxide_ids
    ]
    assert {item.observation_id.rsplit("::", 1)[-1] for item in basis_refusals} == oxide_ids
    assert len(basis_refusals) == 184

    migrated = tuple(result.observations.values())
    unknown_table3 = {
        o.observation_id.rsplit("::", 1)[-1]
        for o in migrated
        if o.locator is not None
        and o.locator.table == "3"
        and o.identity.quantity.is_unknown
    }
    assert unknown_table3 == {row["observation_id"] for row in table3}
    assert all(o.identity.species.phase.is_value for o in migrated)

    sulfur_observations = [
        o for o in migrated
        if o.locator is not None and o.locator.table == "2" and o.identity.species.formula == "S"
    ]
    assert len(sulfur_observations) == 31
    assert all(o.identity.total_pressure_Pa.value == Decimal("100000000") for o in sulfur_observations)
    composition_unknown = {
        o.observation_id.rsplit("::", 1)[-1]
        for o in sulfur_observations
        if o.identity.composition.is_unknown
    }
    assert composition_unknown == {
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_a02",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_a04",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_a05_5",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b07",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b09",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b10",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b11",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b12",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b13",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b15",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b17",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b18",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b19",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_b20",
        "tsujimura_kitakaze_2005_table2_s_sulfur_solubility_c23",
    }
    assert all(
        "mole-percent composition does not close to 100"
        in o.point_conditions["composition"].state.reason
        for o in sulfur_observations
        if o.observation_id.rsplit("::", 1)[-1] in composition_unknown
    )
    assert all(
        o.identity.composition.is_value
        for o in sulfur_observations
        if o.observation_id.rsplit("::", 1)[-1] not in composition_unknown
    )
    assert all(o.identity.fO2_Pa.is_unknown for o in sulfur_observations)
    assert all(o.uncertainty.kind.value == "printed" for o in sulfur_observations)
    assert all(o.uncertainty.verbatim["confidence_level"] == "one_sigma" for o in sulfur_observations)


def test_source_pins_reject_mutated_sulfur_and_pressure(tmp_path):
    document = _document()
    _assert_source_pins(document, tmp_path / "baseline")

    sulfur_mutation = deepcopy(document)
    sulfur = next(
        row for row in sulfur_mutation["species"]["S"]["observations"]
        if row["locator"].get("table") == "2" and row["locator"].get("row") == "A01-2"
    )
    sulfur["values"]["value"] = "911"
    with pytest.raises(AssertionError):
        _assert_source_pins(sulfur_mutation, tmp_path / "s-mutated")

    pressure_mutation = deepcopy(document)
    sulfur = next(
        row for row in pressure_mutation["species"]["S"]["observations"]
        if row["locator"].get("table") == "2" and row["locator"].get("row") == "A01-2"
    )
    sulfur["values"]["total_pressure_Pa"]["value"] = "100"
    sulfur["point_conditions"]["total_pressure_bar"]["state"]["value"] = "100"
    with pytest.raises(AssertionError):
        _assert_source_pins(pressure_mutation, tmp_path / "p-mutated")
