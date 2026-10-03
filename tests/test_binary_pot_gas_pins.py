"""Golden pins for the two binary-pot OpenIMCC gas report paths."""

from __future__ import annotations

from importlib.resources import files
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


# Captured from production at 099d2ff1051b149b500f086314ba7c7b2ed3972b.
# Float outputs are stored as float.hex strings so these are exact pins.
_GOLDEN = {
    ("cao_sio2_40_60", 1200.0, None): {
        "pressures": {
            "Ca": "0x1.86ade57d33d83p-49", "CaO": "0x1.50a95f0dde4d9p-61",
            "O": "0x1.6c7a52d8d08bdp-34", "O2": "0x1.e0745ccaab321p-34",
            "Si": "0x1.b81f8bed2cbc0p-76", "Si2": "0x1.cc0466ac0c8bfp-142",
            "Si3": "0x1.bc566b1be58b7p-198", "SiO": "0x1.b138248136f4bp-32",
            "SiO2": "0x1.0da0a66b9fbb4p-41",
        },
        "total": "0x1.427db20f61ec9p-31", "pO2": "0x1.3adee5673f73dp-50",
        "residual": "0x1.07249d8a74dacp-44", "iterations": 15,
        "O": (("SiO", "0x1.5616675fdd4fap-51"), ("O2", "0x1.bd4eed9fa1ea1p-52"), ("O", "0x1.ddbe495839c02p-53"), ("SiO2", "0x1.6cbe81d16e360p-60"), ("CaO", "0x1.d7699b60757f5p-81")),
        "metal": (("SiO", "0x1.5616675fdd4fap-50"), ("SiO2", "0x1.6cbe81d16e360p-60"), ("Ca", "0x1.438c0366024c0p-68"), ("CaO", "0x1.d7699b60757f5p-81"), ("Si", "0x1.b36b2a799b582p-94")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "T=1200.0 K outside declared G(T) interval for 'Si2(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "T=1200.0 K outside declared G(T) interval for 'Si3(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1200.0, "W"): {
        "pressures": {
            "Ca": "0x1.1ce18f577e314p-45", "CaO": "0x1.50a95f0dde4d9p-61",
            "O": "0x1.f3d61d5735cc9p-38", "O2": "0x1.c3ca06fefc44dp-41",
            "Si": "0x1.d40c64d1ee94fp-69", "Si2": "0x1.041f39abc4a37p-127",
            "Si3": "0x1.0b3293e3f549dp-176", "SiO": "0x1.3be6aa3d077fep-28",
            "SiO2": "0x1.0da0a66b9fbb4p-41", "W2O6": "0x1.24944db453040p-29",
            "W3O8": "0x1.38df1f1a7782cp-34", "W3O9": "0x1.cfc844aa09395p-33",
            "W4O12": "0x1.28016012b9273p-44", "WO": "0x1.5fae6da3c1db6p-55",
            "WO2": "0x1.2de977eac26a9p-44", "WO3": "0x1.260653455ecc1p-36",
        },
        "total": "0x1.e34efb7130608p-28", "pO2": "0x1.2815a510f2a8dp-57",
        "residual": "0x1.bb4e27ca18175p-49", "iterations": 20,
        "O": (("SiO", "0x1.f2e581a4ff4efp-48"), ("W2O6", "0x1.ab6c6b46e8f46p-48"), ("W3O9", "0x1.9ee6af359bbe5p-51"), ("W3O8", "0x1.f76a9f2e0fd3ep-53"), ("WO3", "0x1.2fba26fdf385ep-55")),
        "metal": (("SiO", "0x1.f2e581a4ff4efp-47"), ("SiO2", "0x1.6cbe81d16e360p-60"), ("Ca", "0x1.d7db6cc5ca9f9p-65"), ("CaO", "0x1.d7699b60757f5p-81"), ("Si", "0x1.cf0b98d11fc6cp-87")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "T=1200.0 K outside declared G(T) interval for 'Si2(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "T=1200.0 K outside declared G(T) interval for 'Si3(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fe6ee885a3519p-2", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("cao_sio2_40_60", 1700.0, None): {
        "pressures": {
            "Ca": "0x1.741894e17d23bp-25", "CaO": "0x1.c5e4aefe36cf7p-32",
            "O": "0x1.761451ef29410p-12", "O2": "0x1.4de6b49955af7p-11",
            "Si": "0x1.5ef0bbce3963bp-41", "Si2": "0x1.f2e40060a54d9p-86",
            "Si3": "0x1.3cc14d9257616p-124", "SiO": "0x1.1192a5c481471p-9",
            "SiO2": "0x1.1910b4a52748ap-16",
        },
        "total": "0x1.96027637c359dp-9", "pO2": "0x1.b5a6a61981983p-28",
        "residual": "0x1.789f8e1566362p-50", "iterations": 18,
        "O": (("SiO", "0x1.b00c63c2f9f0fp-29"), ("O2", "0x1.3579c3a1928cbp-29"), ("O", "0x1.ea5421dcaf96cp-31"), ("SiO2", "0x1.7c377e80d17d4p-35"), ("CaO", "0x1.3dc8914f8db53p-51")),
        "metal": (("SiO", "0x1.b00c63c2f9f0fp-28"), ("SiO2", "0x1.7c377e80d17d4p-35"), ("Ca", "0x1.34282bdf4d65fp-44"), ("CaO", "0x1.3dc8914f8db53p-51"), ("Si", "0x1.5b306603f8f82p-59")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("cao_sio2_40_60", 1700.0, "W"): {
        "pressures": {
            "Ca": "0x1.8217848bb06eap-22", "CaO": "0x1.c5e4aefe36cf7p-32",
            "O": "0x1.6884dc221f5b5p-15", "O2": "0x1.3621d14b865e4p-17",
            "Si": "0x1.79d64ad706f08p-35", "Si2": "0x1.21257d538a5cdp-73",
            "Si3": "0x1.8b4f41a7a2511p-106", "SiO": "0x1.1bdce5d4810c3p-6",
            "SiO2": "0x1.1910b4a52748ap-16", "W2O6": "0x1.0bda29a0ff55dp-7",
            "W3O8": "0x1.ae9c7c3e59040p-12", "W3O9": "0x1.02d75758b1141p-11",
            "W4O12": "0x1.2ec6eb4b5a62bp-20", "WO": "0x1.ccf982cbbc3f4p-26",
            "WO2": "0x1.87d6571442073p-18", "WO3": "0x1.d5ca4d7e7105cp-14",
        },
        "total": "0x1.b3b1145924e72p-6", "pO2": "0x1.967f108661a3cp-34",
        "residual": "0x1.1203f3a4f4f1cp-41", "iterations": 17,
        "O": (("SiO", "0x1.c04c9c71baff5p-26"), ("W2O6", "0x1.874cd66043fe3p-26"), ("W3O9", "0x1.cf1eca158be1bp-30"), ("W3O8", "0x1.5a6e5570fd37dp-30"), ("WO3", "0x1.e54af0e4a27c5p-33")),
        "metal": (("SiO", "0x1.c04c9c71baff5p-25"), ("SiO2", "0x1.7c377e80d17d4p-35"), ("Ca", "0x1.3fbf6f13cdba8p-41"), ("CaO", "0x1.3dc8914f8db53p-51"), ("Si", "0x1.75cc5afa30782p-53")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("CaO", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Ca", "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fcd5b2c96ae14p-2", "buffer": "0x1.673a0a0b49045p-30",
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, None): {
        "pressures": {
            "Fe": "0x1.9ab0427e2b128p-27", "FeO": "0x1.51f31284b1206p-33",
            "Mg": "0x1.2bbd768910669p-35", "MgO": "0x1.9b8860638af45p-51",
            "O": "0x1.1afe8398bacb9p-31", "O2": "0x1.21a4f73bdecd8p-28",
            "Si": "0x1.e018a9b0beb89p-81", "Si2": "0x1.11af90ffc5fcep-151",
            "Si3": "0x1.205deac7aede1p-212", "SiO": "0x1.6eeadb8f84b27p-34",
            "SiO2": "0x1.629e4ac439d7ep-41",
        },
        "total": "0x1.2344c8f10a3adp-26", "pO2": "0x1.7ba4884b32494p-45",
        "residual": "0x1.81e5758dcd23ep-49", "iterations": 14,
        "O": (("O2", "0x1.0c74d1682dd17p-46"), ("O", "0x1.72f00ffc423ecp-50"), ("FeO", "0x1.a213c2947944ap-53"), ("SiO", "0x1.21bb9c4465787p-53"), ("SiO2", "0x1.dfb79a2ee5747p-60")),
        "metal": (("Fe", "0x1.20219809f8532p-46"), ("SiO", "0x1.21bb9c4465787p-52"), ("FeO", "0x1.a213c2947944ap-53"), ("Mg", "0x1.3ec32570d81cep-54"), ("SiO2", "0x1.dfb79a2ee5747p-60")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "T=1200.0 K outside declared G(T) interval for 'Si2(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "T=1200.0 K outside declared G(T) interval for 'Si3(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1200.0, "W"): {
        "pressures": {
            "Fe": "0x1.e2c1d0a05d4e2p-22", "FeO": "0x1.51f31284b1206p-33",
            "Mg": "0x1.6056d4577c6d1p-30", "MgO": "0x1.9b8860638af45p-51",
            "O": "0x1.e17e9e1488b63p-37", "O2": "0x1.a33d7deb1bbaep-39",
            "Si": "0x1.4bb04b118ca34p-70", "Si2": "0x1.0544ae30c4b94p-130",
            "Si3": "0x1.7c60009f805c6p-181", "SiO": "0x1.af4e1018231a9p-29",
            "SiO2": "0x1.629e4ac439d7ep-41", "W2O6": "0x1.d3943474fa6a5p-24",
            "W3O8": "0x1.cffc51ae231a5p-27", "W3O9": "0x1.4b45e8b9bb40bp-24",
            "W4O12": "0x1.7a0018874796fp-33", "WO": "0x1.52c6b5a71d33cp-54",
            "WO2": "0x1.18292e1f08a26p-42", "WO3": "0x1.06d46923d3852p-33",
        },
        "total": "0x1.5f1b2c7087f90p-21", "pO2": "0x1.12c0d5980857cp-55",
        "residual": "0x1.639324b53bdccp-48", "iterations": 22,
        "O": (("W2O6", "0x1.5589de57078afp-42"), ("W3O9", "0x1.285ba274f296cp-42"), ("W3O8", "0x1.7547ee8aa8c2cp-45"), ("SiO", "0x1.54936ac07ad5ap-48"), ("W4O12", "0x1.8679529069436p-51")),
        "metal": (("Fe", "0x1.52b173282c514p-41"), ("SiO", "0x1.54936ac07ad5ap-47"), ("Mg", "0x1.76b30e56f0542p-49"), ("FeO", "0x1.a213c2947944ap-53"), ("SiO2", "0x1.dfb79a2ee5747p-60")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "T=1200.0 K outside declared G(T) interval for 'Si2(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "T=1200.0 K outside declared G(T) interval for 'Si3(g)' [1500, 3000] K; SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.fbde37bca9f17p-1", "buffer": "0x1.0e9617dca9e3ap-54",
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, None): {
        "pressures": {
            "Fe": "0x1.8aa0b24253dfap-8", "FeO": "0x1.9e5d975ea14b2p-12",
            "Mg": "0x1.19dccbdf7af2ep-13", "MgO": "0x1.d01051946f620p-23",
            "O": "0x1.6a149c6304b8ep-11", "O2": "0x1.38d311a605c9bp-9",
            "Si": "0x1.a7304aa704c3ap-43", "Si2": "0x1.6ab96e9bcdad4p-89",
            "Si3": "0x1.15b66f097ad2ap-129", "SiO": "0x1.3f4fd5c441fe2p-10",
            "SiO2": "0x1.3d88c64b6dc26p-16",
        },
        "total": "0x1.640b566768a20p-7", "pO2": "0x1.9a067ae92c884p-26",
        "residual": "0x1.84288e0437f4ap-46", "iterations": 16,
        "O": (("O2", "0x1.21f0d45701bf4p-27"), ("SiO", "0x1.f8487cbea9e7dp-30"), ("O", "0x1.da99db163677bp-30"), ("FeO", "0x1.004e45a6c37f8p-31"), ("SiO2", "0x1.ad8d0d676f5d9p-35")),
        "metal": (("Fe", "0x1.14dcffdc70977p-27"), ("SiO", "0x1.f8487cbea9e7dp-29"), ("FeO", "0x1.004e45a6c37f8p-31"), ("Mg", "0x1.2bc008a340a12p-32"), ("SiO2", "0x1.ad8d0d676f5d9p-35")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": None, "buffer": None,
    },
    ("feo_mgo_sio2_30_20_50", 1700.0, "W"): {
        "pressures": {
            "Fe": "0x1.34f72d2f92a77p-4", "FeO": "0x1.9e5d975ea14b2p-12",
            "Mg": "0x1.b95b50664d2fcp-10", "MgO": "0x1.d01051946f620p-23",
            "O": "0x1.ce7815c7a90bfp-15", "O2": "0x1.fe55e8c93b5b5p-17",
            "Si": "0x1.0367c393813d9p-35", "Si2": "0x1.1094e18c8633ap-74",
            "Si3": "0x1.ffb4abfc75258p-108", "SiO": "0x1.f3ff41ec070c5p-7",
            "SiO2": "0x1.3d88c64b6dc26p-16", "W2O6": "0x1.2a6008462421ep-5",
            "W3O8": "0x1.8aab02f1409e3p-9", "W3O9": "0x1.3052a1f2ec83ep-8",
            "W4O12": "0x1.77b6dc5b3deedp-16", "WO": "0x1.27aa89330bc6dp-25",
            "WO2": "0x1.4264781d0589cp-17", "WO3": "0x1.efd5e92980111p-13",
        },
        "total": "0x1.18fe5ed2dd6d6p-3", "pO2": "0x1.4e741a66e12fap-33",
        "residual": "0x1.94a802b4cd989p-49", "iterations": 21,
        "O": (("W2O6", "0x1.b3e3f853600c9p-24"), ("SiO", "0x1.8ad16629e2141p-26"), ("W3O9", "0x1.103f852119268p-26"), ("W3O8", "0x1.3d83a8658f233p-27"), ("FeO", "0x1.004e45a6c37f8p-31")),
        "metal": (("Fe", "0x1.b1875560cbe7fp-24"), ("SiO", "0x1.8ad16629e2141p-25"), ("Mg", "0x1.d55dc027f8d7bp-29"), ("FeO", "0x1.004e45a6c37f8p-31"), ("SiO2", "0x1.ad8d0d676f5d9p-35")),
        "flags": (("SiO", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Mg", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("MgO", "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009"), ("SiO2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si2", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"), ("Si3", "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038")),
        "flux": "0x1.af96ed8649dcep-1", "buffer": "0x1.673a0a0b49045p-30",
    },
}

_GAS_SOURCE_CLASSES = {
    "Ca": "janaf_fitted", "CaO": "janaf_fitted", "O": "janaf_fitted",
    "O2": "janaf_fitted", "Si": "janaf_fitted", "Si2": "janaf_fitted",
    "Si3": "janaf_fitted", "SiO": "janaf_fitted", "SiO2": "janaf_fitted",
    "Fe": "janaf_transcribed", "FeO": "janaf_transcribed",
    "Mg": "janaf_fitted", "MgO": "janaf_fitted",
}
_PROVENANCE_CLASSES = {
    "Al": "janaf_fitted", "Al2": "janaf_fitted", "Al2O": "janaf_fitted",
    "Al2O2": "janaf_fitted", "AlO": "janaf_fitted", "AlO2": "janaf_fitted",
    "Ca": "janaf_fitted", "CaO": "janaf_fitted", "Fe": "janaf_transcribed",
    "FeO": "janaf_transcribed", "K": "secondary_transcription_unverified_primary",
    "K2": "secondary_transcription_unverified_primary", "K2O": "secondary_transcription_unverified_primary",
    "KO": "secondary_transcription_unverified_primary", "Mg": "janaf_fitted",
    "MgO": "janaf_fitted", "Na": "janaf_fitted", "Na2": "janaf_fitted",
    "Na2O": "nasa_glenn_fitted", "NaO": "janaf_fitted", "O": "janaf_fitted",
    "O2": "janaf_fitted", "Si": "janaf_fitted", "Si2": "janaf_fitted",
    "Si3": "janaf_fitted", "SiO": "janaf_fitted", "SiO2": "janaf_fitted",
    "Ti": "janaf_fitted", "TiO": "janaf_fitted", "TiO2": "janaf_fitted",
}
_COMMON_LOW_FLAGS = "Al2O3(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Al-100"
_SILICA_LOW_FLAGS = "SiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-038"
_DOMAIN_FLAGS = {
    1200.0: {
        "Al": _COMMON_LOW_FLAGS,
        "Al2": "T=1200.0 K outside declared G(T) interval for 'Al2(g)' [1500, 3000] K; " + _COMMON_LOW_FLAGS,
        "Al2O": _COMMON_LOW_FLAGS, "Al2O2": _COMMON_LOW_FLAGS, "AlO": _COMMON_LOW_FLAGS,
        "AlO2": _COMMON_LOW_FLAGS,
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "Fe": None, "FeO": None, "K": None, "K2": None, "K2O": None, "KO": None,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "Na": None, "Na2": None, "Na2O": None, "NaO": None, "O": None, "O2": None,
        "Si": _SILICA_LOW_FLAGS,
        "Si2": "T=1200.0 K outside declared G(T) interval for 'Si2(g)' [1500, 3000] K; " + _SILICA_LOW_FLAGS,
        "Si3": "T=1200.0 K outside declared G(T) interval for 'Si3(g)' [1500, 3000] K; " + _SILICA_LOW_FLAGS,
        "SiO": _SILICA_LOW_FLAGS, "SiO2": _SILICA_LOW_FLAGS,
        "Ti": "T=1200.0 K outside declared G(T) interval for 'Ti(g)' [1500, 3000] K; TiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-044",
        "TiO": "T=1200.0 K outside declared G(T) interval for 'TiO(g)' [1500, 3000] K; TiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-044",
        "TiO2": "T=1200.0 K outside declared G(T) interval for 'TiO2(g)' [1500, 3000] K; TiO2(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF O-044",
    },
    1700.0: {
        "Al": _COMMON_LOW_FLAGS, "Al2": _COMMON_LOW_FLAGS, "Al2O": _COMMON_LOW_FLAGS,
        "Al2O2": _COMMON_LOW_FLAGS, "AlO": _COMMON_LOW_FLAGS, "AlO2": _COMMON_LOW_FLAGS,
        "Ca": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "CaO": "CaO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Ca-028",
        "Fe": None, "FeO": None, "K": None, "K2": None, "K2O": None, "KO": None,
        "Mg": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "MgO": "MgO(l) uses a labelled constant-Cp supercooled-liquid continuation from JANAF Mg-009",
        "Na": None, "Na2": None, "Na2O": None, "NaO": None, "O": None, "O2": None,
        "Si": _SILICA_LOW_FLAGS, "Si2": _SILICA_LOW_FLAGS, "Si3": _SILICA_LOW_FLAGS,
        "SiO": _SILICA_LOW_FLAGS, "SiO2": _SILICA_LOW_FLAGS,
        "Ti": None, "TiO": None, "TiO2": None,
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


def test_binary_pot_gas_paths_match_base_revision_hex_pins() -> None:
    openimcc = pytest.importorskip("openimcc", reason="openimcc is not importable")
    if not hasattr(openimcc, "oxygen_balance_from_pressure_model"):
        pytest.skip("installed openimcc lacks oxygen_balance_from_pressure_model")

    gas_data = files("openimcc") / "data" / "gas"
    gas_table = str((gas_data / "gas-shomate.csv").resolve())
    gas_provenance = {
        "gas_condensate_source": str((gas_data / "condensate.csv").resolve()),
        "gas_table_source": gas_table,
        "pack": "1.0.2",
        "pack_digest": "f2b479cd54e3c82704a5863fcc06836f72045375d9a8c7f8d2fad19e98f75d05",
        "package_version": "0.1.0.dev0",
    }

    pots = {pot.pot_id: pot for pot in load_binary_pots(DEFAULT_POTS_PATH)[0]}
    backend = _OpenImccBatteryBackend("openimcc")

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
            species: f"openimcc:{gas_table}:{_GAS_SOURCE_CLASSES[species]}"
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


def test_binary_pot_gas_pin_inputs_cover_both_paths_and_interval_flag() -> None:
    assert set(pot_id for pot_id, _, _ in _GOLDEN) == {
        "cao_sio2_40_60",
        "feo_mgo_sio2_30_20_50",
    }
    assert {temperature for _, temperature, _ in _GOLDEN} == {1200.0, 1700.0}
    assert {cell for _, _, cell in _GOLDEN} == {None, "W"}
    assert any(
        "outside declared G(T) interval" in reason
        for pin in _GOLDEN.values()
        for _, reason in pin["flags"]
    )
