# MRE literature-reproduction presets

These presets are diagnostic execution packages, not plant recipes.

Required top-level identity:

- `schema_version: mre_reproduction_preset.v1`
- `preset_kind: mre_reproduction`
- `execution_scope: literature_reproduction_only`
- a DOI-backed `paper_citation_id`
- an independent `comparison_contract.observation_sidecar_path`

Every published or assumed numeric input carries `value`, `unit`, `status`,
and `source_locator`. Expected results stay in the independent literature
sidecar. A galvanostatic case may reference a published measured-voltage
trajectory from that sidecar because the builtin electrolysis intent requires
an applied potential; the replay is then ineligible as a predicted observable.

Plant fields (`c5_enabled`, MRE target/max-voltage policy, ladders, minimum
holds, and 1000/3000 A aliases) are refused. Missing published figure
transcriptions are also refusals: values must never be estimated at runtime.
