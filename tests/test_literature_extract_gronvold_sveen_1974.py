from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import migrate as battery_migrate
from tools.validate_literature_extracts import check_all_fidelity_samples_match

REPO_ROOT = Path(__file__).resolve().parents[1]
SID = "gronvold-sveen-1974-magnetite-high-temperature-cp"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document):
    return {
        observation["observation_id"]: observation
        for observation in document["species"]["Fe3O4"]["observations"]
    }


def test_printed_rows_fidelity_anchors_and_mutation_are_pinned():
    document = _document()
    observations = _observations(document)
    table1 = [o for oid, o in observations.items() if "table1_" in oid]
    assert sum(len(o["values"]["points"]) for o in table1) == 100
    assert sum(len(observations[f"gronvold_sveen_1974_table2_{q}"]["values"]["points"]) for q in ("cp", "H_minus_H298", "S")) == 114
    assert sum(len(observations[f"gronvold_sveen_1974_table4_{q}"]["values"]["points"]) for q in ("log10_pH2O_over_pH2", "negative_reaction_gibbs_energy", "gibbs_function_G_T_minus_H_0", "negative_formation_enthalpy")) == 68
    assert check_all_fidelity_samples_match(document) == []

    mutated = deepcopy(document)
    _observations(mutated)["gronvold_sveen_1974_table1_i"]["values"]["points"][0]["value"] = "159.12"
    assert check_all_fidelity_samples_match(mutated)


def test_isolated_migration_has_only_the_ruled_schema_gaps_and_keeps_temperature():
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert sum(map(len, result.context_by_work.values())) == 5
    rows = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == SID
    }
    assert len(rows) == 282
    expected_unknown = {
        "gronvold_sveen_1974_table4_log10_pH2O_over_pH2",
        "gronvold_sveen_1974_table4_negative_reaction_gibbs_energy",
        "gronvold_sveen_1974_table4_gibbs_function_G_T_minus_H_0",
        "gronvold_sveen_1974_table4_negative_formation_enthalpy",
    }
    unknown_observation_ids = {
        expected
        for expected in expected_unknown
        if sum(expected in row.observation_id for row in rows.values()) == 17
    }
    assert unknown_observation_ids == expected_unknown
    assert sum(row.identity.quantity.is_unknown for row in rows.values()) == 68
    assert sum(row.value.kind.value == "unavailable" for row in rows.values()) == 68
    assert all(row.identity.species.phase.is_value for row in rows.values())
    assert all(row.identity.species.phase.value.value == "cr" for row in rows.values())
    for observation_id, row in rows.items():
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator.page in {4, 6, 13}
    first = next(row for row in rows.values() if "table1_i::T=329.64" in row.observation_id)
    assert first.identity.temperature_K.value == Decimal("329.64")
