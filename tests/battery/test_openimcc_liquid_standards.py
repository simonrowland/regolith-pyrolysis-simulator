from __future__ import annotations

from decimal import Decimal
from pathlib import Path
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
from tests.battery import factories as F


def _raise_openimcc_policy_refusal(**_kwargs: Any) -> None:
    raise openimcc_bridge.OpenImccCompositionPolicyRefusal(
        "openimcc_liquid_standard_missing",
        "invalid_input; fail_closed_category=1; oxide=K2O",
        diagnostics={
            "invalid_input": True,
            "category": 1,
            "standard_failures": [{
                "oxide": "K2O",
                "kind": "missing-standard",
                "standard_id": None,
            }],
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


def _expected_standard_notice_reasons(
    melt_pack: object,
    composition: dict[str, float],
    *,
    redox_pack: object | None = None,
) -> set[str]:
    assessment = assess_liquid_standards(
        melt_pack, redox_pack, None, composition
    )
    assert not assessment.refusals
    assert assessment.flags
    return {flag.reason for flag in assessment.flags}


def _prediction_standard_notice_reasons(prediction: Any) -> set[str]:
    return {
        notice.reason
        for notice in prediction.notices
        if notice.origin == "openimcc.check_pack_standards"
    }


def test_sf04_k2o_mismatch_is_a_flag_not_a_refusal() -> None:
    assessment = assess_liquid_standards(
        _load_pack("published-core"),
        load_redox_pack(),
        None,
        {"K2O": 1.0, "SiO2": 1.0},
    )

    assert not assessment.refusals
    assert any(
        flag.kind == "reference-unknown"
        and flag.oxide == "K2O"
        for flag in assessment.flags
    )


def test_redox_consumer_flags_melt_pack_standard_mismatch() -> None:
    assessment = assess_liquid_standards(
        _load_pack("published-core"),
        load_redox_pack(),
        None,
        {"K2O": 1.0, "SiO2": 1.0},
    )

    assert not assessment.refusals
    assert any(
        flag.kind == "reference-unknown" and flag.oxide == "K2O"
        for flag in assessment.flags
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


def test_sossi_cohort_result_carries_liquid_standard_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml

    root = Path(__file__).resolve().parents[2]
    source = yaml.safe_load(
        (root / "data/literature/extracts/kems-012-sossi-2019.yaml").read_text()
    )
    experiment = source["experiments"][0]
    experiment_id = experiment["experiment_id"]
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }

    monkeypatch.setattr(openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "default")
    monkeypatch.setattr(
        residue,
        "_evaluate_openimcc_isolated",
        lambda evaluate, *, timeout_s, **kwargs: evaluate(**kwargs),
    )
    monkeypatch.setattr(
        residue,
        "integrate_residue_inventory",
        lambda inventory, *_args, **_kwargs: SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        ),
    )

    prediction = residue._predict_sossi_residue_cohort(
        [experiment],
        yaml.safe_load((root / "data/vapor_pressures.yaml").read_text()),
        starting_trace_ppm_by_experiment={experiment_id: starting_trace},
        buffered_fO2_log_by_experiment={experiment_id: -10.0},
        code_revision="sossi-liquid-standard-flags-test",
        engine="openimcc",
    )[0]

    flags = prediction.provenance["liquid_standard_flags"]
    assert any(
        flag["kind"] == "non-source-basis"
        and flag["oxide"] == "FeO"
        and flag["row_key"] == "FeTiO3"
        and flag["basis"]
        and flag["citation"]
        for flag in flags
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


def test_score_melt_unsupported_inventory_basis_still_refuses(
    monkeypatch: Any,
) -> None:
    components = {"K2O": 1.0, "SiO2": 1.0}
    composition = Composition(
        basis="mole_inventory",
        components=tuple(
            (oxide, Decimal(str(amount)))
            for oxide, amount in components.items()
        ),
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    experiment = SimpleNamespace(
        sample=SimpleNamespace(
            initial_composition=SimpleNamespace(state=State.of(composition))
        )
    )
    monkeypatch.setattr(
        openimcc_bridge, "DEFAULT_BACKEND_PACK_NAME", "published-core"
    )

    prediction = score._predict_mass_loss_with_residue(
        Engine.OPENIMCC,
        SimpleNamespace(temperature_K=State.of(Decimal("1800"))),
        "test-channel",
        ("openimcc",),
        experiment,
        SimpleNamespace(source_id="liquid-standard-refusal-test"),
    )

    assert prediction.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert prediction.refusal_detail["reason"] == "initial_composition_basis_not_supported"


def _openimcc_k_pressure_observation(observation_id: str) -> Any:
    from simulator.battery.enums import (
        AdmissionStatus,
        EvidenceClass,
        Phase,
        Quantity,
        UncertaintyKind,
        ValueKind,
    )
    from simulator.battery.identity import Identity
    from simulator.battery.records import (
        Admission,
        Evidence,
        Observation,
        Species,
        Uncertainty,
        Value,
    )

    composition = Composition(
        basis="oxide_wt_pct",
        components=(("K2O", Decimal("20")), ("SiO2", Decimal("80"))),
        amount_basis=AmountBasis.MASS_PERCENT,
    )
    identity = Identity(
        quantity=Quantity.P_PARTIAL,
        species=Species("K", Phase.G),
        temperature_K=State.of(Decimal("1800")),
        total_pressure_Pa=State.of(Decimal("101325")),
        fO2_Pa=State.of(Decimal("1e-6")),
        composition=State.of(composition),
    )
    observation = Observation(
        observation_id=observation_id,
        experiment_id=observation_id,
        identity=identity,
        value=Value(ValueKind.POINT, point=Decimal("1e-6")),
        uncertainty=Uncertainty(UncertaintyKind.NONE),
        evidence=Evidence(State.of(EvidenceClass.MEASURED_DIRECT)),
        admission=Admission(AdmissionStatus.ADMITTED, "test"),
        notices=(),
    )
    return observation


def test_score_preflight_result_carries_standard_conversion_notice(
    monkeypatch: Any,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )

    observation = _openimcc_k_pressure_observation(
        "liquid-standard-preflight-refusal"
    )
    pack = _load_pack("published-core")
    backend = _OpenImccBatteryBackend("openimcc")
    backend._pack = pack
    handle = SimpleNamespace(
        name=Engine.OPENIMCC.value,
        available=True,
        backend=backend,
        identity=backend._identity,
    )

    prediction = score._predict_with_engine(
        Engine.OPENIMCC,
        observation,
        handles={Engine.OPENIMCC.value: handle},
    )

    assert prediction.value is not None, prediction.refusal_detail
    conversion_notice = next(
        notice
        for notice in prediction.notices
        if notice.reason.startswith("openimcc_liquid_standard_conversion;")
    )
    assert "oxide=K2O" in conversion_notice.reason
    assert "standard_id=FC87-Glushko-K2O" in conversion_notice.reason
    assert "active_standard_id=K-012" in conversion_notice.reason
    assert "temperature_K=1800.0" in conversion_notice.reason
    assert "log10_offset=" in conversion_notice.reason
    assert conversion_notice.origin == "engine:openimcc"


def test_scored_missing_openimcc_liquid_standard_preserves_category_one_refusal(
    monkeypatch: Any,
) -> None:
    from simulator.battery.enums import ExecutionState
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )
    from simulator.melt_backend.openimcc_liquid_standards import (
        LiquidStandardMissingError,
    )

    observation = _openimcc_k_pressure_observation("missing-liquid-standard")
    backend = _OpenImccBatteryBackend("openimcc")
    backend._pack = _load_pack("default")
    error = LiquidStandardMissingError(
        "K2O", "missing-K2O-standard", "no G0(T) row or recorded translation"
    )
    monkeypatch.setattr(
        backend,
        "equilibrate",
        lambda **_kwargs: (_ for _ in ()).throw(error),
    )
    handle = SimpleNamespace(
        name=Engine.OPENIMCC.value,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
        backend=backend,
        identity=backend._identity,
    )

    prediction = score._predict_with_engine(
        Engine.OPENIMCC,
        observation,
        handles={Engine.OPENIMCC.value: handle},
    )

    assert prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert prediction.execution.state is ExecutionState.NOT_PROBED
    assert prediction.refusal_detail["reason"] == "missing_input"
    assert prediction.refusal_detail["category"] == 1
    assert prediction.refusal_detail["invalid_input"] is True
    failure = prediction.refusal_detail["standard_failures"][0]
    assert failure["standard_id"] == "missing-K2O-standard"


def test_closed_redox_result_carries_dependent_standard_notices(
    monkeypatch: Any,
) -> None:
    components = {"FeO": 1.0, "K2O": 1.0, "SiO2": 1.0}
    composition = Composition(
        basis="oxide_inventory",
        components=tuple(
            (oxide, Decimal(str(amount)))
            for oxide, amount in components.items()
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    pack = _load_pack("published-core")
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: pack)
    identity = F.activity_identity(
        formula="FeO",
        T_K=Decimal("1800"),
        composition=composition,
    )

    prediction = score._predict_distin_closed_feo_activity(
        Engine.OPENIMCC,
        SimpleNamespace(source_id="distin-closed-redox-standard-refusal"),
        identity,
        "test-channel",
        ("openimcc",),
    )

    if prediction.value is None:
        assert prediction.refusal_reason is RefusalReason.METRIC_DOMAIN
        assert prediction.refusal_detail.get("category") != 2
    assert _prediction_standard_notice_reasons(prediction) == (
        _expected_standard_notice_reasons(
            pack, components, redox_pack=load_redox_pack()
        )
    )


def test_imposed_redox_result_carries_dependent_standard_notices(
    monkeypatch: Any,
) -> None:
    components = {"FeO": 10.0, "K2O": 20.0, "SiO2": 70.0}
    composition = Composition(
        basis="oxide_wt_pct",
        components=tuple(
            (oxide, Decimal(str(amount)))
            for oxide, amount in components.items()
        ),
        amount_basis=AmountBasis.MASS_PERCENT,
    )
    pack = _load_pack("published-core")
    monkeypatch.setattr(openimcc_bridge, "_load_pack", lambda _name: pack)
    monkeypatch.setattr(
        score,
        "_fe_redox_material",
        lambda *_args: score._FeRedoxMaterial(
            temperature=Decimal("1800"),
            composition_state=State.of(composition),
            composition_wt=components,
        ),
    )
    monkeypatch.setattr(
        score,
        "_printed_fe_redox_fo2_log",
        lambda *_args: Decimal("-10"),
    )

    from simulator.battery.enums import Phase, Quantity
    from simulator.battery.identity import Identity
    from simulator.battery.records import Species

    identity = Identity(
        quantity=Quantity.FE3_FE2_RATIO,
        species=Species("Fe", Phase.CR),
        temperature_K=State.of(Decimal("1800")),
        composition=State.of(composition),
        fO2_Pa=State.of(Decimal("1e-6")),
    )
    prediction = score._predict_fe_redox(
        Engine.OPENIMCC,
        SimpleNamespace(source_id="imposed-redox-standard-refusal"),
        identity,
        "test-channel",
    )

    if prediction.value is None:
        assert prediction.refusal_reason is RefusalReason.METRIC_DOMAIN
        assert prediction.refusal_detail.get("category") != 2
    assert _prediction_standard_notice_reasons(prediction) == (
        _expected_standard_notice_reasons(pack, components)
    )


def test_isolated_residue_worker_preserves_typed_missing_standard_refusal() -> None:
    from simulator.battery.residue import _evaluate_openimcc_isolated

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as caught:
        _evaluate_openimcc_isolated(
            _raise_openimcc_policy_refusal, timeout_s=10.0
        )

    assert caught.value.code == "openimcc_liquid_standard_missing"
    assert caught.value.diagnostics["category"] == 1
    assert caught.value.diagnostics["standard_failures"] == [
        {"oxide": "K2O", "kind": "missing-standard", "standard_id": None}
    ]


def test_hashimoto_residue_mismatch_does_not_mask_missing_cohort(
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

    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "hashimoto_cohort_invalid"
    assert prediction.refusal_detail.get("category") != 2
