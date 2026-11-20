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
    # controls.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "38fdb04742f339a0497d746b073b65197a2e9bc3b1f37472882446836edfadb0"
    )
    assert digest == "c2d21d74c778db86a161dfc0c1dbf0759a1a7c72a8f00c0ad39ceafde12bff9f"
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
