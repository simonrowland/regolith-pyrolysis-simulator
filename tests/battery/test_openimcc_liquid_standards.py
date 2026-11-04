from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import openimcc
import pytest
from openimcc.redox_pack import load_redox_pack

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    RefusalReason,
)
from simulator.battery.records import Composition, State
from simulator.battery import residue
from simulator.battery import score
from simulator.melt_backend import openimcc_bridge
from simulator.melt_backend.openimcc_bridge import _load_pack
from simulator.melt_backend.openimcc_liquid_standards import assess_liquid_standards


def _raise_openimcc_policy_refusal(**_kwargs: Any) -> None:
    raise openimcc_bridge.OpenImccCompositionPolicyRefusal(
        "openimcc_liquid_standard_refused",
        "invalid_input; fail_closed_category=2; oxide=K2O",
        diagnostics={
            "invalid_input": True,
            "category": 2,
            "standard_failures": [{"oxide": "K2O", "kind": "mismatch"}],
        },
    )


def _default_composition() -> dict[str, float]:
    pack = _load_pack("default")
    return {oxide: 1.0 for oxide in pack.parent_oxides} | {"MnO": 1.0}


def _flag_facts(flags: tuple[Any, ...]) -> set[tuple[str, str, str, str]]:
    return {
        (flag.pack, flag.oxide, flag.kind, flag.row_key)
        for flag in flags
    }


def test_sf04_k2o_mismatch_is_typed_fail_closed_refusal() -> None:
    assessment = assess_liquid_standards(
        _load_pack("published-core"),
        load_redox_pack(),
        None,
        {"K2O": 1.0, "SiO2": 1.0},
    )

    assert not assessment.flags
    assert any(
        failure.kind == "mismatch"
        and failure.oxide == "K2O"
        and failure.declared_standard_id
        and failure.active_standard_id
        and failure.declared_standard_id != failure.active_standard_id
        for failure in assessment.refusals
    )


def test_redox_consumer_refuses_melt_pack_standard_mismatch() -> None:
    assessment = assess_liquid_standards(
        _load_pack("published-core"),
        load_redox_pack(),
        None,
        {"K2O": 1.0, "SiO2": 1.0},
    )

    assert any(
        failure.kind == "mismatch" and failure.oxide == "K2O"
        for failure in assessment.refusals
    )


def test_default_packs_allow_scoring_and_flag_only_active_rows() -> None:
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

    melt_assessment = assess_liquid_standards(
        melt_pack, None, None, _default_composition()
    )
    redox_assessment = assess_liquid_standards(
        melt_pack, redox_pack, None, _default_composition()
    )
    assert not melt_assessment.refusals
    assert not redox_assessment.refusals

    melt_facts = _flag_facts(melt_assessment.flags)
    facts = _flag_facts(redox_assessment.flags)
    assert len(melt_facts) == 10
    assert melt_facts.issubset(facts)
    unknown_rows = {
        row_key
        for _pack, _oxide, kind, row_key in facts
        if kind == "reference-unknown"
    }
    assert unknown_rows == {"K2SiO3", "K2Si2O5", "K2Si4O9"}

    non_source_rows = {
        (pack, oxide, row_key)
        for pack, oxide, kind, row_key in facts
        if kind == "non-source-basis"
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
        flag.standard_id and flag.basis and flag.citation
        for flag in redox_assessment.flags
    )


def test_missing_reaction_oxide_suppresses_that_rows_notice() -> None:
    composition = _default_composition()
    composition.pop("SiO2")
    assessment = assess_liquid_standards(
        _load_pack("default"), None, None, composition
    )

    assert not assessment.refusals
    assert "K2SiO3" not in {
        flag.row_key
        for flag in assessment.flags
        if flag.kind == "reference-unknown"
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


def test_isolated_residue_worker_preserves_typed_liquid_standard_refusal() -> None:
    from simulator.battery.residue import _evaluate_openimcc_isolated

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        _evaluate_openimcc_isolated(
            _raise_openimcc_policy_refusal, timeout_s=10.0
        )

    assert caught.value.code == "openimcc_liquid_standard_refused"
    assert caught.value.diagnostics["category"] == 2
    assert caught.value.diagnostics["standard_failures"] == [
        {"oxide": "K2O", "kind": "mismatch"}
    ]


def test_hashimoto_residue_route_refuses_melt_pack_mismatch(
    monkeypatch: Any,
) -> None:
    mismatch_pack = _load_pack("published-core")
    monkeypatch.setattr(
        openimcc_bridge, "_load_pack", lambda _name: mismatch_pack
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
