# Agent instructions

## Engine-result cache

The 2026-11-08 owner ruling is that each cache key includes an opt-in, hand-bumped `ENGINE_CHEMISTRY_VERSION`; runtime identity is metadata for diagnosis only and never expires or skips a cached entry. See `simulator/engine_cache.py` for the bump procedure, dependency propagation, and adapter data dependencies.
