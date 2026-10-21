from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ehrt-2008-sn-redox-aluminosilicate.yaml"
RENDER_PINS = {
    ("ehrt2008_redox_fraction_air_1600_1700C", 0): ("0.20", "~20% Sn2+", "1600", "air"),
    ("ehrt2008_redox_fraction_air_1600_1700C", 1): ("0.30", "~30% Sn2+", "1700", "air"),
    ("ehrt2008_redox_fraction_argon_1600_1700C", 0): ("0.40", "nearly 40% Sn2+", "1600", "argon"),
    ("ehrt2008_redox_fraction_argon_1600_1700C", 1): ("0.50", "~50% Sn2+", "1800", "argon"),
    ("ehrt2008_redox_fraction_argon_1600_1700C", 2): ("0.80", "~80% Sn2+", "1800", "argon"),
}


def _assert_render_pins(document):
    observations = {
        observation["observation_id"]: observation
        for observation in document["species"]["Sn"]["observations"]
    }
    for (observation_id, index), expected in RENDER_PINS.items():
        point = observations[observation_id]["values"]["points"][index]
        assert (point["value"], point["value_as_published"], point["T_C"], point["point_conditions"]["atmosphere"]["state"]["value"]) == expected


def test_ehrt_redox_values_migrate_as_points_with_printed_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    observations = list(migrator.result.observations.values())
    assert len(observations) == len(RENDER_PINS)
    assert all(observation.identity.species.formula == "Sn" for observation in observations)
    assert all(observation.value.kind is ValueKind.POINT for observation in observations)
    assert {str(observation.value.point) for observation in observations} == {"0.20", "0.30", "0.40", "0.50", "0.80"}
    assert all(observation.identity.temperature_K.is_value for observation in observations)
    assert all("atmosphere" in observation.point_conditions for observation in observations)


def test_ehrt_render_pin_fails_after_a_redox_cell_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Sn"]["observations"][0]["values"]["points"][0]["value"] = "0.21"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)

