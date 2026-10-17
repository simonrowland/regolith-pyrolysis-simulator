from pathlib import Path

import yaml

from simulator.battery.enums import IdentityEqualKind
from simulator.battery.identity import identity_equal, profile_for
from tests.battery.test_migrate import _migrate_real_extract


EXTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data/literature/extracts/archakov-1999-titania-slag-evaporation.yaml"
)


def test_table1_initial_compositions_match_rendered_rows() -> None:
    """Table 1, PDF p. 2: pin the row count and values from three rows."""
    extract = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    table = next(
        item
        for item in extract["species"]["CaO"]["context"]
        if item["observation_id"] == "archakov_1999_table1_initial_melt_compositions"
    )
    points = table["values"]["points"]

    assert len(points) == 17
    assert [point["sample_number"] for point in points] == list(range(1, 18))
    assert points[0]["CaO_mol_pct"] == 34.28
    assert points[0]["TiO2_mol_pct"] == 5.05
    assert points[4]["TiO2_mol_pct"] == 10.65
    assert points[13]["CaO_mol_pct"] == 33.42
    assert points[13]["TiO2_mol_pct"] == 33.41
    assert points[13]["SiO2_mol_pct"] == 33.17
    assert points[13]["x_SiO2_over_TiO2_as_printed"] == 0.99
    assert points[16]["SiO2_mol_pct"] == 0.00
    assert points[16]["x_SiO2_over_TiO2_as_printed"] == 0.00
    assert points[13]["locator"]["pdf_page_index"] == 1
    assert points[13]["locator"]["note"] == "Table 1 row 14"
    assert extract["source"]["related_sources"][0]["source_id"] == (
        "shornikov-2000-titania-melts"
    )
    assert "T. Yu." in extract["source"]["citation"]


def test_melting_point_migrates_on_section_with_partial_printed_composition(
    tmp_path: Path,
) -> None:
    extract = yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))
    system = "CaTiSiO5-CaTiO3"
    point = extract["species"][system]["observations"][0]
    row = point["values"]["points"][0]

    assert point["type"] == "transition_point"
    assert point["values"]["property_kind"] == "liquidus"
    assert row["T_m_K"] == 1643
    assert row["T_K"] == 1643
    assert point["values"]["T_m_uncertainty_K"] == 15
    assert row["composition_as_printed"] == "37.60 mole % TiO2"
    assert row["point_conditions"]["printed_composition"]["state"]["value"] == {
        "TiO2": "37.60 mole %"
    }
    assert point["uncertainty"]["T_m_uncertainty_K"] == 15
    assert "confidence level not stated" in point["uncertainty"]["note"]
    assert row["point_conditions"]["composition"]["state"]["tag"] == "unknown"
    assert row["point_conditions"]["atmosphere"]["state"]["value"] == "air"

    migrated = _migrate_real_extract(tmp_path, EXTRACT_PATH.name)
    observation = next(
        observation
        for observation in migrated.observations.values()
        if "::archakov_1999_melting_point_tio2_37_60" in observation.observation_id
    )
    assert observation.identity.species.formula == system
    assert observation.identity.composition.is_unknown
    assert "composition" in profile_for(observation.identity).required
    assert observation.point_conditions["printed_composition"].state.value == {
        "TiO2": "37.60 mole %"
    }
    assert identity_equal(observation.identity, observation.identity).kind is (
        IdentityEqualKind.IDENTITY_UNKNOWN
    )
