"""Shared SSO-R owner recipe certification surface."""

from __future__ import annotations


OWNER_RECIPE_T_C = 1650.0
OWNER_RECIPE_PO2_MBAR = 1.0e-6
OWNER_RECIPE_PN2_MBAR = 10.0
OWNER_RECIPE_TOTAL_PRESSURE_MBAR = OWNER_RECIPE_PO2_MBAR + OWNER_RECIPE_PN2_MBAR
OWNER_RECIPE_STAGE_NAME = "alkali_early_fe"
OWNER_RECIPE_GAS_COVER_MODE = "pn2_sweep"
OWNER_CERTIFICATION_ASSERTION = "owner_pN2_recipe_point_requested_pO2_semantics"
OWNER_CERTIFIED_SURFACE_SOURCE = (
    "scripts/sso_r_validation_map.py owner_pN2_recipe_point_requested_pO2_semantics"
)
