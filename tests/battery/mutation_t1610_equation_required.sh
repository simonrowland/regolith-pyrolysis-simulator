#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
task_scratch=/private/tmp/claude-501/main-review/w1103t1610
target="$repo_root/simulator/battery/records.py"
backup="$task_scratch/tmp/t1610-records.XXXXXX"
log="$task_scratch/tmp/t1610-mutation.XXXXXX"

mkdir -p "$task_scratch/tmp" "$task_scratch/ompi" \
  /private/tmp/claude-501/main-review/mplcache
export DYLD_FALLBACK_LIBRARY_PATH=/Users/simonrowland/lib
export MPLCONFIGDIR=/private/tmp/claude-501/main-review/mplcache
export OMPI_MCA_orte_tmpdir_base="$task_scratch/ompi"
export PRTE_MCA_prte_tmpdir_base="$task_scratch/ompi"
export TMPDIR="$task_scratch/tmp"
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
/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python \
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
