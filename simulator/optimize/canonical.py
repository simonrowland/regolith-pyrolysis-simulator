"""Compatibility alias: canonical JSON helpers live in simulator.canonical.

Layer rule: production must not import the fenced optimize/ package.
"""

from __future__ import annotations

import sys

from simulator import canonical as _canonical

sys.modules[__name__] = _canonical
