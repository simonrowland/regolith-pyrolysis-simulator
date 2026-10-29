"""Figure-read pins for Shornikov 2025 Fig. 1 and Stolyarova 1996 Fig. 2.

The stored numbers are the audit CSV. Doubling one stored value must fail the
pin. Panel J and L series identity is the printed legend and the Fig. 2
caption, not the CSV series cell. Migration has to carry T_C and the printed
composition onto every new point that has them.
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
}
# Panel J legend box on published p. 29: filled circle 1, open square 3,
# filled triangle 5, filled square 6, open triangle 7. No numeral 2.
PANEL_J_LEGEND_NUMERALS = {"1", "3", "5", "6", "7"}
FIGURE2_GLYPH = {
    "1": "filled circle",
    "2": "open circle",
    "3": "open square",
    "4": "plus",
    "5": "filled triangle",
    "6": "filled square",
    "7": "open triangle",
    "9": "filled diamond",
}
# Fig. 2 caption (published p. 26): (3) EMF, ref. 26, 1903 K;
# (7) sulfide capacities in the slags, ref. 22, 1873 K.
CAPTION_SERIES_K = {"3": Decimal("1903"), "7": Decimal("1873")}
CAPTION_SERIES_REGIME = {
    "3": "emf_cell",
    "7": "literature_comparison_figure",
}
CAPTION_SERIES_EXPERIMENT = {
    "3": "stolyarova-1996-fig2-emf-comparison",
    "7": "stolyarova-1996-fig2-literature-comparison",
}
CAPTION_SERIES_METHOD = {
    "3": "EMF, ref. 26, 1903 K",
    "7": "sulfide capacities in the slags, ref. 22, 1873 K",
}
REBOUND_POINT_SERIES = {"J-7-01": "7", "J-7-02": "7", "L-3-05": "3"}
REJECTED_SERIES2_POINT_IDS = {
    "J-2-01", "J-2-02", "J-2-03", "J-2-04", "L-2-01", "L-2-02", "L-2-03",
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


def _jl_digitized(extract: dict) -> list[dict]:
    found = []
    for observation in _observations(extract).values():
        observation_id = observation["observation_id"]
        if "fig2_J_" not in observation_id and "fig2_L_" not in observation_id:
            continue
        if not observation_id.endswith("_digitized") or "figure_only" in observation_id:
            continue
        found.append(observation)
    return found


def _assert_jl_legend(extract: dict) -> None:
    """Series identity for panels J and L comes from the printed legend."""
    located = {}
    by_panel = {"J": set(), "L": set()}
    for observation in _jl_digitized(extract):
        panel = observation["locator"]["panel"]
        series = observation["locator"]["series"]
        by_panel[panel].add(series)
        assert observation["observation_id"].endswith(f"series{series}_digitized")
        glyph = FIGURE2_GLYPH[series]
        if series in CAPTION_SERIES_K:
            expected_k = int(CAPTION_SERIES_K[series])
            assert observation["regime"] == CAPTION_SERIES_REGIME[series]
            assert observation["experiment"] == CAPTION_SERIES_EXPERIMENT[series]
            assert observation["values"]["method_as_printed"] == CAPTION_SERIES_METHOD[series]
            assert observation["T_range_K"] == [expected_k, expected_k]
        for point in observation["values"]["points"]:
            located[point["point_id"]] = series
            assert point["marker_symbol"] == glyph
            assert point["point_conditions"]["composition"]["locator"]["series"] == series
            if series in CAPTION_SERIES_K:
                assert Decimal(point["T_C"]) + Decimal("273.15") == CAPTION_SERIES_K[series]
    assert by_panel["J"] == PANEL_J_LEGEND_NUMERALS
    assert "2" not in by_panel["L"]
    assert set(located).isdisjoint(REJECTED_SERIES2_POINT_IDS)
    for point_id, series in REBOUND_POINT_SERIES.items():
        assert located[point_id] == series


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
    assert len(kept) == 277
    assert sum(int(row["flag_dex"]) or int(row["flag_linear"]) for row in kept) == 1
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
    _assert_jl_legend(stolyarova)


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
    flagged = next(row for row in mutated_rows if row["point_id"] == "J-7-01")
    flagged["stored_activity"] = "0.00096"
    with pytest.raises(AssertionError):
        _assert_figure_reads(shornikov, shornikov_rows, stolyarova, mutated_rows)

    # A coordinated yaml+CSV edit that puts the open triangles back on series 2
    # still fails. The CSV series cell is not the legend.
    relabelled = deepcopy(stolyarova)
    relabelled_rows = deepcopy(stolyarova_rows)
    observation = next(
        item
        for item in relabelled["species"]["SiO2"]["observations"]
        if item["observation_id"] == "stolyarova_1996_fig2_J_sio2_series7_digitized"
    )
    observation["observation_id"] = "stolyarova_1996_fig2_J_sio2_series2_digitized"
    observation["locator"]["series"] = "2"
    observation["regime"] = "emf_cell"
    observation["experiment"] = "stolyarova-1996-fig2-emf-comparison"
    observation["T_range_K"] = [1738, 1738]
    observation["values"]["method_as_printed"] = "EMF, ref. 25, 1738 K"
    for point in observation["values"]["points"]:
        point["marker_symbol"] = "open circle"
        point["T_C"] = "1464.85"
        point["point_conditions"]["composition"]["locator"]["series"] = "2"
    for row in relabelled_rows:
        if row["point_id"] in {"J-7-01", "J-7-02"}:
            row["series"] = "2"
            row["marker"] = "open circle"
            row["T_C"] = "1464.85"
            row["T_K"] = "1738"
            row["experiment"] = "stolyarova-1996-fig2-emf-comparison"
            row["attribution"] = "EMF, ref. 25, 1738 K"
    with pytest.raises(AssertionError):
        _assert_figure_reads(shornikov, shornikov_rows, relabelled, relabelled_rows)


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
