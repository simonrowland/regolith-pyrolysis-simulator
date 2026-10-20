"""Printed antimony activity and deferred figures for Tsukihashi et al. (1985)."""

from __future__ import annotations

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = REPO_ROOT / "data/literature/extracts/tsukihashi-1985-v-nb-sb-soda-silicate-distribution.yaml"


def test_printed_antimony_activity_coefficient_fit_is_preserved_as_context() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert not result.validation.hard_issues
    assert not result.observations
    activity = next(row for row in document["species"]["Sb"]["context"] if row["observation_id"] == "tsukihashi_1985_antimony_activity_coefficient_fit")
    assert activity["values"]["gamma_as_printed"] == "0.45"
    assert activity["values"]["temperature_as_printed"] == "1200 °C"
    assert activity["values"]["oxygen_partial_pressure_as_printed"] == "8.42×10^-18 atm"


def test_distribution_and_activity_figures_are_deferred_without_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    figures = [
        record
        for body in document["species"].values()
        for record in body.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert {record["locator"]["figure"] for record in figures} == {str(i) for i in range(2, 13)}
    assert all(record["numeric_points"] == "none" for record in figures)
    assert all(record["digitisation"] == "deferred to a figure pass" for record in figures)
    assert all(record["series"] and all(isinstance(s["marker_count"], int) for s in record["series"]) for record in figures)
