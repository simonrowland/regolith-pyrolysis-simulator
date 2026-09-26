from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from simulator.optimize.canonical import canonical_json_dumps


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "optimizer_recipe_vocabulary.json"
GENERATOR = ROOT / "scripts" / "generate_optimizer_recipe_vocabulary.py"


def test_optimizer_recipe_vocabulary_manifest_is_generated_and_self_pinned(tmp_path):
    generated = tmp_path / "optimizer_recipe_vocabulary.json"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(generated)],
        cwd=ROOT,
        check=True,
    )
    assert generated.read_bytes() == MANIFEST.read_bytes()
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = payload.pop("payload_digest")
    # Regenerated via scripts/generate_optimizer_recipe_vocabulary.py after the
    # continuous C2A Stage-3 temperature-window knobs were added. The manifest
    # still carries the four categorical C2A_staged.stage3_route leaves.
    # Conditional dimensions are [67, 73] after the two continuous knobs.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "524a187eb7868c9c02d817b5669c5247f44fe6f85e17378015106f445c9101fe"
    )
    assert digest == "80960dcb8823df37ed2efafde15ac6f434d0094b2c7ede6a05dbd3892dab9db1"
    assert hashlib.sha256(canonical_json_dumps(payload).encode()).hexdigest() == digest
    assert {
        row["path"] for row in payload["allowlist"]
        if row["path"].endswith(".stage3_route")
    } == {
        "campaigns.C2A_staged.stages.alkali_early_fe.stage3_route",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.stage3_route",
        "campaigns.C2A_staged.stages.fe_hot_hold.stage3_route",
        "campaigns.C2A_staged.stages.sio_window.stage3_route",
    }
    paths = {row["path"] for row in payload["allowlist"]}
    window_rows = {
        row["path"]: row
        for row in payload["allowlist"]
        if row["path"] in {
            "campaigns.C2A_continuous.stage3_open_T_C",
            "campaigns.C2A_continuous.stage3_close_T_C",
        }
    }
    assert window_rows["campaigns.C2A_continuous.stage3_open_T_C"]["kind"] == "float"
    assert window_rows["campaigns.C2A_continuous.stage3_open_T_C"]["low"] == 900.0
    assert window_rows["campaigns.C2A_continuous.stage3_open_T_C"]["high"] == 1700.0
    assert window_rows["campaigns.C2A_continuous.stage3_close_T_C"]["kind"] == "float"
    assert window_rows["campaigns.C2A_continuous.stage3_close_T_C"]["low"] == 950.0
    assert window_rows["campaigns.C2A_continuous.stage3_close_T_C"]["high"] == 2200.0
    assert all(row["search_enabled"] for row in window_rows.values())
    forbidden_future_prefixes = (
        "campaigns.vacuum_dissociation",
        "condensation_train.ballistic_condenser",
        "overhead_headspace.cover_gas",
        "campaigns.C7",
        "campaigns.reducing_gas",
    )
    assert not any(
        path.startswith(prefix)
        for path in paths
        for prefix in forbidden_future_prefixes
    )
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [67, 73]
