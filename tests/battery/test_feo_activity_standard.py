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


_RESIDUAL_SHA256_BEFORE_STANDARD_STEP = {
    Engine.INTERNAL_ANALYTICAL: (
        "eb6b8cce0b5487f44c7f6a0705d92a3f4ffc4a0248af489784610026544f624d"
    ),
}


def _openimcc_sources() -> tuple[str, ...]:
    from simulator.battery.source_lineage import openimcc_pack_identity_sources
    from simulator.melt_backend.openimcc_bridge import openimcc_pack_identity

    return openimcc_pack_identity_sources(openimcc_pack_identity())


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
    ),
)
def test_internal_analytical_zero_offset_preserves_residual_bytes(
    engine: Engine,
    sources: tuple[str, ...],
    expected_digest: str,
) -> None:
    context, _experiment, reference, prediction = _case(engine, sources)
    resolver = getattr(score_module, "_resolve_feo_activity_standard", None)
    converter = getattr(score_module, "_canonicalize_feo_activity_prediction", None)
    assert callable(resolver)
    assert callable(converter)

    standard, refusal = resolver(engine)
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


def test_openimcc_feo_standard_conversion_uses_the_profile_resolver() -> None:
    import openimcc

    temperature_K = Decimal("1700")
    sources = _openimcc_sources()
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC, sources, temperature_K=temperature_K
    )
    resolver = getattr(score_module, "_resolve_feo_activity_standard", None)
    converter = getattr(score_module, "_canonicalize_feo_activity_prediction", None)
    assert callable(resolver)
    assert callable(converter)

    standard, refusal = resolver(
        Engine.OPENIMCC,
        temperature_K=temperature_K,
    )
    assert refusal is None
    assert standard is not None
    expected_offset = openimcc.feo_liquid_standard_offset(
        float(temperature_K), profile="openimcc"
    )
    assert standard.name == "JANAF-type"
    assert standard.offset_j_mol == Decimal(
        str(expected_offset.value_J_per_mol)
    )

    canonical, refusal = converter(prediction)
    assert refusal is None
    assert canonical is not None
    assert canonical.value != prediction.value
    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )
    assert residual.numeric is not None
    assert float(residual.numeric.value) == pytest.approx(
        float(canonical.value.log10() - reference.value.point.log10()), abs=1e-15
    )


def test_openimcc_pack_identity_has_no_feo_guard_digests() -> None:
    from simulator.battery.source_lineage import openimcc_pack_identity_sources
    from simulator.melt_backend.openimcc_bridge import openimcc_pack_identity

    identity = openimcc_pack_identity()

    assert set(identity["packs"]) == {"melt", "redox", "gas", "condensate"}
    assert "feo_guard" not in identity
    sources = openimcc_pack_identity_sources(identity)
    assert not any(source.startswith("openimcc-feo-") for source in sources)


def test_openimcc_feo_resolver_does_not_require_gas_binding_metadata(
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

    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        temperature_K=Decimal("1700"),
    )

    assert refusal is None
    assert standard is not None
    expected_offset = openimcc.feo_liquid_standard_offset(1700.0, profile="openimcc")
    assert standard.offset_j_mol == Decimal(str(expected_offset.value_J_per_mol))

def test_openimcc_nondefault_profile_uses_its_profile_resolver() -> None:
    import openimcc

    temperature_K = Decimal("1700")
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        temperature_K=temperature_K,
        profile="published",
    )

    assert refusal is None
    assert standard is not None
    expected_offset = openimcc.feo_liquid_standard_offset(
        float(temperature_K), profile="published"
    )
    assert standard.offset_j_mol == Decimal(str(expected_offset.value_J_per_mol))


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


def test_distin_closed_redox_residual_uses_default_profile(
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
    assert (
        residual.numeric.operation.value,
        residual.numeric.unit,
        residual.numeric.value.as_tuple(),
        residual.numeric.decision_band,
        residual.numeric.metric_uncertainty,
        residual.numeric.verdict,
    ) == (
        "dex",
        "dimensionless",
        Decimal("0.08341796180656715").as_tuple(),
        None,
        None,
        None,
    )
    assert candidate.provenance == {
        "selected_oxygen_condition": {
            "fo2_tier": 3,
            "physics_rung": None,
            "route": "openimcc_closed_fe_metal_saturation",
            "value_Pa": "0.02022084627693526699090953590",
        }
    }
    from simulator.melt_backend.openimcc_bridge import openimcc_pack_identity
    from simulator.battery.source_lineage import openimcc_pack_identity_sources

    assert set(openimcc_pack_identity_sources(openimcc_pack_identity())) <= set(
        candidate.engine.coefficient_sources
    )
    serialized = dumps_residual_line(residual, candidate).encode("utf-8")
    # Pin the result produced by the default profile and package identity.
    assert sha256(serialized).hexdigest() == (
        "da28e51e8b7c9432522f76e7a448d113d3df6c9f5de0232a69a50614a0278536"
    )


def test_openimcc_feo_conversion_uses_resolver_across_pack_identity_changes() -> None:
    sources = (
        "openimcc-package-version:1.0.6-d066-v4",
        "openimcc-commit:995dc14507dfeefb6849bfedbf19107d015bc040",
        "openimcc-pack-file:redox:openimcc-redox-v5.json:sha256:" + "b" * 64,
    )
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.NO_BAND
    assert residual.refusal is None
    assert residual.numeric is not None
    assert residual.numeric.value.as_tuple() == Decimal(
        "0.31049585082667236"
    ).as_tuple()


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
        lambda _engine, **_kwargs: (
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
