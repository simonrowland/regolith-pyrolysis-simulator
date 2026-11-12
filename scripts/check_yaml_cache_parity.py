"""Read-only full-store parity check using a disposable parsed-YAML cache."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import (
    iter_observation_store_paths,
    iter_work_store_paths,
)
from simulator.yaml_cache import (
    YAML12SafeLoader,
    _assert_type_exact_equal,
    load_cached_safe_yaml,
)


def _store_yaml_paths(root: Path) -> tuple[Path, ...]:
    literature = root / "data" / "literature"
    works = literature / "works"
    paths = [
        path for path in iter_work_store_paths(works) if path.name != "ALIASES.yaml"
    ]
    for directory in (
        literature / "extracts-v2",
        literature / "observations-v2",
    ):
        paths.extend(iter_observation_store_paths(directory))
    legacy_extracts = literature / "extracts"
    if legacy_extracts.is_dir():
        paths.extend(sorted(legacy_extracts.glob("*.yaml")))
    return tuple(sorted(set(paths)))


def _check_store(root: Path) -> int:
    if getattr(yaml, "CSafeLoader", None) is None:
        print("whole-store YAML parity requires PyYAML's CSafeLoader", file=sys.stderr)
        return 2
    if yaml.CSafeLoader not in YAML12SafeLoader.__mro__:
        print("YAML12SafeLoader is not using CSafeLoader", file=sys.stderr)
        return 2

    paths = _store_yaml_paths(root)
    if not paths:
        print(
            f"no store YAML files found under {root / 'data/literature'}",
            file=sys.stderr,
        )
        return 2

    failures: list[str] = []
    checked = 0
    with TemporaryDirectory(
        prefix="yaml-cache-parity-", dir=os.environ.get("TMPDIR")
    ) as cache_dir:
        os.environ["REGOLITH_PARSED_YAML_CACHE_DIR"] = cache_dir
        os.environ.pop("REGOLITH_PARSED_YAML_CACHE", None)
        for path in paths:
            try:
                raw = path.read_bytes()
                cold_cache = load_cached_safe_yaml(raw)
                warm_cache = load_cached_safe_yaml(raw)
                fresh = yaml.load(raw, Loader=YAML12SafeLoader)
                _assert_type_exact_equal(cold_cache, fresh)
                _assert_type_exact_equal(warm_cache, fresh)
            except Exception as exc:
                failures.append(f"{path}: {type(exc).__name__}: {exc}")
            else:
                checked += 1

    print(
        f"whole-store YAML cache parity: checked={checked} "
        f"failures={len(failures)}"
    )
    for failure in failures[:20]:
        print(f"FAIL: {failure}", file=sys.stderr)
    if len(failures) > 20:
        print(f"... {len(failures) - 20} additional failures", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    return _check_store(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
