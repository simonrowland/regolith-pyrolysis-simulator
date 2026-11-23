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
    # Both sides regenerated this manifest. The b271 digest stays.
    # Needs controller re-measure: the merged schema also has the stack's
    # stage-area, throat and vane rows.
    # b-282: upstream wall knobs search down to the coldest downstream
    # collector interface (20 C) instead of the SiO-quiescent 1400 C figure.
    # Regenerated from the merged schema (b261 wall floor + constackfix routes).
    # Conditional subspace dimensions stay unchanged.
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "36c51c35fc374986022dff805db288fbf2ef09d63f49f04ef6990d66064d0698"
    )
    assert digest == "5a830cfc005bc6674cec2895d92bd80bca5ce151e10d436ce7af0c93eaf02e8b"
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
    # Needs controller re-measure. b271 subspace dimensions left in place.
    # The merged allowlist must also name the five absolute collector areas.
    assert [item["dimension"] for item in payload["conditional_subspaces"]] == [63, 69]
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
