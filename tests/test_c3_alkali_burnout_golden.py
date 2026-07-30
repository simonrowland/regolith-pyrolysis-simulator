from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from simulator.runner import PyrolysisRun
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_session,
)


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = (
    ROOT / "tests" / "fixtures" / "c3_alkali_burnout_duration_isolation.json"
)
RECIPE = ROOT / "data" / "recipes" / "canonical_lunar_full_yield.yaml"


def _base_config() -> SimSessionConfig:
    recipe = yaml.safe_load(RECIPE.read_text()) or {}
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_STAGED",
        hours=120,
        additives_kg={"Na": 140.0, "K": 56.0},
        setpoints_patch=recipe,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-07-29T00:00:00Z",
            "kernel_commit_sha": "c3-duration-isolation-golden",
        },
    )._session_config()


def _with_c3_cap(base: SimSessionConfig, cap_h: float) -> SimSessionConfig:
    override = {"C3_NA": {"max_hours": cap_h}}
    return dataclasses.replace(
        base,
        runtime_campaign_overrides=override,
        setpoints_overrides=override,
    )


def _assert_only_termination_differs(
    capped_3h: SimSessionConfig,
    capped_6h: SimSessionConfig,
) -> None:
    excluded = {"runtime_campaign_overrides", "setpoints_overrides"}
    for field in dataclasses.fields(SimSessionConfig):
        if field.name in excluded:
            continue
        assert getattr(capped_3h, field.name) == getattr(capped_6h, field.name)
    assert capped_3h.runtime_campaign_overrides == {
        "C3_NA": {"max_hours": 3.0}
    }
    assert capped_6h.runtime_campaign_overrides == {
        "C3_NA": {"max_hours": 6.0}
    }


def _run_to_c3_summary(config: SimSessionConfig) -> dict:
    session = SimSession()
    session.start(config)
    rates = []
    authority = []
    summary = None
    for result in drive_session(
        session,
        hours=config.hours,
        policy=DecisionPolicy.AUTO_APPLY,
    ):
        if result.snapshot.campaign.name == "C3_NA":
            rates.append(
                float(result.snapshot.evap_flux.species_kg_hr.get("Na", 0.0))
            )
            diagnostic = dict(
                getattr(
                    session.simulator,
                    "_last_evaporation_flux_diagnostic",
                    {},
                )
                or {}
            )
            authority.append({
                "ledger_yields_authorized": diagnostic.get(
                    "ledger_yields_authorized"
                ),
                "p_bulk_transport_domain": diagnostic.get(
                    "p_bulk_transport_domain"
                ),
            })
        if (
            result.campaign_summary
            and result.campaign_summary.get("campaign") == "C3_NA"
        ):
            summary = result.campaign_summary
            break
    assert summary is not None

    sodium = summary["c3_alkali_accounting"]["by_species"]["Na"]
    credit_line = sodium["credit_line"]
    melt_clearance = sodium["melt_clearance"]
    disposition = sodium["disposition"]
    recoverable_by_account = disposition[
        "recoverable_credit_kg_by_account"
    ]
    return {
        "duration_h": summary["duration_h"],
        "na_rate_kg_hr": rates,
        "credit_line": {
            "requested_inventory_kg": credit_line[
                "requested_inventory_kg"
            ],
            "gross_drawn_kg": credit_line["gross_drawn_kg"],
            "net_outstanding_kg": credit_line["net_outstanding_kg"],
            "reagent_origin_total_kg": credit_line[
                "reagent_origin_total_kg"
            ],
            "available_reagent_inventory_kg": credit_line[
                "available_reagent_inventory_kg"
            ],
        },
        "melt_clearance": {
            "status": melt_clearance["status"],
            "reagent_origin_remaining_in_melt_kg": melt_clearance[
                "reagent_origin_remaining_in_melt_kg"
            ],
            "reagent_origin_outside_melt_kg": melt_clearance[
                "reagent_origin_outside_melt_kg"
            ],
        },
        "disposition": {
            "status": disposition["status"],
            "recoverable_credit_kg": disposition[
                "recoverable_credit_kg"
            ],
            "overhead_kg": recoverable_by_account.get(
                "process.overhead_gas",
                0.0,
            ),
            "terminal_offgas_kg": recoverable_by_account.get(
                "terminal.offgas",
                0.0,
            ),
            "condensation_train_kg": recoverable_by_account.get(
                "process.condensation_train",
                0.0,
            ),
            "irrecoverable_loss_kg": disposition[
                "irrecoverable_loss_kg"
            ],
            "wall_deposit_loss_kg": disposition[
                "wall_deposit_loss_kg"
            ],
            "irrecoverable_vent_loss_kg": disposition[
                "irrecoverable_vent_loss_kg"
            ],
            "wall_loss_provisional_pending_t475": disposition[
                "wall_loss_provisional_pending_t475"
            ],
            "wall_loss_caveat": disposition["wall_loss_caveat"],
        },
        "termination_status": summary["c3_termination"]["status"],
        "mass_balance_error_pct": (
            session.simulator._make_snapshot().mass_balance_error_pct
        ),
        "authority": authority,
    }


@pytest.mark.serial
@pytest.mark.xdist_group("serial")
def test_c3_duration_only_isolation_golden_rejects_confounded_burnout_claim():
    golden = json.loads(GOLDEN.read_text())
    base = _base_config()
    capped_3h_config = _with_c3_cap(base, 3.0)
    capped_6h_config = _with_c3_cap(base, 6.0)
    _assert_only_termination_differs(capped_3h_config, capped_6h_config)

    actual = {
        "capped_3h": _run_to_c3_summary(capped_3h_config),
        "capped_6h": _run_to_c3_summary(capped_6h_config),
    }

    for case_name, expected in (
        ("capped_3h", golden["capped_3h"]),
        ("capped_6h", golden["capped_6h"]),
    ):
        case = actual[case_name]
        assert case["duration_h"] == expected["duration_h"]
        assert case["na_rate_kg_hr"] == pytest.approx(
            expected["na_rate_kg_hr"], rel=0.0, abs=1.0e-9
        )
        for section, fields in (
            (
                "credit_line",
                (
                    "requested_inventory_kg",
                    "gross_drawn_kg",
                    "net_outstanding_kg",
                    "reagent_origin_total_kg",
                    "available_reagent_inventory_kg",
                ),
            ),
            (
                "melt_clearance",
                (
                    "reagent_origin_remaining_in_melt_kg",
                    "reagent_origin_outside_melt_kg",
                ),
            ),
            (
                "disposition",
                (
                    "recoverable_credit_kg",
                    "overhead_kg",
                    "terminal_offgas_kg",
                    "condensation_train_kg",
                    "irrecoverable_loss_kg",
                    "wall_deposit_loss_kg",
                    "irrecoverable_vent_loss_kg",
                ),
            ),
        ):
            for field in fields:
                assert case[section][field] == pytest.approx(
                    expected[section][field],
                    rel=0.0,
                    abs=1.0e-9,
                )
        assert case["mass_balance_error_pct"] == pytest.approx(
            expected["mass_balance_error_pct"],
            rel=0.0,
            abs=1.0e-9,
        )
        assert case["melt_clearance"]["status"] == (
            expected["melt_clearance"]["status"]
        )
        assert case["disposition"]["status"] == (
            expected["disposition"]["status"]
        )
        assert case["disposition"][
            "wall_loss_provisional_pending_t475"
        ] is True
        assert case["disposition"]["wall_loss_caveat"] == (
            expected["disposition"]["wall_loss_caveat"]
        )
        assert "t-475" in case["disposition"]["wall_loss_caveat"]
        assert (
            case["disposition"]["recoverable_credit_kg"]
            == pytest.approx(
                case["disposition"]["overhead_kg"]
                + case["disposition"]["terminal_offgas_kg"]
                + case["disposition"]["condensation_train_kg"],
                rel=0.0,
                abs=1.0e-9,
            )
        )
        assert case["termination_status"] == expected["termination_status"]
        assert all(
            row == {
                "ledger_yields_authorized": True,
                "p_bulk_transport_domain": "in_domain",
            }
            for row in case["authority"]
        )

    melt_remaining_change_kg = (
        actual["capped_6h"]["melt_clearance"][
            "reagent_origin_remaining_in_melt_kg"
        ]
        - actual["capped_3h"]["melt_clearance"][
            "reagent_origin_remaining_in_melt_kg"
        ]
    )
    recoverable_disposition_change_kg = (
        actual["capped_6h"]["disposition"]["recoverable_credit_kg"]
        - actual["capped_3h"]["disposition"]["recoverable_credit_kg"]
    )
    irrecoverable_loss_change_kg = (
        actual["capped_6h"]["disposition"]["irrecoverable_loss_kg"]
        - actual["capped_3h"]["disposition"]["irrecoverable_loss_kg"]
    )
    assert melt_remaining_change_kg == pytest.approx(
        golden["duration_effect"]["melt_remaining_change_kg"],
        rel=0.0,
        abs=1.0e-9,
    )
    assert recoverable_disposition_change_kg == pytest.approx(
        golden["duration_effect"]["recoverable_disposition_change_kg"],
        rel=0.0,
        abs=1.0e-9,
    )
    assert irrecoverable_loss_change_kg == pytest.approx(
        golden["duration_effect"]["irrecoverable_loss_change_kg"],
        rel=0.0,
        abs=1.0e-9,
    )
    assert actual["capped_6h"]["melt_clearance"][
        "reagent_origin_remaining_in_melt_kg"
    ] > 60.0
    assert actual["capped_6h"]["credit_line"]["gross_drawn_kg"] > (
        actual["capped_3h"]["credit_line"]["gross_drawn_kg"]
    )
    assert actual["capped_6h"]["termination_status"] == "truncated"
