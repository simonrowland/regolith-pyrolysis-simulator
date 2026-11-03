from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SID = "iwahashi-2021-cr-oxide-solid-activities"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
PREFIX = "iwahashi_2021_cr_oxide_solid_activities"

EXPECTED_TABLE2 = [
    ("1-1", "1.37 × 10^−3"),
    ("1-2", "1.26 × 10^−3"),
    ("2-1", "2.35 × 10^−3"),
    ("2-2", "2.22 × 10^−3"),
    ("3-1", "3.15 × 10^−3"),
    ("3-2", "3.30 × 10^−3"),
    ("4-1", "8.55 × 10^−3"),
    ("4-2", "8.03 × 10^−3"),
    ("5-1", "1.13 × 10^−2"),
    ("5-2", "8.82 × 10^−3"),
    ("6-1a", "6.60 × 10^−4"),
    ("6-2a", "6.84 × 10^−4"),
    ("7-1b", "1.06 × 10^−3"),
    ("7-2b", "9.78 × 10^−4"),
    ("8-1b", "2.85 × 10^−3"),
    ("8-2b", "2.93 × 10^−3"),
    ("9-1b", "9.41 × 10^−3"),
    ("9-2b", "8.33 × 10^−3"),
    ("10-1", "2.32 × 10^−3"),
    ("10-2", "2.05 × 10^−3"),
    ("11-1", "3.15 × 10^−3"),
    ("11-2", "3.37 × 10^−3"),
    ("12-1", "7.16 × 10^−3"),
    ("12-2", "6.45 × 10^−3"),
]

EXPECTED_TABLE3 = {
    "CaO": ["0", "-0.02", "-1.33", "-1.66", "-1.84", "-2.07", "-2.92", "-3.04"],
    "SiO2": ["-4.81", "-4.75", "-2.12", "-1.46", "-1.20", "-0.85", "0", "0"],
    "Cr2O3": ["-1.33", "-1.31", "0", "0", "0", "-0.35", "-0.35", "0"],
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, formula: str, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"][formula]["observations"]
        if row["observation_id"] == observation_id
    )


def test_render_pins_cover_every_table2_and_table3_row_and_detect_mutations() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=SID,
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SID) == []

    table2 = [
        row
        for row in document["species"]["Cr"]["observations"]
        if row["locator"].get("table") == "2"
    ]
    assert len(table2) == 24, "Table 2 render contains 24 run rows"
    assert all(
        row["locator"]["page"] == 4 and row["locator"]["table"] == "2"
        for row in table2
    )
    assert [
        (row["values"]["sample_number_as_published"], row["values"]["equilibrium_X_Cr_as_published"])
        for row in table2
    ] == EXPECTED_TABLE2
    assert all(row["values"]["quantity"] == "equilibrium_phase_composition" for row in table2)
    assert all(row["phase"] == "l" for row in table2)
    assert all("X_Cu" in row["values"]["printed_column_header_as_published"] for row in table2)
    assert all("X_Cr" in row["values"]["header_interpretation_note"] for row in table2)

    for formula, expected in EXPECTED_TABLE3.items():
        observations = [
            row
            for row in document["species"][formula]["observations"]
            if row["locator"].get("table") == "3"
        ]
        assert len(observations) == 8, "Table 3 render contains eight phase rows per component"
        assert all(
            row["locator"]["page"] == 7 and row["locator"]["table"] == "3"
            for row in observations
        )
        assert [row["values"]["activity"] for row in observations] == expected

    table1 = document["species"]["Cr2O3"]["context"]
    table1 = [row for row in table1 if row["locator"].get("table") == "1"]
    assert len(table1) == 3, "Table 1 render contains three phase-relation rows"
    assert [row["values"]["sample_number_as_published"] for row in table1] == ["1", "2", "3"]
    assert [row["values"]["bulk_composition_wt_pct"]["CaO"] for row in table1] == ["34.5", "45.7", "35.3"]
    assert all(row["values"]["admission_status"] == "typed_refusal" for row in table1)

    k14 = _observation(document, "Cr2O3", f"{PREFIX}_eq14_log10_K13")
    gamma = _observation(document, "Cr", f"{PREFIX}_eq24_log_gamma_Cr")
    eq26 = _observation(document, "Cr2O3", f"{PREFIX}_eq26_log_activity_Cr2O3")
    eq27 = _observation(document, "Cr2O3", f"{PREFIX}_eq27_log_activity_Cr2O3")
    assert k14["values"]["log10_K_reaction"] == "23.94"
    assert (k14["locator"]["page"], k14["locator"]["equation"]) == (4, "14")
    assert gamma["values"]["gamma"] == "1.54"
    assert (gamma["locator"]["page"], gamma["locator"]["equation"]) == (4, "24")
    assert eq26["values"]["activity"] == "-1.33"
    assert (eq26["locator"]["page"], eq26["locator"]["equation"]) == (6, "26")
    assert eq26["uncertainty"]["value"] == "0.09"
    assert eq26["uncertainty"]["confidence_level"] == "not_stated"
    assert eq27["values"]["activity"] == "-0.35"
    assert (eq27["locator"]["page"], eq27["locator"]["equation"]) == (6, "27")
    assert eq27["uncertainty"]["value"] == "0.08"
    assert eq27["uncertainty"]["confidence_level"] == "not_stated"

    changed = yaml.safe_load(yaml.safe_dump(document))
    mutated = _observation(changed, "SiO2", f"{PREFIX}_p007_t03_r05_c03")
    mutated["values"]["activity"] = "-1.47"
    assert check_all_fidelity_samples_match(changed, label=f"{SID}-mutated-table3")


def test_isolated_migration_preserves_known_axes_and_explicitly_unknown_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    observations = list(result.observations.values())
    assert len(observations) == 52
    assert all(row.identity.quantity.is_value for row in observations)
    speciesless = [row for row in observations if row.identity.species is None]
    assert [(row.observation_id, row.identity.quantity.value) for row in speciesless] == [
        (f"{SID}::{PREFIX}_eq14_log10_K13", Quantity.LOG10_K_REACTION)
    ]  # Reaction equilibrium constants have no single species phase in the migrator.
    phase_bearing = [row for row in observations if row.identity.species is not None]
    assert all(row.identity.species.phase.is_value for row in phase_bearing)
    assert all(row.identity.species.phase.value in {Phase.CR, Phase.L, Phase.G} for row in phase_bearing)
    assert all(row.identity.temperature_K is not None and row.identity.temperature_K.is_value for row in observations)
    assert all(row.point_conditions is not None for row in observations)
    assert all("temperature_K" in row.point_conditions for row in observations)
    assert all(row.point_conditions["temperature_K"].locator is not None for row in observations)

    admitted = sum(row.admission.status.value == "admitted" for row in observations)
    unavailable = sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations)
    print(f"migration observations={len(observations)} hard={len(result.validation.hard_issues)} unavailable={unavailable} admitted={admitted}")

    by_id = {row.observation_id.rsplit("::", 1)[-1]: row for row in observations}
    table2 = [row for row in observations if "_p004_t02_" in row.observation_id]
    assert len(table2) == 24
    for row in table2:
        assert row.identity.species.phase.value is Phase.L
        assert row.point_conditions["fO2_log"].state.is_value
        assert row.point_conditions["fO2_log"].locator is not None
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert row.identity.total_pressure_Pa is not None and row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.composition is not None and row.identity.composition.is_value
        assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_value

    row17 = by_id[f"{PREFIX}_p004_t02_r17_c07"]
    assert row17.value.kind is ValueKind.POINT
    assert row17.value.point == Decimal("0.000978")
    assert row17.point_conditions["fO2_log"].state.value == Decimal("-14.99")
    assert _observation(_document(), "Cr", row17.observation_id.rsplit("::", 1)[-1])["point_conditions"]["fO2_log"]["units"] == "log10(atm)"

    eq26 = by_id[f"{PREFIX}_eq26_log_activity_Cr2O3"]
    eq27 = by_id[f"{PREFIX}_eq27_log_activity_Cr2O3"]
    gamma = by_id[f"{PREFIX}_eq24_log_gamma_Cr"]
    table3_cao = by_id[f"{PREFIX}_p007_t03_r04_c02"]
    activity_experiment_id = f"{SID}-activity-equilibration-series"
    activity_experiment = next(
        row for row in _document()["experiments"]
        if row["experiment_id"] == activity_experiment_id
    )
    assert activity_experiment["pressure_environment"]["total_pressure_Pa"]["state"]["value"] == "101325"
    assert activity_experiment["pressure_environment"]["total_pressure_Pa"]["locator"]["page"] == 3
    assert all(
        _observation(_document(), formula, obs_id)["experiment"] == activity_experiment_id
        for formula, obs_id in (
            ("Cr", f"{PREFIX}_eq24_log_gamma_Cr"),
            ("Cr2O3", f"{PREFIX}_eq26_log_activity_Cr2O3"),
            ("Cr2O3", f"{PREFIX}_eq27_log_activity_Cr2O3"),
        )
    )
    for label, row in (("Table 2 row 7-2b", row17), ("Eq. 26 fitted activity", eq26), ("Table 3 row 3 CaO", table3_cao)):
        profile = profile_for(row.identity)
        temperature = row.identity.temperature_K
        composition = row.identity.composition
        pressure = row.identity.total_pressure_Pa
        oxygen = row.identity.fO2_Pa
        print(
            f"identity {label}: required={sorted(profile.required)} "
            f"temperature_K={temperature} composition={composition} "
            f"total_pressure_Pa={pressure} fO2_Pa={oxygen}"
        )

    assert eq26.identity.temperature_K is not None and eq26.identity.temperature_K.is_value
    assert eq26.identity.total_pressure_Pa is not None and eq26.identity.total_pressure_Pa.is_value
    assert eq26.identity.total_pressure_Pa.value == Decimal("101325")
    assert eq26.identity.composition is not None and eq26.identity.composition.is_unknown
    assert eq26.identity.fO2_Pa is not None and eq26.identity.fO2_Pa.is_unknown
    assert eq26.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
    assert eq26.point_conditions["total_pressure_Pa"].locator.page == 3
    assert eq27.identity.total_pressure_Pa is not None and eq27.identity.total_pressure_Pa.is_value
    assert eq27.identity.total_pressure_Pa.value == Decimal("101325")
    assert gamma.identity.total_pressure_Pa is not None and gamma.identity.total_pressure_Pa.is_value
    assert gamma.identity.total_pressure_Pa.value == Decimal("101325")
    assert gamma.identity.composition is not None and gamma.identity.composition.is_unknown
    assert gamma.identity.fO2_Pa is not None and gamma.identity.fO2_Pa.is_unknown

    # Table 3 is a calculated activity list, not a pressure- or composition-controlled run.
    assert "total_pressure_Pa" not in table3_cao.point_conditions
    table3_raw = _observation(_document(), "CaO", f"{PREFIX}_p007_t03_r04_c02")
    assert table3_raw["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert table3_cao.identity.total_pressure_Pa is not None and table3_cao.identity.total_pressure_Pa.is_unknown
    assert table3_cao.point_conditions["composition"].state.is_unknown
    assert table3_cao.identity.composition is not None and table3_cao.identity.composition.is_unknown
    assert table3_cao.identity.fO2_Pa is not None and table3_cao.identity.fO2_Pa.is_unknown
