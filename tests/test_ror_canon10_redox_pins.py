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
