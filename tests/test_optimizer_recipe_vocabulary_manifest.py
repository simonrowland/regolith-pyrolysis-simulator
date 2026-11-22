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
    # d-283 adds five collector areas; d-452 adds throat and per-stage vane
    # controls. Regenerated with scripts/generate_optimizer_recipe_vocabulary.py
    # after the stage-area search high bound moved from 10 m2 to 2000 m2.
    # The five stage_areas_m2 rows are the only allowlist changes; subspace
    # digests stay [70, 76].
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "a2e815945eeba73ddbbd05f0e976e4a4587dbf558db95446977104ba38e7108a"
    )
    assert digest == "8b2c75c390474488d63ecfbebb1ce1cb18b9a8af8f8cd5f69271fd7d79328dd2"
    assert hashlib.sha256(canonical_json_dumps(payload).encode()).hexdigest() == digest
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [70, 76]
    assert {
        path
        for path in paths
        if path.startswith("condenser_geometry.stage_areas_m2.")
    } == {
        "condenser_geometry.stage_areas_m2.fe_stage1",
        "condenser_geometry.stage_areas_m2.cr_stage2",
        "condenser_geometry.stage_areas_m2.sio_stage3",
        "condenser_geometry.stage_areas_m2.alkali_stage4",
        "condenser_geometry.stage_areas_m2.k_stage4b",
    }
