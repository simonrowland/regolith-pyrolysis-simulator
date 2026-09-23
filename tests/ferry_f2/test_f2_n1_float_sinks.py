"""F2 root N1: durable bench/score paths keep Decimal grain (no float sinks)."""

from __future__ import annotations

from decimal import Decimal
import math

from simulator.battery.generators import bench as bench_mod
from simulator.battery.score import MetricOperation, compute_metric


def test_dex_uses_decimal_ln_not_float_log10() -> None:
    ratio = Decimal("1.234567890123456789")
    got = compute_metric(MetricOperation.DEX, ratio, Decimal(1))
    assert got is not None
    mutated = Decimal(str(math.log10(float(ratio))))
    assert str(got) != str(mutated)
    assert len(str(got)) > len(str(mutated))


def test_bench_payload_emits_dec_str_not_float() -> None:
    text = bench_mod._dec_payload(Decimal("0.123456789012345678901234567890"))
    assert isinstance(text, str)
    assert text.startswith("0.12345678901234567890")


def test_mutation_proof_float_dex_loses_digits() -> None:
    ratio = Decimal("1.234567890123456789")
    mutated = Decimal(str(math.log10(float(ratio))))
    fixed = compute_metric(MetricOperation.DEX, ratio, Decimal(1))
    assert fixed != mutated
