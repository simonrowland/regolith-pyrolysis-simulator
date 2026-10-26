from scripts import pytest_partitioned_gate as gate


def test_partitioned_gate_short_circuits_bulk_failure_then_runs_known_red(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def capture(name: str, args: list[str], repo_root) -> int:
        calls.append((name, args))
        return {"bulk": 1, "known-red": 7}.get(name, 0)

    monkeypatch.setattr(gate, "_run_bucket", capture)
    junit = tmp_path / "pr-junit.xml"

    assert gate.main(["--", "-q", "--junitxml", str(junit)]) == 1
    assert [name for name, _ in calls] == ["bulk", "known-red"]

    bulk_args = calls[0][1]
    pytest_module = bulk_args.index("pytest")
    assert bulk_args[pytest_module + 1 : pytest_module + 3] == [
        "-m",
        "not serial and not nightly",
    ]
    assert bulk_args[bulk_args.index("--junitxml") + 1] == str(junit)
    known_red_args = calls[1][1]
    assert "--known-red-only" in known_red_args
    assert f"--junitxml={junit.with_name('pr-junit-known-red.xml')}" in known_red_args


def test_partitioned_gate_collect_only_skips_known_red_execution(
    monkeypatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def capture(name: str, args: list[str], repo_root) -> int:
        calls.append((name, args))
        return 0

    monkeypatch.setattr(gate, "_run_bucket", capture)

    assert gate.main(["--", "--collect-only", "-q"]) == 0
    assert [name for name, _ in calls] == ["bulk", "serial"]
    assert all("--collect-only" in args for _, args in calls)
