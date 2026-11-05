"""FeO activities are converted once to the scorer's JANAF Fe-019 standard."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    RefusalReason,
    ResidualStatus,
)
from simulator.battery.records import Composition, Execution
from simulator.battery.migrate import REPO_ROOT, Migrator
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    dumps_residual_line,
)
import simulator.battery.score as score_module
from tests.battery import factories as F


_JANAF_FE019_CONDENSATE_DIGEST = (
    "b9c497901404b03b1d1e20bec58f2d4c0903f481af17ae82bfcf97ea7f4bf5e8"
)
_OPENIMCC_DEFAULT_VERSION = "1.0.3-d066-v1"
_OPENIMCC_DEFAULT_PACK_DIGEST = (
    "02c88c04fe09f8f494adda8a76e1f95fee536d11b2b6d66639aaea097fce5e75"
)
_OPENIMCC_DEFAULT_REDOX_PACK_DIGEST = (
    "28ac36dfef019b8f0fe3e11a86030c65dbfdae6733d10532d2bdb942cf3dc67b"
)
_RESIDUAL_SHA256_BEFORE_STANDARD_STEP = {
    Engine.INTERNAL_ANALYTICAL: (
        "eb6b8cce0b5487f44c7f6a0705d92a3f4ffc4a0248af489784610026544f624d"
    ),
    Engine.OPENIMCC: (
        "803a03d510720e9d63f7644f9e91546eee250b35d286fadaea69c48bfcd74a24"
    ),
}
_DISTIN_GREEN_RESIDUAL_SHA256 = (
    "3b1518942a5ca7524b6e4be8e2c98fd375db44c5835bd830a4acff0a54e32ce0"
)


def _openimcc_sources(
    *, pack_version: str, pack_digest: str, condensate_digest: str
) -> tuple[str, ...]:
    return (
        f"openimcc-pack-version:{pack_version}",
        f"openimcc-pack-digest:{pack_digest}",
        f"openimcc-condensate-table:sha256:{condensate_digest}",
        f"openimcc-melt-binding:sha256:{pack_digest}",
    )


def _case(
    engine: Engine,
    coefficient_sources: tuple[str, ...],
    *,
    temperature_K: Decimal = Decimal("1643.15"),
):
    temperature = temperature_K
    experiment = F.tabulation_experiment(T_K=temperature)
    identity = F.activity_identity(
        formula="FeO",
        T_K=temperature,
        component_basis="FeO",
        composition=Composition(
            "oxides",
            (("FeO", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
            AmountBasis.MOLE_FRACTION,
        ),
    )
    reference = F.observation(
        "feo-standard-baseline",
        experiment.experiment_id,
        identity,
        Decimal("0.2"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="feo-standard-test",
    )
    work = F.work()
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        extract_review={"feo-standard-test": "reviewed"},
        hostname="test",
    )
    prediction = EnginePrediction(
        engine=engine,
        channel=engine.value,
        execution=Execution(
            state=ExecutionState.PRODUCED,
            call_evidence="test:feo-standard",
        ),
        value=Decimal("0.2"),
        unit="dimensionless",
        authority=Authority.CERTIFIED,
        coefficient_sources=coefficient_sources,
        lineage_complete=False,
        identity=identity,
        version="test",
    )
    return context, experiment, reference, prediction


@pytest.mark.parametrize(
    ("engine", "sources", "expected_digest"),
    (
        (
            Engine.INTERNAL_ANALYTICAL,
            ("nasa-cea-thermo",),
            _RESIDUAL_SHA256_BEFORE_STANDARD_STEP[Engine.INTERNAL_ANALYTICAL],
        ),
        (
            Engine.OPENIMCC,
            _openimcc_sources(
                pack_version=_OPENIMCC_DEFAULT_VERSION,
                pack_digest=_OPENIMCC_DEFAULT_PACK_DIGEST,
                condensate_digest=_JANAF_FE019_CONDENSATE_DIGEST,
            ),
            _RESIDUAL_SHA256_BEFORE_STANDARD_STEP[Engine.OPENIMCC],
        ),
    ),
)
def test_declared_zero_offset_feo_standards_preserve_residual_bytes(
    engine: Engine,
    sources: tuple[str, ...],
    expected_digest: str,
) -> None:
    context, _experiment, reference, prediction = _case(engine, sources)
    resolver = getattr(score_module, "_resolve_feo_activity_standard", None)
    converter = getattr(score_module, "_canonicalize_feo_activity_prediction", None)
    assert callable(resolver)
    assert callable(converter)

    standard, refusal = resolver(engine, prediction.coefficient_sources)
    assert refusal is None
    assert standard is not None
    assert standard.name == "JANAF-type"
    assert standard.offset_j_mol == Decimal(0)

    canonical_prediction, refusal = converter(prediction)
    assert refusal is None
    assert canonical_prediction.value is prediction.value
    before, before_candidate = compile_residual(
        reference, engine, context=context, prediction=prediction
    )
    after, after_candidate = compile_residual(
        reference, engine, context=context, prediction=canonical_prediction
    )
    before_bytes = dumps_residual_line(before, before_candidate).encode("utf-8")
    after_bytes = dumps_residual_line(after, after_candidate).encode("utf-8")

    assert before.numeric is not None
    assert before.numeric.value.as_tuple() == Decimal("0.0").as_tuple()
    assert before_bytes == after_bytes
    assert sha256(before_bytes).hexdigest() == expected_digest


def test_openimcc_feo_row_refuses_a_changed_condensate_digest() -> None:
    actual_digest = "a" * 64
    sources = _openimcc_sources(
        pack_version=_OPENIMCC_DEFAULT_VERSION,
        pack_digest=_OPENIMCC_DEFAULT_PACK_DIGEST,
        condensate_digest=actual_digest,
    )
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "openimcc_feo_condensate_table_digest_mismatch",
        "expected_condensate_table_digest": _JANAF_FE019_CONDENSATE_DIGEST,
        "actual_condensate_table_digest": actual_digest,
    }


def test_openimcc_closed_redox_feo_row_refuses_changed_redox_pack_digest() -> None:
    actual_digest = "c" * 64
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC,
        (f"openimcc-redox-pack:sha256:{actual_digest}",),
    )

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "openimcc_feo_redox_pack_digest_mismatch",
        "expected_redox_pack_digest": _OPENIMCC_DEFAULT_REDOX_PACK_DIGEST,
        "actual_redox_pack_digest": actual_digest,
    }


def test_openimcc_feo_row_refuses_when_gas_load_loses_condensate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openimcc

    from simulator.diagnostic_helpers.binary_pot_battery import open_battery_engine

    def fail_gas_load():
        raise RuntimeError("test gas datapack failure")

    monkeypatch.setattr(openimcc, "load_gas_datapack", fail_gas_load)
    handle = open_battery_engine("openimcc")
    assert handle.available, handle.unavailable_reason
    assert "engine_binding_identity" not in handle.identity
    assert "condensate_table_digest" not in handle.identity
    assert handle.identity["gas_load_failure"] == (
        "RuntimeError: test gas datapack failure"
    )

    context, _experiment, reference, _prediction = _case(
        Engine.OPENIMCC,
        (),
    )
    residual, _candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        handles={"openimcc": handle},
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "openimcc_feo_gas_datapack_load_failed_condensate_identity_missing",
        "gas_load_failure": "RuntimeError: test gas datapack failure",
        "missing_identity": "condensate_table_digest",
    }


@pytest.fixture(scope="module")
def distin_migration():
    root = Path(REPO_ROOT)
    migrator = Migrator(root=root)
    for name in (
        "holzheid-1997-feo-nio-coo-activity-metal-saturated",
        "distin-1971-ferrous-silicate-melts",
    ):
        migrator._migrate_extract(root / "data/literature/extracts" / f"{name}.yaml")
    migrator.finalize()
    return migrator.result


def test_distin_closed_redox_residual_line_matches_green_bytes(
    distin_migration,
) -> None:
    observation = next(
        row
        for row in distin_migration.observations.values()
        if row.source_id == "distin-1971-ferrous-silicate-melts"
        and row.observation_id.endswith("distin1971_t1_1880_1_4")
    )
    context = ScoreContext(
        works=distin_migration.works,
        experiments=distin_migration.experiments,
        observations=distin_migration.observations,
        benches=distin_migration.benches,
        root=Path(REPO_ROOT),
    )
    residual, candidate = compile_residual(
        observation,
        Engine.OPENIMCC,
        context=context,
        comparison_ids={observation.observation_id},
    )

    assert residual.status is ResidualStatus.NO_BAND
    assert residual.numeric is not None
    assert candidate is not None
    serialized = dumps_residual_line(residual, candidate).encode("utf-8")
    assert sha256(serialized).hexdigest() == _DISTIN_GREEN_RESIDUAL_SHA256


def test_nondefault_openimcc_profile_refuses_an_undeclared_feo_standard() -> None:
    sources = _openimcc_sources(
        pack_version="1.0.3-d066-ext-v1",
        pack_digest="b" * 64,
        condensate_digest=_JANAF_FE019_CONDENSATE_DIGEST,
    )
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "feo_activity_standard_not_declared",
        "engine": Engine.OPENIMCC.value,
        "profile": "1.0.3-d066-ext-v1",
    }


def test_nonzero_feo_standard_conversion_matches_hand_value() -> None:
    converter = getattr(score_module, "_canonical_log10_feo_activity", None)
    assert callable(converter)

    canonical = converter(
        Decimal("0"),
        Decimal("1649.15"),
        Decimal("9701.7"),
    )

    # Hand calculation from the ruling: 9701.7 J/mol / (R T ln 10) = 0.3073 dex.
    # Hand calculation from the ruling: 9701.7 J/mol at 1649.15 K is 0.3073 dex.
    assert float(canonical) == pytest.approx(0.3073, abs=0.00005)


def test_compile_residual_applies_nonzero_standard_conversion_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _experiment, reference, prediction = _case(
        Engine.INTERNAL_ANALYTICAL,
        ("nasa-cea-thermo",),
        temperature_K=Decimal("1649.15"),
    )
    monkeypatch.setattr(
        score_module,
        "_resolve_feo_activity_standard",
        lambda _engine, _sources: (
            score_module._FeOActivityStandard(
                name="hand-value test",
                offset_j_mol=Decimal("9701.7"),
            ),
            None,
        ),
    )

    residual, _candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=prediction,
    )

    assert residual.numeric is not None
    assert residual.numeric.operation.value == "dex"
    assert float(residual.numeric.value) == pytest.approx(0.3073, abs=0.00005)
