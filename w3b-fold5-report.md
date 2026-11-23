# W3b fold round 5 report

## Status

P2 and P3 are complete. P1 is transferred to the canonical-buffer car by
controller ruling; I made no phase-row code changes. The Xu audit now describes
the current shared-preflight refusal and names the transferred d-460 owner and
the registry rungs DP-112, DP-003, and DP-010.

The source methods describe the runs as using “PtO2 as the oxygen source” and
state that “no PtO2 is present in the samples after the experiments,” so the
actual fO2 is “more reduced than the fO2 of the Pt–PtO2 buffer.” Table 1 lists
PtO2/No PtO2 controls; it does not print an absolute run fO2, an in-air
atmosphere, or a buffer offset for these rows. The canonical-buffer car owns
applying the default policy. No default was added to the extract.

No engine values moved at a previously valid input, so I did not bump
`ENGINE_CHEMISTRY_VERSION`. The all-source preflight census, flagged
engine-boundary assertion, and refusal-restoration mutation proof are
transferred with P1.

## Probe evidence

### P1 LVP-1 preflight

Before:

```text
migrated observations 1462 hard issues 0 experiments 22
LVP-1 Si: 23.40 wt.%, T=1673.15 K, P=101325 Pa, fO2 unknown
identity_incomplete {'reason': 'missing_input', 'input': 'fO2_Pa'} not_probed engine_call=0
```

After the P2/P3 audit changes, and before the canonical-buffer car applies its
fix, the current preflight still returns:

```text
identity_incomplete {'reason': 'missing_input', 'input': 'fO2_Pa'} not_probed engine_call=0
default-policy registry exists: False
```

This confirms the audit's revised description of current behavior. The
source-wide decision census belongs to the canonical-buffer fix round.

### P2 complete audit YAML parse

Before:

```text
rusiecka-2026-chlorine-data.mapping.yaml: ScannerError, mapping values are not allowed here
xu-2026-excess-oxygen-magma.mapping.yaml: parsed
```

After:

```text
rusiecka-2026-chlorine-data.mapping.yaml: parsed
xu-2026-excess-oxygen-magma.mapping.yaml: parsed
```

The Rusiecka `Z` text is now a folded scalar and retains its wording.

### P3 isolated migration census

Before, direct migration/finalization of the canonical Xu extract produced:

```text
observations=1462; hard_issues=0; experiments=22
quantity_counts={equilibrium_phase_composition: 1461, valence_ratio: 1}
uncertainty_kind_counts={none: 1199, printed: 263}
```

The canonical migration result reproduced those counts after the audit edit.
`isolated_migration` now records this result: 1,462 observations, 1,461 phase
composition rows, one valence ratio, and uncertainty counts of 1,199 none and
263 printed. The prior 1,521 total mixed the migration result with 58
unknown-context inventory records and one residue candidate; those are no
longer labelled as migrated observations.

## Change and verification

- Changed only the Rusiecka mapping audit and Xu mapping audit.
- Complete `yaml.safe_load` was rerun on both touched audits after the edits:
  `rusiecka-2026-chlorine-data.mapping.yaml: parsed`; `xu-2026-excess-oxygen-magma.mapping.yaml: parsed`.
- Direct Python migration probes passed; no pytest or remote CI was run.
- No store regeneration or store delta; no production or extract edits.
- Undefined-name sweep was not applicable because no Python source was
  changed. `git diff --check` passed. All migration scratch output was removed.

## Queued remote runs

- `tests/battery/test_phase_composition_rows.py` (canonical-buffer car)
- `tests/battery/test_fo2_ladder.py::test_tier3_buffer_matches_the_oxygen_condition_point`
- `tests/battery/test_fo2_ladder.py::test_tier_notice_reaches_the_residual`
- `tests/test_literature_extracts.py::test_audit_mapping_fidelity_samples_match_canonical_extracts`
- `tests/test_literature_extracts.py::test_fidelity_sample_matches_extract[xu-2026-excess-oxygen-magma]`
- `tests/test_literature_extract_validation_gate.py::test_committed_extracts_in_family_validate[t-z]`

LAND-PENDING-CI
