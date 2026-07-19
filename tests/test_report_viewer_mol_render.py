import json
import subprocess
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_HARNESS = (
    _ROOT / "tests/fixtures/web_render/render_report_ledger_values.mjs"
)
_SCRIPT = _ROOT / "web/report_viewer/report-viewer.js"


# Regression: WEBQA-002 — JS coercion could fabricate boolean/array mol cells.
# Found by /qa on 2026-07-19
# Report: docs-private/research/2026-07-19-webqa/report.md
def test_every_timestep_and_terminal_ledger_render_true_finite_mol_only():
    completed = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"script_path": str(_SCRIPT)}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)

    assert len(rendered["timesteps"]) == 2
    assert "2.5 mol" in rendered["timesteps"][0]
    assert "7.25 mol" in rendered["timesteps"][1]
    for html in [*rendered["timesteps"], rendered["terminal"]]:
        assert "non-numeric (boolean)" in html
        assert "non-numeric (array)" in html
        assert "non-numeric (string)" in html
        assert "non-numeric (number)" in html
        for fabricated in (
            ">1 mol<",
            ">0 mol<",
            ">3 mol<",
            ">NaN mol<",
            ">Infinity mol<",
        ):
            assert fabricated not in html
    assert "4.5 mol" in rendered["terminal"]
    assert "not numeric" in rendered["terminal"]
