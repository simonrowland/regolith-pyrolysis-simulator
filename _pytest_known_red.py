from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from _pytest_loadgroup_order import strip_loadgroup_suffix

_DEFAULT_ROSTER = Path(__file__).resolve().parent / "tests" / "known_red.toml"
_REQUIRED_FIELDS = {"node", "ticket", "tier", "reason", "since"}
_TICKET_RE = re.compile(r"[a-z]+-\d+\Z")
_SHA_RE = re.compile(r"[0-9a-f]{7,40}\Z")

_MODE_KEY: pytest.StashKey[bool] = pytest.StashKey()
_DESELECTED_KEY: pytest.StashKey[tuple[str, ...]] = pytest.StashKey()
_SELECTED_KEY: pytest.StashKey[tuple[str, ...]] = pytest.StashKey()
_STALE_KEY: pytest.StashKey[tuple[str, ...]] = pytest.StashKey()
_XDIST_COLLECTION_IDS: tuple[str, ...] | None = None
_XDIST_REPORT_KEY = "known_red_quarantine"
_XDIST_WORKER_REPORTS: list[dict[str, Any]] = []


@dataclass(frozen=True)
class _RosterEntry:
    node: str
    ticket: str
    tier: str
    reason: str
    since: str


def _partition_roster_nodes(
    roster_nodes: tuple[str, ...], collected_nodeids: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    collected = {strip_loadgroup_suffix(nodeid) for nodeid in collected_nodeids}
    selected = tuple(node for node in roster_nodes if node in collected)
    stale = tuple(node for node in roster_nodes if node not in collected)
    return selected, stale


def _roster_error(path: Path, message: str) -> pytest.UsageError:
    return pytest.UsageError(f"{path}: {message}")


def _load_roster(path: Path) -> dict[str, _RosterEntry]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _roster_error(path, f"cannot read known-red roster: {exc}") from exc

    if set(payload) != {"test"} or not isinstance(payload.get("test"), list):
        raise _roster_error(path, "expected only [[test]] entries")

    entries: dict[str, _RosterEntry] = {}
    for index, row in enumerate(payload["test"], start=1):
        if not isinstance(row, dict):
            raise _roster_error(path, f"test row {index} must be a table")

        missing = _REQUIRED_FIELDS - row.keys()
        extra = row.keys() - _REQUIRED_FIELDS
        if missing:
            field = sorted(missing)[0]
            raise _roster_error(
                path,
                f"test row {index} is missing required field '{field}'",
            )
        if extra:
            field = sorted(extra)[0]
            raise _roster_error(path, f"test row {index} has unknown field '{field}'")

        values: dict[str, str] = {}
        for field in sorted(_REQUIRED_FIELDS):
            value = row[field]
            if not isinstance(value, str) or not value.strip():
                raise _roster_error(
                    path,
                    f"test row {index} field '{field}' must be a non-empty string",
                )
            if value != value.strip():
                raise _roster_error(
                    path,
                    f"test row {index} field '{field}' has surrounding whitespace",
                )
            values[field] = value

        if values["tier"] not in {"pr", "nightly"}:
            raise _roster_error(
                path,
                f"test row {index} tier must be 'pr' or 'nightly'",
            )
        if "\n" in values["reason"] or "\r" in values["reason"]:
            raise _roster_error(path, f"test row {index} reason must be one line")
        if not _TICKET_RE.fullmatch(values["ticket"]):
            raise _roster_error(path, f"test row {index} ticket is not a ticket id")
        if not _SHA_RE.fullmatch(values["since"]):
            raise _roster_error(path, f"test row {index} since must be a git SHA")

        entry = _RosterEntry(**values)
        if entry.node in entries:
            raise _roster_error(path, f"duplicate node '{entry.node}'")
        entries[entry.node] = entry

    return entries


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("known-red")
    group.addoption(
        "--known-red-only",
        action="store_true",
        default=False,
        help="run only tests listed in tests/known_red.toml as strict xfails",
    )
    group.addoption(
        "--known-red-roster",
        type=Path,
        default=None,
        metavar="PATH",
        help="use an alternate roster (for the quarantine contract tests)",
    )


def pytest_configure(config: pytest.Config) -> None:
    del config
    global _XDIST_COLLECTION_IDS, _XDIST_WORKER_REPORTS
    _XDIST_COLLECTION_IDS = None
    _XDIST_WORKER_REPORTS = []


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del session
    roster_path = config.getoption("known_red_roster") or _DEFAULT_ROSTER
    entries = _load_roster(roster_path)
    known_red_only = config.getoption("known_red_only")
    collected_nodeids = tuple(strip_loadgroup_suffix(item.nodeid) for item in items)
    _, stale_nodes = _partition_roster_nodes(tuple(entries), collected_nodeids)

    if known_red_only:
        selected = [
            item for item in items if strip_loadgroup_suffix(item.nodeid) in entries
        ]
        deselected = [
            item for item in items if strip_loadgroup_suffix(item.nodeid) not in entries
        ]
        stale = stale_nodes
        for item in selected:
            item.add_marker(
                pytest.mark.xfail(
                    strict=True,
                    reason=entries[strip_loadgroup_suffix(item.nodeid)].ticket,
                )
            )
    else:
        selected = [
            item for item in items if strip_loadgroup_suffix(item.nodeid) not in entries
        ]
        deselected = [
            item for item in items if strip_loadgroup_suffix(item.nodeid) in entries
        ]
        stale = ()

    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)

    config.stash[_MODE_KEY] = known_red_only
    config.stash[_DESELECTED_KEY] = tuple(
        strip_loadgroup_suffix(item.nodeid) for item in deselected
    )
    config.stash[_SELECTED_KEY] = tuple(
        strip_loadgroup_suffix(item.nodeid) for item in selected
    )
    config.stash[_STALE_KEY] = stale


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: object, ids: list[str]) -> None:
    del node
    global _XDIST_COLLECTION_IDS
    _XDIST_COLLECTION_IDS = tuple(strip_loadgroup_suffix(nodeid) for nodeid in ids)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    config = session.config
    workeroutput = getattr(config, "workeroutput", None)
    if not isinstance(workeroutput, dict) or _MODE_KEY not in config.stash:
        return
    workeroutput[_XDIST_REPORT_KEY] = {
        "selected": list(config.stash[_SELECTED_KEY]),
        "deselected": list(config.stash[_DESELECTED_KEY]),
        "stale": list(config.stash[_STALE_KEY]),
    }


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: object, error: object) -> None:
    del error
    workeroutput = getattr(node, "workeroutput", None)
    if not isinstance(workeroutput, dict):
        return
    report = workeroutput.get(_XDIST_REPORT_KEY)
    if isinstance(report, dict):
        _XDIST_WORKER_REPORTS.append(report)


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    del exitstatus
    if _XDIST_WORKER_REPORTS:
        known_red_only = config.getoption("known_red_only")
        selected = tuple(
            sorted(
                {
                    node
                    for report in _XDIST_WORKER_REPORTS
                    for node in report["selected"]
                }
            )
        )
        deselected = tuple(
            sorted(
                {
                    node
                    for report in _XDIST_WORKER_REPORTS
                    for node in report["deselected"]
                }
            )
        )
        stale = tuple(
            sorted(
                {
                    node
                    for report in _XDIST_WORKER_REPORTS
                    for node in report["stale"]
                }
            )
        )
    elif _MODE_KEY in config.stash:
        known_red_only = config.stash[_MODE_KEY]
        selected = config.stash[_SELECTED_KEY]
        deselected = config.stash[_DESELECTED_KEY]
        stale = config.stash[_STALE_KEY]
    else:
        roster_path = config.getoption("known_red_roster") or _DEFAULT_ROSTER
        try:
            entries = _load_roster(roster_path)
        except pytest.UsageError:
            return
        known_red_only = config.getoption("known_red_only")
        if known_red_only:
            selected, stale = _partition_roster_nodes(
                tuple(entries), _XDIST_COLLECTION_IDS or ()
            )
            deselected = ()
        else:
            selected = ()
            deselected = ()
            stale = ()

    if known_red_only:
        terminalreporter.write_line(
            "Known-red-only: "
            f"{len(selected)} selected: {', '.join(selected) if selected else '(none)'}"
        )
        for node in stale:
            terminalreporter.write_line(f"Known-red roster stale node: {node}")
        return

    terminalreporter.write_line(
        "Known-red quarantine: "
        f"{len(deselected)} deselected: "
        f"{', '.join(deselected) if deselected else '(none)'}"
    )
