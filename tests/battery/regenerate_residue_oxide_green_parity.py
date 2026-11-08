#!/usr/bin/env python3
"""Regenerate frozen residue migration fingerprints from green 0418c0fd9."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
GREEN = "0418c0fd9"
FIXTURE = (
    ROOT
    / "tests/fixtures/battery/residue_oxide_green_parity_0418c0fd9.json"
)
COMMENT = (
    "Frozen write=False residue migration fingerprints from green 0418c0fd9 for "
    "ivanova-2018-spinel-rich-cai-evaporation, kems-026-markova-1984, "
    "kems-015-hashimoto-1983, kems-012-sossi-2019, "
    "kems-024-gibson-hubbard-1972, and kang-2007-sio2-activity-cas-low-silica. "
    "Regenerate with: python tests/battery/regenerate_residue_oxide_green_parity.py"
)
sys.path.insert(0, str(ROOT))

from tests.battery.test_residue_oxide_green_parity import (  # noqa: E402
    SOURCES,
    migrate_sources,
)


def _install_green_migrate(path: str) -> None:
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_loader

    class Finder:
        def find_spec(self, fullname, search_path, target=None):
            if fullname != "simulator.battery.migrate":
                return None
            loader = SourceFileLoader(fullname, path)
            return spec_from_loader(fullname, loader)

    # Keep this instance finder: it loads the pinned module rather than the
    # worktree module when migrate_sources imports simulator.battery.migrate.
    sys.meta_path.insert(0, Finder())


def _rebind_green_paths() -> None:
    import simulator.battery.migrate as migrate

    migrate.REPO_ROOT = ROOT
    migrate.LITERATURE = ROOT / "data" / "literature"
    migrate.EXTRACTS_DIR = migrate.LITERATURE / "extracts"
    migrate.COMPILATIONS_DIR = migrate.LITERATURE / "compilations"
    migrate.INDEX_PATH = migrate.LITERATURE / "INDEX.yaml"
    migrate.WORKS_DIR = migrate.LITERATURE / "works"
    migrate.EXTRACTS_V2_DIR = migrate.LITERATURE / "extracts-v2"
    migrate.OBSERVATIONS_V2_DIR = migrate.LITERATURE / "observations-v2"
    migrate.BATTERY_DIR = ROOT / "data" / "battery"
    migrate.LAB_PARAMETER_VOCAB_PATH = (
        migrate.LITERATURE / "lab_parameter_vocabulary.yaml"
    )


def _run_green_child() -> int:
    green_src = Path(os.environ["PARITY_GREEN_SRC"]).resolve()
    import simulator.battery as battery_pkg

    sys.modules.pop("simulator.battery.migrate", None)
    if hasattr(battery_pkg, "migrate"):
        delattr(battery_pkg, "migrate")
    _install_green_migrate(str(green_src))
    _rebind_green_paths()
    import simulator.battery.migrate as migrate

    loaded = Path(migrate.__file__).resolve()
    if loaded != green_src:
        raise SystemExit(f"green migrate was not loaded: {loaded}")
    payload = {
        "loaded_migrate": str(loaded),
        "sources": migrate_sources(Path(os.environ["PARITY_ROOT"])),
    }
    Path(os.environ["PARITY_OUT"]).write_text(json.dumps(payload), encoding="utf-8")
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="residue-oxide-green-parity-") as temp:
        temp_path = Path(temp)
        green_src = temp_path / "green-migrate.py"
        with green_src.open("w", encoding="utf-8") as handle:
            subprocess.run(
                ["git", "show", f"{GREEN}:simulator/battery/migrate.py"],
                cwd=ROOT,
                check=True,
                stdout=handle,
            )
        green_out = temp_path / "green.json"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PARITY_GREEN_SRC"] = str(green_src)
        env["PARITY_OUT"] = str(green_out)
        env["PARITY_ROOT"] = str(temp_path / "green")
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=ROOT,
            env=env,
            check=True,
        )
        green_payload = json.loads(green_out.read_text(encoding="utf-8"))
        if Path(green_payload["loaded_migrate"]).resolve() != green_src.resolve():
            raise RuntimeError("pinned green migration was not loaded")
        sources = green_payload["sources"]
        if tuple(sources) != SOURCES:
            raise RuntimeError(f"unexpected source ordering: {tuple(sources)!r}")

    rendered = json.dumps(
        {"_comment": COMMENT, "sources": sources}, indent=2
    ) + "\n"
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(rendered, encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)} ({len(sources)} sources)")
    return 0


if __name__ == "__main__":
    if "PARITY_GREEN_SRC" in os.environ:
        raise SystemExit(_run_green_child())
    raise SystemExit(main())
