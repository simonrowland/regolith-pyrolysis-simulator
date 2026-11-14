#!/usr/bin/env python3
"""Report landed task candidates and offers stranded in the landing ledger."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


TASK_SCRIPT = Path.home() / ".goal-flight/skill/goalflight_task.py"
LEDGER_RELATIVE = Path("docs-private/controller-tools/mb-20260923/pending-landings.txt")
LANDING_GRACE = 1
SHA_PATTERN = r"(?<![0-9a-f])(?=[0-9a-f]{7,40}(?![0-9a-f]))(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9a-f])"
HASH = re.compile(SHA_PATTERN, re.IGNORECASE)
TASK_ID = re.compile(r"\b[bt]-\d+[a-z]?\b", re.IGNORECASE)
SUBJECT_TASK_ID = re.compile(r"(?<![a-z0-9])((?:t-?\d+[a-z]?|b-\d+[a-z]?))(?![a-z0-9])", re.IGNORECASE)
TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})")
BATCH = re.compile(r"\b(?:rides\s+|LANDED\s+)(b\d+)\b", re.IGNORECASE)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result


class GitHistory:
    def __init__(self, root: Path, green: str, since: str | None = None):
        self.root = root
        self.green = self.resolve(green)
        self.ancestors = set(_git(root, "rev-list", self.green).stdout.decode().splitlines())
        self.since = self.resolve(since) if since and self.is_revision(since) else None
        self.since_date = self._parse_since_date(since) if since and self.since is None else None
        if self.since and not self.is_ancestor(self.since, self.green):
            raise ValueError(f"--since commit {since} is not an ancestor of --green {green}")
        if self.since:
            self.since_date = self.commit_date(self.since)
        self.subjects = self._load_subjects()

    def is_revision(self, value: str) -> bool:
        return bool(self._resolve(value))

    def _resolve(self, value: str) -> str | None:
        result = _git(self.root, "rev-parse", "--verify", value + "^{commit}", check=False)
        if result.returncode:
            return None
        return result.stdout.decode().strip()

    def resolve(self, value: str) -> str:
        commit = self._resolve(value)
        if commit is None:
            raise ValueError(f"not a commit in this repository: {value}")
        return commit

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        if descendant == self.green:
            return ancestor in self.ancestors
        return ancestor in set(_git(self.root, "rev-list", descendant).stdout.decode().splitlines())

    def commit_date(self, commit: str) -> dt.datetime:
        value = _git(self.root, "show", "-s", "--format=%cI", commit).stdout.decode().strip()
        return dt.datetime.fromisoformat(value)

    @staticmethod
    def _parse_since_date(value: str) -> dt.datetime:
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"--since must be a commit or ISO date: {value}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed

    def after_since(self, commit: str) -> bool:
        if self.since:
            return commit != self.since and self.is_ancestor(self.since, commit)
        if self.since_date:
            return self.commit_date(commit) >= self.since_date
        return True

    def _load_subjects(self) -> dict[str, str]:
        log = _git(self.root, "log", "--format=%H%x09%s", self.green).stdout.decode()
        result: dict[str, str] = {}
        for row in log.splitlines():
            commit, _, subject = row.partition("\t")
            for match in SUBJECT_TASK_ID.finditer(subject):
                task_id = match.group(1).lower().replace("-", "")
                result.setdefault(task_id, commit)
        return result

    def task_subject_commit(self, task_id: str) -> str | None:
        commit = self.subjects.get(task_id.lower().replace("-", ""))
        return commit if commit and self.after_since(commit) else None

    def changed_files(self, commit: str) -> list[str]:
        parents = _git(self.root, "rev-list", "--parents", "-n", "1", commit).stdout.decode().split()
        if len(parents) > 1:
            args = ["diff", "--no-renames", "--name-only", "-z", parents[1], commit]
        else:
            args = ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit]
        output = _git(self.root, *args).stdout
        return [os.fsdecode(path) for path in output.split(b"\0") if path]

    def _file_bytes(self, commit: str, path: str) -> bytes | None:
        result = _git(self.root, "show", f"{commit}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    def identical_changed_files(self, tip: str) -> tuple[int, int]:
        files = self.changed_files(tip)
        if not files:
            return 0, 0
        matches = sum(self._file_bytes(tip, path) == self._file_bytes(self.green, path) for path in files)
        return matches, len(files)

    def evidence(self, task_id: str, tip_text: str | None) -> dict[str, Any] | None:
        tip = self._resolve(tip_text) if tip_text else None
        if tip and self.after_since(tip) and tip in self.ancestors:
            return {
                "kind": "tip-ancestor",
                "green_commit": tip,
                "confidence": "high",
                "closeable": True,
                "explanation": f"recorded tip {tip} is an ancestor of green",
            }
        subject_commit = self.task_subject_commit(task_id)
        if subject_commit:
            return {
                "kind": "task-subject",
                "green_commit": subject_commit,
                "confidence": "high",
                "closeable": True,
                "explanation": f"green subject names {task_id}",
            }
        if tip and self.after_since(tip):
            matches, total = self.identical_changed_files(tip)
            if total and matches == total:
                return {
                    "kind": "identical-files",
                    "green_commit": self.green,
                    "confidence": "high",
                    "closeable": True,
                    "explanation": f"all {total} changed files from tip {tip} are byte-identical on green",
                }
            if matches:
                return {
                    "kind": "partial-files",
                    "green_commit": self.green,
                    "confidence": "low",
                    "closeable": False,
                    "explanation": f"only {matches} of {total} changed files from tip {tip} match green",
                }
        return None


def recorded_tip(item: dict[str, Any]) -> str | None:
    patterns = (
        re.compile(rf"\bOFFERED\b.*?({SHA_PATTERN})", re.IGNORECASE),
        re.compile(rf"\b(?:LAND|LANDED)\s+({SHA_PATTERN})", re.IGNORECASE),
        re.compile(rf"\bMerge\b[^()\n]*\(({SHA_PATTERN})\)\s+into\s+b\d+\b", re.IGNORECASE),
        re.compile(rf"\bAccepted by train\b[^:\n]*:\s*({SHA_PATTERN})", re.IGNORECASE),
        re.compile(rf"\btip(?:\s+sha)?\s*[:= ]\s*({SHA_PATTERN})", re.IGNORECASE),
    )
    mentions: list[tuple[int, int, str]] = []
    for note_index, note in enumerate(item.get("notes") or []):
        text = note.get("text", "") if isinstance(note, dict) else str(note)
        for pattern in patterns:
            for match in pattern.finditer(text):
                mentions.append((note_index, match.start(), match.group(1)))
    return max(mentions)[2] if mentions else None


def open_close_candidates(items: list[dict[str, Any]], history: GitHistory) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strong: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    for item in items:
        if item.get("done") is True or item.get("kind") not in {"task", "bug"}:
            continue
        task_id = item.get("id")
        if not isinstance(task_id, str):
            continue
        evidence = history.evidence(task_id, recorded_tip(item))
        if not evidence:
            continue
        row = {"id": task_id, **evidence}
        (strong if row["closeable"] else weak).append(row)
    return sorted(strong, key=lambda row: row["id"]), sorted(weak, key=lambda row: row["id"])


def _parse_timestamp(line: str) -> dt.datetime | None:
    match = TIMESTAMP.match(line)
    if not match:
        return None
    try:
        return dt.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def ledger_events(lines: list[str], since_date: dt.datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, tuple[int, str]]]:
    events: list[dict[str, Any]] = []
    last_lines: dict[str, tuple[int, str]] = {}
    for line_number, line in enumerate(lines, 1):
        id_matches = list(TASK_ID.finditer(line))
        ids = [match.group(0).lower() for match in id_matches]
        timestamp = _parse_timestamp(line)
        if not since_date or not timestamp or timestamp >= since_date:
            for task_id in ids:
                last_lines[task_id] = (line_number, line)
        if since_date and timestamp and timestamp < since_date:
            continue
        if not ids:
            continue
        markers = (
            ("INTAKE", re.compile(r"\bINTAKE\b(?!\s+HOLD)")),
            ("BOUNCED", re.compile(r"\bBOUNCED\b", re.IGNORECASE)),
            ("LANDED", re.compile(r"\bLANDED\b")),
        )
        for kind, marker_pattern in markers:
            marker = marker_pattern.search(line)
            if not marker:
                continue
            if kind == "INTAKE":
                scoped_ids = [match.group(0).lower() for match in id_matches if marker.end() <= match.start() <= marker.end() + 300]
            elif kind == "BOUNCED":
                landed_marker = re.search(r"\bLANDED\b", line)
                if landed_marker and landed_marker.start() < marker.start():
                    scoped_ids = [match.group(0).lower() for match in id_matches if marker.end() <= match.start() <= marker.end() + 120]
                else:
                    before = [match for match in id_matches if match.end() <= marker.start()]
                    nearest_before = [max(before, key=lambda match: match.end())] if before else []
                    after = [match for match in id_matches if marker.end() <= match.start() <= marker.end() + 120]
                    scoped_ids = [match.group(0).lower() for match in nearest_before + (after if not nearest_before else [])]
            else:
                scoped_ids = ids
            for task_id in dict.fromkeys(scoped_ids):
                after_id = re.search(re.escape(task_id), line, re.IGNORECASE)
                tail = line[after_id.end():] if after_id else ""
                sha_match = HASH.search(tail)
                sha = sha_match.group(0) if sha_match else None
                landing_match = re.search(rf"\bgreen\s+{SHA_PATTERN}\s*->\s*({SHA_PATTERN})", line, re.IGNORECASE)
                if kind == "LANDED" and not landing_match:
                    landing_match = re.search(rf"\bLANDED\b[^\n]*?{SHA_PATTERN}\s*->\s*({SHA_PATTERN})", line, re.IGNORECASE)
                if kind == "LANDED" and landing_match:
                    sha = landing_match.group(1)
                elif kind == "LANDED" and not sha:
                    marker_hash = HASH.search(line[marker.end():])
                    sha = marker_hash.group(0) if marker_hash else None
                if kind == "LANDED" and not sha:
                    prior_hashes = list(HASH.finditer(line[:marker.start()]))
                    sha = prior_hashes[-1].group(0) if prior_hashes else None
                events.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "timestamp": timestamp,
                        "kind": kind,
                        "task_id": task_id,
                        "sha": sha,
                        "batch": (BATCH.search(line).group(1).lower() if BATCH.search(line) else None),
                        "index": line_number,
                    }
                )
    return events, last_lines


def _content_present(history: GitHistory, task_id: str, tip_text: str | None) -> tuple[bool, str]:
    evidence = history.evidence(task_id, tip_text)
    if evidence and evidence["closeable"]:
        return True, evidence["explanation"]
    if evidence:
        return False, evidence["explanation"]
    if tip_text:
        return False, f"tip {tip_text} has no ancestor, subject, or complete file-content match on green"
    return False, "no usable tip or task-naming green subject was recorded"


def stranded_rows(
    items: list[dict[str, Any]],
    lines: list[str],
    history: GitHistory,
    since_date: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    events, last_lines = ledger_events(lines, since_date)
    landings: dict[int, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "LANDED" or not event["sha"]:
            continue
        # Ledger landing entries commonly abbreviate the green commit SHA.
        commit = history._resolve(event["sha"])
        if commit in history.ancestors:
            landings[event["index"]] = event
    latest_tips = {item.get("id", "").lower(): recorded_tip(item) for item in items if isinstance(item.get("id"), str)}
    result: list[dict[str, Any]] = []
    by_task: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_task.setdefault(event["task_id"], []).append(event)
    for task_id, task_events in by_task.items():
        accepted = [event for event in task_events if event["kind"] == "INTAKE"]
        latest = max(task_events, key=lambda event: event["index"])
        if since_date and latest["timestamp"] and latest["timestamp"] < since_date:
            continue
        if latest["kind"] in {"INTAKE", "BOUNCED"}:
            offer = accepted[-1] if accepted else latest
            tip = offer["sha"] or latest_tips.get(task_id)
            target_batch = offer["batch"] or latest["batch"]
            target_number = int(target_batch[1:]) if target_batch else None
            later_landings = sum(
                event["index"] > offer["index"]
                and (
                    target_number is None
                    or event["batch"] is None
                    or int(event["batch"][1:]) >= target_number
                )
                for event in landings.values()
            )
            if later_landings < LANDING_GRACE:
                continue
            present, proof = _content_present(history, task_id, tip)
            if not present:
                line_number, line = last_lines.get(task_id, (latest["line_number"], latest["line"]))
                result.append(
                    {
                        "task_id": task_id,
                        "line_number": line_number,
                        "line": line,
                        "why": f"latest ledger event is {latest['kind']}; {later_landings} green landing(s) followed the offer; {proof}",
                    }
                )
        for event in task_events:
            if event["kind"] != "LANDED":
                continue
            if since_date and event["timestamp"] and event["timestamp"] < since_date:
                continue
            tip = event["sha"] or latest_tips.get(task_id)
            present, proof = _content_present(history, task_id, tip)
            if not present:
                result.append(
                    {
                        "task_id": task_id,
                        "line_number": event["line_number"],
                        "line": event["line"],
                        "why": f"ledger marks content LANDED but it is absent from green; {proof}",
                    }
                )
    return sorted(result, key=lambda row: (row["line_number"], row["task_id"]))


def _load_store() -> list[dict[str, Any]]:
    if not TASK_SCRIPT.is_file():
        raise RuntimeError(f"task-store CLI not found: {TASK_SCRIPT}")
    result = subprocess.run(
        [sys.executable, str(TASK_SCRIPT), "list", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise RuntimeError("task-store list did not return a JSON array")
    return payload


def _ledger_path(root: Path) -> Path:
    path = root / LEDGER_RELATIVE
    if path.is_file():
        return path
    common_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode().strip())
    fallback = common_dir.parent / LEDGER_RELATIVE
    if fallback.is_file():
        return fallback
    raise RuntimeError(f"landing ledger not found at {path} or {fallback}")


def _render_report(green: str, items: list[dict[str, Any]], strong: list[dict[str, Any]], weak: list[dict[str, Any]], stranded: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in strong:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    lines = [
        f"Green: {green}",
        f"Open task/bug records scanned: {sum(item.get('done') is not True and item.get('kind') in {'task', 'bug'} for item in items)}",
        f"Close candidates: {len(strong)}",
    ]
    for kind in ("tip-ancestor", "task-subject", "identical-files"):
        lines.append(f"  {kind}: {counts.get(kind, 0)}")
    for row in strong:
        lines.append(f"  {row['id']} | {row['kind']} | green commit {row['green_commit']} | confidence {row['confidence']} | {row['explanation']}")
    lines.append(f"Weak content matches refused: {len(weak)}")
    for row in weak:
        lines.append(f"  {row['id']} | {row['kind']} | green commit {row['green_commit']} | confidence {row['confidence']} | {row['explanation']}")
    lines.append(f"Stranded ledger records: {len(stranded)}")
    for row in stranded:
        lines.append(f"  {row['task_id']} | line {row['line_number']} | {row['why']}")
        lines.append(f"    {row['line']}")
    return "\n".join(lines) + "\n"


def _apply_close_candidates(candidates: list[dict[str, Any]], done: Callable[[dict[str, Any]], None]) -> list[str]:
    applied: list[str] = []
    for candidate in candidates:
        if candidate["closeable"] and candidate["kind"] in {"tip-ancestor", "task-subject", "identical-files"}:
            done(candidate)
            applied.append(candidate["id"])
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--green", required=True, help="green commit SHA")
    parser.add_argument("--since", help="limit evidence to commits and ledger events at or after an ISO date or green ancestor SHA")
    parser.add_argument("--apply", action="store_true", help="close only candidates with one of the three complete evidence kinds")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        history = GitHistory(root, args.green, args.since)
        items = _load_store()
        ledger_path = _ledger_path(root)
        ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
        strong, weak = open_close_candidates(items, history)
        stranded = stranded_rows(items, ledger_lines, history, history.since_date)
        sys.stdout.write(_render_report(history.green, items, strong, weak, stranded))
        if args.apply:
            def done(candidate: dict[str, Any]) -> None:
                note = f"{candidate['kind']}: {candidate['explanation']}; green {candidate['green_commit']}"
                result = subprocess.run(
                    [sys.executable, str(TASK_SCRIPT), "done", candidate["id"], "--resolution", "landed-on-green", "--note", note],
                    cwd=root,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"goalflight_task.py done failed for {candidate['id']} (exit {result.returncode})")

            applied = _apply_close_candidates(strong + weak, done)
            print(f"Applied landed-on-green to: {', '.join(applied) if applied else '(none)'}")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"reconcile_landing: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
