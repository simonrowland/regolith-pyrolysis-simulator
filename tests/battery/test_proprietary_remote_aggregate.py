from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    MetricOperation,
    Phase,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.identity import Identity
from simulator.battery.records import Admission, Observation, Species, State, Value
from simulator.battery import score


ROOT = Path(__file__).resolve().parents[2]


def _observation(
    observation_id: str,
    value: str,
    *,
    temperature: str = "1500",
    quantity: Quantity = Quantity.P_SAT,
    subtype: str | None = None,
):
    identity = Identity(
        quantity=quantity,
        species=Species(formula="Fe", phase=Phase.G),
        subtype=None if subtype is None else State.of(subtype),
        temperature_K=State.of(Decimal(temperature)),
    )
    return Observation(
        observation_id=observation_id,
        experiment_id=f"{observation_id}-experiment",
        identity=identity,
        value=Value(kind=ValueKind.POINT, point=Decimal(value)),
        uncertainty=None,
        evidence=None,
        admission=Admission(AdmissionStatus.ADMITTED, "test observation"),
        notices=(),
    )


class _FakeScalarProvider:
    """In-process fake of the scalar contract; it does no engine work."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.requests = []

    def score(self, request):
        self.requests.append(request)
        self.calls.append(
            (
                request.observation_id,
                request.quantity,
                request.identity.temperature_K.value,
            )
        )
        return self.responses[request.observation_id]


def _context(*observations):
    return SimpleNamespace(
        observations={
            observation.observation_id: observation for observation in observations
        }
    )


def test_proprietary_route_retains_only_aggregate_and_never_touches_row_sinks(
    monkeypatch,
) -> None:
    from simulator.optimize.results_store import ResultStore

    observations = (
        _observation("obs-produced", "1"),
        _observation("obs-refused", "4"),
    )
    provider = _FakeScalarProvider(
        {
            "obs-produced": score.ProprietaryProduced(
                observation_id="obs-produced",
                quantity=Quantity.P_SAT,
                scalar=Decimal("2"),
                unit=score.QUANTITY_UNITS[Quantity.P_SAT],
                engine_version="fake-version",
                datafile_fingerprint="sha256:test-data",
            ),
            "obs-refused": score.ProprietaryRefused(
                observation_id="obs-refused",
                code="unsupported",
                detail="fake refusal",
            ),
        }
    )

    def fail(*_args, **_kwargs):
        raise AssertionError("aggregate scoring reached a row store, cache, or serializer")

    for name in (
        "_score_store_with_decisions",
        "_score_store_to_jsonl",
        "_hashimoto_residue_prediction",
        "_knudsen_residue_prediction",
        "_sossi_residue_prediction",
        "residual_to_plain",
        "dumps_residual_line",
        "write_residuals_jsonl",
        "load_residuals_jsonl",
        "iter_residual_payloads",
        "_iter_residuals_jsonl",
        "render_score_report",
    ):
        if hasattr(score, name):
            monkeypatch.setattr(score, name, fail)
    for name in ("lookup", "fetch", "store", "store_with_write_lock"):
        monkeypatch.setattr(ResultStore, name, fail)

    aggregates = score.score_proprietary_aggregate(
        _context(*observations),
        engine=Engine.PROPRIETARY_REMOTE,
        observation_ids=tuple(item.observation_id for item in observations),
        provider=provider,
    )

    assert len(provider.calls) == 2
    assert provider.calls[0] == ("obs-produced", Quantity.P_SAT, Decimal("1500"))
    assert {field.name for field in fields(provider.requests[0])} == {
        "observation_id",
        "quantity",
        "identity",
    }
    assert provider.requests[0].identity is observations[0].identity
    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert isinstance(aggregate, score.ProprietaryRailAggregate)
    assert aggregate.rail.value == "vapour"
    assert aggregate.n == 1
    expected_dex = score.compute_metric(
        MetricOperation.DEX, Decimal("2"), Decimal("1")
    )
    assert aggregate.rms_dex == abs(expected_dex)
    assert aggregate.median_abs_dex == abs(expected_dex)
    assert dict(aggregate.refusal_counts_by_code) == {"unsupported": 1}
    assert (
        aggregate.engine_tier_label
        == score.ENGINE_CHANNELS[Engine.PROPRIETARY_REMOTE]
    )
    assert aggregate.datafile_fingerprint == "sha256:test-data"
    assert {item.name for item in fields(aggregate)} == {
        "rail",
        "n",
        "rms_dex",
        "median_abs_dex",
        "refusal_counts_by_code",
        "engine_tier_label",
        "datafile_fingerprint",
    }
    assert "obs-produced" not in repr(aggregate)
    assert "fake refusal" not in repr(aggregate)
    assert Engine.PROPRIETARY_REMOTE not in score.SCORE_ENGINE_SET
    from simulator.battery.enums import ProprietaryEngineRefusal

    with pytest.raises(ProprietaryEngineRefusal):
        score._require_score_engine(Engine.PROPRIETARY_REMOTE)


def test_no_provider_counts_attempted_unavailable_for_every_candidate() -> None:
    observations = (_observation("obs-a", "1"), _observation("obs-b", "2"))

    aggregates = score.score_proprietary_aggregate(
        _context(*observations),
        engine=Engine.PROPRIETARY_REMOTE,
        observation_ids=("obs-a", "obs-b"),
    )

    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate.n == 0
    assert aggregate.rms_dex is None
    assert aggregate.median_abs_dex is None
    assert dict(aggregate.refusal_counts_by_code) == {
        RefusalReason.ATTEMPTED_UNAVAILABLE.value: 2
    }
    assert aggregate.datafile_fingerprint is None


def test_aggregate_uses_existing_phase_component_reference_normalization() -> None:
    observation = _observation(
        "obs-ppm",
        "10000",
        quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        subtype="element_ppm_by_mass",
    )
    provider = _FakeScalarProvider(
        {
            "obs-ppm": score.ProprietaryProduced(
                observation_id="obs-ppm",
                quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
                scalar=Decimal("1"),
                unit=score.QUANTITY_UNITS[
                    Quantity.EQUILIBRIUM_PHASE_COMPOSITION
                ],
                engine_version="fake-version",
                datafile_fingerprint="sha256:test-data",
            )
        }
    )

    aggregates = score.score_proprietary_aggregate(
        _context(observation),
        engine=Engine.PROPRIETARY_REMOTE,
        observation_ids=(observation.observation_id,),
        provider=provider,
    )

    assert aggregates[0].n == 1
    assert aggregates[0].rms_dex == 0
    assert aggregates[0].median_abs_dex == 0


def test_caller_condition_overrides_are_rejected() -> None:
    observation = _observation("obs-a", "1")

    with pytest.raises(TypeError, match="temperature_K"):
        score.score_proprietary_aggregate(
            _context(observation),
            engine=Engine.PROPRIETARY_REMOTE,
            observation_ids=("obs-a",),
            temperature_K=Decimal("900"),
        )


def test_grid_pregrind_refuses_proprietary_backend_before_creating_database(
    tmp_path,
) -> None:
    from scripts import grid_pregrind
    from scripts.grid_pregrind_writer import GridCacheWriter
    from simulator.battery.enums import ProprietaryEngineRefusal

    args = grid_pregrind.parser().parse_args(
        ["--backend", Engine.PROPRIETARY_REMOTE.value]
    )
    with pytest.raises(ProprietaryEngineRefusal) as caught:
        grid_pregrind.backend_config(args)
    assert caught.value.mode == "grid_pregrind"
    with pytest.raises(ProprietaryEngineRefusal):
        grid_pregrind._validate_args(args)

    database = tmp_path / "must-not-be-created.db"
    with pytest.raises(ProprietaryEngineRefusal):
        GridCacheWriter(database, backend_name=Engine.PROPRIETARY_REMOTE.value)
    assert not database.exists()


def test_sobol_densification_refuses_proprietary_observation_inputs(tmp_path) -> None:
    from scripts.grid_pregrind_writer import GridCacheWriter
    from simulator.battery.enums import ProprietaryEngineRefusal

    with GridCacheWriter(tmp_path / "sobol.db") as writer:
        batch_id = writer.ensure_batch(
            label="proprietary-sobol",
            kind="sobol",
            seed=1,
            params={"test": True},
        )
        with pytest.raises(ProprietaryEngineRefusal) as caught:
            writer.materialize_key(
                {"mode": Engine.PROPRIETARY_REMOTE.value},
                batch_id=batch_id,
                shuffle_rank=0,
                shard=0,
            )
    assert caught.value.mode == "grid_pregrind"


@pytest.mark.parametrize("strategy", ("random", "bayesian"))
def test_optimizer_search_and_sobol_proposals_refuse_before_store_access(
    monkeypatch, tmp_path, strategy
) -> None:
    from simulator.optimize import study
    from simulator.battery.enums import ProprietaryEngineRefusal

    profile = {
        "run": {"backend_name": Engine.PROPRIETARY_REMOTE.value},
        "fidelities": {"high": {"backend_name": Engine.PROPRIETARY_REMOTE.value}},
    }
    monkeypatch.setattr(study, "resolve_profile", lambda *_args, **_kwargs: profile)

    with pytest.raises(ProprietaryEngineRefusal) as caught:
        study.run(
            profile,
            feedstock="test-feedstock",
            strategy=strategy,
            fidelity="high",
            parallel=1,
            budget=1,
            out_dir=tmp_path / "optimizer-run-must-not-start",
            result_store=SimpleNamespace(),
        )
    assert caught.value.mode == "optimizer_search"
    assert not (tmp_path / "optimizer-run-must-not-start").exists()


def test_optimizer_pool_refuses_before_result_store_initialization(
    monkeypatch, tmp_path
) -> None:
    from simulator.optimize import pool
    from simulator.battery.enums import ProprietaryEngineRefusal

    monkeypatch.setattr(
        pool,
        "_task_stage0_subprocess_required",
        lambda _feedstock: False,
    )
    store = SimpleNamespace(
        initialize=lambda: pytest.fail("proprietary profile initialized ResultsStore")
    )
    profile = {
        "run": {"backend_name": Engine.PROPRIETARY_REMOTE.value},
        "fidelities": {"high": {"backend_name": Engine.PROPRIETARY_REMOTE.value}},
    }

    with pytest.raises(ProprietaryEngineRefusal):
        pool._evaluate_batch(
            [({}, "test-feedstock", "high")],
            profile=profile,
            output_root=tmp_path / "pool-run-must-not-start",
            results_store=store,
        )


def test_optimizer_certification_refuses_before_results_store_fetch(
    monkeypatch, tmp_path
) -> None:
    from simulator.optimize import study
    from simulator.battery.enums import ProprietaryEngineRefusal

    profile = {
        "run": {"backend_name": Engine.PROPRIETARY_REMOTE.value},
        "fidelities": {"high": {"backend_name": Engine.PROPRIETARY_REMOTE.value}},
    }
    monkeypatch.setattr(study, "resolve_profile", lambda *_args, **_kwargs: profile)
    monkeypatch.setattr(
        study.ResultStore,
        "fetch",
        lambda *_args, **_kwargs: pytest.fail("certification fetched ResultsStore"),
    )

    with pytest.raises(ProprietaryEngineRefusal):
        study.run_certify(
            profile,
            feedstock="test-feedstock",
            fidelity="high",
            source_store=tmp_path / "source-store.sqlite",
            certify_cache_key="must-not-fetch",
            out_dir=tmp_path / "certification-must-not-start",
        )
    assert not (tmp_path / "certification-must-not-start").exists()


def test_surrogate_fitting_returns_typed_proprietary_refusal() -> None:
    from simulator.battery.compilation_tier import predict_thermo_attempt

    attempt = predict_thermo_attempt(
        Engine.PROPRIETARY_REMOTE,
        _observation("obs-thermo", "1"),
    )

    assert attempt.value is None
    assert attempt.refusal_reason is RefusalReason.PROPRIETARY_ENGINE_EXCLUDED


def test_distilled_grind_archive_refuses_source_before_opening_accumulator(
    monkeypatch, tmp_path
) -> None:
    from scripts import grind_harvest
    from scripts.grid_pregrind_writer import SCHEMA_VARIANT
    from simulator.battery.enums import ProprietaryEngineRefusal

    class Source:
        closed = False

        def execute(self, _query):
            return [(Engine.PROPRIETARY_REMOTE.value,)]

        def close(self):
            self.closed = True

    source = Source()
    monkeypatch.setattr(
        grind_harvest.sqlite3,
        "connect",
        lambda *_args, **_kwargs: source,
    )
    monkeypatch.setattr(
        grind_harvest,
        "_metadata",
        lambda _connection, key: SCHEMA_VARIANT if key == "schema_variant" else None,
    )
    monkeypatch.setattr(
        grind_harvest,
        "GridCacheWriter",
        lambda *_args, **_kwargs: pytest.fail(
            "archive accumulator opened before policy check"
        ),
    )

    with pytest.raises(ProprietaryEngineRefusal) as caught:
        grind_harvest.harvest_snapshot(
            tmp_path / "source.db",
            tmp_path / "accumulator.db",
            source_host="local",
        )
    assert caught.value.mode == "distilled_grind_archive"
    assert source.closed


def test_engine_proprietary_is_the_only_hardcoded_policy_owner() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    token_owners: dict[str, list[str]] = {}
    value_occurrences: list[str] = []
    for relative in tracked:
        if not relative.endswith(".py") or relative.startswith("tests/"):
            continue
        path = ROOT / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        if relative != "simulator/battery/enums.py":
            value_occurrences.extend(
                f"{relative}:{number}" for number, line in enumerate(lines, 1)
                if "proprietary_remote" in line
            )
        if relative != "simulator/battery/enums.py":
            token_owners[relative] = [
                f"{number}: {line.strip()}"
                for number, line in enumerate(lines, 1)
                if "Engine.PROPRIETARY_REMOTE" in line
            ]

    score_registry = token_owners.pop("simulator/battery/score.py", [])
    assert [line.split(": ", 1)[1] for line in score_registry] == [
        'Engine.PROPRIETARY_REMOTE: "proprietary-remote",',
        "Engine.PROPRIETARY_REMOTE: (),",
    ]
    assert not {path: lines for path, lines in token_owners.items() if lines}
    assert not value_occurrences
    assert Engine.PROPRIETARY_REMOTE.proprietary
    assert all(
        not engine.proprietary
        for engine in Engine
        if engine is not Engine.PROPRIETARY_REMOTE
    )
