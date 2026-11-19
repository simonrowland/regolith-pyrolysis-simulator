"""MELTS oxide basis and spelling aliases.

AlphaMELTS and VapoRock both need this table. It lives here so neither
adapter imports the other to read it. ``engines/alphamelts/domain.py``
keeps a separate provider map so that package can import without this
adapter layer; that map is not this one.
"""

from __future__ import annotations

from simulator.feedstock_composition import _TOTAL_IRON_AS_FEO_KEYS

MELTS_OXIDE_BASIS = (
    'SiO2', 'TiO2', 'Al2O3', 'FeO', 'Fe2O3', 'MgO', 'CaO',
    'Na2O', 'K2O', 'Cr2O3', 'MnO', 'P2O5', 'NiO', 'CoO',
)
MELTS_OXIDE_ALIASES = {
    oxide.lower(): oxide
    for oxide in MELTS_OXIDE_BASIS
}
MELTS_OXIDE_ALIASES.update({
    'feo_total': 'FeO_total',
    'feototal': 'FeO_total',
    'feo_tot': 'FeO_total',
})
MELTS_OXIDE_ALIASES.update({
    key.lower(): 'FeO_total' for key in _TOTAL_IRON_AS_FEO_KEYS
})
