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
    # Stage-3 diverter route knobs were added. The prior furnace-envelope
    # rebind remains in the history; this manifest now also carries four
    # categorical C2A_staged.stage3_route leaves.
    # Conditional dimensions remain [65, 71].
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "e55c8f213569f489889440e2c9338f60ab7833cf887471481283264b9de08033"
    )
    assert digest == "1318a1b6ee4378573775b7a665475f23d15c485ae076b4e22c28042866d55e54"
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [65, 71]
