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
# amplify it. CaO–SiO2 pressures do not move. At 1200 K the Fe and FeO domain
# flags are None. That is the current openimcc output: gas.py _source_model_flag
# emits the supercooled notice only when the liquid Ref is Fe-019 or JANAF, and
# the row is now DG46-MASSON65. The redox metadata flags the Darken-Gurry/Masson
# correction as a linear extrapolation outside 1649.15–1766.15 K, and that gas
# matcher does not read the band. This pin records the engine output. It does
# not accept 1200 K as inside the anchor band. 1700 K is inside that band.
# The Fe and FeO channel class becomes janaf_fitted. The oxide authority
# primary_measurement_reanchored (rank 4) outranks the gas JANAF fit
# (rank 3), and the channel keeps the least-authoritative row. 249fde5 still reported
# janaf_transcribed. ccabd0b keeps the 100df4f pressures and the new class.
# Float outputs are stored as float.hex strings so these are exact pins.
_GOLDEN = {
    ("cao_sio2_40_60", 1200.0, None): {
        "pressures": {
            "Ca": "0x1.86a34857d97cfp-49", "CaO": "0x1.509fe9032de8ap-61",
            "O": "0x1.6c79fb9838ad1p-34", "O2": "0x1.e07376c2990acp-34",
            "Si": "0x1.b81f475f1d5dcp-76", "Si2": "0x1.cbf877428edb0p-142",
            "Si3": "0x1.bc561b7e18be2p-198", "SiO": "0x1.b13779512e22ep-32",
            "SiO2": "0x1.0d9ffb54aa148p-41",
        },
        "total": "0x1.427d34a0db165p-31", "pO2": "0x1.3ade4ea65c98ep-50",
        "residual": "0x1.0c60aacc06d05p-44", "iterations": 15,
        "O": (("SiO", "0x1.5615e032a1ad1p-51"), ("O2", "0x1.bd4e186b5803dp-52"), ("O", "0x1.ddbdd6fa4a886p-53"), ("SiO2", "0x1.6cbd9a5f7dfe2p-60"), ("CaO", "0x1.d75c5be5cb0e5p-81")),
        "metal": (("SiO", "0x1.5615e032a1ad1p-50"), ("SiO2", "0x1.6cbd9a5f7dfe2p-60"), ("Ca", "0x1.4383392738376p-68"), ("CaO", "0x1.d75c5be5cb0e5p-81"), ("Si", "0x1.b36ae6a7250e3p-94")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1200.0, "W"): {
        "pressures": {
            "Ca": "0x1.1cd9a66e10688p-45", "CaO": "0x1.509fe9032de8ap-61",
            "O": "0x1.f3d5f229fbfbap-38", "O2": "0x1.c3c9b8f18c976p-41",
            "Si": "0x1.d40b8caf64559p-69", "Si2": "0x1.0417dac439126p-127",
            "Si3": "0x1.0b316eb70e793p-176", "SiO": "0x1.3be5fd12f7c75p-28",
            "SiO2": "0x1.0d9ffb54aa148p-41", "W2O6": "0x1.2493b610681eap-29",
            "W3O8": "0x1.38de46e4c4afbp-34", "W3O9": "0x1.cfc6dc1b16f42p-33",
            "W4O12": "0x1.28002d3e0c262p-44", "WO": "0x1.5fae4f42c90f1p-55",
            "WO2": "0x1.2de943c1fe4b5p-44", "WO3": "0x1.260607137bebcp-36",
        },
        "total": "0x1.e34df36bbb089p-28", "pO2": "0x1.281571e9e9f10p-57",
        "residual": "0x1.171ed7fd6e3b7p-48", "iterations": 20,
        "O": (("SiO", "0x1.f2e4702b5f64ap-48"), ("W2O6", "0x1.ab6b8dbfa2c60p-48"), ("W3O9", "0x1.9ee56ca719ba6p-51"), ("W3O8", "0x1.f769434b4fa00p-53"), ("WO3", "0x1.2fb9d8486522cp-55")),
        "metal": (("SiO", "0x1.f2e4702b5f64ap-47"), ("SiO2", "0x1.6cbd9a5f7dfe2p-60"), ("Ca", "0x1.d7ce52d9a7dffp-65"), ("CaO", "0x1.d75c5be5cb0e5p-81"), ("Si", "0x1.cf0ac2fe0778cp-87")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fe6ee7d423bc6p-2", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("cao_sio2_40_60", 1700.0, None): {
        "pressures": {
            "Ca": "0x1.741c1a40bd670p-25", "CaO": "0x1.c5e7962872692p-32",
            "O": "0x1.76132c40cfc63p-12", "O2": "0x1.4de4a8536f628p-11",
            "Si": "0x1.5eeff66e11a61p-41", "Si2": "0x1.f2e1cf3565080p-86",
            "Si3": "0x1.3cbf3720f7d2dp-124", "SiO": "0x1.1191352190c45p-9",
            "SiO2": "0x1.190e5d4268f5dp-16",
        },
        "total": "0x1.9600592253c4ap-9", "pO2": "0x1.b5a3f6eca9ea4p-28",
        "residual": "0x1.2d4e0946544f0p-52", "iterations": 18,
        "O": (("SiO", "0x1.b00a1d94be1b5p-29"), ("O2", "0x1.3577ddb5b1599p-29"), ("O", "0x1.ea52a0ea98dbcp-31"), ("SiO2", "0x1.7c3453ac02456p-35"), ("CaO", "0x1.3dca999f183b8p-51")),
        "metal": (("SiO", "0x1.b00a1d94be1b5p-28"), ("SiO2", "0x1.7c3453ac02456p-35"), ("Ca", "0x1.342b165b6c4d4p-44"), ("CaO", "0x1.3dca999f183b8p-51"), ("Si", "0x1.5b2fa2bfeda04p-59")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1700.0, "W"): {
        "pressures": {
            "Ca": "0x1.821a6e6fce2bbp-22", "CaO": "0x1.c5e7962872692p-32",
            "O": "0x1.688471ee83635p-15", "O2": "0x1.36211a94015b0p-17",
            "Si": "0x1.79d403af1e4a6p-35", "Si2": "0x1.212200cbdb487p-73",
            "Si3": "0x1.8b481b5425eadp-106", "SiO": "0x1.1bdadc18a1bd0p-6",
            "SiO2": "0x1.190e5d4268f5dp-16", "W2O6": "0x1.0bd850361050ep-7",
            "W3O8": "0x1.ae988576edb70p-12", "W3O9": "0x1.02d4a91d4b143p-11",
            "W4O12": "0x1.2ec2bd0458083p-20", "WO": "0x1.ccf8fb00855e3p-26",
            "WO2": "0x1.87d5703998e82p-18", "WO3": "0x1.d5c8ae52dc4dfp-14",
        },
        "total": "0x1.b3adf5fd9b7dcp-6", "pO2": "0x1.967e2108c5babp-34",
        "residual": "0x1.1ddcdcff20961p-41", "iterations": 17,
        "O": (("SiO", "0x1.c049647ad77e1p-26"), ("W2O6", "0x1.874a22c4e86f4p-26"), ("W3O9", "0x1.cf19fe460307ep-30"), ("W3O8", "0x1.5a6b250aae229p-30"), ("WO3", "0x1.e5494405d3f23p-33")),
        "metal": (("SiO", "0x1.c049647ad77e1p-25"), ("SiO2", "0x1.7c3453ac02456p-35"), ("Ca", "0x1.3fc1d8cc5bf9cp-41"), ("CaO", "0x1.3dca999f183b8p-51"), ("Si", "0x1.75ca1a0e10920p-53")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fcd5aee174395p-2", "buffer": "0x1.673a0a0b49045p-30",
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, None): {
        "pressures": {
            "Fe": "0x1.b809f9a06a029p-27", "FeO": "0x1.77201368019ecp-33",
            "Mg": "0x1.2d809aa80e3e5p-35", "MgO": "0x1.acd74675b03d8p-51",
            "O": "0x1.252c348600ea7p-31", "O2": "0x1.36dabbe7fa937p-28",
            "Si": "0x1.b99470fb07c6bp-81", "Si2": "0x1.cf05bd6b67315p-152",
            "Si3": "0x1.c0c40fba15110p-213", "SiO": "0x1.5d9e7cb565bc2p-34",
            "SiO2": "0x1.5e0d999029c52p-41",
        },
        "total": "0x1.37ca6950dbe12p-26", "pO2": "0x1.977170307807cp-45",
        "residual": "0x1.4ef3db7464879p-49", "iterations": 14,
        "O": (("O2", "0x1.201d633ce9e4ap-46"), ("O", "0x1.8047802a1b698p-50"), ("FeO", "0x1.d0113db9f4368p-53"), ("SiO", "0x1.1412ca2755b56p-53"), ("SiO2", "0x1.d98aa147c3c9ep-60")),
        "metal": (("Fe", "0x1.34b912c0475cfp-46"), ("SiO", "0x1.1412ca2755b56p-52"), ("FeO", "0x1.d0113db9f4368p-53"), ("Mg", "0x1.40a2eaf591fe9p-54"), ("SiO2", "0x1.d98aa147c3c9ep-60")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, "W"): {
        "pressures": {
            "Fe": "0x1.08ad896858a8cp-21", "FeO": "0x1.77201368019ecp-33",
            "Mg": "0x1.6ab320ddea276p-30", "MgO": "0x1.acd74675b03d8p-51",
            "O": "0x1.e769b11ff0c3fp-37", "O2": "0x1.ad9c133663f68p-39",
            "Si": "0x1.3f8407b3d9de4p-70", "Si2": "0x1.e4d6ed374c56cp-131",
            "Si3": "0x1.5403fee2b110ep-181", "SiO": "0x1.a4953afb16902p-29",
            "SiO2": "0x1.5e0d999029c52p-41", "W2O6": "0x1.f723adb693eefp-24",
            "W3O8": "0x1.ff9f2c8f6efccp-27", "W3O9": "0x1.71c61bba31a51p-24",
            "W4O12": "0x1.b5aeaa9ea4d50p-33", "WO": "0x1.56f0b48b84b3fp-54",
            "WO2": "0x1.1f1720cd3d7d0p-42", "WO3": "0x1.10a418e83738dp-33",
        },
        "total": "0x1.806976f32f73cp-21", "pO2": "0x1.198c8abf750a1p-55",
        "residual": "0x1.cc4a5858196f6p-47", "iterations": 22,
        "O": (("W2O6", "0x1.6f8369e6f2731p-42"), ("W3O9", "0x1.4acd05883a59fp-42"), ("W3O8", "0x1.9b9acff0f37a6p-45"), ("SiO", "0x1.4c1bfc6fe4d54p-48"), ("W4O12", "0x1.c4201126c5c3ap-51")),
        "metal": (("Fe", "0x1.7362ca3f477c1p-41"), ("SiO", "0x1.4c1bfc6fe4d54p-47"), ("Mg", "0x1.81b7ad0c6f85ap-49"), ("FeO", "0x1.d0113db9f4368p-53"), ("SiO2", "0x1.d98aa147c3c9ep-60")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fc4c57904c896p-1", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, None): {
        "pressures": {
            "Fe": "0x1.badca78fc8739p-8", "FeO": "0x1.e64dffeec0490p-12",
            "Mg": "0x1.1e7f04c3794dap-13", "MgO": "0x1.ed4a7e8f60e07p-23",
            "O": "0x1.7aa8e63859a96p-11", "O2": "0x1.5620cbe824bf3p-9",
            "Si": "0x1.7a39ba64f1504p-43", "Si2": "0x1.21bdc1f855fb9p-89",
            "Si3": "0x1.8c87adf20a2bdp-130", "SiO": "0x1.2a73f3bc43d26p-10",
            "SiO2": "0x1.3661979684638p-16",
        },
        "total": "0x1.84391dc5e0db2p-7", "pO2": "0x1.c06f24fcd4552p-26",
        "residual": "0x1.accb8a2bdc850p-50", "iterations": 16,
        "O": (("O2", "0x1.3d19ca56986a6p-27"), ("O", "0x1.f0551a765c21ap-30"), ("SiO", "0x1.d7574fbf080a2p-30"), ("FeO", "0x1.2ccdbcd9e77a6p-31"), ("SiO2", "0x1.a3dfe2028ae22p-35")),
        "metal": (("Fe", "0x1.36b41762a4491p-27"), ("SiO", "0x1.d7574fbf080a2p-29"), ("FeO", "0x1.2ccdbcd9e77a6p-31"), ("Mg", "0x1.30ad895022f98p-32"), ("SiO2", "0x1.a3dfe2028ae22p-35")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, "W"): {
        "pressures": {
            "Fe": "0x1.648953e7bacbbp-4", "FeO": "0x1.e64dffeec0490p-12",
            "Mg": "0x1.cd4d0d3b1dba5p-10", "MgO": "0x1.ed4a7e8f60e07p-23",
            "O": "0x1.d6578113fcce1p-15", "O2": "0x1.07ede7626663ep-16",
            "Si": "0x1.ea49ec70c5d9dp-36", "Si2": "0x1.e6de98b947f1ep-75",
            "Si3": "0x1.afddcef0e3723p-108", "SiO": "0x1.e08d9adfebcf0p-7",
            "SiO2": "0x1.3661979684638p-16", "W2O6": "0x1.4a2d872d23589p-5",
            "W3O8": "0x1.c3bae69ba5728p-9", "W3O9": "0x1.624089b4217e7p-8",
            "W4O12": "0x1.cc131f43b3ef0p-16", "WO": "0x1.2cb30918dfa29p-25",
            "WO2": "0x1.4d76546047cc2p-17", "WO3": "0x1.04cbb0333149dp-12",
        },
        "total": "0x1.3a4d0e3679a72p-3", "pO2": "0x1.59effb42cc2a2p-33",
        "residual": "0x1.4f98b9ef123eap-48", "iterations": 21,
        "O": (("W2O6", "0x1.e259b9a654f36p-24"), ("SiO", "0x1.7b76d7ce4b86cp-26"), ("W3O9", "0x1.3cea40c187443p-26"), ("W3O8", "0x1.6b6bd919820fap-27"), ("FeO", "0x1.2ccdbcd9e77a6p-31")),
        "metal": (("Fe", "0x1.f4474029ba693p-24"), ("SiO", "0x1.7b76d7ce4b86cp-25"), ("Mg", "0x1.ea93829a0ae1dp-29"), ("FeO", "0x1.2ccdbcd9e77a6p-31"), ("SiO2", "0x1.a3dfe2028ae22p-35")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.b8edd4f9643b1p-1", "buffer": "0x1.673a0a0b49045p-30",
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
_COMMON_LOW_FLAGS = "Al2O3(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Al-100"
_SILICA_LOW_FLAGS = "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"
_TITANIA_LOW_FLAGS = "TiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-044"
_TITANIA_SUPERCOOLED_FLAGS = "TiO2(l) uses a source-tabulated supercooled liquid branch below the 2130 K fusion marker (O-044)"
_POTASSIUM_OXIDE_LOW_FLAGS = "K2O(l) uses a tier-4 liquid_from_solid construction from K-012"
_SODIUM_OXIDE_LOW_FLAGS = "Na2O(l) uses a source-tabulated supercooled liquid branch below the 1405.2 K fusion marker (Na-013)"
_DOMAIN_FLAGS = {
    1200.0: {
        "Al": _COMMON_LOW_FLAGS,
        "Al2": _COMMON_LOW_FLAGS,
        "Al2O": _COMMON_LOW_FLAGS, "Al2O2": _COMMON_LOW_FLAGS, "AlO": _COMMON_LOW_FLAGS,
        "AlO2": _COMMON_LOW_FLAGS,
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "Fe": None, "FeO": None,
        "K": _POTASSIUM_OXIDE_LOW_FLAGS, "K2": _POTASSIUM_OXIDE_LOW_FLAGS,
        "K2O": _POTASSIUM_OXIDE_LOW_FLAGS, "KO": _POTASSIUM_OXIDE_LOW_FLAGS,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
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
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "Fe": None, "FeO": None,
        "K": _POTASSIUM_OXIDE_LOW_FLAGS, "K2": _POTASSIUM_OXIDE_LOW_FLAGS,
        "K2O": _POTASSIUM_OXIDE_LOW_FLAGS, "KO": _POTASSIUM_OXIDE_LOW_FLAGS,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "Na": None, "Na2": None, "Na2O": None, "NaO": None, "O": None, "O2": None,
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
        # d-071 ccabd0b reuses pack version 1.0.4-d066-v2. The d066-v1 digest
        # was 02c88c04fe09f8f494adda8a76e1f95fee536d11b2b6d66639aaea097fce5e75.
        "pack": "1.0.4-d066-v2",
        "pack_digest": "ac88aa2272c50d1cbf300634737c5d0071bddee0cd0f0483a6530c4a4992c393",
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
