"""OpenIMCC Sossi and Aithala decision pins. This file does not call MAGEMin.

The 18-row numeric digest is measured on the studio with the declared
OpenIMCC pin. The ungrounded log-as-bar digest c79971dc… is not this command.
Eligibility matches the internal-analytical table: no row is score_eligible,
and the one unknown-frame Sossi row carries DP-010.
"""

from __future__ import annotations

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

# Studio 2, OpenIMCC pin 66ec4a3, typed-pascal command. The ungrounded
# log-as-bar digest is a different command and is not this pin.
OPENIMCC_NUMERIC_DIGEST = (
    "31312b4844de6076a64b9a59ceb04a5bd443f6b3f0f61ee5a084bab5adb2cfc1"
)
# assumed_fo2 is the one Sossi DP-010 row. The other seventeen rows add none.
_OPENIMCC_BACKLOG = {
    "admission_defaulted": 18,
    "assumed_fo2": 1,
    "engine_model_caveat": 142,
    "fo2_frame_from_printed_atmosphere": 10,
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
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_10_xanes": "0750fcf07f413df6927a4b26dd25da77d614831b8526b28a06e0e99bd9a00d45",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_11_xanes": "1e0091c52f234529ff0b2df0294cc960f86188e9ab7be9dc1701cce6bd7ae854",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_12_xanes": "d844654f248b29cf469d3bcd8784015a8e36f98139c04928364226a633f0eb58",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes": "32f66f3b58d975eb4e4e9a085ab89832a361a8fdc4ada28e9f3f7d9f9ca9f993",
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
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1800_30_xanes": "92337772bdc6cb4d060dc56767ee7660429e19a7dee01e9ee320c82d1003203e",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_1900_30_xanes": "3e6b340fd5b39923f0301bbbf4509e82e6cb1ec6d9411ba643e5749a690d5c94",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_10_xanes": "b6871bd1bab185973d189db82ec213e030c90bffd06410074d10e85a632b1613",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_120_xanes": "d051bbf194cdc12530fcba06cdcb401c122dc7054b02115ac6374bfa4ed52538",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2000_30_xanes": "0812b6c11bfd3fb93d124472be8b7d0202bc60d17f58bf52e251ec4eebce84b4",
        "aithala-2026-magmatic-iron-redox-to-2100c::aithala_2026_2100_30_xanes": "a8edf9f963a4fc20e5112e6a6bd044c9c71b698d04cf57654bbacc9e48fa7ec2",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_10_xanes": "d196b0397f6b9bf5364e9b40e0f498b969cb980872bfe8cb44a584bf4b042a53",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_11_xanes": "e4afbc218f64fa0df41c5f54bba22ad939e3598105c3c74d9666a15c34ceb5c2",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_12_xanes": "9a0aeb78c814329bf8c0cdbe15ad29ccbcad2649a137f8078302f7bf8b3b2b3e",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes": "c1337f0c1e6e033dbbb7987cbeffd11b30e10b7ea42f90e124f78412a562837c",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_2_xanes": "7967435865ee518b106c846cd58780e59c28bfff4796ae4e5fd56d407f243acf",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_3_xanes": "7c16a76cff661e74617b04b89a71de11fe12fceef10bcd3541435eeecd584994",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_6_xanes": "0f0ebbc71cee4e050e37e08180918fee13e0a86ddeafdb9cd2a5e024b93b2eb9",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_7_xanes": "42176dd625a8c32b69384f238f86c398f1675bd5da405d3f0175870d15960dbe",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_8_xanes": "9d97fa8d4948d5a0dc618bba0a2f6a56375e7b14ce79a106de9124d1e7fc7d0e",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_9_xanes": "20124212f05ae3dee9fcab9711fa615b5387ad7e6d5732603c7e8bea7a0b2ce5",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts1_xanes": "0f832e334a825b92938deee19233d5287d4019485b32d53d770794f23cd7c24e",
        "sossi-2020-magma-ocean-redox::sossi_2020_per_ts2_xanes": "56323f31748cc9ccba8113559e45ea3f5e139a09b6853eecd1d7f2a9d08a67bd",
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
    assert unknown == [
        "sossi-2020-magma-ocean-redox::sossi_2020_per_f_o2_1_xanes"
    ]
    assert len(grounded) == 17

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
