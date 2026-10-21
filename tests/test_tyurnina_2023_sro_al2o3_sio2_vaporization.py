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
    assert len(observations) == 60
    by_id = {o.observation_id.split("::", 1)[1]: o for o in observations}
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c05"].value.point == Decimal("0.0047")
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c08"].value.point == Decimal("0.000003")
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r04_c10"].value.point == Decimal("0.30")
    assert by_id["tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r05_c08"].value.point == Decimal("8.6e-6")
    pins = {
        "tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r08_c05": Decimal("9.3e-2"),
        "tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r06_c09": Decimal("2.2e-2"),
        "tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r08_c10": Decimal("1.4e-2"),
        "tyurnina_2023_sro_al2o3_sio2_vaporization_p006_t02_r10_c10": Decimal("1.3e-3"),
    }
    for observation_id, expected in pins.items():
        assert by_id[observation_id].value.point == expected
    silica = next(item for item in document["species"]["SiO2"]["observations"] if item["observation_id"].endswith("_r04_c10"))
    assert "Near-transition notice" in silica["standard_state"]["note"]
    for species, column in (("SrO", "c08"), ("Al2O3", "c09"), ("SiO2", "c10")):
        raw = next(item for item in document["species"][species]["observations"] if item["observation_id"].endswith("_r04_" + column))
        migrated = by_id[raw["observation_id"]]
        assert migrated.identity.reference_state.is_value
        assert raw["standard_state"]["inferred"] is True
    gibbs = next(item for item in document["species"]["unknown"]["context"] if item["observation_id"] == "tyurnina_2023_table3_gibbs_energy_context")
    assert gibbs["values"]["rows"][0] == {"sample": 10, "minus_delta_G": "12.9", "minus_delta_G_excess": "1.7"}
    assert len(gibbs["values"]["rows"]) == 8
    assert document["species"]["SrO"]["context"][0]["locator"]["table"] == "3"
    mullite = next(item for item in document["species"]["unknown"]["context"] if item["observation_id"] == "tyurnina_2023_mullite_enthalpy_context")
    assert mullite["values"]["formation_enthalpy_mullite"] == "−6869 ± 160 kJ/mol"
    assert "temperature" in mullite["values"]["note"]

    expected_species = {"Sr": "Sr", "SiO": "SiO", "O": "O", "SrO": "SrO", "Al2O3": "Al2O3", "SiO2": "SiO2"}
    for i, expected in enumerate((1848, 1858, 1833, 1815, 1843, 1929, 1913, 1898), 1):
        item_id = f"tyurnina_2023_sro_al2o3_sio2_vaporization_p005_t02_sample{i}_tmelt"
        assert by_id[item_id].value.point == Decimal(expected)
        assert by_id[item_id].identity.species.formula == "unknown"

    for species, container in document["species"].items():
        if species == "unknown":
            continue
        if species == "O":
            for item in container["observations"]:
                migrated = by_id[item["observation_id"]]
                assert migrated.evidence.class_.value.value == "measured_reduced"
                assert len(migrated.derived_from) == (1 if any(f"_{row}_" in item["observation_id"] for row in ("r03", "r11", "r12")) else 2)
        for item in container.get("observations", []):
            observation = by_id[item["observation_id"]]
            assert observation.value.kind.value == "point"
            assert observation.identity.species.formula == expected_species[species]
            assert observation.identity.temperature_K.value == Decimal("2000")
            assert observation.point_conditions["composition"].state.is_value
