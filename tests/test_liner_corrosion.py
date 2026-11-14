"""Real-extract contract for Tier A liner corrosion invariants."""

from __future__ import annotations

from collections import Counter

from simulator.liner_corrosion import load_liner_corrosion_invariants


def test_all_source_records_load_with_temperature_phases_and_locators() -> None:
    records = load_liner_corrosion_invariants()

    assert Counter(row["source_id"] for row in records) == {
        "rankin-wright-1915-ajs-cao-al2o3-sio2": 47,
        "morey-1964-usgs-pp440l-phase-equilibria": 50,
        "shults-1995-calcium-aluminate-thermodynamics": 6,
        "shornikov-1997-cao-al2o3": 23,
    }
    assert len(records) == 126
    for row in records:
        assert row["subsystem"]
        assert isinstance(row["T_invariant_C"], (int, float))
        assert row["phases"] is None or row["phases"]
        assert row["liquid_composition"] is None or row["liquid_composition"]
        assert row["liquid_composition_unit"] == (
            "wt%" if row["liquid_composition"] is not None else None
        )
        assert row["invariant_type"]
        assert row["locator"]["page"]
        assert row["locator"].get("table") or row["locator"].get("section")


def test_binary_aluminates_keep_source_specific_temperatures_and_compositions() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    morey_c3a = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t6_r1",
        )
    ]
    assert morey_c3a["T_invariant_C"] == 1535
    assert morey_c3a["invariant_type"] == "peritectic"
    assert set(morey_c3a["phases"]) == {"3CaO·Al2O3", "CaO", "liquid"}
    assert morey_c3a["liquid_composition"] == {"CaO": 59, "Al2O3": 41}

    morey_binary_expected = {
        "morey1964_t6_r1": (1535, "peritectic", {"CaO": 59, "Al2O3": 41}),
        "morey1964_t6_r3": (1458, "congruent melting", {"CaO": 48.54, "Al2O3": 51.46}),
        "morey1964_t6_r5": (1600, "congruent melting", {"CaO": 35.49, "Al2O3": 64.51}),
        "morey1964_t6_r7": (1750, "congruent melting", {"CaO": 21.54, "Al2O3": 78.46}),
        "morey1964_t6_r9": (1850, "peritectic", {"CaO": 11, "Al2O3": 89}),
    }
    for observation_id, (temperature, kind, composition) in morey_binary_expected.items():
        row = records[("morey-1964-usgs-pp440l-phase-equilibria", observation_id)]
        assert row["subsystem"] == "CaO-Al2O3"
        assert row["T_invariant_C"] == temperature
        assert row["invariant_type"] == kind
        assert row["liquid_composition"] == composition

    morey_c12a7_values = {
        observation_id: records[("morey-1964-usgs-pp440l-phase-equilibria", observation_id)]
        for observation_id in (
            "morey1964_t6_c12a7_1455",
            "morey1964_t6_c12a7_1391",
        )
    }
    assert {row["T_invariant_C"] for row in morey_c12a7_values.values()} == {1455, 1391}
    assert all(row["liquid_composition"] is None for row in morey_c12a7_values.values())

    shornikov = records[
        (
            "shornikov-1997-cao-al2o3",
            "shornikov_1997_cao_al2o3_t3_ca3al2o6_eq7",
        )
    ]
    assert shornikov["T_invariant_C"] == 1534.85
    assert shornikov["temperature_uncertainty_C"] == 5
    assert shornikov["compound_as_printed"] == "3CaO·Al2O3"
    assert shornikov["phases"] is None
    assert shornikov["liquid_composition"] is None
    cited_shornikov = records[
        (
            "shornikov-1997-cao-al2o3",
            "shornikov_1997_cao_al2o3_t3_ca3al2o6_ref16",
        )
    ]
    assert cited_shornikov["T_invariant_C"] == 1534.85
    assert cited_shornikov["source_reference_as_printed"] == "[16]"

    shults_ca2 = records[
        (
            "shults-1995-calcium-aluminate-thermodynamics",
            "shults1995_ca2_melting",
        )
    ]
    assert shults_ca2["T_invariant_C"] == 1761.85
    assert shults_ca2["liquid_composition"] is None
    assert {
        observation_id: records[
            (
                "shults-1995-calcium-aluminate-thermodynamics",
                observation_id,
            )
        ]["T_invariant_C"]
        for observation_id in (
            "shults1995_ca_dissociation",
            "shults1995_c12a7_melting",
            "shults1995_ca_melting",
            "shults1995_ca2_melting",
            "shults1995_ca6_melting",
            "shults1995_c5a3_melting",
        )
    } == {
        "shults1995_ca_dissociation": 1534.85,
        "shults1995_c12a7_melting": 1459.85,
        "shults1995_ca_melting": 1603.85,
        "shults1995_ca2_melting": 1761.85,
        "shults1995_ca6_melting": 1874.85,
        "shults1995_c5a3_melting": 1479.85,
    }


def test_rankin_and_morey_retain_their_distinct_cas_rows() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    rankin = records[
        (
            "rankin-wright-1915-ajs-cao-al2o3-sio2",
            "rankin_wright_1915_tD_r1_invariant",
        )
    ]
    assert rankin["T_invariant_C"] == 1170
    assert rankin["liquid_composition"] == {
        "CaO": 23.25,
        "Al2O3": 14.75,
        "SiO2": 62,
    }

    morey = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t26_r1",
        )
    ]
    assert morey["T_invariant_C"] == 1345
    assert morey["liquid_composition"] == {
        "CaO": 9.8,
        "Al2O3": 18.6,
        "SiO2": 71.6,
    }


def test_morey_na_and_mgo_ternaries_keep_the_printed_compositions() -> None:
    records = {
        (row["source_id"], row["observation_id"]): row
        for row in load_liner_corrosion_invariants()
    }

    na_row = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t12_r1",
        )
    ]
    assert na_row["subsystem"] == "Na2O-CaO-Al2O3"
    assert na_row["T_invariant_C"] == 1550
    assert na_row["liquid_composition"] == {
        "Na2O": 8.6,
        "CaO": 21.3,
        "Al2O3": 70.1,
    }

    mgo_row = records[
        (
            "morey-1964-usgs-pp440l-phase-equilibria",
            "morey1964_t21_r1",
        )
    ]
    assert mgo_row["subsystem"] == "MgO-CaO-Al2O3"
    assert mgo_row["T_invariant_C"] == 1450
    assert mgo_row["liquid_composition"] == {
        "MgO": 6.2,
        "CaO": 51.5,
        "Al2O3": 43.3,
    }
