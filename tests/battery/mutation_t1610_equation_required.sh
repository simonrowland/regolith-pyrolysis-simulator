#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
target="$repo_root/simulator/battery/records.py"
: "${TMPDIR:?set a writable task temp directory before running this script}"

export MPLCONFIGDIR="${MPLCONFIGDIR:-$TMPDIR/mplcache}"
export OMPI_MCA_orte_tmpdir_base="${OMPI_MCA_orte_tmpdir_base:-$TMPDIR/ompi}"
export PRTE_MCA_prte_tmpdir_base="${PRTE_MCA_prte_tmpdir_base:-$TMPDIR/ompi}"
mkdir -p "$TMPDIR" "$MPLCONFIGDIR" \
  "$OMPI_MCA_orte_tmpdir_base" "$PRTE_MCA_prte_tmpdir_base"
backup="$TMPDIR/t1610-records.XXXXXX"
log="$TMPDIR/t1610-mutation.XXXXXX"
python_bin="$repo_root/../../.venv/bin/python"
export PYTHONPATH="$repo_root"

backup=$(mktemp "$backup")
log=$(mktemp "$log")
cp "$target" "$backup"
restore() {
  cp "$backup" "$target"
  rm -f "$backup" "$log"
}
trap restore EXIT
trap 'exit 1' HUP INT TERM

perl -0pi -e 's/and has_section_or_equation/and has_section_or_equation\n            and isinstance(locator.equation, str)\n            and bool(locator.equation.strip())/ or die "could not apply equation-required mutation\n"' "$target"

set +e
"$python_bin" \
  -m pytest -q -n 0 -p no:cacheprovider \
  tests/battery/test_mechanisms.py \
  -k test_model_derived_prose_method_without_equation_passes >"$log" 2>&1
status=$?
set -e
cat "$log"

if [ "$status" -eq 0 ]; then
  echo "equation-required mutation survived the prose-method acceptance test" >&2
  exit 1
fi
if ! rg -q "FAILED .*test_model_derived_prose_method_without_equation_passes" "$log"; then
  echo "mutation failed for an unexpected reason" >&2
  exit 1
fi
echo "equation-required mutation made the prose-method acceptance test fail"
