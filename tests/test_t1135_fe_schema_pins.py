"""Pre-refactor production pins for the ferric/ferrous feedstock work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator import feedstock_composition
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.fe_redox import (
    feot_equivalent_wt_pct,
    intrinsic_melt_fO2,
    kress91_split,
    melt_mol_fractions_for_kress91,
)


FEEDSTOCKS = yaml.safe_load(
    (Path(__file__).parents[1] / "data" / "feedstocks.yaml").read_text()
)
MASS_KG = 1000.0


def _normalize_core_mass_pin(masses: dict[str, float], target_mass_kg: float) -> None:
    normalize = getattr(PyrolysisSimulator, "_normalize_component_masses", None)
    if normalize is None:
        normalize = feedstock_composition.normalize_component_masses_kg
    normalize(masses, target_mass_kg)


def _intrinsic_fO2_pin(composition: dict[str, Any]) -> float:
    return intrinsic_melt_fO2(composition, 1873.15)


def _float_hex_tree(value: Any) -> Any:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {key: _float_hex_tree(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_float_hex_tree(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(
        _float_hex_tree(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# Captured by running the production normalizers on base 257a87a0bbd25ec8190443f47e3215f476c5d727.
_NORMALIZED_KG_PINS = {
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_mare_low_ti": "a2389f28ca1d9cf97f5623338791e167590a585d6958c591c7f611562780ff6c",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_mare_high_ti": "111def02f83c0c5049e02a07b8a82a413c491c2566544434c76219d6a9b7ba74",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_highland": "d7e6b3cada40aba6171d2274ec7f132702482e3a4feca9393a439255df181358",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_pkt_kreep_average": "f4cbb98e0ecf008e54c4c3ba88de30868f54995ec530703f3cc5766727b69704",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_spa_kreep_influenced": "e1c19a7ffc7c49cdb69450b091107f15816aba2a6b322954e96aeec3081023a1",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "targeted_super_kreep_ore": "e8d68db1dcf88b29631b68b4daa8b2ca830ad2694dc17f42246494693cbd041f",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_highlands_lhs1": "d1b38be2f71bb2ea3a293c26dfff76cb4ff9eb6a3f7724eb32f3836d95667660",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_highlands_lhs1_yu_2025_reference": "e217b10f17692a3145a416c0bc1e6445ed3d11222927727c90ee8ff2de3d5cc2",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_mare_lms1": "d21761ba5b083f45670fc675499bb525f4ca4f47520edfe879ee44cd79d9aaad",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_mare_oprl2n": "272a8659a2283373bb800a0f3f3a55d79824b8cb1c28f68ae15910daca9589e4",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_highlands_nuw_lht_5m": "f4ca17359e48251e492f039c50b7e6363dcabd1750e106f1d625019040cdd2ee",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_eac_1a": "98e1804496c776ff03291ec13ce388d6f930b7c17983c4427b36d41937287ed1",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "lunar_mls_1a": "e0486481a0d26d89a3a580b0605cdb7e73adab8412314f60c92935e314c14653",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "s_type_asteroid_silicate": "a2096c4b01482f3bf48e7ded086cc3cf893b4628c1569e8c35a00ec2fc2d1de8",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "m_type_silicate_phase": "966a20de2d8f959caf10152b585c03dba0cb87112451cf0d95cd568f2e3f91d5",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "v_type_vesta_hed": "ef8cbead9e2ce6405dfdaab1e53f89b8e723bfe6abdbc0b1b9787454d0bf18ef",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "e_type_enstatite_aubrite": "32b3f8acd8da34465c8e9fd129550f6abff3d239fd4b509e7e93bf0d88b342bf",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "ci_carbonaceous_chondrite": "1a0466cb2a702b1b57f458551c9fce6b844e7951aefb1fdb47f9f361cd9655bd",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "cm_carbonaceous_chondrite": "defb913476fa12eaedc10582c0062f37fcda790e15842a5729d7c5569bbbc8c1",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "ceres_regolith": "e1698a3d8bd4899f0c517dc702d782687280932dcafdcdd2b0a16581b0b56366",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "comet_nucleus": "fc231d2e98984c6654380b89f08f0fc9760eec57f69f0ade3b78054f06f9692a",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "mars_global_mgs1": "c4674910d7bb139e5b72ba5e246718625b937bb17fcb0523c08e484decf73399",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "mars_basalt": "c2712ca2b31c4ba1de4a0a8f6313608e17943b5ea26a1fec324dc0d22c08ffe8",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "mars_sulfate_rich": "8d2a06cf2d33eb796a0c45ef9e853b646b73f82febecc67d6b89a4244d1b6050",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "mars_phyllosilicate_clay": "2f6d1e99c30ae0c8a2696e65e8f6f78b59a693f898d443e449d9ebf44c1c0c00",
    # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
    "mars_perchlorate_rich": "b7f45907723d3a1b58542170e5ab97c7c95ba3b15c61eaac81c24ef30bf3f074",
}

_CORE_COMPOSITION_KG_PINS = {
    "lunar_mare_low_ti": "8564b6c17a8da2ab4b2144b3f60f8bc13c52f70eff093a320eff0b9a08c48b8e",
    "lunar_mare_high_ti": "5fb2357b11624c00baee20b149cbf4e766feaf8531c132d904cde8a7940acfc4",
    "lunar_highland": "aaf6a5a3de7b8eeafa0f5c4cdc7c26a0264608fb26c66338f2477cb3dbdcd42b",
    "lunar_pkt_kreep_average": "62fd7298b60c327df77387e0026988a7fb31eabebb22879791ff679bfa6ef146",
    "lunar_spa_kreep_influenced": "e6d74afc1d342b481c6dd60909c806a48db289ca65515ec85d0558ecefbf9776",
    "targeted_super_kreep_ore": "2829c35bd650d5240876cea9873b9317b1bb334e69f9c2883340fadedad94208",
    "lunar_highlands_lhs1": "db1c4c641e574f77073a8e1518073fb3928128d80a49829ebb3e81e3d82c2705",
    "lunar_highlands_lhs1_yu_2025_reference": "9d6ba8b764273c7dfaa5845e6df9db2e1760a4594bc27a60d249f0e33eece71c",
    "lunar_mare_lms1": "483151e140a082e9b3d8ebac1fded19b7584f16d5bf66c7b103bf16473ccf8e8",
    "lunar_mare_oprl2n": "06dd786b3019ab8ec98d46c1f3a70634f50ea4eaea03111dbf2fddfffa6b31f0",
    "lunar_highlands_nuw_lht_5m": "923a9d4eacbf4b0dd8898166f4ba6d305bd711182ae9bee4b15842474ecc1011",
    "lunar_eac_1a": "23b9baa8705f2e8e3ca632df1377b1f89b40b0edc7b8d57aebcf0fbcd07a3cbf",
    "lunar_mls_1a": "7429c290680eddee5bbd72a3891678a9bcca2f03d05cd5dc9c6d3ff5e0279e1b",
    "s_type_asteroid_silicate": "9617eaf6970018934ff7bc37e79a9fe56c7416c9a94e0001951407907828aae8",
    "m_type_silicate_phase": "dc3dc10b8e076f17363cf69094195f1dc03bfe2a872fab3441157143e903c41d",
    "v_type_vesta_hed": "4f1533cf616b3aad50ef008e723a9b378208f946cf65be6820695351a0205f3d",
    "e_type_enstatite_aubrite": "2315ab65da993a7151fcb018c76397a17fa576d796f57a00d0b34e13b9e427bd",
    "ci_carbonaceous_chondrite": "162edfc4ae819f7f883d7bf8ef5f5340cec7c1e2a82e64a4dddbf39d5c9f88ea",
    "cm_carbonaceous_chondrite": "bbb6db4f1cfb86b1ce41d9bd70c6522e071d257c233ee44cbeae216bd45653ef",
    "ceres_regolith": "76bc94843d3c2abfcf28a7f916733a0b362d953fde84434f39d2a3d9d000b3ce",
    "comet_nucleus": "b41195ed01eb7a9fdb7cf7e33927f1163fd59c7006acc3ec2a8272eec801ca9f",
    "mars_global_mgs1": "8b806270c59dc47e9b9e859ae694fc2a6872a4ef8462d1a52e44af8558a8c08b",
    "mars_basalt": "99a39805a6124ebbf701fe9c5cb0784e622cf822b379822a39529170369924dc",
    "mars_sulfate_rich": "d015b8bd08dfeda0e669c87fe5b9c6f83091df2d4c09b819bbc6d86cd87228ef",
    "mars_phyllosilicate_clay": "de2eebb899ceddf38fcf02d5ed0c139d2f60d42799ddfa1dd14a7b4e8bdf9a54",
    "mars_perchlorate_rich": "4cabf3e7c75510236437b197b6140188924057188d13268d7ba5a4217c25a50e",
}

_REDOX_PINS = {
    "lunar_mare_low_ti": "e570e7fee33c9d2b85ebaba2d42a101212081f204f67acd220475f942044666c",
    "lunar_mare_high_ti": "d2dfefd66eff3b976fa783311843a27348ad15e2cb02cb9a5e27c1400b63cc8d",
    "lunar_highland": "3cb53b95e9bda746ea34f51e1678dc5a042b5c56312b594edb9a91fbfbb85c48",
    "lunar_pkt_kreep_average": "27d9384fbbd4a1d550de1134e6f42d3f94e249d73346d1cc819edd31365fec12",
    "lunar_spa_kreep_influenced": "c06775b9fc5abc95993b72e4bdd83bb1c71ad297cd3d93cc6c5026307e619c81",
    "targeted_super_kreep_ore": "0f313112fbb3f5d8af2ac8e96117d2e5dd3db17b3b27df639509e23264b20a2f",
    "lunar_highlands_lhs1": "578bb7185c8047eaccba85e7dadf0d0c58ecd9151d98b8c60a3d353cdf0aec6d",
    "lunar_highlands_lhs1_yu_2025_reference": "8f918e78235b00fdf7ed341486db490b0020ca3835710f9f3d4f13c7b03a85b6",
    "lunar_mare_lms1": "c8a8cdb6fb9bd0e66d03de600214f95e606cdab589d17485b302bcae354c1adf",
    "lunar_mare_oprl2n": "b7355a721476f23efbafa7a44a8fb50813d93eb896d7f97383a9b9873c164b45",
    "lunar_highlands_nuw_lht_5m": "4bbd8392bf250827ac0a6ec9acac7f85147bcc26ba7f6c8d21161a71f841a1d8",
    "lunar_eac_1a": "a27b5eff0c85d179edb55da8c50a348e832794174c05b97382693ea5998755a5",
    "lunar_mls_1a": "859868647d7eb8a515f8e94eb6a6d1f199df40ef2f03c5bd90f8ff4b4889f028",
    "s_type_asteroid_silicate": "0f97bfb511ee9abc11be747b22c2fd2892ac64ebc42173e2e472cd0bfec56ecd",
    "m_type_silicate_phase": "a56012eedc1fbd303de200e8580e42e0afe9fdf9749d69c14b8077046f6a88b6",
    "v_type_vesta_hed": "b18b21ef40f409f2c28666a2c8bcb43542031993cb6b305593b5b9bb127e6c02",
    "e_type_enstatite_aubrite": "848f983dddd0fab2f7413e26cb70c2f12ca5ad0bcfde9a11ddbea6fe671f5ebf",
    "ci_carbonaceous_chondrite": "1b09e0b33a91819012bb80d4c5f99a7355ec2de151eeeed9b82cfc3b6780a9d0",
    "cm_carbonaceous_chondrite": "de7d7abd3e8189512e90f5392945918a34b0b111b4b11db50cca5a299b717858",
    "ceres_regolith": "0fce7e44f4b9d56ad2a1a664b3bd56d020ee911bbd9654f8f4c4276d52408b1c",
    "comet_nucleus": "61b799ee2c0a4c9b0dc4b961d83dac325fcc038b8cb643f86a520abcadfebd4f",
    "mars_global_mgs1": "d1397a94e3caf82eeaa96005d64aa052887d88efe8ecdcca68bb35463ecec86a",
    "mars_basalt": "fc332bd4870059b37c0621f620c46f84fb95de389bcd64271600e2ccecd3f6fd",
    "mars_sulfate_rich": "826b20274cc4a84b711836c118b465bc4e03b3c56786a101a3f832e45322c469",
    "mars_phyllosilicate_clay": "68daf73dbe354906c4e31ba1da42820131f7e6aff189e6b87e7454fae4d01a79",
    "mars_perchlorate_rich": "76f03a1f7eeb551ed19ba6e8462ad2abf0d07deb715077922113e0b581b47610",
}


def _iron_oxide_entries() -> dict[str, dict[str, Any]]:
    return {
        key: entry
        for key, entry in FEEDSTOCKS.items()
        if {"FeO", "Fe2O3"} & set(entry.get("composition_wt_pct", {}) or {})
    }


def test_base_feedstock_kg_normalization_is_pinned_for_every_fe_oxide_entry() -> None:
    entries = _iron_oxide_entries()
    assert set(entries) == set(_NORMALIZED_KG_PINS)
    for key, entry in entries.items():
        masses = normalized_feedstock_component_masses_kg(entry, MASS_KG)
        assert _digest(masses) == _NORMALIZED_KG_PINS[key], key
        assert sum(masses.values()) == pytest.approx(MASS_KG, abs=1e-10), key


def test_base_core_composition_normalization_is_pinned_for_every_fe_oxide_entry() -> None:
    entries = _iron_oxide_entries()
    assert set(entries) == set(_CORE_COMPOSITION_KG_PINS)
    for key, entry in entries.items():
        masses = PyrolysisSimulator._component_masses_from_wt_pct(
            entry.get("composition_wt_pct", {}) or {}, MASS_KG
        )
        _normalize_core_mass_pin(masses, MASS_KG)
        assert _digest(masses) == _CORE_COMPOSITION_KG_PINS[key], key


def test_base_feot_and_kress_outputs_are_pinned_for_every_fe_oxide_entry() -> None:
    entries = _iron_oxide_entries()
    assert set(entries) == set(_REDOX_PINS)
    for key, entry in entries.items():
        composition = entry.get("composition_wt_pct", {}) or {}
        fractions = melt_mol_fractions_for_kress91(composition)
        split = kress91_split(
            fO2_log=-7.0,
            mol_fractions=fractions,
            T_K=1473.15,
            pressure_bar=1.0,
        )
        assert _digest(
            {
                "feot": feot_equivalent_wt_pct(composition),
                "mol_fractions": fractions,
                "split": split,
            }
        ) == _REDOX_PINS[key], key
def test_base_intrinsic_fO2_is_pinned_for_every_fe_oxide_entry() -> None:
    entries = _iron_oxide_entries()
    outputs = {
        key: _intrinsic_fO2_pin(entry.get("composition_wt_pct", {}) or {})
        for key, entry in entries.items()
    }

    assert len(outputs) == 26
    assert _digest(outputs) == (
        # d-156 (owner 2026-10-16): b-899 one-redox-state rebaseline, measured at b79c826d8
        "8f2a1bd922788067624c01c30e087debf7c394ccfe5314d2d35483f991ba1f12"
    )
