"""FeO activities are converted once to the scorer's JANAF Fe-019 standard."""

from __future__ import annotations

import csv
from decimal import Decimal
from hashlib import sha256
from importlib import resources
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


_RESIDUAL_SHA256_BEFORE_STANDARD_STEP = (
    "eb6b8cce0b5487f44c7f6a0705d92a3f4ffc4a0248af489784610026544f624d"
)
# b217 pinned the N_Fe2+^0 multiply:
# sha 3b1518942a5ca7524b6e4be8e2c98fd375db44c5835bd830a4acff0a54e32ce0
# dex -0.013180332034779389
# The offer frame is a_D * f from the Darken & Gurry factor.
# The byte pin includes the active OpenIMCC redox-pack identity and current
# JANAF/d-333 lineage. Updating the pack requires updating this digest.
_DISTIN_1880_RESIDUAL_SHA256 = (
    "db630fbbbf4b810f60a09b2e37ee4e9c5be53fc4ff2cd94fb0df4f195cb7cf3a"
)
_DISTIN_1880_RESIDUAL_DEX = Decimal("-0.10531292782444457")


def _openimcc_sources(
    *, pack_version: str, pack_digest: str, condensate_digest: str
) -> tuple[str, ...]:
    return (
        f"openimcc-pack-version:{pack_version}",
        f"openimcc-pack-digest:{pack_digest}",
        f"openimcc-condensate-table:sha256:{condensate_digest}",
        f"openimcc-melt-binding:sha256:{pack_digest}",
    )


def _melt_pack_identity(pack_name: str) -> dict[str, str]:
    from simulator.melt_backend import openimcc_bridge

    pack = openimcc_bridge._load_pack(pack_name)
    return {
        "model_id": str(pack.model_id),
        "pack_version": str(pack.version),
        "binding_digest": openimcc_bridge._pack_digest(pack),
    }


def _case(
    engine: Engine,
    coefficient_sources: tuple[str, ...],
    *,
    temperature_K: Decimal = Decimal("1643.15"),
    provenance: dict[str, object] | None = None,
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
        provenance=provenance or {},
    )
    return context, experiment, reference, prediction


def test_internal_analytical_zero_offset_preserves_residual_bytes() -> None:
    engine = Engine.INTERNAL_ANALYTICAL
    sources = ("nasa-cea-thermo",)
    expected_digest = _RESIDUAL_SHA256_BEFORE_STANDARD_STEP
    context, _experiment, reference, prediction = _case(engine, sources)
    resolver = getattr(score_module, "_resolve_feo_activity_standard", None)
    converter = getattr(score_module, "_canonicalize_feo_activity_prediction", None)
    assert callable(resolver)
    assert callable(converter)

    standard, refusal = resolver(engine, prediction.coefficient_sources)
    assert refusal is None
    assert standard is not None
    assert standard.name == "Fe-019"
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


@pytest.mark.parametrize(
    ("temperature_K", "expected_delta_g_j_mol", "expected_log10_shift"),
    (
        (Decimal("1600"), Decimal("10240.5"), Decimal("0.3343")),
        (Decimal("1650"), Decimal("9692.4"), Decimal("0.3068")),
    ),
)
def test_openimcc_feo_shift_uses_the_pinned_melt_pack_at_temperature(
    temperature_K: Decimal,
    expected_delta_g_j_mol: Decimal,
    expected_log10_shift: Decimal,
) -> None:
    identity = _melt_pack_identity("default")
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest="a" * 64,
        ),
        melt_pack_identity=identity,
        temperature_K=temperature_K,
    )

    assert refusal is None
    assert standard is not None
    assert standard.name == "DG46-MASSON65"
    assert standard.offset_j_mol == expected_delta_g_j_mol
    shift = score_module._canonical_log10_feo_activity(
        Decimal(0), temperature_K, standard.offset_j_mol
    )
    assert float(shift) == pytest.approx(float(expected_log10_shift), abs=0.0001)


def test_melt_pack_shift_matches_condensate_gibbs_rows_as_crosscheck() -> None:
    import openimcc
    from openimcc.gas import _lamor_gibbs

    with (
        resources.files(openimcc)
        .joinpath("data", "gas", "condensate-default-v1.csv")
        .open("r", encoding="utf-8", newline="") as handle
    ):
        current_row = next(
            row for row in csv.DictReader(handle) if "FeO(l)" in row.values()
        )
    old_janaf_row = {
        "dH298_R": "-30.01",
        "dG_A": "6.72",
        "dG_B": "6.588",
        "dG_C": "-1.248",
        "dG_D": "0.150",
        "dG_E": "-0.007697",
    }
    identity = _melt_pack_identity("default")
    for temperature_K, expected in (
        (Decimal("1600"), Decimal("10240.5")),
        (Decimal("1650"), Decimal("9692.4")),
    ):
        standard, refusal = score_module._resolve_feo_activity_standard(
            Engine.OPENIMCC,
            _openimcc_sources(
                pack_version=identity["pack_version"],
                pack_digest=identity["binding_digest"],
                condensate_digest="f" * 64,
            ),
            melt_pack_identity=identity,
            temperature_K=temperature_K,
        )

        assert refusal is None
        assert standard is not None
        condensate_difference = _lamor_gibbs(
            current_row, float(temperature_K)
        ) - _lamor_gibbs(old_janaf_row, float(temperature_K))
        assert condensate_difference == pytest.approx(float(expected), abs=1)
        assert condensate_difference == pytest.approx(
            float(standard.offset_j_mol), abs=1
        )


def test_openimcc_published_v1_feo_standard_has_an_explicit_zero_record() -> None:
    identity = _melt_pack_identity("published-core")
    sources = _openimcc_sources(
        pack_version=identity["pack_version"],
        pack_digest=identity["binding_digest"],
        condensate_digest="b" * 64,
    )
    _context, _experiment, _reference, prediction = _case(
        Engine.OPENIMCC,
        sources,
        temperature_K=Decimal("1600"),
        provenance={"melt_pack_identity": identity},
    )

    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )

    assert normalized.value == prediction.value
    assert normalized.provenance["feo_standard_normalisation"] == {
        "from_standard": "Fe-019",
        "to_standard": "Fe-019",
        "melt_pack_identity": identity,
        "delta_g_j_mol": "0",
        "log10_shift": "0",
        "temperature_K": "1600",
    }


def test_non_v1_pack_without_feo_liquid_candidate_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _melt_pack_identity("default")
    monkeypatch.setattr(
        score_module,
        "_openimcc_pack_identity_and_payload",
        lambda _pack_name: (identity, {}),
    )
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest="1" * 64,
        ),
        melt_pack_identity=identity,
        temperature_K=Decimal("1600"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_standard_pack_candidate_missing"


def test_openimcc_closed_redox_feo_row_refuses_changed_redox_pack_digest() -> None:
    actual_digest = "c" * 64
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        (f"openimcc-redox-pack:sha256:{actual_digest}",),
        melt_pack_identity=_melt_pack_identity("default"),
        temperature_K=Decimal("1600"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail == {
        "reason": "openimcc_feo_redox_pack_digest_mismatch",
        "expected_redox_pack_digest": (
            score_module._OPENIMCC_DEFAULT_REDOX_PACK_DIGEST
        ),
        "actual_redox_pack_digest": actual_digest,
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


def test_distin_closed_redox_residual_pins_green_value_and_serialized_line(
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
    assert residual.numeric.value == _DISTIN_1880_RESIDUAL_DEX
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
        _DISTIN_1880_RESIDUAL_DEX.as_tuple(),
        None,
        None,
        None,
    )
    assert candidate.provenance is not None
    assert candidate.provenance["selected_oxygen_condition"] == {
        "fo2_tier": 3,
        "physics_rung": None,
        "route": "openimcc_closed_fe_metal_saturation",
        "value_Pa": "0.02022084627693526699090953590",
    }
    assert candidate.provenance["feo_standard_normalisation"][
        "from_standard"
    ] == "DG46-MASSON65"
    assert candidate.provenance["feo_standard_normalisation"][
        "to_standard"
    ] == "Fe-019"
    assert float(
        candidate.provenance["feo_standard_normalisation"]["log10_shift"]
    ) > 0
    serialized = dumps_residual_line(residual, candidate).encode("utf-8")
    assert sha256(serialized).hexdigest() == _DISTIN_1880_RESIDUAL_SHA256


def test_nondefault_openimcc_profile_refuses_an_undeclared_feo_standard() -> None:
    sources = _openimcc_sources(
        pack_version="1.0.3-d066-ext-v1",
        pack_digest="b" * 64,
        condensate_digest="d" * 64,
    )
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        sources,
        melt_pack_identity={
            "model_id": "unknown-model",
            "pack_version": "1.0.3-d066-ext-v1",
            "binding_digest": "b" * 64,
        },
        temperature_K=Decimal("1643.15"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.reason is RefusalReason.METRIC_DOMAIN
    assert refusal.detail["reason"] == "openimcc_feo_standard_melt_pack_identity_unknown"


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


def test_openimcc_feo_prediction_is_normalized_and_carries_notice_and_provenance() -> None:
    identity = _melt_pack_identity("default")
    sources = _openimcc_sources(
        pack_version=identity["pack_version"],
        pack_digest=identity["binding_digest"],
        condensate_digest="c" * 64,
    )
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC,
        sources,
        temperature_K=Decimal("1600"),
        provenance={"melt_pack_identity": identity},
    )
    normalized = score_module._normalize_openimcc_feo_activity_prediction(
        prediction
    )

    residual, candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=normalized
    )

    assert residual.status is not ResidualStatus.REFUSED
    assert candidate.provenance is not None
    record = candidate.provenance["feo_standard_normalisation"]
    assert record["from_standard"] == "DG46-MASSON65"
    assert record["to_standard"] == "Fe-019"
    assert record["melt_pack_identity"] == identity
    assert residual.numeric is not None
    assert float(residual.numeric.value) == pytest.approx(
        float(record["log10_shift"]), abs=1e-12
    )
    assert any(
        "DG46-MASSON65" in notice.reason and "Fe-019" in notice.reason
        for notice in candidate.notices
    )


def test_scorer_refuses_an_openimcc_feo_prediction_without_normalisation_record() -> None:
    sources = _openimcc_sources(
        pack_version=score_module._OPENIMCC_DEFAULT_PACK_VERSION,
        pack_digest=score_module._OPENIMCC_DEFAULT_PACK_DIGEST,
        condensate_digest="e" * 64,
    )
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail["reason"] == "openimcc_feo_standard_not_normalised"
