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
    # unchanged.
    # b-245: C2A p_total admission is finite and >= 0 (high is null). Search
    # stays [5, 15] on the bounds identity, not as an admission ceiling.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "01df4d1c83820786ce491b0bd759ef3a0ee7a6c10f9995623ff76d3fe596f116"
    )
    assert digest == "355f88914faa1944e8f4b0372a9c0e313ffe976ce8beefdb6e9ba583894025bb"
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
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [59, 65]
