"""b-1080: vapour-species quantities use gas as their phase."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from simulator.battery.migrate import REPO_ROOT
from simulator.diagnostic_helpers.extract_reproduction import observation_system_class

_NON_VAPOUR_QUANTITIES = {
    "ab_initio_enthalpy_of_formation",
    # Sossi Table 4 values are liquid-oxide activity coefficients; gas_species
    # names the vaporization reaction partner, not the measured species.
    "activity_coefficient_this_work",
    "calibration_reference_gas_composition",
    "condensed_sample_mass",
    "deposit_edx_wt_pct_ranges",
    "equilibrium_molecular_oxygen_yield",
    "gibbs_table",
    "major_gas_speciation_by_environment",
    "not_reported_among_detected_species",
    "one_percent_evaporation_temperature",
    "open_furnace_residue_composition_vs_time",
    "reduced_partition_function_ratio_prefactor",
    "relation",
    "residue_component_composition",
    "residue_composition_vs_time",
    "solar_wind_helium_inventory",
    "free_evap_sio2_loss_with_vf",
}
_CONDENSED_ACTIVITY_MARKERS = ("condensed_component", "condensed_phase")
_EXPECTED_SYSTEM_CLASS_BY_OBSERVATION = {
    ("bencze-yazhenskikh-2016.yaml", "bencze_2016_table_s1_k_all_subsamples"): "silicate_melt",
    ("bencze-yazhenskikh-2016.yaml", "bencze_2016_table_s1_na_all_subsamples"): "silicate_melt",
    ("bencze-yazhenskikh-2016.yaml", "bencze_2016_table_s2_k_selected_means"): "silicate_melt",
    ("bencze-yazhenskikh-2016.yaml", "bencze_2016_table_s2_na_selected_means"): "silicate_melt",
    ("kems-003-pound-1972.yaml", "pound_1972_ag_solid_alpha_wessel_mp"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_b_mp_alpha_burns"): "silicate_melt",
    ("kems-003-pound-1972.yaml", "pound_1972_be_solid_alpha_holden"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_c_graphite_alpha_marshall_norton"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_cr_solid_alpha_mccabe"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_pt_condensation_alpha_chupka_1500k"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_rh_condensation_alpha_chupka_1500k"): "pure_element_condensed",
    ("kems-003-pound-1972.yaml", "pound_1972_w_condensation_alpha_chupka_2200k"): "pure_element_condensed",
    ("kems-008-schaefer-fegley-2004.yaml", "schaefer_fegley_2004_sio_alpha_s_survey"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_ag_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_cd_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_cr_logKstar_table3_CrO_ox_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_cu_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_ga_logKstar_table3_GaO_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_ga_logKstar_table3_Ga_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_ge_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_k_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_li_logKstar_table3_LiO_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_li_logKstar_table3_Li_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_mo_logKstar_table3_MoO2_ox_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_mo_logKstar_table3_MoO3_congruent_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_na_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_pb_logKstar_table3_PbO_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_pb_logKstar_table3_Pb_g_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_rb_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-012-sossi-2019.yaml", "sossi_2019_zn_logKstar_table3_quoted_20260906"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_al2o2_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_al2o_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_al_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_alo_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_ca_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_cao_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_fe_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_feo_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_k_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_mg_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_mgo_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_na_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_o2_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_o_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_po2_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_po_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_sio_psat_figs_figure_only"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_tio2_detected_not_tabulated"): "silicate_melt",
    ("kems-025-markova-1983.yaml", "markova_1983_tio_psat_figs_figure_only"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_Po2_table_calculated"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig1_Murchison_Al_digitized"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig1_Murchison_Ca_digitized"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig1_Murchison_Mg_digitized"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig1_Murchison_SiO_digitized"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig1_murchison_psat_figure_only"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_fig2_CAI_psat_figure_only"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Al_max_2100C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Ca_1825C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Ca_max_1950_2025C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_FeO_top_1650C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Fe_max_1450_1725C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Fe_troilite_1050C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_Mg_max_1750_1825C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_SiO_Mg_max_1750_1825C"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_murchison_TiO_gt_TiO2_at_Ca_max"): "silicate_melt",
    ("kems-028-yakovlev-1984.yaml", "yakovlev_1984_sulfur_900_1000C"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_Al_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_Ca_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_Fe_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_K_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_Mg_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_Na_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_SiO_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig1_TiO_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_Al_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_Ca_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_Fe_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_Mg_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_SiO_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "kems_029_fig2_TiO_psat_digitized"): "silicate_melt",
    ("kems-029-yakovlev-shornikov-2011.yaml", "yakovlev_shornikov_2011_Po2_table_bar_calculated"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table1_COS_calculated_p"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table1_CS_calculated_p"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table1_S2_calculated_p"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table1_SiO_calculated_p"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table1_SiS_calculated_p"): "silicate_melt",
    ("kems-047-turkdogan-1984-isij.yaml", "turkdogan_1984_table2_K_calculated_p"): "silicate_melt",
    ("kems-053-stolyarova-1991.yaml", "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation"): "silicate_melt",
    ("kems-053-stolyarova-1991.yaml", "stolyarova_1991_cao_partial_pressure_1993k_complete_evaporation"): "silicate_melt",
    ("kems-053-stolyarova-1991.yaml", "stolyarova_1991_o2_pressure_fig4"): "silicate_melt",
    ("kems-053-stolyarova-1991.yaml", "stolyarova_1991_o_atomic_partial_pressure_1933k_equilibrium"): "silicate_melt",
    ("kems-053-stolyarova-1991.yaml", "stolyarova_1991_sio_partial_pressure_1933k_complete_evaporation"): "silicate_melt",
    ("kems-058-ohara-1987.yaml", "ohara_1987_p2_table1_partial_pressure"): "molten_metal",
    ("kems-058-ohara-1987.yaml", "ohara_1987_po2_table1_partial_pressure"): "molten_metal",
    ("kems-058-ohara-1987.yaml", "ohara_1987_table1_partial_pressures_1643k"): "molten_metal",
    ("kems-093-piacente-1975.yaml", "piacente_1975_fe_cc_eq2"): "solid_solution_silicate",
    ("kems-093-piacente-1975.yaml", "piacente_1975_mg_cc_eq2"): "solid_solution_silicate",
    ("kems-093-piacente-1975.yaml", "piacente_1975_o2_cc_eq3"): "solid_solution_silicate",
    ("kems-093-piacente-1975.yaml", "piacente_1975_sio_cc_eq2"): "solid_solution_silicate",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fe_detected_kems"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fe_detected_kems_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_mg_IT_figure_only"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_mg_IT_figure_only_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_mg_slope_second_law"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_mg_slope_second_law_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio2_IT_figure_only"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio2_IT_figure_only_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio2_slope_second_law"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio2_slope_second_law_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio_IT_figure_only"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio_IT_figure_only_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio_slope_second_law"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_fig1_sio_slope_second_law_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_o2_abundant_not_quantitative"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_o2_abundant_not_quantitative_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_o_abundant_not_quantitative"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_o_abundant_not_quantitative_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_sio2_detected_kems"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_sio2_detected_kems_quoted"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_sio_detected_kems"): "silicate_melt",
    ("kems-114-nichols-1995.yaml", "nichols_1995_sio_detected_kems_quoted"): "silicate_melt",
    ("kems-133-costa-2017.yaml", "costa_2017_fig6_mg_psat_figure_only"): "solid_solution_silicate",
    ("kems-133-costa-2017.yaml", "costa_2017_fig6_o2_psat_figure_only"): "solid_solution_silicate",
    ("kems-133-costa-2017.yaml", "costa_2017_fig6_o_psat_figure_only"): "solid_solution_silicate",
    ("kems-133-costa-2017.yaml", "costa_2017_fig6_sio_psat_figure_only"): "solid_solution_silicate",
    ("ta-yamanaka-1997-metsoc.yaml", "yamanaka_1997_na_measured_evaporation_mass_fit"): "silicate_melt",
    ("tachibana-tsuchiyama-1998-forsterite-dust-lpsc.yaml", "tachibana_1998_forsterite_hkl_flux_equation"): "silicate_melt",
}

def _mapping(node: object) -> dict[str, object]:
    if not isinstance(node, MappingNode):
        return {}
    return {
        key.value: value
        for key, value in node.value
        if isinstance(key, ScalarNode)
    }


def _has_value(node: object) -> bool:
    if isinstance(node, ScalarNode):
        return node.value.strip().casefold() not in {"", "null", "none", "~"}
    if isinstance(node, SequenceNode):
        return any(_has_value(item) for item in node.value)
    return isinstance(node, MappingNode) and bool(node.value)


def _has_quantity(row: dict[str, object], values: dict[str, object]) -> bool:
    return any(
        isinstance(quantity, ScalarNode) and _has_value(quantity)
        for quantity in (
            values.get("quantity"),
            row.get("quantity"),
            row.get("type"),
        )
    )


def _quantity_name(row: dict[str, object], values: dict[str, object]) -> str | None:
    quantity = next(
        (
            candidate
            for candidate in (
                values.get("quantity"),
                row.get("quantity"),
                row.get("type"),
            )
            if isinstance(candidate, ScalarNode) and _has_value(candidate)
        ),
        None,
    )
    if not isinstance(quantity, ScalarNode):
        return None
    return quantity.value.strip().casefold().replace("-", "_")


def _has_condensed_activity_marker(
    row: dict[str, object], values: dict[str, object]
) -> bool:
    # Extract activity records use condensed_component or condensed_phase to
    # identify the phase of the measured component. condensed_form,
    # condensed_formula, and condensed_reservoir describe sample/reference
    # context and do not identify a condensed activity quantity.
    return any(
        _has_value(owner.get(marker))
        for owner in (values, row)
        for marker in _CONDENSED_ACTIVITY_MARKERS
    )


def _is_condensed_activity(
    row: dict[str, object], values: dict[str, object]
) -> bool:
    return _quantity_name(row, values) in {"activity", "activity_coefficient"} and (
        _has_condensed_activity_marker(row, values)
    )


def _is_vapour_measurement(row: dict[str, object], values: dict[str, object]) -> bool:
    name = _quantity_name(row, values)
    if name is None:
        return False
    if _is_condensed_activity(row, values):
        # Ostrovski's SiO2 activities and DeMaria's Fe activities describe a
        # condensed component/phase; gas_species identifies vapor context.
        return False
    # These quantities describe condensed samples, reference-gas fixtures, or
    # thermochemical/model records; gas_species is context in those rows.
    return name not in _NON_VAPOUR_QUANTITIES


def _has_gas_phase_identity(node: object) -> bool:
    if isinstance(node, ScalarNode):
        return re.search(r"\(\s*g\s*\)", node.value, re.IGNORECASE) is not None
    if isinstance(node, SequenceNode):
        return any(_has_gas_phase_identity(item) for item in node.value)
    if isinstance(node, MappingNode):
        return any(_has_gas_phase_identity(value) for _, value in node.value)
    return False


def _has_vapour_species(row: dict[str, object], values: dict[str, object]) -> bool:
    for owner in (values, row):
        if _has_value(owner.get("gas_species")):
            return True
        if any(
            _has_gas_phase_identity(owner.get(field))
            for field in ("species", "formula", "pressure_species")
        ):
            return True
    return False


def _offending_vapour_phase_rows() -> list[str]:
    findings: list[str] = []
    extracts = REPO_ROOT / "data/literature/extracts"
    for path in sorted(extracts.iterdir()):
        if path.suffix != ".yaml":
            continue
        root = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
        if root is None:
            continue

        def visit(node: object) -> None:
            if isinstance(node, MappingNode):
                row = _mapping(node)
                phase = row.get("phase")
                phase_name = (
                    phase.value.strip().casefold()
                    if isinstance(phase, ScalarNode)
                    else None
                )
                values = _mapping(row.get("values"))
                if (
                    isinstance(phase, ScalarNode)
                    and _has_quantity(row, values)
                    and _has_vapour_species(row, values)
                    and (
                        (
                            # casefold() makes the source tag highT_compiled
                            # read as hight_compiled; it is a range tag, not a
                            # sample, apparatus, or condensed-phase context.
                            phase_name not in {"gas", "g", "hight_compiled"}
                            and _is_vapour_measurement(row, values)
                        )
                        or (
                            phase_name in {"gas", "g"}
                            and _is_condensed_activity(row, values)
                        )
                    )
                ):
                    findings.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{phase.start_mark.line + 1}"
                    )
                for _, child in node.value:
                    visit(child)
            elif isinstance(node, SequenceNode):
                for child in node.value:
                    visit(child)

        visit(root)
    return findings


def test_vapour_species_quantity_phase_matches_measured_quantity() -> None:
    offending = _offending_vapour_phase_rows()
    assert not offending, (
        "vapour measurements require phase: gas and condensed activities must not "
        "use phase: gas; offending extract:line:\n"
        + "\n".join(offending)
    )


def test_vapour_phase_relabels_preserve_observation_system_class() -> None:
    extract_root = REPO_ROOT / "data/literature/extracts"
    observations_by_extract: dict[
        str, dict[str, tuple[str, dict[str, object]]]
    ] = {}
    for extract_name in sorted(
        {key[0] for key in _EXPECTED_SYSTEM_CLASS_BY_OBSERVATION}
    ):
        document = yaml.safe_load((extract_root / extract_name).read_text(encoding="utf-8"))
        observations_by_extract[extract_name] = {
            str(row["observation_id"]): (species_id, row)
            for species_id, block in (document.get("species") or {}).items()
            for row in (block.get("observations") or [])
            if isinstance(row, dict) and row.get("observation_id")
        }

    for (extract_name, observation_id), expected_class in (
        _EXPECTED_SYSTEM_CLASS_BY_OBSERVATION.items()
    ):
        species_id, row = observations_by_extract[extract_name][observation_id]
        system_class = observation_system_class(
            SimpleNamespace(
                species_id=species_id,
                values=row.get("values") or {},
                phase=row.get("phase"),
                regime=row.get("regime"),
                standard_state=row.get("standard_state"),
            )
        )
        assert system_class == expected_class, (extract_name, observation_id)
