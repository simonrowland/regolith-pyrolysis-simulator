from __future__ import annotations

from decimal import Decimal

import pytest

from tests.battery import factories as F
from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    NoticeKind,
)
from simulator.battery.records import Composition
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    predict_with_engine,
)


@pytest.mark.parametrize(
    ("temperature_K", "fusion_authority", "notice_kind", "reason_fragment"),
    (
        (Decimal("3500"), Authority.CERTIFIED, None, None),
        (
            Decimal("418.15"),
            Authority.BRIDGE,
            NoticeKind.FALLBACK,
            "metastable (supercooled) liquid",
        ),
        (
            Decimal("4500"),
            Authority.EXTRAPOLATED,
            NoticeKind.OUT_OF_CERTIFIED_BAND,
            "terminal Gibbs chord",
        ),
    ),
)
def test_scored_mgo_fusion_verdict_keeps_its_authority_and_band(
    temperature_K: Decimal,
    fusion_authority: Authority,
    notice_kind: NoticeKind | None,
    reason_fragment: str | None,
) -> None:
    work = F.work()
    experiment = F.tabulation_experiment(
        experiment_id=f"mgo-fusion-{temperature_K}",
        work_id=work.work_id,
        T_K=temperature_K,
    )
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(("MgO", Decimal("0.6")), ("SiO2", Decimal("0.4"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula="MgO",
        T_K=temperature_K,
        component_basis="MgO",
        composition=composition,
    )
    reference = F.observation(
        f"mgo-fusion-residual-{temperature_K}",
        experiment.experiment_id,
        identity,
        Decimal("0.5"),
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        reference,
        experiment=experiment,
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={"janaf-4th": "reviewed"},
        hostname="test",
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={reference.observation_id},
        prediction=prediction,
    )

    assert prediction.certified_band == {
        "temperature_K": (Decimal("298.15"), Decimal("4000"))
    }
    fusion_notices = tuple(
        notice
        for notice in residual.notices
        if "d144_fusion_conversion" in notice.reason
    )
    if notice_kind is None:
        assert fusion_notices == ()
        return

    assert len(fusion_notices) == 1
    notice = fusion_notices[0]
    assert notice.kind is notice_kind
    assert notice.authority is fusion_authority
    assert reason_fragment in notice.reason
    assert notice.band == "[298.15, 4000] K"
