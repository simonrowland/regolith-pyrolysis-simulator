# Environment configuration

This page lists environment variables that operators can use to change the
simulator, web app, engine setup, or data inputs. Diagnostic, cache-internal,
and harness-only variables are registered with reasons in
[`tests/test_env_knob_registry.py`](../tests/test_env_knob_registry.py).
That test scans tracked Python in `app.py`, `simulator/`, `engines/`, `web/`,
`tools/`, and `scripts/`; each environment read must appear here or in the
internal registry.

| Name | Purpose and effect | Default | Valid values |
| --- | --- | --- | --- |
| `ALPHAMELTS_CALC_MODE` | Ambient AlphaMELTS subprocess mode; mismatch with the model's resolved mode is recorded as a binding conflict. | Adapter-resolved (`MELTS` for the supported subprocess model). | `MELTS` for the currently supported subprocess model. |
| `DYLD_FALLBACK_LIBRARY_PATH` | Search path for ThermoEngine native libraries; the simulator prepends its resolved library directory. | Existing process value, otherwise unset. | Colon-separated directory paths. |
| `DYLD_LIBRARY_PATH` | Additional search path when locating ThermoEngine native libraries. | Unset. | Colon-separated directory paths. |
| `OMP_NUM_THREADS` | Controls numerical-library thread counts; more-specific library settings take precedence. | Preserved if set; otherwise the simulator supplies `1` where no more-specific setting governs. | Positive integer supported by the numerical library. |
| `OPENBLAS_NUM_THREADS` | Controls OpenBLAS thread count. | Preserved if set; otherwise `1` unless a more-specific launcher setting governs. | Positive integer. |
| `MKL_NUM_THREADS` | Controls MKL thread count. | Preserved if set; otherwise `1` unless a more-specific launcher setting governs. | Positive integer. |
| `NUMEXPR_NUM_THREADS` | Controls NumExpr thread count. | Preserved if set; otherwise `1`. | Positive integer. |
| `VECLIB_MAXIMUM_THREADS` | Controls Apple Accelerate thread count. | Preserved if set; otherwise `1`. | Positive integer. |
| `FLASK_SECRET_KEY` | Sets Flask's signing/session secret. | When unset or empty, the app reads `instance/flask_secret_key`; if absent, it creates a random URL-safe secret and stores it with owner-only permissions. There is no fixed or shared fallback secret. | Any non-empty secret string. |
| `GRIND_HOME` | Selects the root directory for grind campaign files. | `~/regolith-grind`. | Absolute filesystem path; `~` is expanded. |
| `OPTIMIZER_RUNS_DIR` | Sets the web UI optimizer run directory and the default output location used by optimizer helper scripts. | `<current working directory>/runs` in the web app; helper scripts can provide their own output directory. | Filesystem path; `~` is expanded by the web app. |
| `PATH` | Participates in executable discovery for installed engine tools. | Inherited from the launching process. | Platform-standard path-separated executable directories. |
| `REGOLITH_ALLOWED_HOSTNAMES` | Adds accepted HTTP Host names to the web app's loopback allowlist. | `localhost` and `127.0.0.1`. | Comma-separated host names; whitespace is trimmed and names lowercased. |
| `REGOLITH_ALLOW_UNSAFE_WERKZEUG` | Retired setting. A truthy value stops startup with guidance to use a real WSGI server for non-loopback deployments. | Unset/false. | No supported enabled value; truthy means `1`, `true`, `yes`, or `on` (case-insensitive). |
| `REGOLITH_CORPUS` | Supplies the corpus root for `tools/extract_gate.py` when `--corpus` is omitted. | No default; the tool requires `--corpus` or this variable. | Filesystem path to the corpus root. |
| `REGOLITH_CORPUS_RAW` | Overrides the raw-source directory used by the FastChem compilation harvester. | `<REGOLITH_CORPUS_ROOT>/raw`. | Filesystem path to the corpus `raw/` directory; `~` is expanded. |
| `REGOLITH_CORPUS_ROOT` | Selects the external regolith-corpus checkout used by corpus-backed tools. | `~/Repos/regolith-corpus`. | Filesystem path to a corpus checkout; `~` is expanded. |
| `REGOLITH_CORPUS_VERSION_FILE` | Selects the YAML file that supplies the simulator corpus version and interoperable-version list. | `data/corpus_version.yaml` in this repository. | Path to a valid corpus-version YAML file; `~` is expanded. |
| `REGOLITH_DATA_CACHE` | Selects the cache root for the downloaded SGTE unary database. | `~/.cache/regolith-pyrolysis-simulator`. | Filesystem path; the database is stored under `sgte/unary50.tdb`. |
| `REGOLITH_FLASK_DEBUG` | Enables Flask development debug mode on loopback only. | Disabled. | `1`, `true`, `yes`, or `on` (case-insensitive) enables it; other values disable it. |
| `REGOLITH_HOST` | Chooses the web server bind host. | `127.0.0.1`. | Host or IP address; debug mode is refused for a non-loopback host. |
| `REGOLITH_MAGEMIN_BINARY` | Explicitly selects MAGEMin in pure-phase diagnostic/scoring helpers. | Unset; engine configuration and documented binary locations are used. | Path to the compiled MAGEMin executable. |
| `REGOLITH_MAGEMIN_MAX_CONCURRENCY` | Sets the host-wide maximum concurrent MAGEMin subprocess launches. | `2`. | Positive integer; invalid, empty, or non-positive values use `2`. |
| `REGOLITH_MAGEMIN_SLOTS` | Legacy alias for `REGOLITH_MAGEMIN_MAX_CONCURRENCY`, used only if the primary name is unset. | Empty/unset. | Positive integer; invalid, empty, or non-positive values use `2`. |
| `REGOLITH_OPTIMIZER_EVAL_TIMEOUT_SECONDS` | Sets the optimizer per-evaluation timeout when no timeout is passed explicitly. | `DEFAULT_EVAL_TIMEOUT_SECONDS`. | Finite positive number of seconds. |
| `REGOLITH_PORT` | Chooses the web server bind port. | `3000`. | Integer from `1` through `65535`. |
