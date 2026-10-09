"""Synthetic admission receipts for PT-0 cache-mechanics tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _set_engine_local_config_path(
    path: Path,
    *,
    monkeypatch: Any | None = None,
) -> tuple[Any, Any]:
    from simulator import engine_local_config

    original_config_path = engine_local_config.config_path
    replacement = lambda: path
    if monkeypatch is None:
        engine_local_config.config_path = replacement
    else:
        monkeypatch.setattr(engine_local_config, "config_path", replacement)
    return engine_local_config, original_config_path


@contextmanager
def isolate_engine_local_config(
    isolated_directory: Path,
    *,
    source_path: Path,
) -> Iterator[Path]:
    """Copy the host engine config and redirect its receipt into a temp dir."""
    source = Path(source_path)
    isolated_path = Path(isolated_directory) / source.name
    if source.is_file():
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_bytes(source.read_bytes())

    engine_local_config, original_config_path = _set_engine_local_config_path(
        isolated_path
    )
    try:
        yield isolated_path
    finally:
        engine_local_config.config_path = original_config_path


def install_synthetic_binding_receipt(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    bind_direct_backend: bool,
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
    identity = admission.BindingIdentity.from_mapping(pin["identity"])
    monkeypatch.setattr(replay, "record_binding_producer_ids", lambda *_args, **_kwargs: ())
    provenance = {
        "binding_provenance_verifiable": True,
        "test_fixture": "binding_admission_synthetic/synthetic-fake.json",
    }
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
