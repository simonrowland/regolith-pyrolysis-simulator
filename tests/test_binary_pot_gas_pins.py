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


# Regenerated at openimcc f037d05; the low-temperature Si2/Si3 gas rows come from upstream 8b99c3e.
# d-071 openimcc 100df4f re-anchors FeO(l) on Darken & Gurry (1946) / Masson (1965),
# Ref DG46-MASSON65. The Fe–Mg–Si pots move with that standard state. The largest
# relative shifts are the high-oxygen polymers (Si3 about -22% at 1200 K with no
# cell, W4O12 about +22% at 1700 K on W), because pO2 moves and those stoichiometries
# amplify it. CaO–SiO2 pressures do not move. openimcc 8de0903 reads the FeO(l)
# fit interval from the redox row. Outside 1649.15–1766.15 K, Fe and FeO carry
# the D&G/Masson extrapolation flag (1200 K and 1800 K). 1700 K is inside that
# band, so those two flags stay empty. The Fe and FeO channel class becomes
# janaf_fitted. The oxide authority primary_measurement_reanchored (rank 4)
# outranks the gas JANAF fit (rank 3), and the channel keeps the
# least-authoritative row. 249fde5 still reported janaf_transcribed. ccabd0b
# keeps the 100df4f pressures and the new class. ec31045 keeps those pressures.
# openimcc 0cb75c1 adds the hypothetical-reference warning below. Gas and
# condensate identities also move at c280dcc/0cb75c1; numeric pins hold.
# openimcc 2064476 promotes measured SiO2/Na2O Cp and D066 v4. All
# pressure/root/carrier/iteration pins below are regenerated from that
# production resolver; SiO2/Na2O source flags describe the promoted rows.
# Float outputs are stored as float.hex strings so these are exact pins.
# openimcc 8de0903. The band is the FeO(l) redox fit interval.
_FEO_DG_MASSON_EXTRAPOLATION = (
    "FeO(l) uses the D&G/Masson correction extrapolated "
    "outside its 1649.15-1766.15 K fit range (Ref=DG46-MASSON65)"
)
_GOLDEN = {
    ("cao_sio2_40_60", 1200.0, None): {
        "pressures": {
            "Ca": "0x1.8a239d7a7845cp-49", "CaO": "0x1.5e723a922144fp-61",
            "O": "0x1.7812260c5584ap-34", "O2": "0x1.ff812b9dc11b4p-34",
            "Si": "0x1.c13c22fa4850ap-76", "Si2": "0x1.df3712d01cc1cp-142",
            "Si3": "0x1.d882feae7a107p-198", "SiO": "0x1.c840bcb3b73c7p-32",
            "SiO2": "0x1.24feccc8f0bc0p-41",
        },
        "total": "0x1.535c6ad0b1e3fp-31", "pO2": "0x1.4f383a1356010p-50",
        "residual": "0x1.f3b3513ad27ecp-47", "iterations": 15,
        "O": (("SiO", "0x1.684697fdbe444p-51"), ("O2", "0x1.da1643c003ae2p-52"), ("O", "0x1.ecf0654a046fap-53"), ("SiO2", "0x1.8c5afb9098385p-60"), ("CaO", "0x1.eab6e96a500f4p-81")),
        "metal": (("SiO", "0x1.684697fdbe444p-50"), ("SiO2", "0x1.8c5afb9098385p-60"), ("Ca", "0x1.466988d8594d5p-68"), ("CaO", "0x1.eab6e96a500f4p-81"), ("Si", "0x1.bc6ed27b343dep-94")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1200.0, "W"): {
        "pressures": {
            "Ca": "0x1.25364db490f18p-45", "CaO": "0x1.5e723a922144fp-61",
            "O": "0x1.f984d27ef69fap-38", "O2": "0x1.ce1eb88470852p-41",
            "Si": "0x1.f13e150ca3f86p-69", "Si2": "0x1.258e2ccc7d9afp-127",
            "Si3": "0x1.40618cc9d2c79p-176", "SiO": "0x1.536ba22eec3ddp-28",
            "SiO2": "0x1.24feccc8f0bc0p-41", "W2O6": "0x1.391ccfc721830p-29",
            "W3O8": "0x1.567c3b2e0cae0p-34", "W3O9": "0x1.00b9cbbe0c1cbp-32",
            "W4O12": "0x1.5302b820fe3aap-44", "WO": "0x1.63adf348c30acp-55",
            "WO2": "0x1.34d0ceb7fa660p-44", "WO3": "0x1.302ac7eb3c0b5p-36",
        },
        "total": "0x1.03946b9fcf7e0p-27", "pO2": "0x1.2edadeff630dfp-57",
        "residual": "0x1.6ebe79e54d17ap-47", "iterations": 20,
        "O": (("SiO", "0x1.0c052764bfe15p-47"), ("W2O6", "0x1.c96b8d12d0e94p-48"), ("W3O9", "0x1.cb55da6250cdfp-51"), ("W3O8", "0x1.1388635393886p-52"), ("WO3", "0x1.3a3449ab7b95fp-55")),
        "metal": (("SiO", "0x1.0c052764bfe15p-46"), ("SiO2", "0x1.8c5afb9098385p-60"), ("Ca", "0x1.e5a7f3cae1306p-65"), ("CaO", "0x1.eab6e96a500f4p-81"), ("Si", "0x1.ebed659432623p-87")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": "0x1.fe851225d368cp-2", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("cao_sio2_40_60", 1700.0, None): {
        "pressures": {
            "Ca": "0x1.75d602e21484cp-25", "CaO": "0x1.c95b9d382452fp-32",
            "O": "0x1.772e2b14a8174p-12", "O2": "0x1.4fde9926bb103p-11",
            "Si": "0x1.5fae2139b1c76p-41", "Si2": "0x1.f4ff0db78f6f2p-86",
            "Si3": "0x1.3ec339d47426bp-124", "SiO": "0x1.12f4d8b1e8717p-9",
            "SiO2": "0x1.1b51717eb227bp-16",
        },
        "total": "0x1.980a60ab43676p-9", "pO2": "0x1.b83b1c75f084ap-28",
        "residual": "0x1.76b8761de21f4p-51", "iterations": 18,
        "O": (("SiO", "0x1.b23bc4f41a6d4p-29"), ("O2", "0x1.374ccbd2b7f70p-29"), ("O", "0x1.ebc59163c4d97p-31"), ("SiO2", "0x1.7f43b017e8a0ep-35"), ("CaO", "0x1.4035885cfe63ep-51")),
        "metal": (("SiO", "0x1.b23bc4f41a6d4p-28"), ("SiO2", "0x1.7f43b017e8a0ep-35"), ("Ca", "0x1.35990f88d6e51p-44"), ("CaO", "0x1.4035885cfe63ep-51"), ("Si", "0x1.5bebc528ea0e6p-59")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1700.0, "W"): {
        "pressures": {
            "Ca": "0x1.849c318d90288p-22", "CaO": "0x1.c95b9d382452fp-32",
            "O": "0x1.68eab14b639bap-15", "O2": "0x1.36d11d668c26bp-17",
            "Si": "0x1.7c06cc88c1e11p-35", "Si2": "0x1.2481d9a479ebbp-73",
            "Si3": "0x1.9238c00196bf9p-106", "SiO": "0x1.1dd2a46913d37p-6",
            "SiO2": "0x1.1b51717eb227bp-16", "W2O6": "0x1.0da15d2931287p-7",
            "W3O8": "0x1.b26d4caaa472ap-12", "W3O9": "0x1.056c442ec1372p-11",
            "W4O12": "0x1.32cf718bf1d17p-20", "WO": "0x1.cd7bb7ea6cc42p-26",
            "WO2": "0x1.88b3d1eb792dfp-18", "WO3": "0x1.d758d5b5b022ep-14",
        },
        "total": "0x1.b6b0df6d3df47p-6", "pO2": "0x1.9764d488b46c1p-34",
        "residual": "0x1.5bed262b1fa85p-41", "iterations": 17,
        "O": (("SiO", "0x1.c36501b0df8e7p-26"), ("W2O6", "0x1.89e5d50f3c1cbp-26"), ("W3O9", "0x1.d3bd52225862bp-30"), ("W3O8", "0x1.5d8030b8fa720p-30"), ("WO3", "0x1.e6e69fc7f1906p-33")),
        "metal": (("SiO", "0x1.c36501b0df8e7p-25"), ("SiO2", "0x1.7f43b017e8a0ep-35"), ("Ca", "0x1.41d554f554fcep-41"), ("CaO", "0x1.4035885cfe63ep-51"), ("Si", "0x1.77f6deddc3ff2p-53")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": "0x1.fcd973617f2e0p-2", "buffer": "0x1.673a0a0b49045p-30",
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, None): {
        "pressures": {
            "Fe": "0x1.bd49e147a311bp-27", "FeO": "0x1.7e0c0e1a9a1dfp-33",
            "Mg": "0x1.321045009f6afp-35", "MgO": "0x1.b6225fb06e943p-51",
            "O": "0x1.270fed7148c96p-31", "O2": "0x1.3adfd52886a40p-28",
            "Si": "0x1.db18960a091c0p-81", "Si2": "0x1.0bfd163b61ff3p-151",
            "Si3": "0x1.17738fcc46032p-212", "SiO": "0x1.7a946c4547133p-34",
            "SiO2": "0x1.7d7e27cfcef6ep-41",
        },
        "total": "0x1.3ba815dbd912ap-26", "pO2": "0x1.9cb64c64a1bb1p-45",
        "residual": "0x1.31392e8c1f925p-49", "iterations": 14,
        "O": (("O2", "0x1.23d734a41e35bp-46"), ("O", "0x1.82c18b854589fp-50"), ("FeO", "0x1.d8a15b2030ad2p-53"), ("SiO", "0x1.2af1223a8ff98p-53"), ("SiO2", "0x1.02093d38415cap-59")),
        "metal": (("Fe", "0x1.3867ee68616e0p-46"), ("SiO", "0x1.2af1223a8ff98p-52"), ("FeO", "0x1.d8a15b2030ad2p-53"), ("Mg", "0x1.457caf9c01163p-54"), ("SiO2", "0x1.02093d38415cap-59")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Fe", _FEO_DG_MASSON_EXTRAPOLATION), ("FeO", _FEO_DG_MASSON_EXTRAPOLATION), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, "W"): {
        "pressures": {
            "Fe": "0x1.0cf6e43404dc7p-21", "FeO": "0x1.7e0c0e1a9a1dfp-33",
            "Mg": "0x1.71bd19604a706p-30", "MgO": "0x1.b6225fb06e943p-51",
            "O": "0x1.e87ec8445e5f5p-37", "O2": "0x1.af8513ae47e54p-39",
            "Si": "0x1.5aabdc6bbe0bbp-70", "Si2": "0x1.1d60951fa41e8p-130",
            "Si3": "0x1.b249393d615fep-181", "SiO": "0x1.c9577076c6966p-29",
            "SiO2": "0x1.7d7e27cfcef6ep-41", "W2O6": "0x1.fde16b58c0fa9p-24",
            "W3O8": "0x1.046414e81de13p-26", "W3O9": "0x1.793aec4809780p-24",
            "W4O12": "0x1.c17d3bba01932p-33", "WO": "0x1.57b3aa02f4946p-54",
            "WO2": "0x1.205de865618ebp-42", "WO3": "0x1.12761d0c7b21dp-33",
        },
        "total": "0x1.86c75b05576b2p-21", "pO2": "0x1.1acd03a41b210p-55",
        "residual": "0x1.0232eb1f35ca5p-46", "iterations": 22,
        "O": (("W2O6", "0x1.746ff6e8e0725p-42"), ("W3O9", "0x1.5178a73b46e7ap-42"), ("W3O8", "0x1.a2f9610f53944p-45"), ("SiO", "0x1.6922ab130a431p-48"), ("W4O12", "0x1.d0525ff08c7bfp-51")),
        "metal": (("Fe", "0x1.79668e64f2857p-41"), ("SiO", "0x1.6922ab130a431p-47"), ("Mg", "0x1.8934010294144p-49"), ("FeO", "0x1.d8a15b2030ad2p-53"), ("SiO2", "0x1.02093d38415cap-59")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Fe", _FEO_DG_MASSON_EXTRAPOLATION), ("FeO", _FEO_DG_MASSON_EXTRAPOLATION), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": "0x1.fc0e5390776a4p-1", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, None): {
        "pressures": {
            "Fe": "0x1.bb8de37019c61p-8", "FeO": "0x1.e7baf58f895fdp-12",
            "Mg": "0x1.1f5c543db1bb7p-13", "MgO": "0x1.ef7495ff513b8p-23",
            "O": "0x1.7b2d53ab96c99p-11", "O2": "0x1.5710434972c83p-9",
            "Si": "0x1.7c834ed639c2dp-43", "Si2": "0x1.2541a47966e41p-89",
            "Si3": "0x1.93c49944ec7a2p-130", "SiO": "0x1.2cab08b502061p-10",
            "SiO2": "0x1.391cb168b0e01p-16",
        },
        "total": "0x1.852cffa698745p-7", "pO2": "0x1.c1a9048dd158fp-26",
        "residual": "0x1.1cf4f559e08bdp-49", "iterations": 16,
        "O": (("O2", "0x1.3df7bd4057765p-27"), ("O", "0x1.f102af28e5a8cp-30"), ("SiO", "0x1.dad6e4aa49b81p-30"), ("FeO", "0x1.2daf7bba6decap-31"), ("SiO2", "0x1.a7919b1879a0ap-35")),
        "metal": (("Fe", "0x1.37306f6d93071p-27"), ("SiO", "0x1.dad6e4aa49b81p-29"), ("FeO", "0x1.2daf7bba6decap-31"), ("Mg", "0x1.3198e44739fb4p-32"), ("SiO2", "0x1.a7919b1879a0ap-35")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, "W"): {
        "pressures": {
            "Fe": "0x1.6565cf884ab15p-4", "FeO": "0x1.e7baf58f895fdp-12",
            "Mg": "0x1.cf1632514779cp-10", "MgO": "0x1.ef7495ff513b8p-23",
            "O": "0x1.d695794e27722p-15", "O2": "0x1.0833782fa04c1p-16",
            "Si": "0x1.ee1803f9b84d8p-36", "Si2": "0x1.ee74b371facdap-75",
            "Si3": "0x1.b9ffe5adaa1ccp-108", "SiO": "0x1.e488292e2664dp-7",
            "SiO2": "0x1.391cb168b0e01p-16", "W2O6": "0x1.4b32e0e8789bfp-5",
            "W3O8": "0x1.c597e628208e7p-9", "W3O9": "0x1.63e578f01027bp-8",
            "W4O12": "0x1.ceec966535e86p-16", "WO": "0x1.2cdaa76dec7f8p-25",
            "WO2": "0x1.4dce38f4ca4c2p-17", "WO3": "0x1.0532d3070e063p-12",
        },
        "total": "0x1.3b55975c586f9p-3", "pO2": "0x1.5a4b299d0acb1p-33",
        "residual": "0x1.654dd13dc6bf4p-48", "iterations": 21,
        "O": (("W2O6", "0x1.e3d786f3b6055p-24"), ("SiO", "0x1.7e9b22cf714eap-26"), ("W3O9", "0x1.3e62d281fca66p-26"), ("W3O8", "0x1.6ceb9909d4f74p-27"), ("FeO", "0x1.2daf7bba6decap-31")),
        "metal": (("Fe", "0x1.f57c9fb4f54d5p-24"), ("SiO", "0x1.7e9b22cf714eap-25"), ("Mg", "0x1.ec79aaa006221p-29"), ("FeO", "0x1.2daf7bba6decap-31"), ("SiO2", "0x1.a7919b1879a0ap-35")),
        "flags": (("SiO", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate"), ("SiO2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si2", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"), ("Si3", "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature")),
        "flux": "0x1.b8a42b046e788p-1", "buffer": "0x1.673a0a0b49045p-30",
    },
}

_PROVENANCE_CLASSES = {
    "Al": "janaf_fitted", "Al2": "janaf_fitted", "Al2O": "janaf_fitted",
    "Al2O2": "janaf_fitted", "AlO": "janaf_fitted", "AlO2": "janaf_fitted",
    "Ca": "janaf_fitted", "CaO": "janaf_fitted", "Fe": "janaf_fitted",
    "FeO": "janaf_fitted", "K": "estimated_systematic",
    "K2": "estimated_systematic", "K2O": "estimated_systematic",
    "KO": "estimated_systematic", "Mg": "janaf_fitted",
    "MgO": "janaf_fitted", "Na": "janaf_fitted", "Na2": "janaf_fitted",
    "Na2O": "nasa_glenn_fitted", "NaO": "janaf_fitted", "O": "janaf_fitted",
    "O2": "janaf_fitted", "Si": "janaf_fitted", "Si2": "janaf_fitted",
    "Si3": "janaf_fitted", "SiO": "janaf_fitted", "SiO2": "janaf_fitted",
    "Ti": "janaf_fitted", "TiO": "janaf_fitted", "TiO2": "janaf_fitted",
}
_COMMON_LOW_FLAGS = "Al2O3(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Al-100; this is a hypothetical reference, not a prediction of a liquid condensate"
_SILICA_LOW_FLAGS = "SiO2(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; SiO2(l) uses a supercooled liquid_from_solid branch below the 1996 K fusion temperature"
_TITANIA_LOW_FLAGS = "TiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-044"
_TITANIA_SUPERCOOLED_FLAGS = "TiO2(l) uses a source-tabulated supercooled liquid branch below the 2130 K fusion marker (O-044)"
_POTASSIUM_OXIDE_LOW_FLAGS = "K2O(l) uses a tier-4 liquid_from_solid construction from K-012"
_SODIUM_OXIDE_LOW_FLAGS = "Na2O(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984; Na2O(l) uses a supercooled liquid_from_solid branch below the 1405.2 K fusion temperature"
_DOMAIN_FLAGS = {
    1200.0: {
        "Al": _COMMON_LOW_FLAGS,
        "Al2": _COMMON_LOW_FLAGS,
        "Al2O": _COMMON_LOW_FLAGS, "Al2O2": _COMMON_LOW_FLAGS, "AlO": _COMMON_LOW_FLAGS,
        "AlO2": _COMMON_LOW_FLAGS,
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate",
        "Fe": _FEO_DG_MASSON_EXTRAPOLATION,
        "FeO": _FEO_DG_MASSON_EXTRAPOLATION,
        "K": _POTASSIUM_OXIDE_LOW_FLAGS, "K2": _POTASSIUM_OXIDE_LOW_FLAGS,
        "K2O": _POTASSIUM_OXIDE_LOW_FLAGS, "KO": _POTASSIUM_OXIDE_LOW_FLAGS,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate",
        "Na": _SODIUM_OXIDE_LOW_FLAGS, "Na2": _SODIUM_OXIDE_LOW_FLAGS,
        "Na2O": _SODIUM_OXIDE_LOW_FLAGS, "NaO": _SODIUM_OXIDE_LOW_FLAGS,
        "O": None, "O2": None,
        "Si": _SILICA_LOW_FLAGS,
        "Si2": _SILICA_LOW_FLAGS,
        "Si3": _SILICA_LOW_FLAGS,
        "SiO": _SILICA_LOW_FLAGS, "SiO2": _SILICA_LOW_FLAGS,
        "Ti": _TITANIA_LOW_FLAGS, "TiO": _TITANIA_LOW_FLAGS,
        "TiO2": _TITANIA_LOW_FLAGS,
    },
    1700.0: {
        "Al": _COMMON_LOW_FLAGS, "Al2": _COMMON_LOW_FLAGS, "Al2O": _COMMON_LOW_FLAGS,
        "Al2O2": _COMMON_LOW_FLAGS, "AlO": _COMMON_LOW_FLAGS, "AlO2": _COMMON_LOW_FLAGS,
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028; this is a hypothetical reference, not a prediction of a liquid condensate",
        "Fe": None, "FeO": None,
        "K": _POTASSIUM_OXIDE_LOW_FLAGS, "K2": _POTASSIUM_OXIDE_LOW_FLAGS,
        "K2O": _POTASSIUM_OXIDE_LOW_FLAGS, "KO": _POTASSIUM_OXIDE_LOW_FLAGS,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009; this is a hypothetical reference, not a prediction of a liquid condensate",
        "Na": "Na2O(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984", "Na2": "Na2O(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984", "Na2O": "Na2O(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984", "NaO": "Na2O(l) uses a tier-1 liquid_from_solid construction from Richet-Bottinga-Tequi-1984", "O": None, "O2": None,
        "Si": _SILICA_LOW_FLAGS, "Si2": _SILICA_LOW_FLAGS, "Si3": _SILICA_LOW_FLAGS,
        "SiO": _SILICA_LOW_FLAGS, "SiO2": _SILICA_LOW_FLAGS,
        "Ti": _TITANIA_SUPERCOOLED_FLAGS,
        "TiO": _TITANIA_SUPERCOOLED_FLAGS, "TiO2": _TITANIA_SUPERCOOLED_FLAGS,
    },
}
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
        # openimcc 2064476 promotes D066 v4 and its measured liquid Cp standards.
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
        assert diagnostics["authority"] == "extrapolated"
        assert diagnostics["openimcc_provenance"] == gas_provenance
        assert gas["domain_flags"] == _DOMAIN_FLAGS[temperature_K]
        assert gas["provenance_class"] == _PROVENANCE_CLASSES
        expected_gas_notices = {
            ("openimcc_gas_flag", "extrapolated", species, reason)
            for species, reason in _DOMAIN_FLAGS[temperature_K].items()
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

    # 1800 K is outside the 1649.15–1766.15 K fit. 1700 K is inside it and
    # stays on the golden above.
    hot = _gas_result(backend, pots["feo_mgo_sio2_30_20_50"], 1800.0, None)
    hot_flags = hot.diagnostics["openimcc_gas"]["domain_flags"]
    assert hot_flags["Fe"] == _FEO_DG_MASSON_EXTRAPOLATION
    assert hot_flags["FeO"] == _FEO_DG_MASSON_EXTRAPOLATION


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
