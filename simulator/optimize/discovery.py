"""Discovery of native optimizer result stores under a runs root."""

from __future__ import annotations

from pathlib import Path

from simulator.optimize.import_bundle import is_imported_path
from simulator.optimize.save_bundle import OPTIMIZER_CACHE_NAME

OPTIMIZER_JOBS_DIR_NAME = "jobs"


def optimizer_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []

    run_dirs: list[Path] = []
    if (root / OPTIMIZER_CACHE_NAME).is_file() and not is_imported_path(root, root):
        run_dirs.append(root)

    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and not is_imported_path(child, root)
            and (child / OPTIMIZER_CACHE_NAME).is_file()
        ):
            run_dirs.append(child)

    jobs_root = root / OPTIMIZER_JOBS_DIR_NAME
    if jobs_root.is_dir():
        for child in sorted(jobs_root.iterdir()):
            if (
                child.is_dir()
                and not is_imported_path(child, root)
                and (child / OPTIMIZER_CACHE_NAME).is_file()
            ):
                run_dirs.append(child)

    unique = {path.resolve(): path for path in run_dirs}
    return sorted(
        unique.values(),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
