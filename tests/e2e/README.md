# E2E harness — Playwright browser tests against the live dev server

Drives the app **through a real Chromium browser over HTTP + socket.io**, like an
operator. This closes the gap left by `tests/test_web_optimizer.py`, which calls
route functions in-process and never issues an HTTP request — every in-process
suite was green while the app was visibly broken in a browser.

## The one command

```bash
.venv/bin/python -m pytest tests/e2e/test_happy_path_journey.py \
    tests/e2e/test_no_feedstock_alert.py \
    tests/e2e/test_run_stall_out_of_domain.py \
    tests/e2e/test_optimizer_bounded.py -n0 -v
```

Prerequisites: the dev server **already running** on `http://127.0.0.1:3000`
(this harness never starts or kills a server), and the `playwright` package from
`.venv` (already installed; Chromium launches headless by default,
`REGOLITH_E2E_HEADED=1` for a visible browser, `REGOLITH_E2E_BASE_URL` to point
elsewhere).

`-n0` is required: it overrides the repo-wide `-n auto` xdist default. Browser
tests against one shared server must run serially.

Stack choice: **python `playwright.sync_api` + plain pytest**, because the repo
is pytest-native and `.venv` already ships `playwright`; the ~40 lines of
fixtures `pytest-playwright` would provide are owned in `conftest.py`.

## What each test covers

| Test | Journey step / defect | Asserts (positive signals, never absence-of-error) |
|---|---|---|
| `test_happy_path_journey.py` | The full operator journey, in order | **1** `/` renders the Configuration panel + ≥1 real feedstock option · **2** selecting a feedstock loads its card back from the server · **3** Start emits `start_simulation` *and the server answers* `simulation_status: started` within 60s · **4** `#status-hour` advances past `Hour: 0` within 90s · **5** run reaches `#status-text == "Complete"` within 180s per state-change and `#product-ledger-content` holds real numbers (plus `simulation_complete.mass_in_kg > 0`) · **6** `/optimizer` renders a leaderboard with ≥1 row inside a 120s bound · **7** `/thermal-train` shows a populated report (`data-state != no_data`, table rows with numbers). Steps 1–4 abort on first failure; steps 5–7 are collected so one run reports everything reachable |
| `test_no_feedstock_alert.py` | Defect (a): Start with no feedstock | Registers a record-and-dismiss dialog handler (Playwright auto-dismisses native dialogs — that is why this looked dead); asserts the alert text is exactly `Please select a feedstock first.`, that `#status-text` stays `Ready`, and that **no** `start_simulation` left the browser |
| `test_run_stall_out_of_domain.py` | Defect (b): run starts then stalls (owner saw OUT_OF_DOMAIN) | Starts a run at max speed; requires the server to answer within 60s, then continuous forward progress (no 90s no-progress window — a single AlphaMELTS-heavy hour has been observed taking >45s) across a 360s window, long enough to reach the C4 campaign where the terminal out-of-domain refusal reproduces. Any stall or terminal refusal fails with the **verbatim socket.io tail** (truncated per-event; the full stream is in the evidence JSON). The run is cancelled afterwards |
| `test_optimizer_bounded.py` | Defect (c): `/optimizer` ~7 min | Bounded 120s goto; blows up loudly instead of hanging the suite; then asserts leaderboard rows exist |

Decision modals (`decision_required`) are auto-answered with the recommended
option — what an operator following the recommendation does — via an
event-driven MutationObserver, and each auto-answer is recorded in the evidence
artifact. No fixed sleeps are used anywhere for synchronisation; all waiting is
web-first assertions or in-page predicates with explicit timeouts, and a
predicate timeout **is** the failure signal.

## Evidence captured on every test

Per test, written to `tests/e2e/artifacts/<UTC-timestamp>/`:

- `<test>.evidence.json` — browser console messages (errors flagged), page
  errors, failed network requests (navigation-aborted same-origin GETs and
  socket.io poll aborts are classified benign, everything else stays loud),
  HTTP ≥ 400 responses, **the full tapped socket.io stream both directions**
  (the client is instrumented via an `io()` wrapper injected with
  `add_init_script`; reserved connect/disconnect events bypass `onAny`, so the
  stream starts at the first custom event), auto-answered decisions, and step
  notes. Large run streams are gzipped on disk (`*.evidence.json.gz`).
- `<test>.png` — full-page screenshot, on failure; the journey also saves
  `journey-step5-*.png` at the terminal-state moment.
- The socket tap records the outbound `start_simulation` payload verbatim, so
  "what did the browser actually send" is never in doubt.

## What it found (live server, 2026-08-27/28, branch engine-2026-08-16)

Authoritative per-run artifacts: `tests/e2e/artifacts/20260828T001656Z/` (full
suite) and the newest journey-only run directory. Reproductions are consistent
across three independent suite runs.

**Suite result: 2 failed, 2 passed.** Furthest journey step reached: **step 5 —
the run starts, advances through C0/C0B/C2A_STAGED/C3_NA, then lawfully
fail-closes at campaign C4 (hour 35) with a transport-model-coverage refusal
(see FINDING 1 — deliberate behaviour, but the happy path does not complete,
so the tests stay red).** Steps 6 and 7 pass.

### FINDING 1 (reproduced ×4) — the journey cannot complete: the run terminally REFUSES at C4 with `viscous_p_bulk_transport_out_of_domain` (a lawful, deliberate fail-closed refusal)

Journey steps 1–4 pass: `/` renders (25 feedstocks), feedstock card loads,
server answers `started` (AlphaMELTSBackend, `backend_status: ok`),
`#status-hour` advances. Then, every time, the run **terminates** at hour 35 /
campaign C4 / 1160 °C instead of completing:

```
status:  "refused"
reason:  "viscous_p_bulk_transport_out_of_domain"
detail:  "transitional Kn uses viscous Poiseuille / Bernoulli P_bulk outside its
         validity domain (Kn < 0.01); free-molecular Kn >= 10 keeps the HKL
         upper-bound path; t-379 (0.7) supplies transitional/molecular conductance"
knudsen_number: 0.0154   (transitional: viscous needs Kn < 0.01)
overhead_pressure_mbar: 0.2   (the UI default C4 override pO2_mbar=0.2)
pipe_diameter_m: 0.12, carrier_gas: O2, stage: C4
ledger_yields_authorized: false
affected_species: 17 (Ca, CrO…, Fe, K…, Mg…, Na…, SiO, SiO2_gas, TiO2_gas)
```

(Full verbatim payload: the watchdog test's `evidence.json.gz` and its failure
message.) En route, 69 socket events mention `out_of_domain` (status-bearing
diagnostics inside ticks) — visible only because the harness taps socket.io;
nothing of it appears in any HTTP log.

**This is not a crash and not a hang.** Controller ruling (2026-08-28): the
refusal is CORRECT, DELIBERATE behaviour — viscous Poiseuille P_bulk is invalid
in the transitional Knudsen band, so pyrolysis extraction fail-closes rather
than extrapolate (Stage 0 bakeout computes-and-marks instead). Documented at
`simulator/evaporation.py viscous_p_bulk_out_of_domain_diagnostic`; the viscous
domain ends at 0.2573 mbar at 1433 K in a 0.12 m duct (computed with the
project's own `_knudsen_number`), and t-379 (0.7) is the planned lift via
transitional/molecular conductance. **The tests keep failing on it because the
happy-path journey genuinely does not complete** — the default UI recipe
drives C4 into a region the transport model lawfully refuses.

The operator-visible consequence is still worth the owner's attention: per
`simulator-socket.js:553-557`, after a `refused` status the UI latches
terminal-refused and **`#btn-start` stays disabled** (only `simulation_complete`
or an `error` status re-enables it). The operator lands on a page that says
`refused — viscous_p_bulk_transport_out_of_domain` with a dead Start button, an
`n/a` product ledger (screenshot: `journey-step5-FAILED.png`), and no forward
path short of reloading — which is exactly how a lawful refusal presents as
"the app stalled". The two decision modals the run hits first (`PATH_AB`,
`BRANCH_ONE_TWO`) were auto-answered with the recommended options (`A_staged`,
Branch Two), so this is the recommended-path outcome, not an exotic choice.

### FINDING 2 (the other half of "start looks dead") — the server wedges ~13–41s before it answers Start at all

Before any of the above, `handle_start` spends tens of seconds silent. Caught
in the act in-process with `faulthandler` (full stack:
`artifacts/diag-inprocess.log`):

```
web/events.py:3265 handle_start → simulator/session.py:321 start
→ simulator/core.py:1231 load_batch → core.py:9581 _build_process_inventory
→ core.py:11103 _emit_stage0_foulant_diagnostics
→ engines/builtin/stage0_pretreatment.py:1094 _dispatch_volatilization_diagnostic
→ engines/builtin/foulant_disposition.py:182 chi_escape_salt
→ foulant_disposition.py:141 _load_vapor_pressures
→ yaml.safe_load   ← pure-Python parse of the 1.2 MB data/vapor_pressures.yaml
```

`_load_vapor_pressures` runs **~8× per start** (counter-verified,
`diag-count.log`), 2.34s per parse vs 0.27s with the already-available
`yaml.CSafeLoader`, no caching. Measured wedge before the client sees anything:
41s on the live server when loaded (`diag-patient.log`), 13s when idle, 57s for
the full handler in-process. Under concurrent starts the parses GIL-serialize
and no client gets answered within 30s at all (6 concurrent probes). During the
wedge the status bar shows `Running` — a **local** UI lifecycle transition, not
a server signal — and no `run_id` exists yet, so the run cannot even be
cancelled. Transport was eliminated as a cause: ledger_api acks, malformed-start
rejects, and polling delivery all work (`diag-probe.log`); the silence is
specific to the valid-start path and backend-agnostic (`diag-backend.log`).

### FINDING 3 — slow hours and megabyte tick payloads (observed en route)

At the C0B→C2A_STAGED boundary one hour took >45s wall-clock (next tick
arrived 73s later). `simulation_tick` payloads run ~1.3 MB each plus ~250 KB
`per_hour_summary`, every simulated hour, over long-polling. A run is hundreds
of ticks → hundreds of MB of socket traffic per run. Not asserted as a failure
(hours still arrive), but it is why the UI feels frozen mid-run.

### Defect (a) — alert confirmed working as coded

`test_no_feedstock_alert.py` passes: the native `alert("Please select a feedstock
first.")` fires with the exact text; no `start_simulation` escapes. The defect
is the UX (auto-dismissed native dialog = looks dead), and this test pins the
text so any regression in that guard is caught.

### Defect (c) — /optimizer now fast

`/optimizer` rendered in 4.9–7.1s with an 11-row leaderboard (winner:
`mars_perchlorate_rich` / mars-perchlorate-rich-objectives-v1) — the in-flight
fix appears to have landed in the running server. The bounded test stays as the
regression guard.

### Step 7 — thermal train populated

`/thermal-train` after the refused run: `data-state=live`, 12 data rows with
numbers (the live session ledger view works even for a run that refused).

One instrumentation note: during one journey run the browser issued a request
for `unpkg.com/maplibre-gl` CSS that aborted. **`maplibre` appears nowhere in
this repository** (verified by repo-wide grep over all non-vendored files) —
that request originated in the browser environment, not the application, and is
environment noise, not an app defect. The recorder now splits failures by
origin: only app origins (`127.0.0.1:3000` plus the three genuine CDN
dependencies — `cdn.plot.ly/plotly-2.35.0`, `cdn.socket.io/4.8.1`,
`unpkg.com/htmx.org@2.0.4`, per `web/templates/base.html:14-20`) are reported
as findings; everything else lands in `third_party_failures` in the evidence
JSON. A failure of one of the three real CDN deps WOULD be a finding (none
failed in any run).

## Notes / caveats

- The dev server auto-reloads when `web/routes.py` is saved by another active
  worker; a restart mid-run invalidates that run's evidence (timestamps in the
  artifacts make this visible).
- Tests start real runs on the shared server. Runs that reach a terminal state
  persist as normal run records (same as an operator clicking); runs still live
  at test end are cancelled via `POST /api/runs/<run_id>/cancel` (best effort,
  30s bound).
- The `diag-*.log` files in the newest artifacts directories are the raw
  investigation transcripts (python socket.io client probes, in-process
  faulthandler repro, parse counters) behind the findings above.
