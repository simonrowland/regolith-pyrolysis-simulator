"""Source pins for DeMaria et al. (1971), checked against page renders."""

from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/kems-022-demaria-1971.yaml"
)


def _observations(extract: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in extract["species"].values()
        for row in species.get("observations", [])
    }


def test_demaria_1971_table_pressures_match_rendered_cells_and_counts() -> None:
    """Pages 4–6 and 9: pin printed values, cell count, and unprinted total P."""
    extract = yaml.safe_load(EXTRACT.read_text())
    rows = _observations(extract)

    table_observations = {
        "demaria_1971_o2_psat_table1_alkali_range": 7,
        "demaria_1971_mn_psat_table2_sample_12022": 3,
        "demaria_1971_cr_psat_table2_sample_12022": 2,
        "demaria_1971_mg_psat_table3_lunar_basalt": 8,
        "demaria_1971_al2o_psat_table4": 14,
        "demaria_1971_alo_psat_table4": 6,
        "demaria_1971_o_atomic_psat_table5": 13,
    }
    assert sum(table_observations.values()) == 53
    assert all(len(rows[oid]["values"]["points"]) == count for oid, count in table_observations.items())

    # Table 1, p. 1370 (PDF p. 4); Table 2, p. 1371 (PDF p. 5).
    assert rows["demaria_1971_o2_psat_table1_alkali_range"]["values"]["points"][0] == {
        "T_K": 1396.0,
        "p_atm": 5.54e-09,
        "sample": "12022",
    }
    assert rows["demaria_1971_mn_psat_table2_sample_12022"]["values"]["points"][2]["p_atm"] == 8.47e-08
    assert rows["demaria_1971_cr_psat_table2_sample_12022"]["values"]["points"][0]["p_atm"] == 1.03e-08

    # Table 3, p. 1372 (PDF p. 6); Tables 4–5, p. 1375 (PDF p. 9).
    assert rows["demaria_1971_mg_psat_table3_lunar_basalt"]["values"]["points"][0]["p_atm"] == 1.6e-08
    assert rows["demaria_1971_al2o_psat_table4"]["values"]["points"][0]["p_atm"] == 5.07e-09
    assert rows["demaria_1971_alo_psat_table4"]["values"]["points"][-1]["p_atm"] == 8.96e-06
    assert rows["demaria_1971_o_atomic_psat_table5"]["values"]["points"][-1]["p_atm"] == 1.42e-05

    reduced = (
        "demaria_1971_mn_psat_table2_sample_12022",
        "demaria_1971_cr_psat_table2_sample_12022",
        "demaria_1971_mg_psat_table3_lunar_basalt",
        "demaria_1971_al2o_psat_table4",
        "demaria_1971_alo_psat_table4",
    )
    assert all(rows[oid]["values"]["method_class"] == "measured_reduced" for oid in reduced)
    # Table 1 and Table 5 were already directly calibrated pressure rows.
    assert rows["demaria_1971_o2_psat_table1_alkali_range"]["values"]["method_class"] == "measured_direct"
    assert rows["demaria_1971_o_atomic_psat_table5"]["values"]["method_class"] == "measured_direct"

    for oid in table_observations:
        assert "p_Pa" not in rows[oid]["values"]
        assert all("p_Pa" not in point for point in rows[oid]["values"]["points"])
        assert "total_pressure_Pa" not in rows[oid].get("point_conditions", {})


def test_demaria_1971_table6_thermochemistry_values_match_render() -> None:
    """Table 6, p. 1376 (PDF p. 10): retain the printed ± values and footnotes."""
    rows = _observations(yaml.safe_load(EXTRACT.read_text()))
    assert rows["demaria_1971_table6_feo_dissociation"]["values"]["third_law_delta_H0_kcal_mol"] == {
        "BC": "39 ± 2",
        "NAA": "38 ± 2",
    }
    assert rows["demaria_1971_table6_feo_dissociation"]["values"]["second_law_delta_H0_kcal_mol"] == {
        "BC": "38 ± 3",
        "footnote": "a",
    }
    assert rows["demaria_1971_table6_tio2_atomic_o"]["values"]["third_law_delta_H0_kcal_mol"] == {
        "BC": None,
        "NAA": "142 ± 3",
    }
