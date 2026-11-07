from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
HIT_ROWS = {'beyer-1980-potassium-metasilicate-disilicate-ri8410': {'bey80_k2si2o5_reference_glass_S_table6': ('S',
                                                                                                    'S°(T)−S°(298.15 '
                                                                                                    'K)',
                                                                                                    'entropy '
                                                                                                    'increment '
                                                                                                    'from '
                                                                                                    '298.15 '
                                                                                                    'K',
                                                                                                    '0'),
                                                         'bey80_k2si2o5_t6_glass_extrapolated_s_800': ('S',
                                                                                                       'S°(T)−S°(298.15 '
                                                                                                       'K)',
                                                                                                       'entropy '
                                                                                                       'increment '
                                                                                                       'from '
                                                                                                       '298.15 '
                                                                                                       'K',
                                                                                                       '46.34'),
                                                         'bey80_k2si2o5_t6_glass_extrapolated_s_850': ('S',
                                                                                                       'S°(T)−S°(298.15 '
                                                                                                       'K)',
                                                                                                       'entropy '
                                                                                                       'increment '
                                                                                                       'from '
                                                                                                       '298.15 '
                                                                                                       'K',
                                                                                                       '49.65'),
                                                         'bey80_k2si2o5_t6_glass_extrapolated_s_900': ('S',
                                                                                                       'S°(T)−S°(298.15 '
                                                                                                       'K)',
                                                                                                       'entropy '
                                                                                                       'increment '
                                                                                                       'from '
                                                                                                       '298.15 '
                                                                                                       'K',
                                                                                                       '52.82'),
                                                         'bey80_k2si2o5_oxide_formation_h_table10': ('delta_fH',
                                                                                                    'K2O(c) + 2SiO2(quartz) = K2Si2O5(c)',
                                                                                                    'formation from simple oxides',
                                                                                                    None),
                                                         'bey80_k2si2o5_oxide_formation_g_table10': ('delta_fG',
                                                                                                    'K2O(c) + 2SiO2(quartz) = K2Si2O5(c)',
                                                                                                    'formation from simple oxides',
                                                                                                    None),
                                                         'bey80_k2si2o5_oxide_formation_g_table10_867_printed_minus_78_8': ('delta_fG',
                                                                                                                          'K2O(c) + 2SiO2(quartz) = K2Si2O5(c)',
                                                                                                                          'formation from simple oxides',
                                                                                                                          '-78.8'),
                                                         'bey80_k2si2o5_oxide_formation_h_table12': ('delta_fH',
                                                                                                    'K2O(c) + 2SiO2(quartz) = K2Si2O5(glass)',
                                                                                                    'formation from simple oxides',
                                                                                                    None),
                                                         'bey80_k2si2o5_oxide_formation_g_table12': ('delta_fG',
                                                                                                    'K2O(c) + 2SiO2(quartz) = K2Si2O5(glass)',
                                                                                                    'formation from simple oxides',
                                                                                                    None),
                                                         'bey80_k2sio3_oxide_formation_h_table8': ('delta_fH',
                                                                                                  'K2O(c) + SiO2(quartz) = K2SiO3(c)',
                                                                                                  'formation from simple oxides',
                                                                                                  None),
                                                         'bey80_k2sio3_oxide_formation_g_table8': ('delta_fG',
                                                                                                  'K2O(c) + SiO2(quartz) = K2SiO3(c)',
                                                                                                  'formation from simple oxides',
                                                                                                  None)},
 'he-2017-karrooite-ordering-heat-capacity': {'he2017_figure11_configurational_entropy': ('S',
                                                                                          'S_config '
                                                                                          '(J K−1)',
                                                                                          'configurational '
                                                                                          'entropy',
                                                                                          '5.9(6)')},
 'sack-ghiorso-2017-fassaite-thermodynamics': {'sack_ghiorso_2017_fig3_configurational_entropy': ('S',
                                                                                                  'Configurational '
                                                                                                  'entropy '
                                                                                                  'of '
                                                                                                  'diopside '
                                                                                                  '– '
                                                                                                  'CATS '
                                                                                                  'solid '
                                                                                                  'solutions',
                                                                                                  'configurational '
                                                                                                  'entropy',
                                                                                                  None),
                                               'sack_ghiorso_2017_fig9_configurational_entropy': ('S',
                                                                                                  'Configurational '
                                                                                                  'entropy '
                                                                                                  'of '
                                                                                                  'diopside '
                                                                                                  '– '
                                                                                                  'grossmanite '
                                                                                                  'solid '
                                                                                                  'solutions',
                                                                                                  'configurational '
                                                                                                  'entropy',
                                                                                                  None)},
 'shornikov-1997-diopside-vaporization': {'shornikov_1997_cao_partial_entropy_table4': ('S',
                                                                                        'ΔS_i(CaO)',
                                                                                        'partial '
                                                                                        'component '
                                                                                        'entropy',
                                                                                        '17.0 ± '
                                                                                        '1.5'),
                                          'shornikov_1997_mgo_partial_entropy_table4': ('S',
                                                                                        'ΔS_i(MgO)',
                                                                                        'partial '
                                                                                        'component '
                                                                                        'entropy',
                                                                                        '19.1 ± '
                                                                                        '1.5'),
                                          'shornikov_1997_sio2_partial_entropy_table4': ('S',
                                                                                         'ΔS_i(SiO2)',
                                                                                         'partial '
                                                                                         'component '
                                                                                         'entropy',
                                                                                         '23.2 ± '
                                                                                         '2.4')},
 'shornikov-2002-aluminosilicate-solid-phases': {'shornikov_2002_mullite_delta_fH': ('delta_fH',
                                                                                     'enthalpy of '
                                                                                     'formation of '
                                                                                     'mullite from '
                                                                                     'oxides',
                                                                                     'formation '
                                                                                     'from simple '
                                                                                     'oxides',
                                                                                     '12.9 ± 0.8')},
 'shornikov-2002-mullite-thermodynamics': {'shornikov_2002_fig2_oxide_formation_gibbs': ('delta_fG',
                                                                                         'Fig. 2. '
                                                                                         'Gibbs '
                                                                                         'energy '
                                                                                         'of '
                                                                                         'mullite '
                                                                                         'formation '
                                                                                         'from '
                                                                                         'oxides',
                                                                                         'formation '
                                                                                         'from '
                                                                                         'simple '
                                                                                         'oxides',
                                                                                         None)},
 'shornikov-2003-mullite-thermodynamics': {'shornikov_2003_mullite_thermodynamics_fig1_curve_02': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_03': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_05': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_07': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_09': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_10': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_11': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_12': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_13': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_14': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_15': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_16': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_curve_22': ('delta_fG',
                                                                                                   'Gibbs '
                                                                                                   'energies '
                                                                                                   'of '
                                                                                                   'formation '
                                                                                                   'of '
                                                                                                   'mullite '
                                                                                                   'from '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_01': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_04': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_06': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_08': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_17': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_18': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_19': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_20': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_21': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_fig1_marker_23': ('delta_fG',
                                                                                                    'Gibbs '
                                                                                                    'energies '
                                                                                                    'of '
                                                                                                    'formation '
                                                                                                    'of '
                                                                                                    'mullite '
                                                                                                    'from '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    None),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r02_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '– '
                                                                                                      '8.0'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r03_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '4.29'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r04_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '–15.4'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r05_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '22.2 '
                                                                                                      '± '
                                                                                                      '4.4'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r06_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.9 '
                                                                                                      '± '
                                                                                                      '2.5'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r07_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '–0.34'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r08_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '–0.87 '
                                                                                                      '± '
                                                                                                      '1.70'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r09_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '–40.5'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r10_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '4.55 '
                                                                                                      '± '
                                                                                                      '0.29'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r11_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.46 '
                                                                                                      '± '
                                                                                                      '0.92'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r12_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.37 '
                                                                                                      '± '
                                                                                                      '2.85'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r13_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.31 '
                                                                                                      '± '
                                                                                                      '0.42'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r14_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.82'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r15_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.84 '
                                                                                                      '± '
                                                                                                      '2.74'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r16_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '3.4'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r17_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '7.62 '
                                                                                                      '± '
                                                                                                      '0.44'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r18_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '6.14 '
                                                                                                      '± '
                                                                                                      '1.36'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r19_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '3.45 '
                                                                                                      '± '
                                                                                                      '0.07*'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r20_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '8.14 '
                                                                                                      '± '
                                                                                                      '0.40'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r21_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '3.1'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r22_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '1.72 '
                                                                                                      '± '
                                                                                                      '0.84'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r23_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '4.59'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r24_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '5.86'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r25_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '3.49 '
                                                                                                      '± '
                                                                                                      '0.03*'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r26_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '15.9 '
                                                                                                      '± '
                                                                                                      '0.6*'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r27_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '4.20 '
                                                                                                      '± '
                                                                                                      '0.40'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r28_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '10.74 '
                                                                                                      '± '
                                                                                                      '4.30'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r29_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '–0.81 '
                                                                                                      '± '
                                                                                                      '0.27*'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r30_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '4.56 '
                                                                                                      '± '
                                                                                                      '0.01*'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r31_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '11.05 '
                                                                                                      '± '
                                                                                                      '0.08'),
                                           'shornikov_2003_mullite_thermodynamics_p002_t01_r32_c04': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '1. '
                                                                                                      'Enthalpy '
                                                                                                      'and '
                                                                                                      'entropy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      'Reaction '
                                                                                                      '(1) '
                                                                                                      '3[Al2O3]+2[SiO2]=[3Al2O3·2SiO2]',
                                                                                                      '12.9 '
                                                                                                      '± '
                                                                                                      '0.8'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r02_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '13.4 '
                                                                                                      '± '
                                                                                                      '2.6'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r02_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '10.3 '
                                                                                                      '± '
                                                                                                      '0.8'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r03_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.3 '
                                                                                                      '± '
                                                                                                      '1.9'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r03_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '14.5 '
                                                                                                      '± '
                                                                                                      '1.2'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r04_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.7 '
                                                                                                      '± '
                                                                                                      '2.1'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r04_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '13.1 '
                                                                                                      '± '
                                                                                                      '1.0'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r05_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '13.1 '
                                                                                                      '± '
                                                                                                      '1.0'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r05_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.0 '
                                                                                                      '± '
                                                                                                      '1.3'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r06_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.8 '
                                                                                                      '± '
                                                                                                      '2.2'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r06_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.8 '
                                                                                                      '± '
                                                                                                      '1.0'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r07_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '13.0 '
                                                                                                      '± '
                                                                                                      '1.4'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r07_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '11.8 '
                                                                                                      '± '
                                                                                                      '1.2'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r08_c03': ('delta_fH',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      'ΔfH_T, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.9 '
                                                                                                      '± '
                                                                                                      '0.8'),
                                           'shornikov_2003_mullite_thermodynamics_p004_t02_r08_c05': ('delta_fG',
                                                                                                      'Table '
                                                                                                      '2. '
                                                                                                      'Enthalpy, '
                                                                                                      'entropy, '
                                                                                                      'and '
                                                                                                      'Gibbs '
                                                                                                      'energy '
                                                                                                      'of '
                                                                                                      'formation '
                                                                                                      'of '
                                                                                                      'mullite '
                                                                                                      'determined '
                                                                                                      'in '
                                                                                                      'this '
                                                                                                      'work '
                                                                                                      'based '
                                                                                                      'on '
                                                                                                      'data '
                                                                                                      'on '
                                                                                                      'solid '
                                                                                                      'phases '
                                                                                                      'in '
                                                                                                      'the '
                                                                                                      'Al2O3–SiO2 '
                                                                                                      'system; '
                                                                                                      'column: '
                                                                                                      '−ΔfG2180, '
                                                                                                      'kJ/mol',
                                                                                                      'formation '
                                                                                                      'from '
                                                                                                      'simple '
                                                                                                      'oxides',
                                                                                                      '12.4 '
                                                                                                      '± '
                                                                                                      '0.5'),
                                                         'shornikov_2003_mullite_thermodynamics_text_ref66_melt_gibbs_extrapolation': ('delta_fG',
                                                                                                                                       'Gibbs energies of formation of melts in the Al2O3–SiO2 system',
                                                                                                                                       'formation from simple oxides',
                                                                                                                                       '–12'),
                                                         'shornikov_2003_mullite_thermodynamics_text_2100k_gibbs_range': ('delta_fG',
                                                                                                            'Gibbs '
                                                                                                            'energies '
                                                                                                            'of '
                                                                                                            'formation '
                                                                                                            'of '
                                                                                                            'mullite '
                                                                                                            'from '
                                                                                                            'oxides',
                                                                                                            'formation '
                                                                                                            'from '
                                                                                                            'simple '
                                                                                                            'oxides',
                                                                                                            '−5 '
                                                                                                            'to '
                                                                                                            '−10 '
                                                                                                            'kJ/mol')},
 'shornikov-2011-cao-mgo-alumina-melts': {'shornikov_2011_fig1_d_allibert_marker_01': ('delta_fG',
                                                                                       'Fig. 1. '
                                                                                       'The '
                                                                                       'activities '
                                                                                       'of oxides '
                                                                                       '(a-c) and '
                                                                                       'the Gibbs '
                                                                                       'energy of '
                                                                                       'formation '
                                                                                       'from '
                                                                                       'oxides (d) '
                                                                                       'of the '
                                                                                       'CaO–MgO–Al2O3 '
                                                                                       'system '
                                                                                       'melt at '
                                                                                       'the '
                                                                                       'temperature '
                                                                                       '1960 K',
                                                                                       'formation '
                                                                                       'from '
                                                                                       'simple '
                                                                                       'oxides',
                                                                                       None),
                                          'shornikov_2011_fig1_d_allibert_marker_02': ('delta_fG',
                                                                                       'Fig. 1. '
                                                                                       'The '
                                                                                       'activities '
                                                                                       'of oxides '
                                                                                       '(a-c) and '
                                                                                       'the Gibbs '
                                                                                       'energy of '
                                                                                       'formation '
                                                                                       'from '
                                                                                       'oxides (d) '
                                                                                       'of the '
                                                                                       'CaO–MgO–Al2O3 '
                                                                                       'system '
                                                                                       'melt at '
                                                                                       'the '
                                                                                       'temperature '
                                                                                       '1960 K',
                                                                                       'formation '
                                                                                       'from '
                                                                                       'simple '
                                                                                       'oxides',
                                                                                       None),
                                          'shornikov_2011_fig1_d_allibert_marker_03': ('delta_fG',
                                                                                       'Fig. 1. '
                                                                                       'The '
                                                                                       'activities '
                                                                                       'of oxides '
                                                                                       '(a-c) and '
                                                                                       'the Gibbs '
                                                                                       'energy of '
                                                                                       'formation '
                                                                                       'from '
                                                                                       'oxides (d) '
                                                                                       'of the '
                                                                                       'CaO–MgO–Al2O3 '
                                                                                       'system '
                                                                                       'melt at '
                                                                                       'the '
                                                                                       'temperature '
                                                                                       '1960 K',
                                                                                       'formation '
                                                                                       'from '
                                                                                       'simple '
                                                                                       'oxides',
                                                                                       None),
                                          'shornikov_2011_fig1_d_allibert_marker_04': ('delta_fG',
                                                                                       'Fig. 1. '
                                                                                       'The '
                                                                                       'activities '
                                                                                       'of oxides '
                                                                                       '(a-c) and '
                                                                                       'the Gibbs '
                                                                                       'energy of '
                                                                                       'formation '
                                                                                       'from '
                                                                                       'oxides (d) '
                                                                                       'of the '
                                                                                       'CaO–MgO–Al2O3 '
                                                                                       'system '
                                                                                       'melt at '
                                                                                       'the '
                                                                                       'temperature '
                                                                                       '1960 K',
                                                                                       'formation '
                                                                                       'from '
                                                                                       'simple '
                                                                                       'oxides',
                                                                                       None),
                                          'shornikov_2011_text_minimum_delta_fG1960_melt': ('delta_fG',
                                                                                            'Fig. '
                                                                                            '1. '
                                                                                            'The '
                                                                                            'activities '
                                                                                            'of '
                                                                                            'oxides '
                                                                                            '(a-c) '
                                                                                            'and '
                                                                                            'the '
                                                                                            'Gibbs '
                                                                                            'energy '
                                                                                            'of '
                                                                                            'formation '
                                                                                            'from '
                                                                                            'oxides '
                                                                                            '(d) '
                                                                                            'of '
                                                                                            'the '
                                                                                            'CaO–MgO–Al2O3 '
                                                                                            'system '
                                                                                            'melt '
                                                                                            'at '
                                                                                            'the '
                                                                                            'temperature '
                                                                                            '1960 '
                                                                                            'K',
                                                                                            'formation '
                                                                                            'from '
                                                                                            'simple '
                                                                                            'oxides',
                                                                                            '−31.5')},
 'shornikov-2014-sodium-germanate-melts': {'na_2014_t1_Na2Ge4O9_1300_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-55.70±1.70'),
                                           'na_2014_t1_Na2Ge4O9_1400_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-56.57±3.00'),
                                           'na_2014_t1_Na2Ge4O9_1450_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-55.23±4.18'),
                                           'na_2014_t1_Na2GeO3_1300_dfH': ('delta_fH',
                                                                           'Table 1. Enthalpies '
                                                                           '(ΔHT) and entropies '
                                                                           '(ΔST) of sodium '
                                                                           'germanates formation '
                                                                           'from the simple oxides',
                                                                           'formation from simple '
                                                                           'oxides',
                                                                           '-129.80±1.70'),
                                           'na_2014_t1_Na2GeO3_1400_dfH': ('delta_fH',
                                                                           'Table 1. Enthalpies '
                                                                           '(ΔHT) and entropies '
                                                                           '(ΔST) of sodium '
                                                                           'germanates formation '
                                                                           'from the simple oxides',
                                                                           'formation from simple '
                                                                           'oxides',
                                                                           '-118.36±5.00'),
                                           'na_2014_t1_Na2GeO3_1450_dfH': ('delta_fH',
                                                                           'Table 1. Enthalpies '
                                                                           '(ΔHT) and entropies '
                                                                           '(ΔST) of sodium '
                                                                           'germanates formation '
                                                                           'from the simple oxides',
                                                                           'formation from simple '
                                                                           'oxides',
                                                                           '-117.82±5.43'),
                                           'na_2014_t1_Na2GeO3_1450b_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-124.68±4.18'),
                                           'na_2014_t1_Na4Ge9O20_1300_dfH': ('delta_fH',
                                                                             'Table 1. Enthalpies '
                                                                             '(ΔHT) and entropies '
                                                                             '(ΔST) of sodium '
                                                                             'germanates formation '
                                                                             'from the simple '
                                                                             'oxides',
                                                                             'formation from '
                                                                             'simple oxides',
                                                                             '-49.97±1.70'),
                                           'na_2014_t1_Na4Ge9O20_1400_dfH': ('delta_fH',
                                                                             'Table 1. Enthalpies '
                                                                             '(ΔHT) and entropies '
                                                                             '(ΔST) of sodium '
                                                                             'germanates formation '
                                                                             'from the simple '
                                                                             'oxides',
                                                                             'formation from '
                                                                             'simple oxides',
                                                                             '-51.49±3.00'),
                                           'na_2014_t1_Na4Ge9O20_1450_dfH': ('delta_fH',
                                                                             'Table 1. Enthalpies '
                                                                             '(ΔHT) and entropies '
                                                                             '(ΔST) of sodium '
                                                                             'germanates formation '
                                                                             'from the simple '
                                                                             'oxides',
                                                                             'formation from '
                                                                             'simple oxides',
                                                                             '-51.81±5.43'),
                                           'na_2014_t1_Na4Ge9O20_1450b_dfH': ('delta_fH',
                                                                              'Table 1. Enthalpies '
                                                                              '(ΔHT) and entropies '
                                                                              '(ΔST) of sodium '
                                                                              'germanates '
                                                                              'formation from the '
                                                                              'simple oxides',
                                                                              'formation from '
                                                                              'simple oxides',
                                                                              '-50.66±4.18'),
                                           'na_2014_t1_Na4GeO4_1400_dfH': ('delta_fH',
                                                                           'Table 1. Enthalpies '
                                                                           '(ΔHT) and entropies '
                                                                           '(ΔST) of sodium '
                                                                           'germanates formation '
                                                                           'from the simple oxides',
                                                                           'formation from simple '
                                                                           'oxides',
                                                                           '-134.35±5.00'),
                                           'na_2014_t1_Na6Ge2O7_1300_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-137.30±1.70'),
                                           'na_2014_t1_Na6Ge2O7_1400_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-131.47±4.00'),
                                           'na_2014_t1_Na6Ge2O7_1450_dfH': ('delta_fH',
                                                                            'Table 1. Enthalpies '
                                                                            '(ΔHT) and entropies '
                                                                            '(ΔST) of sodium '
                                                                            'germanates formation '
                                                                            'from the simple '
                                                                            'oxides',
                                                                            'formation from simple '
                                                                            'oxides',
                                                                            '-130.53±4.18')},
 'shornikov-2017-spinel-thermodynamics': {'shornikov_2017_spinel_thermodynamics_fig2_series16_1863k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_1950k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2004k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2050k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2102k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2160k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2210k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2252k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_fig2_series16_2302k': ('delta_fG',
                                                                                                       'Fig. '
                                                                                                       '2. '
                                                                                                       'Gibbs '
                                                                                                       'energy '
                                                                                                       'of '
                                                                                                       'spinel '
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'formation '
                                                                                                       'from '
                                                                                                       'simple '
                                                                                                       'oxides',
                                                                                                       'not '
                                                                                                       'printed; '
                                                                                                       'read '
                                                                                                       'from '
                                                                                                       'Figure '
                                                                                                       '2 '
                                                                                                       'series '
                                                                                                       '16'),
                                          'shornikov_2017_spinel_thermodynamics_liquid_delta_fH': ('delta_fH',
                                                                                                   'Table '
                                                                                                   '1. '
                                                                                                   'Entropy '
                                                                                                   'and '
                                                                                                   'enthalpy '
                                                                                                   'of '
                                                                                                   'spinel '
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   'formation '
                                                                                                   'from '
                                                                                                   'simple '
                                                                                                   'oxides',
                                                                                                   '43.79 '
                                                                                                   '± '
                                                                                                   '4.84'),
                                          'shornikov_2017_spinel_thermodynamics_p002_t01_r29_c03': ('delta_fH',
                                                                                                    'Table '
                                                                                                    '1. '
                                                                                                    'Entropy '
                                                                                                    'and '
                                                                                                    'enthalpy '
                                                                                                    'of '
                                                                                                    'spinel '
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    'formation '
                                                                                                    'from '
                                                                                                    'simple '
                                                                                                    'oxides',
                                                                                                    '–12.02 '
                                                                                                    '± '
                                                                                                    '1.14')},
 'shornikova-2019-perovskite-thermodynamics': {'shornikova_2019_perovskite_thermodynamics_p002_t01_r03_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-40.48 '
                                                                                                              '± '
                                                                                                              '0.42'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r04_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-41.84 '
                                                                                                              '± '
                                                                                                              '1.88'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r05_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-40.48 '
                                                                                                              '± '
                                                                                                              '1.65'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r06_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-47.11 '
                                                                                                              '± '
                                                                                                              '1.41'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r07_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-42.98 '
                                                                                                              '± '
                                                                                                              '1.96'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r08_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-37.05 '
                                                                                                              '± '
                                                                                                              '3.28'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r09_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-40.07 '
                                                                                                              '± '
                                                                                                              '0.05'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r10_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-38.73 '
                                                                                                              '± '
                                                                                                              '1.34'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r11_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-42.25 '
                                                                                                              '± '
                                                                                                              '1.05'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r12_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-41.88 '
                                                                                                              '± '
                                                                                                              '1.36'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r13_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-42.86 '
                                                                                                              '± '
                                                                                                              '1.71'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r14_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-42.38 '
                                                                                                              '± '
                                                                                                              '1.82'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r15_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-40.45 '
                                                                                                              '± '
                                                                                                              '1.15'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r16_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-40.43 '
                                                                                                              '± '
                                                                                                              '1.87'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r17_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-43.78 '
                                                                                                              '± '
                                                                                                              '1.73'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r18_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-42.83 '
                                                                                                              '± '
                                                                                                              '3.12'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r19_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-26.88 '
                                                                                                              '± '
                                                                                                              '8.05'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r21_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-37.19 '
                                                                                                              '± '
                                                                                                              '0.14'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r22_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-38.23 '
                                                                                                              '± '
                                                                                                              '0.04'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r23_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-37.47 '
                                                                                                              '± '
                                                                                                              '0.03'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r24_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-37.55 '
                                                                                                              '± '
                                                                                                              '3.76'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r25_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '-39.98 '
                                                                                                              '± '
                                                                                                              '0.54'),
                                               'shornikova_2019_perovskite_thermodynamics_p002_t01_r26_c03': ('delta_fH',
                                                                                                              'Table '
                                                                                                              '1. '
                                                                                                              'Entropy '
                                                                                                              '(ΔST) '
                                                                                                              'and '
                                                                                                              'enthalpy '
                                                                                                              '(ΔHT) '
                                                                                                              'of '
                                                                                                              'perovskite '
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              'formation '
                                                                                                              'from '
                                                                                                              'simple '
                                                                                                              'oxides',
                                                                                                              '7.73 '
                                                                                                              '± '
                                                                                                              '1.76')}}
HIT_ROWS["bennington-brown-1982-synthetic-acmite-ri8621"] = {
    "bennington_1982_table6_log10_Kf": (
        "log10_Kf",
        "Log Kf",
        "formation from simple oxides",
        None,
    ),
}

P1_REFERENCES = {'barin-1995': ('barin95_nafeo2_solb_heat_content', 'barin95_nafeo2_liq_heat_content'),
 'beyer-1980-potassium-metasilicate-disilicate-ri8410': ('bey80_k2si2o5_heat_content_crystal_table3',
                                                         'bey80_k2si2o5_crystal_pretransition_503',
                                                         'bey80_k2si2o5_crystal_pretransition_852_6',
                                                         'bey80_k2si2o5_crystal_onset_1309_1',
                                                         'bey80_k2si2o5_heat_content_glass_table3',
                                                         'bey80_k2si2o5_glass_partial_805_4',
                                                         'bey80_k2si2o5_glass_partial_903_2',
                                                         'bey80_k2si2o5_glass_partial_956_0'),
 'nekhoroshev-2024-cao-sio2-thermodynamic-reoptimization': ('nk24_figure5_cao_heat_content',
                                                            'nk24_figure7_ca3sio5_heat_content',
                                                            'nk24_figure9_ca2sio4_heat_content',
                                                            'nk24_figure11_rankinite_heat_content',
                                                            'nk24_figure13_casiO3_heat_content'),
 'schumm-1981-alkali-aluminosilicate-thermochemical-tables': ('schumm81_nepheline_heat_content_beta',
                                                              'schumm81_nepheline_heat_content_gamma',
                                                              'schumm81_kaliophilite_H_minus_H298_beta'),
 'sugawara-akaogi-2003-ni-co-fusion': ('suga03_t1_co2sio4_01',
                                       'suga03_t1_co2sio4_02',
                                       'suga03_t1_co2sio4_03',
                                       'suga03_t1_co2sio4_04',
                                       'suga03_t1_co2sio4_05',
                                       'suga03_t1_co2sio4_06',
                                       'suga03_t1_co2sio4_07',
                                       'suga03_t1_co2sio4_08',
                                       'suga03_t1_co2sio4_09',
                                       'suga03_t1_co2sio4_10',
                                       'suga03_t1_co2sio4_11',
                                       'suga03_t1_co2sio4_12',
                                       'suga03_t1_co2sio4_13',
                                       'suga03_t2_anorthite_ar',
                                       'suga03_t2_diopside_ar',
                                       'suga03_t2_nisio4_ar',
                                       'suga03_t2_nisio4_air'),
 'zigo-1987-gehlenite-fusion': ('zigo_1987_t1_crystal_cooling_measured',
                                'zigo_1987_t1_crystal_cooling_fit')}


def _records(document):
    result = {}

    def visit(node):
        if isinstance(node, dict):
            if "observation_id" in node:
                result[node["observation_id"]] = node
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return result


def test_increment_and_frame_repairs_migrate_unavailable():
    for source_id, expected in HIT_ROWS.items():
        path = ROOT / "data/literature/extracts" / f"{source_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = _records(document)
        assert {gap["observation_id"] for gap in document["schema_gaps"]} == set(expected)
        for observation_id, (old_token, printed, reference, printed_value) in expected.items():
            values = rows[observation_id]["values"]
            assert old_token not in values
            assert values["quantity_as_printed"] == printed
            assert values["reference_state_as_printed"] == reference
            assert values["schema_gap"]
            if printed_value is not None:
                assert values["value_as_printed"] == printed_value
        migrator = Migrator(root=ROOT, index={}, aliases={})
        migrator._migrate_extract(path)
        migrator.finalize()
        for observation_id in expected:
            migrated = [
                row
                for key, row in migrator.result.observations.items()
                if key == f"{source_id}::{observation_id}"
                or key.startswith(f"{source_id}::{observation_id}::")
            ]
            if not migrated:
                source_row = rows[observation_id]
                values = source_row["values"]
                assert (
                    source_row.get("type") in {"figure_record", "figure"}
                    or "deferred" in str(values.get("digitisation", ""))
                ), observation_id
                assert "quantity" not in values
                continue
            assert all(not row.identity.quantity.is_value for row in migrated)
            assert all(row.value.kind.value == "unavailable" for row in migrated)


def test_printed_reference_temperatures_are_recorded_only_for_verified_p1_rows():
    for source_id, observation_ids in P1_REFERENCES.items():
        path = ROOT / "data/literature/extracts" / f"{source_id}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = _records(document)
        migrator = Migrator(root=ROOT, index={}, aliases={})
        migrator._migrate_extract(path)
        migrator.finalize()
        for observation_id in observation_ids:
            values = rows[observation_id]["values"]
            assert values["reference_temperature_K"] in (298, 298.15)
            assert values.get("quantity") in ("H_minus_H298", "negative_enthalpy_change_on_cooling")
            migrated = any(
                key == f"{source_id}::{observation_id}"
                or key.startswith(f"{source_id}::{observation_id}::")
                for key in migrator.result.observations
            )
            if not migrated:
                source_row = rows[observation_id]
                assert (
                        source_row.get("type") in {
                            "figure_record",
                            "figure",
                            "calorimetric_heat_content_context",
                        }
                    or "deferred" in str(values.get("digitisation", ""))
                ), observation_id
