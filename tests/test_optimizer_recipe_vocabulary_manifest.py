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
    # Regenerated on train/b154 after phase-separated chunks 1-2
    # (9e3ace925, including 9367afbed). Three moved leaves:
    # allowlist[100..102].bounds_source for condensation_train stages 4/5,
    # 5/6, 6/7: state.py default-envelope text -> setpoints.yaml metals_train
    # bands. payload_digest changes as a consequence. b-1008 Antoine
    # (5a7334561) changes no vocabulary leaves.
    # C3 leaves C0.temp_range_C and the C2A early-ramp path in the full
    # allowlist but disables both for search; this changes conditional subspace
    # dimensions and derived digests.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "d9712e60baa51f93b8ad62fb4317697ab9fdda5d24f38bdea14f47d06661e811"
    )
    assert digest == "afa70b858622d530fd2050d744827dbfd1ff58484edaf01f98a6b2438c558eb7"
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
