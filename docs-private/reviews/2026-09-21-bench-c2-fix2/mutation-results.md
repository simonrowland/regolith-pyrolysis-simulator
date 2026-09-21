# Mutation evidence — bench-c2-fix2c

26 defect restorations executed, each with a green control, then a pytest assertion failure: 38 failing assertions total. Every source file was restored byte-for-byte. Collection errors, runtime exceptions such as TypeError/IndexError, non-green controls, and surviving mutants are rejected, not counted.

Run scripts **sequentially**, from any directory. Each wrapper resolves this checkout and uses the main repository's `.venv/bin/python` (or explicit `PYTHON`). Default pytest addopts remain enabled; only JUnit output is added. Each run prints its temporary control/mutant/JUnit log directory.

```sh
for n in $(seq 1 13); do
  docs-private/reviews/2026-09-21-bench-c2-fix2/mutations/finding-$n.sh || exit $?
done
```

| Finding | Mutations | Assertion failures | Restored defect |
|---|---:|---:|---|
| 1 | 3 | 4 | Disable implicit bench; assert own-work identity; accept hand-authored inferred identity |
| 2 | 2 | 3 | Mixed source becomes GAP; unique experiment count becomes one |
| 3 | 3 | 3 | Delete blocker list; delete engine summary; reverse source-count ranking |
| 4 | 2 | 3 | Select raw printed area above Clausing correction; remove geometric flag |
| 5 | 2 | 2 | Remove alpha denominator; hide unity-alpha assumption |
| 6 | 1 | 2 | Normalized mole-fraction route wins over partial printed wt% |
| 7 | 1 | 1 | Ignore bench Knudsen method when experiment method unknown |
| 8 | 1 | 2 | Replace unsupported mass with zero before division |
| 9 | 3 | 3 | Drop hold composition; drop incomplete-schedule refusal; concatenate unordered arrays |
| 10 | 1 | 1 | Omit eighth engine |
| 11 | 1 | 1 | Allow geometric-only area to meet effective-area requirement |
| 12 | 5 | 12 | Crossing interval excluded; known Knudsen method refused; wrong Kn threshold; reversed pressure-bound direction; dropped consistency notice |
| 13 | 1 | 1 | Golden fixture mass_kg 1000.0 → 1001.0 |

The final notice mutation was run separately through `python3 .../mutations/run_mutation.py notice`; it is included in the committed `finding-12.sh` and `knudsen.sh` groups. N1–N8 wrappers invoke the identical already-executed mutation groups, without duplicate implementations.

Retained execution transcripts:

- `/tmp/bench-c2-fix2c-mutations-verified.log`: accepted finding-1 and finding-2 evidence. Its later rejected blocker IndexError is excluded from these counts.
- `/tmp/bench-c2-fix2c-mutations-verified-continued.log`: accepted finding-3 through finding-13 evidence after strengthening the blocker assertion.
- `/tmp/bench-c2-fix2c-mutation-notice.log`: accepted notice-removal evidence, three assertion failures.

Golden control: `1 passed in 60.13s`; mutant: one equality assertion failure. Logs: `/var/folders/7n/5xbptw3x78x4tvhqrmy9lpbr0000gn/T/bench-c2-finding-13-vrvjdov6/`. This refutes local non-reproduction only; it does not diagnose the historical Studio host.
