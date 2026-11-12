from __future__ import annotations

import os
from pathlib import Path

import app as app_module
import pytest


def test_generated_flask_secret_file_is_private(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))

    previous_umask = os.umask(0o777)
    try:
        key = app_module._load_secret_key()
    finally:
        os.umask(previous_umask)
    secret_path = tmp_path / "instance" / "flask_secret_key"

    assert secret_path.read_text(encoding="utf-8").strip() == key
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert secret_path.parent.stat().st_mode & 0o777 == 0o700


def test_existing_flask_secret_file_is_tightened(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir(mode=0o777)
    secret_path.parent.chmod(0o777)
    secret_path.write_text("existing-secret\n", encoding="utf-8")
    secret_path.chmod(0o644)

    assert app_module._load_secret_key() == "existing-secret"
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert secret_path.parent.stat().st_mode & 0o777 == 0o700


def test_existing_secret_symlink_is_refused(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir()
    target = tmp_path / "target-secret"
    target.write_text("target-body\n", encoding="utf-8")
    secret_path.symlink_to(target)

    with pytest.raises(OSError):
        app_module._load_secret_key()

    assert target.read_text(encoding="utf-8") == "target-body\n"


def test_dangling_secret_symlink_does_not_create_target(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir()
    target = tmp_path / "missing-secret-target"
    secret_path.symlink_to(target)

    with pytest.raises(OSError):
        app_module._load_secret_key()

    assert not target.exists()


@pytest.mark.parametrize("contents", ["", "  \n"])
def test_empty_flask_secret_file_refuses(contents, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir(mode=0o700)
    secret_path.write_text(contents, encoding="utf-8")

    with pytest.raises(RuntimeError, match="Flask secret file is empty"):
        app_module._load_secret_key()


def test_fifo_secret_path_does_not_block(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))
    secret_path = tmp_path / "instance" / "flask_secret_key"
    secret_path.parent.mkdir(mode=0o700)
    os.mkfifo(secret_path)

    with pytest.raises(RuntimeError, match="not a regular file"):
        app_module._load_secret_key()


def test_explicit_flask_secret_does_not_create_a_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLASK_SECRET_KEY", "operator-secret")
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))

    assert app_module._load_secret_key() == "operator-secret"
    assert not (tmp_path / "instance").exists()
