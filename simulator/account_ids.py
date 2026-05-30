"""Shared process account identifiers."""

OXYGEN_SPECIES = "O2"
OXYGEN_STAGE0_ACCOUNT = "terminal.oxygen_stage0_stored"
OXYGEN_MELT_OFFGAS_ACCOUNT = "terminal.oxygen_melt_offgas_stored"
OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT = (
    "terminal.oxygen_melt_offgas_vented_to_vacuum"
)
OXYGEN_MRE_ANODE_ACCOUNT = "terminal.oxygen_mre_anode_stored"
CHROMIUM_CONDENSED_OXIDE_ACCOUNT = "terminal.chromium_condensed_oxide_stored"
OXYGEN_STORED_ACCOUNTS = (
    OXYGEN_STAGE0_ACCOUNT,
    OXYGEN_MELT_OFFGAS_ACCOUNT,
    OXYGEN_MRE_ANODE_ACCOUNT,
)
OXYGEN_VENTED_ACCOUNTS = (OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT,)
