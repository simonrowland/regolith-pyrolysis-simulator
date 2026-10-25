"""Production resolver outputs at integrated base 1660bc715."""
# d-254/d-257 C11 actual pool film and declared 30 m/s default.
# Fresh production resolver output; pre-C11 owner restores every old leaf.
# C7 R4: production capture after the pinned SI gas-constant and admissible inverse moves.
EXPECTED = {'asteroid': {'carrier_report': {},
              'composition': {'Fe': '0x1.dda43457d09d8p-5',
                              'O2': '0x1.a0ce35c9eb00fp-3'},
              'pressure': '0x1.0c1ba16fef943p-2'},
 'core': ['0x0.0p+0', '0x1.1f6de980a026cp-3', '0x1.b48887b13262ep-1'],
 'inverse': ['0x0.0p+0', '0x1.1ba2662ed17b7p-10', '0x1.c262866b623b4p-1'],
 'mars': {'carrier_report': {'carrier_identity': 'CO2',
                             'p_carrier_bar': '0x1.4e3bcd35a8587p-7'},
          'composition': {'CO2': '0x1.4666666666666p+3',
                          'Fe': '0x1.dda43457d09d8p-5',
                          'O2': '0x1.a0ce35c9eb00fp-3'},
          'pressure': '0x1.4ec74371e5e30p+3'},
 'moon': {'carrier_report': {'carrier_identity': 'N2',
                             'p_carrier_bar': '0x1.47ae147ae147bp-7'},
          'composition': {'Fe': '0x1.dda43457d09d8p-5',
                          'N2': '0x1.4000000000000p+3',
                          'O2': '0x1.a0ce35c9eb00fp-3'},
          'pressure': '0x1.4860dd0b7f7cap+3'},
 'native_fe': {'feo_available_mol': '0x1.5bf9e650df42cp+10',
               'native_fe_frac': '0x1.c6e8d44bdc220p-1',
               'native_fe_mol': '0x1.352cd4bf1ab29p+10',
               'native_fe_state': {'native_fe_activity': '0x1.0000000000000p+0',
                                   'native_fe_activity_source': 'Holzheid1997 '
                                                                'DOI '
                                                                '10.1016/S0009-2541(97)00030-2 '
                                                                'FeO(l)=Fe+0.5O2 '
                                                                'saturation',
                                   'native_fe_frac': '0x1.c6e8d44bdc220p-1',
                                   'native_fe_pure_feo_iw_log': '-0x1.06d3328f7f984p+3',
                                   'native_fe_saturation': True,
                                   'native_fe_saturation_activity': '0x1.a2e1792668552p-7',
                                   'native_fe_threshold': 'FeO_activity_saturation'},
               'total_fe_mol': '0x1.5bf9e650df42cp+10'},
 'native_fe_source': [{'extent': {'feo_available_mol': '0x1.26e0a4f105780p+11',
                                  'native_fe_frac': '0x1.988c1fdb0ebe3p-7',
                                  'native_fe_mol': '0x1.d6976e6bdba7ep+4',
                                  'native_fe_state': {'native_fe_activity': '0x1.a346414c233e9p-2',
                                                      'native_fe_activity_source': 'Holzheid1997 '
                                                                                   'DOI '
                                                                                   '10.1016/S0009-2541(97)00030-2 '
                                                                                   'FeO(l)=Fe+0.5O2 '
                                                                                   'saturation',
                                                      'native_fe_frac': '0x1.988c1fdb0ebe3p-7',
                                                      'native_fe_pure_feo_iw_log': '-0x1.06d3328f7f984p+3',
                                                      'native_fe_saturation': True,
                                                      'native_fe_saturation_activity': '0x1.9df161593811bp-2',
                                                      'native_fe_threshold': 'FeO_activity_saturation'},
                                  'total_fe_mol': '0x1.26e0a4f105780p+11'},
                       'partition': {'P_bulk_Pa': '0x1.999999999999ap-4',
                                     'P_eq_Pa': '0x1.eece5132f09b6p+3',
                                     'P_reference_Antoine_Pa': '0x1.eece5132f09b6p+3',
                                     'activity_factor': '0x1.0000000000000p+0',
                                     'alpha_Fe': '0x1.47ae147ae147bp-6',
                                     'alpha_s_evaluation': {'alpha_s': '0x1.47ae147ae147bp-6',
                                                            'alpha_s_extrapolated': False,
                                                            'alpha_s_form': 'scalar',
                                                            'alpha_s_temperature_K': '0x1.d44999999999ap+10',
                                                            'species': 'Fe'},
                                     'alpha_source': 'REF-016 Costa & Jacobson '
                                                     '2015 KEMS Fo93Fa7 '
                                                     'olivine, Fe+ '
                                                     'alpha=0.011-0.020 at '
                                                     '1700-1800 K; Ebel 2005 '
                                                     'calculated Fe/FeO '
                                                     'alpha~0.2 noted as '
                                                     'non-measured high-side '
                                                     'proxy',
                                     'capacity_allocation_rule': 'pool_first_residual',
                                     'carrier_gas': 'N2',
                                     'native_fe_pool_mol': '0x1.0000000000000p+0',
                                     'native_fe_tap_mol': '0x1.02cfb05daeb88p-4',
                                     'native_fe_vapor_capacity_kg_hr': '0x1.ac935b04e587ap-5',
                                     'native_fe_vapor_capacity_mol_hr': '0x1.dfa609f44a28fp-1',
                                     'native_fe_vapor_escape_fraction_of_pool': '0x1.dfa609f44a28fp-1',
                                     'native_fe_vapor_mol': '0x1.dfa609f44a28fp-1',
                                     'native_pool_activity_argument': 'a_Fe(pool) '
                                                                      '~= 1 '
                                                                      'outcompetes '
                                                                      'dilute '
                                                                      'melt '
                                                                      'FeO '
                                                                      'activity',
                                     'ordinary_melt_fe_residual_capacity_mol_hr': '0x0.0p+0',
                                     'overhead_pressure_pa': '0x1.fe0cccccccccdp+9',
                                     'overhead_pressure_source': '_melt_headspace_composition_mbar',
                                     'series_resistance': {'Kn': '0x1.cb4011b01bceap-15',
                                                           'R_gas_fraction': '0x1.b1d37df17d4c0p-1',
                                                           'R_interface_fraction': '0x1.38b2083a0ad00p-3',
                                                           'R_melt_fraction': '0x0.0p+0',
                                                           'alpha_eff': '0x1.90400a87bbeb9p-9',
                                                           'alpha_effective': '0x1.90400a87bbeb9p-9',
                                                           'alpha_intrinsic': '0x1.47ae147ae147bp-6',
                                                           'authority_class': 'upper-bound',
                                                           'authority_reason': 'missing-species-state-dependent-melt-transfer-inputs',
                                                           'axial_stir_applied': '0x1.8000000000000p+2',
                                                           'axial_stir_clamped': False,
                                                           'd_ab_m2_s': '0x1.03b582b488b64p-5',
                                                           'flux_kg_s_m2': '0x1.2949be1fd84d3p-15',
                                                           'frozen_skull_stir_ceiling': 'not_certified',
                                                           'frozen_skull_stir_clamped': False,
                                                           'gas_resistance_weight': '0x1.0000000000000p+0',
                                                           'k_hk_kg_s_m2_pa': '0x1.8c112cf8db0d9p-11',
                                                           'k_mt_kg_s_m2_pa': '0x1.6d69d979dc796p-19',
                                                           'knudsen_number': '0x1.cb4011b01bceap-15',
                                                           'limiting_resistance_label': 'gas',
                                                           'melt_resistance_enabled': False,
                                                           'melt_surface_renewal_base_kg_s_m2_pa': '0x0.0p+0',
                                                           'melt_surface_renewal_source': 'disabled:missing-species-state-dependent-melt-transfer-inputs',
                                                           'r_gas': '0x1.66b1edfa43c2bp+18',
                                                           'r_interface': '0x1.028ac41d9b957p+16',
                                                           'r_melt': '0x0.0p+0',
                                                           'radial_stir_applied': '0x1.0000000000000p+0',
                                                           'radial_stir_clamped': False,
                                                           'source_film_length_m': '0x1.47ae147ae147bp-4',
                                                           'surface_film': {'D_m2_s': '0x1.03b582b488b64p-5',
                                                                            'Kn_film': '0x1.f0b752938a254p-11',
                                                                            'Kn_geometry': '0x1.cb4011b01bceap-15',
                                                                            'Mach': '0x1.1690ab10be5b2p-5',
                                                                            'Pe': '0x1.55e120ba7fe76p+9',
                                                                            'Re': '0x1.4f2446a96bd46p+9',
                                                                            'Sc': '0x1.052589d539a8ap+0',
                                                                            'Sh': '0x1.14e27478fa171p+4',
                                                                            'authority_status': 'status_bearing_non_authoritative',
                                                                            'binary_approximation': 'independent '
                                                                                                    'dilute '
                                                                                                    'species; '
                                                                                                    'not '
                                                                                                    'coupled '
                                                                                                    'multicomponent '
                                                                                                    'certification',
                                                                            'dilute_flux_kg_s_m2': '0x1.2949be1fd84d3p-15',
                                                                            'domain_flags': ['surface '
                                                                                             'flow '
                                                                                             'undeclared: '
                                                                                             'default '
                                                                                             '30 '
                                                                                             'm/s '
                                                                                             'planar '
                                                                                             'recirculation '
                                                                                             'fan '
                                                                                             '(assumed '
                                                                                             'hardware)',
                                                                                             'momentum '
                                                                                             'layer '
                                                                                             'exceeds '
                                                                                             '10% '
                                                                                             'of '
                                                                                             'flow '
                                                                                             'length'],
                                                                            'flow_assumed': True,
                                                                            'formula_id': 'Blasius_plate_area_mean_0.664',
                                                                            'gas_face_partial_pressure_pa': '0x1.a3bef36c156b3p+3',
                                                                            'gas_face_vapour_fraction': '0x1.a559c48bb73ddp-7',
                                                                            'geometry': 'planar',
                                                                            'geometry_parameters': {'coverage': '0x1.0000000000000p+0',
                                                                                                    'declared_length_m': '0x1.71f471f598449p-1',
                                                                                                    'impeller_material_id': 'dense_alumina_continuous',
                                                                                                    'impeller_service_T_C': '0x1.a900000000000p+10',
                                                                                                    'pool_area_m2': '0x1.a3e6c6fd139b4p-2'},
                                                                            'k_g_m_s': '0x1.84bf3d4378b4dp-1',
                                                                            'length_m': '0x1.71f471f598449p-1',
                                                                            'mu_Pa_s': '0x1.f1c67688076b1p-15',
                                                                            'rho_kg_m3': '0x1.e0ff0e96151fep-10',
                                                                            'stefan_model': 'dilute_passive_scalar',
                                                                            'still_gas_comparison': {'domain_flags': ['declared '
                                                                                                                      'zero '
                                                                                                                      'flow: '
                                                                                                                      'isolated-disk '
                                                                                                                      'halfspace'],
                                                                                                     'flux_kg_s_m2': '0x1.954dcc5044e7cp-18',
                                                                                                     'formula_id': 'isolated_disk_halfspace_8_over_pi',
                                                                                                     'k_g_m_s': '0x1.c9a25498db347p-4',
                                                                                                     'rate_multiplier': '0x1.778c8d1d62cd5p+2',
                                                                                                     'stefan_model': 'binary_stagnant_carrier_logarithmic'},
                                                                            'u_m_s': '0x1.e000000000000p+4',
                                                                            'velocity_provenance': 'd-257 '
                                                                                                   'assumed '
                                                                                                   'recirculation '
                                                                                                   'hardware'},
                                                           'transport_length_m': '0x1.71f471f598449p-1'},
                                     'source_label': 'native_fe_partition:pure_component_derived_from_evaluation',
                                     'temperature_K': '0x1.d44999999999ap+10'},
                       'pressure_bar': '0x1.4e4430b178b37p-7'},
                      {'extent': {'feo_available_mol': '0x1.26e0a4f105780p+11',
                                  'native_fe_frac': '0x1.988c22bea4f90p-7',
                                  'native_fe_mol': '0x1.d69771bfc32f6p+4',
                                  'native_fe_state': {'native_fe_activity': '0x1.a3464155ca0e4p-2',
                                                      'native_fe_activity_source': 'Holzheid1997 '
                                                                                   'DOI '
                                                                                   '10.1016/S0009-2541(97)00030-2 '
                                                                                   'FeO(l)=Fe+0.5O2 '
                                                                                   'saturation',
                                                      'native_fe_frac': '0x1.988c22bea4f90p-7',
                                                      'native_fe_pure_feo_iw_log': '-0x1.06d3328f7f984p+3',
                                                      'native_fe_saturation': True,
                                                      'native_fe_saturation_activity': '0x1.9df161593811bp-2',
                                                      'native_fe_threshold': 'FeO_activity_saturation'},
                                  'total_fe_mol': '0x1.26e0a4f105780p+11'},
                       'partition': {'P_bulk_Pa': '0x1.999999999999ap-4',
                                     'P_eq_Pa': '0x1.eece5132f09b6p+3',
                                     'P_reference_Antoine_Pa': '0x1.eece5132f09b6p+3',
                                     'activity_factor': '0x1.0000000000000p+0',
                                     'alpha_Fe': '0x1.47ae147ae147bp-6',
                                     'alpha_s_evaluation': {'alpha_s': '0x1.47ae147ae147bp-6',
                                                            'alpha_s_extrapolated': False,
                                                            'alpha_s_form': 'scalar',
                                                            'alpha_s_temperature_K': '0x1.d44999999999ap+10',
                                                            'species': 'Fe'},
                                     'alpha_source': 'REF-016 Costa & Jacobson '
                                                     '2015 KEMS Fo93Fa7 '
                                                     'olivine, Fe+ '
                                                     'alpha=0.011-0.020 at '
                                                     '1700-1800 K; Ebel 2005 '
                                                     'calculated Fe/FeO '
                                                     'alpha~0.2 noted as '
                                                     'non-measured high-side '
                                                     'proxy',
                                     'capacity_allocation_rule': 'pool_first_residual',
                                     'carrier_gas': 'N2',
                                     'native_fe_pool_mol': '0x1.0000000000000p+0',
                                     'native_fe_tap_mol': '0x1.01bc5dc39a280p-3',
                                     'native_fe_vapor_capacity_kg_hr': '0x1.8fe8c25c79089p-5',
                                     'native_fe_vapor_capacity_mol_hr': '0x1.bf90e88f19760p-1',
                                     'native_fe_vapor_escape_fraction_of_pool': '0x1.bf90e88f19760p-1',
                                     'native_fe_vapor_mol': '0x1.bf90e88f19760p-1',
                                     'native_pool_activity_argument': 'a_Fe(pool) '
                                                                      '~= 1 '
                                                                      'outcompetes '
                                                                      'dilute '
                                                                      'melt '
                                                                      'FeO '
                                                                      'activity',
                                     'ordinary_melt_fe_residual_capacity_mol_hr': '0x0.0p+0',
                                     'overhead_pressure_pa': '0x1.2c00000000000p+10',
                                     'overhead_pressure_source': 'melt.p_total_mbar',
                                     'series_resistance': {'Kn': '0x1.8666a7f41b64ap-15',
                                                           'R_gas_fraction': '0x1.b70e149c2d56ap-1',
                                                           'R_interface_fraction': '0x1.23c7ad8f4aa59p-3',
                                                           'R_melt_fraction': '0x0.0p+0',
                                                           'alpha_eff': '0x1.757a77c1a73a5p-9',
                                                           'alpha_effective': '0x1.757a77c1a73a5p-9',
                                                           'alpha_intrinsic': '0x1.47ae147ae147bp-6',
                                                           'authority_class': 'upper-bound',
                                                           'authority_reason': 'missing-species-state-dependent-melt-transfer-inputs',
                                                           'axial_stir_applied': '0x1.8000000000000p+2',
                                                           'axial_stir_clamped': False,
                                                           'd_ab_m2_s': '0x1.b98c72e9fa5a7p-6',
                                                           'flux_kg_s_m2': '0x1.1567366940015p-15',
                                                           'frozen_skull_stir_ceiling': 'not_certified',
                                                           'frozen_skull_stir_clamped': False,
                                                           'gas_resistance_weight': '0x1.0000000000000p+0',
                                                           'k_hk_kg_s_m2_pa': '0x1.8c112cf8db0d9p-11',
                                                           'k_mt_kg_s_m2_pa': '0x1.50e93d4b84bccp-19',
                                                           'knudsen_number': '0x1.8666a7f41b64ap-15',
                                                           'limiting_resistance_label': 'gas',
                                                           'melt_resistance_enabled': False,
                                                           'melt_surface_renewal_base_kg_s_m2_pa': '0x0.0p+0',
                                                           'melt_surface_renewal_source': 'disabled:missing-species-state-dependent-melt-transfer-inputs',
                                                           'r_gas': '0x1.850a52a1f4b1cp+18',
                                                           'r_interface': '0x1.028ac41d9b957p+16',
                                                           'r_melt': '0x0.0p+0',
                                                           'radial_stir_applied': '0x1.0000000000000p+0',
                                                           'radial_stir_clamped': False,
                                                           'source_film_length_m': '0x1.47ae147ae147bp-4',
                                                           'surface_film': {'D_m2_s': '0x1.b98c72e9fa5a7p-6',
                                                                            'Kn_film': '0x1.c9f8d97457fc9p-11',
                                                                            'Kn_geometry': '0x1.8666a7f41b64ap-15',
                                                                            'Mach': '0x1.1690ab10be5b2p-5',
                                                                            'Pe': '0x1.922bf064f04d7p+9',
                                                                            'Re': '0x1.8a3ee688e973cp+9',
                                                                            'Sc': '0x1.052589d539a89p+0',
                                                                            'Sh': '0x1.2c4f0c6ded5bfp+4',
                                                                            'authority_status': 'status_bearing_non_authoritative',
                                                                            'binary_approximation': 'independent '
                                                                                                    'dilute '
                                                                                                    'species; '
                                                                                                    'not '
                                                                                                    'coupled '
                                                                                                    'multicomponent '
                                                                                                    'certification',
                                                                            'dilute_flux_kg_s_m2': '0x1.1567366940015p-15',
                                                                            'domain_flags': ['surface '
                                                                                             'flow '
                                                                                             'undeclared: '
                                                                                             'default '
                                                                                             '30 '
                                                                                             'm/s '
                                                                                             'planar '
                                                                                             'recirculation '
                                                                                             'fan '
                                                                                             '(assumed '
                                                                                             'hardware)',
                                                                                             'momentum '
                                                                                             'layer '
                                                                                             'exceeds '
                                                                                             '10% '
                                                                                             'of '
                                                                                             'flow '
                                                                                             'length'],
                                                                            'flow_assumed': True,
                                                                            'formula_id': 'Blasius_plate_area_mean_0.664',
                                                                            'gas_face_partial_pressure_pa': '0x1.a8c43873d00fap+3',
                                                                            'gas_face_vapour_fraction': '0x1.6a77a7a3ae21dp-7',
                                                                            'geometry': 'planar',
                                                                            'geometry_parameters': {'coverage': '0x1.0000000000000p+0',
                                                                                                    'declared_length_m': '0x1.71f471f598449p-1',
                                                                                                    'impeller_material_id': 'dense_alumina_continuous',
                                                                                                    'impeller_service_T_C': '0x1.a900000000000p+10',
                                                                                                    'pool_area_m2': '0x1.a3e6c6fd139b4p-2'},
                                                                            'k_g_m_s': '0x1.666cb3e1db6c6p-1',
                                                                            'length_m': '0x1.71f471f598449p-1',
                                                                            'mu_Pa_s': '0x1.f1c67688076b1p-15',
                                                                            'rho_kg_m3': '0x1.1ae949334f345p-9',
                                                                            'stefan_model': 'dilute_passive_scalar',
                                                                            'still_gas_comparison': {'domain_flags': ['declared '
                                                                                                                      'zero '
                                                                                                                      'flow: '
                                                                                                                      'isolated-disk '
                                                                                                                      'halfspace'],
                                                                                                     'flux_kg_s_m2': '0x1.5984cedb00e14p-18',
                                                                                                     'formula_id': 'isolated_disk_halfspace_8_over_pi',
                                                                                                     'k_g_m_s': '0x1.8506f19993f15p-4',
                                                                                                     'rate_multiplier': '0x1.9b1073e52bda0p+2',
                                                                                                     'stefan_model': 'binary_stagnant_carrier_logarithmic'},
                                                                            'u_m_s': '0x1.e000000000000p+4',
                                                                            'velocity_provenance': 'd-257 '
                                                                                                   'assumed '
                                                                                                   'recirculation '
                                                                                                   'hardware'},
                                                           'transport_length_m': '0x1.71f471f598449p-1'},
                                     'source_label': 'native_fe_partition:pure_component_derived_from_evaluation',
                                     'temperature_K': '0x1.d44999999999ap+10'},
                       'pressure_bar': '0x1.89374bc6a7efap-7'}],
 'overhead': {'Fe': '0x1.1f6de980a026cp-3',
              'Kr': '0x1.6bc71bbe54a7cp+1',
              'O2': '0x1.b48887b13262fp-1'},
 'picard': {'bled': {'O2': '0x1.0000000000000p-2'},
            'deltas': ['0x1.c1ffffffffffcp+7',
                       '0x1.67fffffffffc0p+3',
                       '0x1.1fffffffffc00p-1',
                       '0x1.ccccccccc8000p-6',
                       '0x1.70a3d70a80000p-10',
                       '0x1.26e978d800000p-14',
                       '0x1.d7dbf40000000p-19',
                       '0x1.797cc00000000p-23'],
            'iterations': 8,
            'partials': {'O2': '0x1.6524924a8c154p+9'}},
 'staged': {'Kn': '0x1.7547acf7533dep-12',
            'composition': {'Kr': '0x1.4000000000000p+3',
                            'O2': '0x1.999999999999ap-3'},
            'pressure': '0x1.4666666666666p+3'}}

BLEED_SHORTFALL = {0.0: {'credits': None,
       'debits': None,
       'diagnostic': {'bled_species_mol': {},
                      'candidate_bled_species_mol': {},
                      'capacity_shortfall': {'achieved_pressure_pa': '0x1.b8a363069f1c0p+17',
                                             'allowed_outlet_kg': 0,
                                             'reason': 'duct_capacity_shortfall',
                                             'requested_outlet_kg': '0x1.a997a8fe916afp-5'},
                      'o2_held_mol': '0x0.0p+0'}},
 1e-05: {'credits': {'terminal.offgas': {'Ar': '0x1.3f4f14477141ep-3'},
                     'terminal.oxygen_melt_offgas_stored': {'O2': '0x1.62c9a4c128821p-4'}},
         'debits': {'process.overhead_gas': {'Ar': '0x1.3f4f14477141ep-3',
                                             'O2': '0x1.62c9a4c128821p-4'}},
         'diagnostic': {'bled_o2_kg': '0x1.6b47a56bd992ap-9',
                        'bled_o2_mol': '0x1.62c9a4c128821p-4',
                        'bled_species_mol': {'Ar': '0x1.3f4f14477141ep-3',
                                             'O2': '0x1.62c9a4c128821p-4'},
                        'bled_total_kg': '0x1.26e978d4fdf3cp-7',
                        'candidate_bled_o2_mol': '0x1.62c9a4c128821p-4',
                        'candidate_bled_species_mol': {'Ar': '0x1.3f4f14477141ep-3',
                                                       'O2': '0x1.62c9a4c128821p-4'},
                        'capacity_shortfall': {'achieved_pressure_pa': '0x1.82c9e0ed6f66cp+17',
                                               'allowed_outlet_kg': '0x1.26e978d4fdf3cp-7',
                                               'reason': 'duct_capacity_shortfall',
                                               'requested_outlet_kg': '0x1.a997a8fe916afp-5'},
                        'external_o2_bled_mol': '0x0.0p+0',
                        'external_o2_holdup_mol': '0x0.0p+0',
                        'external_o2_vented_kg': '0x0.0p+0',
                        'external_o2_vented_mol': '0x0.0p+0',
                        'melt_o2_bled_mol': '0x1.62c9a4c128821p-4',
                        'melt_o2_vented_mol': '0x0.0p+0',
                        'o2_admitted_mol': '0x1.62c9a4c128821p-4',
                        'o2_held_mol': '0x0.0p+0',
                        'o2_relieved_mol': '0x0.0p+0',
                        'o2_stored_mol': '0x1.62c9a4c128821p-4',
                        'o2_vented_kg': '0x0.0p+0',
                        'o2_vented_mol': '0x0.0p+0',
                        'p_ref_Pa': None}}}
