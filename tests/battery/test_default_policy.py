from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from simulator.battery.default_policy import (
    append_proposal,
    load_policy_table,
    resolve,
)
from simulator.battery.enums import (
    DEFAULT_POLICY_NOTICE_KINDS,
    Engine,
    MethodToken,
    NoticeKind,
    Phase,
    Quantity,
    ResidualStatus,
)
from simulator.policy.enums import (
    DEFAULT_POLICY_NOTICE_KINDS as OWNER_DEFAULT_POLICY_NOTICE_KINDS,
    MethodToken as OwnerMethodToken,
    NoticeKind as OwnerNoticeKind,
    Phase as OwnerPhase,
)
from simulator.battery.migrate import to_plain
from simulator.battery.records import Notice, NoticePolicy, union_notices
from simulator.battery.score import (
    _headline_payload_admits,
    _flagged_payload_strata,
    flagged_strata,
    flagged_stratum_payloads,
    flagged_stratum_rows,
    headline_payloads,
)
from simulator.equipment import EquipmentDesigner
from simulator.state import MeltState
from tests.battery import factories as F


EXPECTED_POLICY_FLAGS = {
    "assumed_pressure",
    "total_pressure_interval_nominal",
    "method_implied",
    "effusion_regime_assumed",
    "alpha_assumed",
    "knob_defaulted",
    "assumed_fo2",
    "fo2_default_ambient_air_unprinted_atmosphere",
    "method_assumed",
    "kems_calibration_by_source",
    "basis_converted",
    "interval_observation",
    "phase_inferred",
    "composition_from_run_siblings",
    "composition_from_analysed_phase",
    "method_unstated",
    "method_inferred",
    "buffer_calibration_not_printed_canonical_used",
}


def test_battery_policy_tokens_reexport_single_owner_definitions() -> None:
    assert NoticeKind is OwnerNoticeKind
    assert Phase is OwnerPhase
    assert MethodToken is OwnerMethodToken
    assert DEFAULT_POLICY_NOTICE_KINDS is OWNER_DEFAULT_POLICY_NOTICE_KINDS


def test_every_notice_kind_reference_in_simulator_resolves() -> None:
    simulator_root = Path(__file__).resolve().parents[2] / "simulator"
    referenced: set[str] = set()
    for source_path in simulator_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id == "NoticeKind":
                referenced.add(node.attr)
            elif isinstance(owner, ast.Attribute) and owner.attr == "NoticeKind":
                referenced.add(node.attr)

    missing = sorted(
        name for name in referenced if not hasattr(OwnerNoticeKind, name)
    )
    assert not missing, f"unresolved NoticeKind references: {missing}"


def _policy_json_bytes(policy: object) -> bytes:
    return json.dumps(policy, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def test_every_entry_loads_with_closed_flags_and_trace_fields() -> None:
    policies = load_policy_table()
    assert load_policy_table() is policies
    assert set(policies) == {
        *(f"DP-{number:03d}" for number in range(1, 20)),
        "DP-101",
        "DP-106",
        "DP-107",
        "DP-108",
        "DP-112",
        "DP-114",
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
        assert entry["version"] == (2 if policy_id == "DP-010" else 1)
        assert isinstance(entry["history"], list)
        if policy_id == "DP-101":
            assert entry["status"] == "rejected"
            assert entry["proposed_by"] == "w1122cvpsat"
            assert entry["rejection_reason"] == (
                "silent vacuum fallback applied regardless of method; "
                "superseded by DP-001/DP-019 by method"
            )
            assert entry["history"]
        elif policy_id == "DP-010":
            assert entry["status"] == "proposed"
            assert entry["proposed_by"] == "physics owner"
            assert entry["flag"] == "fo2_default_ambient_air_unprinted_atmosphere"
        elif policy_id == "DP-106":
            assert entry["status"] == "proposed"
            assert entry["proposed_by"] == "w1122cvmq"
            assert entry["value"] == "unspecified"
            assert entry["flag"] == "method_unstated"
        elif policy_id == "DP-107":
            assert entry["status"] == "proposed"
            assert entry["proposed_by"] == "w1123cvmq2"
            assert entry["formula_owner"] == (
                "simulator.battery.migrate._method_inference_from_evidence"
            )
            assert entry["flag"] == "method_inferred"
        elif policy_id == "DP-112":
            assert entry["status"] == "proposed"
            assert entry["proposed_by"] == "w1123canon"
            assert entry["canonized_by"] is None
            assert entry["canonized_at"] is None
        elif policy_id == "DP-108":
            assert entry["status"] == "proposed"
            assert entry["flag"] == "assumed_pressure"
        elif policy_id == "DP-114":
            assert entry["status"] == "proposed"
            assert entry["flag"] == "total_pressure_interval_nominal"
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
    assert seen_flags == closed_flags - {"assumed_fo2"}

def test_canonical_policy_values() -> None:
    assert resolve("DP-001").value == Decimal("101325")
    assert resolve("DP-001").units == "Pa"
    assert resolve("DP-003", P_total=Decimal("100000")).value == Decimal("20950")
    assert resolve("DP-005").value == Decimal("1")
    assert resolve("DP-006").value == Decimal("1")
    assert resolve("DP-015").value == Decimal("298.15")
    assert resolve("DP-015").units == "K"


def test_notice_policy_requires_a_normalized_string_value() -> None:
    with pytest.raises(ValueError, match="notice policy requires"):
        NoticePolicy(
            id="DP-001",
            version=1,
            status="canonical",
            value=Decimal("101325"),
            units="Pa",
        )


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


def test_phase_policy_uses_only_the_migrators_closed_phase_result() -> None:
    for phase in Phase:
        resolved = resolve("DP-016", inferred_phase=phase.value)
        policy = NoticePolicy(**resolved.payload["policy"])
        assert resolved.value == phase.value
        assert resolved.flag is NoticeKind.PHASE_INFERRED
        assert policy.value == phase.value

    with pytest.raises(ValueError, match="owning inferred_phase result"):
        resolve("DP-016")
    with pytest.raises(ValueError, match="not a phase token"):
        resolve("DP-016", inferred_phase="unknown-phase")


def test_method_inferred_policy_uses_only_the_migrators_closed_method_result() -> None:
    for method in MethodToken:
        resolved = resolve("DP-107", inferred_method=method.value)
        policy = NoticePolicy(**resolved.payload["policy"])
        assert resolved.value == method.value
        assert resolved.flag is NoticeKind.METHOD_INFERRED
        assert policy.value == method.value

    with pytest.raises(ValueError, match="owning inferred_method result"):
        resolve("DP-107")
    with pytest.raises(ValueError, match="not a method token"):
        resolve("DP-107", inferred_method="unknown-method")


def test_method_implied_notice_requires_policy_metadata() -> None:
    with pytest.raises(ValueError, match="method_implied requires policy metadata"):
        Notice(
            kind=NoticeKind.METHOD_IMPLIED,
            affected_quantities=(Quantity.P_SAT,),
            reason="the method fixes the missing condition",
            origin="test",
        )


def test_formula_results_must_come_from_the_named_owner() -> None:
    ambient_fo2 = resolve(
        "DP-010", resolved_fo2_log10_bar=Decimal("-0.672")
    )
    assert ambient_fo2.value == Decimal("-0.672")
    assert ambient_fo2.payload["policy"]["value"] == "-0.672"
    assert ambient_fo2.flag is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    assert ambient_fo2.payload["policy_status"] == "proposed"

    equilibrium_pressure = resolve(
        "DP-108", predicted_total_pressure_pa=Decimal("250")
    )
    assert equilibrium_pressure.value == Decimal("250")
    assert equilibrium_pressure.flag is NoticeKind.ASSUMED_PRESSURE
    assert equilibrium_pressure.payload["policy_status"] == "proposed"

    measured_total = resolve(
        "DP-004", in_cell_total_pressure_pa=Decimal("250")
    )
    assert measured_total.value == Decimal("250")
    assert measured_total.flag is NoticeKind.EFFUSION_REGIME_ASSUMED

    regime_only = resolve("DP-004", regime_only=True)
    assert regime_only.value == "P_total(cell) = sum of measured partial pressures"

    for policy_id, context in (
        ("DP-010", {"value": Decimal("-0.672")}),
        ("DP-108", {"value": Decimal("250")}),
        ("DP-004", {"value": Decimal("250")}),
        ("DP-004", {"measured_partial_pressures": [Decimal("100"), Decimal("150")]}),
    ):
        with pytest.raises(ValueError, match="owning|requires"):
            resolve(policy_id, **context)
    with pytest.raises(ValueError, match="must be positive"):
        resolve("DP-108", predicted_total_pressure_pa=Decimal("0"))


@pytest.mark.parametrize(
    ("policy_id", "context", "quantity", "rail"),
    (
        (
            "DP-010",
            {"resolved_fo2_log10_bar": Decimal("-0.672064092852")},
            Quantity.FE3_FE2_RATIO,
            "redox",
        ),
        (
            "DP-108",
            {"predicted_total_pressure_pa": Decimal("250")},
            Quantity.P_PARTIAL,
            "vapour",
        ),
    ),
)
def test_proposed_default_flag_mutation_cannot_enter_headline(
    policy_id: str,
    context: dict[str, Decimal],
    quantity: Quantity,
    rail: str,
) -> None:
    resolved = resolve(policy_id, **context)
    notice = Notice(
        kind=resolved.flag,
        affected_quantities=(quantity,),
        reason=f"{policy_id} test default",
        origin="test:default-policy-mutation",
        policy=NoticePolicy(**resolved.payload["policy"]),
    )
    row = {"rail": rail, "notices": [to_plain(notice)]}

    assert _headline_payload_admits(row, tier="compilation") is False

    mutated_row = dict(row)
    mutated_notices = [dict(item) for item in row["notices"]]
    mutated_notices[0].pop("kind")
    mutated_row["notices"] = mutated_notices
    with pytest.raises(ValueError, match="policy-bearing notice kind"):
        _headline_payload_admits(mutated_row, tier="compilation")


def test_runtime_owned_apparatus_defaults_are_read_from_the_runtime() -> None:
    runtime_area = MeltState().melt_surface_area_m2
    assert resolve("DP-008", runtime_default=runtime_area).value == runtime_area


def test_dp007_uses_sample_sized_crucible_area_and_requires_mass() -> None:
    sample_mass_kg = 3.38e-3
    expected_area_m2 = EquipmentDesigner().size_crucible(
        sample_mass_kg
    ).melt_surface_area_m2
    resolved = resolve("DP-007", sample_mass_kg=sample_mass_kg)
    policy_bytes = _policy_json_bytes(resolved.payload["policy"])
    runner_sample_policy = resolve(
        "DP-007", sample_mass_kg=0.01
    ).payload["policy"]

    assert (
        hashlib.sha256(policy_bytes).hexdigest()
        == "b538cb732135d3441ff6fe826811ef25c398f61e9dbbc2755e9849be4ab4ea96"
    )
    assert (
        hashlib.sha256(_policy_json_bytes(runner_sample_policy)).hexdigest()
        == "053e7537cc22fe756da0429942b746f28e2de91a0905b14b83e61b89992b2a12"
    )
    assert resolved.value == Decimal(str(expected_area_m2))
    assert resolved.units == "m²"
    assert resolved.value < Decimal("0.2") / Decimal("1000")

    # Mutation recipe: substitute MeltState().melt_surface_area_m2 (0.2 m²)
    # for the size_crucible result; the gram-scale bound above must fail.
    with pytest.raises(
        ValueError,
        match=(
            "DP-007 requires sample_mass_kg: "
            "an absolute rate needs an area or sample mass"
        ),
    ):
        resolve("DP-007")
    for invalid_mass_kg in (0.0, -sample_mass_kg):
        with pytest.raises(
            ValueError,
            match="DP-007 sample_mass_kg must be positive",
        ):
            resolve("DP-007", sample_mass_kg=invalid_mass_kg)


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


def test_dp112_resolves_the_buffer_owner_formula_result() -> None:
    assert load_policy_table()["DP-112"]["formula_owner"] == (
        "simulator.chemistry.fo2_buffers.evaluate_buffer"
    )
    result = Decimal("27.5")
    resolved = resolve("DP-112", formula_result=result)
    assert resolved.value == result
    assert resolved.flag is NoticeKind.BUFFER_CALIBRATION_NOT_PRINTED_CANONICAL_USED
    assert resolved.payload["policy_status"] == "proposed"
    assert resolved.payload["policy"]["value"] == format(result.normalize(), "f")
    with pytest.raises(ValueError, match="DP-112 requires the owning formula result"):
        resolve("DP-112")


def test_dp010_resolves_the_ambient_air_ladder_result() -> None:
    result = Decimal("-8.25")
    resolved = resolve("DP-010", formula_result=result)
    assert resolved.value == result
    assert resolved.flag is NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE
    assert resolved.payload["policy"]["value"] == "-8.25"


def test_formula_results_cannot_be_overridden_by_a_value_context() -> None:
    assert load_policy_table()["DP-004"]["formula_owner"] == (
        "simulator.battery.score._sum_gas_partial_pressures_pa"
    )
    cases = (
        (
            "DP-004",
            {"measured_partial_pressures": [Decimal("1"), Decimal("2")]},
            "owning partial-pressure sum",
        ),
        ("DP-004", {"value": Decimal("3")}, "owning formula result"),
        (
            "DP-013",
            {
                "value": Decimal("12"),
                "flag": "basis_converted",
                "transform_id": "mol_percent_to_fraction",
            },
            "owning formula result",
        ),
        (
            "DP-017",
            {"value": "fixture composition", "flag": "composition_from_run_siblings"},
            "owning formula result",
        ),
        ("DP-112", {"value": Decimal("1")}),
    )
    for policy_id, context, expected in cases:
        with pytest.raises(ValueError, match=expected):
            resolve(policy_id, **context)


def test_flagged_censuses_sort_rows_with_missing_policy_ids() -> None:
    typed_notice = Notice(
        kind=NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE,
        affected_quantities=(Quantity.P_SAT,),
        reason="DP-010 ambient-air default was used",
        origin="test",
        policy=NoticePolicy(
            id="DP-010",
            version=2,
            status="proposed",
            value="-0.672",
            units="log10(bar)",
        ),
    )
    with_policy = F.residual(
        "with-policy",
        "with-policy",
        status=ResidualStatus.MATCH,
        notices=(typed_notice,),
    )

    live_rows = flagged_stratum_rows(
        (with_policy,), engines=(Engine.NASA_CEA_9,)
    )
    assert [row.get("policy_id") for row in live_rows] == ["DP-010"]

    untyped_payload = {
        "kind": NoticeKind.FO2_DEFAULT_AMBIENT_AIR_UNPRINTED_ATMOSPHERE.value,
        "affected_quantities": [Quantity.P_SAT.value],
        "reason": "legacy DP-010 ambient-air default notice",
        "origin": "test",
    }
    typed_payload = to_plain(typed_notice)
    payload_rows = [
        {
            "rail": "thermochemistry",
            "candidate_request": {"engine": Engine.NASA_CEA_9.value},
            "numeric": {"operation": "dex", "value": "0.25"},
            "notices": [notice_payload],
        }
        for notice_payload in (untyped_payload, typed_payload)
    ]
    summarized_rows = flagged_stratum_payloads(
        payload_rows, (Engine.NASA_CEA_9,)
    )
    assert [row.get("policy_id") for row in summarized_rows] == [None, "DP-010"]


def test_dispatch_environment_does_not_supply_proposer(monkeypatch) -> None:
    import simulator.policy.default_policy as policy_module

    monkeypatch.setenv("GOALFLIGHT_DISPATCH_ID", "dispatcher-only")
    with pytest.raises(ValueError, match="proposed_by is required"):
        policy_module.append_proposal(
            {
                "id": "DP-109",
                "condition": "test-only missing apparatus knob",
                "value": "runtime supplied",
                "units": "knob-specific",
                "physical_basis": "test fixture only",
                "bias": "test fixture only",
                "flag": "knob_defaulted",
            },
            evidence=["fixture row and source"],
        )


def test_proposal_resolves_as_proposed_and_stays_out_of_headline(tmp_path, monkeypatch) -> None:
    import simulator.policy.default_policy as policy_module

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
    assert [item["stratum"] for item in flagged] == ["knob_defaulted"]
    assert flagged[0]["policy_id"] == policy_id
    assert flagged[0]["policy_status"] == "proposed"

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


def test_method_inferred_payload_without_policy_is_rejected_by_headline_census() -> None:
    policy = resolve(
        "DP-107", inferred_method=MethodToken.ELECTRON_MICROPROBE.value
    )
    notice = Notice(
        kind=NoticeKind.METHOD_INFERRED,
        affected_quantities=(Quantity.P_SAT,),
        reason="method_inferred: value=electron_microprobe",
        origin="method-inferred-fixture",
        policy=NoticePolicy(**policy.payload["policy"]),
    )
    row = {
        "reference": "method-inferred-fixture",
        "rail": "vapour",
        "candidate_request": {"engine": Engine.INTERNAL_ANALYTICAL.value},
        "status": "no_band",
        "numeric": {"operation": "dex", "value": "0.25"},
        "exclusions": [],
        "notices": [to_plain(notice)],
    }
    row["notices"][0]["policy"] = None

    with pytest.raises(
        ValueError, match="method_inferred notice requires policy metadata"
    ):
        _headline_payload_admits(row, tier="all-numeric")
    with pytest.raises(
        ValueError, match="method_inferred notice requires policy metadata"
    ):
        flagged_stratum_payloads([row], [Engine.INTERNAL_ANALYTICAL])

    typed_notice_without_policy = replace(notice, policy=None)
    residual = F.residual(
        "method-inferred-without-policy",
        "method-inferred-fixture",
        status=ResidualStatus.MATCH,
        notices=(typed_notice_without_policy,),
    )
    with pytest.raises(
        ValueError, match="method_inferred notice requires policy metadata"
    ):
        flagged_stratum_rows((residual,), engines=(Engine.NASA_CEA_9,))


def test_proposals_require_allocated_ids_and_refuse_duplicate_ids(
    tmp_path, monkeypatch
) -> None:
    import simulator.policy.default_policy as policy_module

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
