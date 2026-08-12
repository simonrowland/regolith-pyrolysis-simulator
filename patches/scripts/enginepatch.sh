#!/usr/bin/env bash
# Engine patch manager. Engine checkouts are siblings of this repo and are NOT
# version-controlled by us, so local edits are invisible drift unless captured here.
#
#   enginepatch.sh verify  [engine]   # engine tree == patch set? (default: all)
#   enginepatch.sh apply   <engine>   # apply patches onto a clean checkout
#   enginepatch.sh refresh <engine>   # re-capture patches from a dirty tree
#   enginepatch.sh status  [engine]   # base SHA, drift, patch list
#
# Exit 0 = match, 1 = drift/failure. Safe to run in CI.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES="$(dirname "$HERE")"
SIBLINGS="$(cd "$PATCHES/../.." && pwd)"

engine_dir() {  # patch-dir name -> checkout path (siblings by default; sulfliq is non-sibling)
  case "$1" in
    vaporock)     echo "$SIBLINGS/VapoRock" ;;
    thermoengine) echo "$SIBLINGS/ThermoEngine" ;;
    pysulfsat)    echo "$SIBLINGS/PySulfSat" ;;
    sulfliq)      echo "${SULFLIQ_CHECKOUT:-$HOME/Repos/sulfliq}" ;;
    *)            echo "" ;;
  esac
}

engines() { for d in "$PATCHES"/*/; do b="$(basename "$d")"; [ "$b" = scripts ] && continue; echo "$b"; done; }

pin_sha() { [ -f "$PATCHES/$1/UPSTREAM.pin" ] && awk '/^base_sha:/{print $2}' "$PATCHES/$1/UPSTREAM.pin" || echo ""; }

report_only() {
  ! ls "$PATCHES/$1"/*.patch >/dev/null 2>&1 &&
    awk '$1=="checkout:" && $2=="absent"{found=1} END{exit !found}' \
      "$PATCHES/$1/UPSTREAM.pin" 2>/dev/null
}

cmd_status() {
  rc=0
  for e in ${1:-$(engines)}; do
    if report_only "$e"; then
      echo "$e: REPORT-ONLY (source checkout absent; no engine patch)"
      continue
    fi
    d="$(engine_dir "$e")"
    # A tracked patch dir whose checkout is absent is a FAILURE, not a skip — this
    # directory exists to catch drift, and "couldn't check" must never read as clean
    # (milestone sweep P1-5: sulfliq passed verify while never being checked).
    if [ -z "$d" ] || [ ! -d "$d" ]; then echo "$e: checkout MISSING"; rc=1; continue; fi
    have="$(git -C "$d" rev-parse HEAD 2>/dev/null)"
    want="$(pin_sha "$e")"
    n=$(ls "$PATCHES/$e"/*.patch 2>/dev/null | wc -l | tr -d ' ')
    dirty=$(git -C "$d" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    base_ok="OK"; [ -n "$want" ] && [ "$have" != "$want" ] && base_ok="DRIFTED (pin ${want:0:8}, head ${have:0:8})"
    echo "$e: patches=$n  base=$base_ok  dirty_files=$dirty"
  done
  return $rc
}

# verify: does the engine's current diff equal the concatenated patch set?
cmd_verify() {
  rc=0
  for e in ${1:-$(engines)}; do
    if report_only "$e"; then
      echo "$e: REPORT-ONLY (source checkout absent; no engine patch)"
      continue
    fi
    d="$(engine_dir "$e")"
    [ -d "$d" ] || { echo "$e: checkout MISSING (verify FAILED — cannot confirm no drift)"; rc=1; continue; }
    live="$(mktemp)"; want="$(mktemp)"
    git -C "$d" diff > "$live"
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
      echo "$e: MATCH"
    else
      echo "$e: DRIFT — engine tree differs from patch set"
      echo "  in tree but not in patches:"; comm -23 <(norm "$live") <(norm "$want") | head -8 | sed 's/^/    /'
      echo "  in patches but not in tree:"; comm -13 <(norm "$live") <(norm "$want") | head -8 | sed 's/^/    /'
      rc=1
    fi
    rm -f "$live" "$want"
  done
  return $rc
}

cmd_apply() {
  e="${1:?engine required}"; d="$(engine_dir "$e")"
  report_only "$e" && {
    echo "$e is report-only; no source patch or checkout to apply"
    return 1
  }
  [ -d "$d" ] || { echo "no checkout for $e"; return 1; }
  for p in "$PATCHES/$e"/*.patch; do
    [ -e "$p" ] || continue
    echo "applying $(basename "$p")"
    git -C "$d" apply --check "$p" 2>/dev/null && git -C "$d" apply "$p" || {
      echo "  FAILED — resolve by hand, then: $0 refresh $e"; return 1; }
  done
  echo "$e: applied"
}

# refresh only rewrites 0001 when it is the sole patch; multi-patch engines must be
# re-split by hand so the one-thing-per-patch rule is not silently collapsed.
cmd_refresh() {
  e="${1:?engine required}"; d="$(engine_dir "$e")"
  report_only "$e" && {
    echo "$e is report-only; no source checkout to refresh"
    return 1
  }
  n=$(ls "$PATCHES/$e"/*.patch 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" -gt 1 ]; then
    echo "$e has $n patches; refusing to collapse them into one."
    echo "Re-capture the changed patch by hand, then run: $0 verify $e"
    return 1
  fi
  git -C "$d" diff > "$PATCHES/$e/0001-local.patch"
  echo "$e: recaptured to 0001-local.patch"
}

case "${1:-status}" in
  status)  shift; cmd_status "${1:-}" ;;
  verify)  shift; cmd_verify "${1:-}" ;;
  apply)   shift; cmd_apply "${1:-}" ;;
  refresh) shift; cmd_refresh "${1:-}" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
