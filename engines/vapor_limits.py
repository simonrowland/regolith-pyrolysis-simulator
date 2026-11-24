"""Shared limits for builtin vapor calculations.

The 400 K floor avoids extrapolating vapor-pressure fits below their physical
useful range. This leaf has no imports so simulator diagnostics can share the
limit without importing builtin providers.
"""

BUILTIN_VAPOR_BELOW_400K_LIMIT_K = 400.0
