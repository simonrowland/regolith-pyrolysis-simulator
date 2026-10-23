from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/chaudary-1997-v-redox-binary-silicate.yaml"


def test_chaudary_figures_are_deferred_and_extract_migrates_cleanly():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    block = document["species"]["V"]
    assert block["observations"] == []
    figure_record = block["context"][0]
    figures = figure_record["values"]["figures"]
    assert [figure["figure"] for figure in figures] == list("123456789")
    assert all("caption" in figure and "axes_as_printed" in figure for figure in figures)
    assert figure_record["values"]["digitisation"] == "deferred to a figure pass"

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 0
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues

