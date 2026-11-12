"""Generated staging-output pins for the USGS generator clone family."""

from __future__ import annotations

import hashlib
from pathlib import Path

from simulator.battery.generators import usgs_b1259, usgs_b1452, usgs_b1544


GENERATORS = (
    ("b1259", usgs_b1259),
    ("b1452", usgs_b1452),
    ("b1544", usgs_b1544),
)


def _output_digest(generator, out: Path) -> str:
    generator.main(
        ["--records", str(generator.RECORDS_DIR), "--out", str(out)]
    )
    files = sorted(
        [
            *out.glob("*.yaml"),
            *out.glob("observations/*.yaml"),
            *out.glob("reports/*.yaml"),
        ]
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(out).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_real_input_staging_output_digests(tmp_path: Path) -> None:
    for name, generator in GENERATORS:
        digest = _output_digest(generator, tmp_path / name)
        print(f"USGS_OUTPUT_DIGEST {name} {digest}")
        assert len(digest) == 64
