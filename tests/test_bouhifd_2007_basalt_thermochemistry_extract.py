"""Table 6 source pins and isolated migration for Bouhifd et al. (2007)."""

from pathlib import Path
from decimal import Decimal

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/bouhifd-2007-basalt-thermochemistry.yaml"


def test_table6_printed_values_are_retained() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        observation["observation_id"]: (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    }
    assert len(observations) == 16
    assert {
        observation_id: observation[1]["values"]["delta_H_fus_kJ_mol-1"]
        for observation_id, observation in observations.items()
        if observation[1]["values"].get("quantity") == "heat_of_fusion"
    } == {
        "bouhifd_2007_table6_albite_heat_of_fusion": "64.5",
        "bouhifd_2007_table6_albite_heat_of_fusion_at_1430k": "64.8",
        "bouhifd_2007_table6_orthoclase_heat_of_fusion": "54.0",
        "bouhifd_2007_table6_orthoclase_heat_of_fusion_at_1430k": "53.6",
        "bouhifd_2007_table6_anorthite_heat_of_fusion": "133.0",
        "bouhifd_2007_table6_anorthite_heat_of_fusion_at_1430k": "123.7",
        "bouhifd_2007_table6_diopside_heat_of_fusion": "82.5",
        "bouhifd_2007_table6_diopside_heat_of_fusion_at_1430k": "78.4",
        "bouhifd_2007_table6_forsterite_heat_of_fusion": "142.0",
        "bouhifd_2007_table6_forsterite_heat_of_fusion_at_1430k": "128.3",
        "bouhifd_2007_table6_fayalite_heat_of_fusion": "89.3",
        "bouhifd_2007_table6_fayalite_heat_of_fusion_at_1430k": "88.8",
        "bouhifd_2007_table6_ilmenite_heat_of_fusion": "21.7",
        "bouhifd_2007_table6_ilmenite_heat_of_fusion_at_1430k": "17.9",
    }


def test_isolated_migration_preserves_values_species_and_temperature() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    observations = list(result.observations.values())
    assert len(observations) == 16
    assert not result.validation.hard_issues
    assert not result.registry_issues
    by_source_id = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations
    }
    expected = {
        "bouhifd_2007_table6_albite_heat_of_fusion": ("NaAlSi3O8", "64.5", "1393"),
        "bouhifd_2007_table6_albite_heat_of_fusion_at_1430k": ("NaAlSi3O8", "64.8", "1430"),
        "bouhifd_2007_table6_orthoclase_heat_of_fusion": ("KAlSi3O8", "54.0", "1500"),
        "bouhifd_2007_table6_orthoclase_heat_of_fusion_at_1430k": ("KAlSi3O8", "53.6", "1430"),
        "bouhifd_2007_table6_anorthite_heat_of_fusion": ("CaAl2Si2O8", "133.0", "1830"),
        "bouhifd_2007_table6_anorthite_heat_of_fusion_at_1430k": ("CaAl2Si2O8", "123.7", "1430"),
        "bouhifd_2007_table6_diopside_heat_of_fusion": ("CaMgSi2O6", "82.5", "1670"),
        "bouhifd_2007_table6_diopside_heat_of_fusion_at_1430k": ("CaMgSi2O6", "78.4", "1430"),
        "bouhifd_2007_table6_forsterite_heat_of_fusion": ("Mg2SiO4", "142.0", "2174"),
        "bouhifd_2007_table6_forsterite_heat_of_fusion_at_1430k": ("Mg2SiO4", "128.3", "1430"),
        "bouhifd_2007_table6_fayalite_heat_of_fusion": ("Fe2SiO4", "89.3", "1490"),
        "bouhifd_2007_table6_fayalite_heat_of_fusion_at_1430k": ("Fe2SiO4", "88.8", "1430"),
        "bouhifd_2007_table6_ilmenite_heat_of_fusion": ("FeTiO3", "21.7", "1640"),
        "bouhifd_2007_table6_ilmenite_heat_of_fusion_at_1430k": ("FeTiO3", "17.9", "1430"),
    }
    for source_id, (formula, value, temperature) in expected.items():
        record = by_source_id[source_id]
        assert record.identity.quantity.is_value
        assert record.identity.quantity.value is Quantity.ENTHALPY_OF_FUSION
        assert record.identity.species.formula == formula
        assert record.evidence.class_.is_value
        assert record.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert record.value.kind is ValueKind.POINT
        assert str(record.value.point) == value
        assert record.identity.temperature_K.is_value
        assert str(record.identity.temperature_K.value) == temperature
        condition = record.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert str(condition.state.value) == temperature
        assert condition.locator is not None

    for record in observations:
        assert record.point_conditions["temperature_K"].state.is_value
    for suffix, temperature in (
        ("bouhifd_2007_sfb_liquidus_temperature_dta", "1430"),
        ("bouhifd_2007_sfb_spinel_first_liquidus_phase", "1570"),
    ):
        record = by_source_id[suffix]
        assert record.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        assert record.identity.species.formula == "unknown"
        assert record.value.kind is ValueKind.POINT
        assert record.value.point == Decimal(temperature)
        condition = record.point_conditions["temperature_K"]
        assert str(condition.state.value) == temperature
    spinel = by_source_id["bouhifd_2007_sfb_spinel_first_liquidus_phase"]
    assert spinel.point_conditions["atmosphere"].state.value == "air"
    assert spinel.point_conditions["atmosphere"].locator is not None

    table1 = document["species"]["unknown"]["context"][0]["values"]
    assert table1["sfb_glass_density_g_cm3"] == "2.862"
    assert table1["sfb_glass_Fe2_over_total_Fe"] == "0.43"
