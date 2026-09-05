"""Compatibility alias: recipe types live in simulator.recipe.

Layer rule: production must not import the fenced optimize/ package.
"""

from __future__ import annotations

import sys

from simulator import recipe as _recipe

sys.modules[__name__] = _recipe
