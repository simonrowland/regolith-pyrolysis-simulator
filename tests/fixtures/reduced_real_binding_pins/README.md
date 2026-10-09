# Reviewed reduced-real binding pins

This directory contains owner-reviewed producer output projections. It is
intentionally empty in chunk 3a. The synthetic pin in
`../binding_admission_synthetic/` is test-only and must never be copied here.

## File format

Use one UTF-8 JSON file for each producer, resolved model, binding revision,
transport, and artifact. The assessment command combines artifacts for the
same producer/model/revision/transport into one receipt entry. Each file has:

```json
{
  "schema_version": 1,
  "identity": {
    "engine_id": "alphamelts",
    "model_id": "MELTSv1.0.2",
    "binding_revision": "alphamelts-r1",
    "transport": "subprocess"
  },
  "artifact": "equilibrium_post_record",
  "probe": {
    "feedstock_id": "lunar_mare_low_ti",
    "mass_kg": 1000.0,
    "campaign": "C2A_STAGED",
    "temperature_C": 950.0
  },
  "projection": {},
  "review": {
    "owner": "<binding owner>",
    "reviewer": "<independent reviewer>",
    "basis": "current green output"
  }
}
```

`projection` is the complete value returned by
`simulator.reduced_real_determinism.canonical_replay_output_projection` for
the named artifact and probe. Do not hand-copy or independently reimplement its
field selection. The probe fields accepted by the command are `feedstock_id`,
`mass_kg`, `campaign`, `temperature_C`, `high_t_melt_activity`, and
`active_backend`. AlphaMELTS and ThermoEngine probes select their own backend
from the identity; other probes default to `internal-analytical` unless an
`active_backend` is recorded.

Equilibrium probes for OpenIMCC must set `high_t_melt_activity` to `openimcc`
and use an input temperature above the VapoRock cap. SulfSat probes need a
sulfur-bearing feedstock and an available PySulfSat result. The assessment
refuses a producer pin when those input selections do not activate that
producer, or when the selected OpenIMCC/SulfSat call returns fallback output.

The assessment writes the host-local receipt to
`engines/engines.local.binding-admission.json`. It records a comparison for
every pin and fingerprints the local engine configuration, resolved paths,
runtime artifacts, selected source digests, commissioning snapshot, and catalog
manifest. A missing pin is a failed result with reason
`no reviewed pins for <producer>/<transport>`; it never produces an admitted
receipt entry.

## Producing and reviewing pins

The named binding owner chooses representative current-green probes for each
supported artifact and transport, then records the canonical projection from
that green result as the pin. Do not derive a reviewed pin by assessing the
same unreviewed installation that will consume its receipt. A second reviewer
checks the probe, output projection, resolved model/database identity, and
transport against the owner’s current-green evidence before the file is added.
The reviewer records both names and the evidence basis in `review`.

The initial binding revisions are `alphamelts-r1` per resolved model,
`thermoengine-r1`, `magemin-ig-r1`, `builtin-vapor-pressure-r1`,
`openimcc-sf04-r1`, and `sulfsat-r1`. AlphaMELTS `subprocess` and `python_api`
are distinct pin transports even when they share the same model revision.
ThermoEngine uses `native`; MAGEMin uses `subprocess`; the builtin vapor
pressure, OpenIMCC, and SulfSat transports are `native`, `python_api`, and
`python_api`, respectively.

The binding revision names the owner-reviewed output contract, not a package
version. Reassessment that matches the reviewed projection refreshes host
provenance without changing the revision. If output changes, the owner reviews
the cause and issues the next revision only after the changed projection is
reviewed. Runtime package versions remain provenance and do not replace the
binding revision.
