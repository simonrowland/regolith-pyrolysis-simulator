"""Source and migration pins for Shilov et al. Table 2."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "shilov-2026-sro-la2o3-kems"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.pdf"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"
TABLE_2 = (
    ("1", "1740", "0.40", "0.06", "0.20", "0.08"),
    ("1", "1777", "0.64", "0.10", "0.18", "0.07"),
    ("1", "1809", "0.97", "0.15", "0.18", "0.07"),
    ("1", "1832", "1.29", "0.19", "0.17", "0.06"),
    ("1", "1855", "1.7", "0.3", "0.16", "0.06"),
    ("2", "1623", "0.060", "0.009", "0.19", "0.07"),
    ("2", "1617", "0.050", "0.008", "0.17", "0.06"),
    ("2", "1662", "0.08", "0.01", "0.11", "0.04"),
    ("2", "1696", "0.18", "0.03", "0.17", "0.06"),
    ("2", "1743", "0.29", "0.04", "0.12", "0.04"),
    ("2", "1775", "0.42", "0.06", "0.10", "0.04"),
    ("2", "1770", "0.39", "0.06", "0.10", "0.04"),
)


def test_table_2_cells_and_every_migrated_point_keep_their_identity() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"source PDF is absent: {SOURCE}")

    source_text = subprocess.run(
        ["pdftotext", "-f", "7", "-l", "7", "-layout", str(SOURCE), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Table 2." in source_text
    collapsed_source = re.sub(r"\s+", " ", source_text)
    for _sample, temperature, pressure, pressure_sigma, activity, activity_sigma in TABLE_2:
        assert (
            f"{temperature} {pressure} ± {pressure_sigma} {activity} ± {activity_sigma}"
            in collapsed_source
        )

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_pressure = Counter(
        (sample, int(t), Decimal(p), Decimal(sigma))
        for sample, t, p, sigma, _a, _a_sigma in TABLE_2
    )
    expected_activity = Counter(
        (sample, int(t), Decimal(a), Decimal(sigma))
        for sample, t, _p, _p_sigma, a, sigma in TABLE_2
    )
    extract_pressure: Counter[tuple[str, int, Decimal, Decimal]] = Counter()
    for observation in extract["species"]["Sr"]["observations"]:
        if observation["values"].get("quantity") != "partial_pressure":
            continue
        assert observation["values"]["formula"] == "Sr"
        sample = str(observation["sample_id_as_printed"])
        for point in observation["values"]["points"]:
            extract_pressure[
                (
                    sample,
                    int(point["T_K"]),
                    Decimal(point["P_Pa"]),
                    Decimal(point["sigma"]),
                )
            ] += 1
    assert extract_pressure == expected_pressure

    extract_activity: Counter[tuple[str, int, Decimal, Decimal]] = Counter()
    for observation in extract["species"]["SrO"]["observations"]:
        if (
            observation["values"].get("quantity") != "activity"
            or "points" not in observation["values"]
            or "sample_id_as_printed" not in observation
        ):
            continue
        assert observation["values"]["formula"] == "SrO"
        sample = str(observation["sample_id_as_printed"])
        for point in observation["values"]["points"]:
            extract_activity[
                (
                    sample,
                    int(point["T_K"]),
                    Decimal(point["activity"]),
                    Decimal(point["sigma"]),
                )
            ] += 1
    assert extract_activity == expected_activity

    figures = [
        record
        for species in extract["species"].values()
        for record in species.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 5
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        and "points" not in record["values"]
        for record in figures
    )
    figure_2 = next(record for record in figures if record["locator"].get("figure") == "2")
    assert [series["marker_count"] for series in figure_2["values"]["series"]] == [
        5,
        7,
        6,
        0,
    ]

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    migrated = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SID
    ]
    assert len(migrated) == 29
    assert not [
        (observation.observation_id, issue)
        for observation in migrated
        for issue in observation.notices
    ]

    migrated_pressure: Counter[tuple[int, Decimal, Decimal]] = Counter()
    migrated_activity: Counter[tuple[int, Decimal, Decimal]] = Counter()
    migrated_enthalpy: Counter[tuple[int, Decimal]] = Counter()
    for observation in migrated:
        identity = observation.identity
        quantity = identity.quantity.value
        assert observation.value.kind is ValueKind.POINT
        assert identity.temperature_K.is_value
        temperature = int(identity.temperature_K.value)
        assert identity.composition.is_value
        identity_composition = dict(identity.composition.value.components)
        conditions = observation.point_conditions or {}
        assert "temperature_K" in conditions, observation.observation_id
        assert "composition" in conditions, observation.observation_id
        temperature_condition = conditions["temperature_K"]
        composition_condition = conditions["composition"]
        assert temperature_condition.state.is_value, observation.observation_id
        assert composition_condition.state.is_value, observation.observation_id
        assert temperature_condition.locator is not None
        assert composition_condition.locator is not None
        assert temperature_condition.locator.page == observation.locator.page
        assert composition_condition.locator.page is not None
        assert dict(composition_condition.state.value.components) == identity_composition
        x_sro = identity_composition["SrO"]

        if quantity is Quantity.P_PARTIAL:
            assert identity.species.formula == "Sr"
            assert identity.species.phase.is_value and identity.species.phase.value is Phase.G
            assert Decimal(str(temperature_condition.state.value.point)) == Decimal(temperature)
            migrated_pressure[(temperature, observation.value.point, x_sro)] += 1
        elif quantity is Quantity.ACTIVITY:
            assert identity.species.formula == "SrO"
            assert identity.species.phase.is_value and identity.species.phase.value is Phase.CR
            assert identity.reference_state.is_value
            reference = identity.reference_state.value
            assert reference.convention.value == "raoultian_pure_endmember"
            assert reference.endmember.formula == "SrO"
            assert reference.endmember.phase.is_value
            assert reference.endmember.phase.value is Phase.CR
            assert Decimal(str(temperature_condition.state.value.point)) == Decimal(temperature)
            migrated_activity[(temperature, observation.value.point, x_sro)] += 1
        elif quantity is Quantity.PARTIAL_MOLAR_ENTHALPY:
            assert identity.species.formula == "SrO"
            assert Decimal(str(temperature_condition.state.value.point)) == Decimal(temperature)
            migrated_enthalpy[(temperature, observation.value.point)] += 1
        else:
            pytest.fail(f"unexpected migrated quantity: {quantity}")

    expected_migrated_pressure: Counter[tuple[int, Decimal, Decimal]] = Counter()
    expected_migrated_activity: Counter[tuple[int, Decimal, Decimal]] = Counter()
    for sample, temperature, pressure, _p_sigma, activity, _a_sigma in TABLE_2:
        x_sro = Decimal("0.90") if sample == "1" else Decimal("0.50")
        expected_migrated_pressure[(int(temperature), Decimal(pressure), x_sro)] += 1
        expected_migrated_activity[(int(temperature), Decimal(activity), x_sro)] += 1
    expected_migrated_activity.update(
        {
            (1750, Decimal("0.13"), Decimal("0.168")): 1,
            (1900, Decimal("0.15"), Decimal("0.88")): 1,
            (1900, Decimal("0.07"), Decimal("0.49")): 1,
        }
    )
    assert migrated_pressure == expected_migrated_pressure
    assert migrated_activity == expected_migrated_activity
    assert migrated_enthalpy == Counter(
        {(1798, Decimal("49")): 1, (1696, Decimal("80")): 1}
    )
