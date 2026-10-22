from __future__ import annotations

import os
from pathlib import Path

import pytest

from simulator.engine_local_config import (
    EngineLocalConfig,
    EngineLock,
    EnginePaths,
    artifact_for_platform,
    check_engine_install,
    engine_install_path,
    load_engine_lock,
    load_config,
    render_locked_config,
    render_toml,
    thermoengine_artifact_names,
    write_config,
    write_locked_config,
)


LOCK_PATH = Path(__file__).resolve().parents[1] / "engines" / "engines.lock.toml"


def _checked_extra_builds(
    lock: EngineLock,
    install_root: Path,
    repos_root: Path,
) -> set[tuple[str, Path]]:
    report = check_engine_install(
        lock,
        install_root,
        repos_root=repos_root,
        local_config=repos_root / "engines.local.toml",
        library_root=repos_root / "home-lib",
        host="fake-host",
    )
    return {
        (drift.engine, drift.path)
        for drift in report.drifts
        if drift.code == "extra_build"
    }


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
    alpha_assets = lock.engines["alphamelts"].platform_assets
    assert set(alpha_assets) == {
        "Darwin-arm64",
        "Darwin-x86_64",
        "Linux-x86_64",
        "Windows-AMD64",
    }
    assert alpha_assets["Darwin-arm64"].url.endswith(
        "alphamelts-app-2.3.1-macos-arm64.zip"
    )
    assert alpha_assets["Darwin-arm64"].reference_sha256 == (
        "d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85"
    )
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


def test_clone_locked_sources_refuses_a_wrong_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import simulator.engine_local_config as engine_config

    installer_path = LOCK_PATH.parents[1] / "install-engines.py"
    spec = importlib.util.spec_from_file_location("install_engines_test", installer_path)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "engine-bin"
    revisions: dict[Path, str] = {}
    for key, pin in lock.engines.items():
        if pin.source_kind != "git":
            continue
        source = engine_install_path(lock, root, key)
        source.mkdir(parents=True)
        revisions[source] = pin.pin
    destination = engine_install_path(lock, root, "magemin")
    marker = destination / "existing-data"
    marker.write_bytes(b"preserve wrong-pin checkout")
    commands: list[list[str]] = []
    revisions[destination] = "0" * 40
    monkeypatch.setattr(engine_config, "git_rev", lambda path: revisions[Path(path)])
    monkeypatch.setattr(installer, "_run", lambda command, **_kwargs: commands.append(command))

    installer.clone_locked_sources(lock, root)

    assert commands == []
    assert marker.read_bytes() == b"preserve wrong-pin checkout"


def test_engine_verify_fails_install_when_a_fresh_import_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from types import SimpleNamespace

    installer_path = LOCK_PATH.parents[1] / "install-engines.py"
    spec = importlib.util.spec_from_file_location("install_engines_verify_test", installer_path)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "engine-bin"
    magemin = engine_install_path(lock, root, "magemin") / "MAGEMin"
    magemin.parent.mkdir(parents=True)
    magemin.write_bytes(b"fake binary")
    import_environments: list[dict[str, str]] = []

    def fail_import(command, **kwargs):
        if "-c" in command:
            import_environments.append(kwargs["env"])
            return SimpleNamespace(returncode=1, stdout="", stderr="import failed")
        return SimpleNamespace(returncode=0, stdout="MAGEMin", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fail_import)
    monkeypatch.setattr(installer, "checkout_matches_pin", lambda _pin, _path: True)

    with pytest.raises(SystemExit, match="Engine verification failed"):
        installer.verify(Path("/fake/python"), lock, root)

    assert len(import_environments) == 5
    assert all(
        environment["DYLD_FALLBACK_LIBRARY_PATH"].startswith(
            str(Path.home() / "lib")
        )
        for environment in import_environments
    )


def test_lock_rendering_is_deterministic_and_pathless_in_identity_versions(
    tmp_path: Path,
) -> None:
    lock = load_engine_lock(LOCK_PATH)
    root = tmp_path / "repos" / "engine-bin"
    other_root = tmp_path / "other-host" / "engine-bin"

    for install_root in (root, other_root):
        for key, pin in lock.engines.items():
            engine_dir = engine_install_path(lock, install_root, key)
            artifacts = (
                artifact_for_platform(pin).artifacts
                if pin.source_kind == "artifact"
                else pin.artifacts
            )
            for artifact in artifacts:
                path = engine_dir / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{key}/{artifact}".encode())

    config = render_locked_config(lock, root)
    first = render_toml(config)
    second = render_toml(render_locked_config(lock, root))
    other_magemin = engine_install_path(lock, other_root, "magemin") / "MAGEMin"
    other_magemin.write_bytes(b"same source pin, different native build")
    other_root_config = render_locked_config(lock, other_root)

    assert first == second
    assert f'"{root}/MAGEMin/{lock.engines["magemin"].pin}/MAGEMin"' in first
    assert 'version = "source 40ed109d07d4246001cab59382596d33fed79086"' in first
    assert config.identities["magemin"].extra == {"db": "ig"}
    assert config.identities["magemin"].version == other_root_config.identities[
        "magemin"
    ].version
    assert config.identities["magemin"].digest != other_root_config.identities[
        "magemin"
    ].digest
    assert config.identities["magemin"].cache_version() != other_root_config.identities[
        "magemin"
    ].cache_version()
    assert {
        key: identity.version for key, identity in config.identities.items()
    } == {
        key: identity.version
        for key, identity in other_root_config.identities.items()
    }
    assert str(tmp_path) not in "\n".join(
        line for line in first.splitlines() if line.startswith("version = ")
    )


def test_render_toml_round_trips_windows_style_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module

    windows_paths = {
        "thermoengine_dylib_dir": (
            r"C:\Users\operator\repos\engine-bin\thermoengine\pin\lib"
        ),
        "alphamelts_binary_path": (
            r"C:\Users\operator\repos\engine-bin\alphamelts\pin\alphamelts.exe"
        ),
        "magemin_binary_path": (
            r"C:\Users\operator\repos\engine-bin\magemin\pin\MAGEMin.exe"
        ),
    }
    config = EngineLocalConfig(
        paths=EnginePaths(
            **{key: Path(value) for key, value in windows_paths.items()}
        ),
        identities={},
    )
    path = tmp_path / "engines.local.toml"
    monkeypatch.setattr(module, "config_path", lambda: path)

    write_config(config)
    loaded = load_config(required=True)

    assert loaded is not None
    assert {
        key: str(getattr(loaded.paths, key))
        for key in windows_paths
    } == windows_paths


def test_lock_rendering_does_not_discover_legacy_engine_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module

    lock = load_engine_lock(LOCK_PATH)
    checkout = tmp_path / "checkout"
    root = tmp_path / "home" / "repos" / "engine-bin"
    legacy_magemin = checkout / "engines" / "magemin" / "MAGEMin"
    sibling_magemin = checkout.parent / "MAGEMin" / "MAGEMin"
    legacy_alpha = checkout / "engines" / "alphamelts" / "alphamelts2"
    legacy_magemin.parent.mkdir(parents=True)
    sibling_magemin.parent.mkdir(parents=True)
    legacy_alpha.parent.mkdir(parents=True)
    legacy_magemin.write_bytes(b"legacy binary")
    sibling_magemin.write_bytes(b"legacy sibling binary")
    legacy_alpha.write_bytes(b"legacy alphaMELTS")
    home = tmp_path / "home"
    path_bin = tmp_path / "path-bin"
    path_bin.mkdir()
    for name in ("MAGEMin", "alphamelts"):
        executable = path_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    (home / "lib").mkdir(parents=True)
    for name in thermoengine_artifact_names(lock):
        (home / "lib" / name).write_bytes(b"legacy dylib")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(path_bin))
    monkeypatch.setattr(
        module,
        "config_path",
        lambda: checkout / "engines" / "engines.local.toml",
    )

    for key, pin in lock.engines.items():
        engine_dir = engine_install_path(lock, root, key)
        if pin.source_kind == "git":
            (engine_dir / ".git").mkdir(parents=True, exist_ok=True)
            artifacts = pin.artifacts
        else:
            asset = artifact_for_platform(pin)
            artifacts = asset.artifacts
        for artifact in artifacts:
            path = engine_dir / artifact
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{key}/{artifact}".encode())

    config_path = write_locked_config(lock, root)
    config = load_config(required=True)
    report = check_engine_install(
        lock,
        root,
        repos_root=tmp_path,
        local_config=config_path,
        library_root=home / "lib",
        host="fake-host",
    )

    assert config is not None
    assert config.paths.magemin_binary_path == (
        engine_install_path(lock, root, "magemin") / "MAGEMin"
    )
    alpha_asset = artifact_for_platform(lock.engines["alphamelts"])
    assert config.paths.alphamelts_binary_path == engine_install_path(
        lock, root, "alphamelts"
    ) / alpha_asset.runtime_artifact
    drift_codes = {drift.code for drift in report.drifts}
    assert "extra_build" in drift_codes
    assert "config_paths_drift" not in drift_codes
    assert "config_digest_drift" not in drift_codes
    extra_paths = {
        drift.path for drift in report.drifts if drift.code == "extra_build"
    }
    assert legacy_magemin.parent in extra_paths
    assert sibling_magemin.parent in extra_paths
    assert legacy_alpha.parent in extra_paths
    written = config_path.read_text(encoding="utf-8")
    assert all(
        str(path) not in written
        for path in (
            checkout,
            sibling_magemin.parent,
            legacy_alpha.parent,
            path_bin,
            home / "lib",
        )
    )


def test_check_reports_each_magemin_runtime_binary_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module
    from simulator.melt_backend import magemin as runtime

    lock = load_engine_lock(LOCK_PATH)
    repos_root = tmp_path / "repos"
    install_root = tmp_path / "home" / "repos" / "engine-bin"
    empty_project = repos_root / "magemin-order" / "project"
    empty_engine_root = empty_project / "engines" / "magemin"
    empty_engine_root.mkdir(parents=True)
    empty_candidates = module._legacy_magemin_binary_candidates(
        empty_engine_root,
        sibling_root=empty_project.parent,
    )
    monkeypatch.setattr(
        runtime,
        "__file__",
        str(empty_project / "simulator" / "melt_backend" / "magemin.py"),
    )
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    observed: list[Path] = []
    original_exists = Path.exists

    def record_candidates(path: Path) -> bool:
        observed.append(path)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", record_candidates)
    assert runtime.MAGEMinBackend._locate_binary(None) is None
    assert tuple(observed) == empty_candidates
    monkeypatch.setattr(Path, "exists", original_exists)
    assert not _checked_extra_builds(lock, install_root, repos_root)

    for index in range(3):
        project_root = repos_root / f"magemin-case-{index}" / "project"
        engine_root = project_root / "engines" / "magemin"
        candidates = module._legacy_magemin_binary_candidates(
            engine_root,
            sibling_root=project_root.parent,
        )
        assert candidates == (
            engine_root / "MAGEMin",
            engine_root / "bin" / "MAGEMin",
            project_root.parent / "MAGEMin" / "MAGEMin",
        )
        selected = candidates[index]
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(b"legacy MAGEMin executable")
        selected.chmod(0o755)
        monkeypatch.setattr(
            runtime,
            "__file__",
            str(project_root / "simulator" / "melt_backend" / "magemin.py"),
        )
        assert runtime.MAGEMinBackend._locate_binary(None) == selected

        expected_checkout = engine_root if index < 2 else selected.parent
        assert ("magemin", expected_checkout) in _checked_extra_builds(
            lock,
            install_root,
            repos_root,
        )


def test_check_reports_each_alphamelts_runtime_binary_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    lock = load_engine_lock(LOCK_PATH)
    repos_root = tmp_path / "repos"
    install_root = tmp_path / "home" / "repos" / "engine-bin"
    order_root = (
        repos_root
        / "alphamelts-order"
        / "project"
        / "engines"
        / "alphamelts"
    )
    order_root.mkdir(parents=True)
    (order_root / "bundle-a").mkdir()
    (order_root / "bundle-z").mkdir()
    expected_candidates = module._legacy_alphamelts_binary_candidates(order_root)
    backend = AlphaMELTSBackend()
    observed: list[Path] = []
    original_exists = Path.exists

    def record_candidates(path: Path) -> bool:
        if path != order_root:
            observed.append(path)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", record_candidates)
    assert backend._find_project_binary(order_root) is None
    assert tuple(observed) == expected_candidates
    monkeypatch.setattr(Path, "exists", original_exists)
    assert ("alphamelts", order_root) not in _checked_extra_builds(
        lock,
        install_root,
        repos_root,
    )

    for index in range(len(expected_candidates)):
        engine_root = (
            repos_root
            / f"alphamelts-case-{index}"
            / "project"
            / "engines"
            / "alphamelts"
        )
        engine_root.mkdir(parents=True)
        (engine_root / "bundle-a").mkdir()
        (engine_root / "bundle-z").mkdir()
        selected = module._legacy_alphamelts_binary_candidates(engine_root)[
            index
        ]
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(b"legacy alphaMELTS executable")
        selected.chmod(0o755)

        assert AlphaMELTSBackend()._find_project_binary(engine_root) == selected
        assert ("alphamelts", engine_root) in _checked_extra_builds(
            lock,
            install_root,
            repos_root,
        )


def test_check_reports_runtime_selected_alphamelts_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as local_config
    from simulator.melt_backend import alphamelts as runtime

    class UnavailableVapoRock:
        def initialize(self, _config: dict) -> None:
            return None

        def is_available(self) -> bool:
            return False

    lock = load_engine_lock(LOCK_PATH)
    repos_root = tmp_path / "repos"
    install_root = tmp_path / "home" / "repos" / "engine-bin"
    project_root = repos_root / "launcher-only" / "project"
    engine_root = project_root / "engines" / "alphamelts"
    launcher = engine_root / "run_alphamelts.command"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(
        runtime,
        "__file__",
        str(project_root / "simulator" / "melt_backend" / "alphamelts.py"),
    )
    monkeypatch.setattr(local_config, "find_alphamelts_binary", lambda _root: None)
    monkeypatch.setattr(runtime, "VapoRockBackend", UnavailableVapoRock)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    backend = runtime.AlphaMELTSBackend()
    assert backend.initialize({"mode": "subprocess"})
    assert backend._engine_path == launcher
    assert backend._binary_path is None
    assert ("alphamelts", engine_root) in _checked_extra_builds(
        lock,
        install_root,
        repos_root,
    )

    launcher.unlink()
    unavailable = runtime.AlphaMELTSBackend()
    with pytest.raises(ImportError, match="subprocess transport unavailable"):
        unavailable.initialize({"mode": "subprocess"})
    assert ("alphamelts", engine_root) not in _checked_extra_builds(
        lock,
        install_root,
        repos_root,
    )


@pytest.mark.parametrize(
    ("engine", "command"),
    (("magemin", "MAGEMin"), ("alphamelts", "alphamelts")),
    ids=("magemin", "alphamelts"),
)
def test_check_reports_runtime_path_lookup_inside_repos(
    engine: str,
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module

    lock = load_engine_lock(LOCK_PATH)
    repos_root = tmp_path / "repos"
    install_root = tmp_path / "home" / "repos" / "engine-bin"
    path_bin = repos_root / "path-bin"
    path_bin.mkdir(parents=True)
    executable = path_bin / (command + (".exe" if os.name == "nt" else ""))
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_bin))

    assert module._legacy_engine_path_command(engine) == command
    if engine == "magemin":
        from simulator.melt_backend import magemin as runtime

        project_root = repos_root / "runtime-magemin" / "project"
        monkeypatch.setattr(
            runtime,
            "__file__",
            str(project_root / "simulator" / "melt_backend" / "magemin.py"),
        )
        assert runtime.MAGEMinBackend._locate_binary(None) == executable
    else:
        from simulator.melt_backend import installer as runtime

        project_root = repos_root / "runtime-alphamelts" / "project"
        monkeypatch.setattr(
            runtime,
            "__file__",
            str(project_root / "simulator" / "melt_backend" / "installer.py"),
        )
        assert runtime.EngineInstaller()._check_binary()
        assert Path(runtime.shutil.which(command)) == executable

    assert (engine, path_bin) in _checked_extra_builds(
        lock,
        install_root,
        repos_root,
    )


def test_check_reports_thermoengine_library_checkout(
    tmp_path: Path,
) -> None:
    lock = load_engine_lock(LOCK_PATH)
    repos_root = tmp_path / "repos"
    install_root = tmp_path / "home" / "repos" / "engine-bin"
    engine_root = repos_root / "legacy" / "engines" / "thermoengine"
    for artifact in lock.engines["thermoengine"].artifacts:
        path = engine_root / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"legacy ThermoEngine dylib")

    assert ("thermoengine", engine_root) in _checked_extra_builds(
        lock,
        install_root,
        repos_root,
    )


def test_check_classifies_source_artifact_config_and_extra_build_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.engine_local_config as module

    lock_text = LOCK_PATH.read_text(encoding="utf-8").replace(
        'reference_sha256 = "d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85"\n'
        'reference_sha256_source = "alphamelts-app-2.3.1-macos-arm64/alphamelts_macos"\n',
        "",
        1,
    )
    local_lock_path = tmp_path / "engines.lock.toml"
    local_lock_path.write_text(lock_text, encoding="utf-8")
    lock = load_engine_lock(local_lock_path)
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
    alpha_archive = engine_install_path(lock, root, "alphamelts") / alpha_asset.archive_filename
    alpha_archive.write_bytes(b"wrong archive")

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
        library_root=tmp_path / "home-lib",
        host="fake-host",
    )

    codes = {drift.code for drift in report.drifts}
    assert report.host == "fake-host"
    assert "wrong_commit" in codes
    assert "missing_artifact" in codes
    assert "archive_sha256_mismatch" in codes
    assert "artifact_sha256_drift" in codes
    assert {
        drift.severity
        for drift in report.drifts
        if drift.code == "artifact_sha256_drift" and drift.engine == "magemin"
    } == {"warning"}
    assert "extra_build" in codes
    assert "thermoengine_library_link_drift" in codes
    assert "config_paths_drift" in codes
    assert "config_digest_drift" in codes
    assert report.artifact_shas


def test_install_engines_loads_owner_by_path_without_simulator() -> None:
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    code = (
        "import runpy, sys; "
        f"runpy.run_path({str(repo / 'install-engines.py')!r}, "
        "run_name='installer_import_check'); "
        "assert '_regolith_engine_local_config' in sys.modules; "
        "assert 'simulator' not in sys.modules"
    )
    loaded = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert loaded.returncode == 0, loaded.stderr


def test_stdlib_bootstrap_and_check_entrypoint_are_read_only(tmp_path: Path) -> None:
    import hashlib
    import os
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    import_code = (
        "import sys; "
        f"sys.path.insert(0, {str(repo)!r}); "
        "from pathlib import Path; "
        "from engine_local_config_loader import load_engine_local_config; "
        f"config = load_engine_local_config(Path({str(repo)!r})); "
        "config.load_engine_lock(); "
        "assert 'simulator' not in sys.modules"
    )
    imported = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", import_code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    dependencies_code = (
        "import runpy, sys; "
        f"runpy.run_path({str(repo / 'install-dependencies.py')!r}, "
        "run_name='installer_import_check'); "
        "assert 'simulator' not in sys.modules"
    )
    dependencies_imported = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", dependencies_code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dependencies_imported.returncode == 0, dependencies_imported.stderr

    fixture_repo = tmp_path / "repo"
    (fixture_repo / "engines").mkdir(parents=True)
    (fixture_repo / "simulator").mkdir()
    shutil.copyfile(
        repo / "simulator" / "engine_local_config.py",
        fixture_repo / "simulator" / "engine_local_config.py",
    )
    shutil.copyfile(
        repo / "engine_local_config_loader.py",
        fixture_repo / "engine_local_config_loader.py",
    )
    shutil.copyfile(repo / "install-engines.py", fixture_repo / "install-engines.py")
    lock_text = (repo / "engines" / "engines.lock.toml").read_text(encoding="utf-8")
    lock_text = "\n".join(
        line for line in lock_text.splitlines() if not line.startswith("reference_sha256")
    ) + "\n"
    archive_sha = hashlib.sha256(b"fixture archive").hexdigest()
    lock_text = "\n".join(
        f'sha256 = "{archive_sha}"' if line.startswith("sha256 = ") else line
        for line in lock_text.splitlines()
    ) + "\n"
    (fixture_repo / "engines" / "engines.lock.toml").write_text(
        lock_text, encoding="utf-8"
    )
    fixture_lock = load_engine_lock(fixture_repo / "engines" / "engines.lock.toml")

    home = tmp_path / "readonly-home"
    install_root = home / "repos" / "engine-bin"
    library_root = home / "lib"
    library_root.mkdir(parents=True)
    for key, pin in fixture_lock.engines.items():
        engine_dir = engine_install_path(fixture_lock, install_root, key)
        engine_dir.mkdir(parents=True, exist_ok=True)
        if pin.source_kind == "git":
            for artifact in pin.artifacts:
                path = engine_dir / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{key}/{artifact}".encode())
        else:
            asset = artifact_for_platform(pin)
            for artifact in asset.artifacts:
                path = engine_dir / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                content = b"fixture archive" if artifact == asset.archive_filename else b"fixture runtime"
                path.write_bytes(content)

    thermoengine_dir = engine_install_path(fixture_lock, install_root, "thermoengine")
    for artifact in fixture_lock.engines["thermoengine"].artifacts:
        target = thermoengine_dir / artifact
        (library_root / Path(artifact).name).symlink_to(target)

    fixture_config = render_toml(render_locked_config(fixture_lock, install_root))
    (fixture_repo / "engines" / "engines.local.toml").write_text(
        fixture_config, encoding="utf-8"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -C) path=\"$2\"; commit=${path##*/}; printf '%s\\n' \"$commit\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    env = os.environ.copy()
    env.update({"HOME": str(home), "PATH": str(bin_dir)})

    def run_check(entrypoint: Path):
        return subprocess.run(
            [sys.executable, "-I", "-S", str(entrypoint), "--check"],
            cwd=fixture_repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    installed_entrypoint = fixture_repo / "install-engines.py"
    bytecode_enabled_entrypoint = fixture_repo / "install-engines-bytecode-enabled.py"
    installed_text = installed_entrypoint.read_text(encoding="utf-8")
    suppression = "sys.dont_write_bytecode = True"
    assert suppression in installed_text
    enabled_text = installed_text.replace(
        suppression,
        "sys.dont_write_bytecode = False",
        1,
    )
    bytecode_enabled_entrypoint.write_text(enabled_text, encoding="utf-8")
    bytecode_enabled = run_check(bytecode_enabled_entrypoint)
    assert bytecode_enabled.returncode == 0, (
        bytecode_enabled.stdout + bytecode_enabled.stderr
    )
    bytecode_caches = (
        fixture_repo / "__pycache__",
        fixture_repo / "simulator" / "__pycache__",
    )
    assert all(path.is_dir() for path in bytecode_caches)
    assert any(
        path.name.startswith("engine_local_config_loader.")
        and path.suffix == ".pyc"
        for path in bytecode_caches[0].iterdir()
    )
    assert any(
        path.name.startswith("engine_local_config.") and path.suffix == ".pyc"
        for path in bytecode_caches[1].iterdir()
    )
    for bytecode_cache in bytecode_caches:
        shutil.rmtree(bytecode_cache)
    bytecode_enabled_entrypoint.unlink()

    def tree_contents(root: Path) -> dict[str, tuple[str, object]]:
        contents: dict[str, tuple[str, object]] = {}
        for current, directories, files in os.walk(root):
            current_path = Path(current)
            for name in directories:
                path = current_path / name
                contents[str(path.relative_to(root))] = ("directory", None)
            for name in files:
                path = current_path / name
                if path.is_symlink():
                    contents[str(path.relative_to(root))] = ("symlink", os.readlink(path))
                else:
                    contents[str(path.relative_to(root))] = ("file", path.read_bytes())
        return contents

    home_before = tree_contents(home)
    repo_before = tree_contents(fixture_repo)

    writable_check = run_check(installed_entrypoint)
    assert writable_check.returncode == 0, (
        writable_check.stdout + writable_check.stderr
    )
    assert not any(path.exists() for path in bytecode_caches)
    assert tree_contents(home) == home_before
    assert tree_contents(fixture_repo) == repo_before

    for readonly_root in (home, fixture_repo):
        for current, directories, files in os.walk(readonly_root):
            Path(current).chmod(0o555)
            for name in files:
                path = Path(current) / name
                if not path.is_symlink():
                    path.chmod(0o444)

    checked = run_check(installed_entrypoint)
    home_after = tree_contents(home)
    repo_after = tree_contents(fixture_repo)
    for readonly_root in (home, fixture_repo):
        for current, directories, files in os.walk(readonly_root):
            for name in files:
                path = Path(current) / name
                if not path.is_symlink():
                    path.chmod(0o644)
            for name in directories:
                (Path(current) / name).chmod(0o755)
        readonly_root.chmod(0o755)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert home_after == home_before
    assert repo_after == repo_before


@pytest.mark.parametrize(
    "platform_key",
    ("Darwin-arm64", "Darwin-x86_64", "Linux-x86_64", "Windows-AMD64"),
)
def test_base_installer_writes_alpha_config_for_each_locked_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_key: str,
) -> None:
    import dataclasses
    import hashlib
    import importlib.util
    import io
    import zipfile
    import simulator.engine_local_config as engine_config

    repo = Path(__file__).resolve().parents[1]
    installer_path = repo / "install-dependencies.py"
    spec = importlib.util.spec_from_file_location("install_dependencies_test", installer_path)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    lock = load_engine_lock(LOCK_PATH)
    system, machine = platform_key.split("-", 1)
    original_pin = lock.engines["alphamelts"]
    original_asset = original_pin.platform_assets[platform_key]
    runtime_bytes = f"alphaMELTS {platform_key}".encode()
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(original_asset.runtime_artifact, runtime_bytes)
    archive_bytes = archive_buffer.getvalue()
    asset = dataclasses.replace(
        original_asset,
        pin=hashlib.sha256(archive_bytes).hexdigest(),
        reference_sha256=None,
        reference_sha256_source=None,
    )
    platform_assets = dict(original_pin.platform_assets)
    platform_assets[platform_key] = asset
    alpha_pin = dataclasses.replace(original_pin, platform_assets=platform_assets)
    engines = dict(lock.engines)
    engines["alphamelts"] = alpha_pin
    lock = dataclasses.replace(lock, engines=engines)

    root = tmp_path / "home" / "repos" / "engine-bin"
    config_path = tmp_path / "project" / "engines.local.toml"
    monkeypatch.setattr(installer, "load_engine_lock", lambda: lock)
    monkeypatch.setattr(installer, "engine_install_root", lambda: root)
    monkeypatch.setattr(installer.platform, "system", lambda: system)
    monkeypatch.setattr(installer.platform, "machine", lambda: machine)
    monkeypatch.setattr(engine_config, "config_path", lambda: config_path)
    monkeypatch.setattr(
        installer,
        "_download",
        lambda _url, target: target.write_bytes(archive_bytes),
    )

    installer._install_alphamelts()

    config = engine_config.load_config(required=True)
    assert config is not None
    expected_binary = engine_install_path(
        lock,
        root,
        "alphamelts",
        platform_key=platform_key,
    ) / asset.runtime_artifact
    assert config.paths.alphamelts_binary_path == expected_binary
    assert config.identities["alphamelts"].version == "2.3.1"
    assert config.identities["alphamelts"].digest == (
        f"sha256:{hashlib.sha256(runtime_bytes).hexdigest()}"
    )
