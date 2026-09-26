from scripts import pytest_partitioned_gate as gate


def test_partitioned_gate_applies_pr_marker_to_both_buckets(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def capture(name: str, args: list[str], repo_root) -> int:
        calls.append((name, args))
        return 0

    monkeypatch.setattr(gate, "_run_bucket", capture)

    assert gate.main(["--", "--collect-only", "-q"]) == 0

    marker_expressions = {
        name: args[[index for index, value in enumerate(args) if value == "-m"][-1] + 1]
        for name, args in calls
    }
    assert marker_expressions == {
        "bulk": "not serial and not nightly",
        "serial": "serial and not nightly",
    }
