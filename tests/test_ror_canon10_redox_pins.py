"""OpenIMCC Sossi and Aithala decision pins. This file does not call MAGEMin.

The 18-row numeric digest is measured on the studio with the declared
OpenIMCC pin. It matches the historical log-as-bar float set because the
pure-O2 log is 0. The payload hash is the printed frame, not that command.
Eligibility matches the internal-analytical table: no row is score_eligible.
The pure-O2 end member is the printed 1 bar frame, so no row carries the
unknown-scale DP-010.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

import pytest

from simulator.battery.enums import Engine, NoticeKind, Rail
from simulator.battery import score as score_module
from simulator.battery.score import (
    notice_backlog,
    notice_backlog_payloads,
    residual_to_plain,
    score_store,
)
from tests.battery.test_redox_extract_scoring import (
    _scale_unknown_oxygen,
    _sossi_aithala_redox_context,
)

# Regenerated on Studio 2 with OpenIMCC pin 66ec4a3 from the merged scorer.
# Log 0 at 1 bar makes this float digest equal the historical log-as-bar float
# set. The payload hash below is the printed-atmosphere frame, not that command.
OPENIMCC_NUMERIC_DIGEST = (
    "c79971dce81ac423bf34a04525b3da956a437f83e6dbe1fa601d24c1627b8f44"
)
# Recounted after the pure-O2 end member joined the printed-atmosphere frame.
# assumed_fo2 is zero on this set. The unknown-scale control lives in the
# redox scoring file, not on a real row.
_OPENIMCC_BACKLOG = {
    "admission_defaulted": 18,
    "engine_model_caveat": 142,
    "fo2_frame_from_printed_atmosphere": 11,
    "input_omitted": 19,
    "total_iron_reported_as_feo": 18,
}

# SHA-256 of each complete residual_to_plain payload, keyed by source
# reference. These bind per-row provenance, notice text/origin and flags.
_FULL_PAYLOAD_PINS = {
    Engine.INTERNAL_ANALYTICAL: {
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes": "386367e1c426c7ffa6f28132e6b58a0ed5c296e87ce2cccedb467c41bbd0ce32",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1900_30_xanes": "58f223e4bb09871e734ea7d5c6068b609d793a25552da6e5f9aa0fe5742abba4",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_10_xanes": "e68f2108d8998d26a16635d7bbf66f9b7e8080d197366654fea0cf361f0318c7",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_120_xanes": "5bbd9d5eb1c8b3edcaa734888e7910e649a4b782702f8dffc3ee302a9f90215a",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_30_xanes": "cff37f189f885060ffdc2ebf13a78b2c25f789b065562fd19d16fc1f45ecd291",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2100_30_xanes": "eb1e195c143036653c7f0485b1002d731bb2eed32f2a6c9b9af4013d96cf5fe8",
        # Receipt 5abafffc matched this payload hash. a95942989e is already
        # an ancestor of f560b42a41, which pinned the previous hash
        # 0750fcf07f41..., so that merge is not the mover. The Sossi
        # extract regenerated at 397cdd443f attaches method_unstated
        # (DP-106): the printed method "Fe K-edge XANES" is not a mapped
        # method token. The hash follows that notice. The other Sossi
        # rows moved the same way and stay unpinned.
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_10_xanes": "97bdbeaa6cbf73ba2cdd6c52e5c2a3d82fa12383e4a2f7047aab0f1b0fe8dd6a",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_11_xanes": "5cbd5a7c95425a27d1665d3408dfaffc24a15ce292f83e6111602a56122e7c00",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_12_xanes": "190cedb86b480e71c0074befcd78589dd72fa3da4b04794ecc6d932c487831a4",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes": "2ae11f8d2d6f8f33087d86c1d45b87c8ecd6f75ca3f7254e0b535a6eed8e790f",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_2_xanes": "327f2135a5ca8b1c15146a22204329cf24ff2d223b8d66c3f276885719519dfb",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_3_xanes": "ed8561974a1e77f228699f31fb9c71ede6c19f5e8afb9f6282888f1c5425d210",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_6_xanes": "b0c0ba8c619e5ebbe64356abe1e9e3a6bfb4228704178779aaeae35d89f6ab9e",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_7_xanes": "92bb1126d9143fc494d6ff983fde87ec456102733f431751956511c71f925b4d",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_8_xanes": "d0afa8c829f7f53c5ca0018acd2a6e3a1a37d074e7c1468302d1d40542fc6190",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_9_xanes": "21021f7019420ea03353233e12d78af9584d2dd792cdff1a00c6784c53bac716",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts1_xanes": "eeafdd909e25e1b276c30a85e8e4e68dbd62e5cf4aa3d84a32cf753c7bcee9c8",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts2_xanes": "615a21efe0548b28e3b7ad879a02d289e00b45f84634d55d2152ebb69e34706c",
    },
    Engine.OPENIMCC: {
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes": "d697d4972715490fb4e56187041c1c2c5deeeea4804e5bab400fec36359688fb",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1900_30_xanes": "9984b244439737ceffc64f908d5953f19e47c39469529e31261f471a8c9fa64e",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_10_xanes": "5ef363f7a75015a7bf4c4bad2bf76426bc2c42fb5d0ff67862c17867429f2cc3",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_120_xanes": "d041168a30a9bd2bdac046aee542a66d6123a9a781ce272e8380564b17c861fd",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_30_xanes": "08de328245b0e82404e44e98b00506d62244f0a6475b0b8d49f2d9a856ed2896",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2100_30_xanes": "5ffed3e52fa9e9dcad06d73f93d66e2c561ead694d7804fb42832fbcf5dea86f",
        # Same receipt and same store regen as the analytical row.
        # OpenIMCC's 18-row numeric digest is still
        # c79971dce81ac423bf34a04525b3da956a437f83e6dbe1fa601d24c1627b8f44,
        # so this arm's float set did not move. The payload hash did,
        # because residual_to_plain includes the new method notice.
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_10_xanes": "95e36cecb79fab0d769fe3bbc07833b71e623cea273844a0b466b0ea41bd0a2a",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_11_xanes": "3a4c4d7d0c3c706e800eb9bbd3b2b608714f6a6a15692fcb6e25258234301f0c",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_12_xanes": "99d9c9ac67a7ca800c33c2a9929ca3fd37b6ed0dbedb1425243a4dcf50cee062",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes": "cfb2a65bbf6ae9f59b0c969f65ef775f03ad46065a9d705e86cbc881cb0ee0b0",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_2_xanes": "7ce80d4f0bc7725d4a28af14686f91c3ee60a303006a5d4265126e72a4a54a86",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_3_xanes": "8263367ea4a8c230d2a18ca3ad78373f246f67f71a2949697a3f6196ce4110bd",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_6_xanes": "31c70ab2d6dda8ecb9e38944b602b7b2e8104285086772c46655d90384b2a704",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_7_xanes": "f42936739e98bcefb2c43d87cbc3308d85260c278bfa51c3d56ccacfef6c4bfc",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_8_xanes": "0cab8011bdd5fe371f0d4fe47a529efc768f3291272931d5e65767e5115849c5",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_9_xanes": "77c5e30b1323ad2b8de414d58a93d300e7e453c3579b47a0290d866000bd1e77",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts1_xanes": "d37d70306a7640bbb08c60aff816dab518f2bdcbc5efa9ec40c5e8959e5289f0",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts2_xanes": "6adadc852a927a4ddc90bc4f307e93b679065c077c22b145cfca26b45a7fc399",
    },
}


def _serialized_payload_hashes(residuals):
    return {
        residual.reference: hashlib.sha256(
            json.dumps(
                score_module.residual_to_plain(residual),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for residual in residuals
    }


def _assert_full_payload_pin(engine, residuals):
    observed = _serialized_payload_hashes(residuals)
    expected = _FULL_PAYLOAD_PINS[engine]
    assert set(observed) == set(expected)
    for reference, digest in expected.items():
        assert observed[reference] == digest, reference


def test_openimcc_sossi_aithala_rows_share_one_ineligible_decision():
    pytest.importorskip("openimcc")
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    context, observations = _sossi_aithala_redox_context()
    residuals, _metadata = score_store(
        context, engines=(Engine.OPENIMCC,), rail=Rail.REDOX
    )
    values = [residual for residual in residuals if residual.numeric is not None]
    assert len(values) == 18
    assert {residual.reference for residual in values} == set(observations)
    _assert_full_payload_pin(Engine.OPENIMCC, values)

    unknown = []
    grounded = []
    for residual in values:
        observation = context.observations[residual.reference]
        policies = tuple(
            notice.policy.id
            for notice in residual.notices
            if notice.policy is not None
        )
        if _scale_unknown_oxygen(observation):
            unknown.append(residual.reference)
            assert "DP-010" in policies
            assert any(
                notice.policy is not None
                and notice.policy.id == "DP-010"
                and _BARE_LOG_SCALE_UNKNOWN in notice.reason
                and "printed_fO2_log=" in notice.reason
                for notice in residual.notices
            )
            assert "not_flagged_stratum" in residual.exclusions
        else:
            grounded.append(residual.reference)
            assert "DP-010" not in policies
        assert residual.score_eligible is False
    assert unknown == []
    assert len(grounded) == 18

    encoded = json.dumps(
        [float(residual.numeric.value).hex() for residual in values],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    live = notice_backlog(values)
    decoded = notice_backlog_payloads(
        [residual_to_plain(residual) for residual in values]
    )
    assert digest == OPENIMCC_NUMERIC_DIGEST
    assert live.get(NoticeKind.SOURCE_DISAGREEMENT.value, 0) == 0
    assert decoded.get(NoticeKind.SOURCE_DISAGREEMENT.value, 0) == 0
    assert live == _OPENIMCC_BACKLOG
    assert decoded == _OPENIMCC_BACKLOG


def test_internal_analytical_full_payload_pin():
    context, observations = _sossi_aithala_redox_context()
    residuals, _metadata = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), rail=Rail.REDOX
    )
    assert {residual.reference for residual in residuals} == set(observations)
    _assert_full_payload_pin(Engine.INTERNAL_ANALYTICAL, residuals)


@pytest.mark.parametrize(
    "engine",
    (Engine.INTERNAL_ANALYTICAL, Engine.OPENIMCC),
    ids=lambda value: value.value,
)
def test_sossi_xanes_payload_changes_only_by_the_method_notice(engine, monkeypatch):
    if engine is Engine.OPENIMCC:
        pytest.importorskip("openimcc")
    import simulator.battery.migrate as migrate

    printed_method = "Fe K-edge XANES"
    context, observations = _sossi_aithala_redox_context()
    with_alias, _metadata = score_store(
        context, engines=(engine,), rail=Rail.REDOX
    )
    with_alias_payloads = {
        residual.reference: score_module.residual_to_plain(residual)
        for residual in with_alias
    }
    aliases_without_sossi_xanes = {
        alias: token
        for alias, token in migrate.METHOD_TOKEN_ALIASES.items()
        if alias != printed_method
    }
    monkeypatch.setattr(
        migrate, "METHOD_TOKEN_ALIASES", aliases_without_sossi_xanes
    )
    context_without_alias, observations_without_alias = _sossi_aithala_redox_context()
    without_alias, _metadata = score_store(
        context_without_alias, engines=(engine,), rail=Rail.REDOX
    )
    without_alias_payloads = {
        residual.reference: score_module.residual_to_plain(residual)
        for residual in without_alias
    }
    assert set(with_alias_payloads) == set(without_alias_payloads)
    sossi_references = {
        reference
        for reference, observation in observations.items()
        if observation.source_id == "sossi-2020-magma-ocean-redox"
    }
    assert len(sossi_references) == 12
    assert sossi_references == {
        reference
        for reference, observation in observations_without_alias.items()
        if observation.source_id == "sossi-2020-magma-ocean-redox"
    }
    for reference in sossi_references:
        before = with_alias_payloads[reference]
        after = without_alias_payloads[reference]
        assert not any(
            notice["kind"] == NoticeKind.METHOD_UNSTATED.value
            for notice in before["notices"]
        )
        method_notices = [
            notice
            for notice in after["notices"]
            if notice["kind"] == NoticeKind.METHOD_UNSTATED.value
        ]
        assert len(method_notices) == 1
        assert method_notices[0]["policy"]["id"] == "DP-106"
        assert method_notices[0]["origin"] == reference
        assert (
            f"source method text is not mapped: {printed_method}"
            in method_notices[0]["reason"]
        )
        without_method_notice = {
            **after,
            "notices": [
                notice
                for notice in after["notices"]
                if notice["kind"] != NoticeKind.METHOD_UNSTATED.value
            ],
        }
        assert without_method_notice == before


@pytest.mark.parametrize(
    "engine", (Engine.INTERNAL_ANALYTICAL, Engine.OPENIMCC), ids=lambda value: value.value
)
def test_full_payload_pin_detects_notice_origin_moved_between_rows(engine, monkeypatch):
    if engine is Engine.OPENIMCC:
        pytest.importorskip("openimcc")
    context, observations = _sossi_aithala_redox_context()
    residuals, _metadata = score_store(context, engines=(engine,), rail=Rail.REDOX)
    assert {residual.reference for residual in residuals} == set(observations)

    target = "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_2_xanes"
    original = score_module.residual_to_plain

    def moved_notice_origin(residual):
        payload = original(residual)
        if residual.reference == target:
            assert payload["notices"]
            payload["notices"][0]["origin"] = (
                "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_3_xanes"
            )
        return payload

    monkeypatch.setattr(score_module, "residual_to_plain", moved_notice_origin)
    with pytest.raises(AssertionError, match=target):
        _assert_full_payload_pin(engine, residuals)


def test_ratio_only_forward_returns_sossi_pure_oxygen_to_the_unknown_scale(
    monkeypatch,
):
    """Dropping the categorical label restores the old unknown-scale row.

    The migrator is the consumer. The other seventeen oxygen identities
    stay on the ratio or bare-log paths.
    """

    import simulator.battery.migrate as migrate
    from simulator.battery.migrate import _BARE_LOG_SCALE_UNKNOWN

    reference = "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    _context, before = _sossi_aithala_redox_context()
    assert before[reference].identity.fO2_Pa.value == Decimal("100000")
    original = migrate._quoted_atmosphere_values

    def ratios_only(container, locator):
        return tuple(
            value
            for value in original(container, locator)
            if ":" in value or "c.c.s/min" in value or "SCCM" in value
        )

    monkeypatch.setattr(migrate, "_quoted_atmosphere_values", ratios_only)
    _context, after = _sossi_aithala_redox_context()
    restored = after[reference].identity.fO2_Pa
    assert restored.is_unknown
    assert restored.reason == _BARE_LOG_SCALE_UNKNOWN
    with pytest.raises(AssertionError):
        assert restored.value == Decimal("100000")
    for key, observation in after.items():
        if key == reference:
            continue
        assert observation.identity.fO2_Pa == before[key].identity.fO2_Pa
