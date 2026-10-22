"""Synthetic admission receipts for PT-0 cache-mechanics tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator


def _set_engine_local_config_path(
    path: Path,
    *,
    monkeypatch: Any | None = None,
) -> tuple[Any, Any]:
    import engine_install_config

    original_config_path = engine_install_config.config_path
    replacement = lambda: path
    if monkeypatch is None:
        engine_install_config.config_path = replacement
    else:
        monkeypatch.setattr(engine_install_config, "config_path", replacement)
    return engine_install_config, original_config_path


@contextmanager
def isolate_engine_local_config(
    isolated_directory: Path,
    *,
    source_path: Path,
) -> Iterator[Path]:
    """Copy the host config and redirect engine-config reads into a temp dir."""
    source = Path(source_path)
    isolated_path = Path(isolated_directory) / source.name
    if source.is_file():
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_bytes(source.read_bytes())

    engine_install_config, original_config_path = _set_engine_local_config_path(
        isolated_path
    )
    try:
        yield isolated_path
    finally:
        engine_install_config.config_path = original_config_path


def install_synthetic_binding_receipt(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    bind_direct_backend: bool,
    identity_override: Any | None = None,
    provenance_override: dict[str, Any] | None = None,
) -> None:
    """Install a test-only receipt without certifying a real engine."""
    from simulator import engine_binding_admission as admission
    from simulator import reduced_real_determinism as replay

    pin_path = (
        Path(__file__).parent
        / "fixtures"
        / "binding_admission_synthetic"
        / "synthetic-fake.json"
    )
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    identity = (
        identity_override
        if identity_override is not None
        else admission.BindingIdentity.from_mapping(pin["identity"])
    )
    monkeypatch.setattr(replay, "record_binding_producer_ids", lambda *_args, **_kwargs: ())
    provenance = (
        provenance_override
        if provenance_override is not None
        else {
            "binding_provenance_verifiable": True,
            "test_fixture": "binding_admission_synthetic/synthetic-fake.json",
        }
    )
    _set_engine_local_config_path(
        tmp_path / "engines.local.toml",
        monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(
        admission,
        "binding_identity_for_cached_real",
        lambda _config: identity,
    )
    monkeypatch.setattr(
        admission,
        "binding_identity_for_backend",
        lambda _backend: identity if bind_direct_backend else None,
    )
    monkeypatch.setattr(
        admission,
        "binding_identity_for_gate_fallback",
        lambda: identity,
    )
    monkeypatch.setattr(
        admission,
        "cached_real_provenance",
        lambda _config, _backend: provenance,
    )
    monkeypatch.setattr(
        admission,
        "current_cached_real_provenance",
        lambda _sim, _identity: provenance,
    )
    monkeypatch.setattr(
        admission,
        "current_backend_provenance",
        lambda _identity, _backend: provenance,
    )
    admission.binding_receipt_path().write_text(
        json.dumps(
            {
                "schema_version": admission.RECEIPT_SCHEMA_VERSION,
                "assessed_at": "test-only",
                "entries": [
                    {
                        "identity": identity.as_dict(),
                        "status": "admitted",
                        "reason": None,
                        "artifacts": [
                            "equilibrium_post_record",
                            "freeze_gate_curve",
                        ],
                        "comparison": {"status": "matched", "artifacts": []},
                        "provenance": provenance,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def seed_gate_curve_memo_record(
    sim: Any,
    curve: dict[str, Any],
    *,
    key: Any = None,
    repair_notices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Seed the runtime slot through the production origin-record writer."""
    from simulator.engine_binding_admission import write_gate_curve_memo_record
    from simulator.reduced_real_determinism import GateCurveProviderOutcome

    if key is None:
        fO2_log = float(sim._current_melt_redox_fO2_log())
        redox_key_fO2_log = sim._freeze_gate_redox_key_fO2_log(
            fO2_log=fO2_log,
        )
        key = sim._freeze_gate_cache_key(
            pressure_bar=float(sim.melt.p_total_mbar) / 1000.0,
            fO2_log=redox_key_fO2_log,
        )
    record = write_gate_curve_memo_record(
        sim,
        curve,
        repair_notices,
        provider_outcome=GateCurveProviderOutcome.PRODUCED,
    )
    sim._freeze_gate_liquid_fraction_cache = {
        **record,
        "key": key,
    }
    return record


def register_synthetic_gate_curve_producer(sim: Any) -> None:
    """Register the no-engine MAGEMin fallback producer used in tests."""
    from simulator.chemistry.kernel import ChemistryIntent

    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    provider._backend = SimpleNamespace(is_available=lambda: True)
    provider._backend_initialised = True
