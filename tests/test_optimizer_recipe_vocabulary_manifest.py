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
    # Regenerated via scripts/generate_optimizer_recipe_vocabulary.py. The
    # prior furnace-envelope rebind remains in the manifest; b-282 adds 16
    # derived changes versus that manifest:
    #   - six upstream overhead *.default_C.low values: 1400 -> 20
    #   - overhead_headspace.temperature_offset_K.low: -800 -> -2180
    #   - six upstream bounds_source strings plus the offset source
    #   - bounds_digest and payload_digest
    # Conditional subspace ids/dimensions/digests remain unchanged. File sha256
    # is pinned below.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "b2b77d0a7fb3aba16edfb6c8b28f1c316cf81e9cc3646132760aa2abef5298eb"
    )
    assert digest == "87e32f07a2eb72863f69338608a8080b5bff1aaba235aa32a6bc0834c2ec43a2"
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [61, 67]
