"""Report-viewer consumption of emitted terminal.confidence.

The run-level grade is copied as text by provenanceSection. P9 defers and
does not compute a tier. No min()-fold, no purity-CSS remap.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"
SCRIPT = VIEWER / "report-viewer.js"
LABELS = VIEWER / "labels.js"
P9 = VIEWER / "panels" / "p9-provenance.js"


def _minimal_artifact(confidence=None, *, include_confidence_key: bool = True) -> dict:
    terminal = {
        "run_metadata": {},
        "mass_balance_closure": {},
    }
    if include_confidence_key:
        terminal["confidence"] = confidence
    return {
        "artifact_schema_version": "0.6",
        "header": {"engine_identity": {}},
        "terminal": terminal,
    }


def _render_surfaces(artifact: object) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const artifact = JSON.parse(process.argv[5]);
const context = {
  console,
  document: { querySelector: () => ({ innerHTML: "" }) },
  fetch: () => new Promise(() => {}),
  URLSearchParams,
  window: { location: { search: "" } },
};
context.globalThis = context;
context.__artifact = artifact;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
vm.runInContext(
  fs.readFileSync(process.argv[4], "utf8") + `
globalThis.__qaResult = {
  provenance: provenanceSection(globalThis.__artifact),
  p9: globalThis.ReportPanels.find((panel) => panel.id === "sec-p9-provenance")
    .render(globalThis.__artifact, [], [], {}),
};
`,
  context,
);
process.stdout.write(JSON.stringify(context.__qaResult));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(LABELS),
            str(P9),
            str(SCRIPT),
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _confidence_note(html: str) -> str | None:
    match = re.search(
        r'<div class="note" data-run-level-confidence="emitted">.*?</div>',
        html,
        flags=re.S,
    )
    return match.group(0) if match else None


def test_grade_string_is_copied_verbatim_not_min_folded() -> None:
    artifact = _minimal_artifact({
        "grade": "copied-verbatim-grade-token",
        "reasons": ["reason-alpha", "reason-beta"],
        # Decoys a 7-axis min()-fold would collapse. The viewer must ignore them.
        "quantities": {"Fe": "low", "SiO": "low"},
        "panels": {"p1": "low", "p10": "low"},
    })
    rendered = _render_surfaces(artifact)
    note = _confidence_note(rendered["provenance"])
    assert note is not None
    assert ">copied-verbatim-grade-token<" in note
    assert "<li>reason-alpha</li>" in note
    assert "<li>reason-beta</li>" in note
    assert "low" not in note
    assert rendered["p9"].count("copied-verbatim-grade-token") == 0
    assert "reason-alpha" not in rendered["p9"]


def test_grade_is_not_mapped_onto_purity_css_bands() -> None:
    for grade, purity_class in (
        ("high", "pure"),
        ("medium", "mixed"),
        ("low", "contaminated"),
    ):
        rendered = _render_surfaces(_minimal_artifact({
            "grade": grade,
            "reasons": [f"{grade}-reason"],
        }))
        note = _confidence_note(rendered["provenance"])
        assert note is not None
        assert f">{grade}<" in note
        assert f"<li>{grade}-reason</li>" in note
        assert "verdict" not in note
        assert f"class=\"{purity_class}\"" not in note
        assert f" {purity_class}" not in note
        assert purity_class.upper() not in note


def test_absent_confidence_key_renders_no_banner() -> None:
    rendered = _render_surfaces(_minimal_artifact(include_confidence_key=False))
    html = rendered["provenance"]
    assert _confidence_note(html) is None
    assert "data-run-level-confidence" not in html
    assert "Emitted run-level grade" not in html
    assert "Pending confidence" not in html
    assert "Confidence not emitted" not in html
    assert "No confidence tier is computed" in rendered["p9"]
    assert not re.search(r"\bConfidence\b", rendered["p9"].split("<details", 1)[0])


def test_exactly_one_provenance_surface_renders_the_run_level_grade() -> None:
    artifact = _minimal_artifact({
        "grade": "medium",
        "reasons": ["mass-balance residual 1e-13% within 5e-12% closure gate"],
    })
    rendered = _render_surfaces(artifact)
    provenance = rendered["provenance"]
    p9 = rendered["p9"]
    assert _confidence_note(provenance) is not None
    assert ">medium<" in _confidence_note(provenance)
    assert p9.count("medium") == 0
    assert "data-run-level-confidence" not in p9
    assert "No confidence tier is computed" in p9
    assert "deferred to the report provenance section" in p9


def test_viewer_source_does_not_fold_or_alias_confidence() -> None:
    viewer = SCRIPT.read_text()
    p9 = P9.read_text()
    assert "confidence_rollup" not in viewer
    assert "confidence_rollup" not in p9
    assert '({ high: "pure", medium: "mixed", low: "contaminated" })' not in viewer
    assert "terminal.confidence" not in p9
    assert "function confidence_rollup" not in (ROOT / "simulator" / "accounting" / "run_artifact.py").read_text()
