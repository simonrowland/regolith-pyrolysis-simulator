from __future__ import annotations

import json
from html import unescape
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _stage(total_kg: float) -> dict[str, object]:
    return {
        "stage_number": 1,
        "label": "Fe Condenser",
        "accepted_species": ["Fe"],
        "designated_species_kg": {"Fe": total_kg} if total_kg else {},
        "coproduct_species_kg": {},
        "impurity_species_kg": {},
        "designated_kg": total_kg,
        "impurity_kg": 0.0,
        "total_kg": total_kg,
        "purity_fraction": None if total_kg == 0 else 1.0,
        "verdict": "INDETERMINATE" if total_kg == 0 else "PURE",
        "activity": {"Fe": total_kg > 0},
    }


def _render_surfaces(total_kg: float) -> dict[str, str]:
    stage = _stage(total_kg)
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C2B"}}],
        "terminal": {"stage_purity": {"stage_1_fe_condenser": stage}},
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const root = process.argv[2];
const artifact = JSON.parse(process.argv[3]);
const context = {
  console,
  URLSearchParams,
  window: { location: { search: "" } },
  document: { querySelector: () => null },
  fetch: () => new Promise(() => {}),
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${root}/labels.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/panels/p4-stage-purity.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/panels/p15-equipment-diagram.js`, "utf8"), context);
context.__artifact = artifact;
const viewer = fs.readFileSync(`${root}/report-viewer.js`, "utf8");
vm.runInContext(`${viewer}\nglobalThis.__overview = tapsAndPuritySection(__artifact.terminal);`, context);
const p4 = context.ReportPanels.find((panel) => panel.id === "sec-p4-stage-purity");
const p15 = context.ReportPanels.find((panel) => panel.id === "sec-p15-equipment-diagram");
process.stdout.write(JSON.stringify({
  overview: context.__overview,
  p4: p4.render(artifact, [], [], {}),
  p15: p15.render(artifact, [], [], {}),
}));
"""
    completed = subprocess.run(
        ["node", "-", str(VIEWER), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _visible(markup: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _stage_mass_outputs(surfaces: dict[str, str]) -> tuple[str, str, str]:
    overview = re.search(
        r'<td class="num">(.*?)</td><td class="num">', surfaces["overview"], re.DOTALL
    )
    p4 = re.search(
        r"<span>Total classified product mass</span><b>(.*?)</b>",
        surfaces["p4"],
        re.DOTALL,
    )
    p15 = re.search(
        r"<span>Total classified product mass</span><strong>(.*?)</strong>",
        surfaces["p15"],
        re.DOTALL,
    )
    assert overview and p4 and p15
    return tuple(_visible(match.group(1)) for match in (overview, p4, p15))


def test_stage_mass_qualifier_is_consistent_across_surfaces() -> None:
    rendered = {total: _render_surfaces(total) for total in (0.0, 0.004, 0.02)}
    expected = {
        0.0: "empty",
        0.004: "trace (<0.01 kg)",
        0.02: "0.02 kg",
    }
    for total, surfaces in rendered.items():
        assert _stage_mass_outputs(surfaces) == (expected[total],) * 3
    assert "backend's empty-stage classification" in _visible(rendered[0.0]["p15"])
