# Wave-Z web client-identity fixture repair

## Scope

Steer `1` superseded Item 1 because `fix/tc8-maxhold` had already landed. This
worker changed Item 2 only. Production behavior in `web/events.py` remains
untouched.

## Finding and fix

The render, socket-trace, and cross-surface parity fixtures opened Socket.IO
clients without first establishing the browser identity over HTTP. The start
handler correctly rejected those clients with `client_identity_required`.

Each fixture now follows the established web test pattern:

1. create one Flask test client;
2. request `/` successfully to establish identity;
3. pass that same client as `flask_test_client` to
   `socketio.test_client(...)`.

Changed files:

- `tests/test_web_render.py`
- `tests/test_web_socket_trace.py`
- `tests/test_cross_surface_parity.py`

## Verification

Main-repo `.venv`; all pytest runs used `-n0`.

- Scoped trio: `2 passed, 1 xfailed, 13 warnings in 228.40s`.
- Complete touched-file suites: `7 passed, 1 xfailed, 13 warnings in 143.09s`.
- The xfail is the pre-existing socket-trace golden-drift marker; the test now
  reaches the trace comparison instead of aborting at the identity gate.
- `git diff --check`: clean.
- Scope audit before staging: only the three test files above were modified;
  no `web/events.py`, `run_executor.py`, or Item-1 change is present.

## Result

TEST-DISHONEST fixture gap repaired. No production behavior changed. Ready for
controller review; requested state is staged without commit.
