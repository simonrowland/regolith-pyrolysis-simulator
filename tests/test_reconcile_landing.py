from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.reconcile_landing import GitHistory, _apply_close_candidates, open_close_candidates, stranded_rows


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

    def test_ancestor_subject_and_reworded_content_evidence(self) -> None:
        ancestor_tip = self.repo.commit_file("ancestor.txt", "ancestor\n", "t-1 implementation")
        main_tip = self.repo.git("rev-parse", "HEAD")

        self.repo.git("checkout", "-q", "-b", "offer-b", self.base)
        subject_tip = self.repo.commit_file("subject.txt", "source\n", "source t-2")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit_file("subject.txt", "train reword\n", "Merge t2 into b263")

        self.repo.git("checkout", "-q", "-b", "offer-c", self.base)
        content_tip = self.repo.commit_file("reworded.txt", "same bytes\n", "source t-3")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("reworded.txt", "same bytes\n", "Merge train batch into b263")
        self.assertNotEqual(main_tip, green)

        items = [
            {"id": "t-1", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t1 {ancestor_tip[:10]}"}]},
            {"id": "t-2", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t2 {subject_tip[:10]}"}]},
            {"id": "t-3", "kind": "task", "done": False, "notes": [{"text": f"Merge review/t3 ({content_tip[:10]}) into b263"}]},
        ]
        history = GitHistory(self.repo.root, green)
        strong, weak = open_close_candidates(items, history)
        self.assertEqual({row["id"]: row["kind"] for row in strong}, {
            "t-1": "tip-ancestor",
            "t-2": "task-subject",
            "t-3": "identical-files",
        })
        self.assertEqual(weak, [])
        applied: list[str] = []
        _apply_close_candidates(strong + weak, lambda candidate: applied.append(candidate["id"]))
        self.assertCountEqual(applied, ["t-1", "t-2", "t-3"])

    def test_bounce_after_landing_and_absent_landed_content(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer-t5", self.base)
        bounced_tip = self.repo.commit_file("not-landed.txt", "offer\n", "source t-5")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("landed.txt", "batch\n", "b1 landing")
        self.repo.git("checkout", "-q", "-b", "offer-t6", self.base)
        absent_landed_tip = self.repo.commit_file("absent.txt", "absent\n", "source t-6")
        self.repo.git("checkout", "-q", "main")

        items = [
            {"id": "t-5", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t5 {bounced_tip[:10]}"}]},
            {"id": "t-6", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t6 {absent_landed_tip[:10]}"}]},
        ]
        ledger = [
            f"2026-11-01 09:00 INTAKE: lane t-5 {bounced_tip[:10]}",
            f"2026-11-02 09:00 LANDED: lane t-8 {green[:10]}",
            "2026-11-02 10:00 BOUNCED: lane t-5 conflict with green",
            f"2026-11-02 11:00 LANDED: lane t-6 {absent_landed_tip[:10]}",
        ]
        history = GitHistory(self.repo.root, green)
        rows = stranded_rows(items, ledger, history)
        reasons = {row["task_id"]: row["why"] for row in rows}
        self.assertIn("t-5", reasons)
        self.assertIn("1 green landing", reasons["t-5"])
        self.assertIn("t-6", reasons)
        self.assertIn("marks content LANDED", reasons["t-6"])

    def test_partial_file_match_is_reported_but_never_applied(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer-weak", self.base)
        (self.repo.root / "shared.txt").write_text("same\n", encoding="utf-8")
        (self.repo.root / "also-shared.txt").write_text("offer version\n", encoding="utf-8")
        self.repo.git("add", "shared.txt", "also-shared.txt")
        self.repo.git("commit", "-q", "-m", "weak t-7")
        weak_tip = self.repo.git("rev-parse", "HEAD")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit_file("shared.txt", "same\n", "green partial")
        green = self.repo.commit_file("also-shared.txt", "green version\n", "green diverged")
        items = [{"id": "t-7", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t7 {weak_tip[:10]}"}]}]

        history = GitHistory(self.repo.root, green)
        strong, weak = open_close_candidates(items, history)
        self.assertEqual(strong, [])
        self.assertEqual(weak[0]["kind"], "partial-files")
        called: list[str] = []
        _apply_close_candidates(strong + weak, lambda candidate: called.append(candidate["id"]))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
