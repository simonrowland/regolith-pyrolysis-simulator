# S17 — masked exit status

**Repo tip (audited):** `fbe3491b2f515212e61f65ac8a2e4f424b868b5d` (`origin/work-v064-green`).  
**Worktree:** `/workspace/repos/wt/slot-07` (detached @ `fbe3491b2`). READ-ONLY on product code; writes only to `/workspace/ferry-inbox/sweeps/`. No push.  
**Scope:** `scripts/`, `tests/run.sh` (absent), `tools/`, any `*.sh`, Makefiles (none), CI configs (`scripts/nightly-gate.sh`; no `.github/` / tox / Makefile CI in-tree).  
**Predicate:** a pipe (`| tail` / `| head` / `| tee` / `| grep` / similar) **or** a `;` / `||` chain hides a failing command's exit code so the script/process **reads as success**.  
**Seed:** a landing script did `pytest … | tail -2 && git commit` and committed on a red test. **Not present on this tip** (no `pytest|tail` / `|head` / `|tee` gate or landing recipe in scoped trees).  
**Batch Y rank rule:** P0 only when a wrong number reaches a result/score/ledger **today**. Process/CI/script masks that do not invent a live physics/score value ≤ P1. Latent = P1 at most when they would corrupt gates/artifacts if fired; weaker = P2/P3.  
**Method:** static audit of all product `*.sh` + `scripts/`/`tools/` shell invocations (`sh -c` pipelines); runtime probes on tip. Mode: **ran-tests** (shell repros) + static.

| site (file:line) | predicate match | constructed trigger | live? | severity | P0? | fix direction |
|---|---|---|---|---|---|---|
| `scripts/collect_recipe_db.sh:6,14-16` | `set -uo pipefail` **without** `-e`. Per node: `rsync … \|\| scp … \|\| echo "  (no runs on $n)"` — both transfer failures become a successful `echo`. Script then prints `collected … N study dirs` and exits **0** even when **every** node failed. | `scripts/collect_recipe_db.sh /tmp/s17-collect-test no-such-host-s17.invalid` → prints `(no runs…)`, `0 study dirs`, **exit 0** (repro on tip). | live | P1 | no | Add `set -e` (keep `pipefail`). Track `rc=1` on node failure; do not `\|\| echo` into success — `\|\| { echo … >&2; rc=1; }`. Exit non-zero if zero studies collected when nodes were requested. |
| `patches/scripts/enginepatch.sh:11,148-161` (`cmd_refresh`) | `set -uo pipefail` **without** `-e`. `git -C "$d" diff > "$PATCHES/$e/0001-local.patch"` then unconditional `echo "$e: recaptured…"`. Failed `git diff` still reports success and returns 0, leaving an **empty** patch file. | Engine checkout dir exists (`-d`) but is not a git work tree (or `git diff` exits non-zero); `enginepatch.sh refresh <single-patch-engine>`. Minimal shape: `git -C "$tmpdir" diff > out; echo recaptured` → echo runs, script status 0, `out` empty (repro). | live (on refresh) | P1 | no | `set -euo pipefail`. After `git diff`, require non-zero patch or explicit `--allow-empty`; `git -C "$d" rev-parse --is-inside-work-tree` before capture; propagate `git` status. |
| `patches/scripts/enginepatch.sh:11` + `cmd_verify` / `cmd_status` / `cmd_apply` body | Same missing `errexit`: intermediate failures (`git diff`, `cat` patch concat, `git apply --check` outside the `&&`/`\|\|` guard) do not abort; only the hand-rolled `rc` / explicit `return 1` paths fail closed. Verify/status **do** return non-zero for MISSING/DRIFT when those branches run (tip: `verify` → exit 1 with three MISSING checkouts). Residual risk is silent continuation between explicit checks. | Force `git -C "$d" diff` to fail after the `-d` check while patch set is empty → possible false `MATCH` (both sides empty after failed write). | latent | P2 | no | Same: `set -euo pipefail` throughout; treat any unexpected non-zero between checks as verify failure. |
| `patches/scripts/enginepatch.sh:121-122` | Drift diagnostics: `comm … \| head -8 \| sed …` under `pipefail`. `head` closing early can SIGPIPE `comm` (pipeline status 141). Today `rc` is already set to 1 before these lines and `return $rc` wins — does **not** flip DRIFT into success. | Engine tree DRIFT with >8 differing lines; observe diagnostic pipeline status vs script exit. | live (diag only) | P3 | no | `head … \|\| true` on diagnostic-only pipes, or `set +o pipefail` around the display lines; keep verify `rc` authoritative. |
| `scripts/epoch_grind.py:186,281-304` | `IOREG_IOSURFACE_COMMAND = "ioreg … \| grep -c …"` run via `["sh", "-c", …]` **without** `set -o pipefail`. Producer failure can be dominated by `grep`'s status; any stdout that parses as an int is recorded `status="ok"` (returncode stored but not gated). Broken `ioreg` with empty stdout → `grep -c` prints `0` → **ok / count 0** (monitor looks healthy). Diagnostic-only IOSurface leak sampler; not a score/ledger path. | Enable IOSurface sampling on Darwin with `ioreg` missing/failing; or `sh -c 'false \| grep -c IOSurfaceRootUserClient'` → stdout `0`, then `status=ok`. | live (when sampling enabled) | P3 | no | `bash -c 'set -o pipefail; …'`; require `returncode == 0` **or** accept `grep -c`'s exit 1 only when count parses as 0 **and** stderr empty; else `status=skipped`. |
| `scripts/pack_recipe_db_starter.sh:11-14` | Packs whatever is under `$SRC` with `set -euo pipefail` (honest tar exit). Does **not** refuse an empty collection, so a green `collect` (site 1) + pack ships a tiny/empty `data/recipe-db-starter.tgz` as a successful pack. Defense-in-depth vs site 1, not an independent pipe mask. | After failed collect into empty `runs/`, `pack_recipe_db_starter.sh` that dir → exit 0, archive with 0 `cache.sqlite`. | live (follows bad collect) | P2 | no | Refuse pack when `find … cache.sqlite \| wc` is 0 (or below a documented minimum); keep collect exit non-zero as primary fix. |

## Counts

- Sites: **6**  
- Live: **5** · Latent: **1**  
- P0: **0** · P1: **2** · P2: **2** · P3: **2**  
- P0 count: **0** (Batch Y: no wrong physics/score/ledger number from these masks on tip today)

## No-hit areas (audited; not counted)

- **Seed `pytest \| tail && git commit`:** no match under `scripts/`, `tools/`, `*.sh`, `CONTRIBUTING.md`, `scripts/RUNBOOK.md`, or CI wrappers on this tip.  
- **`tests/run.sh`:** absent. **Makefiles / `.github/` workflows / tox / nox / pre-commit:** absent. In-tree CI entry is `scripts/nightly-gate.sh` → external untracked `studio-ci.sh`.  
- **`scripts/nightly-gate.sh`:** `set -euo pipefail`; captures `studio-ci` via `set +e` / `RC=$?` / `exit "$RC"`; `\|\| true` only on lock/result best-effort and optional `git fetch` (tip SHA already resolved from `REPO_ROOT`).  
- **`scripts/studio-regen.sh`:** `set -euo pipefail`; remote body also `set -euo pipefail`; `ulimit \|\| true` intentional; `sed\|sed` only in `usage` help. Pullback refuses missing outputs (`exit 1`).  
- **`scripts/blast_grind.sh`:** `set -euo pipefail`; backgrounds grind by design (`nohup … &`); exit reports launch, not grind completion (documented fire-and-forget — not a hidden fail-of-producer-via-pipe).  
- **`scripts/pack_recipe_db_starter.sh` pipes** (`du\|cut`, `find\|wc`) under `pipefail`+`errexit` — producer failure aborts.  
- **`install.sh`:** `set -eu`, no pipes. **`patches/mace/verify-applied.sh`:** `set -u`, no pipes; Python heredoc exit propagates.  
- **`scripts/pytest_partitioned_gate.py`:** `subprocess.run` argv form (no shell pipe); returns first non-zero bucket.  
- **`tools/*.py`:** no `shell=True` / `sh -c` pipelines with `| tail|head|tee|grep` found.  
- **docs-private `reviews/*/mutations/*.sh`:** `set -eu`, no command pipes (review fixtures; not product CI).  
- **Python `check=False` without a shell pipe** (e.g. `install-dependencies.py` recipe-db starter unpack, `battery_score` rsync, `grind_cleanup` ssh) — out of this class (closer to S12); not scored here.  
- **`battery_score._run_studio`:** on ssh non-zero returns `hostname` and main continues laptop-only scoring — process mask, but not a shell `|` / `;` / `\|\|` hiding the failing command's status inside a shell script.

## Method notes

- Enumerated every product `*.sh`; classified `set` flags vs filter pipes.  
- Repro: `collect_recipe_db.sh` against invalid host → exit 0; `enginepatch.sh verify` → exit 1 on MISSING (hand-rolled `rc` works); refresh/`git diff` failure shape → success echo under missing `errexit`.  
- Accidental `pack_recipe_db_starter.sh` run during probe was reverted (`git checkout -- data/recipe-db-starter.tgz`); worktree left clean.

SWEEP: S17 | sites=6 | live=5 | P0=0 P1=2 P2=2 P3=2 | tip=fbe3491b2f515212e61f65ac8a2e4f424b868b5d | path=/workspace/ferry-inbox/sweeps/S17-masked-exit-status.md
