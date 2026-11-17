from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.reconcile_landing import (
    GitHistory,
    _apply_offer_closes,
    _close_candidates,
    _content_present,
    _normalize_task_id,
    _render_close_landed,
    ledger_events,
    main,
    report_candidates,
    recorded_tip,
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

    def test_inferred_ancestor_range_and_subject_evidence_are_report_only(self) -> None:
        ancestor_tip = self.repo.commit_file("ancestor.txt", "ancestor\n", "implementation")
        self.repo.git("checkout", "-q", "-b", "offer-t2", self.base)
        subject_tip = self.repo.commit_file("subject.txt", "source\n", "source implementation")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit_file("subject.txt", "train reword\n", "Merge t-2 into b263")
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
        report_only = report_candidates(items, history)
        rows = {row["id"]: row for row in report_only}
        for task_id, evidence_kind in {
            "t-1": "tip-ancestor",
            "t-2": "task-subject",
            "t-3": "identical-range",
        }.items():
            self.assertEqual(rows[task_id]["evidence_kind"], evidence_kind)
            self.assertFalse(rows[task_id]["closeable"])
        present, _ = _content_present(history, "t-2", subject_tip)
        self.assertFalse(present)

        self.assertTrue(all(not row["closeable"] for row in report_only))

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

        self.assertEqual(recorded_tip(item), note_tip[:10])
        report_only = report_candidates([item], GitHistory(self.repo.root, green))
        partial = next(row for row in report_only if row["id"] == "t-20")
        self.assertFalse(partial["closeable"])
        self.assertEqual(partial["evidence_kind"], "partial-files")
        self.assertEqual(partial["matched_files"], 1)
        self.assertEqual(partial["changed_files"], 2)

    def test_recorded_tip_accepts_an_all_numeric_sha_prefix(self) -> None:
        item = {"notes": [{"text": "OFFERED review/numeric 1234567890"}]}
        self.assertEqual(recorded_tip(item), "1234567890")

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

    def test_offer_and_landed_rows_bind_task_tips_separately_from_green(self) -> None:
        offer_tip = self.repo.commit_file("offer.txt", "offer\n", "offer")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        lines = [
            f"2026-11-01 09:00 OFFER b-671 simulator half {offer_tip[:10]}",
            f"2026-11-01 09:30 RE-OFFER t-1121 Gibson-Hubbard {offer_tip[:10]}",
            f"2026-11-02 09:00 LANDED b1 green {self.base[:10]} -> {green[:10]} = t-720 {offer_tip[:10]}",
        ]
        events, _ = ledger_events(lines)
        by_task = {event["task_id"]: event for event in events}
        self.assertEqual(by_task["b-671"]["kind"], "OFFER")
        self.assertEqual(by_task["b-671"]["tip"], offer_tip[:10])
        self.assertEqual(by_task["t-1121"]["kind"], "RE-OFFER")
        self.assertEqual(by_task["t-1121"]["tip"], offer_tip[:10])
        landed = by_task["t-720"]
        self.assertEqual(landed["kind"], "LANDED")
        self.assertEqual(landed["tip"], offer_tip[:10])
        self.assertEqual(landed["green_before"], self.base[:10])
        self.assertEqual(landed["green_after"], green[:10])

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

    def test_explicit_close_ids_close_exact_targets_only(self) -> None:
        offer_tip = self.repo.commit_file("offer.txt", "offer\n", "offer")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        items = [
            {"id": "t-2", "kind": "task", "done": False, "notes": []},
            {"id": "t-2a", "kind": "task", "done": False, "notes": []},
        ]
        candidates, refusals, close_ids = _close_candidates(offer_tip, ["t-2"], GitHistory(self.repo.root, green), items)
        self.assertEqual(close_ids, ["t-2"])
        self.assertEqual([candidate["id"] for candidate in candidates], ["t-2"])
        self.assertEqual(candidates[0]["close_id"], "t-2")
        self.assertEqual(refusals, [])

        applied_ids: list[str] = []
        applied, apply_refusals = _apply_offer_closes(candidates, lambda candidate: applied_ids.append(candidate["id"]))
        self.assertEqual(applied, ["t-2"])
        self.assertEqual(applied_ids, ["t-2"])
        self.assertEqual(apply_refusals, [])

    def test_unknown_and_suffixed_close_ids_are_typed_refusals(self) -> None:
        tip = self.repo.commit_file("offer.txt", "offer\n", "offer")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        items = [{"id": "t-2", "kind": "task", "done": False, "notes": []}]
        for close_id in ("t-2a", "t-99"):
            with self.subTest(close_id=close_id):
                candidates, refusals, close_ids = _close_candidates(tip, [close_id], GitHistory(self.repo.root, green), items)
                self.assertEqual(close_ids, [close_id])
                self.assertEqual(candidates, [])
                self.assertEqual(refusals[0]["type"], "unknown-task-id")
                self.assertEqual(refusals[0]["id"], close_id)
        self.assertEqual(_normalize_task_id("t-2a"), "t-2a")
        report = _render_close_landed(tip, green, ["t-2a"], [], [{"id": "t-2a", "type": "unknown-task-id", "message": "no exact task-store id matches t-2a"}])
        self.assertIn("Refusal t-2a [unknown-task-id]", report)

    def test_tip_must_be_an_ancestor_of_green(self) -> None:
        self.repo.git("checkout", "-q", "-b", "offer", self.base)
        offer_tip = self.repo.commit_file("offer.txt", "offer\n", "offer")
        self.repo.git("checkout", "-q", "main")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        items = [{"id": "t-2", "kind": "task", "done": False, "notes": []}]

        candidates, refusals, close_ids = _close_candidates(offer_tip, ["t-2"], GitHistory(self.repo.root, green), items)
        self.assertEqual(close_ids, ["t-2"])
        self.assertEqual(candidates, [])
        self.assertEqual(refusals[0]["type"], "offer-tip-not-ancestor")

    def test_close_landed_requires_explicit_close_arguments(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["close-landed", "--tip", self.base, "--green", self.base])
        self.assertEqual(caught.exception.code, 2)

    def test_close_landed_passes_repeated_close_ids_to_runner(self) -> None:
        with patch("scripts.reconcile_landing._run_close_landed", return_value=("report\n", 0)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                status = main([
                    "close-landed",
                    "--tip", self.base,
                    "--green", self.base,
                    "--close", "t-2",
                    "--close", "b-4",
                ])
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.args[:3], (self.base, self.base, ["t-2", "b-4"]))
        self.assertEqual(run.call_args.args[3], Path(__file__).resolve().parents[1])

    def test_colliding_task_ids_remain_distinct_in_history_report(self) -> None:
        tip = self.repo.commit_file("offer.txt", "offer\n", "offer")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        events, _ = ledger_events([f"2026-11-01 09:00 INTAKE: lane t-2a {tip}"])
        items = [
            {"id": "t-2", "kind": "task", "done": False, "notes": []},
            {"id": "t-2a", "kind": "task", "done": False, "notes": []},
        ]
        rows = report_candidates(items, GitHistory(self.repo.root, green), events)
        bound = [row for row in rows if row["evidence_kind"] == "task-bound offer landed"]
        self.assertEqual([row["id"] for row in bound], ["t-2a"])

    def test_offer_without_closes_stays_report_only(self) -> None:
        tip = self.repo.commit_file("offered.txt", "offered\n", "offered")
        green = self.repo.commit_file("green.txt", "green\n", "green")
        item = {"id": "t-2", "kind": "task", "done": False, "notes": [{"text": f"OFFERED review/t2 {tip}"}]}
        events, _ = ledger_events([f"2026-11-01 09:00 OFFER t-2 implementation {tip}"])

        report_only = report_candidates([item], GitHistory(self.repo.root, green), events)
        row = next(row for row in report_only if row["id"] == "t-2")
        self.assertEqual(row["evidence_kind"], "task-bound offer landed")
        self.assertFalse(row["closeable"])

    def test_real_store_note_shapes_are_report_only(self) -> None:
        samples = [
            (
                "b-1037",
                [
                    (
                        "2026-10-24T09:48:16+00:00",
                        "At the t-1457 C0 tip a1d3d8222 the 60 h TPE node passes alone in 658.38 s (green 439.74 s): +50%. The nonfinite_fo2 node is +19%. Likely cause, inferred and not measured: the shorter computed C0 exit (d-252) spends more of each fixed-length run in the later, solver-heavy campaigns, so this is more work rather than a slowdown per step. A CPU-time and work-counter comparison would confirm it.",
                    )
                ],
                None,
                "unbound sha mention",
                False,
            ),
            (
                "b-163",
                [
                    (
                        "2026-08-18T05:38:47+00:00",
                        "LOCALISATION EXPERIMENT NOW RUNNING (job wedgeloc-b7014183, studio-3, full tier, tip b7014183): the SAME full tier with exactly ONE node deselected - that one. Minimal intervention on purpose.",
                    ),
                    (
                        "2026-09-25T16:07:18+00:00",
                        "store-sort 2026-09-25 (luna verifier, storesort-s13-0925): STILL-OPEN. pyproject.toml:101-111 still defaults to xdist plus fixed --timeout=300 and contains no report-log/controller backstop; the wedge fix remains external or unlanded. Today: Find and fix full-suite controller wedge after diagnostics.",
                    ),
                ],
                None,
                "unbound sha mention",
                True,
            ),
            (
                "t-1651",
                [
                    (
                        "2026-11-05T11:30:37+00:00",
                        "BLOCKED 2026-11-05 (w1105t1651 gap, heldout/t1651-gap.md): (A) d-336 closure owner _percent_composition_closure_error lives on unlanded bugs 91585580c (95/102/105 + T.Fe guard); bugs tip 0a4cbf27e has only 95-102; extend it only after it lands (hold, no transplant). (B) d-333 has no slag T.Fe Fe2+/Fe3+ split entry point (iron_saturation_composition returns oxide-standard N0 only) and d-343 names no notice; asked physics for function + notice.",
                    ),
                    (
                        "2026-11-05T12:20:29+00:00",
                        "Part B (d-345) done b287e90ed, ror-t1651b LAND. Wiring follow-up (after d-336 owner lands) MUST: attach out_of_certified_band when kress91_extrapolation (1650 C rows); attach COMPOSITION_PROJECTED from kress91_dropped_components like score._fe_redox_kress_basis; carry the a_FeO ceiling flag (being folded now). Review heldout/ror-t1651b-review.md.",
                    ),
                ],
                None,
                "unbound sha mention",
                True,
            ),
            (
                "b-784",
                [
                    ("2026-10-30T06:31:16+00:00", "d-310 labels offered to train: fcebdc472 (9be111199 labels + digest pin), sol LAND both, studio-2 whole tree accounted for. Close the label half on landing."),
                    ("2026-10-30T12:17:08+00:00", "d-310 label half LANDED in train b181 (d8b3d2bb4 merges fcebdc472; green 6030d877c). Ticket stays open for the C2A recipe-text half (d-309/d-311, held pending physics review)."),
                ],
                ("OFFER", "2026-10-30 06:00", "LANDED", "2026-10-30 07:00"),
                "task-bound offer landed",
                True,
            ),
            (
                "b-644",
                [
                    (
                        "2026-10-02T01:45:14+00:00",
                        "Review LAND 6dc16819a (REVIEW-b644-drop-6dc16819-2026-10-01.md). Spot-check of the 9,330 remainder: LEGIT superseded; still SILENT — pending categorical (helper is REJECTED-only), pending quoted_unattributed, pending model_derived points, unsupported quantities (e.g. evaporation_coefficient_alpha). Follow-up: extend the refusal helper to those classes after d-056 admission default lands (it changes which rows are pending).",
                    )
                ],
                ("OFFER", "2026-10-02 00:30", "LANDED", "2026-10-02 01:00"),
                "task-bound offer landed",
                True,
            ),
            (
                "t-1562",
                [
                    (
                        "2026-10-28T01:56:04+00:00",
                        "2026-10-27 SUPERSEDES the measured_tabulated amendment: Pak 1989 (LAND 9edde05bd, ror-napak89b) found the better in-schema pattern: store the printed log cell as its own typed-refused observation (quantity activity, admission typed_refusal, value_as_printed the log string) and the linear a = 10^(log) row as measured_reduced with derived_from that sibling id + derivation.relation; lineage resolves, 0 hard issues. Use this for Itoh 1984 and the Rego tables.",
                    )
                ],
                ("OFFER", "2026-10-28 00:30", "LANDED", "2026-10-28 01:00"),
                "task-bound offer landed",
                True,
            ),
            (
                "b-671",
                [
                    (
                        "2026-10-04T03:11:14+00:00",
                        "Package half pushed to openimcc main bc3ac65 (owner OK in engine chat; confirm LAND b671-confirm.md:69). Simulator half landed on green as ebe83db46 (ff from bac60dc99; LAND b671-bridge-review.md:137; gate 20,333 tests, 0 new, 0 openimcc_binding_digest_unavailable, no store change). Effect measured: digest is PROVENANCE not key material; v1.0.2 provenance, keys, candidate ids and score rows byte-identical; ext-v4/research packs now record their own digest;",
                    )
                ],
                ("OFFER", "2026-10-03 22:00", None, None),
                "task-bound offer landed",
                False,
            ),
            (
                "t-1121",
                [
                    (
                        "2026-10-04T00:58:09+00:00",
                        "2026-10-03 repair done by t1121-gibson: commit b0380b95e (keep/t1121-gibson), one extract file, 108 ids preserved, decoded values identical, validator 73 errors → OK. Report docs-private/research/2026-10-03-b681/gibson72-repair-report.md. Paper facts: 14163 is a TIME series at 1050 °C (instantaneous/1/6/72 h, separate aliquots, p. 2009); 12022 endpoint independence not stated; mass, crucible geometry, per-run ramp rate, Na/K precision not stated; geometry",
                    )
                ],
                ("RE-OFFER", "2026-10-03 22:49", "LANDED", "2026-10-03 23:28"),
                "task-bound offer landed",
                False,
            ),
            (
                "t-720",
                [
                    (
                        "2026-09-25T16:07:18+00:00",
                        "store-sort 2026-09-25 (luna verifier, storesort-s09-0925): STILL-OPEN. pyproject.toml:111 has parallel default addopts without -m not nightly; :127 registers nightly, and scripts/pytest_partitioned_gate.py:43-46 does not exclude it. Today: Exclude nightly tests from default and PR CI tiers.",
                    )
                ],
                ("LANDED", "2026-09-25 14:30", None, None),
                "task-bound offer landed",
                True,
            ),
        ]

        for task_id, notes, ledger_shape, evidence_kind, is_partial in samples:
            with self.subTest(task_id=task_id):
                tip = self.repo.commit_file(f"{task_id}/offer.txt", f"{task_id}\n", f"offer {task_id}")
                green = self.repo.commit_file(f"{task_id}/green.txt", f"green {task_id}\n", f"green {task_id}")
                item = {
                    "id": task_id,
                    "kind": "bug" if task_id.startswith("b-") else "task",
                    "done": False,
                    "notes": [{"at": at, "text": text} for at, text in notes],
                }
                events = []
                if ledger_shape:
                    offer_kind, offer_time, landed_kind, landed_time = ledger_shape
                    offer_line = (
                        f"{offer_time} {offer_kind}: {task_id} {tip}"
                        if offer_kind == "LANDED"
                        else f"{offer_time} {offer_kind} {task_id} synthetic {tip}"
                    )
                    lines = [offer_line]
                    if landed_kind:
                        lines.append(f"{landed_time} {landed_kind}: {task_id} {tip}")
                    events, _ = ledger_events(lines)
                report_only = report_candidates([item], GitHistory(self.repo.root, green), events)
                row = next(row for row in report_only if row["id"] == task_id)
                self.assertEqual(row["evidence_kind"], evidence_kind)
                self.assertFalse(row["closeable"])
                self.assertEqual(bool(row.get("partial")), is_partial)

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
            {"id": "b-1050"},
            {"id": "t-50"},
        ]
        attempted: list[str] = []

        def mock_owner_cli(candidate: dict[str, object]) -> None:
            task_id = str(candidate["id"])
            attempted.append(task_id)
            if task_id == "b-1050":
                raise RuntimeError("unsatisfied blocker: physics-lane")

        applied, refusals = _apply_offer_closes(candidates, mock_owner_cli)
        self.assertEqual(attempted, ["b-1050", "t-50"])
        self.assertEqual(applied, ["t-50"])
        self.assertEqual(refusals, [{"id": "b-1050", "type": "owner-cli-refusal", "message": "unsatisfied blocker: physics-lane"}])


if __name__ == "__main__":
    unittest.main()
