from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ehrt-2008-sn-redox-aluminosilicate.yaml"
RENDER_PINS = {
    ("ehrt2008_redox_fraction_air_1600_1700C", 0): ("0.20", "~20% Sn2+", "1600", "air", "AS", None),
    ("ehrt2008_redox_fraction_air_1600_1700C", 1): ("0.30", "~30% Sn2+", "1700", "air", "AS", None),
    ("ehrt2008_redox_fraction_argon_1600_1800C", 0): ("0.40", "nearly 40% Sn2+", "1600", "argon", "AS", "1000"),
    ("ehrt2008_redox_fraction_argon_1600_1800C", 1): ("0.50", "~50% Sn2+", "1800", "argon", "ABS", None),
    ("ehrt2008_redox_fraction_argon_1600_1800C", 2): ("0.80", "~80% Sn2+", "1800", "argon", "ABS", None),
    ("ehrt2008_redox_fraction_as_5000_ppmw_1600C_air", 0): ("0.20", "~20% Sn2+; ~80% Sn4+", "1600", "air", "AS", "5000"),
    ("ehrt2008_redox_fraction_abs_1900C_argon", 0): ("1.00", "only Sn2+ species could be detected", "1900", "argon", "ABS", "2000"),
    ("ehrt2008_redox_fraction_abs_pressure_reduction", 0): ("0.20", "~20% Sn2+", "1650", "air", "ABS", "2000"),
    ("ehrt2008_redox_fraction_abs_pressure_reduction", 1): ("0.40", "~40% Sn2+", "1500", "air", "ABS", "2000"),
    ("ehrt2008_redox_fraction_as_1000_ppmw_fig6", 0): ("1.00", "only Sn2+ could be detected", "1900", "argon", "AS", "1000"),
    ("ehrt2008_redox_fraction_as_1000_ppmw_fig6", 1): ("1.00", "only Sn2+ was found; a loss of Sn was noted", "2000", "argon", "AS", "1000"),
}
DENSITY_RENDER_PINS = {
    "ehrt2008_density_AS": ("2.85", "2850.00", "AS"),
    "ehrt2008_density_ABS": ("2.56", "2560.00", "ABS"),
}


def _assert_render_pins(document):
    observations = {
        observation["observation_id"]: observation
        for observation in document["species"]["Sn"]["observations"]
    }
    for (observation_id, index), expected in RENDER_PINS.items():
        point = observations[observation_id]["values"]["points"][index]
        assert (
            point["value"],
            point["value_as_published"],
            point["T_C"],
            point["point_conditions"]["atmosphere"]["state"]["value"],
            point["sample"],
            point.get("tin_ppmw"),
        ) == expected


def _assert_density_render_pins(document):
    observations = {
        observation["observation_id"]: observation
        for observation in document["species"]["Sn"]["observations"]
    }
    for observation_id, (printed, _migrated, sample) in DENSITY_RENDER_PINS.items():
        observation = observations[observation_id]
        assert observation["values"]["sample"] == sample
        assert observation["values"]["density_g_cm3"] == printed
        assert "± 0.01 g/cm3" in observation["values"]["value_as_published"]
        assert observation.get("uncertainty") == {
            "sigma": "10",
            "units": "kg/m3",
        }


def test_ehrt_redox_values_migrate_as_points_with_printed_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    _assert_density_render_pins(document)
    fig6_as_source = next(
        observation
        for observation in document["species"]["Sn"]["observations"]
        if observation["observation_id"] == "ehrt2008_redox_fraction_as_1000_ppmw_fig6"
    )
    assert fig6_as_source["values"]["points"][0]["point_conditions"]["total_pressure_Pa"]["as_printed"] == "1000 mbar"
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    observations = list(migrator.result.observations.values())
    assert len(observations) == 13
    redox_observations = [
        observation for observation in observations
        if "redox_fraction" in observation.observation_id
    ]
    assert len(redox_observations) == len(RENDER_PINS)
    assert all(observation.identity.species.formula == "Sn" for observation in redox_observations)
    assert all(observation.value.kind is ValueKind.POINT for observation in redox_observations)
    assert sorted(str(observation.value.point) for observation in redox_observations) == sorted(
        expected[0] for expected in RENDER_PINS.values()
    )
    assert all(observation.identity.temperature_K.is_value for observation in redox_observations)
    assert sorted(str(observation.identity.temperature_K.value) for observation in redox_observations) == sorted(
        ["1873.15"] * 3 + ["1973.15"] + ["2073.15"] * 2 + ["2173.15"] * 2 + ["2273.15", "1923.15", "1773.15"]
    )
    as_composition = {"Al2O3": 15, "SiO2": 65, "BaO": 10, "CaO": 10}
    abs_composition = {"Al2O3": 10, "B2O3": 8, "SiO2": 67, "BaO": 2, "CaO": 9, "MgO": 4}
    expected_composition = {
        ("1873.15", "0.20"): as_composition,
        ("1873.15", "0.40"): as_composition,
        ("1973.15", "0.30"): as_composition,
        ("2073.15", "0.50"): abs_composition,
        ("2073.15", "0.80"): abs_composition,
        ("2173.15", "1.00"): abs_composition,
        ("2273.15", "1.00"): as_composition,
        ("1923.15", "0.20"): abs_composition,
        ("1773.15", "0.40"): abs_composition,
    }
    expected_pressure = {
        key: (
            None
            if key == ("2273.15", "1.00")
            else 10000 if key == ("1773.15", "0.40") else 100000
        )
        for key in expected_composition
    }
    for observation in redox_observations:
        assert "atmosphere" in observation.point_conditions
        assert observation.point_conditions["atmosphere"].state.value in {"air", "argon"}
        assert observation.point_conditions["atmosphere"].locator.page in {4, 5}
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        components = dict(observation.identity.composition.value.components)
        identity = (
            str(observation.identity.temperature_K.value),
            str(observation.value.point),
        )
        if identity == ("2173.15", "1.00") and (
            "::ehrt2008_redox_fraction_as_1000_ppmw_fig6::"
            in observation.observation_id
        ):
            assert components == as_composition
        else:
            assert components == expected_composition[identity]
        expected_pressure_value = expected_pressure[identity]
        if expected_pressure_value is None:
            assert observation.identity.total_pressure_Pa.tag.value == "unknown"
            assert observation.identity.total_pressure_Pa.value is None
        else:
            assert observation.identity.total_pressure_Pa is not None
            assert observation.identity.total_pressure_Pa.is_value
            assert observation.identity.total_pressure_Pa.value == expected_pressure_value
        assert not observation.notices
    fig6_as = [
        observation
        for observation in redox_observations
        if "::ehrt2008_redox_fraction_as_1000_ppmw_fig6::"
        in observation.observation_id
    ]
    assert len(fig6_as) == 2
    assert sorted(str(observation.identity.temperature_K.value) for observation in fig6_as) == [
        "2173.15",
        "2273.15",
    ]
    for observation in fig6_as:
        assert str(observation.value.point) == "1.00"
        assert observation.point_conditions["atmosphere"].state.value == "argon"
        assert dict(observation.identity.composition.value.components) == as_composition
    conclusion_pressure = next(
        observation
        for observation in fig6_as
        if str(observation.identity.temperature_K.value) == "2173.15"
    ).point_conditions["total_pressure_Pa"]
    assert str(conclusion_pressure.state.value) == "100000"
    assert conclusion_pressure.locator.page == 5
    assert conclusion_pressure.locator.published_page == 72
    assert conclusion_pressure.locator.section == "Conclusions"
    abs_only_sn2 = [
        observation
        for observation in redox_observations
        if "::ehrt2008_redox_fraction_abs_1900C_argon::"
        in observation.observation_id
    ]
    assert len(abs_only_sn2) == 1
    assert str(abs_only_sn2[0].value.point) == "1.00"
    assert dict(abs_only_sn2[0].identity.composition.value.components) == abs_composition
    density_observations = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations
        if observation.observation_id.rsplit("::", 1)[-1] in DENSITY_RENDER_PINS
    }
    assert set(density_observations) == set(DENSITY_RENDER_PINS)
    for observation_id, (printed, migrated, sample) in DENSITY_RENDER_PINS.items():
        observation = density_observations[observation_id]
        assert observation.identity.species.formula == "unknown"
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value
        assert dict(observation.identity.composition.value.components) == (
            {"Al2O3": 15, "SiO2": 65, "BaO": 10, "CaO": 10}
            if sample == "AS"
            else {"Al2O3": 10, "B2O3": 8, "SiO2": 67, "BaO": 2, "CaO": 9, "MgO": 4}
        )
        assert observation.identity.composition.value.amount_basis.value == "mol_inventory"
        assert observation.value.kind is ValueKind.POINT
        assert str(observation.value.point) == migrated
        assert observation.derivation is not None
        assert observation.derivation.output_unit == "kg_per_m3"
        assert observation.derivation.relation == "g_per_cm3_to_kg_per_m3"
        assert sample in {"AS", "ABS"}
        assert printed in {"2.85", "2.56"}
        assert observation.uncertainty.kind.value == "printed"
        assert observation.uncertainty.verbatim["sigma"] == "10"
        assert observation.uncertainty.verbatim["units"] == "kg/m3"


def test_ehrt_render_pin_fails_after_a_redox_cell_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    observation = next(
        item
        for item in mutated["species"]["Sn"]["observations"]
        if item["observation_id"] == "ehrt2008_redox_fraction_air_1600_1700C"
    )
    observation["values"]["points"][0]["value"] = "0.21"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_ehrt_render_pin_fails_after_a_sample_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    observation = next(
        item
        for item in mutated["species"]["Sn"]["observations"]
        if item["observation_id"] == "ehrt2008_redox_fraction_argon_1600_1800C"
    )
    observation["values"]["points"][0]["sample"] = "ABS"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_ehrt_density_sigma_pin_fails_when_sigma_is_removed():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    observation = next(
        item
        for item in mutated["species"]["Sn"]["observations"]
        if item["observation_id"] == "ehrt2008_density_AS"
    )
    observation.pop("uncertainty")
    with pytest.raises(AssertionError):
        _assert_density_render_pins(mutated)


def test_ehrt_render_pin_fails_after_a_density_cell_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    observation = next(
        item
        for item in mutated["species"]["Sn"]["observations"]
        if item["observation_id"] == "ehrt2008_density_AS"
    )
    observation["values"]["density_g_cm3"] = "2.86"
    with pytest.raises(AssertionError):
        _assert_density_render_pins(mutated)

