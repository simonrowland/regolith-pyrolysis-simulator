RULE: The Chart A scorecard labels dex error separately from flagged native-unit residuals | OWNER: scripts/battery_hero_plot.py::_draw_scorecard_png | CALLERS: collection scorecard renderer and tests/test_battery_hero_plot.py | IF THEY DISAGREE: readers may interpret native-unit residuals as dex

Changed the stale D-139 methods assertion to pin the exact dex-scoped population sentence while retaining the full tinted-and-counted clause. Scoped the Chart A subtitle to dex error and labeled the separate absolute section with its subtraction direction; updated both scorecard tests that assert its heading.

Checks: remote whole-file runs passed, `test_battery_hero_redesign.py` 41 passed and `test_battery_hero_plot.py` 62 passed. Both receipts used the declared OpenIMCC pin 66ec4a3. `f821check.py` was clean for all three touched Python files. `git diff --check` passed.

Text search in simulator/, scripts/, and tools/ found no matching Chart A scorecard labels in simulator/ or tools/. In scripts/battery_hero_plot.py, `_draw_scorecard_png` owns the chart subtitle and flagged-absolute section label; collection methods/caveat text and temperature/HTML chart headings are separate callers/surfaces and retain their respective labels. Tests assert the methods wording and scorecard label. No duplicated decision rule was added or consolidated.

Mutation evidence: the updated scorecard assertions require the exact heading `Flagged absolute: predicted − measured (native units)`; restoring the retired heading would violate those assertions. The D-139 pin likewise requires the exact dex-scoped sentence plus its unchanged tint/count clause, so the retired population wording cannot satisfy it.

Expected battery-store delta: zero. No store was regenerated or committed. No scratch tree, migration output, or store output was created. Temporary dispatch OMPI/TMP directories were removed before handoff.
