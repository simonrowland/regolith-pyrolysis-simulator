"""Shared process account identifiers."""

OXYGEN_SPECIES = "O2"
METAL_PHASE_ACCOUNT = "process.metal_phase"
METAL_BOTTOM_POOL_ACCOUNT = "process.metal_phase_bottom_pool"
METAL_FLOAT_LAYER_ACCOUNT = "process.metal_phase_float_layer"
CONDENSATION_RETAINED_HOLDUP_ACCOUNT = (
    "process.condensation_retained_holdup"
)
METAL_PHASE_ACCOUNTS = (
    METAL_PHASE_ACCOUNT,
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
)
# Writer sweep (2026-07-12): evaporation._project_condensed_stage_collection
# projects condensation-train credits; extraction._project_condensed_species
# projects condensation-train or metal-phase balances. Other mutations clear.
STAGE_COLLECTION_BACKING_ACCOUNTS = (
    *METAL_PHASE_ACCOUNTS,
    "process.condensation_train",
)
OXYGEN_STAGE0_ACCOUNT = "terminal.oxygen_stage0_stored"
OXYGEN_MELT_OFFGAS_ACCOUNT = "terminal.oxygen_melt_offgas_stored"
OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT = (
    "terminal.oxygen_melt_offgas_vented_to_vacuum"
)
OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT = (
    "terminal.oxygen_bubbler_external_vented_to_vacuum"
)
OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT = "terminal.oxygen_melt_offgas_captured"
OXYGEN_MRE_ANODE_ACCOUNT = "terminal.oxygen_mre_anode_stored"
OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT = (
    "reservoir.oxygen_cistern_liquid_inventory"
)
# Debitable solid-C inventory from Stage-0 refractory organic partition
# (t-325). Distinct from terminal.stage0_residual_refractory_carbon
# (non-debitable diagnostic sink) and process.reagent_inventory C
# (operator-supplied reductant).
SOLID_CHAR_CARBON_ACCOUNT = "process.solid_char_carbon"
CHROMIUM_CONDENSED_OXIDE_ACCOUNT = "terminal.chromium_condensed_oxide_stored"
SPENT_REDUCTANT_RESIDUE_ACCOUNT = "process.spent_reductant_residue"
C7_AL_CREDIT_ACCOUNT = "process.c7_al_credit"
OXYGEN_STORED_ACCOUNTS = (
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
    OXYGEN_CISTERN_LIQUID_INVENTORY_ACCOUNT,
)
OXYGEN_VENTED_ACCOUNTS = (OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,)
OXYGEN_CAPTURED_ACCOUNTS = (OXYGEN_MELT_OFFGAS_CAPTURED_ACCOUNT,)
