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


_OPENIMCC_FEO_REDOX_ROWS_DIGEST = (
    "8dc55974d7198675266c83634ffa08673c18fdbe228ee01c1017481642c2f0cc"
)
_OPENIMCC_FEO_CONDENSATE_ROW_DIGEST = (
    "0aa4d729270ca1434601f414c11485e70706894b735907d7b6eeade0e24f9312"
)
_OPENIMCC_FEO_PUBLISHED_CONDENSATE_ROW_DIGEST = (
    "63c5a83e84091608bcd56fa97cff10568ce6df568ded5b6845f480f42da62fa8"
)
_RESIDUAL_SHA256_BEFORE_STANDARD_STEP = {
    Engine.INTERNAL_ANALYTICAL: (
        "eb6b8cce0b5487f44c7f6a0705d92a3f4ffc4a0248af489784610026544f624d"
    ),
}
def _openimcc_sources(
    *,
    redox_rows_digest: str = _OPENIMCC_FEO_REDOX_ROWS_DIGEST,
    condensate_row_digest: str = _OPENIMCC_FEO_CONDENSATE_ROW_DIGEST,
    published_condensate_row_digest: str = (
        _OPENIMCC_FEO_PUBLISHED_CONDENSATE_ROW_DIGEST
    ),
) -> tuple[str, ...]:
    return (
        f"openimcc-feo-redox-rows:sha256:{redox_rows_digest}",
        f"openimcc-feo-condensate-row:sha256:{condensate_row_digest}",
        "openimcc-feo-published-condensate-row:sha256:"
        f"{published_condensate_row_digest}",
    )


def _copy_openimcc_guard_data(tmp_path: Path) -> Path:
    import shutil
    from importlib import resources

    import openimcc

    source = Path(resources.files(openimcc).joinpath("data"))
    root = tmp_path / "package"
    for relative in (
        "packs/imcc-sf04-d066-v4.json",
        "packs/openimcc-redox-v5.json",
        "gas/gas-shomate-default-v1.csv",
        "gas/condensate-default-v1.csv",
        "packs/sf04-published/condensate.csv",
    ):
        destination = root / "data" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    return root


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
        prediction.coefficient_sources,
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


def test_openimcc_feo_row_refuses_changed_condensate_feo_content() -> None:
    actual_digest = "a" * 64
    sources = _openimcc_sources(condensate_row_digest=actual_digest)
    context, _experiment, reference, prediction = _case(Engine.OPENIMCC, sources)

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "FeO pinned-digest guard: awaiting re-pin",
        "row": "default-profile condensate FeO(l) row",
        "expected_sha256": _OPENIMCC_FEO_CONDENSATE_ROW_DIGEST,
        "actual_sha256": actual_digest,
    }


def test_openimcc_closed_redox_feo_row_refuses_changed_guarded_rows() -> None:
    actual_digest = "c" * 64
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC,
        _openimcc_sources(redox_rows_digest=actual_digest),
    )

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.METRIC_DOMAIN
    assert residual.refusal.detail == {
        "reason": "FeO pinned-digest guard: awaiting re-pin",
        "row": "redox FeO and Mn reaction/thermo rows",
        "expected_sha256": _OPENIMCC_FEO_REDOX_ROWS_DIGEST,
        "actual_sha256": actual_digest,
    }


def test_openimcc_feo_row_refuses_changed_published_profile_feo_content() -> None:
    actual_digest = "d" * 64
    context, _experiment, reference, prediction = _case(
        Engine.OPENIMCC,
        _openimcc_sources(published_condensate_row_digest=actual_digest),
    )

    residual, _candidate = compile_residual(
        reference, Engine.OPENIMCC, context=context, prediction=prediction
    )

    assert residual.status is ResidualStatus.REFUSED
    assert residual.refusal is not None
    assert residual.refusal.detail == {
        "reason": "FeO pinned-digest guard: awaiting re-pin",
        "row": "published-profile condensate FeO(l) row",
        "expected_sha256": _OPENIMCC_FEO_PUBLISHED_CONDENSATE_ROW_DIGEST,
        "actual_sha256": actual_digest,
    }


@pytest.mark.parametrize(
    ("mutation", "expected_row"),
    (
        ("redox_reaction", "redox FeO and Mn reaction/thermo rows"),
        ("redox_thermo", "redox FeO and Mn reaction/thermo rows"),
        ("default_condensate", "default-profile condensate FeO(l) row"),
        ("published_condensate", "published-profile condensate FeO(l) row"),
    ),
)
def test_openimcc_feo_guard_refuses_mutation_of_each_consumed_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_row: str,
) -> None:
    import csv
    import json
    from types import SimpleNamespace

    from simulator.melt_backend import openimcc_bridge
    from simulator.battery.source_lineage import openimcc_pack_identity_sources

    root = _copy_openimcc_guard_data(tmp_path)
    if mutation.startswith("redox_"):
        path = root / "data/packs/openimcc-redox-v5.json"
        payload = json.loads(path.read_text())
        if mutation == "redox_reaction":
            row = next(row for row in payload["rows"] if row["complex"] == "FeO(l)")
            row["standard_gibbs_terms"][0]["coefficient"] += 0.001
        else:
            row = payload["thermo_species"]["FeO(l)"]["row"]
            row["dG_A"] += 0.001
        path.write_text(json.dumps(payload))
    else:
        relative = (
            "gas/condensate-default-v1.csv"
            if mutation == "default_condensate"
            else "packs/sf04-published/condensate.csv"
        )
        path = root / "data" / relative
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = list(reader)
        feo = next(row for row in rows if row["species_name"] == "FeO(l)")
        feo["dG_A"] = str(float(feo["dG_A"]) + 0.001)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    monkeypatch.setattr(
        openimcc_bridge,
        "resources",
        SimpleNamespace(files=lambda _package: root),
    )
    identity = openimcc_bridge.openimcc_pack_identity()
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        openimcc_pack_identity_sources(identity),
        temperature_K=Decimal("1700"),
    )

    assert standard is None
    assert refusal is not None
    assert refusal.detail["reason"] == "FeO pinned-digest guard: awaiting re-pin"
    assert refusal.detail["row"] == expected_row


def test_openimcc_feo_guard_accepts_unrelated_redox_row_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from types import SimpleNamespace

    from simulator.melt_backend import openimcc_bridge
    from simulator.battery.source_lineage import openimcc_pack_identity_sources

    root = _copy_openimcc_guard_data(tmp_path)
    path = root / "data/packs/openimcc-redox-v5.json"
    payload = json.loads(path.read_text())
    row = next(row for row in payload["rows"] if row["complex"] == "SrO(l)")
    row["standard_gibbs_terms"][0]["coefficient"] += 0.001
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        openimcc_bridge,
        "resources",
        SimpleNamespace(files=lambda _package: root),
    )
    identity = openimcc_bridge.openimcc_pack_identity()

    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        openimcc_pack_identity_sources(identity),
        temperature_K=Decimal("1700"),
    )

    assert refusal is None
    assert standard is not None


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
        "missing_identity": "condensate_feo_row_digest",
    }


def test_openimcc_feo_guard_accepts_the_default_pack_rows_by_content_digest() -> None:
    from simulator.melt_backend import openimcc_bridge

    identity_loader = getattr(openimcc_bridge, "openimcc_pack_identity", None)
    assert callable(identity_loader)
    feo_guard = identity_loader()["feo_guard"]
    from simulator.battery.source_lineage import openimcc_pack_identity_sources

    sources = openimcc_pack_identity_sources(identity_loader())

    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC, sources, temperature_K=Decimal("1700")
    )

    assert refusal is None
    assert standard is not None
    assert standard.name == "JANAF-type"
    assert standard.offset_j_mol != Decimal(0)


def test_openimcc_nondefault_profile_uses_its_profile_resolver() -> None:
    import openimcc

    temperature_K = Decimal("1700")
    standard, refusal = score_module._resolve_feo_activity_standard(
        Engine.OPENIMCC,
        _openimcc_sources(),
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
    # Pin the result produced by the new default profile and FeO content guard.
    assert sha256(serialized).hexdigest() == (
        "db996a2ae254697b9d6d2a1f46fc5f9c2834dfaea48313476dc2cff7149b4b63"
    )


def test_feo_guard_ignores_unrelated_openimcc_pack_content() -> None:
    sources = (
        *_openimcc_sources(),
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
        lambda _engine, _sources, **_kwargs: (
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
