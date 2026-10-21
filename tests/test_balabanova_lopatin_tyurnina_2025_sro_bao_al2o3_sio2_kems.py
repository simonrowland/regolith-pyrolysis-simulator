from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/balabanova-lopatin-tyurnina-2025-sro-bao-al2o3-sio2-kems.yaml"


def test_table_2_values_migrate_as_points_with_composition_temperature_and_species():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={document["source_id"]: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 83

    by_id = {o.observation_id.split("::", 1)[1]: o for o in observations}
    pins = {
        "balabanova_lopatin_tyurnina_2025_sro_bao_al2o3_sio2_kems_p005_t02_r04_c02": Decimal("0.0069"),
        "balabanova_lopatin_tyurnina_2025_sro_bao_al2o3_sio2_kems_p005_t02_r07_c05": Decimal("1.49"),
        "balabanova_lopatin_tyurnina_2025_sro_bao_al2o3_sio2_kems_p005_t02_r13_c09": Decimal("0.25"),
    }
    for observation_id, expected in pins.items():
        assert by_id[observation_id].value.point == expected

    expected_species = {2: "Ba", 3: "BaO", 4: "Sr", 5: "SiO", 6: "O2", 7: "BaO", 8: "SrO", 9: "SiO2"}
    oxygen_rows = [raw for raw in document["species"]["O2"]["observations"]]
    assert len(oxygen_rows) == 11
    for raw in oxygen_rows:
        migrated = by_id[raw["observation_id"]]
        assert migrated.evidence.class_.value.value == "measured_reduced"
        assert 1 <= len(migrated.derived_from) <= 3
        assert all(f"_p005_t02_r{raw['observation_id'].rsplit('_r', 1)[1].split('_c', 1)[0]}_c" in parent for parent in migrated.derived_from)
    context = document["species"]["unknown"]["context"]
    cell = next(item for item in context if item["observation_id"] == "balabanova_2025_table1_unit_cell_context")
    assert cell["values"]["carbonate_oxide_route"] == {"a": "8.525", "b": "13.014", "c": "14.339", "beta": "115.08"}
    assert cell["values"]["aluminosilicate_route"] == {"a": "8.521", "b": "13.012", "c": "14.340", "beta": "115.18"}
    loss = next(item for species in document["species"].values() for item in species.get("context", []) if item["values"].get("quantity_as_printed") == "total mass loss Δm")
    assert loss["values"]["quantity_token"] == "mass_loss_rate"
    assert "token exists" in loss["values"]["schema_gap"]
    energies = next(item for item in context if item["observation_id"] == "balabanova_2025_appearance_energies_context")
    assert energies["values"]["appearance_energies"] == "Sr 5.8 ± 0.3; Ba 5.4 ± 0.3; BaO 7.0 ± 0.3; SiO 11.5 ± 0.3"
    ba_activity = by_id["balabanova_lopatin_tyurnina_2025_sro_bao_al2o3_sio2_kems_p005_t02_r04_c07"]
    assert ba_activity.identity.reference_state.tag.value == "unknown"
    assert "SrAl2Si2O8 or BaAl2Si2O8" in ba_activity.identity.reference_state.reason

    for observation in observations:
        assert observation.value.kind.value == "point"
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("2000")
        column = int(observation.observation_id.rsplit("_c", 1)[1])
        assert observation.identity.species.formula == expected_species[column]
        composition = observation.point_conditions["composition"]
        assert composition.state.is_value
        assert len(composition.state.value.components) == 4
