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
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "1098ed3c68210dd6cdd89b0acc7499673aa044ca141411d15b246e202f58a87c"
    )
    assert digest == "5449df4e9006f39299db373ec7b14902651fe9b8fc4f739adab4f26f87c1f4ca"
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
