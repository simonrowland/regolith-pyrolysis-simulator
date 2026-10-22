from __future__ import annotations

from pathlib import Path

import pytest

from simulator.engine_local_config import (
    artifact_for_platform,
    check_engine_install,
    engine_install_path,
    load_engine_lock,
    render_locked_config,
    render_toml,
)


LOCK_PATH = Path(__file__).resolve().parents[1] / "engines" / "engines.lock.toml"


def test_engine_lock_contains_the_reviewed_studio_source_pins(
    tmp_path: Path,
) -> None:
    lock = load_engine_lock(LOCK_PATH)

    assert lock.engines["magemin"].pin == "40ed109d07d4246001cab59382596d33fed79086"
    assert lock.engines["thermoengine"].pin == "df7a5f4609116a83bf40a5a352a19e3826a822d9"
    assert lock.engines["vaporock"].pin == "0159678a8eefb51dfe3f12bb646dc38507a07230"
    assert lock.engines["pysulfsat"].pin == "ed7c4a03e2e03b327e962d83f7f07ebf418c7bb7"
    assert lock.engines["petthermotools"].pin == "74a41ff5685d9c8280b90667bfa43efe97fca87c"
    assert lock.engines["thermobar"].pin == "90ea2cd71eb41ea7d507263787a2b232446bc822"
    assert lock.engines["alphamelts"].pin == (
        "f233ff5180df4e8318af922183beb85c20c33c28d3cb1be1254916f856327136"
    )
    assert lock.engines["alphamelts"].url.endswith(
        "alphamelts-app-2.3.1-macos-arm64.zip"
    )
    assert lock.engines["alphamelts"].reference_sha256 == (
        "d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85"
    )
    assert set(lock.engines["alphamelts"].platform_assets) == {
        "Darwin-x86_64",
        "Linux-x86_64",
        "Windows-AMD64",
    }
    canonical_root = tmp_path / "engine-bin"
    alpha_pin = lock.engines["alphamelts"]
    for platform_key, asset in lock.engines["alphamelts"].platform_assets.items():
        assert engine_install_path(
            lock, canonical_root, "alphamelts", platform_key=platform_key
        ) == canonical_root / alpha_pin.directory / asset.pin


def test_engine_lock_rejects_short_git_commits(tmp_path: Path) -> None:
    source = LOCK_PATH.read_text(encoding="utf-8")
    invalid = source.replace(
        'commit = "40ed109d07d4246001cab59382596d33fed79086"',
        'commit = "40ed109"',
        1,
    )
    lock_path = tmp_path / "engines.lock.toml"
    lock_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ValueError, match="full 40-character"):
        load_engine_lock(lock_path)


def test_lock_rendering_is_deterministic_and_pathless_in_identity_versions(
    tmp_path: Path,
) -> None:
    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "repos" / "engine-bin"

    config = render_locked_config(lock, root)
    first = render_toml(config)
    second = render_toml(render_locked_config(lock, root))
    other_root_config = render_locked_config(lock, tmp_path / "other-host" / "engine-bin")

    assert first == second
    assert f'"{root}/MAGEMin/{lock.engines["magemin"].pin}/MAGEMin"' in first
    assert 'version = "source 40ed109d07d4246001cab59382596d33fed79086"' in first
    assert config.identities["magemin"].extra == {"db": "ig"}
    assert {
        key: identity.cache_version()
        for key, identity in config.identities.items()
    } == {
        key: identity.cache_version()
        for key, identity in other_root_config.identities.items()
    }
    assert str(tmp_path) not in "\n".join(
        line for line in first.splitlines() if line.startswith("version = ")
    )


def test_lock_rendering_does_not_discover_legacy_engine_paths(tmp_path: Path) -> None:
    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "canonical" / "engine-bin"
    legacy_magemin = tmp_path / "checkout" / "MAGEMin" / "MAGEMin"
    legacy_alpha = tmp_path / "checkout" / "engines" / "alphamelts" / "alphamelts2"
    legacy_magemin.parent.mkdir(parents=True)
    legacy_alpha.parent.mkdir(parents=True)
    legacy_magemin.write_bytes(b"legacy binary")
    legacy_alpha.write_bytes(b"legacy alphaMELTS")

    config = render_locked_config(lock, root)

    assert config.paths.magemin_binary_path == (
        engine_install_path(lock, root, "magemin") / "MAGEMin"
    )
    alpha_asset = artifact_for_platform(lock.engines["alphamelts"])
    assert config.paths.alphamelts_binary_path == engine_install_path(
        lock, root, "alphamelts"
    ) / alpha_asset.runtime_artifact


def test_check_classifies_source_artifact_config_and_extra_build_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator import engine_local_config as module

    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "engine-bin"
    repos_root = tmp_path / "repos"
    config_path = tmp_path / "engines.local.toml"
    commits: dict[Path, str] = {}

    for key, pin in lock.engines.items():
        if pin.source_kind != "git":
            continue
        source = engine_install_path(lock, root, key)
        source.mkdir(parents=True)
        commits[source] = pin.pin
        for artifact in pin.artifacts:
            if key == "magemin" and artifact == "libMAGEMin.dylib":
                continue
            artifact_path = source / artifact
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(f"{key}:{artifact}".encode())

    wrong_checkout = engine_install_path(lock, root, "vaporock")
    commits[wrong_checkout] = "0" * 40
    alpha = lock.engines["alphamelts"]
    alpha_asset = artifact_for_platform(alpha)
    alpha_binary = engine_install_path(lock, root, "alphamelts") / alpha_asset.runtime_artifact
    alpha_binary.parent.mkdir(parents=True, exist_ok=True)
    alpha_binary.write_bytes(b"wrong alphaMELTS")

    extra_magemin = repos_root / "MAGEMin"
    (extra_magemin / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "git_rev",
        lambda path: commits.get(Path(path), "unknown"),
    )

    expected = render_toml(render_locked_config(lock, root))
    drifted = expected.replace(str(root), str(tmp_path / "wrong-root"), 1)
    drifted = drifted.replace('digest = "sha256:', 'digest = "sha256:bad', 1)
    config_path.write_text(drifted, encoding="utf-8")

    report = check_engine_install(
        lock,
        root,
        repos_root=repos_root,
        local_config=config_path,
        host="fake-host",
    )

    codes = {drift.code for drift in report.drifts}
    assert report.host == "fake-host"
    assert "wrong_commit" in codes
    assert "missing_artifact" in codes
    assert "artifact_sha256_drift" in codes
    assert {
        drift.severity
        for drift in report.drifts
        if drift.code == "artifact_sha256_drift" and drift.engine == "magemin"
    } == {"warning"}
    assert "extra_build" in codes
    assert "config_paths_drift" in codes
    assert "config_digest_drift" in codes
    assert report.artifact_shas
