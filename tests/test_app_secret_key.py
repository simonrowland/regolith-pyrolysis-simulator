from __future__ import annotations

import os
from pathlib import Path

import app as app_module


def test_generated_flask_secret_file_is_private(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    (tmp_path / "instance").mkdir(mode=0o700)

    previous_umask = os.umask(0o777)
    try:
        key = app_module._load_secret_key()
    finally:
        os.umask(previous_umask)
    secret_path = tmp_path / "instance" / "flask_secret_key"

    assert secret_path.read_text(encoding="utf-8").strip() == key
    assert secret_path.stat().st_mode & 0o777 == 0o600


def test_existing_flask_secret_file_is_tightened(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir()
    secret_path.write_text("existing-secret\n", encoding="utf-8")
    secret_path.chmod(0o644)

    assert app_module._load_secret_key() == "existing-secret"
    assert secret_path.stat().st_mode & 0o777 == 0o600


def test_explicit_flask_secret_does_not_create_a_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLASK_SECRET_KEY", "operator-secret")
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))

    assert app_module._load_secret_key() == "operator-secret"
    assert not (tmp_path / "instance").exists()
