"""F2 root N2: bench_generate JSON is key-sorted / counter-sorted."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bench_generate_source_uses_sort_keys_and_sorted_counters() -> None:
    src = (ROOT / "scripts" / "bench_generate.py").read_text(encoding="utf-8")
    assert "sort_keys=True" in src
    assert "dict(sorted(counts.items()))" in src
    assert "dict(sorted(reasons.items()))" in src
