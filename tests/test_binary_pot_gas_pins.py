"""Golden pins for the two binary-pot OpenIMCC gas report paths."""

from __future__ import annotations

from typing import Any

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    DEFAULT_POTS_PATH,
    PO2_OXYGEN_BALANCE_EFFUSION,
    Po2Request,
    _OpenImccBatteryBackend,
    composition_kg_and_mol,
    load_binary_pots,
)


# Re-derived for OpenIMCC 995dc14; the controller's Git-installed RPP receipt confirms the new gas set.
# Float outputs are stored as float.hex strings so these are exact pins.
_GOLDEN = {('cao_sio2_40_60', 1200.0, None): {'pressures': {'SiO': '0x1.c84068d1a3a8dp-32',
                                                  'SiO2': '0x1.24feb396da0ecp-41',
                                                  'O': '0x1.78122c7fb02aep-34',
                                                  'Si': '0x1.c13bc9d949cb8p-76',
                                                  'CaO': '0x1.5e7291f1f5cbfp-61',
                                                  'Ca': '0x1.8a243ac91b09dp-49',
                                                  'O2': '0x1.ff8098205e37ap-34',
                                                  'Si2': '0x1.df376dcaf898fp-142',
                                                  'Si3': '0x1.d883193f5c66ap-198',
                                                  'Si2O2': '0x1.0e9a5b7d959b9p-75'},
                                    'total': '0x1.535c2f3841f2cp-31',
                                    'pO2': '0x1.4f37d96aa7c03p-50',
                                    'residual': '0x1.ee05ff71dcca9p-47',
                                    'iterations': 15,
                                    'O': (('SiO', '0x1.684655c0f148bp-51'),
                                          ('O2', '0x1.da15bb0ca8620p-52'),
                                          ('O', '0x1.ecf06dbe8c3dap-53'),
                                          ('SiO2', '0x1.8c5ad97b1af92p-60'),
                                          ('CaO', '0x1.eab763c301de3p-81')),
                                    'metal': (('SiO', '0x1.684655c0f148bp-50'),
                                              ('SiO2', '0x1.8c5ad97b1af92p-60'),
                                              ('Ca', '0x1.466a0b1f14f39p-68'),
                                              ('CaO', '0x1.eab763c301de3p-81'),
                                              ('Si2O2', '0x1.2e3015208f8ebp-93')),
                                    'flags': (('CaO',
                                               'CaO(l) uses a labelled constant-Cp supercooled-liquid '
                                               'continuation from JANAF Ca-028; this is a hypothetical '
                                               'reference, not a prediction of a liquid condensate'),
                                              ('Ca',
                                               'CaO(l) uses a labelled constant-Cp supercooled-liquid '
                                               'continuation from JANAF Ca-028; this is a hypothetical '
                                               'reference, not a prediction of a liquid condensate')),
                                    'flux': '0x0.0p+0',
                                    'buffer': None,
                                    'domain_flags': {'SiO': None,
                                                     'SiO2': None,
                                                     'O': None,
                                                     'Si': None,
                                                     'CaO': 'CaO(l) uses a labelled constant-Cp '
                                                            'supercooled-liquid continuation from JANAF '
                                                            'Ca-028; this is a hypothetical reference, not a '
                                                            'prediction of a liquid condensate',
                                                     'Ca': 'CaO(l) uses a labelled constant-Cp '
                                                           'supercooled-liquid continuation from JANAF '
                                                           'Ca-028; this is a hypothetical reference, not a '
                                                           'prediction of a liquid condensate',
                                                     'O2': None,
                                                     'Si2': None,
                                                     'Si3': None,
                                                     'Si2O2': None},
                                    'provenance_class': {'SiO': 'janaf_fitted',
                                                         'SiO2': 'janaf_fitted',
                                                         'O': 'janaf_fitted',
                                                         'Si': 'janaf_fitted',
                                                         'CaO': 'janaf_fitted',
                                                         'Ca': 'janaf_fitted',
                                                         'O2': 'janaf_fitted',
                                                         'Si2': 'janaf_fitted',
                                                         'Si3': 'janaf_fitted',
                                                         'Si2O2': 'published_fitted'}},
 ('cao_sio2_40_60', 1200.0, 'W'): {'pressures': {'SiO': '0x1.536b41fe5a70ap-28',
                                                 'SiO2': '0x1.24feb396da0ecp-41',
                                                 'O': '0x1.f9850d7cc6835p-38',
                                                 'Si': '0x1.f13d4f672a905p-69',
                                                 'CaO': '0x1.5e7291f1f5cbfp-61',
                                                 'Ca': '0x1.2536a58b380f3p-45',
                                                 'O2': '0x1.ce1e8f4495d94p-41',
                                                 'Si2': '0x1.258defa576715p-127',
                                                 'Si3': '0x1.4060df740c75dp-176',
                                                 'Si2O2': '0x1.2b8525560cbf1p-68',
                                                 'WO': '0x1.63ade368f6b4bp-55',
                                                 'WO2': '0x1.34d0b32743d8fp-44',
                                                 'WO3': '0x1.302a9f3180e0dp-36',
                                                 'W2O6': '0x1.391c7bee751dbp-29',
                                                 'W3O8': '0x1.567bc0e5bcc70p-34',
                                                 'W3O9': '0x1.00b9649f40d29p-32',
                                                 'W4O12': '0x1.53020290fb2d1p-44'},
                                   'total': '0x1.039422557b92cp-27',
                                   'pO2': '0x1.2edac3f6e1823p-57',
                                   'residual': '0x1.5f76edbefac81p-48',
                                   'iterations': 20,
                                   'O': (('SiO', '0x1.0c04db70311e1p-47'),
                                         ('W2O6', '0x1.c96b12957c580p-48'),
                                         ('W3O9', '0x1.cb5521e16d1a7p-51'),
                                         ('W3O8', '0x1.138800f2f33c2p-52'),
                                         ('WO3', '0x1.3a341f99b72a6p-55')),
                                   'metal': (('SiO', '0x1.0c04db70311e1p-46'),
                                             ('SiO2', '0x1.8c5ad97b1af92p-60'),
                                             ('Ca', '0x1.e5a88548330bcp-65'),
                                             ('CaO', '0x1.eab763c301de3p-81'),
                                             ('Si2O2', '0x1.4e7aed4448efep-86')),
                                   'flags': (('CaO',
                                              'CaO(l) uses a labelled constant-Cp supercooled-liquid '
                                              'continuation from JANAF Ca-028; this is a hypothetical '
                                              'reference, not a prediction of a liquid condensate'),
                                             ('Ca',
                                              'CaO(l) uses a labelled constant-Cp supercooled-liquid '
                                              'continuation from JANAF Ca-028; this is a hypothetical '
                                              'reference, not a prediction of a liquid condensate')),
                                   'flux': '0x1.fe85119b3d40dp-2',
                                   'buffer': '0x1.0e9617dca9e3ap-54',
                                   'domain_flags': {'SiO': None,
                                                    'SiO2': None,
                                                    'O': None,
                                                    'Si': None,
                                                    'CaO': 'CaO(l) uses a labelled constant-Cp '
                                                           'supercooled-liquid continuation from JANAF '
                                                           'Ca-028; this is a hypothetical reference, not a '
                                                           'prediction of a liquid condensate',
                                                    'Ca': 'CaO(l) uses a labelled constant-Cp '
                                                          'supercooled-liquid continuation from JANAF '
                                                          'Ca-028; this is a hypothetical reference, not a '
                                                          'prediction of a liquid condensate',
                                                    'O2': None,
                                                    'Si2': None,
                                                    'Si3': None,
                                                    'Si2O2': None},
                                   'provenance_class': {'SiO': 'janaf_fitted',
                                                        'SiO2': 'janaf_fitted',
                                                        'O': 'janaf_fitted',
                                                        'Si': 'janaf_fitted',
                                                        'CaO': 'janaf_fitted',
                                                        'Ca': 'janaf_fitted',
                                                        'O2': 'janaf_fitted',
                                                        'Si2': 'janaf_fitted',
                                                        'Si3': 'janaf_fitted',
                                                        'Si2O2': 'published_fitted'}},
 ('cao_sio2_40_60', 1700.0, None): {'pressures': {'SiO': '0x1.12f50eba3f967p-9',
                                                  'SiO2': '0x1.1b50eeaa0ef31p-16',
                                                  'O': '0x1.772e1922bfd4ap-12',
                                                  'Si': '0x1.5fae804d5b0a1p-41',
                                                  'CaO': '0x1.c95bf706dba79p-32',
                                                  'Ca': '0x1.75d58f460d6a7p-25',
                                                  'O2': '0x1.4fdefbbcea43cp-11',
                                                  'Si2': '0x1.f4fecf0f437dep-86',
                                                  'Si3': '0x1.3ec35429b1cd6p-124',
                                                  'Si2O2': '0x1.2e2547a4ccf70p-37'},
                                    'total': '0x1.980aac27af76fp-9',
                                    'pO2': '0x1.b83b9dae21ef2p-28',
                                    'residual': '0x1.c1a9d0d5defe4p-52',
                                    'iterations': 18,
                                    'O': (('SiO', '0x1.b23c1a494183ep-29'),
                                          ('O2', '0x1.374d2732adae2p-29'),
                                          ('O', '0x1.ebc579de412fcp-31'),
                                          ('SiO2', '0x1.7f42ff1c0eecap-35'),
                                          ('CaO', '0x1.4035c73d70e3ap-51')),
                                    'metal': (('SiO', '0x1.b23c1a494183ep-28'),
                                              ('SiO2', '0x1.7f42ff1c0eecap-35'),
                                              ('Ca', '0x1.3598afca71975p-44'),
                                              ('CaO', '0x1.4035c73d70e3ap-51'),
                                              ('Si2O2', '0x1.5169833573d4ap-55')),
                                    'flags': (),
                                    'flux': '0x0.0p+0',
                                    'buffer': None,
                                    'domain_flags': {'SiO': None,
                                                     'SiO2': None,
                                                     'O': None,
                                                     'Si': None,
                                                     'CaO': None,
                                                     'Ca': None,
                                                     'O2': None,
                                                     'Si2': None,
                                                     'Si3': None,
                                                     'Si2O2': None},
                                    'provenance_class': {'SiO': 'janaf_fitted',
                                                         'SiO2': 'janaf_fitted',
                                                         'O': 'janaf_fitted',
                                                         'Si': 'janaf_fitted',
                                                         'CaO': 'janaf_fitted',
                                                         'Ca': 'janaf_fitted',
                                                         'O2': 'janaf_fitted',
                                                         'Si2': 'janaf_fitted',
                                                         'Si3': 'janaf_fitted',
                                                         'Si2O2': 'published_fitted'}},
 ('cao_sio2_40_60', 1700.0, 'W'): {'pressures': {'SiO': '0x1.1dd2f8d1caa7dp-6',
                                                 'SiO2': '0x1.1b50eeaa0ef31p-16',
                                                 'O': '0x1.68ea7c5f02c0ep-15',
                                                 'Si': '0x1.7c077e5f6076ap-35',
                                                 'CaO': '0x1.c95bf706dba79p-32',
                                                 'Ca': '0x1.849bdfc5780b1p-22',
                                                 'O2': '0x1.36d13b365eb69p-17',
                                                 'Si2': '0x1.248228aa6bf9fp-73',
                                                 'Si3': '0x1.9239cfafac372p-106',
                                                 'Si2O2': '0x1.46801735428ecp-31',
                                                 'WO': '0x1.cd7bce0c12654p-26',
                                                 'WO2': '0x1.88b3f795ebb83p-18',
                                                 'WO3': '0x1.d7591985ef1b5p-14',
                                                 'W2O6': '0x1.0da1aabecbe76p-7',
                                                 'W3O8': '0x1.b26df3568e466p-12',
                                                 'W3O9': '0x1.056cb50453c57p-11',
                                                 'W4O12': '0x1.32d0221cc900ap-20'},
                                   'total': '0x1.b6b1617689f04p-6',
                                   'pO2': '0x1.9764fb9be31dbp-34',
                                   'residual': '0x1.723c9a9b0da5dp-41',
                                   'iterations': 17,
                                   'O': (('SiO', '0x1.c36586ff1afffp-26'),
                                         ('W2O6', '0x1.89e64666ac246p-26'),
                                         ('W3O9', '0x1.d3be1c0498266p-30'),
                                         ('W3O8', '0x1.5d80b6cfbbafdp-30'),
                                         ('WO3', '0x1.e6e6e5d50e5c3p-33')),
                                   'metal': (('SiO', '0x1.c36586ff1afffp-25'),
                                             ('SiO2', '0x1.7f42ff1c0eecap-35'),
                                             ('Ca', '0x1.41d5113aca60fp-41'),
                                             ('Si2O2', '0x1.6c9c0d6cda0e1p-49'),
                                             ('CaO', '0x1.4035c73d70e3ap-51')),
                                   'flags': (),
                                   'flux': '0x1.fcd974c285927p-2',
                                   'buffer': '0x1.673a0a0b49045p-30',
                                   'domain_flags': {'SiO': None,
                                                    'SiO2': None,
                                                    'O': None,
                                                    'Si': None,
                                                    'CaO': None,
                                                    'Ca': None,
                                                    'O2': None,
                                                    'Si2': None,
                                                    'Si3': None,
                                                    'Si2O2': None},
                                   'provenance_class': {'SiO': 'janaf_fitted',
                                                        'SiO2': 'janaf_fitted',
                                                        'O': 'janaf_fitted',
                                                        'Si': 'janaf_fitted',
                                                        'CaO': 'janaf_fitted',
                                                        'Ca': 'janaf_fitted',
                                                        'O2': 'janaf_fitted',
                                                        'Si2': 'janaf_fitted',
                                                        'Si3': 'janaf_fitted',
                                                        'Si2O2': 'published_fitted'}},
 ('feo_mgo_sio2_30_20_50', 1200.0, None): {'pressures': {'SiO': '0x1.7a93e1155d853p-34',
                                                         'Fe': '0x1.bd4a0a7666063p-27',
                                                         'FeO': '0x1.7e0b93a5208afp-33',
                                                         'Mg': '0x1.32103a52a4643p-35',
                                                         'MgO': '0x1.b61e886fc689cp-51',
                                                         'SiO2': '0x1.7d7e070182593p-41',
                                                         'O': '0x1.271028bccc976p-31',
                                                         'Si': '0x1.db1789217918ep-81',
                                                         'O2': '0x1.3adfee1dcbf71p-28',
                                                         'Si2': '0x1.0bfc84149987cp-151',
                                                         'Si3': '0x1.17726b5337226p-212',
                                                         'Si2O2': '0x1.749e2d1e49177p-80'},
                                           'total': '0x1.3ba83105329dbp-26',
                                           'pO2': '0x1.9cb66d1b2d419p-45',
                                           'residual': '0x1.c9d5a0f084f42p-50',
                                           'iterations': 14,
                                           'O': (('O2', '0x1.23d74bc6002a1p-46'),
                                                 ('O', '0x1.82c1d93dfcb52p-50'),
                                                 ('FeO', '0x1.d8a0c3a1c48bcp-53'),
                                                 ('SiO', '0x1.2af0b45229345p-53'),
                                                 ('SiO2', '0x1.02092707c50f2p-59')),
                                           'metal': (('Fe', '0x1.38680b4cfb1e9p-46'),
                                                     ('SiO', '0x1.2af0b45229345p-52'),
                                                     ('FeO', '0x1.d8a0c3a1c48bcp-53'),
                                                     ('Mg', '0x1.457ca44084d8fp-54'),
                                                     ('SiO2', '0x1.02092707c50f2p-59')),
                                           'flags': (('Fe',
                                                      'FeO(l) uses the D&G/Masson correction extrapolated '
                                                      'outside its 1649.15-1766.15 K fit range '
                                                      '(Ref=DG46-MASSON65)'),
                                                     ('FeO',
                                                      'FeO(l) uses the D&G/Masson correction extrapolated '
                                                      'outside its 1649.15-1766.15 K fit range '
                                                      '(Ref=DG46-MASSON65)'),
                                                     ('Mg',
                                                      'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                      'continuation from JANAF Mg-009; this is a '
                                                      'hypothetical reference, not a prediction of a liquid '
                                                      'condensate'),
                                                     ('MgO',
                                                      'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                      'continuation from JANAF Mg-009; this is a '
                                                      'hypothetical reference, not a prediction of a liquid '
                                                      'condensate')),
                                           'flux': '0x0.0p+0',
                                           'buffer': None,
                                           'domain_flags': {'SiO': None,
                                                            'Fe': 'FeO(l) uses the D&G/Masson correction '
                                                                  'extrapolated outside its 1649.15-1766.15 '
                                                                  'K fit range (Ref=DG46-MASSON65)',
                                                            'FeO': 'FeO(l) uses the D&G/Masson correction '
                                                                   'extrapolated outside its 1649.15-1766.15 '
                                                                   'K fit range (Ref=DG46-MASSON65)',
                                                            'Mg': 'MgO(l) uses a labelled constant-Cp '
                                                                  'supercooled-liquid continuation from '
                                                                  'JANAF Mg-009; this is a hypothetical '
                                                                  'reference, not a prediction of a liquid '
                                                                  'condensate',
                                                            'MgO': 'MgO(l) uses a labelled constant-Cp '
                                                                   'supercooled-liquid continuation from '
                                                                   'JANAF Mg-009; this is a hypothetical '
                                                                   'reference, not a prediction of a liquid '
                                                                   'condensate',
                                                            'SiO2': None,
                                                            'O': None,
                                                            'Si': None,
                                                            'O2': None,
                                                            'Si2': None,
                                                            'Si3': None,
                                                            'Si2O2': None},
                                           'provenance_class': {'SiO': 'janaf_fitted',
                                                                'Fe': 'janaf_fitted',
                                                                'FeO': 'janaf_fitted',
                                                                'Mg': 'janaf_fitted',
                                                                'MgO': 'janaf_fitted',
                                                                'SiO2': 'janaf_fitted',
                                                                'O': 'janaf_fitted',
                                                                'Si': 'janaf_fitted',
                                                                'O2': 'janaf_fitted',
                                                                'Si2': 'janaf_fitted',
                                                                'Si3': 'janaf_fitted',
                                                                'Si2O2': 'published_fitted'}},
 ('feo_mgo_sio2_30_20_50', 1200.0, 'W'): {'pressures': {'SiO': '0x1.c956d38599fbcp-29',
                                                        'Fe': '0x1.0cf703aaa8c1ap-21',
                                                        'FeO': '0x1.7e0b93a5208afp-33',
                                                        'Mg': '0x1.71bd158800d43p-30',
                                                        'MgO': '0x1.b61e886fc689cp-51',
                                                        'SiO2': '0x1.7d7e070182593p-41',
                                                        'O': '0x1.e87f1e77e68f7p-37',
                                                        'Si': '0x1.5aab292f552bfp-70',
                                                        'O2': '0x1.af8520beacf31p-39',
                                                        'Si2': '0x1.1d60157312280p-130',
                                                        'Si3': '0x1.b247b28adcb13p-181',
                                                        'Si2O2': '0x1.0fe50259fe42ep-69',
                                                        'WO': '0x1.57b3af36d8935p-54',
                                                        'WO2': '0x1.205df1204d325p-42',
                                                        'WO3': '0x1.1276298334a4dp-33',
                                                        'W2O6': '0x1.fde199a7e4b55p-24',
                                                        'W3O8': '0x1.04643470858d9p-26',
                                                        'W3O9': '0x1.793b1fac67ff2p-24',
                                                        'W4O12': '0x1.c17d8d6000cf3p-33'},
                                          'total': '0x1.86c7870e639acp-21',
                                          'pO2': '0x1.1acd0c33e3757p-55',
                                          'residual': '0x1.14d4d331dc611p-46',
                                          'iterations': 22,
                                          'O': (('W2O6', '0x1.747018bc5994bp-42'),
                                                ('W3O9', '0x1.5178d53501abep-42'),
                                                ('W3O8', '0x1.a2f993cbfc04dp-45'),
                                                ('SiO', '0x1.69222f2584051p-48'),
                                                ('W4O12', '0x1.d052b4484920fp-51')),
                                          'metal': (('Fe', '0x1.7966ba8ae940cp-41'),
                                                    ('SiO', '0x1.69222f2584051p-47'),
                                                    ('Mg', '0x1.8933fcebd3630p-49'),
                                                    ('FeO', '0x1.d8a0c3a1c48bcp-53'),
                                                    ('SiO2', '0x1.02092707c50f2p-59')),
                                          'flags': (('Fe',
                                                     'FeO(l) uses the D&G/Masson correction extrapolated '
                                                     'outside its 1649.15-1766.15 K fit range '
                                                     '(Ref=DG46-MASSON65)'),
                                                    ('FeO',
                                                     'FeO(l) uses the D&G/Masson correction extrapolated '
                                                     'outside its 1649.15-1766.15 K fit range '
                                                     '(Ref=DG46-MASSON65)'),
                                                    ('Mg',
                                                     'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                     'continuation from JANAF Mg-009; this is a hypothetical '
                                                     'reference, not a prediction of a liquid condensate'),
                                                    ('MgO',
                                                     'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                     'continuation from JANAF Mg-009; this is a hypothetical '
                                                     'reference, not a prediction of a liquid condensate')),
                                          'flux': '0x1.fc0e555462c45p-1',
                                          'buffer': '0x1.0e9617dca9e3ap-54',
                                          'domain_flags': {'SiO': None,
                                                           'Fe': 'FeO(l) uses the D&G/Masson correction '
                                                                 'extrapolated outside its 1649.15-1766.15 K '
                                                                 'fit range (Ref=DG46-MASSON65)',
                                                           'FeO': 'FeO(l) uses the D&G/Masson correction '
                                                                  'extrapolated outside its 1649.15-1766.15 '
                                                                  'K fit range (Ref=DG46-MASSON65)',
                                                           'Mg': 'MgO(l) uses a labelled constant-Cp '
                                                                 'supercooled-liquid continuation from JANAF '
                                                                 'Mg-009; this is a hypothetical reference, '
                                                                 'not a prediction of a liquid condensate',
                                                           'MgO': 'MgO(l) uses a labelled constant-Cp '
                                                                  'supercooled-liquid continuation from '
                                                                  'JANAF Mg-009; this is a hypothetical '
                                                                  'reference, not a prediction of a liquid '
                                                                  'condensate',
                                                           'SiO2': None,
                                                           'O': None,
                                                           'Si': None,
                                                           'O2': None,
                                                           'Si2': None,
                                                           'Si3': None,
                                                           'Si2O2': None},
                                          'provenance_class': {'SiO': 'janaf_fitted',
                                                               'Fe': 'janaf_fitted',
                                                               'FeO': 'janaf_fitted',
                                                               'Mg': 'janaf_fitted',
                                                               'MgO': 'janaf_fitted',
                                                               'SiO2': 'janaf_fitted',
                                                               'O': 'janaf_fitted',
                                                               'Si': 'janaf_fitted',
                                                               'O2': 'janaf_fitted',
                                                               'Si2': 'janaf_fitted',
                                                               'Si3': 'janaf_fitted',
                                                               'Si2O2': 'published_fitted'}},
 ('feo_mgo_sio2_30_20_50', 1700.0, None): {'pressures': {'SiO': '0x1.2cab8466155b1p-10',
                                                         'Fe': '0x1.bb8d6b272ff83p-8',
                                                         'FeO': '0x1.e7bb9bdd01ddap-12',
                                                         'Mg': '0x1.1f5c5df8b21b0p-13',
                                                         'MgO': '0x1.ef7250fd2992cp-23',
                                                         'SiO2': '0x1.391c20d1f166cp-16',
                                                         'O': '0x1.7b2cf00efef27p-11',
                                                         'Si': '0x1.7c84593c42cb6p-43',
                                                         'O2': '0x1.5710148da4371p-9',
                                                         'Si2': '0x1.25427bda724a5p-89',
                                                         'Si3': '0x1.93c6c33052d8cp-130',
                                                         'Si2O2': '0x1.694c6775644ccp-39'},
                                           'total': '0x1.852cc61fb9864p-7',
                                           'pO2': '0x1.c1a8c74c9de56p-26',
                                           'residual': '0x0.0p+0',
                                           'iterations': 15,
                                           'O': (('O2', '0x1.3df791efb629ap-27'),
                                                 ('O', '0x1.f1022c97b712cp-30'),
                                                 ('SiO', '0x1.dad7a802573cep-30'),
                                                 ('FeO', '0x1.2dafe2983da7ap-31'),
                                                 ('SiO2', '0x1.a790d780048e1p-35')),
                                           'metal': (('Fe', '0x1.37301b09da96fp-27'),
                                                     ('SiO', '0x1.dad7a802573cep-29'),
                                                     ('FeO', '0x1.2dafe2983da7ap-31'),
                                                     ('Mg', '0x1.3198eea0500b0p-32'),
                                                     ('SiO2', '0x1.a790d780048e1p-35')),
                                           'flags': (('Mg',
                                                      'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                      'continuation from JANAF Mg-009; this is a '
                                                      'hypothetical reference, not a prediction of a liquid '
                                                      'condensate; band ±0.76 dex pO2'),
                                                     ('MgO',
                                                      'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                      'continuation from JANAF Mg-009; this is a '
                                                      'hypothetical reference, not a prediction of a liquid '
                                                      'condensate; band ±0.76 dex pO2')),
                                           'flux': '0x0.0p+0',
                                           'buffer': None,
                                           'domain_flags': {'SiO': None,
                                                            'Fe': None,
                                                            'FeO': None,
                                                            'Mg': 'MgO(l) uses a labelled constant-Cp '
                                                                  'supercooled-liquid continuation from '
                                                                  'JANAF Mg-009; this is a hypothetical '
                                                                  'reference, not a prediction of a liquid '
                                                                  'condensate; band ±0.76 dex pO2',
                                                            'MgO': 'MgO(l) uses a labelled constant-Cp '
                                                                   'supercooled-liquid continuation from '
                                                                   'JANAF Mg-009; this is a hypothetical '
                                                                   'reference, not a prediction of a liquid '
                                                                   'condensate; band ±0.76 dex pO2',
                                                            'SiO2': None,
                                                            'O': None,
                                                            'Si': None,
                                                            'O2': None,
                                                            'Si2': None,
                                                            'Si3': None,
                                                            'Si2O2': None},
                                           'provenance_class': {'SiO': 'janaf_fitted',
                                                                'Fe': 'janaf_fitted',
                                                                'FeO': 'janaf_fitted',
                                                                'Mg': 'janaf_fitted',
                                                                'MgO': 'janaf_fitted',
                                                                'SiO2': 'janaf_fitted',
                                                                'O': 'janaf_fitted',
                                                                'Si': 'janaf_fitted',
                                                                'O2': 'janaf_fitted',
                                                                'Si2': 'janaf_fitted',
                                                                'Si3': 'janaf_fitted',
                                                                'Si2O2': 'published_fitted'}},
 ('feo_mgo_sio2_30_20_50', 1700.0, 'W'): {'pressures': {'SiO': '0x1.e488ddf53645ep-7',
                                                        'Fe': '0x1.656560ed2e264p-4',
                                                        'FeO': '0x1.e7bb9bdd01ddap-12',
                                                        'Mg': '0x1.cf1630440fc35p-10',
                                                        'MgO': '0x1.ef7250fd2992cp-23',
                                                        'SiO2': '0x1.391c20d1f166cp-16',
                                                        'O': '0x1.d6950fb335f5ap-15',
                                                        'Si': '0x1.ee19380d72a7ep-36',
                                                        'O2': '0x1.0833686dcb828p-16',
                                                        'Si2': '0x1.ee75d2db9909fp-75',
                                                        'Si3': '0x1.ba01de7ebccccp-108',
                                                        'Si2O2': '0x1.d524b50dfec41p-32',
                                                        'WO': '0x1.2cda9e753291dp-25',
                                                        'WO2': '0x1.4dce250c3c263p-17',
                                                        'WO3': '0x1.0532bba912bddp-12',
                                                        'W2O6': '0x1.4b32a5a638c71p-5',
                                                        'W3O8': '0x1.c59779f23f066p-9',
                                                        'W3O9': '0x1.63e5196bd3efap-8',
                                                        'W4O12': '0x1.ceebf0bda642cp-16'},
                                          'total': '0x1.3b55581e7a7a0p-3',
                                          'pO2': '0x1.5a4b14f5d1a56p-33',
                                          'residual': '0x1.9eef33c563980p-50',
                                          'iterations': 21,
                                          'O': (('W2O6', '0x1.e3d73061c8c62p-24'),
                                                ('SiO', '0x1.7e9bb18f43c8ep-26'),
                                                ('W3O9', '0x1.3e627d0ef09dep-26'),
                                                ('W3O8', '0x1.6ceb41fb659c8p-27'),
                                                ('FeO', '0x1.2dafe2983da7ap-31')),
                                          'metal': (('Fe', '0x1.f57c04824633ap-24'),
                                                    ('SiO', '0x1.7e9bb18f43c8ep-25'),
                                                    ('Mg', '0x1.ec79a871798ccp-29'),
                                                    ('FeO', '0x1.2dafe2983da7ap-31'),
                                                    ('SiO2', '0x1.a790d780048e1p-35')),
                                          'flags': (('Mg',
                                                     'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                     'continuation from JANAF Mg-009; this is a hypothetical '
                                                     'reference, not a prediction of a liquid condensate; '
                                                     'band ±0.76 dex pO2'),
                                                    ('MgO',
                                                     'MgO(l) uses a labelled constant-Cp supercooled-liquid '
                                                     'continuation from JANAF Mg-009; this is a hypothetical '
                                                     'reference, not a prediction of a liquid condensate; '
                                                     'band ±0.76 dex pO2')),
                                          'flux': '0x1.b8a4086c5ca1cp-1',
                                          'buffer': '0x1.673a0a0b49045p-30',
                                          'domain_flags': {'SiO': None,
                                                           'Fe': None,
                                                           'FeO': None,
                                                           'Mg': 'MgO(l) uses a labelled constant-Cp '
                                                                 'supercooled-liquid continuation from JANAF '
                                                                 'Mg-009; this is a hypothetical reference, '
                                                                 'not a prediction of a liquid condensate; '
                                                                 'band ±0.76 dex pO2',
                                                           'MgO': 'MgO(l) uses a labelled constant-Cp '
                                                                  'supercooled-liquid continuation from '
                                                                  'JANAF Mg-009; this is a hypothetical '
                                                                  'reference, not a prediction of a liquid '
                                                                  'condensate; band ±0.76 dex pO2',
                                                           'SiO2': None,
                                                           'O': None,
                                                           'Si': None,
                                                           'O2': None,
                                                           'Si2': None,
                                                           'Si3': None,
                                                           'Si2O2': None},
                                          'provenance_class': {'SiO': 'janaf_fitted',
                                                               'Fe': 'janaf_fitted',
                                                               'FeO': 'janaf_fitted',
                                                               'Mg': 'janaf_fitted',
                                                               'MgO': 'janaf_fitted',
                                                               'SiO2': 'janaf_fitted',
                                                               'O': 'janaf_fitted',
                                                               'Si': 'janaf_fitted',
                                                               'O2': 'janaf_fitted',
                                                               'Si2': 'janaf_fitted',
                                                               'Si3': 'janaf_fitted',
                                                               'Si2O2': 'published_fitted'}}}

_CELL_SOURCE_LABELS = {
    "W2O6": "cell_shared_janaf:O-088:c1d7a696a2444f19d5dcbdcc2b029fce175bc9fa16c6ab2dfc9d35d66c642b16",
    "W3O8": "cell_shared_janaf:O-092:64e4ead9fde896fc3519c1da31d4c296289a4fc1314fd49c586207f546ecde79",
    "W3O9": "cell_shared_janaf:O-093:f6e6d16a11240da51032aa9ae2885dc9ec0ee74df974eb08abdac421de5bb9a6",
    "W4O12": "cell_shared_janaf:O-096:40420b9f365b697a7494d890aeae2a33551b76482d355b8101783b9eb31a0331",
    "WO": "cell_shared_janaf:O-027:a7b478fbb89b4bf1fbd560c7c8a1400aa8f67b406db3e6dd5d380f037254b722",
    "WO2": "cell_shared_janaf:O-048:8df6c1b4022b439cd93fee827e08d65c62d133ade8355a3325dc7f18f9b86d93",
    "WO3": "cell_shared_janaf:O-068:2e7389ad5a9f80ad4acc4b121aa39f714d93a03b740c776c31308a748e2e6de5",
}


def _hx_tree(value: Any) -> Any:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {key: _hx_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_hx_tree(item) for item in value)
    return value


def _gas_result(backend: Any, pot: Any, temperature_K: float, cell: str | None) -> Any:
    composition_kg, composition_mol = composition_kg_and_mol(pot.composition_wt_pct)
    return backend.equilibrate(
        temperature_C=temperature_K - 273.15,
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        po2_request=Po2Request(
            mode=PO2_OXYGEN_BALANCE_EFFUSION,
            po2_bar=None,
            cell_material=cell,
        ),
    )


def test_binary_pot_gas_records_engine_binding_identity_and_hex_pins() -> None:
    openimcc = pytest.importorskip("openimcc", reason="openimcc is not importable")
    if not hasattr(openimcc, "oxygen_balance_from_pressure_model"):
        pytest.skip("installed openimcc lacks oxygen_balance_from_pressure_model")

    backend = _OpenImccBatteryBackend("openimcc")
    package_identity = openimcc.engine_binding_identity(backend._pack, backend._gas)
    gas_provenance = {
        "engine_binding_identity": {
            "engine_binding_digest": package_identity.digest,
            "melt_binding_digest": package_identity.melt_binding_digest,
            "condensate_table_digest": package_identity.condensate_table_digest,
            "gas_table_digest": package_identity.gas_table_digest,
        },
        "pack": "1.0.6-d066-v4",
        "pack_digest": "0879f5a8e94dfd844ed5cfcd900677b496a89e717a85f320b3e0b374ebcdc3b0",
        "package_version": "0.1.0.dev0",
    }

    pots = {pot.pot_id: pot for pot in load_binary_pots(DEFAULT_POTS_PATH)[0]}

    for (pot_id, temperature_K, cell), expected in _GOLDEN.items():
        result = _gas_result(backend, pots[pot_id], temperature_K, cell)
        diagnostics = result.diagnostics
        gas = diagnostics["openimcc_gas"]
        solved = next(
            notice
            for notice in diagnostics["imcc_notices"]
            if notice["kind"] == "fo2_oxygen_balance_effusion_solved"
        )
        pressure_pa = dict(result.vapor_pressures_Pa)

        assert {key: float(value).hex() for key, value in pressure_pa.items()} == expected["pressures"]
        assert float(sum(pressure_pa.values())).hex() == expected["total"]
        balance = gas["oxygen_balance"]
        assert balance["mode"] == PO2_OXYGEN_BALANCE_EFFUSION
        assert tuple(balance["bracket"]) == (-30.0, 0.0)
        assert int(balance["iterations"]) == expected["iterations"]
        assert float(balance["residual"]).hex() == expected["residual"]
        assert _hx_tree(balance["dominant_O_carriers"]) == expected["O"]
        assert _hx_tree(balance["dominant_metal_carriers"]) == expected["metal"]
        assert float(solved["pO2_bar"]).hex() == expected["pO2"]
        assert diagnostics["openimcc_provenance"] == gas_provenance
        assert gas["domain_flags"] == expected["domain_flags"]
        # Overall authority is raised when any gas species is extrapolated;
        # the detailed flags remain available per species below.
        assert diagnostics["authority"] == (
            "extrapolated" if any(gas["domain_flags"].values()) else None
        )
        assert gas["provenance_class"] == expected["provenance_class"]
        expected_gas_notices = {
            ("openimcc_gas_flag", "extrapolated", species, reason)
            for species, reason in expected["domain_flags"].items()
            if reason
        }
        assert {
            (notice["kind"], notice["authority"], notice["species"], notice["reason"])
            for notice in diagnostics["imcc_notices"]
            if notice["kind"] == "openimcc_gas_flag"
        } == expected_gas_notices
        assert tuple(
            (notice["species"], notice["reason"])
            for notice in diagnostics["imcc_notices"]
            if notice["kind"] == "openimcc_gas_flag"
            and notice["species"] in pressure_pa
        ) == expected["flags"]
        assert float(solved["cell_oxide_flux_fraction"]).hex() == (
            expected["flux"] or (0.0).hex()
        )
        buffer = solved["buffer_pO2_bar"]
        assert (None if buffer is None else float(buffer).hex()) == expected["buffer"]
        assert set(result.vapor_pressures_source) == set(expected["pressures"])
        expected_sources = {
            species: (
                "openimcc-gas-table:sha256:"
                f"{package_identity.gas_table_digest}"
            )
            for species in expected["pressures"]
            if species not in _CELL_SOURCE_LABELS
        }
        if cell is not None:
            expected_sources.update(
                {species: label for species, label in _CELL_SOURCE_LABELS.items() if species in expected["pressures"]}
            )
            assert set(solved["cell_oxide_janaf_sources"]) == set(_CELL_SOURCE_LABELS) | {"buffer_phase"}
            expected_cell_sources = {
                species: {
                    "source_class": "cell_shared_janaf",
                    "table_id": label.split(":")[1],
                    "source_sha256": label.split(":")[2],
                }
                for species, label in _CELL_SOURCE_LABELS.items()
            }
            expected_cell_sources["buffer_phase"] = {
                "formula": "WO2",
                "source_class": "cell_shared_janaf",
                "table_id": "O-047",
                "source_sha256": "e0979ceb818409467a55c5c24d1fb8de7a736b7bdef119141c18eb7e0bd548f0",
            }
            assert solved["cell_oxide_janaf_sources"] == expected_cell_sources
        else:
            assert "cell_oxide_janaf_sources" not in solved
        assert result.vapor_pressures_source == expected_sources


def test_binary_pot_gas_pin_inputs_cover_both_paths_without_low_t_interval_flags() -> None:
    assert set(pot_id for pot_id, _, _ in _GOLDEN) == {
        "cao_sio2_40_60",
        "feo_mgo_sio2_30_20_50",
    }
    assert {temperature for _, temperature, _ in _GOLDEN} == {1200.0, 1700.0}
    assert {cell for _, _, cell in _GOLDEN} == {None, "W"}
    assert not any(
        "outside declared G(T) interval" in reason
        for pin in _GOLDEN.values()
        for _, reason in pin["flags"]
    )
