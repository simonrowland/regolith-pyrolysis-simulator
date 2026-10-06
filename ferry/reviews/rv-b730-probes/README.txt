Probes for REVIEW-b730-b731.md (review of record, origin/review/t1139-build-a-store @ 1e8ed29ae1d9d82f6f6dc75597d5558881a196d3, base 3c56d0351).
Run from a worktree root with PYTHONPATH=. (base = 3c56d0351, head = 1e8ed29ae).
- p1_probe.py -> p1_<sha>.json : P1 re-verification (mixed fractional formulas + controls).
- formulas.py -> f_base.json / f_head.json : wider adversarial formula sweep (earlier pass of this seat).
- diff_parse.py all_formulas.txt -> parse_diff_all_data.out : every unique formula string under data/ parsed by base and head parsers.
- term_probe.py -> term_<sha>.json : battery.validate._term_composition on the same strings.
- diff_0003.py -> diff_0003.out : B1452 0003 shard diff (removed/added/changed fields).
- dump_0003.py -> rows_0003.tsv : record-0003 rows with page, uncertainty-pair flag (U) and cells.
- unc_scan.py -> unc_scan.out : printed uncertainties at head, newly attached counts, implausible tokens.
- fw_check.py -> fw_check_head.out : formula mass vs printed FW for 0003 observations (pre-existing wrong-species rows; slug-matched, some lines may be probe artefacts).
- pytest_*.out : targeted pytest outputs (-n 0) at head.
B1452 PDF sha256 3e394cccef03515310ead8e3b363f631be3af4d60ceeec8f329ff86c74040622; pages read at 300 dpi: PDF 21, 22, 27, 28, 30, 31, 35.
