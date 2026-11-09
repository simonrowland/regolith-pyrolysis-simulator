from pathlib import Path

from simulator.corpus_paths import regolith_corpus_root


def test_regolith_corpus_root_uses_override_and_home_default(monkeypatch, tmp_path):
    configured_root = tmp_path / "configured-corpus"
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(configured_root))
    assert regolith_corpus_root() == configured_root

    monkeypatch.delenv("REGOLITH_CORPUS_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert regolith_corpus_root() == tmp_path / "Repos/regolith-corpus"
