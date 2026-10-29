"""Figure-read pins for Shornikov 2025 Fig. 1 and Stolyarova 1996 Fig. 2.

The stored numbers are the audit CSV. Doubling one stored value must fail the
pin. Migration has to carry T_C and the printed composition onto every new
point that has them.
"""

from __future__ import annotations

import csv
import subprocess
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import Migrator, convert_pressure_to_pa


ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = ROOT / "data/literature/extracts"

SHORNIKOV_YAML = "shornikov-2025-ca-al-inclusion-kems.yaml"
STOLYAROVA_YAML = "stolyarova-1996-cao-alumina-silica-kems.yaml"

SHORNIKOV_PINS = {
    "A-Ca-01": ("Ca", "1719.45", "4.5e-8", "790.90", "1419.47", "791.00", "1419.00"),
    "A-Mg-01": ("Mg", "1527.85", "2.9e-7", "379.65", "1098.09", "379.00", "1097.00"),
    "B-SiO-01": ("SiO", "1539.15", "6.9e-7", "2316.77", "943.46", "2317.00", "944.00"),
}
STOLYAROVA_PINS = {
    "A-1-01": ("CaO", "1659.85", "0.99", "2327.00", "1312.00", "2326.63", "1311.40"),
    "E-1-01": ("Al2O3", "1659.85", "0.0013", "1969.00", "3062.00", "1968.76", "3062.20"),
    "J-2-04": ("SiO2", "1464.85", "0.31", "3064.00", "1220.00", "3070.66", "1238.29"),
}


def _rows(name: str) -> list[dict[str, str]]:
    with (EXTRACTS / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _observations(extract: dict) -> dict[str, dict]:
    found = {}
    for species in extract["species"].values():
        for observation in species.get("observations") or []:
            found[observation["observation_id"]] = observation
    return found


def _shornikov_observation_id(panel: str, species: str) -> str:
    kind = "typeA" if panel == "A" else "typeB"
    return f"shornikov_2025_fig1_{kind}_{species}_digitized"


def _stolyarova_observation_id(row: dict[str, str]) -> str:
    return (
        f"stolyarova_1996_fig2_{row['panel']}_{row['oxide'].lower()}_"
        f"series{row['series']}_digitized"
    )


def _assert_figure_reads(shornikov, shornikov_rows, stolyarova, stolyarova_rows) -> None:
    assert len(shornikov_rows) == 95
    assert sum(int(row["flag_dex"]) or int(row["flag_linear"]) for row in shornikov_rows) == 0
    shornikov_obs = _observations(shornikov)
    for row in shornikov_rows:
        observation = shornikov_obs[_shornikov_observation_id(row["panel"], row["species"])]
        point = next(item for item in observation["values"]["points"] if item["point_id"] == row["point_id"])
        assert point["T_C"] == row["stored_T_C"]
        assert point["pressure_atm"] == row["stored_p_atm"]
        assert observation["values"]["method_class"] == "figure_only"
        assert observation["values"]["admission_status"] == "admitted"
        assert point["point_conditions"]["composition"]["state"]["tag"] == "unknown"
        assert point["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"
    for point_id, expected in SHORNIKOV_PINS.items():
        species, temperature, pressure, x1, y1, x2, y2 = expected
        row = next(item for item in shornikov_rows if item["point_id"] == point_id)
        assert row["species"] == species
        assert (row["read1_x_px"], row["read1_y_px"], row["read2_x_px"], row["read2_y_px"]) == (
            x1, y1, x2, y2,
        )
        assert row["stored_T_C"] == temperature
        assert row["stored_p_atm"] == pressure

    kept = [row for row in stolyarova_rows if not row["rejected_reason"]]
    assert len(stolyarova_rows) == 284
    assert len(kept) == 281
    assert sum(int(row["flag_dex"]) or int(row["flag_linear"]) for row in kept) == 3
    stolyarova_obs = _observations(stolyarova)
    for row in kept:
        observation = stolyarova_obs[_stolyarova_observation_id(row)]
        point = next(item for item in observation["values"]["points"] if item["point_id"] == row["point_id"])
        assert point["activity"] == row["stored_activity"]
        assert point["activity_log10_mean"] == row["stored_log10_a"]
        assert observation["values"]["method_class"] == "figure_only"
        assert observation["values"]["admission_status"] == "admitted"
        components = point["point_conditions"]["composition"]["state"]["value"]["components"]
        assert components == [
            ["CaO", row["x_CaO"]],
            ["Al2O3", row["x_Al2O3"]],
            ["SiO2", row["x_SiO2"]],
        ]
        if row["T_C"]:
            assert point["T_C"] == row["T_C"]
        else:
            assert "T_C" not in point
            assert point["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
        if row["series"] == "1":
            assert observation["experiment"] == "stolyarova-1996-mi1201-1933k"
        else:
            assert observation["experiment"] != "stolyarova-1996-mi1201-1933k"
    for point_id, expected in STOLYAROVA_PINS.items():
        oxide, temperature, activity, x1, y1, x2, y2 = expected
        row = next(item for item in kept if item["point_id"] == point_id)
        assert row["oxide"] == oxide
        assert (row["read1_x_px"], row["read1_y_px"], row["read2_x_px"], row["read2_y_px"]) == (
            x1, y1, x2, y2,
        )
        assert row["T_C"] == temperature
        assert row["stored_activity"] == activity
    placeholders = [
        observation
        for observation in stolyarova_obs.values()
        if "figure_only" in observation["observation_id"]
    ]
    assert len(placeholders) == 6
    assert all(item["values"]["reason"] == "figure_not_digitized" for item in placeholders)


def test_refractory_figure_csvs_match_extracts_and_a_doubled_value_fails() -> None:
    shornikov = yaml.safe_load((EXTRACTS / SHORNIKOV_YAML).read_text())
    stolyarova = yaml.safe_load((EXTRACTS / STOLYAROVA_YAML).read_text())
    shornikov_rows = _rows("shornikov-2025-ca-al-inclusion-kems-figure-audit.csv")
    stolyarova_rows = _rows("stolyarova-1996-cao-alumina-silica-kems-figure-audit.csv")
    _assert_figure_reads(shornikov, shornikov_rows, stolyarova, stolyarova_rows)

    mutated = deepcopy(shornikov)
    point = next(
        item
        for observation in mutated["species"]["Ca"]["observations"]
        for item in observation["values"].get("points") or []
        if item.get("point_id") == "A-Ca-01"
    )
    point["pressure_atm"] = "9.0e-8"
    with pytest.raises(AssertionError):
        _assert_figure_reads(mutated, shornikov_rows, stolyarova, stolyarova_rows)

    mutated_rows = deepcopy(stolyarova_rows)
    flagged = next(row for row in mutated_rows if row["point_id"] == "J-2-04")
    flagged["stored_activity"] = "0.62"
    with pytest.raises(AssertionError):
        _assert_figure_reads(shornikov, shornikov_rows, stolyarova, mutated_rows)


def _migrate(path: Path):
    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _fingerprint(result) -> Counter:
    issues = () if result.validation is None else result.validation.hard_issues
    return Counter((issue.reason.value, issue.detail) for issue in issues)


def _head_copy(name: str, directory: Path) -> Path:
    payload = subprocess.check_output(
        ["git", "show", f"HEAD:data/literature/extracts/{name}"],
        cwd=ROOT,
        text=True,
    )
    path = directory / name
    path.write_text(payload)
    return path


def test_figure_reads_migrate_with_no_new_hard_issues(tmp_path: Path) -> None:
    current = {
        SHORNIKOV_YAML: _migrate(EXTRACTS / SHORNIKOV_YAML),
        STOLYAROVA_YAML: _migrate(EXTRACTS / STOLYAROVA_YAML),
    }
    baseline = {
        name: _migrate(_head_copy(name, tmp_path))
        for name in current
    }
    for name, result in current.items():
        assert _fingerprint(result) == _fingerprint(baseline[name])

    shornikov_rows = _rows("shornikov-2025-ca-al-inclusion-kems-figure-audit.csv")
    shornikov = [
        observation
        for observation in current[SHORNIKOV_YAML].observations.values()
        if "_digitized::" in observation.observation_id
        or observation.observation_id.endswith("_digitized")
    ]
    expected_shornikov = Counter(
        (
            row["species"],
            Decimal(row["stored_T_C"]) + Decimal("273.15"),
            convert_pressure_to_pa(Decimal(row["stored_p_atm"]), "atm")[0],
        )
        for row in shornikov_rows
    )
    migrated_shornikov = Counter(
        (
            observation.identity.species.formula,
            observation.identity.temperature_K.value,
            observation.value.point,
        )
        for observation in shornikov
    )
    assert migrated_shornikov == expected_shornikov
    assert all(
        observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        and observation.point_conditions["composition"].state.is_unknown
        for observation in shornikov
    )

    stolyarova_rows = [
        row
        for row in _rows("stolyarova-1996-cao-alumina-silica-kems-figure-audit.csv")
        if not row["rejected_reason"]
    ]
    stolyarova = [
        observation
        for observation in current[STOLYAROVA_YAML].observations.values()
        if "fig2_" in observation.observation_id and "_digitized" in observation.observation_id
    ]

    def _activity_key(oxide: str, temperature: Decimal | None, activity: Decimal, row: dict[str, str]):
        return (
            oxide,
            temperature,
            activity,
            Decimal(row["x_CaO"]),
            Decimal(row["x_Al2O3"]),
            Decimal(row["x_SiO2"]),
        )

    expected_stolyarova = Counter(
        _activity_key(
            row["oxide"],
            Decimal(row["T_C"]) + Decimal("273.15") if row["T_C"] else None,
            Decimal(row["stored_activity"]),
            row,
        )
        for row in stolyarova_rows
    )
    migrated_stolyarova = Counter()
    for observation in stolyarova:
        temperature = observation.identity.temperature_K
        composition = dict(
            observation.point_conditions["composition"].state.value.components
        )
        migrated_stolyarova[
            (
                observation.identity.species.formula,
                temperature.value if temperature.is_value else None,
                observation.value.point,
                composition["CaO"],
                composition["Al2O3"],
                composition["SiO2"],
            )
        ] += 1
        assert observation.identity.quantity.value is Quantity.ACTIVITY
        assert observation.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        if temperature.is_unknown:
            assert observation.point_conditions["temperature_K"].state.is_unknown
    assert migrated_stolyarova == expected_stolyarova
