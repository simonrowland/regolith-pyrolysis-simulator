"""Contract tests for the OpenIMCC SP-extension battery arm."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    NoticeKind,
    Quantity,
    Rail,
    RefusalReason,
)
from simulator.battery.oxygen_balance import (
    IMCC_ENGINES,
    OXYGEN_BALANCE_EFFUSION_ENGINES,
)
from simulator.battery.records import Composition
from simulator.battery.score import (
    ENGINE_CHANNELS,
    SCORE_ENGINE_SET,
    candidate_observation,
    compile_residual,
    load_score_context,
    predict_with_engine,
    residual_key,
)
from simulator.battery.source_lineage import coefficient_lineage_sources
from simulator.battery.waypoints import ENGINE_POINT_CONSUMERS, MELT_ACTIVITY_ENGINES
from simulator.diagnostic_helpers.binary_pot_battery import (
    _ENGINE_OUTER_TIMEOUT_S,
    BATTERY_ENGINE_NAMES,
    OPENIMCC_ENGINE_NAMES,
)
from simulator.battery.pins import PIN_CHANNEL_ENGINES, PinChannel
from tests.battery import factories as F


_LI_2005_FEO_ROW = (
    "li-2005-na-al-phosphate-fe-activities::"
    "li_2005_table_1_feo_activity_coefficient::T=1823:row=1-1:h=946adbc5ed6a"
)
_SP_PACK_SOURCE = "openimcc-pack-id:d066-ext"


def _sp_engine() -> Engine:
    return next(engine for engine in SCORE_ENGINE_SET if engine.value == "openimcc-sp")


def test_openimcc_sp_is_registered_as_a_distinct_battery_engine() -> None:
    engine = _sp_engine()

    assert engine is not Engine.OPENIMCC
    assert ENGINE_CHANNELS[engine] == "openimcc-sp"
    assert engine in IMCC_ENGINES
    assert engine in OXYGEN_BALANCE_EFFUSION_ENGINES
    assert "openimcc-sp" in BATTERY_ENGINE_NAMES
    assert "openimcc-sp" in OPENIMCC_ENGINE_NAMES
    assert _ENGINE_OUTER_TIMEOUT_S["openimcc-sp"] == _ENGINE_OUTER_TIMEOUT_S[
        "openimcc"
    ]
    assert "openimcc-sp" in ENGINE_POINT_CONSUMERS
    assert "openimcc-sp" in MELT_ACTIVITY_ENGINES
    assert PIN_CHANNEL_ENGINES[PinChannel.OPENIMCC_SP] is engine
    assert residual_key(
        reference_id="obs-1",
        quantity=Quantity.ACTIVITY,
        engine=engine,
        rail=Rail.MELT_ACTIVITY,
    ) == "obs-1::activity::melt_activity::openimcc-sp"


def test_sp_pack_id_is_provenance_not_a_coefficient_ancestor() -> None:
    assert coefficient_lineage_sources(
        ("openimcc-pack-id:d066-ext", "openimcc-v1.0.3-d066-v1")
    ) == ("openimcc-v1.0.3-d066-v1",)


def test_openimcc_sp_selects_the_extension_paired_with_default_d066() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _OpenImccBatteryBackend,
    )
    from simulator.melt_backend import openimcc_bridge

    default = _OpenImccBatteryBackend(Engine.OPENIMCC.value)
    sp_extension = _OpenImccBatteryBackend(Engine.OPENIMCC_SP.value)

    assert default._pack_name == openimcc_bridge.DEFAULT_BACKEND_PACK_NAME
    assert sp_extension._pack_name == "d066-ext"
    assert (
        sp_extension._pack_name
        == openimcc_bridge.OPENIMCC_SP_EXTENSION_PACK_NAME
    )


def test_li_2005_p2o5_feo_row_uses_the_sp_arm_or_refuses_with_identity() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    context = load_score_context(sources=("li-2005-na-al-phosphate-fe-activities",))
    reference = context.observations[_LI_2005_FEO_ROW]
    composition = reference.identity.composition.value
    assert any(name == "P2O5" and amount > 0 for name, amount in composition.components)

    default = predict_with_engine(Engine.OPENIMCC, reference, isolated=False)
    assert default.value is None
    assert default.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert default.refusal_detail["engine_reason"] == "imcc_sp_extension_required"

    prediction = predict_with_engine(_sp_engine(), reference, isolated=False)
    candidate = candidate_observation(reference, prediction)
    assert candidate.engine is not None
    assert _SP_PACK_SOURCE in candidate.engine.coefficient_sources
    assert candidate.authority is not None
    if prediction.value is None:
        assert isinstance(prediction.refusal_reason, RefusalReason)
        assert candidate.authority is Authority.REFUSED
    else:
        assert candidate.authority in {Authority.BRIDGE, Authority.EXTRAPOLATED}
    assert any(
        _SP_PACK_SOURCE in notice.reason
        and f"authority={candidate.authority.value}" in notice.reason
        for notice in prediction.notices
    )
    residual, _ = compile_residual(
        reference,
        _sp_engine(),
        context=context,
        prediction=prediction,
    )
    assert residual.key.endswith("::openimcc-sp")
    assert any(
        _SP_PACK_SOURCE in notice.reason
        and f"authority={candidate.authority.value}" in notice.reason
        for notice in residual.notices
    )
    gated_residual, gated_candidate = compile_residual(
        reference,
        _sp_engine(),
        context=replace(context, experiments={}),
    )
    assert gated_candidate is None
    assert any(
        _SP_PACK_SOURCE in notice.reason and "authority=refused" in notice.reason
        for notice in gated_residual.notices
    )


def test_openimcc_sp_outside_validated_envelope_is_flagged() -> None:
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    composition = Composition(
        basis="ordered_complete_mole_inventory",
        components=(
            ("K2O", Decimal("0.50001")),
            ("SiO2", Decimal("0.49999")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    identity = F.activity_identity(
        formula="SiO2",
        component_basis="SiO2",
        composition=composition,
        T_K=Decimal("1800"),
    )
    reference = F.observation(
        "openimcc-sp-outside-validated-envelope",
        F.tabulation_experiment().experiment_id,
        identity,
        Decimal("1"),
    )

    prediction = predict_with_engine(_sp_engine(), reference, isolated=False)
    candidate = candidate_observation(reference, prediction)
    assert candidate.engine is not None
    assert _SP_PACK_SOURCE in candidate.engine.coefficient_sources
    assert prediction.value is not None
    assert candidate.authority is Authority.EXTRAPOLATED
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.authority is Authority.EXTRAPOLATED
        and "outside_validated" in notice.reason
        for notice in prediction.notices
    )


def test_openimcc_sp_residue_domain_status_becomes_a_scored_notice() -> None:
    from simulator.battery.score import _residue_domain_notices

    notices = _residue_domain_notices(
        Engine.OPENIMCC_SP,
        {
            "engine_flags": (
                "openimcc_extrapolated",
                "composition_outside_validated_envelope",
            ),
            "engine_metadata": {
                "envelope_status": "outside_validated",
                "extrapolated": "true",
            },
        },
    )

    assert notices
    assert all(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and notice.authority is Authority.EXTRAPOLATED
        for notice in notices
    )
    assert any("outside_validated" in notice.reason for notice in notices)
    assert any("openimcc_extrapolated" in notice.reason for notice in notices)
    assert _residue_domain_notices(
        Engine.OPENIMCC,
        {
            "engine_flags": ("openimcc_extrapolated",),
            "engine_metadata": {"envelope_status": "outside_validated"},
        },
    ) == ()


def test_openimcc_sp_residue_provenance_keeps_the_bridge_domain_result() -> None:
    from simulator.battery.residue import (
        _openimcc_residue_state_provenance,
        _record_openimcc_residue_state,
    )

    state = SimpleNamespace(
        flags=("composition_outside_validated_envelope",),
        extrapolated=True,
        envelope_status="outside_validated",
        pack_model_id="openimcc-test-model",
        pack_version="test-version",
        pack_digest="test-digest",
        openimcc_version="test-openimcc-version",
    )
    flags: set[str] = set()
    metadata: dict[str, str] = {}

    _record_openimcc_residue_state(
        Engine.OPENIMCC_SP.value,
        state,
        pack_name="d066-ext",
        engine_metadata=metadata,
        engine_flags=flags,
    )
    provenance = _openimcc_residue_state_provenance(
        Engine.OPENIMCC_SP.value, flags, metadata
    )

    assert metadata["pack"] == "d066-ext"
    assert metadata["envelope_status"] == "outside_validated"
    assert metadata["extrapolated"] == "true"
    assert "composition_outside_validated_envelope" in flags
    assert "openimcc_extrapolated" in flags
    assert "openimcc_envelope_status:outside_validated" in flags
    assert provenance["engine_flags"] == tuple(sorted(flags))
    assert provenance["engine_metadata"] == metadata


def test_openimcc_sp_residue_worker_refusal_names_the_sp_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery import residue

    class Worker:
        def is_alive(self) -> bool:
            return True

    class Connection:
        def send(self, _payload) -> None:
            pass

        def poll(self, _timeout) -> bool:
            return True

        def recv(self):
            return False, "ImccNonconvergenceError", "forced SP nonconvergence"

    def evaluate(**_kwargs):
        raise AssertionError("the cached worker response should be used")

    monkeypatch.setattr(
        residue,
        "_OPENIMCC_EVALUATION_WORKER",
        (Worker(), Connection(), evaluate),
    )
    with pytest.raises(residue.ResidueEngineNonconvergence) as raised:
        residue._evaluate_openimcc_isolated(
            evaluate,
            timeout_s=1.0,
            engine_name=Engine.OPENIMCC_SP.value,
            temperature_K=1800.0,
        )

    assert raised.value.engine == Engine.OPENIMCC_SP.value
    assert raised.value.reason == "engine_nonconvergence"
