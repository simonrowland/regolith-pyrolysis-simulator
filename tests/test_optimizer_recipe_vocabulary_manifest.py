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
    # that one bound change; the conditional subspace dimensions/digests stay
    # unchanged for d-137. b-231 removes the pipe-segment default knob because
    # every runtime segment has a per-segment override, reducing each subspace
    # by one dimension.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "2440c836bc79e7db0bca6f25c5ffa98cb362ba1d25efe2f08af1232ae3bf08f2"
    )
    assert digest == "f5822a55439579d8011fa80a838b7a6344c8f831ab7660d4002a5e16707dfd91"
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [58, 64]
