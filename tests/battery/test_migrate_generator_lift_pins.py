from __future__ import annotations

import hashlib
from pathlib import Path

from simulator.battery.migrate import Migrator, _store_output_file_bytes, write_outputs


_GENERATOR_SOURCE_DIRS = (
    "janaf",
    "hemingway-haas-robinson-1982-usgs-b1544",
    "robie-hemingway-fisher-1978-usgs-b1452",
    "robie-waldbaum-1968-usgs-b1259",
)
# Re-pinned from pre-move scratch commit 4d76149804d5c7e561db8968844c0eea16e622e0;
# its equivalent candidate snapshot 91fda04a23e3dcb44b915ca0ec7526018b1b5c74
# was paired against merged base e4496039e842c67a2bc1a914ae94a94d9fec3eea in
# receipt 596cc09762d344b1bae94f25109e6bab (both on Mac-Studio-256-2). The old
# 406d9c40 pin used the older writer; queue-report changes in 3c99a4492 and
# 241d8641e changed migration-report.md, so the digest moved with the writer,
# not the consolidation.
_BASELINE_STORE_SHA256 = (
    "84404b759339cc9624808dcdfaa5cc984afea7ccac48fb8d65cb4715e2623262"
)


def _isolated_generator_store(root: Path, output_root: Path) -> tuple[str, int, int]:
    compilation_root = root / "data" / "literature" / "compilations"
    migrator = Migrator(root)
    source_count = 0
    for source_dir in _GENERATOR_SOURCE_DIRS:
        directory = compilation_root / source_dir
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in {".yaml", ".yml", ".json"}:
                migrator._migrate_compilation_file(path)
                source_count += 1

    write_outputs(migrator.result, output_root)
    digest = hashlib.sha256()
    for rel_path, contents in sorted(_store_output_file_bytes(output_root).items()):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(contents)
    return digest.hexdigest(), source_count, len(migrator.result.observations)


def test_generator_backed_compilation_sources_keep_store_bytes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    digest, source_count, observation_count = _isolated_generator_store(
        root, tmp_path
    )

    assert source_count > 0
    assert observation_count > 0
    assert digest == _BASELINE_STORE_SHA256, (
        f"isolated generator store SHA-256: {digest}; sources={source_count}; "
        f"observations={observation_count}"
    )
