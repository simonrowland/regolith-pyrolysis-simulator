from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/tyurnina-2023-sro-al2o3-sio2-vaporization.yaml"


def test_table_3_points_migrate_with_species_composition_and_temperature():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={document["source_id"]: document["source"]}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 52
    by_id = {o.observation_id.split("::", 1)[1]: o for o in observations}
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c05"].value.point == Decimal("0.0047")
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c08"].value.point == Decimal("0.000003")
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c10"].value.point == Decimal("0.30")

    expected_species = {"Sr": "Sr", "SiO": "SiO", "O": "O", "SrO": "SrO", "Al2O3": "Al2O3", "SiO2": "SiO2"}
    for species, container in document["species"].items():
        for item in container.get("observations", []):
            observation = by_id[item["observation_id"]]
            assert observation.value.kind.value == "point"
            assert observation.identity.species.formula == expected_species[species]
            assert observation.identity.temperature_K.value == Decimal("2000")
            assert observation.point_conditions["composition"].state.is_value
