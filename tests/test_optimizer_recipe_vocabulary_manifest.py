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
    # d-137's Stage-4 split changes the stage_4_to_stage_5 wall-knob ceiling
    # from 700 C to the 250 C Na/Mg collector. The generated vocabulary records
    # that bound change alongside car1's Stage-3 route controls.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "2d83dd325798ac7d83982f0f3104a486e741e91b75dbf40b45de859a54df13ea"
    )
    assert digest == "2913cc10acba28ffd0a630d11755edb6ada7190c79ff68f6f07dd883b4fa0278"
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [63, 69]
