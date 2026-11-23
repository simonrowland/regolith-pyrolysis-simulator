from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.default_policy import append_proposal, load_policy_table, resolve
from simulator.battery.enums import (
    DEFAULT_POLICY_NOTICE_KINDS,
    Engine,
    NoticeKind,
    Quantity,
    ResidualStatus,
)
from simulator.battery.records import Notice, NoticePolicy
from simulator.battery.migrate import to_plain
from simulator.battery.records import union_notices
from simulator.battery.score import (
    _headline_payload_admits,
    _flagged_payload_strata,
    flagged_strata,
    flagged_stratum_payloads,
    flagged_stratum_rows,
    headline_payloads,
)
from simulator.state import MeltState
from tests.battery import factories as F


EXPECTED_POLICY_FLAGS = {
    "assumed_pressure",
    "method_implied",
    "effusion_regime_assumed",
    "effusion_regime_violated",
    "alpha_assumed",
    "knob_defaulted",
    "assumed_fo2",
    "method_assumed",
    "kems_calibration_by_source",
    "basis_converted",
    "interval_observation",
    "phase_inferred",
    "composition_from_run_siblings",
    "composition_from_analysed_phase",
}


def test_every_entry_loads_with_closed_flags_and_trace_fields() -> None:
    policies = load_policy_table()
    assert load_policy_table() is policies
    assert set(policies) == {
        *(f"DP-{number:03d}" for number in range(1, 20)),
        "DP-109",
        "DP-101",
    }

    required = {
        "id",
        "condition",
        "units",
        "physical_basis",
        "bias",
        "flag",
        "ruling",
        "version",
        "status",
        "proposed_by",
        "proposed_at",
        "evidence",
        "canonized_by",
        "canonized_at",
        "history",
    }
    closed_flags = {kind.value for kind in DEFAULT_POLICY_NOTICE_KINDS}
    assert closed_flags == EXPECTED_POLICY_FLAGS
    seen_flags: set[str] = set()
    for policy_id, entry in policies.items():
        assert required <= entry.keys()
        assert entry["id"] == policy_id
        assert entry["version"] == 1
        assert isinstance(entry["history"], list)
        if policy_id == "DP-101":
            assert entry["status"] == "rejected"
            assert entry["proposed_by"] == "w1122cvpsat"
            assert entry["rejection_reason"] == (
                "silent vacuum fallback applied regardless of method; "
                "superseded by DP-001/DP-019 by method"
            )
            assert entry["history"]
        elif policy_id == "DP-109":
            assert entry["status"] == "proposed"
            assert entry["proposed_by"] == "w1122cvga"
            assert entry["units"] == "retention rule"
            assert entry["evidence"]["rows"][0]["observation_id"] == (
                "stolyarova_1996_table2_pSiO_1933k"
            )
        else:
            assert entry["status"] == "canonical"
            assert entry["canonized_by"] in {"owner", "controller"}
            assert entry["canonized_at"] == "2026-11-23"
        flags = entry["flag"] if isinstance(entry["flag"], list) else [entry["flag"]]
        assert flags
        for flag in flags:
            assert flag in closed_flags
            assert NoticeKind(flag).value == flag
            seen_flags.add(flag)
    assert seen_flags == closed_flags

def test_canonical_policy_values() -> None:
    assert resolve("DP-001").value == Decimal("101325")
    assert resolve("DP-001").units == "Pa"
    assert resolve("DP-003", P_total=Decimal("100000")).value == Decimal("20950")
    assert resolve("DP-005").value == Decimal("1")
    assert resolve("DP-006").value == Decimal("1")
    assert resolve("DP-015").value == Decimal("298.15")
    assert resolve("DP-015").units == "K"
    policies = load_policy_table()
    assert policies["DP-002"]["ruling"] == "d-460, gap manual"
    assert policies["DP-003"]["ruling"] == "gap manual"


def test_resolved_policy_payload_carries_registry_basis_and_bias() -> None:
    entry = load_policy_table()["DP-005"]
    resolved = resolve("DP-005")
    assert resolved.payload["physical_basis"] == entry["physical_basis"]
    assert resolved.payload["bias"] == entry["bias"]


def test_kems_method_policies_resolve_owner_results() -> None:
    regime = resolve("DP-004")
    assert regime.value == (
        "molecular effusion inside the cell; vacuum outside; "
        "P_total(cell) = Σ measured partials. Never 1 atm"
    )
    assert regime.units == "Pa"
    assert regime.payload["formula_owner"] == (
        "simulator.battery.score._sum_gas_partial_pressures_pa"
    )
    assert NoticePolicy(**regime.payload["policy"]).value == regime.value

    method = resolve(
        "DP-011", inferred_method="equilibrium_partial_pressure"
    )
    assert method.value == "equilibrium_partial_pressure"
    assert method.flag is NoticeKind.METHOD_ASSUMED
    assert NoticePolicy(**method.payload["policy"]).value == method.value

    conditions = resolve(
        "DP-018",
        method_condition="molecular effusion regime inside the cell; vacuum outside",
    )
    assert conditions.value == (
        "molecular effusion regime inside the cell; vacuum outside"
    )
    assert conditions.flag is NoticeKind.METHOD_IMPLIED
    assert NoticePolicy(**conditions.payload["policy"]).value == conditions.value

    with pytest.raises(ValueError, match="DP-011 requires an inferred method"):
        resolve("DP-011")
    with pytest.raises(ValueError, match="DP-018 requires the method-implied condition"):
        resolve("DP-018")


def test_numeric_policy_payloads_construct_notice_policy_with_normalized_values() -> None:
    expected = {
        "DP-001": ("101325", Decimal("101325")),
        "DP-005": ("1", Decimal("1")),
        "DP-006": ("1", Decimal("1")),
        "DP-015": ("298.15", Decimal("298.15")),
        "DP-019": ("0.1", Decimal("0.1")),
    }
    for policy_id, (expected_text, expected_value) in expected.items():
        resolved = resolve(policy_id)
        policy = NoticePolicy(**resolved.payload["policy"])
        assert isinstance(resolved.value, Decimal)
        assert resolved.value == expected_value
        assert policy.value == expected_text


def test_method_implied_notice_requires_policy_metadata() -> None:
    with pytest.raises(ValueError, match="method_implied requires policy metadata"):
        Notice(
            kind=NoticeKind.METHOD_IMPLIED,
            affected_quantities=(Quantity.P_SAT,),
            reason="the method fixes the missing condition",
            origin="test",
        )


def test_runtime_owned_apparatus_defaults_are_read_from_the_runtime() -> None:
    runtime_area = MeltState().melt_surface_area_m2

    assert resolve("DP-007").value == Decimal(str(runtime_area))
    assert resolve("DP-008", runtime_default=runtime_area).value == runtime_area


def test_vacuum_pressure_uses_the_runtime_owner_constant() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        DP_019_VACUUM_BOUNDARY_BAR,
    )
    from simulator.physical_constants import PA_PER_BAR

    assert resolve("DP-019").value == Decimal(
        str(DP_019_VACUUM_BOUNDARY_BAR)
    ) * Decimal(str(PA_PER_BAR))
    assert resolve("DP-019").value == Decimal("0.1")
    assert resolve("DP-019").units == "Pa"
    assert resolve("DP-019").flag is NoticeKind.METHOD_IMPLIED
    assert resolve("DP-019").payload["policy_status"] == "canonical"


def test_unrecognized_policy_id_raises() -> None:
    with pytest.raises(KeyError, match="DP-999"):
        resolve("DP-999")


def test_rejected_policy_names_its_replacements() -> None:
    with pytest.raises(
        ValueError,
        match=r"DP-101 is rejected: .*DP-001 or DP-019",
    ):
        resolve("DP-101")


def test_contextual_formula_does_not_return_its_text_as_a_value() -> None:
    with pytest.raises(ValueError, match="owning formula result"):
        resolve("DP-010")


def test_formula_results_cannot_be_overridden_by_a_value_context() -> None:
    cases = (
        (
            "DP-013",
            {
                "value": Decimal("12"),
                "flag": "basis_converted",
                "transform_id": "mol_percent_to_fraction",
            },
        ),
        (
            "DP-017",
            {"value": "fixture composition", "flag": "composition_from_run_siblings"},
        ),
    )
    for policy_id, context in cases:
        with pytest.raises(ValueError, match="owning formula result"):
            resolve(policy_id, **context)


def test_flagged_censuses_sort_rows_with_missing_policy_ids() -> None:
    untyped_notice = Notice(
        kind=NoticeKind.ASSUMED_FO2,
        affected_quantities=(Quantity.P_SAT,),
        reason="intrinsic melt fO2 was used",
        origin="test",
    )
    typed_notice = replace(
        untyped_notice,
        policy=NoticePolicy(
            id="DP-010",
            version=1,
            status="canonical",
            value="-9",
            units="log10(bar)",
        ),
    )
    no_policy = F.residual(
        "no-policy",
        "no-policy",
        status=ResidualStatus.MATCH,
        notices=(untyped_notice,),
    )
    with_policy = F.residual(
        "with-policy",
        "with-policy",
        status=ResidualStatus.MATCH,
        notices=(typed_notice,),
    )

    live_rows = flagged_stratum_rows(
        (with_policy, no_policy), engines=(Engine.NASA_CEA_9,)
    )
    assert [row.get("policy_id") for row in live_rows] == [None, "DP-010"]

    payload_rows = [
        {
            "rail": "thermochemistry",
            "candidate_request": {"engine": Engine.NASA_CEA_9.value},
            "numeric": {"operation": "dex", "value": "0.25"},
            "notices": [to_plain(notice)],
        }
        for notice in (untyped_notice, typed_notice)
    ]
    summarized_rows = flagged_stratum_payloads(
        payload_rows, (Engine.NASA_CEA_9,)
    )
    assert [row.get("policy_id") for row in summarized_rows] == [None, "DP-010"]


def test_proposal_resolves_as_proposed_and_stays_out_of_headline(tmp_path, monkeypatch) -> None:
    import simulator.battery.default_policy as policy_module

    policy_path = tmp_path / "default_policy.yaml"
    policy_path.write_text("schema_version: 1\npolicies: []\n", encoding="utf-8")
    monkeypatch.setattr(policy_module, "POLICY_PATH", policy_path)

    policy_id = append_proposal(
        {
            "id": "DP-109",
            "condition": "test-only missing apparatus knob",
            "value": "runtime supplied",
            "units": "knob-specific",
            "physical_basis": "test fixture only",
            "bias": "test fixture only",
            "flag": "knob_defaulted",
        },
        proposed_by="test-dispatch",
        proposed_at="2026-11-23T00:00:00+00:00",
        evidence=["fixture row and source"],
    )
    assert policy_id == "DP-109"
    resolved = resolve(policy_id)
    assert resolved.payload["policy_status"] == "proposed"
    assert resolved.payload["policy_id"] == policy_id
    assert resolved.payload["proposed_by"] == "test-dispatch"
    assert resolved.payload["proposed_at"] == "2026-11-23T00:00:00+00:00"
    assert resolved.payload["evidence"] == ["fixture row and source"]

    notice_policy = resolved.payload["policy"]
    typed_policy = NoticePolicy(**notice_policy)
    notice = Notice(
        kind=resolved.flag,
        affected_quantities=(Quantity.P_SAT,),
        reason="the fixture's original missing-knob reason",
        origin="fixture-observation",
        policy=typed_policy,
    )
    assert flagged_strata((notice,)) == (
        "knob_defaulted",
        "proposed-policy",
    )
    assert to_plain(notice)["policy"] == notice_policy
    assert len(
        union_notices(
            (notice,),
            (replace(notice, policy=replace(typed_policy, value="different")),),
        )
    ) == 2
    row = {
        "reference": "fixture-observation",
        "rail": "vapour",
        "candidate_request": {"engine": Engine.INTERNAL_ANALYTICAL.value},
        "status": "no_band",
        "numeric": {"operation": "dex", "value": "0.25"},
        "exclusions": [],
        "notices": [to_plain(notice)],
    }

    headlines = headline_payloads([row], [Engine.INTERNAL_ANALYTICAL])
    assert not any(int(headline["n_candidates"]) for headline in headlines)
    assert _flagged_payload_strata(row) == (
        "knob_defaulted",
        "proposed-policy",
    )
    assert not _headline_payload_admits(row, tier="measured")
    flagged = flagged_stratum_payloads([row], [Engine.INTERNAL_ANALYTICAL])
    assert [item["stratum"] for item in flagged] == [
        "knob_defaulted",
        "proposed-policy",
    ]
    assert all(item["policy_id"] == policy_id for item in flagged)
    assert all(item["policy_status"] == "proposed" for item in flagged)

    canonical_policy = {**notice_policy, "status": "canonical"}
    canonical_row = {
        **row,
        "reference": "canonical-fixture-observation",
        "notices": [
            {**row["notices"][0], "policy": canonical_policy}
        ],
    }
    assert _flagged_payload_strata(canonical_row) == ("knob_defaulted",)
    assert not _headline_payload_admits(canonical_row, tier="measured")
    rejected_row = {
        **canonical_row,
        "reference": "rejected-fixture-observation",
        "notices": [
            {
                **canonical_row["notices"][0],
                "policy": {**notice_policy, "status": "rejected"},
            }
        ],
    }
    status_groups = flagged_stratum_payloads(
        [canonical_row, rejected_row], [Engine.INTERNAL_ANALYTICAL]
    )
    assert [(item["policy_id"], item["policy_status"], item["n"]) for item in status_groups] == [
        (policy_id, "canonical", 1),
        (policy_id, "rejected", 1),
    ]

    # A policy-bearing notice cannot silently lose its typed flag.
    with pytest.raises(ValueError, match="policy notice kind"):
        Notice(
            kind=NoticeKind.ENGINE_MODEL_CAVEAT,
            affected_quantities=(Quantity.P_SAT,),
            reason="the fixture's original missing-knob reason",
            origin="fixture-observation",
            policy=typed_policy,
        )

    # Mutation evidence: removing the table flag from a serialized, defaulted
    # row must fail closed instead of admitting it into the clean headline.
    flagless_row = {
        **row,
        "notices": [
            {**row["notices"][0], "kind": NoticeKind.ENGINE_MODEL_CAVEAT.value}
        ],
    }
    with pytest.raises(ValueError, match="must name its default-policy flag"):
        _headline_payload_admits(flagless_row, tier="measured")
    with pytest.raises(ValueError, match="must name its default-policy flag"):
        flagged_stratum_payloads([flagless_row], [Engine.INTERNAL_ANALYTICAL])


def test_proposals_require_allocated_ids_and_refuse_duplicate_ids(
    tmp_path, monkeypatch
) -> None:
    import simulator.battery.default_policy as policy_module

    policy_path = tmp_path / "default_policy.yaml"
    policy_path.write_text(
        "schema_version: 1\npolicies:\n  - id: DP-101\n", encoding="utf-8"
    )
    monkeypatch.setattr(policy_module, "POLICY_PATH", policy_path)
    proposal = {
        "id": "DP-109",
        "condition": "test",
        "value": 1,
        "units": "dimensionless",
        "physical_basis": "test",
        "bias": "test",
        "flag": "knob_defaulted",
    }
    assert append_proposal(
        proposal,
        proposed_by="test-dispatch",
        proposed_at="2026-11-23T00:00:00+00:00",
        evidence=["fixture"],
    ) == "DP-109"
    with pytest.raises(ValueError, match="already exists"):
        append_proposal(
            proposal,
            proposed_by="test-dispatch",
            proposed_at="2026-11-23T00:00:00+00:00",
            evidence=["fixture"],
        )
    without_id = {key: value for key, value in proposal.items() if key != "id"}
    with pytest.raises(ValueError, match="must name its allocated DP-1xx id"):
        append_proposal(
            without_id,
            proposed_by="test-dispatch",
            proposed_at="2026-11-23T00:00:00+00:00",
            evidence=["fixture"],
        )


def test_no_unregistered_scorer_default_literals() -> None:
    battery_dir = Path(__file__).resolve().parents[2] / "simulator" / "battery"
    scorer_paths = sorted(battery_dir.glob("score*.py"))
    # The pre-registry scorer has no assignment of these literals as defaults.
    # This allowlist stays file:line keyed so the coverage cars can retire any
    # legacy assignment instead of turning it into a permanent baseline.
    pre_registry_allowlist: dict[str, set[int]] = {}
    target_literals = {"101325", "0.2095", "298.15"}
    assignments: set[tuple[str, int]] = set()

    def constants(node: ast.AST) -> set[str]:
        values: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Constant):
                continue
            if isinstance(child.value, (int, float, str)):
                token = str(child.value)
                if token in target_literals:
                    values.add(token)
        return values

    for path in scorer_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(battery_dir.parent.parent).as_posix()
        for node in ast.walk(tree):
            value_node = (
                node.value
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                else None
            )
            if value_node is not None and constants(value_node):
                assignments.add((relative, node.lineno))

    expected = {
        (path, line)
        for path, lines in pre_registry_allowlist.items()
        for line in lines
    }
    assert assignments == expected
