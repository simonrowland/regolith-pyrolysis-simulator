"""The fog flag does not move mass. Digests are the pre-flag census ledgers.

Mars basalt will not start without its declared Stage 0 carbon reductant.
That mass comes from the feedstock owner, not from a nucleation constant.
The zero-additive call is the typed refusal. The pinned Mars ledger is the
run with that declared reductant.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from simulator.core import PyrolysisSimulator
from simulator.nucleation import NOTICE_FOG, NOTICE_UNEVALUABLE
from simulator.optimize.evaluate import _build_eval_inputs
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.runner import PyrolysisRun, RunnerError
from tests.test_optimizer_evalspec import _c2a_window_profile

# sha256 of float.hex kg_by_account plus product_ledger. Re-read from the
# production runs after the approved condenser stack (film temperature,
# every-species cold capture, e-NTU, the freeze-out cartridge, and the
# 14.7388 m2 area). Those moves change ledger bytes. This pin is not a
# with/without check of the fog flag; the history assertion below is.
_LEDGER_DIGEST = {
    "lunar_c2a_18h": "2c6c392904e1fbec287d9d3a2c2a86e6233dc877ca424a52ead0599f41b9d580",
    "mars_c2a_18h_declared_carbon": (
        "03777f56497ec7ff81a01ceef1c55bd4aaf84c4b1613a293e432b6a559656a83"
    ),
    "ci_c2a_18h": "d958b2a5a3716255fbb9c1a352425388e70c42955583c1ae266d89306e0e670a",
    "lunar_c0_24h": "ff94b195c9eab7017033c1c6b6cf9419fea1875741f4c2400b29334b3f49fede",
}


def _hexify(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return float.hex(value)
    if isinstance(value, dict):
        return {
            str(key): _hexify(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    raise TypeError(f"unsupported ledger value {type(value).__name__}")


def ledger_digest(accounts, products) -> str:
    payload = {
        "kg_by_account": _hexify(accounts),
        "product_ledger": _hexify(products),
    }
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _c2a_run(feedstock_id: str, *, additives_kg: dict[str, float] | None = None) -> PyrolysisRun:
    profile = _c2a_window_profile(1400.0, 1450.0, 18)
    profile["feedstock"] = feedstock_id
    _spec, run_config = _build_eval_inputs(
        RecipePatch({}),
        feedstock_id,
        "internal-analytical",
        profile,
        RecipeSchema(),
    )
    return PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=run_config.campaign,
        hours=run_config.hours,
        mass_kg=run_config.mass_kg,
        backend_name=run_config.backend_name,
        runtime_campaign_overrides=run_config.runtime_campaign_overrides,
        additives_kg=additives_kg or {},
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )


def _declared_mars_carbon_kg(mass_kg: float) -> float:
    from simulator.config import load_config_bundle

    feedstock = load_config_bundle().feedstocks["mars_basalt"]
    return PyrolysisSimulator._carbon_reductant_required_kg(feedstock, mass_kg)


def _fog_summary(model) -> dict:
    history = list(getattr(model, "vapour_fog_history", ()) or ())
    fog = []
    clear_na = []
    unevaluable_keys: dict[tuple, int] = {}
    for notice in history:
        code = notice.get("code")
        if code == NOTICE_UNEVALUABLE:
            key = (
                notice.get("species"),
                notice.get("stage_number"),
                notice.get("sub_order"),
                notice.get("stage_key"),
                notice.get("label"),
            )
            unevaluable_keys[key] = unevaluable_keys.get(key, 0) + 1
            continue
        row = {
            "hour": notice.get("hour"),
            "campaign_hour": notice.get("campaign_hour"),
            "stage_number": notice.get("stage_number"),
            "sub_order": notice.get("sub_order"),
            "stage_key": notice.get("stage_key"),
            "label": notice.get("label"),
            "species": notice.get("species"),
            "T_K": notice.get("T_K"),
            "p_Pa": notice.get("p_Pa"),
            "J_m3_s": notice.get("J_m3_s"),
            "saturation_ratio": notice.get("saturation_ratio"),
            "eta_molecular": notice.get("eta_molecular"),
            "eta_role": notice.get("eta_role"),
            "critical_cluster_molecules": notice.get("critical_cluster_molecules"),
            "property_notice": notice.get("property_notice"),
            "code": code,
        }
        if code == NOTICE_FOG:
            fog.append(row)
        elif notice.get("species") == "Na":
            clear_na.append(row)
    return {
        "n_notices": len(history),
        "fog": fog,
        "clear_na": clear_na,
        "unevaluable": [
            {
                "species": key[0],
                "stage_number": key[1],
                "sub_order": key[2],
                "stage_key": key[3],
                "label": key[4],
                "n": count,
            }
            for key, count in sorted(unevaluable_keys.items(), key=lambda item: str(item[0]))
        ],
    }


def _record(name: str, session, result) -> None:
    directory = Path(os.environ.get("TMPDIR", "/tmp"))
    model = getattr(session.simulator, "_condensation_model", None)
    record = getattr(session.simulator, "record", None)
    snapshots = getattr(record, "snapshots", None) if record is not None else None
    last = snapshots[-1] if snapshots else None
    payload = {
        "name": name,
        "status": result.get("status") if isinstance(result, dict) else None,
        "n_snapshots": len(snapshots) if snapshots else 0,
        "last_snapshot_hour": getattr(last, "hour", None) if last is not None else None,
        "last_snapshot_mass_balance_error_pct": (
            getattr(last, "mass_balance_error_pct", None) if last is not None else None
        ),
        "fog": _fog_summary(model),
    }
    path = directory / "t1605-fog-census.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _assert_ledger(name: str, run: PyrolysisRun) -> None:
    session = run._start_session()
    result = run._run_session(session)
    _record(name, session, result)
    assert result["status"] == "ok"
    digest = ledger_digest(
        session.simulator.atom_ledger.kg_by_account(),
        session.simulator.product_ledger(),
    )
    assert digest == _LEDGER_DIGEST[name]
    model = session.simulator._condensation_model
    # The hook ran on the campaign model. Which stages fog is reported from
    # the census file; a hot collector can strip Na before a cold wall.
    assert model.vapour_fog_history


def test_lunar_c2a_ledger_matches_pre_flag_census() -> None:
    _assert_ledger("lunar_c2a_18h", _c2a_run("lunar_mare_low_ti"))


def test_mars_c2a_ledger_matches_pre_flag_census() -> None:
    carbon_kg = _declared_mars_carbon_kg(1000.0)
    assert carbon_kg == pytest.approx(30.0)
    _assert_ledger(
        "mars_c2a_18h_declared_carbon",
        _c2a_run("mars_basalt", additives_kg={"C": carbon_kg}),
    )


def test_mars_c2a_without_declared_carbon_is_refused() -> None:
    run = _c2a_run("mars_basalt")
    with pytest.raises(RunnerError, match="requires 30 kg C reductant"):
        run._start_session()


def test_ci_c2a_ledger_matches_pre_flag_census() -> None:
    _assert_ledger("ci_c2a_18h", _c2a_run("ci_carbonaceous_chondrite"))


def test_lunar_c0_ledger_matches_pre_flag_census() -> None:
    _assert_ledger(
        "lunar_c0_24h",
        PyrolysisRun(
            feedstock_id="lunar_mare_low_ti",
            campaign="C0",
            hours=24,
            mass_kg=1000.0,
            backend_name="internal-analytical",
            force_builtin_vapor_pressure=True,
            allow_fallback_vapor=True,
            allow_unmeasured_alpha_fallback=True,
        ),
    )
