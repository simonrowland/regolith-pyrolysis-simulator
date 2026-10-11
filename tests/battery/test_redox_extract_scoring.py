from pathlib import Path

from simulator.battery.enums import Engine, Rail, ResidualStatus
from simulator.battery.migrate import Migrator
from simulator.battery.score import ScoreContext, comparison_candidates, score_store


def test_borisov_2018_rows_reach_redox_scoring_population():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = Migrator(root)
    migrator._migrate_extract(extract)
    migrator.finalize()
    result = migrator.result
    origins = {
        observation_id: "extracts/borisov-2018-compilation.yaml"
        for observation_id in result.observations
    }
    context = ScoreContext(
        result.works,
        result.experiments,
        result.observations,
        origins=origins,
        benches=result.benches,
    )

    assert len(comparison_candidates(context)) == 435
    residuals, _ = score_store(context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX)
    assert len(residuals) == 435
    assert all(residual.status is ResidualStatus.REFUSED for residual in residuals)
    assert all(residual.refusal is not None and residual.numeric is None for residual in residuals)
