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
    # furnace-envelope rebind. All 17 changed leaves vs 082c2809:
    #   1. furnace_max_T_C.high 2000 -> 2200 (c5434d19; zirconia_ysz
    #      max_service_T_C)
    #   2-7. six overhead_headspace *.default_C.high 1750 -> 2200
    #      (36da8e17/b-329; liner, pipe default, stage_0..3_to_next; inherited
    #      from FURNACE_MAX_T_BOUNDS_C[1], not an independent lever)
    #   8. overhead_headspace.temperature_offset_K.low -443 -> -800
    #      (1400 - 2200; 36da8e17)
    #   9-14. those six overhead *.default_C.bounds_source: literal 1750 /
    #      Doloma service text -> inherited-from-envelope text (36da8e17)
    #   15. overhead_headspace.temperature_offset_K.bounds_source:
    #      1843-dense-alumina derivation -> envelope-minus-1400 (36da8e17)
    #   16. bounds_digest 5a5aba76...ecd9184 -> 9d87f239...df66cccd (derived)
    #   17. payload_digest 77ff7776...d4ab6a0 -> 511c72ec...1dc56c12 (derived)
    # C3 leaves C0.temp_range_C and the C2A early-ramp path in the full
    # allowlist but disables both for search; this changes conditional subspace
    # dimensions and derived digests.
    # d-212 (2026-10-20) added searchable campaigns.C3.skip and campaigns.C4.skip.
    # Both are unguarded, so each conditional subspace grew by 2: [59, 65] -> [61, 67].
    # bounds_digest and payload_digest moved with those two leaves.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "c07bd7a7192ef0e7a94b8bf01a29a25416f06555a356541d3d6175f8560a20c6"
    )
    assert digest == "18fb18127bd5027229718bab4a1c2316750a457f3ca5535ed96906b096356496"
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
