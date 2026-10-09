"""Pre-refactor production pins for the ferric/ferrous feedstock work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator import feedstock_composition
from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.fe_redox import (
    feot_equivalent_wt_pct,
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
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1600.0),
        _melt_oxide_wt_pct=lambda: composition,
        _vacuum_floor_bar=lambda: DEFAULT_VACUUM_FLOOR_BAR,
    )
    return PyrolysisSimulator._compute_intrinsic_melt_fO2(
        sim, temperature_K=1873.15
    )


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
# The six lunar-family digests include the t1139 trace-oxide bridge.
_NORMALIZED_KG_PINS = {
    "lunar_mare_low_ti": "5354d9293e9c2928181053c2f6bcac4bf4f5997659a8e8ac33b9089a90ef7eac",
    "lunar_mare_high_ti": "d259c944ed17e25a1b9ab331c4613ae5efacdd3a40f5a078914ba4fcb080c26a",
    "lunar_highland": "d2cb29d3f32242ccd9842887455e776a71139bb6fe66d9b59836bb3ac5b771a2",
    "lunar_pkt_kreep_average": "28c1713258e2c65ed1fde9d625f5fbc691914d5edca1f50d73bfdfff156b9dce",
    "lunar_spa_kreep_influenced": "0646c9fb3d5e6938fccd5f1533f378473c4f9c8ef27bad6e8625eebe28798db4",
    "targeted_super_kreep_ore": "40a9df616e55df2a750d79d464c4c5656448a5b5452eda0f0bb320581d98f36d",
    "lunar_highlands_lhs1": "db1c4c641e574f77073a8e1518073fb3928128d80a49829ebb3e81e3d82c2705",
    "lunar_highlands_lhs1_yu_2025_reference": "9d6ba8b764273c7dfaa5845e6df9db2e1760a4594bc27a60d249f0e33eece71c",
    "lunar_mare_lms1": "483151e140a082e9b3d8ebac1fded19b7584f16d5bf66c7b103bf16473ccf8e8",
    "lunar_mare_oprl2n": "06dd786b3019ab8ec98d46c1f3a70634f50ea4eaea03111dbf2fddfffa6b31f0",
    "lunar_highlands_nuw_lht_5m": "923a9d4eacbf4b0dd8898166f4ba6d305bd711182ae9bee4b15842474ecc1011",
    "lunar_eac_1a": "3765d862e9720f4040ddcdc50366775294046fa93452e5f0cdaff1ec8a03c142",
    "lunar_mls_1a": "8c92b068847db22935bd80f7c4a16822d623f0c23152fd7f15f77c6f0be4daec",
    "s_type_asteroid_silicate": "1e536d93a85c508204d10fcf864920fb6df7dcd4ac3b35795193695a55b60260",
    "m_type_silicate_phase": "dc3dc10b8e076f17363cf69094195f1dc03bfe2a872fab3441157143e903c41d",
    "v_type_vesta_hed": "4f1533cf616b3aad50ef008e723a9b378208f946cf65be6820695351a0205f3d",
    "e_type_enstatite_aubrite": "bc291a77356054fe62539c4d08743c9d53210d3e26ddd791389e78d22c2194f5",
    "ci_carbonaceous_chondrite": "162edfc4ae819f7f883d7bf8ef5f5340cec7c1e2a82e64a4dddbf39d5c9f88ea",
    "cm_carbonaceous_chondrite": "bbb6db4f1cfb86b1ce41d9bd70c6522e071d257c233ee44cbeae216bd45653ef",
    "ceres_regolith": "76bc94843d3c2abfcf28a7f916733a0b362d953fde84434f39d2a3d9d000b3ce",
    "comet_nucleus": "b41195ed01eb7a9fdb7cf7e33927f1163fd59c7006acc3ec2a8272eec801ca9f",
    "mars_global_mgs1": "8b806270c59dc47e9b9e859ae694fc2a6872a4ef8462d1a52e44af8558a8c08b",
    "mars_basalt": "99a39805a6124ebbf701fe9c5cb0784e622cf822b379822a39529170369924dc",
    "mars_sulfate_rich": "d015b8bd08dfeda0e669c87fe5b9c6f83091df2d4c09b819bbc6d86cd87228ef",
    "mars_phyllosilicate_clay": "f1252a51b08f655d154e280721a5797aae202973fa1d189b90cb56cc977bcc82",
    "mars_perchlorate_rich": "4cabf3e7c75510236437b197b6140188924057188d13268d7ba5a4217c25a50e",
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
    # b03045041 evaluates the melt fO2 seed at the current temperature.
    assert _digest(outputs) == (
        "7a4a995e9f35b031b4331f14f0005c26602f323bd609e9d4969c360dbe58a8d5"
    )
