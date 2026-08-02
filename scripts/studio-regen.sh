#!/bin/bash
# studio-regen.sh [--dry-run] <tip-ref> <target> [<target>...]
#
# Machine-sensitive golden regeneration under the STUDIO CI engine config.
# Doctrine: laptop regens of engine-touched goldens go red on the gate
# (train11: sio_yield / runner_smoke / staged_bakeout / capacity pins).
# Always regenerate on mac-studio-256-1 with the same rsync + engines.local.toml
# + venv + PATH/ulimit stanzas as ~/Repos/studio-ci.sh.
#
# (a) tip ref + regen targets (fixture paths / pin-bearing test files / aliases)
# (b) rsync pinned worktree @ tip → studio; run named regen cmds under studio config
# (c) rsync ONLY regenerated outputs back into the LOCAL worktree; list changes + value-diff
# (d) no commit, no push; refuse if LOCAL target paths are dirty
#
# Targets / aliases:
#   coating | tests/test_coating_rate.py
#     → scripts/regenerate_coating_diagnostic_golden.py
#       writes: tests/test_coating_rate.py (SHA pin)
#   runner  | tests/fixtures/runner[/...] | tests/test_runner_smoke.py
#     → scripts/regenerate_runner_goldens.py
#       writes: tests/fixtures/runner/*.json
#       (also covers recipe_io + cost_ledger goldens that bind the lunar runner fixture)
#   sio_yield | tests/fixtures/sio_yield[/...]
#     → -m simulator.runner.sio_yield (form from S-03 commit 4fce2f0)
#       writes: tests/fixtures/sio_yield/*.json
#   cache | cache_identity | cache_convert | tests/fixtures/cache_identity[/...]
#     → scripts/regenerate_cache_identity_goldens.py
#       writes: tests/fixtures/cache_identity/b-043-cache-contract.golden.json
#   pins | capacity | sio_pins | staged_bakeout
#     → scripts/emit_studio_pin_values.py
#       writes: studio-pin-report.json (value report; local worker patches code pins)
#
# Examples:
#   scripts/studio-regen.sh --dry-run HEAD coating
#   scripts/studio-regen.sh HEAD coating
#   scripts/studio-regen.sh abc1234 runner sio_yield cache pins
#   scripts/studio-regen.sh HEAD tests/fixtures/sio_yield/mars_basalt_c2a.json
set -euo pipefail

HOST=mac-studio-256-1
# Studio engine config source (same as studio-ci.sh).
STUDIO_ENGINE_TOML='~/repos/regolith-grind-0a3d5e9/engines/engines.local.toml'
STUDIO_VENV='~/repos/regolith-pyrolysis-simulator/.venv'

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

if [ "$#" -lt 2 ]; then
  usage
fi

TIP_REF="$1"
shift
TARGETS=("$@")

LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$LOCAL_ROOT"

if ! git rev-parse --verify "${TIP_REF}^{commit}" >/dev/null 2>&1; then
  echo "ERROR: tip-ref not a commit: $TIP_REF" >&2
  exit 1
fi
TIP_SHA="$(git rev-parse --verify "${TIP_REF}^{commit}")"
TIP_SHORT="$(git rev-parse --short "$TIP_SHA")"

# --- target resolution -------------------------------------------------------
# OUTPUTS: repo-relative paths that will be rsynced back
# REMOTE_CMDS: shell lines executed on the studio after config provision
declare -a OUTPUTS=()
declare -a REMOTE_CMDS=()
declare -a FAMILIES=()

_add_unique() {
  # Avoid set -u trips on empty arrays (bash 3.2 / nounset).
  local arr_name="$1" val="$2" existing n i
  eval "n=\${#${arr_name}[@]}"
  i=0
  while [ "$i" -lt "${n:-0}" ]; do
    eval "existing=\${${arr_name}[$i]}"
    [ "$existing" = "$val" ] && return 0
    i=$((i + 1))
  done
  eval "${arr_name}+=(\"\$val\")"
}

_resolve_one() {
  local t="$1"
  case "$t" in
    coating|tests/test_coating_rate.py)
      _add_unique FAMILIES coating
      _add_unique OUTPUTS "tests/test_coating_rate.py"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python scripts/regenerate_coating_diagnostic_golden.py"
      ;;
    runner|tests/test_runner_smoke.py|tests/fixtures/runner|tests/fixtures/runner/*)
      _add_unique FAMILIES runner
      _add_unique OUTPUTS "tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json"
      _add_unique OUTPUTS "tests/fixtures/runner/mars_basalt_C2A_12h.json"
      _add_unique OUTPUTS "tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python scripts/regenerate_runner_goldens.py"
      ;;
    sio_yield|tests/fixtures/sio_yield)
      _add_unique FAMILIES sio_yield
      _add_unique OUTPUTS "tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json"
      _add_unique OUTPUTS "tests/fixtures/sio_yield/mars_basalt_c2a.json"
      # Form documented in S-03 commit 4fce2f0.
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python -m simulator.runner.sio_yield --feedstock lunar_mare_low_ti --campaign C2A_continuous --hours 24 --allow-unmeasured-alpha-fallback --output tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python -m simulator.runner.sio_yield --feedstock mars_basalt --campaign C2A_continuous --hours 24 --allow-unmeasured-alpha-fallback --output tests/fixtures/sio_yield/mars_basalt_c2a.json"
      ;;
    tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json)
      _add_unique FAMILIES sio_yield
      _add_unique OUTPUTS "tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python -m simulator.runner.sio_yield --feedstock lunar_mare_low_ti --campaign C2A_continuous --hours 24 --allow-unmeasured-alpha-fallback --output tests/fixtures/sio_yield/lunar_mare_low_ti_c2a.json"
      ;;
    tests/fixtures/sio_yield/mars_basalt_c2a.json)
      _add_unique FAMILIES sio_yield
      _add_unique OUTPUTS "tests/fixtures/sio_yield/mars_basalt_c2a.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python -m simulator.runner.sio_yield --feedstock mars_basalt --campaign C2A_continuous --hours 24 --allow-unmeasured-alpha-fallback --output tests/fixtures/sio_yield/mars_basalt_c2a.json"
      ;;
    tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json|\
    tests/fixtures/runner/mars_basalt_C2A_12h.json|\
    tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json)
      # Family regen writes all three; pull back only the named one.
      _add_unique FAMILIES runner
      _add_unique OUTPUTS "$t"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python scripts/regenerate_runner_goldens.py"
      ;;
    cache|cache_identity|cache_convert|\
    tests/fixtures/cache_identity|\
    tests/fixtures/cache_identity/*|\
    tests/test_cache_convert.py)
      _add_unique FAMILIES cache
      _add_unique OUTPUTS "tests/fixtures/cache_identity/b-043-cache-contract.golden.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python scripts/regenerate_cache_identity_goldens.py"
      ;;
    pins|pin_report|capacity|capacity_coupling|sio_pins|staged_bakeout|\
    tests/test_capacity_coupling.py|tests/test_staged_bakeout.py|\
    tests/chemistry/test_sio_chain_coherence.py|\
    tests/chemistry/test_sio_step_condensation.py|\
    tests/chemistry/test_sio_step_wall_deposit.py)
      _add_unique FAMILIES pins
      _add_unique OUTPUTS "studio-pin-report.json"
      _add_unique REMOTE_CMDS \
        "./.venv/bin/python scripts/emit_studio_pin_values.py"
      ;;
    *)
      echo "ERROR: unknown regen target: $t" >&2
      echo "Known: coating | runner | sio_yield | cache | pins | explicit fixture/pin paths under those families" >&2
      exit 1
      ;;
  esac
}

for t in "${TARGETS[@]}"; do
  _resolve_one "$t"
done

if [ "${#OUTPUTS[@]}" -eq 0 ] || [ "${#REMOTE_CMDS[@]}" -eq 0 ]; then
  echo "ERROR: no regeneration work resolved from targets" >&2
  exit 1
fi

# --- refuse dirty local target paths ----------------------------------------
dirty=0
for p in "${OUTPUTS[@]}"; do
  if [ ! -e "$p" ] && [ ! -e "$LOCAL_ROOT/$p" ]; then
    # pin/fixture may be new; only refuse if git already tracks dirty content
    :
  fi
  if ! git diff --quiet -- "$p" 2>/dev/null; then
    echo "ERROR: refuse — local worktree dirty on target path: $p" >&2
    dirty=1
  fi
  if ! git diff --cached --quiet -- "$p" 2>/dev/null; then
    echo "ERROR: refuse — local index dirty on target path: $p" >&2
    dirty=1
  fi
done
if [ "$dirty" -ne 0 ]; then
  echo "Clean the target paths (or commit/stash them) before studio-regen." >&2
  exit 1
fi

JOB="regen-${TIP_SHORT}-$(printf '%s' "${FAMILIES[*]}" | tr ' ' '-')"
DEST="~/repos/ci-jobs/$JOB"
# Local pullback staging (never committed by this script).
STAGE_DIR="${TMPDIR:-/tmp}/studio-regen-${JOB}"
PINNED_WT="${TMPDIR:-/tmp}/studio-regen-wt-${JOB}"

echo "=== studio-regen plan ==="
echo "tip:      $TIP_SHA ($TIP_SHORT)"
echo "host:     $HOST"
echo "job:      $JOB"
echo "dest:     $DEST"
echo "families: ${FAMILIES[*]}"
echo "outputs:"
for p in "${OUTPUTS[@]}"; do echo "  - $p"; done
echo "remote commands:"
for c in "${REMOTE_CMDS[@]}"; do echo "  \$ $c"; done
echo "dry_run:  $DRY_RUN"
echo "commit:   NO (script never commits/pushes)"
echo "========================="

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN complete — no rsync, no remote execution, no pullback."
  exit 0
fi

# --- pinned worktree at tip -------------------------------------------------
# Overlay harness helpers from LOCAL so tip may predate this script; physics
# code remains tip-pinned.
rm -rf "$PINNED_WT"
git worktree add --detach "$PINNED_WT" "$TIP_SHA"
mkdir -p "$PINNED_WT/scripts"
# Always overlay the regen harness + producers from the invoking tree so tip
# may predate this worker's harness extensions; physics code stays tip-pinned.
cp -f "$LOCAL_ROOT/scripts/studio-regen.sh" "$PINNED_WT/scripts/studio-regen.sh"
cp -f "$LOCAL_ROOT/scripts/regenerate_coating_diagnostic_golden.py" \
  "$PINNED_WT/scripts/regenerate_coating_diagnostic_golden.py"
cp -f "$LOCAL_ROOT/scripts/emit_studio_pin_values.py" \
  "$PINNED_WT/scripts/emit_studio_pin_values.py"
# Prefer tip copies of longer-lived regenerators; fall back to local if absent.
for helper in regenerate_runner_goldens.py regenerate_cache_identity_goldens.py; do
  if [ ! -f "$PINNED_WT/scripts/$helper" ] \
    && [ -f "$LOCAL_ROOT/scripts/$helper" ]; then
    cp -f "$LOCAL_ROOT/scripts/$helper" "$PINNED_WT/scripts/$helper"
  fi
done
# Always overlay cache identity regenerator when local is newer/present so
# studio-regen can target it even if tip path layout drifts.
if [ -f "$LOCAL_ROOT/scripts/regenerate_cache_identity_goldens.py" ]; then
  cp -f "$LOCAL_ROOT/scripts/regenerate_cache_identity_goldens.py" \
    "$PINNED_WT/scripts/regenerate_cache_identity_goldens.py"
fi

cleanup() {
  # Drop the detached worktree; ignore failures so pullback results survive.
  git worktree remove --force "$PINNED_WT" 2>/dev/null || rm -rf "$PINNED_WT"
}
trap cleanup EXIT

# --- rsync + provision + run (studio-ci.sh stanzas) -------------------------
ssh -o BatchMode=yes "$HOST" "mkdir -p ~/repos/ci-jobs"
# rsync working tree (pinned tip); exclude repo-local venv/git — same as studio-ci.sh.
# engines.local.toml is machine-local and overwritten from the studio grind tree.
rsync -a --delete --exclude .git --exclude .venv "$PINNED_WT/" "$HOST:repos/ci-jobs/$JOB/"

# Build remote script body. PATH + ulimit match studio-ci.sh (t-435 env parity).
REMOTE_BODY=$(
  cat <<EOF
set -euo pipefail
cd ~/repos/ci-jobs/$JOB
cp $STUDIO_ENGINE_TOML engines/engines.local.toml
ln -sfn $STUDIO_VENV .venv
export PATH=/opt/homebrew/bin:/usr/local/bin:\$PATH
ulimit -n 8192 || true
hostname
./.venv/bin/python -c "import sys; print('python', sys.version.split()[0])"
EOF
)
for c in "${REMOTE_CMDS[@]}"; do
  # shell-quote each command line as a single remote invocation
  REMOTE_BODY+=$'\n'"echo '+ $c'"
  REMOTE_BODY+=$'\n'"$c"
done
REMOTE_BODY+=$'\n'"echo REMOTE_OK"

ssh -o BatchMode=yes "$HOST" "$REMOTE_BODY"

# --- pull back ONLY the regenerated outputs ---------------------------------
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
for p in "${OUTPUTS[@]}"; do
  mkdir -p "$STAGE_DIR/$(dirname "$p")"
  # scp each output from remote job tree into staging, then into LOCAL.
  scp -q "$HOST:repos/ci-jobs/$JOB/$p" "$STAGE_DIR/$p"
done

echo "=== pullback / value-diff ==="
CHANGED=0
UNCHANGED=0
for p in "${OUTPUTS[@]}"; do
  local_path="$LOCAL_ROOT/$p"
  staged_path="$STAGE_DIR/$p"
  if [ ! -f "$staged_path" ]; then
    echo "MISSING remote output: $p" >&2
    exit 1
  fi
  if [ -f "$local_path" ] && cmp -s "$local_path" "$staged_path"; then
    echo "UNCHANGED: $p"
    UNCHANGED=$((UNCHANGED + 1))
    continue
  fi
  echo "CHANGED: $p"
  CHANGED=$((CHANGED + 1))
  # Value-diff summary (JSON recursive or text pin SHA).
  "$LOCAL_ROOT/.venv/bin/python" - "$local_path" "$staged_path" "$p" <<'PY'
import json, re, sys
from pathlib import Path

old_p, new_p, rel = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
old_raw = old_p.read_text(encoding="utf-8") if old_p.exists() else None
new_raw = new_p.read_text(encoding="utf-8")

def try_json(s):
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

old_j, new_j = try_json(old_raw), try_json(new_raw)

def walk(a, b, path="$"):
    rows = []
    if type(a) != type(b) or (isinstance(a, dict) != isinstance(b, dict)):
        rows.append((path, "type", type(a).__name__ if a is not None else "MISSING", type(b).__name__))
        return rows
    if isinstance(a, dict):
        keys = sorted(set(a) | set(b))
        for k in keys:
            p = f"{path}.{k}"
            if k not in a:
                rows.append((p, "added", None, b[k] if not isinstance(b[k], (dict, list)) else f"<{type(b[k]).__name__}>"))
            elif k not in b:
                rows.append((p, "removed", a[k] if not isinstance(a[k], (dict, list)) else f"<{type(a[k]).__name__}>", None))
            else:
                rows.extend(walk(a[k], b[k], p))
        return rows
    if isinstance(a, list):
        if len(a) != len(b):
            rows.append((path, "len", len(a), len(b)))
        for i, (ai, bi) in enumerate(zip(a, b)):
            rows.extend(walk(ai, bi, f"{path}[{i}]"))
        return rows
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a != b:
            delta = b - a if isinstance(a, (int, float)) else None
            rows.append((path, "num", a, b if delta is None else f"{b} (delta={delta!r})"))
        return rows
    if a != b:
        rows.append((path, "val", a, b))
    return rows

if old_j is not None and new_j is not None:
    rows = walk(old_j, new_j)
    print(f"  json value-diff: {len(rows)} leaf change(s)")
    for path, kind, o, n in rows[:80]:
        print(f"  - {path}: {kind}: {o!r} -> {n!r}")
    if len(rows) > 80:
        print(f"  ... ({len(rows) - 80} more)")
elif rel.endswith("test_coating_rate.py") or "coating" in rel:
    sha_re = re.compile(r'"([0-9a-f]{64})"')
    # Prefer the pin adjacent to the coating diagnostic test name.
    def pin_sha(text):
        if text is None:
            return None
        m = re.search(
            r"test_coating_diagnostic_default_output_is_byte_identical_to_golden[\s\S]{0,1200}?"
            r'"([0-9a-f]{64})"',
            text,
        )
        return m.group(1) if m else None
    o, n = pin_sha(old_raw), pin_sha(new_raw)
    print(f"  coating diagnostic SHA: {o} -> {n}")
else:
    # Generic text: show first differing region sizes + unified-ish head.
    import difflib
    old_lines = (old_raw or "").splitlines(keepends=True)
    new_lines = new_raw.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="local/"+rel, tofile="studio/"+rel, n=2))
    print(f"  text diff: {len(diff)} lines")
    for line in diff[:60]:
        print("  " + line.rstrip("\n"))
    if len(diff) > 60:
        print(f"  ... ({len(diff) - 60} more diff lines)")
PY
  # Install into LOCAL worktree (no git commit).
  mkdir -p "$(dirname "$local_path")"
  cp -f "$staged_path" "$local_path"
done

echo "=== summary ==="
echo "changed_files:   $CHANGED"
echo "unchanged_files: $UNCHANGED"
echo "local_root:      $LOCAL_ROOT"
echo "NO commit, NO push (by design)."
if [ "$CHANGED" -gt 0 ]; then
  echo "Local target paths updated from studio. Review + commit separately."
  git status --short -- "${OUTPUTS[@]}"
fi
