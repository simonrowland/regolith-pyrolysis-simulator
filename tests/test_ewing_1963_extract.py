"""Regression pins for Ewing (1963) extract migration and source readings."""

from pathlib import Path

import yaml

from simulator.battery.enums import (
    Engine,
    EvidenceClass,
    MethodToken,
    NoticeKind,
    Phase,
)
from simulator.battery.migrate import Migrator
from simulator.battery.score import ScoreContext, score_store


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ewing-1963-sodium-potassium-properties.yaml"


def _extract_document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_ewing_figure_only_evidence_and_scoring_notice():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    observations = tuple(result.observations.values())
    assert len(observations) == 43
    assert all(
        o.evidence.class_.value is EvidenceClass.FIGURE_ONLY
        for o in observations
    )
    assert len({o.experiment_id for o in observations}) == 1
    experiment = result.experiments[observations[0].experiment_id]
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.STATIC_EQUILIBRIUM
    assert all(
        o.identity.reservoir.is_value
        and o.identity.reservoir.value.formula == "Na"
        and o.identity.reservoir.value.phase.value is Phase.L
        for o in observations
    )

    residuals, _ = score_store(
        ScoreContext(
            works=result.works,
            experiments=result.experiments,
            observations=result.observations,
        ),
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=True,
    )
    assert len(residuals) == 43
    assert all(
        any(notice.kind is NoticeKind.FIGURE_ONLY for notice in residual.notices)
        for residual in residuals
    )
    assert all(residual.numeric is not None for residual in residuals)


def test_ewing_source_locators_and_table_1_readings():
    doc = _extract_document()
    na = doc["species"]["Na"]
    figure = na["observations"][0]
    table1, table3 = na["context"][:2]

    assert figure["locator"]["pdf_page_index"] == 11
    assert figure["locator"]["figure"] == "6"
    assert table1["locator"]["pdf_page_index"] == 12
    assert table1["locator"]["table"] == "1"
    assert table3["locator"]["pdf_page_index"] == 14
    assert table3["locator"]["table"] == "3"

    points1 = table1["values"]["nrl_measured_points"]
    points3 = table3["values"]["nrl_measured_points"]
    assert [(p["T_C"], p["specific_heat_cal_g_C"]) for p in points1] == [
        (315.9, 0.314),
        (599.5, 0.301),
        (723.9, 0.291),
        (826.4, 0.297),
        (920.5, 0.313),
        (1008.9, 0.324),
        (1109.6, 0.325),
    ]
    assert [(p["T_C"], p["specific_heat_cal_g_C"]) for p in points3] == [
        (155.2, 0.325),
        (255.3, 0.315),
        (352.0, 0.312),
        (449.7, 0.304),
        (555.3, 0.297),
        (652.6, 0.300),
    ]
    assert "probable error of the NRL measurement as ±3%" in table1["values"]["method_and_conditions"]
