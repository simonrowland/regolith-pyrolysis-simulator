#!/usr/bin/env python3
"""Report landing evidence and close tasks from the single offer being landed.

Usage for the train: on landing offer X, run
``reconcile_landing.py close-landed --offer X --green <new green>``. ``X`` may
be the offer body file or the id of its merge-request envelope. The historical
census is report-only; only the live command reads ``Closes:`` declarations.
"""

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
# Accept valid all-numeric SHA prefixes without matching digits inside task or branch IDs.
SHA_PATTERN = r"(?<![0-9a-z_-])(?=[0-9a-f]{7,40}(?![0-9a-f]))[0-9a-f]{7,40}(?![0-9a-f])"
TASK_ID = re.compile(r"\b(?:t-?\d+[a-z]?|b-\d+[a-z]?)\b", re.IGNORECASE)
TASK_ID_TEXT = r"(?:t-?\d+[a-z]?|b-\d+[a-z]?)"
SUBJECT_TASK_ID = re.compile(r"(?<![a-z0-9])((?:t-?\d+[a-z]?|b-\d+[a-z]?))(?![a-z0-9])", re.IGNORECASE)
TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+~?(\d{2}:\d{2})")
BATCH = re.compile(r"\b(?:rides\s+|LANDED\s+)(b\d+)\b", re.IGNORECASE)
PARTIAL_NOTE = re.compile(
    r"\b(?:remaining|still[- ]open|stays? open|follow[- ]?up|blocked|reopened|supersed(?:e|ed|es|ing))\b",
    re.IGNORECASE,
)
GREEN_TRANSITION = re.compile(
    rf"\bLANDED\b[^\n]*?\bgreen\s+({SHA_PATTERN})\s*->\s*({SHA_PATTERN})",
    re.IGNORECASE,
)


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
        self._resolved: dict[str, str | None] = {}
        self._green_blobs: dict[str, str] | None = None
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
        if value in self._resolved:
            return self._resolved[value]
        result = _git(self.root, "rev-parse", "--verify", value + "^{commit}", check=False)
        commit = result.stdout.decode().strip() if result.returncode == 0 else None
        self._resolved[value] = commit
        return commit

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
        base_result = _git(self.root, "merge-base", self.green, commit, check=False)
        if base_result.returncode:
            return []
        base = base_result.stdout.decode().strip()
        output = _git(self.root, "diff", "--no-renames", "--name-only", "-z", base, commit).stdout
        return [os.fsdecode(path) for path in output.split(b"\0") if path]

    def _tree_blobs(self, commit: str) -> dict[str, str]:
        output = _git(self.root, "ls-tree", "-r", "-z", "--full-tree", commit).stdout
        blobs: dict[str, str] = {}
        for entry in output.split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            _, kind, object_id = metadata.decode().split()
            if kind == "blob":
                blobs[os.fsdecode(path)] = object_id
        return blobs

    def _green_tree_blobs(self) -> dict[str, str]:
        if self._green_blobs is None:
            self._green_blobs = self._tree_blobs(self.green)
        return self._green_blobs

    def identical_range_files(self, tip: str) -> tuple[int, int]:
        files = self.changed_files(tip)
        if not files:
            return 0, 0
        # Equal Git blob IDs mean the file bytes are equal, without spawning one show per path.
        tip_blobs = self._tree_blobs(tip)
        green_blobs = self._green_tree_blobs()
        matches = sum(tip_blobs.get(path) == green_blobs.get(path) for path in files)
        return matches, len(files)

    def evidence(self, task_id: str, tip_text: str | None) -> dict[str, Any] | None:
        tip = self._resolve(tip_text) if tip_text else None
        if tip and self.after_since(tip) and tip in self.ancestors:
            return {
                "kind": "tip-ancestor",
                "green_commit": tip,
                "confidence": "high",
                "closeable": False,
                "explanation": f"recorded tip {tip} is an ancestor of green",
            }
        if tip and self.after_since(tip):
            matches, total = self.identical_range_files(tip)
            if total and matches == total:
                return {
                    "kind": "identical-range",
                    "green_commit": self.green,
                    "confidence": "high",
                    "closeable": False,
                    "explanation": f"all {total} files in the offer range from tip {tip} are byte-identical on green",
                    "matched_files": matches,
                    "changed_files": total,
                }
            if matches:
                return {
                    "kind": "partial-files",
                    "green_commit": self.green,
                    "confidence": "low",
                    "closeable": False,
                    "explanation": f"only {matches} of {total} changed files from tip {tip} match green",
                    "matched_files": matches,
                    "changed_files": total,
                }
        return None


def recorded_tip(item: dict[str, Any]) -> str | None:
    patterns = (
        re.compile(rf"\bOFFERED\b.*?({SHA_PATTERN})", re.IGNORECASE),
        re.compile(rf"\bMerge\b[^()\n]*\(({SHA_PATTERN})\)\s+into\s+b\d+\b", re.IGNORECASE),
        re.compile(rf"\bAccepted by train\b[^:\n]*:\s*({SHA_PATTERN})", re.IGNORECASE),
    )
    mentions: list[tuple[int, int, str]] = []
    for note_index, note in enumerate(item.get("notes") or []):
        text = note.get("text", "") if isinstance(note, dict) else str(note)
        for pattern in patterns:
            for match in pattern.finditer(text):
                mentions.append((note_index, match.start(), match.group(1)))
    return max(mentions)[2] if mentions else None


def _item_notes(item: dict[str, Any]) -> list[tuple[dt.datetime | None, str]]:
    result: list[tuple[dt.datetime | None, str]] = []
    for note in item.get("notes") or []:
        if isinstance(note, dict):
            text = str(note.get("text", ""))
            timestamp = note.get("at")
            try:
                noted_at = dt.datetime.fromisoformat(str(timestamp)) if timestamp else None
            except ValueError:
                noted_at = None
        else:
            text = str(note)
            noted_at = None
        result.append((noted_at, text))
    return result


def _partial_note(item: dict[str, Any], after: dt.datetime | None = None) -> str | None:
    for noted_at, text in reversed(_item_notes(item)):
        if PARTIAL_NOTE.search(text) and (after is None or noted_at is None or noted_at >= after):
            return text
    return None


def _unbound_sha(item: dict[str, Any]) -> str | None:
    offered = recorded_tip(item)
    for _, text in reversed(_item_notes(item)):
        for match in re.finditer(SHA_PATTERN, text, re.IGNORECASE):
            if not offered or match.group(0).lower() != offered.lower():
                return match.group(0)
    return None


OFFER_KINDS = {"OFFER", "RE-OFFER", "INTAKE"}


def _task_offer_evidence(task_id: str, task_events: list[dict[str, Any]], history: GitHistory) -> dict[str, Any] | None:
    for event in reversed(task_events):
        if event["kind"] not in OFFER_KINDS | {"LANDED"} or not event.get("tip"):
            continue
        tip = history._resolve(event["tip"])
        if not tip:
            continue
        evidence = history.evidence(task_id, tip)
        if not evidence:
            continue
        if tip in history.ancestors:
            return {
                "kind": "task-bound offer landed",
                "green_commit": tip,
                "confidence": "report-only",
                "closeable": False,
                "explanation": f"ledger {event['kind']} binds {task_id} to offer tip {tip}, an ancestor of green",
            }
        return evidence
    return None


def _evidence_row(task_id: str, evidence: dict[str, Any], partial: str | None = None) -> dict[str, Any]:
    row = {"id": task_id, **evidence}
    row["closeable"] = False
    row["evidence_kind"] = row["kind"]
    if partial:
        row["partial"] = "partial: remaining work recorded"
        excerpt = " ".join(partial.split())[:240]
        row["explanation"] += f"; partial: remaining work recorded ({excerpt})"
    return row


def report_candidates(
    items: list[dict[str, Any]],
    history: GitHistory,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    task_events: dict[str, list[dict[str, Any]]] = {}
    for event in events or []:
        task_events.setdefault(event["task_id"], []).append(event)
    report_only: list[dict[str, Any]] = []
    for item in items:
        if item.get("done") is True or item.get("kind") not in {"task", "bug"}:
            continue
        task_id = item.get("id")
        if not isinstance(task_id, str):
            continue
        normalized = _normalize_task_id(task_id)
        relevant_events = task_events.get(normalized, [])
        last_landing = max(
            (event["timestamp"] for event in relevant_events if event["kind"] == "LANDED" and event["timestamp"]),
            default=None,
        )
        partial = _partial_note(item, last_landing)
        evidence = _task_offer_evidence(task_id, relevant_events, history)
        if not evidence:
            evidence = history.evidence(task_id, recorded_tip(item))
        if not evidence:
            sha = _unbound_sha(item)
            if sha:
                evidence = {
                    "kind": "unbound sha mention",
                    "green_commit": history.green,
                    "confidence": "report-only",
                    "closeable": False,
                    "explanation": f"note mentions {sha} without binding it to this task's offered content",
                }
        if not evidence:
            subject_commit = history.task_subject_commit(task_id)
            if subject_commit:
                evidence = {
                    "kind": "task-subject",
                    "green_commit": subject_commit,
                    "confidence": "report-only",
                    "closeable": False,
                    "explanation": f"green subject mentions {task_id}; subject mention only, offered content is unverified",
                }
        if not evidence:
            if not partial:
                continue
            evidence = {
                "kind": "partial: remaining work recorded",
                "green_commit": history.green,
                "confidence": "report-only",
                "closeable": False,
                "explanation": "explicit remaining-work note",
            }
            partial = None
        report_only.append(_evidence_row(task_id, evidence, partial))
    return sorted(report_only, key=lambda row: (row["id"], row["kind"]))


def _parse_timestamp(line: str) -> dt.datetime | None:
    match = TIMESTAMP.match(line)
    if not match:
        return None
    try:
        return dt.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _normalize_task_id(task_id: str) -> str:
    match = re.fullmatch(r"([tb])-?(\d+)([a-z]?)", task_id, re.IGNORECASE)
    if match:
        return f"{match.group(1).lower()}-{match.group(2)}{match.group(3).lower()}"
    return task_id.lower()


def ledger_events(lines: list[str], since_date: dt.datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, tuple[int, str]]]:
    events: list[dict[str, Any]] = []
    last_lines: dict[str, tuple[int, str]] = {}
    event_prefix = re.compile(
        r"^(?:\d{4}-\d{2}-\d{2}\s+(?:~?\d{2}:\d{2})\s+)?(?P<kind>RE-OFFER|OFFER|INTAKE|BOUNCED|LANDED)\b",
        re.IGNORECASE,
    )
    green_landing = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}\s+.*?\bGREEN\s+{SHA_PATTERN}\s+LANDED\b", re.IGNORECASE)

    for line_number, line in enumerate(lines, 1):
        id_matches = list(TASK_ID.finditer(line))
        ids = [_normalize_task_id(match.group(0)) for match in id_matches]
        timestamp = _parse_timestamp(line)
        if not since_date or not timestamp or timestamp >= since_date:
            for task_id in ids:
                last_lines[task_id] = (line_number, line)
        if since_date and timestamp and timestamp < since_date:
            continue
        if not ids:
            continue

        prefix = event_prefix.match(line)
        kind_match = prefix.group("kind").upper() if prefix else None
        if not kind_match and green_landing.match(line):
            kind_match = "LANDED"
        if not kind_match or (kind_match == "INTAKE" and re.match(r"\s+HOLD\b", line[prefix.end():], re.IGNORECASE)):
            continue
        marker = re.search(rf"\b{re.escape(kind_match)}\b", line, re.IGNORECASE)
        if not marker:
            continue
        if kind_match in OFFER_KINDS:
            tail_start = marker.end()
            while tail_start < len(line) and (line[tail_start].isspace() or line[tail_start] == ":"):
                tail_start += 1
            tokens = list(re.finditer(r"[^\s:(),]+", line[tail_start:]))[:2]
            token_ranges = [(tail_start + token.start(), tail_start + token.end()) for token in tokens]
            scoped_matches = [
                match for match in id_matches
                if any(start <= match.start() and match.end() <= end for start, end in token_ranges)
            ]
        elif kind_match == "BOUNCED":
            following = [match for match in id_matches if marker.end() <= match.start() <= marker.end() + 120]
            before = [match for match in id_matches if match.end() <= marker.start()]
            scoped_matches = following[:1] or (before[-1:] if before else [])
        else:
            scoped_matches = [match for match in id_matches if match.start() >= marker.end()]
        scoped_by_id = {_normalize_task_id(match.group(0)): match for match in scoped_matches}
        transition = GREEN_TRANSITION.search(line) if kind_match == "LANDED" else None
        for task_id, task_match in scoped_by_id.items():
            tail = line[task_match.end():]
            if kind_match in OFFER_KINDS:
                offer_match = re.search(rf"({SHA_PATTERN})", tail, re.IGNORECASE)
            elif kind_match == "LANDED":
                offer_match = re.match(rf"\s*(?:[=+(:]\s*)?({SHA_PATTERN})", tail, re.IGNORECASE)
            else:
                offer_match = None
            event = {
                "line_number": line_number,
                "line": line,
                "timestamp": timestamp,
                "kind": kind_match,
                "task_id": task_id,
                "tip": offer_match.group(1) if offer_match else None,
                "green_before": transition.group(1) if transition else None,
                "green_after": transition.group(2) if transition else None,
                "batch": (BATCH.search(line).group(1).lower() if BATCH.search(line) else None),
                "index": line_number,
            }
            events.append(event)
    return events, last_lines


def _content_present(history: GitHistory, task_id: str, tip_text: str | None) -> tuple[bool | None, str]:
    if not tip_text:
        return None, "no offered tip recorded"
    tip = history._resolve(tip_text)
    if tip is None:
        return None, f"offered tip {tip_text} does not resolve to a commit in this repo"
    evidence = history.evidence(task_id, tip)
    if evidence and evidence["kind"] in {"tip-ancestor", "identical-range"}:
        return True, evidence["explanation"]
    if evidence:
        return False, evidence["explanation"]
    return False, f"offered tip {tip_text} has no ancestor or complete offer-range match on green"


def _offer_tip_for_event(
    task_id: str,
    event: dict[str, Any],
    task_events: list[dict[str, Any]],
    latest_tips: dict[str, str | None],
) -> str | None:
    if event.get("tip"):
        return event["tip"]
    offers = [row for row in task_events if row["kind"] in OFFER_KINDS and row["index"] <= event["index"]]
    if offers:
        return offers[-1]["tip"]
    return latest_tips.get(_normalize_task_id(task_id))


def stranded_rows(
    items: list[dict[str, Any]],
    lines: list[str],
    history: GitHistory,
    since_date: dt.datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    events, last_lines = ledger_events(lines, since_date)
    landings: dict[int, dict[str, Any]] = {}
    unresolved_hashes: set[str] = set()
    for event in events:
        for field in ("tip", "green_before", "green_after"):
            sha = event[field]
            if sha and history._resolve(sha) is None:
                unresolved_hashes.add(sha)
        if event["kind"] == "LANDED" and event["green_after"]:
            # Ledger landing entries commonly abbreviate the destination green SHA.
            commit = history._resolve(event["green_after"])
            if commit in history.ancestors:
                landings[event["index"]] = event
    latest_tips = {_normalize_task_id(item["id"]): recorded_tip(item) for item in items if isinstance(item.get("id"), str)}
    for tip in latest_tips.values():
        if tip and history._resolve(tip) is None:
            unresolved_hashes.add(tip)
    result: list[dict[str, Any]] = []
    by_task: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_task.setdefault(event["task_id"], []).append(event)
    for task_id, task_events in by_task.items():
        accepted = [event for event in task_events if event["kind"] in OFFER_KINDS]
        latest = max(task_events, key=lambda event: event["index"])
        if since_date and latest["timestamp"] and latest["timestamp"] < since_date:
            continue
        if latest["kind"] in OFFER_KINDS | {"BOUNCED"}:
            offer = accepted[-1] if accepted else latest
            tip = _offer_tip_for_event(task_id, offer, task_events, latest_tips)
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
            if present is not True:
                line_number, line = last_lines.get(task_id, (latest["line_number"], latest["line"]))
                result.append(
                    {
                        "task_id": task_id,
                        "category": "unknown" if present is None else "stranded",
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
            tip = _offer_tip_for_event(task_id, event, task_events, latest_tips)
            present, proof = _content_present(history, task_id, tip)
            if present is not True:
                result.append(
                    {
                        "task_id": task_id,
                        "category": "unknown" if present is None else "stranded",
                        "line_number": event["line_number"],
                        "line": event["line"],
                        "why": f"ledger marks content LANDED; {proof}",
                    }
                )
    return sorted(result, key=lambda row: (row["line_number"], row["task_id"])), len(unresolved_hashes)


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


CLOSES_LINE = re.compile(rf"^Closes:\s+({TASK_ID_TEXT}(?:,\s*{TASK_ID_TEXT})*)\s*$")
OFFER_SHA_LINE = re.compile(rf"^Offer sha:\s+({SHA_PATTERN})(?:\s+.*)?$", re.IGNORECASE)
TASK_NOTE_HEADING = re.compile(rf"^\s*{TASK_ID_TEXT}\s+notes?\s*:", re.IGNORECASE)


def _offer_declarations(body: str) -> tuple[list[str], list[dict[str, str]]]:
    declared: list[str] = []
    refusals: list[dict[str, str]] = []
    fence: tuple[str, int] | None = None
    in_task_note = False
    for line_number, line in enumerate(body.splitlines(), 1):
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            continue
        if fence or stripped.startswith(">"):
            continue
        if TASK_NOTE_HEADING.match(line):
            in_task_note = True
        if in_task_note:
            continue
        if not line.startswith("Closes:"):
            continue
        match = CLOSES_LINE.fullmatch(line)
        if not match:
            refusals.append({"type": "invalid-closes-declaration", "line": str(line_number), "message": "Closes line must contain exact task ids separated by commas"})
            continue
        if declared:
            refusals.append({"type": "multiple-closes-declarations", "line": str(line_number), "message": "offer must contain one Closes line"})
            return [], refusals
        declared.extend(value.strip() for value in match.group(1).split(","))
    return list(dict.fromkeys(declared)), refusals


def _offer_tip(body: str) -> tuple[str | None, str | None]:
    tips = [match.group(1) for line in body.splitlines() if (match := OFFER_SHA_LINE.fullmatch(line))]
    unique = list(dict.fromkeys(tips))
    if not unique:
        return None, "offer has no anchored Offer sha declaration"
    if len(unique) != 1:
        return None, "offer has multiple distinct Offer sha declarations"
    return unique[0], None


def _merge_request_body(reference: str, root: Path) -> tuple[str, str]:
    requested_path = Path(reference).expanduser()
    paths = [requested_path]
    if not requested_path.is_absolute():
        paths.append(root / requested_path)
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            return text, str(path)
        if isinstance(envelope, dict) and envelope.get("type") == "merge-request":
            payload = envelope.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                return payload["text"], str(envelope.get("id", path))
            raise RuntimeError("invalid-offer-envelope: merge-request payload has no text body")
        return text, str(path)

    messages = Path.home() / ".goal-flight/messages"
    if not messages.is_dir():
        raise RuntimeError(f"offer file or envelope not found: {reference}")
    matches: list[dict[str, Any]] = []
    for stream_path in messages.iterdir():
        if stream_path.suffix != ".jsonl" or not stream_path.is_file():
            continue
        with stream_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(envelope, dict) and envelope.get("id") == reference and envelope.get("type") == "merge-request":
                    matches.append(envelope)
    if len(matches) != 1:
        reason = "not found" if not matches else "ambiguous"
        raise RuntimeError(f"offer envelope {reason}: {reference}")
    payload = matches[0].get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise RuntimeError("invalid-offer-envelope: merge-request payload has no text body")
    return payload["text"], str(matches[0]["id"])


def _offer_close_candidates(
    body: str,
    history: GitHistory,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    targets, refusals = _offer_declarations(body)
    if refusals:
        return [], refusals, targets
    if not targets:
        refusals.append({"type": "no-closes-declaration", "message": "offer contains no anchored Closes declaration"})
        return [], refusals, []

    tip_text, tip_error = _offer_tip(body)
    if tip_error:
        refusals.append({"type": "invalid-offer-tip", "message": tip_error})
        return [], refusals, targets
    tip = history._resolve(tip_text or "")
    if tip is None:
        refusals.append({"type": "unresolved-offer-tip", "message": f"offer tip {tip_text} does not resolve to a commit in this repository"})
        return [], refusals, targets
    if tip not in history.ancestors:
        refusals.append({"type": "offer-tip-not-ancestor", "message": f"offer tip {tip} is not an ancestor of green {history.green}"})
        return [], refusals, targets

    items_by_exact_id = {
        item["id"]: item
        for item in items
        if isinstance(item.get("id"), str) and item.get("kind") in {"task", "bug"}
    }
    candidates: list[dict[str, Any]] = []
    for target in targets:
        item = items_by_exact_id.get(target)
        if item is None:
            refusals.append({"id": target, "type": "unknown-task-id", "message": f"no exact task-store id matches {target}"})
        elif item.get("done") is True:
            refusals.append({"id": target, "type": "already-done", "message": f"task {target} is already done"})
        else:
            candidates.append(
                {
                    "id": target,
                    "kind": "offer-closes",
                    "tip": tip,
                    "green_commit": history.green,
                    "declaration": target,
                    "explanation": f"this offer at {tip} declares Closes: {target}",
                }
            )
    return candidates, refusals, targets


def _render_report(
    green: str,
    items: list[dict[str, Any]],
    report_only: list[dict[str, Any]],
    stranded: list[dict[str, Any]],
    unresolved_hashes: int,
) -> str:
    evidence_kinds = (
        "task-bound offer landed",
        "tip-ancestor",
        "identical-range",
        "partial-files",
        "task-subject",
        "unbound sha mention",
        "partial: remaining work recorded",
    )
    report_counts = {kind: sum(row["evidence_kind"] == kind for row in report_only) for kind in evidence_kinds}
    unknown_count = sum(row["category"] == "unknown" for row in stranded)
    stranded_count = sum(row["category"] == "stranded" for row in stranded)
    lines = [
        f"Green: {green}",
        f"Open task/bug records scanned: {sum(item.get('done') is not True and item.get('kind') in {'task', 'bug'} for item in items)}",
        *(f"Report-only {kind}: {report_counts[kind]}" for kind in evidence_kinds),
        f"Partial: remaining work recorded: {sum(bool(row.get('partial')) or row['evidence_kind'] == 'partial: remaining work recorded' for row in report_only)}",
        f"Unknown: {unknown_count}",
        f"Stranded: {stranded_count}",
        f"Unresolved hashes ignored: {unresolved_hashes}",
        "Offer authors: add one Closes: <task-id>[, <task-id>...] line per offer; at landing, pass only that offer to close-landed.",
    ]
    lines.append(f"Report-only evidence: {len(report_only)}")
    for row in report_only:
        partial = f" | {row['partial']}" if row.get("partial") else ""
        lines.append(f"  {row['id']} | {row['evidence_kind']}{partial} | green commit {row['green_commit']} | confidence {row['confidence']} | {row['explanation']}")
    for row in stranded:
        lines.append(f"  {row['category']} | {row['task_id']} | line {row['line_number']} | {row['why']}")
        lines.append(f"    {row['line']}")
    return "\n".join(lines) + "\n"


def _apply_offer_closes(
    candidates: list[dict[str, Any]],
    done: Callable[[dict[str, Any]], None],
) -> tuple[list[str], list[dict[str, str]]]:
    applied: list[str] = []
    refusals: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            done(candidate)
        except (OSError, RuntimeError) as exc:
            refusals.append({"id": candidate["id"], "type": "owner-cli-refusal", "message": str(exc)})
        else:
            applied.append(candidate["id"])
    return applied, refusals


def _render_close_landed(
    offer_label: str,
    green: str,
    declarations: list[str],
    applied: list[str],
    refusals: list[dict[str, str]],
) -> str:
    lines = [f"Offer: {offer_label}", f"Green: {green}"]
    lines.append(f"Declared closes: {', '.join(declarations) if declarations else '(none)'}")
    lines.append(f"Applied landed-on-green to: {', '.join(applied) if applied else '(none)'}")
    for refusal in refusals:
        identity = f" {refusal['id']}" if refusal.get("id") else ""
        line = f"Refusal{identity} [{refusal['type']}]: {refusal['message']}"
        if refusal.get("line"):
            line += f" (offer line {refusal['line']})"
        lines.append(line)
    lines.append(f"Refusal count: {len(refusals)}")
    return "\n".join(lines) + "\n"


def _run_close_landed(offer_ref: str, green: str, root: Path) -> tuple[str, int]:
    body, offer_label = _merge_request_body(offer_ref, root)
    history = GitHistory(root, green)
    items = _load_store()
    candidates, refusals, declarations = _offer_close_candidates(body, history, items)

    def done(candidate: dict[str, Any]) -> None:
        note = f"offer {offer_label} at {candidate['tip']} declares Closes: {candidate['declaration']}; green {candidate['green_commit']}"
        result = subprocess.run(
            [sys.executable, str(TASK_SCRIPT), "done", candidate["id"], "--resolution", "landed-on-green", "--note", note],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise RuntimeError(message)

    applied, owner_refusals = _apply_offer_closes(candidates, done)
    refusals.extend(owner_refusals)
    return _render_close_landed(offer_label, history.green, declarations, applied, refusals), 1 if refusals else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--green", dest="census_green", help="green commit SHA for the historical census")
    parser.add_argument("--since", help="limit evidence to commits and ledger events at or after an ISO date or green ancestor SHA")
    commands = parser.add_subparsers(dest="command")
    close_landed = commands.add_parser("close-landed", help="close tasks declared by one offer being landed")
    close_landed.add_argument("--offer", required=True, help="offer body file or merge-request envelope id")
    close_landed.add_argument("--green", required=True, help="new green commit SHA")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "close-landed":
            output, status = _run_close_landed(args.offer, args.green, root)
            sys.stdout.write(output)
            return status
        if not args.census_green:
            parser.error("--green is required for the historical census")
        history = GitHistory(root, args.census_green, args.since)
        items = _load_store()
        ledger_path = _ledger_path(root)
        ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
        events, _ = ledger_events(ledger_lines, history.since_date)
        report_only = report_candidates(items, history, events)
        stranded, unresolved_hashes = stranded_rows(items, ledger_lines, history, history.since_date)
        sys.stdout.write(_render_report(history.green, items, report_only, stranded, unresolved_hashes))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"reconcile_landing: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
