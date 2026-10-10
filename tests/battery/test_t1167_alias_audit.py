"""t1167 audit: mixed or identity-incomplete aliases stay typed refusals."""

from simulator.battery.migrate import map_quantity


def test_pure_alias_candidates_without_a_single_complete_identity_stay_refused() -> None:
    unsupported = (
        "activity_and_raoultian_gamma",
        "zinc_isotope_delta_and_concentration",
        "closed_system_cd_volatility",
        "closed_system_tl_volatility",
        "delta_53Cr_and_bulk_Cr_MgO",
        "delta_53Cr_vs_Cr_and_MgO",
        "enthalpy_of_formation",
        "evaporated_residue_isotope_compositions",
        "invariant_arrest_temperatures_and_relative_heat",
        "invariant_reaction_and_solidus_liquidus_temperatures",
        "ion_current_ratio_and_fragment_fraction",
        "readable_table2_fraction_evaporated_and_Mg_isotopes",
        "relative_ion_intensity",
    )

    for name in unsupported:
        quantity, reason = map_quantity(None, {"quantity": name})
        assert quantity.is_unknown, name
        assert reason and "unsupported quantity" in reason, name
