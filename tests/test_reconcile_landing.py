from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.reconcile_landing import (
    GitHistory,
    _apply_close_candidates,
    _content_present,
    ledger_events,
    open_close_candidates,
    stranded_rows,
)


class ScratchGit:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.invalid")

    def close(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def commit_file(self, path: str, content: str, subject: str) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")


class LandingReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = ScratchGit()
        self.base = self.repo.commit_file("base.txt", "base\n", "base")

    def tearDown(self) -> None:
        self.repo.close()

    def test_ancestor_and_identical_range_close_but_subject_is_report_only(self) -> None:
        ancestor_tip = self.repo.commit_file("ancestor.txt", "ancestor\n", "t-1 implementation")
        self.repo.git("checkout", "-q", "-b", "offer-t2", self.base)
        subject_tip = self.repo.commit_file("subject.txt", "source\n", "source implementation")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit_file("subject.txt", "train reword\n", "Merge t2 into b263")
        self.repo.git("checkout", "-q", "-b", "offer-t3", self.base)
        content_tip = self.repo.commit_file("reworded.txt", "same bytes\n", "source implementation")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("reworded.txt", "same bytes\n", "Merge train batch into b263")

        items = [
            {"id": "t-1", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t1 {ancestor_tip[:10]}"}]},
            {"id": "t-2", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t2 {subject_tip[:10]}"}]},
            {"id": "t-3", "kind": "task", "done": False, "notes": [{"text": f"Merge review/t3 ({content_tip[:10]}) into b263"}]},
        ]
        history = GitHistory(self.repo.root, green)
        closeable, report_only = open_close_candidates(items, history)
        self.assertEqual({row["id"]: row["kind"] for row in closeable}, {
            "t-1": "tip-ancestor",
            "t-3": "identical-range",
        })
        subject = next(row for row in report_only if row["id"] == "t-2")
        self.assertFalse(subject["closeable"])
        self.assertEqual(subject["confidence"], "report-only")
        self.assertIn("subject mention only", subject["explanation"])
        present, reason = _content_present(history, "t-2", subject_tip)
        self.assertFalse(present)
        self.assertIn("subject mention only", reason)

        applied_ids: list[str] = []
        applied, refusals = _apply_close_candidates(closeable + report_only, lambda row: applied_ids.append(row["id"]))
        self.assertEqual(applied, ["t-1", "t-3"])
        self.assertEqual(applied_ids, ["t-1", "t-3"])
        self.assertEqual(refusals, [])

    def test_bounce_after_a_green_landing_and_absent_landed_content(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer-t5", self.base)
        bounced_tip = self.repo.commit_file("not-landed.txt", "offer\n", "source implementation")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("landed.txt", "batch\n", "batch landing")
        self.repo.git("checkout", "-q", "-b", "offer-t6", self.base)
        absent_landed_tip = self.repo.commit_file("absent.txt", "absent\n", "source implementation")
        self.repo.git("checkout", "-q", "main")

        items = [
            {"id": "t-5", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t5 {bounced_tip[:10]}"}]},
            {"id": "t-6", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t6 {absent_landed_tip[:10]}"}]},
            {"id": "t-8", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t8 {self.base[:10]}"}]},
        ]
        ledger = [
            f"2026-11-01 09:00 INTAKE: lane t-5 {bounced_tip[:10]}",
            "2026-11-01 09:30 INTAKE: engine typed-failures 4de120b56 (trial vs t-5 clean)",
            f"2026-11-02 09:00 LANDED b1 green {self.base[:10]} -> {green[:10]} (t-8 batch)",
            "2026-11-02 10:00 BOUNCED: lane t-5 conflict with green",
            f"2026-11-02 11:00 LANDED: lane t-6 {absent_landed_tip[:10]}",
        ]
        history = GitHistory(self.repo.root, green)
        rows, unresolved = stranded_rows(items, ledger, history)
        reasons = {row["task_id"]: row["why"] for row in rows}
        self.assertEqual(unresolved, 0)
        self.assertIn("t-5", reasons)
        self.assertIn("1 green landing", reasons["t-5"])
        self.assertIn("t-6", reasons)
        self.assertIn("ledger marks content LANDED", reasons["t-6"])

    def test_identical_range_requires_every_commit_file(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer-range", self.base)
        self.repo.commit_file("implementation.py", "implementation\n", "add implementation")
        note_tip = self.repo.commit_file("note.md", "note\n", "add offer note")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("note.md", "note\n", "land note only")
        item = {"id": "t-20", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/range {note_tip[:10]}"}]}

        closeable, report_only = open_close_candidates([item], GitHistory(self.repo.root, green))
        self.assertEqual(closeable, [])
        partial = next(row for row in report_only if row["id"] == "t-20")
        self.assertEqual(partial["kind"], "partial-files")
        self.assertEqual(partial["matched_files"], 1)
        self.assertEqual(partial["changed_files"], 2)

    def test_landed_green_destination_is_not_the_offer_tip(self) -> None:
        green = self.repo.commit_file("batch.txt", "unrelated batch\n", "unrelated batch")
        line = f"2026-11-03 10:00 LANDED b263 green {self.base[:10]} -> {green[:10]} (t-9001)"
        rows, unresolved = stranded_rows([], [line], GitHistory(self.repo.root, green))
        row = next(row for row in rows if row["task_id"] == "t-9001")
        self.assertEqual(row["category"], "unknown")
        self.assertIn("no offered tip recorded", row["why"])
        self.assertEqual(unresolved, 0)

    def test_dependency_pin_hashes_are_not_parsed_as_green_or_offer_tips(self) -> None:
        green = self.repo.commit_file("batch.txt", "batch\n", "batch")
        line = f"2026-11-03 10:00 GREEN {green[:10]} LANDED (b-671 simulator half: openimcc pin be41a6d->bc3ac65)"
        events, _ = ledger_events([line])
        landed = next(event for event in events if event["task_id"] == "b-671")
        self.assertIsNone(landed["tip"])
        self.assertIsNone(landed["green_before"])
        self.assertIsNone(landed["green_after"])

        rows, unresolved = stranded_rows([], [line], GitHistory(self.repo.root, green))
        row = next(row for row in rows if row["task_id"] == "b-671")
        self.assertEqual(row["category"], "unknown")
        self.assertIn("no offered tip recorded", row["why"])
        self.assertEqual(unresolved, 0)

    def test_landed_rows_use_the_offer_for_each_slice(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer-old", self.base)
        old_tip = self.repo.commit_file("old.txt", "old offer\n", "old offer")
        self.repo.git("checkout", "-q", "main")
        green1 = self.repo.commit_file("old.txt", "old offer\n", "reworded old slice")
        self.repo.git("checkout", "-q", "-b", "offer-new", self.base)
        new_tip = self.repo.commit_file("new.txt", "new offer\n", "new offer")
        self.repo.git("checkout", "-q", "main")
        green2 = self.repo.commit_file("batch.txt", "unrelated batch\n", "squashed batch landing")
        items = [{"id": "t-30", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/new {new_tip[:10]}"}]}]
        ledger = [
            f"2026-11-01 09:00 INTAKE: lane t-30 {old_tip[:10]}",
            f"2026-11-02 09:00 LANDED b1 green {self.base[:10]} -> {green1[:10]} (t-30 old slice)",
            f"2026-11-03 09:00 INTAKE: lane t-30 {new_tip[:10]}",
            "2026-11-03 10:00 BOUNCED: lane t-30 old merge conflict",
            f"2026-11-04 09:00 LANDED b2 green {green1[:10]} -> {green2[:10]} (t-30 reworded slice)",
        ]

        rows, unresolved = stranded_rows(items, ledger, GitHistory(self.repo.root, green2))
        self.assertEqual(unresolved, 0)
        self.assertEqual([(row["line_number"], row["task_id"], row["category"]) for row in rows], [(5, "t-30", "stranded")])
        self.assertIn(new_tip[:10], rows[0]["why"])
        self.assertNotIn(old_tip[:10], rows[0]["why"])

    def test_unresolvable_offer_tip_is_unknown_and_counted(self) -> None:
        green = self.repo.commit_file("batch.txt", "batch\n", "batch")
        lines = [
            "2026-11-01 09:00 INTAKE: lane t-40 deadbeef",
            f"2026-11-02 09:00 LANDED b1 green {self.base[:10]} -> {green[:10]} (t-40)",
        ]
        rows, unresolved = stranded_rows([], lines, GitHistory(self.repo.root, green))
        row = next(row for row in rows if row["task_id"] == "t-40")
        self.assertEqual(row["category"], "unknown")
        self.assertIn("does not resolve to a commit", row["why"])
        self.assertEqual(unresolved, 1)

    def test_refused_close_is_reported_and_next_candidate_is_attempted(self) -> None:
        candidates = [
            {"id": "b-1050", "kind": "tip-ancestor", "closeable": True},
            {"id": "t-50", "kind": "identical-range", "closeable": True},
            {"id": "t-51", "kind": "task-subject", "closeable": False},
        ]
        attempted: list[str] = []

        def mock_owner_cli(candidate: dict[str, object]) -> None:
            task_id = str(candidate["id"])
            attempted.append(task_id)
            if task_id == "b-1050":
                raise RuntimeError("unsatisfied blocker: physics-lane")

        applied, refusals = _apply_close_candidates(candidates, mock_owner_cli)
        self.assertEqual(attempted, ["b-1050", "t-50"])
        self.assertEqual(applied, ["t-50"])
        self.assertEqual(refusals, [{"id": "b-1050", "message": "unsatisfied blocker: physics-lane"}])


if __name__ == "__main__":
    unittest.main()
