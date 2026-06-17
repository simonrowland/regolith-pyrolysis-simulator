"""Shared product-pool policy for composition-target profile gates."""

from __future__ import annotations


STREAM_PRODUCT_POOLS = frozenset({"captured_products", "captured_stage_3_silica"})
MELT_PRODUCT_POOLS = frozenset(
    {
        "cleaned_melt_at_stage0_exit",
        "residual_rump_at_stop",
        "terminal_rump_earned",
    }
)
COMPOSITION_PRODUCT_POOLS = STREAM_PRODUCT_POOLS | MELT_PRODUCT_POOLS
MELT_POOL_FORBIDDEN_GATES = ("delivered_stream_purity",)


def product_pool_class(pool: str) -> str:
    if pool in STREAM_PRODUCT_POOLS:
        return "stream"
    if pool in MELT_PRODUCT_POOLS:
        return "melt"
    raise ValueError(f"unclassified product pool {pool!r}")


def forbidden_gates_for_pool(pool: str) -> tuple[str, ...]:
    if product_pool_class(pool) == "melt":
        return MELT_POOL_FORBIDDEN_GATES
    return ()
