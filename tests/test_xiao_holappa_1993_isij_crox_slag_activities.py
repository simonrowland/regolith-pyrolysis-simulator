from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from simulator.battery.migrate import Migrator, REPO_ROOT


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/xiao-holappa-1993-isij-crox-slag-activities.yaml"
)


def _assert_figure_inventory(document: dict) -> None:
    all_contexts = [
        row
        for species in document["species"].values()
        for row in species["context"]
    ]
    contexts = [row for row in all_contexts if row["type"] == "figure_only"]
    assert len(contexts) == 22
    equations = {row["observation_id"]: row for row in all_contexts if row["type"] == "equation_context"}
    assert len(equations) == 4
    assert equations["xiao_holappa_1993_oxygen_pressure_equation_15"]["values"]["equation_as_printed"] == "log(Po2/atm) = 8.613 − 3.87 × 10^4/(T/K)"
    assert equations["xiao_holappa_1993_electronic_pressure_equation_17"]["values"]["equation_as_printed"] == "log(Pe/atm) = 20.40 − 6.45 × 10^4/(T/K)"
    assert equations["xiao_holappa_1993_cr_o_formation_gibbs_equation_18"]["values"]["equation_as_printed"] == "ΔG° = −79880 + 15.25 T (cal)"
    assert equations["xiao_holappa_1993_cr_oxide_formation_gibbs_equation"]["values"]["equation_as_printed"] == "ΔG°3 = −132665 + 29.555 T (cal)"
    conditions = next(row for row in all_contexts if row["observation_id"] == "xiao_holappa_1993_crucible_charge_and_gas_conditions")
    assert conditions["values"]["chromium_crucible_dimensions_as_printed"] == "28 mm i.d.; 32 mm o.d.; 50 mm h"
    assert conditions["values"]["alloy_charge"] == "16 g CrAg alloy"
    assert conditions["values"]["slag_charge"] == "30 g slag"
    assert conditions["values"]["argon_flow_rate"] == "6 l/h"
    assert {row["locator"]["figure"] for row in contexts} == {
        str(index) for index in range(1, 23)
    }
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        for row in contexts
    )
    assert all(
        "marker_count" in series
        for row in contexts
        for series in row["values"]["series"]
    )
    assert all(not species["observations"] for species in document["species"].values())


def test_xiao_holappa_figures_are_deferred_and_unscored() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_figure_inventory(document)

    mutated = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated["species"]["CrO"]["context"].pop()
    with pytest.raises(AssertionError):
        _assert_figure_inventory(mutated)

    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert migrator.result.observations == {}
    assert sum(len(rows) for rows in migrator.result.context_by_work.values()) == 27
