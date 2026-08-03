#!/bin/bash
# scripts/nightly-gate.sh — full-tier nightly CI for launchd/cron callers.
#
# Resolves the current branch tip, creates/refreshes a pinned worktree, runs
# studio-ci.sh full-tier with CI_WALL_CAP_S=16200 (4.5 h), then writes the
# junit path + a one-line result into docs-private/research/nightly-gates/<date>.md.
#
# Designed for an external scheduler; this script does NOT install launchd/cron
# (the controller wires that at landing).
#
# studio-ci.sh is an untracked sibling of this repo
# ($REPO_ROOT/../studio-ci.sh by default). The pr|full tier logic lives there,
# outside this repository's version control; the controller owns that file.
# Landing this repo chunk does not land studio-ci.sh.
#
# Usage (from any checkout of this repo, or with REPO_ROOT set):
#   scripts/nightly-gate.sh
#   BRANCH=main scripts/nightly-gate.sh
#   REPO_ROOT=/path/to/repo scripts/nightly-gate.sh
#
# Env:
#   REPO_ROOT       — git repo root (default: this script's parent parent)
#   BRANCH          — branch to pin (default: current branch of REPO_ROOT, or main)
#   STUDIO_CI       — path to studio-ci.sh (default: $REPO_ROOT/../studio-ci.sh)
#   NIGHTLY_WT      — pinned worktree path (default: /tmp/rps-nightly-gate)
#   CI_WALL_CAP_S   — wall cap passed through (default: 16200)
#   JOB_NAME        — studio-ci job name (default: nightly-YYYYMMDD-HHMMSS)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${REPO_ROOT:-$SCRIPT_DIR/..}" && pwd)"
STUDIO_CI="${STUDIO_CI:-$REPO_ROOT/../studio-ci.sh}"
NIGHTLY_WT="${NIGHTLY_WT:-/tmp/rps-nightly-gate}"
CI_WALL_CAP_S="${CI_WALL_CAP_S:-16200}"
DATE_UTC="$(date -u +%Y-%m-%d)"
DATE_COMPACT="$(date -u +%Y%m%d)"
TIME_COMPACT="$(date -u +%H%M%S)"
# Include wall-clock time so same-day concurrent fires do not share JOB_NAME
# (and therefore junit / result paths). Override JOB_NAME to pin explicitly.
JOB_NAME="${JOB_NAME:-nightly-$DATE_COMPACT-$TIME_COMPACT}"
RESULT_DIR="$REPO_ROOT/docs-private/research/nightly-gates"
RESULT_MD="$RESULT_DIR/${DATE_UTC}.md"
# Concurrency guard: same-day fires share the worktree path by default;
# serialize via flock so two invocations cannot clobber each other.
# N3b: portable exclusive lock (mkdir is atomic on macOS + Linux; util-linux
# flock is not guaranteed on macOS). Stale lock older than the wall cap is
# stolen so a SIGKILL'd prior run cannot wedge the scheduler forever.
LOCK_DIR="${NIGHTLY_GATE_LOCK:-/tmp/rps-nightly-gate.lock.d}"
LOCK_HELD=0
STUDIO_CI_STARTED=0

# N3a: durable FAIL result on any early/non-zero exit before/around the run.
# Scheduler-friendly: greppable one-liner even when git/setup aborts.
_write_fail_result() {
  local rc="${1:-1}"
  local reason="${2:-early-exit}"
  mkdir -p "$RESULT_DIR" 2>/dev/null || true
  local one_line="${DATE_UTC} FAIL tip=${TIP_SHA:-unknown} job=${JOB_NAME} junit=(none) exit=${rc} wall_cap_s=${CI_WALL_CAP_S} reason=${reason}"
  {
    echo "# Nightly gate ${DATE_UTC}"
    echo
    echo "$one_line"
    echo
    echo "- branch/ref: \`${TIP_REF:-unknown}\`"
    echo "- tip: \`${TIP_SHA:-unknown}\`"
    echo "- job: \`${JOB_NAME}\`"
    echo "- wall_cap_s: ${CI_WALL_CAP_S}"
    echo "- worktree: \`${NIGHTLY_WT}\`"
    echo "- studio-ci exit: ${rc}"
    echo "- reason: ${reason}"
    echo "- junit: \`(none)\`"
  } > "$RESULT_MD" 2>/dev/null || true
  echo "nightly-gate: wrote FAIL result $RESULT_MD ($reason exit=$rc)" >&2
}

_release_lock() {
  if [ "$LOCK_HELD" -eq 1 ]; then
    # Contains a pid file; remove the whole lock dir (not bare rmdir).
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    LOCK_HELD=0
  fi
}

_on_exit() {
  local rc=$?
  # Success path and studio-ci path write their own result; only fill gaps.
  if [ "$rc" -ne 0 ] && [ "$STUDIO_CI_STARTED" -eq 0 ]; then
    _write_fail_result "$rc" "pre-studio-ci"
  fi
  _release_lock
  exit "$rc"
}
trap '_on_exit' EXIT

# N3b: exclusive lock so concurrent same-day fires do not share NIGHTLY_WT.
_acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    LOCK_HELD=1
    echo "$$" >"$LOCK_DIR/pid" 2>/dev/null || true
    return 0
  fi
  # Stale-lock recovery: if the lock dir is older than the wall cap, steal it.
  local age_s=0
  if [ -d "$LOCK_DIR" ]; then
    # portable mtime age (GNU + BSD stat)
    local mtime
    mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)"
    local now
    now="$(date +%s)"
    age_s=$((now - mtime))
  fi
  if [ "$age_s" -gt "$CI_WALL_CAP_S" ]; then
    echo "nightly-gate: stealing stale lock $LOCK_DIR (age ${age_s}s > wall cap ${CI_WALL_CAP_S}s)" >&2
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      LOCK_HELD=1
      echo "$$" >"$LOCK_DIR/pid" 2>/dev/null || true
      return 0
    fi
  fi
  return 1
}

if ! _acquire_lock; then
  echo "nightly-gate: another run holds $LOCK_DIR; refusing concurrent fire" >&2
  # Do NOT write RESULT_MD here — the holder owns today's result file.
  STUDIO_CI_STARTED=1  # suppress trap's FAIL writer
  exit 3
fi

if [ ! -x "$STUDIO_CI" ] && [ -f "$STUDIO_CI" ]; then
  chmod +x "$STUDIO_CI" || true
fi
if [ ! -f "$STUDIO_CI" ]; then
  echo "nightly-gate: studio-ci.sh not found at $STUDIO_CI" >&2
  # N3d: studio-ci is an untracked sibling owned by the controller; missing
  # it is an environment/setup failure, not a suite failure.
  exit 2
fi

cd "$REPO_ROOT"
if [ -n "${BRANCH:-}" ]; then
  TIP_REF="$BRANCH"
else
  # Prefer symbolic branch name; fall back to main / master / HEAD.
  TIP_REF="$(git symbolic-ref -q --short HEAD 2>/dev/null || true)"
  if [ -z "$TIP_REF" ]; then
    if git show-ref --verify --quiet refs/heads/main; then
      TIP_REF=main
    elif git show-ref --verify --quiet refs/heads/master; then
      TIP_REF=master
    else
      TIP_REF=HEAD
    fi
  fi
fi
TIP_SHA="$(git rev-parse "${TIP_REF}^{commit}")"
echo "nightly-gate: tip $TIP_REF @ $TIP_SHA"

# N3e: refuse to delete a path that exists, is non-empty, and is not a git
# worktree (env override of NIGHTLY_WT could otherwise wipe arbitrary dirs).
_safe_rm_nightly_wt() {
  local path="$1"
  if [ ! -e "$path" ]; then
    return 0
  fi
  if git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    rm -rf "$path"
    return 0
  fi
  # Empty directory is fine to remove; non-empty non-worktree is refused.
  if [ -d "$path" ] && [ -z "$(ls -A "$path" 2>/dev/null || true)" ]; then
    rmdir "$path" 2>/dev/null || rm -rf "$path"
    return 0
  fi
  echo "nightly-gate: refusing to delete NIGHTLY_WT=$path (exists, non-empty, not a git worktree)" >&2
  return 1
}

# N3c: prune stale worktree registrations before either add path so a
# leftover registration cannot make `worktree add` fail after rm -rf.
git worktree prune 2>/dev/null || true

# Create or refresh a pinned worktree at NIGHTLY_WT on the tip commit.
if [ -d "$NIGHTLY_WT" ]; then
  # Existing worktree: hard-reset to tip (detached), keep it clean for rsync.
  if git -C "$NIGHTLY_WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$NIGHTLY_WT" fetch --quiet origin 2>/dev/null || true
    git -C "$NIGHTLY_WT" checkout --detach "$TIP_SHA"
    git -C "$NIGHTLY_WT" reset --hard "$TIP_SHA"
    git -C "$NIGHTLY_WT" clean -fdx -e .venv 2>/dev/null || git -C "$NIGHTLY_WT" clean -fd
  else
    _safe_rm_nightly_wt "$NIGHTLY_WT"
    git worktree prune 2>/dev/null || true
    git worktree add --detach "$NIGHTLY_WT" "$TIP_SHA"
  fi
else
  git worktree add --detach "$NIGHTLY_WT" "$TIP_SHA"
fi

echo "nightly-gate: worktree $NIGHTLY_WT @ $(git -C "$NIGHTLY_WT" rev-parse HEAD)"
echo "nightly-gate: invoking studio-ci full tier (CI_WALL_CAP_S=$CI_WALL_CAP_S job=$JOB_NAME)"

STUDIO_CI_STARTED=1
set +e
CI_WALL_CAP_S="$CI_WALL_CAP_S" "$STUDIO_CI" "$NIGHTLY_WT" "$JOB_NAME" full
RC=$?
set -e

JUNIT_SRC="/tmp/ci-$JOB_NAME.xml"
mkdir -p "$RESULT_DIR"
JUNIT_DST="$RESULT_DIR/${DATE_UTC}-junit.xml"
if [ -f "$JUNIT_SRC" ]; then
  cp -f "$JUNIT_SRC" "$JUNIT_DST"
else
  JUNIT_DST="(missing: $JUNIT_SRC)"
fi

STATUS="FAIL"
if [ "$RC" -eq 0 ]; then
  STATUS="PASS"
fi
# One-line result for the date file (scheduler-friendly greppable).
# Format: DATE STATUS tip=<sha> job=<name> junit=<path> exit=<rc> wall_cap_s=<n>
ONE_LINE="${DATE_UTC} ${STATUS} tip=${TIP_SHA} job=${JOB_NAME} junit=${JUNIT_DST} exit=${RC} wall_cap_s=${CI_WALL_CAP_S}"
{
  echo "# Nightly gate ${DATE_UTC}"
  echo
  echo "$ONE_LINE"
  echo
  echo "- branch/ref: \`${TIP_REF}\`"
  echo "- tip: \`${TIP_SHA}\`"
  echo "- job: \`${JOB_NAME}\`"
  echo "- wall_cap_s: ${CI_WALL_CAP_S}"
  echo "- worktree: \`${NIGHTLY_WT}\`"
  echo "- studio-ci exit: ${RC}"
  echo "- junit: \`${JUNIT_DST}\`"
} > "$RESULT_MD"

echo "nightly-gate: wrote $RESULT_MD"
echo "$ONE_LINE"
# Clear trap's early-exit writer; we already wrote the durable result.
# Release the concurrency lock explicitly (trap would also do this, but we
# clear the trap so the early-exit FAIL writer does not re-fire).
_release_lock
trap - EXIT
exit "$RC"
