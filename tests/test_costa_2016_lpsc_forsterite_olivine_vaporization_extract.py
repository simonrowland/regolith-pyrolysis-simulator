"""Migration pins for Costa et al. (2016), LPSC abstract 1454."""

from pathlib import Path

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/costa-2016-lpsc-forsterite-olivine-vaporization.yaml"


def test_costa_2016_observations_and_table_1_context_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 8
    contexts = {
        context["observation_id"]: context
        for work_contexts in result.context_by_work.values()
        for context in work_contexts
    }
    # Table 1 (printed p. 2) reports −47.7 and 98.0 kJ/mol in this study.
    assert contexts["costa_2016_t1_feo_this_study"]["values"]["value"] == -47.7
    assert contexts["costa_2016_t1_sio2_this_study"]["values"]["value"] == 98.0
    assert not result.validation.hard_issues
