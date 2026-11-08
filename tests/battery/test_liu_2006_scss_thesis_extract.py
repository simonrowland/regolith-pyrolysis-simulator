from __future__ import annotations

import csv
from collections import Counter
from copy import deepcopy
from decimal import Decimal

import yaml

from simulator.battery.migrate import REPO_ROOT
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = "liu-2006-scss-anhydrous-silicate-thesis"
EXTRACT_NAME = f"{SID}.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
AUDIT = REPO_ROOT / "data/literature/extracts/audits" / SID


def _document() -> dict:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _run_id(observation_id: str) -> str:
    return observation_id.rsplit("_", 2)[-2]


def test_liu_thesis_full_cell_pins_and_render_mutation() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=SID) == []
    source = _source_observations(document)

    expected_rendered = {
        "liu06_t4_yn40_sio2": ("59.3", "59.3(6)", 63, "4"),
        "liu06_t4_yn40_s": ("1070", "1070(32)", 63, "4"),
        "liu06_t5_yn4_s": ("34.2", "34.2(10)", 66, "5"),
    }
    assert len(document["fidelity_samples"]) == 3
    for observation_id, (value, printed, page, table) in expected_rendered.items():
        row = source[observation_id]
        assert row["values"]["value"] == value
        assert row["values"]["value_as_published"] == printed
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        sample = next(
            item
            for item in document["fidelity_samples"]
            if f"[{observation_id}]" in item["path"]
        )
        assert sample["value"] == value
        assert sample["locator"]["page"] == page

    mutated = deepcopy(document)
    mutated_source = _source_observations(mutated)
    mutated_source["liu06_t4_yn40_sio2"]["values"]["value"] = "59.4"
    assert check_all_fidelity_samples_match(mutated, label=f"{SID}-mutated")


def test_liu_thesis_row_counts_uncertainties_and_isolated_migration(tmp_path) -> None:
    document = _document()
    source = _source_observations(document)
    table4 = [row for key, row in source.items() if key.startswith("liu06_t4_")]
    table5 = [row for key, row in source.items() if key.startswith("liu06_t5_")]
    appendix = [row for key, row in source.items() if key.startswith("liu06_app1_")]
    experiments = document["experiments"]

    assert len(table4) == 21 * 11
    assert len({row["values"]["sample_id"] for row in table4}) == 21
    assert len(table5) == 10 * 6
    assert len({row["values"]["sample_id"] for row in table5}) == 10
    assert len(appendix) == 21
    assert sum("table3_cells_as_published" in row for row in experiments) == 20
    assert len(source) == 312

    mapping = yaml.safe_load((AUDIT.with_suffix(".mapping.yaml")).read_text(encoding="utf-8"))
    printed_rows = {row["table"]: row["printed_rows"] for row in mapping["tables"]}
    assert printed_rows == {
        "1": 11,
        "2": 1,
        "3": 45,
        "4": 47,
        "5": 10,
        "6": 6,
        "Appendix I": 77,
    }
    with AUDIT.with_suffix(".cells.csv").open(encoding="utf-8", newline="") as stream:
        cell_audit = list(csv.DictReader(stream))
    assert len(cell_audit) == 1828
    assert all(row["confirmation"].startswith("b:") for row in cell_audit)

    result = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    migrated = result.observations
    assert len(migrated) == 312
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "unavailable" for row in migrated.values()) == 33

    uncertainty_counts = Counter(
        (
            row.uncertainty.kind.value,
            (row.uncertainty.verbatim or {}).get("confidence_level", "missing"),
        )
        for row in migrated.values()
    )
    assert uncertainty_counts == {
        ("printed", "one_sigma"): 279,
        ("printed", "not_stated"): 33,
    }

    by_suffix = {
        suffix: next(
            row for key, row in migrated.items() if key.endswith(f"::{suffix}")
        )
        for suffix in (
            "liu06_t4_yn40_sio2",
            "liu06_t4_yn40_s",
            "liu06_t5_yn4_s",
        )
    }
    expected_migrated = {
        "liu06_t4_yn40_sio2": (Decimal("59.3"), "0.6", 63),
        "liu06_t4_yn40_s": (Decimal("1070"), "32", 63),
        "liu06_t5_yn4_s": (Decimal("34.2"), "1.0", 66),
    }
    for observation_id, (point, sigma, page) in expected_migrated.items():
        row = by_suffix[observation_id]
        assert row.value.point == point
        assert row.uncertainty.kind.value == "printed"
        assert row.uncertainty.verbatim["value"] == sigma
        assert row.uncertainty.verbatim["confidence_level"] == "one_sigma"
        assert row.locator.page == page

    unknown_quantity_ids = {
        key.split("::", 1)[1]
        for key, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    expected_unknown_quantity_ids = {
        f"liu06_app1_{row['values']['source_row_as_published']['Source']}_ln_s"
        for row in appendix
    }
    assert unknown_quantity_ids == expected_unknown_quantity_ids

    measured = [row for key, row in migrated.items() if "::liu06_t4_" in key or "::liu06_t5_" in key]
    assert len(measured) == 291
    assert all(row.identity.quantity.is_value for row in measured)
    assert all(row.identity.phase_role is not None and row.identity.phase_role.is_value for row in measured)
    unknown_composition_ids = {
        key.split("::", 1)[1]
        for key, row in migrated.items()
        if ("::liu06_t4_" in key or "::liu06_t5_" in key)
        and row.identity.composition is not None
        and row.identity.composition.is_unknown
    }
    assert unknown_composition_ids == {
        f"liu06_t4_yn73_{formula}"
        for formula in (
            "sio2",
            "tio2",
            "al2o3",
            "feo",
            "mno",
            "mgo",
            "cao",
            "na2o",
            "k2o",
            "p2o5",
            "s",
        )
    }
    assert all(
        row.identity.composition is not None and row.identity.composition.is_value
        for key, row in migrated.items()
        if ("::liu06_t4_" in key or "::liu06_t5_" in key)
        and key.split("::", 1)[1] not in unknown_composition_ids
    )
    incomplete = source["liu06_t4_yn73_sio2"]["point_conditions"]["composition"]
    assert incomplete["state"]["tag"] == "unknown"
    assert "MnO=<d.l." in incomplete["state"]["reason"]
    assert "P2O5=<d.l." in incomplete["state"]["reason"]
    assert {
        row.identity.phase_role.value.value for row in measured
    } == {"glass", "matte"}

    for axis, expected_unknown_runs in {
        "temperature_K": {"yn38", "yn49"},
        "total_pressure_Pa": {"yn38", "yn49"},
        "fO2_Pa": {"yn38", "yn49"},
    }.items():
        unknown_runs = {
            _run_id(key.split("::", 1)[1])
            for key, row in migrated.items()
            if ("::liu06_t4_" in key or "::liu06_t5_" in key)
            and getattr(row.identity, axis).is_unknown
        }
        assert unknown_runs == expected_unknown_runs

    yn40 = by_suffix["liu06_t4_yn40_sio2"]
    assert yn40.identity.temperature_K.value == Decimal("1703")
    assert yn40.identity.total_pressure_Pa.value == Decimal("1000000000")
    assert yn40.identity.fO2_Pa.value == Decimal("0.003388441561392025470482735905")
    yn38 = next(row for key, row in migrated.items() if key.endswith("::liu06_t4_yn38_sio2"))
    assert yn38.identity.composition.is_value
    yn49 = next(row for key, row in migrated.items() if key.endswith("::liu06_t5_yn49_s"))
    assert yn49.identity.composition.is_value
    assert yn49.identity.composition.value.amount_basis.value == "mole_fraction"

    app_yn38 = source["liu06_app1_yn38_ln_s"]
    assert app_yn38["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
    assert app_yn38["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert app_yn38["point_conditions"]["fO2_Pa"]["locator"]["page"] == 83
