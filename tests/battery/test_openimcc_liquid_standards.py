from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

import openimcc
from openimcc.redox_pack import load_redox_pack

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import Composition, State
from simulator.battery import residue
from simulator.battery import score
from simulator.melt_backend.openimcc_bridge import _load_pack


def _resolve_standards() -> Callable[..., Any]:
    resolver = getattr(
        score, "_resolve_openimcc_liquid_reference_standards", None
    )
    assert callable(resolver), "OpenIMCC liquid-reference owner is missing"
    return resolver


def _default_composition() -> dict[str, float]:
    pack = _load_pack("default")
    return {oxide: 1.0 for oxide in pack.parent_oxides} | {"MnO": 1.0}


def _notice_facts(notices: tuple[Any, ...]) -> list[str]:
    assert all(notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT for notice in notices)
    return [notice.reason for notice in notices]


def test_sf04_k2o_mismatch_is_typed_fail_closed_refusal() -> None:
    resolve = _resolve_standards()
    assessment = resolve(
        {"K2O": 1.0, "SiO2": 1.0},
        Quantity.ACTIVITY,
        consumer="melt",
        melt_pack=_load_pack("published-core"),
        redox_pack=load_redox_pack(),
    )

    assert assessment.refusal_reason is RefusalReason.METRIC_DOMAIN
    reason = str(assessment.refusal_detail["reason"])
    assert "oxide=K2O" in reason
    assert "pack=IMCC-SF04 1.0.2" in reason
    assert assessment.refusal_detail["category"] == 2
    failures = assessment.refusal_detail["standard_failures"]
    assert any(
        failure["kind"] == "mismatch"
        and failure["oxide"] == "K2O"
        and failure["declared_standard_id"]
        and failure["active_standard_id"]
        and failure["declared_standard_id"] != failure["active_standard_id"]
        for failure in failures
    )
    assert assessment.notices == ()


def test_redox_consumer_refuses_melt_pack_standard_mismatch() -> None:
    assessment = _resolve_standards()(
        {"K2O": 1.0, "SiO2": 1.0},
        Quantity.FE3_FE2_RATIO,
        consumer="redox",
        melt_pack=_load_pack("published-core"),
        redox_pack=load_redox_pack(),
    )

    assert assessment.refusal_reason is RefusalReason.METRIC_DOMAIN
    assert assessment.refusal_detail["category"] == 2
    failures = assessment.refusal_detail["standard_failures"]
    assert any(
        failure["kind"] == "mismatch" and failure["oxide"] == "K2O"
        for failure in failures
    )


def test_default_packs_allow_scoring_and_flag_only_active_rows() -> None:
    resolve = _resolve_standards()
    melt_pack = _load_pack("default")
    redox_pack = load_redox_pack()
    records = openimcc.check_pack_standards(
        melt_pack, redox_pack=redox_pack
    )
    assert not any(
        record.kind in {"mismatch", "undeclared", "redox-mismatch"}
        for record in records
    )
    unknown_records = {
        record.row_key
        for record in records
        if record.kind == "reference-unknown"
    }
    active_rows = set(melt_pack.kernel_datapack.reactions)
    assert unknown_records == {
        "K2SiO3",
        "K2Si2O5",
        "K2Si4O9",
        "KCaAlSi2O7",
    }
    # KCaAlSi2O7 has a registry marker but is inactive in this loaded pack.
    assert "KCaAlSi2O7" not in active_rows
    assert len(unknown_records & active_rows) == 3
    assert sum(record.kind == "non-source-basis" for record in records) == 3

    melt_assessment = resolve(
        _default_composition(),
        Quantity.ACTIVITY,
        consumer="melt",
        melt_pack=melt_pack,
        redox_pack=redox_pack,
    )
    redox_assessment = resolve(
        _default_composition(),
        Quantity.ACTIVITY,
        consumer="redox",
        melt_pack=melt_pack,
        redox_pack=redox_pack,
    )
    assert melt_assessment.refusal_reason is None
    assert redox_assessment.refusal_reason is None

    melt_facts = _notice_facts(melt_assessment.notices)
    facts = _notice_facts(redox_assessment.notices)
    assert len(melt_facts) == 10
    assert set(melt_facts).issubset(facts)
    unknown_rows = {
        fact.split("row=", 1)[1].split(";", 1)[0]
        for fact in facts
        if "kind=reference-unknown" in fact
    }
    assert unknown_rows == {"K2SiO3", "K2Si2O5", "K2Si4O9"}

    non_source_rows = {
        (
            fact.split("pack=", 1)[1].split(";", 1)[0],
            fact.split("oxide=", 1)[1].split(";", 1)[0],
            fact.split("row=", 1)[1].split(";", 1)[0],
        )
        for fact in facts
        if "kind=non-source-basis" in fact
    }
    assert non_source_rows == {
        ("IMCC-SF04-D066 1.0.3-d066-v1", "K2O", "KAlSiO4"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "K2O", "KAlSi3O8"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "K2O", "KAlO2"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "K2O", "KAlSi2O6"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "FeO", "FeTiO3"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "FeO", "Fe2SiO4"),
        ("IMCC-SF04-D066 1.0.3-d066-v1", "FeO", "FeAl2O4"),
        ("openimcc-redox-pack 1.4.0", "MnO", "MnO(l)"),
        ("openimcc-redox-pack 1.4.0", "MnO", "MnSiO3(l)"),
        ("openimcc-redox-pack 1.4.0", "MnO", "Mn2SiO4(l)"),
        ("openimcc-redox-pack 1.4.0", "MnO", "MnAl2O4(l)"),
    }
    assert len(facts) == 14
    assert all(
        "standard_id=" in fact and "basis=" in fact and "citation=" in fact
        for fact in facts
    )


def test_missing_reaction_oxide_suppresses_that_rows_notice() -> None:
    resolve = _resolve_standards()
    composition = _default_composition()
    composition.pop("SiO2")
    assessment = resolve(
        composition,
        Quantity.ACTIVITY,
        consumer="melt",
        melt_pack=_load_pack("default"),
        redox_pack=load_redox_pack(),
    )

    assert assessment.refusal_reason is None
    assert "K2SiO3" not in {
        fact.split("row=", 1)[1].split(";", 1)[0]
        for fact in _notice_facts(assessment.notices)
        if "kind=reference-unknown" in fact
    }


def test_openimcc_residue_result_carries_liquid_standard_notices(
    monkeypatch: Any,
) -> None:
    pack = _load_pack("default")
    composition = Composition(
        basis="mole_inventory",
        components=tuple(
            (oxide, Decimal("1")) for oxide in (*pack.parent_oxides, "MnO")
        ),
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    experiment = SimpleNamespace(
        sample=SimpleNamespace(
            initial_composition=SimpleNamespace(state=State.of(composition))
        )
    )
    observation = SimpleNamespace(source_id="liquid-standard-residue-test")
    identity = SimpleNamespace(temperature_K=State.of(Decimal("1800")))
    residue_result = SimpleNamespace(
        engine_flags=(), engine_metadata={}, mass_loss_fraction=0.1
    )
    monkeypatch.setattr(score, "load_yaml", lambda _path: {})
    monkeypatch.setattr(
        residue, "predict_mass_loss_fraction", lambda *_args, **_kwargs: residue_result
    )

    prediction = score._predict_mass_loss_with_residue(
        Engine.OPENIMCC,
        identity,
        "test-channel",
        ("openimcc",),
        experiment,
        observation,
    )

    assert prediction.value == Decimal("0.1")
    assert any("row=K2SiO3;" in notice.reason for notice in prediction.notices)


def test_hashimoto_residue_route_refuses_melt_pack_mismatch(
    monkeypatch: Any,
) -> None:
    mismatch_pack = _load_pack("published-core")
    monkeypatch.setattr(
        openimcc,
        "load_datapack",
        lambda *_args, **_kwargs: mismatch_pack,
    )
    composition = Composition(
        basis="mole_inventory",
        components=(("K2O", Decimal("1")), ("SiO2", Decimal("1"))),
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    experiment_id = "hashimoto-test-run"
    experiment = SimpleNamespace(
        sample=SimpleNamespace(
            initial_composition=SimpleNamespace(state=State.of(composition))
        )
    )
    context = SimpleNamespace(
        experiments={experiment_id: experiment},
        observations={},
    )
    reference = SimpleNamespace(
        source_id="kems-015-hashimoto-1983",
        observation_id="hashimoto-test-row",
        experiment_id=experiment_id,
        identity=SimpleNamespace(),
    )

    prediction = score._hashimoto_residue_prediction(
        context, reference, Engine.OPENIMCC, {}
    )

    assert prediction.refusal_reason is RefusalReason.METRIC_DOMAIN
    assert prediction.refusal_detail["category"] == 2
    assert "oxide=K2O" in str(prediction.refusal_detail["reason"])
