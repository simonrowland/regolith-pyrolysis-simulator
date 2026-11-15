"""Shared account set for tests that close the native-Fe vapor route."""

from simulator.account_ids import ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT
from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS


# d-402 can place vapor Fe in the Stage-4 cartridge residue, so source-pool
# tests must include that sink with the gas, collector, offgas, and wall bins.
NATIVE_FE_ROUTE_ACCOUNTS = (
    "process.overhead_gas",
    "process.condensation_train",
    "terminal.offgas",
    "process.condensation_retained_holdup",
    ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT,
    *PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS,
)
