# Landing merge report

Candidate `357ea34638589d5aba6b8e03474780d7a4c0da2b` was merged with green
`be76d28ad8b73d0563ee34fddf452ec77b3d6de5` as a merge, with no rebase.
`git fetch origin work-v064-green` failed because this seat could not resolve
`github.com`; the specified green commit was already available locally.

## Conflict resolution

- `tests/test_literature_extracts.py`: preserved the t-1753a2/t-1753a3
  annotation, formula-label, and cell-matching notes, and preserved green's
  b261 corpus-join note. The ceiling names remain unchanged. Their values use
  green's expanded-corpus ceilings: 50,791 uncovered observations, 639
  uncovered extracts, and 3,385 unattested samples. No other conflicts were
  reported.

## Verification

- Ruff was unavailable in the seat and network access was unavailable. Per
  controller steer 1, substituted `python3 -m py_compile` for all six touched
  Python files with `PYTHONPYCACHEPREFIX` under `$TMPDIR`; it passed.
- Imported the touched `simulator` and `tools` modules together with one
  `python3 -c`; imports passed. Grepped the merged tree for the three shared
  ceiling identifiers; each remains defined and referenced. The conflict
  resolution removed or renamed no identifiers.
- The remote runner rejected the initial shell-wrapped command on
  `SimonMaookProM5` because profiled gates require a direct Python
  `-m pytest` command. A valid direct request with both required test files
  and the absolute controller config was then submitted, but had not returned
  a receipt after 40 minutes. The bounded local fallback first encountered
  missing xdist/timeout plugins; rerunning serially with project addopts
  disabled completed successfully: **4,828 passed, 1 skipped, 0 failed** in
  693.31 seconds.
- Failing node IDs: none. Green comparisons: not applicable because there were
  no failing nodes.
- No goldens were regenerated or edited.
