# Environment configuration

This registry distinguishes settings intended for operators from names kept
private for tests, CI, diagnostics, and internal plumbing. The guard in
[`tests/test_env_knob_registry.py`](../tests/test_env_knob_registry.py) scans
tracked Python in `app.py`, `simulator/`, `engines/`, `web/`, `tools/`, and
`scripts/`; every environment key it resolves must appear in exactly one
section below. The operator table records the default, accepted values, code
owner, and effect. Harness names are intentionally not application settings.

## Operator knobs

| Name | What it changes | Default | Values or units | Owner module::function |
| --- | --- | --- | --- | --- |
| `ALPHAMELTS_CALC_MODE` | Ambient AlphaMELTS subprocess mode. Any set value that differs raises `ALPHAMELTS_REASON_CALC_MODE_MISMATCH`; this includes the empty string. The binding fingerprint separately stores `conflict` only for a non-empty mismatch. | Unset; adapter resolves `MELTS` for the supported subprocess model. | `MELTS` for the currently supported subprocess model. | `simulator.melt_backend.alphamelts::_ambient_calc_mode` |
| `DYLD_FALLBACK_LIBRARY_PATH` | Search path for ThermoEngine native libraries; the simulator prepends its resolved library directory. | Existing process value, otherwise unset. | Colon-separated directory paths. | `simulator.engine_local_config::setup_thermoengine_dylib_path` |
| `DYLD_LIBRARY_PATH` | Additional search path when locating ThermoEngine native libraries. | Unset. | Colon-separated directory paths. | `simulator.engine_local_config::setup_thermoengine_dylib_path` |
| `LD_LIBRARY_PATH` | Inherited by AlphaMELTS and included in the binding environment fingerprint. | Inherited from the launching process; the simulator does not set it. | Platform-standard path-separated library directories. | `simulator.engine_binding_admission::_runtime_environment_fingerprint` |
| `OMP_NUM_THREADS` | Controls numerical-library thread counts; more-specific library settings take precedence. | Preserved if set; otherwise the simulator supplies `1` where no more-specific setting governs. | Positive integer supported by the numerical library. | `simulator._blas_policy::prepare` |
| `OPENBLAS_NUM_THREADS` | Controls OpenBLAS thread count. | Preserved if set; otherwise `1` unless a more-specific launcher setting governs. | Positive integer. | `simulator._blas_policy::prepare` |
| `MKL_NUM_THREADS` | Controls MKL thread count. | Preserved if set; otherwise `1` unless a more-specific launcher setting governs. | Positive integer. | `simulator._blas_policy::prepare` |
| `NUMEXPR_NUM_THREADS` | Controls NumExpr thread count. | Preserved if set; otherwise `1`. | Positive integer. | `simulator._blas_policy::prepare` |
| `VECLIB_MAXIMUM_THREADS` | Controls Apple Accelerate thread count. | Preserved if set; otherwise `1`. | Positive integer. | `simulator._blas_policy::prepare` |
| `FLASK_SECRET_KEY` | Sets Flask's signing and session secret. | When unset or empty, the app reads `instance/flask_secret_key`; if absent, it atomically creates a random URL-safe secret file with mode `0600`. An existing file is tightened to `0600` before reading. File errors stop app startup; there is no fixed or shared fallback secret. | Any non-empty secret string. | `app::_load_secret_key` |
| `GRIND_HOME` | Selects the root directory for grind campaign files. | `~/regolith-grind`. | Absolute filesystem path; `~` is expanded. | `scripts.grind_home::resolve_grind_home` |
| `OPTIMIZER_RUNS_DIR` | Sets the web UI optimizer run directory and the default output location used by optimizer helper scripts. | `<current working directory>/runs` in the web app; helper scripts can provide their own output directory. The `prune-stale-corpus` CLI accepts `--runs-root` (default `<current working directory>/runs`). | Filesystem path; `~` is expanded by the web app. | `web.routes::_optimizer_runs_root`; `scripts.install_recipe_db_starter::runs_root` |
| `PATH` | Participates in executable discovery for installed engine tools and is inherited by child engines. | Inherited from the launching process. | Platform-standard path-separated executable directories. | `simulator.engine_binding_admission::_runtime_environment_fingerprint` |
| `REGOLITH_ALLOWED_HOSTNAMES` | Adds accepted HTTP Host names to the web app's loopback allowlist. | `localhost` and `127.0.0.1`. | Comma-separated host names; whitespace is trimmed and names lowercased. | `app::_request_authority_config` |
| `REGOLITH_ALLOW_UNSAFE_WERKZEUG` | Retired setting. A truthy value stops startup with guidance to use a real WSGI server for non-loopback deployments. | Unset or false. | No supported enabled value; truthy means `1`, `true`, `yes`, or `on` (case-insensitive). | `app::_run_config_from_env` |
| `REGOLITH_CORPUS` | Supplies the corpus root for `tools/extract_gate.py` when `--corpus` is omitted. | No default; the tool requires `--corpus` or this variable. | Filesystem path to the corpus root. | `tools.extract_gate::main` |
| `REGOLITH_CORPUS_RAW` | Overrides the raw-source directory used by the FastChem compilation harvester. | `<REGOLITH_CORPUS_ROOT>/raw`. | Filesystem path to the corpus `raw/` directory; `~` is expanded. | `tools.harvest_fastchem_compilation::default_corpus` |
| `REGOLITH_CORPUS_ROOT` | Selects the external regolith-corpus checkout used by corpus-backed tools. | `regolith_corpus_root()` uses `~/Repos/regolith-corpus`; `regolith_corpus_ctl_root()` uses `~/Repos/regolith-corpus-ctl` when unset. | Filesystem path to a corpus checkout; `~` is expanded. | `simulator.corpus_paths::regolith_corpus_root` |
| `REGOLITH_CORPUS_VERSION_FILE` | Selects the YAML file that supplies the simulator corpus version and interoperable-version list. | `data/corpus_version.yaml` in this repository. | Path to a valid corpus-version YAML file; `~` is expanded. | `simulator.corpus_version::corpus_version_path` |
| `REGOLITH_DATA_CACHE` | Selects the cache root for the downloaded SGTE unary database. | `~/.cache/regolith-pyrolysis-simulator`. | Filesystem path; the database is stored under `sgte/unary50.tdb`. | `simulator.chemistry.sgte_unary::cached_source_tdb_path` |
| `REGOLITH_ENGINE_CACHE` | SQLite cache of HOT engine outputs. It speeds up CI, gates, the accuracy scorecard, user runs, and the optimizer. Engines are stateless and deterministic: the same engine, chemistry version, and request always give the same prediction, so a cached value is the engine's answer. CI and scorecards set this by default; CI opens it read-only. The key is engine + version + request. Do not leave it unset in CI to isolate tests. | Disabled when unset or empty. CI and scorecard runners set a cache file. | Filesystem path; `~` is expanded. A path sqlite cannot open (mode 000, or a directory) is disabled for the process after one notice naming the path and the reason; the run solves uncached and counts `not_stored:unopenable`. A locked or busy open is retried. A wrong-schema or non-sqlite file is disabled for the process after one notice and counts `not_stored:schema_mismatch`. A read-only open does not create a missing file. | `simulator.engine_cache::_configured_engine_cache_path` |
| `REGOLITH_ENGINE_CACHE_MODE` | Access mode for that cache. CI sets `readonly` by default so a store attempt is a counted no-op and the shared file is never written. Read-only is enforced here, not by deleting the path. | Read/write when unset or empty. CI sets `readonly`. | Unset or empty means read/write; `readonly` uses an existing cache read-only (a missing file is not created). Any other non-empty value raises `ValueError` when the cache path is configured. | `simulator.engine_cache::_configured_cache_mode` |
| `REGOLITH_FLASK_DEBUG` | Enables Flask development debug mode on loopback only and exposes debug feedstocks in the development UI. | Disabled. | `1`, `true`, `yes`, or `on` (case-insensitive) enables it; other values disable it. | `app::_run_config_from_env` |
| `REGOLITH_HOST` | Chooses the web server bind host. | `127.0.0.1`. | Host or IP address; debug mode is refused for a non-loopback host. | `app::_run_config_from_env` |
| `REGOLITH_MAGEMIN_BINARY` | Explicitly selects MAGEMin in pure-phase diagnostic and scoring helpers. | Unset; engine configuration and documented binary locations are used. | Path to the compiled MAGEMin executable. | `simulator.battery.compilation_tier::default_pure_phase`; `scripts.janaf_pure_phase_score::_backend` |
| `REGOLITH_MAGEMIN_MAX_CONCURRENCY` | Sets the host-wide maximum concurrent MAGEMin subprocess launches. | `2`. | Positive integer; invalid, empty, or non-positive values use `2`. | `simulator.melt_backend.magemin::_magemin_subprocess_slot_count` |
| `REGOLITH_MAGEMIN_SLOTS` | Legacy alias for `REGOLITH_MAGEMIN_MAX_CONCURRENCY`, used only if the primary name is unset. | Empty or unset. | Positive integer; invalid, empty, or non-positive values use `2`. | `simulator.melt_backend.magemin::_magemin_subprocess_slot_count` |
| `REGOLITH_OPTIMIZER_EVAL_TIMEOUT_SECONDS` | Sets the optimizer per-evaluation timeout when no timeout is passed explicitly. | `2700` seconds (45 minutes). | Finite positive number of seconds. | `simulator.optimize.pool::resolve_eval_timeout_seconds` |
| `REGOLITH_PARSED_YAML_CACHE_MAX_BYTES` | Sets the per-box parsed-YAML disk-cache byte cap. On each process's first cache write and then every 32 writes, eviction orders entries by file mtime (cache hits refresh mtime) and removes oldest entries until usage reaches 90% of the cap or only the protected just-written entry remains. With `0`, each sweep retains only that entry. Literal `off` disables size-based eviction. | `1610612736` bytes (1.5 GiB). | Non-negative integer bytes or literal `off`; invalid values raise `ValueError` naming this variable. | `simulator.yaml_cache::_cache_max_bytes` |
| `REGOLITH_PORT` | Chooses the web server bind port. | `3000`. | Integer from `1` through `65535`. | `app::_run_config_from_env` |

## Harness (not for operators)

These names are retained for test, CI, diagnostic, or implementation plumbing.
They are private controls and are not part of the simulator's operator
configuration.

| Name | Why private |
| --- | --- |
| `DISPLAY` | Removed from the MAGEMin child environment to prevent display discovery in a headless subprocess. |
| `FIDELITY_DIAGNOSTIC_INTERNAL_ANALYTICAL_HIGH` | Enables internal analytical-model comparison in the fidelity diagnostic harness. |
| `FIDELITY_DIAGNOSTIC_STUB_HIGH` | Legacy alias used only by the fidelity diagnostic harness. |
| `FIDELITY_HIGH_BACKEND` | Selects a backend for the fidelity DOE harness. |
| `FIDELITY_HIGH_HOURS` | Sets run duration in the fidelity DOE harness. |
| `FIDELITY_SKIP_PROFILE_CONSTRAINTS` | Disables profile constraints in the fidelity DOE harness. |
| `GOALFLIGHT_HOSTNAME` | Supplies a host label to an internal battery diagnostic. |
| `HOSTNAME` | Fallback host label for an internal battery diagnostic. |
| `MAGEMIN_PROBE_BINARY` | Selects the binary for a targeted MAGEMin hang probe. |
| `MAGEMIN_PROBE_OUTPUT_DIR` | Routes output from a targeted MAGEMin hang probe. |
| `MAGEMIN_PROBE_SCAN_ID` | Labels output from a targeted MAGEMin hang probe. |
| `MPLCONFIGDIR` | Selects a matplotlib cache for internal profiling and calibration scripts. |
| `REGOLITH_DEBUG_FEEDSTOCKS` | Exposes debug-only feedstocks in the development UI. |
| `REGOLITH_ENGINE_CACHE_BOX` | Labels cache rows with a source box for internal engine-cache diagnostics. |
| `REGOLITH_ENGINE_POOL_DEBUG` | Prints internal engine-worker lifecycle diagnostics. |
| `REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP` | Internal worker lifecycle handoff flag. |
| `REGOLITH_OPTIMIZER_CHILD_PID_LOG` | Internal child-process tracking log path. |
| `REGOLITH_OPTIMIZER_WARM_WORKERS` | Controls optimizer worker reuse, an internal performance setting. |
| `REGOLITH_OPTIMIZER_WORKER_OUTPUT_DIR` | Internal worker output handoff path. |
| `REGOLITH_PARSED_YAML_CACHE` | Internal switch for the process-shared parsed-YAML disk cache. |
| `REGOLITH_PARSED_YAML_CACHE_DIR` | Selects the implementation cache directory for parsed YAML. |
| `REGOLITH_REPO_ROOT` | Overrides the repository path for an internal cache validation script. |
| `REGOLITH_STUDIO_HOST` | Labels the Studio host in an internal battery scoring report. |
| `REGOLITH_THERMOENGINE_LOG_DIR` | Routes native engine diagnostics to a log directory. |
| `REGOLITH_VAPOROCK_SESSION_WARM` | Enables internal VapoRock worker reuse for test and diagnostic workloads. |
| `TMPDIR` | Selects temporary files for internal scripts and subprocesses. |
| `XDG_CACHE_HOME` | Selects XDG cache storage for the parsed-YAML cache and a profiling script. |
