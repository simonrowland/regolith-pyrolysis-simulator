#!/usr/bin/env bash
# Engine patch manager. Engine checkouts are outside this repo and are NOT
# version-controlled by us, so local edits are invisible drift unless captured here.
#
#   enginepatch.sh [--python PATH] verify  [engine]   # engine tree == patch set? (default: all)
#   enginepatch.sh [--python PATH] apply   <engine>   # apply patches onto a clean checkout
#   enginepatch.sh [--python PATH] refresh <engine>   # re-capture patches from a dirty tree
#   enginepatch.sh [--python PATH] status  [engine]   # base SHA, drift, patch list
#
# Exit 0 = match, 1 = drift/failure. Safe to run in CI.
set -euo pipefail

# pwd -P is load-bearing. Reached through a symlink (e.g. ~/Repos/<repo> ->
# a synced folder), bash's logical pwd can select the wrong repo-local venv or
# patch directory. Canonical paths keep interpreter and patch lookup aligned.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PATCHES="$(dirname "$HERE")"
REPO_ROOT="$(cd "$PATCHES/.." && pwd -P)"

# A pooled worktree has the main checkout's .git as its common directory. Use
# that checkout's venv when the worktree itself has no .venv (the normal seat
# layout), while still preferring a worktree-local venv when one exists.
COMMON_REPO_ROOT=""
if COMMON_GIT_DIR="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  COMMON_REPO_ROOT="$(cd "$COMMON_GIT_DIR/.." && pwd -P)"
fi

PYTHON_ARG=""
PYTHON=""
FORCE_PATH=0

engine_package() {
  case "$1" in
    vaporock)     echo "vaporock" ;;
    thermoengine) echo "thermoengine" ;;
    pysulfsat)    echo "PySulfSat" ;;
    sulfliq)      echo "SulfLiq" ;;
    *)            echo "" ;;
  esac
}

engine_override_var() {
  case "$1" in
    vaporock)     echo "VAPOROCK_CHECKOUT" ;;
    thermoengine) echo "THERMOENGINE_CHECKOUT" ;;
    pysulfsat)    echo "PYSULFSAT_CHECKOUT" ;;
    sulfliq)      echo "SULFLIQ_CHECKOUT" ;;
    *)            echo "" ;;
  esac
}

engine_override() {
  case "$1" in
    vaporock)     echo "${VAPOROCK_CHECKOUT:-}" ;;
    thermoengine) echo "${THERMOENGINE_CHECKOUT:-}" ;;
    pysulfsat)    echo "${PYSULFSAT_CHECKOUT:-}" ;;
    # Preserve sulfliq's historical default, but actions against it still
    # require --force-path when the selected interpreter cannot import it.
    sulfliq)      echo "${SULFLIQ_CHECKOUT:-$HOME/Repos/sulfliq}" ;;
    *)            echo "" ;;
  esac
}

select_python() {
  if [ -n "$PYTHON_ARG" ]; then
    [ -x "$PYTHON_ARG" ] || {
      echo "python interpreter is not executable: $PYTHON_ARG" >&2
      return 2
    }
    echo "$PYTHON_ARG"
    return 0
  fi

  for candidate in "$REPO_ROOT/.venv/bin/python" "$COMMON_REPO_ROOT/.venv/bin/python"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && {
      echo "$candidate"
      return 0
    }
  done
  command -v python3
}

# Return the source directory for a top-level package without executing the
# package. Editable installs expose the real source tree through this lookup.
python_import_root() {
  "$PYTHON" - "$1" <<'PY'
import importlib.util
import os
import sys

try:
    spec = importlib.util.find_spec(sys.argv[1])
except Exception:
    raise SystemExit(1)

if spec is None:
    raise SystemExit(1)

locations = spec.submodule_search_locations
if locations:
    print(os.path.realpath(next(iter(locations))))
elif spec.origin and spec.origin not in {"built-in", "frozen"}:
    print(os.path.realpath(os.path.dirname(spec.origin)))
else:
    raise SystemExit(1)
PY
}

# Globals set by resolve_engine / validate_checkout for the current engine.
RESOLVED_PATH=""
IMPORTABLE=0
RESOLVE_ERROR=""
CHECKOUT_STATUS=""

resolve_engine() {
  local e="$1" package source override top override_var
  RESOLVED_PATH=""
  IMPORTABLE=0
  RESOLVE_ERROR=""

  package="$(engine_package "$e")"
  if [ -n "$package" ] && source="$(python_import_root "$package" 2>/dev/null)"; then
    IMPORTABLE=1
    if top="$(git -C "$source" rev-parse --show-toplevel 2>/dev/null)"; then
      RESOLVED_PATH="$(cd "$top" && pwd -P)"
    else
      RESOLVED_PATH="$source"
      RESOLVE_ERROR="imported package is not inside a git checkout"
    fi
    return 0
  fi

  override="$(engine_override "$e")"
  override_var="$(engine_override_var "$e")"
  if [ -n "$override" ]; then
    if [ -d "$override" ]; then
      override="$(cd "$override" && pwd -P)"
      if top="$(git -C "$override" rev-parse --show-toplevel 2>/dev/null)"; then
        RESOLVED_PATH="$(cd "$top" && pwd -P)"
      else
        RESOLVED_PATH="$override"
      fi
    else
      RESOLVED_PATH="$override"
    fi
    return 0
  fi

  RESOLVE_ERROR="package ${package:-<none>} is not importable and no ${override_var:-engine}_CHECKOUT override is set"
  return 1
}

validate_checkout() {
  local e="$1" top have want
  CHECKOUT_STATUS=""

  if [ -z "$RESOLVED_PATH" ]; then
    CHECKOUT_STATUS="unresolved"
    return 1
  fi
  if [ ! -d "$RESOLVED_PATH" ]; then
    CHECKOUT_STATUS="checkout missing"
    return 1
  fi
  if ! top="$(git -C "$RESOLVED_PATH" rev-parse --show-toplevel 2>/dev/null)"; then
    CHECKOUT_STATUS="not a git checkout"
    return 1
  fi
  RESOLVED_PATH="$(cd "$top" && pwd -P)"

  want="$(pin_sha "$e")"
  have="$(git -C "$RESOLVED_PATH" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$have" ]; then
    CHECKOUT_STATUS="cannot read HEAD"
    return 1
  fi
  if [ -n "$want" ] && [ "$have" != "$want" ]; then
    CHECKOUT_STATUS="base DRIFTED (pin ${want:0:8}, head ${have:0:8})"
    return 1
  fi
  CHECKOUT_STATUS="base OK"
  return 0
}

prepare_action_target() {
  local e="$1" override_var
  resolve_engine "$e" || true
  if [ -z "$RESOLVED_PATH" ]; then
    echo "$e: refusing action — $RESOLVE_ERROR" >&2
    return 1
  fi
  if [ "$IMPORTABLE" -eq 0 ] && [ "$FORCE_PATH" -ne 1 ]; then
    override_var="$(engine_override_var "$e")"
    echo "$e: refusing action on RESOLVED=$RESOLVED_PATH (interpreter does not load it; pass --force-path for $override_var)" >&2
    return 1
  fi
  if ! validate_checkout "$e"; then
    echo "$e: refusing action on RESOLVED=$RESOLVED_PATH ($CHECKOUT_STATUS)" >&2
    return 1
  fi
}

engines() { for d in "$PATCHES"/*/; do b="$(basename "$d")"; [ "$b" = scripts ] && continue; echo "$b"; done; }

pin_sha() { [ -f "$PATCHES/$1/UPSTREAM.pin" ] && awk '/^base_sha:/{print $2}' "$PATCHES/$1/UPSTREAM.pin" || echo ""; }

# pip_target: the patch dir targets a pip-installed package (site-packages of a
# named venv), not a sibling git checkout. Declared explicitly in UPSTREAM.pin
# (install_kind: pip-venv), never inferred — an undeclared dir with no checkout
# mapping stays a hard FAILURE, per the couldnt-check-never-reads-clean rule.
pip_target() {
  awk '$1=="install_kind:" && $2=="pip-venv"{found=1} END{exit !found}'     "$PATCHES/$1/UPSTREAM.pin" 2>/dev/null
}

report_only() {
  ! ls "$PATCHES/$1"/*.patch >/dev/null 2>&1 &&
    awk '$1=="checkout:" && $2=="absent"{found=1} END{exit !found}' \
      "$PATCHES/$1/UPSTREAM.pin" 2>/dev/null
}

cmd_status() {
  rc=0
  for e in ${1:-$(engines)}; do
    if report_only "$e"; then
      echo "$e: RESOLVED=absent REPORT-ONLY (source checkout absent; no engine patch)"
      continue
    fi
    if pip_target "$e"; then
      # Verification for a pip target happens ON THE BOX that holds the venv,
      # via patches/<e>/verify-applied.sh <python>. On other hosts this line is
      # a declared remote-scope notice, not a pass: it never says clean.
      echo "$e: RESOLVED=pip-venv PIP-TARGET (verify on the target box: patches/$e/verify-applied.sh <venv-python>)"
      continue
    fi
    resolve_engine "$e" || true
    if ! validate_checkout "$e"; then
      echo "$e: FAIL RESOLVED=${RESOLVED_PATH:-unresolved} — ${RESOLVE_ERROR:-$CHECKOUT_STATUS}"
      rc=1
      continue
    fi
    n=$(ls "$PATCHES/$e"/*.patch 2>/dev/null | wc -l | tr -d ' ')
    dirty=$(git -C "$RESOLVED_PATH" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    echo "$e: RESOLVED=$RESOLVED_PATH patches=$n  base=$CHECKOUT_STATUS  dirty_files=$dirty"
  done
  return $rc
}

# verify: does the engine's current diff equal the concatenated patch set?
cmd_verify() {
  rc=0
  for e in ${1:-$(engines)}; do
    if report_only "$e"; then
      echo "$e: REPORT-ONLY RESOLVED=absent (source checkout absent; no engine patch)"
      continue
    fi
    if pip_target "$e"; then
      if [ -n "${MACE_PYTHON:-}" ] && [ "$e" = "mace" ]; then
        if "$PATCHES/$e/verify-applied.sh" "$MACE_PYTHON"; then
          echo "$e: PIP-TARGET verify OK (via MACE_PYTHON) RESOLVED=pip-venv"
        else
          echo "$e: PIP-TARGET verify FAILED (via MACE_PYTHON) RESOLVED=pip-venv"; rc=1
        fi
      else
        echo "$e: PIP-TARGET RESOLVED=pip-venv (venv not bound on this host; run patches/$e/verify-applied.sh <venv-python> on the target box — this line is not a pass)"
      fi
      continue
    fi
    resolve_engine "$e" || true
    if ! validate_checkout "$e"; then
      echo "$e: FAIL RESOLVED=${RESOLVED_PATH:-unresolved} — ${RESOLVE_ERROR:-$CHECKOUT_STATUS}"
      rc=1
      continue
    fi
    live="$(mktemp)"; want="$(mktemp)"
    git -C "$RESOLVED_PATH" diff HEAD > "$live"
    : > "$want"
    for p in "$PATCHES/$e"/*.patch; do
      [ -e "$p" ] || continue
      # skip patches explicitly declared unapplied in STATUS (documented, not in tree)
      if [ -f "$PATCHES/$e/STATUS" ] && \
         awk -v f="$(basename "$p")" '$1==f && $2=="unapplied"{found=1} END{exit !found}' "$PATCHES/$e/STATUS"; then
        continue
      fi
      cat "$p" >> "$want"
    done
    # compare the set of changed +/- lines, not byte-identical headers: patch
    # files are captured at different times and carry differing index/context lines.
    norm() { grep -E '^[+-]' "$1" | grep -Ev '^(\+\+\+|---)' | sort; }
    if diff -q <(norm "$live") <(norm "$want") >/dev/null 2>&1; then
      echo "$e: MATCH RESOLVED=$RESOLVED_PATH"
    else
      echo "$e: DRIFT — engine tree differs from patch set RESOLVED=$RESOLVED_PATH"
      echo "  in tree but not in patches:"; comm -23 <(norm "$live") <(norm "$want") | head -8 | sed 's/^/    /'
      echo "  in patches but not in tree:"; comm -13 <(norm "$live") <(norm "$want") | head -8 | sed 's/^/    /'
      rc=1
    fi
    rm -f "$live" "$want"
  done
  return $rc
}

cmd_apply() {
  e="${1:?engine required}"
  report_only "$e" && {
    echo "$e is report-only; no source patch or checkout to apply"
    return 1
  }
  pip_target "$e" && {
    echo "$e is a pip target; use its verify/apply procedure instead"
    return 1
  }
  prepare_action_target "$e" || return 1
  d="$RESOLVED_PATH"
  for p in "$PATCHES/$e"/*.patch; do
    [ -e "$p" ] || continue
    echo "applying $(basename "$p")"
    git -C "$d" apply --check "$p" 2>/dev/null && git -C "$d" apply "$p" || {
      echo "  FAILED — resolve by hand, then: $0 refresh $e"; return 1; }
  done
  echo "$e: applied RESOLVED=$d"
}

# refresh only rewrites 0001 when it is the sole patch; multi-patch engines must be
# re-split by hand so the one-thing-per-patch rule is not silently collapsed.
cmd_refresh() {
  e="${1:?engine required}"
  report_only "$e" && {
    echo "$e is report-only; no source checkout to refresh"
    return 1
  }
  pip_target "$e" && {
    echo "$e is a pip target; use its verify/apply procedure instead" >&2
    return 1
  }
  prepare_action_target "$e" || return 1
  d="$RESOLVED_PATH"
  n=$(ls "$PATCHES/$e"/*.patch 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" -gt 1 ]; then
    echo "$e has $n patches; refusing to collapse them into one."
    echo "Re-capture the changed patch by hand, then run: $0 verify $e"
    return 1
  fi
  mkdir -p "$PATCHES/$e"
  out="$PATCHES/$e/0001-local.patch"
  tmp="$(mktemp "$PATCHES/$e/.enginepatch-refresh.XXXXXX")"
  if ! git -C "$d" diff HEAD > "$tmp"; then
    echo "$e: git diff failed for $d" >&2
    rm -f "$tmp"
    return 1
  fi
  if [ ! -s "$tmp" ]; then
    echo "$e: refusing empty patch capture (working tree clean or diff failed)" >&2
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$out"
  echo "$e: recaptured to 0001-local.patch"
}

COMMAND="status"
COMMAND_SET=0
ENGINE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --python)
      [ "$#" -ge 2 ] || { echo "--python requires a path" >&2; exit 2; }
      PYTHON_ARG="$2"
      shift 2
      ;;
    --python=*)
      PYTHON_ARG="${1#*=}"
      shift
      ;;
    --force-path)
      FORCE_PATH=1
      shift
      ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    status|verify|apply|refresh)
      [ "$COMMAND_SET" -eq 0 ] || { echo "multiple commands supplied" >&2; exit 2; }
      COMMAND="$1"
      COMMAND_SET=1
      shift
      ;;
    -* )
      echo "unknown option: $1" >&2
      exit 2
      ;;
    *)
      [ -z "$ENGINE" ] || { echo "multiple engines supplied" >&2; exit 2; }
      ENGINE="$1"
      shift
      ;;
  esac
done

PYTHON="$(select_python)" || exit $?
case "$COMMAND" in
  status)  cmd_status "$ENGINE" ;;
  verify)  cmd_verify "$ENGINE" ;;
  apply)   [ -n "$ENGINE" ] || { echo "apply requires an engine" >&2; exit 2; }; cmd_apply "$ENGINE" ;;
  refresh) [ -n "$ENGINE" ] || { echo "refresh requires an engine" >&2; exit 2; }; cmd_refresh "$ENGINE" ;;
esac
