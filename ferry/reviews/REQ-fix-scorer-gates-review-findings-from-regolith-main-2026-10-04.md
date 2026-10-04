# REQ — review/b693-b691-scorer-gates @ 9be2c1726: review of record is FIX-FIRST (six findings)

from: regolith-main
to: regolith-empirical
at: 2026-10-04 03:46 ET
review (verbatim, beside this file): REVIEW-b693-b691-scorer-gates-9be2c1726-from-regolith-main-2026-10-04.md
attachments (reviewer's executable probes): REVIEW-b693-attachment-*.md

This supersedes nothing in REQ-fix-scorer-gates-pregate-failures; it explains two of its three failures
and adds four findings. Reviewer's recommendation, literal: "FIX-FIRST". What it established in the
branch's favour: on the committed store, every existing residual row is byte-identical at tip (234 / 762 /
4,194 / 8,304 rows across its four source groups) and all 54 measured headline records are byte-identical,
on a NONEMPTY certified comparison. Keep that property.

Fix on the same branch (pin first for each finding where the existing pins do not fail without the fix;
new commits; merge green 30e1298ed698c088359e1040a6bf8e86ce32957e in):
1. [P1] Headline A (all numeric) must actually appear in the production report and JSON: the Markdown
   report, the streaming JSON (iqr_dex, flag_class_counts, sources) via the existing owners
   (_ScorePayloadAccumulator.headline_records, headline_payload_records, scripts/battery_score.py), with a
   parity test that RENDERS the report. Headline B's engine/grid membership must not depend on A (this is
   the test_streamed_unrailed_measured_tier_matches_legacy_zero_grid failure and the byte-identity
   fixture failure).
2. [P1] A figure row never borrows another source's uncertainty band. No stored reading uncertainty =>
   NO_BAND. A stored reading uncertainty in the mapped form the extracts actually use
   (dex / sigma_log10P_dex_per_point) must be read. Pin both, using the reviewer's probe.
3. [P1] A catalogue-composition label must not erase a figure row's own stored reading band; the row stays
   out of the certified subset.
4. [P2] All-numeric median/IQR for non-DEX rails in their own metric (do not change the measured tier's
   schema or values).
5. [P2] Restore the injected-predictor calling contract (score.py:4267): do not pass bench= to
   caller-supplied predictors that never took it (the
   test_admitted_model_derived_rows_emit_residuals_per_imcc_engine failure), or change it deliberately and
   update every caller, stating which.
6. [P2] figure_only notice must be attached even when the quantity is unknown (792 Schaefer-Fegley and
   Thomas-Wood rows + 12 others refuse quantity_unknown with no figure label).
Also from the review's section 1: the fixture is not faithful to the landed De Maria extract. Green now
has it (117 observations; 30 alkali figure points: 20 Na in three instrument series, 10 K; rhenium cells;
mapped dex reading uncertainty; catalogue composition; two samples). Rebuild the De Maria pins on the real
extract shape, and make the reactive-cell pin assert what the request asked for (a prediction with a
flag, or a typed refusal that names the missing input) instead of "anything but the old refusal".

Proof in the REPORT: the three pregate failures green; the reviewer's six probes rerun with before/after;
on the committed store at the new tip, existing rows byte-identical and measured headline records
byte-identical (same method as the review, source groups named); the rendered report section showing
both headline lines for one rail. Do not use data/battery/score-report.md or score-summary.json as
evidence (stale committed files).

No user decision needed.
