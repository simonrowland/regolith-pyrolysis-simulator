"""Content-addressed parsed-store cache for slow store guards."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
from unittest.mock import patch

import simulator.battery.migrate as migrate
import simulator.yaml_cache as yaml_cache


def load_cached_test_store(
    root: Path,
    *,
    cache_root: Path,
    typed: bool = False,
) -> object:
    """Read parsed store YAML once, or pass it through the existing typed loader."""

    root = Path(root).resolve()
    literature = root / "data" / "literature"
    paths = [
        path
        for path in migrate.iter_work_store_paths(literature / "works")
        if path.name != "ALIASES.yaml"
    ]
    for directory in (
        literature / "extracts-v2",
        literature / "observations-v2",
    ):
        paths.extend(migrate.iter_observation_store_paths(directory))
    paths = sorted(set(paths))

    payloads: dict[str, bytes] = {}
    manifest = []
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        payloads[relative_path] = payload
        manifest.append((relative_path, hashlib.sha256(payload).hexdigest()))
    loader_source_hashes = {
        "migrate": hashlib.sha256(Path(migrate.__file__).read_bytes()).hexdigest(),
        "yaml_cache": hashlib.sha256(
            Path(yaml_cache.__file__).read_bytes()
        ).hexdigest(),
        "test_owner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "documents": manifest,
                "loader_source_hashes": loader_source_hashes,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cache_root = Path(cache_root)
    cache_path = cache_root / f"{cache_key}.pickle"
    try:
        with cache_path.open("rb") as handle:
            envelope = pickle.load(handle)
    except (OSError, EOFError, pickle.UnpicklingError):
        envelope = None
    documents = (
        envelope["documents"]
        if isinstance(envelope, dict) and envelope.get("key") == cache_key
        else None
    )
    if documents is None:
        previous_cache_setting = os.environ.get("REGOLITH_PARSED_YAML_CACHE")
        os.environ["REGOLITH_PARSED_YAML_CACHE"] = "off"
        try:
            documents = {
                relative_path: migrate._load_yaml_text(payload)
                for relative_path, payload in payloads.items()
            }
        finally:
            if previous_cache_setting is None:
                os.environ.pop("REGOLITH_PARSED_YAML_CACHE", None)
            else:
                os.environ["REGOLITH_PARSED_YAML_CACHE"] = previous_cache_setting
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=cache_root, prefix=".t1745-", delete=False
        ) as handle:
            pickle.dump(
                {"key": cache_key, "documents": documents},
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, cache_path)

    if not typed:
        return documents

    def cached_load_yaml(path: Path) -> object:
        return documents[path.resolve().relative_to(root).as_posix()]

    with patch.object(migrate, "load_yaml", cached_load_yaml):
        return migrate.load_migrated_store(root)
