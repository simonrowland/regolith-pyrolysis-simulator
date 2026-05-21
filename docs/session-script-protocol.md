# Session Script Protocol

This document pins the non-interactive session harness contract produced by
`python -m simulator session --script`. The harness drives
`simulator.session.SimSession` verbs headlessly and emits one newline-delimited
JSON frame per script command. It is the browser-free operator test surface for
the web/simulator flow.

**Protocol version:** `1.0.0`
**Owning goal:** `WEB-THIN-DRIVER Step 4`

Run the CLI as:

```bash
python -m simulator session \
    --script=recipes/lunar_session.txt \
    [--strict] \
    [--started-at-utc=ISO8601] \
    [--kernel-commit-sha=SHA]
```

Use `--script=-` to read the recipe from stdin. The harness never prompts,
never opens a TUI, and never writes simulator ledgers or run artifacts. Stdout
is NDJSON only. Human diagnostics for failed commands are written to stderr.
`--started-at-utc` and `--kernel-commit-sha` are accepted for invocation parity
with deterministic runner commands; session frames omit volatile provenance.

## Script Grammar

Scripts are UTF-8 text. Each non-empty, non-comment line is one command. Lines
whose first non-space character is `#` are ignored. Tokens use POSIX shell
quoting via `shlex`, so quoted strings may contain spaces.

```text
start --feedstock=<id> [--campaign=<phase>] [--mass-kg=<kg>]
      [--backend=stub|alphamelts|factsage] [--track=pyrolysis|mre_baseline]
      [--additive=SPECIES=KG ...] [--c4-max-temp=<C>]
      [--setpoint=CAMPAIGN.FIELD=VALUE ...]
advance [N]
decide <choice>
adjust <param> <value>
adjust campaign_override <campaign> <field> <value>
pause
resume
snapshot
quit
```

`start` operands mirror the batch runner where a runner flag exists:
`--feedstock`, `--campaign`, `--mass-kg`, `--backend`, `--track`, and repeated
`--additive=SPECIES=KG`. Session-only startup operands are
`--c4-max-temp=<C>` and repeated `--setpoint=CAMPAIGN.FIELD=VALUE`, which map
to `SimSessionConfig.c4_max_temp` and `setpoints_overrides`.
Campaign aliases include `C2A_continuous` -> `C2A` and
`C2A_staged` -> `C2A_STAGED`; `C2A_staged.hold_temp_C` is the staged hot-hold
operator knob.

The default backend is `stub`. Backend resolution uses
`BackendSelectionPolicy.RUNNER_STRICT`, matching the deterministic runner path.

## Frames

Each executed command emits exactly one primary frame:

```jsonc
{
  "seq": 1,                    // 1-based executed-command sequence
  "cmd": "advance 4",          // normalized command text
  "ok": true,
  "frame_type": "step"
}
```

Frames are compact JSON objects with sorted keys and no extra whitespace.
Consumers must treat unknown keys as forward-compatible additions after a
protocol version bump.

### Start

```jsonc
{
  "seq": 1,
  "cmd": "start --feedstock=lunar_mare_low_ti",
  "ok": true,
  "frame_type": "start",
  "protocol_version": "1.0.0",
  "backend": "stub",
  "feedstock_id": "lunar_mare_low_ti",
  "campaign": "C0",
  "mass_kg": 1000.0,
  "track": "pyrolysis"
}
```

`backend` is the resolved backend name requested by the script. The harness
does not emit wall-clock timestamps or live git SHAs.

### Step And Advance

`advance` without `N` advances one hour. `advance N` attempts up to `N`
one-hour steps and stops on the first control condition:

* a surfaced decision
* simulator completion
* a command error

An `advance` command that completes exactly one ordinary step emits:

```jsonc
{
  "seq": 2,
  "cmd": "advance",
  "ok": true,
  "frame_type": "step",
  "step": {
    "hour": 1,
    "campaign": "C0",
    "T_C": 75.0,
    "P_total_bar": 0.0,
    "pO2_bar": 0.0,
    "mass_balance_pct": 2.6e-13,
    "O2_yield_kg_cumulative": 0.0,
    "metal_yields_kg": {},
    "condensation_train_kg": {}
  },
  "steps": [{...}]
}
```

An `advance N` command that completes multiple ordinary steps emits:

```jsonc
{
  "seq": 2,
  "cmd": "advance 4",
  "ok": true,
  "frame_type": "advance",
  "steps": [
    {
      "hour": 1,
      "campaign": "C0",
      "T_C": 75.0,
      "P_total_bar": 0.0,
      "pO2_bar": 0.0,
      "mass_balance_pct": 2.6e-13,
      "O2_yield_kg_cumulative": 0.0,
      "metal_yields_kg": {},
      "condensation_train_kg": {}
    }
  ],
  "steps": [
    {
      "hour": 1,
      "campaign": "C0",
      "T_C": 75.0,
      "P_total_bar": 0.0,
      "pO2_bar": 0.0,
      "mass_balance_pct": 2.6e-13,
      "O2_yield_kg_cumulative": 0.0,
      "metal_yields_kg": {},
      "condensation_train_kg": {}
    }
  ]
}
```

Every object in `steps` is exactly the same per-hour summary shape produced by
`simulator.runner.build_per_hour_summary` and documented in
`docs/runner-output-schema.md`.

If the first control condition is a decision, the command's primary frame type
is `decision_required`:

```jsonc
{
  "seq": 2,
  "cmd": "advance 10",
  "ok": true,
  "frame_type": "decision_required",
  "steps": [{...}],
  "decision": {
    "type": "PATH_AB",
    "options": ["A", "A_staged", "B"],
    "recommendation": "B",
    "context": "choose route"
  }
}
```

If the simulator is complete, the command's primary frame type is `complete`:

```jsonc
{
  "seq": 2,
  "cmd": "advance 10",
  "ok": true,
  "frame_type": "complete",
  "steps": [{...}]
}
```

### Decide, Adjust, Pause, Resume, Snapshot, Quit

Successful non-step command frames have `frame_type` matching the verb.
`snapshot` includes a read-only snapshot projection:

```jsonc
{
  "seq": 4,
  "cmd": "snapshot",
  "ok": true,
  "frame_type": "snapshot",
  "snapshot": {
    "hour": 1,
    "campaign": "C0",
    "T_C": 75.0,
    "P_total_bar": 0.0,
    "pO2_bar": 0.0,
    "mass_balance_pct": 0.0,
    "O2_yield_kg_cumulative": 0.0,
    "metal_yields_kg": {},
    "condensation_train_kg": {}
  },
  "complete": false,
  "paused": false
}
```

`adjust` accepts scalar forms (`stir_factor`, `pO2_mbar`, `c4_max_temp`) and
the variadic campaign override form:

```text
adjust pO2_mbar 1.0
adjust campaign_override C2A stir_factor 1.5
```

## Errors And Strict Mode

Bad syntax, invalid operands, backend resolution failures, and out-of-state
verbs emit an in-stream error frame:

```jsonc
{
  "seq": 3,
  "cmd": "decide B",
  "ok": false,
  "frame_type": "error",
  "error_type": "RuntimeError",
  "error_message": "no pending decision"
}
```

By default, the harness continues with the next command after writing the error
frame to stdout and a human-readable diagnostic to stderr. With `--strict`, the
first error frame terminates execution and the process exits non-zero.

## Determinism

For the same script, same repository data files, and same explicit backend,
stdout is byte-identical across runs. Determinism is enforced by:

* defaulting to the deterministic `stub` backend
* using `RUNNER_STRICT` backend semantics
* omitting wall-clock fields and live git metadata from frames
* serializing compact JSON with sorted keys
* reusing `build_per_hour_summary` for step and snapshot numeric payloads
